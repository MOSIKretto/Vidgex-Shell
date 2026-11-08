from modules.Notch.MainWindow.Dashboard.Controls.common import BaseIconButton, BaseSmoothSlider

import services.icons as icons
from services.Controls.microphone import Microphone


class MicSlider(BaseSmoothSlider):
    def __init__(self, **kwargs):
        super().__init__(style_class="mic", **kwargs)
        self.service = Microphone.get_initial()
        hid = self.service.connect("changed", lambda _, v: self.update_external(v / 100))
        self.connect("destroy", lambda _: self.service.disconnect(hid))
        self.update_external(self.service.volume / 100)

    def on_user_value_changed(self, norm_val: float):
        v = round(norm_val * 100)
        if self.service.volume != v:
            self.service.volume = v


class MicIcon(BaseIconButton):
    def __init__(self, **kwargs):
        super().__init__("mic-icon", "mic-label-dash", **kwargs)
        self.service = Microphone.get_initial()
        hid = self.service.connect("changed", self._chg)
        self.connect("destroy", lambda _: self.service.disconnect(hid))
        self._chg(None, self.service.volume)

    def get_current_value(self) -> float:
        return float(self.service.volume)

    def set_current_value(self, val: float):
        self.service.volume = round(val)

    def _chg(self, _, cur):
        if cur == 0 or self.service.muted:
            self.update_ui(icons.mic_mute, "Microphone off")
        else:
            self.update_ui(icons.mic, f"Microphone: {cur}%")