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


_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_FIELD_RE = re.compile(r"%[a-zA-Z]")
_TITLE_SEP_RE = re.compile(r"\s+[-–—:|]\s+")
_PATH_RE = re.compile(r"[~/][^\s:,;\"'<>|]+")
_STRIP_RE = re.compile(r"[^a-z0-9]")
_POSITIONAL_RE = re.compile(r"\$\{?[*@0-9]")

def _cmd_standalone_ok(cmd: str) -> bool:
    if not cmd:
        return False
    return not _POSITIONAL_RE.search(cmd)

def _hypr_json(cmd: str):
    try:
        conn = get_hyprland_connection()
        return json.loads(conn.send_command(f"j/{cmd}").reply.decode())
    except Exception:
        return None

def _hypr_dispatch(cmd: str) -> bool:
    try:
        get_hyprland_connection().send_command(f"dispatch {cmd}")
        return True
    except Exception:
        return False

def _hypr_batch(cmds: list[str]) -> bool:
    if not cmds:
        return True
    try:
        get_hyprland_connection().send_command(
            "[[BATCH]]" + ";".join(f"dispatch {c}" for c in cmds)
        )
        return True
    except Exception:
        return False

_skip_cache: Optional[tuple[str, frozenset[str]]] = None
_gtk_launch_ok: Optional[bool] = None

def _home() -> str:
    return GLib.get_home_dir()

def _has_gtk_launch() -> bool:
    global _gtk_launch_ok
    if _gtk_launch_ok is None:
        _gtk_launch_ok = bool(GLib.find_program_in_path("gtk-launch"))
    return _gtk_launch_ok

def _skip_dirs() -> frozenset[str]:
    global _skip_cache
    home = _home()
    if _skip_cache and _skip_cache[0] == home:
        return _skip_cache[1]
    dirs: set[str] = set()
    for d in (
        GLib.get_user_cache_dir(),
        GLib.get_user_data_dir(),
        GLib.get_user_config_dir(),
    ):
        dirs.add(os.path.realpath(d))
    try:
        dirs.add(os.path.realpath(GLib.get_user_state_dir()))
    except AttributeError:
        dirs.add(os.path.realpath(os.path.join(home, ".local", "state")))
    result = frozenset(dirs)
    _skip_cache = (home, result)
    return result

def _is_project_dir(path: str) -> bool:
    real = os.path.realpath(path)
    real_home = os.path.realpath(_home())
    if real == real_home:
        return False
    if not real.startswith(real_home + os.sep):
        return True
    for skip in _skip_dirs():
        if real == skip or real.startswith(skip + os.sep):
            return False
    return True

def _build_dir_index(home: str) -> dict[str, str]:
    index: dict[str, str] = {}
    try:
        for entry in os.scandir(home):
            if not entry.is_dir(follow_symlinks=False) or entry.name[0] == ".":
                continue
            lo = entry.name.lower()
            index[lo] = entry.path
            try:
                for sub in os.scandir(entry.path):
                    if sub.is_dir(follow_symlinks=False) and sub.name[0] != ".":
                        slo = sub.name.lower()
                        if slo not in index:
                            index[slo] = sub.path
            except OSError:
                pass
    except OSError:
        pass
    return index

def _norm(s: str) -> str:
    return _STRIP_RE.sub("", s.lower()) if s else ""

def _title_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    al, bl = a.lower(), b.lower()
    if al == bl:
        return 1.0
    wa, wb = set(al.split()), set(bl.split())
    union = len(wa | wb)
    if not union:
        return 0.0
    return len(wa & wb) / union

