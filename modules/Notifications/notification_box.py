import os
import weakref
import shutil
from threading import Thread
from queue import Queue

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer

from gi.repository import GdkPixbuf, GLib, Gtk

import services.icons as icons
from services.image import CustomImage


PERSISTENT_DIR = f"{GLib.get_user_cache_dir()}/vidgex-shell/notifications"
PERSISTENT_HISTORY_FILE = PERSISTENT_DIR + "/notification_history.json"
PERSISTENT_IMAGES_DIR = PERSISTENT_DIR + "/images"
MAX_NOTIFICATION_HISTORY = 50
MAX_POPUP_NOTIFICATIONS = 5
NOTIFICATION_WIDTH = 320
GROUP_ANIMATION_DURATION = 200

_history_ignored_apps = frozenset()
def get_history_ignored_apps(): return _history_ignored_apps

class _IOWorker:
    __slots__ = ('_queue', '_thread')
    def __init__(self):
        self._queue = Queue()
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()
    
    def _run(self):
        while True:
            task = self._queue.get(block=True)
            if task is None: break
            try: task()
            except Exception: pass
            finally: self._queue.task_done()
    
    def submit(self, task): self._queue.put(task)

_io_worker = _IOWorker()
def submit_io_task(task): _io_worker.submit(task)

def create_micro_thumbnail(notification, uuid):
    pixbuf = getattr(notification, 'image_pixbuf', None)
    icon_path = getattr(notification, 'app_icon', None)
    
    image_path = os.path.join(PERSISTENT_IMAGES_DIR, f"{uuid}.png")
    
    def process_and_save():
        os.makedirs(PERSISTENT_IMAGES_DIR, exist_ok=True)
        pb = None
        try:
            if pixbuf:
                pb = pixbuf.scale_simple(48, 48, GdkPixbuf.InterpType.BILINEAR)
            elif icon_path:
                path = icon_path[7:] if icon_path.startswith("file://") else icon_path
                if os.path.exists(path):
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 48, 48, False)
            
            if pb:
                pb.savev(image_path, "png", [], [])
        except Exception:
            pass
        finally:
            if pb: del pb # Очистка С-памяти в фоне

    _io_worker.submit(process_and_save)
    return image_path

def delete_notification_image(uuid):
    def do_delete():
        try:
            p = os.path.join(PERSISTENT_IMAGES_DIR, f"{uuid}.png")
            if os.path.exists(p): os.remove(p)
        except OSError: pass
    _io_worker.submit(do_delete)

def clear_all_notification_images():
    def do_clear():
        try:
            if os.path.exists(PERSISTENT_IMAGES_DIR): shutil.rmtree(PERSISTENT_IMAGES_DIR, ignore_errors=True)
        except Exception: pass
    _io_worker.submit(do_clear)

class ActionButton(Button):
    __slots__ = ('action', '_nb_ref', '_handlers')

    def __init__(self, action, index, total, notification_box):
        self.action = action
        self._nb_ref = weakref.ref(notification_box)
        super().__init__(
            name="action-button", h_expand=True, on_clicked=self._on_clicked,
            child=Label(name="button-label", h_expand=True, h_align="fill", ellipsization="end", max_chars_width=1, label=str(action.label)[:20]),
        )
        if index == 0: self.add_style_class("start-action")
        elif index == total - 1: self.add_style_class("end-action")
        else: self.add_style_class("middle-action")
        self._handlers = (self.connect("enter-notify-event", self._on_enter), self.connect("leave-notify-event", self._on_leave))

    def _on_enter(self, *_):
        nb = self._nb_ref()
        if nb and not getattr(nb, '_destroyed', True): nb.hover_button(self)
    def _on_leave(self, *_):
        nb = self._nb_ref()
        if nb and not getattr(nb, '_destroyed', True): nb.unhover_button(self)
    def _on_clicked(self, *_):
        if self.action:
            self.action.invoke()
            if getattr(self.action, 'parent', None): self.action.parent.close("dismissed-by-user")

    def destroy(self):
        for hid in self._handlers:
            try: self.disconnect(hid)
            except Exception: pass
        self.action = None
        self._nb_ref = None
        super().destroy()

