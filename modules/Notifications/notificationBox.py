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

from gi.repository import GdkPixbuf, GLib, Gtk

import services.icons as icons
from modules.Notifications.NotificationBox.image import CustomImage

# ── Константы ──

CACHE_DIR = f"{GLib.get_user_cache_dir()}/vidgex-shell/notifications"
HISTORY_FILE = f"{CACHE_DIR}/notification_history.json"
IMAGES_DIR = f"{CACHE_DIR}/images"

MAX_HISTORY = 50
MAX_POPUPS = 5
NOTIF_WIDTH = 320
GROUP_ANIM_MS = 200
THUMB_SIZE = 48
MAX_IMG_BYTES = 10 * 1024 * 1024
MAX_IMG_DIM = 2048

_ignored_apps: frozenset = frozenset()


def get_ignored_apps():
    return _ignored_apps


# ── Работа с изображениями ──

def _safe_image_id(uid):
    return hashlib.md5(str(uid).encode()).hexdigest()


def image_path_for(uid):
    return os.path.join(IMAGES_DIR, f"{_safe_image_id(uid)}.png")


def is_valid_image(path):
    if not path or not os.path.isfile(path):
        return False
    try:
        size = os.path.getsize(path)
        if not (0 < size < MAX_IMG_BYTES):
            return False
        fmt, w, h = GdkPixbuf.Pixbuf.get_file_info(path)
        return fmt is not None and w <= MAX_IMG_DIM and h <= MAX_IMG_DIM
    except Exception:
        return False


def load_pixbuf(path, size=THUMB_SIZE):
    if not is_valid_image(path):
        return None
    try:
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size, size, False)
    except GLib.Error:
        return None


def scale_pixbuf(pb, size=THUMB_SIZE):
    try:
        return pb.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
    except Exception:
        return None


# ── Фоновый IO-воркер ──

class _IOWorker:
    def __init__(self):
        self._q: Queue = Queue()
        Thread(target=self._loop, daemon=True, name="notif-io").start()

    def _loop(self):
        while True:
            task = self._q.get()
            if task is None:
                break
            try:
                task()
            except Exception:
                import traceback
                traceback.print_exc()

    def submit(self, fn):
        self._q.put_nowait(fn)


_io = _IOWorker()


def _save_thumbnail(notification, uid, callback=None):
    dest = image_path_for(uid)
    png_data = None

    pb = getattr(notification, "image_pixbuf", None)
    if pb:
        scaled = scale_pixbuf(pb)
        if scaled:
            ok, data = scaled.save_to_bufferv("png", [], [])
            if ok and data:
                png_data = bytes(data)

    icon = getattr(notification, "app_icon", None) or ""
    if icon.startswith("file://"):
        icon = icon[7:]

    if not png_data and not is_valid_image(icon):
        return dest

    def _work():
        os.makedirs(IMAGES_DIR, exist_ok=True)
        saved = False

        if png_data:
            try:
                with open(dest, "wb") as f:
                    f.write(png_data)
                saved = True
            except OSError:
                pass

        if not saved and is_valid_image(icon):
            try:
                p = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    icon, THUMB_SIZE, THUMB_SIZE, False
                )
                if p:
                    p.savev(dest, "png", [], [])
                    saved = True
            except Exception:
                pass

        if saved and callback and os.path.exists(dest):
            GLib.idle_add(callback, dest, priority=GLib.PRIORITY_LOW)

    _io.submit(_work)
    return dest


def _delete_image(uid):
    _io.submit(lambda: _try_remove(image_path_for(uid)))


def _clear_all_images():
    def _do():
        if os.path.isdir(IMAGES_DIR):
            shutil.rmtree(IMAGES_DIR, ignore_errors=True)
    _io.submit(_do)


def _cleanup_orphans(active_ids):
    if not os.path.isdir(IMAGES_DIR):
        return
    valid = {f"{_safe_image_id(uid)}.png" for uid in active_ids}
    try:
        for f in os.listdir(IMAGES_DIR):
            if f.endswith(".png") and f not in valid:
                _try_remove(os.path.join(IMAGES_DIR, f))
    except OSError:
        pass


def _try_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


# ── Модель исторического уведомления ──

