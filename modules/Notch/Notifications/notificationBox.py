import hashlib
import os
import shutil
import weakref
from queue import Queue
from threading import Thread

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

import services.icons as icons
from modules.Notch.Notifications.NotificationBox.image import CustomImage


# Константы
PERSISTENT_DIR = f"{GLib.get_user_cache_dir()}/vidgex-shell/notifications"
PERSISTENT_HISTORY_FILE = PERSISTENT_DIR + "/notification_history.json"
PERSISTENT_IMAGES_DIR = PERSISTENT_DIR + "/images"

MAX_NOTIFICATION_HISTORY = 50
NOTIFICATION_WIDTH = 320
GROUP_ANIMATION_DURATION = 200
THUMBNAIL_SIZE = 48
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_DIMENSION = 2048

_history_ignored_apps: frozenset = frozenset()


def get_history_ignored_apps() -> frozenset:
    return _history_ignored_apps


# Вспомогательные утилиты
def set_pointer_cursor(widget: Gtk.Widget) -> None:
    def _enter(w, _e):
        win = w.get_window()
        if win:
            win.set_cursor(Gdk.Cursor.new_from_name(w.get_display(), "pointer"))

    def _leave(w, _e):
        win = w.get_window()
        if win:
            win.set_cursor(None)

    widget.connect("enter-notify-event", _enter)
    widget.connect("leave-notify-event", _leave)


def get_safe_image_path(uuid) -> str:
    safe_id = hashlib.md5(str(uuid).encode()).hexdigest()
    return os.path.join(PERSISTENT_IMAGES_DIR, f"{safe_id}.png")


def is_safe_image_file(path: str) -> bool:
    if not path or not path.startswith("/") or not os.path.isfile(path):
        return False
    try:
        size = os.path.getsize(path)
        if not (0 < size < MAX_IMAGE_BYTES):
            return False
        fmt, w, h = GdkPixbuf.Pixbuf.get_file_info(path)
        return bool(fmt) and w <= MAX_IMAGE_DIMENSION and h <= MAX_IMAGE_DIMENSION
    except Exception:
        return False


# Фоновый I/O-поток
class _IOWorker:
    # Единственный фоновый поток для всех дисковых операций с уведомлениями
    __slots__ = ("_queue", "_thread")

    def __init__(self):
        self._queue: Queue = Queue()
        self._thread = Thread(target=self._run, daemon=True, name="notif-io")
        self._thread.start()

    def _run(self) -> None:
        while True:
            task = self._queue.get(block=True)
            if task is None:
                break
            try:
                task()
            except Exception:
                pass
            finally:
                self._queue.task_done()

    def submit(self, task) -> None:
        self._queue.put_nowait(task)


_io_worker = _IOWorker()


def submit_io_task(task) -> None:
    _io_worker.submit(task)


def cleanup_orphan_images(active_ids) -> None:
    if not os.path.isdir(PERSISTENT_IMAGES_DIR):
        return
    valid = {f"{hashlib.md5(str(uid).encode()).hexdigest()}.png" for uid in active_ids}
    try:
        for fn in os.listdir(PERSISTENT_IMAGES_DIR):
            if fn.endswith(".png") and fn not in valid:
                try:
                    os.remove(os.path.join(PERSISTENT_IMAGES_DIR, fn))
                except OSError:
                    pass
    except OSError:
        pass


def delete_notification_image(uuid) -> None:
    _io_worker.submit(lambda: _delete_image_sync(uuid))


def _delete_image_sync(uuid) -> None:
    try:
        os.remove(get_safe_image_path(uuid))
    except OSError:
        pass


def clear_all_notification_images() -> None:
    def _do():
        if os.path.isdir(PERSISTENT_IMAGES_DIR):
            shutil.rmtree(PERSISTENT_IMAGES_DIR, ignore_errors=True)
    _io_worker.submit(_do)


# Модели данных
class HistoricalNotification:
    # Лёгкая модель данных для уведомлений из истории.
    # Не имеет D-Bus соединения, не может быть закрыта системой,
    # не имеет actions с invoke().
    __slots__ = ("id", "summary", "body", "app_name", "timestamp", "actions")

    def __init__(self, *, id, summary, body, app_name, timestamp):
        self.id        = id
        self.summary   = summary
        self.body      = body
        self.app_name  = app_name
        self.timestamp = timestamp
        self.actions: list = []


