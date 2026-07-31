import os, json, subprocess, threading, time, socket
import evdev


# Конфиг
SPEED = float(os.environ.get('SPEED', 1.6))
FPS = 1.0 / int(os.environ.get('FPS_LIMIT', 60))
EDGE_ZONE = int(os.environ.get('EDGE_ZONE', 40))
EDGE_SPEED = int(20 * SPEED)

STATE_DIR = os.environ.get('STATE_DIR', os.path.expanduser("~/.cache/vidgex-shell/vidgex_canvas"))
os.makedirs(STATE_DIR, exist_ok=True)

# Глобальное состояние
lock = threading.Lock()
st = {
    'sup': False, 'alt': False, 'btn': False,
    'ax': 0.0, 'ay': 0.0,
    'last_nav_time': 0.0,
    'btn_just_pressed': False,
    'mode': None,
    'active_win_addr': None,
    'mon': {'x': 0, 'y': 0, 'w': 0, 'h': 0}
}
active_devices = set()

# Помогалки
def hc(cmd):
    try:
        res = subprocess.run(['hyprctl', '-j', *cmd], capture_output=True, text=True)
        return json.loads(res.stdout) if res.stdout.strip() else {}
    except: return {}

def hc_batch(cmds):
    if cmds: subprocess.Popen(['hyprctl', '--batch', ";".join(cmds)], stdout=subprocess.DEVNULL)

def get_cursor_pos():
    try:
        res = subprocess.run(['hyprctl', 'cursorpos'], capture_output=True, text=True)
        return map(int, res.stdout.strip().replace(' ', '').split(','))
    except: return 0, 0


# Логика переключения (TOGGLE)
def toggle_mode():
    ws_id = hc(['activeworkspace']).get('id')
    if ws_id is None: return

    lock_file = os.path.join(STATE_DIR, f"ws_{ws_id}")
    layout_file = f"{lock_file}_layout.json"
    clients = [w for w in hc(['clients']) if isinstance(w, dict) and w.get('workspace', {}).get('id') == ws_id]
    cmds = []

    if os.path.exists(lock_file):
        os.remove(lock_file)
        layout = {w['address']: {'at': w['at'], 'size': w['size']} for w in clients if w.get('floating')}
        cmds.extend([f"dispatch togglefloating address:{w['address']}" for w in clients if w.get('floating')])
        
        with open(layout_file, 'w') as f: json.dump(layout, f)
        msg = f'Tiling (Workspace {ws_id})'
    else:
        open(lock_file, 'w').close()
        layout = {}
        if os.path.exists(layout_file):
            try:
                with open(layout_file, 'r') as f: layout = json.load(f)
            except: pass

        for w in clients:
            if not w.get('floating'):
                addr = w['address']
                cmds.append(f"dispatch togglefloating address:{addr}")
                if addr in layout:
                    x, y = layout[addr]['at']
                    ww, wh = layout[addr]['size']
                    cmds.extend([f"dispatch resizewindowpixel exact {ww} {wh},address:{addr}",
                                 f"dispatch movewindowpixel exact {x} {y},address:{addr}"])
        msg = f'Canvas (Workspace {ws_id})'

    hc_batch(cmds)
    subprocess.run(['notify-send', '-a', 'Vidgex-Shell', '🔲 Workspace', msg])