@dataclass(slots=True)
class _ProcInfo:
    pid: int
    cmdline: str = ""
    cwd: str = ""
    args: list[str] = field(default_factory=list)
    _pty: Optional[bool] = field(default=None, repr=False)

    @classmethod
    def read(cls, pid: int) -> Optional[_ProcInfo]:
        base = f"/proc/{pid}"
        try:
            raw = Path(f"{base}/cmdline").read_bytes()
        except OSError:
            return None
        info = cls(pid=pid)
        info.args = [a for a in raw.decode(errors="replace").split("\x00") if a]
        info.cmdline = " ".join(info.args)
        try:
            cwd = os.readlink(f"{base}/cwd")
            info.cwd = cwd if os.path.isdir(cwd) else _home()
        except OSError:
            info.cwd = _home()
        return info

    @property
    def uses_pty(self) -> bool:
        if self._pty is not None:
            return self._pty
        result = False
        try:
            for fd in os.scandir(f"/proc/{self.pid}/fd"):
                try:
                    if os.readlink(fd.path).startswith("/dev/pts/"):
                        result = True
                        break
                except OSError:
                    continue
        except OSError:
            pass
        self._pty = result
        return result

    @property
    def ppid(self) -> int:
        try:
            stat = Path(f"/proc/{self.pid}/stat").read_text()
        except OSError:
            return 0
        idx = stat.rfind(")")
        if idx < 0:
            return 0
        try:
            return int(stat[idx + 2:].split()[1])
        except (ValueError, IndexError):
            return 0

    def dir_args(self) -> list[str]:
        out: list[str] = []
        for arg in self.args[1:]:
            if arg[0:1] == "-":
                continue
            expanded = os.path.expanduser(arg)
            if os.path.isdir(expanded) and _is_project_dir(expanded):
                out.append(os.path.realpath(expanded))
        return out


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
    cats = getattr(gio, "get_categories", lambda: "")() or ""
    if "TerminalEmulator" in cats:
        return True
    if hasattr(gio, "get_boolean"):
        try:
            return gio.get_boolean("Terminal")
        except Exception:
            pass
    return None


