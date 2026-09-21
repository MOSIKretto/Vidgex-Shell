from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
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
_CANVAS_WS_RE = re.compile(r"^ws_(\d+)$")

CANVAS_STATE_DIR = Path(os.path.expanduser("~/.cache/vidgex-shell/vidgex_canvas"))

_DEBUG = os.environ.get("VIDGEX_DEBUG", "0") == "1"


def _dbg(*args):
    if _DEBUG:
        print("[vidgex]", *args, file=sys.stderr)


# ---------------------------------------------------------------------------
# IPC helpers
# ---------------------------------------------------------------------------

def _cmd_standalone_ok(cmd: str) -> bool:
    return bool(cmd) and not _POSITIONAL_RE.search(cmd)


def _unwrap_reply(result) -> str:
    reply = result.reply
    if isinstance(reply, (bytes, bytearray)):
        reply = reply.decode()
    return str(reply)


def _hypr_json(cmd: str):
    conn = get_hyprland_connection()
    raw = _unwrap_reply(conn.send_command(f"j/{cmd}"))
    _dbg(f"j/{cmd} -> {raw[:200]!r}")
    return json.loads(raw)


def _hypr_dispatch(cmd: str) -> bool:
    if not cmd:
        return False
    conn = get_hyprland_connection()
    reply = _unwrap_reply(conn.send_command(f"dispatch {cmd}")).strip()
    _dbg(f"dispatch {cmd} -> {reply!r}")
    return True


def _hypr_batch(cmds: list[str]) -> bool:
    if not cmds:
        return True
    return all([_hypr_dispatch(c) for c in cmds])


# ---------------------------------------------------------------------------
# Кэш и вспомогательные функции
# ---------------------------------------------------------------------------

def _home() -> str:
    return GLib.get_home_dir()


@lru_cache(maxsize=1)
def _has_gtk_launch() -> bool:
    return bool(GLib.find_program_in_path("gtk-launch"))


@lru_cache(maxsize=1)
def _skip_dirs() -> frozenset[str]:
    return frozenset(
        os.path.realpath(d)
        for d in (
            GLib.get_user_cache_dir(),
            GLib.get_user_data_dir(),
            GLib.get_user_config_dir(),
            GLib.get_user_state_dir(),
        )
    )


def _is_project_dir(path: str) -> bool:
    real = os.path.realpath(path)
    real_home = os.path.realpath(_home())
    if real == real_home:
        return False
    if not real.startswith(real_home + os.sep):
        return True
    return not any(real == s or real.startswith(s + os.sep) for s in _skip_dirs())


def _build_dir_index(home: str) -> dict[str, str]:
    index: dict[str, str] = {}
    top_entries = list(os.scandir(home))

    for entry in top_entries:
        if entry.name.startswith(".") or not entry.is_dir(follow_symlinks=False):
            continue
        index[entry.name.lower()] = entry.path
        with os.scandir(entry.path) as sub_it:
            for sub in sub_it:
                if sub.name.startswith(".") or not sub.is_dir(follow_symlinks=False):
                    continue
                index.setdefault(sub.name.lower(), sub.path)

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
    return len(wa & wb) / union if union else 0.0


# ---------------------------------------------------------------------------
# Генерация вариантов идентификаторов (используется и для поиска
# desktop-приложений, и для подбора имени бинарника)
# ---------------------------------------------------------------------------

def _name_variants(name: str) -> list[str]:
    if not name:
        return []
    lo = name.lower()
    kebab = _CAMEL_RE.sub("-", name).lower()

    variants: dict[str, None] = {}

    def add(v: str) -> None:
        if v:
            variants.setdefault(v, None)

    add(name)
    add(lo)
    add(kebab)
    add(lo.replace(" ", "-"))
    add(lo.replace(" ", ""))
    add(lo.replace("-", ""))
    add(lo.replace("_", ""))
    add(lo.replace("_", "-"))
    add(kebab.replace("-", ""))
    add(_norm(lo))

    for sep in (".", "-", "_"):
        if sep not in lo:
            continue
        parts = lo.split(sep)
        for i in range(1, len(parts)):
            tail = sep.join(parts[i:])
            add(tail)
            add(tail.replace(sep, ""))
            add(tail.replace(sep, "-"))
            add(_norm(tail))

    if "." in name:
        last = name.rsplit(".", 1)[-1]
        kb2 = _CAMEL_RE.sub("-", last).lower()
        add(kb2)
        add(kb2.replace("-", ""))

    return list(variants)


