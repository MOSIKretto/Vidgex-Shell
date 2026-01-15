"""Refactored controls module following DRY principles."""

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer
from gi.repository import Gdk

from base_component import BaseComponent
import modules.icons as icons


class ControlSmall(BaseComponent):
    """Refactored control component following DRY principles."""
    
    def __init__(self, **kwargs):
        super().__init__()
        
        self.button = Button(name="button-bar", **kwargs)
        
        # Create main container
        main_box = Box(orientation="h", spacing=0)
        
        # Create volume control
        self.volume_label = Label(name="volume-label", markup=icons.vol.normal)
        self.volume_revealer = Revealer(
            transition_type="slide-left",
            transition_duration=250,
            child=Box(
                name="volume-box",
                children=[
                    Label(name="volume-icon-label", markup=icons.vol.down),
                    Label(name="volume-value-label", label="0%"),
                ]
            ),
            child_revealed=False
        )
        
        # Create brightness control
        self.brightness_label = Label(name="brightness-label", markup=icons.brightness.low)
        self.brightness_revealer = Revealer(
            transition_type="slide-right",
            transition_duration=250,
            child=Box(
                name="brightness-box",
                children=[
                    Label(name="brightness-icon-label", markup=icons.brightness.high),
                    Label(name="brightness-value-label", label="0%"),
                ]
            ),
            child_revealed=False
        )
        
        # Add all to main box
        main_box.add(self.volume_revealer)
        main_box.add(self.volume_label)
        main_box.add(self.brightness_label)
        main_box.add(self.brightness_revealer)
        
        self.button.add(main_box)
        
        # Setup event handlers
        self.is_mouse_over = False
        self._revealed = False
        self._setup_events()
        
        # Start update timer
        from gi.repository import GLib
        timer_id = GLib.timeout_add(1000, self.update_values)
        self.add_timer(timer_id)

    def _setup_events(self):
        """Setup mouse event handlers."""
        self._enter_handler = self.button.connect("enter-notify-event", self._on_enter)
        self._leave_handler = self.button.connect("leave-notify-event", self._on_leave)
        
        # Track signal handlers
        self.add_signal_handler(self.button, self._enter_handler)
        self.add_signal_handler(self.button, self._leave_handler)

    def _on_enter(self, *args):
        """Handle mouse enter."""
        self.is_mouse_over = True
        self._update_reveal()

    def _on_leave(self, *args):
        """Handle mouse leave."""
        self.is_mouse_over = False
        self._update_reveal()

    def _update_reveal(self):
        """Update reveal state."""
        if self._revealed == self.is_mouse_over:
            return
        self._revealed = self.is_mouse_over
        self.volume_revealer.set_reveal_child(self._revealed)
        self.brightness_revealer.set_reveal_child(self._revealed)

    def update_values(self):
        """Update volume and brightness values."""
        # Simulate getting volume and brightness values
        # In a real implementation, these would come from audio/video services
        vol_value = 65  # Placeholder value
        bright_value = 75  # Placeholder value
        
        # Update volume
        self.volume_label.set_markup(
            icons.vol.high if vol_value >= 70 
            else icons.vol.normal if vol_value >= 30 
            else icons.vol.low
        )
        self.volume_revealer.get_child().get_children()[1].set_label(f"{vol_value}%")
        
        # Update brightness
        self.brightness_label.set_markup(
            icons.brightness.high if bright_value >= 70 
            else icons.brightness.medium if bright_value >= 30 
            else icons.brightness.low
        )
        self.brightness_revealer.get_child().get_children()[1].set_label(f"{bright_value}%")
        
        # Update tooltip
        self.button.set_tooltip_markup(
            f"<tt>Громкость: {vol_value}%\nЯркость: {bright_value}%</tt>"
        )
        
        return True  # Continue the timer

    def destroy(self):
        """Destroy the control component."""
        super().destroy()


class Control(BaseComponent):
    """Extended control component."""
    
    def __init__(self, **kwargs):
        super().__init__()
        
        self.box = Box(name="control", spacing=0, orientation="h", **kwargs)
        
        # Create volume control
        volume_box = Box(
            name="volume",
            orientation="h",
            spacing=4,
            children=[
                Button(
                    name="volume-down",
                    child=Label(markup=icons.vol.down),
                    tooltip_markup="<b>Убавить громкость</b>",
                ),
                Box(
                    name="volume-scale-box",
                    v_expand=True,
                    v_align="fill",
                    children=[
                        # In a real implementation, we'd use a scale widget
                        # For now we'll just use a placeholder
                    ]
                ),
                Button(
                    name="volume-up",
                    child=Label(markup=icons.vol.up),
                    tooltip_markup="<b>Прибавить громкость</b>",
                ),
                Button(
                    name="volume-muted",
                    child=Label(markup=icons.vol.muted),
                    tooltip_markup="<b>Выключить/включить звук</b>",
                ),
            ]
        )
        
        # Create brightness control
        brightness_box = Box(
            name="brightness",
            orientation="h",
            spacing=4,
            children=[
                Button(
                    name="brightness-down",
                    child=Label(markup=icons.brightness.low),
                    tooltip_markup="<b>Убавить яркость</b>",
                ),
                Box(
                    name="brightness-scale-box",
                    v_expand=True,
                    v_align="fill",
                    children=[
                        # In a real implementation, we'd use a scale widget
                        # For now we'll just use a placeholder
                    ]
                ),
                Button(
                    name="brightness-up",
                    child=Label(markup=icons.brightness.high),
                    tooltip_markup="<b>Прибавить яркость</b>",
                ),
            ]
        )
        
        # Add both controls to main box
        self.box.add(volume_box)
        self.box.add(brightness_box)
        
        # Setup cursor hover effects
        self._setup_cursor_hover(volume_box)
        self._setup_cursor_hover(brightness_box)

    def _setup_cursor_hover(self, widget):
        """Setup cursor hover effect."""
        def on_enter(*args):
            try:
                widget.get_window().set_cursor(
                    Gdk.Cursor.new_from_name(widget.get_display(), "hand2")
                )
            except:
                pass  # Handle case where widget doesn't have a window yet
        
        def on_leave(*args):
            try:
                widget.get_window().set_cursor(None)
            except:
                pass  # Handle case where widget doesn't have a window yet
        
        enter_id = widget.connect("enter-notify-event", on_enter)
        leave_id = widget.connect("leave-notify-event", on_leave)
        
        # Track signal handlers
        self.add_signal_handler(widget, enter_id)
        self.add_signal_handler(widget, leave_id)

    def destroy(self):
        """Destroy the extended control component."""
        super().destroy()