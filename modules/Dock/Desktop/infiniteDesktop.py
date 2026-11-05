import ctypes
import errno
import fcntl
import functools
import json
import os
import select
import socket
import threading
import time
import evdev
from evdev import ecodes

# ── env ──────────────────────────────────────────────────────────────────────


def _env(name, default, lo=float('-inf'), hi=float('inf')):
    return min(hi, max(lo, type(default)(os.environ.get(name, default))))


SPEED                = _env('SPEED', 1.6, lo=0.05, hi=20.0)
FPS                  = 1.0 / _env('FPS_LIMIT', 60, lo=1, hi=240)
EDGE_ZONE_FRAC       = _env('EDGE_ZONE_FRAC', 0.3, lo=0.02, hi=0.45)
_EDGE_FULL_DEPTH     = 0.6
_EDGE_DWELL          = 0.2
EDGE_SPEED_PPS       = max(1.0, 1200.0 * SPEED)
_MAX_FRAME_DT        = 0.1
DRAG_ZOOM_ANIM       = _env('DRAG_ZOOM_ANIM', 0.35, lo=0.01, hi=2.0)
_DRAG_START_PX       = 8
_DRAG_PROBE_INTERVAL = 0.05   # не чаще 20 опросов `clients` в секунду в фазе ожидания drag
ABS_COUNTS_PER_MM    = 8.0
_INERTIA_DECAY       = _env('INERTIA_DECAY', 0.88, lo=0.5, hi=0.99)
_INERTIA_THRESHOLD   = 0.5
_VIEW_MARGIN_PX      = 64
_EVDEV_COALESCE_S    = 0.008
_NAV_FOLLOW_S        = 0.5
_HOTPLUG_SETTLE_S    = 0.15
_RECONNECT_MIN_S     = 1.0
_RECONNECT_MAX_S     = 30.0

# ── state ────────────────────────────────────────────────────────────────────
STATE_DIR = os.path.expanduser("~/.cache/vidgex-shell/vidgex_session")
os.makedirs(STATE_DIR, exist_ok=True)

# Порядок захвата: mode_lock -> lock. _devices_lock — листовой, вложенных захватов нет.
lock = threading.Lock()
mode_lock = threading.RLock()
_devices_lock = threading.Lock()

st = {
    'holders': {'sup': set(), 'alt': set(), 'btn': set()},
    'acc': [0.0, 0.0],
    'epoch': 0,
    'last_nav_time': float('-inf'),
}
_on_mode_change_callbacks: list = []
_wake_event = threading.Event()
_motion_active = False
_motion_seq = 0
_ignore_hold = False

_ws_mode_cache: dict[int, tuple[int, dict, float]] = {}  # ws_id -> (mode, data, mtime)
_current_ws_id: int | None = None


def _invalidate_ws_cache(ws_id=None):
    with mode_lock:
        if ws_id is None:
            _ws_mode_cache.clear()
        else:
            _ws_mode_cache.pop(ws_id, None)


def register_mode_change_callback(fn):
    _on_mode_change_callbacks.append(fn)


def _bump_epoch():
    global _ignore_hold
    with lock:
        st['epoch'] += 1
        _ignore_hold = False
    _wake_event.set()


def _fire_mode_change(ws_id, is_canvas):
    _bump_epoch()
    for fn in tuple(_on_mode_change_callbacks):
        fn(ws_id, is_canvas)


def _held():
    """Жест активен (Super + кнопка). Вызывать под lock."""
    h = st['holders']
    return bool(h['sup'] and h['btn'])


# ── Hyprland IPC ─────────────────────────────────────────────────────────────

@functools.cache
def _hypr_socket_path(name):
    return (
        f"{os.environ['XDG_RUNTIME_DIR']}/hypr/"
        f"{os.environ['HYPRLAND_INSTANCE_SIGNATURE']}/{name}"
    )


def _hypr_socket_request(payload: bytes, timeout=2.0) -> bytes:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(_hypr_socket_path('.socket.sock'))
        s.sendall(payload)
        s.shutdown(socket.SHUT_WR)
        return b"".join(iter(lambda: s.recv(65536), b""))


def hc(cmd, timeout=2.0):
    raw = _hypr_socket_request(("j/" + " ".join(cmd)).encode('utf-8'), timeout)
    return json.loads(raw.decode('utf-8', errors='replace'))


# Публичные имена сохранены, реализация одна
hc_list = hc
hc_dict = hc


def get_cursor_pos():
    x, y = _hypr_socket_request(b"cursorpos", 1.0).decode().split(',')
    return int(x), int(y)


# Пакетная отправка: один воркер (создаётся лениво) и один `eval` на пачку.
# Сдвиги окон копятся словарём addr -> (x, y): новый кадр затирает не успевший уйти.
# Внутри Hyprland все сдвиги пачки исполняет один Lua-цикл, Python шлёт только данные.
# Порядок в пачке: сначала сырые команды (float/resize/center), затем цикл сдвигов —
# так resize всегда предшествует move того же окна.
_batch_cond = threading.Condition()
_raw_cmds: list[str] = []
_pending_moves: dict[str, tuple[int, int]] = {}
_worker_busy = False
_worker_started = False

