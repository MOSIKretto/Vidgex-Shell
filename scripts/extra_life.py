#!/usr/bin/env python3
"""
Hyprland Session Manager v4.3
С отладкой для понимания проблемы
"""

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

SESSION_DIR = Path.home() / ".cache" / "hypr-session"
SESSION_FILE = SESSION_DIR / "session.json"
DEBUG = "--debug" in sys.argv


def debug(msg: str):
    if DEBUG:
        print(f"    [DEBUG] {msg}")


@dataclass
class ProcessInfo:
    pid: int
    cmdline: str = ""
    cwd: str = ""
    exe: str = ""
    cmdline_args: list = field(default_factory=list)
    
    @classmethod
    def from_pid(cls, pid: int) -> Optional["ProcessInfo"]:
        proc_path = Path(f"/proc/{pid}")
        if not proc_path.exists():
            return None
        
        info = cls(pid=pid)
        
        try:
            cmdline_raw = (proc_path / "cmdline").read_bytes()
            # Сохраняем как список аргументов
            info.cmdline_args = [arg for arg in cmdline_raw.decode(errors='ignore').split('\x00') if arg]
            info.cmdline = ' '.join(info.cmdline_args)
        except:
            pass
        
        try:
            info.cwd = os.readlink(proc_path / "cwd")
        except:
            info.cwd = str(Path.home())
        
        try:
            info.exe = os.readlink(proc_path / "exe")
        except:
            pass
        
        return info
    
    def extract_project_path(self) -> Optional[str]:
        """Извлекает путь к проекту из аргументов командной строки."""
        for arg in self.cmdline_args:
            # Пропускаем флаги
            if arg.startswith('-'):
                continue
            # Пропускаем исполняемые файлы
            if '/bin/' in arg or '/lib/' in arg or '/usr/' in arg:
                continue
            # Проверяем, является ли аргумент существующей директорией
            if os.path.isdir(arg):
                return arg
            # Или файлом
            if os.path.isfile(arg):
                return os.path.dirname(arg)
        return None


@dataclass
class DesktopEntry:
    exec_cmd: str = ""
    wm_class: str = ""
    categories: set = field(default_factory=set)
    is_terminal: bool = False
    
    @classmethod
    def find_by_class(cls, wm_class: str) -> Optional["DesktopEntry"]:
        dirs = [
            Path("/usr/share/applications"),
            Path("/usr/local/share/applications"),
            Path.home() / ".local/share/applications",
            Path("/var/lib/flatpak/exports/share/applications"),
            Path.home() / ".local/share/flatpak/exports/share/applications",
        ]
        
        wm_lower = wm_class.lower()
        
        for d in dirs:
            if not d.exists():
                continue
            for f in d.glob("*.desktop"):
                entry = cls._parse_desktop(f, wm_lower)
                if entry:
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
                case 'StartupWMClass':
                    entry.wm_class = val
                    if val.lower() == target_class:
                        match_found = True
                case 'Categories':
                    entry.categories = set(c.strip() for c in val.split(';') if c.strip())
                case 'Terminal':
                    entry.is_terminal = val.lower() == 'true'
        
        if not match_found and target_class not in path.stem.lower():
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


