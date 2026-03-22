from __future__ import annotations

import json
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from gi.repository import GLib, Gio
from fabric.hyprland.widgets import get_hyprland_connection
from fabric.utils import DesktopApp, get_desktop_applications
from fabric.utils.helpers import exec_shell_command_async


def _hypr_json(cmd: str):
    try:
        conn = get_hyprland_connection()
        raw = conn.send_command(f"j/{cmd}").reply.decode()
        return json.loads(raw)
    except Exception:
        return None

def _hypr_dispatch(cmd: str):
    try:
        get_hyprland_connection().send_command(
            f"dispatch {cmd}")
    except Exception:
        pass

def _hypr_batch(cmds: list[str]):
    if not cmds:
        return
    try:
        get_hyprland_connection().send_command(
            "[[BATCH]]" +
            ";".join(f"dispatch {c}" for c in cmds))
    except Exception:
        pass


@dataclass
class _ProcInfo:
    pid: int
    cmdline: str = ""
    cwd: str = ""
    args: list = field(default_factory=list)

    @classmethod
    def read(cls, pid: int) -> Optional[_ProcInfo]:
        p = Path(f"/proc/{pid}")
        if not p.exists():
            return None
        info = cls(pid=pid)
        try:
            raw = (p / "cmdline").read_bytes()
            info.args = [
                a for a in raw.decode(errors="ignore")
                    .split("\x00") if a
            ]
            info.cmdline = " ".join(info.args)
        except Exception:
            pass
        try:
            info.cwd = os.readlink(p / "cwd")
        except Exception:
            info.cwd = GLib.get_home_dir()
        return info

    @property
    def uses_pty(self) -> bool:
        try:
            for fd in Path(f"/proc/{self.pid}/fd").iterdir():
                try:
                    if "/dev/pts/" in os.readlink(fd):
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    @property
    def ppid(self) -> int:
        try:
            stat = Path(f"/proc/{self.pid}/stat").read_text()
            idx = stat.rfind(")")
            if idx < 0:
                return 0
            return int(stat[idx + 2:].split()[1])
        except Exception:
            return 0

    def dir_args(self) -> list[str]:
        result = []
        for arg in self.args[1:]:
            if arg.startswith("-"):
                continue
            expanded = os.path.expanduser(arg)
            if os.path.isdir(expanded) \
                    and _is_project_dir(expanded):
                result.append(expanded)
        return result

def _is_project_dir(path: str) -> bool:
    home = GLib.get_home_dir()
    if not path.startswith(home):
        return False
    if os.path.realpath(path) == os.path.realpath(home):
        return False
    skip = [
        GLib.get_user_cache_dir(),
        GLib.get_user_data_dir(),
        GLib.get_user_config_dir(),
    ]
    try:
        skip.append(GLib.get_user_state_dir())
    except AttributeError:
        skip.append(os.path.join(home, ".local", "state"))
    return not any(path.startswith(d) for d in skip)

def _build_dir_index(home: str) -> dict[str, str]:
    index: dict[str, str] = {}
    try:
        for entry in os.scandir(home):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            index[entry.name.lower()] = entry.path
            try:
                for sub in os.scandir(entry.path):
                    if sub.is_dir() \
                            and not sub.name.startswith("."):
                        lo = sub.name.lower()
                        if lo not in index:
                            index[lo] = sub.path
            except PermissionError:
                pass
    except Exception:
        pass
    return index

def _get_gio(app: DesktopApp) -> Optional[Gio.DesktopAppInfo]:
    for attr in ("_app", "app_info", "desktop_app_info"):
        obj = getattr(app, attr, None)
        if isinstance(obj, (Gio.DesktopAppInfo, Gio.AppInfo)):
            return obj
    return None

def _gio_wm_class(app: DesktopApp) -> str:
    gio = _get_gio(app)
    if gio and hasattr(gio, "get_startup_wm_class"):
        return (gio.get_startup_wm_class() or "").lower()
    return (getattr(app, "window_class", "") or "").lower()

def _gio_is_terminal(app: DesktopApp) -> Optional[bool]:
    gio = _get_gio(app)
    if not gio:
        return None
    if hasattr(gio, "get_categories"):
        cats = gio.get_categories() or ""
        if "TerminalEmulator" in cats:
            return True
    if hasattr(gio, "get_boolean"):
        try:
            return gio.get_boolean("Terminal")
        except Exception:
            pass
    return None


