from fabric.audio.service import Audio
from fabric.core.service import Property, Service, Signal
from modules.Notch.MainWindow.Dashboard.Controls.common import BaseIconButton, BaseSmallIndicator, BaseSmoothSlider
import services.icons as icons


def _icon_and_tip(service: "Microphone", cur: int) -> tuple[str, str]:
    is_off = cur == 0 or service.muted
    icon = icons.mic if not is_off else icons.mic_mute
    tip = f"Microphone: {cur}%" if not is_off else "Microphone off"
    return icon, tip

class Microphone(Service):
    instance = None

    @staticmethod
    def get_initial():
        if Microphone.instance is None:
            Microphone.instance = Microphone()
        return Microphone.instance

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

        self._audio_hid = self.audio.connect("notify::microphone", self._on_stream_notify)
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

        self._stream = self.audio.microphone
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


class MicSlider(BaseSmoothSlider):
    def __init__(self, **kwargs):
        super().__init__(style_class="mic", **kwargs)
        self.service = Microphone.get_initial()
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


class MicSmall(BaseSmallIndicator):
    def __init__(self, **kwargs):
        super().__init__("button-bar-mic", "button-mic", "mic-label", **kwargs)
        self.service = Microphone.get_initial()
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


class MicIcon(BaseIconButton):
    def __init__(self, **kwargs):
        super().__init__("mic-icon", "mic-label-dash", **kwargs)
        self.service = Microphone.get_initial()
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