def _expand(name: str) -> set[str]:
    return set(_name_variants(name))


# ---------------------------------------------------------------------------
# ProcInfo
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class _ProcInfo:
    pid: int
    cmdline: str = ""
    cwd: str = ""
    args: list[str] = field(default_factory=list)
    _pty: Optional[bool] = field(default=None, repr=False)

    @classmethod
    def read(cls, pid: int) -> _ProcInfo:
        base = f"/proc/{pid}"
        raw = Path(f"{base}/cmdline").read_bytes()
        cwd = os.readlink(f"{base}/cwd")

        info = cls(pid=pid)
        info.args = [a for a in raw.decode(errors="replace").split("\x00") if a]
        info.cmdline = " ".join(info.args)
        info.cwd = cwd
        return info

    @property
    def uses_pty(self) -> bool:
        if self._pty is not None:
            return self._pty
        result = False
        with os.scandir(f"/proc/{self.pid}/fd") as it:
            for fd in it:
                if os.readlink(fd.path).startswith("/dev/pts/"):
                    result = True
                    break
        self._pty = result
        return result

    @property
    def ppid(self) -> int:
        stat = Path(f"/proc/{self.pid}/stat").read_text()
        idx = stat.rfind(")")
        return int(stat[idx + 2:].split()[1])

    def dir_args(self) -> list[str]:
        out: list[str] = []
        for arg in self.args[1:]:
            if arg.startswith("-"):
                continue
            expanded = os.path.expanduser(arg)
            if os.path.isdir(expanded) and _is_project_dir(expanded):
                out.append(os.path.realpath(expanded))
        return out


# ---------------------------------------------------------------------------
# Gio helpers
# ---------------------------------------------------------------------------

def _get_gio(app: DesktopApp) -> Optional[Gio.DesktopAppInfo]:
    for attr in ("_app", "app_info", "desktop_app_info"):
        obj = getattr(app, attr, None)
        if isinstance(obj, (Gio.DesktopAppInfo, Gio.AppInfo)):
            return obj
    return None


def _gio_wm_class(app: DesktopApp) -> str:
    gio = _get_gio(app)
    if gio is None:
        return ""
    wm_class = gio.get_startup_wm_class()
    return wm_class.lower() if wm_class else ""


def _gio_is_terminal(app: DesktopApp) -> Optional[bool]:
    gio = _get_gio(app)
    if gio is None:
        return None
    cats = gio.get_categories() or ""
    if "TerminalEmulator" in cats:
        return True
    return gio.get_boolean("Terminal")