class SessionManager:
    def __init__(self):
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
    
    def _get_base_cmd(self, desktop: Optional[DesktopEntry], wm_class: str) -> str:
        """Получает базовую команду без аргументов."""
        if desktop and desktop.exec_cmd:
            # Берём только первое слово (саму команду)
            return desktop.exec_cmd.split()[0]
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
    
    def _get_project_path(self, proc: ProcessInfo) -> str:
        """Определяет путь к проекту для окна."""
        # Сначала пробуем извлечь из cmdline
        project = proc.extract_project_path()
        if project:
            return project
        
        # Если нет — используем cwd, но только если он значимый
        cwd = proc.cwd
        home = str(Path.home())
        
        if cwd and cwd != home and cwd != "/" and not cwd.startswith(("/tmp", "/run", "/usr")):
            return cwd
        
        return ""
    
    def save(self):
        print("💾 Сохранение сессии...\n")
        
        clients = hyprctl("clients") or []
        ws_info = hyprctl("activeworkspace") or {}
        
        # Собираем информацию
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
            project_path = self._get_project_path(proc)
            
            debug(f"{wm_class}: pid={pid}, cwd={proc.cwd}")
            debug(f"  cmdline: {proc.cmdline[:100]}...")
            debug(f"  project_path: {project_path}")
            
            windows_data.append({
                "pid": pid,
                "wm_class": wm_class,
                "wm_class_lower": wm_class.lower(),
                "title": client.get("title", ""),
                "workspace": ws_id,
                "floating": client.get("floating", False),
                "fullscreen": client.get("fullscreen", 0),
                "position": list(client.get("at", [0, 0])),
                "size": list(client.get("size", [800, 600])),
                "base_cmd": self._get_base_cmd(desktop, wm_class),
                "project_path": project_path,
                "is_terminal": desktop.is_terminal if desktop else self._detect_terminal(pid),
            })
        
        # Определяем multi-instance
        by_class: dict[str, list[dict]] = defaultdict(list)
        for w in windows_data:
            by_class[w["wm_class_lower"]].append(w)
        
        for cls, wins in by_class.items():
            # Multi-instance если больше одного окна с разными project_path или разными PID
            pids = {w["pid"] for w in wins}
            projects = {w["project_path"] for w in wins if w["project_path"]}
            
            debug(f"{cls}: pids={pids}, projects={projects}")
            
            # Multi-instance если:
            # - больше одного окна И
            # - (разные PID ИЛИ разные проекты ИЛИ есть хотя бы один проект)
            is_multi = len(wins) > 1 and (len(pids) > 1 or len(projects) > 1 or len(projects) >= 1)
            
            for w in wins:
                w["is_multi_instance"] = is_multi
        
        # Формируем результат
        windows = []
        for w in windows_data:
            del w["wm_class_lower"]
            windows.append(w)
            
            if w["is_terminal"]:
                icon, note = "💻", "terminal"
            elif w["is_multi_instance"]:
                project = w["project_path"] or "no-project"
                icon, note = "📑", f"multi → {project}"
            else:
                icon, note = "📦", "single"
            
            print(f"  {icon} {w['wm_class']} [WS{w['workspace']}] ({note})")
        
        session = {
            "version": "4.3",
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
        windows = session.get("windows", [])
        active_ws = session.get("active_workspace", 1)
        
        print(f"📅 {session.get('timestamp')} | {len(windows)} окон\n")
        
        if not windows:
            return
        
        start = time.time()
        
        # Существующие окна
        existing = hyprctl("clients") or []
        existing_by_class: dict[str, int] = defaultdict(int)
        for w in existing:
            cls = w.get("class", "").lower()
            if cls:
                existing_by_class[cls] += 1
        
        # Группируем сохранённые
        saved_by_class: dict[str, list[dict]] = defaultdict(list)
        for w in windows:
            cls = w.get("wm_class", "").lower()
            if cls:
                saved_by_class[cls].append(w)
        
        # Запускаем
        for cls, saved_wins in saved_by_class.items():
            existing_count = existing_by_class.get(cls, 0)
            is_multi = any(w.get("is_multi_instance", False) for w in saved_wins)
            
            if is_multi:
                # Запускаем каждое окно отдельно
                to_launch = len(saved_wins) - existing_count
                
                for i in range(to_launch):
                    idx = existing_count + i
                    if idx >= len(saved_wins):
                        break
                    
                    w = saved_wins[idx]
                    base_cmd = w.get("base_cmd", "")
                    project = w.get("project_path", "")
                    ws = w.get("workspace", 1)
                    
                    if not base_cmd:
                        continue
                    
                    # Формируем команду
                    if project:
                        cmd = f"{base_cmd} {project}"
                    else:
                        cmd = base_cmd
                    
                    print(f"  🚀 {cls} #{idx + 1} → WS{ws}")
                    if project:
                        print(f"      📂 {project}")
                    
                    dispatch(f"exec [workspace {ws} silent] {cmd}")
            else:
                # Один запуск
                if existing_count > 0:
                    print(f"  ✓ {cls}: уже запущен")
                    continue
                
                w = saved_wins[0]
                cmd = w.get("base_cmd", "")
                ws = w.get("workspace", 1)
                
                if not cmd:
                    continue
                
                print(f"  🚀 {cls} → WS{ws}")
                dispatch(f"exec [workspace {ws} silent] {cmd}")
        
        print(f"\n⏳ Ожидание окон...")
        time.sleep(2.5)
        
        self._apply_properties(windows)
        dispatch(f"workspace {active_ws}")
        
        elapsed = time.time() - start
        print(f"\n✅ Готово за {elapsed:.1f}с")
        
        subprocess.run(
            ["notify-send", "-t", "2000", "Session", f"Restored {len(windows)} windows"],
            capture_output=True
        )
    
    def _apply_properties(self, saved_windows: list[dict]):
        print("📍 Применение свойств...")
        
        current = hyprctl("clients") or []
        
        current_by_class: dict[str, list[dict]] = defaultdict(list)
        for w in current:
            cls = w.get("class", "").lower()
            if cls:
                current_by_class[cls].append(w)
        
        saved_by_class: dict[str, list[dict]] = defaultdict(list)
        for w in saved_windows:
            cls = w.get("wm_class", "").lower()
            if cls:
                saved_by_class[cls].append(w)
        
        commands = []
        matched = 0
        
        for cls, saved_list in saved_by_class.items():
            current_list = current_by_class.get(cls, [])
            
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
                
                matched += 1
        
        batch_dispatch(commands)
        print(f"  ✓ Применено к {matched} окнам")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    
    if not args:
        print("Использование: save | restore [--debug]")
        return
    
    manager = SessionManager()
    
    match args[0]:
        case "save":
            manager.save()
        case "restore":
            manager.restore()
        case _:
            print("Неизвестная команда")


if __name__ == "__main__":
    main()