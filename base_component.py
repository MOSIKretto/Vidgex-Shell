"""Base component module implementing DRY principles for all UI components."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Set, Tuple, Callable
import weakref
import gc
from gi.repository import GLib


class BaseComponent(ABC):
    """Base class for all UI components implementing common patterns."""
    
    def __init__(self):
        # Resource management
        self._timers: Set[int] = set()
        self._signal_handlers: List[Tuple[Any, int]] = []
        self._keybindings: List[int] = []
        self._alive: bool = True
        
        # Weak references to prevent circular dependencies
        self._parent_ref: Optional[weakref.ReferenceType] = None
    
    def set_parent(self, parent):
        """Set parent reference using weak reference to avoid circular dependencies."""
        self._parent_ref = weakref.ref(parent) if parent else None
    
    def get_parent(self):
        """Get parent reference safely."""
        return self._parent_ref() if self._parent_ref else None
    
    def add_timer(self, timer_id: int):
        """Add timer to tracking set."""
        self._timers.add(timer_id)
    
    def remove_timer(self, timer_id: int):
        """Remove timer from tracking set."""
        self._timers.discard(timer_id)
        if timer_id:
            GLib.source_remove(timer_id)
    
    def clear_timers(self):
        """Clear all tracked timers."""
        for timer_id in list(self._timers):
            if timer_id:
                GLib.source_remove(timer_id)
        self._timers.clear()
    
    def add_signal_handler(self, obj: Any, handler_id: int):
        """Add signal handler to tracking list."""
        self._signal_handlers.append((obj, handler_id))
    
    def clear_signal_handlers(self):
        """Disconnect and clear all tracked signal handlers."""
        for obj, handler_id in self._signal_handlers:
            try:
                obj.disconnect(handler_id)
            except Exception:
                pass  # Object might already be destroyed
        self._signal_handlers.clear()
    
    def add_keybinding(self, keybinding_id: int):
        """Add keybinding to tracking list."""
        self._keybindings.append(keybinding_id)
    
    def clear_keybindings(self):
        """Remove all tracked keybindings."""
        for keybinding_id in self._keybindings:
            try:
                if hasattr(self, 'remove_keybinding'):
                    self.remove_keybinding(keybinding_id)
            except Exception:
                pass
        self._keybindings.clear()
    
    @abstractmethod
    def destroy(self):
        """Abstract method for destroying the component."""
        self._alive = False
        self.clear_timers()
        self.clear_signal_handlers()
        self.clear_keybindings()
        
        # Clear weak references
        self._parent_ref = None
        
        # Force garbage collection
        gc.collect()


def debounce(delay: int):
    """Decorator for debouncing function calls."""
    def decorator(func):
        def wrapper(self, *args, **kwargs):
            if not hasattr(self, '_debounce_timers'):
                self._debounce_timers = {}
            
            timer_attr = f'_debounce_{func.__name__}'
            
            if hasattr(self, timer_attr):
                old_timer_id = getattr(self, timer_attr)
                if old_timer_id:
                    GLib.source_remove(old_timer_id)
                    if hasattr(self, '_debounce_timers') and old_timer_id in self._debounce_timers:
                        self._debounce_timers.discard(old_timer_id)
            
            def call_func():
                try:
                    result = func(self, *args, **kwargs)
                    # Clean up the stored timer ID
                    if hasattr(self, timer_attr):
                        delattr(self, timer_attr)
                    if hasattr(self, '_debounce_timers') and timer_id in self._debounce_timers:
                        self._debounce_timers.discard(timer_id)
                    return result
                except Exception:
                    return False
            
            timer_id = GLib.timeout_add(delay, call_func)
            setattr(self, timer_attr, timer_id)
            if not hasattr(self, '_debounce_timers'):
                self._debounce_timers = set()
            self._debounce_timers.add(timer_id)
            
            return False
        return wrapper
    return decorator


class ReusableUIBuilder:
    """Reusable UI building utilities to avoid code duplication."""
    
    @staticmethod
    def create_revealer(child, transition_type="slide-up", revealed=False, duration=250):
        """Create a standard revealer widget."""
        from fabric.widgets.revealer import Revealer
        return Revealer(
            transition_type=transition_type,
            transition_duration=duration,
            child=child,
            child_revealed=revealed
        )
    
    @staticmethod
    def create_circular_progress_bar(value=0, size=28, line_width=2, start_angle=150, end_angle=390, style_classes="", child=None):
        """Create a standard circular progress bar."""
        from fabric.widgets.circularprogressbar import CircularProgressBar
        return CircularProgressBar(
            value=value,
            size=size,
            line_width=line_width,
            start_angle=start_angle,
            end_angle=end_angle,
            style_classes=style_classes,
            child=child
        )
    
    @staticmethod
    def create_box_with_revealer(children=None, name="", orientation="h", spacing=0, reveal_transition="slide-right"):
        """Create a box with a built-in revealer."""
        from fabric.widgets.box import Box
        box = Box(
            name=name,
            orientation=orientation,
            spacing=spacing,
            children=children or []
        )
        revealer = ReusableUIBuilder.create_revealer(box, reveal_transition, False)
        return box, revealer
    
    @staticmethod
    def create_metric_widget(icon_markup, style_class, is_temp=False):
        """Create a reusable metric widget."""
        from fabric.widgets.label import Label
        from fabric.widgets.box import Box
        
        icon = Label(name="metrics-icon", markup=icon_markup)
        circle = ReusableUIBuilder.create_circular_progress_bar(
            value=0,
            size=28,
            line_width=2,
            start_angle=150,
            end_angle=390,
            style_classes=style_class,
            child=icon
        )
        
        level_label = Label(
            name="metrics-level", 
            style_classes=style_class, 
            label="0°C" if is_temp else "0%"
        )
        
        revealer = ReusableUIBuilder.create_revealer(level_label, "slide-left", False)
        
        container = Box(
            name=f"metrics-{style_class}-box",
            orientation="h",
            spacing=0,
            children=[circle, revealer]
        )
        
        return {
            'icon': icon,
            'circle': circle,
            'level': level_label,
            'revealer': revealer,
            'container': container,
            'markup': f"{icon_markup} {style_class.upper()}"
        }


class ResourceTracker:
    """Global resource tracker to help manage memory efficiently."""
    
    def __init__(self):
        self.tracked_objects = set()
        self.cleanup_callbacks = []
    
    def track_object(self, obj):
        """Track an object for later cleanup."""
        self.tracked_objects.add(id(obj))
    
    def untrack_object(self, obj):
        """Untrack an object."""
        self.tracked_objects.discard(id(obj))
    
    def add_cleanup_callback(self, callback):
        """Add a cleanup callback."""
        self.cleanup_callbacks.append(callback)
    
    def cleanup_all(self):
        """Cleanup all tracked resources."""
        for callback in self.cleanup_callbacks:
            try:
                callback()
            except Exception:
                pass
        self.cleanup_callbacks.clear()
        self.tracked_objects.clear()


# Global resource tracker instance
resource_tracker = ResourceTracker()