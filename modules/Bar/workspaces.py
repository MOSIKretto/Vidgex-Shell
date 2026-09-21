import json
import os
import re
import signal
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
_LST = 0.0

# Путь для сохранения порядка матрицы между перезапусками
_CACHE_DIR = os.path.expanduser("~/.cache/vidgex-shell")
_ORDER_FILE = os.path.join(_CACHE_DIR, "matrix_order")


def _load_matrix_order():
    if not os.path.exists(_ORDER_FILE):
        _save_matrix_order(3)

    with open(_ORDER_FILE, "r") as f:
        val = int(f.read().strip())
        return max(1, min(9, val))


def _save_matrix_order(order):
    os.makedirs(_CACHE_DIR, exist_ok=True)
    with open(_ORDER_FILE, "w") as f:
        f.write(str(order))


# Глобальное состояние
_MATRIX_ORDER = _load_matrix_order()
_GLOBAL_CONN = None

# Реестр активных компонентов
_top_workspaces = []
_left_workspaces = []
_sidebars = []
_hover_timer_id = None

# Lua-строки анимаций
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


def _apply_persistent_rules(order):
    """Применяет persistent = true только для столов 1..order^2, для остальных снимает persistent"""
    total = order * order
    lua_code = (
        f"for i = 1, 81 do "
        f"  if i <= {total} then "
        f'    hl.workspace_rule({{ workspace = tostring(i), persistent = true }}); '
        f"  else "
        f'    hl.workspace_rule({{ workspace = tostring(i), persistent = false }}); '
        f"  end "
        f"end"
    )
    subprocess.run(["hyprctl", "eval", lua_code], capture_output=True)

    for i in range(total + 1, 82):
        subprocess.run(["hyprctl", "keyword", "workspace", f"{i},persistent:false"], capture_output=True)


_apply_persistent_rules(_MATRIX_ORDER)


def _apply_order_actions_visible(visible: bool):
    """Показывает или скрывает кнопки '+' и '-' и управляет выезжанием левой панели"""
    for top in _top_workspaces:
        top.revealer_plus.set_reveal_child(visible)
    for left in _left_workspaces:
        left.revealer_minus.set_reveal_child(visible)
    for sb in _sidebars:
        sb.set_force_revealed(visible)


def _schedule_order_actions(visible: bool):
    """Управляет задержкой при переходе курсора между цифрой и кнопками '+' / '-'"""
    global _hover_timer_id
    if _hover_timer_id:
        GLib.source_remove(_hover_timer_id)
        _hover_timer_id = None

    if visible:
        _apply_order_actions_visible(True)
    else:
        def _hide():
            global _hover_timer_id
            _hover_timer_id = None
            _apply_order_actions_visible(False)
            return False

        _hover_timer_id = GLib.timeout_add(400, _hide)


def _dispatch_ws(ws, action="workspace"):
    cmd_new = f'hl.dsp.window.move({{ workspace = "{ws}" }})' if action == "movetoworkspace" else f'hl.dsp.focus({{ workspace = "{ws}" }})'
    subprocess.run(["hyprctl", "dispatch", cmd_new], capture_output=True)


def _get_active_ws(conn=None):
    c = conn or _GLOBAL_CONN
    res = c.send_command("j/activeworkspace")
    if hasattr(res, "reply"): res = res.reply
    if isinstance(res, bytes): res = res.decode("utf-8")
    match = re.search(r'"id":\s*([0-9]+)', str(res))
    return int(match.group(1))


def _switch_workspace(conn, target_ws, action="workspace"):
    cur_ws = _get_active_ws(conn)
    if cur_ws == target_ws:
        return

    order = _MATRIX_ORDER
    cur_row = (cur_ws - 1) // order
    target_row = (target_ws - 1) // order

    if cur_row != target_row:
        subprocess.run(["hyprctl", "eval", LUA_VERT], capture_output=True)

        def do_step():
            _dispatch_ws(target_ws, action)
            subprocess.run(["hyprctl", "eval", LUA_HORIZ], capture_output=True)
            return False

        GLib.timeout_add(50, do_step)
    else:
        _dispatch_ws(target_ws, action)


def _get_clients_list():
    """Получает список всех открытых окон через сокет"""
    res = _GLOBAL_CONN.send_command("j/clients")
    if hasattr(res, "reply"): res = res.reply
    if isinstance(res, bytes): res = res.decode("utf-8")
    return json.loads(str(res))


