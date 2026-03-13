import json
import os
import shlex
import time
import traceback
from collections import defaultdict
from pathlib import Path
from typing import Optional

from gi.repository import GLib

from .sessionUtils import (
    SESSION_DIR, SESSION_FILE, EXCLUSIONS_FILE,
    normalize_str, hyprctl, dispatch, batch_dispatch,
    is_user_path, get_current_terminal_pid,
    get_windows_by_class, close_excess_windows,
)
from .sessionProcess import ProcessInfo, DesktopEntry


class SessionManager:
    def __init__(self):
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        self.terminal_pid = get_current_terminal_pid()
        self._autosave_id: Optional[int] = None
        self.exclusions: list[str] = self._load_exclusions()

    def _load_exclusions(self) -> list[str]:
        if EXCLUSIONS_FILE.exists():
            try:
                return json.loads(EXCLUSIONS_FILE.read_text())
            except:
                pass
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
        if not wm_class:
            return True
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
        self._autosave_id = GLib.timeout_add_seconds(
            interval_seconds, self._autosave_tick
        )

    def stop_autosave(self):
        if self._autosave_id:
            GLib.source_remove(self._autosave_id)
            self._autosave_id = None

    def _autosave_tick(self) -> bool:
        self.save()
        return True

    def _get_launch_cmd(
        self, desktop: Optional[DesktopEntry], wm_class: str, proc: ProcessInfo
    ) -> str:
        if desktop:
            if desktop.is_flatpak and desktop.flatpak_id:
                return f"flatpak run {desktop.flatpak_id}"
            if desktop.exec_cmd:
                return desktop.exec_cmd

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
                if "/dev/pts/" in os.readlink(fd):
                    return True
        except:
            pass
        return False

    def _get_project(self, proc: ProcessInfo) -> str:
        for arg in proc.cmdline_args[1:]:
            if arg.startswith('-'):
                continue
            if (path := Path(arg)).exists() and path.is_dir() and \
               is_user_path(str(path)):
                return str(path)
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
                    "is_terminal": (
                        desktop.is_terminal if desktop
                        else self._detect_terminal(pid)
                    ),
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

            active_ws_id = (
                ws_info.get("id", 1) if isinstance(ws_info, dict) else 1
            )

            session = {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "active_workspace": active_ws_id,
                "windows": windows,
            }

            SESSION_FILE.write_text(json.dumps(session, indent=2))

        except Exception:
            traceback.print_exc()

    def restore(self):
        self.sync_exclusions()
        if not SESSION_FILE.exists():
            return
        try:
            session = json.loads(SESSION_FILE.read_text())
        except:
            return

        saved_windows = session.get("windows", [])
        active_ws = session.get("active_workspace", 1)
        if not saved_windows:
            return

        target_counts: dict[str, int] = defaultdict(int)
        saved_by_class: dict[str, list[dict]] = defaultdict(list)

        for w in saved_windows:
            cls = w.get("wm_class", "").lower()
            if cls and not self.is_excluded(cls):
                target_counts[cls] += 1
                saved_by_class[cls].append(w)

        if close_excess_windows(
            target_counts, self.is_excluded, self.terminal_pid
        ):
            time.sleep(0.3)

        current = get_windows_by_class()
        opened = 0
        expected_total = sum(len(wins) for wins in saved_by_class.values())

        for cls, saved_list in saved_by_class.items():
            current_count = len(current.get(cls, []))
            need = len(saved_list)
            if current_count >= need:
                continue

            for i in range(need - current_count):
                idx = current_count + i
                if idx >= len(saved_list):
                    break

                w = saved_list[idx]
                cmd = w.get("launch_cmd", "")
                project = w.get("project", "")

                if not cmd:
                    continue
                if w.get("is_multi_instance") and project and \
                   os.path.isdir(project):
                    cmd = f"{cmd} {shlex.quote(project)}"

                dispatch(
                    f"exec [workspace {w.get('workspace', 1)} silent] {cmd}"
                )
                opened += 1

        if opened:
            timeout = 20
            while timeout > 0:
                total_now = sum(
                    len(wins) for wins in get_windows_by_class().values()
                )
                if total_now >= expected_total:
                    break
                time.sleep(0.5)
                timeout -= 1

        if close_excess_windows(
            target_counts, self.is_excluded, self.terminal_pid
        ):
            time.sleep(0.3)

        self._apply_properties(saved_windows)
        dispatch(f"workspace {active_ws}")

    def _apply_properties(self, saved_windows: list[dict]):
        current = get_windows_by_class()
        saved_by_class: dict[str, list[dict]] = defaultdict(list)
        for w in saved_windows:
            if cls := w.get("wm_class", "").lower():
                saved_by_class[cls].append(w)

        commands = []
        for cls, saved_list in saved_by_class.items():
            current_list = current.get(cls, [])
            for i, saved in enumerate(saved_list):
                if i >= len(current_list):
                    continue
                addr = current_list[i].get("address", "")
                if not addr:
                    continue
                commands.append(
                    f"movetoworkspacesilent "
                    f"{saved.get('workspace', 1)},address:{addr}"
                )
                if saved.get("floating"):
                    commands.append(f"setfloating address:{addr}")
                    pos = saved.get("position", [0, 0])
                    size = saved.get("size", [800, 600])
                    commands.append(
                        f"movewindowpixel exact "
                        f"{pos[0]} {pos[1]},address:{addr}"
                    )
                    commands.append(
                        f"resizewindowpixel exact "
                        f"{size[0]} {size[1]},address:{addr}"
                    )
                if fs := saved.get("fullscreen"):
                    commands.append(f"focuswindow address:{addr}")
                    commands.append(f"fullscreen {fs}")

        batch_dispatch(commands)