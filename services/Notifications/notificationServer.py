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
# флаг хранится наличием файла: нет формата, который мог бы оказаться битым
_DND_FLAG = Path(_CACHE, "dnd")

THUMBNAIL_SIZE = 48
_MAX_SUMMARY = 80
_MAX_BODY = 150
_MAX_APP_NAME = 30
_MAX_ACTION_LABEL = 20

# глифы подъезжают к вырезу: отрицательный отступ и подъём вверх
_LEFT_GLYPH_STYLE = "margin-right: -10px; margin-top: -8px;"
_RIGHT_GLYPH_STYLE = "margin-left: -10px; margin-top: -8px;"


def _safe_markup(text: str) -> str:
    try:
        Pango.parse_markup(text, -1, "\0")
        return text
    except GLib.Error:
        # тело от клиента: невалидная разметка или тег, оборванный обрезкой по длине
        return GLib.markup_escape_text(text)


class NotificationData:
    """Подготовленное к показу уведомление: единый формат для Popup и History."""

    __slots__ = (
        "id", "uuid", "timestamp",
        "app_name", "summary", "body", "body_markup",
        "timeout", "thumbnail", "actions",
        "_raw",
    )

    def __init__(self, raw: Notification, notif_id: int) -> None:
        self._raw = raw
        self.id = notif_id               # числовой id сервера, живёт до перезапуска
        self.uuid = uuid4().hex          # постоянный id для истории
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
            # image-path указывает на отсутствующий или битый файл
            return None

    def _build_actions(self, raw: Notification) -> tuple:
        # (подпись, колбэк): подписчику не нужно знать, как устроено действие
        try:
            return tuple(
                (str(a.label)[:_MAX_ACTION_LABEL], self._bind_action(a)) for a in raw.actions
            )
        except Exception:
            return ()

    def _bind_action(self, action):
        def run() -> None:
            action.invoke()
            self._raw.close("dismissed-by-user")
        return run

    # --- управление живым уведомлением ---
    def close(self, reason: str = "dismissed-by-user") -> None:
        self._raw.close(reason)

    def connect_closed(self, callback) -> int:
        """callback(reason)"""
        return self._raw.connect("closed", lambda _n, reason: callback(reason))

    def disconnect_closed(self, handler_id: int) -> None:
        self._raw.disconnect(handler_id)


class NotificationServer(Notifications):
    # экземпляр по умолчанию: его создаёт main.py, а Popup и History берут отсюда
    _default: "NotificationServer | None" = None

    @Signal
    def notification_dnd_added(self, notification_id: int) -> None:
        # уведомление, принятое в режиме DND: его должна получить только история.
        # Popup и эффекты подписаны на notification-added и этот сигнал не видят
        ...

    @classmethod
    def get_default(cls) -> "NotificationServer":
        if NotificationServer._default is None:
            raise RuntimeError(
                "NotificationServer не создан: создайте его в main.py до Notch"
            )
        return NotificationServer._default

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        NotificationServer._default = self
        os.makedirs(_CACHE, exist_ok=True)
        self._dnd = _DND_FLAG.exists()
        self._cleaned = False
        self._attached = False
        self._data: dict[int, NotificationData] = {}

        # сервер владеет эффектами: создаёт, запускает и сам размещает их в Popup
        self._left_glyph = SideGlyph("left")
        self._right_glyph = SideGlyph("right")
        self._sound = GlitchSound()

        # подписка на собственный сигнал: обработчик стоит раньше Popup/History.
        # В DND эмитится notification-dnd-added, поэтому эффекты в DND молчат
        self.connect("notification-added", self._on_notification_added)

    # ---------------- публичный API ----------------

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
        """Данные уведомления. Создаются при первом запросе, поэтому порядок
        подписчиков не важен. Вызывать синхронно из обработчика сигнала:
        после закрытия уведомления данные удаляются."""
        data = self._data.get(notif_id)
        if data is None:
            data = self._prepare(notif_id)
        return data

    @staticmethod
    def make_image(pixbuf: GdkPixbuf.Pixbuf) -> CustomImage:
        """Фабрика: у виджета один родитель, поэтому каждый подписчик берёт свой."""
        img = CustomImage(pixbuf=pixbuf)
        img.set_valign(Gtk.Align.START)
        return img

    @staticmethod
    def safe_markup(text: str) -> str:
        return _safe_markup(text)

    def attach(self, popup) -> None:
        """Оформляет Popup своими эффектами. От Popup нужны только два пустых
        контейнера: side_left и side_right. Что в них класть, знает только сервер.
        Виджет можно положить лишь в одного родителя, поэтому вызывать один раз."""
        if self._attached:
            raise RuntimeError("attach() уже вызывался: сервер оформляет один Popup")
        self._attached = True

        popup.side_left.add(Box(children=[self._left_glyph], style=_LEFT_GLYPH_STYLE))
        popup.side_right.add(Box(children=[self._right_glyph], style=_RIGHT_GLYPH_STYLE))

    # ---------------- внутренности ----------------

    def _prepare(self, notif_id: int) -> NotificationData:
        raw = self.get_notification_from_id(notif_id)
        data = NotificationData(raw, notif_id)
        self._data[notif_id] = data
        # данные живут ровно столько же, сколько само уведомление
        data.connect_closed(lambda _reason, i=notif_id: self._data.pop(i, None))
        return data

    def _on_notification_added(self, _server, _notif_id) -> None:
        self._sound.play()
        self._left_glyph.trigger()
        self._right_glyph.trigger()

    def cleanup(self) -> None:
        # у сервера нет сигнала destroy: cleanup вызывает владелец (main.py)
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
        # DND: объект создаётся так же, как в базовом Notify, но рассылается отдельным
        # сигналом, который слушает только история
        notif_id = self.new_notification_id()
        notification = Notification(
            id=notif_id,
            raw_variant=params,
            on_closed=self.do_handle_notification_closed,
            on_action_invoked=self.do_handle_notification_action_invoke,
        )
        self._notifications[notif_id] = notification
        # обработчики истории отрабатывают синхронно и сами вызывают get_data
        self.notification_dnd_added(notif_id)

        invocation.return_value(GLib.Variant("(u)", (notif_id,)))
        # закрытие после return_value: NotificationClosed должен уйти позже ответа на Notify
        notification.close("unknown")
        conn.flush()