class NotificationBox(Box):
    __slots__ = (
        'notification', 'uuid', 'timeout_ms', '_timeout_id', '_container_ref',
        '_destroyed', '_is_history', '_hover_handlers', '_action_buttons', '_thumb_path'
    )

    def __init__(self, notification, timeout_ms=5000, **kwargs):
        super().__init__(name="notification-box", orientation="v", h_align="fill", h_expand=True)
        self.notification = notification
        self.uuid = getattr(notification, 'id', None) or GLib.uuid_string_random()
        self._timeout_id = None
        self._container_ref = None
        self._destroyed = False
        self._is_history = False
        self._action_buttons = []
        
        # Фон генерирует и сохраняет легкий PNG
        self._thumb_path = create_micro_thumbnail(notification, self.uuid) if not isinstance(notification, HistoricalNotification) else getattr(notification, 'app_icon', None)

        live_timeout = getattr(notification, "timeout", -1)
        self.timeout_ms = 0 if timeout_ms == 0 else (live_timeout if live_timeout != -1 else timeout_ms)
        if self.timeout_ms > 0: self.start_timeout()

        self.add(self._create_content())
        actions = self._create_action_buttons()
        if actions: self.add(actions)

        self._hover_handlers = (self.connect("enter-notify-event", self._on_hover_enter), self.connect("leave-notify-event", self._on_hover_leave))

    def set_is_history(self, value): self._is_history = value
    def set_container(self, container): self._container_ref = weakref.ref(container) if container else None
    def get_container(self): return self._container_ref() if self._container_ref else None

    def _create_content(self):
        notif = self.notification
        image_box = Box(name="notification-image", orientation="v")
        
        pb = None
        is_live = not isinstance(notif, HistoricalNotification)

        # 1. Для МОМЕНТАЛЬНОГО попапа берем картинку из ОЗУ
        if is_live and getattr(notif, 'image_pixbuf', None):
            pb = notif.image_pixbuf.scale_simple(48, 48, GdkPixbuf.InterpType.BILINEAR)
        # 2. Для ИСТОРИИ (или если ОЗУ-картинки нет) берем легкий PNG с диска
        elif self._thumb_path and os.path.exists(self._thumb_path):
            try: pb = GdkPixbuf.Pixbuf.new_from_file(self._thumb_path)
            except Exception: pass
        # 3. Запасной вариант - иконка приложения
        elif is_live and getattr(notif, 'app_icon', None):
            path = notif.app_icon[7:] if notif.app_icon.startswith("file://") else notif.app_icon
            if os.path.exists(path):
                try: pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 48, 48, False)
                except Exception: pass

        if pb:
            # ВОЗВРАЩЕНО: CustomImage для сохранения ваших стилей верстки
            img = CustomImage(pixbuf=pb)
            img.set_valign(Gtk.Align.START)
            image_box.add(img)
            del pb # МГНОВЕННОЕ УНИЧТОЖЕНИЕ Pixbuf (Секрет низкого ОЗУ)
            
        image_box.add(Box(v_expand=True))

        summary_str = str(getattr(notif, 'summary', ''))[:80]
        app_name_str = str(getattr(notif, 'app_name', 'Unknown'))[:20]
        
        summary = Label(name="notification-summary", markup=summary_str, h_align="start", max_chars_width=20, ellipsization="end")
        app_name = Label(name="notification-app-name", markup=app_name_str, h_align="start", max_chars_width=12, ellipsization="end")
        text_children = [Box(name="notification-summary-box", orientation="h", children=[summary, Box(name="notif-sep"), app_name])]
        
        body_raw = getattr(notif, 'body', None)
        if body_raw:
            body_str = str(body_raw)[:150]
            body = Label(name="notification-body", markup=body_str, h_align="start", max_chars_width=40, ellipsization="end")
            body.set_single_line_mode(True)
            text_children.append(body)

        text_box = Box(name="notification-text", orientation="v", v_align="center", h_expand=True, children=text_children)

        close_btn = Button(name="notif-close-button", child=Label(name="notif-close-label", markup=icons.cancel))
        close_btn.connect("clicked", self._on_close_clicked)
        close_btn.connect("enter-notify-event", self._on_close_hover_enter)
        close_btn.connect("leave-notify-event", self._on_close_hover_leave)

        return Box(name="notification-content", spacing=8, h_expand=True, children=[
            image_box, text_box, Box(orientation="v", v_align="center", children=[close_btn])
        ])

    def _create_action_buttons(self):
        actions = getattr(self.notification, 'actions', None)
        if not actions: return None
        grid = Gtk.Grid(column_homogeneous=True, column_spacing=4)
        for i, action in enumerate(actions):
            btn = ActionButton(action, i, len(actions), self)
            self._action_buttons.append(btn)
            grid.attach(btn, i, 0, 1, 1)
        return grid

    def _on_close_clicked(self, *_):
        if self.notification and hasattr(self.notification, 'close'): self.notification.close("dismissed-by-user")

    def _on_close_hover_enter(self, btn, _): self.hover_button(btn)
    def _on_close_hover_leave(self, btn, _): self.unhover_button(btn)
    def start_timeout(self):
        self.stop_timeout()
        if self.timeout_ms > 0: self._timeout_id = GLib.timeout_add(self.timeout_ms, self._close_notification)
    def stop_timeout(self):
        if self._timeout_id is not None:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = None
    def _close_notification(self):
        if not self._destroyed and self.notification and hasattr(self.notification, 'close'): 
            self.notification.close("expired")
        self.stop_timeout()
        return GLib.SOURCE_REMOVE
    def _on_hover_enter(self, *_):
        cont = self.get_container()
        if cont and hasattr(cont, 'pause_and_reset_all_timeouts'): cont.pause_and_reset_all_timeouts()
    def _on_hover_leave(self, *_):
        cont = self.get_container()
        if cont and hasattr(cont, 'resume_all_timeouts'): cont.resume_all_timeouts()
    def hover_button(self, *_): self._on_hover_enter()
    def unhover_button(self, *_): self._on_hover_leave()

    def destroy(self, from_history_delete=False):
        if self._destroyed: return
        self._destroyed = True

        for hid in self._hover_handlers:
            try: self.disconnect(hid)
            except Exception: pass
        self._hover_handlers = ()

        for btn in self._action_buttons:
            try: btn.destroy()
            except Exception: pass
        self._action_buttons.clear()

        self.stop_timeout()
        self.notification = None
        self._container_ref = None

        for child in self.get_children():
            try:
                self.remove(child)
                child.destroy()
            except Exception: pass
        super().destroy()

