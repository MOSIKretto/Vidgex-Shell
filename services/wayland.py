import cairo
from fabric.widgets.window import Window
from gi.repository import Gdk, Gtk, GtkLayerShell

_LAYER_MAP = {"background": 0, "bottom": 1, "top": 2, "overlay": 3}
_EXCL_MAP = {"none": 0, "normal": 1, "auto": 2}
_KBD_MAP = {"none": 0, "exclusive": 1, "on_demand": 2, "on-demand": 2}

_EMPTY_REGION = cairo.Region()


class WaylandWindow(Window):
    __slots__ = (
        "_layer", "_exclusivity", "_pass_through",
        "_keyboard_mode", "_keyboard_interactivity",
        "_monitor_obj", "_display",
    )

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
            title=title,
            type=window_type,
            visible=False,
            all_visible=False,
            **kwargs,
        )

        self._display = Gdk.Display.get_default()
        self._monitor_obj = None
        
        self._layer = self._exclusivity = self._keyboard_mode = -1
        self._keyboard_interactivity = self._pass_through = False

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, title)

        self.layer = layer
        if anchor: self.anchor = anchor
        if margin: self.margin = margin
        if exclusivity: self.exclusivity = exclusivity
        if keyboard_mode: self.keyboard_mode = keyboard_mode
        if pass_through: self.pass_through = True
        if monitor is not None: self.monitor = monitor

        self.connect("notify::title", self._on_title_changed)

        if all_visible: self.show_all()
        elif visible: self.show()

    def _on_title_changed(self, *_) -> None:
        GtkLayerShell.set_namespace(self, self.get_title())

    @property
    def layer(self) -> int:
        return self._layer if self._layer >= 0 else 2

    @layer.setter
    def layer(self, value: str | int) -> None:
        val = _LAYER_MAP.get(value.lower(), 2) if type(value) is str else value
        if self._layer != val:
            self._layer = val
            GtkLayerShell.set_layer(self, val)

    @property
    def anchor(self) -> int:
        return (GtkLayerShell.get_anchor(self, 0) |
               (GtkLayerShell.get_anchor(self, 1) << 1) |
               (GtkLayerShell.get_anchor(self, 2) << 2) |
               (GtkLayerShell.get_anchor(self, 3) << 3))

    @anchor.setter
    def anchor(self, value: str | tuple | list | set) -> None:
        if type(value) is str:
            v = value.lower().split()
            GtkLayerShell.set_anchor(self, 0, "left" in v)
            GtkLayerShell.set_anchor(self, 1, "right" in v)
            GtkLayerShell.set_anchor(self, 2, "top" in v)
            GtkLayerShell.set_anchor(self, 3, "bottom" in v)
        else:
            GtkLayerShell.set_anchor(self, 0, "left" in value or 0 in value)
            GtkLayerShell.set_anchor(self, 1, "right" in value or 1 in value)
            GtkLayerShell.set_anchor(self, 2, "top" in value or 2 in value)
            GtkLayerShell.set_anchor(self, 3, "bottom" in value or 3 in value)

    @property
    def margin(self) -> tuple[int, int, int, int]:
        return (
            GtkLayerShell.get_margin(self, 2),
            GtkLayerShell.get_margin(self, 1),
            GtkLayerShell.get_margin(self, 3),
            GtkLayerShell.get_margin(self, 0),
        )

    @margin.setter
    def margin(self, value: str | tuple | list) -> None:
        nums = []
        if type(value) is str:
            for x in value.replace(",", " ").replace("px", "").split():
                try: nums.append(int(float(x)))
                except ValueError: pass
        else:
            nums = [int(v) for v in value[:4] if isinstance(v, (int, float))]

        l = len(nums)
        if l == 0: t = r = b = left = 0
        elif l == 1: t = r = b = left = nums[0]
        elif l == 2: t = b = nums[0]; r = left = nums[1]
        elif l == 3: t = nums[0]; r = left = nums[1]; b = nums[2]
        else: t, r, b, left = nums[:4]

        GtkLayerShell.set_margin(self, 0, left)
        GtkLayerShell.set_margin(self, 1, r)
        GtkLayerShell.set_margin(self, 2, t)
        GtkLayerShell.set_margin(self, 3, b)

    @property
    def monitor(self) -> int | None:
        if not self._display or not self._monitor_obj: return None
        for i in range(self._display.get_n_monitors()):
            if self._display.get_monitor(i) is self._monitor_obj: return i
        return None

    @monitor.setter
    def monitor(self, value: int | Gdk.Monitor | None) -> None:
        mon = None
        if type(value) is Gdk.Monitor: mon = value
        elif value is not None and self._display:
            if 0 <= value < self._display.get_n_monitors():
                mon = self._display.get_monitor(value)

        if self._monitor_obj is not mon:
            self._monitor_obj = mon
            if mon: GtkLayerShell.set_monitor(self, mon)

    @property
    def exclusivity(self) -> int:
        return self._exclusivity if self._exclusivity >= 0 else 0

    @exclusivity.setter
    def exclusivity(self, value: str | int) -> None:
        val = _EXCL_MAP.get(value.lower(), 0) if type(value) is str else value
        if self._exclusivity != val:
            self._exclusivity = val
            if val == 1: GtkLayerShell.set_exclusive_zone(self, -1)
            elif val == 2: GtkLayerShell.auto_exclusive_zone_enable(self)
            else: GtkLayerShell.set_exclusive_zone(self, 0)

    @property
    def keyboard_mode(self) -> int:
        return self._keyboard_mode if self._keyboard_mode >= 0 else 0

    @keyboard_mode.setter
    def keyboard_mode(self, value: str | int) -> None:
        val = _KBD_MAP.get(value.lower(), 0) if type(value) is str else value
        if self._keyboard_mode != val:
            self._keyboard_mode = val
            GtkLayerShell.set_keyboard_mode(self, val)

    @property
    def keyboard_interactivity(self) -> bool:
        return self._keyboard_interactivity

    @keyboard_interactivity.setter
    def keyboard_interactivity(self, value: bool) -> None:
        if self._keyboard_interactivity != value:
            self._keyboard_interactivity = value
            GtkLayerShell.set_keyboard_interactivity(self, value)

    @property
    def pass_through(self) -> bool:
        return self._pass_through

    @pass_through.setter
    def pass_through(self, value: bool) -> None:
        if self._pass_through != value:
            self._pass_through = value
            if self.get_visible(): self._apply_input_region()

    def _apply_input_region(self) -> None:
        self.input_shape_combine_region(_EMPTY_REGION if self._pass_through else None)

    def show(self) -> None:
        super().show()
        if self._pass_through: self._apply_input_region()

    def show_all(self) -> None:
        super().show_all()
        if self._pass_through: self._apply_input_region()

    def steal_input(self) -> None:
        self.keyboard_interactivity = True

    def return_input(self) -> None:
        self.keyboard_interactivity = False