# pcall: окно могло закрыться между расчётом кадра и исполнением; остальные окна кадра
# не должны потеряться из-за одной ошибки dispatch
_MOVES_LUA = (
    'local d,m,t=hl.dispatch,hl.dsp.window.move,{%s} '
    'local function f(c) d(m({window="address:"..c[1],x=c[2],y=c[3],relative=false})) end '
    'for i=1,#t do pcall(f,t[i]) end'
)


def _batch_worker():
    global _worker_busy
    while True:
        with _batch_cond:
            while not (_raw_cmds or _pending_moves):
                _worker_busy = False
                _batch_cond.notify_all()
                _batch_cond.wait()
            lines = _raw_cmds[:]
            _raw_cmds.clear()
            if _pending_moves:
                lines.append(_MOVES_LUA % ",".join(
                    f'{{"{a}",{x},{y}}}' for a, (x, y) in _pending_moves.items()
                ))
                _pending_moves.clear()
            _worker_busy = True
        try:
            _hypr_socket_request(("eval " + "\n".join(lines)).encode('utf-8'))
        except OSError:
            # Сокет Hyprland может быть временно недоступен при перезапуске композитора
            pass


def _submit(cmds=(), moves=()):
    """cmds — Lua-команды строками; moves — список (addr, x, y) абсолютных позиций."""
    global _worker_started
    if not (cmds or moves):
        return
    with _batch_cond:
        if not _worker_started:
            _worker_started = True
            threading.Thread(target=_batch_worker, daemon=True).start()
        _raw_cmds.extend(cmds)
        for addr, x, y in moves:
            _pending_moves[addr] = (int(x), int(y))
        _batch_cond.notify()


def hc_batch(cmds):
    _submit(cmds)


def _flush_batch():
    with _batch_cond:
        while _raw_cmds or _pending_moves or _worker_busy:
            _batch_cond.wait()


def _resize_cmd(addr, w, h):
    return (
        f'hl.dispatch(hl.dsp.window.resize({{ window = "address:{addr}", '
        f'x = {int(w)}, y = {int(h)}, relative = false }}))'
    )


def _float_toggle_cmd(addr):
    return (
        f'hl.dispatch(hl.dsp.window.float({{ window = "address:{addr}", '
        f'action = "toggle" }}))'
    )


def _ws_clients(ws_id):
    return [w for w in hc(['clients']) if w['workspace']['id'] == ws_id]


# Мониторы везде представлены логическими прямоугольниками (x, y, w, h)
def _rect(m):
    return (m['x'], m['y'], m['width'] / m['scale'], m['height'] / m['scale'])


def _rect_center(r):
    return (r[0] + r[2] / 2, r[1] + r[3] / 2)


def _ws_monitor_rect(ws_id, monitors):
    """Монитор воркспейса (иначе первый); None, если мониторов нет."""
    mon = next(
        (m for m in monitors if m['activeWorkspace']['id'] == ws_id),
        monitors[0] if monitors else None,
    )
    return None if mon is None else _rect(mon)


def _view_box(rects):
    if not rects:
        return (-100000.0, -100000.0, 100000.0, 100000.0)
    return (
        min(r[0] for r in rects) - _VIEW_MARGIN_PX,
        min(r[1] for r in rects) - _VIEW_MARGIN_PX,
        max(r[0] + r[2] for r in rects) + _VIEW_MARGIN_PX,
        max(r[1] + r[3] for r in rects) + _VIEW_MARGIN_PX,
    )


# ── per-workspace mode: ws_{N}_layout.json = "<0|1>\n<json>" ─────────────────

def _read_ws_file(ws_id):
    with mode_lock:
        path = os.path.join(STATE_DIR, f"ws_{ws_id}_layout.json")
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            # Файл раскладки ещё не создан для данного воркспейса
            _ws_mode_cache.pop(ws_id, None)
            return 0, {}
        cached = _ws_mode_cache.get(ws_id)
        if cached is not None and cached[2] == mtime:
            return cached[0], cached[1]
        try:
            with open(path, "r", encoding="utf-8") as f:
                flag, _, body = f.read().partition("\n")
            mode = int(flag)
            data = json.loads(body) if body.strip() else {}
        except (OSError, ValueError):
            # Повреждённый или параллельно удалённый файл состояния сбрасывается к тайлингу
            # (JSONDecodeError — подкласс ValueError)
            return 0, {}
        _ws_mode_cache[ws_id] = (mode, data, mtime)
        return mode, data


