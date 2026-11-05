import json
import os
import re
import signal

from gi.repository import GLib


_WS_ID_RE = re.compile(r'"id":\s*(-?[0-9]+)')
_HEX_ADDR_RE = re.compile(r"^(?:0x)?[0-9a-fA-F]+$")
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_ORDER_FILE = os.path.expanduser("~/.cache/vidgex-shell/matrix_order")
_NAV_DELTAS = {
    "nextR": (0, 1),
    "nextL": (0, -1),
    "nextD": (1, 0),
    "nextU": (-1, 0),
}


class _MatrixState:
    __slots__ = ("rows", "cols", "order_actions_visible")

    def __init__(self, rows: int, cols: int):
        self.rows = rows
        self.cols = cols
        self.order_actions_visible = False


def _lua_anim(style: str) -> str:
    return "; ".join(
        f'hl.animation({{ leaf = "{leaf}", enabled = true, speed = 6, bezier = "overshot", style = "{style}" }})'
        for leaf in ("workspaces", "workspacesIn", "workspacesOut")
    )


def cancel_source(tid: int) -> int:
    if tid:
        GLib.source_remove(tid)
    return 0


def _save_matrix_dims(r: int, c: int):
    try:
        os.makedirs(os.path.dirname(_ORDER_FILE), exist_ok=True)
        with open(_ORDER_FILE, "w", encoding="utf-8") as f:
            f.write(f"{r}x{c}")
    except OSError:
        # Файловая система может быть недоступна для записи (RO-раздел, переполнение диска)
        pass


def _load_matrix_dims() -> tuple[int, int]:
    try:
        with open(_ORDER_FILE, "r", encoding="utf-8") as f:
            raw = f.read().strip()
        parts = raw.split("x", 1) if "x" in raw else (raw, raw)
        r = max(1, min(9, int(parts[0])))
        c = max(1, min(9, int(parts[1])))
        if raw != f"{r}x{c}":
            _save_matrix_dims(r, c)
        return r, c
    except (OSError, ValueError):
        # Файл отсутствует при первом запуске или повреждён вручную
        pass
    _save_matrix_dims(3, 3)
    return 3, 3


state = _MatrixState(*_load_matrix_dims())
_conn = None
_ws_hid = 0
_mon_hid = 0
_ready_hid = 0
_occlusion_hids: list[int] = []
_occlusion_tid = 0
_vert_step_tid = 0
_close_tid = 0
_rules_initialized = False
axis_widgets: list = []
sidebars: list = []


def ipc_raw(cmd: str, conn=None) -> str:
    c = conn or _conn
    if c is None or not c.ready:
        return ""
    reply = c.send_command(cmd).reply
    return reply.decode("utf-8", errors="replace") if isinstance(reply, bytes) else str(reply)


def ipc_json(cmd: str, default, conn=None):
    raw = ipc_raw(cmd, conn)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Hyprland IPC может вернуть обрезанный JSON или текстовую ошибку при гонке состояний
        return default


def get_active_ws(conn=None) -> int:
    match = _WS_ID_RE.search(ipc_raw("j/activeworkspace", conn))
    return int(match.group(1)) if match else 1


def clamp_coords(ws: int, r: int = 0, c: int = 0) -> tuple[int, int]:
    r = r or state.rows
    c = c or state.cols
    if ws < 1 or ws > r * c:
        return 0, 0
    return (ws - 1) // c, (ws - 1) % c


def client_ws_id(client: dict) -> int:
    ws_info = client.get("workspace", {})
    ws_id = ws_info.get("id", 1) if isinstance(ws_info, dict) else ws_info
    try:
        return int(ws_id) if ws_id is not None else 1
    except (TypeError, ValueError):
        # Нестандартный ответ IPC при невалидном дескрипторе рабочего стола
        return 1


def dispatch_ws(ws: int, action: str = "workspace", conn=None):
    fn = "window.move" if action == "movetoworkspace" else "focus"
    ipc_raw(f'dispatch hl.dsp.{fn}({{ workspace = "{int(ws)}" }})', conn)


