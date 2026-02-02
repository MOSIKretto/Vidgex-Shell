from fabric.hyprland.widgets import (
    HyprlandLanguage as Language,
    HyprlandWorkspaces as Workspaces,
    WorkspaceButton,
    get_hyprland_connection,
)
from fabric.widgets.box import Box
from fabric.widgets.label import Label
from fabric.widgets.button import Button
from fabric.widgets.datetime import DateTime
from fabric.widgets.revealer import Revealer
from fabric.widgets.centerbox import CenterBox
from gi.repository import Gdk

from modules.Bar.metrics import Battery, MetricsSmall, NetworkApplet
from modules.Bar.systemtray import SystemTray
from services.wayland import WaylandWindow as Window
import services.icons as icons

from time import time

_CD = 0.2
_TH = 0.3
_SM = Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK
_CP = "dispatch workspace e-1"
_CN = "dispatch workspace e+1"
_WB = tuple(WorkspaceButton(h_expand=False, v_expand=False, h_align="center", v_align="center", id=i, label=None) for i in range(1, 10))


class Bar(Window):
    __slots__ = (
        "mid", "notch", "lang", "conn", "_lst", "_hc", "ws", "wsc",
        "tray", "net", "rl", "met", "rr", "ll", "dt", "bat", "bp",
    )

    def __init__(self, monitor_id=0, **kwargs):
        super().__init__(exclusivity="auto")

        self.mid = monitor_id
        self.notch = kwargs.get("notch")
        self.anchor = "left top right"
        self.margin = "-4px -4px -8px -4px"

        self.lang = Language()
        self.conn = get_hyprland_connection()
        self._lst = 0.0
        self._hc = None

        self._build()
        self.lang.connect("notify::label", self._lchg)
        self._lchg()

    def _build(self):
        self.ws = Workspaces(
            name="workspaces", 
            invert_scroll=True, 
            empty_scroll=True,
            v_align="fill", 
            orientation="h", 
            spacing=8, 
            buttons=list(_WB),
        )
        self.ws.add_events(_SM)
        self.ws.connect("scroll-event", self._scr)

        self.wsc = Box(name="workspaces-container", children=[self.ws])
        self.tray = SystemTray()
        self.net = NetworkApplet()
        self.met = MetricsSmall()

        self.rl = Revealer(
            name="bar-revealer", 
            transition_type="slide-right", 
            child_revealed=True,
            child=Box(
                name="bar-revealer-box", 
                spacing=4, 
                children=[
                    self.tray, 
                    Box(name="network-container", children=[self.net]),
                ]
            ),
        )

        self.rr = Revealer(
            name="bar-revealer", 
            transition_type="slide-left", 
            child_revealed=True,
            child=Box(
                name="bar-revealer-box", 
                spacing=4, 
                children=[self.met]
            ),
        )

        self.ll = Label(name="lang-label")
        self.dt = DateTime(name="date-time", formatters=["%H:%M"])
        self.bat = Battery()

        self.bp = Button(
            name="button-bar", 
            tooltip_markup="<b>Меню питания</b>",
            on_clicked=self._pwr, 
            child=Label(name="button-bar-label", markup=icons.shutdown),
        )
        self._hov(self.bp)

        self.add(CenterBox(
            name="bar-inner",
            start_children=Box(
                name="start-container", 
                spacing=4, 
                children=[
                    self.wsc, 
                    Box(name="boxed-revealer", children=[self.rl]),
                ]
            ),
            end_children=Box(
                name="end-container", 
                spacing=4, 
                children=[
                    Box(name="boxed-revealer", children=[self.rr]),
                    Box(
                        name="power-battery-container", 
                        children=[
                            self.dt, 
                            Box(name="language-indicator", children=[self.ll]), 
                            self.bat, 
                            self.bp,
                        ]
                    ),
                ]
            ),
        ))

    def _scr(self, _, e):
        now = time()
        if now - self._lst < _CD:
            return True

        d, cmd = e.direction, None

        if d == Gdk.ScrollDirection.UP:
            cmd = _CP
        elif d == Gdk.ScrollDirection.DOWN:
            cmd = _CN
        elif d == Gdk.ScrollDirection.SMOOTH:
            dy = e.get_scroll_deltas()[2]
            cmd = _CP if dy < -_TH else (_CN if dy > _TH else None)

        if cmd:
            self._lst = now
            self.conn.send_command(cmd)

        return True

    def _lchg(self, *_):
        if l := self.lang.get_label():
            self.ll.set_label(l[:3].upper())

    def _hov(self, w):
        def sc(_, __, h):
            if h and not self._hc:
                self._hc = Gdk.Cursor.new_from_name(w.get_display(), "hand2")
            if win := w.get_window():
                win.set_cursor(self._hc if h else None)

        w.connect("enter-notify-event", sc, True)
        w.connect("leave-notify-event", sc, False)

    def _pwr(self, *_):
        if self.notch:
            self.notch.open_notch("power")

    def cleanup(self):
        if hasattr(self.tray, 'cleanup'):
            self.tray.cleanup()
        if hasattr(self.net, 'cleanup'):
            self.net.cleanup()
        if hasattr(self.bat, 'cleanup'):
            self.bat.cleanup()
        self.notch = self.conn = self.lang = None