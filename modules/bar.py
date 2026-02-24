import json
import re
import subprocess
from time import time

from fabric.hyprland.widgets import (
    HyprlandLanguage as Language,
    get_hyprland_connection,
)
from fabric.widgets.box import Box
from fabric.widgets.label import Label
from fabric.widgets.button import Button
from fabric.widgets.datetime import DateTime
from fabric.widgets.revealer import Revealer
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.eventbox import EventBox

from gi.repository import Gdk, GLib

from modules.Bar.metrics import Battery, MetricsSmall, NetworkApplet
from modules.Bar.systemtray import SystemTray
from services.wayland import WaylandWindow as Window
import services.icons as icons

_CD = 0.2
_TH = 0.3
_SM = Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK
_cursor_hand = None

def _hov(w):
    def sc(widget, _, is_hovered):
        global _cursor_hand
        if not _cursor_hand:
            _cursor_hand = Gdk.Cursor.new_from_name(widget.get_display(), "hand2")
        if win := widget.get_window():
            win.set_cursor(_cursor_hand if is_hovered else None)
    w.connect("enter-notify-event", sc, True)
    w.connect("leave-notify-event", sc, False)

def _get_active_ws(conn):
    try:
        res = conn.send_command("j/activeworkspace")
        if hasattr(res, "reply"): res = res.reply
        if isinstance(res, bytes): res = res.decode("utf-8", errors="ignore")
        match = re.search(r'"id":\s*([0-9]+)', str(res))
        if match: return int(match.group(1))
    except Exception:
        pass
    try:
        out = subprocess.check_output("hyprctl activeworkspace -j", shell=True).decode("utf-8")
        match = re.search(r'"id":\s*([0-9]+)', out)
        if match: return int(match.group(1))
    except Exception:
        pass
    return 1

def _dispatch_exact(conn, r, c, cur_row):
    next_ws = r * 3 + c + 1
    is_vert = (r != cur_row)
    if is_vert:
        conn.send_command(f"keyword animation workspaces,1,6,overshot,slidevert; dispatch workspace {next_ws}; keyword animation workspaces,1,6,overshot,slide")
    else:
        conn.send_command(f"dispatch workspace {next_ws}")

_LST = 0.0
def _handle_scroll(conn, e):
    global _LST
    now = time()
    if now - _LST < _CD: return True
    
    d = e.direction
    cmd = None
    res, dx, dy = e.get_scroll_deltas()

    if d == Gdk.ScrollDirection.UP or (res and dy < -_TH): cmd = "U"
    elif d == Gdk.ScrollDirection.DOWN or (res and dy > _TH): cmd = "D"
    elif d == Gdk.ScrollDirection.LEFT or (res and dx < -_TH): cmd = "L"
    elif d == Gdk.ScrollDirection.RIGHT or (res and dx > _TH): cmd = "R"

    if cmd:
        _LST = now
        ws = _get_active_ws(conn)
        if ws < 1 or ws > 9: ws = 5
        
        r = cur_row = (ws - 1) // 3
        c = (ws - 1) % 3
        
        if cmd == "R": c = (c + 1) % 3
        elif cmd == "L": c = (c + 2) % 3
        elif cmd == "D": r = (r + 1) % 3
        elif cmd == "U": r = (r + 2) % 3
            
        _dispatch_exact(conn, r, c, cur_row)
    return True


class TopWorkspaces(Box):
    def __init__(self, conn, **kwargs):
        super().__init__(name="workspaces-container-top", orientation="h", **kwargs)
        self.conn = conn
        
        self.lbl_num = Label(label="3")
        self.btn_num = Button(child=self.lbl_num, h_align="center", v_align="center", can_focus=False)
        self.btn_num.add_style_class("active") 
        
        self.num_box = Box(name="workspaces-num-top", children=[self.btn_num])
        self.add(self.num_box)
        
        self.dots_box = Box(name="workspaces-top", orientation="h", spacing=8)
        self.dots = []
        for i in range(3):
            btn = Button(h_expand=False, v_expand=False, h_align="center", v_align="center", can_focus=False)
            self.dots.append(btn)
            self.dots_box.add(btn)
        
        self.add(self.dots_box)
        
        self.add_events(_SM)
        self.connect("scroll-event", lambda _, e: _handle_scroll(self.conn, e))
        self.conn.connect("event::workspace", self._update_ui)
        self._update_ui()

    def _update_ui(self, *_):
        ws = _get_active_ws(self.conn)
        cur_col = (ws - 1) % 3
        in_bounds = (1 <= ws <= 9)

        for i, dot in enumerate(self.dots):
            if i == cur_col and in_bounds:
                dot.add_style_class("active")
                dot.remove_style_class("empty")
            else:
                dot.add_style_class("empty")
                dot.remove_style_class("active")


