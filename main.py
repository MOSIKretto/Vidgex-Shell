import sys, signal, weakref
import setproctitle

from gi.repository import GLib

from fabric import Application
from fabric.utils import get_relative_path

from modules.notch import Notch
from modules.bar import Bar
from modules.dock import Dock
from modules.corners import Corners

from services.session import SessionManager


setproctitle.setproctitle("vidgex-shell")


bar = Bar()
notch = Notch()
dock = Dock()
corners = Corners()

bar.notch = notch
notch.bar = weakref.ref(bar)

app_widgets = [bar, notch, dock, corners]

app = Application("vidgex-shell", *app_widgets)
css_path = get_relative_path("main.css")
app.set_stylesheet_from_file(css_path)
app.set_css = lambda: app.set_stylesheet_from_file(css_path)

session = SessionManager()


def _autosave() -> bool:
    session.save_all()
    return True


def _quit(*_):
    app.quit()
    return GLib.SOURCE_REMOVE


def run():
    session.restore()

    autosave_id = GLib.timeout_add_seconds(5, _autosave)

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, _quit)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, _quit)

    try:
        return app.run()
    finally:
        GLib.source_remove(autosave_id)
        session.save_all()


if __name__ == "__main__":
    sys.exit(run())