import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, ClassVar

from .sessionUtils import normalize_str


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

        try:
            info.comm = (proc_path / "comm").read_text().strip()
        except:
            pass

        try:
            info.exe = os.readlink(proc_path / "exe")
        except:
            pass

        try:
            cmdline_raw = (proc_path / "cmdline").read_bytes()
            info.cmdline_args = [
                arg for arg in cmdline_raw.decode(errors='ignore').split('\x00') if arg
            ]
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

    _cache: ClassVar[dict[str, "DesktopEntry"]] = {}

    @classmethod
    def find_by_class(cls, wm_class: str, proc: ProcessInfo) -> Optional["DesktopEntry"]:
        wm_lower = wm_class.lower()
        if wm_lower in cls._cache:
            return cls._cache[wm_lower]

        wm_norm = normalize_str(wm_class)
        exe_name = Path(proc.exe).name if proc.exe else ""

        data_dirs = os.environ.get(
            'XDG_DATA_DIRS', '/usr/share:/usr/local/share'
        ).split(':')
        data_dirs.append(str(Path.home() / ".local/share"))
        flatpak_dirs = [
            "/var/lib/flatpak/exports/share",
            str(Path.home() / ".local/share/flatpak/exports/share")
        ]
        all_dirs = [Path(d) / "applications" for d in data_dirs + flatpak_dirs]

        for d in all_dirs:
            if not d.exists():
                continue
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
    def _parse_desktop(
        cls, path: Path, target_norm: str, exe_name: str, comm: str
    ) -> Optional["DesktopEntry"]:
        try:
            text = path.read_text(errors='ignore')
        except:
            return None
        entry = cls()
        in_entry = match_found = False

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
                case 'Name':
                    if normalize_str(val) == target_norm:
                        match_found = True
                case 'Exec':
                    entry.exec_cmd = re.sub(r'%[a-zA-Z]', '', val).strip()
                    if 'flatpak run' in val:
                        entry.is_flatpak = True
                    val_lower = val.lower()
                    if (exe_name and exe_name in val_lower) or \
                       (comm and comm in val_lower):
                        match_found = True
                case 'StartupWMClass':
                    entry.wm_class = val
                    if normalize_str(val) == target_norm:
                        match_found = True
                case 'Categories':
                    entry.categories = set(
                        c.strip() for c in val.split(';') if c.strip()
                    )
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

        if 'TerminalEmulator' in entry.categories:
            entry.is_terminal = True
        return entry