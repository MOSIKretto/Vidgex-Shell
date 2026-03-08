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

from gi.repository import Gdk, GLib, Gtk

from modules.Notch.Widgets.buttons import AutolayoutButton
from modules.Bar.metrics import Battery, MetricsSmall
from modules.Bar.systemtray import SystemTray
from services.wayland import WaylandWindow as Window
import services.icons as icons


_CD = 0.2
_TH = 0.5
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

def _dispatch_exact(conn, target_row, target_col, is_vertical=False):
    next_ws = target_row * 3 + target_col + 1
    
    if is_vertical:
        cmd = (
            f"[[BATCH]] keyword animation workspaces,1,6,overshot,slidevert ; "
            f"dispatch workspace {next_ws} ; "
            f"keyword animation workspaces,1,6,overshot,slide"
        )
        conn.send_command(cmd)
    else:
        conn.send_command(f"dispatch workspace {next_ws}")

_LST = 0.0

def _check_scroll_cooldown(e, axis=None):
    global _LST
    now = time()
    if now - _LST < _CD: 
        return 0
    
    direction = 0
    d = e.direction
    res, dx, dy = e.get_scroll_deltas()

    if axis == 'y':
        if d == Gdk.ScrollDirection.UP or (res and dy < -_TH): 
            direction = -1
        elif d == Gdk.ScrollDirection.DOWN or (res and dy > _TH): 
            direction = 1 
    
    elif axis == 'x':
        if d == Gdk.ScrollDirection.RIGHT or (res and dx > _TH):
            direction = 1
        elif d == Gdk.ScrollDirection.LEFT or (res and dx < -_TH):
            direction = -1

    if direction != 0:
        _LST = now

    return direction