class LeftWorkspaces(Box):
    def __init__(self, conn, **kwargs):
        super().__init__(name="workspaces-container-left", orientation="v", **kwargs)
        self.conn = conn
        
        self.dots_box = Box(name="workspaces-left", orientation="v", spacing=8)
        self.dots = []
        for i in range(3):
            btn = Button(h_expand=False, v_expand=False, h_align="center", v_align="center", can_focus=False)
            btn.add_style_class("row-dot") 
            self.dots.append(btn)
            self.dots_box.add(btn)
            
        self.add(self.dots_box)

        self.add_events(_SM)
        self.connect("scroll-event", lambda _, e: _handle_scroll(self.conn, e))
        self.conn.connect("event::workspace", self._update_ui)
        self._update_ui()

    def _update_ui(self, *_):
        ws = _get_active_ws(self.conn)
        cur_row = (ws - 1) // 3
        in_bounds = (1 <= ws <= 9)

        for i, dot in enumerate(self.dots):
            if i == cur_row and in_bounds:
                dot.add_style_class("active")
                dot.remove_style_class("empty")
            else:
                dot.add_style_class("empty")
                dot.remove_style_class("active")


class SideBarWindow(Window):
    def __init__(self, conn, monitor_id=0):
        super().__init__(exclusivity="none", layer="top", monitor_id=monitor_id)
        self.anchor = "left top bottom"
        self.margin = "-4px -4px -8px -4px" 
        
        self.conn = conn
        self.monitor_id = monitor_id
        
        self._mouse_over = False
        self._is_hidden = False
        self._pending_occlusion = False
        self._bar_width = 0

        self._ws_switch_active = False
        self._ws_switch_timer_id = None

        self._init_ui()
        self._bind_events()

    def _parse(self, cmd):
        try:
            s = self.conn.send_command(cmd).reply.decode()
            return json.loads(s)
        except Exception:
            return []

    def _init_ui(self):
        self.ws = LeftWorkspaces(self.conn, v_align="start", h_align="start")
        self.wrapper = Box(name="bar-inner", children=[self.ws], v_expand=True, orientation="v")
        self.wrapper.connect("size-allocate", self._on_size_allocate)

        self.revealer = Revealer(
            name="sidebar-revealer",
            transition_type="slide-right",
            child_revealed=True,
            child=self.wrapper,
        )

        self.activator = EventBox()
        self.activator.add(Box())
        self.activator.set_size_request(6, -1) 

        layout_box = Box(orientation="h", children=[self.revealer, self.activator])

        self.root_eb = EventBox()
        self.root_eb.add(layout_box)
        self.root_eb.connect("enter-notify-event", self._on_hover_enter)
        self.root_eb.connect("leave-notify-event", self._on_hover_leave)

        self.add(self.root_eb)

    def _on_size_allocate(self, _, alloc):
        if not self._is_hidden and alloc.width > 10:
            self._bar_width = alloc.width

    def _trigger_ws_switch(self, *_):
        """Вызывается при переходе на новый воркспейс. Открывает док и ставит таймер."""
        self._ws_switch_active = True
        self._is_hidden = False
        self.revealer.set_reveal_child(True)

        if self._ws_switch_timer_id:
            GLib.source_remove(self._ws_switch_timer_id)
        
        self._ws_switch_timer_id = GLib.timeout_add(1000, self._on_ws_switch_timeout)

    def _on_ws_switch_timeout(self):
        """Таймер бездействия (сработал спустя 1 с)"""
        self._ws_switch_active = False
        self._ws_switch_timer_id = None
        self._schedule_occlusion()
        return False

    def _bind_events(self):
        c = self.conn
        c.connect("event::openwindow", self._schedule_occlusion)
        c.connect("event::closewindow", self._schedule_occlusion)
        c.connect("event::movewindow", self._schedule_occlusion)
        
        c.connect("event::workspace", self._trigger_ws_switch)
        
        c.connect("event::activewindow", self._schedule_occlusion)
        c.connect("event::changefloatingmode", self._schedule_occlusion)
        c.connect("event::fullscreen", self._schedule_occlusion)

        if c.ready:
            GLib.idle_add(self._do_occlusion)
        else:
            c.connect("event::ready", lambda *_: GLib.idle_add(self._do_occlusion))

    def _schedule_occlusion(self, *_):
        if not self._pending_occlusion:
            self._pending_occlusion = True
            GLib.idle_add(self._do_occlusion)

    def _do_occlusion(self):
        self._pending_occlusion = False
        self._check_occlusion(self._parse("j/clients"))
        return False

    def _get_monitor_x(self):
        for m in self._parse("j/monitors"):
            if m.get("id") == self.monitor_id:
                return m.get("x", 0)
        return 0

    def _check_occlusion(self, clients):
        if not self._bar_width:
            return

        mon_x = self._get_monitor_x()
        bw = self._bar_width or 60
        
        ws = self._parse("j/activeworkspace")
        ws_id = ws.get("id", 0) if ws else 0

        overlap = False
        for w in clients:
            if w.get("hidden") or w.get("minimized"):
                continue
            
            w_ws = w.get("workspace", {})
            if (w_ws.get("id") if isinstance(w_ws, dict) else w_ws) != ws_id:
                continue
            
            if w.get("monitor") != self.monitor_id:
                continue

            pos, size = w.get("at"), w.get("size")
            if not pos or not size:
                continue

            wx, ww = pos[0], size[0]
            
            if wx < mon_x + bw and wx + ww > mon_x:
                overlap = True
                break

        should_hide = overlap and not self._mouse_over and not self._ws_switch_active
        
        if should_hide != self._is_hidden:
            self._is_hidden = should_hide
            self.revealer.set_reveal_child(not should_hide)

    def _on_hover_enter(self, *_):
        self._mouse_over = True
        self._is_hidden = False
        self.revealer.set_reveal_child(True)
        return True

    def _on_hover_leave(self, _, e):
        if e.detail == Gdk.NotifyType.INFERIOR:
            return False
            
        self._mouse_over = False
        self._schedule_occlusion()
        return True


