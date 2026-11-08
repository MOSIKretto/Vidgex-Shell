import os

import gi
gi.require_version("Cvc", "1.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Gst", "1.0")
from gi.repository import Cvc, GLib, Gst, Gtk

from fabric.widgets.box import Box
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer


Gst.init(None)

_SHOW_MS = 1400
_DUCK_RATIO = 0.20
_FADE_MS = 25
_STEPS = 13

_IDLE, _DUCK, _PLAY, _UNDUCK = range(4)

_APP_ID = "fabric.glyph.sound"
_STREAM_NAME = "fabric-glyph-sound"

_STREAM_PROPS, _ = Gst.Structure.from_string(
    f"props,application.id=(string){_APP_ID},"
    f"application.name=(string){_STREAM_NAME},"
    f"media.name=(string){_STREAM_NAME}"
)

_SOUND_URI = GLib.filename_to_uri(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "Glyph", "glyph.wav"),
    None,
)


def _mk(text: str) -> str:
    return f'<span font_family="monospace" weight="bold">{GLib.markup_escape_text(text)}</span>'


_BASE = _mk("[!]\n[!]")


class GlyphSound:
    def __init__(self) -> None:
        self._closed = False
        self._state = _IDLE
        self._step = 0
        self._tid: int | None = None
        self._sigs: dict[int, tuple[Cvc.MixerStream, int]] = {}  # id -> (поток, handler)
        self._saved: dict[int, int] = {}  # id -> исходная громкость
        self._fade: dict[int, tuple[int, int]] = {}  # id -> (старт, цель) текущего затухания
        self._pending: dict[str, int] = {}  # ключ приложения -> громкость для восстановления

        self._control = Cvc.MixerControl(name=_STREAM_NAME)
        self._control.connect("stream-added", self._on_stream_added)
        self._control.connect("stream-removed", self._on_stream_removed)

        self._playbin = Gst.ElementFactory.make("playbin", None)
        self._playbin.set_property("flags", 0x02)
        self._playbin.set_property("uri", _SOUND_URI)
        self._playbin.connect("element-setup", self._on_element_setup)
        self._bus = self._playbin.get_bus()
        self._bus.add_signal_watch()
        self._bus.connect("message::eos", self._stop)
        self._bus.connect("message::error", self._stop)

        self._control.open()

    @staticmethod
    def _on_element_setup(_playbin, element) -> None:
        if element.find_property("stream-properties") is not None:
            element.set_property("stream-properties", _STREAM_PROPS)
        if element.find_property("client-name") is not None:
            element.set_property("client-name", _STREAM_NAME)

    @staticmethod
    def _is_own(stream) -> bool:
        return _APP_ID == stream.get_application_id() or _STREAM_NAME in (
            stream.get_name(), stream.get_description()
        )

    @staticmethod
    def _key(stream) -> str | None:
        return stream.get_application_id() or stream.get_name() or None

    @staticmethod
    def _set(stream, vol: int) -> None:
        stream.set_volume(vol)
        stream.push_volume()

    def _target(self) -> int:
        return int(self._control.get_vol_max_norm() * _DUCK_RATIO)

    def _cancel(self) -> None:
        if self._tid is not None:
            GLib.source_remove(self._tid)
            self._tid = None

    def _start_fade(self, state: int) -> None:
        self._state = state
        self._step = 0
        self._tid = GLib.timeout_add(_FADE_MS, self._tick)

    def _register(self, stream, immediate: bool) -> None:
        if not isinstance(stream, Cvc.MixerSinkInput) or self._is_own(stream):
            return
        sid = stream.get_id()
        if sid not in self._sigs:
            self._sigs[sid] = (stream, stream.connect("notify::volume", self._on_volume))

        target, cur = self._target(), stream.get_volume()
        if cur <= target:
            return
        self._saved.setdefault(sid, cur)
        if immediate:
            self._set(stream, target)
        else:
            self._fade[sid] = (cur, target)

    def _on_volume(self, stream, _pspec) -> None:
        if self._state != _PLAY:
            return
        sid, target, cur = stream.get_id(), self._target(), stream.get_volume()
        if cur == target:
            return
        saved = self._saved.get(sid)
        if cur > target:
            if saved is None or cur < saved:
                self._saved[sid] = cur
            self._set(stream, target)
        elif saved is not None and cur < saved:
            self._saved[sid] = cur

    def _on_stream_added(self, _control, sid: int) -> None:
        stream = self._control.lookup_stream_id(sid)
        if stream is None:
            return
        saved = self._pending.pop(self._key(stream), None)
        if saved is not None and stream.get_volume() < saved:
            self._set(stream, saved)
        if self._state in (_DUCK, _PLAY):
            self._register(stream, self._state == _PLAY)

    def _on_stream_removed(self, _control, sid: int) -> None:
        entry = self._sigs.pop(sid, None)
        saved = self._saved.pop(sid, None)
        self._fade.pop(sid, None)
        if entry is None:
            return
        stream, handler = entry
        stream.disconnect(handler)
        key = self._key(stream)
        if saved is not None and key:
            self._pending[key] = saved

    def _tick(self) -> bool:
        self._step += 1
        progress = min(1.0, self._step / _STEPS)
        for sid, (a, b) in list(self._fade.items()):
            stream = self._control.lookup_stream_id(sid)
            if stream is None:
                self._on_stream_removed(None, sid)
            else:
                self._set(stream, int(a + (b - a) * progress))
        if self._step < _STEPS:
            return True
        self._tid = None
        if self._state == _DUCK:
            self._play_now()
        else:
            self._idle()
        return False

    def _play_now(self) -> None:
        self._cancel()
        self._state = _PLAY
        self._playbin.set_state(Gst.State.NULL)
        if self._playbin.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self._unduck()

    def _unduck(self) -> None:
        self._cancel()
        self._fade.clear()
        for sid, saved in list(self._saved.items()):
            stream = self._control.lookup_stream_id(sid)
            if stream is None:
                self._on_stream_removed(None, sid)
            else:
                self._fade[sid] = (stream.get_volume(), saved)
        if self._fade:
            self._start_fade(_UNDUCK)
        else:
            self._idle()

    def _idle(self) -> None:
        self._cancel()
        self._state = _IDLE
        self._fade.clear()
        self._saved.clear()
        for stream, handler in self._sigs.values():
            stream.disconnect(handler)
        self._sigs.clear()

    def play(self) -> None:
        if self._closed:
            return
        playing = self._state == _PLAY
        self._cancel()
        self._fade.clear()
        for stream in self._control.get_sink_inputs():
            self._register(stream, playing)
        if playing or not self._fade:
            self._play_now()
        else:
            self._start_fade(_DUCK)

    def _stop(self, _bus, _msg) -> None:
        if self._state == _PLAY:
            self._playbin.set_state(Gst.State.NULL)
            self._unduck()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._cancel()
        self._bus.remove_signal_watch()
        self._playbin.set_state(Gst.State.NULL)
        for sid, saved in self._saved.items():
            stream = self._control.lookup_stream_id(sid)
            if stream is not None:
                self._set(stream, saved)
        self._pending.clear()
        self._idle()
        self._control.close()


