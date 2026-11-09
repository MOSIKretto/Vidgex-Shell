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
        self._state = (0, True)

        self.audio.connect("changed", self._refresh)
        self.audio.connect("speaker-changed", self._refresh)

    def _read(self) -> tuple[int, bool]:
        stream = self.audio.speaker
        if stream is None:
            return 0, True
        raw = stream.stream
        norm = self.audio._control.get_vol_max_norm()
        vol = round(raw.get_property("volume") / norm * 100)
        return min(vol, self.max_volume), bool(raw.get_property("is-muted"))

    def _refresh(self, *_):
        state = self._read()
        if state != self._state:
            self._state = state
            self.emit("changed", state[0])

    @Property(int, "read-write")
    def volume(self) -> int:
        return self._state[0]

    @volume.setter
    def volume(self, value: int):
        stream = self.audio.speaker
        if stream is None:
            return
        value = max(0, min(self.max_volume, int(value)))
        raw = stream.stream
        raw.set_volume(int(value * self.audio._control.get_vol_max_norm() / 100))
        raw.push_volume()

    @property
    def muted(self) -> bool:
        return self._state[1]

    @muted.setter
    def muted(self, value: bool):
        stream = self.audio.speaker
        if stream is not None:
            stream.stream.set_is_muted(bool(value))
            stream.stream.change_is_muted(bool(value))

    @property
    def is_bluetooth(self) -> bool:
        stream = self.audio.speaker
        return stream is not None and "bluetooth" in (stream.icon_name or "").lower()


def get_audio():
    return Volume.get_initial().audio