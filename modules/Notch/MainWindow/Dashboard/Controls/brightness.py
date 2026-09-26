import os

from fabric.core.service import Property, Service, Signal
from fabric.utils import monitor_file
from gi.repository import GLib

from modules.Notch.MainWindow.Dashboard.Controls.common import BaseIconButton, BaseSmallIndicator, BaseSmoothSlider
import services.icons as icons


_BTH = (75, 24)
_BIC = (icons.brightness_high, icons.brightness_medium, icons.brightness_low)
_BACKLIGHT_DIR = "/sys/class/backlight"


def _bicon(p: int) -> str:
    return _BIC[0] if p >= _BTH[0] else (_BIC[1] if p >= _BTH[1] else _BIC[2])

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
        value = max(0, min(int(value), self.max_screen))
        try:
            GLib.spawn_command_line_async(f"brightnessctl --device='{self.device}' set {value}")
        except GLib.Error:
            # brightnessctl отсутствует в системе или бинарник удалён после
            # старта сервиса — молча пропускаем попытку установки яркости,
            # как и с недоступным sysfs-чтением у GPU-опроса.
            pass


class BrightnessSlider(BaseSmoothSlider):
    def __init__(self, **kwargs):
        super().__init__(style_class="brightness", **kwargs)
        self.service = Brightness.get_initial()
        self._tid = None
        self._target = -1
        self._hid = None

        if self.service.max_screen <= 0:
            self.set_no_show_all(True)
            self.hide()
            return

        self._hid = self.service.connect("screen", self._chg)
        self.update_external(self.service.screen_brightness / self.service.max_screen)

    def _chg(self, _, cur):
        if self._tid or self.service.max_screen <= 0:
            return
        self.update_external(cur / self.service.max_screen)

    def on_user_value_changed(self, norm_val: float):
        self._target = int(norm_val * self.service.max_screen)
        if not self._tid:
            self._tid = GLib.timeout_add(30, self._apply)

    def _apply(self):
        self._tid = None
        if self._target >= 0 and self._target != self.service.screen_brightness:
            self.service.screen_brightness = self._target
        return False

    def cleanup(self):
        super().cleanup()
        if self._tid:
            GLib.source_remove(self._tid)
            self._tid = None
        if self._hid:
            self.service.disconnect(self._hid)
            self._hid = None


class BrightnessSmall(BaseSmallIndicator):
    def __init__(self, **kwargs):
        super().__init__("button-bar-brightness", "button-brightness", "brightness-label", **kwargs)
        self.service = Brightness.get_initial()
        self._hid = None

        if self.service.max_screen <= 0:
            return

        self._hid = self.service.connect("screen", self._chg)
        self._chg()

    def _chg(self, *_):
        mx = self.service.max_screen
        if mx <= 0:
            return
        p = int(self.service.screen_brightness * 100 / mx)
        self.update_ui(p / 100.0, _bicon(p), f"Brightness: {p}%")

    def cleanup(self):
        super().cleanup()
        if self._hid:
            self.service.disconnect(self._hid)
            self._hid = None


class BrightnessIcon(BaseIconButton):
    def __init__(self, **kwargs):
        super().__init__("brightness-icon", "brightness-label-dash", **kwargs)
        self.service = Brightness.get_initial()
        self._hid = None

        if self.service.max_screen <= 0:
            return

        self._hid = self.service.connect("screen", self._chg)
        self._chg()

    def get_current_value(self) -> float:
        return float(self.service.screen_brightness)

    def set_current_value(self, val: float):
        self.service.screen_brightness = int(val)

    def _chg(self, *_):
        mx = self.service.max_screen
        if mx <= 0:
            return
        p = int(self.service.screen_brightness * 100 / mx)
        self.update_ui(_bicon(p), f"Brightness: {p}%")

    def cleanup(self):
        super().cleanup()
        if self._hid:
            self.service.disconnect(self._hid)
            self._hid = None