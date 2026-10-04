import os
import random

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst, Gtk

from fabric.widgets.box import Box
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer


Gst.init(None)

_TICKS = 35
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
    __slots__ = ("_playbin", "_bus", "_sig_ids")

    def __init__(self) -> None:
        self._playbin = Gst.ElementFactory.make("playbin", None)
        self._playbin.set_property("flags", 0x02)  # GST_PLAY_FLAG_AUDIO
        self._playbin.set_property("uri", _SOUND_URI)
        self._bus = self._playbin.get_bus()
        self._bus.add_signal_watch()
        self._sig_ids = (
            self._bus.connect("message::eos", self._stop),
            self._bus.connect("message::error", self._stop),
        )

    def play(self) -> None:
        self._playbin.set_state(Gst.State.NULL)
        self._playbin.set_state(Gst.State.PLAYING)

    def _stop(self, _bus, _msg) -> None:
        self._playbin.set_state(Gst.State.NULL)

    def close(self) -> None:
        for sig_id in self._sig_ids:
            self._bus.disconnect(sig_id)
        self._bus.remove_signal_watch()
        self._playbin.set_state(Gst.State.NULL)


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