def _cancel_vert_step(conn=None):
    global _vert_step_tid
    if _vert_step_tid:
        _vert_step_tid = cancel_source(_vert_step_tid)
        ipc_raw(f"eval {_lua_anim('slide')}", conn)


def perform_ws_transition(target_ws: int, vertical: bool, action: str = "workspace", conn=None):
    global _vert_step_tid
    c = conn or _conn
    if c is None or not c.ready:
        return

    had_pending = _vert_step_tid != 0
    _vert_step_tid = cancel_source(_vert_step_tid)

    if vertical:
        if not had_pending:
            ipc_raw(f"eval {_lua_anim('slidevert')}", c)

        def _step():
            global _vert_step_tid
            _vert_step_tid = 0
            dispatch_ws(target_ws, action, c)
            ipc_raw(f"eval {_lua_anim('slide')}", c)
            return False

        _vert_step_tid = GLib.timeout_add(50, _step)
    else:
        if had_pending:
            ipc_raw(f"eval {_lua_anim('slide')}", c)
        dispatch_ws(target_ws, action, c)


def _apply_persistent_rules(r: int, c: int, conn=None) -> bool:
    target_conn = conn or _conn
    if target_conn is None or not target_conn.ready:
        return False
    total = int(r) * int(c)
    lua_code = (
        f"for i = 1, 81 do "
        f"hl.workspace_rule({{ workspace = tostring(i), persistent = i <= {total} }}); "
        f"end"
    )
    cmds = [f"eval {lua_code}"] + [f"keyword workspace {i},persistent:false" for i in range(total + 1, 82)]
    ipc_raw(f"[[BATCH]]{' ; '.join(cmds)}", target_conn)
    return True


def _on_workspace_event(*_):
    if (not axis_widgets and not sidebars) or _conn is None or not _conn.ready:
        return
    ws = get_active_ws(_conn)
    for w in tuple(axis_widgets):
        w._update_ui(ws=ws)
    for sb in tuple(sidebars):
        sb._trigger_ws_switch(ws=ws)


def sync_occlusion_watch():
    global _occlusion_tid
    need = _conn is not None and any(sb._needs_occlusion_watch() for sb in sidebars)
    if need and not _occlusion_hids:
        for ev in (
            "event::openwindow",
            "event::closewindow",
            "event::movewindow",
            "event::resizewindow",
            "event::activewindow",
            "event::changefloatingmode",
            "event::fullscreen",
            "event::pin",
        ):
            _occlusion_hids.append(_conn.connect(ev, request_occlusion_check))
    elif not need:
        if _conn is not None:
            while _occlusion_hids:
                _conn.disconnect(_occlusion_hids.pop())
        else:
            _occlusion_hids.clear()
        _occlusion_tid = cancel_source(_occlusion_tid)


def request_occlusion_check(*_):
    global _occlusion_tid
    if _occlusion_tid:
        return
    if _conn is None or not any(sb._needs_occlusion_watch() for sb in sidebars):
        sync_occlusion_watch()
        return
    _occlusion_tid = GLib.timeout_add(50, _run_occlusion, priority=GLib.PRIORITY_DEFAULT_IDLE)


def _run_occlusion() -> bool:
    global _occlusion_tid
    _occlusion_tid = 0
    active_sbs = [sb for sb in sidebars if sb._needs_occlusion_watch()]
    if not active_sbs or _conn is None or not _conn.ready:
        sync_occlusion_watch()
        return False
    monitors = ipc_json("j/monitors", [], _conn)
    clients = ipc_json("j/clients", [], _conn) if monitors else []
    for sb in active_sbs:
        sb._apply_occlusion_data(monitors, clients)
    return False


def _disconnect_conn():
    global _ws_hid, _mon_hid, _ready_hid, _occlusion_tid, _close_tid, _rules_initialized
    _cancel_vert_step(_conn)
    _close_tid = cancel_source(_close_tid)
    _occlusion_tid = cancel_source(_occlusion_tid)
    if _conn is not None:
        if _ws_hid:
            _conn.disconnect(_ws_hid)
            _ws_hid = 0
        if _mon_hid:
            _conn.disconnect(_mon_hid)
            _mon_hid = 0
        if _ready_hid:
            _conn.disconnect(_ready_hid)
            _ready_hid = 0
        while _occlusion_hids:
            _conn.disconnect(_occlusion_hids.pop())
    _rules_initialized = False


