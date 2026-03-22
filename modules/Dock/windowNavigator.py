import random

from gi.repository import GLib
from fabric.utils import exec_shell_command


class WindowNavigator:
    ANIM_TIME_MS = 200

    def __init__(self, conn, parse_fn):
        self.conn = conn
        self._parse = parse_fn

    def cycle_and_focus(self, insts: list):
        if not insts:
            return

        aw = self._parse("j/activewindow")
        focused = aw.get("address", "") if aw else ""

        idx = next(
            (i for i, x in enumerate(insts) if x["address"] == focused),
            -1,
        )
        target = insts[(idx + 1) % len(insts)]
        addr = target["address"]

        ws_info = target.get("workspace", {})
        ws_id = ws_info.get("id") if isinstance(ws_info, dict) else ws_info

        if isinstance(ws_id, int) and 1 <= ws_id <= 9:
            self.switch_workspace(
                ws_id, on_complete=lambda: exec_shell_command(f"hyprctl dispatch focuswindow address:{addr}")
            )
        else:
            exec_shell_command(f"hyprctl dispatch focuswindow address:{addr}")

    def switch_workspace(self, target_ws: int, on_complete=None):
        try:
            ws_data = self._parse("j/activeworkspace")
            active_ws = ws_data.get("id", 1) if ws_data else 1
        except Exception:
            active_ws = 1

        if not (1 <= active_ws <= 9):
            active_ws = 5

        if active_ws == target_ws:
            if on_complete:
                on_complete()
            return

        steps = self._build_path(active_ws, target_ws)
        self._run_steps(steps, on_complete)

    @staticmethod
    def _build_path(start: int, end: int) -> list[tuple[int, bool]]:
        r, c = divmod(start - 1, 3)
        tr, tc = divmod(end - 1, 3)

        steps: list[tuple[int, bool]] = []
        horiz_first = random.choice([True, False])

        def h():
            nonlocal c
            d = 1 if tc > c else -1
            while c != tc:
                c += d
                steps.append((r * 3 + c + 1, False))

        def v():
            nonlocal r
            d = 1 if tr > r else -1
            while r != tr:
                r += d
                steps.append((r * 3 + c + 1, True))

        if horiz_first:
            h(); v()
        else:
            v(); h()

        return steps

    def _run_steps(self, steps: list, on_complete):
        if not steps:
            if on_complete:
                on_complete()
            return

        ws, is_vert = steps.pop(0)
        if is_vert:
            cmd = (
                f"[[BATCH]] keyword animation "
                f"workspaces,1,6,overshot,slidevert ; "
                f"dispatch workspace {ws} ; "
                f"keyword animation workspaces,1,6,overshot,slide"
            )
            self.conn.send_command(cmd)
        else:
            self.conn.send_command(f"dispatch workspace {ws}")

        GLib.timeout_add(
            self.ANIM_TIME_MS,
            lambda s=steps, cb=on_complete: self._run_steps(s, cb) or False,
        )