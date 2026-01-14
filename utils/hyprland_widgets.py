"""
Direct Hyprland Widgets Replacement
Replaces fabric.hyprland.widgets with direct communication
"""

from gi.repository import GObject, GLib
from typing import Any, Dict, List, Callable
from .hyprland_direct import get_hyprland_communicator
import json


class HyprlandEventEmitter(GObject.GObject):
    """Base class for emitting Hyprland events"""
    
    __gsignals__ = {
        'ready': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'openwindow': (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        'closewindow': (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        'movewindow': (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        'resizewindow': (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        'workspace': (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        'activewindow': (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        'activelayout': (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        'monitoradded': (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        'monitorremoved': (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self):
        super().__init__()
        self.comm = get_hyprland_communicator()
        self._event_handlers = {}
        
        # Connect to Hyprland events
        self.comm.subscribe_to_events([
            'openwindow', 'closewindow', 'movewindow', 'resizewindow',
            'workspace', 'activewindow', 'activelayout', 'monitoradded', 'monitorremoved'
        ], self._handle_generic_event)
        
        # Emit ready signal immediately
        GLib.idle_add(self._emit_ready)
    
    def _emit_ready(self):
        self.emit('ready')

    def _handle_generic_event(self, event_data: str):
        """Handle generic Hyprland events"""
        try:
            # Split event name and data if available
            if '>>' in event_data:
                parts = event_data.split('>>', 1)
                event_name = parts[0]
                event_payload = parts[1] if len(parts) > 1 else ''
                
                # Emit the specific event
                if event_name in [
                    'openwindow', 'closewindow', 'movewindow', 'resizewindow',
                    'workspace', 'activewindow', 'activelayout', 'monitoradded', 'monitorremoved'
                ]:
                    self.emit(event_name, event_payload)
                    
        except Exception as e:
            print(f"Error handling Hyprland event: {e}")

    def connect(self, signal_name: str, callback: Callable):
        """Connect to a signal"""
        return super().connect(signal_name, callback)

    def disconnect(self, handler_id):
        """Disconnect from a signal"""
        super().disconnect(handler_id)

    def send_command(self, command: str):
        """Send a command to Hyprland"""
        class ReplyObj:
            def __init__(self, data):
                self.reply = data.encode() if isinstance(data, str) else data
        
        response = self.comm._send_command(command)
        return ReplyObj(response)


def get_hyprland_connection():
    """Get Hyprland connection object"""
    return HyprlandEventEmitter()


class HyprlandWorkspaces:
    """Replacement for fabric.hyprland.widgets.HyprlandWorkspaces"""
    
    def __init__(self, name: str = None, spacing: int = None, **kwargs):
        self.name = name
        self.spacing = spacing
        self.comm = get_hyprland_communicator()
        self.workspaces_data = []
        
        # Initialize with current workspaces
        self.refresh_workspaces()

    def refresh_workspaces(self):
        """Refresh workspace data"""
        self.workspaces_data = self.comm.get_workspaces()

    def get_workspaces(self):
        """Get current workspaces"""
        return self.workspaces_data


class HyprlandLanguage:
    """Replacement for fabric.hyprland.widgets.HyprlandLanguage"""
    
    def __init__(self, **kwargs):
        self.comm = get_hyprland_communicator()
        
    def get_label(self):
        """Get current keyboard layout label"""
        try:
            # Get the current layout via hyprctl
            import subprocess
            result = subprocess.run(['hyprctl', 'devices', '-j'], 
                                  capture_output=True, text=True)
            if result.returncode == 0:
                devices_data = json.loads(result.stdout)
                # Find keyboard layouts in the devices data
                keyboards = devices_data.get('keyboards', [])
                if keyboards:
                    # Return the first keyboard's layout name
                    layout = keyboards[0].get('active_keymap', 'en')
                    return layout.split('(')[0].strip()  # Clean up layout name
        except Exception:
            pass
        return 'en'