from fabric.notifications.service import Notification, NotificationAction
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label

from gi.repository import GdkPixbuf, GLib, Gtk

import os
import uuid
import weakref

import modules.icons as icons
from widgets.image import CustomImage


PERSISTENT_DIR = "/tmp/vidgex-shell/notifications"
PERSISTENT_HISTORY_FILE = os.path.join(PERSISTENT_DIR, "notification_history.json")

MAX_NOTIFICATION_HISTORY = 10
MAX_CACHED_IMAGES = 10
MAX_POPUP_NOTIFICATIONS = 3


def get_limited_apps_history():
    return []


def get_history_ignored_apps():
    return []


def ensure_cache_dir():
    os.makedirs(PERSISTENT_DIR, exist_ok=True)


def cache_notification_pixbuf(notification_box):
    notification = notification_box.notification
    pixbuf = notification.image_pixbuf if notification else None
    if not pixbuf:
        return None

    ensure_cache_dir()
    cache_file = os.path.join(
        PERSISTENT_DIR,
        f"notification_{notification_box.uuid}.png",
    )

    try:
        scaled = pixbuf.scale_simple(48, 48, GdkPixbuf.InterpType.BILINEAR)
        scaled.savev(cache_file, "png", [], [])
        return cache_file
    except Exception:
        return None


def get_app_icon_pixbuf(icon_path, width, height):
    if not icon_path:
        return None

    if icon_path.startswith("file://"):
        icon_path = icon_path[7:]

    if not os.path.exists(icon_path):
        return None

    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(icon_path)
        return pixbuf.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
    except Exception:
        return None


def load_scaled_pixbuf(notification_box, width, height):
    notification = getattr(notification_box, "notification", None)
    if not notification:
        return None

    cached_path = getattr(notification_box, "cached_image_path", None)
    if cached_path and os.path.exists(cached_path):
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(cached_path)
            return pixbuf.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
        except Exception:
            pass

    if notification.image_pixbuf:
        return notification.image_pixbuf.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)

    return get_app_icon_pixbuf(notification.app_icon, width, height)


class ActionButton(Button):
    def __init__(self, action: NotificationAction, index: int, total: int, notification_box):
        self.action = action
        self.notification_box_ref = weakref.ref(notification_box)
        self._signal_handlers = []

        label = Label(
            name="button-label",
            h_expand=True,
            h_align="fill",
            ellipsization="end",
            max_chars_width=1,
            label=action.label,
        )

        super().__init__(
            name="action-button",
            h_expand=True,
            on_clicked=self.on_clicked,
            child=label,
        )

        if index == 0: self.add_style_class("start-action")
        elif index == total - 1: self.add_style_class("end-action")
        else: self.add_style_class("middle-action")

        self._signal_handlers.extend([
            self.connect("enter-notify-event", self._on_enter_notify),
            self.connect("leave-notify-event", self._on_leave_notify),
        ])

    def _on_enter_notify(self, *_):
        box = self.notification_box_ref()
        if box:
            box.hover_button(self)

    def _on_leave_notify(self, *_):
        box = self.notification_box_ref()
        if box:
            box.unhover_button(self)

    def on_clicked(self, *_):
        self.action.invoke()
        self.action.parent.close("dismissed-by-user")

    def destroy(self):
        for hid in self._signal_handlers:
            try: self.disconnect(hid)
            except Exception: pass
        self._signal_handlers.clear()

        self.action = None
        self.notification_box_ref = None
        super().destroy()


