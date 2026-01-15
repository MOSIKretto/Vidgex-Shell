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


def global_cleanup():
    """Global cleanup function to be called on application exit"""
    # Clear all managed timers
    SharedTimerManager.clear_all_timers()
    
    # Shutdown thread pool
    pool = SharedThreadPool()
    pool.shutdown()
    
    # Force garbage collection
    import gc
    gc.collect()