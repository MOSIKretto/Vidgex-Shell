import errno, os, json, threading, time, socket, queue
import evdev
from evdev import ecodes

# ── env ──────────────────────────────────────────────────────────────────────

def _env(name, default, lo=float('-inf'), hi=float('inf')):
    return min(hi, max(lo, type(default)(os.environ.get(name, default))))


SPEED              = _env('SPEED', 1.6, lo=0.05, hi=20.0)
FPS                = 1.0 / _env('FPS_LIMIT', 60, lo=1, hi=240)
IDLE_FPS           = 1.0 / _env('IDLE_FPS_LIMIT', 10, lo=1, hi=30)  # в простое — 10 Гц вместо 60
EDGE_ZONE_FRAC     = _env('EDGE_ZONE_FRAC', 0.3, lo=0.02, hi=0.45)
_EDGE_FULL_DEPTH   = 0.6
_EDGE_DWELL        = 0.2
EDGE_SPEED_PPS     = max(1.0, 1200.0 * SPEED)
_MAX_FRAME_DT      = 0.1
DRAG_ZOOM          = _env('DRAG_ZOOM', 0.75, lo=0.1, hi=1.0)
DRAG_ZOOM_ANIM     = _env('DRAG_ZOOM_ANIM', 0.35, lo=0.01, hi=2.0)
OVERVIEW_RATIO     = _env('OVERVIEW_RATIO', 1.5, lo=1.0, hi=5.0)
OVERVIEW_ZOOM      = DRAG_ZOOM / OVERVIEW_RATIO
_DRAG_START_PX     = 8
ABS_COUNTS_PER_MM  = 8.0
_INERTIA_DECAY     = _env('INERTIA_DECAY', 0.88, lo=0.5, hi=0.99)  # затухание инерции панорамы
_INERTIA_THRESHOLD = 0.5  # ниже — стоп

# ── state dir ────────────────────────────────────────────────────────────────
STATE_DIR = os.path.expanduser("~/.cache/vidgex-shell/vidgex_session")
os.makedirs(STATE_DIR, exist_ok=True)

# Порядок захвата: mode_lock -> lock.
lock = threading.Lock()
mode_lock = threading.RLock()

st = {
    'holders': {'sup': set(), 'alt': set(), 'btn': set()},
    'acc': [0.0, 0.0],
    'epoch': 0,
    'last_nav_time': float('-inf'),
}
active_devices = set()
_on_mode_change_callbacks: list = []
_wake_event = threading.Event()  # будит главный цикл мгновенно при вводе

# ── ws mode cache (избегаем чтения файла каждый кадр) ───────────────────────
_ws_mode_cache: dict[int, tuple[int, dict, float]] = {}  # ws_id -> (mode, data, mtime)

def _invalidate_ws_cache(ws_id=None):
    if ws_id is None:
        _ws_mode_cache.clear()
    else:
        _ws_mode_cache.pop(ws_id, None)


def register_mode_change_callback(fn):
    _on_mode_change_callbacks.append(fn)


def _bump_epoch():
    with lock:
        st['epoch'] += 1
    _wake_event.set()


def _fire_mode_change(ws_id, is_canvas):
    _bump_epoch()
    for fn in _on_mode_change_callbacks:
        try:
            fn(ws_id, is_canvas)
        except Exception:
            pass


# ── Hyprland IPC ─────────────────────────────────────────────────────────────

def _hypr_socket_path(name):
    return (f"{os.environ['XDG_RUNTIME_DIR']}/hypr/"
            f"{os.environ['HYPRLAND_INSTANCE_SIGNATURE']}/{name}")


def _hypr_socket_request(request, timeout=2.0):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(_hypr_socket_path('.socket.sock'))
        s.sendall(request.encode('utf-8'))
        s.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            data = s.recv(65536)
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks).decode('utf-8', errors='replace')


def hc(cmd, timeout=2.0):
    return json.loads(_hypr_socket_request("j/" + " ".join(cmd), timeout=timeout))


def hc_list(cmd, timeout=2.0):
    return hc(cmd, timeout=timeout)


def hc_dict(cmd, timeout=2.0):
    return hc(cmd, timeout=timeout)


