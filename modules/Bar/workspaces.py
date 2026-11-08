import gi

gi.require_version("Gdk", "3.0")
from fabric.widgets.box import Box
from fabric.widgets.eventbox import EventBox
from fabric.widgets.revealer import Revealer
from gi.repository import Gdk, GLib

from modules.Bar.Workspaces.axis import AxisWindow
from modules.Bar.Workspaces.bars import (
    REVEAL_MS,
    LeftWorkspaces,
    LifecycleMixin,
    TopWorkspaces,
    matrix_nav,
    set_matrix_dims,
    set_matrix_order,
)
from services.corners import MyCorner
from services.wayland import WaylandWindow as Window


__all__ = [
    "TopWorkspaces",
    "AxisWindow",
    "LeftWorkspaces",
    "SideBarWindow",
    "matrix_nav",
    "set_matrix_dims",
    "set_matrix_order",
]

def _corner_box(name: str) -> Box:
    return Box(name=name, h_align="start", v_align="start", children=[MyCorner("top-left")])


class SideBarWindow(LifecycleMixin, Window):
    def __init__(self, conn, monitor_id=0):
        super().__init__(exclusivity="none", layer="overlay", monitor_id=monitor_id)
        self._init_lifecycle()
        self.anchor = "left top"
        self.margin = "0px 0px 0px -8px"

        self.conn = conn
        self.monitor_id = monitor_id
        self._monitor_id_str = str(monitor_id)
        self.ws = LeftWorkspaces(self.conn, v_align="start", h_align="start")

        self._ws_switch_timer_id = 0
        self._mouse_over = False
        self._fullscreen = False
        self._force_revealed = self.ws.actions_visible
        self._is_hidden = not self.ws.has_multiple_rows and not self._force_revealed
        self._bar_width = 36
        self._bar_height = 200
        self._ws_switch_active = False

        self._last_row = max(0, self.ws._active_idx)

        self._init_ui()
        self.ws.attach_sidebar(self)
        self._connect(self, "unmap", self._on_unmap)
        self._connect(self, "map", self._on_map)
        if self.conn is not None and self.conn.ready:
            self._schedule_occlusion()
        self.ws.sync_joint()

    def _cancel_timers(self):
        self._ws_switch_timer_id = self._cancel_source(self._ws_switch_timer_id)

    def _needs_fullscreen_watch(self) -> bool:
        return not self._destroyed and (self.get_mapped() or self._fullscreen)

    def _needs_occlusion_watch(self) -> bool:
        return (
            not self._destroyed
            and self.get_mapped()
            and self.ws.has_multiple_rows
            and not self._force_revealed
            and not self._mouse_over
            and not self._ws_switch_active
        )

    def _on_unmap(self, *_):
        self._cancel_timers()
        self._ws_switch_active = False
        self._mouse_over = False
        self.ws.sync_occlusion_watch()

    def _on_map(self, *_):
        if not self._destroyed:
            self._schedule_occlusion()

    def cleanup(self):
        if self._destroyed:
            return
        self._cancel_timers()
        super().cleanup()
        self.ws.cleanup()

    def is_panel_visible(self) -> bool:
        return not self._is_hidden

    def _set_hidden(self, hidden: bool):
        if self._is_hidden == hidden:
            return
        self._is_hidden = hidden
        self.revealer.set_reveal_child(not hidden)
        self._sync_top_corner()
        self.ws.sync_joint()

    def _set_fullscreen(self, fullscreen: bool):
        if self._fullscreen == fullscreen:
            return
        self._fullscreen = fullscreen
        self.set_visible(not fullscreen)

    def _sync_activator_visibility(self):
        self.activator.set_visible(self.ws.has_multiple_rows or self._force_revealed)

    def _sync_top_corner(self):
        self.revealer_top_corner.set_reveal_child(
            self.ws.has_multiple_cols or self._force_revealed or self._is_hidden
        )

    def _resolve_static_visibility(self) -> bool:
        if not self.ws.has_multiple_rows and not self._force_revealed:
            self._set_hidden(True)
            return True
        if self._force_revealed or self._mouse_over or self._ws_switch_active:
            self._set_hidden(False)
            return True
        return False

    def on_matrix_dims_changed(self, ws: int | None = None):
        if self._destroyed:
            return
        self._last_row = self.ws.get_active_row(ws)
        self._sync_activator_visibility()
        self._sync_top_corner()
        self._schedule_occlusion()

    def set_force_revealed(self, force: bool):
        if self._destroyed or self._force_revealed == force:
            return
        self._force_revealed = force
        self._sync_activator_visibility()
        self._sync_top_corner()
        self._schedule_occlusion()

    def _init_ui(self):
        self.bottom_corner = _corner_box("sidebar-bottom-corner")
        self.panel_col = Box(
            orientation="v",
            h_align="start",
            v_align="start",
            children=[self.ws, self.bottom_corner],
        )
        self._connect(self.panel_col, "size-allocate", self._on_panel_size_allocate)

        self.revealer = Revealer(
            name="sidebar-revealer",
            transition_type="slide-right",
            transition_duration=REVEAL_MS,
            child_revealed=not self._is_hidden,
            child=self.panel_col,
        )

        self.top_corner = _corner_box("sidebar-top-corner")
        self.revealer_top_corner = Revealer(
            transition_type="slide-down",
            transition_duration=200,
            child_revealed=(self.ws.has_multiple_cols or self._force_revealed or self._is_hidden),
            child=self.top_corner,
        )

        self.wrapper = Box(
            name="bar-inner",
            orientation="h",
            h_align="start",
            v_align="start",
            children=[self.revealer, self.revealer_top_corner],
        )

        self.activator = Box(style="background: transparent;")
        self.activator.set_size_request(15, -1)
        self._sync_activator_visibility()

        layout_box = Box(
            orientation="h",
            spacing=0,
            h_align="start",
            v_align="start",
            children=[self.wrapper, self.activator],
        )
        self.main_eb = EventBox(child=layout_box)
        self._connect(self.main_eb, "enter-notify-event", self._on_hover_enter)
        self._connect(self.main_eb, "leave-notify-event", self._on_hover_leave)
        self.add(self.main_eb)

    def _on_panel_size_allocate(self, _, alloc):
        changed = False
        if alloc.width > 16 and alloc.width != self._bar_width:
            self._bar_width = alloc.width
            changed = True
        if alloc.height > 16 and alloc.height != self._bar_height:
            self._bar_height = alloc.height
            changed = True
        if changed and self._needs_occlusion_watch():
            self.ws.request_occlusion_check()

    def _trigger_ws_switch(self, *_, ws: int | None = None):
        if self._destroyed:
            return
        if not self.ws.has_multiple_rows and not self._force_revealed:
            self._last_row = 0
            self.ws.request_occlusion_check()
            return

        cur_row = self.ws.get_active_row(ws)
        row_changed = cur_row != self._last_row
        self._last_row = cur_row

        if not row_changed:
            self._schedule_occlusion()
            return
        if not self._needs_fullscreen_watch():
            return

        self._ws_switch_active = True
        self.ws.sync_occlusion_watch()
        self._cancel_timers()
        self._ws_switch_timer_id = GLib.timeout_add(1500, self._on_ws_switch_timeout)
        self.ws.request_occlusion_check()

    def _on_ws_switch_timeout(self):
        self._ws_switch_timer_id = 0
        if not self._destroyed:
            self._ws_switch_active = False
            self._schedule_occlusion()
        return False

    def _schedule_occlusion(self, *_):
        if self._destroyed:
            return
        self.ws.sync_occlusion_watch()
        self._resolve_static_visibility()
        if self._needs_fullscreen_watch():
            self.ws.request_occlusion_check()

    def _extract_monitor_info(self, monitors: list) -> tuple[int, int, int] | None:
        for m in monitors:
            if not isinstance(m, dict):
                continue
            m_id = m.get("id")
            if m_id == self.monitor_id or str(m_id) == self._monitor_id_str:
                active_ws = m.get("activeWorkspace", {})
                ws_id = active_ws.get("id", 1) if isinstance(active_ws, dict) else 1
                return int(m.get("x", 0)), int(m.get("y", 0)), int(ws_id)
        return None

    def _scan_clients(self, clients: list, mon_x: int, mon_y: int, active_ws_id: int) -> tuple[bool, bool]:
        px1, px2 = mon_x, mon_x + self._bar_width
        py1, py2 = mon_y + 36, mon_y + 36 + self._bar_height

        overlap = False
        for w in clients:
            if not isinstance(w, dict):
                continue
            if w.get("hidden") or w.get("minimized") or not w.get("mapped", True):
                continue
            w_ws_id = self.ws.client_ws_id(w)
            if w_ws_id != active_ws_id or w_ws_id < 1:
                continue
            w_mon = w.get("monitor")
            w_mon_id = w_mon.get("id") if isinstance(w_mon, dict) else w_mon
            if w_mon_id != self.monitor_id and str(w_mon_id) != self._monitor_id_str:
                continue
            if w.get("fullscreen"):
                return True, overlap
            if overlap:
                continue
            pos, size = w.get("at"), w.get("size")
            if not pos or not size or len(pos) < 2 or len(size) < 2:
                continue
            overlap = pos[0] < px2 and pos[0] + size[0] > px1 and pos[1] < py2 and pos[1] + size[1] > py1

        return False, overlap

    def _apply_occlusion_data(self, monitors: list, clients: list):
        if self._destroyed:
            return
        mon_info = self._extract_monitor_info(monitors)
        if mon_info is None:
            return

        fullscreen, overlap = self._scan_clients(clients, *mon_info)
        self._set_fullscreen(fullscreen)
        if fullscreen or self._resolve_static_visibility():
            return

        self._set_hidden(overlap)

    def _check_occlusion(self):
        self._schedule_occlusion()

    def _on_hover_enter(self, _, event):
        if event.detail == Gdk.NotifyType.INFERIOR or event.mode != Gdk.CrossingMode.NORMAL:
            return False
        self._mouse_over = True
        self.ws.sync_occlusion_watch()
        if self.ws.has_multiple_rows or self._force_revealed:
            self._set_hidden(False)
        return False

    def _on_hover_leave(self, _, event):
        if event.detail == Gdk.NotifyType.INFERIOR or event.mode != Gdk.CrossingMode.NORMAL:
            return False
        self._mouse_over = False
        self._schedule_occlusion()
        return False