class AppResolver:
    _INDEX_TTL = 30.0

    __slots__ = ("_icons", "_cache", "_apps", "_index", "_index_ts")

    def __init__(self, app_resolver=None):
        self._icons = app_resolver
        self._cache: dict[tuple, Optional[DesktopApp]] = {}
        self._apps: Optional[list[DesktopApp]] = None
        self._index: Optional[dict[str, DesktopApp]] = None
        self._index_ts: float = 0.0

    def invalidate_cache(self):
        self._cache.clear()
        self._apps = self._index = None
        self._index_ts = 0.0

    def find(self, *identifiers: str) -> Optional[DesktopApp]:
        ids = tuple(s.lower() for s in identifiers if s)
        if not ids:
            return None
        key = tuple(sorted(set(ids)))
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        if key in self._cache:
            return None
        result = self._do_find(ids)
        self._cache[key] = result
        return result

    def launch(self, app=None, key="", original_class="") -> bool:
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
        for binary in self._binary_candidates(key, original_class):
            if GLib.find_program_in_path(binary):
                exec_shell_command_async(binary)
                return True
        return False

    @staticmethod
    def get_command(app: DesktopApp) -> str:
        cmd = getattr(app, "command_line", "") or ""
        return _FIELD_RE.sub("", cmd).strip()

    @staticmethod
    def get_desktop_id(app: DesktopApp) -> str:
        did = getattr(app, "desktop_id", "") or ""
        if did:
            return did
        gio = _get_gio(app)
        if gio:
            return gio.get_id() or ""
        return ""

    def _ensure_index(self):
        now = time.monotonic()
        if self._apps is None or now - self._index_ts > self._INDEX_TTL:
            self._apps = get_desktop_applications()
            idx: dict[str, DesktopApp] = {}
            for a in self._apps:
                did = getattr(a, "desktop_id", "") or ""
                if not did:
                    continue
                bn = os.path.basename(did)
                base = os.path.splitext(bn)[0]
                for k in (did, bn, base, base.lower()):
                    if k not in idx:
                        idx[k] = a
            self._index = idx
            self._index_ts = now
        return self._apps, self._index

    def _do_find(self, ids):
        r = self._icon_lookup(*ids)
        if r:
            return r
        apps, idx = self._ensure_index()
        r = self._gio_find(ids, idx)
        if r:
            return r
        return self._attrs_match(ids, apps)

    def _icon_lookup(self, *names):
        if not self._icons:
            return None
        amap = getattr(self._icons, "app_map", None)
        norm_fn = getattr(self._icons, "norm_name", None)
        if amap:
            for name in names:
                if not name:
                    continue
                lo = name.lower()
                r = amap.get(lo)
                if r:
                    return r
                if norm_fn:
                    r = amap.get(norm_fn(lo))
                    if r:
                        return r
        find_fn = getattr(self._icons, "find_app", None)
        if find_fn:
            for name in names:
                if name:
                    r = find_fn(name)
                    if r:
                        return r
        return None

    @staticmethod
    def _resolve(desktop_id: str, idx):
        if not desktop_id:
            return None
        bn = os.path.basename(desktop_id)
        base = os.path.splitext(bn)[0]
        for k in (desktop_id, bn, base, base.lower()):
            r = idx.get(k)
            if r:
                return r
        return None

    def _gio_find(self, ids, idx):
        for raw in ids:
            if not raw:
                continue
            for sfx in ("", ".desktop"):
                try:
                    info = Gio.DesktopAppInfo.new(raw + sfx)
                except Exception:
                    continue
                if info:
                    m = self._resolve(info.get_id() or "", idx)
                    if m:
                        return m
        for raw in ids:
            if not raw or len(raw) < 2:
                continue
            try:
                groups = Gio.DesktopAppInfo.search(raw)
            except Exception:
                continue
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
                terms.update(_expand(raw))
        if not terms:
            return None
        norms = {_norm(t) for t in terms}
        norms.discard("")

        for a in apps:
            aids: set[str] = set()
            for attr in ("name", "display_name", "generic_name", "window_class"):
                v = getattr(a, attr, None)
                if v:
                    aids.update(_expand(v))
            wm = _gio_wm_class(a)
            if wm:
                aids.update(_expand(wm))
            did = getattr(a, "desktop_id", "") or ""
            if did:
                aids.update(_expand(os.path.splitext(os.path.basename(did))[0]))
            anorms = {_norm(i) for i in aids}
            anorms.discard("")
            if terms & aids or norms & anorms:
                return a

        for a in apps:
            cmd = (getattr(a, "command_line", "") or "").lower()
            if not cmd:
                continue
            tokens = cmd.split()
            if not tokens:
                continue
            if terms & _expand(os.path.basename(tokens[0])):
                return a
            for tok in tokens[1:]:
                if "." in tok and tok[0] not in "-/%":
                    if terms & _expand(tok):
                        return a
        return None

    @classmethod
    def _binary_candidates(cls, *identifiers):
        seen: set[str] = set()
        out: list[str] = []
        for b in identifiers:
            if not b:
                continue
            lo = b.lower()
            for v in (
                b, lo, lo.replace(" ", "-"), lo.replace(" ", ""),
                lo.replace("_", "-"), _CAMEL_RE.sub("-", b).lower(),
            ):
                if v and v not in seen:
                    seen.add(v)
                    out.append(v)
            if "." in lo:
                parts = lo.split(".")
                for i in range(1, len(parts)):
                    v = ".".join(parts[i:])
                    if v not in seen:
                        seen.add(v)
                        out.append(v)
                v = parts[-1]
                if v and v not in seen:
                    seen.add(v)
                    out.append(v)
            if "-" in lo:
                parts = lo.split("-")
                for i in range(1, len(parts)):
                    v = "-".join(parts[i:])
                    if v not in seen:
                        seen.add(v)
                        out.append(v)
        return out


