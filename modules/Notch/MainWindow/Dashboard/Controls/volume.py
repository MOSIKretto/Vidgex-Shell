from modules.Notch.MainWindow.Dashboard.Controls.common import BaseIconButton, BaseSmoothSlider
import services.icons as icons
from services.Controls.volume import Volume


_IS = {"high": icons.vol_high, "medium": icons.vol_medium, "off": icons.vol_mute}
_IB = {"high": icons.bluetooth_connected, "medium": icons.bluetooth, "off": icons.bluetooth_disconnected}


def _icon_and_tip(service: Volume, cur: int) -> tuple[str, str]:
    is_off = cur == 0 or service.muted
    im = _IB if service.is_bluetooth else _IS
    icon = im["high"] if (cur > 74 and not is_off) else (im["medium"] if not is_off else im["off"])
    tip = f"Volume: {cur}%" if not is_off else "Muted"
    return icon, tip


class VolumeSlider(BaseSmoothSlider):
    def __init__(self, **kwargs):
        super().__init__(style_class="vol", **kwargs)
        self.service = Volume.get_initial()
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


class VolumeIcon(BaseIconButton):
    def __init__(self, **kwargs):
        super().__init__("vol-icon", "vol-label-dash", **kwargs)
        self.service = Volume.get_initial()
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