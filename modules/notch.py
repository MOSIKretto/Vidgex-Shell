import json
from fabric.hyprland.widgets import get_hyprland_connection
from fabric.widgets.box import Box
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer
from fabric.widgets.stack import Stack
from gi.repository import Gdk, GLib, Gtk

from modules.Notch.dashboard import Dashboard
from modules.Notch.cliphist import ClipHistory
from modules.Notch.launcher import AppLauncher
from modules.Notch.overview import Overview
from modules.Notch.power import PowerMenu
from modules.Notch.tools import Toolbox

from modules.corners import MyCorner
from modules.Notch.Widgets.controls import ControlSmall, get_audio

from services.brightness import Brightness
from services.icon_resolver import IconResolver
from services.wayland import WaylandWindow as Window


class Notch(Window):
    __slots__ = (
        '_bar', '_wc', '_conn', '_icr', '_sigs', '_cw', '_lws', '_lwt', '_lwc',
        '_cht', '_csd', '_lv', '_lmv', '_lb', '_init', 'audio', '_br',
        'win_ic', 'ws_lbl', 'awc', 'awb', 'cs', 'ctrl', 'ctrl_rev', 'cc',
        'compact', 'stack', 'cl', 'cr', 'nb', 'nr', 'nc', 'nw', 'heb',
    )

    def __init__(self, **kwargs):
        self._bar = kwargs.get('bar')

        super().__init__(anchor="top", margin="-40px 0px 0px 0px", monitor=0)

        self._wc = {}
        self._conn = get_hyprland_connection()
        self._icr = IconResolver.get_default()

        self._sigs = []
        self._cw = None
        self._lws = None
        self._lwt = ""
        self._lwc = ""

        self._cht = None
        self._csd = 2000

        self._lv = None
        self._lmv = None
        self._lb = None
        self._init = False

        self._build()
        self._signals()
        self._watchers()

        GLib.idle_add(self._final)

    def _get_or_create(self, key, widget_class, w=None, h=None):
        if key not in self._wc:
            widget = widget_class(notch=self) if key in ('dashboard', 'launcher', 'power', 'cliphist', 'tools') else widget_class()
            if w is not None and h is not None:
                widget.set_size_request(w, h)
            self.stack.add_named(widget, key)
            self._wc[key] = widget
        return self._wc[key]

    @property
    def dashboard(self): return self._get_or_create('dashboard', Dashboard, 1093, 472)
    @property
    def launcher(self): return self._get_or_create('launcher', AppLauncher, 480, 244)
    @property
    def overview(self): return self._get_or_create('overview', Overview)
    @property
    def power(self): return self._get_or_create('power', PowerMenu)
    @property
    def cliphist(self): return self._get_or_create('cliphist', ClipHistory, 480, 244)
    @property
    def tools(self): return self._get_or_create('tools', Toolbox)

    def _build(self):
        self.win_ic = Image(name="notch-window-icon", icon_name="application-x-executable", icon_size=20)
        self.ws_lbl = Label(name="workspace-label", label="Workspace 1")

        self.awc = Box(name="active-window-container", spacing=8, children=[self.win_ic, self.ws_lbl])
        self.awb = Box(name="active-window-box", h_align="center", children=[self.awc])

        self.cs = Stack(name="notch-compact-stack", transition_type="slide-up-down")
        self.cs.add_named(self.awb, "window")

        self.ctrl = ControlSmall()
        self.ctrl_rev = Revealer(
            name="control-revealer", 
            transition_type="slide-down", 
            transition_duration=200,
            child_revealed=False, 
            child=Box(name="control-revealer-box", h_align="center", children=[self.ctrl]),
        )

        self.cc = Box(name="compact-content", orientation="v", children=[self.cs, self.ctrl_rev])

        self.compact = Gtk.EventBox(name="notch-compact")
        self.compact.set_visible(True)
        self.compact.add(self.cc)
        self.compact.set_size_request(260, -1)

        self.stack = Stack(name="notch-content", transition_type="crossfade", transition_duration=200)
        self.stack.add_named(self.compact, "compact")

        for s in ("panel", "bottom", "Top"):
            self.stack.add_style_class(s)

        if hasattr(self.stack, 'set_interpolate_size'):
            self.stack.set_interpolate_size(True)
        if hasattr(self.stack, 'set_homogeneous'):
            self.stack.set_homogeneous(False)

        self.cl = Box(name="notch-corner-left", orientation="v", h_align="start", children=[MyCorner("top-right")])
        self.cr = Box(name="notch-corner-right", orientation="v", h_align="end", children=[MyCorner("top-left")])

        self.nb = CenterBox(
            name="notch-box", 
            start_children=self.cl, 
            center_children=self.stack, 
            end_children=self.cr
        )
        self.nb.add_style_class("notch")

        self.nr = Revealer(name="notch-revealer", child_revealed=True, child=self.nb)
        self.nr.set_size_request(-1, 1)

        self.nc = Box(name="notch-complete", children=[self.nr])
        
        self.nw = Box(name="notch-wrap", h_align="center", children=[self.nc])

        self.heb = Gtk.EventBox(name="notch-hover-eventbox")
        self.heb.set_halign(Gtk.Align.CENTER)
        self.heb.add(self.nw)
        self.heb.set_visible(True)
        self.heb.set_size_request(260, 4)

        self.add(Box(name="notch-root-container", h_align="center", children=[self.heb]))

    def _signals(self):
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
            cb = lambda *_: self._updwin()
            for ev in ("event::activewindow", "event::workspace"):
                self._sigs.append((self._conn, self._conn.connect(ev, cb)))

    def _watchers(self):
        self.audio = get_audio()

        if spk := self.audio.speaker:
            self._lv = spk.volume
            spk.connect("changed", self._spkchg)
        self.audio.connect("notify::speaker", self._nspk)

        if mic := self.audio.microphone:
            self._lmv = mic.volume
            mic.connect("changed", self._micchg)
        self.audio.connect("notify::microphone", self._nmic)

        self._br = Brightness.get_initial()
        if (cur_br := self._br.screen_brightness) != -1:
            self._lb = cur_br
            self._br.connect("screen", self._brchg)

    def _nspk(self, *_):
        if spk := self.audio.speaker:
            self._lv = spk.volume
            spk.connect("changed", self._spkchg)

    def _nmic(self, *_):
        if mic := self.audio.microphone:
            self._lmv = mic.volume
            mic.connect("changed", self._micchg)

    def _spkchg(self, *_):
        if not self._init or not (spk := self.audio.speaker): return
        cur = spk.volume
        if self._lv is not None and abs(cur - self._lv) > 0.5:
            self._showctrl()
        self._lv = cur

    def _micchg(self, *_):
        if not self._init or not (mic := self.audio.microphone): return
        cur = mic.volume
        if self._lmv is not None and abs(cur - self._lmv) > 0.5:
            self._showctrl()
        self._lmv = cur

    def _brchg(self, *_):
        if not self._init: return
        cur = self._br.screen_brightness
        if self._lb is not None and cur != self._lb:
            self._showctrl()
        self._lb = cur

    def _showctrl(self):
        if self._cw is not None: return
        self._cancelcht()
        self.ctrl_rev.set_reveal_child(True)
        self._cht = GLib.timeout_add(self._csd, self._hidectrl)

    def _cancelcht(self):
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

    def open_notch(self, name: str): self._open(name)
    def toggle_notch(self, name: str): self.close_notch() if self._cw == name else self._open(name)

    def _open(self, name: str):
        self._cancelcht()
        self.ctrl_rev.set_reveal_child(False)

        self.nr.set_reveal_child(True)
        self.nb.add_style_class("open")
        self.stack.add_style_class("open")
        self.keyboard_mode = "exclusive"

        if name in ("network_applet", "bluetooth"): self._showdw(name)
        elif name == "dashboard": self._showdw("notification_history")
        elif name in ("wallpapers", "mixer"): self._showdb(name)
        elif name == "overview": self.stack.set_visible_child(self.overview)
        elif name == "power": self.stack.set_visible_child(self.power)
        elif name == "tools": self.stack.set_visible_child(self.tools)
        elif name == "cliphist": self._showclip()
        elif name == "launcher": self._showlaunch()

        self._cw = name
        self._setbar(False)

    def _showdw(self, wn: str):
        db = self.dashboard
        self.stack.set_visible_child(db)
        db.go_to_section("widgets")

        try:
            target_name = 'network_connections' if wn == "network_applet" else ('bluetooth' if wn == "bluetooth" else 'notification_history')
            tgt = getattr(db.widgets, target_name, None)
            if tgt:
                db.widgets.applet_stack.set_visible_child(tgt)
        except AttributeError:
            pass

    def _showdb(self, b: str):
        self.stack.set_visible_child(self.dashboard)
        self.dashboard.go_to_section(b)

    def _showclip(self):
        ch = self.cliphist
        self.stack.set_visible_child(ch)
        if hasattr(ch, 'open'): GLib.idle_add(ch.open)

    def _showlaunch(self):
        ln = self.launcher
        self.stack.set_visible_child(ln)
        ln.open()
        if ent := getattr(ln, 'ent', None):
            ent.set_text("")
            ent.grab_focus()

    def _setbar(self, v: bool):
        if not self._bar: return
        for a in ('rr', 'rl'):
            if r := getattr(self._bar, a, None):
                r.set_reveal_child(v)

    def close_notch(self):
        self.keyboard_mode = "none"
        self.nb.remove_style_class("open")
        self.stack.remove_style_class("open")

        self._cancelcht()
        self.ctrl_rev.set_reveal_child(False)
        self._setbar(True)

        try:
            ws = self._wc['dashboard'].widgets
            ws.applet_stack.set_visible_child(ws.notification_history)
        except (KeyError, AttributeError):
            pass

        self._cw = None
        self.stack.set_visible_child(self.compact)
        self._updwin()

    def _wclick(self, *_):
        self._cancelcht()
        self.ctrl_rev.set_reveal_child(False)
        self.toggle_notch("dashboard")
        return True

    def _bent(self, w, _):
        if win := w.get_window():
            if d := Gdk.Display.get_default():
                win.set_cursor(Gdk.Cursor.new_for_display(d, Gdk.CursorType.HAND2))
        return True

    def _blev(self, w, e):
        if e.detail == Gdk.NotifyType.INFERIOR: return False
        if win := w.get_window():
            win.set_cursor(None)
        return True

    def _cscr(self, _, e):
        ch = self.cs.get_children()
        if not ch: return False

        try:
            idx = ch.index(self.cs.get_visible_child())
        except ValueError:
            idx = 0

        if e.direction == Gdk.ScrollDirection.UP: idx = (idx - 1) % len(ch)
        elif e.direction == Gdk.ScrollDirection.DOWN: idx = (idx + 1) % len(ch)
        else: return False

        self.cs.set_visible_child(ch[idx])
        return True

    def _kp(self, _, e):
        if e.keyval == Gdk.KEY_Escape:
            self.close_notch()
            return True
        return False

    def _updwin(self):
        if self._cw is not None: return

        wsid, wt, wc = self._getwin()
        if wsid == self._lws and wt == self._lwt and wc == self._lwc: return
        self._lws, self._lwt, self._lwc = wsid, wt, wc

        self.ws_lbl.set_label(f"Workspace {wsid}")

        if wc and wc.strip():
            self._updic(wc)
            if not self.win_ic.get_visible():
                self.win_ic.show()
                self.awc.set_spacing(8)
        elif self.win_ic.get_visible():
            self.win_ic.hide()
            self.awc.set_spacing(0)

    def _getwin(self):
        wsid, wt, wc = 1, "", ""
        if not self._conn: return wsid, wt, wc

        try:
            if wr := self._conn.send_command("j/activeworkspace").reply:
                ws_data = json.loads(wr)
                if ws_data:
                    wsid = ws_data.get("id", wsid)

            if r := self._conn.send_command("j/activewindow").reply:
                win_data = json.loads(r)
                if win_data:
                    wc = win_data.get("class") or win_data.get("initialClass", "")
                    wt = win_data.get("title", "")
                    
        except Exception:
            pass

        return wsid, wt, wc

    def _updic(self, aid: str):
        if ic := self._getic(aid):
            self.win_ic.set_from_pixbuf(ic)
        else:
            self.win_ic.set_from_icon_name("application-x-executable-symbolic", 20)

    def _getic(self, aid: str):
        if not aid or not self._icr: return None
        return self._icr.get_icon(aid, 20, self._icr.find_app(aid))

    def toggle_hidden(self):
        v = self.get_visible()
        self.set_visible(not v)
        if not v: self._updwin()

    def cleanup(self):
        self._cancelcht()

        for obj, hid in self._sigs:
            try: obj.disconnect(hid)
            except Exception: pass
        self._sigs.clear()

        for w in self._wc.values():
            if hasattr(w, 'cleanup'): w.cleanup()
        self._wc.clear()

        if hasattr(self.ctrl, 'cleanup'): self.ctrl.cleanup()
        self._conn = self._icr = self._bar = self.audio = self._br = None