# batch — один воркер, последовательная отправка eval-скриптов
_cmd_queue: queue.Queue = queue.Queue(maxsize=2000)
_worker_lock = threading.Lock()
_batch_worker_started = False

# coalesce: склеиваем команды за окно COALESCE, шлём одним eval
_BATCH_COALESCE_MS = 8  # мс — окно для склейки

def _hc_batch_worker():
    while True:
        script = _cmd_queue.get()
        # склеить всё что накопилось за COALESCE окно, чтобы слать одним eval
        try:
            time.sleep(_BATCH_COALESCE_MS / 1000.0)
            extra = []
            while True:
                try:
                    extra.append(_cmd_queue.get_nowait())
                except queue.Empty:
                    break
            if extra:
                script = script + "\n" + "\n".join(extra)
            _hypr_socket_request("eval " + script, timeout=2.0)
        except Exception:
            pass
        finally:
            _cmd_queue.task_done()
            for _ in extra:
                _cmd_queue.task_done()


def _ensure_batch_worker():
    global _batch_worker_started
    with _worker_lock:
        if not _batch_worker_started:
            threading.Thread(target=_hc_batch_worker, daemon=True).start()
            _batch_worker_started = True


def hc_batch(cmds):
    if not cmds:
        return
    _ensure_batch_worker()
    try:
        _cmd_queue.put_nowait("\n".join(cmds))
    except queue.Full:
        # переполнение — дроп самых старых, чтобы не блокировать главный цикл
        try:
            _cmd_queue.get_nowait()
            _cmd_queue.task_done()
        except queue.Empty:
            pass
        try:
            _cmd_queue.put_nowait("\n".join(cmds))
        except queue.Full:
            pass


def get_cursor_pos():
    out = _hypr_socket_request("cursorpos", timeout=1.0)
    x_str, y_str = out.strip().replace(' ', '').split(',')
    return int(x_str), int(y_str)


def _move_cmd(addr, x, y):
    return (
        f'hl.dispatch(hl.dsp.window.move({{ window = "address:{addr}", '
        f'x = {int(x)}, y = {int(y)}, relative = false }}))'
    )


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


def _monitor_for_workspace(ws_id, monitors=None):
    if monitors is None:
        monitors = hc_list(['monitors'])
    return next(
        (m for m in monitors if m.get('activeWorkspace', {}).get('id') == ws_id),
        None,
    )


def _monitor_center(mon):
    return (mon['x'] + mon['width'] / mon['scale'] / 2,
            mon['y'] + mon['height'] / mon['scale'] / 2)


def _ws_clients(ws_id):
    return [w for w in hc_list(['clients']) if w['workspace']['id'] == ws_id]


# ── per-workspace mode: ws_{N}_layout.json = "<0|1>\n<json>" ─────────────────

def _read_ws_file(ws_id):
    # быстрый путь — кэш
    cached = _ws_mode_cache.get(ws_id)
    path = os.path.join(STATE_DIR, f"ws_{ws_id}_layout.json")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        if cached is not None:
            _ws_mode_cache.pop(ws_id, None)
        return 0, {}
    if cached is not None and cached[2] == mtime:
        return cached[0], cached[1]
    try:
        with open(path, "r", encoding="utf-8") as f:
            flag, _, body = f.read().partition("\n")
        mode = int(flag)
        data = json.loads(body) if body.strip() else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return 0, {}
    _ws_mode_cache[ws_id] = (mode, data, mtime)
    return mode, data


