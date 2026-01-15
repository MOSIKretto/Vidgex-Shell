from fabric.hyprland.widgets import (
    HyprlandLanguage as Language,
    HyprlandWorkspaces as Workspaces,
    get_hyprland_connection
)
from fabric.widgets.box import Box
from fabric.widgets.label import Label
from fabric.widgets.button import Button
from fabric.widgets.datetime import DateTime
from fabric.widgets.revealer import Revealer
from fabric.widgets.centerbox import CenterBox
from gi.repository import Gdk
import weakref


from modules.metrics import Battery, MetricsSmall, NetworkApplet
from modules.systemtray import SystemTray
from modules.controls import ControlSmall

from widgets.wayland import WaylandWindow as Window

import modules.icons as icons


class Bar(Window):
    def __init__(self, monitor_id=0, **kwargs):
        super().__init__(exclusivity="auto")
        self.monitor_id = monitor_id
        
        self._set_anchor_fast("left top right")
        self.set_margin("-4px -4px -8px -4px")
        
        self._notch_ref = weakref.ref(kwargs["notch"]) if "notch" in kwargs else None
        self._signal_handlers = []
        
        self._build_ui()
        self._setup_hyprland_signals()
    
    def _build_ui(self):
        self.workspaces = Workspaces(name="workspaces", spacing=8)
        ws_container = Box(children=[self.workspaces])
        
        self.systray = SystemTray()
        self.network = NetworkApplet()
        network_container = Box(name="network-container", children=[self.network])
        
        left_widgets = Box(
            name="bar-revealer-box",
            orientation="h",
            spacing=4,
            children=[self.systray, network_container]
        )
        
        self.revealer_left = Revealer(
            name="bar-revealer",
            transition_type="slide-right",
            child_revealed=True,
            child=left_widgets
        )
        boxed_revealer_left = Box(name="boxed-revealer", children=[self.revealer_left])
        
        self.metrics = MetricsSmall()
        self.control = ControlSmall()
        
        right_widgets = Box(
            name="bar-revealer-box",
            orientation="h",
            spacing=4,
            children=[self.metrics, self.control]
        )
        
        self.revealer_right = Revealer(
            name="bar-revealer",
            transition_type="slide-left",
            child_revealed=True,
            child=right_widgets
        )
        boxed_revealer_right = Box(name="boxed-revealer", children=[self.revealer_right])
        
        self.lang_label = Label(name="lang-label")
        language_indicator = Box(name="language-indicator", children=[self.lang_label])
        
        self.date_time = DateTime(name="date-time", formatters=["%H:%M"])
        self.battery = Battery()
        
        self.button_power = Button(
            name="button-bar",
            tooltip_markup="<b>Меню питания</b>",
            on_clicked=self.power_menu,
            child=Label(name="button-bar-label", markup=icons.shutdown)
        )
        self._setup_cursor_hover(self.button_power)
        
        power_battery_container = Box(
            name="power-battery-container",
            children=[self.date_time, language_indicator, self.battery, self.button_power]
        )
        
        self.add(CenterBox(
            name="bar-inner",
            start_children=Box(
                name="start-container",
                spacing=4,
                children=[ws_container, boxed_revealer_left]
            ),
            end_children=Box(
                name="end-container",
                spacing=4,
                children=[boxed_revealer_right, power_battery_container]
            )
        ))
    
    def _setup_hyprland_signals(self):
        conn = get_hyprland_connection()
        if conn:
            handler = conn.connect("event::activelayout", self._update_language)
            self._signal_handlers.append((conn, handler))
        self._update_language()
    
    def _setup_cursor_hover(self, button):
        def on_enter(_, event):
            button.get_window().set_cursor(
                Gdk.Cursor.new_from_name(button.get_display(), "hand2")
            )
        
        def on_leave(_, event):
            button.get_window().set_cursor(None)
        
        button.connect("enter-notify-event", on_enter)
        button.connect("leave-notify-event", on_leave)
    
    def power_menu(self, *args):
        if self.notch:
            self.notch.open_notch("power")
    
    def _update_language(self, *args):
        if args and len(args) > 1 and args[1]:
            lang_data = getattr(args[1], 'data', [''])[1] if hasattr(args[1], 'data') else Language().get_label()
        else:
            lang_data = Language().get_label()
        
        self.lang_label.set_label(lang_data[:3].upper() if len(lang_data) >= 3 else lang_data.upper())
    
    def destroy(self):
        if hasattr(self, '_signal_handlers') and self._signal_handlers:
            for conn, handler_id in list(self._signal_handlers):
                conn.disconnect(handler_id)
            self._signal_handlers.clear()
        
        self._notch_ref = None
        
        components = ['systray', 'network', 'control', 'metrics', 'battery', 'date_time', 
                     'workspaces', 'button_power', 'lang_label', 'revealer_left', 'revealer_right']
        for name in components:
            comp = getattr(self, name, None)
            if comp:
                if hasattr(comp, 'cleanup'):
                    comp.cleanup()
                elif hasattr(comp, 'destroy'):
                    comp.destroy()
                setattr(self, name, None)
        
        super().destroy()