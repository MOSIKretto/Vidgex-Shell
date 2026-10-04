from gi.repository import GLib

from modules.Notch.MainWindow.Dashboard.Controls.common import BaseIconButton, BaseSmoothSlider
import services.icons as icons
from services.Controls.brightness import Brightness


_BTH = (75, 24)
_BIC = (icons.brightness_high, icons.brightness_medium, icons.brightness_low)


def _bicon(p: int) -> str:
    return _BIC[0] if p >= _BTH[0] else (_BIC[1] if p >= _BTH[1] else _BIC[2])


class BrightnessSlider(BaseSmoothSlider):
    def __init__(self, **kwargs):
        super().__init__(style_class="brightness", **kwargs)
        self.service = Brightness.get_initial()
        self._tid = None
        self._hid = None

        if self.service.max_screen <= 0:
            self.set_no_show_all(True)
            self.set_visible(False)
            return

        self._hid = self.service.connect("screen", self._chg)
        self.update_external(self.service.screen_brightness / self.service.max_screen)

    def _chg(self, _, cur):
        if self._tid is not None:
            return
        self.update_external(cur / self.service.max_screen)

    def on_user_value_changed(self, norm_val: float):
        self._target = round(norm_val * self.service.max_screen)
        if self._tid is None:
            self._tid = GLib.timeout_add(30, self._apply)

    def _apply(self):
        self._tid = None
        if self._target != self.service.screen_brightness:
            self.service.screen_brightness = self._target
        return False

    def cleanup(self):
        super().cleanup()
        if self._tid is not None:
            GLib.source_remove(self._tid)
            self._tid = None
        if self._hid is not None:
            self.service.disconnect(self._hid)
            self._hid = None


class BrightnessIcon(BaseIconButton):
    def __init__(self, **kwargs):
        super().__init__("brightness-icon", "brightness-label-dash", **kwargs)
        self.service = Brightness.get_initial()
        self._hid = None

        if self.service.max_screen <= 0:
            self.set_no_show_all(True)
            self.set_visible(False)
            return

        self._hid = self.service.connect("screen", self._chg)
        self._chg()

    def get_current_value(self) -> float:
        return self.service.screen_brightness * 100 / self.service.max_screen

    def set_current_value(self, val: float):
        self.service.screen_brightness = round(val * self.service.max_screen / 100)

    def _chg(self, *_):
        p = round(self.service.screen_brightness * 100 / self.service.max_screen)
        self.update_ui(_bicon(p), f"Brightness: {p}%")

    def cleanup(self):
        super().cleanup()
        if self._hid is not None:
            self.service.disconnect(self._hid)
            self._hid = None