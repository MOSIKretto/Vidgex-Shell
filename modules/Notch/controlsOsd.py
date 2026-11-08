from fabric.widgets.box import Box
from fabric.widgets.label import Label
from gi.repository import GLib

from services.Controls.brightness import Brightness
from services.Controls.microphone import Microphone
from services.Controls.volume import Volume
import services.icons as icons


DOT_ACTIVE = "●"
DOT_INACTIVE = "○"


def _dots(v: int) -> int:
    return max(0, min(10, int(round(v / 10.0))))


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
        self._cur_dev = "speaker"

        self._icon_lbl = Label(name="osd-icon-label", markup=icons.vol_high, v_align="center")
        self._icon_lbl.set_yalign(0.5)
        self.add(Box(name="osd-icon-box", v_align="center", children=[self._icon_lbl]))

        self._dots = []
        self._ctxs = []
        dots_box = Box(name="osd-dots-box", orientation="h", spacing=2, v_align="center")
        for i in range(10):
            lbl = Label(name="osd-dot", label=DOT_INACTIVE, v_align="center")
            lbl.set_width_chars(1)
            lbl.set_max_width_chars(1)
            lbl.set_xalign(0.5)
            lbl.set_yalign(0.5)
            ctx = lbl.get_style_context()
            ctx.add_class("osd-dot")
            ctx.add_class("inactive")
            self._dots.append(lbl)
            self._ctxs.append(ctx)
            dots_box.add(lbl)
        self.add(dots_box)

        self._val_lbl = Label(name="osd-value-label", label="0%", v_align="center")
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

        self._dev_dots = {k: _dots(v) for k, v in self._dev_levels.items()}

        self._update_display("speaker", vol.volume, vol.muted, is_init=True)
        GLib.idle_add(self._mark_init_done)
        self.show_all()

    def _mark_init_done(self):
        self._init_done = True
        return False

    def _paint(self, i: int, active: bool) -> None:
        ctx = self._ctxs[i]
        ctx.add_class("active" if active else "inactive")
        ctx.remove_class("inactive" if active else "active")
        self._dots[i].set_text(DOT_ACTIVE if active else DOT_INACTIVE)

    def _paint_all(self, n: int) -> None:
        for i in range(10):
            self._paint(i, i < n)

    def _check_change(self, dev: str, val: int, is_muted: bool = False):
        if not self._init_done:
            return
        if val != self._dev_levels[dev] or is_muted != self._dev_muted[dev]:
            self._dev_muted[dev] = is_muted
            self._update_display(dev, val, is_muted)

    def _update_display(self, dev: str, val: int, is_muted: bool = False, is_init: bool = False):
        if dev == "speaker":
            off = is_muted or val <= 0
            if self._vol.is_bluetooth:
                icon = (icons.bluetooth_disconnected if off
                        else icons.bluetooth_connected if val > 74 else icons.bluetooth)
            else:
                icon = icons.vol_mute if off else icons.vol_high if val > 74 else icons.vol_medium
        elif dev == "microphone":
            icon = icons.mic if (val > 0 and not is_muted) else icons.mic_mute
        else:
            icon = (icons.brightness_high if val >= 75
                    else icons.brightness_medium if val >= 24 else icons.brightness_low)
        self._icon_lbl.set_markup(icon)

        eff = 0 if is_muted else val
        self._val_lbl.set_label(f"{eff}%")

        new = _dots(eff)
        old = self._dev_dots[dev]
        old_val = self._dev_levels[dev]

        if self._cur_dev != dev:
            self._cur_dev = dev
            self._paint_all(old)

        if is_init or not self._init_done:
            self._dev_dots[dev], self._dev_levels[dev] = new, eff
            self._paint_all(new)
            return

        if new != old:
            for i in range(min(new, old), max(new, old)):
                self._paint(i, new > old)
        elif eff != old_val:
            self._paint(min(9, max(0, new - 1)), new > 0)

        self._dev_dots[dev], self._dev_levels[dev] = new, eff

        if self._on_changed:
            self._on_changed()

    def cleanup(self):
        for service, hid in self._hids:
            service.disconnect(hid)
        self._hids.clear()