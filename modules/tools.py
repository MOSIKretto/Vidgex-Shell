from fabric.utils.helpers import exec_shell_command_async, get_relative_path
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label

from gi.repository import GLib, Gdk

import subprocess

import modules.icons as icons

# Константы путей
SCREENSHOT_SCRIPT = get_relative_path("../scripts/screenshot.sh")
OCR_SCRIPT = get_relative_path("../scripts/ocr.sh")
GAMEMODE_SCRIPT = get_relative_path("../scripts/gamemode.sh")
SCREENRECORD_SCRIPT = get_relative_path("../scripts/screenrecord.sh")
HYPICKER_SCRIPT = get_relative_path("../scripts/hyprpicker.sh")

# Кэш для подсказок
TOOLTIPS = {
    "ssregion": """<b><u>Скриншот области</u></b>
<b>Левый клик:</b> Сделать скриншот выбранной области.
<b>Правый клик:</b> Сделать стилизованный скриншот выбранной области.""",
    "ssfull": """<b><u>Скриншот</u></b>
<b>Левый клик:</b> Сделать скриншот всего экрана.
<b>Правый клик:</b> Сделать стилизованный скриншот всего экрана.""",
    "sswindow": """<b><u>Скриншот окна</u></b>
<b>Левый клик:</b> Сделать скриншот активного окна.
<b>Правый клик:</b> Сделать стилизованный скриншот активного окна.""",
    "screenrecord": "<b>Запись экрана</b>",
    "ocr": "<b>OCR (распознавание текста)</b>",
    "colorpicker": """<b><u>Пипетка</u></b>
<b>Мышь:</b>
Левый клик: HEX
Средний клик: HSV
Правый клик: RGB""",
    "gamemode": "<b>Игровой режим</b>"
}

