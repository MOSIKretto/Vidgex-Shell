import sys
import signal
import atexit
import setproctitle

import gi
gi.require_version("GLib", "2.0")
from gi.repository import GLib

from fabric import Application
from fabric.utils import get_relative_path

from modules.bar import Bar
from modules.corners import Corners
from modules.dock import Dock
from modules.notch import Notch
from modules.notifications import NotificationPopup


APP_NAME_CAP = "Ax-Shell"
APP_NAME = APP_NAME_CAP.lower()
CACHE_DIR = GLib.get_user_cache_dir() + f"/{APP_NAME}"

_app_instance = None
_cleanup_handlers = []


def cleanup_resources():
    global _cleanup_handlers, _app_instance

    for handler in _cleanup_handlers:
        handler()

    _cleanup_handlers = []

    if _app_instance:
        try:
            _app_instance.quit()
        except Exception:
            pass
        _app_instance = None


def signal_handler(signum, frame):
    cleanup_resources()
    sys.exit(0)


def register_cleanup(handler):
    global _cleanup_handlers
    if handler not in _cleanup_handlers:
        _cleanup_handlers.append(handler)


def setup_monitors():
    try:
        from utils.monitor_manager import get_monitor_manager
        monitor_manager = get_monitor_manager()
        all_monitors = monitor_manager.get_monitors()
        multi_monitor_enabled = True
        monitors = all_monitors
        if not monitors:
            monitors = [{'id': 0, 'name': 'default'}]
    except (ImportError, RuntimeError):
        monitors = [{'id': 0, 'name': 'default'}]
        monitor_manager = None
        multi_monitor_enabled = False

    return monitor_manager, multi_monitor_enabled, monitors


def create_monitor_components(monitor_id, multi_monitor_enabled, app_components, monitor_manager):
    instances = {}

    if monitor_id == 0:
        corners = Corners()
        app_components.append(corners)
        instances['corners'] = corners

    if multi_monitor_enabled:
        bar = Bar(monitor_id=monitor_id)
        notch = Notch(monitor_id=monitor_id)
        dock = Dock(monitor_id=monitor_id)
    else:
        bar = Bar()
        notch = Notch()
        dock = Dock()

    bar.notch = notch
    notch.bar = bar

    instances.update({
        'bar': bar,
        'notch': notch,
        'dock': dock
    })

    if monitor_id == 0:
        notification = NotificationPopup(widgets=notch.dashboard.widgets)
        app_components.append(notification)
        instances['notification'] = notification

    if multi_monitor_enabled and monitor_manager:
        monitor_manager.register_monitor_instances(
            monitor_id,
            {
                'bar': bar,
                'notch': notch,
                'dock': dock,
                'corners': instances.get('corners')
            }
        )

    app_components.extend([bar, notch, dock])

    return instances


def initialize_app():
    global _app_instance

    setproctitle.setproctitle(APP_NAME)

    monitor_manager, multi_monitor_enabled, monitors = setup_monitors()

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

    app = Application(f"{APP_NAME}", *app_components)
    _app_instance = app

    # Загрузка CSS‑стилей
    def set_css():
        app.set_stylesheet_from_file(get_relative_path("main.css"))

    app.set_css = set_css
    app.set_css()

    # Очистка компонентов при завершении
    def cleanup_components():
        for instances in component_instances.values():
            for component in instances.values():
                if component:
                    try:
                        if hasattr(component, 'destroy'):
                            component.destroy()
                        elif hasattr(component, 'cleanup'):
                            component.cleanup()
                    except Exception:
                        pass
        component_instances.clear()

    register_cleanup(cleanup_components)

    return app, component_instances

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    atexit.register(cleanup_resources)

    try:
        app, component_instances = initialize_app()

        import __main__
        notch = None
        for instances in component_instances.values():
            if 'notch' in instances and instances['notch']:
                notch = instances['notch']
                __main__.notch = notch
                break

        __main__.app = app

        all_notches = []
        for instances in component_instances.values():
            if 'notch' in instances and instances['notch']:
                all_notches.append(instances['notch'])

        if len(all_notches) > 1:
            __main__.notches = all_notches

        app.run()
    except Exception:
        cleanup_resources()
        sys.exit(1)