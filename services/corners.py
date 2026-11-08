import json

from fabric.hyprland.service import Hyprland
from fabric.widgets.box import Box
from fabric.widgets.shapes import Corner

from services.wayland import WaylandWindow


class MyCorner(Box):
    def __init__(self, corner: str):
        super().__init__(
            name="corner-container",
            children=(Corner(name="corner", orientation=corner, size=20),),
        )


class Corners(WaylandWindow):
    def __init__(self):
        super().__init__(
            layer="overlay",
            anchor="top bottom left right",
            exclusivity="none",
            pass_through=True,
        )

        self._shown = True

        self.corners_box = Box(
            name="all-corners",
            orientation="v",
            children=(
                Box(v_expand=True),
                Box(
                    name="bottom-corners",
                    orientation="h",
                    children=(
                        MyCorner("bottom-left"),
                        Box(h_expand=True),
                        MyCorner("bottom-right"),
                    ),
                ),
            ),
        )

        self.add(self.corners_box)
        self.show_all()

        self.hyprland = Hyprland()
        for event in ("fullscreen", "workspace", "activewindow", "closewindow", "focusedmon"):
            self.hyprland.connect(f"event::{event}", self._refresh)

        self._refresh()

    def _refresh(self, *_) -> None:
        reply = self.hyprland.send_command("j/activeworkspace").reply
        show = not json.loads(reply).get("hasfullscreen", False)
        if show != self._shown:
            self._shown = show
            self.corners_box.set_visible(show)