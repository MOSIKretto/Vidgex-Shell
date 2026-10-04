import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fabric.core.service import Signal
from fabric.notifications.service import Notification, Notifications
from fabric.widgets.box import Box
from gi.repository import GdkPixbuf, GLib, Gtk, Pango

from services.Notifications.glyph import GlitchSound, SideGlyph
from services.Notifications.image import CustomImage


_CACHE = GLib.get_user_cache_dir() + "/vidgex-shell"
_DND_FLAG = Path(_CACHE, "dnd")

THUMBNAIL_SIZE = 48
_MAX_SUMMARY = 80
_MAX_BODY = 150
_MAX_APP_NAME = 30
_MAX_ACTION_LABEL = 20

_LEFT_GLYPH_STYLE = "margin-right: -10px; margin-top: -8px;"
_RIGHT_GLYPH_STYLE = "margin-left: -10px; margin-top: -8px;"


def _safe_markup(text: str) -> str:
    try:
        Pango.parse_markup(text, -1, "\0")
        return text
    except GLib.Error:
        return GLib.markup_escape_text(text)


class NotificationData:
    __slots__ = (
        "id", "uuid", "timestamp",
        "app_name", "summary", "body", "body_markup",
        "timeout", "thumbnail", "actions",
        "_raw",
    )

    def __init__(self, raw: Notification, notif_id: int) -> None:
        self._raw = raw
        self.id = notif_id
        self.uuid = uuid4().hex
        self.timestamp = datetime.now()

        self.app_name = (raw.app_name or "Unknown")[:_MAX_APP_NAME]
        self.summary = str(raw.summary or "")[:_MAX_SUMMARY]
        self.body = str(raw.body or "")[:_MAX_BODY]
        self.body_markup = _safe_markup(self.body)
        self.timeout = raw.timeout
        self.thumbnail = self._load_thumbnail(raw)
        self.actions = self._build_actions(raw)

    @staticmethod
    def _load_thumbnail(raw: Notification) -> GdkPixbuf.Pixbuf | None:
        try:
            pixbuf = raw.image_pixbuf
            if pixbuf is None:
                return None
            return pixbuf.scale_simple(
                THUMBNAIL_SIZE, THUMBNAIL_SIZE, GdkPixbuf.InterpType.BILINEAR
            )
        except GLib.Error:
            return None

    def _build_actions(self, raw: Notification) -> tuple:
        return tuple(
            (str(a.label)[:_MAX_ACTION_LABEL], self._bind_action(a)) for a in raw.actions
        )

    def _bind_action(self, action):
        def run() -> None:
            action.invoke()
            self._raw.close("dismissed-by-user")
        return run

    def close(self, reason: str = "dismissed-by-user") -> None:
        self._raw.close(reason)

    def connect_closed(self, callback) -> int:
        return self._raw.connect("closed", lambda _n, reason: callback(reason))

    def disconnect_closed(self, handler_id: int) -> None:
        self._raw.disconnect(handler_id)


class NotificationServer(Notifications):
    _default: "NotificationServer | None" = None

    @Signal
    def notification_dnd_added(self, notification_id: int) -> None:...

    @classmethod
    def get_default(cls) -> "NotificationServer":
        if NotificationServer._default is None:
            raise RuntimeError(
                "NotificationServer не создан: создайте его в main.py до Notch"
            )
        return NotificationServer._default

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        os.makedirs(_CACHE, exist_ok=True)
        self._dnd = _DND_FLAG.exists()
        self._cleaned = False
        self._attached = False
        self._data: dict[int, NotificationData] = {}

        self._left_glyph = SideGlyph("left")
        self._right_glyph = SideGlyph("right")
        self._sound = GlitchSound()
        self.connect("notification-added", self._on_notification_added)

        NotificationServer._default = self

    @property
    def dnd(self) -> bool:
        return self._dnd

    @dnd.setter
    def dnd(self, value: bool) -> None:
        self._dnd = value
        if value:
            _DND_FLAG.touch()
        else:
            _DND_FLAG.unlink(missing_ok=True)

    def get_data(self, notif_id: int) -> NotificationData:
        data = self._data.get(notif_id)
        if data is None:
            data = self._prepare(notif_id)
        return data

    @staticmethod
    def make_image(pixbuf: GdkPixbuf.Pixbuf) -> CustomImage:
        img = CustomImage(pixbuf=pixbuf)
        img.set_valign(Gtk.Align.START)
        return img

    @staticmethod
    def safe_markup(text: str) -> str:
        return _safe_markup(text)

    def attach(self, popup) -> None:
        if self._attached:
            raise RuntimeError("attach() уже вызывался: сервер оформляет один Popup")
        self._attached = True

        popup.side_left.add(Box(children=[self._left_glyph], style=_LEFT_GLYPH_STYLE))
        popup.side_right.add(Box(children=[self._right_glyph], style=_RIGHT_GLYPH_STYLE))

    def _prepare(self, notif_id: int) -> NotificationData:
        raw = self.get_notification_from_id(notif_id)
        data = NotificationData(raw, notif_id)
        self._data[notif_id] = data
        data.connect_closed(lambda _reason, i=notif_id: self._data.pop(i, None))
        return data

    def _on_notification_added(self, _server, _notif_id) -> None:
        self._sound.play()
        self._left_glyph.trigger()
        self._right_glyph.trigger()

    def cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        if NotificationServer._default is self:
            NotificationServer._default = None
        self._sound.close()
        self._left_glyph.destroy()
        self._right_glyph.destroy()
        self._data.clear()

    def do_handle_bus_call(
        self, conn, sender, path, interface, target, params, invocation, user_data=None
    ) -> None:
        if target != "Notify" or not self.dnd:
            return super().do_handle_bus_call(
                conn, sender, path, interface, target, params, invocation, user_data
            )
        notif_id = self.new_notification_id()
        notification = Notification(
            id=notif_id,
            raw_variant=params,
            on_closed=self.do_handle_notification_closed,
            on_action_invoked=self.do_handle_notification_action_invoke,
        )
        self._notifications[notif_id] = notification
        self.notification_dnd_added(notif_id)

        invocation.return_value(GLib.Variant("(u)", (notif_id,)))
        notification.close("unknown")
        conn.flush()