# Фоновые потоки
def hyprland_ipc_listener():
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not sig: return
    
    sock_path = f"{os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')}/hypr/{sig}/.socket2.sock"
    if not os.path.exists(sock_path): sock_path = f"/tmp/hypr/{sig}/.socket2.sock"

    while True:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(sock_path)
            for line in s.makefile():
                parts = line.strip().split('>>')
                event, args = parts[0], parts[1] if len(parts) > 1 else ""
                
                if event == "activewindowv2" and (time.time() - st['last_nav_time'] < 0.5):
                    ws_id = hc(['activeworkspace']).get('id')
                    if not ws_id or not os.path.exists(os.path.join(STATE_DIR, f"ws_{ws_id}")): continue

                    foc, mon = hc(['activewindow']), next((m for m in hc(['monitors']) if m.get('focused')), None)
                    flt = [w for w in hc(['clients']) if w.get('floating') and w.get('workspace',{}).get('id') == ws_id]
                    
                    if foc and mon and flt:
                        dx = (mon['x'] + mon['width'] // 2) - (foc['at'][0] + foc['size'][0] // 2)
                        dy = (mon['y'] + mon['height'] // 2) - (foc['at'][1] + foc['size'][1] // 2)
                        if dx or dy:
                            hc_batch([f"dispatch movewindowpixel exact {int(w['at'][0]+dx)} {int(w['at'][1]+dy)},address:{w['address']}" for w in flt])

                elif event == "openwindow":
                    addr = f"0x{args.split(',')[0]}"
                    ws_id = hc(['activeworkspace']).get('id')
                    if not ws_id or not os.path.exists(os.path.join(STATE_DIR, f"ws_{ws_id}")): continue

                    w_info = next((w for w in hc(['clients']) if w.get('address') == addr), None)
                    if w_info and not w_info.get('floating'):
                        hc_batch([f"dispatch togglefloating address:{addr}",
                                  f"dispatch resizewindowpixel exact 800 600,address:{addr}",
                                  f"dispatch centerwindow address:{addr}"])
        except: time.sleep(2)

def listen_input(dev_path):
    active_devices.add(dev_path)
    try:
        device = evdev.InputDevice(dev_path)
        last_abs = {0: None, 1: None}
        
        for e in device.read_loop():
            if e.type == 1: 
                if e.code in (125, 126): 
                    with lock: st['sup'] = bool(e.value)
                elif e.code in (56, 100): 
                    with lock: st['alt'] = bool(e.value)
                elif e.code in (272, 330, 325):
                    with lock:
                        st['btn'] = bool(e.value)
                        if e.value == 1: st['btn_just_pressed'] = True
                        elif e.value == 0:
                            st['mode'] = None
                            last_abs = {0: None, 1: None}
                elif e.value in (1, 2) and e.code in (103, 108, 105, 106):
                    with lock:
                        if st['alt']: st['last_nav_time'] = time.time()
            
            elif st['mode'] == 'CANVAS' and st['sup'] and st['btn']:
                if e.type == 2:
                    with lock:
                        if e.code == 0: st['ax'] += e.value * SPEED
                        elif e.code == 1: st['ay'] += e.value * SPEED
                elif e.type == 3:
                    if e.code in last_abs:
                        if last_abs[e.code] is not None:
                            delta = (e.value - last_abs[e.code]) * SPEED * 0.5
                            with lock:
                                if e.code == 0: st['ax'] += delta
                                elif e.code == 1: st['ay'] += delta
                        last_abs[e.code] = e.value
    except: pass
    finally:
        active_devices.discard(dev_path)

def hotplug_monitor():
    while True:
        for path in evdev.list_devices():
            if path not in active_devices:
                threading.Thread(target=listen_input, args=(path,), daemon=True).start()
        time.sleep(3)

def _main_canvas_loop():
    while True:
        time.sleep(FPS)
        with lock:
            jp, btn, sup, mode = st['btn_just_pressed'], st['btn'], st['sup'], st['mode']
            dx, dy = int(round(st['ax'])), int(round(st['ay']))
            st['ax'] -= dx
            st['ay'] -= dy
            st['btn_just_pressed'] = False

        if not (btn and sup): continue

        ws_id = hc(['activeworkspace']).get('id')
        if not ws_id or not os.path.exists(os.path.join(STATE_DIR, f"ws_{ws_id}")): continue

        if jp:
            cx, cy = get_cursor_pos()
            clients = hc(['clients'])
            over_win = any(w.get('workspace',{}).get('id') == ws_id and not w.get('hidden') and w.get('mapped') and 
                           (w.get('at', [0,0])[0] <= cx <= w.get('at', [0,0])[0] + w.get('size', [0,0])[0]) and 
                           (w.get('at', [0,0])[1] <= cy <= w.get('at', [0,0])[1] + w.get('size', [0,0])[1]) 
                           for w in clients)
            
            with lock:
                if over_win:
                    st['mode'] = 'WINDOW'
                    st['active_win_addr'] = hc(['activewindow']).get('address')
                    mon = next((m for m in hc(['monitors']) if m.get('focused')), None)
                    if mon: st['mon'] = {'x': mon['x'], 'y': mon['y'], 'w': mon['width'], 'h': mon['height']}
                else:
                    st['mode'] = 'CANVAS'
                    st['ax'] = st['ay'] = 0.0
            continue

        if mode == 'CANVAS' and (dx or dy):
            hc_batch([f"dispatch movewindowpixel exact {int(w['at'][0]+dx)} {int(w['at'][1]+dy)},address:{w['address']}" 
                      for w in hc(['clients']) if w.get('floating') and w.get('workspace',{}).get('id') == ws_id])
        elif mode == 'WINDOW':
            cx, cy = get_cursor_pos()
            mon = st['mon']
            rel_x, rel_y = cx - mon['x'], cy - mon['y']
            pan_x = EDGE_SPEED if rel_x < EDGE_ZONE else (-EDGE_SPEED if rel_x > mon['w'] - EDGE_ZONE else 0)
            pan_y = EDGE_SPEED if rel_y < EDGE_ZONE else (-EDGE_SPEED if rel_y > mon['h'] - EDGE_ZONE else 0)
            
            if pan_x or pan_y:
                hc_batch([f"dispatch movewindowpixel exact {int(w['at'][0]+pan_x)} {int(w['at'][1]+pan_y)},address:{w['address']}" 
                          for w in hc(['clients']) if w.get('floating') and w.get('workspace',{}).get('id') == ws_id and w.get('address') != st['active_win_addr']])
    

# Точка входа для запуска потоков холста из основного приложения
def start_canvas_daemon():
    threading.Thread(target=hyprland_ipc_listener, daemon=True).start()
    threading.Thread(target=hotplug_monitor, daemon=True).start()
    threading.Thread(target=_main_canvas_loop, daemon=True).start()