def _write_ws_file(ws_id, mode, data):
    path = os.path.join(STATE_DIR, f"ws_{ws_id}_layout.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(f"{1 if mode else 0}\n")
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = time.monotonic()
    _ws_mode_cache[ws_id] = (1 if mode else 0, data, mtime)


def is_canvas_mode(ws_id=None) -> bool:
    if ws_id is None:
        ws_id = hc_dict(['activeworkspace']).get('id')
    if ws_id is None:
        return False
    mode, _ = _read_ws_file(ws_id)
    return mode == 1


def _active_canvas_ws():
    ws_id = hc_dict(['activeworkspace']).get('id')
    return ws_id if ws_id is not None and is_canvas_mode(ws_id) else None


def toggle_mode(silent: bool = False):
    with mode_lock:
        _end_zoom()

        ws_id = hc_dict(['activeworkspace']).get('id')
        if ws_id is None:
            return

        current_mode, data = _read_ws_file(ws_id)

        if current_mode == 1:
            clients = [
                w for w in hc_list(['clients'])
                if w.get('workspace', {}).get('id') == ws_id
                and w.get('mapped')
                and not w.get('hidden')
                and w.get('floating')
            ]
            data['layout'] = {
                w['address']: {'at': list(w['at']), 'size': list(w['size'])}
                for w in clients
            }
            _write_ws_file(ws_id, 0, data)
            if clients:
                hc_batch([_float_toggle_cmd(w['address']) for w in clients])
            if not silent:
                _fire_mode_change(ws_id, False)
        else:
            saved_layout = data.get('layout', {})
            _write_ws_file(ws_id, 1, data)
            clients = [
                w for w in hc_list(['clients'])
                if w.get('workspace', {}).get('id') == ws_id
                and w.get('mapped')
                and not w.get('hidden')
                and not w.get('floating')
            ]
            cmds = []
            for w in clients:
                addr = w['address']
                cmds.append(_float_toggle_cmd(addr))
                if addr in saved_layout:
                    geo = saved_layout[addr]
                    new_x, new_y = geo['at']
                    new_w, new_h = geo['size']
                    cmds.append(_resize_cmd(addr, new_w, new_h))
                    cmds.append(_move_cmd(addr, new_x, new_y))
            hc_batch(cmds)
            if not silent:
                _fire_mode_change(ws_id, True)

        _bump_epoch()


def enable_canvas():
    with mode_lock:
        if not is_canvas_mode():
            toggle_mode()


def _center_camera_on_window(ws_id, win_addr):
    if not win_addr:
        return
    with mode_lock:
        monitors = hc_list(['monitors'])
        mon = _monitor_for_workspace(ws_id, monitors)
        clients = [
            w for w in hc_list(['clients'])
            if w.get('floating') and w.get('workspace', {}).get('id') == ws_id
        ]
        foc = next((w for w in clients if w.get('address') == win_addr), None)
        if not mon or not foc:
            return
        mon_cx, mon_cy = _monitor_center(mon)
        dx = round(mon_cx - (foc['at'][0] + foc['size'][0] / 2))
        dy = round(mon_cy - (foc['at'][1] + foc['size'][1] / 2))
        if dx or dy:
            hc_batch([
                _move_cmd(w['address'], w['at'][0] + dx, w['at'][1] + dy)
                for w in clients
            ])


# ── Hyprland events ──────────────────────────────────────────────────────────

def _float_opened_window(addr):
    with mode_lock:
        w_info = next(
            (w for w in hc_list(['clients']) if w['address'] == addr),
            None,
        )
        if w_info is None:
            return
        ws_id = w_info['workspace']['id']
        if not is_canvas_mode(ws_id):
            return
        ov = _overview
        if ov is not None and ov.kind != 'off' and ov.ws_id == ws_id:
            floating = w_info['floating']
            ov.adopt(addr, w_info['size'] if floating else (800, 600),
                     toggle_float=not floating)
            return
        if w_info['floating']:
            return
        hc_batch([
            _float_toggle_cmd(addr),
            _resize_cmd(addr, 800, 600),
            f'hl.dispatch(hl.dsp.window.center({{ window = "address:{addr}" }}))',
        ])


def hyprland_ipc_listener():
    while True:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(30)
            s.connect(_hypr_socket_path('.socket2.sock'))
            for line in s.makefile(encoding='utf-8', errors='replace'):
                parts = line.strip().split('>>')
                event = parts[0]
                args = parts[1] if len(parts) > 1 else ""
                if event == "activewindowv2" and (
                    time.monotonic() - st['last_nav_time'] < 0.5
                ):
                    ws_id = _active_canvas_ws()
                    if ws_id is None:
                        continue
                    addr = hc_dict(['activewindow']).get('address')
                    _center_camera_on_window(ws_id, addr)
                elif event == "workspace":
                    new_ws_id = hc_dict(['activeworkspace']).get('id')
                    if new_ws_id is not None:
                        _invalidate_ws_cache(new_ws_id)
                        _fire_mode_change(new_ws_id, is_canvas_mode(new_ws_id))
                elif event == "openwindow":
                    _float_opened_window(f"0x{args.split(',')[0]}")
        except (OSError, socket.error):
            time.sleep(1)
        except Exception:
            time.sleep(1)
        finally:
            try:
                s.close()
            except Exception:
                pass


# ── Input (evdev) ────────────────────────────────────────────────────────────

_KEY_HOLDERS = {
    ecodes.KEY_LEFTMETA: 'sup', ecodes.KEY_RIGHTMETA: 'sup',
    ecodes.KEY_LEFTALT: 'alt',  ecodes.KEY_RIGHTALT: 'alt',
    ecodes.BTN_LEFT: 'btn', ecodes.BTN_TOUCH: 'btn', ecodes.BTN_TOOL_FINGER: 'btn',
}
_TOUCH_CODES = (ecodes.BTN_TOUCH, ecodes.BTN_TOOL_FINGER)
_NAV_KEYS = (ecodes.KEY_UP, ecodes.KEY_DOWN, ecodes.KEY_LEFT, ecodes.KEY_RIGHT)


def _set_holder(name, dev_path, pressed):
    with lock:
        holders = st['holders'][name]
        if pressed:
            holders.add(dev_path)
        else:
            holders.discard(dev_path)
    _wake_event.set()


def _add_motion(axis, delta):
    with lock:
        holders = st['holders']
        if holders['sup'] and holders['btn']:
            st['acc'][axis] += delta
            _wake_event.set()


def _get_abs_norm_factors(device):
    caps = dict(device.capabilities().get(ecodes.EV_ABS, []))
    return {
        code: info.resolution or (max(1, info.max - info.min) / 100.0)
        for code, info in caps.items()
        if code in (ecodes.ABS_X, ecodes.ABS_Y)
    }


def listen_input(dev_path):
    active_devices.add(dev_path)
    try:
        device = evdev.InputDevice(dev_path)
        abs_factors = _get_abs_norm_factors(device)
        last_abs = {ecodes.ABS_X: None, ecodes.ABS_Y: None}
        for e in device.read_loop():
            if e.type == ecodes.EV_KEY:
                if e.code in _KEY_HOLDERS:
                    _set_holder(_KEY_HOLDERS[e.code], dev_path, bool(e.value))
                    if e.code in _TOUCH_CODES:
                        last_abs = dict.fromkeys(last_abs)
                elif e.value in (1, 2) and e.code in _NAV_KEYS:
                    with lock:
                        if st['holders']['alt']:
                            st['last_nav_time'] = time.monotonic()
            elif e.type == ecodes.EV_REL and e.code in (ecodes.REL_X, ecodes.REL_Y):
                _add_motion(e.code, e.value)
            elif e.type == ecodes.EV_ABS and e.code in last_abs:
                prev = last_abs[e.code]
                if prev is not None:
                    delta_mm = (e.value - prev) / abs_factors[e.code]
                    _add_motion(e.code, delta_mm * ABS_COUNTS_PER_MM)
                last_abs[e.code] = e.value
    except OSError as err:
        if err.errno not in (errno.ENODEV, errno.ENOENT):
            raise
    finally:
        active_devices.discard(dev_path)
        for name in st['holders']:
            _set_holder(name, dev_path, False)


def hotplug_monitor():
    while True:
        try:
            for path in evdev.list_devices():
                if path not in active_devices:
                    threading.Thread(
                        target=listen_input, args=(path,), daemon=True
                    ).start()
        except Exception:
            pass
        # 5 сек вместо 3 — в 1.6× меньше пробуждений; событие всё равно ловит wake_event
        time.sleep(5)


# ── Camera / Gesture / Overview ──────────────────────────────────────────────
# Камера: экран = origin + world * k, origin = pivot - pivot_world * k.

def _gesture_state():
    with lock:
        holders = st['holders']
        return bool(holders['sup'] and holders['btn']), st['epoch']


def _take_counts():
    with lock:
        acc = st['acc']
        counts = (acc[0], acc[1])
        acc[0] = acc[1] = 0.0
    return counts


def _is_idle():
    """Нет активного ввода и нет анимации — можно спать дольше."""
    with lock:
        holders = st['holders']
        if holders['sup'] or holders['btn']:
            return False
    # проверяем анимации под mode_lock снаружи; здесь быстрая проверка
    if _gesture is not None and _gesture.kind in ('zoom', 'release'):
        return False
    if _overview is not None and _overview.anim is not None:
        return False
    # overview idle тоже idle, но нужно чуть чаще проверять held
    if _overview is not None and _overview.kind == 'idle':
        # если есть нажатие — не idle
        with lock:
            if st['holders']['sup'] and st['holders']['btn']:
                return False
        return True
    return _gesture is None and _overview is None


def _contains(w, x, y):
    return (w['at'][0] <= x <= w['at'][0] + w['size'][0]
            and w['at'][1] <= y <= w['at'][1] + w['size'][1])


def _hits_window(clients, x, y):
    return any(w['mapped'] and not w['hidden'] and _contains(w, x, y) for w in clients)


def _ramp(rel, size, zone):
    if rel < zone:
        sign, depth = 1.0, (zone - rel) / zone
    elif rel > size - zone:
        sign, depth = -1.0, (rel - (size - zone)) / zone
    else:
        return 0.0
    t = min(1.0, depth / _EDGE_FULL_DEPTH)
    return sign * t * t * (3.0 - 2.0 * t)


def _edge_pan(monitors, cx, cy, step):
    mon = next(
        ((m, m['width'] / m['scale'], m['height'] / m['scale']) for m in monitors
         if m['x'] <= cx < m['x'] + m['width'] / m['scale']
         and m['y'] <= cy < m['y'] + m['height'] / m['scale']),
        None,
    )
    if mon is None:
        return 0.0, 0.0
    m, w, h = mon
    return (step * _ramp(cx - m['x'], w, w * EDGE_ZONE_FRAC),
            step * _ramp(cy - m['y'], h, h * EDGE_ZONE_FRAC))


def _scaled_size_cmd(addr, size, k):
    return _resize_cmd(addr, max(1, round(size[0] * k)), max(1, round(size[1] * k)))


class _Camera:
    __slots__ = ('ws_id', 'world', 'sizes', 'pivot', 'pivot_world', 'k', 'anim',
                 'monitors', 'edge_time', 'skip')

    def __init__(self, ws_id):
        self.ws_id = ws_id
        self.world: dict = {}
        self.sizes: dict = {}
        self.pivot = [0.0, 0.0]
        self.pivot_world = (0.0, 0.0)
        self.k = 1.0
        self.anim = None
        self.monitors = None
        self.edge_time = 0.0
        self.skip = None

    def _load_world(self, clients):
        for w in clients:
            if w['floating']:
                self.world[w['address']] = (w['at'][0], w['at'][1])
                self.sizes[w['address']] = tuple(w['size'])

    def _origin(self):
        return (self.pivot[0] - self.pivot_world[0] * self.k,
                self.pivot[1] - self.pivot_world[1] * self.k)

    def _to_world(self, x, y):
        ox, oy = self._origin()
        return ((x - ox) / self.k, (y - oy) / self.k)

    def _anchor(self, point_world):
        ox, oy = self._origin()
        self.pivot_world = point_world
        self.pivot = [ox + point_world[0] * self.k, oy + point_world[1] * self.k]

    def _layout_cmds(self, with_size):
        ox, oy = self._origin()
        k = self.k
        cmds = []
        for addr, (x, y) in self.world.items():
            if addr == self.skip:
                continue
            if with_size:
                cmds.append(_scaled_size_cmd(addr, self.sizes[addr], k))
            cmds.append(_move_cmd(addr, round(ox + x * k), round(oy + y * k)))
        return cmds

    def _pan(self, dx, dy):
        if dx or dy:
            self.pivot[0] += dx
            self.pivot[1] += dy
            hc_batch(self._layout_cmds(with_size=False))

    def _edge_step(self, dt):
        pan_x, pan_y = _edge_pan(self.monitors, *get_cursor_pos(), EDGE_SPEED_PPS * dt)
        if not (pan_x or pan_y):
            self.edge_time = 0.0
            return
        self.edge_time += dt
        dwell = min(1.0, self.edge_time / _EDGE_DWELL)
        self._pan(pan_x * dwell, pan_y * dwell)

    def _start_anim(self, k_to, pivot_to):
        self.anim = (time.monotonic(), self.k, k_to, tuple(self.pivot), tuple(pivot_to))

    def _advance(self):
        t0, k0, k1, p0, p1 = self.anim  # type: ignore
        t = (time.monotonic() - t0) / DRAG_ZOOM_ANIM
        if t >= 1.0:
            self.k = k1
            self.pivot = list(p1)
            self.anim = None
        else:
            e = t * t * t * (t * (6.0 * t - 15.0) + 10.0)
            self.k = k0 + (k1 - k0) * e
            self.pivot = [p0[0] + (p1[0] - p0[0]) * e, p0[1] + (p1[1] - p0[1]) * e]


class _Gesture(_Camera):
    __slots__ = ('epoch', 'kind', 'zoom', 'dragged', 'dragged_size', '_vel', '_has_inertia')

    def __init__(self, epoch):
        _cmd_queue.join()
        super().__init__(_active_canvas_ws())
        self.epoch = epoch
        self.kind = 'off'
        self.zoom = DRAG_ZOOM
        self.dragged = None
        self.dragged_size = None
        self._vel = [0.0, 0.0]       # для инерции панорамы
        self._has_inertia = False
        if self.ws_id is None:
            return
        cx, cy = get_cursor_pos()
        clients = _ws_clients(self.ws_id)
        self._load_world(clients)
        if _hits_window(clients, cx, cy):
            self.kind = 'wait'
            self.monitors = hc_list(['monitors'])
        else:
            self.kind = 'pan'

    def step(self, counts, dt):
        if self.kind == 'pan':
            dx = counts[0] * SPEED
            dy = counts[1] * SPEED
            if dx or dy:
                self._vel = [dx, dy]
                self._has_inertia = True
            self._pan(dx, dy)
            # инерция: если палец отпустили но скорость осталась
            if not dx and not dy and self._has_inertia:
                self._pan(self._vel[0], self._vel[1])
                self._vel[0] *= _INERTIA_DECAY
                self._vel[1] *= _INERTIA_DECAY
                if abs(self._vel[0]) < _INERTIA_THRESHOLD and abs(self._vel[1]) < _INERTIA_THRESHOLD:
                    self._has_inertia = False
        elif self.kind == 'wait':
            self._find_dragged()
        elif self.kind == 'zoom':
            if self.anim is not None:
                self._advance()
                cmds = self._layout_cmds(with_size=True)
                cmds.append(_scaled_size_cmd(self.dragged, self.dragged_size, self.k))
                hc_batch(cmds)
            else:
                self._edge_step(dt)
        elif self.kind == 'release':
            self._advance()
            hc_batch(self._layout_cmds(with_size=True))
            if self.anim is None:
                self.kind = 'off'

    def _find_dragged(self):
        for w in hc_list(['clients']):
            base = self.world.get(w['address'])
            if base is None:
                continue
            if max(abs(w['at'][0] - base[0]), abs(w['at'][1] - base[1])) <= _DRAG_START_PX:
                continue
            self.dragged = w['address']
            self.dragged_size = self.sizes.pop(self.dragged)
            del self.world[self.dragged]
            self.pivot = [float(w['at'][0]), float(w['at'][1])]
            self.pivot_world = (float(w['at'][0]), float(w['at'][1]))
            self.kind = 'zoom'
            self._start_anim(self.zoom, self.pivot)
            return

    def release(self):
        if self.kind == 'zoom':
            self._begin_release()
        elif self.kind != 'release':
            self.kind = 'off'

    def _begin_release(self):
        win = next((w for w in hc_list(['clients']) if w['address'] == self.dragged), None)
        if win is None:
            px, py = get_cursor_pos()
            self._anchor(self._to_world(px, py))
            target = (float(px), float(py))
        else:
            wx, wy = self._to_world(*win['at'])
            self.world[self.dragged] = (wx, wy)
            self.sizes[self.dragged] = self.dragged_size
            self._anchor((wx + self.dragged_size[0] / 2, wy + self.dragged_size[1] / 2))
            target = _monitor_center(_monitor_for_workspace(self.ws_id, self.monitors))
        self.kind = 'release'
        self._start_anim(1.0, target)

    def settle(self):
        self.release()
        if self.kind == 'release':
            self.k = 1.0
            self.pivot = list(self.anim[4])  # type: ignore
            self.anim = None
            hc_batch(self._layout_cmds(with_size=True))
            self.kind = 'off'


class _Overview(_Camera):
    __slots__ = ('epoch', 'kind', 'center', 'shown', 'held', 'dragged')

    def __init__(self, ws_id, epoch):
        super().__init__(ws_id)
        self.epoch = epoch
        self.shown: dict = {}
        self.held = None
        self.dragged = None
        self.center = _monitor_center(_monitor_for_workspace(ws_id))
        self._load_world(_ws_clients(ws_id))
        self.pivot = list(self.center)
        self.pivot_world = self.center
        self._enter()

    def _enter(self):
        self.kind = 'enter'
        self._start_anim(OVERVIEW_ZOOM, self.pivot)

    def toggle(self):
        if self.kind == 'leave':
            self._enter()
        else:
            self._leave()

    def _leave(self):
        self._aim_at_focus()
        self.kind = 'leave'
        self._start_anim(1.0, self.center)

    def settle(self):
        self._aim_at_focus()
        self.k = 1.0
        self.pivot = list(self.center)
        self.anim = None
        hc_batch(self._layout_cmds(with_size=True))
        self.kind = 'off'

    def _aim_at_focus(self):
        if self.kind == 'idle':
            self._absorb()
        addr = hc_dict(['activewindow']).get('address')
        if addr in self.world:
            (x, y), (w, h) = self.world[addr], self.sizes[addr]
            self._anchor((x + w / 2, y + h / 2))
        else:
            self._anchor(self._to_world(*self.center))

    def adopt(self, addr, size, toggle_float):
        if addr in self.world:
            return
        k = self.k
        w, h = max(1, round(size[0] * k)), max(1, round(size[1] * k))
        x = round(self.center[0] - w / 2)
        y = round(self.center[1] - h / 2)
        self.world[addr] = self._to_world(x, y)
        self.sizes[addr] = (float(size[0]), float(size[1]))
        cmds = [_float_toggle_cmd(addr)] if toggle_float else []
        cmds += [_resize_cmd(addr, w, h), _move_cmd(addr, x, y)]
        hc_batch(cmds)
        if self.kind == 'idle':
            _cmd_queue.join()
            win = next((c for c in hc_list(['clients']) if c['address'] == addr), None)
            if win is None:
                del self.world[addr]
                del self.sizes[addr]
            else:
                self.shown[addr] = tuple(win['size'])

    def _sync_present(self):
        present = {w['address']: w for w in hc_list(['clients'])}
        for addr in list(self.world.keys()):
            if addr not in present:
                del self.world[addr]
                del self.sizes[addr]
                self.shown.pop(addr, None)
        return present

    def _absorb(self):
        _cmd_queue.join()
        self.skip = None
        self.dragged = None
        ox, oy = self._origin()
        k = self.k
        present = self._sync_present()
        for addr, (x, y) in self.world.items():
            w = present[addr]
            if tuple(w['at']) != (round(ox + x * k), round(oy + y * k)):
                self.world[addr] = ((w['at'][0] - ox) / k, (w['at'][1] - oy) / k)
            if tuple(w['size']) != self.shown[addr]:
                self.sizes[addr] = (w['size'][0] / k, w['size'][1] / k)
                self.shown[addr] = tuple(w['size'])

    def _arrive(self):
        if self.kind == 'leave':
            self.kind = 'off'
            return
        _cmd_queue.join()
        present = self._sync_present()
        self.shown = {addr: tuple(present[addr]['size']) for addr in self.world}
        self.held = None
        self.dragged = None
        self.skip = None
        self.kind = 'idle'

    def step(self, pressed, counts, dt):
        if self.anim is not None:
            self._advance()
            hc_batch(self._layout_cmds(with_size=True))
            if self.anim is None:
                self._arrive()
        elif self.kind == 'idle':
            self._follow_press(pressed, counts, dt)

    def _find_dragged(self):
        ox, oy = self._origin()
        k = self.k
        best, best_d = None, _DRAG_START_PX
        for w in hc_list(['clients']):
            base = self.world.get(w['address'])
            if base is None:
                continue
            d = max(abs(w['at'][0] - round(ox + base[0] * k)),
                    abs(w['at'][1] - round(oy + base[1] * k)))
            if d > best_d:
                best, best_d = w['address'], d
        if best is not None:
            self.dragged = best
            self.skip = best

    def _follow_press(self, pressed, counts, dt):
        if not pressed:
            if self.held == 'drag':
                self._absorb()
            self.held = None
        elif self.held is None:
            self._absorb()
            on_window = _hits_window(_ws_clients(self.ws_id), *get_cursor_pos())
            self.held = 'drag' if on_window else 'pan'
            self.edge_time = 0.0
            if on_window:
                self.monitors = hc_list(['monitors'])
        elif self.held == 'pan':
            self._pan(counts[0] * SPEED, counts[1] * SPEED)
        elif self.dragged is None:
            self._find_dragged()
        else:
            self._edge_step(dt)


_gesture = None
_overview = None


def _end_zoom():
    global _overview
    if _gesture is not None:
        _gesture.settle()
    if _overview is not None:
        _overview.settle()
        _overview = None
    _cmd_queue.join()


def toggle_overview():
    global _overview
    with mode_lock:
        if _overview is not None:
            _overview.toggle()
            _wake_event.set()
            return
        ws_id = _active_canvas_ws()
        if ws_id is None:
            return
        _end_zoom()
        _, epoch = _gesture_state()
        _overview = _Overview(ws_id, epoch)
        _wake_event.set()


def _main_canvas_loop():
    global _gesture, _overview
    last_tick = time.monotonic()

    while True:
        # ── адаптивное ожидание ──────────────────────────────────────
        # в простое: спим IDLE_FPS и не крутим CPU; при вводе — wake_event будит мгновенно
        is_idle = _is_idle()
        timeout = IDLE_FPS if is_idle else FPS
        # если есть инерция или анимация — не спать долго, иначе ждём события
        if is_idle and _gesture is None and _overview is None:
            # глубокий idle: ждём события до IDLE_FPS, не крутим цикл
            _wake_event.wait(timeout=timeout)
            _wake_event.clear()
        else:
            # активен: короткий сон, но тоже с возможностью раннего пробуждения
            _wake_event.wait(timeout=timeout)
            _wake_event.clear()

        now = time.monotonic()
        dt = min(now - last_tick, _MAX_FRAME_DT)
        last_tick = now

        if not mode_lock.acquire(blocking=False):
            continue
        try:
            active, epoch = _gesture_state()
            counts = _take_counts()

            if _overview is not None:
                if epoch != _overview.epoch:
                    _overview.settle()
                else:
                    _overview.step(active, counts, dt)
                if _overview.kind == 'off':
                    _overview = None
                continue

            g = _gesture

            if g is not None and g.kind == 'release':
                g.step(counts, dt)
                if g.kind == 'off':
                    _gesture = None
                continue

            # инерция панорамы — продолжаем даже без active
            has_inertia = g is not None and g.kind == 'pan' and getattr(g, '_has_inertia', False)
            if not active and not has_inertia:
                if g is not None:
                    g.release()
                    if g.kind != 'release':
                        _gesture = None
                continue

            if g is None:
                if not active:
                    continue
                _gesture = _Gesture(epoch)
                continue

            if epoch != g.epoch:
                g.release()
            # инерция: шаг даже без counts
            if has_inertia or active:
                g.step(counts, dt)
            else:
                g.step(counts, dt)
        finally:
            mode_lock.release()


_daemon_start_lock = threading.Lock()
_daemon_started = False


def start_canvas_daemon():
    global _daemon_started
    with _daemon_start_lock:
        if _daemon_started:
            return
        _daemon_started = True

    _ensure_batch_worker()
    threading.Thread(target=hyprland_ipc_listener, daemon=True).start()
    threading.Thread(target=hotplug_monitor,       daemon=True).start()
    threading.Thread(target=_main_canvas_loop,     daemon=True).start()
