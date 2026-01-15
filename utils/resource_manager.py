"""
Global resource manager to handle shared resources and prevent memory leaks.
"""

import threading
import weakref
from gi.repository import GLib
from typing import Dict, List, Set, Any


class ResourceManager:
    """
    A centralized resource manager to track and cleanup resources properly.
    """
    
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if not cls._instance:
                cls._instance = super().__new__(cls)
            return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True
        
        # Track all resources that need cleanup
        self.timers: Set[int] = set()
        self.signal_handlers: List[tuple] = []
        self.background_threads: List[threading.Thread] = []
        self.shared_resources: Dict[str, Any] = {}
        
        # Weak references to objects that might need cleanup
        self.tracked_objects: Set[weakref.ref] = set()

    def register_timer(self, timer_id: int) -> int:
        """Register a timer for cleanup."""
        self.timers.add(timer_id)
        return timer_id

    def unregister_timer(self, timer_id: int) -> bool:
        """Unregister a timer."""
        return self.timers.discard(timer_id) is not None

    def register_signal_handler(self, connection, handler_id) -> None:
        """Register a signal handler for cleanup."""
        self.signal_handlers.append((connection, handler_id))

    def register_background_thread(self, thread: threading.Thread) -> None:
        """Register a background thread for tracking."""
        self.background_threads.append(thread)

    def register_shared_resource(self, name: str, resource: Any) -> None:
        """Register a shared resource."""
        self.shared_resources[name] = resource

    def get_shared_resource(self, name: str, factory=None):
        """Get a shared resource, creating it if needed."""
        if name not in self.shared_resources and factory:
            self.shared_resources[name] = factory()
        return self.shared_resources.get(name)

    def cleanup_timers(self):
        """Clean up all registered timers."""
        for timer_id in list(self.timers):
            try:
                GLib.source_remove(timer_id)
            except Exception:
                pass  # Timer might have already been removed
        self.timers.clear()

    def cleanup_signal_handlers(self):
        """Clean up all registered signal handlers."""
        for connection, handler_id in self.signal_handlers:
            try:
                connection.disconnect(handler_id)
            except Exception:
                pass  # Handler might have already been disconnected
        self.signal_handlers.clear()

    def cleanup_background_threads(self):
        """Clean up background threads."""
        for thread in self.background_threads:
            if thread.is_alive():
                # Don't force kill threads, just clear reference
                pass
        self.background_threads.clear()

    def cleanup_all(self):
        """Clean up all resources."""
        self.cleanup_timers()
        self.cleanup_signal_handlers()
        self.cleanup_background_threads()
        
        # Clean up shared resources that have a destroy method
        for name, resource in list(self.shared_resources.items()):
            if hasattr(resource, 'destroy'):
                try:
                    resource.destroy()
                except Exception:
                    pass
            del self.shared_resources[name]


def get_resource_manager() -> ResourceManager:
    """Get the global resource manager instance."""
    return ResourceManager()