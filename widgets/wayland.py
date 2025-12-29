from gi.repository import Gdk, GObject, Gtk
import re

import cairo
import gi
from fabric.core.service import Property
from fabric.utils.helpers import extract_css_values, get_enum_member
from fabric.widgets.window import Window

gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import GtkLayerShell


class WaylandWindowExclusivity:
    NONE = 1
    NORMAL = 2
    AUTO = 3


class Layer(GObject.GEnum):
    BACKGROUND = 0
    BOTTOM = 1
    TOP = 2
    OVERLAY = 3
    ENTRY_NUMBER = 4


class KeyboardMode(GObject.GEnum):
    NONE = 0
    EXCLUSIVE = 1
    ON_DEMAND = 2
    ENTRY_NUMBER = 3


class Edge(GObject.GEnum):
    LEFT = 0
    RIGHT = 1
    TOP = 2
    BOTTOM = 3
    ENTRY_NUMBER = 4

_ALL_EDGES = [Edge.TOP, Edge.RIGHT, Edge.BOTTOM, Edge.LEFT]


class WaylandWindow(Window):
    @Property(Layer, flags="read-write", default_value=Layer.TOP)
    def layer(self):
        return self._layer

    @layer.setter
    def layer(self, value):
        self._layer = get_enum_member(Layer, value, default=Layer.TOP)
        GtkLayerShell.set_layer(self, self._layer)

    @Property(int, "read-write")
    def monitor(self):
        monitor = GtkLayerShell.get_monitor(self)
        if not monitor:
            return -1
        
        display = monitor.get_display() or Gdk.Display.get_default()
        for i in range(display.get_n_monitors()):
            if display.get_monitor(i) is monitor:
                return i
        return -1

    @monitor.setter
    def monitor(self, monitor):
        if isinstance(monitor, int):
            display = Gdk.Display.get_default()
            if display:
                monitor = display.get_monitor(monitor)
        
        if monitor:
            GtkLayerShell.set_monitor(self, monitor)

    @Property(WaylandWindowExclusivity, "read-write")
    def exclusivity(self):
        return self._exclusivity

    @exclusivity.setter
    def exclusivity(self, value):
        value = get_enum_member(WaylandWindowExclusivity, value, default=WaylandWindowExclusivity.NONE)
        self._exclusivity = value
        
        if value == WaylandWindowExclusivity.NORMAL:
            GtkLayerShell.set_exclusive_zone(self, True)
        elif value == WaylandWindowExclusivity.AUTO:
            GtkLayerShell.auto_exclusive_zone_enable(self)
        else:
            GtkLayerShell.set_exclusive_zone(self, False)

    @Property(bool, "read-write", default_value=False)
    def pass_through(self):
        return self._pass_through

    @pass_through.setter
    def pass_through(self, pass_through=False):
        self._pass_through = pass_through
        region = cairo.Region() if pass_through else None
        self.input_shape_combine_region(region)

    @Property(KeyboardMode, "read-write", default_value=KeyboardMode.NONE)
    def keyboard_mode(self):
        if GtkLayerShell.get_keyboard_interactivity(self):
            return KeyboardMode.EXCLUSIVE
        return GtkLayerShell.get_keyboard_mode(self)

    @keyboard_mode.setter
    def keyboard_mode(self, value):
        self._keyboard_mode = get_enum_member(KeyboardMode, value, default=KeyboardMode.NONE)
        GtkLayerShell.set_keyboard_mode(self, self._keyboard_mode)

    @Property(tuple, "read-write")
    def anchor(self):
        return tuple(edge for edge in _ALL_EDGES if GtkLayerShell.get_anchor(self, edge))

    @anchor.setter
    def anchor(self, value):
        if isinstance(value, (list, tuple)):
            self._set_anchor_from_edges(value)
        elif isinstance(value, str):
            self._set_anchor_from_string(value)

    @Property(tuple, flags="read-write")
    def margin(self):
        return tuple(GtkLayerShell.get_margin(self, edge) for edge in _ALL_EDGES)

    @margin.setter
    def margin(self, value):
        margins = self._parse_margin(value)
        for edge, margin_value in margins.items():
            GtkLayerShell.set_margin(self, edge, margin_value)

    def __init__(
        self,
        layer="top",
        anchor="",
        margin="0px 0px 0px 0px",
        exclusivity="none",
        title="fabric",
        type=Gtk.WindowType.TOPLEVEL,
        visible=True,
        all_visible=False,
        **kwargs,
    ):
        Window.__init__(
            self,
            title=title,
            type=type,
            visible=False,
            all_visible=False,
            **kwargs,
        )
        
        self._layer = Layer.ENTRY_NUMBER
        self._keyboard_mode = KeyboardMode.NONE
        self._exclusivity = WaylandWindowExclusivity.NONE
        self._pass_through = False

        self._init_gtk_layer_shell(title)
        
        self.layer = layer
        self.anchor = anchor
        self.margin = margin
        self.exclusivity = exclusivity
        
        self._show_window(visible, all_visible)

    def _init_gtk_layer_shell(self, title):
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, title)
        self.connect("notify::title", self._on_title_changed)

    def _on_title_changed(self, *args):
        GtkLayerShell.set_namespace(self, self.get_title())

    def _set_anchor_from_edges(self, edges):
        edges_to_anchor = set(edges)
        for edge in _ALL_EDGES:
            should_anchor = edge in edges_to_anchor
            GtkLayerShell.set_anchor(self, edge, should_anchor)

    def _set_anchor_from_string(self, string):
        edges_dict = self.extract_edges_from_string(string)
        for edge, anchored in edges_dict.items():
            GtkLayerShell.set_anchor(self, edge, anchored)

    def _parse_margin(self, input_value):
        if isinstance(input_value, str):
            margins = extract_css_values(input_value.lower())
        elif isinstance(input_value, (tuple, list)) and len(input_value) == 4:
            margins = input_value
        else:
            margins = (0, 0, 0, 0)
        
        if len(margins) != 4:
            margins = (0, 0, 0, 0)
        
        return {
            Edge.TOP: margins[0],
            Edge.RIGHT: margins[1],
            Edge.BOTTOM: margins[2],
            Edge.LEFT: margins[3],
        }

    def _show_window(self, visible, all_visible):
        if all_visible:
            self.show_all()
        elif visible:
            self.show()

    def steal_input(self):
        GtkLayerShell.set_keyboard_interactivity(self, True)

    def return_input(self):
        GtkLayerShell.set_keyboard_interactivity(self, False)

    def show(self):
        super().show()
        self._handle_post_show()

    def show_all(self):
        super().show_all()
        self._handle_post_show()

    def _handle_post_show(self):
        self.pass_through = self._pass_through
    
    @staticmethod
    def extract_anchor_values(string):
        matches = re.findall(r"\b(left|right|top|bottom)\b", string, re.IGNORECASE)
        return tuple(match.lower() for match in matches)

    @staticmethod
    def extract_edges_from_string(string):
        anchor_values = WaylandWindow.extract_anchor_values(string.lower())
        return {
            Edge.TOP: "top" in anchor_values,
            Edge.RIGHT: "right" in anchor_values,
            Edge.BOTTOM: "bottom" in anchor_values,
            Edge.LEFT: "left" in anchor_values,
        }

    @staticmethod
    def extract_margin(input_value):
        if isinstance(input_value, str):
            margins = extract_css_values(input_value.lower())
        elif isinstance(input_value, (tuple, list)) and len(input_value) == 4:
            margins = input_value
        else:
            margins = (0, 0, 0, 0)
        
        if len(margins) != 4:
            margins = (0, 0, 0, 0)
            
        return {
            Edge.TOP: margins[0],
            Edge.RIGHT: margins[1],
            Edge.BOTTOM: margins[2],
            Edge.LEFT: margins[3],
        }