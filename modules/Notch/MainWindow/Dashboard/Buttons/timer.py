import json
import os
import random

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label


_GLITCH_CLASSES = (
    "glitch-shift-right",
    "glitch-shift-left",
    "glitch-flicker",
    "glitch-aberration",
    "glitch-heavy",
    "glitch-color-swap",
)

CACHE_DIR = os.path.expanduser("~/.cache/vidgex-shell")
SETTINGS_FILE = os.path.join(CACHE_DIR, "timer_settings.json")

_MIN_SECONDS = 60
_MAX_SECONDS = 86400
_SCROLL_STEP = 0.25
_SCROLL_DIRECTIONS = {Gdk.ScrollDirection.UP: 1, Gdk.ScrollDirection.DOWN: -1}
_SAVE_DELAY_MS = 500


def _cancel(source_id):
    if source_id is not None:
        GLib.source_remove(source_id)
    return None


class _DurationStore:
    __slots__ = ("_data", "_source")

    def __init__(self):
        self._data = None
        self._source = None

    def _load(self) -> dict:
        if self._data is None:
            self._data = {}
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE) as f:
                    self._data = json.load(f)
        return self._data

    def get(self, key: str, default: int) -> int:
        return self._load().get(key, default)

    def set(self, key: str, value: int) -> None:
        self._load()[key] = value
        self._source = _cancel(self._source)
        self._source = GLib.timeout_add(_SAVE_DELAY_MS, self._on_timeout)

    def flush(self) -> None:
        if self._source is not None:
            self._source = _cancel(self._source)
            self._write()

    def _on_timeout(self) -> bool:
        self._source = None
        self._write()
        return False

    def _write(self) -> None:
        os.makedirs(CACHE_DIR, exist_ok=True)
        GLib.file_set_contents(SETTINGS_FILE, json.dumps(self._data, indent=2).encode())


_durations = _DurationStore()


def _dis(ws, disabled: bool):
    for w in ws:
        (w.add_style_class if disabled else w.remove_style_class)("disabled")

def _get_all_labels(widget) -> list:
    if isinstance(widget, Label):
        return [widget]
    if isinstance(widget, Gtk.Container):
        return [lbl for child in widget.get_children() for lbl in _get_all_labels(child)]
    return []

def _content(ic: Label, title_box: Box) -> Box:
    return Box(h_align="start", v_align="center", spacing=10, children=(ic, title_box))


