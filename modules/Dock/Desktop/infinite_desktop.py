import os, json, subprocess, threading, time, socket
import evdev

SPEED = float(os.environ.get('SPEED', 1.6))
FPS = 1.0 / int(os.environ.get('FPS_LIMIT', 60))
EDGE_ZONE = int(os.environ.get('EDGE_ZONE', 40))
EDGE_SPEED = int(20 * SPEED)

OVERVIEW_PADDING   = int(os.environ.get('OVERVIEW_PADDING', 80))
OVERVIEW_MIN_SCALE = float(os.environ.get('OVERVIEW_MIN_SCALE', 0.15))

STATE_DIR = os.environ.get(
    'STATE_DIR',
    os.path.expanduser("~/.cache/vidgex-shell/vidgex_canvas"),
)
os.makedirs(STATE_DIR, exist_ok=True)

lock = threading.Lock()
st = {
    'sup': False, 'alt': False, 'btn': False,
    'ax': 0.0, 'ay': 0.0,
    'last_nav_time': 0.0,
    'btn_just_pressed': False,
    'mode': None,             # 'CANVAS' или 'WINDOW'
    'active_win_addr': None,
    'mon': {'x': 0, 'y': 0, 'w': 0, 'h': 0},
    # ── Overview State (ws_id -> dict) ──
    'overview': False,
    'overview_data': {},
}
active_devices = set()

# ── Колбэки для внешних подписчиков (Dock и др.) ────────────────────
_on_mode_change_callbacks = []
_on_overview_callbacks = []


def register_mode_change_callback(fn):
    _on_mode_change_callbacks.append(fn)


def register_overview_callback(fn):
    _on_overview_callbacks.append(fn)


def _fire_mode_change(ws_id, is_canvas):
    for fn in _on_mode_change_callbacks:
        try:
            fn(ws_id, is_canvas)
        except Exception:
            pass


def _fire_overview(is_overview):
    for fn in _on_overview_callbacks:
        try:
            fn(is_overview)
        except Exception:
            pass


def hc(cmd):
    try:
        res = subprocess.run(
            ['hyprctl', '-j', *cmd], capture_output=True, text=True
        )
        return json.loads(res.stdout) if res.stdout.strip() else {}
    except Exception:
        return {}


