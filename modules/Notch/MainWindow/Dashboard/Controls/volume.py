from fabric.audio.service import Audio
from fabric.core.service import Property, Service, Signal
from modules.Notch.MainWindow.Dashboard.Controls.common import BaseIconButton, BaseSmallIndicator, BaseSmoothSlider
import services.icons as icons


_IS = {"high": icons.vol_high, "medium": icons.vol_medium, "off": icons.vol_mute}
_IB = {"high": icons.bluetooth_connected, "medium": icons.bluetooth, "off": icons.bluetooth_disconnected}


def _icon_and_tip(service: "Volume", cur: int) -> tuple[str, str]:
    is_off = cur == 0 or service.muted
    im = _IB if service.is_bluetooth else _IS
    icon = im["high"] if (cur > 74 and not is_off) else (im["medium"] if not is_off else im["off"])
    tip = f"Volume: {cur}%" if not is_off else "Muted"
    return icon, tip

class Volume(Service):
    instance = None

    @staticmethod
    def get_initial():
        if Volume.instance is None:
            Volume.instance = Volume()
        return Volume.instance

    @Signal
    def changed(self, value: int): ...

    def __init__(self):
        super().__init__()
        self.audio = Audio()
        self.max_volume = 100
        self._stream = None
        self._stream_hid = None
        self._last_val = -1
        self._last_muted = None

        self._audio_hid = self.audio.connect("notify::speaker", self._on_stream_notify)
        self._on_stream_notify()

    def _on_stream_notify(self, *_):
        if self._stream and self._stream_hid:
            # Стрим может успеть стать невалидным (устройство отключено
            # физически раньше, чем пришло событие об удалении) — тогда
            # disconnect на уже мёртвом GObject кидает TypeError.
            try:
                self._stream.disconnect(self._stream_hid)
            except TypeError:
                pass
            self._stream_hid = None

        self._stream = self.audio.speaker
        if self._stream:
            self._stream_hid = self._stream.connect("changed", self._on_stream_changed)
            self._on_stream_changed()
        else:
            self._last_val = 0
            self._last_muted = True
            self.emit("changed", 0)

    def _on_stream_changed(self, *_):
        if not self._stream:
            return
        val = int(round(self._stream.volume))
        muted = bool(self._stream.muted)
        if val != self._last_val or muted != self._last_muted:
            self._last_val = val
            self._last_muted = muted
            self.emit("changed", val)

    @Property(int, "read-write")
    def volume(self) -> int:
        return int(round(self._stream.volume)) if self._stream else 0

    @volume.setter
    def volume(self, value: int):
        if not self._stream:
            return
        value = max(0, min(self.max_volume, int(value)))
        if int(round(self._stream.volume)) != value:
            self._stream.volume = float(value)

    @property
    def muted(self) -> bool:
        return bool(self._stream.muted) if self._stream else True

    @muted.setter
    def muted(self, value: bool):
        if self._stream:
            self._stream.muted = bool(value)

    @property
    def is_bluetooth(self) -> bool:
        if not self._stream:
            return False
        return "bluetooth" in (getattr(self._stream, "icon_name", "") or "").lower()


def get_audio():
    return Volume.get_initial().audio


class VolumeSlider(BaseSmoothSlider):
    def __init__(self, **kwargs):
        super().__init__(style_class="vol", **kwargs)
        self.service = Volume.get_initial()
        self._hid = self.service.connect("changed", lambda _, v: self.update_external(v / 100.0))
        self.update_external(self.service.volume / 100.0)

    def on_user_value_changed(self, norm_val: float):
        v = int(norm_val * 100)
        if self.service.volume != v:
            self.service.volume = v

    def cleanup(self):
        super().cleanup()
        if self._hid:
            self.service.disconnect(self._hid)
            self._hid = None


class VolumeSmall(BaseSmallIndicator):
    def __init__(self, **kwargs):
        super().__init__("button-bar-vol", "button-volume", "vol-label", **kwargs)
        self.service = Volume.get_initial()
        self._hid = self.service.connect("changed", self._chg)
        self._chg(None, self.service.volume)

    def _chg(self, _, cur):
        icon, tip = _icon_and_tip(self.service, cur)
        self.update_ui(cur / 100.0, icon, tip)

    def cleanup(self):
        super().cleanup()
        if self._hid:
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
        self.service.volume = int(val)

    def _chg(self, _, cur):
        icon, tip = _icon_and_tip(self.service, cur)
        self.update_ui(icon, tip)

    def cleanup(self):
        super().cleanup()
        if self._hid:
            self.service.disconnect(self._hid)
            self._hid = None