def bind_global_conn(conn):
    global _conn, _ws_hid, _mon_hid, _ready_hid
    if conn is None:
        return
    if _conn is not conn:
        _disconnect_conn()
        _conn = conn
    if not _ws_hid:
        _ws_hid = conn.connect("event::workspace", _on_workspace_event)
    if not _mon_hid:
        _mon_hid = conn.connect("event::focusedmon", _on_workspace_event)

    sync_occlusion_watch()
    if _rules_initialized or _ready_hid:
        return

    def _init_ready(*_):
        global _ready_hid, _rules_initialized
        if _ready_hid:
            conn.disconnect(_ready_hid)
            _ready_hid = 0
        if _conn is not conn:
            return
        _rules_initialized = _apply_persistent_rules(state.rows, state.cols, conn)
        ws = get_active_ws(conn)
        for w in tuple(axis_widgets):
            w._update_ui(ws=ws)
        for sb in tuple(sidebars):
            sb._last_row = sb.ws.get_active_row(ws)
            sb._schedule_occlusion()

    if conn.ready:
        _init_ready()
    else:
        _ready_hid = conn.connect("event::ready", _init_ready)


def release_global_conn_if_unused():
    global _conn
    if axis_widgets or sidebars:
        sync_occlusion_watch()
        return
    _disconnect_conn()
    _conn = None


def joint_flush() -> bool:
    return (
        state.rows > 1
        and state.cols == 1
        and not state.order_actions_visible
        and any(sb.is_panel_visible() for sb in sidebars)
    )


def sync_joint():
    flush = joint_flush()
    for w in tuple(axis_widgets):
        w.set_flush(flush)


def toggle_order_actions(*_):
    state.order_actions_visible = not state.order_actions_visible
    for w in tuple(axis_widgets):
        w.sync_visibility()
    for sb in tuple(sidebars):
        sb.set_force_revealed(state.order_actions_visible)
    sync_joint()


def _protect_ancestors(pid: int, protected: set[int]):
    # Предков живого процесса убивать нельзя: SIGKILL по дереву предка снёс бы и самого потомка.
    while pid > 1:
        try:
            with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as f:
                # comm может содержать пробелы и скобки, поэтому берём хвост после последней ")"
                ppid = int(f.read().rpartition(")")[2].split()[1])
        except (OSError, ValueError, IndexError):
            # Процесс успел завершиться между чтением j/clients и обходом /proc
            return
        if ppid <= 1 or ppid in protected:
            # Дошли до init/ядра или до предка, чья цепочка уже защищена
            return
        protected.add(ppid)
        pid = ppid


def _kill_process_tree(root_pid: int, protected: set[int]):
    pids = [root_pid]
    seen = {root_pid}
    for cur in pids:
        task_dir = f"/proc/{cur}/task"
        try:
            tids = os.listdir(task_dir)
        except OSError:
            # Процесс завершился до чтения его потоков
            tids = (str(cur),)
        for tid in tids:
            try:
                with open(f"{task_dir}/{tid}/children", "r", encoding="utf-8") as f:
                    for token in f.read().split():
                        cpid = int(token)
                        if cpid > 1 and cpid not in seen and cpid not in protected:
                            seen.add(cpid)
                            pids.append(cpid)
            except (OSError, ValueError):
                # Поток завершился во время обхода /proc
                pass

    for pid in reversed(pids):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            # Процесс уже мёртв или принадлежит другому пользователю (root/polkit)
            pass