class Toolbox(Box):
    def __init__(self, **kwargs):
        super().__init__(
            name="toolbox",
            orientation="h",
            spacing=4,
            v_align="center",
            h_align="center",
            visible=True,
            **kwargs,
        )

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

    def _setup_keyboard_controls(self):
        self.set_can_focus(True)
        self.connect("key-press-event", self._on_key_press)
        
        def grab_focus_on_click(w, e):
            self.grab_focus()
        self.connect("button-press-event", grab_focus_on_click)

    def _setup_buttons(self):
        self.btn_ssregion = self._create_button(icons.ssregion, "ssregion", self._ssregion)
        self.btn_sswindow = self._create_button(icons.sswindow, "sswindow", self._sswindow)
        self.btn_ssfull = self._create_button(icons.ssfull, "ssfull", self._ssfull)
        
        self.btn_screenrecord = self._create_button(icons.screenrecord, "screenrecord", self._screenrecord)
        
        self.btn_ocr = self._create_button(icons.ocr, "ocr", self._ocr)
        
        self.btn_color = self._create_button(icons.colorpicker, "colorpicker")
        self.btn_color.connect("button-press-event", self._colorpicker)
        
        self.btn_gamemode = self._create_button(icons.gamemode, "gamemode", self._gamemode)

        self._buttons_list = [
            self.btn_ssregion, self.btn_sswindow, self.btn_ssfull, 
            self.btn_screenrecord, self.btn_ocr, self.btn_color, self.btn_gamemode,
        ]

        self._arrange_buttons()

    def _create_button(self, icon, tooltip_key, clicked_callback=None):
        button = Button(
            name="toolbox-button",
            tooltip_markup=TOOLTIPS.get(tooltip_key, ""),
            child=Label(name="button-label", markup=icon),
            h_expand=False,
            v_expand=False,
            h_align="center",
            v_align="center",
        )
        
        if clicked_callback:
            button.connect("clicked", clicked_callback)
            
        button.set_can_focus(True)
        return button

    def _arrange_buttons(self):
        buttons = [
            self.btn_ssregion, self.btn_sswindow, self.btn_ssfull, self.btn_screenrecord,
            self._create_separator(),
            self.btn_ocr, self.btn_color,
            self._create_separator(),
            self.btn_gamemode,
        ]
        
        for button in buttons:
            self.add(button)

    def _create_separator(self):
        return Box(name="tool-sep", h_expand=False, v_expand=False)

    def _set_initial_focus(self):
        if self._buttons_list:
            self._current_focus_index = 0
            self._buttons_list[0].grab_focus()

    def _on_key_press(self, widget, event):
        if not self._buttons_list:
            return False

        keyval = event.keyval

        if keyval == Gdk.KEY_Left:
            self._navigate_focus(-1)
            return True
        elif keyval == Gdk.KEY_KP_Left:
            self._navigate_focus(-1)
            return True
        elif keyval == Gdk.KEY_Right:
            self._navigate_focus(1)
            return True
        elif keyval == Gdk.KEY_KP_Right:
            self._navigate_focus(1)
            return True
        elif keyval == Gdk.KEY_Up:
            self._navigate_focus(-1)
            return True
        elif keyval == Gdk.KEY_KP_Up:
            self._navigate_focus(-1)
            return True
        elif keyval == Gdk.KEY_Down:
            self._navigate_focus(1)
            return True
        elif keyval == Gdk.KEY_KP_Down:
            self._navigate_focus(1)
            return True
        elif keyval == Gdk.KEY_Return:
            current_button = self._buttons_list[self._current_focus_index]
            current_button.emit("clicked")
            return True
        elif keyval == Gdk.KEY_KP_Enter:
            current_button = self._buttons_list[self._current_focus_index]
            current_button.emit("clicked")
            return True
        elif keyval == Gdk.KEY_space:
            current_button = self._buttons_list[self._current_focus_index]
            current_button.emit("clicked")
            return True
        elif keyval == Gdk.KEY_Escape:
            self.close_menu()
            return True

        return False

    def _navigate_focus(self, direction):
        if not self._buttons_list:
            return

        new_index = self._current_focus_index + direction
        if new_index < 0:
            new_index = len(self._buttons_list) - 1
        elif new_index >= len(self._buttons_list):
            new_index = 0

        self._buttons_list[self._current_focus_index].set_property("has-focus", False)
        self._buttons_list[new_index].grab_focus()
        self._current_focus_index = new_index

    def _ssregion(self, button=None):
        self._execute_command(f"bash {SCREENSHOT_SCRIPT} s")
        self.close_menu()

    def _ssfull(self, button=None):
        self._execute_command(f"bash {SCREENSHOT_SCRIPT} p")
        self.close_menu()

    def _sswindow(self, button=None):
        self._execute_command(f"bash {SCREENSHOT_SCRIPT} w")
        self.close_menu()

    def _screenrecord(self, button=None):
        self._execute_command(f"bash {SCREENRECORD_SCRIPT}")
        self.close_menu()

    def _ocr(self, button=None):
        self._execute_command(f"bash {OCR_SCRIPT} s")
        self.close_menu()

    def _gamemode(self, button=None):
        self._execute_command(f"bash {GAMEMODE_SCRIPT}")
        self.close_menu()

    def _colorpicker(self, button, event):
        if event.button == 1:
            cmd = "-hex"
            self._execute_command(f"bash {HYPICKER_SCRIPT} {cmd}")
            self.close_menu()
        elif event.button == 2:
            cmd = "-hsv"
            self._execute_command(f"bash {HYPICKER_SCRIPT} {cmd}")
            self.close_menu()
        elif event.button == 3:
            cmd = "-rgb"
            self._execute_command(f"bash {HYPICKER_SCRIPT} {cmd}")
            self.close_menu()

    def _execute_command(self, cmd):
        try:
            exec_shell_command_async(cmd)
        except Exception as e:
            print(f"Command execution error: {e}")

    def _update_screenrecord_state(self):
        if not self._is_active:
            return True
            
        try:
            result = subprocess.run(
                ["pgrep", "-f", "gpu-screen-recorder"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL
            )
            running = result.returncode == 0
            
            if running:
                self.btn_screenrecord.get_child().set_markup(icons.stop)
                self.btn_screenrecord.add_style_class("recording")
            else:
                self.btn_screenrecord.get_child().set_markup(icons.screenrecord)
                self.btn_screenrecord.remove_style_class("recording")
                
        except Exception:
            pass
            
        return self._is_active

    def _update_gamemode_state(self):
        if not self._is_active:
            return True
            
        try:
            result = subprocess.run(
                ["bash", GAMEMODE_SCRIPT, "check"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL
            )
            output = result.stdout
            if output == b't\n':
                enabled = True
            else:
                enabled = False
            
            if enabled:
                icon = icons.gamemode_off
            else:
                icon = icons.gamemode
                
            self.btn_gamemode.get_child().set_markup(icon)
            
        except Exception:
            pass
            
        return self._is_active

    def _start_timers(self):
        self._stop_timers()
        
        timer_id = GLib.timeout_add_seconds(10, self._update_screenrecord_state)
        self._timers.append(timer_id)
        
        timer_id = GLib.timeout_add_seconds(15, self._update_gamemode_state)
        self._timers.append(timer_id)

    def _stop_timers(self):
        for timer_id in self._timers:
            GLib.source_remove(timer_id)
        self._timers = []

    def close_menu(self):
        if hasattr(self, 'notch'):
            self.notch.close_notch()

    def destroy(self):
        self._stop_timers()
        super().destroy()