from typing import Callable

from fabric.widgets.box import Box
from gi.repository import GLib

from services.Notifications.notificationServer import NotificationServer
from modules.Notch.NotificationsPopup.notificationBox import NotificationBox


def _never_blocked() -> bool:
    return False

def _noop() -> None:
    pass


class NotificationsPopup(Box):
    def __init__(self, server: NotificationServer | None = None):
        super().__init__(
            name="notch-notification-popup",
            orientation="v",
            h_align="fill",
            h_expand=True,
        )
        self._is_blocked: Callable[[], bool] = _never_blocked
        self._on_show: Callable[[], None] = _noop
        self._on_hide: Callable[[], None] = _noop

        self._current_nb: NotificationBox | None = None
        self._closed_handler: int | None = None
        self._timeout_id: int | None = None
        self._destroyed = False

        self.side_left = Box(name="notification-side-left")
        self.side_right = Box(name="notification-side-right")

        self._inner = Box(name="notch-notification-inner", orientation="v", h_expand=True)
        self.add(self._inner)

        self._server = server if server is not None else NotificationServer.get_default()
        self._server.attach(self)
        self._server_handler = self._server.connect(
            "notification-added", self._on_notification_added
        )
        self.connect("destroy", self._on_destroy)

    def set_handlers(
        self,
        is_blocked: Callable[[], bool] = _never_blocked,
        on_show: Callable[[], None] = _noop,
        on_hide: Callable[[], None] = _noop,
    ) -> None:
        self._is_blocked = is_blocked
        self._on_show = on_show
        self._on_hide = on_hide

    def _on_notification_added(self, server, notif_id: int) -> None:
        data = server.get_data(notif_id)

        if self._is_blocked():
            GLib.idle_add(self._close_unseen, data)
            return

        self._stop_timeout()
        superseded = self._release_current()
        if superseded is not None:
            superseded.close("unknown")

        image = server.make_image(data.thumbnail) if data.thumbnail is not None else None
        nb = NotificationBox(data, image)
        self._current_nb = nb
        self._closed_handler = data.connect_closed(self._on_notification_closed)
        self._inner.add(nb)
        nb.show_all()

        self._on_show()

        if data.timeout != 0:
            ms = data.timeout if data.timeout > 0 else 5000
            self._timeout_id = GLib.timeout_add(ms, self._on_timeout)

    def _close_unseen(self, data) -> bool:
        data.close("unknown")
        return GLib.SOURCE_REMOVE

    def _release_current(self):
        nb = self._current_nb
        if nb is None:
            return None
        data = nb.data
        data.disconnect_closed(self._closed_handler)
        self._current_nb = None
        nb.destroy()
        return data

    def _on_timeout(self) -> bool:
        self._timeout_id = None
        current = self._release_current()
        if current is not None:
            current.close("expired")
        self._on_hide()
        return GLib.SOURCE_REMOVE

    def _on_notification_closed(self, _reason) -> None:
        self._stop_timeout()
        self._release_current()
        self._on_hide()

    def _stop_timeout(self) -> None:
        if self._timeout_id is not None:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = None

    def _on_destroy(self, *_) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        self._stop_timeout()
        self._server.disconnect(self._server_handler)
        current = self._release_current()
        if current is not None:
            current.close("unknown")
        self._is_blocked = _never_blocked
        self._on_show = self._on_hide = _noop