#!/usr/bin/env python3
"""
Hyprland Session Saver v1.6
- Учитывает приложения с собственным восстановлением сессии (VSCode, Firefox, etc.)
- Такие приложения запускаются ОДИН раз, потом ждём ВСЕ их окна
"""

import json
import os
import subprocess
import sys
import time
import re
from pathlib import Path
from typing import Optional
from collections import defaultdict


SESSION_FILE = Path.home() / ".cache" / "vidgex-shell" / "session.json"
WINDOW_TIMEOUT = 10
POLL_INTERVAL = 0.05

# Приложения, которые САМИ восстанавливают свои окна
# Запускаем только ОДИН раз, независимо от количества окон в сессии
SELF_RESTORING_APPS = {
    # VSCode
    "code", "code-oss", "vscodium", "codium",
    # Браузеры
    "firefox", "firefox-esr", "librewolf",
    "chromium", "chromium-browser",
    "google-chrome", "google-chrome-stable",
    "brave", "brave-browser",
    "vivaldi", "opera",
    # Electron apps с восстановлением
    "slack", "discord", "spotify",
}

DESKTOP_DIRS = [
    Path("/usr/share/applications"),
    Path("/usr/local/share/applications"),
    Path.home() / ".local/share/applications",
    Path("/var/lib/flatpak/exports/share/applications"),
    Path.home() / ".local/share/flatpak/exports/share/applications",
]


def hyprctl_json(cmd: str):
    try:
        r = subprocess.run(["hyprctl", cmd, "-j"], capture_output=True, text=True, timeout=2)
        return json.loads(r.stdout) if r.returncode == 0 else None
    except:
        return None


def hyprctl_dispatch(cmd: str):
    subprocess.run(["hyprctl", "dispatch", "--", cmd], capture_output=True, text=True, timeout=2)


def get_all_windows() -> list[dict]:
    return hyprctl_json("clients") or []


def get_window_addresses() -> set[str]:
    return {w["address"] for w in get_all_windows() if w.get("address")}


def get_active_workspace() -> int:
    ws = hyprctl_json("activeworkspace")
    return ws.get("id", 1) if ws else 1


def is_self_restoring(class_name: str, cmd: str) -> bool:
    """Проверяет, восстанавливает ли приложение сессию само"""
    cl = class_name.lower()
    
    # Проверяем по классу
    for app in SELF_RESTORING_APPS:
        if app in cl or cl in app:
            return True
    
    # Проверяем по команде
    cmd_lower = cmd.lower()
    for app in SELF_RESTORING_APPS:
        if app in cmd_lower:
            return True
    
    return False


class AppsRegistry:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._loaded = False
            cls._instance._by_class = {}
            cls._instance._by_name = {}
        return cls._instance

    def load(self):
        if self._loaded:
            return

        for d in DESKTOP_DIRS:
            if not d.exists():
                continue
            for f in d.glob("*.desktop"):
                self._parse(f)
        self._loaded = True

    def _parse(self, path: Path):
        try:
            text = path.read_text(errors='ignore')
        except:
            return

        exe = wm_class = ""
        in_entry = is_app = False

        for line in text.splitlines():
            line = line.strip()
            if line.startswith('['):
                in_entry = (line == '[Desktop Entry]')
                continue
            if not in_entry or '=' not in line:
                continue

            k, _, v = line.partition('=')
            k, v = k.strip(), v.strip()

            if k == 'Exec':
                exe = re.sub(r'%[a-zA-Z]', '', v).strip()
            elif k == 'StartupWMClass':
                wm_class = v
            elif k == 'Type' and v == 'Application':
                is_app = True

        if not is_app or not exe:
            return

        fname = path.stem.lower()
        self._by_name[fname] = exe

        if wm_class:
            self._by_class[wm_class.lower()] = exe

        bin_name = os.path.basename(exe.split()[0]).lower()
        if bin_name and bin_name not in self._by_class:
            self._by_class[bin_name] = exe

    def find(self, window_class: str, initial_class: str = "") -> Optional[str]:
        self.load()

        for cls in (window_class, initial_class):
            if not cls:
                continue
            cl = cls.lower()

            if cl in self._by_class:
                return self._by_class[cl]
            if cl in self._by_name:
                return self._by_name[cl]

            for key, exe in self._by_class.items():
                if cl in key or key in cl:
                    return exe
            for key, exe in self._by_name.items():
                if cl in key or key in cl:
                    return exe

        return window_class.lower().replace('.', '-') if window_class else None


