"""
Common utilities and shared components to reduce code duplication (DRY principle)
"""

from gi.repository import GLib
from typing import Callable, Any, Optional
import threading
import weakref


class SharedTimerManager:
    """
    A centralized timer manager to prevent timer proliferation
    """
    _instance = None
    _timers = set()
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @classmethod
    def add_timer(cls, timer_id: int) -> int:
        """Add a timer to the managed collection"""
        cls._timers.add(timer_id)
        return timer_id
    
    @classmethod
    def remove_timer(cls, timer_id: int):
        """Remove a timer from the managed collection"""
        cls._timers.discard(timer_id)
    
    @classmethod
    def clear_all_timers(cls):
        """Clear all managed timers"""
        for timer_id in list(cls._timers):
            try:
                GLib.source_remove(timer_id)
            except Exception:
                pass  # Timer already removed
        cls._timers.clear()


class Debouncer:
    """
    A debouncer utility to prevent function call flooding
    """
    def __init__(self, delay_ms: int = 150):
        self.delay_ms = delay_ms
        self._timer_ids = {}
    
    def debounce(self, func: Callable) -> Callable:
        """Decorator to debounce function calls"""
        def wrapper(*args, **kwargs):
            # Get instance from args if available
            instance = args[0] if args else None
            func_name = f"{id(instance)}_{func.__name__}" if instance else func.__name__
            
            # Cancel existing timer
            if func_name in self._timer_ids:
                try:
                    GLib.source_remove(self._timer_ids[func_name])
                except Exception:
                    pass
            
            # Create new timer
            def call_func():
                try:
                    result = func(*args, **kwargs)
                    # Clean up the timer ID
                    if func_name in self._timer_ids:
                        del self._timer_ids[func_name]
                    return result
                except Exception:
                    if func_name in self._timer_ids:
                        del self._timer_ids[func_name]
                    raise
                return False
            
            timer_id = GLib.timeout_add(self.delay_ms, call_func)
            self._timer_ids[func_name] = timer_id
            return None
        
        return wrapper


class SharedThreadPool:
    """
    A shared thread pool to prevent thread proliferation
    """
    _instance = None
    _max_workers = 4
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._workers = []
        return cls._instance
    
    def submit_task(self, func: Callable, *args, **kwargs) -> threading.Thread:
        """Submit a task to the thread pool"""
        def worker():
            try:
                func(*args, **kwargs)
            finally:
                # Clean up thread from pool
                if threading.current_thread() in self._workers:
                    self._workers.remove(threading.current_thread())
        
        # Limit number of concurrent workers
        active_count = len([t for t in self._workers if t.is_alive()])
        if active_count >= self._max_workers:
            # If pool is full, run immediately but don't add to pool
            thread = threading.Thread(target=func, args=args, kwargs=kwargs, daemon=True)
            thread.start()
            return thread
        
        thread = threading.Thread(target=worker, daemon=True)
        self._workers.append(thread)
        thread.start()
        return thread
    
    def shutdown(self):
        """Shutdown all worker threads"""
        for thread in self._workers[:]:
            if thread.is_alive():
                # Threads are daemon, so they will terminate with main process
                pass
        self._workers.clear()


def safe_disconnect_signal(obj, handler_id):
    """Safely disconnect a signal handler"""
    try:
        obj.disconnect(handler_id)
    except Exception:
        pass  # Handler already disconnected


def safe_destroy_widget(widget):
    """Safely destroy a widget"""
    if widget and hasattr(widget, 'destroy'):
        try:
            widget.destroy()
        except Exception:
            pass


def create_weak_method(method):
    """Create a weak reference to a method to prevent circular references"""
    instance = method.__self__
    func = method.__func__
    cls = instance.__class__
    
    def weak_method(*args, **kwargs):
        # Check if instance still exists
        import gc
        for obj in gc.get_objects():
            if isinstance(obj, cls) and id(obj) == id(instance):
                return func(obj, *args, **kwargs)
        return None
    
    return weak_method


