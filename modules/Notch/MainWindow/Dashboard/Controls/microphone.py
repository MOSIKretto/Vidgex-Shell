from modules.Notch.MainWindow.Dashboard.Controls.common import BaseIconButton, BaseSmoothSlider
import services.icons as icons
from services.Controls.microphone import Microphone


def _icon_and_tip(service: Microphone, cur: int) -> tuple[str, str]:
    is_off = cur == 0 or service.muted
    icon = icons.mic if not is_off else icons.mic_mute
    tip = f"Microphone: {cur}%" if not is_off else "Microphone off"
    return icon, tip


class MicSlider(BaseSmoothSlider):
    def __init__(self, **kwargs):
        super().__init__(style_class="mic", **kwargs)
        self.service = Microphone.get_initial()
        self._hid = self.service.connect("changed", lambda _, v: self.update_external(v / 100.0))
        self.update_external(self.service.volume / 100.0)

    def on_user_value_changed(self, norm_val: float):
        v = round(norm_val * 100)
        if self.service.volume != v:
            self.service.volume = v

    def cleanup(self):
        super().cleanup()
        if self._hid is not None:
            self.service.disconnect(self._hid)
            self._hid = None


class MicIcon(BaseIconButton):
    def __init__(self, **kwargs):
        super().__init__("mic-icon", "mic-label-dash", **kwargs)
        self.service = Microphone.get_initial()
        self._hid = self.service.connect("changed", self._chg)
        self._chg(None, self.service.volume)

    def get_current_value(self) -> float:
        return float(self.service.volume)

    def set_current_value(self, val: float):
        self.service.volume = round(val)

    def _chg(self, _, cur):
        icon, tip = _icon_and_tip(self.service, cur)
        self.update_ui(icon, tip)

    def cleanup(self):
        super().cleanup()
        if self._hid is not None:
            self.service.disconnect(self._hid)
            self._hid = None