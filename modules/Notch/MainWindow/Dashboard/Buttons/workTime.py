from gi.repository import Gio, GLib


def _on_bus(_source, result) -> None:
    # Fire-and-forget: ответ Notify нам не нужен, ошибки отсутствия демона уведомлений
    # в этом сценарии не критичны, поэтому колбэк у вызова не задаётся.
    Gio.bus_get_finish(result).call(
        "org.freedesktop.Notifications",
        "/org/freedesktop/Notifications",
        "org.freedesktop.Notifications",
        "Notify",
        # app_name, replaces_id, app_icon, summary, body, actions, hints, expire_timeout
        GLib.Variant("(susssasa{sv}i)", ("Vidgex-Shell", 0, "", "Work Time", "Take a break!", [], {}, -1)),
        None, Gio.DBusCallFlags.NONE, -1, None, None,
    )


def _notify_break() -> None:
    # Соединение с сессионной шиной берётся асинхронно, чтобы не блокировать главный поток.
    Gio.bus_get(Gio.BusType.SESSION, None, _on_bus)


class WorkTime:
    """Рабочий таймер. Чистый Python без GObject в MRO — поэтому __slots__."""

    __slots__ = ("update_callback", "remaining", "WORK_TIME", "_timer_id")

    def __init__(self, update_callback=None, work_time: int = 3600):
        self.update_callback = update_callback
        self.WORK_TIME = work_time
        self.remaining = work_time
        self._timer_id = None

    @property
    def is_running(self) -> bool:
        # Единственный источник правды — наличие GLib-источника.
        return self._timer_id is not None

    @property
    def state(self) -> str:
        return "WORK" if self.is_running else "STOPPED"

    def start(self) -> None:
        if self.is_running:
            return
        self.remaining = self.WORK_TIME
        self._timer_id = GLib.timeout_add_seconds(1, self._tick)
        self._notify()

    def stop(self) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        self.remaining = self.WORK_TIME
        self._notify()

    def toggle(self) -> None:
        if self.is_running:
            self.stop()
        else:
            self.start()

    def _tick(self) -> bool:
        self.remaining -= 1
        if self.remaining > 0:
            self._notify()
            return True
        # Источник завершится возвратом False; снимать его через source_remove изнутри
        # собственного колбэка не нужно, поэтому id обнуляется до stop().
        self._timer_id = None
        _notify_break()
        self.stop()
        return False

    def _notify(self) -> None:
        if self.update_callback:
            self.update_callback(self.get_status_text(), self.is_running)

    def get_status_text(self) -> str:
        if not self.is_running:
            return "Disabled"
        mins, secs = divmod(self.remaining, 60)
        return f"Work {mins:02d}:{secs:02d}"