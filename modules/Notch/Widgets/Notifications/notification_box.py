from fabric.notifications.service import Notification, NotificationAction
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label

from gi.repository import GdkPixbuf, GLib, Gtk

import modules.icons as icons
from widgets.image import CustomImage


PERSISTENT_DIR = "/tmp/vidgex-shell/notifications"
PERSISTENT_HISTORY_FILE = PERSISTENT_DIR + "/notification_history.json"

MAX_NOTIFICATION_HISTORY = 20
MAX_CACHED_IMAGES = 10
MAX_POPUP_NOTIFICATIONS = 5

_cache_dir_exists = False


def get_limited_apps_history():
    return []


def get_history_ignored_apps():
    return []


def ensure_cache_dir():
    global _cache_dir_exists
    if not _cache_dir_exists:
        GLib.mkdir_with_parents(PERSISTENT_DIR, 0o700)
        _cache_dir_exists = True


def cache_notification_pixbuf(notification_box):
    notification = notification_box.notification
    pixbuf = notification.image_pixbuf if notification else None
    if not pixbuf:
        return None

    ensure_cache_dir()
    cache_file = f"{PERSISTENT_DIR}/notification_{notification_box.uuid}.png"

    try:
        pixbuf.scale_simple(48, 48, GdkPixbuf.InterpType.BILINEAR).savev(cache_file, "png", [], [])
        return cache_file
    except Exception:
        return None


def get_app_icon_pixbuf(icon_path, width, height):
    if not icon_path:
        return None

    if icon_path.startswith("file://"):
        icon_path = icon_path[7:]

    if not GLib.file_test(icon_path, GLib.FileTest.EXISTS):
        return None

    try:
        return GdkPixbuf.Pixbuf.new_from_file(icon_path).scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
    except Exception:
        return None


def load_scaled_pixbuf(notification_box, width, height):
    notification = getattr(notification_box, "notification", None)
    if not notification:
        return None

    cached_path = getattr(notification_box, "cached_image_path", None)
    if cached_path and GLib.file_test(cached_path, GLib.FileTest.EXISTS):
        try:
            return GdkPixbuf.Pixbuf.new_from_file(cached_path).scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
        except Exception:
            pass

    if notification.image_pixbuf:
        return notification.image_pixbuf.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)

    return get_app_icon_pixbuf(notification.app_icon, width, height)


class ActionButton(Button):
    __slots__ = ('action', 'notification_box', '_handlers')

    _style_classes = ("start-action", "end-action", "middle-action")

    def __init__(self, action: NotificationAction, index: int, total: int, notification_box):
        self.action = action
        self.notification_box = notification_box

        super().__init__(
            name="action-button",
            h_expand=True,
            on_clicked=self._on_clicked,
            child=Label(
                name="button-label", h_expand=True, h_align="fill",
                ellipsization="end", max_chars_width=1, label=action.label,
            ),
        )

        style_idx = 0 if index == 0 else (1 if index == total - 1 else 2)
        self.add_style_class(self._style_classes[style_idx])

        self._handlers = (
            self.connect("enter-notify-event", self._on_enter),
            self.connect("leave-notify-event", self._on_leave),
        )

    def _on_enter(self, *_):
        if self.notification_box:
            self.notification_box.hover_button(self)

    def _on_leave(self, *_):
        if self.notification_box:
            self.notification_box.unhover_button(self)

    def _on_clicked(self, *_):
        self.action.invoke()
        self.action.parent.close("dismissed-by-user")

    def destroy(self):
        for hid in self._handlers:
            try:
                self.disconnect(hid)
            except Exception:
                pass

        self.action = None
        self.notification_box = None
        super().destroy()


