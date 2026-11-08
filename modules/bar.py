import weakref

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import GtkLayerShell

from fabric.hyprland.widgets import HyprlandLanguage, get_hyprland_connection
from fabric.widgets.box import Box
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.datetime import DateTime
from fabric.widgets.eventbox import EventBox
from fabric.widgets.label import Label

from modules.Bar.quickPanel import QuickPanel
from modules.Bar.workspaces import SideBarWindow, AxisWindow

from services.corners import MyCorner
from services.wayland import WaylandWindow as Window


class Bar(Window):
    def __init__(self, monitor_id=0, notch=None):
        super().__init__(exclusivity=36, monitor_id=monitor_id)
        self.mid = monitor_id
        self.notch = notch

        if GtkLayerShell.is_layer_window(self):
            GtkLayerShell.set_exclusive_zone(self, 36)
        self.anchor = "left top right"
        self.margin = "0px -8px 0px -8px"

        self.conn = get_hyprland_connection()
        self.lang = HyprlandLanguage()
        self._last_lang = ""

        self.sidebar = SideBarWindow(conn=self.conn, monitor_id=self.mid)
        self.sidebar.show_all()
        self.quick_panel = QuickPanel(monitor=self.mid)
        self.top_window = AxisWindow(conn=self.conn, monitor_id=self.mid)
        self.ws = self.top_window.ws

        self._build()

        self._lang_id = self.lang.connect("notify::label", self._lchg)
        self._lchg()
        self.connect("destroy", lambda _: self.cleanup())

    @property
    def notch(self):
        return self._notch() if self._notch else None

    @notch.setter
    def notch(self, value):
        self._notch = weakref.ref(value) if value else None

    def _build(self):
        self.dt = DateTime(name="date-time", formatters=["%H:%M"], v_align="center")
        self.dt.set_size_request(46, -1)

        self.ll = Label(name="lang-label", xalign=0.5, v_align="center")
        self.ll.set_size_request(32, -1)

        self.lang_eb = EventBox(
            v_align="center",
            child=Box(name="HyprlandLanguage-indicator", v_align="center", children=[self.ll]),
        )

        def corner(name):
            return Box(
                name=name, orientation="v", v_align="start", h_align="end",
                children=[MyCorner("top-right")],
            )

        power = Box(
            name="power-battery-container",
            spacing=4,
            v_align="fill",
            children=[self.dt, self.lang_eb, self.quick_panel.trigger_strip],
        )
        self.quick_panel.bind_width_to(power)

        self.nb = CenterBox(
            name="bar-inner",
            v_align="start",
            start_children=Box(
                name="bar-group-left",
                v_align="start",
                children=[
                    self.top_window.island,
                    self.top_window.cap,
                    self.top_window.corner_static,
                ],
            ),
            end_children=Box(
                name="bar-group-right",
                orientation="v",
                v_align="start",
                children=[
                    Box(
                        name="bar-group-right-top",
                        v_align="start",
                        children=[corner("bar-right-corner"), power],
                    ),
                    corner("bar-right-bottom-corner"),
                ],
            ),
        )
        self.add(self.nb)

    def _lchg(self, *_):
        short = self.lang.get_label()[:2].upper()
        if short != self._last_lang:
            self._last_lang = short
            self.ll.set_label(short)

    def cleanup(self):
        if self.lang is None:
            return
        self.lang.disconnect(self._lang_id)
        self.lang = self.conn = self._notch = None
        self.quick_panel.cleanup()
        for w in (self.top_window, self.sidebar, self.quick_panel):
            w.destroy()