class AppResolver:
    _CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

    def __init__(self, app_resolver=None):
        self._icons = app_resolver
        self._cache: dict[tuple, Optional[DesktopApp]] = {}

    def invalidate_cache(self):
        self._cache.clear()

    def find(self, *identifiers: str) -> Optional[DesktopApp]:
        ids = tuple(s.lower() for s in identifiers if s)
        if not ids:
            return None
        key = tuple(sorted(set(ids)))
        if key in self._cache:
            return self._cache[key]
        result = self._do_find(ids)
        self._cache[key] = result
        return result

    def launch(self, app=None, key="",
               original_class="") -> bool:
        if app:
            try:
                app.launch()
                return True
            except Exception:
                pass
        found = self.find(key, original_class)
        if found:
            try:
                found.launch()
                return True
            except Exception:
                pass
        for binary in self._binary_candidates(
                key, original_class):
            if GLib.find_program_in_path(binary):
                exec_shell_command_async(binary)
                return True
        return False

    @staticmethod
    def get_command(app: DesktopApp) -> str:
        cmd = getattr(app, "command_line", "") or ""
        return re.sub(r"%[a-zA-Z]", "", cmd).strip()

    def _do_find(self, ids):
        r = self._icon_lookup(*ids)
        if r:
            return r
        all_apps = get_desktop_applications()
        idx = self._build_index(all_apps)
        r = self._gio_find(ids, idx)
        if r:
            return r
        return self._attrs_match(ids, all_apps)

    def _icon_lookup(self, *names):
        if not self._icons:
            return None
        amap = self._icons.app_map
        for name in names:
            if not name:
                continue
            lo = name.lower()
            for k in (lo, self._icons.norm_name(lo)):
                if k in amap:
                    return amap[k]
        for name in names:
            if name:
                found = self._icons.find_app(name)
                if found:
                    return found
        return None

    @staticmethod
    def _build_index(apps) -> dict[str, DesktopApp]:
        idx: dict[str, DesktopApp] = {}
        for a in apps:
            did = getattr(a, "desktop_id", "") or ""
            if not did:
                continue
            bn = os.path.basename(did)
            base = os.path.splitext(bn)[0]
            for k in (did, bn, base, base.lower()):
                idx[k] = a
        return idx

    @staticmethod
    def _resolve(desktop_id: str, idx):
        if not desktop_id:
            return None
        bn = os.path.basename(desktop_id)
        base = os.path.splitext(bn)[0]
        for k in (desktop_id, bn, base, base.lower()):
            if k in idx:
                return idx[k]
        return None

    def _gio_find(self, ids, idx):
        for raw in ids:
            if not raw:
                continue
            for sfx in ("", ".desktop"):
                try:
                    info = Gio.DesktopAppInfo.new(raw + sfx)
                except Exception:
                    info = None
                if info:
                    m = self._resolve(
                        info.get_id() or "", idx)
                    if m:
                        return m
        for raw in ids:
            if not raw or len(raw) < 2:
                continue

            try: groups = Gio.DesktopAppInfo.search(raw)
            except Exception: continue

            for group in groups:
                for did in group:
                    m = self._resolve(did, idx)
                    if m:
                        return m
        return None

    def _attrs_match(self, ids, apps):
        terms: set[str] = set()
        for raw in ids:
            if raw:
                terms.update(self._expand(raw))
        if not terms:
            return None
        norms = {self._norm(t) for t in terms} - {""}

        for a in apps:
            aids: set[str] = set()
            for attr in ("name", "display_name", "generic_name", "window_class"):
                v = getattr(a, attr, None) or ""
                if v:
                    aids.update(self._expand(v))
            wm = _gio_wm_class(a)
            if wm:
                aids.update(self._expand(wm))
            did = getattr(a, "desktop_id", "") or ""
            if did:
                aids.update(self._expand(
                    os.path.splitext(
                        os.path.basename(did))[0]))
            anorms = {self._norm(i) for i in aids} - {""}
            if terms & aids or norms & anorms:
                return a

        for a in apps:
            cmd = (getattr(a, "command_line", "") or "").lower()
            if not cmd:
                continue
            tokens = cmd.split()
            if not tokens:
                continue
            if terms & self._expand(
                    os.path.basename(tokens[0])):
                return a
            for tok in tokens[1:]:
                if "." in tok \
                        and not tok.startswith(("-", "/")):
                    if terms & self._expand(tok):
                        return a
        return None

    @staticmethod
    def _norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", s.lower()) if s else ""

    @classmethod
    def _expand(cls, name: str) -> set[str]:
        if not name:
            return set()
        out: set[str] = set()
        lo = name.lower()
        n = cls._norm
        out.update({
            lo, lo.replace(" ", "-"), lo.replace(" ", ""),
            lo.replace("-", ""), lo.replace("_", ""), n(lo),
        })
        kb = cls._CAMEL.sub("-", name).lower()
        out.update({kb, kb.replace("-", "")})
        for sep in (".", "-", "_"):
            if sep not in lo:
                continue
            parts = lo.split(sep)
            for i in range(1, len(parts)):
                tail = sep.join(parts[i:])
                out.update({tail, tail.replace(sep, ""),
                            tail.replace(sep, "-"), n(tail)})
        if "." in name:
            last = name.rsplit(".", 1)[-1]
            kb2 = cls._CAMEL.sub("-", last).lower()
            out.update({kb2, kb2.replace("-", "")})
        out.discard("")
        return out

    @classmethod
    def _binary_candidates(cls, *identifiers):
        seen: set[str] = set()
        out: list[str] = []

        def add(v):
            if v and v not in seen:
                seen.add(v)
                out.append(v)

        for b in identifiers:
            if not b:
                continue
            lo = b.lower()
            for v in (b, lo, lo.replace(" ", "-"),
                      lo.replace(" ", ""),
                      lo.replace("_", "-"),
                      cls._CAMEL.sub("-", b).lower()):
                add(v)
            if "." in lo:
                parts = lo.split(".")
                for i in range(1, len(parts)):
                    add(".".join(parts[i:]))
                add(parts[-1])
            if "-" in lo:
                parts = lo.split("-")
                for i in range(1, len(parts)):
                    add("-".join(parts[i:]))
        return out