class TopWorkspaces(Box):
    def __init__(self, conn, **kwargs):
        super().__init__(name="workspaces-container-top", orientation="h", **kwargs)
        self.conn = conn
        
        self.inner_box = Box(orientation="h", spacing=0)

        self.lbl_num = Label(label="1")
        self.btn_num = Button(child=self.lbl_num, h_align="center", v_align="center", can_focus=False)
        self.btn_num.add_style_class("active") 
        
        self.num_box = Box(name="workspaces-num-top", children=[self.btn_num])
        self.inner_box.add(self.num_box)
        
        self.dots_box = Box(name="workspaces-top", orientation="h", spacing=8)
        self.dots = []
        for col in range(3):
            btn = Button(h_expand=False, v_expand=False, h_align="center", v_align="center", can_focus=False)
            btn.connect("clicked", lambda _, c=col: self._on_dot_clicked(c))
            btn.add_events(_SM)
            btn.connect("scroll-event", self._on_scroll)
            _hov(btn)
            self.dots.append(btn)
            self.dots_box.add(btn)
        
        self.inner_box.add(self.dots_box)
        
        self.event_box = EventBox(child=self.inner_box)
        self.event_box.add_events(_SM)
        self.event_box.connect("scroll-event", self._on_scroll)
        
        self.add(self.event_box)
        
        self.conn.connect("event::workspace", self._update_ui)
        self._update_ui()

    def _on_scroll(self, _, event):
        direction = _check_scroll_cooldown(event, axis='x')
        if direction == 0: return False

        ws = _get_active_ws(self.conn)
        cur_row = (ws - 1) // 3
        cur_col = (ws - 1) % 3

        target_col = (cur_col + direction) % 3

        _dispatch_exact(self.conn, cur_row, target_col, is_vertical=False)
        return True

    def _on_dot_clicked(self, target_col):
        ws = _get_active_ws(self.conn)
        if ws < 1 or ws > 9: ws = 5
        current_row = (ws - 1) // 3
        _dispatch_exact(self.conn, current_row, target_col, is_vertical=False)

    def _update_ui(self, *_):
        ws = _get_active_ws(self.conn)
        self.lbl_num.set_label(str(ws) if 1 <= ws <= 9 else "?")
        
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
        
        self.inner_box = Box(orientation="v", spacing=0)

        self.dots_box = Box(name="workspaces-left", orientation="v", spacing=8)
        self.dots = []
        for row in range(3):
            btn = Button(h_expand=False, v_expand=False, h_align="center", v_align="center", can_focus=False)
            btn.add_style_class("row-dot") 
            btn.connect("clicked", lambda _, r=row: self._on_dot_clicked(r))
            btn.add_events(_SM)
            btn.connect("scroll-event", self._on_scroll)
            _hov(btn)
            self.dots.append(btn)
            self.dots_box.add(btn)
            
        self.inner_box.add(self.dots_box)

        self.event_box = EventBox(child=self.inner_box)
        self.event_box.add_events(_SM)
        self.event_box.connect("scroll-event", self._on_scroll)

        self.add(self.event_box)

        self.conn.connect("event::workspace", self._update_ui)
        self._update_ui()

    def _on_scroll(self, _, event):
        direction = _check_scroll_cooldown(event, axis='y')
        if direction == 0: return False

        ws = _get_active_ws(self.conn)
        cur_row = (ws - 1) // 3
        cur_col = (ws - 1) % 3

        target_row = (cur_row + direction) % 3

        _dispatch_exact(self.conn, target_row, cur_col, is_vertical=True)
        return True

    def _on_dot_clicked(self, target_row):
        ws = _get_active_ws(self.conn)
        if ws < 1 or ws > 9: ws = 5
        current_col = (ws - 1) % 3
        _dispatch_exact(self.conn, target_row, current_col, is_vertical=True)

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
        self.anchor = "left top"
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

        self.content_eb = EventBox()
        self.content_eb.add(self.wrapper)
        self.content_eb.connect("enter-notify-event", self._on_hover_enter)
        self.content_eb.connect("leave-notify-event", self._on_hover_leave)

        self.revealer = Revealer(
            name="sidebar-revealer",
            transition_type="slide-right",
            child_revealed=True,
            child=self.content_eb,
        )

        self.activator = EventBox()
        self.activator.add(Box(style="background: transparent;"))
        
        display = Gdk.Display.get_default()
        monitor = display.get_monitor(self.monitor_id) if display else None
        mon_h = monitor.get_geometry().height if monitor else 1080
        
        self.activator.set_size_request(15, mon_h)
        self.activator.set_valign(Gtk.Align.FILL)
        
        self.activator.connect("enter-notify-event", self._on_hover_enter)
        self.activator.connect("leave-notify-event", self._on_hover_leave)

        layout_box = Box(orientation="h", children=[self.revealer, self.activator])
        self.add(layout_box)

    def _on_size_allocate(self, _, alloc):
        if not self._is_hidden and alloc.width > 15:
            self._bar_width = alloc.width

    def _trigger_ws_switch(self, *_):
        self._ws_switch_active = True
        self._is_hidden = False
        self.revealer.set_reveal_child(True)

        if self._ws_switch_timer_id:
            GLib.source_remove(self._ws_switch_timer_id)
        
        self._ws_switch_timer_id = GLib.timeout_add(1000, self._on_ws_switch_timeout)

    def _on_ws_switch_timeout(self):
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
        "tray", "rl", "met", "rr", "ll", "dt", "bat", "bp",
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
        self.met = MetricsSmall()

        self.rl = Revealer(
            name="bar-revealer", transition_type="slide-right", child_revealed=True,
            child=Box(name="bar-revealer-box", spacing=4, children=[self.tray]),
        )

        self.rr = Revealer(
            name="bar-revealer", transition_type="slide-left", child_revealed=True,
            child=Box(name="bar-revealer-box", spacing=4, children=[self.met]),
        )

        self.dt = DateTime(name="date-time", formatters=["%H:%M"])
        self.bat = Battery()

        self.bp = Button(
            name="button-bar", tooltip_markup="<b>Меню питания</b>",
            on_clicked=self._pwr, child=Label(name="button-bar-label", markup=icons.shutdown),
        )
        _hov(self.bp)

        self.ll = Label(name="lang-label")
        
        self.autolayout_btn = AutolayoutButton()
        
        self.lang_revealer = Revealer(
            name="lang-revealer", 
            transition_type="slide-right", 
            child_revealed=False,
            child=self.autolayout_btn
        )

        self.lang_box = Box(
            name="language-indicator", 
            spacing=4, 
            children=[self.lang_revealer, self.ll]
        )
        
        self.lang_eb = EventBox(child=self.lang_box)
        
        self.lang_eb.connect("enter-notify-event", lambda *_: self.lang_revealer.set_reveal_child(True))
        
        def _on_lang_leave(widget, event):
            if event.detail != Gdk.NotifyType.INFERIOR:
                self.lang_revealer.set_reveal_child(False)
            return False
            
        self.lang_eb.connect("leave-notify-event", _on_lang_leave)

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
                    Box(name="power-battery-container", children=[self.dt, self.lang_eb, self.bat, self.bp])
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
        if hasattr(self.bat, 'cleanup'): self.bat.cleanup()
        self.notch = self.conn = self.lang = None