class TimerWidget(Box):
    def __init__(self, name_prefix: str):
        super().__init__(orientation="v", v_align="center", h_align="center")
        self.hh_label = Label(name=f"{name_prefix}-timer-hh", label="00", xalign=0.5)
        self.mm_label = Label(name=f"{name_prefix}-timer-mm", label="05", xalign=0.5)
        self.add(self.hh_label)
        self.add(self.mm_label)

        self._last_str = ""
        self._gl_rem = 0
        self._gl_tid = None
        self._destroyed = False
        self.connect("destroy", self._on_destroy)

    def update_time(self, total_seconds: int):
        total_minutes = total_seconds // 60
        hh = min(24, total_minutes // 60)
        mm = total_minutes % 60 if hh < 24 else 0
        time_str = f"{hh:02d}:{mm:02d}"
        if time_str == self._last_str:
            return

        if self._last_str:
            self._trigger_glitch()
        self._last_str = time_str
        self.hh_label.set_label(f"{hh:02d}")
        self.mm_label.set_label(f"{mm:02d}")

    def _trigger_glitch(self):
        self._gl_rem = 6
        if self._gl_tid is None:
            self._gl_tid = GLib.timeout_add(35, self._glitch_tick)

    def _clear_glitch(self):
        for lbl in (self.hh_label, self.mm_label):
            ctx = lbl.get_style_context()
            for cls in _GLITCH_CLASSES:
                ctx.remove_class(cls)
            ctx.remove_class("glitching")

    def _glitch_tick(self) -> bool:
        if self._destroyed:
            self._gl_tid = None
            return False

        self._clear_glitch()
        if self._gl_rem == 0:
            self._gl_tid = None
            return False

        self._gl_rem -= 1
        for lbl in (self.hh_label, self.mm_label):
            ctx = lbl.get_style_context()
            ctx.add_class("glitching")
            for cls in random.sample(_GLITCH_CLASSES, random.randint(1, 2)):
                ctx.add_class(cls)
        return True

    def _on_destroy(self, *_):
        self._destroyed = True
        self._gl_tid = _cancel(self._gl_tid)


class _TimerSplitButton(Box):
    def __init__(self, name_prefix: str, icon_markup: str, title_widget: Box, default_seconds: int = 1500):
        super().__init__(name=f"{name_prefix}-button")
        self._name_prefix = name_prefix

        self._duration = _durations.get(name_prefix, default_seconds)
        self._remaining = self._duration
        self._is_running = False
        self._timer_id = None
        self._pulse_id = None
        self._scroll_acc = 0.0
        self._destroyed = False
        self.connect("destroy", lambda *_: self.cleanup())

        self.icon = Label(name=f"{name_prefix}-icon", markup=icon_markup)
        self.status_button = Button(
            name=f"{name_prefix}-status-button",
            h_expand=True,
            child=_content(self.icon, title_widget),
            on_clicked=self._on_status_click,
        )

        self.timer_widget = TimerWidget(name_prefix)
        self.timer_button = Button(
            name=f"{name_prefix}-timer-button",
            child=self.timer_widget,
            on_clicked=self._on_status_click,
        )

        self.timer_button.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        self.timer_button.connect("scroll-event", self._on_timer_scroll)

        self.add(self.status_button)
        self.add(self.timer_button)

        self._sw = (
            self,
            self.icon,
            self.status_button,
            self.timer_button,
            self.timer_widget.hh_label,
            self.timer_widget.mm_label,
            *_get_all_labels(title_widget),
        )
        self._dis_ui(True)
        self.update_timer_display()

    def _dis_ui(self, disabled: bool):
        _dis(self._sw, disabled)

    def update_timer_display(self):
        self.timer_widget.update_time(self._remaining if self._is_running else self._duration)

    def _on_timer_scroll(self, widget, event: Gdk.EventScroll) -> bool:
        if self._is_running:
            return True

        if event.direction == Gdk.ScrollDirection.SMOOTH:
            _, _, dy = event.get_scroll_deltas()
            self._scroll_acc += dy
            steps = int(self._scroll_acc / _SCROLL_STEP)
            self._scroll_acc -= steps * _SCROLL_STEP
            delta_minutes = -steps
        else:
            delta_minutes = _SCROLL_DIRECTIONS.get(event.direction, 0)

        duration = max(_MIN_SECONDS, min(_MAX_SECONDS, self._duration + delta_minutes * 60))
        if duration != self._duration:
            self._duration = self._remaining = duration
            self.update_timer_display()
            _durations.set(self._name_prefix, duration)
        return True

    def _trigger_tick_pulse(self):
        if self._destroyed or self._pulse_id is not None:
            return
        self.timer_button.add_style_class("tick-pulse")
        self._pulse_id = GLib.timeout_add(120, self._clear_pulse)

    def _clear_pulse(self) -> bool:
        self._pulse_id = None
        self.timer_button.remove_style_class("tick-pulse")
        return False

    def start_timer(self):
        if self._is_running:
            return
        self._is_running = True
        self._remaining = self._duration
        self._timer_id = GLib.timeout_add(1000, self._tick)
        self._dis_ui(False)
        self.update_timer_display()

    def stop_timer(self):
        if not self._is_running:
            return
        self._is_running = False
        self._timer_id = _cancel(self._timer_id)
        self._remaining = self._duration
        self._dis_ui(True)
        self.update_timer_display()

    def _tick(self) -> bool:
        if self._destroyed:
            self._timer_id = None
            return False
        self._remaining -= 1
        if self._remaining <= 0:
            self._timer_id = None
            self._on_timer_finished()
            self.stop_timer()
            return False
        self._trigger_tick_pulse()
        self.update_timer_display()
        return True

    def _on_status_click(self, *_):
        raise NotImplementedError

    def _on_timer_finished(self):
        raise NotImplementedError

    def cleanup(self):
        if self._destroyed:
            return
        self._destroyed = True
        self._timer_id = _cancel(self._timer_id)
        self._pulse_id = _cancel(self._pulse_id)
        _durations.flush()