from fabric.hyprland.widgets import (
    HyprlandLanguage as Language,
    get_hyprland_connection,
)
from fabric.widgets.box import Box
from fabric.widgets.label import Label
from fabric.widgets.button import Button
from fabric.widgets.datetime import DateTime
from fabric.widgets.revealer import Revealer
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.eventbox import EventBox

from gi.repository import Gdk

from modules.Notch.MainWindow.Dashboard.buttons import AutolayoutButton
from modules.Bar.metrics import Battery, MetricsSmall
from modules.Bar.systemtray import SystemTray
from modules.Bar.workspaces import TopWorkspaces, SideBarWindow, _hov

from services.wayland import WaylandWindow as Window
import services.icons as icons


def _hand_cursor(widget):
    widget.add_events(
        Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
    )
    _cursors = [None, None]

    def _ensure(w):
        if _cursors[0] is None:
            d = w.get_display()
            _cursors[0] = Gdk.Cursor.new_from_name(d, "pointer")
            _cursors[1] = Gdk.Cursor.new_from_name(d, "default")

    def _set(w, idx):
        _ensure(w)
        top = w.get_toplevel()
        if top and top.get_window():
            top.get_window().set_cursor(_cursors[idx])

    widget.connect("enter-notify-event",
                   lambda w, e: (e.detail != Gdk.NotifyType.INFERIOR and _set(w, 0)) or False)
    widget.connect("leave-notify-event",
                   lambda w, e: (e.detail != Gdk.NotifyType.INFERIOR and _set(w, 1)) or False)
    widget.connect("clicked", lambda w, *_: _set(w, 1))


class Bar(Window):
    __slots__ = (
        "mid", "notch", "lang", "conn", "ws", "wsc",
        "tray", "rl", "met", "rr", "ll", "dt", "bat", "bp",
        "sidebar"
    )

    def __init__(self, monitor_id=0, **kwargs):
        super().__init__(exclusivity="auto", monitor_id=monitor_id)
        self.mid = monitor_id
        self.notch = kwargs.get("notch")
        self.anchor = "left top right"
        self.margin = "-4px -4px -8px -4px"

        self.lang = Language()
        self.conn = get_hyprland_connection()

        self.sidebar = SideBarWindow(conn=self.conn, monitor_id=self.mid)
        self.sidebar.show_all()

        self._build()
        self.lang.connect("notify::label", self._lchg)
        self._lchg()

    def _build(self):
        self.ws = TopWorkspaces(conn=self.conn, v_align="center", h_align="start")

        self.tray = SystemTray()
        self.met = MetricsSmall()

        self.rl = Revealer(
            name="bar-revealer", transition_type="slide-right", child_revealed=True,
            child=Box(name="bar-revealer-box", spacing=4, children=[self.tray]),
        )

        self.rr = Revealer(
            name="bar-revealer", transition_type="slide-left", child_revealed=True,
            child=Box(name="bar-revealer-box", spacing=4, children=[self.met]),
        )

        self.dt = DateTime(name="date-time", formatters=["%H:%M"])
        self.bat = Battery()

        self.bp = Button(
            name="button-bar", tooltip_markup="<b>Power menu</b>",
            on_clicked=self._pwr, child=Label(name="button-bar-label", markup=icons.shutdown),
        )
        _hov(self.bp)
        _hand_cursor(self.bp)

        self.ll = Label(name="lang-label")

        self.autolayout_btn = AutolayoutButton()

        self.lang_revealer = Revealer(
            name="lang-revealer",
            transition_type="slide-right",
            child_revealed=False,
            child=self.autolayout_btn
        )

        self.lang_box = Box(
            name="language-indicator",
            spacing=4,
            children=[self.lang_revealer, self.ll]
        )

        self.lang_eb = EventBox(child=self.lang_box)

        self.lang_eb.connect("enter-notify-event", lambda *_: self.lang_revealer.set_reveal_child(True))

        def _on_lang_leave(widget, event):
            if event.detail != Gdk.NotifyType.INFERIOR:
                self.lang_revealer.set_reveal_child(False)
            return False

        self.lang_eb.connect("leave-notify-event", _on_lang_leave)

        self.add(CenterBox(
            name="bar-inner",
            start_children=Box(
                name="start-container", spacing=4,
                children=[self.ws, Box(name="boxed-revealer", children=[self.rl])]
            ),
            end_children=Box(
                name="end-container", spacing=4,
                children=[
                    Box(name="boxed-revealer", children=[self.rr]),
                    Box(name="power-battery-container", children=[self.dt, self.lang_eb, self.bat, self.bp])
                ]
            ),
        ))

    def _lchg(self, *_):
        if l := self.lang.get_label():
            self.ll.set_label(l[:3].upper())

    def _pwr(self, *_):
        if self.notch:
            self.notch.open_notch("power")

    def cleanup(self):
        if self.sidebar:
            self.sidebar.destroy()
            self.sidebar = None
        if hasattr(self.tray, 'cleanup'): self.tray.cleanup()
        if hasattr(self.bat, 'cleanup'): self.bat.cleanup()
        self.notch = self.conn = self.lang = None