# Кнопка действия
class ActionButton(Button):
    __slots__ = ("action", "_nb_ref", "_handlers")

    def __init__(self, action, index: int, total: int, notification_box):
        self.action  = action
        self._nb_ref = weakref.ref(notification_box)

        super().__init__(
            name="action-button",
            h_expand=True,
            on_clicked=self._on_clicked,
            child=Label(
                name="button-label",
                h_expand=True,
                h_align="fill",
                ellipsization="end",
                max_chars_width=1,
                label=str(action.label)[:20],
            ),
        )

        if index == 0:
            self.add_style_class("start-action")
        elif index == total - 1:
            self.add_style_class("end-action")
        else:
            self.add_style_class("middle-action")

        self._handlers = (
            self.connect("enter-notify-event", self._on_enter),
            self.connect("leave-notify-event", self._on_leave),
        )
        set_pointer_cursor(self)

    def _on_enter(self, *_):
        nb = self._nb_ref()
        if nb and not getattr(nb, "_destroyed", True):
            nb.hover_button()

    def _on_leave(self, *_):
        nb = self._nb_ref()
        if nb and not getattr(nb, "_destroyed", True):
            nb.unhover_button()

    def _on_clicked(self, *_):
        if self.action:
            self.action.invoke()
            parent = getattr(self.action, "parent", None)
            if parent:
                parent.close("dismissed-by-user")

    def destroy(self) -> None:
        for hid in self._handlers:
            try:
                if self.handler_is_connected(hid):
                    self.disconnect(hid)
            except Exception:
                pass
        self.action  = None
        self._nb_ref = None
        super().destroy()