class TimerManager:
    """
    A centralized timer manager to handle GLib timers efficiently
    """
    _instance = None
    _timers = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def add_timer(self, obj, timer_id: int, timer_type: str = "generic"):
        """Add a timer to the managed collection"""
        key = f"{id(obj)}_{timer_type}"
        if obj not in self._timers:
            self._timers[id(obj)] = {}
        self._timers[id(obj)][timer_type] = timer_id
        return timer_id
    
    def remove_timer(self, obj, timer_type: str = "generic"):
        """Remove a timer from the managed collection"""
        if id(obj) in self._timers and timer_type in self._timers[id(obj)]:
            timer_id = self._timers[id(obj)][timer_type]
            try:
                GLib.source_remove(timer_id)
            except Exception:
                pass  # Timer already removed
            del self._timers[id(obj)][timer_type]
            if not self._timers[id(obj)]:
                del self._timers[id(obj)]
    
    def remove_all_timers_for_obj(self, obj):
        """Remove all timers associated with an object"""
        if id(obj) in self._timers:
            for timer_type, timer_id in self._timers[id(obj)].items():
                try:
                    GLib.source_remove(timer_id)
                except Exception:
                    pass
            del self._timers[id(obj)]
    
    def clear_all_timers(self):
        """Clear all managed timers"""
        for obj_timers in self._timers.values():
            for timer_id in obj_timers.values():
                try:
                    GLib.source_remove(timer_id)
                except Exception:
                    pass
        self._timers.clear()


class MouseEventHandler:
    """
    A mixin class to handle common mouse enter/leave events
    """
    def setup_mouse_events(self, widget, on_enter_callback=None, on_leave_callback=None):
        """Setup mouse enter/leave events for a widget"""
        enter_handler = widget.connect("enter-notify-event", 
                                     on_enter_callback or self._on_mouse_enter)
        leave_handler = widget.connect("leave-notify-event", 
                                     on_leave_callback or self._on_mouse_leave)
        return enter_handler, leave_handler
    
    def _on_mouse_enter(self, widget, event):
        """Default mouse enter handler"""
        pass
    
    def _on_mouse_leave(self, widget, event):
        """Default mouse leave handler"""
        pass


class CursorHoverHandler:
    """
    A mixin class to handle cursor hover effects
    """
    def setup_cursor_hover(self, button):
        """Setup cursor hover effect for a button"""
        def on_enter(widget, event):
            if hasattr(button, 'get_window') and button.get_window():
                button.get_window().set_cursor(
                    Gdk.Cursor.new_from_name(button.get_display(), "hand2")
                )
        
        def on_leave(widget, event):
            if hasattr(button, 'get_window') and button.get_window():
                button.get_window().set_cursor(None)
        
        enter_handler = button.connect("enter-notify-event", on_enter)
        leave_handler = button.connect("leave-notify-event", on_leave)
        return enter_handler, leave_handler


class DelayedUpdateMixin:
    """
    A mixin class to handle delayed updates with proper cleanup
    """
    def init_delayed_update(self):
        self._pending_values = {}
        self._update_source_ids = {}
    
    def schedule_update(self, update_type: str, callback, delay=100):
        """Schedule an update with a specific delay"""
        if update_type in self._update_source_ids:
            try:
                GLib.source_remove(self._update_source_ids[update_type])
            except Exception:
                pass
        self._update_source_ids[update_type] = GLib.timeout_add(delay, callback)
    
    def cancel_update(self, update_type: str):
        """Cancel a scheduled update"""
        if update_type in self._update_source_ids:
            try:
                GLib.source_remove(self._update_source_ids[update_type])
            except Exception:
                pass
            del self._update_source_ids[update_type]
    
    def cleanup_delayed_updates(self):
        """Clean up all pending delayed updates"""
        for source_id in self._update_source_ids.values():
            try:
                GLib.source_remove(source_id)
            except Exception:
                pass
        self._update_source_ids.clear()
        self._pending_values.clear()


class ProgressUpdateMixin:
    """
    A mixin class to handle progress updates with timers
    """
    def init_progress_updates(self):
        self._progress_timers = {}
    
    def start_progress_timer(self, timer_type: str, callback, interval_ms: int):
        """Start a progress timer"""
        self._progress_timers[timer_type] = GLib.timeout_add(interval_ms, callback)
    
    def stop_progress_timer(self, timer_type: str):
        """Stop a progress timer"""
        if timer_type in self._progress_timers:
            try:
                GLib.source_remove(self._progress_timers[timer_type])
            except Exception:
                pass
            del self._progress_timers[timer_type]
    
    def stop_all_progress_timers(self):
        """Stop all progress timers"""
        for timer_id in self._progress_timers.values():
            try:
                GLib.source_remove(timer_id)
            except Exception:
                pass
        self._progress_timers.clear()


