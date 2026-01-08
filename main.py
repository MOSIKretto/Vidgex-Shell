import sys
import signal
import atexit
import setproctitle
from typing import Dict, List, Optional, Tuple

import gi
gi.require_version("GLib", "2.0")

from fabric import Application
from fabric.utils import get_relative_path

from modules.Panel.Dashboard_Bar.Widgets.notifications import NotificationPopup
from modules.Panel.notch import Notch
from modules.Panel.Dashboard_Bar.bar import Bar
from widgets.corners import Corners
from modules.Dock.dock import Dock


APP_NAME = "vidgex-shell"
CSS_PATH = get_relative_path("main.css")

class ShellManager:
    __slots__ = ('app', 'components', 'cleanup_handlers', '_cleaned_up')

    def __init__(self):
        self.app: Optional[Application] = None
        self.components: Dict[int, Dict] = {}
        self.cleanup_handlers: List[callable] = []
        self._cleaned_up: bool = False

    def register_cleanup(self, handler: callable) -> None:
        self.cleanup_handlers.append(handler)

    def cleanup(self) -> None:
        if self._cleaned_up:
            return
        self._cleaned_up = True

        for handler in reversed(self.cleanup_handlers):
            try:
                handler()
            except Exception:
                pass
        self.cleanup_handlers.clear()

        for instances in self.components.values():
            bar = instances.get('bar')
            notch = instances.get('notch')
            
            if bar and hasattr(bar, 'notch'):
                bar.notch = None
            if notch and hasattr(notch, 'bar'):
                notch.bar = None

        for monitor_id, instances in list(self.components.items()):
            for component_name, component in list(instances.items()):
                if component and hasattr(component, 'destroy'):
                    try:
                        component.destroy()
                    except Exception:
                        pass

                instances[component_name] = None
            self.components[monitor_id] = {}

        self.components.clear()

        if self.app:
            try:
                self.app.quit()
            except Exception:
                pass
            self.app = None

    def setup_monitors(self) -> Tuple[Optional[object], bool, List[Dict]]:
        try:
            from utils.monitor_manager import get_monitor_manager
            manager = get_monitor_manager()
            monitors = manager.get_monitors() or [{'id': 0, 'name': 'default'}]
            return manager, len(monitors) > 1, monitors
        except (ImportError, RuntimeError):
            return None, False, [{'id': 0, 'name': 'default'}]

    def create_components(self, monitor_id: int, multi_monitor: bool, app_widgets: List, monitor_manager: Optional[object]) -> Dict:
        
        args = (monitor_id,) if multi_monitor else ()
        
        bar = Bar(*args)
        notch = Notch(*args)
        dock = Dock(*args)
        
        instances = {
            'bar': bar,
            'notch': notch,
            'dock': dock
        }

        bar.notch = notch
        notch.bar = bar

        if monitor_id == 0:
            corners = Corners()

            widgets = getattr(getattr(notch, 'dashboard', None), 'widgets', None)
            notification = NotificationPopup(widgets=widgets)
            
            instances['corners'] = corners
            instances['notification'] = notification
            
            app_widgets.append(corners)
            app_widgets.append(notification)

        if multi_monitor and monitor_manager:
            monitor_manager.register_monitor_instances(
                monitor_id,
                {k: v for k, v in instances.items() if k != 'notification'}
            )

        app_widgets.extend((bar, notch, dock))
        return instances

    def run(self) -> int:
        setproctitle.setproctitle(APP_NAME)
        
        monitor_manager, multi_monitor, monitors = self.setup_monitors()
        app_widgets = []
        
        for monitor in monitors:
            m_id = monitor['id']
            self.components[m_id] = self.create_components(
                m_id, multi_monitor, app_widgets, monitor_manager
            )

        self.app = Application(APP_NAME, *app_widgets)
        self.app.set_stylesheet_from_file(CSS_PATH)
        self.app.set_css = lambda: self.app.set_stylesheet_from_file(CSS_PATH)

        self.register_cleanup(self.cleanup)
        atexit.register(self.cleanup)
        
        def signal_handler(signum, frame):
            self.cleanup()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        import __main__ as main_module
        main_module.app = self.app
        
        all_notches = [inst['notch'] for inst in self.components.values() if 'notch' in inst]
        if all_notches:
            main_module.notch = all_notches[0]
            if len(all_notches) > 1:
                main_module.notches = all_notches

        return self.app.run()

if __name__ == "__main__":
    manager = ShellManager()
    try:
        sys.exit(manager.run())
    except Exception:
        manager.cleanup()
        sys.exit(1)