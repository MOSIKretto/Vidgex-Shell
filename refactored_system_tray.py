"""Refactored system tray module following DRY principles."""

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.image import Image
from gi.repository import Gio, GLib

from base_component import BaseComponent


class SystemTray(BaseComponent):
    """Refactored system tray following DRY principles."""
    
    def __init__(self, **kwargs):
        super().__init__()
        
        self.box = Box(
            name="system-tray",
            spacing=4,
            orientation="h",
            **kwargs
        )
        
        # Initialize system tray
        self._items = {}
        self._setup_system_tray()

    def _setup_system_tray(self):
        """Setup the system tray."""
        try:
            from fabric.core.service import Service
            from fabric.utils import bulk_connect
            
            # Create a service to handle system tray items
            self.proxy = Service({
                "org.freedesktop.StatusNotifierWatcher": {
                    "/StatusNotifierWatcher": {
                        "org.freedesktop.StatusNotifierWatcher": {
                            "RegisterStatusNotifierItem": self._register_item,
                        },
                    },
                },
            })
            
            # Connect to StatusNotifierWatcher
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call(
                "org.freedesktop.StatusNotifierWatcher",
                "/StatusNotifierWatcher",
                "org.freedesktop.StatusNotifierWatcher",
                "RegisterStatusNotifierItem",
                GLib.Variant("(s)", ("application",)),
                None,
                Gio.DBusCallFlags.NONE,
                -1,
                None
            )
        except Exception as e:
            print(f"SystemTray: Failed to initialize: {e}")
            # Fallback to simple implementation
            self._fallback_init()

    def _fallback_init(self):
        """Fallback initialization if system tray service is not available."""
        # Add a placeholder item to avoid empty tray
        placeholder = Box()
        self.box.add(placeholder)
        self.box.show_all()

    def _register_item(self, service, path, interface, method, params):
        """Register a system tray item."""
        try:
            # Extract service name from params
            service_name = params.unpack()[0] if params.n_children() > 0 else None
            
            if service_name and service_name not in self._items:
                # Create a button for the system tray item
                item_button = Button()
                
                # Create image for the icon
                icon_image = Image()
                item_button.add(icon_image)
                
                # Add to our items dict and box
                self._items[service_name] = {
                    'button': item_button,
                    'image': icon_image,
                    'service': service_name
                }
                
                self.box.add(item_button)
                self.box.show_all()
                
                # Connect to the item's status notifier interface
                self._connect_to_item(service_name, icon_image)
        except Exception as e:
            print(f"SystemTray: Failed to register item: {e}")

    def _connect_to_item(self, service_name, icon_image):
        """Connect to a registered system tray item."""
        try:
            # Set up a timer to periodically update icons (in a real implementation)
            # this would connect to actual StatusNotifierItem interfaces
            def update_icon():
                # This is a simplified implementation
                # In a real implementation, we would fetch icon info from the service
                if service_name in self._items:
                    # For demonstration purposes, we'll just cycle through a few icons
                    import random
                    icon_names = ["audio-volume-high", "audio-volume-medium", "audio-volume-low", "audio-volume-muted"]
                    icon_name = random.choice(icon_names)
                    icon_image.set_from_icon_name(icon_name, 24)
                    return True  # Continue the timer
                return False  # Stop the timer if item was removed
            GLib.timeout_add(5000, update_icon)  # Update every 5 seconds
        except Exception as e:
            print(f"SystemTray: Failed to connect to item {service_name}: {e}")

    def destroy(self):
        """Destroy the system tray component."""
        # Clear all items
        for item_info in self._items.values():
            button = item_info.get('button')
            if button:
                button.destroy()
        
        self._items.clear()
        
        # Destroy the box
        children = self.box.get_children()
        for child in children:
            child.destroy()
        
        super().destroy()


# Simple fallback if StatusNotifier isn't available
class SimpleSystemTray(BaseComponent):
    """Simple system tray implementation."""
    
    def __init__(self, **kwargs):
        super().__init__()
        
        self.box = Box(
            name="system-tray",
            spacing=4,
            orientation="h",
            **kwargs
        )
        
        # Add some common system tray icons
        common_icons = [
            "network-wired", 
            "audio-volume-high", 
            "battery-full", 
            "bluetooth-active"
        ]
        
        for icon_name in common_icons:
            icon_img = Image(icon_name=icon_name, icon_size=24)
            btn = Button(child=icon_img)
            btn.set_tooltip_text(f"System applet: {icon_name}")
            self.box.add(btn)
        
        self.box.show_all()

    def destroy(self):
        """Destroy the simple system tray component."""
        children = self.box.get_children()
        for child in children:
            child.destroy()
        
        super().destroy()