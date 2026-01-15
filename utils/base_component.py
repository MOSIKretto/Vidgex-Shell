"""
Base component class for all UI components to ensure proper resource management.
"""

import weakref
from gi.repository import GLib
from typing import List, Set, Any, Optional
from .resource_manager import get_resource_manager


class BaseComponent:
    """
    Base class for all components that manages resources properly.
    """
    
    def __init__(self):
        self._alive = True
        self._timers: Set[int] = set()
        self._signal_handlers: List[tuple] = []
        self._keybindings: List[int] = []
        self._resource_manager = get_resource_manager()
        
    def register_timer(self, timer_id: int) -> int:
        """Register a timer for cleanup."""
        self._timers.add(timer_id)
        return self._resource_manager.register_timer(timer_id)
    
    def register_signal_handler(self, connection, handler_id) -> None:
        """Register a signal handler for cleanup."""
        self._signal_handlers.append((connection, handler_id))
        self._resource_manager.register_signal_handler(connection, handler_id)
        
    def register_keybinding(self, key_id: int) -> None:
        """Register a keybinding for cleanup."""
        self._keybindings.append(key_id)
    
    def _stop_all_timers(self):
        """Stop all registered timers."""
        for timer_id in list(self._timers):
            try:
                GLib.source_remove(timer_id)
            except Exception:
                pass  # Timer might have already been removed
            self._resource_manager.unregister_timer(timer_id)
        self._timers.clear()
    
    def _disconnect_all_handlers(self):
        """Disconnect all registered signal handlers."""
        for conn, handler_id in self._signal_handlers:
            try:
                conn.disconnect(handler_id)
            except Exception:
                pass  # Handler might have already been disconnected
        self._signal_handlers.clear()
    
    def _remove_all_keybindings(self):
        """Remove all registered keybindings."""
        for key_id in self._keybindings:
            try:
                if hasattr(self, 'remove_keybinding'):
                    self.remove_keybinding(key_id)
            except Exception:
                pass  # Keybinding might have already been removed
        self._keybindings.clear()
    
    def destroy(self):
        """Destroy the component and cleanup all resources."""
        if not self._alive:
            return
        self._alive = False
        
        # Stop all timers
        self._stop_all_timers()
        
        # Disconnect all signal handlers
        self._disconnect_all_handlers()
        
        # Remove all keybindings
        self._remove_all_keybindings()
        
        # Clear references to avoid circular references
        self._resource_manager = None