class SessionManager:
    def __init__(self, resolver: Optional[AppResolver] = None):
        self._file = (Path(GLib.get_user_cache_dir()) / "vidgex-shell" / "session.json")
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._resolver = resolver or AppResolver()
        self._protect_pid = self._ancestor_pid()
        self._pinned_classes: set[str] = set()
        self._pinned_info: list[dict] = []
        self._dir_index: Optional[dict[str, str]] = None

    @staticmethod
    def _ancestor_pid() -> Optional[int]:
        try:
            ppid = os.getppid()
            stat = Path(f"/proc/{ppid}/stat").read_text()
            idx = stat.rfind(")")
            return int(stat[idx + 2:].split()[1])
        except Exception:
            return None

    def _matches(self, client: dict) -> bool:
        if not self._pinned_classes:
            return False
        for key in ("class", "initialClass"):
            val = (client.get(key, "") or "").lower()
            if not val:
                continue
            if val in self._pinned_classes:
                return True
            if val.split(" - ", 1)[0].strip() \
                    in self._pinned_classes:
                return True
        return False

    def get_pinned(self) -> list[dict]:
        if not self._file.exists():
            return []
        try:
            return json.loads(
                self._file.read_text()
            ).get("pinned", [])
        except Exception:
            return []

    def _detect_project(self, client: dict, proc: _ProcInfo) -> str:
        home = GLib.get_home_dir()

        dirs = proc.dir_args()
        if dirs:
            return dirs[0]

        if proc.cwd != home and _is_project_dir(proc.cwd):
            return proc.cwd

        project = self._walk_parents(proc.pid)
        if project:
            return project

        return self._project_from_title(
            client.get("title", ""))

    def _walk_parents(self, pid: int) -> str:
        home = GLib.get_home_dir()
        current = pid
        for _ in range(5):
            proc = _ProcInfo.read(current)
            if not proc:
                break
            ppid = proc.ppid
            if ppid <= 1:
                break
            parent = _ProcInfo.read(ppid)
            if not parent:
                break
            dirs = parent.dir_args()
            if dirs:
                return dirs[0]
            if parent.cwd != home \
                    and _is_project_dir(parent.cwd):
                return parent.cwd
            current = ppid
        return ""

    def _project_from_title(self, title: str) -> str:
        if not title:
            return ""

        for m in re.finditer(r"[~/][^\s:,;\"']+", title):
            expanded = os.path.expanduser(m.group())
            if os.path.isdir(expanded) \
                    and _is_project_dir(expanded):
                return expanded

        segments = re.split(r"\s+[-–—:]\s+", title)
        if len(segments) < 2:
            return ""

        if self._dir_index is None:
            self._dir_index = _build_dir_index(
                GLib.get_home_dir())

        for seg in reversed(segments[:-1]):
            seg = seg.strip()
            if len(seg) < 2:
                continue
            lo = seg.lower()
            if lo in self._dir_index:
                path = self._dir_index[lo]
                if _is_project_dir(path):
                    return path
        return ""

    @staticmethod
    def _windows_by_class() -> dict[str, list[dict]]:
        by: dict[str, list[dict]] = defaultdict(list)
        for w in _hypr_json("clients") or []:
            cls = w.get("class", "").lower()
            if cls:
                by[cls].append(w)
        return dict(by)

    def _close_excess(self, targets: dict[str, int]) -> int:
        cur = self._windows_by_class()
        closed = 0
        for cls, wins in cur.items():
            excess = len(wins) - targets.get(cls, len(wins))
            if excess <= 0:
                continue
            for win in wins[-excess:]:
                if win.get("pid") == self._protect_pid:
                    continue
                addr = win.get("address", "")
                if addr:
                    _hypr_dispatch(
                        f"closewindow address:{addr}")
                    closed += 1
        return closed

    def save(self, pinned_classes=None, pinned_info=None):
        if pinned_classes is not None:
            self._pinned_classes = pinned_classes
        if pinned_info is not None:
            self._pinned_info = pinned_info
        try:
            clients = _hypr_json("clients") or []
            ws_info = _hypr_json("activeworkspace") or {}
            rows: list[dict] = []

            for c in clients:
                wm = c.get("class", "")
                pid = c.get("pid", 0)
                ws = c.get("workspace", {}).get("id", 1)
                if not wm or ws < 0 or pid <= 0:
                    continue
                if not self._matches(c):
                    continue
                proc = _ProcInfo.read(pid)
                if not proc:
                    continue

                app = self._resolver.find(wm)
                cmd = (AppResolver.get_command(app)
                       if app else wm.lower())

                is_term = (
                    _gio_is_terminal(app) if app else None)
                if is_term is None:
                    is_term = proc.uses_pty

                project = self._detect_project(c, proc)

                rows.append({
                    "wm_class": wm,
                    "_lo": wm.lower(),
                    "workspace": ws,
                    "title": c.get("title", ""),
                    "launch_cmd": cmd,
                    "project": project,
                    "is_terminal": is_term,
                })

            by: dict[str, list[dict]] = defaultdict(list)
            for r in rows:
                by[r["_lo"]].append(r)
            for wins in by.values():
                multi = len(wins) > 1
                for w in wins:
                    w["is_multi_instance"] = multi
            for r in rows:
                r.pop("_lo")

            self._file.write_text(json.dumps({
                "timestamp": GLib.DateTime.new_now_local().format("%Y-%m-%d %H:%M:%S"),
                "active_workspace": ws_info.get("id", 1),
                "pinned": self._pinned_info,
                "windows": rows,
            }, indent=2))
        except Exception:
            pass

    def restore(self):
        if not self._file.exists():
            return
        try:
            session = json.loads(self._file.read_text())
        except Exception:
            return

        saved = session.get("windows", [])
        active_ws = session.get("active_workspace", 1)
        if not saved:
            return

        targets: dict[str, int] = defaultdict(int)
        by_cls: dict[str, list[dict]] = defaultdict(list)
        for w in saved:
            cls = w.get("wm_class", "").lower()
            if cls:
                targets[cls] += 1
                by_cls[cls].append(w)

        if self._close_excess(targets):
            time.sleep(0.3)

        sorted_windows = sorted(
            saved, key=lambda w: w.get("workspace", 1))

        cur = self._windows_by_class()
        opened_per_class: dict[str, int] = {}
        total_opened = 0

        for w in sorted_windows:
            wm = w.get("wm_class", "")
            cls = wm.lower()
            project = w.get("project", "")
            ws = w.get("workspace", 1)
            title = w.get("title", "")
            cmd = w.get("launch_cmd", "")
            is_term = w.get("is_terminal", False)

            have = len(cur.get(cls, []))
            already = opened_per_class.get(cls, 0)
            need = targets.get(cls, 0)

            if have + already >= need:
                continue

            if project and not os.path.isdir(project):
                project = self._project_from_title(title)
            has_project = project and os.path.isdir(project)

            if has_project and cmd:
                if is_term:
                    launch = (
                        f"sh -c 'cd \"{project}\" "
                        f"&& exec {cmd}'")
                else:
                    launch = f"{cmd} \"{project}\""
                _hypr_dispatch(
                    f"exec [workspace {ws} silent] "
                    f"{launch}")
                opened_per_class[cls] = already + 1
                total_opened += 1
                continue

            app = self._resolver.find(wm)
            if app:
                try:
                    app.launch()
                    opened_per_class[cls] = already + 1
                    total_opened += 1
                    continue
                except Exception:
                    pass

            if cmd:
                _hypr_dispatch(
                    f"exec [workspace {ws} silent] {cmd}")
                opened_per_class[cls] = already + 1
                total_opened += 1

        if total_opened:
            time.sleep(2.5)
        if self._close_excess(targets):
            time.sleep(0.3)

        self._assign_workspaces(saved)
        _hypr_dispatch(f"workspace {active_ws}")

    def _assign_workspaces(self, saved: list[dict]):
        cur = self._windows_by_class()
        by_cls: dict[str, list[dict]] = defaultdict(list)
        for w in saved:
            cls = w.get("wm_class", "").lower()
            if cls:
                by_cls[cls].append(w)

        cmds: list[str] = []
        for cls, slist in by_cls.items():
            clist = cur.get(cls, [])
            for i, sw in enumerate(slist):
                if i >= len(clist):
                    break
                addr = clist[i].get("address", "")
                if not addr:
                    continue
                ws = sw.get("workspace", 1)
                cmds.append(
                    f"movetoworkspacesilent "
                    f"{ws},address:{addr}")

        _hypr_batch(cmds)