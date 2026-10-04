from fabric.audio.service import Audio
from fabric.core.service import Property, Service, Signal


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
        stream = self.audio.speaker
        if stream is self._stream:
            # notify::speaker сработал без реальной смены устройства —
            # не пересоздаём подписку впустую.
            return

        if self._stream is not None:
            self._stream.disconnect(self._stream_hid)

        self._stream = stream
        if self._stream is not None:
            self._stream_hid = self._stream.connect("changed", self._on_stream_changed)
            self._last_val = -1
            self._on_stream_changed()
        elif self._last_val != 0 or self._last_muted is not True:
            self._last_val = 0
            self._last_muted = True
            self.emit("changed", 0)

    def _on_stream_changed(self, *_):
        val = int(round(self._stream.volume))
        muted = bool(self._stream.muted)
        if val != self._last_val or muted != self._last_muted:
            self._last_val = val
            self._last_muted = muted
            self.emit("changed", val)

    @Property(int, "read-write")
    def volume(self) -> int:
        return int(round(self._stream.volume)) if self._stream is not None else 0

    @volume.setter
    def volume(self, value: int):
        if self._stream is None:
            return
        value = max(0, min(self.max_volume, int(value)))
        if int(round(self._stream.volume)) != value:
            self._stream.volume = float(value)

    @property
    def muted(self) -> bool:
        return bool(self._stream.muted) if self._stream is not None else True

    @muted.setter
    def muted(self, value: bool):
        if self._stream is not None:
            self._stream.muted = bool(value)

    @property
    def is_bluetooth(self) -> bool:
        if self._stream is None:
            return False
        return "bluetooth" in (self._stream.icon_name or "").lower()


def get_audio():
    return Volume.get_initial().audio