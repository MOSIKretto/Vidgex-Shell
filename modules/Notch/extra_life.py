import json
import os
import re
import shlex
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, ClassVar

from fabric.utils import DesktopApp, get_desktop_applications, idle_add, remove_handler
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label
from fabric.widgets.entry import Entry
from fabric.widgets.scrolledwindow import ScrolledWindow
from fabric.widgets.stack import Stack
from fabric.widgets.image import Image

from gi.repository import GLib

import services.icons as icons
from services.list_navigation import ListNavigationMixin


SESSION_DIR = Path.home() / ".cache" / "vidgex-shell"
SESSION_FILE = SESSION_DIR / "session.json"
EXCLUSIONS_FILE = SESSION_DIR / "exclusions.json"


def normalize_str(s: str) -> str:
    return re.sub(r'[^a-z0-9]', '', s.lower())


@dataclass
class ProcessInfo:
    pid: int
    comm: str = ""
    exe: str = ""
    cmdline: str = ""
    cwd: str = ""
    cmdline_args: list = field(default_factory=list)
    
    @classmethod
    def from_pid(cls, pid: int) -> Optional["ProcessInfo"]:
        proc_path = Path(f"/proc/{pid}")
        if not proc_path.exists():
            return None
        info = cls(pid=pid)
        
        try: info.comm = (proc_path / "comm").read_text().strip()
        except: pass
        
        try: info.exe = os.readlink(proc_path / "exe")
        except: pass
        
        try:
            cmdline_raw = (proc_path / "cmdline").read_bytes()
            info.cmdline_args = [arg for arg in cmdline_raw.decode(errors='ignore').split('\x00') if arg]
            info.cmdline = ' '.join(info.cmdline_args)
        except: pass
        
        try: info.cwd = os.readlink(proc_path / "cwd")
        except: info.cwd = str(Path.home())
        
        return info


@dataclass
class DesktopEntry:
    exec_cmd: str = ""
    wm_class: str = ""
    categories: set = field(default_factory=set)
    is_terminal: bool = False
    is_flatpak: bool = False
    flatpak_id: str = ""
    
    _cache: ClassVar[dict[str, "DesktopEntry"]] = {}
    
    @classmethod
    def find_by_class(cls, wm_class: str, proc: ProcessInfo) -> Optional["DesktopEntry"]:
        wm_lower = wm_class.lower()
        if wm_lower in cls._cache: return cls._cache[wm_lower]
        
        wm_norm = normalize_str(wm_class)
        exe_name = Path(proc.exe).name if proc.exe else ""
            
        data_dirs = os.environ.get('XDG_DATA_DIRS', '/usr/share:/usr/local/share').split(':')
        data_dirs.append(str(Path.home() / ".local/share"))
        flatpak_dirs = ["/var/lib/flatpak/exports/share", str(Path.home() / ".local/share/flatpak/exports/share")]
        all_dirs = [Path(d) / "applications" for d in data_dirs + flatpak_dirs]
        
        for d in all_dirs:
            if not d.exists(): continue
            for f in d.glob("*.desktop"):
                entry = cls._parse_desktop(f, wm_norm, exe_name, proc.comm)
                if entry:
                    if "flatpak" in str(f):
                        entry.is_flatpak = True
                        if not entry.flatpak_id:
                            entry.flatpak_id = f.stem
                    cls._cache[wm_lower] = entry
                    return entry
        cls._cache[wm_lower] = None
        return None
    
    @classmethod
    def _parse_desktop(cls, path: Path, target_norm: str, exe_name: str, comm: str) -> Optional["DesktopEntry"]:
        try: text = path.read_text(errors='ignore')
        except: return None
        entry = cls()
        in_entry = match_found = False
        
        for line in text.splitlines():
            line = line.strip()
            if line.startswith('['):
                in_entry = (line == '[Desktop Entry]')
                continue
            if not in_entry or '=' not in line: continue
            key, _, val = line.partition('=')
            key, val = key.strip(), val.strip()
            
            match key:
                case 'Name':
                    if normalize_str(val) == target_norm: match_found = True
                case 'Exec':
                    entry.exec_cmd = re.sub(r'%[a-zA-Z]', '', val).strip()
                    if 'flatpak run' in val: entry.is_flatpak = True
                    val_lower = val.lower()
                    if (exe_name and exe_name in val_lower) or (comm and comm in val_lower):
                        match_found = True
                case 'StartupWMClass':
                    entry.wm_class = val
                    if normalize_str(val) == target_norm: match_found = True
                case 'Categories': 
                    entry.categories = set(c.strip() for c in val.split(';') if c.strip())
                case 'Terminal': 
                    entry.is_terminal = val.lower() == 'true'
                case 'X-Flatpak':
                    entry.is_flatpak = True
                    entry.flatpak_id = val
                    
        if not match_found:
            stem_norm = normalize_str(path.stem)
            if target_norm in stem_norm or stem_norm in target_norm: 
                match_found = True
            
        if not match_found:
            return None
            
        if 'TerminalEmulator' in entry.categories: entry.is_terminal = True
        return entry


