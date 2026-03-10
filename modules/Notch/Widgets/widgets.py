import gi
gi.require_version("Gtk", "3.0")

from fabric.widgets.box import Box
from fabric.widgets.stack import Stack

from modules.Notch.Widgets.calendar import Calendar
from modules.Notch.Widgets.network import NetworkConnections
from modules.Notch.Widgets.bluetooth import BluetoothConnections
from modules.Notch.Widgets.buttons import Buttons
from modules.Notch.Widgets.controls import ControlSliders
from modules.Bar.metrics import Metrics
from modules.Notifications.history import NotificationHistory


class Widgets(Box):
    __slots__ = ('notch', 'calendar', 'buttons', 'bluetooth', 'controls',
                 'metrics', 'notification_history', 'network_connections',
                 'applet_stack')

    def __init__(self, notch=None, **kwargs):
        super().__init__(
            name="dash-widgets",
            h_align="fill",
            v_align="fill",
            h_expand=True,
            v_expand=True,
            visible=True,
            **kwargs
        )

        self.notch = notch

        self.calendar = Calendar(view_mode="month")
        self.buttons = Buttons(widgets=self)
        self.bluetooth = BluetoothConnections(widgets=self)
        self.controls = ControlSliders()
        self.metrics = Metrics()
        self.notification_history = NotificationHistory()
        self.network_connections = NetworkConnections(widgets=self)

        self.applet_stack = Stack(
            transition_type="slide-left-right",
            children=(self.notification_history, self.network_connections, self.bluetooth),
            h_expand=True,
            v_expand=True
        )

        self._build()

    def _build(self):
        applet_box = Box(
            name="applet-stack", h_align="fill", h_expand=True, v_expand=True,
            children=(self.applet_stack,)
        )

        sub = Box(
            name="container-sub-1", spacing=8, h_expand=True, v_expand=True,
            children=(self.calendar, applet_box)
        )

        c1 = Box(
            name="container-1", orientation="h", spacing=8, h_expand=True, v_expand=True,
            children=(sub, self.metrics)
        )

        self.add(Box(
            name="container-2", orientation="v", spacing=8, h_expand=True, v_expand=True,
            children=(self.buttons, self.controls, c1)
        ))

    def show_bt(self):
        self.applet_stack.set_visible_child(self.bluetooth)

    def show_notif(self):
        self.applet_stack.set_visible_child(self.notification_history)

    def show_network_applet(self):
        if self.notch:
            self.notch.open_notch("network_applet")

    def cleanup(self):
        for w in (self.controls, self.bluetooth, self.network_connections):
            if (c := getattr(w, 'cleanup', None)):
                c()
        self.notch = None