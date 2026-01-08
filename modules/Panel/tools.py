from fabric.utils.helpers import exec_shell_command_async, get_relative_path
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label

from gi.repository import GLib, Gdk

import modules.icons as icons

import subprocess

# Константы
SCREENSHOT_SCRIPT = get_relative_path("../scripts/screenshot.sh")
OCR_SCRIPT = get_relative_path("../scripts/ocr.sh")
GAMEMODE_SCRIPT = get_relative_path("../scripts/gamemode.sh")
SCREENRECORD_SCRIPT = get_relative_path("../scripts/screenrecord.sh")
HYPICKER_SCRIPT = get_relative_path("../scripts/hyprpicker.sh")

TOOLTIPS = {
    "ssregion": "<b>Скриншот области</b>",
    "ssfull": "<b>Скриншот</b>",
    "sswindow": "<b>Скриншот окна</b>",
    "screenrecord": "<b>Запись экрана</b>",
    "ocr": "<b>OCR (распознавание текста)</b>",
    "colorpicker": """<b>Пипетка</b>""",
    "gamemode": "<b>Игровой режим</b>"
}

class Toolbox(Box):
    def __init__(self, **kwargs):
        super().__init__(name="toolbox", spacing=4, visible=True, **kwargs)
        
        self.notch = kwargs["notch"]
        self._is_active = False
        self._timers = []
        self._current_focus_index = 0
        self._buttons_list = []
        
        self._setup_buttons()
        self._setup_keyboard_controls()
        self.show_all()
        
        self.connect("map-event", self._on_show)
        self.connect("unmap-event", self._on_hide)

    def _on_show(self, widget=None, event=None):
        if not self._is_active:
            self._is_active = True
            self._start_timers()
            self._set_initial_focus()

    def _on_hide(self, widget=None, event=None):
        if self._is_active:
            self._is_active = False
            self._stop_timers()

    def _setup_buttons(self):
        button_configs = [
            ("ssregion", icons.ssregion, lambda: self._run_script(SCREENSHOT_SCRIPT, 's')),
            ("sswindow", icons.sswindow, lambda: self._run_script(SCREENSHOT_SCRIPT, 'w')),
            ("ssfull", icons.ssfull, lambda: self._run_script(SCREENSHOT_SCRIPT, 'p')),
            ("screenrecord", icons.screenrecord, lambda: self._run_script(SCREENRECORD_SCRIPT)),
            ("ocr", icons.ocr, lambda: self._run_script(OCR_SCRIPT, 's')),
            ("gamemode", icons.gamemode, lambda: self._run_script(GAMEMODE_SCRIPT)),
        ]
        
        self.btn_ssregion = self._create_button(*button_configs[0])
        self.btn_sswindow = self._create_button(*button_configs[1])
        self.btn_ssfull = self._create_button(*button_configs[2])
        self.btn_screenrecord = self._create_button(*button_configs[3])
        self.btn_ocr = self._create_button(*button_configs[4])
        self.btn_gamemode = self._create_button(*button_configs[5])
        
        self.btn_color = self._create_button("colorpicker", icons.colorpicker, None)
        self.btn_color.connect("button-press-event", lambda b, e: self._run_script(HYPICKER_SCRIPT, '-hex'))
        
        self._buttons_list = [
            self.btn_ssregion, self.btn_sswindow, self.btn_ssfull,
            self.btn_screenrecord, self.btn_ocr, self.btn_color, self.btn_gamemode,
        ]
        
        self._arrange_buttons()

    def _create_button(self, tooltip_key, icon, clicked_callback):
        button = Button(
            name="toolbox-button",
            tooltip_markup=TOOLTIPS.get(tooltip_key, ""),
            child=Label(name="button-label", markup=icon),
        )
        
        if clicked_callback:
            button.connect("clicked", clicked_callback)
        
        button.set_can_focus(True)
        return button

    def _arrange_buttons(self):
        widgets = [
            self.btn_ssregion, self.btn_sswindow, self.btn_ssfull, self.btn_screenrecord,
            Box(name="tool-sep", h_expand=False, v_expand=False),
            self.btn_ocr, self.btn_color,
            Box(name="tool-sep", h_expand=False, v_expand=False),
            self.btn_gamemode,
        ]
        
        for widget in widgets:
            self.add(widget)

    def _setup_keyboard_controls(self):
        self.set_can_focus(True)
        self.connect("key-press-event", self._on_key_press)
        self.connect("button-press-event", lambda w, e: self.grab_focus())

    def _on_key_press(self, widget, event):
        if not self._buttons_list:
            return False
        
        keyval = event.keyval
        key_actions = {
            Gdk.KEY_Left: -1, Gdk.KEY_KP_Left: -1, Gdk.KEY_Up: -1,
            Gdk.KEY_Right: 1, Gdk.KEY_KP_Right: 1, Gdk.KEY_Down: 1,
            Gdk.KEY_KP_Up: -1, Gdk.KEY_KP_Down: 1,
        }
        
        if keyval in key_actions:
            self._navigate_focus(key_actions[keyval])
            return True
        
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self._buttons_list[self._current_focus_index].emit("clicked")
            return True
        
        if keyval == Gdk.KEY_Escape:
            self.close_menu()
            return True
        
        return False

    def _navigate_focus(self, direction):
        new_index = self._current_focus_index + direction
        
        if new_index < 0:
            new_index = len(self._buttons_list) - 1
        elif new_index >= len(self._buttons_list):
            new_index = 0
        
        self._buttons_list[self._current_focus_index].set_property("has-focus", False)
        self._buttons_list[new_index].grab_focus()
        self._current_focus_index = new_index

    def _set_initial_focus(self):
        if self._buttons_list:
            self._buttons_list[0].grab_focus()
            self._current_focus_index = 0

    def _run_script(self, script_path, *args):
        self.close_menu()
        
        def _execute_script():
            command = f"bash {script_path}"
            if args:
                command += " " + " ".join(str(arg) for arg in args)
            exec_shell_command_async(command)
            return False
        
        timer_id = GLib.timeout_add(900, _execute_script)
        if timer_id not in self._timers:
            self._timers.append(timer_id)

    def _update_screenrecord_state(self):
        if not self._is_active:
            return True
        
        from modules.common import check_process_running
        running = check_process_running("gpu-screen-recorder")
        
        icon = icons.stop if running else icons.screenrecord
        self.btn_screenrecord.get_child().set_markup(icon)
        
        if running:
            self.btn_screenrecord.add_style_class("recording")
        else:
            self.btn_screenrecord.remove_style_class("recording")
        
        return self._is_active

    def _update_gamemode_state(self):
        if not self._is_active:
            return True
        
        result = subprocess.run(
            ["bash", GAMEMODE_SCRIPT, "check"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )
        
        enabled = result.stdout == b't\n'
        icon = icons.gamemode_off if enabled else icons.gamemode
        self.btn_gamemode.get_child().set_markup(icon)
        
        return self._is_active

    def _start_timers(self):
        self._stop_timers()
        
        self._timers.append(GLib.timeout_add_seconds(10, self._update_screenrecord_state))
        self._timers.append(GLib.timeout_add_seconds(15, self._update_gamemode_state))

    def _stop_timers(self):
        for timer_id in self._timers:
            GLib.source_remove(timer_id)
        self._timers = []

    def close_menu(self):
        if hasattr(self, 'notch') and self.notch:
            self.notch.close_notch()

    def destroy(self):
        self._stop_timers()
        super().destroy()