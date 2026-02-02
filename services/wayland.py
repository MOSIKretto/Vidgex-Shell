from fabric.widgets.window import Window

from gi.repository import Gdk, Gtk, GtkLayerShell


class WaylandWindow(Window):
    """Wayland-окно с поддержкой gtk-layer-shell. Максимально оптимизировано."""

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
        if isinstance(value, str):
            value = {"background": 0, "bottom": 1, "top": 2, "overlay": 3}.get(value.lower(), 2)
        if self._layer != value:
            self._layer = value
            GtkLayerShell.set_layer(self, value)

    @property
    def anchor(self) -> int:
        """Возвращает битовую маску: 1=left, 2=right, 4=top, 8=bottom"""
        result = 0
        for i in range(4):
            if GtkLayerShell.get_anchor(self, i):
                result |= 1 << i
        return result

    @anchor.setter
    def anchor(self, value: str | tuple | list | set) -> None:
        edges = 0
        if isinstance(value, str):
            for part in value.lower().split():
                if part == "left": edges |= 1
                elif part == "right": edges |= 2
                elif part == "top": edges |= 4
                elif part == "bottom": edges |= 8
        else:
            for v in value:
                if isinstance(v, int): edges |= 1 << v
                elif v == "left": edges |= 1
                elif v == "right": edges |= 2
                elif v == "top": edges |= 4
                elif v == "bottom":
                    edges |= 8

        for i in range(4):
            GtkLayerShell.set_anchor(self, i, bool(edges & (1 << i)))

    @property
    def margin(self) -> tuple[int, int, int, int]:
        return (
            GtkLayerShell.get_margin(self, 2),  # top
            GtkLayerShell.get_margin(self, 1),  # right
            GtkLayerShell.get_margin(self, 3),  # bottom
            GtkLayerShell.get_margin(self, 0),  # left
        )

    @margin.setter
    def margin(self, value: str | tuple | list) -> None:
        if isinstance(value, str):
            nums = []
            for part in value.replace(",", " ").split():
                part = part.strip()
                if part.endswith("px"):
                    part = part[:-2]
                try:
                    nums.append(int(float(part)))
                except ValueError:
                    continue
        else:
            nums = [int(v) for v in value[:4] if isinstance(v, (int, float))]

        n = len(nums)
        if n == 0: t = r = b = l = 0
        elif n == 1: t = r = b = l = nums[0]
        elif n == 2:
            t = b = nums[0]
            r = l = nums[1]
        elif n == 3:
            t, r, b = nums[0], nums[1], nums[2]
            l = nums[1]
        else: t, r, b, l = nums[0], nums[1], nums[2], nums[3]

        GtkLayerShell.set_margin(self, 2, t)
        GtkLayerShell.set_margin(self, 1, r)
        GtkLayerShell.set_margin(self, 3, b)
        GtkLayerShell.set_margin(self, 0, l)

    @property
    def monitor(self) -> int | None:
        if not self._display or not self._monitor_obj:
            return None
        n = self._display.get_n_monitors()
        for i in range(n):
            if self._display.get_monitor(i) is self._monitor_obj:
                return i
        return None

    @monitor.setter
    def monitor(self, value: int | Gdk.Monitor | None) -> None:
        if value is None: mon = None
        elif isinstance(value, Gdk.Monitor): mon = value
        elif self._display and 0 <= value < self._display.get_n_monitors(): mon = self._display.get_monitor(value)
        else: mon = None

        if self._monitor_obj is not mon:
            self._monitor_obj = mon
            if mon: GtkLayerShell.set_monitor(self, mon)

    @property
    def exclusivity(self) -> int:
        return self._exclusivity if self._exclusivity >= 0 else 0

    @exclusivity.setter
    def exclusivity(self, value: str | int) -> None:
        if isinstance(value, str):
            value = {"none": 0, "normal": 1, "auto": 2}.get(value.lower(), 0)
        if self._exclusivity != value:
            self._exclusivity = value
            if value == 1: GtkLayerShell.set_exclusive_zone(self, -1)
            elif value == 2: GtkLayerShell.auto_exclusive_zone_enable(self)
            else: GtkLayerShell.set_exclusive_zone(self, 0)

    @property
    def keyboard_mode(self) -> int:
        return self._keyboard_mode if self._keyboard_mode >= 0 else 0

    @keyboard_mode.setter
    def keyboard_mode(self, value: str | int) -> None:
        if isinstance(value, str):
            value = {"none": 0, "exclusive": 1, "on_demand": 2}.get(value.lower().replace("-", "_"), 0)
        if self._keyboard_mode != value:
            self._keyboard_mode = value
            GtkLayerShell.set_keyboard_mode(self, value)

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
            if self.get_visible():
                self._apply_input_region()

    def _apply_input_region(self) -> None:
        if self._pass_through:
            import cairo
            self.input_shape_combine_region(cairo.Region())
        else:
            self.input_shape_combine_region(None)

    def show(self) -> None:
        super().show()
        if self._pass_through:
            self._apply_input_region()

    def show_all(self) -> None:
        super().show_all()
        if self._pass_through:
            self._apply_input_region()

    def steal_input(self) -> None:
        self.keyboard_interactivity = True

    def return_input(self) -> None:
        self.keyboard_interactivity = False