# Карточка уведомления
class NotificationBox(Box):
    __slots__ = (
        "notification", "uuid", "timeout_ms", "_timeout_id", "_container_ref",
        "_destroyed", "_is_history", "_hover_handlers", "_action_buttons",
        "_thumb_path", "image_box", "_close_btn",
    )

    def __init__(self, notification, timeout_ms: int = 5000,
                 is_history: bool = False, **kwargs):
        super().__init__(
            name="notification-box",
            orientation="v",
            h_align="fill",
            h_expand=True,
        )
        self.notification = notification
        self.uuid = getattr(notification, "id", None) or GLib.uuid_string_random()
        self._timeout_id = None
        self._container_ref = None
        self._destroyed = False
        self._is_history = is_history
        self._action_buttons: list[ActionButton] = []
        self._thumb_path = get_safe_image_path(self.uuid)
        self._close_btn = None

        live_timeout = getattr(notification, "timeout", -1)
        self.timeout_ms = (
            live_timeout
            if timeout_ms != 0 and live_timeout not in (-1, None)
            else timeout_ms
        )
        if self.timeout_ms > 0:
            self.start_timeout()

        self.image_box = Box(name="notification-image", orientation="v")
        self._load_image_async()

        self.add(self._create_content())
        actions = self._create_action_buttons()
        if actions:
            self.add(actions)

        self._hover_handlers = (
            self.connect("enter-notify-event", self._on_hover_enter),
            self.connect("leave-notify-event", self._on_hover_leave),
        )

    # Изображение (асинхронно)
    def _load_image_async(self) -> None:
        notif = self.notification
        is_live = not isinstance(notif, HistoricalNotification)
        thumb = self._thumb_path

        if is_live and getattr(notif, "image_pixbuf", None):
            try:
                pb = notif.image_pixbuf.scale_simple(
                    THUMBNAIL_SIZE, THUMBNAIL_SIZE,
                    GdkPixbuf.InterpType.BILINEAR,
                )
                if pb:
                    self._apply_pixbuf(pb)
                    submit_io_task(lambda p=pb, t=thumb: self._save_pixbuf(p, t))
            except Exception:
                pass
            return

        def _load():
            pb = None
            try:
                if is_live:
                    icon = getattr(notif, "app_icon", None)
                    path = icon.replace("file://", "") if icon else None
                    if path and is_safe_image_file(path):
                        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                            path, THUMBNAIL_SIZE, THUMBNAIL_SIZE, False
                        )
                        if pb and not os.path.exists(thumb):
                            self._save_pixbuf(pb, thumb)
                else:
                    if is_safe_image_file(thumb):
                        pb = GdkPixbuf.Pixbuf.new_from_file(thumb)
            except Exception:
                pass
            if pb:
                GLib.idle_add(self._apply_pixbuf, pb)

        submit_io_task(_load)

    def _apply_pixbuf(self, pb: GdkPixbuf.Pixbuf) -> bool:
        if self._destroyed:
            return False
        img = CustomImage(pixbuf=pb)
        img.set_valign(Gtk.Align.START)
        for child in self.image_box.get_children():
            self.image_box.remove(child)
        self.image_box.add(img)
        self.image_box.show_all()
        return False

    @staticmethod
    def _save_pixbuf(pb: GdkPixbuf.Pixbuf, path: str) -> None:
        try:
            os.makedirs(PERSISTENT_IMAGES_DIR, exist_ok=True)
            pb.savev(path, "png", [], [])
        except OSError:
            pass

    # Контент
    def _create_content(self) -> Box:
        notif = self.notification

        summary = Label(
            name="notification-summary",
            markup=str(getattr(notif, "summary", ""))[:80],
            h_align="start",
            max_chars_width=20,
            ellipsization="end",
        )
        app_name_label = Label(
            name="notification-app-name",
            markup=str(getattr(notif, "app_name", "Unknown"))[:20],
            h_align="start",
            max_chars_width=12,
            ellipsization="end",
        )
        text_children = [
            Box(
                name="notification-summary-box",
                orientation="h",
                children=[summary, Box(name="notif-sep"), app_name_label],
            )
        ]

        body_raw = getattr(notif, "body", None)
        if body_raw:
            text_children.append(
                Label(
                    name="notification-body",
                    markup=str(body_raw)[:150],
                    h_align="start",
                    max_chars_width=40,
                    ellipsization="end",
                )
            )

        content_children = [
            self.image_box,
            Box(
                name="notification-text",
                orientation="v",
                v_align="center",
                h_expand=True,
                children=text_children,
            ),
        ]

        if not self._is_history:
            close_btn = Button(
                name="notif-close-button",
                child=Label(name="notif-close-label", markup=icons.cancel),
            )
            close_btn.connect("clicked", self._on_close_clicked)
            close_btn.connect("enter-notify-event", lambda btn, _: self.hover_button())
            close_btn.connect("leave-notify-event", lambda btn, _: self.unhover_button())
            set_pointer_cursor(close_btn)
            self._close_btn = close_btn
            content_children.append(
                Box(orientation="v", v_align="center", children=[close_btn])
            )

        return Box(
            name="notification-content",
            spacing=8,
            h_expand=True,
            children=content_children,
        )

    def _create_action_buttons(self):
        actions = getattr(self.notification, "actions", None)
        if not actions:
            return None
        grid = Gtk.Grid(column_homogeneous=True, column_spacing=4)
        for i, action in enumerate(actions):
            btn = ActionButton(action, i, len(actions), self)
            self._action_buttons.append(btn)
            grid.attach(btn, i, 0, 1, 1)
        return grid

    # Закрытие
    def _on_close_clicked(self, *_) -> None:
        if hasattr(self.notification, "close"):
            self.notification.close("dismissed-by-user")

    # Таймаут
    def start_timeout(self) -> None:
        self.stop_timeout()
        if self.timeout_ms > 0:
            self._timeout_id = GLib.timeout_add(
                self.timeout_ms, self._close_notification
            )

    def stop_timeout(self) -> None:
        if self._timeout_id is not None:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = None

    def _close_notification(self) -> bool:
        if not self._destroyed and hasattr(self.notification, "close"):
            self.notification.close("expired")
        self.stop_timeout()
        return GLib.SOURCE_REMOVE

    # Ховер
    def set_is_history(self, value: bool) -> None:
        self._is_history = value

    def set_container(self, container) -> None:
        self._container_ref = weakref.ref(container) if container else None

    def get_container(self):
        return self._container_ref() if self._container_ref else None

    def _on_hover_enter(self, *_) -> None:
        cont = self.get_container()
        if cont and hasattr(cont, "pause_and_reset_all_timeouts"):
            cont.pause_and_reset_all_timeouts()

    def _on_hover_leave(self, *_) -> None:
        cont = self.get_container()
        if cont and hasattr(cont, "resume_all_timeouts"):
            cont.resume_all_timeouts()

    def hover_button(self) -> None:
        self._on_hover_enter()

    def unhover_button(self) -> None:
        self._on_hover_leave()

    # Уничтожение
    def destroy(self, from_history_delete: bool = False) -> None:
        if self._destroyed:
            return
        self._destroyed = True

        for hid in self._hover_handlers:
            try:
                if self.handler_is_connected(hid):
                    self.disconnect(hid)
            except Exception:
                pass
        self._hover_handlers = ()

        for btn in self._action_buttons:
            try:
                if btn.get_parent():
                    btn.destroy()
            except Exception:
                pass
        self._action_buttons.clear()

        self.stop_timeout()
        self.notification = None
        self._container_ref = None
        self._close_btn = None

        for child in list(self.get_children()):
            try:
                self.remove(child)
                child.destroy()
            except Exception:
                pass

        super().destroy()


