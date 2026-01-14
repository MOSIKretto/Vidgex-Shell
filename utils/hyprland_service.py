"""
Direct Hyprland Service Replacement
Replaces fabric.hyprland.service with direct communication
"""

from gi.repository import GObject
from typing import Any, Dict, List, Callable
from .hyprland_direct import get_hyprland_communicator


class Hyprland(GObject.GObject):
    """Replacement for fabric.hyprland.service.Hyprland"""

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
        from gi.repository import GLib
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

    def command(self, cmd: str):
        """Send a command to Hyprland"""
        class ReplyObj:
            def __init__(self, data):
                self.data = data.encode() if isinstance(data, str) else data
        
        response = self.comm._send_command(cmd)
        return ReplyObj(response)