class HistoricalNotification:
    __slots__ = (
        "id", "app_icon", "summary", "body",
        "app_name", "timestamp", "actions",
    )

    def __init__(self, *, id, app_icon, summary, body, app_name, timestamp):
        self.id = id
        self.app_icon = app_icon
        self.summary = summary
        self.body = body
        self.app_name = app_name
        self.timestamp = timestamp
        self.actions = []


# ── Кнопка действия ──

class ActionButton(Button):
    def __init__(self, action, index, total, notif_box):
        self._action = action
        self._box_ref = weakref.ref(notif_box)

        super().__init__(
            name="action-button",
            h_expand=True,
            on_clicked=self._invoke,
            child=Label(
                name="button-label",
                h_expand=True,
                h_align="fill",
                ellipsization="end",
                max_chars_width=1,
                label=str(action.label)[:20],
            ),
        )

        style = (
            "start-action" if index == 0
            else ("end-action" if index == total - 1 else "middle-action")
        )
        self.add_style_class(style)

        self._h1 = self.connect(
            "enter-notify-event",
            lambda *_: self._relay("hover_button"),
        )
        self._h2 = self.connect(
            "leave-notify-event",
            lambda *_: self._relay("unhover_button"),
        )

    def _relay(self, method):
        box = self._box_ref()
        if box and not getattr(box, "_destroyed", True):
            getattr(box, method)()

    def _invoke(self, *_):
        if self._action:
            self._action.invoke()
            parent = getattr(self._action, "parent", None)
            if parent and hasattr(parent, "close"):
                try:
                    parent.close("dismissed-by-user")
                except TypeError:
                    parent.close()

    def destroy(self):
        for h in (self._h1, self._h2):
            if self.handler_is_connected(h):
                self.disconnect(h)
        self._action = None
        self._box_ref = None
        super().destroy()


# ── Виджет одного уведомления ──