class HistoricalNotification:
    __slots__ = ('id', 'app_icon', 'summary', 'body', 'app_name', 'timestamp', 'actions')
    def __init__(self, id, app_icon, summary, body, app_name, timestamp):
        self.id = id
        self.app_icon = app_icon
        self.summary = summary
        self.body = body
        self.app_name = app_name
        self.timestamp = timestamp
        self.actions = []

class NotificationGroup(Box):
    __slots__ = (
        'app_name', 'notification_ids', 'is_expanded', '_history_ref',
        'header_row', 'header', 'count_label', 'expand_icon', 'clear_btn',
        'first_container_box', 'stack_indicators_revealer',
        'stack_indicator_1', 'stack_indicator_2',
        'stacked_revealer', 'stacked_container',
        'latest_arrival_time', '_expand_handler', '_clear_handler', '_is_destroyed'
    )

    def __init__(self, app_name, history, is_expanded=False):
        super().__init__(name="notification-group", orientation="v", h_align="fill", h_expand=True)
        self.set_size_request(NOTIFICATION_WIDTH, -1)
        self.app_name = app_name[:30]
        self._history_ref = weakref.ref(history)
        self.notification_ids = []
        self.is_expanded = is_expanded
        self.latest_arrival_time = None
        self._is_destroyed = False
        self._expand_handler = None
        self._clear_handler = None
        self._build_ui()

    def _build_ui(self):
        self.expand_icon = Label(name="group-expand-icon", markup=icons.chevron_up if self.is_expanded else icons.chevron_down)
        self.expand_icon.set_no_show_all(True)
        self.count_label = Label(name="group-count-label", label="", h_align="end")
        self.count_label.set_no_show_all(True)
        
        app_label = Label(name="group-app-name", label=self.app_name, h_align="start", h_expand=True, ellipsization="end", max_chars_width=20)
        self.header = Button(name="group-expand-button", child=Box(name="group-header-content", spacing=8, h_expand=True, children=[self.expand_icon, app_label, self.count_label]), h_expand=True)
        self._expand_handler = self.header.connect("clicked", self._toggle_expand)

        self.clear_btn = Button(name="notif-close-button", child=Label(name="notif-close-label", markup=icons.cancel))
        self._clear_handler = self.clear_btn.connect("clicked", self._on_clear_group)

        self.header_row = Box(name="notification-group-header", orientation="h", spacing=4, h_expand=True, children=[self.header, self.clear_btn])
        self.header_row.set_visible(False)

        self.first_container_box = Box(name="group-first-notification", orientation="v", h_expand=True)
        self.stack_indicator_1 = Box(name="stack-indicator")
        self.stack_indicator_1.add_style_class("first")
        self.stack_indicator_1.set_no_show_all(True)
        self.stack_indicator_2 = Box(name="stack-indicator")
        self.stack_indicator_2.add_style_class("second")
        self.stack_indicator_2.set_no_show_all(True)

        self.stack_indicators_revealer = Revealer(
            name="stack-indicators-revealer", transition_type="slide-down", transition_duration=GROUP_ANIMATION_DURATION,
            child=Box(name="stack-indicators", orientation="v", children=[self.stack_indicator_1, self.stack_indicator_2]), reveal_child=False
        )
        self.stacked_container = Box(name="group-stacked-container", orientation="v", spacing=4, h_expand=True)
        self.stacked_revealer = Revealer(
            name="group-stacked-revealer", transition_type="slide-down", transition_duration=GROUP_ANIMATION_DURATION,
            child=self.stacked_container, reveal_child=self.is_expanded
        )

        self.add(self.header_row)
        self.add(self.first_container_box)
        self.add(self.stack_indicators_revealer)
        self.add(self.stacked_revealer)

        if self.is_expanded: self._apply_expanded_state()

    def _apply_expanded_state(self):
        self.expand_icon.set_markup(icons.chevron_up)
        self.header_row.add_style_class("expanded")
        self.add_style_class("expanded")

    def _apply_collapsed_state(self):
        self.expand_icon.set_markup(icons.chevron_down)
        self.header_row.remove_style_class("expanded")
        self.remove_style_class("expanded")

    def _toggle_expand(self, *_):
        if self._is_destroyed or len(self.notification_ids) <= 1: return
        self.is_expanded = not self.is_expanded
        self.stack_indicators_revealer.set_reveal_child(not self.is_expanded)
        self.stacked_revealer.set_reveal_child(self.is_expanded)
        if self.is_expanded: self._apply_expanded_state()
        else: self._apply_collapsed_state()

    def _on_clear_group(self, *_):
        if self._is_destroyed: return
        self.clear_btn.set_sensitive(False)
        history = self._history_ref()
        if history and not getattr(history, '_is_destroyed', False): GLib.idle_add(self._trigger_history_clear, history)

    def _trigger_history_clear(self, history):
        if hasattr(history, 'clear_history_for_app'): history.clear_history_for_app(self.app_name)
        return GLib.SOURCE_REMOVE

    def _update_stack_indicators(self, count):
        self.stack_indicator_1.set_visible(count > 1)
        self.stack_indicator_2.set_visible(count > 2)
        self.stack_indicators_revealer.set_reveal_child(count > 1 and not self.is_expanded)

    def update_display(self, containers_by_id):
        if self._is_destroyed: return
        self.clear_containers()
        if not self.notification_ids: return

        valid_containers = [c for nid in self.notification_ids if (c := containers_by_id.get(nid)) is not None]
        valid_containers.sort(key=lambda c: c.arrival_time, reverse=True)
        if not valid_containers: return

        for i, container in enumerate(valid_containers):
            parent = container.get_parent()
            if parent: parent.remove(container)
            (self.first_container_box if i == 0 else self.stacked_container).add(container)

        count = len(valid_containers)
        is_multi = count > 1
        self.header_row.set_visible(True)
        
        self.count_label.set_label(f"+{count - 1}" if is_multi else "")
        self.count_label.set_visible(is_multi)
        self.expand_icon.set_visible(is_multi)
        self.header.set_can_focus(is_multi)
        
        if not is_multi:
            self.is_expanded = False
            self._apply_collapsed_state()
            
        self.stacked_revealer.set_reveal_child(self.is_expanded if is_multi else False)
        self._update_stack_indicators(count)
        self.latest_arrival_time = valid_containers[0].arrival_time

    def add_notification_id(self, nid, arrival_time):
        if nid not in self.notification_ids: self.notification_ids.insert(0, nid)
        if self.latest_arrival_time is None or arrival_time > self.latest_arrival_time: self.latest_arrival_time = arrival_time
    def remove_notification_id(self, nid):
        if nid in self.notification_ids: self.notification_ids.remove(nid)
        return len(self.notification_ids) == 0

    def get_notification_count(self): return len(self.notification_ids)
    def collapse(self):
        if self.is_expanded: self._toggle_expand()

    def clear_containers(self):
        for box in (self.first_container_box, self.stacked_container):
            for child in box.get_children(): box.remove(child)

    def destroy(self):
        if self._is_destroyed: return
        self._is_destroyed = True
        
        if self._expand_handler and self.header:
            try: self.header.disconnect(self._expand_handler)
            except Exception: pass
            self._expand_handler = None

        if self._clear_handler and self.clear_btn:
            try: self.clear_btn.disconnect(self._clear_handler)
            except Exception: pass
            self._clear_handler = None

        self.clear_containers()
        self.notification_ids.clear()
        self._history_ref = None
        super().destroy()