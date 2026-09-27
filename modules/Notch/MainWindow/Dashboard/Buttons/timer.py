import json
import os
import random
import threading
from gi.repository import Gdk, GLib, Gtk

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label


_GLITCH_CLASSES = [
    "glitch-shift-right",
    "glitch-shift-left",
    "glitch-flicker",
    "glitch-aberration",
    "glitch-heavy",
    "glitch-color-swap",
]

CACHE_DIR = os.path.expanduser("~/.cache/vidgex-shell")
SETTINGS_FILE = os.path.join(CACHE_DIR, "timer_settings.json")

# Защищает read-modify-write settings-файла от гонки между воркер-потоками,
# запускаемыми разными таймер-кнопками (или быстрыми последовательными скроллами).
_settings_lock = threading.Lock()


def _load_saved_durations() -> dict:
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    return {}


def _save_duration(key: str, value: int) -> None:
    def worker():
        with _settings_lock:
            os.makedirs(CACHE_DIR, exist_ok=True)
            data = _load_saved_durations()
            data[key] = value
            with open(SETTINGS_FILE, "w") as f:
                json.dump(data, f, indent=2)

    threading.Thread(target=worker, daemon=True).start()


def _dis(ws, disabled: bool):
    m = "add_style_class" if disabled else "remove_style_class"
    for w in ws:
        getattr(w, m)("disabled")


def _get_all_labels(widget) -> list:
    labels = []
    if isinstance(widget, Label):
        labels.append(widget)
    elif isinstance(widget, (Box, Gtk.Container)):
        for child in widget.get_children():
            labels.extend(_get_all_labels(child))
    return labels


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

        if self._last_str and time_str != self._last_str:
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
        if self._gl_rem > 0:
            for lbl in (self.hh_label, self.mm_label):
                lbl.get_style_context().add_class("glitching")
                for cls in random.sample(_GLITCH_CLASSES, random.randint(1, 2)):
                    lbl.get_style_context().add_class(cls)
            self._gl_rem -= 1
            return True
        self._gl_tid = None
        return False

    def _on_destroy(self, *_):
        self._destroyed = True
        if self._gl_tid is not None:
            GLib.source_remove(self._gl_tid)
            self._gl_tid = None


class _TimerSplitButton(Box):
    def __init__(self, name_prefix: str, icon_markup: str, title_widget: Box, default_seconds: int = 1500):
        super().__init__(name=f"{name_prefix}-button")
        self._name_prefix = name_prefix

        saved_durations = _load_saved_durations()
        if name_prefix in saved_durations:
            default_seconds = saved_durations[name_prefix]

        self._duration = default_seconds
        self._remaining = default_seconds
        self._is_running = False
        self._timer_id = None
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
            on_clicked=self._on_timer_click,
        )

        self.timer_button.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        self.timer_button.connect("scroll-event", self._on_timer_scroll)

        self.add(self.status_button)
        self.add(self.timer_button)

        title_labels = _get_all_labels(title_widget)
        self._sw = (
            self,
            self.icon,
            self.status_button,
            self.timer_button,
            self.timer_widget.hh_label,
            self.timer_widget.mm_label,
            *title_labels,
        )
        self._dis_ui(True)
        self.update_timer_display()

    def _dis_ui(self, disabled: bool):
        _dis(self._sw, disabled)

    def update_timer_display(self):
        sec = self._remaining if self._is_running else self._duration
        self.timer_widget.update_time(sec)

    def _on_timer_click(self, *_):
        self._on_status_click()

    def _on_timer_scroll(self, widget, event: Gdk.EventScroll) -> bool:
        if self._is_running:
            return True

        delta_minutes = 0
        if event.direction == Gdk.ScrollDirection.UP:
            delta_minutes = 1
        elif event.direction == Gdk.ScrollDirection.DOWN:
            delta_minutes = -1
        elif event.direction == Gdk.ScrollDirection.SMOOTH:
            _, _, dy = event.get_scroll_deltas()
            if dy != 0.0:
                self._scroll_acc += dy
                threshold = 0.25
                if abs(self._scroll_acc) >= threshold:
                    steps = int(self._scroll_acc / threshold)
                    delta_minutes = -steps
                    self._scroll_acc -= steps * threshold

        if delta_minutes != 0:
            self._duration = max(60, min(86400, self._duration + delta_minutes * 60))
            self._remaining = self._duration
            self.update_timer_display()
            _save_duration(self._name_prefix, self._duration)

        return True

    def _trigger_tick_pulse(self):
        ctx = self.timer_button.get_style_context()
        ctx.add_class("tick-pulse")

        def _clear():
            ctx.remove_class("tick-pulse")
            return False

        GLib.timeout_add(120, _clear)

    def start_timer(self):
        if not self._is_running:
            self._is_running = True
            self._remaining = self._duration
            if self._timer_id is None:
                self._timer_id = GLib.timeout_add(1000, self._tick)
            self._dis_ui(False)
            self.update_timer_display()

    def stop_timer(self):
        if self._is_running:
            self._is_running = False
            if self._timer_id:
                GLib.source_remove(self._timer_id)
                self._timer_id = None
            self._remaining = self._duration
            self._dis_ui(True)
            self.update_timer_display()

    def _tick(self) -> bool:
        if self._destroyed or not self._is_running:
            self._timer_id = None
            return False
        self._remaining -= 1
        self._trigger_tick_pulse()
        if self._remaining <= 0:
            self._remaining = 0
            self.update_timer_display()
            self._on_timer_finished()
            self.stop_timer()
            return False
        self.update_timer_display()
        return True

    def _on_status_click(self, *_):
        # Обязательный контракт подкласса: без переопределения кнопка не
        # должна молча превращаться в бездействующую заглушку.
        raise NotImplementedError

    def _on_timer_finished(self):
        raise NotImplementedError

    def cleanup(self):
        if self._destroyed:
            return
        self._destroyed = True
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = None