from fabric.utils.helpers import exec_shell_command, exec_shell_command_async
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label
from gi.repository import GLib, Gio

import services.icons as icons
from services.list_navigation import HorizontalNavigationMixin


class Toolbox(HorizontalNavigationMixin, Box):
    __slots__ = ('notch', '_nav_items', '_nav_idx', '_path', 'btn_rec', 'btn_game')

    def __init__(self, **kw):
        super().__init__(name="toolbox", spacing=4, visible=True, **kw)
        self.notch, self._nav_items, self._nav_idx = kw.get("notch"), [], 0
        self._path = Gio.File.new_for_path(__file__).get_parent().get_parent().get_parent().get_child("scripts").get_path()
        
        items = [
            (icons.ssregion, "screenshot.sh s", "<b>Screenshot of screen area</b>", 0),
            (icons.sswindow, "screenshot.sh w", "<b>Window screenshot</b>", 0),
            (icons.ssfull, "screenshot.sh p", "<b>Screenshot</b>", 0),
            (icons.screenrecord, "screenrecord.sh", "<b>Screen Recording</b>", 1),
            None,
            (icons.ocr, "ocr.sh s", "<b>OCR</b>", 0),
            (icons.colorpicker, "hyprpicker.sh -hex", "<b>Pipette</b>", 0),
            None,
            (icons.gamemode, "gamemode.sh", "<b>Game mode</b>", 2),
        ]
        
        for item in items:
            if item is None: self.add(Box(name="tool-sep")); continue
            ic, script, tip, typ = item
            btn = Button(
                name="toolbox-button", 
                tooltip_markup=tip, 
                can_focus=True,
                child=Label(name="button-label", markup=ic)
            )
            cmd = f"bash {self._path}/{script}"
            btn.connect("clicked", (lambda c: lambda *_: self._toggle(c))(cmd) if typ else (lambda c: lambda *_: self._run(c))(cmd))
            self._nav_items.append(btn); self.add(btn)
            if typ == 1: self.btn_rec = btn
            elif typ == 2: self.btn_game = btn

        self.connect("key-press-event", self._hnav_key)
        self.connect("map", lambda *_: (self._update_ui(), self._hnav_focus_first()) or False)

    def _toggle(self, cmd):
        exec_shell_command_async(cmd)
        GLib.timeout_add(100, self._update_ui); GLib.timeout_add(500, self._update_ui)

    def _run(self, cmd):
        self._hnav_close(); exec_shell_command_async(cmd)

    def _update_ui(self, *_):
        is_rec = bool((o := exec_shell_command("pgrep -f gpu-screen-recorder")) and o.strip())
        self.btn_rec.get_child().set_markup(icons.stop if is_rec else icons.screenrecord)
        ctx = self.btn_rec.get_style_context()
        ctx.add_class("recording") if is_rec else ctx.remove_class("recording")
        exec_shell_command_async(
            f"bash {self._path}/gamemode.sh check",
            lambda o: self.btn_game.get_child().set_markup(icons.gamemode_off if 't' in str(o) else icons.gamemode)
        )
        return False