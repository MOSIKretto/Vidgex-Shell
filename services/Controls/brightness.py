import os

from fabric.core.service import Property, Service, Signal
from fabric.utils import monitor_file
from gi.repository import Gio, GLib


_BACKLIGHT_DIR = "/sys/class/backlight"


def _pick_backlight_device() -> str | None:
    if not os.path.isdir(_BACKLIGHT_DIR):
        return None
    entries = sorted(os.listdir(_BACKLIGHT_DIR))
    if not entries:
        return None
    # acpi_video* — generic ACPI-интерфейс, часто присутствует параллельно
    # с настоящим драйвером панели (intel_backlight, amdgpu_bl0 и т.д.) и
    # при этом реально не управляет яркостью. Предпочитаем вендор-специфичные
    # устройства — так же поступает brightnessctl.
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
        self.base_path = f"{_BACKLIGHT_DIR}/{self.device}" if self.device else None
        self.max_screen = -1
        self._valid = False
        self.monitor = None
        self._pending = None
        self._proc = None

        if not self.device:
            return

        try:
            with open(f"{self.base_path}/max_brightness") as f:
                self.max_screen = int(f.read().strip())
        except (OSError, ValueError):
            # sysfs-запись может быть недоступна сразу после смены режима
            # питания панели — считаем устройство непригодным.
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
            # Потеря live-синхронизации с внешними изменениями яркости не
            # должна отключать собственное чтение/запись через сервис.
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
        if self._proc is None:
            self._spawn_pending()

    def _spawn_pending(self):
        value, self._pending = self._pending, None
        try:
            self._proc = Gio.Subprocess.new(
                ["brightnessctl", f"--device={self.device}", "set", str(value)],
                Gio.SubprocessFlags.STDOUT_SILENCE,
            )
        except GLib.Error:
            # brightnessctl отсутствует в PATH (G_SPAWN_ERROR_NOENT) —
            # записать яркость невозможно.
            return
        self._proc.wait_async(None, self._on_proc_done)

    def _on_proc_done(self, _proc, _res):
        self._proc = None
        if self._pending is not None:
            self._spawn_pending()