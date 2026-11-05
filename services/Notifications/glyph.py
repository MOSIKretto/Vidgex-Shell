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


Gst.init(None)

_TICKS = 35
_DUCK_RATIO = 0.20
_FADE_INTERVAL_MS = 25

_STEPS = 13

_STATE_IDLE = 0
_STATE_DUCKING = 1
_STATE_PLAYING = 2
_STATE_UNDUCKING = 3

_APP_ID = "fabric.glitch.sound"
_STREAM_NAME = "fabric-glitch-sound"
_STREAM_PROPS, _ = Gst.Structure.from_string(
    f"props,application.id=(string){_APP_ID},"
    f"application.name=(string){_STREAM_NAME},"
    f"media.name=(string){_STREAM_NAME}"
)
_SOUND_URI = GLib.filename_to_uri(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "Glyph", "glitch.wav"), None
)

_GLITCH_CLASSES = (
    "glitch-shift-right",
    "glitch-shift-left",
    "glitch-flicker",
    "glitch-aberration",
    "glitch-heavy",
    "glitch-color-swap",
)

_BASE_MARKUP = '<span font_family="monospace" weight="bold">[!]\n[!]</span>'
_GLITCH_MARKUPS = (
    '<span font_family="monospace" weight="bold">|#|\n|#|</span>',
    '<span font_family="monospace" weight="bold">[@]\n[@]</span>',
    '<span font_family="monospace" weight="bold">&lt;*&gt;\n&lt;*&gt;</span>',
    '<span font_family="monospace" weight="bold">/!\\\n\\!/</span>',
    '<span font_family="monospace" weight="bold">\\!/\n/!\\</span>',
    '<span font_family="monospace" weight="bold">!?!\n!?!</span>',
    '<span font_family="monospace" weight="bold">{$}\n{$}</span>',
    '<span font_family="monospace" weight="bold">###\n###</span>',
    '<span font_family="monospace" weight="bold">010\n101</span>',
)


