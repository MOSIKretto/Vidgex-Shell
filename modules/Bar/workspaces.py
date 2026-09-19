import json
import random
import re
import subprocess
from time import time

from fabric.widgets.box import Box
from fabric.widgets.label import Label
from fabric.widgets.button import Button
from fabric.widgets.revealer import Revealer
from fabric.widgets.eventbox import EventBox

from gi.repository import Gdk, GLib

from services.wayland import WaylandWindow as Window


_CD = 0.2
_TH = 0.5
_SM = Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK
_cursor_hand = None
_LST = 0.0

# Lua-строки настройки анимаций
LUA_VERT = (
    'hl.animation({ leaf = "workspaces", enabled = true, speed = 6, bezier = "overshot", style = "slidevert" }); '
    'hl.animation({ leaf = "workspacesIn", enabled = true, speed = 6, bezier = "overshot", style = "slidevert" }); '
    'hl.animation({ leaf = "workspacesOut", enabled = true, speed = 6, bezier = "overshot", style = "slidevert" })'
)
LUA_HORIZ = (
    'hl.animation({ leaf = "workspaces", enabled = true, speed = 6, bezier = "overshot", style = "slide" }); '
    'hl.animation({ leaf = "workspacesIn", enabled = true, speed = 6, bezier = "overshot", style = "slide" }); '
    'hl.animation({ leaf = "workspacesOut", enabled = true, speed = 6, bezier = "overshot", style = "slide" })'
)


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
        out = subprocess.check_output("hyprctl activeworkspace -j", shell=True, stderr=subprocess.DEVNULL).decode("utf-8")
        match = re.search(r'"id":\s*([0-9]+)', out)
        if match: return int(match.group(1))
    except Exception:
        pass

    try:
        out = subprocess.check_output("hyprctl repl 'hl.get_active_workspace().id'", shell=True, stderr=subprocess.DEVNULL).decode("utf-8")
        match = re.search(r'([0-9]+)', out)
        if match: return int(match.group(1))
    except Exception:
        pass

    return 1


def _build_path(cur_ws, target_ws):
    """Вычисляет пошаговый маршрут в сетке 3x3 со случайным порядком обхода"""
    if cur_ws == target_ws or not (1 <= cur_ws <= 9) or not (1 <= target_ws <= 9):
        return []

    c_r, c_c = (cur_ws - 1) // 3, (cur_ws - 1) % 3
    t_r, t_c = (target_ws - 1) // 3, (target_ws - 1) % 3

    steps = []
    r, c = c_r, c_c

    def step_h():
        nonlocal c
        step_c = 1 if t_c > c else -1
        while c != t_c:
            c += step_c
            ws = r * 3 + c + 1
            steps.append(("H", ws))

    def step_v():
        nonlocal r
        step_r = 1 if t_r > r else -1
        while r != t_r:
            r += step_r
            ws = r * 3 + c + 1
            steps.append(("V", ws))

    # 50% шанс начать с горизонтального движения, 50% с вертикального
    if random.choice([True, False]):
        step_h()
        step_v()
    else:
        step_v()
        step_h()

    return steps