class NotificationBox(Box):
    def __init__(self, notification: Notification, timeout_ms=5000, **kwargs):
        super().__init__(
            name="notification-box",
            orientation="v",
            h_align="fill",
            h_expand=True,
            children=[],
        )

        self.notification = notification
        self.uuid = str(uuid.uuid4())

        live_timeout = getattr(notification, "timeout", -1)
        self.timeout_ms = 0 if timeout_ms == 0 else (
            live_timeout if live_timeout != -1 else timeout_ms
        )

        self._timeout_id = None
        self._container = None
        self.cached_image_path = None
        self._destroyed = False
        self._is_history = False

        if self.timeout_ms > 0:
            self.start_timeout()

        if notification.image_pixbuf:
            self.cached_image_path = cache_notification_pixbuf(self)

        self.add(self.create_content())

        actions = self.create_action_buttons()
        if actions:
            self.add(actions)

        self._hover_enter_handler = self.connect("enter-notify-event", self.on_hover_enter)
        self._hover_leave_handler = self.connect("leave-notify-event", self.on_hover_leave)

    def set_is_history(self, value: bool):
        self._is_history = value

    def set_container(self, container):
        self._container = container

    def get_container(self):
        return self._container

    def create_close_button(self):
        button = Button(
            name="notif-close-button",
            child=Label(name="notif-close-label", markup=icons.cancel),
        )

        self_ref = weakref.ref(self)

        def on_click(*_):
            notif = self_ref()
            if notif and notif.notification:
                notif.notification.close("dismissed-by-user")

        def on_enter(*_):
            notif = self_ref()
            if notif:
                notif.hover_button(button)

        def on_leave(*_):
            notif = self_ref()
            if notif:
                notif.unhover_button(button)

        button.connect("clicked", on_click)
        button.connect("enter-notify-event", on_enter)
        button.connect("leave-notify-event", on_leave)

        return button

    def create_content(self):
        pixbuf = load_scaled_pixbuf(self, 48, 48)

        image_box = Box(
            name="notification-image",
            orientation="v",
            children=[CustomImage(pixbuf=pixbuf), Box(v_expand=True)],
        )

        summary = Label(
            name="notification-summary",
            markup=self.notification.summary,
            h_align="start",
            max_chars_width=16,
            ellipsization="end",
        )

        app_name = Label(
            name="notification-app-name",
            markup=self.notification.app_name,
            h_align="start",
            max_chars_width=16,
            ellipsization="end",
        )

        if self.notification.body:
            body = Label(
                markup=self.notification.body,
                h_align="start",
                max_chars_width=34,
                ellipsization="end",
            )
            body.set_single_line_mode(True)
        else:
            body = Box()

        text_box = Box(
            name="notification-text",
            orientation="v",
            v_align="center",
            h_expand=True,
            children=[
                Box(
                    name="notification-summary-box",
                    orientation="h",
                    children=[
                        summary,
                        Box(name="notif-sep"),
                        app_name,
                    ],
                ),
                body,
            ],
        )

        close_box = Box(orientation="v", children=[self.create_close_button()])

        return Box(
            name="notification-content",
            spacing=8,
            children=[image_box, text_box, close_box],
        )

    def create_action_buttons(self):
        actions = self.notification.actions
        if not actions:
            return None

        grid = Gtk.Grid(column_homogeneous=True, column_spacing=4)
        self._action_buttons = []

        for i, action in enumerate(actions):
            btn = ActionButton(action, i, len(actions), self)
            self._action_buttons.append(btn)
            grid.attach(btn, i, 0, 1, 1)

        return grid

    def start_timeout(self):
        self.stop_timeout()
        self._timeout_id = GLib.timeout_add(
            self.timeout_ms, self.close_notification
        )

    def stop_timeout(self):
        if self._timeout_id is not None:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = None

    def close_notification(self):
        if not self._destroyed:
            self.notification.close("expired")
            self.stop_timeout()
        return False

    def on_hover_enter(self, *_):
        if self._container:
            self._container.pause_and_reset_all_timeouts()

    def on_hover_leave(self, *_):
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

        for hid in (self._hover_enter_handler, self._hover_leave_handler):
            try:
                self.disconnect(hid)
            except Exception:
                pass

        for btn in getattr(self, "_action_buttons", []):
            try: btn.destroy()
            except Exception: pass

        if self.cached_image_path and os.path.exists(self.cached_image_path):
            if not self._is_history or from_history_delete:
                try: os.remove(self.cached_image_path)
                except Exception: pass

        self.stop_timeout()
        self._destroyed = True
        self.notification = None
        self._container = None

        for child in list(self.get_children()):
            try:
                self.remove(child)
                child.destroy()
            except Exception:
                pass

        super().destroy()


class HistoricalNotification:
    def __init__(
        self,
        id,
        app_icon,
        summary,
        body,
        app_name,
        timestamp,
        cached_image_path=None,
    ):
        self.id = id
        self.app_icon = app_icon
        self.summary = summary
        self.body = body
        self.app_name = app_name
        self.timestamp = timestamp
        self.cached_image_path = cached_image_path
        self.image_pixbuf = None
        self.actions = []
        self.cached_scaled_pixbuf = None
