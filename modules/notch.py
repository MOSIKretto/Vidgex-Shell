import json
import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from fabric.hyprland.widgets import get_hyprland_connection
from fabric.utils import get_desktop_applications
from fabric.widgets.box import Box
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer
from fabric.widgets.stack import Stack

from modules.Notch.mainWindow import MainWindow
from modules.Notch.notificationsPopup import NotificationsPopup
from modules.Notch.controlsOsd import ControlOSD

from services.corners import MyCorner
from services.wayland import WaylandWindow as Window


APPLET_MAP = {
    "network_applet": "network_connections",
    "bluetooth":      "bluetooth",
    "dashboard":      "notification_history",
}

_app_map: dict = {}
_theme = Gtk.IconTheme.get_default()

for _app in get_desktop_applications():
    for _k in filter(None, (_app.name, _app.display_name)):
        _app_map[_k.lower()] = _app
        _app_map[_k.lower().strip().rsplit(".", 1)[-1]] = _app


def _icon(cls: str, size: int = 20):
    key = cls.lower()
    app = _app_map.get(key) or _app_map.get(key.strip().rsplit(".", 1)[-1])
    if app:
        return app.get_icon_pixbuf(size=size)
    for name in dict.fromkeys((cls, key)):
        try:
            return _theme.load_icon(name, size, Gtk.IconLookupFlags.FORCE_SIZE)
        except GLib.Error:
            continue
    return None