class GlitchSound:
    __slots__ = (
        "_playbin",
        "_bus",
        "_sig_ids",
        "_control",
        "_control_sigs",
        "_stream_sigs",
        "_saved_volumes",
        "_pending_restores",
        "_fade_start",
        "_state",
        "_fade_step",
        "_fade_tid",
    )

    def __init__(self) -> None:
        self._control = Cvc.MixerControl(name=_STREAM_NAME)
        self._control.open()
        self._control_sigs: list[int] = []
        self._stream_sigs: dict[int, tuple[Cvc.MixerStream, int]] = {}
        self._saved_volumes: dict[int, int] = {}
        # Потоки, исчезнувшие задушенными: app_id -> громкость до уведомлений.
        # Нужны, т.к. аудиосервер запоминает задушенные 20% (stream-restore),
        # и новый поток приложения иначе стартует с них — в т.ч. после рестарта.
        self._pending_restores: dict[str, int] = {}
        self._fade_start: dict[int, int] = {}
        self._state = _STATE_IDLE
        self._fade_step = 0
        self._fade_tid: int | None = None

        self._playbin = Gst.ElementFactory.make("playbin", None)
        self._playbin.set_property("flags", 0x02)  # GST_PLAY_FLAG_AUDIO
        self._playbin.set_property("uri", _SOUND_URI)
        self._bus = self._playbin.get_bus()
        self._bus.add_signal_watch()
        self._sig_ids: list[tuple[object, int]] = [
            (self._playbin, self._playbin.connect("element-setup", self._on_element_setup)),
            (self._bus, self._bus.connect("message::eos", self._stop)),
            (self._bus, self._bus.connect("message::error", self._stop)),
        ]
        # Подписываемся сразу, а не только на время уведомления: иначе потоки,
        # пересозданные в IDLE после duck, никто не восстановит.
        self._ensure_subscriptions()

    @staticmethod
    def _on_element_setup(_playbin: Gst.Element, element: Gst.Element) -> None:
        if element.find_property("stream-properties") is not None:
            element.set_property("stream-properties", _STREAM_PROPS)
        if element.find_property("client-name") is not None:
            element.set_property("client-name", _STREAM_NAME)

    @staticmethod
    def _is_own_stream(stream: Cvc.MixerStream) -> bool:
        return (
            stream.get_application_id() == _APP_ID
            or stream.get_name() == _STREAM_NAME
            or stream.get_description() == _STREAM_NAME
        )

    def _target_volume(self) -> int:
        return int(self._control.get_vol_max_norm() * _DUCK_RATIO)

    def _cancel_fade_timer(self) -> None:
        if self._fade_tid is not None:
            GLib.source_remove(self._fade_tid)
            self._fade_tid = None

    def _ensure_subscriptions(self) -> None:
        if not self._control_sigs:
            self._control_sigs.append(
                self._control.connect("stream-added", self._on_stream_added)
            )
            self._control_sigs.append(
                self._control.connect("stream-removed", self._on_stream_removed)
            )

    def _register_stream(self, stream: Cvc.MixerStream, immediate: bool) -> None:
        if not isinstance(stream, Cvc.MixerSinkInput) or self._is_own_stream(stream):
            return

        stream_id = stream.get_id()
        if stream_id not in self._stream_sigs:
            sig_id = stream.connect("notify::volume", self._on_stream_volume)
            self._stream_sigs[stream_id] = (stream, sig_id)

        target_vol = self._target_volume()
        current_vol = stream.get_volume()
        if current_vol > target_vol:
            if stream_id not in self._saved_volumes:
                self._saved_volumes[stream_id] = current_vol
            if immediate:
                stream.set_volume(target_vol)
                stream.push_volume()
            else:
                self._fade_start[stream_id] = current_vol

    def _on_stream_volume(self, stream: Cvc.MixerStream, _pspec) -> None:
        if self._state != _STATE_PLAYING:
            return
        target_vol = self._target_volume()
        current_vol = stream.get_volume()
        if current_vol > target_vol:
            stream_id = stream.get_id()
            if stream_id not in self._saved_volumes:
                self._saved_volumes[stream_id] = current_vol
            stream.set_volume(target_vol)
            stream.push_volume()

    @staticmethod
    def _stream_app_key(stream: Cvc.MixerStream) -> str | None:
        return stream.get_application_id() or stream.get_name() or None

    def _restore_pending_volume(self, stream: Cvc.MixerStream) -> None:
        """Вернуть исходную громкость потоку, унаследовавшему задушенное значение."""
        if self._is_own_stream(stream):
            return
        key = self._stream_app_key(stream)
        if not key:
            return
        saved_vol = self._pending_restores.pop(key, None)
        if saved_vol is None:
            return
        if stream.get_volume() < saved_vol:
            stream.set_volume(saved_vol)
            stream.push_volume()

    def _on_stream_added(self, _control: Cvc.MixerControl, stream_id: int) -> None:
        stream = self._control.lookup_stream_id(stream_id)
        if stream is None:
            return
        # Новый поток мог унаследовать задушенную громкость через stream-restore —
        # восстанавливаем до duck-логики, иначе она сочтёт 20% исходными.
        self._restore_pending_volume(stream)
        if self._state in (_STATE_IDLE, _STATE_UNDUCKING):
            return
        self._register_stream(stream, immediate=(self._state == _STATE_PLAYING))

    def _on_stream_removed(self, _control: Cvc.MixerControl, stream_id: int) -> None:
        entry = self._stream_sigs.pop(stream_id, None)
        stream = entry[0] if entry is not None else None
        if entry is not None:
            _, sig_id = entry
            stream.disconnect(sig_id)
        saved_vol = self._saved_volumes.pop(stream_id, None)
        self._fade_start.pop(stream_id, None)
        if saved_vol is not None and stream is not None and not self._is_own_stream(stream):
            key = self._stream_app_key(stream)
            if key:
                # Поток исчез задушенным: сервер мог запомнить заниженную
                # громкость — восстановим её, когда приложение создаст новый поток.
                self._pending_restores[key] = saved_vol

    def _on_fade_tick(self) -> bool:
        self._fade_step += 1
        target_vol = self._target_volume()

        if self._state == _STATE_DUCKING:
            progress = min(1.0, self._fade_step / _STEPS)
            for stream_id, start_vol in list(self._fade_start.items()):
                stream = self._control.lookup_stream_id(stream_id)
                if stream is None:
                    self._on_stream_removed(self._control, stream_id)
                    continue
                vol = int(start_vol + (target_vol - start_vol) * progress)
                stream.set_volume(vol)
                stream.push_volume()

            if self._fade_step >= _STEPS:
                self._fade_tid = None
                self._start_playback()
                return False
            return True

        if self._state == _STATE_UNDUCKING:
            progress = min(1.0, self._fade_step / _STEPS)
            for stream_id, saved_vol in list(self._saved_volumes.items()):
                stream = self._control.lookup_stream_id(stream_id)
                if stream is None:
                    self._on_stream_removed(self._control, stream_id)
                    continue
                start_vol = self._fade_start.get(stream_id, target_vol)
                vol = int(start_vol + (saved_vol - start_vol) * progress)
                stream.set_volume(vol)
                stream.push_volume()

            if self._fade_step >= _STEPS:
                self._fade_tid = None
                self._reset_to_idle()
                return False
            return True

        self._fade_tid = None
        return False

    def _start_playback(self) -> None:
        self._cancel_fade_timer()
        self._state = _STATE_PLAYING
        self._playbin.set_state(Gst.State.NULL)
        if self._playbin.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self._start_unduck()

    def _start_unduck(self) -> None:
        self._cancel_fade_timer()
        self._fade_start.clear()

        for stream_id in list(self._saved_volumes):
            stream = self._control.lookup_stream_id(stream_id)
            if stream is None:
                self._on_stream_removed(self._control, stream_id)
            else:
                self._fade_start[stream_id] = stream.get_volume()

        if not self._saved_volumes:
            self._reset_to_idle()
            return

        self._state = _STATE_UNDUCKING
        self._fade_step = 0
        self._fade_tid = GLib.timeout_add(_FADE_INTERVAL_MS, self._on_fade_tick)

    def _reset_to_idle(self) -> None:
        self._cancel_fade_timer()
        self._state = _STATE_IDLE
        self._fade_start.clear()
        self._saved_volumes.clear()

        # Подписки на появление/исчезновение потоков НЕ снимаем специально:
        # так _on_stream_added ловит потоки, пересозданные после duck,
        # и возвращает им исходную громкость через _pending_restores.
        # _pending_restores при этом не чистим — восстановление может
        # понадобиться позже, когда приложение создаст новый поток.

        while self._stream_sigs:
            _, (stream, sig_id) = self._stream_sigs.popitem()
            stream.disconnect(sig_id)

    def play(self) -> None:
        self._ensure_subscriptions()

        if self._state == _STATE_PLAYING:
            for stream in self._control.get_sink_inputs():
                self._register_stream(stream, immediate=True)
            self._start_playback()
            return

        self._cancel_fade_timer()
        self._fade_start.clear()
        for stream in self._control.get_sink_inputs():
            self._register_stream(stream, immediate=False)

        if not self._fade_start:
            self._start_playback()
            return

        self._state = _STATE_DUCKING
        self._fade_step = 0
        self._fade_tid = GLib.timeout_add(_FADE_INTERVAL_MS, self._on_fade_tick)

    def _stop(self, _bus: Gst.Bus, _msg: Gst.Message) -> None:
        self._playbin.set_state(Gst.State.NULL)
        self._start_unduck()

    def close(self) -> None:
        self._cancel_fade_timer()
        while self._sig_ids:
            obj, sig_id = self._sig_ids.pop()
            obj.disconnect(sig_id)
        if self._bus is not None:
            self._bus.remove_signal_watch()
            self._bus = None
        self._playbin.set_state(Gst.State.NULL)

        while self._saved_volumes:
            stream_id, saved_vol = self._saved_volumes.popitem()
            stream = self._control.lookup_stream_id(stream_id)
            if stream is not None:
                stream.set_volume(saved_vol)
                stream.push_volume()

        self._pending_restores.clear()
        self._reset_to_idle()

        while self._control_sigs:
            self._control.disconnect(self._control_sigs.pop())

        self._control.close()


