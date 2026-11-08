import json

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from fabric.hyprland.widgets import get_hyprland_connection
from fabric.widgets.box import Box
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer
from fabric.widgets.stack import Stack

from modules.Notch.mainWindow import MainWindow
from modules.Notch.notificationsPopup import NotificationsPopup
from modules.Notch.controlsOsd import ControlOSD

from services.getIcons import resolve_app_pixbuf
from services.corners import MyCorner
from services.wayland import WaylandWindow as Window


APPLET_MAP = {
    "network_applet": "network_connections",
    "bluetooth":      "bluetooth",
    "dashboard":      "notification_history",
}


class Notch(Window):
    def __init__(self):
        super().__init__(anchor="top", margin="-36px 0px 0px 0px", monitor=0)

        self._cw: str | None = None
        self._last_win: tuple = (None, None)
        self._init = False
        self._osd_tick = None
        self._osd_t0 = None

        self._build()

        conn = get_hyprland_connection()
        ids = [
            conn.connect(f"event::{name}", self._refresh)
            for name in ("workspace", "activewindow", "closewindow", "focusedmon")
        ]
        self.connect("destroy", lambda _: [conn.disconnect(i) for i in ids])
        self.compact.connect(
            "button-press-event",
            lambda *_: self.toggle_notch("dashboard") or True,
        )
        self._conn = conn

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
            Box(name="compact-content", h_align="center", v_align="center", children=[self.cs])
        )
        self.compact.set_size_request(290, 36)

        self.main_window = MainWindow(notch=self)
        self.main_window.set_size_request(1093, 472)

        self.notif_popup = NotificationsPopup()
        self.notif_popup.set_handlers(
            is_blocked=lambda: self.notifications_blocked,
            on_show=self.open_notification,
            on_hide=self.close_notification,
        )
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
            children=[self.notif_popup.side_left, hover_box, self.notif_popup.side_right],
        )
        self.add(root_box)

    @property
    def notifications_blocked(self) -> bool:
        return self._cw not in (None, "notification")

    def _final(self) -> bool:
        self.show_all()
        self._refresh()
        self._init = True
        return False

    def _on_ctrl_changed(self) -> None:
        if not self._init or self._cw:
            return
        self.cs.set_visible_child_name("control")
        self._osd_t0 = None
        if self._osd_tick is None:
            self._osd_tick = self.cs.add_tick_callback(self._osd_step)

    def _osd_step(self, _, clock) -> bool:
        now = clock.get_frame_time()
        self._osd_t0 = self._osd_t0 or now
        if now - self._osd_t0 < 2_200_000:
            return True
        self._osd_tick = None
        self.cs.set_visible_child_name("window")
        return False

    def _stop_osd(self) -> None:
        if self._osd_tick is not None:
            self.cs.remove_tick_callback(self._osd_tick)
            self._osd_tick = None

    def _open_panel(self) -> None:
        self._stop_osd()
        self.cs.set_visible_child_name("window")
        self.nb.add_style_class("open")
        self.stack.add_style_class("open")

    def _close_panel(self) -> None:
        self.nb.remove_style_class("open")
        self.stack.remove_style_class("open")
        self._cw = None
        self.stack.set_visible_child(self.compact)
        self._refresh()

    def open_notification(self) -> None:
        self._open_panel()
        self._cw = "notification"
        self.stack.set_visible_child(self.notif_popup)

    def close_notification(self) -> None:
        if self._cw == "notification":
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

    def _refresh(self, *_) -> None:
        if self._cw:
            return
        try:
            ws = json.loads(self._conn.send_command("j/activeworkspace").reply)["id"]
            win = json.loads(self._conn.send_command("j/activewindow").reply)
        except (OSError, ValueError, KeyError):
            return

        result = (ws, win.get("class") or win.get("initialClass") or "")
        if result == self._last_win:
            return
        self._last_win = result

        self.ws_lbl.set_label(f"Workspace {ws}")
        wc = result[1].strip()
        if not wc:
            self.win_ic.hide()
            self.awc.set_spacing(0)
            return

        px = resolve_app_pixbuf(wc, 20)
        if px:
            self.win_ic.set_from_pixbuf(px)
        else:
            self.win_ic.set_from_icon_name("application-x-executable-symbolic", 20)
        self.win_ic.show()
        self.awc.set_spacing(8)