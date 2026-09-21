import random
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from fabric.widgets.box import Box
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer


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


class SideGlyph(Gtk.Overlay):
    __slots__ = (
        "spacer", "revealer", "lbl",
        "_gl_rem", "_gl_tid", "_active_classes", "_current_markup",
    )

    def __init__(self, side: str = "left", **kwargs):
        super().__init__(**kwargs)

        # Спейсер задаёт минимальную ширину оверлея,
        # но растягивается по высоте родителя
        self.spacer = Box()
        self.spacer.set_size_request(45, -1)
        self.spacer.set_vexpand(True)
        self.add(self.spacer)

        # Оверлей сам тоже растягивается по высоте родителя
        self.set_valign(Gtk.Align.FILL)
        self.set_vexpand(True)

        self.revealer = Revealer(
            transition_type="slide-left" if side == "left" else "slide-right",
            transition_duration=250,
        )
        # Revealer центрируется вертикально внутри оверлея
        self.revealer.set_valign(Gtk.Align.CENTER)
        self.revealer.set_vexpand(False)

        self.lbl = Label(name=f"side-glyph-{side}")
        self.lbl.set_justify(Gtk.Justification.CENTER)
        self.lbl.set_valign(Gtk.Align.CENTER)

        if side == "left":
            self.lbl.set_halign(Gtk.Align.END)
            self.revealer.set_halign(Gtk.Align.END)
        else:
            self.lbl.set_halign(Gtk.Align.START)
            self.revealer.set_halign(Gtk.Align.START)

        self.revealer.add(self.lbl)
        self.add_overlay(self.revealer)

        self.show_all()
        self.revealer.set_reveal_child(False)
        self.lbl.set_markup(_BASE_MARKUP)

        self._gl_rem = 0
        self._gl_tid = None
        self._active_classes: set = set()
        self._current_markup = _BASE_MARKUP

    def trigger(self):
        if self._gl_tid:
            GLib.source_remove(self._gl_tid)
        self.revealer.set_reveal_child(True)
        self._gl_rem = 35
        self._gl_tid = GLib.timeout_add(40, self._tick)

    def _clear_classes(self, ctx):
        for cls in self._active_classes:
            ctx.remove_class(cls)
        self._active_classes.clear()

    def _tick(self) -> bool:
        ctx = self.lbl.get_style_context()
        self._gl_rem -= 1

        if self._gl_rem <= 0:
            self._clear_classes(ctx)
            if self._current_markup != _BASE_MARKUP:
                self.lbl.set_markup(_BASE_MARKUP)
                self._current_markup = _BASE_MARKUP
            self.revealer.set_reveal_child(False)
            self._gl_tid = None
            return False

        progress = self._gl_rem / 35.0

        if random.random() < progress:
            self._clear_classes(ctx)
            ctx.add_class("glitching")
            self._active_classes.add("glitching")

            new_markup = random.choice(_GLITCH_MARKUPS)
            if self._current_markup != new_markup:
                self.lbl.set_markup(new_markup)
                self._current_markup = new_markup

            cls1 = random.choice(_GLITCH_CLASSES)
            ctx.add_class(cls1)
            self._active_classes.add(cls1)

            if random.random() > 0.5:
                cls2 = random.choice(_GLITCH_CLASSES)
                if cls2 != cls1:
                    ctx.add_class(cls2)
                    self._active_classes.add(cls2)
        else:
            self._clear_classes(ctx)
            if self._current_markup != _BASE_MARKUP:
                self.lbl.set_markup(_BASE_MARKUP)
                self._current_markup = _BASE_MARKUP

        return True