import os, re, json, threading, time, socket, queue
import evdev


def _env_float(name, default, lo=None, hi=None):
    v = float(os.environ.get(name, default))
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def _env_int(name, default, lo=None, hi=None):
    v = int(os.environ.get(name, default))
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


SPEED       = _env_float('SPEED', 1.6, lo=0.05, hi=20.0)
FPS_LIMIT   = _env_int('FPS_LIMIT', 60, lo=1, hi=240)
FPS         = 1.0 / FPS_LIMIT
EDGE_ZONE   = _env_int('EDGE_ZONE', 40, lo=0)
EDGE_SPEED_PPS = max(1.0, 1200.0 * SPEED)
_MAX_FRAME_DT = 0.1
OVERVIEW_PADDING   = _env_int('OVERVIEW_PADDING', 80, lo=0)
OVERVIEW_MIN_SCALE = _env_float('OVERVIEW_MIN_SCALE', 0.15, lo=0.01, hi=1.0)
ABS_SENSITIVITY = 8.0 * SPEED
STATE_DIR = os.environ.get(
    'STATE_DIR',
    os.path.expanduser("~/.cache/vidgex-shell/vidgex_canvas"),
)
os.makedirs(STATE_DIR, exist_ok=True)


lock = threading.Lock()
mode_lock = threading.RLock()

st = {
    'sup_holders': set(),
    'alt_holders': set(),
    'btn_holders': set(),
    'sup': False, 'alt': False, 'btn': False,

    'ax': 0.0, 'ay': 0.0,
    'last_nav_time': 0.0,

    'mode': None,             # 'CANVAS' или 'WINDOW'
    'active_win_addr': None,
    'mon': {'x': 0, 'y': 0, 'w': 0, 'h': 0},

    'drag_cache': {},          # addr -> {'at':[x,y], 'size':[w,h]}
    'drag_monitors': [],
    'drag_ws': None,

    'overview': False,
    'overview_data': {},
}
active_devices = set()

_on_mode_change_callbacks = []
_on_overview_callbacks = []


def register_mode_change_callback(fn):
    _on_mode_change_callbacks.append(fn)


def register_overview_callback(fn):
    _on_overview_callbacks.append(fn)


def _fire_mode_change(ws_id, is_canvas):
    for fn in _on_mode_change_callbacks:
        fn(ws_id, is_canvas)


def _fire_overview(is_overview):
    for fn in _on_overview_callbacks:
        fn(is_overview)

_CMD_SOCK_PATH = None
_EVT_SOCK_PATH = None


def _get_command_socket_path():
    global _CMD_SOCK_PATH
    if _CMD_SOCK_PATH:
        return _CMD_SOCK_PATH
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    base = os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
    path = f"{base}/hypr/{sig}/.socket.sock"
    _CMD_SOCK_PATH = path
    return path


def _get_event_socket_path():
    global _EVT_SOCK_PATH
    if _EVT_SOCK_PATH:
        return _EVT_SOCK_PATH
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    base = os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
    path = f"{base}/hypr/{sig}/.socket2.sock"
    _EVT_SOCK_PATH = path
    return path


def _hypr_socket_request(request, timeout=2.0):
    path = _get_command_socket_path()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(path)
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
    request = "j/" + " ".join(cmd)
    out = _hypr_socket_request(request, timeout=timeout)
    out = out.strip()
    return json.loads(out)


def hc_list(cmd, timeout=2.0):
    return hc(cmd, timeout=timeout)


def hc_dict(cmd, timeout=2.0):
    return hc(cmd, timeout=timeout)


_cmd_queue = queue.Queue(maxsize=2000)
_batch_worker_started = False


def _hc_batch_worker():
    while True:
        script = _cmd_queue.get()
        _hypr_socket_request("eval " + script, timeout=2.0)


def _ensure_batch_worker():
    global _batch_worker_started
    if not _batch_worker_started:
        threading.Thread(target=_hc_batch_worker, daemon=True).start()
        _batch_worker_started = True


