from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.circularprogressbar import CircularProgressBar
from fabric.widgets.eventbox import EventBox
from fabric.widgets.label import Label
from fabric.widgets.overlay import Overlay
from fabric.widgets.scale import Scale
from gi.repository import GLib

ANIM_STEPS = 25
ANIM_INTERVAL_MS = 16
CLICK_STEPS = 20
CLICK_MS = 14


def ease_out_cubic(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


class BaseSmoothSlider(Scale):
    __slots__ = ('_upd', '_canim_id', '_canim_s', '_canim_e', '_canim_n', '_pressed', '_last_pct')

    def __init__(self, style_class: str = "", **kwargs):
        super().__init__(
            name="control-slider",
            orientation="h",
            h_expand=True,
            h_align="fill",
            has_origin=True,
            increments=(0.01, 0.1),
            **kwargs,
        )
        self.set_can_focus(False)  # Запрещаем перехватывать стрелки клавиатуры
        self._upd = False
        self._canim_id = None
        self._canim_s = self._canim_e = 0.0
        self._canim_n = 0
        self._pressed = False
        self._last_pct = -1

        if style_class:
            self.add_style_class(style_class)

        self.connect("value-changed", self._on_scale_val_changed)
        self.connect("button-press-event", self._on_click_press)
        self.connect("button-release-event", self._on_click_release)
        self.connect("motion-notify-event", self._on_click_motion)

    def _on_click_press(self, _, event):
        if event.button != 1:
            return False
        w = self.get_allocation().width
        target = max(0.0, min(1.0, event.x / w)) if w > 0 else 0.0
        cur = self.get_value()
        if abs(target - cur) < 0.03:
            return False
        self._pressed = True
        self._cancel_anim()
        self._canim_s = cur
        self._canim_e = target
        self._canim_n = 0
        self._canim_id = GLib.timeout_add(CLICK_MS, self._canim_tick)
        return True

    def _on_click_release(self, _, event):
        if not self._pressed:
            return False
        self._pressed = False
        return True

    def _on_click_motion(self, _, event):
        if not self._pressed:
            return False
        self._cancel_anim()
        w = self.get_allocation().width
        self.set_value(max(0.0, min(1.0, event.x / w)) if w > 0 else 0.0)
        return True

    def _canim_tick(self):
        self._canim_n += 1
        t = min(self._canim_n / float(CLICK_STEPS), 1.0)
        self.set_value(self._canim_s + (self._canim_e - self._canim_s) * ease_out_cubic(t))
        if self._canim_n >= CLICK_STEPS:
            self._canim_id = None
            return False
        return True

    def _cancel_anim(self):
        if self._canim_id is not None:
            GLib.source_remove(self._canim_id)
            self._canim_id = None

    def _on_scale_val_changed(self, _):
        if self._upd:
            return
        pct = int(self.get_value() * 100)
        if pct != self._last_pct:
            self.set_tooltip_text(f"{pct}%")
            self._last_pct = pct
        self.on_user_value_changed(self.get_value())

    def update_external(self, norm_val: float):
        if self._canim_id is not None or self._pressed:
            return
        if abs(self.get_value() - norm_val) > 0.005:
            self._upd = True
            self.set_value(norm_val)
            pct = int(norm_val * 100)
            if pct != self._last_pct:
                self.set_tooltip_text(f"{pct}%")
                self._last_pct = pct
            self._upd = False

    def on_user_value_changed(self, norm_val: float):
        pass

    def cleanup(self):
        self._cancel_anim()


class BaseSmallIndicator(Box):
    __slots__ = ('progress_bar', 'label')

    def __init__(self, box_name: str, prog_name: str, lbl_name: str, **kwargs):
        super().__init__(name=box_name, **kwargs)
        self.progress_bar = CircularProgressBar(name=prog_name, size=28, line_width=2, start_angle=150, end_angle=390)
        self.label = Label(name=lbl_name)
        self.add(Overlay(child=self.progress_bar, overlays=self.label))

    def update_ui(self, norm_val: float, icon: str, tooltip: str):
        if abs(self.progress_bar.value - norm_val) > 0.005:
            self.progress_bar.value = norm_val
        self.label.set_markup(icon)
        self.set_tooltip_text(tooltip)


class BaseIconButton(Box):
    __slots__ = ('label', 'btn', '_anim_id', '_soft_muted', '_saved_val', '_anim_s', '_anim_e', '_anim_step')

    def __init__(self, box_name: str, lbl_name: str, **kwargs):
        super().__init__(name=box_name, **kwargs)
        self.label = Label(name=lbl_name)
        self.btn = Button(on_clicked=self._on_toggle, child=self.label)
        self.btn.set_can_focus(False)  # Не держим фокус
        self.add(EventBox(child=self.btn, h_expand=True))

        self._anim_id = None
        self._soft_muted = False
        self._saved_val = 100.0
        self._anim_s = self._anim_e = 0.0
        self._anim_step = 0

    def _on_toggle(self, *_):
        if self._anim_id is not None:
            GLib.source_remove(self._anim_id)
            self._anim_id = None

        cur = float(self.get_current_value())
        if not self._soft_muted:
            self._saved_val = cur or 100.0
            self._soft_muted = True
            self._start_anim(cur, 0.0)
        else:
            self._soft_muted = False
            self._start_anim(cur, self._saved_val)

    def _start_anim(self, start: float, end: float):
        if abs(start - end) < 0.5:
            self.set_current_value(end)
            return
        self._anim_s = start
        self._anim_e = end
        self._anim_step = 0
        self._anim_id = GLib.timeout_add(ANIM_INTERVAL_MS, self._anim_tick)

    def _anim_tick(self):
        self._anim_step += 1
        t = min(self._anim_step / float(ANIM_STEPS), 1.0)
        v = self._anim_s + (self._anim_e - self._anim_s) * ease_out_cubic(t)
        self.set_current_value(v)
        if self._anim_step >= ANIM_STEPS:
            self._anim_id = None
            return False
        return True

    def get_current_value(self) -> float:
        raise NotImplementedError

    def set_current_value(self, val: float):
        raise NotImplementedError

    def update_ui(self, icon: str, tooltip: str):
        self.label.set_markup(icon)
        self.set_tooltip_text(tooltip)

    def cleanup(self):
        if self._anim_id is not None:
            GLib.source_remove(self._anim_id)
            self._anim_id = None