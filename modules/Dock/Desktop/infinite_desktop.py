import errno, os, json, threading, time, socket, queue
import evdev
from evdev import ecodes


def _env(name, default, lo=float('-inf'), hi=float('inf')):
    return min(hi, max(lo, type(default)(os.environ.get(name, default))))


SPEED              = _env('SPEED', 1.6, lo=0.05, hi=20.0)
FPS                = 1.0 / _env('FPS_LIMIT', 60, lo=1, hi=240)
EDGE_ZONE_FRAC     = _env('EDGE_ZONE_FRAC', 0.3, lo=0.02, hi=0.45)  # доля стороны монитора, в которой холст начинает ехать
_EDGE_FULL_DEPTH   = 0.6    # на какой доле глубины зоны достигается полная скорость (не на самом краю)
_EDGE_DWELL        = 0.2    # секунд непрерывного пребывания в зоне до полной скорости
EDGE_SPEED_PPS     = max(1.0, 1200.0 * SPEED)
_MAX_FRAME_DT      = 0.1
DRAG_ZOOM          = _env('DRAG_ZOOM', 0.75, lo=0.1, hi=1.0)       # масштаб всех окон при перетаскивании
DRAG_ZOOM_ANIM     = _env('DRAG_ZOOM_ANIM', 0.35, lo=0.01, hi=2.0)  # секунд на отдаление и на возврат
OVERVIEW_RATIO     = _env('OVERVIEW_RATIO', 1.5, lo=1.0, hi=5.0)    # во сколько раз обзор отдалён сильнее, чем перетаскивание
OVERVIEW_ZOOM      = DRAG_ZOOM / OVERVIEW_RATIO                     # масштаб окон в обзоре
_DRAG_START_PX     = 8      # сдвиг окна, начиная с которого считаем, что его тянут (а не кликнули)
ABS_COUNTS_PER_MM  = 8.0    # мм на тачпаде -> «счётчики мыши»; в пиксели панорамы переводит SPEED

# ЕДИНАЯ ПАПКА СЕССИЙ
STATE_DIR = os.path.expanduser("~/.cache/vidgex-shell/vidgex_session")
os.makedirs(STATE_DIR, exist_ok=True)

# Порядок захвата: mode_lock -> lock.
lock = threading.Lock()
mode_lock = threading.RLock()

st = {
    'holders': {'sup': set(), 'alt': set(), 'btn': set()},
    'acc': [0.0, 0.0],     # накопленное движение указателя в «счётчиках мыши»
    'epoch': 0,            # растёт при смене workspace/режима: идущий жест завершается
    'last_nav_time': float('-inf'),  # time.monotonic() последней стрелки при зажатом Alt
}
active_devices = set()

_on_mode_change_callbacks = []


def register_mode_change_callback(fn):
    _on_mode_change_callbacks.append(fn)


def _bump_epoch():
    with lock:
        st['epoch'] += 1


def _fire_mode_change(ws_id, is_canvas):
    _bump_epoch()
    for fn in _on_mode_change_callbacks:
        fn(ws_id, is_canvas)


# ---------------------------------------------------------------------------
# Hyprland IPC
# ---------------------------------------------------------------------------

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


_cmd_queue = queue.Queue(maxsize=2000)
_worker_lock = threading.Lock()
_batch_worker_started = False


def _hc_batch_worker():
    while True:
        script = _cmd_queue.get()
        try:
            _hypr_socket_request("eval " + script, timeout=2.0)
        finally:
            _cmd_queue.task_done()  # иначе _cmd_queue.join() в _end_zoom зависнет


def _ensure_batch_worker():
    # hc_batch зовут из GTK-потока, IPC-слушателя и цикла кадров: два воркера
    # применяли бы команды раскладки вперемешку
    global _batch_worker_started
    with _worker_lock:
        if not _batch_worker_started:
            threading.Thread(target=_hc_batch_worker, daemon=True).start()
            _batch_worker_started = True


def hc_batch(cmds):
    if not cmds:
        return
    _ensure_batch_worker()
    _cmd_queue.put_nowait("\n".join(cmds))


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
    # width/height в hyprctl физические, а x/y логические
    return (mon['x'] + mon['width'] / mon['scale'] / 2,
            mon['y'] + mon['height'] / mon['scale'] / 2)


