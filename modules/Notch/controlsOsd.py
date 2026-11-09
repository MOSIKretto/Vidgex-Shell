import math

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from fabric.widgets.box import Box
from fabric.widgets.label import Label

from services.Controls.brightness import Brightness
from services.Controls.microphone import Microphone
from services.Controls.volume import Volume
import services.icons as icons


_BAR_W = 168
_BAR_H = 12
_INSET = 2
_DOTS = 10
_DOT_R = 2.8
_DOT_PITCH = 16
_DOT_PAD = 12
_ANIM_MS = 350
_TWO_PI = 2 * math.pi


def ease_out_cubic(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


class OsdBar(Gtk.DrawingArea):
    def __init__(self):
        super().__init__(valign=Gtk.Align.CENTER)
        self.set_name("osd-bar")
        self.set_size_request(_BAR_W, _BAR_H)
        self._ctx = self.get_style_context()
        self._target = 0.0
        self._shown = 0.0
        self._anim = None
        self._colors = None

        self.connect("draw", self._on_draw)
        self.connect("style-updated", self._on_style_updated)
        self.connect("unmap", self._on_unmap)
        self.connect("destroy", lambda _: self._stop())

    def set_fraction(self, frac: float) -> None:
        frac = max(0.0, min(1.0, frac))
        if frac == self._target:
            return
        self._target = frac
        if not self.get_mapped():
            self._stop()
            self._shown = frac
            return
        self._animate(self._shown, frac)

    def _animate(self, a: float, b: float) -> None:
        self._stop()
        t0 = 0

        def tick(_widget, clock):
            nonlocal t0
            now = clock.get_frame_time()
            t0 = t0 or now
            t = min((now - t0) / (_ANIM_MS * 1000), 1.0)
            self._shown = a + (b - a) * ease_out_cubic(t)
            self.queue_draw()
            if t < 1.0:
                return True
            self._anim = None
            return False

        self._anim = self.add_tick_callback(tick)

    def _stop(self) -> None:
        if self._anim is not None:
            self.remove_tick_callback(self._anim)
            self._anim = None

    def _on_unmap(self, _w) -> None:
        self._stop()
        self._shown = self._target

    def _on_style_updated(self, _w) -> None:
        self._colors = None

    def _load_colors(self) -> None:
        state = self._ctx.get_state()
        fill = self._ctx.get_color(state)
        dots = self._ctx.get_color(state | Gtk.StateFlags.ACTIVE)
        self._colors = tuple((c.red, c.green, c.blue, c.alpha) for c in (fill, dots))

    @staticmethod
    def _capsule(cr, x: float, y: float, w: float, h: float) -> None:
        r = min(h / 2, w / 2)
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
        cr.close_path()

    def _on_draw(self, _w, cr) -> bool:
        if self._colors is None:
            self._load_colors()
        fill, dots = self._colors

        w, h = self.get_allocated_width(), self.get_allocated_height()
        Gtk.render_background(self._ctx, cr, 0, 0, w, h)

        cy = h / 2
        cr.set_source_rgba(*dots)
        for i in range(_DOTS):
            cr.new_sub_path()
            cr.arc(_DOT_PAD + i * _DOT_PITCH, cy, _DOT_R, 0, _TWO_PI)
        cr.fill()

        fw = (w - 2 * _INSET) * self._shown
        if fw <= 0:
            return False

        self._capsule(cr, _INSET, _INSET, fw, h - 2 * _INSET)
        cr.set_source_rgba(*fill)
        cr.fill()
        return False


class ControlOSD(Box):
    def __init__(self, on_changed=None, **kwargs):
        super().__init__(
            name="control-osd", orientation="h", spacing=8,
            h_align="center", v_align="center", **kwargs
        )
        self.connect("destroy", lambda _: self.cleanup())

        self._on_changed = on_changed
        self._init_done = False
        self._hids = []
        self._shown_icon = None
        self._shown_text = None

        self._icon_lbl = Label(name="osd-icon-label", v_align="center")
        self._icon_lbl.set_yalign(0.5)
        self.add(Box(name="osd-icon-box", v_align="center", children=[self._icon_lbl]))

        self._bar = OsdBar()
        self.add(self._bar)

        self._val_lbl = Label(name="osd-value-label", v_align="center")
        self._val_lbl.set_yalign(0.5)
        self.add(self._val_lbl)

        self._vol = vol = Volume.get_initial()
        mic = Microphone.get_initial()
        br = Brightness.get_initial()

        def pct(v):
            return int(v * 100 / br.max_screen) if br.max_screen > 0 else 0

        self._dev_levels = {"speaker": vol.volume, "microphone": mic.volume, "screen": 0}
        self._dev_muted = {"speaker": False, "microphone": False, "screen": False}

        self._hids.append((vol, vol.connect("changed", lambda _, v: self._check_change("speaker", v, vol.muted))))
        self._hids.append((mic, mic.connect("changed", lambda _, v: self._check_change("microphone", v, mic.muted))))
        if br.screen_brightness != -1:
            self._dev_levels["screen"] = pct(br.screen_brightness)
            self._hids.append((br, br.connect("screen", lambda _, v: self._check_change("screen", pct(v)))))

        self._render("speaker", vol.volume, vol.muted)
        GLib.idle_add(self._mark_init_done)
        self.show_all()

    def _mark_init_done(self):
        self._init_done = True
        return False

    def _icon_for(self, dev: str, val: int, is_muted: bool) -> str:
        if dev == "speaker":
            off = is_muted or val <= 0
            if self._vol.is_bluetooth:
                return (icons.bluetooth_disconnected if off
                        else icons.bluetooth_connected if val > 74 else icons.bluetooth)
            return icons.vol_mute if off else icons.vol_high if val > 74 else icons.vol_medium
        if dev == "microphone":
            return icons.mic if (val > 0 and not is_muted) else icons.mic_mute
        return (icons.brightness_high if val >= 75
                else icons.brightness_medium if val >= 24 else icons.brightness_low)

    def _render(self, dev: str, val: int, is_muted: bool) -> None:
        icon = self._icon_for(dev, val, is_muted)
        if icon != self._shown_icon:
            self._shown_icon = icon
            self._icon_lbl.set_markup(icon)

        eff = 0 if is_muted else val
        text = f"{eff}%"
        if text != self._shown_text:
            self._shown_text = text
            self._val_lbl.set_label(text)

        self._bar.set_fraction(eff / 100.0)
        self._dev_levels[dev] = eff

    def _check_change(self, dev: str, val: int, is_muted: bool = False):
        if not self._init_done:
            return
        if val != self._dev_levels[dev] or is_muted != self._dev_muted[dev]:
            self._dev_muted[dev] = is_muted
            self._render(dev, val, is_muted)
            if self._on_changed:
                self._on_changed()

    def cleanup(self):
        for service, hid in self._hids:
            service.disconnect(hid)
        self._hids.clear()