from fabric.widgets.window import Window
from gi.repository import Gdk, Gtk, GtkLayerShell


class WaylandWindow(Window):
    __slots__ = (
        "_layer", "_exclusivity", "_pass_through",
        "_keyboard_mode", "_keyboard_interactivity",
        "_monitor_obj", "_display",
    )

    def __init__(
        self,
        layer: str = "top",
        anchor: str | tuple | list | set = "",
        margin: str | tuple | list = "0px 0px 0px 0px",
        exclusivity: str = "none",
        title: str = "fabric",
        window_type=Gtk.WindowType.TOPLEVEL,
        monitor: int | Gdk.Monitor | None = None,
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

        self._display = Gdk.Display.get_default()
        self._monitor_obj = None

        self._layer = -1
        self._exclusivity = -1
        self._pass_through = False
        self._keyboard_mode = -1
        self._keyboard_interactivity = False

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, title)

        self.layer = layer
        self.anchor = anchor
        self.margin = margin
        self.exclusivity = exclusivity
        if monitor is not None:
            self.monitor = monitor

        self.connect("notify::title", lambda *_: GtkLayerShell.set_namespace(self, self.title))

        if visible:
            (self.show_all if all_visible else self.show)()

    @property
    def layer(self) -> str:
        return ("background", "bottom", "top", "overlay")[self._layer if self._layer != -1 else 2]

    @layer.setter
    def layer(self, value: str | int):
        map_ = {"background": 0, "bottom": 1, "top": 2, "overlay": 3}
        new = value if isinstance(value, int) else map_.get(str(value).lower(), 2)
        if self._layer == new:
            return
        self._layer = new
        GtkLayerShell.set_layer(self, new)

    @property
    def anchor(self) -> str:
        parts = []
        if GtkLayerShell.get_anchor(self, 2): parts.append("top")
        if GtkLayerShell.get_anchor(self, 1): parts.append("right")
        if GtkLayerShell.get_anchor(self, 3): parts.append("bottom")
        if GtkLayerShell.get_anchor(self, 0): parts.append("left")
        return " ".join(parts)

    @anchor.setter
    def anchor(self, value):
        edge_map = {"left": 0, "right": 1, "top": 2, "bottom": 3}
        new = set()

        if isinstance(value, str) and value.strip():
            for part in value.lower().split():
                if part in edge_map:
                    new.add(edge_map[part])
        elif isinstance(value, (tuple, list, set)):
            for v in value:
                if isinstance(v, int) and 0 <= v <= 3:
                    new.add(v)
                elif isinstance(v, str) and v.lower() in edge_map:
                    new.add(edge_map[v.lower()])

        for edge in range(4):
            GtkLayerShell.set_anchor(self, edge, edge in new)

    @property
    def margin(self):
        return (
            GtkLayerShell.get_margin(self, 2),  # top
            GtkLayerShell.get_margin(self, 1),  # right
            GtkLayerShell.get_margin(self, 3),  # bottom
            GtkLayerShell.get_margin(self, 0),  # left
        )

    @margin.setter
    def margin(self, value):
        if isinstance(value, str):
            try:
                nums = [int(float(p.replace("px", ""))) for p in value.lower().split() if p.replace("px", "").replace("-", "").replace(".", "").isdigit() or p == "0"]
            except:
                nums = []
        else:
            nums = [int(v) if isinstance(v, (int, float)) else 0 for v in (value if isinstance(value, (tuple, list)) else [])][:4]

        t = r = b = l = 0
        if nums:
            t = nums[0]
            if len(nums) >= 2: r = nums[1]
            if len(nums) >= 3: b = nums[2]
            if len(nums) >= 4: l = nums[3]
            if len(nums) == 1: r = b = l = t
            if len(nums) == 2: b = t; l = r
            if len(nums) == 3: l = r

        GtkLayerShell.set_margin(self, 2, t)
        GtkLayerShell.set_margin(self, 1, r)
        GtkLayerShell.set_margin(self, 3, b)
        GtkLayerShell.set_margin(self, 0, l)

    @property
    def monitor(self) -> int | None:
        if not self._display or not self._monitor_obj:
            return None
        for i in range(self._display.get_n_monitors()):
            if self._display.get_monitor(i) is self._monitor_obj:
                return i
        return None

    @monitor.setter
    def monitor(self, value: int | Gdk.Monitor):
        mon = None
        if isinstance(value, int) and self._display:
            if 0 <= value < self._display.get_n_monitors():
                mon = self._display.get_monitor(value)
        elif isinstance(value, Gdk.Monitor):
            mon = value

        if self._monitor_obj is mon:
            return
        self._monitor_obj = mon
        GtkLayerShell.set_monitor(self, mon)

    @property
    def exclusivity(self) -> str:
        return ("", "none", "normal", "auto")[self._exclusivity if self._exclusivity != -1 else 1]

    @exclusivity.setter
    def exclusivity(self, value: str | int):
        map_ = {"none": 1, "normal": 2, "auto": 3}
        new = value if isinstance(value, int) else map_.get(str(value).lower(), 1)
        if self._exclusivity == new:
            return
        self._exclusivity = new
        if new == 2:
            GtkLayerShell.set_exclusive_zone(self, -1)
        elif new == 3:
            GtkLayerShell.auto_exclusive_zone_enable(self)
        else:
            GtkLayerShell.set_exclusive_zone(self, 0)

    @property
    def keyboard_mode(self) -> str:
        return ("none", "exclusive", "on_demand")[self._keyboard_mode if self._keyboard_mode != -1 else 0]

    @keyboard_mode.setter
    def keyboard_mode(self, value: str | int):
        map_ = {"none": 0, "exclusive": 1, "on_demand": 2}
        new = value if isinstance(value, int) else map_.get(str(value).lower(), 0)
        if self._keyboard_mode == new:
            return
        self._keyboard_mode = new
        GtkLayerShell.set_keyboard_mode(self, new)

    @property
    def keyboard_interactivity(self) -> bool:
        return self._keyboard_interactivity

    @keyboard_interactivity.setter
    def keyboard_interactivity(self, value: bool):
        value = bool(value)
        if self._keyboard_interactivity == value:
            return
        self._keyboard_interactivity = value
        GtkLayerShell.set_keyboard_interactivity(self, value)

    @property
    def pass_through(self) -> bool:
        return self._pass_through

    @pass_through.setter
    def pass_through(self, value: bool):
        value = bool(value)
        if self._pass_through == value:
            return
        self._pass_through = value
        if self.get_visible():
            self._update_input_region()

    def _update_input_region(self):
        region = None
        if self._pass_through:
            # Импортируем cairo только при необходимости создать пустой регион
            import cairo
            region = cairo.Region()
        
        self.input_shape_combine_region(region)

    def show(self):
        super().show()
        if self._pass_through:
            self._update_input_region()

    def show_all(self):
        super().show_all()
        if self._pass_through:
            self._update_input_region()

    set_margin          = margin.fset
    set_layer           = layer.fset
    set_exclusivity     = exclusivity.fset
    set_keyboard_mode   = keyboard_mode.fset
    set_pass_through    = pass_through.fset
    set_anchor          = anchor.fset

    def steal_input(self):
        self.keyboard_interactivity = True

    def return_input(self):
        self.keyboard_interactivity = False