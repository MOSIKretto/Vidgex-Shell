import sys
import signal
import setproctitle
from fabric import Application
from fabric.utils import get_relative_path

from modules.Notch.Widgets.Notifications.popup import NotificationPopup
from modules.notch import Notch
from modules.bar import Bar
from widgets.corners import Corners
from modules.dock import Dock


def run():
    setproctitle.setproctitle("vidgex-shell")

    bar = Bar()
    notch = Notch()
    dock = Dock()
    corners = Corners()
    
    bar.notch = notch
    notch.bar = bar
    
    widgets = getattr(getattr(notch, 'dashboard', None), 'widgets', None)
    notification = NotificationPopup(widgets=widgets)

    app_widgets = [bar, notch, dock, corners, notification]

    app = Application("vidgex-shell", *app_widgets)
    css_path = get_relative_path("main.css")
    app.set_stylesheet_from_file(css_path)
    app.set_css = lambda: app.set_stylesheet_from_file(css_path)
    
    signal.signal(signal.SIGINT, lambda s, f: app.quit())
    signal.signal(signal.SIGTERM, lambda s, f: app.quit())
    
    import __main__ as main_module
    main_module.app = app
    main_module.notch = notch

    return app.run()


if __name__ == "__main__":
    sys.exit(run())