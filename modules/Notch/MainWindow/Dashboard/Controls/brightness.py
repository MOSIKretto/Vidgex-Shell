from modules.Notch.MainWindow.Dashboard.Controls.common import BaseIconButton, BaseSmoothSlider

import services.icons as icons
from services.Controls.brightness import Brightness


class BrightnessSlider(BaseSmoothSlider):
    def __init__(self, **kwargs):
        super().__init__(style_class="brightness", **kwargs)
        self.service = Brightness.get_initial()

        if self.service.max_screen <= 0:
            self.set_no_show_all(True)
            self.set_visible(False)
            return

        hid = self.service.connect(
            "screen", lambda _, cur: self.update_external(cur / self.service.max_screen)
        )
        self.connect("destroy", lambda _: self.service.disconnect(hid))
        self.update_external(self.service.screen_brightness / self.service.max_screen)

    def on_user_value_changed(self, norm_val: float):
        target = round(norm_val * self.service.max_screen)
        if target != self.service.screen_brightness:
            self.service.screen_brightness = target


class BrightnessIcon(BaseIconButton):
    def __init__(self, **kwargs):
        super().__init__("brightness-icon", "brightness-label-dash", **kwargs)
        self.service = Brightness.get_initial()

        if self.service.max_screen <= 0:
            self.set_no_show_all(True)
            self.set_visible(False)
            return

        hid = self.service.connect("screen", self._chg)
        self.connect("destroy", lambda _: self.service.disconnect(hid))
        self._chg()

    def get_current_value(self) -> float:
        return self.service.screen_brightness * 100 / self.service.max_screen

    def set_current_value(self, val: float):
        self.service.screen_brightness = round(val * self.service.max_screen / 100)

    def _chg(self, *_):
        p = round(self.get_current_value())
        icon = (
            icons.brightness_high if p >= 75
            else icons.brightness_medium if p >= 24
            else icons.brightness_low
        )
        self.update_ui(icon, f"Brightness: {p}%")