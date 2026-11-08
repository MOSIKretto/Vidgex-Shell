import cairo
from fabric.widgets.window import Window
from gi.repository import Gdk, Gtk, GtkLayerShell


_LAYER = {"background": 0, "bottom": 1, "top": 2, "overlay": 3}
_EXCL = {"none": 0, "normal": 1, "auto": 2}
_KBD = {"none": 0, "exclusive": 1, "on_demand": 2, "on-demand": 2}
_EDGES = ("left", "right", "top", "bottom")

def _enum(table: dict, value, default: int) -> int:
    return table.get(value.lower(), default) if isinstance(value, str) else value


class WaylandWindow(Window):
    def __init__(
        self,
        layer: str | int = 2,
        anchor: str | tuple | list | set = "",
        margin: str | tuple | list = "",
        exclusivity: str | int = 0,
        keyboard_mode: str | int = 0,
        pass_through: bool = False,
        monitor: int | Gdk.Monitor | None = None,
        title: str = "fabric",
        window_type: Gtk.WindowType = Gtk.WindowType.TOPLEVEL,
        visible: bool = True,
        all_visible: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(
            title=title, type=window_type,
            visible=False, all_visible=False, **kwargs,
        )
        self._exclusivity = 0
        self._pass_through = False

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, title)

        self.layer = layer
        if anchor: self.anchor = anchor
        if margin: self.margin = margin
        if exclusivity: self.exclusivity = exclusivity
        if keyboard_mode: self.keyboard_mode = keyboard_mode
        if pass_through: self.pass_through = True
        if monitor is not None: self.monitor = monitor

        self.connect("notify::title", lambda *_: GtkLayerShell.set_namespace(self, self.get_title()))

        if all_visible: self.show_all()
        elif visible: self.show()

    @property
    def layer(self) -> int:
        return GtkLayerShell.get_layer(self)

    @layer.setter
    def layer(self, value: str | int) -> None:
        GtkLayerShell.set_layer(self, _enum(_LAYER, value, 2))

    @property
    def keyboard_mode(self) -> int:
        return GtkLayerShell.get_keyboard_mode(self)

    @keyboard_mode.setter
    def keyboard_mode(self, value: str | int) -> None:
        GtkLayerShell.set_keyboard_mode(self, _enum(_KBD, value, 0))

    @property
    def exclusivity(self) -> int:
        return self._exclusivity

    @exclusivity.setter
    def exclusivity(self, value: str | int) -> None:
        val = _enum(_EXCL, value, 0)
        if self._exclusivity != val:
            self._exclusivity = val
            if val == 2: GtkLayerShell.auto_exclusive_zone_enable(self)
            else: GtkLayerShell.set_exclusive_zone(self, -1 if val == 1 else 0)

    def _set_anchor(self, value: str | tuple | list | set) -> None:
        v = value.lower().split() if isinstance(value, str) else value
        for i, name in enumerate(_EDGES):
            GtkLayerShell.set_anchor(self, i, name in v or i in v)

    def _set_margin(self, value: str | tuple | list) -> None:
        if isinstance(value, str):
            nums = [int(float(x)) for x in value.replace(",", " ").replace("px", "").split()]
        else:
            nums = [int(v) for v in value if isinstance(v, (int, float))]
        nums = nums[:4] or [0]
        n = len(nums)
        t = nums[0]
        r = nums[1] if n > 1 else t
        b = nums[2] if n > 2 else t
        l = nums[3] if n > 3 else r
        GtkLayerShell.set_margin(self, 0, l)
        GtkLayerShell.set_margin(self, 1, r)
        GtkLayerShell.set_margin(self, 2, t)
        GtkLayerShell.set_margin(self, 3, b)

    def _set_monitor(self, value: int | Gdk.Monitor | None) -> None:
        if value is not None and not isinstance(value, Gdk.Monitor):
            display = Gdk.Display.get_default()
            value = display.get_monitor(value) if 0 <= value < display.get_n_monitors() else None
        GtkLayerShell.set_monitor(self, value)

    anchor = property(fset=_set_anchor)
    margin = property(fset=_set_margin)
    monitor = property(fset=_set_monitor)

    @property
    def pass_through(self) -> bool:
        return self._pass_through

    @pass_through.setter
    def pass_through(self, value: bool) -> None:
        if self._pass_through != value:
            self._pass_through = value
            if self.get_visible():
                self._apply_input_region()

    def _apply_input_region(self) -> None:
        self.input_shape_combine_region(cairo.Region() if self._pass_through else None)

    def show(self) -> None:
        super().show()
        if self._pass_through:
            self._apply_input_region()

    def show_all(self) -> None:
        super().show_all()
        if self._pass_through:
            self._apply_input_region()

    def steal_input(self) -> None:
        self.keyboard_mode = "exclusive"

    def return_input(self) -> None:
        self.keyboard_mode = "none"