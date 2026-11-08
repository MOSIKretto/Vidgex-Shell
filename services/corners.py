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

        events = [
            "fullscreen",
            "workspace",
            "activewindow",
            "closewindow",
            "focusedmon",
        ]
        for event in events:
            self.hyprland.connect(f"event::{event}", self.update_visibility)

        self.update_visibility()

    def update_visibility(self, *args):
        response = self.hyprland.send_command("j/activeworkspace").reply.decode()
        workspace_data = json.loads(response)
        has_fullscreen = workspace_data.get("hasfullscreen", False)

        self.corners_box.set_visible(not has_fullscreen)