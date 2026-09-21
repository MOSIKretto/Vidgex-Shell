import weakref

from fabric.hyprland.widgets import (
    HyprlandLanguage as Language,
    get_hyprland_connection,
)
from fabric.widgets.box import Box
from fabric.widgets.label import Label
from fabric.widgets.button import Button
from fabric.widgets.datetime import DateTime
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.eventbox import EventBox

from modules.Bar.powerMenu import PowerMenu
from modules.Bar.toolBox import ToolBox
from modules.Bar.workspaces import TopWorkspaces, SideBarWindow
from modules.Bar.battery import Battery

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

        self.sidebar = SideBarWindow(conn=self.conn, monitor_id=self.mid)
        self.sidebar.show_all()

        self.power_menu = PowerMenu(monitor=self.mid)
        self.toolbox_menu = ToolBox(monitor=self.mid)

        self._build()
        self._lang_sig_id = self.lang.connect("notify::label", self._lchg)
        self._lchg()

    @property
    def notch(self):
        return self._notch_ref() if self._notch_ref else None

    @notch.setter
    def notch(self, value):
        self._notch_ref = weakref.ref(value) if value else None

    def _build(self):
        self.ws = TopWorkspaces(conn=self.conn, v_align="center", h_align="start")

        self.dt  = DateTime(name="date-time", formatters=["%H:%M"])
        self.bat = Battery()

        self.bt = Button(
            name="button-bar",
            tooltip_markup="<b>Tools</b>",
            on_clicked=self._tools,
            child=Label(name="button-bar-label", markup=icons.photo),
        )
        self.toolbox_menu.set_trigger_button(self.bt)

        self.bp = Button(
            name="button-bar",
            tooltip_markup="<b>Power menu</b>",
            on_clicked=self._pwr,
            child=Label(name="button-bar-label", markup=icons.shutdown),
        )
        self.power_menu.set_trigger_button(self.bp)

        self.ll = Label(name="lang-label", xalign=0.5)

        self.lang_eb = EventBox(
            child=Box(
                name="language-indicator",
                spacing=0,
                children=[self.ll],
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
                    children=[self.ws],
                ),
                end_children=Box(
                    name="end-container",
                    spacing=4,
                    children=[
                        Box(
                            name="power-battery-container",
                            children=[self.dt, self.lang_eb, self.bat, self.bt, self.bp],
                        ),
                    ],
                ),
            )
        )

    def _lang_enter(self, w, event):
        return False

    def _lang_leave(self, w, event):
        return False

    def _lchg(self, *_):
        raw = self.lang.get_label()
        short = raw[:2].upper()
        if short != self._last_lang:
            self._last_lang = short
            self.ll.set_label(short)

    def _pwr(self, *_):
        pm = self.power_menu
        if pm.is_open():
            pm.close()
        else:
            pm.open()

    def _tools(self, *_):
        tm = self.toolbox_menu
        if tm.is_open():
            tm.close()
        else:
            tm.open()

    def cleanup(self):
        self.lang.disconnect(self._lang_sig_id)
        self._lang_sig_id = 0

        self.sidebar.destroy()
        self.sidebar = None

        self.power_menu.cleanup()
        self.power_menu.destroy()
        self.power_menu = None

        self.toolbox_menu.cleanup()
        self.toolbox_menu.destroy()
        self.toolbox_menu = None

        self.bat.cleanup()

        self._notch_ref = None
        self.conn = None
        self.lang = None