"""Refactored bar module following DRY principles."""

from fabric.hyprland.widgets import (
    HyprlandLanguage as Language,
    HyprlandWorkspaces as Workspaces,
    get_hyprland_connection
)
from fabric.widgets.box import Box
from fabric.widgets.label import Label
from fabric.widgets.button import Button
from fabric.widgets.datetime import DateTime
from gi.repository import Gdk

from base_component import BaseComponent, ReusableUIBuilder
from refactored_metrics import MetricsSmall, NetworkApplet, BatteryButton
from modules.systemtray import SystemTray
from modules.controls import ControlSmall

from widgets.wayland import WaylandWindow as Window

import modules.icons as icons


class Bar(BaseComponent):
    """Refactored bar component following DRY principles."""
    
    def __init__(self, monitor_id=0, **kwargs):
        super().__init__()
        self.monitor_id = monitor_id
        
        # Create the window
        self.window = Window(exclusivity="auto")
        self.window._set_anchor_fast("left top right")
        self.window.set_margin("-4px -4px -8px -4px")
        
        # Store references to other components
        self.notch = kwargs.get("notch")
        
        # Build UI
        self._build_ui()
        self._setup_hyprland_signals()
        
        # Add to window
        self.window.add(self.main_container)

    def _build_ui(self):
        """Build the user interface."""
        # Create workspaces
        self.workspaces = Workspaces(name="workspaces", spacing=8)
        ws_container = Box(children=[self.workspaces])
        
        # Create system tray and network
        self.systray = SystemTray()
        self.network = NetworkApplet()
        network_container = Box(name="network-container", children=[self.network])
        
        # Left side widgets
        left_widgets = Box(
            name="bar-revealer-box",
            orientation="h",
            spacing=4,
            children=[self.systray, network_container]
        )
        
        # Create revealers for left side
        self.revealer_left = ReusableUIBuilder.create_revealer(
            left_widgets, "slide-right", True
        )
        boxed_revealer_left = Box(name="boxed-revealer", children=[self.revealer_left])
        
        # Create metrics and controls
        self.metrics = MetricsSmall()
        self.control = ControlSmall()
        
        # Right side widgets
        right_widgets = Box(
            name="bar-revealer-box",
            orientation="h",
            spacing=4,
            children=[self.metrics, self.control]
        )
        
        # Create revealers for right side
        self.revealer_right = ReusableUIBuilder.create_revealer(
            right_widgets, "slide-left", True
        )
        boxed_revealer_right = Box(name="boxed-revealer", children=[self.revealer_right])
        
        # Create language indicator
        self.lang_label = Label(name="lang-label")
        language_indicator = Box(name="language-indicator", children=[self.lang_label])
        
        # Create date/time and battery
        self.date_time = DateTime(name="date-time", formatters=["%H:%M"])
        self.battery = BatteryButton()  # Using the refactored battery button
        
        # Create power button
        self.button_power = Button(
            name="button-bar",
            tooltip_markup="<b>Меню питания</b>",
            on_clicked=self.power_menu,
            child=Label(name="button-bar-label", markup=icons.shutdown)
        )
        self._setup_cursor_hover(self.button_power)
        
        # Create power/battery container
        power_battery_container = Box(
            name="power-battery-container",
            children=[self.date_time, language_indicator, self.battery, self.button_power]
        )
        
        # Create the main layout
        from fabric.widgets.centerbox import CenterBox
        self.main_container = CenterBox(
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
        )

    def _setup_hyprland_signals(self):
        """Setup Hyprland signal connections."""
        conn = get_hyprland_connection()
        if conn:
            handler = conn.connect("event::activelayout", self._update_language)
            self.add_signal_handler(conn, handler)
        self._update_language()

    def _setup_cursor_hover(self, button):
        """Setup cursor hover effect."""
        def on_enter(_, event):
            button.get_window().set_cursor(
                Gdk.Cursor.new_from_name(button.get_display(), "hand2")
            )
        
        def on_leave(_, event):
            button.get_window().set_cursor(None)
        
        enter_handler = button.connect("enter-notify-event", on_enter)
        leave_handler = button.connect("leave-notify-event", on_leave)
        
        self.add_signal_handler(button, enter_handler)
        self.add_signal_handler(button, leave_handler)

    def power_menu(self, *args):
        """Handle power menu click."""
        if self.notch:
            self.notch.open_notch("power")

    def _update_language(self, *args):
        """Update language display."""
        if args and len(args) > 1 and args[1]:
            lang_data = getattr(args[1], 'data', [''])[1] if hasattr(args[1], 'data') else Language().get_label()
        else:
            lang_data = Language().get_label()
        
        self.lang_label.set_label(lang_data[:3].upper() if len(lang_data) >= 3 else lang_data.upper())

    def destroy(self):
        """Destroy the bar component."""
        # Destroy child components
        components = [
            self.systray, self.network, self.control, self.metrics, 
            self.battery, self.date_time, self.workspaces, self.button_power, 
            self.lang_label, self.revealer_left, self.revealer_right
        ]
        
        for comp in components:
            if comp and hasattr(comp, 'destroy'):
                comp.destroy()
        
        # Destroy the window
        if hasattr(self, 'window') and self.window:
            self.window.destroy()
        
        super().destroy()