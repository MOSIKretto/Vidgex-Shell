from gi.repository import Gio, GLib


_session_bus: Gio.DBusConnection | None = None
_pending_break_requests: list = []

def _send_break_notification(bus: Gio.DBusConnection) -> None:
    bus.call(
        "org.freedesktop.Notifications",
        "/org/freedesktop/Notifications",
        "org.freedesktop.Notifications",
        "Notify",
        GLib.Variant("(susssasa{sv}i)", ("Vidgex-Shell", 0, "", "Work Time", "Take a break!", [], {}, -1)),
        None, Gio.DBusCallFlags.NONE, -1, None, None,
    )

def _on_session_bus_ready(_source, result) -> None:
    global _session_bus
    pending = _pending_break_requests[:]
    _pending_break_requests.clear()
    try:
        _session_bus = Gio.bus_get_finish(result)
    except GLib.Error:
        # Сессионная шина недоступна (например, запущено вне графической
        # сессии) — уведомление о перерыве не критично, тихо пропускаем.
        return
    for request in pending:
        request(_session_bus)

def _notify_break() -> None:
    if _session_bus is not None:
        _send_break_notification(_session_bus)
        return
    _pending_break_requests.append(_send_break_notification)
    if len(_pending_break_requests) == 1:
        Gio.bus_get(Gio.BusType.SESSION, None, _on_session_bus_ready)


class WorkTime:
    __slots__ = ("update_callback", "remaining", "WORK_TIME", "_timer_id")

    def __init__(self, update_callback=None, work_time: int = 3600):
        self.update_callback = update_callback
        self.WORK_TIME = work_time
        self.remaining = work_time
        self._timer_id = None

    @property
    def is_running(self) -> bool:
        return self._timer_id is not None

    @property
    def state(self) -> str:
        return "WORK" if self.is_running else "STOPPED"

    def start(self) -> None:
        if self.is_running:
            return
        self.remaining = self.WORK_TIME
        self._timer_id = GLib.timeout_add_seconds(
            1, self._tick, priority=GLib.PRIORITY_DEFAULT_IDLE,
        )
        self._notify()

    def stop(self) -> None:
        if not self.is_running:
            return
        GLib.source_remove(self._timer_id)
        self._timer_id = None
        self.remaining = self.WORK_TIME
        self._notify()

    def toggle(self) -> None:
        if self.is_running:
            self.stop()
        else:
            self.start()

    def cleanup(self) -> None:
        self.stop()

    def _tick(self) -> bool:
        self.remaining -= 1
        if self.remaining > 0:
            self._notify()
            return True
        self._timer_id = None
        self.remaining = self.WORK_TIME
        _notify_break()
        self._notify()
        return False

    def _notify(self) -> None:
        if self.update_callback:
            self.update_callback(self.get_status_text(), self.is_running)

    def get_status_text(self) -> str:
        if not self.is_running:
            return "Disabled"
        mins, secs = divmod(self.remaining, 60)
        return f"Work {mins:02d}:{secs:02d}"