# ---------------------------------------------------------------------------
# AppResolver
# ---------------------------------------------------------------------------

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
        if key in self._cache:
            return self._cache[key]
        result = self._do_find(ids)
        self._cache[key] = result
        return result

    def launch(self, app=None, key="", original_class="") -> bool:
        if app:
            app.launch()
            return True
        found = self.find(key, original_class)
        if found:
            found.launch()
            return True
        for binary in self._binary_candidates(key, original_class):
            if GLib.find_program_in_path(binary):
                exec_shell_command_async(binary)
                return True
        return False

    @staticmethod
    def get_command(app: Optional[DesktopApp]) -> str:
        if app is None:
            return ""
        cmd = app.command_line or ""
        return _FIELD_RE.sub("", cmd).strip()

    @staticmethod
    def get_desktop_id(app: Optional[DesktopApp]) -> str:
        return app.desktop_id or "" if app else ""

    def _ensure_index(self):
        now = time.monotonic()
        if self._apps is None or now - self._index_ts > self._INDEX_TTL:
            self._apps = get_desktop_applications()
            idx: dict[str, DesktopApp] = {}
            for a in self._apps:
                did = a.desktop_id
                if not did:
                    continue
                bn = os.path.basename(did)
                base = os.path.splitext(bn)[0]
                for k in (did, bn, base, base.lower()):
                    idx.setdefault(k, a)
            self._index = idx
            self._index_ts = now
        return self._apps, self._index

    def _do_find(self, ids):
        if self._icons is not None:
            r = self._icon_lookup(*ids)
            if r:
                return r
        apps, idx = self._ensure_index()
        return self._gio_find(ids, idx) or self._attrs_match(ids, apps)

    def _icon_lookup(self, *names):
        amap = self._icons.app_map
        norm_fn = self._icons.norm_name
        for name in names:
            if not name:
                continue
            r = amap.get(name.lower()) or amap.get(norm_fn(name.lower()))
            if r:
                return r
        find_fn = self._icons.find_app
        for name in names:
            if name and (r := find_fn(name)):
                return r
        return None

    @staticmethod
    def _resolve(desktop_id: str, idx):
        if not desktop_id:
            return None
        bn = os.path.basename(desktop_id)
        base = os.path.splitext(bn)[0]
        for k in (desktop_id, bn, base, base.lower()):
            if r := idx.get(k):
                return r
        return None

    def _gio_find(self, ids, idx):
        for raw in ids:
            if not raw:
                continue
            for sfx in ("", ".desktop"):
                info = Gio.DesktopAppInfo.new(raw + sfx)
                if info and (m := self._resolve(info.get_id(), idx)):
                    return m
        for raw in ids:
            if not raw or len(raw) < 2:
                continue
            for group in Gio.DesktopAppInfo.search(raw):
                for did in group:
                    if m := self._resolve(did, idx):
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
            if wm := _gio_wm_class(a):
                aids.update(_expand(wm))
            if did := a.desktop_id:
                aids.update(_expand(os.path.splitext(os.path.basename(did))[0]))
            anorms = {_norm(i) for i in aids}
            anorms.discard("")
            if terms & aids or norms & anorms:
                return a

        for a in apps:
            tokens = (a.command_line or "").lower().split()
            if not tokens:
                continue
            if terms & _expand(os.path.basename(tokens[0])):
                return a
            for tok in tokens[1:]:
                if "." in tok and tok[0] not in "-/%" and terms & _expand(tok):
                    return a
        return None

    @classmethod
    def _binary_candidates(cls, *identifiers) -> list[str]:
        seen: dict[str, None] = {}
        for ident in identifiers:
            for v in _name_variants(ident):
                seen.setdefault(v, None)
        return list(seen)


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------

