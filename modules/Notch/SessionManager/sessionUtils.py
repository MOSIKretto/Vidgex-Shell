import json
import os
import re
import shlex
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Optional


SESSION_DIR = Path.home() / ".cache" / "vidgex-shell"
SESSION_FILE = SESSION_DIR / "session.json"
EXCLUSIONS_FILE = SESSION_DIR / "exclusions.json"


def normalize_str(s: str) -> str:
    return re.sub(r'[^a-z0-9]', '', s.lower())


def hyprctl(cmd: str) -> Optional[dict | list]:
    try:
        args = shlex.split(cmd)
        r = subprocess.run(
            ["hyprctl", "-j"] + args,
            capture_output=True, text=True, timeout=2
        )
        return json.loads(r.stdout) if r.returncode == 0 else None
    except:
        return None


def dispatch(cmd: str):
    subprocess.run(
        ["hyprctl", "dispatch", "--", cmd],
        capture_output=True, timeout=2
    )


def batch_dispatch(commands: list[str]):
    if commands:
        subprocess.run(
            ["hyprctl", "--batch", ";".join(f"dispatch {c}" for c in commands)],
            capture_output=True, timeout=5
        )


def is_user_path(path: str) -> bool:
    home = str(Path.home())
    if not path.startswith(home):
        return False
    relative = path[len(home):]
    excluded = ('/.cache/', '/.local/share/', '/.config/')
    return not any(ex in relative for ex in excluded)


def get_current_terminal_pid() -> Optional[int]:
    try:
        stat = Path(f"/proc/{os.getppid()}/stat").read_text()
        pid = int(stat.split()[3])
        return None if pid <= 1 else pid
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


def close_excess_windows(
    target_counts: dict[str, int],
    is_excluded_fn,
    protect_pid: Optional[int] = None
) -> int:
    current = get_windows_by_class()
    closed = 0
    for cls, wins in current.items():
        if is_excluded_fn(cls):
            continue
        excess = len(wins) - target_counts.get(cls, 0)
        if excess > 0:
            for win in wins[-excess:]:
                pid = win.get("pid", 0)
                if pid == protect_pid or pid <= 1:
                    continue
                if addr := win.get("address", ""):
                    dispatch(f"closewindow address:{addr}")
                    closed += 1
    return closed