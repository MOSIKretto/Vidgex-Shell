from fabric.widgets.box import Box
from fabric.widgets.shapes import Corner

from widgets.wayland import WaylandWindow as Window


class MyCorner(Box):
    def __init__(self, corner):
        super().__init__(
            name="corner-container",
            children=Corner(
                name="corner",
                orientation=corner,
                size=20,
            ),
        )


class Corners(Window):
    def __init__(self):
        super().__init__(
            name="corners",
            layer="bottom",
            anchor="top bottom left right",
            exclusivity="normal",
            visible=False,
            all_visible=False,
        )

        top_corners = Box(
            name="top-corners",
            orientation="h",
            children=[
                MyCorner("top-left"),
                Box(h_expand=True),
                MyCorner("top-right"),
            ],
        )

        bottom_corners = Box(
            name="bottom-corners",
            orientation="h",
            children=[
                MyCorner("bottom-left"),
                Box(h_expand=True),
                MyCorner("bottom-right"),
            ],
        )

        self.add(
            Box(
                name="all-corners",
                orientation="v",
                children=[
                    top_corners,
                    Box(v_expand=True),
                    bottom_corners,
                ],
            )
        )

        self.show_all()