def _ws_clients(ws_id):
    return [w for w in hc_list(['clients']) if w['workspace']['id'] == ws_id]


# ---------------------------------------------------------------------------
# Режимы per-workspace: ws_{N}_layout.json = "<0|1>\n<json>"
# ---------------------------------------------------------------------------

def _read_ws_file(ws_id):
    path = os.path.join(STATE_DIR, f"ws_{ws_id}_layout.json")
    if not os.path.exists(path):
        return 0, {}
    with open(path, "r", encoding="utf-8") as f:
        flag, _, body = f.read().partition("\n")
    return int(flag), (json.loads(body) if body.strip() else {})


def _write_ws_file(ws_id, mode, data):
    path = os.path.join(STATE_DIR, f"ws_{ws_id}_layout.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(f"{1 if mode else 0}\n")
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


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
        _end_zoom()  # раскладка сохраняется по текущей геометрии: окна не должны быть уменьшены

        ws_id = hc_dict(['activeworkspace']).get('id')
        if ws_id is None:
            return

        current_mode, data = _read_ws_file(ws_id)

        if current_mode == 1:
            # Выход из режима CANVAS -> HYPRLAND (Тайлинг)
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

            hc_batch([_float_toggle_cmd(w['address']) for w in clients])

            if not silent:
                _fire_mode_change(ws_id, False)

        else:
            # Вход в режим CANVAS (1)
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

        # режим сменился и при silent: идущий жест устарел
        _bump_epoch()


def enable_canvas():
    with mode_lock:  # проверка и переключение атомарны относительно других toggle_mode
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
        # mon бывает None легитимно: между событием и запросом workspace могли переключить,
        # и ws_id уже не активен ни на одном мониторе
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


# ---------------------------------------------------------------------------
# События Hyprland
# ---------------------------------------------------------------------------

def _float_opened_window(addr):
    # Под mode_lock: иначе toggle_mode успевает записать режим и поставить в очередь
    # переключение этого же окна, и мы переключаем его второй раз - окно возвращается в тайлинг.
    with mode_lock:
        w_info = next(
            (w for w in hc_list(['clients']) if w['address'] == addr),
            None,
        )
        if w_info is None:  # окно успело закрыться между событием и запросом
            return
        ws_id = w_info['workspace']['id']
        if not is_canvas_mode(ws_id):
            return

        # Идёт обзор этого workspace: окно сразу входит в мир уменьшенным, а его
        # натуральный размер (800x600 или собственный) вернётся при выходе из обзора.
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
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(_hypr_socket_path('.socket2.sock'))

    # Заголовки окон (особенно XWayland) не обязаны быть валидным UTF-8: строгое
    # декодирование убило бы поток слушателя
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
                _fire_mode_change(new_ws_id, is_canvas_mode(new_ws_id))

        elif event == "openwindow":
            _float_opened_window(f"0x{args.split(',')[0]}")


# ---------------------------------------------------------------------------
# Ввод (evdev): клавиатура, мышь, тачпад
# ---------------------------------------------------------------------------

# 'btn' = ЛКМ мыши (272) либо касание/клик тачпада (330, 325)
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


def _add_motion(axis, delta):
    # axis: 0 = X, 1 = Y (коды REL_X/ABS_X и REL_Y/ABS_Y совпадают)
    with lock:
        holders = st['holders']
        if holders['sup'] and holders['btn']:
            st['acc'][axis] += delta


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
                        # новое касание / смена числа пальцев: абсолютные координаты
                        # прыгают, дельта от старой точки была бы скачком
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
        # Устройство отключили: read_loop падает с ENODEV, а если оно исчезло
        # между list_devices() и open() — с ENOENT. Остальные OSError
        # (например EACCES без группы input) — ошибка конфигурации, пусть падают.
        if err.errno not in (errno.ENODEV, errno.ENOENT):
            raise
    finally:
        active_devices.discard(dev_path)
        for name in st['holders']:
            _set_holder(name, dev_path, False)


def hotplug_monitor():
    while True:
        for path in evdev.list_devices():
            if path not in active_devices:
                threading.Thread(
                    target=listen_input, args=(path,), daemon=True
                ).start()
        time.sleep(3)


# ---------------------------------------------------------------------------
# Жест Super + нажатие:
#   мимо окон — панорама холста;
#   на окне   — окно тянет Hyprland (drag); камера плавно отдаляется: все окна
#               уменьшаются, в приграничной зоне монитора холст едет навстречу
#               (чем глубже в зону, тем быстрее, с плавным разгоном по времени).
#               При отпускании камера так же плавно возвращается к k = 1, а
#               отпущенное окно уезжает в центр монитора вместе со всем холстом
#               (тот же результат, что и фокус стрелками).
#
# Обзор (toggle_overview): та же камера, отдалённая в OVERVIEW_RATIO раз сильнее, чем
#   при перетаскивании, и остающаяся такой, пока обзор не выключат. Окна в нём двигает
#   и растягивает сам Hyprland; мы лишь возвращаем эти правки в мир при выходе.
#   При выходе камера уезжает на окно в фокусе и ставит его в центр монитора.
#   Super + нажатие мимо окон — панорама; на окне — окно тянет Hyprland, а у края
#   монитора холст едет навстречу (как в обычном жесте), так что окно можно унести
#   куда угодно.
#   Окно, открытое во время обзора, сразу входит в мир уменьшенным (натуральный размер
#   800x600 запоминается); если пользователь растянул его вручную, запоминается новый размер.
#
# Камера: экран = origin + world * k, origin = pivot - pivot_world * k.
#   pivot       - экранная точка, которая при зуме остаётся на месте;
#   pivot_world - какая мировая точка в ней лежит.
#   панорама / автопан  -> pivot += сдвиг;
#   отдаление           -> pivot = pivot_world = левый верхний угол тянущегося окна;
#   возврат             -> pivot едет из центра мини-окна в центр монитора,
#                          pivot_world = центр тянувшегося окна в мире;
#   обзор               -> вход: pivot = центр монитора, k = OVERVIEW_ZOOM;
#                          выход: pivot едет из центра окна в фокусе в центр монитора,
#                          pivot_world = центр этого окна в мире;
#   k и pivot анимируются по времени по одной кривой, origin пересчитывается
#   каждый кадр одной формулой.
# ---------------------------------------------------------------------------

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


def _contains(w, x, y):
    return (w['at'][0] <= x <= w['at'][0] + w['size'][0]
            and w['at'][1] <= y <= w['at'][1] + w['size'][1])


def _hits_window(clients, x, y):
    return any(w['mapped'] and not w['hidden'] and _contains(w, x, y) for w in clients)


def _ramp(rel, size, zone):
    # Коэффициент скорости по одной оси: 0 на границе зоны, ±1 при глубине >= _EDGE_FULL_DEPTH.
    # Знак: + у начала оси (холст едет вправо/вниз), - у конца.
    # Smoothstep: плавный вход на границе зоны и плавный выход на полную скорость.
    if rel < zone:
        sign, depth = 1.0, (zone - rel) / zone
    elif rel > size - zone:
        sign, depth = -1.0, (rel - (size - zone)) / zone
    else:
        return 0.0
    t = min(1.0, depth / _EDGE_FULL_DEPTH)
    return sign * t * t * (3.0 - 2.0 * t)


def _edge_pan(monitors, cx, cy, step):
    # width/height в hyprctl физические, а x/y, at и cursorpos — в логических координатах
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
    """Общая часть жеста и обзора: floating-окна в мировых координатах и камера над ними.

    Живёт только под mode_lock.
    """
    __slots__ = ('ws_id', 'world', 'sizes', 'pivot', 'pivot_world', 'k', 'anim',
                 'monitors', 'edge_time', 'skip')

    def __init__(self, ws_id):
        self.ws_id = ws_id
        self.world = {}          # addr -> (x, y): позиции floating-окон в мире
        self.sizes = {}          # addr -> (w, h): размеры при k = 1
        self.pivot = [0.0, 0.0]
        self.pivot_world = (0.0, 0.0)
        self.k = 1.0
        self.anim = None         # (t0, k_from, k_to, pivot_from, pivot_to) или None
        self.monitors = None     # мониторы для автопана у края
        self.edge_time = 0.0     # сколько курсор непрерывно находится в приграничной зоне
        self.skip = None         # addr окна, которое сейчас ведёт Hyprland: раскладка его не трогает

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
        # Сменить опорную мировую точку, не сдвигая окна: origin остаётся прежним.
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
        dwell = min(1.0, self.edge_time / _EDGE_DWELL)  # линейный разгон за _EDGE_DWELL: пролёт через зону не дёргает холст
        self._pan(pan_x * dwell, pan_y * dwell)

    def _start_anim(self, k_to, pivot_to):
        self.anim = (time.monotonic(), self.k, k_to, tuple(self.pivot), tuple(pivot_to))

    def _advance(self):
        t0, k0, k1, p0, p1 = self.anim
        t = (time.monotonic() - t0) / DRAG_ZOOM_ANIM
        if t >= 1.0:
            self.k = k1
            self.pivot = list(p1)
            self.anim = None
        else:
            # smootherstep: нулевые 1-я и 2-я производные на концах; одна кривая и для k, и для pivot
            e = t * t * t * (t * (6.0 * t - 15.0) + 10.0)
            self.k = k0 + (k1 - k0) * e
            self.pivot = [p0[0] + (p1[0] - p0[0]) * e, p0[1] + (p1[1] - p0[1]) * e]


class _Gesture(_Camera):
    """Один жест Super+нажатие, от нажатия до отпускания.

    kind:
      'off'     - ничего не двигаем (не canvas-режим, жест отменён или завершён);
      'pan'     - нажатие мимо окон: панорама холста;
      'wait'    - нажатие на окне, его тянет Hyprland. Ждём, пока какое-то окно реально
                  сдвинется: только так известно, КАКОЕ из перекрывающихся окон тянется;
      'zoom'    - окно тянется: камера отдаляется (anim), затем в приграничной зоне холст едет;
      'release' - кнопку отпустили: камера возвращается к k = 1 и центрирует отпущенное
                  окно на мониторе (anim), ввод игнорируется.

    Тянущееся окно в world не входит, пока его ведёт Hyprland (позиция в начале drag +
    сдвиг курсора): ему мы меняем только размер. При отпускании оно входит в world в той
    точке, где стоит, и возвращается вместе со всеми одной и той же формулой.
    """
    __slots__ = ('epoch', 'kind', 'zoom', 'dragged', 'dragged_size')

    def __init__(self, epoch):
        # снимок геометрии берём только после применения всех команд предыдущего жеста/обзора:
        # иначе почти-натуральные размеры записались бы как натуральные
        _cmd_queue.join()
        super().__init__(_active_canvas_ws())
        self.epoch = epoch
        self.kind = 'off'
        self.zoom = DRAG_ZOOM
        self.dragged = None
        self.dragged_size = None
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
            self._pan(counts[0] * SPEED, counts[1] * SPEED)
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
        # Пока тянущееся окно не определено, мы ничего не двигаем сами, поэтому
        # любое окно, ушедшее от снимка, сдвинул Hyprland: это и есть тянущееся.
        for w in hc_list(['clients']):
            base = self.world.get(w['address'])
            if base is None:
                continue
            if max(abs(w['at'][0] - base[0]), abs(w['at'][1] - base[1])) <= _DRAG_START_PX:
                continue

            self.dragged = w['address']
            self.dragged_size = self.sizes.pop(self.dragged)
            del self.world[self.dragged]
            # Отдаляем вокруг левого верхнего угла тянущегося окна: он неподвижен и для
            # камеры, и для Hyprland, так что окно и соседи уменьшаются как одна сцена.
            self.pivot = [float(w['at'][0]), float(w['at'][1])]
            self.pivot_world = (float(w['at'][0]), float(w['at'][1]))
            self.kind = 'zoom'
            self._start_anim(self.zoom, self.pivot)  # pivot неподвижен: меняется только k
            return

    def release(self):
        """Кнопку отпустили или жест отменён: запустить возврат камеры, если она отдалена."""
        if self.kind == 'zoom':
            self._begin_release()
        elif self.kind != 'release':
            self.kind = 'off'

    def _begin_release(self):
        win = next((w for w in hc_list(['clients']) if w['address'] == self.dragged), None)
        # окно может закрыться прямо во время перетаскивания (приложение завершилось):
        # центрировать нечего, камера возвращается на месте
        if win is None:
            px, py = get_cursor_pos()
            self._anchor(self._to_world(px, py))
            target = (float(px), float(py))
        else:
            # тянущееся окно входит в мир там, где стоит сейчас
            wx, wy = self._to_world(*win['at'])
            self.world[self.dragged] = (wx, wy)
            self.sizes[self.dragged] = self.dragged_size
            # Центр берём по натуральному размеру, а не по фактическому: Hyprland зажимает
            # окно по min_size клиента, и центр мини-окна не совпал бы с центром натурального
            self._anchor((wx + self.dragged_size[0] / 2, wy + self.dragged_size[1] / 2))
            target = _monitor_center(_monitor_for_workspace(self.ws_id, self.monitors))

        # Возврат вокруг центра окна, а сам центр уезжает в центр монитора:
        # при k = 1 точка мира под pivot (центр окна) окажется в target.
        self.kind = 'release'
        self._start_anim(1.0, target)

    def settle(self):
        """Мгновенно довести камеру до k = 1 и до конечной позиции: для операций, читающих геометрию окон."""
        self.release()
        if self.kind == 'release':
            self.k = 1.0
            self.pivot = list(self.anim[4])
            self.anim = None
            hc_batch(self._layout_cmds(with_size=True))
            self.kind = 'off'


class _Overview(_Camera):
    """Обзор workspace: камера отдалена до OVERVIEW_ZOOM вокруг центра монитора.

    kind:
      'enter' - камера отдаляется (anim);
      'idle'  - обзор стоит: окна двигает и растягивает Hyprland, мы ждём нажатия;
      'leave' - камера возвращается к k = 1 и ставит окно в фокусе в центр монитора (anim);
                после анимации kind = 'off' и главный цикл отбрасывает объект.

    held: 'pan' | 'drag' | None - что делает текущее нажатие (решается в момент нажатия).
    dragged: окно, которое тянет Hyprland (известно после первого заметного сдвига);
             пока оно тянется, раскладка его не трогает (skip), а у края монитора
             холст едет навстречу. При отпускании _absorb возвращает его в мир.

    shown: addr -> (w, h): реальные размеры окон после отдаления. Hyprland зажимает окна
    по min_size клиента, поэтому реальный размер может отличаться от size * k; ручной
    ресайз определяется только как отличие от этого снимка.

    Окна, открытые во время обзора, входят в мир через adopt: сразу уменьшенными, с
    натуральным размером в sizes. Без ручного ресайза при выходе они получат его.
    """
    __slots__ = ('epoch', 'kind', 'center', 'shown', 'held', 'dragged')

    def __init__(self, ws_id, epoch):
        super().__init__(ws_id)
        self.epoch = epoch
        self.shown = {}
        self.held = None
        self.dragged = None
        self.center = _monitor_center(_monitor_for_workspace(ws_id))
        self._load_world(_ws_clients(ws_id))
        # при k = 1 мир совпадает с экраном (origin = 0): отдаляемся вокруг центра монитора
        self.pivot = list(self.center)
        self.pivot_world = self.center
        self._enter()

    def _enter(self):
        self.kind = 'enter'
        self._start_anim(OVERVIEW_ZOOM, self.pivot)  # pivot неподвижен: меняется только k

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
        """Мгновенно довести камеру до k = 1 и до конечной позиции: для смены
        workspace/режима и операций, читающих геометрию."""
        self._aim_at_focus()
        self.k = 1.0
        self.pivot = list(self.center)
        self.anim = None
        hc_batch(self._layout_cmds(with_size=True))
        self.kind = 'off'

    def _aim_at_focus(self):
        # Возврат к k = 1 приближает опорную мировую точку: это центр окна в фокусе.
        # Если фокус не на окне мира (окна нет, оно закрылось, workspace сменился) -
        # точка под центром монитора.
        if self.kind == 'idle':
            self._absorb()  # центр берётся по актуальной геометрии, после ручных правок
        addr = hc_dict(['activewindow']).get('address')
        if addr in self.world:
            (x, y), (w, h) = self.world[addr], self.sizes[addr]
            self._anchor((x + w / 2, y + h / 2))
        else:
            self._anchor(self._to_world(*self.center))

    def adopt(self, addr, size, toggle_float):
        """Окно открылось во время обзора: вводим его в мир сразу уменьшенным, в центре монитора.

        size - натуральный размер (при k = 1). При выходе окно получит его, если
        пользователь не растянет окно вручную.
        """
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
            # shown должен быть реальным размером (Hyprland зажимает по min_size клиента),
            # иначе _absorb примет зажатие за ручной ресайз.
            # Во время анимации shown заполнит _arrive.
            _cmd_queue.join()
            win = next((c for c in hc_list(['clients']) if c['address'] == addr), None)
            if win is None:
                del self.world[addr]
                del self.sizes[addr]
            else:
                self.shown[addr] = tuple(win['size'])

    def _sync_present(self):
        # окно могло закрыться во время обзора: команды на несуществующий адрес не шлём
        present = {w['address']: w for w in hc_list(['clients'])}
        for addr in self.world.keys() - present.keys():
            del self.world[addr]
            del self.sizes[addr]
            self.shown.pop(addr, None)
        return present

    def _absorb(self):
        """Только в 'idle' (k постоянен, shown актуален): вернуть ручные правки в мир.

        Тронутые окна (сдвинутые, растянутые, перетащенные) входят в мир по фактической
        геометрии, остальные остаются как есть: пересчёт всех окон при k < 1 съедал бы
        пиксели округлением при каждом обзоре.
        """
        _cmd_queue.join()  # недоехавшая раскладка выглядела бы как ручная правка
        self.skip = None   # перетаскивание закончено: окно снова под раскладкой
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
        _cmd_queue.join()  # снимок размеров только после применения всех команд анимации
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
        # После _absorb на нажатии все окна стоят ровно там, где их оставила раскладка,
        # а сами мы ничего не двигаем, пока тянущееся окно не найдено: значит, любое
        # сдвинувшееся окно тянет Hyprland. Если сдвинулись несколько - берём то, что ушло дальше.
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
            self.skip = best  # дальше панорама двигает всех, кроме него

    def _follow_press(self, pressed, counts, dt):
        if not pressed:
            if self.held == 'drag':
                self._absorb()  # перетащенное окно входит в мир там, где его бросили
            self.held = None
        elif self.held is None:
            # Решаем на нажатии. _absorb нужен в обоих случаях: и панорама, и поиск
            # перетаскиваемого окна опираются на актуальный снимок раскладки.
            # Движение этого тика не учитываем, как в обычном жесте.
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
            self._edge_step(dt)  # у края монитора холст едет навстречу окну


_gesture = None   # текущий _Gesture; читается и пишется только под mode_lock
_overview = None  # текущий _Overview или None; читается и пишется только под mode_lock


def _end_zoom():
    """Под mode_lock: вернуть камеру и дождаться применения команд до операции,
    которая читает геометрию окон (иначе она увидит уменьшенные окна)."""
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
            return
        ws_id = _active_canvas_ws()
        if ws_id is None:
            return  # обзор существует только в canvas-режиме; нажатие в тайлинге - не ошибка
        _end_zoom()
        _, epoch = _gesture_state()
        _overview = _Overview(ws_id, epoch)


def _main_canvas_loop():
    global _gesture, _overview
    last_tick = time.monotonic()

    while True:
        time.sleep(FPS)
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
                    _overview.settle()  # workspace/режим сменились: окна нельзя оставлять уменьшенными
                else:
                    _overview.step(active, counts, dt)
                if _overview.kind == 'off':
                    _overview = None
                continue

            g = _gesture

            if g is not None and g.kind == 'release':
                g.step(counts, dt)  # доигрываем возврат камеры, ввод игнорируем
                if g.kind == 'off':
                    _gesture = None  # нажатие, пришедшее во время возврата, начнёт новый жест
                continue

            if not active:
                if g is not None:
                    g.release()
                    if g.kind != 'release':
                        _gesture = None
                continue

            if g is None:
                _gesture = _Gesture(epoch)  # движение до решения о жесте не учитываем
                continue

            if epoch != g.epoch:
                g.release()  # workspace/режим сменились посреди нажатия: до отпускания не двигаем
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