def _close_excess_windows(new_max, force=False):
    """Закрывает все окна, находящиеся на столах с номером больше new_max"""
    clients = _get_clients_list()

    for client in clients:
        ws_info = client.get("workspace", {})
        ws_id = ws_info.get("id") if isinstance(ws_info, dict) else ws_info
        ws_id = int(ws_id)

        # Обрабатываем только окна на обычных столах выше допустимого максимума
        if ws_id > new_max:
            addr = client.get("address")
            pid = client.get("pid")

            if addr:
                addr_str = str(addr)
                if not addr_str.startswith("0x"):
                    addr_str = f"0x{addr_str}"

                # 1. Запрос закрытия окна через CLI hyprctl (с раздельными аргументами)
                subprocess.run(["hyprctl", "dispatch", "closewindow", f"address:{addr_str}"], capture_output=True)

                # 2. Запрос закрытия окна через Lua
                subprocess.run(["hyprctl", "dispatch", f'hl.dsp.window.close({{ address = "{addr_str}" }})'], capture_output=True)

                # 3. Запрос закрытия через прямой сокет Fabric
                if _GLOBAL_CONN:
                    _GLOBAL_CONN.send_command(f"dispatch closewindow address:{addr_str}")

            # Принудительное закрытие процесса, если окно упорствует при повторном проходе
            if force and pid:
                os.kill(int(pid), signal.SIGTERM)


def matrix_nav(action, direction):
    """Функция навигации по стрелкам для вызова через fabric-cli"""
    order = _MATRIX_ORDER
    ws = _get_active_ws(_GLOBAL_CONN)

    row = (ws - 1) // order
    col = (ws - 1) % order

    if direction == "nextR":
        col = (col + 1) % order
    elif direction == "nextL":
        col = (col + order - 1) % order
    elif direction == "nextD":
        row = (row + 1) % order
    elif direction == "nextU":
        row = (row + order - 1) % order
    else:
        return

    next_ws = row * order + col + 1

    if direction in ("nextU", "nextD"):
        subprocess.run(["hyprctl", "eval", LUA_VERT], capture_output=True)

        def do_step():
            _dispatch_ws(next_ws, action)
            subprocess.run(["hyprctl", "eval", LUA_HORIZ], capture_output=True)
            return False

        GLib.timeout_add(50, do_step)
    else:
        _dispatch_ws(next_ws, action)


