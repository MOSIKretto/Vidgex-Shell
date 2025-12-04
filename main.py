from fabric import Application
from fabric.utils import get_relative_path

import gi
gi.require_version("GLib", "2.0")
from gi.repository import GLib

import os
import sys
import signal
import atexit
import setproctitle
from typing import List, Optional, Dict, Any

# Lazy imports for heavy modules
def lazy_import_modules():
    """Lazy import heavy modules to improve startup time."""
    global Corners, Dock, Notch, Bar, NotificationPopup
    from modules.corners import Corners
    from modules.dock import Dock
    from modules.notch import Notch, Bar
    from modules.notifications import NotificationPopup

APP_NAME_CAP = "Ax-Shell"
APP_NAME = APP_NAME_CAP.lower()
CACHE_DIR = str(GLib.get_user_cache_dir()) + f"/{APP_NAME}"
CONFIG_DIR = os.path.expanduser(f"~/.config/{APP_NAME_CAP}")

fonts_updated_file = f"{CACHE_DIR}/fonts_updated"

# Global reference for cleanup
_app_instance: Optional[Application] = None
_cleanup_handlers: List[callable] = []


def cleanup_resources():
    """Clean up all resources before exit."""
    global _cleanup_handlers, _app_instance

    # Cleanup async subprocess thread pool first
    try:
        from utils.async_subprocess import shutdown
        shutdown()
    except Exception as e:
        print(f"Error shutting down async_subprocess: {e}", file=sys.stderr)

    # Run all registered cleanup handlers
    for handler in _cleanup_handlers:
        try:
            handler()
        except Exception as e:
            print(f"Error during cleanup: {e}", file=sys.stderr)

    _cleanup_handlers.clear()

    if _app_instance:
        try:
            _app_instance.quit()
        except:
            pass
        _app_instance = None

def signal_handler(signum, frame):
    """Handle termination signals gracefully."""
    cleanup_resources()
    sys.exit(0)

def register_cleanup(handler: callable):
    """Register a cleanup handler."""
    global _cleanup_handlers
    if handler not in _cleanup_handlers:
        _cleanup_handlers.append(handler)

def initialize_app():
    """Initialize the application with optimized resource loading."""
    global _app_instance
    
    # Lazy load heavy modules
    lazy_import_modules()
    
    setproctitle.setproctitle(APP_NAME)
    current_wallpaper = os.path.expanduser("~/.current.wall")
    
    # Initialize monitor configuration
    monitor_manager, multi_monitor_enabled, monitors = setup_monitors()
    
    # Create components with resource tracking
    app_components = []
    component_instances = {}
    
    for monitor in monitors:
        monitor_id = monitor['id']
        instances = create_monitor_components(
            monitor_id, 
            multi_monitor_enabled,
            app_components,
            monitor_manager
        )
        component_instances[monitor_id] = instances
    
    # Create application
    app = Application(f"{APP_NAME}", *app_components)
    _app_instance = app
    
    # Setup CSS
    def set_css():
        app.set_stylesheet_from_file(get_relative_path("main.css"))
    
    app.set_css = set_css
    app.set_css()
    
    # Register cleanup for all components
    def cleanup_components():
        for instances in component_instances.values():
            for component in instances.values():
                if component and hasattr(component, 'destroy'):
                    try:
                        component.destroy()
                    except:
                        pass
    
    register_cleanup(cleanup_components)
    
    # Return both app and component_instances for fabric-cli access
    return app, component_instances

def setup_monitors():
    """Setup monitor configuration with error handling."""
    try:
        from utils.monitor_manager import get_monitor_manager
        from services.monitor_focus import get_monitor_focus_service
        
        monitor_manager = get_monitor_manager()
        monitor_focus_service = get_monitor_focus_service()
        monitor_manager.set_monitor_focus_service(monitor_focus_service)
        
        all_monitors = monitor_manager.get_monitors()
        multi_monitor_enabled = True
        monitors = all_monitors
        
        if not monitors:
            print("Ax-Shell: No valid selected monitors found, falling back to default")
            monitors = [{'id': 0, 'name': 'default'}]
            
    except (ImportError, Exception) as e:
        print(f"Monitor setup failed: {e}, using default", file=sys.stderr)
        monitors = [{'id': 0, 'name': 'default'}]
        monitor_manager = None
        multi_monitor_enabled = False
    
    return monitor_manager, multi_monitor_enabled, monitors

def create_monitor_components(monitor_id: int, multi_monitor_enabled: bool, 
                             app_components: list, monitor_manager) -> Dict[str, Any]:
    """Create components for a specific monitor."""
    instances = {}
    
    # Create corners only for primary monitor
    if monitor_id == 0:
        corners = Corners()
        app_components.append(corners)
        instances['corners'] = corners
    
    # Create bar, notch, and dock
    if multi_monitor_enabled:
        bar = Bar(monitor_id=monitor_id)
        notch = Notch(monitor_id=monitor_id)
        dock = Dock(monitor_id=monitor_id)
    else:
        bar = Bar()
        notch = Notch()
        dock = Dock()
    
    # Setup cross-references (using weak references would be better)
    bar.notch = notch
    notch.bar = bar
    
    instances.update({
        'bar': bar,
        'notch': notch,
        'dock': dock
    })
    
    # Create notification popup for primary monitor
    if monitor_id == 0:
        notification = NotificationPopup(widgets=notch.dashboard.widgets)
        app_components.append(notification)
        instances['notification'] = notification
    
    # Register with monitor manager
    if multi_monitor_enabled and monitor_manager:
        monitor_manager.register_monitor_instances(monitor_id, {
            'bar': bar,
            'notch': notch,
            'dock': dock,
            'corners': instances.get('corners')
        })
    
    # Add main components to app
    app_components.extend([bar, notch, dock])
    
    return instances

if __name__ == "__main__":
    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    atexit.register(cleanup_resources)
    
    try:
        app, component_instances = initialize_app()
        
        # Make components accessible for fabric-cli commands
        # This is needed for keybindings to work
        import __main__
        
        # Find and expose the first notch instance globally
        notch = None
        for instances in component_instances.values():
            if 'notch' in instances and instances['notch']:
                notch = instances['notch']
                __main__.notch = notch
                break
        
        # Also expose app for CSS reload
        __main__.app = app
        
        # For backwards compatibility, if there are multiple monitors,
        # expose all notch instances
        all_notches = []
        for instances in component_instances.values():
            if 'notch' in instances and instances['notch']:
                all_notches.append(instances['notch'])
        
        if len(all_notches) > 1:
            __main__.notches = all_notches
        
        app.run()
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        cleanup_resources()
        sys.exit(1)