def hc_batch(cmds):
    if not cmds:
        return
    _ensure_batch_worker()
    script = "\n".join(cmds)
    _cmd_queue.put_nowait(script)


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


def _monitor_for_point(monitors, x, y):
    for m in monitors:
        if m['x'] <= x < m['x'] + m['w'] and m['y'] <= y < m['y'] + m['h']:
            return m


def _monitor_for_workspace(ws_id, monitors=None):
    if monitors is None:
        monitors = hc_list(['monitors'])
    mon = next(
        (m for m in monitors if m.get('activeWorkspace', {}).get('id') == ws_id),
        None,
    )
    return mon


def is_canvas_mode(ws_id=None) -> bool:
    if ws_id is None:
        ws_id = hc_dict(['activeworkspace']).get('id')
    if ws_id is None:
        return False
    return os.path.exists(os.path.join(STATE_DIR, f"ws_{ws_id}"))


def _active_canvas_ws():
    ws_id = hc_dict(['activeworkspace']).get('id')
    return ws_id if ws_id is not None and is_canvas_mode(ws_id) else None


def toggle_mode(silent: bool = False):
    with mode_lock:
        ws_id = hc_dict(['activeworkspace']).get('id')
        if ws_id is None:
            return

        lock_file = os.path.join(STATE_DIR, f"ws_{ws_id}")
        layout_file = os.path.join(STATE_DIR, f"ws_{ws_id}_layout.json")

        if is_canvas_mode(ws_id):
            _exit_overview(ws_id)

            clients = [
                w for w in hc_list(['clients'])
                if w.get('workspace', {}).get('id') == ws_id
                and w.get('mapped')
                and not w.get('hidden')
                and w.get('floating')
            ]

            saved_layout = {
                w['address']: {'at': list(w['at']), 'size': list(w['size'])}
                for w in clients
            }

            if saved_layout:
                with open(layout_file, 'w') as f:
                    json.dump(saved_layout, f)

            if os.path.exists(lock_file):
                os.remove(lock_file)

            hc_batch([_float_toggle_cmd(w['address']) for w in clients])

            if not silent:
                _fire_mode_change(ws_id, False)

        else:
            open(lock_file, 'w').close()

            saved_layout = {}
            if os.path.exists(layout_file):
                with open(layout_file, 'r') as f:
                    saved_layout = json.load(f)

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


def enable_canvas():
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

        mon_cx = mon['x'] + mon['width'] // 2
        mon_cy = mon['y'] + mon['height'] // 2
        win_cx = foc['at'][0] + foc['size'][0] // 2
        win_cy = foc['at'][1] + foc['size'][1] // 2

        dx = mon_cx - win_cx
        dy = mon_cy - win_cy

        if dx or dy:
            hc_batch([
                _move_cmd(w['address'], w['at'][0] + dx, w['at'][1] + dy)
                for w in clients
            ])


def toggle_overview():
    ws_id = _active_canvas_ws()
    if not ws_id:
        return

    with mode_lock:
        with lock:
            is_on = ws_id in st['overview_data']
        if is_on:
            _exit_overview(ws_id)
        else:
            _enter_overview(ws_id)


