import sys, signal, threading

if len(sys.argv) > 1 and sys.argv[1] == "--canvas-toggle":
    from modules.Desktop.infinite_desktop import toggle_mode
    toggle_mode()
    sys.exit(0)

import setproctitle

from fabric import Application
from fabric.utils import get_relative_path

from modules.notch import Notch
from modules.bar import Bar
from modules.dock import Dock
from modules.corners import Corners

from modules.Dock.SessionManager.restore import SessionManager
from modules.Desktop.infinite_desktop import start_canvas_daemon


def run():
    setproctitle.setproctitle("vidgex-shell")

    session_manager = SessionManager()

    bar = Bar()
    notch = Notch()
    dock = Dock(session_manager=session_manager)
    corners = Corners()

    bar.notch = notch
    notch.bar = bar

    app_widgets = [bar, notch, dock, corners]

    app = Application("vidgex-shell", *app_widgets)
    css_path = get_relative_path("main.css")
    app.set_stylesheet_from_file(css_path)
    app.set_css = lambda: app.set_stylesheet_from_file(css_path)

    start_canvas_daemon()

    restore_thread = threading.Thread(
        target=session_manager.restore,
        name="session-restore",
        daemon=True,
    )
    restore_thread.start()

    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal.signal(signal.SIGTERM, lambda *_: app.quit())

    import __main__ as main_module
    main_module.app = app
    main_module.notch = notch

    return app.run()


if __name__ == "__main__":
    sys.exit(run())