def _write_ws_file(ws_id, mode, data):
    with mode_lock:
        path = os.path.join(STATE_DIR, f"ws_{ws_id}_layout.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(f"{1 if mode else 0}\n")
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        # Кэш перечитается по mtime при ближайшем обращении
        _ws_mode_cache.pop(ws_id, None)


# Геометрия холста хранится отдельным файлом: ws_{N}_layout.json перезаписывает
# services/session.py (периодический снимок живых окон), и всё, что toggle_mode
# клал в его ключ `layout`, затиралось текущей тайловой геометрией.
def _canvas_layout_path(ws_id):
    return os.path.join(STATE_DIR, f"ws_{ws_id}_canvas.json")


def _read_canvas_layout(ws_id):
    try:
        with open(_canvas_layout_path(ws_id), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        # Холст ещё не сохранялся или файл повреждён: восстанавливать нечего
        # (JSONDecodeError — подкласс ValueError)
        return {}


def _write_canvas_layout(ws_id, layout):
    path = _canvas_layout_path(ws_id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(layout, f, ensure_ascii=False)
    os.replace(tmp, path)


def _get_active_ws_id() -> int | None:
    global _current_ws_id
    if _current_ws_id is None:
        _current_ws_id = hc(['activeworkspace']).get('id')
    return _current_ws_id


def is_canvas_mode(ws_id=None) -> bool:
    if ws_id is None:
        ws_id = _get_active_ws_id()
    return ws_id is not None and _read_ws_file(ws_id)[0] == 1


def _active_canvas_ws():
    ws_id = _get_active_ws_id()
    return ws_id if is_canvas_mode(ws_id) else None


def toggle_mode(silent: bool = False):
    global _current_ws_id
    with mode_lock:
        _end_zoom()

        ws_id = hc(['activeworkspace']).get('id')
        _current_ws_id = ws_id
        if ws_id is None:
            return

        mode, data = _read_ws_file(ws_id)
        canvas = mode == 1
        # canvas -> tiling: берём плавающие; tiling -> canvas: берём тайловые
        clients = [
            w for w in _ws_clients(ws_id)
            if w['mapped'] and not w['hidden'] and w['floating'] == canvas
        ]
        # tiling -> canvas: возвращаются сохранённые позиции; canvas -> tiling: saved пуст
        saved = {} if canvas else _read_canvas_layout(ws_id)
        cmds, moves = [], []
        for w in clients:
            addr = w['address']
            cmds.append(_float_toggle_cmd(addr))
            geo = saved.get(addr)
            if geo is not None:
                cmds.append(_resize_cmd(addr, *geo['size']))
                moves.append((addr, *geo['at']))
        if canvas:
            _write_canvas_layout(ws_id, {
                w['address']: {'at': list(w['at']), 'size': list(w['size'])}
                for w in clients
            })
        _write_ws_file(ws_id, not canvas, data)
        _submit(cmds, moves)
        if not silent:
            _fire_mode_change(ws_id, not canvas)
        _bump_epoch()


def enable_canvas():
    with mode_lock:
        if not is_canvas_mode():
            toggle_mode()


def _center_camera_on_window(ws_id, win_addr):
    with mode_lock:
        rect = _ws_monitor_rect(ws_id, hc(['monitors']))
        if rect is None:
            return
        clients = [w for w in _ws_clients(ws_id) if w['floating']]
        foc = next((w for w in clients if w['address'] == win_addr), None)
        if foc is None:
            return
        cx, cy = _rect_center(rect)
        dx = round(cx - (foc['at'][0] + foc['size'][0] / 2))
        dy = round(cy - (foc['at'][1] + foc['size'][1] / 2))
        if dx or dy:
            _submit(moves=[
                (w['address'], w['at'][0] + dx, w['at'][1] + dy) for w in clients
            ])


# ── Hyprland events ──────────────────────────────────────────────────────────

def _addr(raw: str) -> str:
    return raw if raw.startswith("0x") else f"0x{raw}"


def _float_opened_window(addr):
    with mode_lock:
        w = next((w for w in hc(['clients']) if w['address'] == addr), None)
        if w is None or w['floating'] or not is_canvas_mode(w['workspace']['id']):
            return
        _submit([
            _float_toggle_cmd(addr),
            _resize_cmd(addr, 800, 600),
            f'hl.dispatch(hl.dsp.window.center({{ window = "address:{addr}" }}))',
        ])


def _switch_workspace(new_ws_id: int):
    global _current_ws_id
    with mode_lock:
        if new_ws_id == _current_ws_id:
            return
        _current_ws_id = new_ws_id
        _fire_mode_change(new_ws_id, is_canvas_mode(new_ws_id))


def _on_active_window(args: str):
    with lock:
        nav_recent = (time.monotonic() - st['last_nav_time']) < _NAV_FOLLOW_S
    if nav_recent and args:
        ws_id = _active_canvas_ws()
        if ws_id is not None:
            _center_camera_on_window(ws_id, _addr(args))


# openwindow>>ADDRESS,WORKSPACENAME,CLASS,TITLE
def _on_open_window(args: str):
    addr, _, rest = args.partition(',')
    ws_name = rest.partition(',')[0]
    # У числовых воркспейсов name == id: режим берётся из кэша без IPC.
    # Именованные/special проверяются по списку clients.
    if ws_name.isdigit() and not is_canvas_mode(int(ws_name)):
        return
    _float_opened_window(_addr(addr))


# Остальные события (movewindow, windowtitle и т.д.) отбрасываются без декодирования.
# workspacev2>>ID,NAME ; focusedmonv2>>MONNAME,ID
_EVENT_HANDLERS = {
    b"activewindowv2": _on_active_window,
    b"workspacev2": lambda a: _switch_workspace(int(a.partition(',')[0])),
    b"focusedmonv2": lambda a: _switch_workspace(int(a.rpartition(',')[2])),
    b"openwindow": _on_open_window,
}


def _stream_hypr_events():
    global _current_ws_id
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(5.0)
        s.connect(_hypr_socket_path('.socket2.sock'))
        # Стриминговый сокет событий должен блокироваться в простое без таймаута
        s.settimeout(None)
        with mode_lock:
            _current_ws_id = hc(['activeworkspace']).get('id')
        with s.makefile('rb') as stream:
            for raw_line in stream:
                name, _, args = raw_line.partition(b">>")
                handler = _EVENT_HANDLERS.get(name)
                if handler is not None:
                    handler(args.decode('utf-8', errors='replace').strip())


def hyprland_ipc_listener():
    delay = _RECONNECT_MIN_S
    while True:
        try:
            _stream_hypr_events()
            delay = _RECONNECT_MIN_S
        except (OSError, ValueError):
            # Разрыв соединения, перезапуск композитора или обрезанный ответ IPC
            # (JSONDecodeError — подкласс ValueError)
            pass
        time.sleep(delay)
        delay = min(delay * 2.0, _RECONNECT_MAX_S)


# ── Input: один поток на evdev-устройства + inotify (poll) ───────────────────

_KEY_HOLDERS = {
    ecodes.KEY_LEFTMETA: 'sup', ecodes.KEY_RIGHTMETA: 'sup',
    ecodes.KEY_LEFTALT: 'alt',  ecodes.KEY_RIGHTALT: 'alt',
    ecodes.BTN_LEFT: 'btn', ecodes.BTN_TOUCH: 'btn', ecodes.BTN_TOOL_FINGER: 'btn',
}
_TOUCH_CODES = (ecodes.BTN_TOUCH, ecodes.BTN_TOOL_FINGER)
_NAV_KEYS = (ecodes.KEY_UP, ecodes.KEY_DOWN, ecodes.KEY_LEFT, ecodes.KEY_RIGHT)
_WATCHED_KEYS = frozenset(_KEY_HOLDERS) | frozenset(_NAV_KEYS)
_EV_KEY, _EV_REL, _EV_ABS = ecodes.EV_KEY, ecodes.EV_REL, ecodes.EV_ABS
_ABS_XY = (ecodes.ABS_X, ecodes.ABS_Y)
_BENIGN_OPEN_ERRNO = (errno.ENODEV, errno.ENOENT, errno.EACCES, errno.EPERM)
_GONE_ERRNO = (errno.ENODEV, errno.ENOENT)
_POLL_DEAD = select.POLLHUP | select.POLLERR | select.POLLNVAL

_libc = ctypes.CDLL("libc.so.6", use_errno=True)
_IN_ATTRIB = 0x00000004
_IN_MOVED_TO = 0x00000080
_IN_CREATE = 0x00000100
_IN_DELETE = 0x00000200


class _InputMask(ctypes.Structure):
    __slots__ = ()
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("codes_size", ctypes.c_uint32),
        ("codes_ptr", ctypes.c_uint64),
    ]


# ioctl _IOW('E', 0x93, struct input_mask) для фильтрации событий evdev на уровне ядра
_EVIOCSMASK = (1 << 30) | (ctypes.sizeof(_InputMask) << 16) | (ord('E') << 8) | 0x93
_BITS_PER_LONG = ctypes.sizeof(ctypes.c_ulong) * 8


def _build_bitmask(codes, max_code):
    arr = (ctypes.c_ulong * ((max_code // _BITS_PER_LONG) + 1))()
    for c in codes:
        if 0 <= c <= max_code:
            arr[c // _BITS_PER_LONG] |= 1 << (c % _BITS_PER_LONG)
    return arr


_MASK_EV_IDLE = _build_bitmask((ecodes.EV_SYN, _EV_KEY), ecodes.EV_MAX)
_MASK_EV_MOTION = _build_bitmask((ecodes.EV_SYN, _EV_KEY, _EV_REL, _EV_ABS), ecodes.EV_MAX)
_MASK_KEYS = _build_bitmask(_WATCHED_KEYS, ecodes.KEY_MAX)
_MASK_RELS = _build_bitmask((ecodes.REL_X, ecodes.REL_Y), ecodes.REL_MAX)
_MASK_ABS = _build_bitmask(_ABS_XY, ecodes.ABS_MAX)


def _apply_evdev_mask(fd: int, ev_type: int, mask_arr):
    req = _InputMask(ev_type, ctypes.sizeof(mask_arr), ctypes.addressof(mask_arr))
    try:
        fcntl.ioctl(fd, _EVIOCSMASK, req)
    except OSError as err:
        # Устройство отключено в момент вызова ioctl; прочие ошибки — это сломанный фильтр
        if err.errno != errno.ENODEV:
            raise


class _Dev:
    __slots__ = ('path', 'dev', 'motion', 'factors', 'last_abs', 'seq')

    def __init__(self, path, dev, motion, factors):
        self.path = path
        self.dev = dev
        self.motion = motion
        self.factors = factors
        self.last_abs = dict.fromkeys(factors)
        self.seq = _motion_seq

    def reset_abs(self):
        self.last_abs = dict.fromkeys(self.factors)


# fd -> устройство. Пишет только поток ввода (под _devices_lock), читают и другие потоки.
_devs: dict[int, _Dev] = {}


def _configure_masks(d: _Dev):
    """Первичные маски устройства. Вызывать под _devices_lock."""
    fd = d.dev.fd
    _apply_evdev_mask(fd, _EV_KEY, _MASK_KEYS)
    if d.motion:
        _apply_evdev_mask(fd, _EV_REL, _MASK_RELS)
        _apply_evdev_mask(fd, _EV_ABS, _MASK_ABS)
    _apply_evdev_mask(
        fd,
        ecodes.EV_SYN,
        _MASK_EV_MOTION if d.motion and _motion_active else _MASK_EV_IDLE,
    )


def _sync_motion_mask():
    # Маска строится из ТЕКУЩЕГО _motion_active, а не из аргумента: при гонке двух
    # переключений последний вызов всегда применяет актуальное состояние.
    # ioctl под _devices_lock: устройство закрывается только после pop под этим же
    # локом, поэтому номер fd не переиспользуется за время вызова.
    with _devices_lock:
        mask = _MASK_EV_MOTION if _motion_active else _MASK_EV_IDLE
        for d in _devs.values():
            if d.motion:
                _apply_evdev_mask(d.dev.fd, ecodes.EV_SYN, mask)


def _ignore_active_hold(epoch: int):
    global _ignore_hold, _motion_active
    with lock:
        if st['epoch'] != epoch or not _held():
            return
        _ignore_hold = True
        _motion_active = False
        st['acc'][0] = st['acc'][1] = 0.0
    _sync_motion_mask()


def _set_holder(name, dev_path, pressed):
    global _motion_active, _motion_seq, _ignore_hold
    with lock:
        was_active = _held()
        holders = st['holders'][name]
        if pressed:
            holders.add(dev_path)
        else:
            holders.discard(dev_path)
        now_active = _held()
        if not now_active:
            st['acc'][0] = st['acc'][1] = 0.0
            _ignore_hold = False
        want_motion = now_active and not _ignore_hold
        motion_changed = _motion_active != want_motion
        if motion_changed:
            _motion_active = want_motion
            if want_motion:
                _motion_seq += 1
    if motion_changed:
        _sync_motion_mask()
    if motion_changed or was_active != now_active:
        _wake_event.set()


def _add_motion_batch(dx: float, dy: float):
    if not _motion_active or _ignore_hold:
        return
    with lock:
        if not _motion_active or _ignore_hold:
            return
        was_empty = not (st['acc'][0] or st['acc'][1])
        st['acc'][0] += dx
        st['acc'][1] += dy
    if was_empty:
        _wake_event.set()


def _probe(device):
    """(нужны_клавиши, есть_движение, нормирующие_факторы_ABS) за один capabilities()."""
    caps = device.capabilities()
    has_keys = bool(_WATCHED_KEYS.intersection(caps.get(_EV_KEY, ())))
    rels = caps.get(_EV_REL, ())
    abs_caps = {c: i for c, i in caps.get(_EV_ABS, ()) if c in _ABS_XY}
    has_motion = ecodes.REL_X in rels or ecodes.REL_Y in rels or bool(abs_caps)
    factors = {
        c: i.resolution or (max(1, i.max - i.min) / 100.0)
        for c, i in abs_caps.items()
    }
    return has_keys, has_motion, factors


def _open_device(poller, ignored, path):
    device = None
    try:
        device = evdev.InputDevice(path)
        has_keys, has_motion, factors = _probe(device)
    except OSError as err:
        # Устройство исчезло или ещё недоступно по правам: udev выдаёт ACL после создания узла
        if device is not None:
            device.close()
        if err.errno not in _BENIGN_OPEN_ERRNO:
            raise
        return
    if not (has_keys or has_motion):
        device.close()
        ignored.add(path)
        return
    d = _Dev(path, device, has_motion, factors)
    # Регистрация и первичные маски — в одной секции с _sync_motion_mask,
    # иначе смена режима могла бы затереться устаревшей маской
    with _devices_lock:
        _devs[device.fd] = d
        _configure_masks(d)
    poller.register(device.fd, select.POLLIN)


def _drop_device(poller, fd):
    poller.unregister(fd)
    with _devices_lock:
        d = _devs.pop(fd)
    d.dev.close()
    for name in ('sup', 'alt', 'btn'):
        _set_holder(name, d.path, False)


def _rescan(poller, ignored):
    paths = {
        f"/dev/input/{n}" for n in os.listdir('/dev/input') if n.startswith("event")
    }
    ignored &= paths
    known = {d.path for d in _devs.values()}
    for path in paths - known - ignored:
        _open_device(poller, ignored, path)


def _read_device(d: _Dev) -> bool:
    """Разбирает пачку событий устройства. True — в конце пачки осталось движение."""
    dx = dy = 0.0
    last_abs = d.last_abs
    for e in d.dev.read():
        if e.type == _EV_KEY:
            if dx or dy:
                _add_motion_batch(dx, dy)
                dx = dy = 0.0
            holder = _KEY_HOLDERS.get(e.code)
            if holder is not None:
                # Автоповтор (value == 2) удерживаемой клавиши состояния не меняет
                if e.value != 2:
                    _set_holder(holder, d.path, bool(e.value))
                    if e.code in _TOUCH_CODES:
                        d.reset_abs()
                        last_abs = d.last_abs
            elif e.value in (1, 2) and e.code in _NAV_KEYS:
                with lock:
                    if st['holders']['alt']:
                        st['last_nav_time'] = time.monotonic()
        elif e.type == _EV_REL:
            if e.code == ecodes.REL_X:
                dx += e.value
            elif e.code == ecodes.REL_Y:
                dy += e.value
        elif e.type == _EV_ABS and e.code in last_abs:
            if d.seq != _motion_seq:
                d.seq = _motion_seq
                d.reset_abs()
                last_abs = d.last_abs
            prev = last_abs[e.code]
            if prev is not None:
                delta = ((e.value - prev) / d.factors[e.code]) * ABS_COUNTS_PER_MM
                if e.code == ecodes.ABS_X:
                    dx += delta
                else:
                    dy += delta
            last_abs[e.code] = e.value
    if dx or dy:
        _add_motion_batch(dx, dy)
        return True
    return False


def _service_device(poller, fd):
    try:
        if _read_device(_devs[fd]) and _motion_active:
            time.sleep(_EVDEV_COALESCE_S)
    except BlockingIOError:
        # События уже вычитаны или отфильтрованы маской: штатно для неблокирующего fd
        pass
    except OSError as err:
        # Устройство отключено между poll и read
        if err.errno not in _GONE_ERRNO:
            raise
        _drop_device(poller, fd)


def _drain(fd):
    try:
        while os.read(fd, 65536):
            pass
    except BlockingIOError:
        # Очередь неблокирующего inotify-fd пуста — штатный конец чтения
        pass


def _input_loop():
    ino = _libc.inotify_init1(os.O_CLOEXEC | os.O_NONBLOCK)
    if ino < 0:
        raise OSError(ctypes.get_errno(), "inotify_init1 failed")
    try:
        wd = _libc.inotify_add_watch(
            ino, b"/dev/input", _IN_CREATE | _IN_ATTRIB | _IN_MOVED_TO | _IN_DELETE
        )
        if wd < 0:
            raise OSError(ctypes.get_errno(), "inotify_add_watch failed")
        poller = select.poll()
        poller.register(ino, select.POLLIN)
        ignored: set[str] = set()
        # Первый скан — сразу, но уже после установки вотча: устройство,
        # появившееся между ними, не теряется
        rescan_at: float | None = time.monotonic()
        while True:
            # Бесконечное ожидание, пока нет ни событий, ни отложенного скана
            timeout = (
                None if rescan_at is None
                else int(max(0.0, rescan_at - time.monotonic()) * 1000) + 1
            )
            for fd, ev in poller.poll(timeout):
                if fd == ino:
                    _drain(ino)
                    # Один плаг даёт пачку событий (event*, js*, смена ACL от udev):
                    # скан откладывается до тишины и выполняется один раз
                    rescan_at = time.monotonic() + _HOTPLUG_SETTLE_S
                elif ev & _POLL_DEAD:
                    _drop_device(poller, fd)
                else:
                    _service_device(poller, fd)
            if rescan_at is not None and time.monotonic() >= rescan_at:
                rescan_at = None
                _rescan(poller, ignored)
    finally:
        os.close(ino)


# ── Camera / Gesture ─────────────────────────────────────────────────────────
# Камера без зума: экран = мир + off.

def _take_frame_input():
    """Один захват лока на кадр: состояние жеста + накопленные counts (с обнулением)."""
    with lock:
        acc = st['acc']
        counts = (acc[0], acc[1])
        acc[0] = acc[1] = 0.0
        return _held(), st['epoch'], _ignore_hold, counts


def _needs_tick() -> bool:
    with lock:
        active = _held()
        has_counts = bool(st['acc'][0] or st['acc'][1])
        epoch = st['epoch']
        ignored = _ignore_hold

    g = _gesture
    if g is None:
        return active and not ignored
    if g.epoch != epoch or g.kind == 'release' or not active:
        return True
    if g.kind == 'wait':
        return has_counts or g.probe_pending
    if g.kind == 'zoom':
        return g.edge_active or has_counts
    return g.inertia or has_counts


def _hits_window(clients, x, y):
    return any(
        w['mapped'] and not w['hidden']
        and w['at'][0] <= x <= w['at'][0] + w['size'][0]
        and w['at'][1] <= y <= w['at'][1] + w['size'][1]
        for w in clients
    )


def _ramp(rel, size, zone):
    if rel < zone:
        sign, depth = 1.0, (zone - rel) / zone
    elif rel > size - zone:
        sign, depth = -1.0, (rel - (size - zone)) / zone
    else:
        return 0.0
    t = min(1.0, depth / _EDGE_FULL_DEPTH)
    return sign * t * t * (3.0 - 2.0 * t)


def _edge_pan(rects, cx, cy, step):
    for x, y, w, h in rects:
        if x <= cx < x + w and y <= cy < y + h:
            return (
                step * _ramp(cx - x, w, w * EDGE_ZONE_FRAC),
                step * _ramp(cy - y, h, h * EDGE_ZONE_FRAC),
            )
    return 0.0, 0.0


def _in_view(x, y, w, h, vbox):
    return x + w >= vbox[0] and x <= vbox[2] and y + h >= vbox[1] and y <= vbox[3]


class _Gesture:
    __slots__ = (
        'ws_id', 'epoch', 'kind', 'world', 'sizes', 'sent', 'off', 'anim',
        'rects', 'home', 'view', 'cursor', 'edge_time', 'edge_active',
        'dragged', 'dragged_size', 'vel', 'inertia', 'dirty',
        'next_probe', 'probe_pending',
    )

    def __init__(self, ws_id, epoch):
        _flush_batch()
        self.ws_id = ws_id
        self.epoch = epoch
        self.off = [0.0, 0.0]
        self.anim = None            # (t0, off_from, off_to) только в kind == 'release'
        self.edge_time = 0.0
        self.edge_active = False
        self.dragged = None
        self.dragged_size = None
        self.vel = [0.0, 0.0]
        self.inertia = False
        self.dirty = False
        self.next_probe = 0.0
        self.probe_pending = False
        self.cursor = get_cursor_pos()
        clients = _ws_clients(ws_id)
        monitors = hc(['monitors'])
        self.rects = [_rect(m) for m in monitors]
        self.view = _view_box(self.rects)
        home = _ws_monitor_rect(ws_id, monitors)
        self.home = _rect_center(home) if home is not None else (0.0, 0.0)
        self.world = {}
        self.sizes = {}
        self.sent = {}
        for w in clients:
            if w['floating']:
                addr = w['address']
                pos = (int(w['at'][0]), int(w['at'][1]))
                self.world[addr] = pos
                self.sizes[addr] = (int(w['size'][0]), int(w['size'][1]))
                self.sent[addr] = pos
        self.kind = 'wait' if _hits_window(clients, *self.cursor) else 'pan'

    def _send(self, everything=False):
        """Отправляет сдвиги изменившихся окон; без `everything` — только видимых."""
        ox, oy = self.off
        vbox, sent = self.view, self.sent
        moves = []
        for addr, (x, y) in self.world.items():
            nx, ny = int((ox + x + 0.5) // 1), int((oy + y + 0.5) // 1)
            prev = sent.get(addr)
            if prev == (nx, ny):
                continue
            if not everything and prev is not None:
                w, h = self.sizes[addr]
                if not (_in_view(nx, ny, w, h, vbox) or _in_view(prev[0], prev[1], w, h, vbox)):
                    continue
            sent[addr] = (nx, ny)
            moves.append((addr, nx, ny))
        _submit(moves=moves)

    def _flush_all(self):
        self._send(True)
        self.dirty = False

    def _pan(self, dx, dy):
        self.off[0] += dx
        self.off[1] += dy
        self.dirty = True
        self._send()

    def _edge_step(self, dt, moved):
        if moved:
            self.cursor = get_cursor_pos()
        pan_x, pan_y = _edge_pan(self.rects, *self.cursor, EDGE_SPEED_PPS * dt)
        if not (pan_x or pan_y):
            self.edge_time = 0.0
            self.edge_active = False
            return
        self.edge_active = True
        self.edge_time += dt
        dwell = min(1.0, self.edge_time / _EDGE_DWELL)
        self._pan(pan_x * dwell, pan_y * dwell)

    def _advance(self):
        t0, o0, o1 = self.anim
        t = (time.monotonic() - t0) / DRAG_ZOOM_ANIM
        if t >= 1.0:
            self.off[0], self.off[1] = o1
            self.anim = None
        else:
            e = t * t * t * (t * (6.0 * t - 15.0) + 10.0)
            self.off[0] = o0[0] + (o1[0] - o0[0]) * e
            self.off[1] = o0[1] + (o1[1] - o0[1]) * e

    def step(self, counts, dt, active=True):
        moved = bool(counts[0] or counts[1])
        if self.kind == 'pan':
            dx = counts[0] * SPEED
            dy = counts[1] * SPEED
            if dx or dy:
                self.vel[0] = dx
                self.vel[1] = dy
                self.inertia = True
                self._pan(dx, dy)
            elif self.inertia:
                if not active:
                    self._pan(self.vel[0], self.vel[1])
                self.vel[0] *= _INERTIA_DECAY
                self.vel[1] *= _INERTIA_DECAY
                if abs(self.vel[0]) < _INERTIA_THRESHOLD and abs(self.vel[1]) < _INERTIA_THRESHOLD:
                    self.vel[0] = self.vel[1] = 0.0
                    self.inertia = False
                    if self.dirty:
                        self._flush_all()
        elif self.kind == 'wait':
            if moved or self.probe_pending:
                self._probe_dragged()
        elif self.kind == 'zoom':
            self._edge_step(dt, moved)
        elif self.kind == 'release':
            self._advance()
            if self.anim is None:
                self._flush_all()
                self.kind = 'off'
            else:
                self._send()

    def _probe_dragged(self):
        # Полный список clients — самый тяжёлый запрос жеста, поэтому не чаще раза
        # в _DRAG_PROBE_INTERVAL; пропущенный опрос догоняется через probe_pending
        now = time.monotonic()
        if now < self.next_probe:
            self.probe_pending = True
            return
        self.probe_pending = False
        self.next_probe = now + _DRAG_PROBE_INTERVAL
        self._find_dragged()

    def _find_dragged(self):
        for w in hc(['clients']):
            base = self.world.get(w['address'])
            if base is None:
                continue
            if max(abs(w['at'][0] - base[0]), abs(w['at'][1] - base[1])) <= _DRAG_START_PX:
                continue
            self.dragged = w['address']
            self.dragged_size = self.sizes.pop(self.dragged)
            del self.world[self.dragged]
            self.sent.pop(self.dragged, None)
            self.kind = 'zoom'
            self.cursor = get_cursor_pos()
            self.edge_active = any(_edge_pan(self.rects, *self.cursor, 1.0))
            return

    def release(self):
        self.inertia = False
        self.probe_pending = False
        self.vel[0] = self.vel[1] = 0.0
        self.edge_active = False
        if self.kind == 'zoom':
            self._begin_release()
        elif self.kind != 'release':
            if self.dirty:
                self._flush_all()
            self.kind = 'off'

    def _begin_release(self):
        win = next((w for w in hc(['clients']) if w['address'] == self.dragged), None)
        if win is None:
            # Окно закрыто во время перетаскивания: возвращать к центру нечего
            self._flush_all()
            self.kind = 'off'
            return
        at = (int(win['at'][0]), int(win['at'][1]))
        wx, wy = at[0] - self.off[0], at[1] - self.off[1]
        w, h = self.dragged_size
        self.sent[self.dragged] = at
        self.world[self.dragged] = (wx, wy)
        self.sizes[self.dragged] = self.dragged_size
        # Целевое смещение: центр брошенного окна попадает в центр монитора
        target = (int((self.home[0] - w / 2 + 0.5) // 1) - wx, int((self.home[1] - h / 2 + 0.5) // 1) - wy)
        self.anim = (time.monotonic(), tuple(self.off), target)
        self.kind = 'release'

    def settle(self):
        self.release()
        if self.kind == 'release':
            self.off[0], self.off[1] = self.anim[2]
            self.anim = None
            self._flush_all()
            self.kind = 'off'


_gesture: _Gesture | None = None


def _end_zoom():
    global _gesture
    if _gesture is not None:
        _gesture.settle()
        _gesture = None
    _flush_batch()


def _canvas_tick(active, epoch, ignored, counts, dt):
    global _gesture
    g = _gesture
    if g is None:
        if active and not ignored:
            ws_id = _active_canvas_ws()
            if ws_id is None:
                _ignore_active_hold(epoch)
            else:
                _gesture = _Gesture(ws_id, epoch)
        return
    if g.kind == 'release' or (epoch == g.epoch and (active or g.inertia)):
        g.step(counts, dt, active)
    else:
        g.release()
    if g.kind == 'off' or (not active and not g.inertia and g.kind != 'release'):
        _gesture = None


def _main_canvas_loop():
    global _gesture
    last_tick = 0.0

    while True:
        _wake_event.clear()
        if not _needs_tick():
            _wake_event.wait()

        now = time.monotonic()
        rem = FPS - (now - last_tick)
        if rem > 0.0:
            time.sleep(rem)
            now = time.monotonic()

        dt = min(now - last_tick, _MAX_FRAME_DT)
        last_tick = now

        with mode_lock:
            active, epoch, ignored, counts = _take_frame_input()
            try:
                _canvas_tick(active, epoch, ignored, counts, dt)
            except (OSError, ValueError):
                # Сокет Hyprland недоступен или оборвал ответ (перезапуск композитора;
                # JSONDecodeError — подкласс ValueError): жест сбрасывается и игнорируется
                # до отпускания, поток не умирает
                _gesture = None
                _ignore_active_hold(epoch)


_daemon_start_lock = threading.Lock()
_daemon_started = False


def start_canvas_daemon():
    global _daemon_started
    with _daemon_start_lock:
        if _daemon_started:
            return
        _daemon_started = True

    for target in (hyprland_ipc_listener, _input_loop, _main_canvas_loop):
        threading.Thread(target=target, daemon=True).start()