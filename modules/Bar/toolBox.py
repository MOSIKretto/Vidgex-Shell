import builtins
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer
from fabric.utils.helpers import exec_shell_command_async

from services.wayland import WaylandWindow as Window
import services.icons as icons


# --- Вспомогательные сервисные функции ---

def _get_pictures_dir() -> Path:
    xdg = os.environ.get("XDG_PICTURES_DIR")
    return Path(xdg) if xdg else Path.home() / "Pictures"


def _get_videos_dir() -> Path:
    xdg = os.environ.get("XDG_VIDEOS_DIR")
    return Path(xdg) if xdg else Path.home() / "Videos"


def _send_notification(summary: str, body: str = "", icon: str | Path = None, actions: list[tuple[str, str]] = None) -> str:
    """Отправляет уведомление через notify-send и возвращает выбранное действие (если есть)."""
    cmd = ["notify-send", "-a", "Vidgex-Shell", summary]
    if body:
        cmd.append(body)
    if icon:
        cmd.extend(["-i", str(icon)])
    if actions:
        for act_id, act_label in actions:
            cmd.extend(["-A", f"{act_id}={act_label}"])

    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.stdout.strip()


def _copy_to_clipboard(file_path: Path):
    """Копирует изображение в буфер обмена (wl-copy или xclip)."""
    if shutil.which("wl-copy"):
        with open(file_path, "rb") as f:
            subprocess.run(["wl-copy"], stdin=f)
    elif shutil.which("xclip"):
        subprocess.run(["xclip", "-selection", "clipboard", "-t", "image/png", str(file_path)])