def _navigate_to_workspace(conn, target_ws, action="workspace"):
    """Асинхронное пошаговое выполнение маршрута (как в Bash скрипте)"""
    cur_ws = _get_active_ws(conn)
    if cur_ws == target_ws:
        return

    steps = _build_path(cur_ws, target_ws)
    if not steps:
        return

    def _dispatch_ws(ws):
        cmd_new = f'hl.dsp.window.move({{ workspace = "{ws}" }})' if action == "movetoworkspace" else f'hl.dsp.focus({{ workspace = "{ws}" }})'
        res = subprocess.run(["hyprctl", "dispatch", cmd_new], capture_output=True)
        if res.returncode != 0:
            cmd_old = f"movetoworkspace {ws}" if action == "movetoworkspace" else f"workspace {ws}"
            subprocess.run(["hyprctl", "dispatch", cmd_old], capture_output=True)

    def _run_step(step_idx):
        if step_idx >= len(steps):
            return False

        dir_type, ws = steps[step_idx]

        if dir_type == "V":
            # 1. Применяем вертикальную анимацию
            subprocess.run(["hyprctl", "eval", LUA_VERT], capture_output=True)

            def do_v_dispatch():
                # 2. Переключаем воркспейс
                _dispatch_ws(ws)
                # 3. Возвращаем горизонтальную анимацию обратно
                subprocess.run(["hyprctl", "eval", LUA_HORIZ], capture_output=True)
                # Пауза ANIM_DELAY (150мс) перед следующим шагом
                GLib.timeout_add(150, lambda: _run_step(step_idx + 1))
                return False

            # Пауза 50мс для применения стилей в памяти композитора
            GLib.timeout_add(50, do_v_dispatch)
        else:
            # Горизонтальный шаг
            _dispatch_ws(ws)
            # Пауза ANIM_DELAY (150мс) перед следующим шагом
            GLib.timeout_add(150, lambda: _run_step(step_idx + 1))

        return False

    # Запускаем выполнение с первого шага
    _run_step(0)


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
        target_ws = cur_row * 3 + target_col + 1

        _navigate_to_workspace(self.conn, target_ws)
        return True

    def _on_dot_clicked(self, target_col):
        ws = _get_active_ws(self.conn)
        if ws < 1 or ws > 9: ws = 5
        current_row = (ws - 1) // 3
        target_ws = current_row * 3 + target_col + 1

        _navigate_to_workspace(self.conn, target_ws)

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
        target_ws = target_row * 3 + cur_col + 1

        _navigate_to_workspace(self.conn, target_ws)
        return True

    def _on_dot_clicked(self, target_row):
        ws = _get_active_ws(self.conn)
        if ws < 1 or ws > 9: ws = 5
        current_col = (ws - 1) % 3
        target_ws = target_row * 3 + current_col + 1

        _navigate_to_workspace(self.conn, target_ws)

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

        self._bar_width = 60
        self._bar_height = 200

        self._ws_switch_active = False
        self._ws_switch_timer_id = None

        self._init_ui()
        self._bind_events()

    def _parse(self, cmd):
        try:
            s = self.conn.send_command(cmd).reply.decode()
            data = json.loads(s)
            return data
        except Exception:
            return None

    def _init_ui(self):
        self.ws = LeftWorkspaces(self.conn, v_align="start", h_align="start")

        self.wrapper = Box(name="bar-inner", children=[self.ws], orientation="v")
        self.wrapper.connect("size-allocate", self._on_size_allocate)

        self.revealer = Revealer(
            name="sidebar-revealer",
            transition_type="slide-right",
            child_revealed=True,
            child=self.wrapper,
        )

        self.activator = Box(style="background: transparent;")
        self.activator.set_size_request(15, -1)

        layout_box = Box(orientation="h", children=[self.revealer, self.activator])

        self.main_eb = EventBox(child=layout_box)
        self.main_eb.connect("enter-notify-event", self._on_hover_enter)
        self.main_eb.connect("leave-notify-event", self._on_hover_leave)

        self.add(self.main_eb)

    def _on_size_allocate(self, _, alloc):
        if alloc.width > 20:
            self._bar_width = alloc.width
        if alloc.height > 20:
            self._bar_height = alloc.height

    def _trigger_ws_switch(self, *_):
        self._ws_switch_active = True
        self._is_hidden = False
        self.revealer.set_reveal_child(True)

        if self._ws_switch_timer_id:
            GLib.source_remove(self._ws_switch_timer_id)

        # Длительность показа панели увеличена до 1.5 сек, чтобы успели завершиться все пошаговые анимации
        self._ws_switch_timer_id = GLib.timeout_add(1500, self._on_ws_switch_timeout)

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
        c.connect("event::resizewindow", self._schedule_occlusion)
        c.connect("event::activewindow", self._schedule_occlusion)
        c.connect("event::changefloatingmode", self._schedule_occlusion)
        c.connect("event::fullscreen", self._schedule_occlusion)
        c.connect("event::pin", self._schedule_occlusion)

        c.connect("event::workspace", self._trigger_ws_switch)

        if c.ready:
            GLib.idle_add(self._do_occlusion)
        else:
            c.connect("event::ready", lambda *_: GLib.idle_add(self._do_occlusion))

    def _schedule_occlusion(self, *_):
        if not self._pending_occlusion:
            self._pending_occlusion = True
            GLib.timeout_add(50, self._do_occlusion)

    def _do_occlusion(self):
        self._pending_occlusion = False
        self._check_occlusion()
        return False

    def _get_monitor_info(self):
        monitors = self._parse("j/monitors")
        if not isinstance(monitors, list):
            return None

        for m in monitors:
            if not isinstance(m, dict):
                continue
            m_id = m.get("id")
            if m_id == self.monitor_id or str(m_id) == str(self.monitor_id):
                active_ws = m.get("activeWorkspace", {})
                ws_id = active_ws.get("id", 1) if isinstance(active_ws, dict) else 1
                return {
                    "x": m.get("x", 0),
                    "y": m.get("y", 0),
                    "active_ws_id": ws_id
                }
        return None

    def _check_occlusion(self):
        mon_info = self._get_monitor_info()
        if not mon_info:
            return

        mon_x = mon_info["x"]
        mon_y = mon_info["y"]
        active_ws_id = mon_info["active_ws_id"]

        bw = self._bar_width if self._bar_width > 10 else 60
        bh = self._bar_height if self._bar_height > 10 else 200

        panel_x1 = mon_x
        panel_x2 = mon_x + bw
        panel_y1 = mon_y
        panel_y2 = mon_y + bh

        clients = self._parse("j/clients")
        if not isinstance(clients, list):
            clients = []

        overlap = False
        for w in clients:
            if not isinstance(w, dict):
                continue

            if w.get("hidden") or w.get("minimized") or not w.get("mapped", True):
                continue

            w_ws = w.get("workspace", {})
            w_ws_id = w_ws.get("id") if isinstance(w_ws, dict) else w_ws
            if w_ws_id != active_ws_id or w_ws_id < 1:
                continue

            w_mon = w.get("monitor")
            w_mon_id = w_mon.get("id") if isinstance(w_mon, dict) else w_mon
            if w_mon_id != self.monitor_id and str(w_mon_id) != str(self.monitor_id):
                continue

            pos, size = w.get("at"), w.get("size")
            if not pos or not size or len(pos) < 2 or len(size) < 2:
                continue

            wx, wy = pos[0], pos[1]
            ww, wh = size[0], size[1]

            win_x1, win_x2 = wx, wx + ww
            win_y1, win_y2 = wy, wy + wh

            if (win_x1 < panel_x2) and (win_x2 > panel_x1) and (win_y1 < panel_y2) and (win_y2 > panel_y1):
                overlap = True
                break

        should_hide = overlap and not self._mouse_over and not self._ws_switch_active

        if should_hide != self._is_hidden:
            self._is_hidden = should_hide
            self.revealer.set_reveal_child(not should_hide)

    def _on_hover_enter(self, widget, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self._mouse_over = True
        self._is_hidden = False
        self.revealer.set_reveal_child(True)
        return False

    def _on_hover_leave(self, widget, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self._mouse_over = False
        self._schedule_occlusion()
        return False