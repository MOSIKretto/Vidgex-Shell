import sys
import signal
import threading
import setproctitle
from fabric import Application
from fabric.utils import get_relative_path
from gi.repository import GLib

from modules.notifications import Notification
from modules.notch import Notch
from modules.bar import Bar
from modules.corners import Corners
from modules.dock import Dock
from modules.explorer import Explorer
from modules.Notch.sessionManager import SessionManager


session_manager: SessionManager = None

def run():
    global session_manager

    setproctitle.setproctitle("vidgex-shell")

    session_manager = SessionManager()

    bar = Bar()
    notch = Notch()
    dock = Dock()
    corners = Corners()
    explorer = Explorer()

    bar.notch = notch
    notch.bar = bar

    widgets = getattr(getattr(notch, 'dashboard', None), 'widgets', None)
    notification = Notification(widgets=widgets)

    app_widgets = [bar, notch, dock, corners, notification, explorer]

    app = Application("vidgex-shell", *app_widgets)
    css_path = get_relative_path("main.css")
    app.set_stylesheet_from_file(css_path)
    app.set_css = lambda: app.set_stylesheet_from_file(css_path)

    def restore_in_background():
        session_manager.restore()
        GLib.idle_add(on_session_restored)

    def on_session_restored():
        session_manager.start_autosave(5)
        return False

    restore_thread = threading.Thread(
        target=restore_in_background,
        name="session-restore",
        daemon=True,
    )
    restore_thread.start()

    def on_shutdown(sig, frame):
        if session_manager:
            session_manager.stop_autosave()
            session_manager.save()
        app.quit()

    signal.signal(signal.SIGINT, on_shutdown)
    signal.signal(signal.SIGTERM, on_shutdown)

    import __main__ as main_module
    main_module.app = app
    main_module.notch = notch
    main_module.explorer = explorer

    return app.run()


if __name__ == "__main__":
    sys.exit(run())