def hyprctl(cmd: str) -> Optional[dict | list]:
    try:
        args = shlex.split(cmd)
        r = subprocess.run(["hyprctl", "-j"] + args, capture_output=True, text=True, timeout=2)
        return json.loads(r.stdout) if r.returncode == 0 else None
    except: return None

def dispatch(cmd: str): subprocess.run(["hyprctl", "dispatch", "--", cmd], capture_output=True, timeout=2)
def batch_dispatch(commands: list[str]):
    if commands: subprocess.run(["hyprctl", "--batch", ";".join(f"dispatch {c}" for c in commands)], capture_output=True, timeout=5)

def is_user_path(path: str) -> bool:
    home = str(Path.home())
    if not path.startswith(home): return False
    relative = path[len(home):]
    excluded = ('/.cache/', '/.local/share/', '/.config/')
    return not any(ex in relative for ex in excluded)

def get_current_terminal_pid() -> Optional[int]:
    try:
        stat = Path(f"/proc/{os.getppid()}/stat").read_text()
        pid = int(stat.split()[3])
        return None if pid <= 1 else pid
    except: return None

def get_windows_by_class() -> dict[str, list[dict]]:
    clients = hyprctl("clients") or []
    by_class: dict[str, list[dict]] = defaultdict(list)
    for w in clients:
        cls = w.get("class", "").lower()
        if cls: by_class[cls].append(w)
    return dict(by_class)

def close_excess_windows(target_counts: dict[str, int], is_excluded_fn, protect_pid: Optional[int] = None) -> int:
    current = get_windows_by_class()
    closed = 0
    for cls, wins in current.items():
        if is_excluded_fn(cls): 
            continue
            
        excess = len(wins) - target_counts.get(cls, 0)
        if excess > 0:
            for win in wins[-excess:]:
                pid = win.get("pid", 0)
                if pid == protect_pid or pid <= 1: continue
                if addr := win.get("address", ""):
                    dispatch(f"closewindow address:{addr}")
                    closed += 1
    return closed