def set_matrix_order(new_order, conn=None):
    global _MATRIX_ORDER
    new_order = max(1, min(9, new_order))
    if new_order == _MATRIX_ORDER:
        return

    old_order = _MATRIX_ORDER
    _MATRIX_ORDER = new_order
    _save_matrix_order(_MATRIX_ORDER)
    new_max = new_order * new_order

    if new_order < old_order:
        # 1. Если активный стол превышает новый максимум — переводим фокус на new_max
        cur_ws = _get_active_ws(conn)
        if cur_ws > new_max:
            _dispatch_ws(new_max, "workspace")

        # 2. Переводим мониторы, если они смотрят на удаляемые столы
        m_data = subprocess.check_output("hyprctl monitors -j", shell=True, stderr=subprocess.DEVNULL).decode("utf-8")
        for m in json.loads(m_data):
            m_ws = m.get("activeWorkspace", {}).get("id", 1)
            if m_ws > new_max:
                subprocess.run(["hyprctl", "dispatch", f"focusmonitor {m.get('name')}"], capture_output=True)
                subprocess.run(["hyprctl", "dispatch", f"workspace {new_max}"], capture_output=True)

        _close_excess_windows(new_max, force=False)

        GLib.timeout_add(150, lambda: _close_excess_windows(new_max, force=True) or False)
        GLib.timeout_add(350, lambda: _close_excess_windows(new_max, force=True) or False)

    _apply_persistent_rules(_MATRIX_ORDER)

    for top in _top_workspaces:
        top.on_matrix_order_changed(_MATRIX_ORDER)
    for left in _left_workspaces:
        left.on_matrix_order_changed(_MATRIX_ORDER)


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
        global _GLOBAL_CONN
        _GLOBAL_CONN = conn
        _top_workspaces.append(self)
        self.connect("destroy", lambda *_: _top_workspaces.remove(self))

        self.inner_box = Box(orientation="h", spacing=0)

        self.lbl_num = Label(label=str(_MATRIX_ORDER))
        self.btn_num = Button(child=self.lbl_num, h_align="center", v_align="center", can_focus=False)
        self.btn_num.add_style_class("active")

        self.num_box = Box(name="workspaces-num-top", children=[self.btn_num])

        self.btn_plus = Button(child=Label(label="+"), h_align="center", v_align="center", can_focus=False)
        self.btn_plus.connect("clicked", lambda _: set_matrix_order(_MATRIX_ORDER + 1, self.conn))

        self.revealer_plus = Revealer(
            transition_type="slide-right",
            transition_duration=200,
            child_revealed=False,
            child=Box(name="workspaces-action-top", children=[self.btn_plus])
        )

        self.order_box = Box(orientation="h", spacing=0, children=[self.num_box, self.revealer_plus])
        self.order_eb = EventBox(child=self.order_box)
        self.order_eb.connect("enter-notify-event", self._on_order_enter)
        self.order_eb.connect("leave-notify-event", self._on_order_leave)

        self.inner_box.add(self.order_eb)

        self.dots_box = Box(name="workspaces-top", orientation="h", spacing=8)
        self.dots = []
        self._rebuild_dots()

        self.inner_box.add(self.dots_box)

        self.event_box = EventBox(child=self.inner_box)
        self.event_box.add_events(_SM)
        self.event_box.connect("scroll-event", self._on_scroll)

        self.add(self.event_box)

        self.conn.connect("event::workspace", self._update_ui)
        self._update_ui()

    def _on_order_enter(self, _, event):
        if event.detail != Gdk.NotifyType.INFERIOR:
            _schedule_order_actions(True)
        return False

    def _on_order_leave(self, _, event):
        if event.detail != Gdk.NotifyType.INFERIOR:
            _schedule_order_actions(False)
        return False

    def _rebuild_dots(self):
        for child in self.dots_box.get_children():
            self.dots_box.remove(child)
        self.dots = []
        for col in range(_MATRIX_ORDER):
            btn = Button(h_expand=False, v_expand=False, h_align="center", v_align="center", can_focus=False)
            btn.connect("clicked", lambda _, c=col: self._on_dot_clicked(c))
            btn.add_events(_SM)
            btn.connect("scroll-event", self._on_scroll)
            self.dots.append(btn)
            self.dots_box.add(btn)
        self.dots_box.show_all()

    def on_matrix_order_changed(self, new_order):
        self.lbl_num.set_label(str(new_order))
        self._rebuild_dots()
        self._update_ui()

    def _on_scroll(self, _, event):
        direction = _check_scroll_cooldown(event, axis='x')
        if direction == 0: return False

        order = _MATRIX_ORDER
        ws = _get_active_ws(self.conn)
        cur_row = (ws - 1) // order
        cur_col = (ws - 1) % order

        target_col = (cur_col + direction) % order
        target_ws = cur_row * order + target_col + 1

        _switch_workspace(self.conn, target_ws)
        return True

    def _on_dot_clicked(self, target_col):
        order = _MATRIX_ORDER
        ws = _get_active_ws(self.conn)
        current_row = (ws - 1) // order
        target_ws = current_row * order + target_col + 1

        _switch_workspace(self.conn, target_ws)

    def _update_ui(self, *_):
        ws = _get_active_ws(self.conn)
        order = _MATRIX_ORDER
        self.lbl_num.set_label(str(order))

        cur_col = (ws - 1) % order
        in_bounds = (1 <= ws <= order * order)

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
        global _GLOBAL_CONN
        _GLOBAL_CONN = conn
        _left_workspaces.append(self)
        self.connect("destroy", lambda *_: _left_workspaces.remove(self))

        self.inner_box = Box(orientation="v", spacing=0, h_align="center")

        self.btn_minus = Button(child=Label(label="-"), h_align="center", v_align="center", can_focus=False)
        self.btn_minus.connect("clicked", lambda _: set_matrix_order(_MATRIX_ORDER - 1, self.conn))

        self.revealer_minus = Revealer(
            transition_type="slide-down",
            transition_duration=200,
            child_revealed=False,
            child=Box(name="workspaces-action-left", children=[self.btn_minus], h_align="center")
        )

        self.action_eb = EventBox(child=self.revealer_minus)
        self.action_eb.connect("enter-notify-event", self._on_action_enter)
        self.action_eb.connect("leave-notify-event", self._on_action_leave)

        self.inner_box.add(self.action_eb)

        self.dots_box = Box(name="workspaces-left", orientation="v", spacing=8, h_align="center")
        self.dots = []
        self._rebuild_dots()

        self.inner_box.add(self.dots_box)

        self.event_box = EventBox(child=self.inner_box)
        self.event_box.add_events(_SM)
        self.event_box.connect("scroll-event", self._on_scroll)

        self.add(self.event_box)

        self.conn.connect("event::workspace", self._update_ui)
        self._update_ui()

    def _on_action_enter(self, _, event):
        if event.detail != Gdk.NotifyType.INFERIOR:
            _schedule_order_actions(True)
        return False

    def _on_action_leave(self, _, event):
        if event.detail != Gdk.NotifyType.INFERIOR:
            _schedule_order_actions(False)
        return False

    def _rebuild_dots(self):
        for child in self.dots_box.get_children():
            self.dots_box.remove(child)
        self.dots = []
        for row in range(_MATRIX_ORDER):
            btn = Button(h_expand=False, v_expand=False, h_align="center", v_align="center", can_focus=False)
            btn.add_style_class("row-dot")
            btn.connect("clicked", lambda _, r=row: self._on_dot_clicked(r))
            btn.add_events(_SM)
            btn.connect("scroll-event", self._on_scroll)
            self.dots.append(btn)
            self.dots_box.add(btn)
        self.dots_box.show_all()

    def on_matrix_order_changed(self, new_order):
        self._rebuild_dots()
        self._update_ui()

    def _on_scroll(self, _, event):
        direction = _check_scroll_cooldown(event, axis='y')
        if direction == 0: return False

        order = _MATRIX_ORDER
        ws = _get_active_ws(self.conn)
        cur_row = (ws - 1) // order
        cur_col = (ws - 1) % order

        target_row = (cur_row + direction) % order
        target_ws = target_row * order + cur_col + 1

        _switch_workspace(self.conn, target_ws)
        return True

    def _on_dot_clicked(self, target_row):
        order = _MATRIX_ORDER
        ws = _get_active_ws(self.conn)
        current_col = (ws - 1) % order
        target_ws = target_row * order + current_col + 1

        _switch_workspace(self.conn, target_ws)

    def _update_ui(self, *_):
        ws = _get_active_ws(self.conn)
        order = _MATRIX_ORDER
        cur_row = (ws - 1) // order
        in_bounds = (1 <= ws <= order * order)

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
        self._force_revealed = False

        self._bar_width = 36
        self._bar_height = 200

        self._ws_switch_active = False
        self._ws_switch_timer_id = None

        _sidebars.append(self)
        self.connect("destroy", lambda *_: _sidebars.remove(self))

        self._init_ui()
        self._bind_events()

    def set_force_revealed(self, force):
        self._force_revealed = force
        if force:
            self._is_hidden = False
            self.revealer.set_reveal_child(True)
        else:
            self._schedule_occlusion()

    def _parse(self, cmd):
        s = self.conn.send_command(cmd).reply.decode()
        return json.loads(s)

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

        for m in monitors:
            m_id = m.get("id")
            if m_id == self.monitor_id or str(m_id) == str(self.monitor_id):
                active_ws = m.get("activeWorkspace", {})
                ws_id = active_ws.get("id", 1) if isinstance(active_ws, dict) else 1
                return {
                    "x": m.get("x", 0),
                    "y": m.get("y", 0),
                    "active_ws_id": ws_id
                }

    def _check_occlusion(self):
        mon_info = self._get_monitor_info()

        mon_x = mon_info["x"]
        mon_y = mon_info["y"]
        active_ws_id = mon_info["active_ws_id"]

        bw = self._bar_width
        bh = self._bar_height

        panel_x1 = mon_x
        panel_x2 = mon_x + bw
        panel_y1 = mon_y
        panel_y2 = mon_y + bh

        clients = self._parse("j/clients")

        overlap = False
        for w in clients:
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

            wx, wy = pos[0], pos[1]
            ww, wh = size[0], size[1]

            win_x1, win_x2 = wx, wx + ww
            win_y1, win_y2 = wy, wy + wh

            if (win_x1 < panel_x2) and (win_x2 > panel_x1) and (win_y1 < panel_y2) and (win_y2 > panel_y1):
                overlap = True
                break

        should_hide = overlap and not self._mouse_over and not self._ws_switch_active and not self._force_revealed

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