def save_session():
    print("💾 Сохранение сессии...\n")

    reg = AppsRegistry()
    clients = get_all_windows()

    windows = []
    for c in clients:
        ws_id = c.get("workspace", {}).get("id", 0)
        if ws_id < 0:
            continue

        cls = c.get("class", "")
        init_cls = c.get("initialClass", "")

        if not cls and not init_cls:
            continue

        cmd = reg.find(cls, init_cls)
        if not cmd:
            print(f"  ⚠ Не найдено: {cls}")
            continue

        windows.append({
            "class": cls,
            "initial_class": init_cls,
            "workspace": ws_id,
            "floating": c.get("floating", False),
            "fullscreen": c.get("fullscreen", 0),
            "pos": c.get("at", [0, 0]),
            "size": c.get("size", [800, 600]),
            "pinned": c.get("pinned", False),
            "cmd": cmd
        })
        print(f"  ✓ {cls} [WS {ws_id}]")

    windows.sort(key=lambda w: (w["workspace"], w["class"]))

    session = {
        "v": "1.6",
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "active_ws": get_active_workspace(),
        "windows": windows
    }

    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(json.dumps(session, indent=2, ensure_ascii=False))

    print(f"\n✅ Сохранено: {len(windows)} окон")


def wait_for_windows(known_addresses: set[str], expected_count: int, 
                     target_class: str = "", timeout: float = WINDOW_TIMEOUT) -> list[str]:
    """
    Ждёт появления нескольких новых окон.
    Возвращает список новых адресов.
    """
    start = time.time()
    found = []
    target_lower = target_class.lower()
    
    # Для self-restoring apps ждём чуть дольше и проверяем стабильность
    stable_count = 0
    last_count = 0
    
    while time.time() - start < timeout:
        current = get_window_addresses()
        new_addrs = current - known_addresses
        
        # Фильтруем по классу если указан
        if target_lower:
            matching = []
            for addr in new_addrs:
                for w in get_all_windows():
                    if w.get("address") == addr and target_lower in w.get("class", "").lower():
                        matching.append(addr)
                        break
            new_addrs = set(matching)
        
        found = list(new_addrs)
        
        # Проверяем стабильность (для self-restoring apps)
        if len(found) >= expected_count:
            if len(found) == last_count:
                stable_count += 1
                if stable_count >= 5:  # 5 * 0.05 = 0.25с стабильности
                    break
            else:
                stable_count = 0
                last_count = len(found)
        
        time.sleep(POLL_INTERVAL)
    
    return found


