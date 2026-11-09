import weakref

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import GLib, Gtk, GtkLayerShell

from fabric.hyprland.widgets import HyprlandLanguage, get_hyprland_connection
from fabric.widgets.box import Box
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.eventbox import EventBox
from fabric.widgets.label import Label

from modules.Bar.quickPanel import QuickPanel
from modules.Bar.workspaces import SideBarWindow, AxisWindow

from services.corners import MyCorner
from services.wayland import WaylandWindow as Window


_LANG_MS = 350
_DIGIT_MS = 350


class _Digit(Gtk.Stack):
    def __init__(self):
        super().__init__(
            transition_type=Gtk.StackTransitionType.NONE,
            transition_duration=_DIGIT_MS,
            homogeneous=True,
        )
        self._lbls = (Label(v_align="center"), Label(v_align="center"))
        self._idx = 0
        for lbl in self._lbls:
            self.add(lbl)
        self.show_all()

    def set_char(self, ch: str, transition: Gtk.StackTransitionType) -> None:
        self._idx ^= 1
        lbl = self._lbls[self._idx]
        lbl.set_label(ch)
        self.set_transition_type(transition)
        self.set_visible_child(lbl)


class _Clock(Box):
    def __init__(self):
        super().__init__(name="date-time", spacing=0, v_align="center")
        self.set_size_request(46, -1)

        self._digits = tuple(_Digit() for _ in range(4))
        d = self._digits
        self.add(Box(
            h_align="center", h_expand=True, v_align="center", spacing=0,
            children=[d[0], d[1], Label(label=":", v_align="center"), d[2], d[3]],
        ))

        self._shown = ""
        self._tid = None

        self._refresh()
        self.connect("map", lambda _: self._sync() if self._tid is None else None)
        self.connect("unmap", lambda _: self._cancel())
        self.connect("destroy", lambda _: self._cancel())
        self.show_all()

    def _refresh(self) -> int:
        now = GLib.DateTime.new_now_local()
        text = f"{now.get_hour():02d}{now.get_minute():02d}"
        old = self._shown
        if text != old:
            self._shown = text
            for i, ch in enumerate(text):
                if old and ch == old[i]:
                    continue
                if not old:
                    kind = Gtk.StackTransitionType.NONE
                elif ch > old[i]:
                    kind = Gtk.StackTransitionType.SLIDE_DOWN
                else:
                    kind = Gtk.StackTransitionType.SLIDE_UP
                self._digits[i].set_char(ch, kind)
        return now.get_second()

    def _sync(self) -> None:
        sec = self._refresh()
        self._tid = GLib.timeout_add_seconds(
            60 - sec, self._on_timer, priority=GLib.PRIORITY_DEFAULT_IDLE
        )

    def _on_timer(self) -> bool:
        self._tid = None
        self._sync()
        return False

    def _cancel(self) -> None:
        if self._tid is not None:
            GLib.source_remove(self._tid)
            self._tid = None


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
        self._lang_idx = 0

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
        self.dt = _Clock()

        self._lang_lbls = tuple(
            Label(name="lang-label", xalign=0.5, v_align="center") for _ in range(2)
        )
        self._lang_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT,
            transition_duration=_LANG_MS,
            homogeneous=True,
        )
        self._lang_stack.set_size_request(32, -1)
        for lbl in self._lang_lbls:
            self._lang_stack.add(lbl)
        self._lang_stack.show_all()

        self.lang_eb = EventBox(
            v_align="center",
            child=Box(name="HyprlandLanguage-indicator", v_align="center", children=[self._lang_stack]),
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
        if short == self._last_lang:
            return
        self._last_lang = short
        self._lang_idx ^= 1
        lbl = self._lang_lbls[self._lang_idx]
        lbl.set_label(short)
        self._lang_stack.set_visible_child(lbl)

    def cleanup(self):
        if self.lang is None:
            return
        self.lang.disconnect(self._lang_id)
        self.lang = self.conn = self._notch = None
        self.quick_panel.cleanup()
        for w in (self.top_window, self.sidebar, self.quick_panel):
            w.destroy()