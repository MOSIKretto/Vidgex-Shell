"""
DRY Utilities Module
Centralized components and functions to eliminate code duplication
"""

from gi.repository import GLib, Gdk
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label
from fabric.widgets.image import Image
from fabric.widgets.revealer import Revealer
from fabric.widgets.circularprogressbar import CircularProgressBar
from fabric.widgets.scale import Scale
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.eventbox import EventBox
from fabric.widgets.stack import Stack
import threading
import weakref
from typing import Callable, Any, Optional, List, Dict, Set
from enum import Enum


class WidgetType(Enum):
    """Enumeration of common widget types to standardize creation"""
    METRICS_CIRCLE = "metrics_circle"
    SCALED_METRIC = "scaled_metric"
    REVEALABLE_BUTTON = "reveailable_button"


class CentralizedResources:
    """
    Singleton class to manage shared resources and prevent duplication
    """
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._shared_timers = set()
            cls._instance._shared_threads = set()
            cls._instance._shared_widgets = {}
        return cls._instance

    def register_timer(self, timer_id: int) -> int:
        """Register a timer for centralized management"""
        self._shared_timers.add(timer_id)
        return timer_id

    def unregister_timer(self, timer_id: int):
        """Unregister a timer"""
        self._shared_timers.discard(timer_id)

    def clear_all_timers(self):
        """Clear all registered timers"""
        for timer_id in list(self._shared_timers):
            try:
                GLib.source_remove(timer_id)
            except Exception:
                pass  # Timer already removed
        self._shared_timers.clear()

    def register_widget(self, name: str, widget: Any):
        """Register a widget for centralized management"""
        self._shared_widgets[name] = widget

    def get_widget(self, name: str) -> Optional[Any]:
        """Get a registered widget"""
        return self._shared_widgets.get(name)

    def cleanup(self):
        """Clean up all managed resources"""
        self.clear_all_timers()
        self._shared_widgets.clear()


class CommonWidgetFactory:
    """
    Factory class to create commonly used widget patterns
    """
    
    @staticmethod
    def create_circular_metric(
        name: str,
        icon_markup: str,
        size: int = 28,
        line_width: int = 2,
        start_angle: int = 150,
        end_angle: int = 390,
        style_classes: Optional[str] = None
    ):
        """Create a standardized circular metric widget"""
        icon = Label(name="metrics-icon", markup=icon_markup)
        circle = CircularProgressBar(
            name="metrics-circle",
            value=0,
            size=size,
            line_width=line_width,
            start_angle=start_angle,
            end_angle=end_angle,
            style_classes=style_classes,
            child=icon,
        )
        
        level = Label(
            name="metrics-level", 
            style_classes=style_classes, 
            label="0%"
        )
        
        revealer = Revealer(
            name=f"metrics-{name}-revealer",
            transition_duration=250,
            transition_type="slide-left",
            child=level,
            child_revealed=False,
        )
        
        widget_box = Box(
            name=f"metrics-{name}-box",
            orientation="h",
            spacing=0,
            children=[circle, revealer],
        )
        
        return {
            'circle': circle,
            'level': level,
            'revealer': revealer,
            'box': widget_box,
            'icon': icon
        }

    @staticmethod
    def create_scaled_metric(
        name: str,
        icon_markup: str,
        orientation: str = 'v',
        inverted: bool = True
    ):
        """Create a standardized scaled metric widget"""
        scale_widget = Scale(
            name=f"{name}-usage",
            value=0.25,
            orientation=orientation,
            inverted=inverted,
            v_align='fill',
            v_expand=True,
        )

        label = Label(
            name=f"{name}-label",
            markup=icon_markup,
        )

        box = Box(
            name=f"{name}-box",
            orientation='v',
            spacing=8,
            children=[
                scale_widget,
                label,
            ]
        )

        box.set_tooltip_markup(f"{icon_markup} {name}")
        
        return {
            'scale': scale_widget,
            'label': label,
            'box': box
        }

    @staticmethod
    def create_revealer_button(
        child_widget: Any,
        name: str = "revealer-button",
        reveal_on_hover: bool = True,
        transition_type: str = "slide-left",
        transition_duration: int = 250
    ):
        """Create a button with integrated revealer functionality"""
        revealer = Revealer(
            name=f"{name}-revealer",
            transition_duration=transition_duration,
            transition_type=transition_type,
            child_revealed=False,
        )
        
        button = Button(
            name=name,
            child=Box(children=[child_widget, revealer])
        )
        
        if reveal_on_hover:
            button.connect("enter-notify-event", lambda w, e: revealer.set_reveal_child(True))
            button.connect("leave-notify-event", lambda w, e: revealer.set_reveal_child(False))
        
        return {
            'button': button,
            'revealer': revealer
        }