def hc_batch(cmds):
    if cmds:
        subprocess.Popen(
            ['hyprctl', 'eval', "\n".join(cmds)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def get_cursor_pos():
    try:
        res = subprocess.run(
            ['hyprctl', 'cursorpos'], capture_output=True, text=True
        )
        return map(int, res.stdout.strip().replace(' ', '').split(','))
    except Exception:
        return 0, 0


def is_canvas_mode(ws_id=None) -> bool:
    if ws_id is None:
        ws_id = hc(['activeworkspace']).get('id')
    if ws_id is None:
        return False
    return os.path.exists(os.path.join(STATE_DIR, f"ws_{ws_id}"))


def _active_canvas_ws():
    """Возвращает id активного workspace, если он в Canvas-режиме, иначе None."""
    ws_id = hc(['activeworkspace']).get('id')
    return ws_id if ws_id and is_canvas_mode(ws_id) else None


# ────────────────────────────────────────────────────────────────────
#                 ПЕРЕКЛЮЧЕНИЕ РЕЖИМА (CANVAS <-> HYPRLAND)
# ────────────────────────────────────────────────────────────────────

def toggle_mode(silent: bool = False):
    """Переключает текущий workspace между Canvas (float) и Hyprland (tiling)."""
    ws_id = hc(['activeworkspace']).get('id')
    if ws_id is None:
        return

    lock_file = os.path.join(STATE_DIR, f"ws_{ws_id}")
    layout_file = os.path.join(STATE_DIR, f"ws_{ws_id}_layout.json")

    if is_canvas_mode(ws_id):
        # ── ВЫКЛЮЧАЕМ CANVAS -> Переходим в Hyprland Tiling ──
        _exit_overview(ws_id)

        clients = [
            w for w in hc(['clients'])
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

        cmds = [
            f'hl.dispatch(hl.dsp.window.float({{ window = "address:{w["address"]}", action = "toggle" }}))'
            for w in clients
        ]
        hc_batch(cmds)

        if not silent:
            _fire_mode_change(ws_id, False)

    else:
        # ── ВКЛЮЧАЕМ CANVAS -> Переводим окна в Floating и восстанавливаем позиции ──
        open(lock_file, 'w').close()

        saved_layout = {}
        if os.path.exists(layout_file):
            with open(layout_file, 'r') as f:
                saved_layout = json.load(f)

        clients = [
            w for w in hc(['clients'])
            if w.get('workspace', {}).get('id') == ws_id
            and w.get('mapped')
            and not w.get('hidden')
            and not w.get('floating')
        ]

        cmds = []
        for w in clients:
            addr = w['address']
            cmds.append(
                f'hl.dispatch(hl.dsp.window.float({{ window = "address:{addr}", action = "toggle" }}))'
            )

            if addr in saved_layout:
                geo = saved_layout[addr]
                new_x, new_y = geo['at']
                new_w, new_h = geo['size']
                cmds.extend([
                    f'hl.dispatch(hl.dsp.window.resize({{ window = "address:{addr}", x = {new_w}, y = {new_h}, relative = false }}))',
                    f'hl.dispatch(hl.dsp.window.move({{ window = "address:{addr}", x = {new_x}, y = {new_y}, relative = false }}))',
                ])

        hc_batch(cmds)

        if not silent:
            _fire_mode_change(ws_id, True)


def enable_canvas():
    if not is_canvas_mode():
        toggle_mode()


# ────────────────────────────────────────────────────────────────────
#                     ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ КАМЕРЫ
# ────────────────────────────────────────────────────────────────────

def _center_camera_on_window(ws_id, win_addr):
    """Центрирует камеру холста на указанном окне."""
    if not win_addr:
        return
    mon = next((m for m in hc(['monitors']) if m.get('focused')), None)
    clients = [
        w for w in hc(['clients'])
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
            f'hl.dispatch(hl.dsp.window.move({{ window = "address:{w["address"]}", x = {int(w["at"][0]+dx)}, y = {int(w["at"][1]+dy)}, relative = false }}))'
            for w in clients
        ])


# ────────────────────────────────────────────────────────────────────
#                        OVERVIEW LOGIC (Affin Math)
# ────────────────────────────────────────────────────────────────────

def toggle_overview():
    """Переключает режим обзора."""
    ws_id = _active_canvas_ws()
    if not ws_id:
        return

    with lock:
        is_on = st['overview']

    if is_on:
        _exit_overview(ws_id)
    else:
        _enter_overview(ws_id)


def _enter_overview(ws_id):
    mon = next((m for m in hc(['monitors']) if m.get('focused')), None)
    if not mon:
        return

    clients = [
        w for w in hc(['clients'])
        if w.get('floating')
        and w.get('workspace', {}).get('id') == ws_id
        and w.get('mapped')
        and not w.get('hidden')
        and w['size'][0] > 0
        and w['size'][1] > 0
    ]
    if not clients:
        return

    # 1. Bounding Box всех окон в Canvas координатах
    min_x = min(w['at'][0] for w in clients)
    min_y = min(w['at'][1] for w in clients)
    max_x = max(w['at'][0] + w['size'][0] for w in clients)
    max_y = max(w['at'][1] + w['size'][1] for w in clients)
    bbox_w = max(max_x - min_x, 1)
    bbox_h = max(max_y - min_y, 1)

    # 2. Расчет коэффициента масштабирования S
    avail_w = max(mon['width']  - 2 * OVERVIEW_PADDING, 1)
    avail_h = max(mon['height'] - 2 * OVERVIEW_PADDING, 1)

    scale = min(avail_w / bbox_w, avail_h / bbox_h, 1.0)
    scale = max(scale, OVERVIEW_MIN_SCALE)

    # 3. Начальное смещение (Центрирование Bounding Box на мониторе)
    new_bbox_w = bbox_w * scale
    new_bbox_h = bbox_h * scale
    off_x = mon['x'] + (mon['width']  - new_bbox_w) / 2.0
    off_y = mon['y'] + (mon['height'] - new_bbox_h) / 2.0

    cmds = []
    saved_geoms = {}
    overview_target_geoms = {}

    for w in clients:
        addr = w['address']
        saved_geoms[addr] = {
            'at': list(w['at']),
            'size': list(w['size'])
        }

        rel_x = w['at'][0] - min_x
        rel_y = w['at'][1] - min_y

        new_x = int(round(off_x + rel_x * scale))
        new_y = int(round(off_y + rel_y * scale))
        new_w = max(1, int(round(w['size'][0] * scale)))
        new_h = max(1, int(round(w['size'][1] * scale)))

        overview_target_geoms[addr] = {
            'at': [new_x, new_y],
            'size': [new_w, new_h]
        }

        cmds.extend([
            f'hl.dispatch(hl.dsp.window.resize({{ window = "address:{addr}", x = {new_w}, y = {new_h}, relative = false }}))',
            f'hl.dispatch(hl.dsp.window.move({{ window = "address:{addr}", x = {new_x}, y = {new_y}, relative = false }}))',
        ])

    hc_batch(cmds)

    with lock:
        st['overview'] = True
        st['overview_data'][ws_id] = {
            'scale': scale,
            'bbox_min': [min_x, min_y],
            'offset': [off_x, off_y],
            'saved_geoms': saved_geoms,
            'overview_target_geoms': overview_target_geoms,
        }

    _fire_overview(True)


def _exit_overview(ws_id):
    with lock:
        was = st['overview']
        data = st['overview_data'].pop(ws_id, None)
        if not st['overview_data']:
            st['overview'] = False

    if not was or not data:
        return

    scale = data['scale']
    bbox_min = data['bbox_min']
    off_x, off_y = data['offset']
    saved_geoms = data['saved_geoms']
    overview_target_geoms = data['overview_target_geoms']

    mon = next((m for m in hc(['monitors']) if m.get('focused')), None)
    clients = [
        w for w in hc(['clients'])
        if w.get('floating')
        and w.get('workspace', {}).get('id') == ws_id
        and w.get('mapped')
        and not w.get('hidden')
    ]

    if not clients or not mon:
        _fire_overview(False)
        return

    focus_addr = hc(['activewindow']).get('address')

    # 1. Расчет реальной геометрии (Zero-Drift)
    real_geoms = {}
    for w in clients:
        addr = w['address']
        curr_x, curr_y = w['at']
        curr_w, curr_h = w['size']

        target = overview_target_geoms.get(addr)
        saved = saved_geoms.get(addr)

        # Если окно НЕ двигали/НЕ меняли в режиме обзора -> восстанавливаем точные исходные координаты
        if target and saved and (
            abs(curr_x - target['at'][0]) <= 2 and
            abs(curr_y - target['at'][1]) <= 2 and
            abs(curr_w - target['size'][0]) <= 2 and
            abs(curr_h - target['size'][1]) <= 2
        ):
            real_geoms[addr] = {
                'at': list(saved['at']),
                'size': list(saved['size'])
            }
        else:
            # Окно перемещали или меняли размер -> обратное масштабирование
            real_w = max(1, int(round(curr_w / scale)))
            real_h = max(1, int(round(curr_h / scale)))

            rel_x = (curr_x - off_x) / scale
            rel_y = (curr_y - off_y) / scale

            real_x = int(round(bbox_min[0] + rel_x))
            real_y = int(round(bbox_min[1] + rel_y))

            real_geoms[addr] = {'at': [real_x, real_y], 'size': [real_w, real_h]}

    # 2. Привязка камеры к фокусному окну (Focus Latching)
    dx, dy = 0, 0
    if focus_addr and focus_addr in real_geoms:
        foc_geo = real_geoms[focus_addr]
        mon_cx = mon['x'] + mon['width'] // 2
        mon_cy = mon['y'] + mon['height'] // 2
        win_cx = foc_geo['at'][0] + foc_geo['size'][0] // 2
        win_cy = foc_geo['at'][1] + foc_geo['size'][1] // 2

        dx = mon_cx - win_cx
        dy = mon_cy - win_cy

    # 3. Применение финальной 1:1 Canvas геометрии
    cmds = []
    for addr, geo in real_geoms.items():
        final_x = geo['at'][0] + dx
        final_y = geo['at'][1] + dy
        final_w = geo['size'][0]
        final_h = geo['size'][1]

        cmds.extend([
            f'hl.dispatch(hl.dsp.window.resize({{ window = "address:{addr}", x = {final_w}, y = {final_h}, relative = false }}))',
            f'hl.dispatch(hl.dsp.window.move({{ window = "address:{addr}", x = {final_x}, y = {final_y}, relative = false }}))',
        ])

    hc_batch(cmds)

    if focus_addr:
        subprocess.run(
            ['hyprctl', 'dispatch', 'focuswindow', f'address:{focus_addr}'],
            stdout=subprocess.DEVNULL,
        )

    _fire_overview(False)


def _restore_overview_if_needed():
    with lock:
        ws_ids = list(st['overview_data'].keys())
    for ws_id in ws_ids:
        _exit_overview(ws_id)


# ────────────────────────────────────────────────────────────────────
#                       HYPRLAND IPC LISTENER
# ────────────────────────────────────────────────────────────────────

def hyprland_ipc_listener():
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not sig:
        return

    sock_path = (
        f"{os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')}"
        f"/hypr/{sig}/.socket2.sock"
    )
    if not os.path.exists(sock_path):
        sock_path = f"/tmp/hypr/{sig}/.socket2.sock"

    while True:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(sock_path)
            for line in s.makefile():
                parts = line.strip().split('>>')
                event = parts[0]
                args = parts[1] if len(parts) > 1 else ""

                # ── Смена фокуса ───────
                if event == "activewindowv2" and (
                    time.time() - st['last_nav_time'] < 0.5
                ):
                    ws_id = _active_canvas_ws()
                    if not ws_id:
                        continue

                    with lock:
                        is_ov = st['overview']

                    if not is_ov:
                        addr = hc(['activewindow']).get('address')
                        _center_camera_on_window(ws_id, addr)

                # ── Смена рабочего стола ─────────────────────────────
                elif event == "workspace":
                    _restore_overview_if_needed()
                    ws_id = hc(['activeworkspace']).get('id')
                    if ws_id is not None:
                        _fire_mode_change(ws_id, is_canvas_mode(ws_id))

                # ── Новое окно ───────────────────────────────────────
                elif event == "openwindow":
                    addr = f"0x{args.split(',')[0]}"
                    if not _active_canvas_ws():
                        continue

                    w_info = next(
                        (w for w in hc(['clients']) if w.get('address') == addr),
                        None,
                    )
                    if w_info and not w_info.get('floating'):
                        hc_batch([
                            f'hl.dispatch(hl.dsp.window.float({{ window = "address:{addr}", action = "toggle" }}))',
                            f'hl.dispatch(hl.dsp.window.resize({{ window = "address:{addr}", x = 800, y = 600, relative = false }}))',
                            f'hl.dispatch(hl.dsp.window.center({{ window = "address:{addr}" }}))',
                        ])
        except Exception:
            time.sleep(2)


# ────────────────────────────────────────────────────────────────────
#                          INPUT LISTENER
# ────────────────────────────────────────────────────────────────────

def listen_input(dev_path):
    active_devices.add(dev_path)
    try:
        device = evdev.InputDevice(dev_path)
        last_abs = {0: None, 1: None}

        for e in device.read_loop():
            if e.type == 1:
                if e.code in (125, 126):
                    with lock:
                        st['sup'] = bool(e.value)
                elif e.code in (56, 100):
                    with lock:
                        st['alt'] = bool(e.value)
                elif e.code in (272, 330, 325):
                    with lock:
                        st['btn'] = bool(e.value)
                        if e.value == 1:
                            st['btn_just_pressed'] = True
                        elif e.value == 0:
                            st['mode'] = None
                            last_abs = {0: None, 1: None}
                elif e.value in (1, 2) and e.code in (103, 108, 105, 106):
                    with lock:
                        if st['alt']:
                            st['last_nav_time'] = time.time()

            elif st['mode'] == 'CANVAS' and st['sup'] and st['btn']:
                if e.type == 2:
                    with lock:
                        if e.code == 0:
                            st['ax'] += e.value * SPEED
                        elif e.code == 1:
                            st['ay'] += e.value * SPEED
                elif e.type == 3:
                    if e.code in last_abs:
                        if last_abs[e.code] is not None:
                            delta = (e.value - last_abs[e.code]) * SPEED * 0.5
                            with lock:
                                if e.code == 0:
                                    st['ax'] += delta
                                elif e.code == 1:
                                    st['ay'] += delta
                        last_abs[e.code] = e.value
    except Exception:
        pass
    finally:
        active_devices.discard(dev_path)


def hotplug_monitor():
    while True:
        for path in evdev.list_devices():
            if path not in active_devices:
                threading.Thread(
                    target=listen_input, args=(path,), daemon=True
                ).start()
        time.sleep(3)


# ────────────────────────────────────────────────────────────────────
#                          MAIN CANVAS LOOP
# ────────────────────────────────────────────────────────────────────

def _main_canvas_loop():
    while True:
        time.sleep(FPS)
        with lock:
            jp = st['btn_just_pressed']
            btn = st['btn']
            sup = st['sup']
            mode = st['mode']
            dx, dy = int(round(st['ax'])), int(round(st['ay']))
            st['ax'] -= dx
            st['ay'] -= dy
            st['btn_just_pressed'] = False

        if not (btn and sup):
            continue

        ws_id = _active_canvas_ws()
        if not ws_id:
            continue

        if jp:
            cx, cy = get_cursor_pos()
            clients = hc(['clients'])
            over_win = any(
                w.get('workspace', {}).get('id') == ws_id
                and not w.get('hidden')
                and w.get('mapped')
                and (w['at'][0] <= cx <= w['at'][0] + w['size'][0])
                and (w['at'][1] <= cy <= w['at'][1] + w['size'][1])
                for w in clients
            )
            with lock:
                if over_win:
                    st['mode'] = 'WINDOW'
                    st['active_win_addr'] = hc(['activewindow']).get('address')
                    mon = next(
                        (m for m in hc(['monitors']) if m.get('focused')), None
                    )
                    if mon:
                        st['mon'] = {
                            'x': mon['x'], 'y': mon['y'],
                            'w': mon['width'], 'h': mon['height'],
                        }
                else:
                    st['mode'] = 'CANVAS'
                    st['ax'] = st['ay'] = 0.0
            continue

        # ── 1. Панорамирование всего холста (Canvas / Overview) ──
        if mode == 'CANVAS' and (dx or dy):
            hc_batch([
                f'hl.dispatch(hl.dsp.window.move({{ window = "address:{w["address"]}", x = {int(w["at"][0]+dx)}, y = {int(w["at"][1]+dy)}, relative = false }}))'
                for w in hc(['clients'])
                if w.get('floating')
                and w.get('workspace', {}).get('id') == ws_id
            ])

        # ── 2. Перетаскивание окна с краевым панорамированием ──
        elif mode == 'WINDOW':
            cx, cy = get_cursor_pos()
            mon = st['mon']
            rel_x, rel_y = cx - mon['x'], cy - mon['y']

            pan_x = 0
            pan_y = 0

            if rel_x < EDGE_ZONE:
                pan_x = EDGE_SPEED
            elif rel_x > mon['w'] - EDGE_ZONE:
                pan_x = -EDGE_SPEED

            if rel_y < EDGE_ZONE:
                pan_y = EDGE_SPEED
            elif rel_y > mon['h'] - EDGE_ZONE:
                pan_y = -EDGE_SPEED

            if pan_x or pan_y:
                hc_batch([
                    f'hl.dispatch(hl.dsp.window.move({{ window = "address:{w["address"]}", x = {int(w["at"][0]+pan_x)}, y = {int(w["at"][1]+pan_y)}, relative = false }}))'
                    for w in hc(['clients'])
                    if w.get('floating')
                    and w.get('workspace', {}).get('id') == ws_id
                    and w.get('address') != st['active_win_addr']
                ])


def _cleanup_stale_locks():
    active_ws_ids = {str(w.get('id')) for w in hc(['workspaces'])}
    for fname in os.listdir(STATE_DIR):
        if fname.startswith('ws_'):
            ws_id = fname.replace('ws_', '').replace('_layout.json', '')
            if ws_id not in active_ws_ids:
                os.remove(os.path.join(STATE_DIR, fname))


def start_canvas_daemon():
    _cleanup_stale_locks()
    threading.Thread(target=hyprland_ipc_listener, daemon=True).start()
    threading.Thread(target=hotplug_monitor,       daemon=True).start()
    threading.Thread(target=_main_canvas_loop,     daemon=True).start()