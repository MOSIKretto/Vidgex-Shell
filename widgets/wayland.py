from gi.repository import Gdk, GObject, Gtk
import re

import cairo
import gi
from fabric.core.service import Property
from fabric.utils.helpers import extract_css_values, get_enum_member
from fabric.widgets.window import Window
from loguru import logger

gi.require_version("Gtk", "3.0")

try:
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import GtkLayerShell
except ImportError:
    raise ImportError("install gtk-layer-shell first")


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

_ALL_EDGES = [
    Edge.TOP,
    Edge.RIGHT,
    Edge.BOTTOM,
    Edge.LEFT,
]


class WaylandWindow(Window):
    @Property(
        Layer,
        flags="read-write",
        default_value=Layer.TOP,
    )
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
            if not display:
                 logger.error("Default Gdk.Display is not available.")
                 return False
            monitor = display.get_monitor(monitor)

        if monitor is not None:
            GtkLayerShell.set_monitor(self, monitor)
            return True
        return False

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

    @Property(
        KeyboardMode,
        "read-write",
        default_value=KeyboardMode.NONE,
    )
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
        return tuple(
            x
            for x in _ALL_EDGES
            if GtkLayerShell.get_anchor(self, x)
        )

    @anchor.setter
    def anchor(self, value):
        if isinstance(value, (list, tuple)) and all(
            isinstance(edge, Edge) for edge in value
        ):
            edges_to_anchor = set(value)
            for edge in _ALL_EDGES:
                should_anchor = edge in edges_to_anchor
                GtkLayerShell.set_anchor(self, edge, should_anchor)
        
        elif isinstance(value, str):
            for edge, anchored in WaylandWindow.extract_edges_from_string(value).items():
                GtkLayerShell.set_anchor(self, edge, anchored)

    @Property(tuple, flags="read-write")
    def margin(self):
        return tuple(
            GtkLayerShell.get_margin(self, x)
            for x in _ALL_EDGES
        )

    @margin.setter
    def margin(self, value):
        for edge, mrgv in WaylandWindow.extract_margin(value).items():
            GtkLayerShell.set_margin(self, edge, mrgv)

    def __init__(
        self,
        layer="top",
        anchor="",
        margin="0px 0px 0px 0px",
        exclusivity="none",
        keyboard_mode="none",
        pass_through=False,
        monitor=None,
        title="fabric",
        type=Gtk.WindowType.TOPLEVEL,
        child=None,
        name=None,
        visible=True,
        all_visible=False,
        style=None,
        style_classes=None,
        tooltip_text=None,
        tooltip_markup=None,
        h_align=None,
        v_align=None,
        h_expand=False,
        v_expand=False,
        size=None,
        **kwargs,
    ):
        Window.__init__(
            self,
            title=title,
            type=type,
            child=child,
            name=name,
            visible=False,
            all_visible=False,
            style=style,
            style_classes=style_classes,
            tooltip_text=tooltip_text,
            tooltip_markup=tooltip_markup,
            h_align=h_align,
            v_align=v_align,
            h_expand=h_expand,
            v_expand=v_expand,
            size=size,
            **kwargs,
        )
        self._layer = Layer.ENTRY_NUMBER
        self._keyboard_mode = KeyboardMode.NONE
        self._exclusivity = WaylandWindowExclusivity.NONE
        self._pass_through = pass_through

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, title)
        self.connect(
            "notify::title",
            lambda *_: GtkLayerShell.set_namespace(self, self.get_title()),
        )
        
        if monitor is not None:
            self.monitor = monitor
        self.layer = layer
        self.anchor = anchor
        self.margin = margin
        self.keyboard_mode = keyboard_mode
        self.exclusivity = exclusivity
        self.pass_through = pass_through
        
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
        self.do_handle_post_show_request()

    def show_all(self):
        super().show_all()
        self.do_handle_post_show_request()

    def do_handle_post_show_request(self):
        if not self.get_children():
            logger.warning(
                "[WaylandWindow] showing an empty window is not recommended"
            )
        self.pass_through = self._pass_through

    @staticmethod
    def extract_anchor_values(string):
        matches = re.findall(r"\b(left|right|top|bottom)\b", string, re.IGNORECASE)
        return tuple(
            {match.lower() for match in matches}
        )

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
    def extract_margin(input):
        if isinstance(input, str):
            margins = extract_css_values(input.lower())
        elif isinstance(input, (tuple, list)) and len(input) == 4:
            margins = input
        else:
            margins = (0, 0, 0, 0)
        
        if len(margins) != 4:
            margins = (0, 0, 0, 0) 
            logger.warning(f"Invalid margin format: {input}")
            
        return {
            Edge.TOP: margins[0],
            Edge.RIGHT: margins[1],
            Edge.BOTTOM: margins[2],
            Edge.LEFT: margins[3],
        }