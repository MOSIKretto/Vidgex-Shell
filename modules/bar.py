from fabric.hyprland.widgets import (
    HyprlandLanguage as Language,
    HyprlandWorkspaces as Workspaces,
)
from fabric.widgets.box import Box
from fabric.widgets.label import Label
from fabric.widgets.button import Button
from fabric.widgets.datetime import DateTime
from fabric.widgets.revealer import Revealer
from fabric.widgets.centerbox import CenterBox
from gi.repository import Gdk

from modules.metrics import Battery, MetricsSmall, NetworkApplet
from modules.systemtray import SystemTray
from modules.controls import ControlSmall
from widgets.wayland import WaylandWindow as Window
import modules.icons as icons


class Bar(Window):
    def __init__(self, monitor_id=0, **kwargs):
        super().__init__(exclusivity="auto")

        self.monitor_id = monitor_id
        self.notch = kwargs.get("notch")

        self.anchor = "left top right"
        self.set_margin("-4px -4px -8px -4px")

        self.language = Language()

        self._build_ui()
        self._setup_signals()

    def _build_ui(self):
        self.workspaces = Workspaces(name="workspaces", spacing=8)
        self.systray = SystemTray()
        self.network = NetworkApplet()

        left_revealer = Revealer(
            name="bar-revealer",
            transition_type="slide-right",
            child_revealed=True,
            child=Box(
                name="bar-revealer-box",
                spacing=4,
                children=[
                    self.systray,
                    Box(name="network-container", children=[self.network]),
                ],
            ),
        )

        self.metrics = MetricsSmall()
        self.control = ControlSmall()

        right_revealer = Revealer(
            name="bar-revealer",
            transition_type="slide-left",
            child_revealed=True,
            child=Box(
                name="bar-revealer-box",
                spacing=4,
                children=[self.metrics, self.control],
            ),
        )

        self.lang_label = Label(name="lang-label")
        self.date_time = DateTime(name="date-time", formatters=["%H:%M"])
        self.battery = Battery()

        self.button_power = Button(
            name="button-bar",
            tooltip_markup="<b>Меню питания</b>",
            on_clicked=self.power_menu,
            child=Label(
                name="button-bar-label",
                markup=icons.shutdown,
            ),
        )
        self._setup_cursor_hover(self.button_power)

        self.add(
            CenterBox(
                name="bar-inner",
                start_children=Box(
                    name="start-container",
                    spacing=4,
                    children=[
                        Box(children=[self.workspaces]),
                        Box(name="boxed-revealer", children=[left_revealer]),
                    ],
                ),
                end_children=Box(
                    name="end-container",
                    spacing=4,
                    children=[
                        Box(name="boxed-revealer", children=[right_revealer]),
                        Box(
                            name="power-battery-container",
                            children=[
                                self.date_time,
                                Box(
                                    name="language-indicator",
                                    children=[self.lang_label],
                                ),
                                self.battery,
                                self.button_power,
                            ],
                        ),
                    ],
                ),
            )
        )

    def _setup_signals(self):
        self.language.connect("notify::label", self._update_language)
        self._update_language()

    def _update_language(self, *args):
        lang = self.language.get_label()
        if not lang:
            return

        self.lang_label.set_label(lang[:3].upper())

    def _setup_cursor_hover(self, button):
        cursor = Gdk.Cursor.new_from_name(button.get_display(), "hand2")
        button.connect(
            "enter-notify-event",
            lambda *_: button.get_window().set_cursor(cursor),
        )
        button.connect(
            "leave-notify-event",
            lambda *_: button.get_window().set_cursor(None),
        )

    def power_menu(self, *args):
        if self.notch:
            self.notch.open_notch("power")
