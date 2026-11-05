import gi

gi.require_version("Gtk", "3.0")
from fabric.widgets.box import Box
from gi.repository import GObject

from modules.Bar.Workspaces.bars import TopWorkspaces
from services.corners import MyCorner
from services.wayland import WaylandWindow as Window


_EDGE_PX = 8
_CAP_PX = 8
_CAP_ON_CLASS = "shown"
_ISLAND_ROUND_CLASS = "island-round"
_COLLAPSED_PX = 2

class DetachedTopWorkspaces(TopWorkspaces):
    on_flush_change = None

    def _make_leading_widget(self):
        super()._make_leading_widget()
        return None

    def set_flush(self, flush: bool):
        before = self._flush
        super().set_flush(flush)
        if self._flush != before and self.on_flush_change is not None:
            self.on_flush_change(self._flush)


class AxisWindow(Window):

    def __init__(self, conn, monitor_id=0):
        super().__init__(
            layer="top",
            exclusivity="normal",
            monitor_id=monitor_id,
            visible=False,
        )
        self._destroyed = False
        self._island_width = 0
        self._cap_on = False
        self._collapsed = False
        self._flush = False
        self._island_round = False
        self.anchor = "left top"

        self.ws = DetachedTopWorkspaces(conn=conn, v_align="center", h_align="start")
        self.island = self.ws.num_box
        self.cap = Box(name="axis-cap")
        self.cap.set_size_request(_CAP_PX, -1)
        self.corner_static = Box(
            name="bar-left-corner",
            orientation="v",
            v_align="start",
            h_align="start",
            style=f"margin-left: -{_CAP_PX}px;",
            children=[MyCorner("top-left")],
        )

        self._sync_margin(self.island.get_preferred_width()[1])

        start_container = Box(
            name="start-container",
            spacing=4,
            v_align="start",
            children=[self.ws],
        )
        self.corner = Box(
            name="bar-left-corner",
            orientation="v",
            v_align="start",
            h_align="start",
            children=[MyCorner("top-left")],
        )
        left_group = Box(
            name="bar-group-left",
            orientation="h",
            spacing=0,
            v_align="start",
            children=[start_container, self.corner],
        )
        self.add(
            Box(
                name="bar-inner",
                orientation="h",
                h_align="start",
                v_align="start",
                children=[left_group],
            )
        )

        self._island_hid = self.island.connect("size-allocate", self._on_island_allocate)
        self._ws_hid = self.ws.connect("size-allocate", self._on_ws_allocate)
        self.ws.on_flush_change = self._on_flush_change
        self.connect("destroy", self._on_destroy)
        self.show()

    def _sync_margin(self, island_width: int) -> None:
        if island_width == self._island_width:
            return
        self._island_width = island_width
        self.margin = f"0px 0px 0px {island_width - _EDGE_PX}px"

    def _sync_island(self) -> None:
        if self._destroyed:
            return
        rounded = self._collapsed and not self._flush
        if rounded == self._island_round:
            return
        self._island_round = rounded
        if rounded:
            self.island.add_style_class(_ISLAND_ROUND_CLASS)
        else:
            self.island.remove_style_class(_ISLAND_ROUND_CLASS)

    def _on_island_allocate(self, _widget, alloc) -> None:
        if alloc.width > 0:
            self._sync_margin(alloc.width)

    def _on_flush_change(self, flush: bool) -> None:
        self._flush = flush
        self._sync_island()

    def _on_ws_allocate(self, _widget, alloc) -> None:
        if self._destroyed:
            return
        cap_on = alloc.width >= _CAP_PX
        if cap_on != self._cap_on:
            self._cap_on = cap_on
            if cap_on:
                self.cap.add_style_class(_CAP_ON_CLASS)
            else:
                self.cap.remove_style_class(_CAP_ON_CLASS)

        collapsed = alloc.width < _COLLAPSED_PX
        if collapsed != self._collapsed:
            self._collapsed = collapsed
            opacity = 0.0 if collapsed else 1.0
            self.corner.set_opacity(opacity)
            self.ws.set_opacity(opacity)
            self._sync_island()

    def _on_destroy(self, *_) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        self.ws.on_flush_change = None
        if GObject.signal_handler_is_connected(self.island, self._island_hid):
            self.island.disconnect(self._island_hid)
        if GObject.signal_handler_is_connected(self.ws, self._ws_hid):
            self.ws.disconnect(self._ws_hid)