def _close_excess_windows(new_max: int, force: bool = False, conn=None) -> bool:
    clients = ipc_json("j/clients", [], conn)
    if not clients:
        return False

    # live_pids: процессы, владеющие окнами на остающихся столах, и сам шелл
    live_pids = {os.getpid()}
    cmds = []
    candidates = set()

    for c in clients:
        if not isinstance(c, dict) or not c.get("mapped", True):
            continue
        raw_pid = c.get("pid")
        pid = int(raw_pid) if isinstance(raw_pid, int) else 0
        if client_ws_id(c) <= new_max:
            if pid > 1:
                live_pids.add(pid)
            continue
        addr = c.get("address")
        if addr is not None:
            addr_str = str(addr)
            if _HEX_ADDR_RE.match(addr_str):
                s = addr_str if addr_str.startswith("0x") else f"0x{addr_str}"
                # Только адресный диспатч: он закрывает строго указанное окно.
                # Lua-вариант с ключом address закрывал АКТИВНОЕ окно (после смены стола — окно на остающемся столе).
                cmds.append(f"dispatch closewindow address:{s}")
        if force and pid > 1:
            candidates.add(pid)

    if cmds:
        ipc_raw(f"[[BATCH]]{' ; '.join(cmds)}", conn)

    targets = candidates - live_pids
    if targets:
        # Дерево убиваемого процесса не должно содержать живых окон: защищаем всех предков живых
        for pid in tuple(live_pids):
            _protect_ancestors(pid, live_pids)
        for pid in targets - live_pids:
            _kill_process_tree(pid, live_pids)

    return bool(cmds or targets)


def matrix_nav(action: str, direction: str):
    if direction not in _NAV_DELTAS:
        return
    ws = get_active_ws(_conn)
    r, c = clamp_coords(ws)
    dr, dc = _NAV_DELTAS[direction]
    next_ws = ((r + dr) % state.rows) * state.cols + ((c + dc) % state.cols) + 1
    if next_ws != ws:
        perform_ws_transition(next_ws, dr != 0, action, _conn)


def set_matrix_dims(new_rows: int, new_cols: int, conn=None):
    global _close_tid, _rules_initialized
    new_rows = max(1, min(9, int(new_rows)))
    new_cols = max(1, min(9, int(new_cols)))
    if new_rows == state.rows and new_cols == state.cols:
        return

    _cancel_vert_step(conn)
    _close_tid = cancel_source(_close_tid)

    old_rows, old_cols = state.rows, state.cols
    old_max = old_rows * old_cols
    state.rows, state.cols = new_rows, new_cols
    _save_matrix_dims(state.rows, state.cols)
    new_max = state.rows * state.cols

    cur_ws = get_active_ws(conn)
    cur_r, cur_c = clamp_coords(cur_ws, old_rows, old_cols)
    target_ws = min(state.rows - 1, cur_r) * state.cols + min(state.cols - 1, cur_c) + 1
    if target_ws != cur_ws:
        dispatch_ws(target_ws, "workspace", conn)
        cur_ws = target_ws

    if new_max < old_max:
        monitors = ipc_json("j/monitors", [], conn)
        focused = next(
            (m.get("name") for m in monitors if isinstance(m, dict) and m.get("focused")),
            None,
        )
        mon_cmds = []
        for m in monitors:
            if not isinstance(m, dict) or m.get("focused"):
                continue
            name = m.get("name")
            active_id = m.get("activeWorkspace", {}).get("id", 1) if isinstance(m.get("activeWorkspace"), dict) else 1
            if name and _SAFE_NAME_RE.match(str(name)) and active_id > new_max:
                mon_cmds.extend((f"dispatch focusmonitor {name}", f"dispatch workspace {new_max}"))
        if mon_cmds:
            if focused and _SAFE_NAME_RE.match(str(focused)):
                mon_cmds.append(f"dispatch focusmonitor {focused}")
            ipc_raw(f"[[BATCH]]{' ; '.join(mon_cmds)}", conn)

        if _close_excess_windows(new_max, force=False, conn=conn):
            def _force_close():
                global _close_tid
                _close_tid = 0
                _close_excess_windows(state.rows * state.cols, force=True, conn=conn)
                return False

            _close_tid = GLib.timeout_add(250, _force_close)

    if _apply_persistent_rules(state.rows, state.cols, conn):
        _rules_initialized = True

    for w in tuple(axis_widgets):
        w.on_matrix_order_changed(cur_ws)
    for sb in tuple(sidebars):
        sb.on_matrix_dims_changed(cur_ws)
    sync_joint()


def set_matrix_order(new_order: int, conn=None):
    set_matrix_dims(new_order, new_order, conn=conn)