class SessionManager:
    def __init__(self):
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        self.terminal_pid = get_current_terminal_pid()
        self._autosave_id: Optional[int] = None
        self.exclusions: list[str] = self._load_exclusions()
    
    def _load_exclusions(self) -> list[str]:
        if EXCLUSIONS_FILE.exists():
            try: return json.loads(EXCLUSIONS_FILE.read_text())
            except: pass
        return ["hyprland-share-picker", "xdg-desktop-portal-gtk"]
        
    def sync_exclusions(self):
        self.exclusions = self._load_exclusions()
        
    def save_exclusions(self):
        EXCLUSIONS_FILE.write_text(json.dumps(self.exclusions, indent=2))
        
    def add_exclusion(self, wm_class: str):
        self.sync_exclusions()
        cls_lower = wm_class.lower().strip()
        if cls_lower and cls_lower not in self.exclusions:
            self.exclusions.append(cls_lower)
            self.save_exclusions()
            
    def remove_exclusion(self, wm_class: str):
        self.sync_exclusions()
        cls_lower = wm_class.lower().strip()
        if cls_lower in self.exclusions:
            self.exclusions.remove(cls_lower)
            self.save_exclusions()

    def is_excluded(self, wm_class: str) -> bool:
        if not wm_class: return True
        cls_lower = wm_class.lower().strip()
        
        if cls_lower in self.exclusions:
            return True
            
        c_clean = normalize_str(cls_lower)
        for ex in self.exclusions:
            e_clean = normalize_str(ex)
            if e_clean and c_clean and (e_clean in c_clean or c_clean in e_clean):
                return True
        return False

    def start_autosave(self, interval_seconds: int = 60):
        self._autosave_id = GLib.timeout_add_seconds(interval_seconds, self._autosave_tick)

    def stop_autosave(self):
        if self._autosave_id:
            GLib.source_remove(self._autosave_id)
            self._autosave_id = None

    def _autosave_tick(self) -> bool:
        self.save()
        return True
    
    def _get_launch_cmd(self, desktop: Optional[DesktopEntry], wm_class: str, proc: ProcessInfo) -> str:
        if desktop:
            if desktop.is_flatpak and desktop.flatpak_id: return f"flatpak run {desktop.flatpak_id}"
            if desktop.exec_cmd: return desktop.exec_cmd
            
        if proc.cmdline_args:
            first_arg = proc.cmdline_args[0]
            if Path(first_arg).exists() and not first_arg.endswith(".so"):
                return first_arg
                
        if proc.comm:
            return proc.comm
            
        return wm_class.lower().replace(" ", "-")
    
    def _detect_terminal(self, pid: int) -> bool:
        try:
            for fd in Path(f"/proc/{pid}/fd").iterdir():
                if "/dev/pts/" in os.readlink(fd): return True
        except: pass
        return False
    
    def _get_project(self, proc: ProcessInfo) -> str:
        for arg in proc.cmdline_args[1:]:
            if arg.startswith('-'): continue
            if (path := Path(arg)).exists() and path.is_dir() and is_user_path(str(path)): return str(path)
        return proc.cwd if is_user_path(proc.cwd) else ""

    def save(self):
        self.sync_exclusions()
        try:
            clients = hyprctl("clients") or []
            ws_info = hyprctl("activeworkspace") or {}
            windows_data = []
            
            for client in clients:
                wm_class = client.get("class", "")
                pid = client.get("pid", 0)
                
                ws_data = client.get("workspace", {})
                ws_id = ws_data.get("id", 1) if isinstance(ws_data, dict) else 1
                
                if ws_id < 0 or pid <= 0 or self.is_excluded(wm_class):
                    continue
                
                proc = ProcessInfo.from_pid(pid)
                if not proc: 
                    continue
                    
                desktop = DesktopEntry.find_by_class(wm_class, proc)
                
                windows_data.append({
                    "wm_class": wm_class,
                    "wm_class_lower": wm_class.lower(),
                    "workspace": ws_id,
                    "floating": client.get("floating", False),
                    "fullscreen": client.get("fullscreen", 0),
                    "position": list(client.get("at", [0, 0])),
                    "size": list(client.get("size", [800, 600])),
                    "launch_cmd": self._get_launch_cmd(desktop, wm_class, proc),
                    "project": self._get_project(proc),
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
                w.pop("wm_class_lower", None)
                windows.append(w)
            
            active_ws_id = ws_info.get("id", 1) if isinstance(ws_info, dict) else 1
            
            session = {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "active_workspace": active_ws_id,
                "windows": windows
            }
            
            SESSION_FILE.write_text(json.dumps(session, indent=2))
            
        except Exception as e:
            import traceback
            traceback.print_exc()
    
    def restore(self):
        self.sync_exclusions()
        if not SESSION_FILE.exists(): return
        try: session = json.loads(SESSION_FILE.read_text())
        except: return
        
        saved_windows = session.get("windows", [])
        active_ws = session.get("active_workspace", 1)
        if not saved_windows: return
        
        target_counts: dict[str, int] = defaultdict(int)
        saved_by_class: dict[str, list[dict]] = defaultdict(list)
        
        for w in saved_windows:
            cls = w.get("wm_class", "").lower()
            if cls and not self.is_excluded(cls):
                target_counts[cls] += 1
                saved_by_class[cls].append(w)
        
        if close_excess_windows(target_counts, self.is_excluded, self.terminal_pid): time.sleep(0.3)
        
        current = get_windows_by_class()
        opened = 0
        expected_total = sum(len(wins) for wins in saved_by_class.values())
        
        for cls, saved_list in saved_by_class.items():
            current_count = len(current.get(cls, []))
            need = len(saved_list)
            if current_count >= need: continue
            
            for i in range(need - current_count):
                idx = current_count + i
                if idx >= len(saved_list): break
                
                w = saved_list[idx]
                cmd = w.get("launch_cmd", "")
                project = w.get("project", "")
                
                if not cmd: continue
                if w.get("is_multi_instance") and project and os.path.isdir(project):
                    cmd = f"{cmd} {shlex.quote(project)}"
                
                dispatch(f"exec [workspace {w.get('workspace', 1)} silent] {cmd}")
                opened += 1
        
        if opened:
            timeout = 20
            while timeout > 0:
                if sum(len(wins) for wins in get_windows_by_class().values()) >= expected_total: break
                time.sleep(0.5)
                timeout -= 1
        
        if close_excess_windows(target_counts, self.is_excluded, self.terminal_pid): time.sleep(0.3)
        self._apply_properties(saved_windows)
        dispatch(f"workspace {active_ws}")
    
    def _apply_properties(self, saved_windows: list[dict]):
        current = get_windows_by_class()
        saved_by_class: dict[str, list[dict]] = defaultdict(list)
        for w in saved_windows:
            if (cls := w.get("wm_class", "").lower()): saved_by_class[cls].append(w)
        
        commands = []
        for cls, saved_list in saved_by_class.items():
            current_list = current.get(cls, [])
            for i, saved in enumerate(saved_list):
                if i >= len(current_list) or not (addr := current_list[i].get("address", "")): continue
                commands.append(f"movetoworkspacesilent {saved.get('workspace', 1)},address:{addr}")
                if saved.get("floating"):
                    commands.append(f"setfloating address:{addr}")
                    pos, size = saved.get("position", [0, 0]), saved.get("size", [800, 600])
                    commands.append(f"movewindowpixel exact {pos[0]} {pos[1]},address:{addr}")
                    commands.append(f"resizewindowpixel exact {size[0]} {size[1]},address:{addr}")
                if fs := saved.get("fullscreen"):
                    commands.append(f"focuswindow address:{addr}")
                    commands.append(f"fullscreen {fs}")
        batch_dispatch(commands)


class SessionManagerUI(ListNavigationMixin, Box):
    __slots__ = ('notch', 'manager', 'vp_ignored', 'vp_picker', 'sw_ignored', 
                 'sw_picker', 'stack', 'ent', 'sel', '_hnd', '_apps', 'vp', 'sw', 'page_ignored', 'page_picker')

    def __init__(self, notch=None, **kw):
        super().__init__(name="app-launcher", visible=False, all_visible=False, **kw)
        self.notch = notch
        self.manager = SessionManager()
        self.sel, self._hnd = -1, 0
        
        self._apps = get_desktop_applications()
        
        self.vp_picker = None
        self.vp_ignored = None

        btn_save = Button(name="clear-button", tooltip_markup="<b>Save Session</b>", child=Label(name="clear-label", markup=icons.download), on_clicked=self._on_save)
        btn_add = Button(name="session-add-btn", label="Add Application", h_expand=True, on_clicked=lambda *_: self.stack.set_visible_child_name("picker"))
        btn_load = Button(name="close-button", tooltip_markup="<b>Restore Session</b>", child=Label(name="close-label", markup=icons.upload), on_clicked=self._on_load)
        
        header_ignored = Box(name="header_box", spacing=10, orientation="h", h_expand=True, children=[btn_save, btn_add, btn_load])
        self.vp_ignored = Box(name="viewport", spacing=4, orientation="v")
        
        self.sw_ignored = ScrolledWindow(
            name="scrolled-window", spacing=10, h_expand=True, v_expand=True, 
            h_align="fill", v_align="fill", child=self.vp_ignored, 
            propagate_width=False, propagate_height=False,
            can_focus=True, on_key_press_event=self._nav_key 
        )
        
        self.page_ignored = Box(spacing=10, h_expand=True, v_expand=True, h_align="fill", v_align="fill", orientation="v", children=[header_ignored, self.sw_ignored])

        self.ent = Entry(
            name="search-entry", placeholder="Search Applications...", h_expand=True,
            notify_text=lambda e, *_: self._arr(e.get_text()), 
            on_activate=lambda *_: self._handle_enter(), 
            on_key_press_event=self._nav_key
        )
        self.ent.props.xalign = 0.5
        
        btn_cancel = Button(name="close-button", tooltip_markup="<b>Cancel</b>", child=Label(name="close-label", markup=icons.cancel), on_clicked=lambda *_: self.stack.set_visible_child_name("ignored"))
        
        header_picker = Box(name="header_box", spacing=10, orientation="h", h_expand=True, children=[self.ent, btn_cancel])
        self.vp_picker = Box(name="viewport", spacing=4, orientation="v")
        
        self.sw_picker = ScrolledWindow(
            name="scrolled-window", spacing=10, h_expand=True, v_expand=True, 
            h_align="fill", v_align="fill", child=self.vp_picker, 
            propagate_width=False, propagate_height=False
        )
        
        self.page_picker = Box(spacing=10, h_expand=True, v_expand=True, h_align="fill", v_align="fill", orientation="v", children=[header_picker, self.sw_picker])

        self.stack = Stack(name="stack", transition_type="slide-left-right", v_expand=True, h_expand=True)
        self.stack.set_homogeneous(False)
        self.stack.add_named(self.page_ignored, "ignored")
        self.stack.add_named(self.page_picker, "picker")

        self.stack.connect("notify::visible-child", self._on_vis)

        self.add(Box(
            name="launcher-box", spacing=10, h_expand=True, v_expand=True,
            h_align="fill", v_align="fill", orientation="v", children=[self.stack]
        ))
        
        self.vp = self.vp_ignored
        self.sw = self.sw_ignored
        
        self.show_all()
        self.refresh_ignored(target_sel=0)

    def _on_vis(self, stack, _):
        if self._hnd: remove_handler(self._hnd); self._hnd = 0
        self._nav_clear()
        
        if stack.get_visible_child() is self.page_picker:
            self.vp = self.vp_picker
            self.sw = self.sw_picker 
            self.ent.set_text("")
            self._arr()
            GLib.idle_add(lambda: self.ent.grab_focus() or False)
        else:
            self.vp = self.vp_ignored
            self.sw = self.sw_ignored
            self.refresh_ignored(target_sel=0)
            GLib.idle_add(lambda: self.sw_ignored.grab_focus() or False)

    def _get_app_class(self, a: DesktopApp) -> str:
        if hasattr(a, 'app_info'):
            try:
                wm = a.app_info.get_startup_wm_class()
                if wm: return wm.lower()
            except: pass
            try:
                app_id = a.app_info.get_id()
                if app_id: return app_id.replace('.desktop', '').lower()
            except: pass

        for attr in ['get_startup_wm_class', 'get_id', 'id', 'app_id']:
            val = getattr(a, attr, None)
            if callable(val):
                try: val = val()
                except: continue
            if isinstance(val, str) and val:
                return val.replace('.desktop', '').lower()

        wrappers = {'env', 'flatpak', 'sh', 'bash', 'dbus-run-session', 'prime-run'}
        for attr in ['get_commandline', 'command_line', 'get_executable', 'executable']:
            val = getattr(a, attr, None)
            if callable(val):
                try: val = val()
                except: continue
            if isinstance(val, str) and val.strip():
                parts = val.split()
                for part in parts:
                    clean_part = Path(part).name.lower()
                    if clean_part not in wrappers and '=' not in part and not part.startswith('-'):
                        return clean_part

        name = getattr(a, 'name', '') or getattr(a, 'display_name', '') or 'unknown'
        return str(name).lower()

    def _arr(self, q=""):
        if self.vp_picker is None:
            return
        if self._hnd: remove_handler(self._hnd)
        self.vp_picker.children, self.sel, qf = [], -1, q.casefold()
        
        if self._apps:
            apps = sorted((a for a in self._apps if not qf or any(qf in (s or "").casefold() 
                           for s in (a.display_name, a.name, a.generic_name, a.command_line))),
                          key=lambda a: (a.display_name or "").casefold())
            
            apps = [a for a in apps if not self.manager.is_excluded(self._get_app_class(a))]
            
            self._hnd = idle_add(lambda it: self._add_picker(it), iter(apps), pin=True)
            if apps: GLib.idle_add(lambda: self._nav_usel(0) or False)

    def _add_picker(self, it):
        if not (app := next(it, None)): return False
        self.vp_picker.add(self._mk_picker(app))
        self.vp_picker.show_all()
        return True

    def _mk_picker(self, a: DesktopApp) -> Button:
        return Button(
            name="slot-button", tooltip_text=a.description, 
            on_clicked=lambda *_: self._on_add(a),
            child=Box(
                name="slot-box", orientation="h", spacing=10, 
                children=[
                    Image(name="app-icon", pixbuf=a.get_icon_pixbuf(size=24), h_align="start"),
                    Label(name="app-label", label=a.display_name or "Unknown", ellipsization="end", v_align="center", h_align="center"),
                    Label(name="app-desc", label=a.description or "", ellipsization="end", v_align="center", h_align="start", h_expand=True)
                ]
            )
        )

    def refresh_ignored(self, target_sel: int = 0):
        if self._hnd: remove_handler(self._hnd); self._hnd = 0
        self._nav_clear()
        self.vp_ignored.children, self.sel = [], -1
        
        self.manager.sync_exclusions() 
        
        if not self.manager.exclusions:
            self._empty(self.vp_ignored, icons.close)
        else:
            max_idx = len(self.manager.exclusions) - 1
            if target_sel > max_idx: target_sel = max_idx
            if target_sel < 0: target_sel = 0

            self._hnd = idle_add(lambda it: self._add_ignored(it, target_sel), iter(self.manager.exclusions), pin=True)

    def _add_ignored(self, it, target_sel: int):
        if not (app_name := next(it, None)): 
            GLib.idle_add(lambda: self._nav_usel(target_sel) or False)
            return False
            
        self.vp_ignored.add(self._mk_ignored(app_name))
        self.vp_ignored.show_all()
        return True

    def _mk_ignored(self, app_name: str) -> Button:
        found_app = None
        c_clean = normalize_str(app_name)
        if self._apps:
            for a in self._apps:
                app_cls = normalize_str(self._get_app_class(a))
                if c_clean in app_cls or app_cls in c_clean:
                    found_app = a
                    break

        if found_app:
            icon_w = Image(name="app-icon", pixbuf=found_app.get_icon_pixbuf(size=24), h_align="start")
            title = found_app.display_name or app_name
            desc = found_app.description or "Click to remove from exclusions"
        else:
            icon_w = Image(name="app-icon", icon_name="application-x-executable", pixel_size=24, h_align="start")
            title = app_name
            desc = "Manual exclusion (Click to remove)"

        return Button(
            name="slot-button", tooltip_text="Remove from exclusions", 
            on_clicked=lambda *_: self._on_remove(app_name),
            child=Box(
                name="slot-box", orientation="h", spacing=10,
                children=[
                    icon_w,
                    Label(name="app-label", label=title, ellipsization="end", v_align="center", h_align="center"),
                    Label(name="app-desc", label=desc, ellipsization="end", v_align="center", h_align="start", h_expand=True),
                    Label(name="clip-icon", markup=icons.trash, v_align="center", h_align="end")
                ]
            )
        )

    def _empty(self, target_box, icon):
        target_box.children = []
        target_box.add(Box(
            name="no-clip-container", v_expand=True, h_expand=True, orientation="v",
            children=[Label(name="no-clip", markup=icon, v_align="center", h_align="center", v_expand=True, h_expand=True)]
        ))
        target_box.show_all()

    def _handle_enter(self, *args):
        if self.sel >= 0 and self.vp.children:
            self._nav_activate()
        elif self.vp.children:
            self._nav_usel(0)
            self._nav_activate()
        elif text := self.ent.get_text().strip():
            self._on_add(text.lower())

    def _on_add(self, a):
        app_name = a if isinstance(a, str) else self._get_app_class(a)
        if app_name:
            self.manager.add_exclusion(app_name)
        self.stack.set_visible_child_name("ignored") 

    def _on_remove(self, app_name: str):
        idx = -1
        if app_name in self.manager.exclusions:
            idx = self.manager.exclusions.index(app_name)
            
        self.manager.remove_exclusion(app_name)
        target_sel = idx - 1 if idx > 0 else 0
        
        self.refresh_ignored(target_sel=target_sel)

    def _on_save(self, *args):
        self.manager.save()
        if self.notch: self.notch.close_notch()

    def _on_load(self, *args):
        if self.notch: self.notch.close_notch()
        import threading
        threading.Thread(target=self.manager.restore, daemon=True).start()