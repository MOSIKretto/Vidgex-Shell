import random
import time
from fabric.utils import exec_shell_command

from modules.Dock.Desktop.infinite_desktop import st, lock


class WindowNavigator:
    ANIM_DELAY = 0.15
    TICK_DELAY = 0.05

    def __init__(self, conn, parse_fn):
        self.conn = conn
        self._parse = parse_fn

    def cycle_and_focus(self, insts: list):
        if not insts:
            return

        # 1. Получаем текущее активное окно
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

        # 2. Если окно на другом воркспейсе — плавно двигаем камеру по матрице
        if isinstance(ws_id, int) and 1 <= ws_id <= 9:
            self.switch_workspace(ws_id)
            
        # 3. ПОТОКОБЕЗОПАСНЫЙ ХАК ДЛЯ БЕСКОНЕЧНОГО ХОЛСТА:
        # Захватываем локальный мьютекс холста и обновляем время навигации.
        # Теперь hyprland_ipc_listener() увидит, что < 0.5с, высчитает dx/dy центра экрана 
        # до центра floating-окна и сдвинет холст пачкой hc_batch.
        with lock:
            st['last_nav_time'] = time.time()

        # 4. Передаем логический фокус ввода на целевое окно
        exec_shell_command(f"hyprctl dispatch 'hl.dsp.focus({{ window = \"address:{addr}\" }})'")

    def switch_workspace(self, target_ws: int):
        try:
            ws_data = self._parse("j/activeworkspace")
            active_ws = ws_data.get("id", 1) if ws_data else 1
        except Exception:
            active_ws = 5

        if not (1 <= active_ws <= 9):
            active_ws = 5

        if active_ws == target_ws:
            return

        steps = self._build_path(active_ws, target_ws)
        
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
    def _build_path(start: int, end: int) -> list[tuple[int, str]]:
        c_r, c_c = divmod(start - 1, 3)
        t_r, t_c = divmod(end - 1, 3)
        steps = []
        r, c = c_r, c_c

        if random.choice([True, False]):
            step_c = 1 if t_c > c else -1
            while c != t_c:
                c += step_c
                steps.append((r * 3 + c + 1, "H"))
            step_r = 1 if t_r > r else -1
            while r != t_r:
                r += step_r
                steps.append((r * 3 + c + 1, "V"))
        else:
            step_r = 1 if t_r > r else -1
            while r != t_r:
                r += step_r
                steps.append((r * 3 + c + 1, "V"))
            step_c = 1 if t_c > c else -1
            while c != t_c:
                c += step_c
                steps.append((r * 3 + c + 1, "H"))

        return steps
