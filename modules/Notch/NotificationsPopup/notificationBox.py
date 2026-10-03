from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label
from gi.repository import Gtk

import services.icons as icons


class ActionButton(Button):
    def __init__(self, label: str, callback, index: int, total: int):
        super().__init__(
            name="action-button",
            h_expand=True,
            on_clicked=lambda *_: callback(),
            child=Label(
                name="button-label",
                h_expand=True,
                h_align="fill",
                ellipsization="end",
                max_chars_width=1,
                label=label,
            ),
        )
        style = ("start" if index == 0 else "end" if index == total - 1 else "middle") + "-action"
        self.add_style_class(style)


class NotificationBox(Box):
    def __init__(self, data, image=None):
        super().__init__(name="notification-box", orientation="v", h_align="fill", h_expand=True)
        self.data = data

        self.image_box = Box(name="notification-image", orientation="v")
        if image is not None:
            self.image_box.add(image)

        self.add(self._create_content())
        if data.actions:
            self.add(self._create_action_buttons())

    def _create_content(self) -> Box:
        d = self.data
        summary = Label(
            name="notification-summary",
            label=d.summary,
            h_align="start",
            max_chars_width=20,
            ellipsization="end",
        )
        app_name_label = Label(
            name="notification-app-name",
            label=d.app_name[:20],
            h_align="start",
            max_chars_width=12,
            ellipsization="end",
        )
        text_children = [
            Box(
                name="notification-summary-box",
                orientation="h",
                children=[summary, Box(name="notif-sep"), app_name_label],
            )
        ]
        if d.body:
            text_children.append(
                Label(
                    name="notification-body",
                    markup=d.body_markup,
                    h_align="start",
                    max_chars_width=40,
                    ellipsization="end",
                )
            )
        close_btn = Button(
            name="notif-close-button",
            child=Label(name="notif-close-label", markup=icons.cancel),
            on_clicked=lambda *_: d.close("dismissed-by-user"),
        )
        return Box(
            name="notification-content",
            spacing=8,
            h_expand=True,
            children=[
                self.image_box,
                Box(name="notification-text", orientation="v", v_align="center",
                    h_expand=True, children=text_children),
                Box(orientation="v", v_align="center", children=[close_btn]),
            ],
        )

    def _create_action_buttons(self) -> Gtk.Grid:
        actions = self.data.actions
        grid = Gtk.Grid(column_homogeneous=True, column_spacing=4)
        for i, (label, callback) in enumerate(actions):
            grid.attach(ActionButton(label, callback, i, len(actions)), i, 0, 1, 1)
        return grid