def _expand(name: str) -> set[str]:
    if not name:
        return set()
    lo = name.lower()
    out: set[str] = {
        lo, lo.replace(" ", "-"), lo.replace(" ", ""),
        lo.replace("-", ""), lo.replace("_", ""), _norm(lo),
    }
    kb = _CAMEL_RE.sub("-", name).lower()
    out.add(kb)
    out.add(kb.replace("-", ""))
    for sep in (".", "-", "_"):
        if sep not in lo:
            continue
        parts = lo.split(sep)
        for i in range(1, len(parts)):
            tail = sep.join(parts[i:])
            out.add(tail)
            out.add(tail.replace(sep, ""))
            out.add(tail.replace(sep, "-"))
            out.add(_norm(tail))
    if "." in name:
        last = name.rsplit(".", 1)[-1]
        kb2 = _CAMEL_RE.sub("-", last).lower()
        out.add(kb2)
        out.add(kb2.replace("-", ""))
    out.discard("")
    return out


class SessionManager:
    CLOSE_SETTLE = 0.3
    POLL_INTERVAL = 0.4
    LAUNCH_TIMEOUT = 8.0

    __slots__ = (
        "_file", "_resolver", "_protect_pid",
        "_pinned_classes", "_pinned_info", "_dir_index",
    )

    def __init__(self, resolver: Optional[AppResolver] = None):
        self._file = Path(GLib.get_user_cache_dir()) / "vidgex-shell" / "session.json"
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._resolver = resolver or AppResolver()
        self._protect_pid = self._ancestor_pid()
        self._pinned_classes: set[str] = set()
        self._pinned_info: list[dict] = []
        self._dir_index: Optional[dict[str, str]] = None

    @staticmethod
    def _ancestor_pid() -> Optional[int]:
        try:
            proc = _ProcInfo.read(os.getppid())
            return proc.ppid if proc else None
        except Exception:
            return None

    def _matches(self, client: dict) -> bool:
        if not self._pinned_classes:
            return False
        for key in ("class", "initialClass"):
            val = (client.get(key) or "").lower()
            if val and val in self._pinned_classes:
                return True
        return False

    def get_pinned(self) -> list[dict]:
        if not self._file.exists():
            return []
        try:
            return json.loads(self._file.read_text()).get("pinned", [])
        except Exception:
            return []

    def _detect_project(self, client: dict, proc: _ProcInfo) -> str:
        home = _home()
        dirs = proc.dir_args()
        if dirs:
            return dirs[0]
        if proc.cwd and proc.cwd != home and _is_project_dir(proc.cwd):
            return os.path.realpath(proc.cwd)
        project = self._walk_parents(proc.pid)
        if project:
            return project
        return self._project_from_title(client.get("title", ""))

    def _walk_parents(self, pid: int) -> str:
        home = _home()
        visited: set[int] = set()
        cur = pid
        for _ in range(5):
            if cur in visited or cur <= 1:
                break
            visited.add(cur)
            proc = _ProcInfo.read(cur)
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
            if parent.cwd and parent.cwd != home and _is_project_dir(parent.cwd):
                return os.path.realpath(parent.cwd)
            cur = ppid
        return ""

    def _project_from_title(self, title: str) -> str:
        if not title:
            return ""
        for m in _PATH_RE.finditer(title):
            expanded = os.path.expanduser(m.group())
            if os.path.isdir(expanded) and _is_project_dir(expanded):
                return os.path.realpath(expanded)
        segments = _TITLE_SEP_RE.split(title)
        if len(segments) < 2:
            return ""
        if self._dir_index is None:
            self._dir_index = _build_dir_index(_home())
        for seg in reversed(segments[:-1]):
            seg = seg.strip()
            if len(seg) < 2:
                continue
            path = self._dir_index.get(seg.lower())
            if path and _is_project_dir(path):
                return path
        return ""

    @staticmethod
    def _by_class(clients=None) -> dict[str, list[dict]]:
        if clients is None:
            clients = _hypr_json("clients") or []
        by: dict[str, list[dict]] = defaultdict(list)
        for w in clients:
            cls = (w.get("class") or "").lower()
            if cls:
                by[cls].append(w)
        return dict(by)

    def save(self, pinned_classes=None, pinned_info=None):
        if pinned_classes is not None:
            self._pinned_classes = pinned_classes
        if pinned_info is not None:
            self._pinned_info = pinned_info
        try:
            self._do_save()
        except Exception:
            pass

    def _do_save(self):
        clients = _hypr_json("clients") or []
        ws_info = _hypr_json("activeworkspace") or {}
        windows: list[dict] = []
        counts: dict[str, int] = defaultdict(int)

        for c in clients:
            wm = c.get("class", "")
            pid = c.get("pid", 0)
            ws_id = c.get("workspace", {}).get("id", 1)
            if not wm or ws_id < 0 or pid <= 0:
                continue
            if not self._matches(c):
                continue
            proc = _ProcInfo.read(pid)
            if not proc:
                continue

            lo = wm.lower()
            counts[lo] += 1
            app = self._resolver.find(wm)

            cmd = AppResolver.get_command(app) if app else lo
            desktop_id = AppResolver.get_desktop_id(app) if app else ""

            is_term = _gio_is_terminal(app) if app else None
            if is_term is None:
                is_term = proc.uses_pty

            windows.append({
                "wm_class": wm,
                "workspace": ws_id,
                "title": c.get("title", ""),
                "desktop_id": desktop_id,
                "launch_cmd": cmd,
                "project": self._detect_project(c, proc),
                "is_terminal": bool(is_term),
            })

        for w in windows:
            w["is_multi_instance"] = counts[w["wm_class"].lower()] > 1

        tmp = self._file.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps({
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "active_workspace": ws_info.get("id", 1),
                "pinned": self._pinned_info,
                "windows": windows,
            }, indent=2))
            tmp.rename(self._file)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

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
        for w in saved:
            cls = (w.get("wm_class") or "").lower()
            if cls:
                targets[cls] += 1

        if self._close_excess(targets):
            time.sleep(self.CLOSE_SETTLE)

        launched = self._launch_missing(saved, targets)

        if launched:
            self._poll_windows(targets)

        if self._close_excess(targets):
            time.sleep(self.CLOSE_SETTLE)

        self._assign_workspaces(saved)
        _hypr_dispatch(f"workspace {active_ws}")

    def _close_excess(self, targets: dict[str, int]) -> int:
        cur = self._by_class()
        closed = 0
        for cls, wins in cur.items():
            if cls not in targets:
                continue
            excess = len(wins) - targets[cls]
            if excess <= 0:
                continue
            for win in wins[-excess:]:
                if win.get("pid") == self._protect_pid:
                    continue
                addr = win.get("address", "")
                if addr and _hypr_dispatch(f"closewindow address:{addr}"):
                    closed += 1
        return closed

    def _launch_missing(self, saved: list[dict], targets: dict[str, int]) -> int:
        cur = self._by_class()
        opened: dict[str, int] = {}
        total = 0
        for w in sorted(saved, key=lambda x: x.get("workspace", 1)):
            cls = (w.get("wm_class") or "").lower()
            if not cls:
                continue
            have = len(cur.get(cls, []))
            already = opened.get(cls, 0)
            if have + already >= targets.get(cls, 0):
                continue
            if self._launch_one(w):
                opened[cls] = already + 1
                total += 1
        return total

    def _launch_one(self, w: dict) -> bool:
        wm = w.get("wm_class", "")
        project = w.get("project", "")
        ws = w.get("workspace", 1)
        title = w.get("title", "")
        cmd = w.get("launch_cmd", "")
        is_term = w.get("is_terminal", False)
        desktop_id = w.get("desktop_id", "")

        if project and not os.path.isdir(project):
            project = self._project_from_title(title)
        has_project = bool(project and os.path.isdir(project))

        app = None
        if not cmd or not _cmd_standalone_ok(cmd) or not desktop_id:
            app = self._resolver.find(wm)
            if app:
                if not desktop_id:
                    desktop_id = AppResolver.get_desktop_id(app)
                if not cmd or not _cmd_standalone_ok(cmd):
                    alt = AppResolver.get_command(app)
                    if _cmd_standalone_ok(alt):
                        cmd = alt

        if cmd and _cmd_standalone_ok(cmd):
            if has_project:
                if is_term:
                    launch = f"sh -c 'cd \"{project}\" && exec {cmd}'"
                else:
                    launch = f"{cmd} \"{project}\""
            else:
                launch = cmd
            return _hypr_dispatch(f"exec [workspace {ws} silent] {launch}")

        gtk_name = self._gtk_launch_name(desktop_id, wm)
        if gtk_name and _has_gtk_launch():
            return _hypr_dispatch(
                f"exec [workspace {ws} silent] gtk-launch {gtk_name}"
            )

        if app is None:
            app = self._resolver.find(wm)
        if app:
            try:
                app.launch()
                return True
            except Exception:
                pass

        for binary in AppResolver._binary_candidates(wm):
            if GLib.find_program_in_path(binary):
                return _hypr_dispatch(
                    f"exec [workspace {ws} silent] {binary}"
                )

        return False

    @staticmethod
    def _gtk_launch_name(desktop_id: str, wm_class: str) -> str:
        if desktop_id:
            bn = os.path.basename(desktop_id)
            return os.path.splitext(bn)[0]
        if wm_class:
            lo = wm_class.lower()
            for candidate in (
                lo.replace(" ", "-"),
                lo.replace(" ", ""),
                lo,
            ):
                if candidate:
                    return candidate
        return ""

    def _poll_windows(self, targets: dict[str, int]):
        deadline = time.monotonic() + self.LAUNCH_TIMEOUT
        while time.monotonic() < deadline:
            time.sleep(self.POLL_INTERVAL)
            cur = self._by_class()
            if all(len(cur.get(c, [])) >= n for c, n in targets.items()):
                return

    def _assign_workspaces(self, saved: list[dict]):
        cur = self._by_class()
        saved_by: dict[str, list[dict]] = defaultdict(list)
        for w in saved:
            cls = (w.get("wm_class") or "").lower()
            if cls:
                saved_by[cls].append(w)

        cmds: list[str] = []
        for cls, slist in saved_by.items():
            clist = cur.get(cls, [])
            if not clist:
                continue
            for sw, cw in _match_windows(slist, clist):
                addr = cw.get("address", "")
                ws = sw.get("workspace", 1)
                cur_ws = cw.get("workspace", {}).get("id", -1)
                if addr and ws != cur_ws:
                    cmds.append(f"movetoworkspacesilent {ws},address:{addr}")
        if cmds:
            _hypr_batch(cmds)


def _match_windows(saved: list[dict], current: list[dict]) -> list[tuple[dict, dict]]:
    if not saved or not current:
        return []
    if len(saved) == 1:
        return [(saved[0], current[0])]

    limit = min(len(saved), len(current))
    scores: list[tuple[float, int, int]] = []
    for si, sw in enumerate(saved):
        st = sw.get("title", "")
        sp = sw.get("project", "")
        for ci, cw in enumerate(current):
            ct = cw.get("title", "")
            score = _title_similarity(st, ct)
            if sp and sp in ct:
                score += 0.3
            scores.append((score, si, ci))

    scores.sort(reverse=True)
    used_s: set[int] = set()
    used_c: set[int] = set()
    pairs: list[tuple[dict, dict]] = []

    for _score, si, ci in scores:
        if si in used_s or ci in used_c:
            continue
        used_s.add(si)
        used_c.add(ci)
        pairs.append((saved[si], current[ci]))
        if len(pairs) >= limit:
            return pairs

    for si in range(len(saved)):
        if si in used_s:
            continue
        for ci in range(len(current)):
            if ci not in used_c:
                used_c.add(ci)
                pairs.append((saved[si], current[ci]))
                break
        if len(pairs) >= limit:
            break
    return pairs