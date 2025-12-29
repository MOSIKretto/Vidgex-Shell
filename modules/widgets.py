from fabric.widgets.box import Box
from fabric.widgets.stack import Stack

import gi
gi.require_version("Gtk", "3.0")

from modules.bluetooth import BluetoothConnections
from modules.buttons import Buttons
from modules.calendar import Calendar
from modules.controls import ControlSliders
from modules.metrics import Metrics
from modules.network import NetworkConnections
from modules.notifications import NotificationHistory
from modules.player import Player


class Widgets(Box):
    def __init__(self, **kwargs):
        super().__init__(
            name="dash-widgets",
            visible=True,
            all_visible= True,
        )

        self.calendar = Calendar(view_mode="month")

        self.notch = kwargs["notch"]

        self.buttons = Buttons(widgets=self, notch=self.notch)
        self.bluetooth = BluetoothConnections(widgets=self)

        self.controls = ControlSliders()
        self.player = Player()
        self.metrics = Metrics()
        self.notification_history = NotificationHistory()
        self.network_connections = NetworkConnections(widgets=self)

        self.applet_stack = Stack(
            h_expand=True,
            v_expand=True,
            transition_type="slide-left-right",
            children=[
                self.notification_history,
                self.network_connections,
                self.bluetooth,
            ],
        )

        self.applet_stack_box = Box(
            name="applet-stack",
            h_expand=True,
            v_expand=True,
            h_align="fill",
            children=[
                self.applet_stack,
            ],
        )

        self.children_1 = [
            Box(
                name="container-sub-1",
                h_expand=True,
                v_expand=True,
                spacing=8,
                children=[
                    self.calendar,
                    self.applet_stack_box,
                ],
            ),
            self.metrics,
        ]

        self.container_1 = Box(
            name="container-1",
            h_expand=True,
            v_expand=True,
            spacing=8,
            children=self.children_1,
        )

        self.container_2 = Box(
            name="container-2",
            h_expand=True,
            v_expand=True,
            orientation="v",
            spacing=8,
            children=[
                self.buttons,
                self.controls,
                self.container_1,
            ],
        )

        self.children_3 = [
            self.player,
            self.container_2,
        ]
        self.container_3 = Box(
            name="container-3",
            h_expand=True,
            v_expand=True,
            spacing=8,
            children=self.children_3,
        )

        self.add(self.container_3)

    def show_bt(self):
        self.notch.open_notch("bluetooth")

    def show_notif(self):
        self.applet_stack.set_visible_child(self.notification_history)

    def show_network_applet(self):
        self.notch.open_notch("network_applet")