class Bar(Window):
    __slots__ = (
        "mid", "notch", "lang", "conn", "ws", "wsc",
        "tray", "net", "rl", "met", "rr", "ll", "dt", "bat", "bp",
        "sidebar"
    )

    def __init__(self, monitor_id=0, **kwargs):
        super().__init__(exclusivity="auto", monitor_id=monitor_id)
        self.mid = monitor_id
        self.notch = kwargs.get("notch")
        self.anchor = "left top right"
        self.margin = "-4px -4px -8px -4px"

        self.lang = Language()
        self.conn = get_hyprland_connection()

        self.sidebar = SideBarWindow(conn=self.conn, monitor_id=self.mid)
        self.sidebar.show_all()

        self._build()
        self.lang.connect("notify::label", self._lchg)
        self._lchg()

    def _build(self):
        self.ws = TopWorkspaces(conn=self.conn, v_align="center", h_align="start")        

        self.tray = SystemTray()
        self.net = NetworkApplet()
        self.met = MetricsSmall()

        self.rl = Revealer(
            name="bar-revealer", transition_type="slide-right", child_revealed=True,
            child=Box(name="bar-revealer-box", spacing=4, children=[self.tray, Box(name="network-container", children=[self.net])]),
        )

        self.rr = Revealer(
            name="bar-revealer", transition_type="slide-left", child_revealed=True,
            child=Box(name="bar-revealer-box", spacing=4, children=[self.met]),
        )

        self.ll = Label(name="lang-label")
        self.dt = DateTime(name="date-time", formatters=["%H:%M"])
        self.bat = Battery()

        self.bp = Button(
            name="button-bar", tooltip_markup="<b>Меню питания</b>",
            on_clicked=self._pwr, child=Label(name="button-bar-label", markup=icons.shutdown),
        )
        _hov(self.bp)

        self.add(CenterBox(
            name="bar-inner",
            start_children=Box(
                name="start-container", spacing=4, 
                children=[self.ws, Box(name="boxed-revealer", children=[self.rl])]
            ),
            end_children=Box(
                name="end-container", spacing=4, 
                children=[
                    Box(name="boxed-revealer", children=[self.rr]),
                    Box(name="power-battery-container", children=[self.dt, Box(name="language-indicator", children=[self.ll]), self.bat, self.bp])
                ]
            ),
        ))

    def _lchg(self, *_):
        if l := self.lang.get_label():
            self.ll.set_label(l[:3].upper())

    def _pwr(self, *_):
        if self.notch:
            self.notch.open_notch("power")

    def cleanup(self):
        if self.sidebar:
            self.sidebar.destroy()
            self.sidebar = None
        if hasattr(self.tray, 'cleanup'): self.tray.cleanup()
        if hasattr(self.net, 'cleanup'): self.net.cleanup()
        if hasattr(self.bat, 'cleanup'): self.bat.cleanup()
        self.notch = self.conn = self.lang = None