class NotificationBox(Box):
    __slots__ = (
        'notification', 'uuid', 'timeout_ms', '_timeout_id', '_container',
        'cached_image_path', '_destroyed', '_is_history', '_hover_handlers', '_action_buttons'
    )

    def __init__(self, notification: Notification, timeout_ms=5000, **kwargs):
        super().__init__(
            name="notification-box", orientation="v",
            h_align="fill", h_expand=True,
        )

        self.notification = notification
        self.uuid = GLib.uuid_string_random()
        self._timeout_id = None
        self._container = None
        self.cached_image_path = None
        self._destroyed = False
        self._is_history = False
        self._action_buttons = []

        live_timeout = getattr(notification, "timeout", -1)
        self.timeout_ms = 0 if timeout_ms == 0 else (live_timeout if live_timeout != -1 else timeout_ms)

        if self.timeout_ms > 0:
            self.start_timeout()

        if notification.image_pixbuf:
            self.cached_image_path = cache_notification_pixbuf(self)

        self.add(self._create_content())

        if (actions := self._create_action_buttons()):
            self.add(actions)

        self._hover_handlers = (
            self.connect("enter-notify-event", self._on_hover_enter),
            self.connect("leave-notify-event", self._on_hover_leave),
        )

    def set_is_history(self, value: bool):
        self._is_history = value

    def set_container(self, container):
        self._container = container

    def get_container(self):
        return self._container

    def _create_close_button(self):
        btn = Button(
            name="notif-close-button",
            child=Label(name="notif-close-label", markup=icons.cancel),
        )

        def on_close(*_):
            if self.notification:
                self.notification.close("dismissed-by-user")

        def on_enter(*_):
            self.hover_button(btn)

        def on_leave(*_):
            self.unhover_button(btn)

        btn.connect("clicked", on_close)
        btn.connect("enter-notify-event", on_enter)
        btn.connect("leave-notify-event", on_leave)

        return btn

    def _create_content(self):
        notif = self.notification

        image_box = Box(
            name="notification-image", orientation="v",
            children=[CustomImage(pixbuf=load_scaled_pixbuf(self, 48, 48)), Box(v_expand=True)],
        )

        summary = Label(
            name="notification-summary", markup=notif.summary,
            h_align="start", max_chars_width=16, ellipsization="end",
        )

        app_name = Label(
            name="notification-app-name", markup=notif.app_name,
            h_align="start", max_chars_width=16, ellipsization="end",
        )

        if notif.body:
            body = Label(markup=notif.body, h_align="start", max_chars_width=34, ellipsization="end")
            body.set_single_line_mode(True)
        else:
            body = Box()

        text_box = Box(
            name="notification-text", orientation="v", v_align="center", h_expand=True,
            children=[
                Box(name="notification-summary-box", orientation="h", children=[summary, Box(name="notif-sep"), app_name]),
                body,
            ],
        )

        return Box(
            name="notification-content", spacing=8,
            children=[image_box, text_box, Box(orientation="v", children=[self._create_close_button()])],
        )

    def _create_action_buttons(self):
        actions = self.notification.actions
        if not actions:
            return None

        grid = Gtk.Grid(column_homogeneous=True, column_spacing=4)
        total = len(actions)

        for i, action in enumerate(actions):
            btn = ActionButton(action, i, total, self)
            self._action_buttons.append(btn)
            grid.attach(btn, i, 0, 1, 1)

        return grid

    def start_timeout(self):
        self.stop_timeout()
        self._timeout_id = GLib.timeout_add(self.timeout_ms, self._close_notification)

    def stop_timeout(self):
        if self._timeout_id is not None:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = None

    def _close_notification(self):
        if not self._destroyed:
            self.notification.close("expired")
            self.stop_timeout()
        return False

    def _on_hover_enter(self, *_):
        if self._container:
            self._container.pause_and_reset_all_timeouts()

    def _on_hover_leave(self, *_):
        if self._container:
            self._container.resume_all_timeouts()

    def hover_button(self, *_):
        if self._container:
            self._container.pause_and_reset_all_timeouts()

    def unhover_button(self, *_):
        if self._container:
            self._container.resume_all_timeouts()

    def destroy(self, from_history_delete=False):
        if self._destroyed:
            return

        for hid in self._hover_handlers:
            try:
                self.disconnect(hid)
            except Exception:
                pass

        for btn in self._action_buttons:
            try:
                btn.destroy()
            except Exception:
                pass
        self._action_buttons.clear()

        if self.cached_image_path and GLib.file_test(self.cached_image_path, GLib.FileTest.EXISTS):
            if not self._is_history or from_history_delete:
                try:
                    GLib.unlink(self.cached_image_path)
                except Exception:
                    pass

        self.stop_timeout()
        self._destroyed = True
        self.notification = None
        self._container = None

        for child in self.get_children():
            try:
                self.remove(child)
                child.destroy()
            except Exception:
                pass

        super().destroy()


class HistoricalNotification:
    __slots__ = ('id', 'app_icon', 'summary', 'body', 'app_name', 'timestamp', 'cached_image_path', 'image_pixbuf', 'actions')

    def __init__(self, id, app_icon, summary, body, app_name, timestamp, cached_image_path=None):
        self.id = id
        self.app_icon = app_icon
        self.summary = summary
        self.body = body
        self.app_name = app_name
        self.timestamp = timestamp
        self.cached_image_path = cached_image_path
        self.image_pixbuf = None
        self.actions = []