class Notch(Window):
    def __init__(self):
        super().__init__(anchor="top", margin="-36px 0px 0px 0px", monitor=0)

        self._cw: str | None = None
        self._cht: int | None = None
        self._last_win: tuple = (None, None)
        self._conn = get_hyprland_connection()
        self._fetching = False
        self._fetch_pending = False
        self._destroyed = False
        self._init = False

        self._build()
        self._bind()
        self.connect("destroy", self._cleanup)
        GLib.idle_add(self._final)

    def _build(self) -> None:
        self.win_ic = Image(
            name="notch-window-icon",
            icon_name="application-x-executable",
            icon_size=20,
        )
        self.ws_lbl = Label(name="workspace-label", label="Workspace 1")
        self.awc = Box(
            name="active-window-container",
            spacing=8,
            v_align="center",
            children=[self.win_ic, self.ws_lbl],
        )

        self.ctrl_osd = ControlOSD(on_changed=self._on_ctrl_changed)

        self.cs = Stack(
            name="notch-compact-stack",
            transition_type="slide-up-down",
            transition_duration=220,
        )
        self.cs.set_interpolate_size(True)
        self.cs.add_named(
            Box(name="active-window-box", h_align="center", v_align="center", children=[self.awc]),
            "window",
        )
        self.cs.add_named(self.ctrl_osd, "control")

        self.compact = Gtk.EventBox(name="notch-compact", visible=True)
        self.compact.add(
            Box(name="compact-content", h_align="center", v_align="center",
                children=[self.cs])
        )
        self.compact.set_size_request(290, 36)

        self.main_window = MainWindow(notch=self)

        self.notif_popup = NotificationsPopup()
        self.notif_popup.set_handlers(
            is_blocked=lambda: self.notifications_blocked,
            on_show=self.open_notification,
            on_hide=self.close_notification,
        )
        self.main_window.set_size_request(1093, 472)
        self.notif_popup.set_size_request(360, -1)

        self.stack = Stack(
            name="notch-content",
            transition_type="crossfade",
            transition_duration=200,
        )
        self.stack.add_named(self.compact, "compact")
        self.stack.add_named(self.main_window, "main_window")
        self.stack.add_named(self.notif_popup, "notification")
        for s in ("panel", "bottom", "Top"):
            self.stack.add_style_class(s)
        self.stack.set_interpolate_size(True)
        self.stack.set_homogeneous(False)

        self.nb = CenterBox(
            name="notch-box",
            start_children=Box(
                name="notch-corner-left", orientation="v", h_align="start",
                children=[MyCorner("top-right")],
            ),
            center_children=self.stack,
            end_children=Box(
                name="notch-corner-right", orientation="v", h_align="end",
                children=[MyCorner("top-left")],
            ),
        )
        self.nb.add_style_class("notch")

        revealer = Revealer(name="notch-revealer", child_revealed=True, child=self.nb)
        revealer.set_size_request(-1, 1)

        hover_box = Gtk.EventBox(
            name="notch-hover-eventbox", visible=True,
            halign=Gtk.Align.CENTER, valign=Gtk.Align.START,
        )
        hover_box.add(Box(name="notch-complete", children=[revealer]))
        hover_box.set_size_request(-1, 4)

        root_box = Box(
            name="notch-root-container", orientation="h",
            h_align="center", v_align="start", spacing=0,
        )
        root_box.add(self.notif_popup.side_left)
        root_box.add(hover_box)
        root_box.add(self.notif_popup.side_right)
        self.add(root_box)

    def _bind(self) -> None:
        self.compact.connect(
            "button-press-event",
            lambda *_: self.toggle_notch("dashboard") or True,
        )
        self._hypr_handler_id = self._conn.connect(
            "event", lambda *_: self._schedule_updwin()
        )

    def _cleanup(self, *_) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        self._cancel_osd_timer()
        self._conn.disconnect(self._hypr_handler_id)

    @property
    def notifications_blocked(self) -> bool:
        return self._cw not in (None, "notification")

    def _cancel_osd_timer(self) -> None:
        if self._cht is not None:
            GLib.source_remove(self._cht)
            self._cht = None

    def _on_ctrl_changed(self) -> None:
        if self._destroyed or not self._init or self._cw:
            return
        self._cancel_osd_timer()
        self.cs.set_visible_child_name("control")
        self._cht = GLib.timeout_add(2200, self._reset_compact_stack)

    def _reset_compact_stack(self) -> bool:
        self._cht = None
        if not self._destroyed and not self._cw:
            self.cs.set_visible_child_name("window")
        return False

    def _final(self) -> bool:
        if self._destroyed:
            return False
        self.show_all()
        self._schedule_updwin()
        self._init = True
        return False

    def _open_panel(self) -> None:
        self._cancel_osd_timer()
        self.cs.set_visible_child_name("window")
        self.nb.add_style_class("open")
        self.stack.add_style_class("open")

    def _close_panel(self) -> None:
        self.nb.remove_style_class("open")
        self.stack.remove_style_class("open")
        self._cw = None
        self.stack.set_visible_child(self.compact)
        self._schedule_updwin()

    def open_notification(self) -> None:
        self._open_panel()
        self._cw = "notification"
        self.stack.set_visible_child(self.notif_popup)

    def close_notification(self) -> None:
        if self._cw != "notification":
            return
        self._close_panel()

    def toggle_notch(self, name: str) -> None:
        if self._cw == name:
            self.close_notch()
            return

        self._open_panel()
        self.keyboard_mode = "exclusive"
        self.stack.set_visible_child(self.main_window)
        if name in APPLET_MAP:
            self.main_window.go_to_section("dashboard")
            dashboard = self.main_window.dashboard
            dashboard.applet_stack.set_visible_child(getattr(dashboard, APPLET_MAP[name]))
        else:
            self.main_window.go_to_section(name)
        self._cw = name

    def close_notch(self) -> None:
        self.keyboard_mode = "none"
        self._close_panel()

    def _schedule_updwin(self) -> None:
        if self._destroyed or self._cw:
            return
        if self._fetching:
            self._fetch_pending = True
            return
        self._fetching = True
        threading.Thread(target=self._fetch_win_info, daemon=True).start()

    def _fetch_win_info(self) -> None:
        result = None
        try:
            ws = json.loads(self._conn.send_command("j/activeworkspace").reply.decode())["id"]
            win = json.loads(self._conn.send_command("j/activewindow").reply.decode())
            result = (ws, win.get("class", ""))
        except (OSError, ValueError):
            pass
        finally:
            GLib.idle_add(self._on_fetch_done, result)

    def _on_fetch_done(self, result: tuple | None) -> bool:
        self._fetching = False
        if self._destroyed:
            return False

        if self._fetch_pending:
            self._fetch_pending = False
            self._schedule_updwin()
            return False

        if self._cw or result is None or result == self._last_win:
            return False
        self._last_win = result

        ws, wc = result
        self.ws_lbl.set_label(f"Workspace {ws}")
        if wc.strip():
            px = _icon(wc)
            if px:
                self.win_ic.set_from_pixbuf(px)
            else:
                self.win_ic.set_from_icon_name("application-x-executable-symbolic", 20)
            self.win_ic.show()
            self.awc.set_spacing(8)
        else:
            self.win_ic.hide()
            self.awc.set_spacing(0)

        return False