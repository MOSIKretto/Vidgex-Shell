import gi
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
from modules.Notch.MainWindow.Dashboard.systemtray import SystemTray
from modules.Notch.MainWindow.Dashboard.metrics import Metrics
from modules.Notch.Notifications.history import get_shared_history


class Dashboard(Box):
    def __init__(self, notch=None, **kwargs):
        super().__init__(
            name="dash-widgets",
            h_align="fill",
            v_align="fill",
            h_expand=True,
            v_expand=True,
            visible=True,
            **kwargs,
        )

        self.notch = notch

        self.time_widget = TimeWidget()
        self.calendar = Calendar(view_mode="month")

        self._size_group_left = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        self._size_group_left.add_widget(self.time_widget)
        self._size_group_left.add_widget(self.calendar)

        self.buttons = Buttons(widgets=self)
        self.bluetooth = BluetoothConnections(widgets=self)
        self.network_connections = NetworkConnections(widgets=self)

        self.controls = ControlSliders()
        self.metrics = Metrics()
        self.systray = SystemTray(pixel_size=20)
        self.notification_history = get_shared_history()

        self.applet_stack = Stack(
            transition_type="slide-left-right",
            children=(
                self.notification_history,
                self.network_connections,
                self.bluetooth,
            ),
            h_expand=True,
            v_expand=True,
        )

        self._size_group_right = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        self._size_group_right.add_widget(self.metrics)
        self._size_group_right.add_widget(self.systray)

        metrics_tray_col = Box(
            name="metrics-tray-col",
            orientation="v",
            spacing=8,
            h_expand=False,
            v_expand=True,
            v_align="fill",
        )
        metrics_tray_col.pack_start(self.metrics, True, True, 0)
        metrics_tray_col.pack_end(self.systray, False, False, 0)

        self._top_row = Box(
            name="top-row",
            orientation="h",
            spacing=8,
            h_expand=True,
            v_expand=False,
            v_align="start",
            children=(
                self.time_widget,
                Box(
                    name="buttons-controls-col",
                    orientation="v",
                    spacing=8,
                    h_expand=True,
                    v_expand=True,
                    v_align="fill",
                    children=(
                        Box(
                            v_align="start",
                            v_expand=False,
                            h_expand=True,
                            children=(self.buttons,),
                        ),
                        Box(
                            orientation="v",
                            v_expand=True,
                            v_align="center",
                            h_expand=True,
                            children=(self.controls,),
                        ),
                    ),
                ),
            ),
        )

        self._bottom_row = Box(
            name="container-1",
            orientation="h",
            spacing=8,
            h_expand=True,
            v_expand=True,
            children=(
                Box(
                    name="container-sub-1",
                    spacing=8,
                    h_expand=True,
                    v_expand=True,
                    children=(
                        self.calendar,
                        Box(
                            name="applet-stack",
                            h_align="fill",
                            h_expand=True,
                            v_expand=True,
                            children=(self.applet_stack,),
                        ),
                    ),
                ),
                metrics_tray_col,
            ),
        )

        self._root_container = Box(
            name="container-2",
            orientation="v",
            spacing=8,
            h_expand=True,
            v_expand=True,
            children=(self._top_row, self._bottom_row),
        )

        self.add(self._root_container)

    def show_notif(self):
        self.applet_stack.set_visible_child(self.notification_history)

    def show_bt(self):
        self.applet_stack.set_visible_child(self.bluetooth)

    def show_network_applet(self):
        self.applet_stack.set_visible_child(self.network_connections)


    def cleanup(self):
        for widget in (
            self.controls,
            self.bluetooth,
            self.network_connections,
            self.time_widget,
            self.systray,
        ):
            if widget is None:
                continue
            cleanup_fn = getattr(widget, "cleanup", None)
            if callable(cleanup_fn):
                cleanup_fn()

        if self._root_container is not None:
            self.remove(self._root_container)
            self._root_container.destroy()

        self.notch = None
        self.time_widget = None
        self.calendar = None
        self.buttons = None
        self.bluetooth = None
        self.controls = None
        self.metrics = None
        self.systray = None
        self.notification_history = None
        self.network_connections = None
        self.applet_stack = None
        self._size_group_left = None
        self._size_group_right = None
        self._top_row = None
        self._bottom_row = None
        self._root_container = None