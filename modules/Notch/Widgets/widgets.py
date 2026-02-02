import gi
gi.require_version("Gtk", "3.0")

from fabric.widgets.box import Box
from fabric.widgets.stack import Stack

from modules.Notch.Widgets.player import Player
from modules.Notch.Widgets.calendar import Calendar
from modules.Notch.Widgets.network import NetworkConnections
from modules.Notch.Widgets.bluetooth import BluetoothConnections
from modules.Notch.Widgets.buttons import Buttons

from modules.Notch.Widgets.controls import ControlSliders
from modules.Bar.metrics import Metrics

from modules.Notifications.history import NotificationHistory


class Widgets(Box):
    __slots__ = ('notch', 'calendar', 'buttons', 'bluetooth', 'controls',
                 'player', 'metrics', 'notification_history', 'network_connections',
                 'applet_stack')

    _D = {'h_expand': True, 'v_expand': True}

    def __init__(self, notch=None, **kwargs):
        super().__init__(name="dash-widgets", h_align="fill", v_align="fill",
                         visible=True, all_visible=True, **self._D)

        self.notch = notch

        self.calendar = Calendar(view_mode="month")
        self.buttons = Buttons(widgets=self)
        self.bluetooth = BluetoothConnections(widgets=self)
        self.controls = ControlSliders()
        self.player = Player()
        self.metrics = Metrics()
        self.notification_history = NotificationHistory()
        self.network_connections = NetworkConnections(widgets=self)

        self.applet_stack = Stack(
            transition_type="slide-left-right",
            children=[self.notification_history, self.network_connections, self.bluetooth],
            **self._D,
        )

        self._build()

    def _build(self):
        d, mkbox = self._D, lambda **kw: Box(**{**d, **kw})

        applet_box = mkbox(name="applet-stack", h_align="fill", children=[self.applet_stack])

        sub = mkbox(name="container-sub-1", spacing=8, children=[self.calendar, applet_box])

        c1 = mkbox(name="container-1", orientation="h", spacing=8, children=[sub, self.metrics])

        c2 = mkbox(name="container-2", orientation="v", spacing=8,
                   children=[self.buttons, self.controls, c1])

        self.add(mkbox(name="container-3", orientation="h", spacing=8,
                       children=[self.player, c2]))

    def show_bt(self):
        self.applet_stack.set_visible_child(self.bluetooth)

    def show_notif(self):
        self.applet_stack.set_visible_child(self.notification_history)

    def show_network_applet(self):
        if self.notch:
            self.notch.open_notch("network_applet")

    def cleanup(self):
        for w in (self.player, self.controls, self.bluetooth, self.network_connections):
            if hasattr(w, 'cleanup'):
                w.cleanup()
        self.notch = None