class NotificationBox(Box):
    def __init__(self, notification, timeout_ms=5000):
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
        self._is_history = False
        self._actions: list[ActionButton] = []
        self.thumb_path = image_path_for(self.uuid)

        is_live = not isinstance(notification, HistoricalNotification)

        # Таймаут
        live_t = getattr(notification, "timeout", -1)
        if timeout_ms == 0:
            self.timeout_ms = 0
        elif live_t != -1:
            self.timeout_ms = live_t
        else:
            self.timeout_ms = timeout_ms

        if self.timeout_ms > 0:
            self.start_timeout()

        # UI
        self.add(self._build_content(is_live))
        actions_w = self._build_actions()
        if actions_w:
            self.add(actions_w)

        self._hh = (
            self.connect("enter-notify-event", lambda *_: self._pause_container()),
            self.connect("leave-notify-event", lambda *_: self._resume_container()),
        )

        if is_live:
            _save_thumbnail(
                notification,
                self.uuid,
                lambda p: (
                    setattr(self, "thumb_path", p)
                    if not self._destroyed
                    else None
                ),
            )

    # ── Контейнер ──

    def set_container(self, c):
        self._container_ref = weakref.ref(c) if c else None

    def _get_container(self):
        return self._container_ref() if self._container_ref else None

    def _pause_container(self):
        c = self._get_container()
        if c and hasattr(c, "pause_all"):
            c.pause_all()

    def _resume_container(self):
        c = self._get_container()
        if c and hasattr(c, "resume_all"):
            c.resume_all()

    def hover_button(self, *_):
        self._pause_container()

    def unhover_button(self, *_):
        self._resume_container()

    # ── Таймаут ──

    def start_timeout(self):
        self.stop_timeout()
        if self.timeout_ms > 0:
            self._timeout_id = GLib.timeout_add(self.timeout_ms, self._expire)

    def stop_timeout(self):
        if self._timeout_id:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = None

    def _expire(self):
        self._timeout_id = None
        if (
            not self._destroyed
            and self.notification
            and hasattr(self.notification, "close")
        ):
            try:
                self.notification.close("expired")
            except TypeError:
                self.notification.close()
        return GLib.SOURCE_REMOVE

    # ── UI ──

    def _build_content(self, is_live):
        notif = self.notification
        pb = self._get_pixbuf(notif, is_live)

        image_box = Box(name="notification-image", orientation="v")
        if pb:
            img = CustomImage(pixbuf=pb)
            img.set_valign(Gtk.Align.START)
            image_box.add(img)
        image_box.add(Box(v_expand=True))

        summary = Label(
            name="notification-summary",
            markup=str(getattr(notif, "summary", ""))[:80],
            h_align="start",
            max_chars_width=20,
            ellipsization="end",
        )
        app_name = Label(
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
                children=[summary, Box(name="notif-sep"), app_name],
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

        text_box = Box(
            name="notification-text",
            orientation="v",
            v_align="center",
            h_expand=True,
            children=text_children,
        )

        close_btn = Button(
            name="notif-close-button",
            child=Label(name="notif-close-label", markup=icons.cancel),
        )
        close_btn.connect("clicked", self._close_notif)
        close_btn.connect(
            "enter-notify-event", lambda *_: self._pause_container()
        )
        close_btn.connect(
            "leave-notify-event", lambda *_: self._resume_container()
        )

        return Box(
            name="notification-content",
            spacing=8,
            h_expand=True,
            children=[
                image_box,
                text_box,
                Box(orientation="v", v_align="center", children=[close_btn]),
            ],
        )

    def _get_pixbuf(self, notif, is_live):
        if is_live and getattr(notif, "image_pixbuf", None):
            return scale_pixbuf(notif.image_pixbuf)

        if not is_live:
            pb = load_pixbuf(self.thumb_path)
            if pb:
                return pb

        if is_live:
            icon = getattr(notif, "app_icon", "") or ""
            if icon.startswith("file://"):
                icon = icon[7:]
            return load_pixbuf(icon)

        return None

    def _build_actions(self):
        actions = getattr(self.notification, "actions", None)
        if not actions:
            return None
        grid = Gtk.Grid(column_homogeneous=True, column_spacing=4)
        for i, a in enumerate(actions):
            btn = ActionButton(a, i, len(actions), self)
            self._actions.append(btn)
            grid.attach(btn, i, 0, 1, 1)
        return grid

    def _close_notif(self, *_):
        if self.notification and hasattr(self.notification, "close"):
            try:
                self.notification.close("dismissed-by-user")
            except TypeError:
                self.notification.close()

    def set_is_history(self, v):
        self._is_history = v

    def destroy(self, from_history_delete=False):
        if self._destroyed:
            return
        self._destroyed = True

        for h in self._hh:
            if self.handler_is_connected(h):
                self.disconnect(h)
        self._hh = ()

        for btn in self._actions:
            if btn.get_parent():
                btn.destroy()
        self._actions.clear()

        self.stop_timeout()
        self.notification = None
        self._container_ref = None

        for ch in list(self.get_children()):
            self.remove(ch)
            ch.destroy()
        super().destroy()


# ── Группа уведомлений ──

class NotificationGroup(Box):
    def __init__(self, app_name, history, is_expanded=False):
        super().__init__(
            name="notification-group",
            orientation="v",
            h_align="fill",
            h_expand=True,
        )
        self.set_size_request(NOTIF_WIDTH, -1)

        self.app_name = app_name[:30]
        self._history_ref = weakref.ref(history)
        self.nids: list = []
        self.is_expanded = is_expanded
        self.latest_time = None
        self._dead = False

        self._build()

    def _build(self):
        self.expand_icon = Label(
            name="group-expand-icon",
            markup=icons.chevron_up if self.is_expanded else icons.chevron_down,
        )
        self.expand_icon.set_no_show_all(True)

        self.count_label = Label(
            name="group-count-label", label="", h_align="end"
        )
        self.count_label.set_no_show_all(True)

        app_lbl = Label(
            name="group-app-name",
            label=self.app_name,
            h_align="start",
            h_expand=True,
            ellipsization="end",
            max_chars_width=20,
        )

        self.header_btn = Button(
            name="group-expand-button",
            h_expand=True,
            child=Box(
                name="group-header-content",
                spacing=8,
                h_expand=True,
                children=[self.expand_icon, app_lbl, self.count_label],
            ),
        )
        self._eh = self.header_btn.connect("clicked", self._toggle)

        self.clear_btn = Button(
            name="notif-close-button",
            child=Label(name="notif-close-label", markup=icons.cancel),
        )
        self._ch = self.clear_btn.connect("clicked", self._on_clear)

        self.header_row = Box(
            name="notification-group-header",
            orientation="h",
            spacing=4,
            h_expand=True,
            children=[self.header_btn, self.clear_btn],
        )
        self.header_row.set_visible(False)

        self.first_box = Box(
            name="group-first-notification",
            orientation="v",
            h_expand=True,
        )

        self.ind1 = Box(name="stack-indicator")
        self.ind1.add_style_class("first")
        self.ind1.set_no_show_all(True)

        self.ind2 = Box(name="stack-indicator")
        self.ind2.add_style_class("second")
        self.ind2.set_no_show_all(True)

        self.ind_revealer = Revealer(
            name="stack-indicators-revealer",
            transition_type="slide-down",
            transition_duration=GROUP_ANIM_MS,
            reveal_child=False,
            child=Box(
                name="stack-indicators",
                orientation="v",
                children=[self.ind1, self.ind2],
            ),
        )

        self.stacked_box = Box(
            name="group-stacked-container",
            orientation="v",
            spacing=4,
            h_expand=True,
        )
        self.stacked_revealer = Revealer(
            name="group-stacked-revealer",
            transition_type="slide-down",
            transition_duration=GROUP_ANIM_MS,
            child=self.stacked_box,
            reveal_child=self.is_expanded,
        )

        for w in (
            self.header_row,
            self.first_box,
            self.ind_revealer,
            self.stacked_revealer,
        ):
            self.add(w)

        if self.is_expanded:
            self._set_expanded_style()

    def _set_expanded_style(self):
        self.expand_icon.set_markup(icons.chevron_up)
        self.header_row.add_style_class("expanded")
        self.add_style_class("expanded")

    def _set_collapsed_style(self):
        self.expand_icon.set_markup(icons.chevron_down)
        self.header_row.remove_style_class("expanded")
        self.remove_style_class("expanded")

    def _toggle(self, *_):
        if self._dead or len(self.nids) <= 1:
            return
        self.is_expanded = not self.is_expanded
        self.ind_revealer.set_reveal_child(not self.is_expanded)
        self.stacked_revealer.set_reveal_child(self.is_expanded)
        if self.is_expanded:
            self._set_expanded_style()
        else:
            self._set_collapsed_style()

    def _on_clear(self, *_):
        if self._dead:
            return
        self.clear_btn.set_sensitive(False)
        h = self._history_ref()
        if h and not getattr(h, "_dead", True):
            h.clear_app(self.app_name)

    def add_nid(self, nid, time):
        if nid not in self.nids:
            self.nids.insert(0, nid)
        if self.latest_time is None or time > self.latest_time:
            self.latest_time = time

    def remove_nid(self, nid):
        if nid in self.nids:
            self.nids.remove(nid)
        return len(self.nids) == 0

    def refresh(self, id_map):
        if self._dead or not self.nids:
            return

        for box in (self.first_box, self.stacked_box):
            for ch in list(box.get_children()):
                box.remove(ch)

        visible = sorted(
            (id_map[nid] for nid in self.nids if nid in id_map),
            key=lambda c: c.arrival_time,
            reverse=True,
        )
        if not visible:
            return

        for i, c in enumerate(visible):
            target = self.first_box if i == 0 else self.stacked_box
            p = c.get_parent()
            if p and p is not target:
                p.remove(c)
            if c.get_parent() is None:
                target.add(c)

        count = len(visible)
        multi = count > 1
        self.header_row.set_visible(True)
        self.count_label.set_label(f"+{count - 1}" if multi else "")
        self.count_label.set_visible(multi)
        self.expand_icon.set_visible(multi)
        self.header_btn.set_can_focus(multi)

        if not multi:
            self.is_expanded = False
            self._set_collapsed_style()

        self.stacked_revealer.set_reveal_child(
            self.is_expanded if multi else False
        )
        self.ind1.set_visible(count > 1)
        self.ind2.set_visible(count > 2)
        self.ind_revealer.set_reveal_child(count > 1 and not self.is_expanded)
        self.latest_time = visible[0].arrival_time

        self.first_box.show_all()
        self.stacked_box.show_all()

    def detach_all(self):
        for box in (self.first_box, self.stacked_box):
            for ch in list(box.get_children()):
                box.remove(ch)

    def destroy(self):
        if self._dead:
            return
        self._dead = True

        for handler, widget in (
            (self._eh, self.header_btn),
            (self._ch, self.clear_btn),
        ):
            if handler and widget and widget.handler_is_connected(handler):
                widget.disconnect(handler)

        self.detach_all()
        self.nids.clear()
        self._history_ref = None
        super().destroy()