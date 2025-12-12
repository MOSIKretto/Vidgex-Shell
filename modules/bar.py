from fabric.hyprland.widgets import HyprlandLanguage as Language
from fabric.hyprland.widgets import HyprlandWorkspaces as Workspaces
from fabric.hyprland.widgets import WorkspaceButton
from fabric.hyprland.widgets import get_hyprland_connection

from gi.repository import Gdk, Gtk

from fabric.widgets.box import Box
from fabric.widgets.label import Label
from fabric.widgets.button import Button
from fabric.widgets.datetime import DateTime
from fabric.widgets.revealer import Revealer
from fabric.widgets.centerbox import CenterBox

from modules.metrics import Battery, MetricsSmall, NetworkApplet
import modules.icons as icons
from modules.systemtray import SystemTray
from modules.controls import ControlSmall
from widgets.wayland import WaylandWindow as Window

import os
import weakref


class Bar(Window):
    def __init__(self, monitor_id=0, **kwargs):
        self.monitor_id = monitor_id
        
        super().__init__(
            name="bar", 
            layer="top", 
            exclusivity="auto", 
            visible=True,
            all_visible=True, 
            monitor=monitor_id,
        )

        self._setup_appearance()
        
        self._notch_ref = None
        if "notch" in kwargs:
            self.notch = kwargs["notch"]
        
        self._state = {
            'menu_visible': False,
            'hidden': False
        }
        
        self._signal_handlers = []
        
        self._init_components()
        self._setup_layout()
    
    @property
    def notch(self):
        if self._notch_ref:
            return self._notch_ref()
        return None
    
    @notch.setter
    def notch(self, value):
        if value:
            self._notch_ref = weakref.ref(value)
        else:
            self._notch_ref = None

    def _init_components(self):
        self._init_workspaces()
        self._init_language_widget()
        self._init_power_battery_widget()
        self._init_widgets()

    def _init_workspaces(self):
        start_ws = self.monitor_id * 9 + 1
        ws_range = range(start_ws, start_ws + 9)
        
        ws_common_params = {
            "invert_scroll": True,
            "empty_scroll": True,
            "v_align": "fill",
            "orientation": "h",
            "buttons_factory": None
        }
        
        buttons = []
        for i in ws_range:
            button = WorkspaceButton(
                h_expand=False,
                v_expand=False,
                h_align="center",
                v_align="center",
                id=i,
                label=None,
            )
            buttons.append(button)
        
        self.workspaces = Workspaces(
            name="workspaces",
            spacing=8,
            buttons=buttons,
            **ws_common_params
        )

        num_buttons = []
        for i in ws_range:
            label_text = str(i)
            button = WorkspaceButton(
                h_expand=False,
                v_expand=False,
                h_align="center",
                v_align="center",
                id=i,
                label=label_text,
            )
            num_buttons.append(button)

        spacing_val = 0
        self.workspaces_num = Workspaces(
            name="workspaces-num",
            spacing=spacing_val,
            buttons=num_buttons,
            **ws_common_params
        )

        ws_child = self.workspaces
        self.ws_container = Box(
            name="workspaces-container",
            children=ws_child,
        )

    def _init_language_widget(self):
        self.lang_label = Label(name="lang-label")
        self.language_indicator = Box(
            name="language-indicator", 
            h_align="center", 
            v_align="center",
            children=[self.lang_label]
        )
        self.on_language_switch()
        
        conn = get_hyprland_connection()
        if conn:
            handler = conn.connect("event::activelayout", self.on_language_switch)
            self._signal_handlers.append((conn, handler))

    def _init_power_battery_widget(self):
        self.power_battery_container = Box(
            name="power-battery-container",
            orientation="h",
        )
        
        self._init_datetime_widget()
        self.power_battery_container.add(self.date_time)
        self.power_battery_container.add(self.language_indicator)
        
        self.battery = Battery()
        self.button_power = Button(
            name="button-bar", 
            tooltip_markup="<b>Меню питания</b>",
            on_clicked=self.power_menu,
            child=Label(name="button-bar-label", markup=icons.shutdown)
        )
        
        self.power_battery_container.add(self.battery)
        self.power_battery_container.add(self.button_power)
        
        def on_enter(w, e):
            cursor = Gdk.Cursor.new_from_name(w.get_display(), "hand2")
            w.get_window().set_cursor(cursor)
            
        def on_leave(w, e):
            w.get_window().set_cursor(None)
            
        self.button_power.connect("enter-notify-event", on_enter)
        self.button_power.connect("leave-notify-event", on_leave)

    def _init_widgets(self):
        self.systray = SystemTray()
        
        self.network_container = Box(name="network-container")
        self.network = NetworkApplet()
        self.network_container.add(self.network)
        
        self.control = ControlSmall()
        self.metrics = MetricsSmall()
        
        self._init_datetime_widget()
        self._setup_revealers()
        self.apply_component_props()

    def _init_datetime_widget(self):
        time_format = "%H:%M"
        self.date_time = DateTime(
            name="date-time",
            formatters=[time_format],
            h_align="center",
            v_align="center",
            h_expand=True,
            v_expand=True,
        )

    def _setup_revealers(self):
        left_widgets_box = Box(name="bar-revealer-box", orientation="h", spacing=4)
        left_widgets_box.add(self.systray)
        left_widgets_box.add(self.network_container)
        
        right_box = Box(name="bar-revealer-box", orientation="h", spacing=4)
        right_box.add(self.metrics)
        right_box.add(self.control)
        
        self.revealer_left = Revealer(
            name="bar-revealer",
            transition_type="slide-right",
            child_revealed=True,
            child=left_widgets_box,
        )
        
        self.revealer_right = Revealer(
            name="bar-revealer",
            transition_type="slide-left",
            child_revealed=True,
            child=right_box,
        )

        self.boxed_revealer_left = Box(name="boxed-revealer", children=[self.revealer_left])
        self.boxed_revealer_right = Box(name="boxed-revealer", children=[self.revealer_right])

    def _setup_appearance(self):
        self.set_anchor("left top right")
        self.set_margin("-4px -4px -8px -4px")
        css_path = os.path.expanduser("~/.config/ax-shell/styles/colors.css")
        if os.path.exists(css_path):
            provider = Gtk.CssProvider()
            provider.load_from_path(css_path)
            screen = Gdk.Screen.get_default()
            self.get_style_context().add_provider_for_screen(
                screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def _setup_layout(self):
        start_children = [self.ws_container, self.boxed_revealer_left]
        end_children = [self.boxed_revealer_right, self.power_battery_container]

        start_container = Box(name="start-container", spacing=4, children=start_children)
        end_container = Box(name="end-container", spacing=4, children=end_children)

        self.bar_inner = CenterBox(
            name="bar-inner", 
            orientation=Gtk.Orientation.HORIZONTAL,
            start_children=start_container,
            end_children=end_container
        )
        self.children = self.bar_inner

    def apply_component_props(self):
        self.component_visibility = {
            "button_apps": True,
            "systray": True,
            "control": True,
            "network": True,
            "button_tools": True,
            "sysprofiles": True,
            "button_overview": True,
            "ws_container": True,
            "battery": True,
            "metrics": True,
            "language": True,
            "date_time": True,
            "button_power": True,
        }

        components = {
            "systray": self.systray,
            "control": self.control,
            "ws_container": self.ws_container,
            "network": self.network,
            "network_container": self.network_container,
            "battery": self.battery, 
            "metrics": self.metrics, 
            "language_indicator": self.language_indicator, 
            "date_time": self.date_time,
            "button_power": self.button_power,
            "power_battery_container": self.power_battery_container,
            "revealer_left": self.revealer_left,
            "revealer_right": self.revealer_right
        }
        
        for name, widget in components.items():
            if name in self.component_visibility:
                widget.set_visible(self.component_visibility[name])

    def power_menu(self, *args):
        if self.notch:
            self.notch.open_notch("power")

    def on_language_switch(self, *args):
        if len(args) > 1:
            event = args[1]
            if event and event.data and len(event.data) > 1:
                lang_data = event.data[1]
            else:
                lang_data = Language().get_label()
        else:
            lang_data = Language().get_label()
        
        short_lang = lang_data[:3].upper() if len(lang_data) >= 3 else lang_data.upper()
        self.lang_label.set_label(short_lang)

    def toggle_hidden(self):
        self._state['hidden'] = not self._state['hidden']
        if self._state['hidden']:
            self.bar_inner.add_style_class("hidden")
        else:
            self.bar_inner.remove_style_class("hidden")
    
    def destroy(self):
        if hasattr(self, '_signal_handlers'):
            for conn, handler_id in self._signal_handlers:
                try:
                    conn.disconnect(handler_id)
                except:
                    pass
            self._signal_handlers = []
        
        self._notch_ref = None
        
        for component_name in ['systray', 'network', 'control', 'metrics', 'battery']:
            if hasattr(self, component_name):
                component = getattr(self, component_name)
                if hasattr(component, 'cleanup'):
                    try:
                        component.cleanup()
                    except:
                        pass
                elif hasattr(component, 'destroy'):
                    try:
                        component.destroy()
                    except:
                        pass
                setattr(self, component_name, None)

        super().destroy()