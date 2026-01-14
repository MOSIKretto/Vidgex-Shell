from fabric.widgets.window import Window

from gi.repository import Gdk, Gtk, GtkLayerShell

import cairo
import weakref
from typing import Optional, Tuple, Set, Union, List
from functools import lru_cache


class WaylandWindow(Window):
    __slots__ = (
        "_layer", "_exclusivity", "_pass_through", "_keyboard_mode",
        "_keyboard_interactivity", "_monitor_obj", "_margin_cache",
        "_anchor_cache", "_display", "_monitors_cache",
        "_monitor_index_cache", "_signal_handlers",
        "_display_signal_handlers", "_cleaned",
    )

    def __init__(
        self,
        layer: str = "top",
        anchor: Union[str, Tuple, List, Set] = "",
        margin: Union[str, Tuple[int, int, int, int]] = "0px 0px 0px 0px",
        exclusivity: str = "none",
        title: str = "fabric",
        window_type: Gtk.WindowType = Gtk.WindowType.TOPLEVEL,
        monitor: Optional[Union[int, Gdk.Monitor]] = None,
        visible: bool = True,
        all_visible: bool = False,
        **kwargs,
    ):
        super().__init__(
            title=title,
            type=window_type,
            visible=False,
            all_visible=False,
            **kwargs,
        )

        self._layer = -1 
        self._exclusivity = -1
        self._pass_through = False
        self._keyboard_mode = -1
        self._keyboard_interactivity = False
        self._monitor_obj = None
        self._margin_cache = None
        self._anchor_cache: Set[int] = set()
        self._monitors_cache = None
        self._monitor_index_cache = None
        self._signal_handlers: List[int] = []
        self._display_signal_handlers: List[int] = []
        self._cleaned = False

        self._display: Optional[Gdk.Display] = Gdk.Display.get_default()

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, title)

        self.layer = layer
        self.anchor = anchor
        self.margin = margin
        self.exclusivity = exclusivity

        if monitor is not None:
            self.monitor = monitor

        self._signal_handlers.append(self.connect("notify::title", self._on_title_changed))

        if self._display:
            weak_self = weakref.ref(self)
            def _on_monitors_changed(*_):
                inst = weak_self()
                if inst: inst._invalidate_monitors_cache()

            self._display_signal_handlers.extend((
                self._display.connect("monitor-added", _on_monitors_changed),
                self._display.connect("monitor-removed", _on_monitors_changed),
            ))

        if visible:
            self.show_all() if all_visible else self.show()

    def _invalidate_monitors_cache(self):
        self._monitors_cache = None
        self._monitor_index_cache = None

    def _on_title_changed(self, *_):
        GtkLayerShell.set_namespace(self, self.get_title())

    def _update_input_region(self):
        region = cairo.Region() if self._pass_through else None
        self.input_shape_combine_region(region)

    @property
    def layer(self) -> str:
        reverse_layer_map = ("background", "bottom", "top", "overlay")
        return reverse_layer_map[self._layer if self._layer != -1 else 2]

    @layer.setter
    def layer(self, value: Union[str, int]):
        layer_map = {"background": 0, "bottom": 1, "top": 2, "overlay": 3}
        new = value if isinstance(value, int) else layer_map.get(str(value).lower(), 2)
        if self._layer != new:
            self._layer = new
            GtkLayerShell.set_layer(self, new)

    @property
    def monitor(self) -> Optional[int]:
        if not self._monitor_obj or not self._display:
            return None
        if self._monitor_index_cache is not None:
            return self._monitor_index_cache

        if self._monitors_cache is None:
            self._monitors_cache = [self._display.get_monitor(i) for i in range(self._display.get_n_monitors())]

        try:
            idx = self._monitors_cache.index(self._monitor_obj)
            self._monitor_index_cache = idx
            return idx
        except ValueError:
            return None

    @monitor.setter
    def monitor(self, value: Union[int, Gdk.Monitor]):
        mon = None
        if isinstance(value, int) and self._display:
            if 0 <= value < self._display.get_n_monitors():
                mon = self._display.get_monitor(value)
        elif isinstance(value, Gdk.Monitor):
            mon = value

        if self._monitor_obj is not mon:
            self._monitor_obj = mon
            self._monitor_index_cache = None
            GtkLayerShell.set_monitor(self, mon)

    @property
    def exclusivity(self) -> str:
        reverse_exclusivity_map = ("", "none", "normal", "auto")
        return reverse_exclusivity_map[self._exclusivity if self._exclusivity != -1 else 1]

    @exclusivity.setter
    def exclusivity(self, value: Union[str, int]):
        exclusivity_map = {"none": 1, "normal": 2, "auto": 3}
        new = value if isinstance(value, int) else exclusivity_map.get(str(value).lower(), 1)
        if self._exclusivity == new:
            return
        self._exclusivity = new
        if new == 2: GtkLayerShell.set_exclusive_zone(self, -1)
        elif new == 3: GtkLayerShell.auto_exclusive_zone_enable(self)
        else: GtkLayerShell.set_exclusive_zone(self, 0)

    @property
    def keyboard_mode(self) -> str:
        reverse_keyboard_map = ("none", "exclusive", "on_demand")
        return reverse_keyboard_map[self._keyboard_mode if self._keyboard_mode != -1 else 0]

    @keyboard_mode.setter
    def keyboard_mode(self, value: Union[str, int]):
        keyboard_map = {"none": 0, "exclusive": 1, "on_demand": 2}
        new = value if isinstance(value, int) else keyboard_map.get(str(value).lower(), 0)
        if self._keyboard_mode != new:
            self._keyboard_mode = new
            GtkLayerShell.set_keyboard_mode(self, new)

    @property
    def keyboard_interactivity(self) -> bool:
        return self._keyboard_interactivity

    @keyboard_interactivity.setter
    def keyboard_interactivity(self, value: bool):
        if self._keyboard_interactivity != value:
            self._keyboard_interactivity = bool(value)
            GtkLayerShell.set_keyboard_interactivity(self, self._keyboard_interactivity)

    @property
    def margin(self) -> Tuple[int, int, int, int]:
        if self._margin_cache is None:
            self._margin_cache = (
                GtkLayerShell.get_margin(self, 2), GtkLayerShell.get_margin(self, 1),
                GtkLayerShell.get_margin(self, 3), GtkLayerShell.get_margin(self, 0)
            )
        return self._margin_cache

    @margin.setter
    def margin(self, value):
        new = self._parse_margin_fast(value)
        if self._margin_cache == new:
            return
        self._margin_cache = new
        for edge, val in zip((2, 1, 3, 0), new):
            GtkLayerShell.set_margin(self, edge, val)

    @property
    def anchor(self) -> str:
        reverse_edge_map = ("left", "right", "top", "bottom")
        return " ".join(reverse_edge_map[e] for e in (2, 1, 3, 0) if e in self._anchor_cache)

    @anchor.setter
    def anchor(self, value):
        self._set_anchor_fast(value)

    def _set_anchor_fast(self, value):
        edge_map = {"left": 0, "right": 1, "top": 2, "bottom": 3}
        new: Set[int] = set()
        if isinstance(value, str) and value:
            for part in value.lower().split():
                edge = edge_map.get(part)
                if edge is not None: new.add(edge)
        elif isinstance(value, (tuple, list, set)):
            for v in value:
                if isinstance(v, int) and 0 <= v <= 3:
                    new.add(v)
                elif isinstance(v, str):
                    edge = edge_map.get(v.lower())
                    if edge is not None: new.add(edge)

        if new == self._anchor_cache:
            return

        for e in self._anchor_cache - new:
            GtkLayerShell.set_anchor(self, e, False)
        for e in new - self._anchor_cache:
            GtkLayerShell.set_anchor(self, e, True)
        self._anchor_cache = new

    @staticmethod
    @lru_cache(maxsize=64)
    def _parse_margin_fast(value):
        if isinstance(value, str):
            try:
                nums = tuple(int(float(p.replace("px", ""))) for p in value.lower().split())
            except (ValueError, IndexError):
                return (0, 0, 0, 0)

            l_n = len(nums)
            if l_n == 1: return (nums[0],) * 4
            if l_n == 2: return (nums[0], nums[1], nums[0], nums[1])
            if l_n == 3: return (nums[0], nums[1], nums[2], nums[1])
            if l_n >= 4: return nums[:4]

        if isinstance(value, (tuple, list)) and len(value) >= 4:
            return tuple(int(v) if isinstance(v, (int, float)) else 0 for v in value[:4])
        return (0, 0, 0, 0)

    @property
    def pass_through(self) -> bool:
        return self._pass_through

    @pass_through.setter
    def pass_through(self, value: bool):
        if self._pass_through != value:
            self._pass_through = bool(value)
            if self.get_visible():
                self._update_input_region()

    def show(self):
        super().show()
        if self._pass_through: self._update_input_region()

    def show_all(self):
        super().show_all()
        if self._pass_through: self._update_input_region()

    def set_margin(self, v): self.margin = v
    def set_layer(self, v): self.layer = v
    def set_exclusivity(self, v): self.exclusivity = v
    def set_keyboard_mode(self, v): self.keyboard_mode = v
    def set_pass_through(self, v): self.pass_through = v
    def set_anchor(self, v): self.anchor = v

    def steal_input(self): self.keyboard_interactivity = True
    def return_input(self): self.keyboard_interactivity = False

    def cleanup(self):
        if self._cleaned: return
        self._cleaned = True

        for hid in self._signal_handlers:
            if self.handler_is_connected(hid): self.disconnect(hid)
        self._signal_handlers.clear()

        if self._display:
            for hid in self._display_signal_handlers:
                if self._display.handler_is_connected(hid): self._display.disconnect(hid)
            self._display_signal_handlers.clear()

        self._anchor_cache.clear()
        self._invalidate_monitors_cache()
        self._monitor_obj = None
        self._display = None

        parent_cleanup = getattr(super(), "cleanup", None)
        if callable(parent_cleanup): parent_cleanup()