import os

from fabric.core.service import Property, Service, Signal
from fabric.utils import monitor_file
from gi.repository import Gio, GLib


def _pick_backlight_device() -> str | None:
    try:
        entries = sorted(os.listdir("/sys/class/backlight"))
    except OSError:
        return None
    preferred = [e for e in entries if not e.startswith("acpi_video")]
    return (preferred or entries or [None])[0]

 
class Brightness(Service):
    instance = None

    @staticmethod
    def get_initial():
        if Brightness.instance is None:
            Brightness.instance = Brightness()
        return Brightness.instance

    @Signal
    def screen(self, value: int): ...

    def __init__(self):
        super().__init__()
        self.device = _pick_backlight_device()
        self.base_path = f"/sys/class/backlight/{self.device}" if self.device else None
        self.max_screen = -1
        self._valid = False
        self.monitor = None
        self._bus: Gio.DBusConnection | None = None
        self._inflight = False
        self._pending: int | None = None

        if not self.device:
            return
        try:
            with open(f"{self.base_path}/max_brightness") as f:
                max_screen = int(f.read().strip())
        except (OSError, ValueError):
            return
        if max_screen <= 0:
            return

        self.max_screen = max_screen
        self._valid = True

        try:
            self.monitor = monitor_file(f"{self.base_path}/brightness")
            self.monitor.connect("changed", lambda *_: self._read_and_emit())
        except GLib.Error:
            self.monitor = None

        self._read_and_emit()

    def _read_and_emit(self):
        value = self.screen_brightness
        if value != -1:
            self.emit("screen", value)

    @Property(int, "read-write")
    def screen_brightness(self) -> int:
        if not self._valid:
            return -1
        try:
            with open(f"{self.base_path}/brightness") as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return -1

    @screen_brightness.setter
    def screen_brightness(self, value: int):
        if not self._valid:
            return
        self._pending = max(0, min(int(value), self.max_screen))
        self._write_pending()

    def _write_pending(self):
        if self._pending is None or self._inflight:
            return
        if self._bus is None:
            try:
                self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            except GLib.Error:
                return
        value, self._pending = self._pending, None
        self._inflight = True
        self._bus.call(
            "org.freedesktop.login1",
            "/org/freedesktop/login1/session/auto",
            "org.freedesktop.login1.Session",
            "SetBrightness",
            GLib.Variant("(ssu)", ("backlight", self.device, value)),
            None, Gio.DBusCallFlags.NONE, -1, None, self._on_set_done,
        )

    def _on_set_done(self, connection: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
        self._inflight = False
        try:
            connection.call_finish(result)
        except GLib.Error:
            pass
        self._write_pending()