def _run_ocr():
    """Распознавание текста (OCR через hyprshot и tesseract)."""
    time.sleep(0.5)
    try:
        p1 = subprocess.Popen(
            ["hyprshot", "-m", "region", "-z", "-r", "-s"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )
        p2 = subprocess.Popen(
            ["tesseract", "-l", "eng+rus", "-", "-"],
            stdin=p1.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )
        p1.stdout.close()
        out, _ = p2.communicate()
        text = out.decode("utf-8", errors="ignore").strip()

        if text:
            if shutil.which("wl-copy"):
                subprocess.run(["wl-copy"], input=text.encode("utf-8"))
            _send_notification("OCR Success", "Text Copied to Clipboard")
        else:
            _send_notification("OCR Failed", "No text recognized or operation failed")
    except Exception as e:
        _send_notification("OCR Error", str(e))


def _run_recording():
    """Запись экрана через gpu-screen-recorder."""
    save_dir = _get_videos_dir() / "Recordings"
    save_dir.mkdir(parents=True, exist_ok=True)

    is_running = subprocess.run(["pgrep", "-f", "gpu-screen-recorder"], capture_output=True).returncode == 0

    if is_running:
        subprocess.run(["pkill", "-SIGINT", "-f", "gpu-screen-recorder"])
        time.sleep(1)

        mp4_files = list(save_dir.glob("*.mp4"))
        last_video = max(mp4_files, key=lambda f: f.stat().st_mtime, default=None)

        action = _send_notification(
            "⬜ Recording stopped",
            actions=[("view", "View"), ("open", "Open folder")]
        )

        if action == "view" and last_video:
            subprocess.run(["xdg-open", str(last_video)])
        elif action == "open":
            subprocess.run(["xdg-open", str(save_dir)])
    else:
        output_file = save_dir / f"{datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}.mp4"
        _send_notification("🔴 Recording started")
        subprocess.Popen([
            "gpu-screen-recorder",
            "-w", "screen",
            "-q", "ultra",
            "-a", "default_output",
            "-ac", "opus",
            "-cr", "full",
            "-f", "60",
            "-o", str(output_file)
        ])


def _wait_for_file(file_path: Path, timeout: float = 6.0) -> bool:
    count = 0
    max_steps = int(timeout / 0.2)
    while count < max_steps:
        if file_path.is_file() and file_path.stat().st_size > 0:
            lsof = subprocess.run(["lsof", str(file_path)], capture_output=True)
            if lsof.returncode != 0:
                time.sleep(0.2)
                return True
        time.sleep(0.2)
        count += 1
    return False


def _find_screenshot_fallback(save_dir: Path, expected: Path) -> Path | None:
    if expected.is_file() and expected.stat().st_size > 0:
        return expected

    now = time.time()
    png_files = list(save_dir.glob("*.png"))
    if not png_files:
        return None

    newest = max(png_files, key=lambda f: f.stat().st_mtime)
    if (now - newest.stat().st_mtime) <= 60 and newest.stat().st_size > 0:
        return newest
    return None


def _apply_mockup(full_path: Path):
    """Скругление углов и тень с помощью ImageMagick."""
    temp_file = full_path.with_name(f"{full_path.stem}_temp.png")
    mockup_file = full_path.with_name(f"{full_path.stem}_mockup.png")

    cmd1 = [
        "magick", str(full_path),
        "(", "+clone", "-alpha", "extract", "-draw", "fill black polygon 0,0 0,20 20,0 fill white circle 20,20 20,0",
        "(", "+clone", "-flip", ")", "-compose", "Multiply", "-composite",
        "(", "+clone", "-flop", ")", "-compose", "Multiply", "-composite",
        ")", "-alpha", "off", "-compose", "CopyOpacity", "-composite", str(temp_file)
    ]
    cmd2 = [
        "magick", str(temp_file),
        "(", "+clone", "-background", "black", "-shadow", "60x20+0+10", "-alpha", "set", "-channel", "A", "-evaluate", "multiply", "1", "+channel", ")",
        "+swap", "-background", "none", "-layers", "merge", "+repage", str(mockup_file)
    ]

    try:
        if subprocess.run(cmd1, capture_output=True).returncode == 0:
            if subprocess.run(cmd2, capture_output=True).returncode == 0 and mockup_file.is_file():
                mockup_file.replace(full_path)
    except Exception:
        pass
    finally:
        temp_file.unlink(missing_ok=True)
        mockup_file.unlink(missing_ok=True)


def _run_screenshot(mode: str, mockup: bool = False):
    """Снятие скриншота (hyprshot в тихом режиме -s)."""
    time.sleep(0.5)

    save_dir = _get_pictures_dir() / "Screenshots"
    save_dir.mkdir(parents=True, exist_ok=True)

    save_file = f"{datetime.now().strftime('%y%m%d_%Hh%Mm%Ss')}_screenshot.png"
    full_path = save_dir / save_file

    # Флаг "-s" отключает встроенные уведомления hyprshot
    cmd = ["hyprshot", "-s"]
    if mode == "p":
        cmd += ["-z", "-m", "output", "-o", str(save_dir), "-f", save_file]
    elif mode == "s":
        cmd += ["-z", "-m", "region", "-o", str(save_dir), "-f", save_file]
    elif mode == "w":
        time.sleep(0.1)
        cmd += ["-m", "window", "-o", str(save_dir), "-f", save_file]
    else:
        return

    res = subprocess.run(cmd)
    time.sleep(0.5)

    actual_file = None
    if _wait_for_file(full_path):
        actual_file = full_path
    else:
        actual_file = _find_screenshot_fallback(save_dir, full_path)

    if not actual_file or not actual_file.is_file():
        if res.returncode != 0:
            _send_notification("Screenshot Aborted", "Cancelled by user")
        else:
            _send_notification("Screenshot Failed", f"File was not created (exit: {res.returncode})")
        return

    full_path = actual_file

    if mockup:
        _apply_mockup(full_path)

    _copy_to_clipboard(full_path)

    # Уведомление с действиями View и Open Folder (без Edit/swappy)
    action = _send_notification(
        "Screenshot saved",
        str(full_path),
        icon=full_path,
        actions=[("view", "View"), ("open", "Open Folder")]
    )

    if action == "view":
        subprocess.run(["xdg-open", str(full_path)])
    elif action == "open":
        subprocess.run(["xdg-open", str(full_path.parent)])


# --- Основной класс панели ---

class ToolBox(Window):
    _MENU_ITEMS = (
        (icons.ssregion,     ("screenshot", "s"), "<b>Screenshot of screen area</b>", 0),
        (icons.sswindow,     ("screenshot", "w"), "<b>Window screenshot</b>",         0),
        (icons.ssfull,       ("screenshot", "p"), "<b>Screenshot</b>",                0),
        (icons.ocr,          ("ocr",),            "<b>OCR</b>",                       0),
        (icons.screenrecord, ("record",),         "<b>Screen Recording</b>",          1),
    )

    REVEAL_STEP_DELAY = 45
    CLOSE_HIDE_DELAY = 250
    REVEALER_DURATION = 200

    MARGIN_RIGHT_OFFSET = 8
    MARGIN_TOP_OFFSET = 50
    BUTTON_SPACING = 4

    CSS_BTN = "toolbox-icon-btn"
    CSS_ACTIVE = "power-active"

    WINDOW_NAME = "toolbox-menu-window"
    LAYER_RULE_CMD = 'hyprctl keyword layerrule "noanim, {}"'

    def __init__(self, monitor=0, **kwargs):
        super().__init__(
            exclusivity="none",
            layer="top",
            monitor=monitor,
            keyboard_mode="none",
        )
        builtins.toolbox = self

        self.set_name(self.WINDOW_NAME)
        self.anchor = "top right"
        self.margin = "0px 0px 0px 0px"

        self._setup_transparency()
        self._init_state()
        self._build_ui()

        self.set_visible(False)
        self._disable_animations()

    def _setup_transparency(self):
        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_app_paintable(True)
            self.set_visual(visual)

    def _init_state(self):
        self._open_flag = False
        self._close_timer = None
        self._reveal_timer = None

        self._trigger_btn = None
        self._trigger_sig = None

        self._cursors = {"hand": None, "default": None}

        self._btns = []
        self._revealers = []
        self._dyn = {}

    def _disable_animations(self):
        cmd = self.LAYER_RULE_CMD.format(self.WINDOW_NAME)
        exec_shell_command_async(cmd)

    def _ensure_cursors(self):
        if self._cursors["hand"] is None:
            display = self.get_display()
            self._cursors["hand"] = Gdk.Cursor.new_from_name(display, "pointer")
            self._cursors["default"] = Gdk.Cursor.new_from_name(display, "default")

    def _set_window_cursor(self, cursor_name):
        self._ensure_cursors()
        cursor = self._cursors.get(cursor_name)
        if cursor:
            toplevel = self.get_toplevel()
            if toplevel:
                window = toplevel.get_window()
                if window:
                    window.set_cursor(cursor)

    def _on_btn_enter(self, widget, event):
        if event.detail != Gdk.NotifyType.INFERIOR:
            self._set_window_cursor("hand")
        return False

    def _on_btn_leave(self, widget, event):
        if event.detail != Gdk.NotifyType.INFERIOR:
            self._set_window_cursor("default")
        return False

    def _create_action_button(self, icon_markup, action_tuple, tooltip, action_type):
        lbl = Label(markup=icon_markup)
        btn = Button(
            child=lbl,
            tooltip_markup=tooltip,
            can_focus=False,
        )
        btn.get_style_context().add_class(self.CSS_BTN)
        btn.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        btn.connect("enter-notify-event", self._on_btn_enter)
        btn.connect("leave-notify-event", self._on_btn_leave)
        btn.connect("clicked", self._on_action_clicked, action_tuple, action_type)

        if action_type == 1:
            self._dyn["record"] = lbl
            self._dyn["record_btn"] = btn

        revealer = Revealer(
            transition_type="slide-down",
            child_revealed=False,
            child=btn,
        )
        revealer.set_transition_duration(self.REVEALER_DURATION)

        return btn, revealer

    def _build_ui(self):
        icons_box = Box(
            name="toolbox-menu-icons",
            orientation="v",
            spacing=self.BUTTON_SPACING
        )

        for icon_markup, action_tuple, tooltip, action_type in self._MENU_ITEMS:
            btn, revealer = self._create_action_button(icon_markup, action_tuple, tooltip, action_type)
            self._btns.append(btn)
            self._revealers.append(revealer)
            icons_box.add(revealer)

        self._wrapper = Box(
            name="toolbox-menu-wrapper",
            orientation="v",
            children=[icons_box],
        )
        self.add(self._wrapper)

    def set_trigger_button(self, btn):
        self._disconnect_trigger()
        self._trigger_btn = btn
        self._trigger_sig = btn.connect("size-allocate", self._on_trigger_size_allocate)

    def _disconnect_trigger(self):
        if self._trigger_btn and self._trigger_sig:
            self._trigger_btn.disconnect(self._trigger_sig)
            self._trigger_sig = None

    def _on_trigger_size_allocate(self, widget, allocation):
        width, height = allocation.width, allocation.height
        if width <= 0 or height <= 0:
            return

        self._update_button_sizes(width, height)
        self._update_wrapper_size(width, height)
        self._update_window_position(widget, width, height)

    def _update_button_sizes(self, width, height):
        for btn in self._btns:
            btn.set_size_request(width, height)

    def _update_wrapper_size(self, width, height):
        num_actions = len(self._MENU_ITEMS)
        total_spacing = self.BUTTON_SPACING * (num_actions - 1)
        total_height = height * num_actions + total_spacing
        self._wrapper.set_size_request(width, total_height)

    def _update_window_position(self, widget, width, height):
        toplevel = widget.get_toplevel()
        if not toplevel:
            return

        coords = widget.translate_coordinates(toplevel, 0, 0)
        if coords is None:
            return

        x, y = coords
        toplevel_width = toplevel.get_allocated_width()

        margin_right = max(0, toplevel_width - x - width - self.MARGIN_RIGHT_OFFSET)
        margin_top = max(0, y + height + self.BUTTON_SPACING - self.MARGIN_TOP_OFFSET)

        self.margin = f"{margin_top}px {margin_right}px 0px 0px"

    def open(self):
        if self._open_flag:
            return

        self._cancel_timers()
        self._open_flag = True
        self._set_trigger_active_state(True)
        self._refresh_dyn()

        self._reset_revealers()
        self.set_visible(True)
        self.show_all()
        self._start_reveal_animation()

    def _reset_revealers(self):
        for revealer in self._revealers:
            revealer.set_reveal_child(False)

    def _start_reveal_animation(self):
        self._reveal_step(0)

    def _reveal_step(self, index):
        self._reveal_timer = None

        if not self._open_flag or index >= len(self._revealers):
            return False

        self._revealers[index].set_reveal_child(True)

        if index + 1 < len(self._revealers):
            self._reveal_timer = GLib.timeout_add(
                self.REVEAL_STEP_DELAY,
                self._reveal_step,
                index + 1
            )

        return False

    def close(self):
        if not self._open_flag:
            return

        self._cancel_timers()
        self._open_flag = False
        self._set_trigger_active_state(False)

        for revealer in self._revealers:
            revealer.set_reveal_child(False)

        self._close_timer = GLib.timeout_add(self.CLOSE_HIDE_DELAY, self._hide_window)

    def _hide_window(self):
        self._close_timer = None
        if not self._open_flag:
            self.set_visible(False)
        return False

    def _cancel_timers(self):
        if self._close_timer is not None:
            GLib.source_remove(self._close_timer)
            self._close_timer = None

        if self._reveal_timer is not None:
            GLib.source_remove(self._reveal_timer)
            self._reveal_timer = None

    def _set_trigger_active_state(self, active):
        if not self._trigger_btn:
            return

        style_context = self._trigger_btn.get_style_context()
        if active:
            style_context.add_class(self.CSS_ACTIVE)
        else:
            style_context.remove_class(self.CSS_ACTIVE)

    def toggle_camera(self):
        if self._open_flag:
            self.close()
        else:
            self.open()

    def is_open(self):
        return self._open_flag

    def _refresh_dyn(self, *_):
        if "record" in self._dyn:
            is_rec = subprocess.run(["pgrep", "-f", "gpu-screen-recorder"], capture_output=True).returncode == 0

            lbl = self._dyn["record"]
            lbl.set_markup(icons.stop if is_rec else icons.screenrecord)

            btn = self._dyn.get("record_btn")
            if btn:
                ctx = btn.get_style_context()
                if is_rec:
                    ctx.add_class(self.CSS_ACTIVE)
                    ctx.add_class("active")
                else:
                    ctx.remove_class(self.CSS_ACTIVE)
                    ctx.remove_class("active")
        return False

    def _dispatch_action(self, action_type_name: str, *args):
        """Запуск действия в фоновом потоке."""
        if action_type_name == "ocr":
            threading.Thread(target=_run_ocr, daemon=True).start()
        elif action_type_name == "record":
            threading.Thread(target=_run_recording, daemon=True).start()
        elif action_type_name == "screenshot":
            mode = args[0] if args else "p"
            threading.Thread(target=_run_screenshot, args=(mode,), daemon=True).start()

    def _on_action_clicked(self, button, action_tuple, action_type):
        action_name = action_tuple[0]
        args = action_tuple[1:]

        self._dispatch_action(action_name, *args)

        if action_type == 0:
            self.close()
        else:
            GLib.timeout_add(100, self._refresh_dyn)
            GLib.timeout_add(600, self._refresh_dyn)
            GLib.timeout_add(1200, self._refresh_dyn)

    def cleanup(self):
        self._cancel_timers()
        self._set_trigger_active_state(False)

        if self._open_flag:
            self._open_flag = False
            self.set_visible(False)

        self._disconnect_trigger()
        self._trigger_btn = None