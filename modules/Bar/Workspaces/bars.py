import random
from time import monotonic

import gi

gi.require_version("Gdk", "3.0")
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.eventbox import EventBox
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer
from gi.repository import Gdk, GLib, GObject

from modules.Bar.Workspaces.matrix import (
    bind_global_conn, clamp_coords, client_ws_id,
    get_active_ws, joint_flush, matrix_nav,
    perform_ws_transition, release_global_conn_if_unused, request_occlusion_check,
    set_matrix_dims, set_matrix_order, sync_joint,
    sync_occlusion_watch, toggle_order_actions,
    sidebars, state, axis_widgets,
)
import services.icons as icons


__all__ = [
    "LifecycleMixin",
    "TopWorkspaces",
    "LeftWorkspaces",
    "matrix_nav",
    "set_matrix_dims",
    "set_matrix_order",
]

_SM = Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK
_LST = 0.0
_ACC = {"x": 0.0, "y": 0.0}

_GLITCH_CLASSES = (
    "glitch-shift-right",
    "glitch-shift-left",
    "glitch-flicker",
    "glitch-aberration",
    "glitch-heavy",
    "glitch-color-swap",
)


def _toggle_class(widget, cls: str, enabled: bool):
    if enabled:
        widget.add_style_class(cls)
    else:
        widget.remove_style_class(cls)

def _set_dot_active(btn: Button, active: bool):
    btn.remove_style_class("empty" if active else "active")
    btn.add_style_class("active" if active else "empty")

def _icon_btn(markup: str, on_click) -> Button:
    btn = Button(
        child=Label(markup=markup, h_align="center", v_align="center"),
        h_align="center",
        v_align="center",
        can_focus=False,
    )
    btn.connect("clicked", on_click)
    return btn

def _check_scroll_cooldown(e, axis: str) -> int:
    global _LST
    now = monotonic()
    if now - _LST < 0.2:
        return 0

    d = e.direction
    res, dx, dy = e.get_scroll_deltas()
    direction = 0

    if d in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.LEFT if axis == "x" else None):
        direction = -1
    elif d in (Gdk.ScrollDirection.DOWN, Gdk.ScrollDirection.RIGHT if axis == "x" else None):
        direction = 1
    elif res:
        delta = (dx if abs(dx) >= abs(dy) else dy) if axis == "x" else dy
        acc = _ACC[axis]
        acc = delta if acc * delta < 0 else acc + delta
        _ACC[axis] = acc
        if abs(acc) >= 0.5:
            direction = 1 if acc > 0 else -1

    if direction:
        _LST = now
        _ACC["x"] = _ACC["y"] = 0.0
    return direction


class LifecycleMixin:
    def _init_lifecycle(self):
        self._destroyed = False
        self._subscriptions: list[tuple[object, int]] = []
        self.connect("destroy", self._on_widget_destroy)

    @staticmethod
    def _cancel_source(tid: int) -> int:
        if tid:
            GLib.source_remove(tid)
        return 0

    def _connect(self, obj, sig: str, cb):
        hid = obj.connect(sig, cb)
        self._subscriptions.append((obj, hid))
        return hid

    def _on_widget_destroy(self, *_):
        self.cleanup()

    def cleanup(self):
        self._destroyed = True
        while self._subscriptions:
            obj, hid = self._subscriptions.pop()
            if GObject.signal_handler_is_connected(obj, hid):
                obj.disconnect(hid)