class SignalManager:
    """
    A centralized signal manager to handle GObject signal connections
    """
    _instance = None
    _connections = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def connect_and_store(self, obj, signal_name: str, callback, store_key: str = None):
        """Connect a signal and store the handler ID for later cleanup"""
        handler_id = obj.connect(signal_name, callback)
        obj_id = id(obj)
        if obj_id not in self._connections:
            self._connections[obj_id] = {}
        key = store_key or signal_name
        self._connections[obj_id][key] = (obj, handler_id, signal_name)
        return handler_id
    
    def disconnect_signal(self, obj, store_key: str = None):
        """Disconnect a stored signal"""
        obj_id = id(obj)
        if obj_id in self._connections:
            key = store_key or next(iter(self._connections[obj_id]), None)
            if key and key in self._connections[obj_id]:
                connected_obj, handler_id, signal_name = self._connections[obj_id][key]
                try:
                    connected_obj.disconnect(handler_id)
                except Exception:
                    pass
                del self._connections[obj_id][key]
                if not self._connections[obj_id]:
                    del self._connections[obj_id]
    
    def disconnect_all_for_obj(self, obj):
        """Disconnect all signals for an object"""
        obj_id = id(obj)
        if obj_id in self._connections:
            for connected_obj, handler_id, signal_name in self._connections[obj_id].values():
                try:
                    connected_obj.disconnect(handler_id)
                except Exception:
                    pass
            del self._connections[obj_id]
    
    def clear_all_connections(self):
        """Clear all managed signal connections"""
        for obj_connections in self._connections.values():
            for connected_obj, handler_id, signal_name in obj_connections.values():
                try:
                    connected_obj.disconnect(handler_id)
                except Exception:
                    pass
        self._connections.clear()


class MutedStyleMixin:
    """
    A mixin class to handle muted style updates
    """
    def update_muted_style(self, is_muted, widgets=None):
        if widgets is None:
            widgets = [self]
        
        for widget in widgets:
            if hasattr(widget, 'add_style_class') and hasattr(widget, 'remove_style_class'):
                if is_muted:
                    widget.add_style_class("muted")
                else:
                    widget.remove_style_class("muted")


class RevealerManager:
    """
    A centralized manager for revealer animations
    """
    def __init__(self):
        self._reveal_timers = {}
    
    def reveal_with_delay(self, revealer, revealed: bool, delay_ms: int = 500):
        """Reveal a widget with a delay"""
        key = f"{id(revealer)}_reveal"
        if key in self._reveal_timers:
            try:
                GLib.source_remove(self._reveal_timers[key])
            except Exception:
                pass
        
        def do_reveal():
            revealer.set_reveal_child(revealed)
            if key in self._reveal_timers:
                del self._reveal_timers[key]
            return False
        
        self._reveal_timers[key] = GLib.timeout_add(delay_ms, do_reveal)
    
    def cancel_reveal(self, revealer):
        """Cancel a pending reveal animation"""
        key = f"{id(revealer)}_reveal"
        if key in self._reveal_timers:
            try:
                GLib.source_remove(self._reveal_timers[key])
            except Exception:
                pass
            del self._reveal_timers[key]
    
    def cleanup_all_revealers(self):
        """Clean up all reveal timers"""
        for timer_id in self._reveal_timers.values():
            try:
                GLib.source_remove(timer_id)
            except Exception:
                pass
        self._reveal_timers.clear()


def global_cleanup():
    """Global cleanup function to be called on application exit"""
    # Clear all managed timers
    timer_manager = TimerManager()
    timer_manager.clear_all_timers()
    
    # Clear all signal connections
    signal_manager = SignalManager()
    signal_manager.clear_all_connections()
    
    # Shutdown thread pool
    pool = SharedThreadPool()
    pool.shutdown()
    
    # Force garbage collection
    import gc
    gc.collect()