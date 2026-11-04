import math
import os
import random

import gi
gi.require_version("PangoCairo", "1.0")
gi.require_version("Gdk", "3.0")

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.eventbox import EventBox
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer
from gi.repository import GLib, Gio, Gdk, Gtk, PangoCairo

from services.bluetooth import Subscriptions, battery_percentage, device_slot
import services.icons as icons


_POWER_SUPPLY_DIR = "/sys/class/power_supply"
_FALLBACK_POLL_SECONDS = 30
_PCT_WIDTH_PX = 34
_RETRY_DELAY_MS = 2000
_THRESHOLD_PANEL_TRANSITION_MS = 360
_SLIDER_COL_WIDTH_PX = 157
_HEADER_HEIGHT_PX = 16

_GLITCH_CLASSES = ("glitch-aberration", "glitch-color-swap")
_TICK_GLITCH_CLASSES = (
    "glitch-shift-right",
    "glitch-shift-left",
    "glitch-flicker",
    "glitch-aberration",
    "glitch-heavy",
    "glitch-color-swap",
)

_TICK_BELOW_CLASS = "below-threshold"
_TICK_AT_CLASS = "at-threshold"
_TICK_ABOVE_CLASS = "above-threshold"
_TICK_STATE_CLASSES = (_TICK_BELOW_CLASS, _TICK_AT_CLASS, _TICK_ABOVE_CLASS)

_THRESH_STEP = 10
_THRESH_MIN = _THRESH_STEP
_THRESH_MAX = 100
_THRESH_DEFAULT = 20
_THRESH_CONFIG_DIR = os.path.join(GLib.get_user_config_dir(), "vidgex-shell")
_THRESH_CONFIG_PATH = os.path.join(_THRESH_CONFIG_DIR, "battery_threshold")

_UNSET = object()

_MODE_PULSE_SPEC = {
    "power-saver": (
        0.30, 0.040, 2.60,
        ((-0.24, 0.0), (-0.10, -3.2), (0.06, -3.2), (0.20, 0.0)),
    ),
    "balanced": (
        0.64, 0.068, 2.20,
        ((-0.34, 0.0), (-0.20, 2.8), (-0.04, -8.0), (0.12, 5.0), (0.28, 0.0)),
    ),
    "performance": (
        1.00, 0.105, 1.45,
        ((-0.46, 0.0), (-0.32, -7.6), (-0.16, 6.2), (0.00, -8.6), (0.16, 6.8), (0.32, -4.4), (0.46, 0.0)),
    ),
}

_MODE_TITLES = {
    "power-saver": "Power Saver Mode",
    "balanced": "Balanced Mode",
    "performance": "Performance Mode",
}


def _set_css_class(widget: Gtk.Widget, cls_name: str, enabled: bool) -> bool:
    ctx = widget.get_style_context()
    if ctx.has_class(cls_name) == enabled:
        return False
    if enabled:
        ctx.add_class(cls_name)
    else:
        ctx.remove_class(cls_name)
    return True

def _build_trigger_button(icon_label: Label, *extra_widgets: Label) -> Button:
    wrapper = Box(
        name="button-bar-content",
        orientation="h", spacing=0,
        h_align="center", v_align="center",
        h_expand=False, v_expand=False,
        children=[icon_label, *extra_widgets],
    )
    return Button(name="button-bar", v_align="center", can_focus=False, child=wrapper)


class _GlitchFX:
    FRAMES = 7
    FRAME_MS = 35

    __slots__ = (
        "_widget", "_classes", "_base_class", "_state_classes",
        "_rem", "_timer_id", "_target", "_destroyed",
    )

    def __init__(self, widget, classes, base_class=None, state_classes=()):
        self._widget = widget
        self._classes = classes
        self._base_class = base_class
        self._state_classes = state_classes
        self._rem = 0
        self._timer_id = None
        self._target = None
        self._destroyed = False

    def trigger(self, target_class: str | None = None) -> None:
        if self._destroyed:
            return
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        self._target = target_class
        if not self._widget.get_mapped():
            ctx = self._widget.get_style_context()
            self._clear(ctx)
            self._apply_target(ctx)
            return
        self._rem = self.FRAMES
        self._timer_id = GLib.timeout_add(self.FRAME_MS, self._tick)

    def _clear(self, ctx) -> None:
        for cls in self._classes:
            ctx.remove_class(cls)
        if self._base_class is not None:
            ctx.remove_class(self._base_class)

    def _apply_target(self, ctx) -> None:
        if self._target is not None:
            for cls in self._state_classes:
                ctx.remove_class(cls)
            ctx.add_class(self._target)

    def _tick(self) -> bool:
        if self._destroyed or not self._widget.get_mapped():
            self._timer_id = None
            ctx = self._widget.get_style_context()
            self._clear(ctx)
            self._apply_target(ctx)
            return False

        ctx = self._widget.get_style_context()
        self._clear(ctx)

        self._rem -= 1
        if self._rem <= 0:
            self._timer_id = None
            self._apply_target(ctx)
            return False

        if self._base_class is not None:
            ctx.add_class(self._base_class)
        count = 1 if random.random() > 0.4 else 2
        for cls in random.sample(self._classes, min(count, len(self._classes))):
            ctx.add_class(cls)
        return True

    def stop(self) -> None:
        self._destroyed = True
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        self._clear(self._widget.get_style_context())