# Группа уведомлений
class NotificationGroup(Box):
    __slots__ = (
        "app_name", "notification_ids", "is_expanded", "_history_ref",
        "header_row", "header", "count_label", "expand_icon", "clear_btn",
        "first_container_box", "stack_indicators_revealer",
        "stack_indicator_1", "stack_indicator_2",
        "stacked_revealer", "stacked_container",
        "latest_arrival_time", "_expand_handler", "_clear_handler",
        "_is_destroyed",
    )

    def __init__(self, app_name: str, history, is_expanded: bool = False):
        super().__init__(
            name="notification-group",
            orientation="v",
            h_align="fill",
            h_expand=True,
        )
        self.set_size_request(NOTIFICATION_WIDTH, -1)
        self.app_name = app_name[:30]
        self._history_ref = weakref.ref(history)
        self.notification_ids: list = []
        self.is_expanded = is_expanded
        self.latest_arrival_time = None
        self._is_destroyed = False
        self._expand_handler = None
        self._clear_handler = None
        self._build_ui()

    def _build_ui(self) -> None:
        self.expand_icon = Label(
            name="group-expand-icon",
            markup=icons.chevron_up if self.is_expanded else icons.chevron_down,
        )
        self.expand_icon.set_no_show_all(True)

        self.count_label = Label(name="group-count-label", label="", h_align="end")
        self.count_label.set_no_show_all(True)

        app_label = Label(
            name="group-app-name",
            label=self.app_name,
            h_align="start",
            h_expand=True,
            ellipsization="end",
            max_chars_width=20,
        )
        self.header = Button(
            name="group-expand-button",
            h_expand=True,
            child=Box(
                name="group-header-content",
                spacing=8,
                h_expand=True,
                children=[self.expand_icon, app_label, self.count_label],
            ),
        )
        self._expand_handler = self.header.connect("clicked", self._toggle_expand)
        set_pointer_cursor(self.header)

        self.clear_btn = Button(
            name="notif-close-button",
            child=Label(name="notif-close-label", markup=icons.cancel),
        )
        self._clear_handler = self.clear_btn.connect("clicked", self._on_clear_group)
        set_pointer_cursor(self.clear_btn)

        self.header_row = Box(
            name="notification-group-header",
            orientation="h",
            spacing=4,
            h_expand=True,
            children=[self.header, self.clear_btn],
        )
        self.header_row.set_visible(False)

        self.first_container_box = Box(
            name="group-first-notification", orientation="v", h_expand=True
        )

        self.stack_indicator_1 = Box(name="stack-indicator")
        self.stack_indicator_1.add_style_class("first")
        self.stack_indicator_1.set_no_show_all(True)

        self.stack_indicator_2 = Box(name="stack-indicator")
        self.stack_indicator_2.add_style_class("second")
        self.stack_indicator_2.set_no_show_all(True)

        self.stack_indicators_revealer = Revealer(
            name="stack-indicators-revealer",
            transition_type="slide-down",
            transition_duration=GROUP_ANIMATION_DURATION,
            child=Box(
                name="stack-indicators",
                orientation="v",
                children=[self.stack_indicator_1, self.stack_indicator_2],
            ),
            reveal_child=False,
        )

        self.stacked_container = Box(
            name="group-stacked-container",
            orientation="v",
            spacing=4,
            h_expand=True,
        )
        self.stacked_revealer = Revealer(
            name="group-stacked-revealer",
            transition_type="slide-down",
            transition_duration=GROUP_ANIMATION_DURATION,
            child=self.stacked_container,
            reveal_child=self.is_expanded,
        )

        for w in (
            self.header_row,
            self.first_container_box,
            self.stack_indicators_revealer,
            self.stacked_revealer,
        ):
            self.add(w)

        if self.is_expanded:
            self._apply_expanded_state()

    def _apply_expanded_state(self) -> None:
        self.expand_icon.set_markup(icons.chevron_up)
        self.header_row.add_style_class("expanded")
        self.add_style_class("expanded")

    def _apply_collapsed_state(self) -> None:
        self.expand_icon.set_markup(icons.chevron_down)
        self.header_row.remove_style_class("expanded")
        self.remove_style_class("expanded")

    def _toggle_expand(self, *_) -> None:
        if self._is_destroyed or len(self.notification_ids) <= 1:
            return
        self.is_expanded = not self.is_expanded
        self.stack_indicators_revealer.set_reveal_child(not self.is_expanded)
        self.stacked_revealer.set_reveal_child(self.is_expanded)
        if self.is_expanded:
            self._apply_expanded_state()
        else:
            self._apply_collapsed_state()

    def _on_clear_group(self, *_) -> None:
        if self._is_destroyed:
            return
        self.clear_btn.set_sensitive(False)
        history = self._history_ref()
        if history and not getattr(history, "_is_destroyed", True):
            history.clear_history_for_app(self.app_name)

    def update_display(self, containers_by_id: dict) -> None:
        if self._is_destroyed or not self.notification_ids:
            return

        valid = sorted(
            [containers_by_id[nid] for nid in self.notification_ids
             if nid in containers_by_id],
            key=lambda c: c.arrival_time,
            reverse=True,
        )
        if not valid:
            return

        for i, container in enumerate(valid):
            target = self.first_container_box if i == 0 else self.stacked_container
            parent = container.get_parent()
            if parent is target:
                continue
            if parent is not None:
                parent.remove(container)
            target.add(container)

        count = len(valid)
        is_multi = count > 1
        self.header_row.set_visible(True)
        self.count_label.set_label(f"+{count - 1}" if is_multi else "")
        self.count_label.set_visible(is_multi)
        self.expand_icon.set_visible(is_multi)
        self.header.set_can_focus(is_multi)

        if not is_multi:
            self.is_expanded = False
            self._apply_collapsed_state()

        self.stacked_revealer.set_reveal_child(self.is_expanded and is_multi)
        self.stack_indicator_1.set_visible(count > 1)
        self.stack_indicator_2.set_visible(count > 2)
        self.stack_indicators_revealer.set_reveal_child(count > 1 and not self.is_expanded)

        self.latest_arrival_time = valid[0].arrival_time
        self.first_container_box.show_all()
        self.stacked_container.show_all()

    def add_notification_id(self, nid, arrival_time) -> None:
        if nid not in self.notification_ids:
            self.notification_ids.insert(0, nid)
        if self.latest_arrival_time is None or arrival_time > self.latest_arrival_time:
            self.latest_arrival_time = arrival_time

    def remove_notification_id(self, nid) -> bool:
        if nid in self.notification_ids:
            self.notification_ids.remove(nid)
        return len(self.notification_ids) == 0

    def get_notification_count(self) -> int:
        return len(self.notification_ids)

    def clear_containers(self) -> None:
        for box in (self.first_container_box, self.stacked_container):
            for child in list(box.get_children()):
                box.remove(child)

    def destroy(self) -> None:
        if self._is_destroyed:
            return
        self._is_destroyed = True
        try:
            if self._expand_handler and self.header.handler_is_connected(self._expand_handler):
                self.header.disconnect(self._expand_handler)
        except Exception:
            pass
        self._expand_handler = None

        try:
            if self._clear_handler and self.clear_btn.handler_is_connected(self._clear_handler):
                self.clear_btn.disconnect(self._clear_handler)
        except Exception:
            pass
        self._clear_handler = None

        self.clear_containers()
        self.notification_ids.clear()
        self._history_ref = None
        super().destroy()