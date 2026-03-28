import json

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from fabric.hyprland.widgets import get_hyprland_connection
from fabric.utils import get_desktop_applications
from fabric.widgets.box import Box
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer
from fabric.widgets.stack import Stack

from modules.Notch.mainWindow import MainWindow
from modules.Notch.clipHist import ClipHistory
from modules.Notch.appLauncher import AppLauncher
from modules.Notch.overview import Overview
from modules.Notch.powerMenu import PowerMenu
from modules.Notch.toolBox import ToolBox
from modules.Notch.MainWindow.Dashboard.controls import ControlSmall, get_audio
from modules.Notch.MainWindow.Dashboard.Controls.brightness import Brightness
from modules.corners import MyCorner
from services.wayland import WaylandWindow as Window


MARGIN = "-40px 0px 0px 0px"
ICON_SIZE = 20
SPACING = 8
COMPACT_WIDTH = 260
HOVER_HEIGHT = 4
TRANSITION_MS = 200
CTRL_HIDE_MS = 2000
VOLUME_THRESHOLD = 0.5
FALLBACK_ICON = "application-x-executable"
FALLBACK_ICON_SYM = "application-x-executable-symbolic"

WIDGET_REGISTRY = {
    "main_window": (MainWindow, (1093, 472)),
    "launcher":    (AppLauncher, (480, 244)),
    "cliphist":    (ClipHistory, (480, 244)),
    "overview":    (Overview, None),
    "power":       (PowerMenu, None),
    "tools":       (ToolBox, None),
}
NOTCH_INJECTED = {"main_window", "launcher", "power", "cliphist", "tools"}

APPLET_MAP = {
    "network_applet": "network_connections",
    "bluetooth": "bluetooth",
    "dashboard": "notification_history",
}
SIMPLE_VIEWS = {"overview", "power", "tools"}
DASHBOARD_SECTIONS = {"wallpapers", "player"}

_apps, _app_map, _theme = [], {}, Gtk.IconTheme.get_default()

def _norm(name):
    return name.lower().strip().rsplit(".", 1)[-1] if name else ""

def _refresh():
    global _apps
    _apps = get_desktop_applications()
    _app_map.clear()
    for app in _apps:
        for k in filter(None, (app.name, app.display_name)):
            _app_map.setdefault(k.lower(), app)
            _app_map.setdefault(_norm(k), app)

def _find(name):
    if not name:
        return None
    low, n = name.lower(), _norm(name)
    for k in (low, n):
        if k in _app_map:
            return _app_map[k]
    if "." in low:
        for seg in low.split("."):
            if seg in _app_map:
                return _app_map[seg]
    for app in _apps:
        an, ad = (app.name or "").lower(), (app.display_name or "").lower()
        if n in an or an in n or n in ad or ad in n:
            return app
    return None

def _icon(cls, size):
    app = _find(cls)
    if app and hasattr(app, "get_icon_pixbuf"):
        try:
            px = app.get_icon_pixbuf(size=size)
            if px:
                return px
        except Exception:
            pass
    for n in filter(None, (cls, _norm(cls), cls and cls.lower(), FALLBACK_ICON_SYM)):
        try:
            px = _theme.load_icon(n, size, Gtk.IconLookupFlags.FORCE_SIZE)
            if px:
                return px
        except Exception:
            pass
    return None

_refresh()


