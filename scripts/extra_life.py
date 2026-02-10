#!/usr/bin/env python3

import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

SESSION_DIR = Path.home() / ".cache" / "vidgex-shell"
SESSION_FILE = SESSION_DIR / "session.json"


@dataclass
class ProcessInfo:
    pid: int
    cmdline: str = ""
    cwd: str = ""
    cmdline_args: list = field(default_factory=list)
    
    @classmethod
    def from_pid(cls, pid: int) -> Optional["ProcessInfo"]:
        proc_path = Path(f"/proc/{pid}")
        if not proc_path.exists():
            return None
        
        info = cls(pid=pid)
        
        try:
            cmdline_raw = (proc_path / "cmdline").read_bytes()
            info.cmdline_args = [arg for arg in cmdline_raw.decode(errors='ignore').split('\x00') if arg]
            info.cmdline = ' '.join(info.cmdline_args)
        except:
            pass
        
        try:
            info.cwd = os.readlink(proc_path / "cwd")
        except:
            info.cwd = str(Path.home())
        
        return info


@dataclass
class DesktopEntry:
    exec_cmd: str = ""
    wm_class: str = ""
    categories: set = field(default_factory=set)
    is_terminal: bool = False
    is_flatpak: bool = False
    flatpak_id: str = ""
    
    @classmethod
    def find_by_class(cls, wm_class: str) -> Optional["DesktopEntry"]:
        data_dirs = os.environ.get('XDG_DATA_DIRS', '/usr/share:/usr/local/share').split(':')
        data_dirs.append(str(Path.home() / ".local/share"))
        
        flatpak_dirs = [
            "/var/lib/flatpak/exports/share",
            str(Path.home() / ".local/share/flatpak/exports/share"),
        ]
        
        all_dirs = [Path(d) / "applications" for d in data_dirs + flatpak_dirs]
        wm_lower = wm_class.lower()
        
        for d in all_dirs:
            if not d.exists():
                continue
            for f in d.glob("*.desktop"):
                entry = cls._parse_desktop(f, wm_lower)
                if entry:
                    if "flatpak" in str(f):
                        entry.is_flatpak = True
                        entry.flatpak_id = f.stem
                    return entry
        return None
    
    @classmethod
    def _parse_desktop(cls, path: Path, target_class: str) -> Optional["DesktopEntry"]:
        try:
            text = path.read_text(errors='ignore')
        except:
            return None
        
        entry = cls()
        in_entry = False
        match_found = False
        
        for line in text.splitlines():
            line = line.strip()
            if line.startswith('['):
                in_entry = (line == '[Desktop Entry]')
                continue
            if not in_entry or '=' not in line:
                continue
            
            key, _, val = line.partition('=')
            key, val = key.strip(), val.strip()
            
            match key:
                case 'Exec':
                    entry.exec_cmd = re.sub(r'%[a-zA-Z]', '', val).strip()
                    if 'flatpak run' in val:
                        entry.is_flatpak = True
                case 'StartupWMClass':
                    entry.wm_class = val
                    if val.lower() == target_class:
                        match_found = True
                case 'Categories':
                    entry.categories = set(c.strip() for c in val.split(';') if c.strip())
                case 'Terminal':
                    entry.is_terminal = val.lower() == 'true'
                case 'X-Flatpak':
                    entry.is_flatpak = True
                    entry.flatpak_id = val
        
        if not match_found:
            file_stem = path.stem.lower()
            if target_class not in file_stem and file_stem not in target_class:
                return None
        
        if 'TerminalEmulator' in entry.categories:
            entry.is_terminal = True
        
        return entry


def hyprctl(cmd: str) -> Optional[dict | list]:
    try:
        r = subprocess.run(["hyprctl", "-j"] + cmd.split(), 
                          capture_output=True, text=True, timeout=2)
        return json.loads(r.stdout) if r.returncode == 0 else None
    except:
        return None


def dispatch(cmd: str):
    subprocess.run(["hyprctl", "dispatch", "--", cmd], 
                   capture_output=True, timeout=2)


def batch_dispatch(commands: list[str]):
    if commands:
        batch = ";".join(f"dispatch {c}" for c in commands)
        subprocess.run(["hyprctl", "--batch", batch], capture_output=True, timeout=5)


def is_user_path(path: str) -> bool:
    home = str(Path.home())
    if not path.startswith(home):
        return False
    relative = path[len(home):]
    excluded = ('/.cache/', '/.local/share/', '/.config/')
    return not any(ex in relative for ex in excluded)


