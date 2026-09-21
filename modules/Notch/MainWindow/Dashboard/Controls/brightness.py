from fabric.core.service import Property, Service, Signal
from fabric.utils import exec_shell_command_async, monitor_file
from gi.repository import GLib

from modules.Notch.MainWindow.Dashboard.Controls.common import BaseIconButton, BaseSmallIndicator, BaseSmoothSlider
import services.icons as icons

_BTH = (75, 24)
_BIC = (icons.brightness_high, icons.brightness_medium, icons.brightness_low)

def _bicon(p: int) -> str:
    return _BIC[0] if p >= _BTH[0] else (_BIC[1] if p >= _BTH[1] else _BIC[2])


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
        self.device = None
        self.base_path = None
        self.max_screen = 0
        self._valid = False

        try:
            backlight_dir = "/sys/class/backlight"
            if GLib.file_test(backlight_dir, GLib.FileTest.IS_DIR):
                dir_handle = GLib.Dir.open(backlight_dir, 0)
                name = dir_handle.read_name()
                if name:
                    self.device = name
                    self.base_path = f"{backlight_dir}/{name}"
        except Exception as e:
            print(f"[Brightness] Ошибка поиска backlight: {e}")

        if not self.device or not self.base_path:
            self.max_screen = -1
            return

        try:
            with open(f"{self.base_path}/max_brightness") as f:
                self.max_screen = int(f.read().strip())
        except Exception:
            self.max_screen = -1
            return

        if self.max_screen <= 0:
            self.max_screen = -1
            return

        self._valid = True

        try:
            self.monitor = monitor_file(f"{self.base_path}/brightness")
            self.monitor.connect("changed", lambda *_: self._read_and_emit())
        except Exception:
            self._valid = False

        if self._valid:
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
        except Exception:
            return -1

    @screen_brightness.setter
    def screen_brightness(self, value: int):
        if not self._valid:
            return
        value = max(0, min(int(value), self.max_screen))
        try:
            exec_shell_command_async(f"brightnessctl --device='{self.device}' set {value}", None)
        except Exception as e:
            print(f"[Brightness] Ошибка brightnessctl: {e}")


class BrightnessSlider(BaseSmoothSlider):
    def __init__(self, **kwargs):
        super().__init__(style_class="brightness", **kwargs)
        self.service = Brightness.get_initial()
        self._tid = None
        self._target = -1

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
        if self.service.screen_brightness == -1:
            return
        self._hid = self.service.connect("screen", self._chg)
        self._chg()

    def _chg(self, *_):
        mx = self.service.max_screen
        if mx <= 0: return
        p = int(self.service.screen_brightness * 100 / mx)
        self.update_ui(p / 100.0, _bicon(p), f"Brightness: {p}%")

    def cleanup(self):
        if self._hid:
            self.service.disconnect(self._hid)
            self._hid = None


class BrightnessIcon(BaseIconButton):
    def __init__(self, **kwargs):
        super().__init__("brightness-icon", "brightness-label-dash", **kwargs)
        self.service = Brightness.get_initial()
        if self.service.screen_brightness == -1:
            return
        self._hid = self.service.connect("screen", self._chg)
        self._chg()

    def get_current_value(self) -> float:
        return float(self.service.screen_brightness)

    def set_current_value(self, val: float):
        self.service.screen_brightness = int(val)

    def _chg(self, *_):
        mx = self.service.max_screen
        if mx <= 0: return
        p = int(self.service.screen_brightness * 100 / mx)
        self.update_ui(_bicon(p), f"Brightness: {p}%")

    def cleanup(self):
        super().cleanup()
        if self._hid:
            self.service.disconnect(self._hid)
            self._hid = None