class EventManager:
    """
    Centralized event management to prevent duplicate event handlers
    """
    
    def __init__(self):
        self._handlers: Dict[str, List] = {}
        self._timers: Set[int] = set()

    def connect_multiple_events(self, widget: Any, events: Dict[str, Callable]):
        """Connect multiple events to a single widget efficiently"""
        handler_ids = []
        for event_name, callback in events.items():
            handler_id = widget.connect(event_name, callback)
            handler_ids.append(handler_id)
        
        widget_id = id(widget)
        self._handlers[widget_id] = handler_ids
        return handler_ids

    def disconnect_widget_events(self, widget: Any):
        """Disconnect all events for a specific widget"""
        widget_id = id(widget)
        if widget_id in self._handlers:
            for handler_id in self._handlers[widget_id]:
                try:
                    widget.disconnect(handler_id)
                except Exception:
                    pass  # Handler already disconnected
            del self._handlers[widget_id]

    def add_managed_timer(self, timeout_ms: int, callback: Callable, *args) -> int:
        """Add a timer that will be managed centrally"""
        timer_id = GLib.timeout_add(timeout_ms, callback, *args)
        self._timers.add(timer_id)
        return timer_id

    def remove_managed_timer(self, timer_id: int):
        """Remove a managed timer"""
        try:
            GLib.source_remove(timer_id)
        except Exception:
            pass  # Timer already removed
        self._timers.discard(timer_id)

    def cleanup(self):
        """Clean up all managed events and timers"""
        for widget_id, handler_ids in self._handlers.items():
            for handler_id in handler_ids:
                try:
                    # We can't easily get the widget back from ID, so we skip disconnecting
                    pass
        self._handlers.clear()
        
        for timer_id in self._timers:
            try:
                GLib.source_remove(timer_id)
            except Exception:
                pass
        self._timers.clear()


class SafeDestroyMixin:
    """
    Mixin class providing safe destruction methods
    """
    
    def safe_destroy_widget(self, widget: Any):
        """Safely destroy a widget with error handling"""
        if widget and hasattr(widget, 'destroy'):
            try:
                widget.destroy()
            except Exception:
                pass

    def safe_disconnect_signal(self, obj: Any, handler_id: int):
        """Safely disconnect a signal handler"""
        if obj:
            try:
                obj.disconnect(handler_id)
            except Exception:
                pass  # Handler already disconnected

    def safe_call_method(self, obj: Any, method_name: str, *args, **kwargs):
        """Safely call a method on an object"""
        if obj and hasattr(obj, method_name):
            try:
                method = getattr(obj, method_name)
                return method(*args, **kwargs)
            except Exception:
                return None
        return None


class CursorManager:
    """
    Centralized cursor management to prevent duplicate cursor operations
    """
    
    @staticmethod
    def setup_cursor_on_hover(widget: Button, cursor_name: str = "hand2"):
        """Setup cursor change on hover for a button"""
        def on_enter(_, event):
            if hasattr(widget, 'get_window') and widget.get_window():
                widget.get_window().set_cursor(
                    Gdk.Cursor.new_from_name(widget.get_display(), cursor_name)
                )
        
        def on_leave(_, event):
            if hasattr(widget, 'get_window') and widget.get_window():
                widget.get_window().set_cursor(None)
        
        widget.connect("enter-notify-event", on_enter)
        widget.connect("leave-notify-event", on_leave)


class ResourceTracker:
    """
    Track and manage widget lifecycles to prevent memory leaks
    """
    
    def __init__(self):
        self._tracked_widgets = weakref.WeakSet()
        self._tracked_components = {}

    def track_widget(self, widget: Any, name: str = None):
        """Track a widget for lifecycle management"""
        self._tracked_widgets.add(widget)
        if name:
            self._tracked_components[name] = widget

    def untrack_widget(self, widget: Any):
        """Stop tracking a widget"""
        if widget in self._tracked_widgets:
            self._tracked_widgets.discard(widget)
            
        # Also remove from named components
        for name, tracked_widget in list(self._tracked_components.items()):
            if tracked_widget is widget:
                del self._tracked_components[name]
                break

    def get_tracked_widgets(self) -> List[Any]:
        """Get list of currently tracked widgets"""
        return list(self._tracked_widgets)

    def cleanup_all(self):
        """Clean up all tracked resources"""
        self._tracked_widgets.clear()
        self._tracked_components.clear()


# Global instances for sharing across modules
central_resources = CentralizedResources()
common_factory = CommonWidgetFactory()
event_manager = EventManager()
resource_tracker = ResourceTracker()