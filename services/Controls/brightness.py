import os

from fabric.core.service import Property, Service, Signal
from fabric.utils import monitor_file
from gi.repository import Gio, GLib



def _pick_backlight_device() -> str | None:
    if not os.path.isdir("/sys/class/backlight"):
        return None
    entries = sorted(os.listdir("/sys/class/backlight"))
    if not entries:
        return None
    preferred = [e for e in entries if not e.startswith("acpi_video")]
    return (preferred or entries)[0]


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
        self.base_path = f"{"/sys/class/backlight"}/{self.device}" if self.device else None
        self.max_screen = -1
        self._valid = False
        self.monitor = None
        self._session_id = os.environ.get("XDG_SESSION_ID")
        self._bus: Gio.DBusConnection | None = None
        self._session_path: str | None = None
        self._resolving = False
        self._inflight = False
        self._pending: int | None = None

        if not self.device:
            return

        try:
            with open(f"{self.base_path}/max_brightness") as f:
                self.max_screen = int(f.read().strip())
        except (OSError, ValueError):
            self.max_screen = -1
            return

        if self.max_screen <= 0:
            self.max_screen = -1
            return

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
        if not self._valid or not self._session_id:
            return
        self._pending = max(0, min(int(value), self.max_screen))
        self._write_pending()

    def _write_pending(self):
        if self._pending is None or self._inflight:
            return
        if self._session_path is None:
            self._resolve_session()
            return
        value, self._pending = self._pending, None
        self._inflight = True
        self._bus.call(
            "org.freedesktop.login1",
            self._session_path,
            "org.freedesktop.login1.Session",
            "SetBrightness",
            GLib.Variant("(ssu)", ("backlight", self.device, value)),
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            self._on_set_done,
        )

    def _on_set_done(self, connection: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
        self._inflight = False
        try:
            connection.call_finish(result)
        except GLib.Error:
            pass
        if self._pending is not None:
            self._write_pending()

    def _resolve_session(self):
        if self._resolving:
            return
        self._resolving = True
        if self._bus is not None:
            self._request_session_path()
        else:
            Gio.bus_get(Gio.BusType.SYSTEM, None, self._on_bus_ready)

    def _on_bus_ready(self, _src, result: Gio.AsyncResult, *_args) -> None:
        try:
            self._bus = Gio.bus_get_finish(result)
        except GLib.Error:
            self._resolving = False
            return
        self._request_session_path()

    def _request_session_path(self) -> None:
        self._bus.call(
            "org.freedesktop.login1",
            "/org/freedesktop/login1",
            "org.freedesktop.login1.Manager",
            "GetSession",
            GLib.Variant("(s)", (self._session_id,)),
            GLib.VariantType("(o)"),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            self._on_session_reply,
        )

    def _on_session_reply(self, connection: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
        self._resolving = False
        try:
            reply = connection.call_finish(result)
        except GLib.Error:
            return
        (path,) = reply.unpack()
        self._session_path = path
        self._write_pending()