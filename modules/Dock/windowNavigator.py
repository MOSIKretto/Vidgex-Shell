import os
import random
import time
from fabric.utils import exec_shell_command

from modules.Dock.Desktop.infinite_desktop import st, lock

_ORDER_FILE = os.path.expanduser("~/.cache/vidgex-shell/matrix_order")


def _read_matrix_order() -> int:
    with open(_ORDER_FILE, "r") as f:
        val = int(f.read().strip())
        return max(1, min(9, val))


class WindowNavigator:
    ANIM_DELAY = 0.15
    TICK_DELAY = 0.05

    def __init__(self, conn, parse_fn):
        self.conn = conn
        self._parse = parse_fn

    def cycle_and_focus(self, insts: list):
        if not insts:
            return

        aw = self._parse("j/activewindow")
        focused = aw.get("address")

        idx = next(
            (i for i, x in enumerate(insts) if x["address"] == focused),
            -1,
        )
        target = insts[(idx + 1) % len(insts)]
        addr = target["address"]

        ws_info = target.get("workspace", {})
        ws_id = ws_info.get("id") if isinstance(ws_info, dict) else ws_info

        order = _read_matrix_order()
        max_ws = order * order

        if isinstance(ws_id, int) and 1 <= ws_id <= max_ws:
            self.switch_workspace(ws_id)

        with lock:
            st['last_nav_time'] = time.time()

        exec_shell_command(f"hyprctl dispatch 'hl.dsp.focus({{ window = \"address:{addr}\" }})'")

    def switch_workspace(self, target_ws: int):
        ws_data = self._parse("j/activeworkspace")
        active_ws = ws_data.get("id")

        if active_ws == target_ws:
            return

        order = _read_matrix_order()
        steps = self._build_path(active_ws, target_ws, order)

        for ws, direction in steps:
            if direction == "V":
                exec_shell_command(
                    "hyprctl eval '"
                    "hl.animation({ leaf = \"workspaces\", enabled = true, speed = 6, bezier = \"overshot\", style = \"slidevert\" }) "
                    "hl.animation({ leaf = \"workspacesIn\", enabled = true, speed = 6, bezier = \"overshot\", style = \"slidevert\" }) "
                    "hl.animation({ leaf = \"workspacesOut\", enabled = true, speed = 6, bezier = \"overshot\", style = \"slidevert\" })"
                    "'"
                )
                time.sleep(self.TICK_DELAY)
                exec_shell_command(f"hyprctl dispatch 'hl.dsp.focus({{ workspace = {ws} }})'")
                exec_shell_command(
                    "hyprctl eval '"
                    "hl.animation({ leaf = \"workspaces\", enabled = true, speed = 6, bezier = \"overshot\", style = \"slide\" }) "
                    "hl.animation({ leaf = \"workspacesIn\", enabled = true, speed = 6, bezier = \"overshot\", style = \"slide\" }) "
                    "hl.animation({ leaf = \"workspacesOut\", enabled = true, speed = 6, bezier = \"overshot\", style = \"slide\" })"
                    "'"
                )
            else:
                exec_shell_command(f"hyprctl dispatch 'hl.dsp.focus({{ workspace = {ws} }})'")

            time.sleep(self.ANIM_DELAY)

    @staticmethod
    def _build_path(start: int, end: int, order: int) -> list[tuple[int, str]]:
        order = max(1, order)
        c_r, c_c = divmod(start - 1, order)
        t_r, t_c = divmod(end - 1, order)
        steps = []
        r, c = c_r, c_c

        if random.choice([True, False]):
            step_c = 1 if t_c > c else -1
            while c != t_c:
                c += step_c
                steps.append((r * order + c + 1, "H"))
            step_r = 1 if t_r > r else -1
            while r != t_r:
                r += step_r
                steps.append((r * order + c + 1, "V"))
        else:
            step_r = 1 if t_r > r else -1
            while r != t_r:
                r += step_r
                steps.append((r * order + c + 1, "V"))
            step_c = 1 if t_c > c else -1
            while c != t_c:
                c += step_c
                steps.append((r * order + c + 1, "H"))

        return steps