class SideGlyph(Gtk.Overlay):
    def __init__(self, side: str, **kwargs):
        super().__init__(**kwargs)
        self.set_valign(Gtk.Align.FILL)
        self.set_vexpand(True)

        spacer = Box()
        spacer.set_size_request(45, -1)
        spacer.set_vexpand(True)
        self.add(spacer)

        halign = Gtk.Align.END if side == "left" else Gtk.Align.START

        self.lbl = Label(name=f"side-glyph-{side}")
        self.lbl.set_justify(Gtk.Justification.CENTER)
        self.lbl.set_valign(Gtk.Align.CENTER)
        self.lbl.set_halign(halign)
        self.lbl.set_markup(_BASE_MARKUP)

        self.revealer = Revealer(
            transition_type="slide-left" if side == "left" else "slide-right",
            transition_duration=250,
        )
        self.revealer.set_valign(Gtk.Align.CENTER)
        self.revealer.set_halign(halign)
        self.revealer.add(self.lbl)
        self.revealer.set_reveal_child(False)
        self.add_overlay(self.revealer)

        self.show_all()

        self._lbl_ctx = self.lbl.get_style_context()
        self._active_classes: set = set()
        self._current_markup = _BASE_MARKUP
        self._gl_rem = 0
        self._gl_tid = None
        self._destroyed = False
        self.connect("destroy", self._on_destroy)

    def _add_class(self, cls: str) -> None:
        self._lbl_ctx.add_class(cls)
        self._active_classes.add(cls)

    def _clear_visual(self) -> None:
        for cls in self._active_classes:
            self._lbl_ctx.remove_class(cls)
        self._active_classes.clear()
        if self._current_markup != _BASE_MARKUP:
            self.lbl.set_markup(_BASE_MARKUP)
            self._current_markup = _BASE_MARKUP

    def trigger(self) -> None:
        if self._destroyed:
            return

        if self._gl_tid is not None:
            GLib.source_remove(self._gl_tid)
        self._clear_visual()
        self.revealer.set_reveal_child(True)
        self._gl_rem = _TICKS
        self._gl_tid = GLib.timeout_add(40, self._tick)

    def _tick(self) -> bool:
        if self._destroyed:
            return False

        self._gl_rem -= 1
        if self._gl_rem <= 0:
            self._clear_visual()
            self.revealer.set_reveal_child(False)
            self._gl_tid = None
            return False

        self._clear_visual()

        if random.random() >= (self._gl_rem / _TICKS):
            return True

        self._add_class("glitching")

        new_markup = random.choice(_GLITCH_MARKUPS)
        if self._current_markup != new_markup:
            self.lbl.set_markup(new_markup)
            self._current_markup = new_markup

        self._add_class(random.choice(_GLITCH_CLASSES))
        if random.random() > 0.5:
            self._add_class(random.choice(_GLITCH_CLASSES))

        return True

    def _on_destroy(self, _widget) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        if self._gl_tid is not None:
            GLib.source_remove(self._gl_tid)
            self._gl_tid = None