class SideGlyph(Gtk.Overlay):
    def __init__(self, side: str, **kwargs):
        super().__init__(valign=Gtk.Align.FILL, vexpand=True, **kwargs)

        spacer = Box(v_expand=True)
        spacer.set_size_request(45, -1)
        self.add(spacer)

        left = side == "left"
        align = "end" if left else "start"

        self.lbl = Label(name=f"side-glyph-{side}", markup=_BASE, h_align=align, v_align="center")
        self.lbl.set_justify(Gtk.Justification.CENTER)

        self.revealer = Revealer(
            transition_type="slide-left" if left else "slide-right",
            transition_duration=250,
            h_align=align, v_align="center",
            child_revealed=False, child=self.lbl,
        )
        self.add_overlay(self.revealer)

        self._tid: int | None = None
        self._pending = False

        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)
        self.connect("destroy", lambda _: self._cancel())
        self.show_all()

    def _cancel(self) -> None:
        if self._tid is not None:
            GLib.source_remove(self._tid)
            self._tid = None

    def trigger(self) -> None:
        if self.get_mapped():
            self._run()
        else:
            self._pending = True

    def _run(self) -> None:
        self._cancel()
        self.revealer.set_reveal_child(True)
        self._tid = GLib.timeout_add(_SHOW_MS, self._hide)

    def _hide(self) -> bool:
        self._tid = None
        self.revealer.set_reveal_child(False)
        return False

    def _on_map(self, _w) -> None:
        if self._pending:
            self._pending = False
            self._run()

    def _on_unmap(self, _w) -> None:
        self._pending = False
        self._cancel()
        self.revealer.set_reveal_child(False)