import gi
import weakref
gi.require_version("Gtk", "3.0")

from gi.repository import Gtk
from fabric.widgets.box import Box
from fabric.widgets.stack import Stack

from modules.Notch.MainWindow.Dashboard.calendar import Calendar
from modules.Notch.MainWindow.Dashboard.time import TimeWidget
from modules.Notch.MainWindow.Dashboard.network import NetworkConnections
from modules.Notch.MainWindow.Dashboard.bluetooth import BluetoothConnections
from modules.Notch.MainWindow.Dashboard.buttons import Buttons
from modules.Notch.MainWindow.Dashboard.controls import ControlSliders
from modules.Bar.metrics import Metrics
from modules.Notch.Notifications.history import get_shared_history


class Dashboard(Box):
    __slots__ = (
        'notch', 'time_widget', 'calendar', 'buttons', 'bluetooth',
        'controls', 'metrics', 'notification_history',
        'network_connections', 'applet_stack', '_size_group'
    )

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

        weak_self = weakref.proxy(self)

        self.time_widget = TimeWidget()
        self.calendar = Calendar(view_mode="month")

        self._size_group = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        self._size_group.add_widget(self.time_widget)
        self._size_group.add_widget(self.calendar)

        self.buttons = Buttons(widgets=weak_self)
        self.bluetooth = BluetoothConnections(widgets=weak_self)
        self.controls = ControlSliders()
        self.metrics = Metrics()
        self.notification_history = get_shared_history()
        self.network_connections = NetworkConnections(widgets=weak_self)

        self.applet_stack = Stack(
            transition_type="slide-left-right",
            children=(self.notification_history, self.network_connections, self.bluetooth),
            h_expand=True,
            v_expand=True
        )

        self.add(Box(
            name="container-2", orientation="v", spacing=8, h_expand=True, v_expand=True,
            children=(
                Box(
                    name="top-row", orientation="h", spacing=8, h_expand=True, v_expand=False, v_align="start",
                    children=(
                        self.time_widget,
                        Box(
                            name="buttons-controls-col", orientation="v", spacing=8, h_expand=True, v_expand=True, v_align="fill",
                            children=(
                                Box(v_align="start", v_expand=False, h_expand=True, children=(self.buttons,)),
                                Box(orientation="v", v_expand=True, v_align="center", h_expand=True, children=(self.controls,))
                            )
                        )
                    )
                ),
                Box(
                    name="container-1", orientation="h", spacing=8, h_expand=True, v_expand=True,
                    children=(
                        Box(
                            name="container-sub-1", spacing=8, h_expand=True, v_expand=True,
                            children=(
                                self.calendar,
                                Box(name="applet-stack", h_align="fill", h_expand=True, v_expand=True, children=(self.applet_stack,))
                            )
                        ),
                        self.metrics
                    )
                )
            )
        ))

    def show_bt(self):
        self.applet_stack.set_visible_child(self.bluetooth)

    def show_notif(self):
        self.applet_stack.set_visible_child(self.notification_history)

    def show_network_applet(self):
        if self.notch:
            self.notch.open_notch("network_applet")

    def cleanup(self):
        for w in (self.controls, self.bluetooth, self.network_connections, self.time_widget):
            try:
                w.cleanup()
            except AttributeError:
                pass

        self.notch = None
        self.time_widget = None
        self.calendar = None
        self.buttons = None
        self.bluetooth = None
        self.controls = None
        self.metrics = None
        self.notification_history = None
        self.network_connections = None
        self.applet_stack = None
        self._size_group = None