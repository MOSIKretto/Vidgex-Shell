import sys
import signal
import atexit
import setproctitle
from typing import Dict, List, Optional, Tuple, Callable, Any

from fabric import Application
from fabric.utils import get_relative_path

from modules.Notch.Widgets.Notifications.popup import NotificationPopup
from modules.notch import Notch
from modules.bar import Bar
from widgets.corners import Corners
from modules.dock import Dock

# Import cleanup functions
from modules.metrics import cleanup_shared_provider
from utils.common import global_cleanup


class ShellManager:
    __slots__ = ('app', 'components', 'cleanup_handlers', '_cleaned_up')

    def __init__(self):
        self.app: Optional[Application] = None
        self.components: Dict[int, Dict[str, Any]] = {}
        self.cleanup_handlers: List[Callable[[], None]] = []
        self._cleaned_up: bool = False

    def register_cleanup(self, handler: Callable[[], None]) -> None:
        self.cleanup_handlers.append(handler)

    def cleanup(self) -> None:
        if self._cleaned_up:
            return
        self._cleaned_up = True

        # Выполняем обработчики очистки в обратном порядке
        for handler in reversed(self.cleanup_handlers):
            try:
                handler()
            except Exception:
                pass
        self.cleanup_handlers.clear()

        # Отвязываем компоненты друг от друга
        for instances in self.components.values():
            bar = instances.get('bar')
            notch = instances.get('notch')
            
            if bar is not None:
                bar.notch = None
            if notch is not None:
                notch.bar = None

        # Уничтожаем компоненты
        for instances in self.components.values():
            for component in instances.values():
                if component is not None and hasattr(component, 'destroy'):
                    try:
                        component.destroy()
                    except Exception:
                        pass
        
        # Очищаем словарь компонентов
        self.components.clear()

        # Завершаем приложение
        if self.app:
            try:
                self.app.quit()
            except Exception:
                pass
            self.app = None

        # Clean up shared resources
        try:
            cleanup_shared_provider()
        except Exception:
            pass
        
        # Run global cleanup
        try:
            global_cleanup()
        except Exception:
            pass

    def setup_monitors(self) -> Tuple[Optional[Any], bool, List[Dict[str, Any]]]:
        try:
            from utils.monitor_manager import get_monitor_manager
            manager = get_monitor_manager()
            monitors = manager.get_monitors() or [{'id': 0, 'name': 'default'}]
            return manager, len(monitors) > 1, monitors
        except (ImportError, RuntimeError):
            return None, False, [{'id': 0, 'name': 'default'}]

    def create_components(self, monitor_id: int, multi_monitor: bool, app_widgets: List[Any], monitor_manager: Optional[Any]) -> Dict[str, Any]:
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
        setproctitle.setproctitle("vidgex-shell")
        
        monitor_manager, multi_monitor, monitors = self.setup_monitors()
        app_widgets = []
        
        for monitor in monitors:
            m_id = monitor['id']
            self.components[m_id] = self.create_components(
                m_id, multi_monitor, app_widgets, monitor_manager
            )

        self.app = Application("vidgex-shell", *app_widgets)
        css_path = get_relative_path("main.css")
        self.app.set_stylesheet_from_file(css_path)
        self.app.set_css = lambda: self.app.set_stylesheet_from_file(css_path)

        self.register_cleanup(self.cleanup)
        atexit.register(self.cleanup)
        
        def signal_handler(signum, frame):
            self.cleanup()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        import __main__ as main_module
        main_module.app = self.app
        
        all_notches = [inst['notch'] for inst in self.components.values() if 'notch' in inst and inst['notch'] is not None]
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