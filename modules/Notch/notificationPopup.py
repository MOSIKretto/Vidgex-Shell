import weakref

from fabric.notifications.service import Notifications as FabricNotifications
from fabric.widgets.box import Box

from gi.repository import GLib

from modules.Notch.Notifications.history import get_shared_history
from modules.Notch.Notifications.notificationBox import NotificationBox


_notification_server: FabricNotifications | None = None


def _get_notification_server() -> FabricNotifications:
    global _notification_server
    if _notification_server is None:
        _notification_server = FabricNotifications()
    return _notification_server


class NotificationPopup(Box):
    # Виджет уведомлений внутри стека Notch.
    # При получении нового уведомления текущее немедленно уходит в историю
    # и заменяется новым — без очереди и ожидания таймера.
    def __init__(self, notch=None, **kwargs):
        super().__init__(
            name="notch-notification-popup",
            orientation="v",
            h_align="fill",
            h_expand=True,
            **kwargs,
        )
        self._notch_ref = weakref.ref(notch) if notch else None
        self._current_nb: NotificationBox | None = None
        self._timeout_id: int | None = None
        self._closed_handler: int | None = None
        self._current_notification = None
        self._destroyed = False

        self._inner = Box(
            name="notch-notification-inner",
            orientation="v",
            h_expand=True,
        )
        self.add(self._inner)

        self._server = _get_notification_server()
        self._server_handler = self._server.connect(
            "notification-added", self._on_notification_added
        )

    # Публичный метод
    def open(self) -> None:
        pass

    # Приём нового уведомления
    def _on_notification_added(self, server, notif_id: int) -> None:
        if self._destroyed:
            return

        n = server.get_notification_from_id(notif_id)
        if n is None:
            return

        history = get_shared_history()

        if history.glyphs_enabled and history.trigger_glyphs_callback:
            history.trigger_glyphs_callback()

        # Сразу в историю если DND или notch открыт с другим виджетом
        notch = self._get_notch()
        notch_busy = (
            notch is not None
            and notch._cw is not None
            and notch._cw != "notification"
        )

        if history.do_not_disturb_enabled or notch_busy:
            nb = NotificationBox(n, timeout_ms=0)
            history.add_notification(nb)
            return

        self._stop_timeout()
        self._unsubscribe_closed()
        self._dismiss_current(send_to_history=True)

        nb = NotificationBox(n, timeout_ms=0)
        self._current_nb = nb
        self._subscribe_closed(n)

        for child in list(self._inner.get_children()):
            self._inner.remove(child)
        self._inner.add(nb)
        self._inner.show_all()

        if notch:
            notch.open_notification()

        live_timeout = getattr(n, "timeout", -1)
        ms = live_timeout if (live_timeout and live_timeout > 0) else 5000
        self._timeout_id = GLib.timeout_add(ms, self._on_timeout)

    # Подписка / отписка
    def _subscribe_closed(self, notification) -> None:
        if hasattr(notification, "connect"):
            self._closed_handler = notification.connect(
                "closed", self._on_notification_closed
            )
            self._current_notification = notification

    def _unsubscribe_closed(self) -> None:
        n = self._current_notification
        h = self._closed_handler
        if n and h is not None:
            try:
                if n.handler_is_connected(h):
                    n.disconnect(h)
            except Exception:
                pass
        self._closed_handler = None
        self._current_notification = None

    # Таймер и закрытие
    def _on_timeout(self) -> bool:
        self._timeout_id = None
        self._unsubscribe_closed()
        self._dismiss_current(send_to_history=True)
        notch = self._get_notch()
        if notch:
            notch.close_notification()
        return GLib.SOURCE_REMOVE

    def _on_notification_closed(self, notification, reason) -> None:
        self._stop_timeout()
        self._unsubscribe_closed()
        self._dismiss_current(send_to_history=True)
        notch = self._get_notch()
        if notch:
            notch.close_notification()

    def _stop_timeout(self) -> None:
        if self._timeout_id is not None:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = None

    def _dismiss_current(self, send_to_history: bool = True) -> None:
        nb = self._current_nb
        if nb is None:
            return
        self._current_nb = None

        for child in list(self._inner.get_children()):
            self._inner.remove(child)

        if send_to_history and not getattr(nb, "_destroyed", False):
            get_shared_history().add_notification(nb)
        elif not getattr(nb, "_destroyed", False):
            nb.destroy()

    # Вспомогательные
    def _get_notch(self):
        return self._notch_ref() if self._notch_ref else None

    # Уничтожение
    def destroy(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True

        self._stop_timeout()
        self._unsubscribe_closed()
        self._dismiss_current(send_to_history=False)

        if self._server and self._server_handler is not None:
            try:
                if self._server.handler_is_connected(self._server_handler):
                    self._server.disconnect(self._server_handler)
            except Exception:
                pass
        self._server_handler = None
        self._server = None

        super().destroy()