def _enter_overview(ws_id):
    with mode_lock:
        monitors = hc_list(['monitors'])
        mon = _monitor_for_workspace(ws_id, monitors)
        if not mon:
            return

        clients = [
            w for w in hc_list(['clients'])
            if w.get('floating')
            and w.get('workspace', {}).get('id') == ws_id
            and w.get('mapped')
            and not w.get('hidden')
            and w['size'][0] > 0
            and w['size'][1] > 0
        ]
        if not clients:
            return

        min_x = min(w['at'][0] for w in clients)
        min_y = min(w['at'][1] for w in clients)
        max_x = max(w['at'][0] + w['size'][0] for w in clients)
        max_y = max(w['at'][1] + w['size'][1] for w in clients)
        bbox_w = max(max_x - min_x, 1)
        bbox_h = max(max_y - min_y, 1)

        pad_x = max(0, min(OVERVIEW_PADDING, mon['width'] // 2 - 1))
        pad_y = max(0, min(OVERVIEW_PADDING, mon['height'] // 2 - 1))

        avail_w = max(mon['width'] - 2 * pad_x, 1)
        avail_h = max(mon['height'] - 2 * pad_y, 1)

        scale = min(avail_w / bbox_w, avail_h / bbox_h, 1.0)
        scale = max(scale, OVERVIEW_MIN_SCALE)

        new_bbox_w = bbox_w * scale
        new_bbox_h = bbox_h * scale
        off_x = mon['x'] + (mon['width'] - new_bbox_w) / 2.0
        off_y = mon['y'] + (mon['height'] - new_bbox_h) / 2.0

        cmds = []
        saved_geoms = {}
        overview_target_geoms = {}

        for w in clients:
            addr = w['address']
            saved_geoms[addr] = {'at': list(w['at']), 'size': list(w['size'])}

            rel_x = w['at'][0] - min_x
            rel_y = w['at'][1] - min_y

            new_x = int(round(off_x + rel_x * scale))
            new_y = int(round(off_y + rel_y * scale))
            new_w = max(1, int(round(w['size'][0] * scale)))
            new_h = max(1, int(round(w['size'][1] * scale)))

            overview_target_geoms[addr] = {'at': [new_x, new_y], 'size': [new_w, new_h]}

            cmds.append(_resize_cmd(addr, new_w, new_h))
            cmds.append(_move_cmd(addr, new_x, new_y))

        hc_batch(cmds)

        with lock:
            st['overview_data'][ws_id] = {
                'scale': scale,
                'bbox_min': [min_x, min_y],
                'offset': [off_x, off_y],
                'saved_geoms': saved_geoms,
                'overview_target_geoms': overview_target_geoms,
            }
            st['overview'] = bool(st['overview_data'])

    _fire_overview(True)


def _exit_overview(ws_id):
    with mode_lock:
        with lock:
            data = st['overview_data'].pop(ws_id, None)
            was = data is not None
            st['overview'] = bool(st['overview_data'])

        if not was:
            return

        scale = data['scale']
        bbox_min = data['bbox_min']
        off_x, off_y = data['offset']
        saved_geoms = data['saved_geoms']
        overview_target_geoms = data['overview_target_geoms']

        monitors = hc_list(['monitors'])
        mon = _monitor_for_workspace(ws_id, monitors)
        clients = [
            w for w in hc_list(['clients'])
            if w.get('floating')
            and w.get('workspace', {}).get('id') == ws_id
            and w.get('mapped')
            and not w.get('hidden')
        ]

        if not clients or not mon:
            _fire_overview(bool(st['overview']))
            return

        focus_addr = hc_dict(['activewindow']).get('address')

        real_geoms = {}
        for w in clients:
            addr = w['address']
            curr_x, curr_y = w['at']
            curr_w, curr_h = w['size']

            target = overview_target_geoms.get(addr)
            saved = saved_geoms.get(addr)

            if target and saved and (
                abs(curr_x - target['at'][0]) <= 2 and
                abs(curr_y - target['at'][1]) <= 2 and
                abs(curr_w - target['size'][0]) <= 2 and
                abs(curr_h - target['size'][1]) <= 2
            ):
                real_geoms[addr] = {'at': list(saved['at']), 'size': list(saved['size'])}
            else:
                real_w = max(1, int(round(curr_w / scale)))
                real_h = max(1, int(round(curr_h / scale)))

                rel_x = (curr_x - off_x) / scale
                rel_y = (curr_y - off_y) / scale

                real_x = int(round(bbox_min[0] + rel_x))
                real_y = int(round(bbox_min[1] + rel_y))

                real_geoms[addr] = {'at': [real_x, real_y], 'size': [real_w, real_h]}

        dx, dy = 0, 0
        if focus_addr and focus_addr in real_geoms:
            foc_geo = real_geoms[focus_addr]
            mon_cx = mon['x'] + mon['width'] // 2
            mon_cy = mon['y'] + mon['height'] // 2
            win_cx = foc_geo['at'][0] + foc_geo['size'][0] // 2
            win_cy = foc_geo['at'][1] + foc_geo['size'][1] // 2
            dx = mon_cx - win_cx
            dy = mon_cy - win_cy

        cmds = []
        for addr, geo in real_geoms.items():
            final_x = geo['at'][0] + dx
            final_y = geo['at'][1] + dy
            cmds.append(_resize_cmd(addr, geo['size'][0], geo['size'][1]))
            cmds.append(_move_cmd(addr, final_x, final_y))

        hc_batch(cmds)

        if focus_addr:
            _hypr_socket_request(f"dispatch focuswindow address:{focus_addr}", timeout=1.0)

    _fire_overview(False)


def _restore_overview_if_needed():
    monitors = hc_list(['monitors'])
    visible_ws_ids = {
        m.get('activeWorkspace', {}).get('id')
        for m in monitors
        if m.get('activeWorkspace')
    }
    with lock:
        ws_ids = list(st['overview_data'].keys())
    for ws_id in ws_ids:
        if ws_id not in visible_ws_ids:
            _exit_overview(ws_id)


def hyprland_ipc_listener():
    sock_path = _get_event_socket_path()

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(sock_path)

    for line in s.makefile():
        parts = line.strip().split('>>')
        event = parts[0]
        args = parts[1] if len(parts) > 1 else ""

        if event == "activewindowv2" and (
            time.time() - st['last_nav_time'] < 0.5
        ):
            ws_id = _active_canvas_ws()
            if not ws_id:
                continue
            with lock:
                is_ov = ws_id in st['overview_data']
            if not is_ov:
                addr = hc_dict(['activewindow']).get('address')
                _center_camera_on_window(ws_id, addr)

        elif event == "workspace":
            new_ws_id = hc_dict(['activeworkspace']).get('id')

            with lock:
                active_drag_ws = st['drag_ws']
            if active_drag_ws is not None and new_ws_id != active_drag_ws:
                _reset_drag_state()

            _restore_overview_if_needed()
            if new_ws_id is not None:
                _fire_mode_change(new_ws_id, is_canvas_mode(new_ws_id))

        elif event == "openwindow":
            addr = f"0x{args.split(',')[0]}"

            w_info = next(
                (w for w in hc_list(['clients']) if w.get('address') == addr),
                None,
            )
            if not w_info:
                continue

            win_ws_id = w_info.get('workspace', {}).get('id')
            if win_ws_id is None or not is_canvas_mode(win_ws_id):
                continue

            if not w_info.get('floating'):
                hc_batch([
                    _float_toggle_cmd(addr),
                    _resize_cmd(addr, 800, 600),
                    f'hl.dispatch(hl.dsp.window.center({{ window = "address:{addr}" }}))',
                ])


def _update_holder(modifier_key, dev_path, pressed):
    holders_key = f'{modifier_key}_holders'
    with lock:
        holders = st[holders_key]
        was_active = bool(holders)
        if pressed:
            holders.add(dev_path)
        else:
            holders.discard(dev_path)
        is_active = bool(holders)
        st[modifier_key] = is_active
        released = was_active and not is_active
    return released


def _reset_drag_state():
    with lock:
        st['mode'] = None
        st['drag_cache'] = {}
        st['drag_monitors'] = []
        st['drag_ws'] = None


def _get_abs_norm_factors(device):
    factors = {}
    caps = dict(device.capabilities().get(evdev.ecodes.EV_ABS, []))

    for code in (0, 1):
        info = caps.get(code)
        if info is None:
            continue
        res = getattr(info, 'resolution', 0)
        if res > 0:
            factors[code] = float(res)
        else:
            span = max(1, info.max - info.min)
            factors[code] = span / 100.0
    return factors


def listen_input(dev_path):
    active_devices.add(dev_path)
    try:
        device = evdev.InputDevice(dev_path)
        last_abs = {0: None, 1: None}
        abs_factors = _get_abs_norm_factors(device)

        for e in device.read_loop():
            if e.type == 1:
                if e.code in (125, 126):
                    _update_holder('sup', dev_path, bool(e.value))
                elif e.code in (56, 100):
                    _update_holder('alt', dev_path, bool(e.value))
                elif e.code in (272, 330, 325):
                    released = _update_holder('btn', dev_path, bool(e.value))
                    if released:
                        _reset_drag_state()
                        last_abs = {0: None, 1: None}
                elif e.value in (1, 2) and e.code in (103, 108, 105, 106):
                    with lock:
                        if st['alt']:
                            st['last_nav_time'] = time.time()
            else:
                with lock:
                    gate = (st['mode'] == 'CANVAS' and st['sup'] and st['btn'])
                if not gate:
                    continue

                if e.type == 2:
                    with lock:
                        if e.code == 0:
                            st['ax'] += e.value * SPEED
                        elif e.code == 1:
                            st['ay'] += e.value * SPEED
                elif e.type == 3:
                    if e.code in last_abs:
                        if last_abs[e.code] is not None:
                            factor = abs_factors.get(e.code, 1.0) or 1.0
                            raw_diff = e.value - last_abs[e.code]
                            delta_mm = raw_diff / factor
                            delta = delta_mm * ABS_SENSITIVITY
                            with lock:
                                if e.code == 0:
                                    st['ax'] += delta
                                elif e.code == 1:
                                    st['ay'] += delta
                        last_abs[e.code] = e.value
    finally:
        active_devices.discard(dev_path)
        for mod in ('sup', 'alt', 'btn'):
            released = _update_holder(mod, dev_path, False)
            if mod == 'btn' and released:
                _reset_drag_state()


def hotplug_monitor():
    while True:
        devices = evdev.list_devices()
        for path in devices:
            if path not in active_devices:
                threading.Thread(
                    target=listen_input, args=(path,), daemon=True
                ).start()
        time.sleep(3)


def _main_canvas_loop():
    last_tick = time.monotonic()

    while True:
        time.sleep(FPS)

        now = time.monotonic()
        dt = now - last_tick
        last_tick = now
        if dt > _MAX_FRAME_DT:
            dt = _MAX_FRAME_DT

        if not mode_lock.acquire(blocking=False):
            continue
        try:
            with lock:
                btn = st['btn']
                sup = st['sup']
                mode = st['mode']
                dx, dy = int(round(st['ax'])), int(round(st['ay']))
                st['ax'] -= dx
                st['ay'] -= dy

            if not (btn and sup):
                continue

            if mode is None:
                ws_id = _active_canvas_ws()
                if not ws_id:
                    continue

                cx, cy = get_cursor_pos()
                clients = hc_list(['clients'])

                win_under_cursor = next(
                    (
                        w for w in clients
                        if w.get('workspace', {}).get('id') == ws_id
                        and not w.get('hidden')
                        and w.get('mapped')
                        and (w['at'][0] <= cx <= w['at'][0] + w['size'][0])
                        and (w['at'][1] <= cy <= w['at'][1] + w['size'][1])
                    ),
                    None,
                )
                over_win = win_under_cursor is not None

                floating_in_ws = [
                    w for w in clients
                    if w.get('floating') and w.get('workspace', {}).get('id') == ws_id
                ]
                drag_cache = {
                    w['address']: {'at': list(w['at']), 'size': list(w['size'])}
                    for w in floating_in_ws
                }

                monitors = hc_list(['monitors'])

                with lock:
                    st['drag_ws'] = ws_id
                    st['drag_cache'] = drag_cache

                    if over_win:
                        st['mode'] = 'WINDOW'
                        st['active_win_addr'] = win_under_cursor['address']
                        st['drag_monitors'] = [
                            {'x': m['x'], 'y': m['y'], 'w': m['width'], 'h': m['height']}
                            for m in monitors
                        ]
                        mon = _monitor_for_workspace(ws_id, monitors)
                        if mon:
                            st['mon'] = {
                                'x': mon['x'], 'y': mon['y'],
                                'w': mon['width'], 'h': mon['height'],
                            }
                    else:
                        st['mode'] = 'CANVAS'
                        st['ax'] = st['ay'] = 0.0
                continue

            with lock:
                ws_id = st['drag_ws']
            if ws_id is None:
                continue

            if mode == 'CANVAS' and (dx or dy):
                with lock:
                    cache = st['drag_cache']
                    cmds = []
                    for addr, g in cache.items():
                        g['at'][0] += dx
                        g['at'][1] += dy
                        cmds.append(_move_cmd(addr, g['at'][0], g['at'][1]))
                hc_batch(cmds)

            elif mode == 'WINDOW':
                cx, cy = get_cursor_pos()
                with lock:
                    monitors = st['drag_monitors']
                mon = _monitor_for_point(monitors, cx, cy)
                if not mon:
                    continue

                edge_zone = max(0, min(EDGE_ZONE, mon['w'] // 2 - 1, mon['h'] // 2 - 1))

                rel_x, rel_y = cx - mon['x'], cy - mon['y']

                step = EDGE_SPEED_PPS * dt
                pan_x = 0.0
                pan_y = 0.0

                if edge_zone > 0:
                    if rel_x < edge_zone:
                        pan_x = step
                    elif rel_x > mon['w'] - edge_zone:
                        pan_x = -step

                    if rel_y < edge_zone:
                        pan_y = step
                    elif rel_y > mon['h'] - edge_zone:
                        pan_y = -step

                if pan_x or pan_y:
                    with lock:
                        cache = st['drag_cache']
                        active_addr = st['active_win_addr']
                        cmds = []
                        for addr, g in cache.items():
                            if addr == active_addr:
                                continue
                            fx = g.get('_pan_fx', float(g['at'][0])) + pan_x
                            fy = g.get('_pan_fy', float(g['at'][1])) + pan_y
                            g['_pan_fx'] = fx
                            g['_pan_fy'] = fy
                            g['at'][0] = int(round(fx))
                            g['at'][1] = int(round(fy))
                            cmds.append(_move_cmd(addr, g['at'][0], g['at'][1]))
                    hc_batch(cmds)
        finally:
            mode_lock.release()


_LOCK_RE = re.compile(r'^ws_(?P<id>-?\d+)(?P<layout>_layout\.json)?$')


def _cleanup_stale_locks():
    workspaces = hc_list(['workspaces'])
    if not workspaces:
        return

    active_ids = {str(w.get('id')) for w in workspaces if 'id' in w}

    fnames = os.listdir(STATE_DIR)

    for fname in fnames:
        m = _LOCK_RE.match(fname)
        if not m:
            continue
        ws_id = m.group('id')
        if ws_id not in active_ids:
            os.remove(os.path.join(STATE_DIR, fname))


_daemon_start_lock = threading.Lock()
_daemon_started = False

def start_canvas_daemon():
    global _daemon_started
    with _daemon_start_lock:
        if _daemon_started:
            return
        _daemon_started = True

    _cleanup_stale_locks()
    _ensure_batch_worker()
    threading.Thread(target=hyprland_ipc_listener, daemon=True).start()
    threading.Thread(target=hotplug_monitor,       daemon=True).start()
    threading.Thread(target=_main_canvas_loop,     daemon=True).start()