class Notch(Window):
    def __init__(self, **kwargs):
        self._bar = kwargs.get("bar")
        super().__init__(anchor="top", margin=MARGIN, monitor=0)

        self._wc = {}
        self._conn = get_hyprland_connection()
        self._sigs = []
        self._cw = None
        self._lws = self._lwt = self._lwc = None
        self._cht = None
        self._lv = self._lmv = self._lb = None
        self._init = False
        self._pointer_cursor = None

        self._build()
        self._bind()
        self._watch()
        GLib.idle_add(self._final)

    def _qj(self, cmd):
        try:
            raw = self._conn.send_command(f"j/{cmd}").reply
            return json.loads(raw.decode() if isinstance(raw, bytes) else raw) or {}
        except Exception:
            return {}

    def _get_or_create(self, key):
        if key not in self._wc:
            cls, size = WIDGET_REGISTRY[key]
            w = cls(notch=self) if key in NOTCH_INJECTED else cls()
            if size:
                w.set_size_request(*size)
            self.stack.add_named(w, key)
            self._wc[key] = w
        return self._wc[key]

    @property
    def main_window(self): return self._get_or_create("main_window")
    @property
    def launcher(self): return self._get_or_create("launcher")
    @property
    def overview(self): return self._get_or_create("overview")
    @property
    def power(self): return self._get_or_create("power")
    @property
    def cliphist(self): return self._get_or_create("cliphist")
    @property
    def tools(self): return self._get_or_create("tools")

    def _build(self):
        self.win_ic = Image(name="notch-window-icon", icon_name=FALLBACK_ICON, icon_size=ICON_SIZE)
        self.ws_lbl = Label(name="workspace-label", label="Workspace 1")
        self.awc = Box(name="active-window-container", spacing=SPACING, children=[self.win_ic, self.ws_lbl])
        self.awb = Box(name="active-window-box", h_align="center", children=[self.awc])

        self.cs = Stack(name="notch-compact-stack", transition_type="slide-up-down")
        self.cs.add_named(self.awb, "window")

        self.ctrl = ControlSmall()
        self.ctrl_rev = Revealer(
            name="control-revealer", transition_type="slide-down",
            transition_duration=TRANSITION_MS, child_revealed=False,
            child=Box(name="control-revealer-box", h_align="center", children=[self.ctrl]),
        )

        self.compact = Gtk.EventBox(name="notch-compact")
        self.compact.set_visible(True)
        self.compact.add(Box(name="compact-content", orientation="v", children=[self.cs, self.ctrl_rev]))
        self.compact.set_size_request(COMPACT_WIDTH, -1)

        self.stack = Stack(
            name="notch-content", transition_type="crossfade",
            transition_duration=TRANSITION_MS,
        )
        self.stack.add_named(self.compact, "compact")
        for s in ("panel", "bottom", "Top"):
            self.stack.add_style_class(s)
        if hasattr(self.stack, "set_interpolate_size"):
            self.stack.set_interpolate_size(True)
        if hasattr(self.stack, "set_homogeneous"):
            self.stack.set_homogeneous(False)

        self.nb = CenterBox(
            name="notch-box",
            start_children=Box(name="notch-corner-left", orientation="v", h_align="start", children=[MyCorner("top-right")]),
            center_children=self.stack,
            end_children=Box(name="notch-corner-right", orientation="v", h_align="end", children=[MyCorner("top-left")]),
        )
        self.nb.add_style_class("notch")

        self.nr = Revealer(name="notch-revealer", child_revealed=True, child=self.nb)
        self.nr.set_size_request(-1, 1)

        self.heb = Gtk.EventBox(name="notch-hover-eventbox")
        self.heb.set_halign(Gtk.Align.CENTER)
        self.heb.add(Box(name="notch-wrap", h_align="center", children=[
            Box(name="notch-complete", children=[self.nr]),
        ]))
        self.heb.set_visible(True)
        self.heb.set_size_request(COMPACT_WIDTH, HOVER_HEIGHT)

        self.add(Box(name="notch-root-container", h_align="center", children=[self.heb]))

    def _bind(self):
        self.compact.connect("button-press-event", self._wclick)
        self.compact.connect("enter-notify-event", self._bent)
        self.compact.connect("leave-notify-event", self._blev)
        self.compact.connect("scroll-event", self._cscr)
        self.awb.connect("button-press-event", self._wclick)

        self.heb.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.heb.connect("enter-notify-event", lambda *_: False)
        self.heb.connect("leave-notify-event", lambda *_: False)

        self.connect("key-press-event", self._kp)
        self.add_keybinding("Escape", lambda *_: self.close_notch())

        if self._conn:
            hid = self._conn.connect("event", lambda *_: self._updwin())
            self._sigs.append((self._conn, hid))

    def _watch(self):
        self.audio = get_audio()
        self._br = Brightness.get_initial()

        for prop, attr in (("speaker", "_lv"), ("microphone", "_lmv")):
            self._bind_dev(prop, attr)
            self.audio.connect(
                f"notify::{prop}",
                lambda *_, p=prop, a=attr: self._bind_dev(p, a),
            )

        if (b := self._br.screen_brightness) != -1:
            self._lb = b
            self._br.connect("screen", self._brchg)

    def _bind_dev(self, prop, attr):
        dev = getattr(self.audio, prop, None)
        if dev:
            setattr(self, attr, dev.volume)
            dev.connect(
                "changed",
                lambda *_, p=prop, a=attr: self._on_vol(p, a),
            )

    def _on_vol(self, prop, attr):
        if not self._init:
            return
        dev = getattr(self.audio, prop, None)
        if not dev:
            return
        cur = dev.volume
        if getattr(self, attr) is not None and abs(cur - getattr(self, attr)) > VOLUME_THRESHOLD:
            self._showctrl()
        setattr(self, attr, cur)

    def _brchg(self, *_):
        if not self._init:
            return
        cur = self._br.screen_brightness
        if self._lb is not None and cur != self._lb:
            self._showctrl()
        self._lb = cur

    def _showctrl(self):
        if self._cw is not None:
            return
        self._cancel_ctrl()
        self.ctrl_rev.set_reveal_child(True)
        self._cht = GLib.timeout_add(CTRL_HIDE_MS, self._hidectrl)

    def _cancel_ctrl(self):
        if self._cht:
            GLib.source_remove(self._cht)
            self._cht = None

    def _hidectrl(self):
        self.ctrl_rev.set_reveal_child(False)
        self._cht = None
        return False

    def _final(self):
        self.show_all()
        self._updwin()
        self._init = True
        return False

    def open_notch(self, name):
        self._open(name)

    def toggle_notch(self, name):
        self.close_notch() if self._cw == name else self._open(name)

    def _open(self, name):
        self._cancel_ctrl()
        self.ctrl_rev.set_reveal_child(False)
        self.nr.set_reveal_child(True)
        self.nb.add_style_class("open")
        self.stack.add_style_class("open")
        self.keyboard_mode = "exclusive"

        if name in APPLET_MAP:
            self._show_dashboard(APPLET_MAP[name])
        elif name in DASHBOARD_SECTIONS:
            self.stack.set_visible_child(self.main_window)
            self.main_window.go_to_section(name)
        elif name in SIMPLE_VIEWS:
            self.stack.set_visible_child(getattr(self, name))
        elif name == "cliphist":
            ch = self.cliphist
            self.stack.set_visible_child(ch)
            ch.open()
        elif name == "launcher":
            ln = self.launcher
            self.stack.set_visible_child(ln)
            ln.open()
            if ent := getattr(ln, "ent", None):
                ent.set_text("")
                ent.grab_focus()

        self._cw = name
        self._setbar(False)

    def _show_dashboard(self, applet):
        mw = self.main_window
        self.stack.set_visible_child(mw)
        mw.go_to_section("dashboard")
        try:
            tgt = getattr(mw.dashboard, applet, None)
            if tgt:
                mw.dashboard.applet_stack.set_visible_child(tgt)
        except AttributeError:
            pass

    def close_notch(self):
        self.keyboard_mode = "none"
        self.nb.remove_style_class("open")
        self.stack.remove_style_class("open")
        self._cancel_ctrl()
        self.ctrl_rev.set_reveal_child(False)
        self._setbar(True)

        try:
            db = self._wc["main_window"].dashboard
            db.applet_stack.set_visible_child(db.notification_history)
        except (KeyError, AttributeError):
            pass

        self._cw = None
        self.stack.set_visible_child(self.compact)
        self._updwin()

    def _setbar(self, v):
        if not self._bar:
            return
        for a in ("rr", "rl"):
            if r := getattr(self._bar, a, None):
                r.set_reveal_child(v)

    def _wclick(self, *_):
        self._cancel_ctrl()
        self.ctrl_rev.set_reveal_child(False)
        self.toggle_notch("dashboard")
        return True

    def _bent(self, w, _):
        if win := w.get_window():
            if not self._pointer_cursor:
                self._pointer_cursor = Gdk.Cursor.new_from_name(w.get_display(), "pointer")
            win.set_cursor(self._pointer_cursor)
        return True

    def _blev(self, w, e):
        if e.detail == Gdk.NotifyType.INFERIOR:
            return False
        if win := w.get_window():
            win.set_cursor(None)
        return True

    def _cscr(self, _, e):
        ch = self.cs.get_children()
        if not ch:
            return False
        try:
            idx = ch.index(self.cs.get_visible_child())
        except ValueError:
            idx = 0
        if e.direction == Gdk.ScrollDirection.UP:
            idx = (idx - 1) % len(ch)
        elif e.direction == Gdk.ScrollDirection.DOWN:
            idx = (idx + 1) % len(ch)
        else:
            return False
        self.cs.set_visible_child(ch[idx])
        return True

    def _kp(self, _, e):
        if e.keyval == Gdk.KEY_Escape:
            self.close_notch()
            return True
        return False

    def _updwin(self):
        if self._cw is not None:
            return
        ws = self._qj("activeworkspace")
        win = self._qj("activewindow")
        wsid = ws.get("id", 1)
        wc = win.get("class") or win.get("initialClass", "")
        wt = win.get("title", "")

        if wsid == self._lws and wt == self._lwt and wc == self._lwc:
            return
        self._lws, self._lwt, self._lwc = wsid, wt, wc

        self.ws_lbl.set_label(f"Workspace {wsid}")

        if wc and wc.strip():
            px = _icon(wc, ICON_SIZE)
            if px:
                self.win_ic.set_from_pixbuf(px)
            else:
                self.win_ic.set_from_icon_name(FALLBACK_ICON_SYM, ICON_SIZE)
            if not self.win_ic.get_visible():
                self.win_ic.show()
                self.awc.set_spacing(SPACING)
        elif self.win_ic.get_visible():
            self.win_ic.hide()
            self.awc.set_spacing(0)

    def toggle_hidden(self):
        v = self.get_visible()
        self.set_visible(not v)
        if not v:
            self._updwin()

    def cleanup(self):
        self._cancel_ctrl()
        for obj, hid in self._sigs:
            try:
                obj.disconnect(hid)
            except Exception:
                pass
        self._sigs.clear()
        for w in self._wc.values():
            if hasattr(w, "cleanup"):
                w.cleanup()
        self._wc.clear()
        if hasattr(self.ctrl, "cleanup"):
            self.ctrl.cleanup()
        self._conn = self._bar = self.audio = self._br = self._pointer_cursor = None