class SessionManager:
    CLOSE_SETTLE_MS = 300
    POLL_INTERVAL_MS = 400
    LAUNCH_TIMEOUT_MS = 8000

    __slots__ = (
        "_file", "_resolver", "_protect_pid",
        "_pinned_classes", "_pinned_info", "_dir_index",
        "_restore_pairs", "_canvas_ws", "_restoring",
    )

    def __init__(self, resolver: Optional[AppResolver] = None):
        self._file = Path(GLib.get_user_cache_dir()) / "vidgex-shell" / "session.json"
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._resolver = resolver
        self._protect_pid = self._ancestor_pid()
        self._pinned_classes: set[str] = set()
        self._pinned_info: list[dict] = []
        self._dir_index: Optional[dict[str, str]] = None
        self._restore_pairs: list[tuple[dict, dict]] = []
        self._canvas_ws: list[int] = []
        self._restoring: bool = False

    @staticmethod
    def _ancestor_pid() -> int:
        return _ProcInfo.read(os.getppid()).ppid

    def _matches(self, client: dict) -> bool:
        if not self._pinned_classes:
            return False
        for key in ("class", "initialClass"):
            val = (client.get(key) or "").lower()
            if val and val in self._pinned_classes:
                return True
        return False

    def get_pinned(self) -> list[dict]:
        session = self._load_session()
        return session.get("pinned", []) if session else []

    def _load_session(self) -> Optional[dict]:
        if not self._file.exists():
            return None
        return json.loads(self._file.read_text())

    def _detect_project(self, client: dict, proc: _ProcInfo) -> str:
        home = _home()
        if dirs := proc.dir_args():
            return dirs[0]
        if proc.cwd and proc.cwd != home and _is_project_dir(proc.cwd):
            return os.path.realpath(proc.cwd)
        if project := self._walk_parents(proc.pid):
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
            ppid = proc.ppid
            if ppid <= 1:
                break
            parent = _ProcInfo.read(ppid)
            if dirs := parent.dir_args():
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
            clients = _hypr_json("clients")
        by: dict[str, list[dict]] = defaultdict(list)
        for w in clients:
            cls = (w.get("class") or "").lower()
            if cls:
                by[cls].append(w)
        return dict(by)

    # ------------------------------------------------------------------
    # SAVE
    # ------------------------------------------------------------------

    def save_all(self) -> None:
        if self._restoring:
            _dbg("skip autosave: restore in progress")
            return
        clients = _hypr_json("clients")
        classes = {c["class"].lower() for c in clients if c.get("class")}
        self.save(pinned_classes=classes, pinned_info=[])

    def save(self, pinned_classes=None, pinned_info=None):
        if pinned_classes is not None:
            self._pinned_classes = pinned_classes
        if pinned_info is not None:
            self._pinned_info = pinned_info
        self._do_save()

    def _do_save(self):
        if self._resolver is None:
            _dbg("no resolver configured, skipping save")
            return

        self._resolver.invalidate_cache()

        clients = _hypr_json("clients")
        ws_info = _hypr_json("activeworkspace")
        windows: list[dict] = []
        counts: dict[str, int] = defaultdict(int)

        canvas_workspaces: list[int] = []
        if CANVAS_STATE_DIR.exists():
            for f in CANVAS_STATE_DIR.iterdir():
                if m := _CANVAS_WS_RE.match(f.name):
                    canvas_workspaces.append(int(m.group(1)))

        for c in clients:
            wm = c.get("class") or ""
            pid = c.get("pid", 0)
            ws_id = c.get("workspace", {}).get("id", -1)
            if not wm or ws_id < 0 or pid <= 0 or not self._matches(c):
                continue

            proc = _ProcInfo.read(pid)
            lo = wm.lower()
            counts[lo] += 1
            app = self._resolver.find(wm)

            windows.append({
                "wm_class": wm,
                "workspace": ws_id,
                "title": c.get("title", ""),
                "desktop_id": AppResolver.get_desktop_id(app),
                "launch_cmd": AppResolver.get_command(app),
                "project": self._detect_project(c, proc),
                "is_terminal": bool(_gio_is_terminal(app) if app else None),
                "floating": c.get("floating", False),
                "at": c.get("at", [0, 0]),
                "size": c.get("size", [0, 0]),
            })

        for w in windows:
            w["is_multi_instance"] = counts[w["wm_class"].lower()] > 1

        self._sync_canvas_state_dir(canvas_workspaces)

        tmp = self._file.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "active_workspace": ws_info.get("id"),
            "canvas_workspaces": canvas_workspaces,
            "pinned": self._pinned_info,
            "windows": windows,
        }, indent=2))
        tmp.rename(self._file)

    @staticmethod
    def _sync_canvas_state_dir(canvas_workspaces: list[int]) -> None:
        CANVAS_STATE_DIR.mkdir(parents=True, exist_ok=True)
        wanted = {f"ws_{ws_id}" for ws_id in canvas_workspaces}
        for f in CANVAS_STATE_DIR.iterdir():
            if _CANVAS_WS_RE.match(f.name) and f.name not in wanted:
                f.unlink(missing_ok=True)
        for name in wanted:
            (CANVAS_STATE_DIR / name).touch()

    # ------------------------------------------------------------------
    # RESTORE
    # ------------------------------------------------------------------

    def restore(self):
        session = self._load_session()
        if session is None:
            return

        saved = session.get("windows", [])
        if not saved:
            return

        self._canvas_ws = [int(x) for x in session.get("canvas_workspaces", [])]

        targets: dict[str, int] = defaultdict(int)
        for w in saved:
            if cls := (w.get("wm_class") or "").lower():
                targets[cls] += 1

        self._restoring = True

        if self._close_excess(targets):
            GLib.timeout_add(self.CLOSE_SETTLE_MS, self._restore_step_launch, saved, targets)
        else:
            self._restore_step_launch(saved, targets)

    def _restore_step_launch(self, saved: list[dict], targets: dict[str, int]) -> bool:
        if self._launch_missing(saved, targets):
            GLib.timeout_add(self.POLL_INTERVAL_MS, self._restore_step_poll, saved, targets, 0)
        else:
            self._restore_step_assign(saved, targets)
        return False

    def _restore_step_poll(self, saved: list[dict], targets: dict[str, int], elapsed: int) -> bool:
        cur = self._by_class()
        done = all(len(cur.get(c, [])) >= n for c, n in targets.items())
        if done or elapsed >= self.LAUNCH_TIMEOUT_MS:
            self._restore_step_assign(saved, targets)
            return False
        GLib.timeout_add(self.POLL_INTERVAL_MS, self._restore_step_poll, saved, targets,
                          elapsed + self.POLL_INTERVAL_MS)
        return False

    def _restore_step_assign(self, saved: list[dict], targets: dict[str, int]):
        self._close_excess(targets)
        self._restore_pairs = self._assign_workspaces(saved)
        GLib.timeout_add(100, self._restore_geometry_and_canvas)

    def _restore_geometry_and_canvas(self) -> bool:
        self._sync_canvas_state_dir(self._canvas_ws)

        float_cmds: list[str] = []
        geo_cmds: list[str] = []

        for saved_win, cur_win in self._restore_pairs:
            addr = cur_win.get("address")
            if not addr:
                continue

            was_floating = saved_win.get("floating", False)
            if was_floating != cur_win.get("floating", False):
                float_cmds.append(
                    f'hl.dsp.window.float({{ window = "address:{addr}", action = "toggle" }})'
                )

            if was_floating:
                w, h = saved_win.get("size", [0, 0])
                x, y = saved_win.get("at", [0, 0])
                if w > 0 and h > 0:
                    geo_cmds.append(
                        f'hl.dsp.window.resize({{ window = "address:{addr}", '
                        f'x = {int(w)}, y = {int(h)}, relative = false }})'
                    )
                    geo_cmds.append(
                        f'hl.dsp.window.move({{ window = "address:{addr}", '
                        f'x = {int(x)}, y = {int(y)}, relative = false }})'
                    )

        if float_cmds:
            _hypr_batch(float_cmds)

        if geo_cmds:
            GLib.timeout_add(200, self._apply_geo_cmds, geo_cmds)
        else:
            self._restoring = False

        return False

    def _apply_geo_cmds(self, geo_cmds: list[str]) -> bool:
        _hypr_batch(geo_cmds)
        self._restoring = False
        return False

    # ------------------------------------------------------------------
    # Внутренние операции восстановления
    # ------------------------------------------------------------------

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
                addr = win.get("address")
                if addr and _hypr_dispatch(
                    f'hl.dsp.window.close({{ window = "address:{addr}" }})'
                ):
                    closed += 1
        return closed

    def _launch_missing(self, saved: list[dict], targets: dict[str, int]) -> int:
        cur = self._by_class()
        opened: dict[str, int] = defaultdict(int)
        total = 0
        for w in sorted(saved, key=lambda x: x.get("workspace", 0)):
            cls = (w.get("wm_class") or "").lower()
            if not cls:
                continue
            have = len(cur.get(cls, [])) + opened[cls]
            if have >= targets.get(cls, 0):
                continue
            if self._launch_one(w):
                opened[cls] += 1
                total += 1
        return total

    def _launch_one(self, w: dict) -> bool:
        wm = w.get("wm_class", "")
        project = w.get("project", "")
        ws = w.get("workspace", 0)
        title = w.get("title", "")
        cmd = w.get("launch_cmd", "")
        is_term = w.get("is_terminal", False)
        desktop_id = w.get("desktop_id", "")

        if project and not os.path.isdir(project):
            project = self._project_from_title(title)
        has_project = bool(project and os.path.isdir(project))

        if not cmd or not _cmd_standalone_ok(cmd) or not desktop_id:
            app = self._resolver.find(wm) if self._resolver else None
            if app:
                desktop_id = desktop_id or AppResolver.get_desktop_id(app)
                if not cmd or not _cmd_standalone_ok(cmd):
                    alt = AppResolver.get_command(app)
                    if _cmd_standalone_ok(alt):
                        cmd = alt

        rule = f'{{ workspace = "{int(ws)} silent" }}'

        if cmd and _cmd_standalone_ok(cmd):
            if has_project:
                launch = (
                    f'sh -c \'cd "{project}" && exec {cmd}\'' if is_term
                    else f'{cmd} "{project}"'
                )
            else:
                launch = cmd
            return _hypr_dispatch(f'hl.dsp.exec_cmd({json.dumps(launch)}, {rule})')

        gtk_name = self._gtk_launch_name(desktop_id, wm)
        if gtk_name and _has_gtk_launch():
            lua_cmd = json.dumps(f"gtk-launch {gtk_name}")
            return _hypr_dispatch(f'hl.dsp.exec_cmd({lua_cmd}, {rule})')

        app = self._resolver.find(wm) if self._resolver else None
        if app:
            app.launch()
            return True

        for binary in AppResolver._binary_candidates(wm):
            if GLib.find_program_in_path(binary):
                return _hypr_dispatch(f'hl.dsp.exec_cmd({json.dumps(binary)}, {rule})')
        return False

    @staticmethod
    def _gtk_launch_name(desktop_id: str, wm_class: str) -> str:
        if desktop_id:
            return os.path.splitext(os.path.basename(desktop_id))[0]
        if wm_class:
            lo = wm_class.lower()
            return lo.replace(" ", "-") or lo.replace(" ", "") or lo
        return ""

    def _assign_workspaces(self, saved: list[dict]) -> list[tuple[dict, dict]]:
        cur = self._by_class()
        saved_by: dict[str, list[dict]] = defaultdict(list)
        for w in saved:
            if cls := (w.get("wm_class") or "").lower():
                saved_by[cls].append(w)

        cmds: list[str] = []
        all_pairs: list[tuple[dict, dict]] = []

        for cls, slist in saved_by.items():
            clist = cur.get(cls, [])
            if not clist:
                continue

            pairs = _match_windows(slist, clist)
            all_pairs.extend(pairs)

            for sw, cw in pairs:
                addr = cw.get("address")
                ws = sw.get("workspace", 0)
                cur_ws = cw.get("workspace", {}).get("id")
                if addr and int(ws) != cur_ws:
                    cmds.append(
                        f'hl.dsp.window.move({{ window = "address:{addr}", '
                        f'workspace = {int(ws)} }})'
                    )

        if cmds:
            _hypr_batch(cmds)
        return all_pairs


# ---------------------------------------------------------------------------
# Матчинг окон
# ---------------------------------------------------------------------------

def _match_windows(saved: list[dict], current: list[dict]) -> list[tuple[dict, dict]]:
    if not saved or not current:
        return []
    if len(saved) == 1:
        return [(saved[0], current[0])]

    limit = min(len(saved), len(current))
    scores: list[tuple[float, int, int]] = []
    for si, sw in enumerate(saved):
        st, sp = sw.get("title", ""), sw.get("project", "")
        for ci, cw in enumerate(current):
            ct = cw.get("title", "")
            score = _title_similarity(st, ct)
            if sp and sp in ct:
                score += 0.3
            scores.append((score, si, ci))

    scores.sort(key=lambda t: (-t[0], t[1], t[2]))
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