from fabric.utils.helpers import exec_shell_command_async
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label

from gi.repository import GLib, Gdk

import os
from pathlib import Path

import modules.icons as icons


class Toolbox(Box):
    def __init__(self, **kwargs):
        super().__init__(name="toolbox", spacing=4, visible=True, **kwargs)
        self.notch = kwargs.get("notch")
        self._buttons_list = []
        self._path = str(Path(__file__).parent.parent.parent / "scripts")
        
        self._setup_ui()
        self.connect("key-press-event", self._on_key_press)
        self.connect("map", self._update_ui_state)

    def _create_btn(self, icon, cmd, tooltip, is_toggle=False):
        btn = Button(
            name="toolbox-button",
            tooltip_markup=tooltip,
            child=Label(name="button-label", markup=icon),
            can_focus=True
        )
        if is_toggle:
            btn.connect("clicked", lambda *_: self._toggle_action(cmd))
        else:
            btn.connect("clicked", lambda *_: self._run_and_close(cmd))
        
        self._buttons_list.append(btn)
        self.add(btn)
        return btn

    def _setup_ui(self):
        self._create_btn(icons.ssregion, f"bash {self._path}/screenshot.sh s", "<b>Область</b>")
        self._create_btn(icons.sswindow, f"bash {self._path}/screenshot.sh w", "<b>Окно</b>")
        self._create_btn(icons.ssfull, f"bash {self._path}/screenshot.sh p", "<b>Экран</b>")
        
        self.btn_rec = self._create_btn(icons.screenrecord, f"bash {self._path}/screenrecord.sh", "<b>Запись</b>", True)
        
        self.add(Box(name="tool-sep"))
        
        self._create_btn(icons.ocr, f"bash {self._path}/ocr.sh s", "<b>OCR</b>")
        self._create_btn(icons.colorpicker, f"bash {self._path}/hyprpicker.sh -hex", "<b>Пипетка</b>")
        
        self.add(Box(name="tool-sep"))
        
        self.btn_game = self._create_btn(icons.gamemode, f"bash {self._path}/gamemode.sh", "<b>Игра</b>", True)

    def _toggle_action(self, cmd):
        exec_shell_command_async(cmd)
        GLib.timeout_add(100, self._update_ui_state)
        GLib.timeout_add(500, self._update_ui_state)

    def _run_and_close(self, cmd):
        if self.notch: self.notch.close_notch()
        exec_shell_command_async(cmd)

    def _update_ui_state(self, *args):
        is_rec = any(os.path.exists(f"/proc/{p}/cmdline") and 
                     b"gpu-screen-recorder" in open(f"/proc/{p}/cmdline", "rb").read()
                     for p in os.listdir("/proc") if p.isdigit())
        
        self.btn_rec.get_child().set_markup(icons.stop if is_rec else icons.screenrecord)
        ctx = self.btn_rec.get_style_context()
        if is_rec: ctx.add_class("recording")
        else: ctx.remove_class("recording")

        def apply_game(output):
            out_str = str(output)
            self.btn_game.get_child().set_markup(icons.gamemode_off if 't' in out_str else icons.gamemode)

        exec_shell_command_async(f"bash {self._path}/gamemode.sh check", apply_game)

        if not hasattr(self, '_cur_idx'):
            self._cur_idx = 0
            if self._buttons_list: self._buttons_list[0].grab_focus()
        
        return False

    def _on_key_press(self, _, event):
        kv = event.keyval
        if kv in (Gdk.KEY_Left, Gdk.KEY_Up): self._nav(-1)
        elif kv in (Gdk.KEY_Right, Gdk.KEY_Down): self._nav(1)
        elif kv in (Gdk.KEY_Return, Gdk.KEY_space, Gdk.KEY_KP_Enter):
            self._buttons_list[self._cur_idx].clicked()
        elif kv == Gdk.KEY_Escape and self.notch: self.notch.close_notch()
        return True

    def _nav(self, d):
        self._cur_idx = (self._cur_idx + d) % len(self._buttons_list)
        self._buttons_list[self._cur_idx].grab_focus()