def _read_uevent(bat_path: str) -> dict[str, str]:
    try:
        with open(os.path.join(bat_path, "uevent")) as handle:
            return dict(line.strip().split("=", 1) for line in handle if "=" in line)
    except OSError:
        return {}

def _find_battery_path() -> str | None:
    if not os.path.isdir(_POWER_SUPPLY_DIR):
        return None
    for entry in sorted(os.listdir(_POWER_SUPPLY_DIR)):
        node = os.path.join(_POWER_SUPPLY_DIR, entry)
        if _read_uevent(node).get("POWER_SUPPLY_TYPE") == "Battery":
            return node
    return None

def _calc_eta_seconds(target_pct: float, charging: bool | None, now: int, full: int, rate: int) -> float:
    if rate <= 0 or full <= 0 or charging is None:
        return 0.0
    target = full * (target_pct / 100.0)
    diff = (target - now) if charging else (now - target)
    return max(0.0, diff / rate * 3600.0) if diff > 0 else 0.0

def _format_eta(seconds: float) -> str:
    if seconds <= 0:
        return "—"
    hours, minutes = divmod(int(seconds // 60), 60)
    return f"{hours}h {minutes:02d}m" if hours > 0 else f"{minutes}m"


_bat_subs: list = []
_bat_path: str | None = _find_battery_path()
_bat_monitor: Gio.FileMonitor | None = None
_bat_timer: int | None = None
_bat_idle_id: int | None = None
_bat_pct: float = 0.0
_bat_chg: bool | None = None
_bat_time: float = 0.0
_bat_now: int = 0
_bat_full: int = 0
_bat_rate: int = 0
_power_mode: str = "balanced"


def seconds_to_threshold(threshold_pct: float) -> float:
    if _bat_path is None:
        return 0.0
    return _calc_eta_seconds(threshold_pct, _bat_chg, _bat_now, _bat_full, _bat_rate)

def _set_power_mode(mode: str) -> None:
    global _power_mode
    if mode not in _MODE_PULSE_SPEC or mode == _power_mode:
        return
    _power_mode = mode
    for widget in list(_bat_subs):
        widget._upd()

def _bat_refresh() -> None:
    global _bat_pct, _bat_chg, _bat_time, _bat_now, _bat_full, _bat_rate
    if _bat_path is None:
        _bat_pct, _bat_chg, _bat_time = 0.0, None, 0.0
        _bat_now = _bat_full = _bat_rate = 0
    else:
        data = _read_uevent(_bat_path)
        _bat_chg = data.get("POWER_SUPPLY_STATUS") in ("Charging", "Full")
        _bat_pct = float(int(data.get("POWER_SUPPLY_CAPACITY") or 0))
        _bat_now = abs(int(data.get("POWER_SUPPLY_ENERGY_NOW") or data.get("POWER_SUPPLY_CHARGE_NOW") or 0))
        _bat_full = abs(int(data.get("POWER_SUPPLY_ENERGY_FULL") or data.get("POWER_SUPPLY_CHARGE_FULL") or 0))
        _bat_rate = abs(int(data.get("POWER_SUPPLY_POWER_NOW") or data.get("POWER_SUPPLY_CURRENT_NOW") or 0))
        _bat_time = _calc_eta_seconds(100.0 if _bat_chg else 0.0, _bat_chg, _bat_now, _bat_full, _bat_rate)
    for widget in list(_bat_subs):
        widget._upd()


def _bat_tick() -> bool:
    _bat_refresh()
    return True

def _bat_on_uevent_changed(*_args) -> None:
    global _bat_idle_id
    if _bat_idle_id is None:
        _bat_idle_id = GLib.idle_add(_bat_flush_pending)

def _bat_flush_pending() -> bool:
    global _bat_idle_id
    _bat_idle_id = None
    if _bat_subs:
        _bat_refresh()
    return False

def _bat_sub(widget) -> None:
    global _bat_monitor, _bat_timer
    first = not _bat_subs
    if widget not in _bat_subs:
        _bat_subs.append(widget)
    if first:
        if _bat_path is not None:
            uevent_file = Gio.File.new_for_path(os.path.join(_bat_path, "uevent"))
            _bat_monitor = uevent_file.monitor_file(Gio.FileMonitorFlags.NONE, None)
            _bat_monitor.connect("changed", _bat_on_uevent_changed)
            _bat_timer = GLib.timeout_add_seconds(
                _FALLBACK_POLL_SECONDS, _bat_tick, priority=GLib.PRIORITY_LOW
            )
        _bat_refresh()
    else:
        widget._upd()

def _bat_unsub(widget) -> None:
    global _bat_monitor, _bat_timer, _bat_idle_id
    if widget in _bat_subs:
        _bat_subs.remove(widget)
    if not _bat_subs:
        if _bat_monitor is not None:
            _bat_monitor.cancel()
            _bat_monitor = None
        if _bat_timer is not None:
            GLib.source_remove(_bat_timer)
            _bat_timer = None
        if _bat_idle_id is not None:
            GLib.source_remove(_bat_idle_id)
            _bat_idle_id = None


def get_snapshot() -> tuple[float, bool | None, float]:
    return _bat_pct, _bat_chg, _bat_time

def has_battery() -> bool:
    return _bat_path is not None

def _bat_icon(pct: float, charging: bool) -> str:
    if charging:
        return icons.battery_charging
    threshold = _threshold_pct
    if pct <= threshold:
        return icons.bat_low
    band = (100.0 - threshold) / 4.0
    if band <= 0 or pct >= threshold + 3 * band:
        return icons.battery_4
    if pct >= threshold + 2 * band:
        return icons.battery_3
    if pct >= threshold + band:
        return icons.battery_2
    return icons.battery_1

def _quantize_threshold(value: int) -> int:
    return round(max(_THRESH_MIN, min(_THRESH_MAX, value)) / _THRESH_STEP) * _THRESH_STEP

def _load_threshold() -> int:
    try:
        with open(_THRESH_CONFIG_PATH) as handle:
            return _quantize_threshold(int(handle.read().strip()))
    except (OSError, ValueError):
        return _THRESH_DEFAULT

def _save_threshold(value: int) -> None:
    try:
        os.makedirs(_THRESH_CONFIG_DIR, exist_ok=True)
        with open(_THRESH_CONFIG_PATH, "w") as handle:
            handle.write(str(value))
    except OSError:
        pass


_threshold_pct = _load_threshold()


def get_threshold() -> int:
    return _threshold_pct

def set_threshold(value: int) -> None:
    global _threshold_pct
    value = _quantize_threshold(value)
    if value == _threshold_pct:
        return
    _threshold_pct = value
    _save_threshold(value)
    for widget in list(_bat_subs):
        widget.recheck_threshold()


class BatteryTrigger:
    def __init__(self):
        self._lv = -1.0
        self._lc = None
        self._last_pct_text = None
        self._destroyed = False

        self._icon = Label(name="metrics-icon", markup=icons.battery_3)
        self._pct = Label(name="metrics-level", style_classes="bat", label="—")
        self._pct.set_xalign(1.0)
        self._pct.set_size_request(_PCT_WIDTH_PX, -1)
        self._pct_glitch = _GlitchFX(self._pct, _GLITCH_CLASSES, base_class="glitching")
        self.button = _build_trigger_button(self._icon, self._pct)
        self.button.connect("destroy", lambda *_: self.cleanup())

        self.mode = "balanced"
        self._ui_ready = False
        self._synced = False
        self._timer_id = None
        self._auto_pending_mode = None
        self._auto_pending_notify = True

        _bat_sub(self)
        try:
            proc = Gio.Subprocess.new(
                ["powerprofilesctl", "get"],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE,
            )
            proc.communicate_utf8_async(None, None, self._on_init_mode_done)
        except GLib.Error:
            self._on_mode_ready()

    def _on_init_mode_done(self, proc, result) -> None:
        if self._destroyed:
            return
        try:
            ok, out, _ = proc.communicate_utf8_finish(result)
            if ok and out:
                mode = out.strip()
                if mode in _MODE_PULSE_SPEC:
                    self.mode = mode
        except GLib.Error:
            pass
        self._on_mode_ready()

    def _on_mode_ready(self) -> None:
        self._ui_ready = True
        _set_power_mode(self.mode)
        self._evaluate_profile()

    def _upd(self) -> None:
        val, chg = _bat_pct, _bat_chg
        if val == self._lv and chg == self._lc:
            return
        self._lv, self._lc = val, chg
        self.button.set_visible(val != 0)
        self._icon.set_markup(_bat_icon(val, bool(chg)))

        pct_text = f"{int(val)}%"
        if pct_text != self._last_pct_text:
            changed = self._last_pct_text is not None
            self._last_pct_text = pct_text
            self._pct.set_label(pct_text)
            if changed:
                self._pct_glitch.trigger()

        self._evaluate_profile()

    def recheck_threshold(self) -> None:
        if self._lv < 0:
            return
        self._icon.set_markup(_bat_icon(self._lv, bool(self._lc)))
        self._evaluate_profile()

    def _target_mode(self) -> str:
        if self._lc:
            return "performance"
        return "power-saver" if self._lv <= _threshold_pct else "balanced"

    def _evaluate_profile(self) -> None:
        if not self._ui_ready or self._lv < 0:
            return
        tgt = self._target_mode()
        if not self._synced:
            self._synced = True
            if tgt != self.mode:
                self._set_profile_async(tgt, False)
            return
        if tgt != self.mode and tgt != self._auto_pending_mode:
            self._schedule_switch(tgt, True, 300)

    def _schedule_switch(self, mode: str, notify: bool, delay_ms: int) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
        self._auto_pending_mode = mode
        self._auto_pending_notify = notify
        self._timer_id = GLib.timeout_add(delay_ms, self._apply_auto_switch)

    def _apply_auto_switch(self) -> bool:
        self._timer_id = None
        if not self._destroyed and self._auto_pending_mode:
            mode, notify = self._auto_pending_mode, self._auto_pending_notify
            self._auto_pending_mode = None
            self._set_profile_async(mode, notify)
        return False

    def _set_profile_async(self, mode: str, notify: bool) -> None:
        try:
            proc = Gio.Subprocess.new(["powerprofilesctl", "set", mode], Gio.SubprocessFlags.NONE)
        except GLib.Error:
            self._schedule_switch(mode, notify, _RETRY_DELAY_MS)
            return
        proc.wait_check_async(None, self._on_profile_set_done, (mode, notify))

    def _on_profile_set_done(self, proc, result, data) -> None:
        if self._destroyed:
            return
        mode, notify = data
        try:
            ok = proc.wait_check_finish(result)
        except GLib.Error:
            ok = False
        if not ok:
            self._schedule_switch(mode, notify, _RETRY_DELAY_MS)
            return
        self.mode = mode
        _set_power_mode(mode)
        if notify:
            try:
                Gio.Subprocess.new(
                    ["notify-send", "-a", "Vidgex-Shell", "Power Profile", f"Auto switched to {_MODE_TITLES[mode]}"],
                    Gio.SubprocessFlags.NONE,
                )
            except GLib.Error:
                pass

    def cleanup(self) -> None:
        self._destroyed = True
        self._pct_glitch.stop()
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        _bat_unsub(self)


class BatteryRing(Gtk.DrawingArea):
    SIZE = 43
    RING_WIDTH = 4
    _INNER_GAP = 2.0

    def __init__(self, icon_markup: str):
        super().__init__()
        self.get_style_context().add_class("battery-ring")
        self.set_size_request(self.SIZE, self.SIZE)
        self.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self._icon_markup = icon_markup
        self._percentage: float | None = None
        self._layout = None
        self._layout_size = (0, 0)
        self.connect("draw", self._on_draw)

    def set_icon(self, markup: str) -> None:
        if self._icon_markup == markup:
            return
        self._icon_markup = markup
        self._layout = None
        self.queue_draw()

    def set_percentage(self, pct: float | None) -> None:
        if self._percentage == pct:
            return
        self._percentage = pct
        self.queue_draw()

    def _on_draw(self, widget, cr) -> bool:
        ctx = widget.get_style_context()
        icon_color = ctx.get_color(ctx.get_state())

        parent = widget.get_parent()
        ring_ctx = parent.get_style_context() if parent is not None else ctx
        ring_color = ring_ctx.get_color(Gtk.StateFlags.NORMAL)

        w = h = self.SIZE
        cx = cy = w / 2.0
        radius = (w - self.RING_WIDTH) / 2.0
        inner_radius = radius - self.RING_WIDTH / 2.0 - self._INNER_GAP

        cr.save()
        cr.arc(cx, cy, inner_radius, 0, 2 * math.pi)
        cr.clip()
        Gtk.render_background(ctx, cr, 0, 0, w, h)
        cr.restore()

        cr.set_line_width(self.RING_WIDTH)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.set_source_rgba(ring_color.red, ring_color.green, ring_color.blue, 0.18)
        cr.stroke()

        if self._percentage is not None:
            frac = max(0.0, min(1.0, self._percentage / 100.0))
            start = -math.pi / 2.0
            cr.arc(cx, cy, radius, start, start + 2 * math.pi * frac)
            cr.set_source_rgba(ring_color.red, ring_color.green, ring_color.blue, 0.95)
            cr.stroke()

        if self._layout is None:
            self._layout = widget.create_pango_layout(None)
            self._layout.set_markup(self._icon_markup, -1)
            self._layout_size = self._layout.get_pixel_size()
        lw, lh = self._layout_size
        cr.move_to(cx - lw / 2.0, cy - lh / 2.0)
        cr.set_source_rgba(icon_color.red, icon_color.green, icon_color.blue, 1.0)
        PangoCairo.show_layout(cr, self._layout)
        return False


class PowerModePulse(Gtk.DrawingArea):
    SIZE = BatteryRing.SIZE
    RING_WIDTH = BatteryRing.RING_WIDTH
    _HALF_SPAN = 11.5
    _FRAME_MS = 40
    _GLITCH_FRAMES = 7

    def __init__(self, mode: str = "balanced"):
        super().__init__()
        self.get_style_context().add_class("battery-ring")
        self.set_size_request(self.SIZE, self.SIZE)
        self._mode = mode if mode in _MODE_PULSE_SPEC else "balanced"
        self._phase = 0.0
        self._running = False
        self._timer_id = None
        self._destroyed = False
        self._glitch_rem = 0
        self._split_x = 0.0
        self._split_y = 0.0
        self._gain = 1.0
        self.connect("draw", self._on_draw)
        self.connect("map", self._sync_timer)
        self.connect("unmap", self._sync_timer)
        self.connect("destroy", self._on_destroy)

    def set_mode(self, mode: str) -> None:
        if mode not in _MODE_PULSE_SPEC:
            mode = "balanced"
        if mode == self._mode:
            return
        self._mode = mode
        self.trigger_glitch()
        self.queue_draw()

    def set_running(self, running: bool) -> None:
        self._running = running
        self._sync_timer()

    def trigger_glitch(self) -> None:
        if self._destroyed or not self._running or not self.get_mapped():
            return
        self._glitch_rem = self._GLITCH_FRAMES
        self._step_glitch()
        self._sync_timer()
        self.queue_draw()

    def stop(self) -> None:
        self._running = False
        self._glitch_rem = 0
        self._split_x = self._split_y = 0.0
        self._gain = 1.0
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    def _on_destroy(self, *_args) -> None:
        self._destroyed = True
        self.stop()

    def _sync_timer(self, *_args) -> None:
        need_timer = (not self._destroyed) and self.get_mapped() and (self._running or self._glitch_rem > 0)
        if need_timer and self._timer_id is None:
            self._timer_id = GLib.timeout_add(self._FRAME_MS, self._on_tick)
        elif not need_timer and self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    def _step_glitch(self) -> None:
        self._split_x = random.choice((-2.0, -1.4, 1.4, 2.0))
        self._split_y = random.uniform(-1.1, 1.1)
        self._gain = random.choice((0.65, 1.28, 1.40, -0.85))

    def _on_tick(self) -> bool:
        if self._destroyed or not self.get_mapped():
            self._timer_id = None
            return False

        _, speed, period, _ = _MODE_PULSE_SPEC[self._mode]
        self._phase = (self._phase + speed) % period

        if self._glitch_rem > 0:
            self._glitch_rem -= 1
            if self._glitch_rem > 0:
                self._step_glitch()
            else:
                self._split_x = self._split_y = 0.0
                self._gain = 1.0

        self.queue_draw()
        if not self._running and self._glitch_rem <= 0:
            self._timer_id = None
            return False
        return True

    def _trace_running_wave(self, cr, cx: float, cy: float, period: float,
                            shape: tuple[tuple[float, float], ...],
                            phase: float, dx: float, dy: float, gain: float) -> None:
        base_x = cx + dx
        base_y = cy + dy
        cr.move_to(base_x - self._HALF_SPAN, base_y)
        for k in range(math.floor((-1.5 - phase) / period), math.ceil((1.5 - phase) / period) + 1):
            center = phase + k * period
            for ox, py in shape:
                nx = center + ox
                if -1.0 < nx < 1.0:
                    env = min(1.0, (1.0 - nx * nx) * 2.5)
                    cr.line_to(base_x + nx * self._HALF_SPAN, base_y + py * gain * env)
        cr.line_to(base_x + self._HALF_SPAN, base_y)

    def _on_draw(self, widget, cr) -> bool:
        ctx = widget.get_style_context()
        color = ctx.get_color(ctx.get_state())
        r, g, b = color.red, color.green, color.blue

        cx = cy = self.SIZE / 2.0
        radius = (self.SIZE - self.RING_WIDTH) / 2.0
        frac, _, period, shape = _MODE_PULSE_SPEC[self._mode]
        glitching = self._glitch_rem > 0

        cr.set_line_width(self.RING_WIDTH)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.set_source_rgba(r, g, b, 0.18)
        cr.stroke()

        start = -math.pi / 2.0
        cr.arc_negative(cx, cy, radius, start, start - 2 * math.pi * frac)
        cr.set_source_rgba(r, g, b, 0.95)
        cr.stroke()

        cr.set_line_cap(1)
        cr.set_line_join(1)

        sx = self._split_x if glitching else 0.75
        sy = self._split_y if glitching else 0.25
        ghost_alpha = 0.80 if glitching else 0.42
        gain = self._gain if glitching else 1.0
        lag = 0.16 if glitching else 0.08

        cr.set_line_width(1.5)
        self._trace_running_wave(cr, cx, cy, period, shape, (self._phase - lag) % period, -sx, -sy, gain)
        cr.set_source_rgba(0.0, 0.898, 1.0, ghost_alpha)
        cr.stroke()

        self._trace_running_wave(cr, cx, cy, period, shape, (self._phase - lag * 0.5) % period, sx, sy, gain)
        cr.set_source_rgba(1.0, 0.0, 0.333, ghost_alpha)
        cr.stroke()

        cr.set_line_width(2.0)
        self._trace_running_wave(cr, cx, cy, period, shape, self._phase, 0.0, 0.0, gain)
        cr.set_source_rgba(r, g, b, 1.0)
        cr.stroke()
        return False


class BatteryCircle(Box):
    def __init__(self, icon_markup: str):
        super().__init__(name="battery-circle", orientation="v", spacing=2,
                         h_align="center", v_align="start")
        self._ring = BatteryRing(icon_markup)
        self._ring.connect("enter-notify-event", self._on_enter)
        self._ring.connect("leave-notify-event", self._on_leave)

        self._pct_label = Label(name="battery-circle-pct", label="—")
        self._reveal = Revealer(
            name="battery-circle-reveal",
            transition_type="slide-down",
            transition_duration=220,
            child_revealed=False,
            child=self._pct_label,
        )
        self._wrap = Box(orientation="v", h_align="center")
        self._wrap.get_style_context().add_class("battery-circle-wrap")
        self._wrap.add(self._ring)

        self.add(self._wrap)
        self.add(self._reveal)
        self._hoverable = True
        self._is_trigger = False

    def _set_ring_class(self, cls_name: str, enabled: bool) -> None:
        if _set_css_class(self._ring, cls_name, enabled):
            self._ring.queue_draw()

    def set_trigger(self, is_trigger: bool) -> None:
        self._is_trigger = is_trigger
        self._set_ring_class("battery-trigger", is_trigger)

    def set_active(self, active: bool) -> None:
        self._set_ring_class("active", active)

    def set_pressed(self, pressed: bool) -> None:
        self._set_ring_class("pressed", pressed)

    def set_hoverable(self, hoverable: bool) -> None:
        if self._hoverable == hoverable:
            return
        self._hoverable = hoverable
        if not hoverable:
            _set_css_class(self._wrap, "lifted", False)
            self._reveal.set_reveal_child(False)

    def set_state(self, icon_markup: str, percentage: float | None, label_text: str) -> None:
        self._ring.set_icon(icon_markup)
        self._ring.set_percentage(percentage)
        if self._pct_label.get_label() != label_text:
            self._pct_label.set_label(label_text)

    def set_empty(self, empty: bool) -> None:
        _set_css_class(self, "empty", empty)

    def _on_enter(self, *_args) -> bool:
        if self._is_trigger:
            self._ring.set_state_flags(Gtk.StateFlags.PRELIGHT, False)
            self._set_ring_class("hovered", True)
        if self._hoverable:
            _set_css_class(self._wrap, "lifted", True)
            self._reveal.set_reveal_child(True)
        return False

    def _on_leave(self, *_args) -> bool:
        if self._is_trigger:
            self._ring.unset_state_flags(Gtk.StateFlags.PRELIGHT)
            self._set_ring_class("hovered", False)
            self._set_ring_class("pressed", False)
        _set_css_class(self._wrap, "lifted", False)
        self._reveal.set_reveal_child(False)
        return False


class BatteryThresholdSlider(EventBox):
    SEGMENTS = 10
    STICK_WIDTH = 10
    STICK_HEIGHT = 20
    STICK_GAP = 6

    def __init__(self, initial_pct: int, on_drag, on_commit):
        self._on_drag = on_drag
        self._on_commit = on_commit
        self._dragging = False
        self._ticks = []
        self._tick_glitches = []
        for _ in range(self.SEGMENTS):
            tick = Box(style_classes=["battery-threshold-tick"], h_expand=True)
            tick.set_size_request(self.STICK_WIDTH, self.STICK_HEIGHT)
            self._ticks.append(tick)
            self._tick_glitches.append(_GlitchFX(tick, _TICK_GLITCH_CLASSES, state_classes=_TICK_STATE_CLASSES))
        row = Box(
            name="battery-threshold-row", orientation="h",
            spacing=self.STICK_GAP, h_align="fill", h_expand=True,
            v_align="center", children=self._ticks,
        )
        super().__init__(
            name="battery-threshold-slider", h_align="fill", h_expand=True,
            v_align="center", child=row,
        )

        self._index = max(0, min(self.SEGMENTS - 1, round(initial_pct / _THRESH_STEP) - 1))
        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.connect("button-press-event", self._on_press)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("button-release-event", self._on_release)
        self.connect("destroy", self._on_destroy)
        self._apply_classes()

    def get_percentage(self) -> int:
        return (self._index + 1) * _THRESH_STEP

    def cancel_drag(self) -> None:
        self._dragging = False

    def _tick_class(self, i: int) -> str:
        if i < self._index:
            return _TICK_BELOW_CLASS
        return _TICK_AT_CLASS if i == self._index else _TICK_ABOVE_CLASS

    def _x_to_index(self, x: float) -> int:
        width = self.get_allocated_width()
        if width <= 0:
            return self._index
        return max(0, min(self.SEGMENTS - 1, int(x // (width / self.SEGMENTS))))

    def _set_index(self, idx: int) -> None:
        if idx == self._index:
            return
        old_index, self._index = self._index, idx
        lo, hi = (old_index, idx) if old_index < idx else (idx, old_index)
        for i in range(lo, hi + 1):
            self._tick_glitches[i].trigger(self._tick_class(i))
        self._on_drag(self.get_percentage())

    def _apply_classes(self) -> None:
        for i, tick in enumerate(self._ticks):
            ctx = tick.get_style_context()
            for cls in _TICK_STATE_CLASSES:
                ctx.remove_class(cls)
            ctx.add_class(self._tick_class(i))

    def _on_press(self, _widget, event) -> bool:
        if event.button != 1:
            return False
        self._dragging = True
        self._set_index(self._x_to_index(event.x))
        return True

    def _on_motion(self, _widget, event) -> bool:
        if not self._dragging:
            return False
        self._set_index(self._x_to_index(event.x))
        return True

    def _on_release(self, _widget, _event) -> bool:
        if not self._dragging:
            return False
        self._dragging = False
        self._on_commit(self.get_percentage())
        return True

    def _on_destroy(self, *_args) -> None:
        for glitch in self._tick_glitches:
            glitch.stop()


class BatteryTab(Box):
    _SLOT_ICON = {
        "headphones": icons.headphones,
        "mouse": icons.mouse,
        "keyboard": icons.keyboard,
        "other": icons.bluetooth_connected,
    }
    _SLOT_ORDER = ("headphones", "mouse", "keyboard")
    _BT_SLOTS = 4

    def __init__(self, bluetooth_client=None, **kwargs):
        super().__init__(name="battery-tab", orientation="v", spacing=0,
                         h_align="start", v_align="start", v_expand=False,
                         h_expand=True, **kwargs)
        self._bt_client = bluetooth_client
        self._bt_subs = Subscriptions()
        self._bt_watched: dict[str, object] = {}
        self._bt_pct_state: dict[str, tuple[float | None, object]] = {}
        self._active = False
        self._expanded = False
        self._last_pct: int | None = None
        self.connect("destroy", lambda *_: self.deactivate())
        self._build()

    def _build(self) -> None:
        self._device = BatteryCircle(icons.battery_3)
        self._device.set_hoverable(False)
        self._device.set_trigger(True)

        self._device_event = EventBox(child=self._device)
        self._device_event.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
        )
        self._device_event.connect("button-press-event", self._on_device_pressed)
        self._device_event.connect("button-release-event", self._on_device_released)
        self._device_event.set_margin_top(4)

        self._bt_circles = [BatteryCircle(icons.plus) for _ in range(self._BT_SLOTS)]
        for circle in self._bt_circles:
            circle.set_hoverable(False)
            circle.set_empty(True)

        self._bt_row = Box(
            name="battery-bt-row", orientation="h", spacing=14,
            h_align="start", v_align="start", children=self._bt_circles,
        )
        self._bt_spacer = Box(name="battery-bt-spacer")
        self._bt_spacer.set_size_request(-1, 4)

        self._bt_column = Box(
            name="battery-bt-column", orientation="v", spacing=0,
            h_align="start", v_align="start",
            children=[self._bt_spacer, self._bt_row],
        )
        self._bt_row_revealer = Revealer(
            name="battery-bt-revealer", transition_type="slide-left",
            transition_duration=_THRESHOLD_PANEL_TRANSITION_MS,
            child_revealed=True, child=self._bt_column,
        )

        self._threshold_time_label = Label(
            name="battery-threshold-time", h_align="start", v_align="center", label="—",
        )
        self._threshold_time_label.set_xalign(0.0)
        self._threshold_time_label.set_size_request(_SLIDER_COL_WIDTH_PX, _HEADER_HEIGHT_PX)

        header_row = Box(
            name="battery-slider-header", orientation="h", spacing=0,
            h_align="fill", v_align="center",
            children=[self._threshold_time_label],
        )
        header_row.set_size_request(_SLIDER_COL_WIDTH_PX, _HEADER_HEIGHT_PX)

        self._slider = BatteryThresholdSlider(
            _threshold_pct, on_drag=self._on_threshold_drag, on_commit=set_threshold,
        )
        left_col = Box(
            name="battery-slider-left", orientation="v", spacing=6,
            h_align="start", v_align="center",
            children=[header_row, self._slider],
        )
        left_col.set_size_request(_SLIDER_COL_WIDTH_PX, BatteryRing.SIZE)

        self._mode_pulse = PowerModePulse(_power_mode)

        slider_panel = Box(
            name="battery-slider-panel", orientation="h", spacing=14,
            h_align="start", v_align="start",
            children=[left_col, self._mode_pulse],
        )
        slider_panel.set_margin_top(4)

        self._slider_revealer = Revealer(
            name="battery-slider-revealer", transition_type="slide-right",
            transition_duration=_THRESHOLD_PANEL_TRANSITION_MS,
            child_revealed=False,
            h_align="start", h_expand=False, v_align="start", child=slider_panel,
        )

        self._top_row = Box(
            name="battery-tab-row", orientation="h", spacing=0,
            h_align="start", v_align="start", h_expand=True,
            children=[self._device_event, self._bt_row_revealer, self._slider_revealer],
        )

        self.add(self._top_row)
        self._mode_pulse.set_mode(_power_mode)
        self._apply_device_visibility()

    def _apply_device_visibility(self) -> None:
        visible = has_battery()
        self._device_event.set_no_show_all(not visible)
        self._device_event.set_visible(visible)

    def _on_device_pressed(self, _widget, event) -> bool:
        if not has_battery() or event.button != 1:
            return False
        self._device.set_pressed(True)
        self._set_expanded(not self._expanded)
        return True

    def _on_device_released(self, _widget, event) -> bool:
        if event.button != 1:
            return False
        self._device.set_pressed(False)
        return True

    def _set_expanded(self, expanded: bool) -> None:
        if self._expanded == expanded:
            return
        self._expanded = expanded
        self._device.set_active(expanded)
        self._bt_row_revealer.set_reveal_child(not expanded)
        self._slider_revealer.set_reveal_child(expanded)
        if expanded:
            self._update_threshold_time()
            self._mode_pulse.set_mode(_power_mode)
            self._mode_pulse.set_running(self._active)
        else:
            self._mode_pulse.stop()

    def _on_threshold_drag(self, _pct: int) -> None:
        self._update_threshold_time()

    def _update_threshold_time(self) -> None:
        if _bat_chg:
            text = f"{_format_eta(_bat_time)} to full"
        else:
            threshold = self._slider.get_percentage()
            text = f"{_format_eta(seconds_to_threshold(threshold))} to {threshold}%"
        if self._threshold_time_label.get_label() != text:
            self._threshold_time_label.set_label(text)

    def activate(self) -> None:
        if self._active:
            return
        self._active = True
        self._apply_device_visibility()
        _bat_sub(self)
        self._upd()
        if self._expanded:
            self._mode_pulse.set_running(True)
        if self._bt_client is not None:
            self._bt_subs.connect(self._bt_client, "device-added", self._refresh_bt)
            self._bt_subs.connect(self._bt_client, "device-removed", self._refresh_bt)
            self._refresh_bt()

    def deactivate(self) -> None:
        if not self._active:
            return
        self._active = False
        self._last_pct = None
        self._device.set_pressed(False)
        self._mode_pulse.stop()
        _bat_unsub(self)
        self._bt_subs.clear()
        self._bt_watched.clear()
        self._bt_pct_state.clear()
        self._slider.cancel_drag()

    def _upd(self) -> None:
        pct, charging = _bat_pct, _bat_chg
        pct_int = int(pct)
        if self._last_pct is not None and pct_int != self._last_pct:
            self._mode_pulse.trigger_glitch()
        self._last_pct = pct_int
        self._device.set_state(_bat_icon(pct, bool(charging)), pct, f"{pct_int}%")
        self._mode_pulse.set_mode(_power_mode)
        if self._expanded:
            self._update_threshold_time()

    def recheck_threshold(self) -> None:
        self._upd()

    def _stable_pct(self, dev) -> float | None:
        raw = battery_percentage(dev)
        committed, pending = self._bt_pct_state.get(dev.address, (raw, raw))
        if raw in (committed, pending):
            committed, pending = raw, _UNSET
        else:
            pending = raw
        self._bt_pct_state[dev.address] = (committed, pending)
        return committed

    def _refresh_bt(self, *_args) -> None:
        all_devices = list(self._bt_client.devices)
        live = {dev.address: dev for dev in all_devices}
        for address, old in list(self._bt_watched.items()):
            if live.get(address) is not old:
                self._bt_subs.release(self._bt_watched.pop(address))
                self._bt_pct_state.pop(address, None)
        for address, dev in live.items():
            if address not in self._bt_watched:
                self._bt_subs.connect(dev, "changed", self._refresh_bt)
                self._bt_watched[address] = dev

        by_slot: dict[str, object] = {}
        other = None
        for dev in all_devices:
            if not dev.connected:
                continue
            slot = device_slot(dev)
            if slot in self._SLOT_ORDER and slot not in by_slot:
                by_slot[slot] = dev
            elif other is None:
                other = dev

        packed = [(s, by_slot[s]) for s in self._SLOT_ORDER if s in by_slot]
        if other is not None:
            packed.append(("other", other))

        for i, circle in enumerate(self._bt_circles):
            if i < len(packed):
                slot, dev = packed[i]
                pct = self._stable_pct(dev)
                circle.set_empty(False)
                circle.set_hoverable(True)
                circle.set_state(self._SLOT_ICON[slot], pct, f"{int(pct)}%" if pct is not None else "—")
            else:
                circle.set_empty(True)
                circle.set_hoverable(False)
                circle.set_state(icons.plus, None, "—")