import weakref

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

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk

from modules.Notch.MainWindow.Dashboard.buttons import AutolayoutButton
from modules.Bar.powerMenu import PowerMenu
from modules.Bar.metrics import Battery, MetricsSmall
from modules.Bar.systemtray import SystemTray
from modules.Bar.workspaces import TopWorkspaces, SideBarWindow, _hov

from services.wayland import WaylandWindow as Window
import services.icons as icons


class Bar(Window):
    def __init__(self, monitor_id=0, **kwargs):
        super().__init__(exclusivity="auto", monitor_id=monitor_id)
        self.mid = monitor_id

        notch = kwargs.get("notch")
        self._notch_ref = weakref.ref(notch) if notch else None

        self.anchor = "left top right"
        self.margin = "-4px -4px -8px -4px"

        self.conn = get_hyprland_connection()
        self.lang = Language()
        self._last_lang = ""
        self._hand = None
        self._default = None

        self.sidebar = SideBarWindow(conn=self.conn, monitor_id=self.mid)
        self.sidebar.show_all()

        self.power_menu = PowerMenu(monitor=self.mid)

        self._build()
        self._lang_sig_id = self.lang.connect("notify::label", self._lchg)
        self._lchg()

    @property
    def notch(self):
        return self._notch_ref() if self._notch_ref else None

    @notch.setter
    def notch(self, value):
        self._notch_ref = weakref.ref(value) if value else None

    def _ensure_cursors(self):
        if self._hand is None:
            display = self.get_display()
            self._hand = Gdk.Cursor.new_from_name(display, "pointer")
            self._default = Gdk.Cursor.new_from_name(display, "default")

    def _set_toplevel_cursor(self, cursor):
        toplevel = self.get_toplevel()
        if toplevel:
            win = toplevel.get_window()
            if win:
                win.set_cursor(cursor)

    def _on_btn_enter(self, w, event):
        if event.detail != Gdk.NotifyType.INFERIOR:
            self._ensure_cursors()
            self._set_toplevel_cursor(self._hand)
        return False

    def _on_btn_leave(self, w, event):
        if event.detail != Gdk.NotifyType.INFERIOR:
            self._ensure_cursors()
            self._set_toplevel_cursor(self._default)
        return False

    def _hand_cursor(self, widget):
        widget.add_events(
            Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        widget.connect("enter-notify-event", self._on_btn_enter)
        widget.connect("leave-notify-event", self._on_btn_leave)

    def _build(self):
        self.ws = TopWorkspaces(conn=self.conn, v_align="center", h_align="start")
        self.tray = SystemTray()
        self.met = MetricsSmall()

        self.rl = Revealer(
            name="bar-revealer",
            transition_type="slide-right",
            child_revealed=True,
            child=Box(name="bar-revealer-box", spacing=4, children=[self.tray]),
        )

        self.rr = Revealer(
            name="bar-revealer",
            transition_type="slide-left",
            child_revealed=True,
            child=Box(name="bar-revealer-box", spacing=4, children=[self.met]),
        )

        self.dt = DateTime(name="date-time", formatters=["%H:%M"])
        self.bat = Battery()

        self.bp = Button(
            name="button-bar",
            tooltip_markup="<b>Power menu</b>",
            on_clicked=self._pwr,
            child=Label(name="button-bar-label", markup=icons.shutdown),
        )
        _hov(self.bp)
        self._hand_cursor(self.bp)
        self.power_menu.set_trigger_button(self.bp)

        self.ll = Label(name="lang-label")
        self.autolayout_btn = AutolayoutButton()

        self.lang_revealer = Revealer(
            name="lang-revealer",
            transition_type="slide-right",
            child_revealed=False,
            child=self.autolayout_btn,
        )

        self.lang_eb = EventBox(
            child=Box(
                name="language-indicator",
                spacing=4,
                children=[self.lang_revealer, self.ll],
            )
        )

        self.lang_eb.connect("enter-notify-event", self._lang_enter)
        self.lang_eb.connect("leave-notify-event", self._lang_leave)

        self.add(
            CenterBox(
                name="bar-inner",
                start_children=Box(
                    name="start-container",
                    spacing=4,
                    children=[
                        self.ws,
                        Box(name="boxed-revealer", children=[self.rl]),
                    ],
                ),
                end_children=Box(
                    name="end-container",
                    spacing=4,
                    children=[
                        Box(name="boxed-revealer", children=[self.rr]),
                        Box(
                            name="power-battery-container",
                            children=[self.dt, self.lang_eb, self.bat, self.bp],
                        ),
                    ],
                ),
            )
        )

    def _lang_enter(self, w, event):
        if event.detail != Gdk.NotifyType.INFERIOR:
            self.lang_revealer.set_reveal_child(True)
        return False

    def _lang_leave(self, w, event):
        if event.detail != Gdk.NotifyType.INFERIOR:
            self.lang_revealer.set_reveal_child(False)
        return False

    def _lchg(self, *_):
        raw = self.lang.get_label()
        if not raw:
            return
        short = raw[:3].upper()
        if short != self._last_lang:
            self._last_lang = short
            self.ll.set_label(short)

    def _pwr(self, *_):
        pm = self.power_menu
        if pm:
            if pm.is_open():
                pm.close()
            else:
                pm.open()

    def cleanup(self):
        if self.lang and self._lang_sig_id:
            self.lang.disconnect(self._lang_sig_id)
            self._lang_sig_id = 0

        if self.sidebar:
            self.sidebar.destroy()
            self.sidebar = None

        if self.power_menu:
            self.power_menu.cleanup()
            self.power_menu.destroy()
            self.power_menu = None

        if hasattr(self.tray, "cleanup"):
            self.tray.cleanup()
        if hasattr(self.bat, "cleanup"):
            self.bat.cleanup()

        self._notch_ref = None
        self.conn = None
        self.lang = None