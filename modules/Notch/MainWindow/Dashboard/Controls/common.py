from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.eventbox import EventBox
from fabric.widgets.label import Label
from fabric.widgets.scale import Scale


def ease_out_cubic(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


class _Anim:
    _anim = None

    def _animate(self, a: float, b: float, ms: int, setter):
        self._stop()
        t0 = None

        def tick(_, clock):
            nonlocal t0
            now = clock.get_frame_time()
            t0 = t0 or now
            t = min((now - t0) / (ms * 1000), 1.0)
            setter(a + (b - a) * ease_out_cubic(t))
            if t < 1.0:
                return True
            self._anim = None
            return False

        self._anim = self.add_tick_callback(tick)

    def _stop(self):
        if self._anim is not None:
            self.remove_tick_callback(self._anim)
            self._anim = None

    def cleanup(self):
        self._stop()


class BaseSmoothSlider(Scale, _Anim):
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
        self.set_can_focus(False)
        self._upd = False
        self._pressed = False
        self._last_pct = -1

        if style_class:
            self.add_style_class(style_class)

        self.connect("value-changed", self._on_scale_val_changed)
        self.connect("button-press-event", self._on_click_press)
        self.connect("button-release-event", self._on_click_release)
        self.connect("motion-notify-event", self._on_click_motion)

    def _event_value(self, event) -> float:
        return max(0.0, min(1.0, event.x / self.get_allocated_width()))

    def _on_click_press(self, _, event):
        if event.button != 1:
            return False
        self._pressed = True
        self._animate(self.get_value(), self._event_value(event), 280, self.set_value)
        return True

    def _on_click_release(self, *_):
        was, self._pressed = self._pressed, False
        return was

    def _on_click_motion(self, _, event):
        if not self._pressed:
            return False
        self._stop()
        self.set_value(self._event_value(event))
        return True

    def _on_scale_val_changed(self, _):
        pct = round(self.get_value() * 100)
        if pct != self._last_pct:
            self.set_tooltip_text(f"{pct}%")
            self._last_pct = pct
        if not self._upd:
            self.on_user_value_changed(self.get_value())

    def update_external(self, norm_val: float):
        if self._anim is not None or self._pressed:
            return
        if abs(self.get_value() - norm_val) > 0.005:
            self._upd = True
            self.set_value(norm_val)
            self._upd = False

    def on_user_value_changed(self, norm_val: float):
        raise NotImplementedError


class BaseIconButton(Box, _Anim):
    def __init__(self, box_name: str, lbl_name: str, **kwargs):
        super().__init__(name=box_name, **kwargs)
        self.label = Label(name=lbl_name)
        self.btn = Button(on_clicked=self._on_toggle, child=self.label)
        self.btn.set_can_focus(False)
        self.add(EventBox(child=self.btn, h_expand=True))
        self._soft_muted = False
        self._saved_val = 100.0

    def _on_toggle(self, *_):
        cur = self.get_current_value()
        if self._anim is None:
            self._soft_muted = cur < 0.5
            if not self._soft_muted:
                self._saved_val = cur
        else:
            self._stop()
        self._soft_muted = not self._soft_muted
        end = 0.0 if self._soft_muted else self._saved_val
        if abs(cur - end) < 0.5:
            self.set_current_value(end)
        else:
            self._animate(cur, end, 400, self.set_current_value)

    def get_current_value(self) -> float:
        raise NotImplementedError

    def set_current_value(self, val: float):
        raise NotImplementedError

    def update_ui(self, icon: str, tooltip: str):
        self.label.set_markup(icon)
        self.set_tooltip_text(tooltip)