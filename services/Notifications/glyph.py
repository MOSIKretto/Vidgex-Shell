import os
import random

import gi
gi.require_version("Cvc", "1.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Gst", "1.0")
from gi.repository import Cvc, GLib, Gst, Gtk

from fabric.widgets.box import Box
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer


_TICKS = 35
_TICK_MS = 40
_DUCK_RATIO = 0.20
_FADE_MS = 25
_STEPS = 13

_IDLE, _DUCK, _PLAY, _UNDUCK = range(4)

_APP_ID = "fabric.glyph.sound"
_STREAM_NAME = "fabric-glyph-sound"

_STREAM_PROPS_DESC = (
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


_BASE = _mk("███\n███")
_TIERS = (
    tuple(_mk(t) for t in ("▓▓▓\n▓▓▓", "▓█▓\n█▓█")),
    tuple(_mk(t) for t in ("▒▒▒\n▒▒▒", "▀▄▀\n▄▀▄")),
    tuple(_mk(t) for t in ("░░░\n░░░", "▚▞▚\n▞▚▞", "▞▚▞\n▚▞▚")),
)


class GlyphSound:
    __slots__ = (
        "_closed", "_state", "_step", "_tid", "_wait", "_duck_vol",
        "_sigs", "_saved", "_fade", "_pending",
        "_control", "_playbin", "_bus", "_props",
    )

    def __init__(self) -> None:
        self._closed = False
        self._state = _IDLE
        self._step = 0
        self._tid: int | None = None
        self._wait = False
        self._duck_vol = 0
        self._sigs: dict[int, tuple[Cvc.MixerStream, int]] = {}
        self._saved: dict[int, int] = {}
        self._fade: dict[int, tuple[int, int]] = {}
        self._pending: dict[str, int] = {}
        self._control: Cvc.MixerControl | None = None
        self._playbin = None
        self._bus = None
        self._props = None

    def _open(self) -> None:
        Gst.init(None)
        self._props, _ = Gst.Structure.from_string(_STREAM_PROPS_DESC)

        self._control = Cvc.MixerControl(name=_STREAM_NAME)
        self._control.connect("stream-added", self._on_stream_added)
        self._control.connect("stream-removed", self._on_stream_removed)
        self._control.connect("state-changed", self._on_state)

        self._playbin = Gst.ElementFactory.make("playbin", None)
        self._playbin.set_property("flags", 0x02)
        self._playbin.set_property("uri", _SOUND_URI)
        self._playbin.connect("element-setup", self._on_element_setup)
        self._bus = self._playbin.get_bus()
        self._bus.add_signal_watch()
        self._bus.connect("message::eos", self._stop)
        self._bus.connect("message::error", self._stop)

        self._control.open()

    def _on_state(self, _control, state: int) -> None:
        if self._wait and state == Cvc.MixerControlState.READY:
            self._begin()

    def _on_element_setup(self, _playbin, element) -> None:
        if element.find_property("stream-properties") is not None:
            element.set_property("stream-properties", self._props)
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
        if stream.get_volume() != vol:
            stream.set_volume(vol)
            stream.push_volume()

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

        cur = stream.get_volume()
        if cur <= self._duck_vol:
            return
        self._saved.setdefault(sid, cur)
        if immediate:
            self._set(stream, self._duck_vol)
        else:
            self._fade[sid] = (cur, self._duck_vol)

    def _on_volume(self, stream, _pspec) -> None:
        if self._state != _PLAY:
            return
        sid, target, cur = stream.get_id(), self._duck_vol, stream.get_volume()
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
        if not self._pending and self._state not in (_DUCK, _PLAY):
            return
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
        for sid, (a, b) in self._fade.items():
            self._set(self._sigs[sid][0], int(a + (b - a) * progress))
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
        for sid, saved in self._saved.items():
            cur = self._sigs[sid][0].get_volume()
            if cur != saved:
                self._fade[sid] = (cur, saved)
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

    def _begin(self) -> None:
        self._wait = False
        playing = self._state == _PLAY
        self._cancel()
        self._fade.clear()
        self._duck_vol = int(self._control.get_vol_max_norm() * _DUCK_RATIO)
        for stream in self._control.get_sink_inputs():
            self._register(stream, playing)
        if playing or not self._fade:
            self._play_now()
        else:
            self._start_fade(_DUCK)

    def play(self) -> None:
        if self._closed:
            return
        if self._control is None:
            self._open()
            self._wait = True
        else:
            self._begin()

    def _stop(self, _bus, _msg) -> None:
        if self._state == _PLAY:
            self._playbin.set_state(Gst.State.NULL)
            self._unduck()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._wait = False
        self._cancel()
        if self._control is None:
            return
        self._bus.remove_signal_watch()
        self._playbin.set_state(Gst.State.NULL)
        for sid, saved in self._saved.items():
            self._set(self._sigs[sid][0], saved)
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

        self._markup = _BASE
        self._left = 0
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

    def _set_markup(self, markup: str) -> None:
        if markup != self._markup:
            self._markup = markup
            self.lbl.set_markup(markup)

    def trigger(self) -> None:
        if self.get_mapped():
            self._run()
        else:
            self._pending = True

    def _run(self) -> None:
        self._cancel()
        self._set_markup(_BASE)
        self.revealer.set_reveal_child(True)
        self._left = _TICKS
        self._tid = GLib.timeout_add(_TICK_MS, self._tick)

    def _tick(self) -> bool:
        self._left -= 1
        if self._left <= 0:
            self._tid = None
            self._set_markup(_BASE)
            self.revealer.set_reveal_child(False)
            return False
        ratio = self._left / _TICKS
        if random.random() < ratio:
            self._set_markup(random.choice(_TIERS[int(ratio * 3)]))
        else:
            self._set_markup(_BASE)
        return True

    def _on_map(self, _w) -> None:
        if self._pending:
            self._pending = False
            self._run()

    def _on_unmap(self, _w) -> None:
        self._pending = False
        self._cancel()
        self._set_markup(_BASE)
        self.revealer.set_reveal_child(False)