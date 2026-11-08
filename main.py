import sys, signal, weakref
import setproctitle

from gi.repository import GLib

from fabric import Application
from fabric.hyprland.service import Hyprland
from fabric.utils import get_relative_path

from modules.notch import Notch
from modules.bar import Bar
from modules.dock import Dock

from services.corners import Corners
from services.Notifications.notificationServer import NotificationServer
from services.session import SessionManager, AppResolver


setproctitle.setproctitle("vidgex-shell")

notification_server = NotificationServer()
bar = Bar()
notch = Notch()
dock = Dock()

bar.notch = notch
notch.bar = weakref.ref(bar)

app = Application("vidgex-shell", bar, notch, dock, Corners())
css_path = get_relative_path("main.css")
app.set_stylesheet_from_file(css_path)
app.set_css = lambda: app.set_stylesheet_from_file(css_path)

session = SessionManager(resolver=AppResolver())


def run():
    session.restore()

    hypr = Hyprland()
    for event in ("openwindow", "closewindow", "movewindow"):
        hypr.connect(f"event::{event}", lambda *_: session.save_all())

    for sig in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda *_: app.quit())

    try:
        return app.run()
    finally:
        session.save_all()
        notification_server.cleanup()


if __name__ == "__main__":
    sys.exit(run())