class _AxisWorkspaces(LifecycleMixin, Box):
    _is_vertical = False

    def __init__(self, conn, container_name: str, orientation: str, **kwargs):
        super().__init__(name=container_name, orientation=orientation, **kwargs)
        self._init_lifecycle()
        self.conn = conn
        self._flush = False

        self.dots: list[Button] = []
        self._active_idx = -1
        self._glitch_tid = 0
        self._glitch_rem: dict[Button, int] = {}
        self._glitch_removing: set[Button] = set()

        self._connect(self, "unmap", self._on_unmap)
        axis_widgets.append(self)
        bind_global_conn(conn)

    def _axis_count(self) -> int:
        return state.rows if self._is_vertical else state.cols

    def _make_action_revealer(self, tag: str, kind: str, icon: str, transition: str, cb, **align_kw) -> Revealer:
        return Revealer(
            transition_type=transition,
            transition_duration=200,
            child_revealed=state.order_actions_visible,
            child=Box(name=f"workspaces-action-{tag}-{kind}", children=[_icon_btn(icon, cb)], **align_kw),
        )

    def _build_axis_controls(self, axis_tag: str, transition: str, on_minus, on_plus, leading_widget=None):
        align_kw = {"h_align": "center"} if self._is_vertical else {}
        orient = "v" if self._is_vertical else "h"

        self.revealer_minus = self._make_action_revealer(
            axis_tag, "minus", icons.minus, transition, on_minus, **align_kw
        )
        self.dots_box = Box(name=f"workspaces-{axis_tag}", orientation=orient, spacing=8, **align_kw)
        self._sync_dots(animate=False)

        self.revealer_dots = Revealer(
            transition_type=transition,
            transition_duration=200,
            child_revealed=(self._axis_count() > 1 or state.order_actions_visible),
            child=self.dots_box,
        )
        self.revealer_plus = self._make_action_revealer(
            axis_tag, "plus", icons.plus, transition, on_plus, **align_kw
        )

        children = [self.revealer_minus, self.revealer_dots, self.revealer_plus]
        if leading_widget is not None:
            children.insert(0, leading_widget)

        self.inner_box = Box(orientation=orient, spacing=0, children=children, **align_kw)
        self.event_box = EventBox(child=self.inner_box)
        self.event_box.add_events(_SM)
        self._connect(self.event_box, "scroll-event", self._on_scroll)

        self.add(self.event_box)
        self._update_ui()

    def _clear_glitch_classes(self, btn: Button):
        ctx = btn.get_style_context()
        for cls in _GLITCH_CLASSES:
            ctx.remove_class(cls)

    def _apply_random_glitch_classes(self, btn: Button):
        ctx = btn.get_style_context()
        for cls in _GLITCH_CLASSES:
            ctx.remove_class(cls)
        count = 1 if random.random() > 0.4 else 2
        for cls in random.sample(_GLITCH_CLASSES, count):
            ctx.add_class(cls)

    def _remove_dot_widget(self, btn: Button):
        self._glitch_rem.pop(btn, None)
        self._glitch_removing.discard(btn)
        if not self._glitch_rem:
            self._glitch_tid = self._cancel_source(self._glitch_tid)
        if btn in self.dots:
            self.dots.remove(btn)
        self.dots_box.remove(btn)
        btn.destroy()

    def _finish_all_glitches(self):
        had_removing = bool(self._glitch_removing)
        for btn in tuple(self._glitch_removing):
            self._remove_dot_widget(btn)
        self._glitch_tid = self._cancel_source(self._glitch_tid)
        self._glitch_rem.clear()
        for btn in self.dots:
            self._clear_glitch_classes(btn)
        if had_removing and not self._destroyed and hasattr(self, "revealer_dots"):
            self.sync_visibility()

    def _on_unmap(self, *_):
        self._finish_all_glitches()

    def _start_dot_glitch(self, btn: Button, removing: bool):
        if removing:
            self._glitch_removing.add(btn)
        else:
            self._glitch_removing.discard(btn)

        self._apply_random_glitch_classes(btn)
        self._glitch_rem[btn] = 6
        if not self._glitch_tid:
            self._glitch_tid = GLib.timeout_add(35, self._glitch_tick)

    def _glitch_tick(self) -> bool:
        if self._destroyed or not self._glitch_rem:
            self._glitch_tid = 0
            return False

        removed_any = False
        for btn in tuple(self._glitch_rem):
            rem = self._glitch_rem[btn] - 1
            if rem <= 0:
                self._glitch_rem.pop(btn, None)
                if btn in self._glitch_removing:
                    self._remove_dot_widget(btn)
                    removed_any = True
                else:
                    self._clear_glitch_classes(btn)
            else:
                self._glitch_rem[btn] = rem
                self._apply_random_glitch_classes(btn)

        if removed_any:
            self.sync_visibility()

        if not self._glitch_rem:
            self._glitch_tid = 0
            return False
        return True

    def _create_dot(self) -> Button:
        btn = Button(h_expand=False, v_expand=False, h_align="center", v_align="center", can_focus=False)
        if self._is_vertical:
            btn.add_style_class("row-dot")
        btn.add_style_class("empty")
        btn.connect("clicked", self._on_dot_button_clicked)
        btn.add_events(_SM)
        btn.connect("scroll-event", self._on_scroll)
        return btn

    def _sync_dots(self, animate: bool = True):
        target = self._axis_count()

        if self._active_idx >= target:
            if 0 <= self._active_idx < len(self.dots):
                _set_dot_active(self.dots[self._active_idx], False)
            self._active_idx = -1

        if not animate or not self.get_mapped():
            self._finish_all_glitches()
            while len(self.dots) > target:
                self._remove_dot_widget(self.dots[-1])
            for _ in range(len(self.dots), target):
                btn = self._create_dot()
                self.dots.append(btn)
                self.dots_box.add(btn)
                btn.show()
            return

        for idx in range(target):
            if idx < len(self.dots):
                if self.dots[idx] in self._glitch_removing:
                    self._start_dot_glitch(self.dots[idx], removing=False)
            else:
                btn = self._create_dot()
                self.dots.append(btn)
                self.dots_box.add(btn)
                btn.show()
                self._start_dot_glitch(btn, removing=False)

        for idx in range(target, len(self.dots)):
            btn = self.dots[idx]
            if btn not in self._glitch_removing:
                if idx == self._active_idx:
                    self._active_idx = -1
                _set_dot_active(btn, False)
                self._start_dot_glitch(btn, removing=True)

    def on_matrix_order_changed(self, ws: int | None = None):
        if self._destroyed:
            return
        self._sync_dots(animate=True)
        self.sync_visibility()
        self._update_ui(ws=ws)

    def set_flush(self, flush: bool):
        if self._is_vertical or self._destroyed or self._flush == flush:
            return
        self._flush = flush
        _toggle_class(self, "joint-flush", flush)

    def sync_visibility(self):
        if self._destroyed:
            return
        self.revealer_minus.set_reveal_child(state.order_actions_visible)
        self.revealer_dots.set_reveal_child(
            self._axis_count() > 1 or len(self.dots) > 1 or state.order_actions_visible
        )
        self.revealer_plus.set_reveal_child(state.order_actions_visible)

    def _step_to_index(self, cur_ws: int, target_idx: int):
        cur_row, cur_col = clamp_coords(cur_ws)
        if self._is_vertical:
            target_ws = target_idx * state.cols + cur_col + 1
            vertical = cur_row != target_idx
        else:
            target_ws = cur_row * state.cols + target_idx + 1
            vertical = False

        if target_ws != cur_ws:
            perform_ws_transition(target_ws, vertical, "workspace", self.conn)

    def _on_scroll(self, _, event):
        direction = _check_scroll_cooldown(event, axis="y" if self._is_vertical else "x")
        if direction != 0:
            ws = get_active_ws(self.conn)
            cur_row, cur_col = clamp_coords(ws)
            target_idx = (
                (cur_row + direction) % state.rows
                if self._is_vertical
                else (cur_col + direction) % state.cols
            )
            self._step_to_index(ws, target_idx)
        return True

    def _on_dot_button_clicked(self, btn: Button):
        if btn in self.dots and btn not in self._glitch_removing:
            self._on_dot_clicked(self.dots.index(btn))

    def _on_dot_clicked(self, target_idx: int):
        if 0 <= target_idx < self._axis_count():
            self._step_to_index(get_active_ws(self.conn), target_idx)

    def _update_ui(self, *_, ws: int | None = None):
        if self._destroyed or not hasattr(self, "dots_box"):
            return
        if ws is None:
            ws = get_active_ws(self.conn)
        in_bounds = 1 <= ws <= state.rows * state.cols
        new_idx = ((ws - 1) // state.cols if self._is_vertical else (ws - 1) % state.cols) if in_bounds else -1
        if new_idx == self._active_idx:
            return

        if 0 <= self._active_idx < len(self.dots):
            _set_dot_active(self.dots[self._active_idx], False)
        if 0 <= new_idx < self._axis_count() and new_idx < len(self.dots):
            _set_dot_active(self.dots[new_idx], True)
        self._active_idx = new_idx

    def cleanup(self):
        if self._destroyed:
            return
        if self in axis_widgets:
            axis_widgets.remove(self)
        self._finish_all_glitches()
        super().cleanup()
        release_global_conn_if_unused()


class TopWorkspaces(_AxisWorkspaces):
    _is_vertical = False

    def __init__(self, conn, **kwargs):
        super().__init__(conn, "workspaces-container-top", "h", **kwargs)
        self.btn_settings = _icon_btn(icons.hash, toggle_order_actions)
        _toggle_class(self.btn_settings, "active", state.order_actions_visible)
        self.num_box = Box(name="workspaces-num-top", children=[self.btn_settings])

        self._build_axis_controls(
            axis_tag="top",
            transition="slide-right",
            on_minus=lambda _: set_matrix_dims(state.rows, state.cols - 1, self.conn),
            on_plus=lambda _: set_matrix_dims(state.rows, state.cols + 1, self.conn),
            leading_widget=self.num_box,
        )
        self.set_flush(joint_flush())

    def sync_visibility(self):
        if self._destroyed:
            return
        _toggle_class(self.btn_settings, "active", state.order_actions_visible)
        super().sync_visibility()


class LeftWorkspaces(_AxisWorkspaces):
    _is_vertical = True

    def __init__(self, conn, **kwargs):
        super().__init__(conn, "workspaces-container-left", "v", **kwargs)
        self._sidebar = None
        self._build_axis_controls(
            axis_tag="left",
            transition="slide-down",
            on_minus=lambda _: set_matrix_dims(state.rows - 1, state.cols, self.conn),
            on_plus=lambda _: set_matrix_dims(state.rows + 1, state.cols, self.conn),
        )

    @property
    def has_multiple_rows(self) -> bool:
        return state.rows > 1

    @property
    def has_multiple_cols(self) -> bool:
        return state.cols > 1

    @property
    def actions_visible(self) -> bool:
        return state.order_actions_visible

    def attach_sidebar(self, sidebar):
        self._sidebar = sidebar
        if sidebar not in sidebars:
            sidebars.append(sidebar)

    def get_active_row(self, ws: int | None = None) -> int:
        if ws is None:
            ws = get_active_ws(self.conn)
        row, _ = clamp_coords(ws)
        return row

    @staticmethod
    def client_ws_id(client: dict) -> int:
        return client_ws_id(client)

    @staticmethod
    def sync_joint():
        sync_joint()

    @staticmethod
    def sync_occlusion_watch():
        sync_occlusion_watch()

    @staticmethod
    def request_occlusion_check():
        request_occlusion_check()

    def check_occlusion(self):
        if self._sidebar is not None:
            self._sidebar._schedule_occlusion()

    def cleanup(self):
        if self._destroyed:
            return
        if self._sidebar is not None:
            if self._sidebar in sidebars:
                sidebars.remove(self._sidebar)
            self._sidebar = None
        super().cleanup()
        sync_joint()