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
        self._hid = None
        self._last = None
        self.audio.connect("notify::speaker", self._on_speaker)
        self._on_speaker()

    def _read(self) -> tuple[int, bool]:
        s = self._stream
        return (int(round(s.volume)), bool(s.muted)) if s is not None else (0, True)

    def _sync(self, *_):
        state = self._read()
        if state != self._last:
            self._last = state
            self.emit("changed", state[0])

    def _on_speaker(self, *_):
        stream = self.audio.speaker
        if stream is self._stream:
            return

        if self._stream is not None:
            self._stream.disconnect(self._hid)
        self._stream = stream
        if stream is not None:
            self._hid = stream.connect("changed", self._sync)
            self._last = None
        self._sync()

    @Property(int, "read-write")
    def volume(self) -> int:
        return self._read()[0]

    @volume.setter
    def volume(self, value: int):
        if self._stream is None:
            return
        value = max(0, min(self.max_volume, int(value)))
        if self._read()[0] != value:
            self._stream.volume = float(value)

    @property
    def muted(self) -> bool:
        return self._read()[1]

    @muted.setter
    def muted(self, value: bool):
        if self._stream is not None:
            self._stream.muted = bool(value)

    @property
    def is_bluetooth(self) -> bool:
        return (
            self._stream is not None
            and "bluetooth" in (self._stream.icon_name or "").lower()
        )


def get_audio():
    return Volume.get_initial().audio