def restore_session():
    print("🔄 Восстановление сессии...\n")

    if not SESSION_FILE.exists():
        print("❌ Файл не найден")
        return

    session = json.loads(SESSION_FILE.read_text())
    windows = session.get("windows", [])
    active_ws = session.get("active_ws", 1)

    print(f"📅 Сессия: {session.get('ts')}")
    print(f"📊 Окон в сессии: {len(windows)}\n")

    if not windows:
        return

    # ═══════════════════════════════════════════════════════════════════
    # ШАГ 1: Группируем окна по классу
    # ═══════════════════════════════════════════════════════════════════
    
    session_by_class: dict[str, list[dict]] = defaultdict(list)
    for w in windows:
        cls = w.get("class", "").lower()
        if cls:
            session_by_class[cls].append(w)

    # Существующие окна
    existing_windows = get_all_windows()
    existing_by_class: dict[str, list[dict]] = defaultdict(list)
    for w in existing_windows:
        cls = w.get("class", "").lower()
        if cls:
            existing_by_class[cls].append(w)

    print(f"Существующие окна: {len(existing_windows)}")
    for cls, wins in existing_by_class.items():
        print(f"  • {cls}: {len(wins)}")
    print()

    # ═══════════════════════════════════════════════════════════════════
    # ШАГ 2: Обрабатываем по классам
    # ═══════════════════════════════════════════════════════════════════
    
    known_addresses = get_window_addresses()
    restored = 0
    total_expected = len(windows)

    for cls, session_wins in session_by_class.items():
        existing_wins = existing_by_class.get(cls, [])
        needed = len(session_wins)
        have = len(existing_wins)
        
        print(f"{'═' * 45}")
        print(f"📦 {cls}: нужно {needed}, есть {have}")
        
        cmd = session_wins[0].get("cmd", "")
        is_self_rest = is_self_restoring(cls, cmd)
        
        if is_self_rest:
            print(f"   ⚡ Self-restoring app — запуск ОДИН раз")
        
        # Сколько окон нужно получить
        to_create = needed - have
        
        if to_create > 0 and cmd:
            if is_self_rest:
                # Запускаем ОДИН раз и ждём ВСЕ окна
                print(f"   🚀 Запуск: {cmd}")
                subprocess.Popen(
                    cmd,
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True
                )
                
                # Ждём появления ВСЕХ нужных окон
                print(f"   ⏳ Ожидание {to_create} окон...")
                new_addrs = wait_for_windows(
                    known_addresses, 
                    expected_count=to_create,
                    target_class=cls,
                    timeout=WINDOW_TIMEOUT
                )
                
                print(f"   ✓ Появилось: {len(new_addrs)} окон")
                
                for addr in new_addrs:
                    known_addresses.add(addr)
                    existing_wins.append({"address": addr})
                
            else:
                # Обычное приложение — запускаем по одному
                for i in range(to_create):
                    print(f"   🚀 Запуск #{i+1}: {cmd}")
                    
                    before = get_window_addresses()
                    
                    subprocess.Popen(
                        cmd,
                        shell=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True
                    )
                    
                    # Ждём одно окно
                    new_addrs = wait_for_windows(before, expected_count=1, timeout=WINDOW_TIMEOUT)
                    
                    if new_addrs:
                        addr = new_addrs[0]
                        known_addresses.add(addr)
                        existing_wins.append({"address": addr})
                        print(f"   ✓ Создано: {addr}")
                    else:
                        print(f"   ✗ Таймаут")
        
        # Небольшая пауза для инициализации
        time.sleep(0.2)
        
        # Получаем актуальный список окон этого класса
        current_windows = [w for w in get_all_windows() if w.get("class", "").lower() == cls]
        
        print(f"   📍 Распределение по workspace:")
        
        # Распределяем окна по workspace
        for i, sw in enumerate(session_wins):
            target_ws = sw.get("workspace", 1)
            
            if i < len(current_windows):
                addr = current_windows[i]["address"]
                
                print(f"      • Окно {i+1} → WS {target_ws}")
                hyprctl_dispatch(f"movetoworkspacesilent {target_ws},address:{addr}")
                
                # Применяем свойства
                if sw.get("floating"):
                    hyprctl_dispatch(f"setfloating address:{addr}")
                    pos = sw.get("pos", [0, 0])
                    size = sw.get("size", [800, 600])
                    time.sleep(0.05)
                    hyprctl_dispatch(f"resizewindowpixel exact {size[0]} {size[1]},address:{addr}")
                    hyprctl_dispatch(f"movewindowpixel exact {pos[0]} {pos[1]},address:{addr}")
                
                if sw.get("fullscreen"):
                    hyprctl_dispatch(f"focuswindow address:{addr}")
                    hyprctl_dispatch(f"fullscreen {sw['fullscreen']}")
                
                restored += 1
            else:
                print(f"      ⚠ Окно {i+1} — не хватает окон!")

    # Переключаемся на активный workspace
    time.sleep(0.1)
    hyprctl_dispatch(f"workspace {active_ws}")

    # ═══════════════════════════════════════════════════════════════════
    # Итог
    # ═══════════════════════════════════════════════════════════════════
    
    print(f"\n{'═' * 45}")
    print(f"✅ Восстановлено: {restored}/{total_expected}")
    
    print(f"\nИтоговое состояние:")
    for w in get_all_windows():
        ws = w.get("workspace", {}).get("id", "?")
        print(f"  • {w.get('class')} @ WS{ws}")

    try:
        subprocess.run([
            "notify-send", "Session Restored",
            f"{restored}/{total_expected} окон", "-t", "3000"
        ], capture_output=True, timeout=1)
    except:
        pass


def main():
    if len(sys.argv) < 2:
        print("save | restore")
        return

    cmd = sys.argv[1].lower()

    if cmd == "save":
        save_session()
    elif cmd == "restore":
        restore_session()

if __name__ == "__main__":
    main()