def get_current_terminal_pid() -> Optional[int]:
    try:
        ppid = os.getppid()
        stat = Path(f"/proc/{ppid}/stat").read_text()
        return int(stat.split()[3])
    except:
        return None


def get_windows_by_class() -> dict[str, list[dict]]:
    clients = hyprctl("clients") or []
    by_class: dict[str, list[dict]] = defaultdict(list)
    for w in clients:
        cls = w.get("class", "").lower()
        if cls:
            by_class[cls].append(w)
    return dict(by_class)


def close_excess_windows(target_counts: dict[str, int], protect_pid: Optional[int] = None) -> int:
    current = get_windows_by_class()
    closed = 0
    
    for cls, wins in current.items():
        target = target_counts.get(cls, 0)
        excess = len(wins) - target
        
        if excess > 0:
            for win in wins[-excess:]:
                pid = win.get("pid", 0)
                if pid == protect_pid:
                    continue
                addr = win.get("address", "")
                if addr:
                    dispatch(f"closewindow address:{addr}")
                    closed += 1
    
    return closed


class SessionManager:
    def __init__(self):
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        self.terminal_pid = get_current_terminal_pid()
    
    def _get_launch_cmd(self, desktop: Optional[DesktopEntry], wm_class: str) -> str:
        if desktop:
            if desktop.is_flatpak and desktop.flatpak_id:
                return f"flatpak run {desktop.flatpak_id}"
            if desktop.exec_cmd:
                return desktop.exec_cmd
        return wm_class.lower()
    
    def _detect_terminal(self, pid: int) -> bool:
        try:
            fd_dir = Path(f"/proc/{pid}/fd")
            for fd in fd_dir.iterdir():
                try:
                    target = os.readlink(fd)
                    if "/dev/pts/" in target:
                        return True
                except:
                    continue
        except:
            pass
        return False
    
    def _get_project(self, proc: ProcessInfo) -> str:
        for arg in proc.cmdline_args[1:]:
            if arg.startswith('-'):
                continue
            path = Path(arg)
            if path.exists() and path.is_dir() and is_user_path(str(path)):
                return str(path)
        
        if is_user_path(proc.cwd):
            return proc.cwd
        
        return ""
    
    def save(self):
        print("💾 Сохранение сессии...\n")
        
        clients = hyprctl("clients") or []
        ws_info = hyprctl("activeworkspace") or {}
        
        windows_data = []
        
        for client in clients:
            wm_class = client.get("class", "")
            pid = client.get("pid", 0)
            ws_id = client.get("workspace", {}).get("id", 1)
            
            if not wm_class or ws_id < 0 or pid <= 0:
                continue
            
            proc = ProcessInfo.from_pid(pid)
            if not proc:
                continue
            
            desktop = DesktopEntry.find_by_class(wm_class)
            project = self._get_project(proc)
            launch_cmd = self._get_launch_cmd(desktop, wm_class)
            
            windows_data.append({
                "wm_class": wm_class,
                "wm_class_lower": wm_class.lower(),
                "workspace": ws_id,
                "floating": client.get("floating", False),
                "fullscreen": client.get("fullscreen", 0),
                "position": list(client.get("at", [0, 0])),
                "size": list(client.get("size", [800, 600])),
                "launch_cmd": launch_cmd,
                "project": project,
                "is_terminal": desktop.is_terminal if desktop else self._detect_terminal(pid),
            })
        
        by_class: dict[str, list[dict]] = defaultdict(list)
        for w in windows_data:
            by_class[w["wm_class_lower"]].append(w)
        
        for cls, wins in by_class.items():
            is_multi = len(wins) > 1
            for w in wins:
                w["is_multi_instance"] = is_multi
        
        windows = []
        for w in windows_data:
            del w["wm_class_lower"]
            windows.append(w)
            
            if w["is_terminal"]:
                icon, note = "💻", "terminal"
                if w["project"]:
                    note += f" @ {Path(w['project']).name}"
            elif w["is_multi_instance"]:
                project_name = Path(w["project"]).name if w["project"] else "?"
                icon, note = "📑", f"multi → {project_name}"
            else:
                icon, note = "📦", "single"
            
            print(f"  {icon} {w['wm_class']} [WS{w['workspace']}] ({note})")
        
        session = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "active_workspace": ws_info.get("id", 1),
            "windows": windows
        }
        
        SESSION_FILE.write_text(json.dumps(session, indent=2))
        print(f"\n✅ Сохранено: {len(windows)} окон")
    
    def restore(self):
        print("🔄 Восстановление сессии...\n")
        
        if not SESSION_FILE.exists():
            print("❌ Файл сессии не найден")
            return
        
        session = json.loads(SESSION_FILE.read_text())
        saved_windows = session.get("windows", [])
        active_ws = session.get("active_workspace", 1)
        
        print(f"📅 {session.get('timestamp')} | Цель: {len(saved_windows)} окон\n")
        
        if not saved_windows:
            return
        
        start = time.time()
        
        target_counts: dict[str, int] = defaultdict(int)
        for w in saved_windows:
            cls = w.get("wm_class", "").lower()
            if cls:
                target_counts[cls] += 1
        
        saved_by_class: dict[str, list[dict]] = defaultdict(list)
        for w in saved_windows:
            cls = w.get("wm_class", "").lower()
            if cls:
                saved_by_class[cls].append(w)
        
        print("1️⃣ Проверка лишних окон...")
        closed = close_excess_windows(target_counts, self.terminal_pid)
        if closed:
            print(f"   Закрыто: {closed}")
            time.sleep(0.3)
        else:
            print("   Лишних нет")
        
        print("\n2��⃣ Проверка недостающих...")
        
        current = get_windows_by_class()
        opened = 0
        
        for cls, saved_list in saved_by_class.items():
            current_count = len(current.get(cls, []))
            need = len(saved_list)
            
            if current_count >= need:
                print(f"   {cls}: ✓ ({current_count}/{need})")
                continue
            
            to_open = need - current_count
            print(f"   {cls}: открываем {to_open}")
            
            for i in range(to_open):
                idx = current_count + i
                if idx >= len(saved_list):
                    break
                
                w = saved_list[idx]
                cmd = w.get("launch_cmd", "")
                project = w.get("project", "")
                ws = w.get("workspace", 1)
                
                if not cmd:
                    continue
                
                if w.get("is_multi_instance") and project and os.path.isdir(project):
                    cmd = f"{cmd} {project}"
                
                dispatch(f"exec [workspace {ws} silent] {cmd}")
                opened += 1
        
        if opened:
            print(f"\n⏳ Ожидание {opened} окон...")
            time.sleep(2.5)
        
        print("\n3️⃣ Повторная проверка...")
        closed = close_excess_windows(target_counts, self.terminal_pid)
        if closed:
            print(f"   Закрыто ещё: {closed}")
            time.sleep(0.3)
        else:
            print("   Всё в норме")
        
        print("\n4️⃣ Применение свойств...")
        self._apply_properties(saved_windows)
        dispatch(f"workspace {active_ws}")
        
        elapsed = time.time() - start
        final = get_windows_by_class()
        final_count = sum(len(v) for v in final.values())
        
        print(f"\n{'═' * 40}")
        print(f"✅ Готово за {elapsed:.1f}с ({final_count}/{len(saved_windows)} окон)")
        
        subprocess.run(
            ["notify-send", "-t", "2000", "Session", f"{final_count}/{len(saved_windows)}"],
            capture_output=True
        )
    
    def _apply_properties(self, saved_windows: list[dict]):
        current = get_windows_by_class()
        
        saved_by_class: dict[str, list[dict]] = defaultdict(list)
        for w in saved_windows:
            cls = w.get("wm_class", "").lower()
            if cls:
                saved_by_class[cls].append(w)
        
        commands = []
        
        for cls, saved_list in saved_by_class.items():
            current_list = current.get(cls, [])
            
            for i, saved in enumerate(saved_list):
                if i >= len(current_list):
                    break
                
                addr = current_list[i].get("address", "")
                if not addr:
                    continue
                
                ws = saved.get("workspace", 1)
                commands.append(f"movetoworkspacesilent {ws},address:{addr}")
                
                if saved.get("floating"):
                    commands.append(f"setfloating address:{addr}")
                    pos = saved.get("position", [0, 0])
                    size = saved.get("size", [800, 600])
                    commands.append(f"movewindowpixel exact {pos[0]} {pos[1]},address:{addr}")
                    commands.append(f"resizewindowpixel exact {size[0]} {size[1]},address:{addr}")
                
                if fs := saved.get("fullscreen"):
                    commands.append(f"focuswindow address:{addr}")
                    commands.append(f"fullscreen {fs}")
        
        batch_dispatch(commands)
        print(f"   Применено: {len(commands)} команд")


def main():
    if len(sys.argv) < 2:
        print("save | restore")
        return
    
    manager = SessionManager()
    
    match sys.argv[1]:
        case "save":
            manager.save()
        case "restore":
            manager.restore()
        case _:
            print("save | restore")


if __name__ == "__main__":
    main()