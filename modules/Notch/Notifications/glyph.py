import random
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from fabric.widgets.box import Box
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer


_GLITCH_CLASSES = [
    "glitch-shift-right",
    "glitch-shift-left",
    "glitch-flicker",
    "glitch-aberration",
    "glitch-heavy",
    "glitch-color-swap",
]

class SideGlyph(Gtk.Overlay):
    def __init__(self, side="left", **kwargs):
        super().__init__(**kwargs)
        
        self.spacer = Box()
        self.spacer.set_size_request(45, 55)
        self.add(self.spacer)
        
        self.set_valign(Gtk.Align.START)
        self.set_margin_top(0) 
        
        transition = "slide-left" if side == "left" else "slide-right"
        self.revealer = Revealer(
            transition_type=transition,
            transition_duration=250
        )
        
        self.lbl = Label(name=f"side-glyph-{side}")
        self.lbl.set_valign(Gtk.Align.CENTER)
        self.lbl.set_justify(Gtk.Justification.CENTER) 
        
        if side == "left":
            self.lbl.set_halign(Gtk.Align.END)
            self.revealer.set_halign(Gtk.Align.END)
            self.set_margin_right(0)
        else:
            self.lbl.set_halign(Gtk.Align.START)
            self.revealer.set_halign(Gtk.Align.START)
            self.set_margin_left(0)

        self.revealer.add(self.lbl)
        self.add_overlay(self.revealer)

        self.show_all()
        self.revealer.set_reveal_child(False)
        
        self._base = '<span font_family="monospace" weight="bold">[!]\n[!]</span>'
        self._glitches = [
            '<span font_family="monospace" weight="bold">|#|\n|#|</span>',
            '<span font_family="monospace" weight="bold">[@]\n[@]</span>',
            '<span font_family="monospace" weight="bold">&lt;*&gt;\n&lt;*&gt;</span>',
            '<span font_family="monospace" weight="bold">/!\\\n\\!/</span>',
            '<span font_family="monospace" weight="bold">\\!/\n/!\\</span>',
            '<span font_family="monospace" weight="bold">!?!\n!?!</span>',
            '<span font_family="monospace" weight="bold">{$}\n{$}</span>',
            '<span font_family="monospace" weight="bold">###\n###</span>',
            '<span font_family="monospace" weight="bold">010\n101</span>'
        ]
        self.lbl.set_markup(self._base)
        
        self._gl_rem = 0
        self._gl_total = 0
        self._gl_tid = None

    def trigger(self):
        if self._gl_tid:
            GLib.source_remove(self._gl_tid)
        self.revealer.set_reveal_child(True)
        self._gl_total = 45 
        self._gl_rem = self._gl_total
        self._gl_tid = GLib.timeout_add(35, self._tick)

    def _tick(self):
        ctx = self.lbl.get_style_context()
        for cls in _GLITCH_CLASSES:
            ctx.remove_class(cls)
        ctx.remove_class("glitching")

        if self._gl_rem <= 0:
            self.revealer.set_reveal_child(False)
            self.lbl.set_markup(self._base)
            self._gl_tid = None
            return False

        progress = 1.0 - self._gl_rem / self._gl_total
        
        if random.random() > progress:
            ctx.add_class("glitching")
            self.lbl.set_markup(random.choice(self._glitches))
            count = 1 if random.random() > 0.4 else 2
            for cls in random.sample(_GLITCH_CLASSES, count):
                ctx.add_class(cls)
        else:
            self.lbl.set_markup(self._base)

        self._gl_rem -= 1
        return True