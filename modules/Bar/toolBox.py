from fabric.utils.helpers import exec_shell_command, exec_shell_command_async
from fabric.widgets.box import Box
from fabric.widgets.label import Label
from fabric.widgets.button import Button
from fabric.widgets.revealer import Revealer

import gi
import os
import builtins
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib

from services.wayland import WaylandWindow as Window
import services.icons as icons


def _wrap_revealer(child, duration=200, transition="slide-down"):
    rev = Revealer(transition_type=transition, child_revealed=False, child=child)
    rev.set_transition_duration(duration)
    return rev


class ToolBox(Window):
    _REVEAL_STEP = 45
    _CLOSE_DELAY = 250
    _REV_DURATION = 200
    _BTN_SPACING = 4
    _TOP_OFFSET = 50
    _RIGHT_OFFSET = 8

    _CSS_BTN = "toolbox-icon-btn"
    _CSS_ACTIVE = "power-active"
    _WIN_NAME = "toolbox-menu-window"

    _MENU_CAMERA = [
        (icons.ssregion,     "screenshot.sh s", "<b>Screenshot of screen area</b>", 0),
        (icons.sswindow,     "screenshot.sh w", "<b>Window screenshot</b>",         0),
        (icons.ssfull,       "screenshot.sh p", "<b>Screenshot</b>",                0),
        (icons.screenrecord, "screenrecord.sh", "<b>Screen Recording</b>",          1),
    ]

    _MENU_TOOLS = [
        (icons.ocr,         "ocr.sh s",           "<b>OCR</b>",           0),
        (icons.colorpicker, "hyprpicker.sh -hex", "<b>Pipette</b>",       0),
        (icons.keyboard,    "autoLanguage.py",    "<b>Auto Language</b>", 3),
        (icons.gamemode,    "gamemode.sh",        "<b>Game mode</b>",     2),
    ]

    _AL_PAT = "vidgex-autolanguage"
    _AL_CMD = "python ~/.config/Vidgex-Shell/scripts/autoLanguage.py"
    _AL_STOP = "pkill -f vidgex-autolanguage"

    def __init__(self, monitor=0, **kwargs):
        super().__init__(exclusivity="none", layer="top", monitor=monitor, keyboard_mode="none")

        builtins.toolbox = self

        self.set_name(self._WIN_NAME)
        self.anchor = "top right"
        self.margin = "0px 0px 0px 0px"

        self._scripts = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "scripts"
        )

        self._open = False
        self._c_timer = self._r_timer = None
        self._trig_btn = self._trig_sig = None
        self._cursors = {}

        self._btns = []
        self._revealers = []
        self._dyn = {"al_pid": None}

        self._setup_transparency()
        self._build_ui()

        self.set_visible(False)
        exec_shell_command_async(f'hyprctl keyword layerrule "noanim, {self._WIN_NAME}"')

    def _setup_transparency(self):
        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_app_paintable(True)
            self.set_visual(visual)

    def _ensure_cursors(self):
        if not self._cursors:
            d = self.get_display()
            self._cursors = {
                "hand":    Gdk.Cursor.new_from_name(d, "pointer"),
                "default": Gdk.Cursor.new_from_name(d, "default"),
            }

    def _set_cursor(self, name):
        self._ensure_cursors()
        win = self.get_toplevel().get_window()
        if win:
            win.set_cursor(self._cursors[name])

    def _on_enter(self, w, e):
        if e.detail != Gdk.NotifyType.INFERIOR:
            self._set_cursor("hand")
        return False

    def _on_leave(self, w, e):
        if e.detail != Gdk.NotifyType.INFERIOR:
            self._set_cursor("default")
        return False

    def _make_btn(self, child, tooltip):
        btn = Button(child=child, tooltip_markup=tooltip)
        btn.set_can_focus(False)
        btn.set_focus_on_click(False)
        btn.get_style_context().add_class(self._CSS_BTN)
        btn.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        btn.connect("enter-notify-event", self._on_enter)
        btn.connect("leave-notify-event", self._on_leave)
        return btn

    def _build_row(self, main_icon, items):
        sub_box = Box(orientation="h", spacing=self._BTN_SPACING)
        sub_box.set_margin_right(self._BTN_SPACING)

        for item in items:
            icon_markup, cmd, tooltip, action_type = item
            lbl = Label(markup=icon_markup)
            btn = self._make_btn(child=lbl, tooltip=tooltip)
            btn.connect("clicked", self._on_click, cmd, action_type, lbl)

            if action_type == 1:
                self._dyn["record"] = lbl
            if action_type == 2:
                self._dyn["game"] = lbl
            if action_type == 3:
                self._dyn["al_btn"] = btn
                exec_shell_command_async(
                    f"pgrep -f {self._AL_PAT}",
                    lambda out: self._al_set_active(bool(out and out.strip()))
                )

            self._btns.append(btn)
            sub_box.add(btn)

        sub_rev = _wrap_revealer(sub_box, self._REV_DURATION, transition="slide-left")

        main_btn = self._make_btn(Label(markup=main_icon), tooltip="Expand")
        main_btn.connect("clicked", lambda b, r=sub_rev: self._on_row_btn_click(r))
        self._btns.append(main_btn)

        row_box = Box(orientation="h", spacing=0)
        row_box.set_halign(Gtk.Align.END)
        row_box.pack_start(sub_rev, False, False, 0)
        row_box.pack_start(main_btn, False, False, 0)

        main_rev = _wrap_revealer(row_box, self._REV_DURATION, transition="slide-down")
        return main_rev, sub_rev, main_btn

    def _build_ui(self):
        self._outer_box = Box(
            name="toolbox-menu-icons",
            orientation="v",
            spacing=self._BTN_SPACING
        )
        self._outer_box.set_halign(Gtk.Align.END)

        self._cam_main_rev, self._cam_sub_rev, _ = \
            self._build_row(icons.photo, self._MENU_CAMERA)
        self._revealers.append(self._cam_main_rev)
        self._outer_box.add(self._cam_main_rev)

        self._tools_main_rev, self._tools_sub_rev, _ = \
            self._build_row(icons.pencil, self._MENU_TOOLS)
        self._revealers.append(self._tools_main_rev)
        self._outer_box.add(self._tools_main_rev)

        self._wrapper = Box(
            name="toolbox-menu-wrapper",
            orientation="v",
            children=[self._outer_box]
        )
        self.add(self._wrapper)

    def _any_sub_open(self):
        return (
            self._cam_sub_rev.get_reveal_child() or
            self._tools_sub_rev.get_reveal_child()
        )

    def _on_row_btn_click(self, target_rev):
        current = target_rev.get_reveal_child()
        target_rev.set_reveal_child(not current)

    def _check_close_all(self):
        if not self._any_sub_open():
            self.close()
        return False

    def toggle_camera(self):
        if not self._open:
            self.open()
            GLib.timeout_add(
                self._REV_DURATION + 50,
                lambda: self._cam_sub_rev.set_reveal_child(True) or False
            )
            return

        cam_open = self._cam_sub_rev.get_reveal_child()

        if cam_open:
            self._cam_sub_rev.set_reveal_child(False)
            GLib.timeout_add(self._REV_DURATION + 50, self._check_close_all)
        else:
            if self._any_sub_open():
                self._cam_sub_rev.set_reveal_child(True)
            else:
                self.close()

    def toggle_tools(self):
        if not self._open:
            self.open()
            GLib.timeout_add(
                self._REV_DURATION + 50,
                lambda: self._tools_sub_rev.set_reveal_child(True) or False
            )
            return

        tools_open = self._tools_sub_rev.get_reveal_child()

        if tools_open:
            self._tools_sub_rev.set_reveal_child(False)
            GLib.timeout_add(self._REV_DURATION + 50, self._check_close_all)
        else:
            if self._any_sub_open():
                self._tools_sub_rev.set_reveal_child(True)
            else:
                self.close()

    def _al_set_active(self, en):
        btn = self._dyn.get("al_btn")
        if btn:
            (btn.add_style_class if en else btn.remove_style_class)("active")

    def _al_toggle(self):
        pid = self._dyn.get("al_pid")
        if pid is not None:
            try:
                os.kill(pid, 15)
            except OSError:
                pass
            self._dyn["al_pid"] = None
            self._al_set_active(False)
            return

        is_running = bool(
            (out := exec_shell_command(f"pgrep -f {self._AL_PAT}")) and out.strip()
        )
        if is_running:
            exec_shell_command_async(self._AL_STOP)
            self._al_set_active(False)
        else:
            def _spawn():
                try:
                    new_pid, *_ = GLib.spawn_async(
                        argv=["/bin/sh", "-c", self._AL_CMD],
                        flags=GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.DO_NOT_REAP_CHILD,
                    )
                    self._dyn["al_pid"] = new_pid
                    GLib.child_watch_add(GLib.PRIORITY_DEFAULT, new_pid, self._al_on_exit)
                except Exception:
                    exec_shell_command_async(self._AL_CMD)
            GLib.idle_add(_spawn)
            self._al_set_active(True)

    def _al_on_exit(self, pid, _):
        if self._dyn.get("al_pid") == pid:
            self._dyn["al_pid"] = None
        exec_shell_command_async(
            f"pgrep -f {self._AL_PAT}",
            lambda out: self._al_set_active(bool(out and out.strip()))
        )

    def _refresh_dyn(self, *_):
        if "record" in self._dyn:
            is_rec = bool(
                (o := exec_shell_command("pgrep -f gpu-screen-recorder")) and o.strip()
            )
            self._dyn["record"].set_markup(icons.stop if is_rec else icons.screenrecord)

        if "game" in self._dyn:
            exec_shell_command_async(
                f"bash {self._scripts}/gamemode.sh check",
                lambda o: self._dyn["game"].set_markup(
                    icons.gamemode_off if 't' in str(o) else icons.gamemode
                ),
            )
        return False

    def _on_click(self, btn, cmd, action_type, lbl):
        if action_type == 3:
            self._al_toggle()
            return

        exec_shell_command_async(f"bash {self._scripts}/{cmd}")

        if action_type == 0:
            self.close()
        else:
            GLib.timeout_add(100, self._refresh_dyn)
            GLib.timeout_add(500, self._refresh_dyn)

    def set_trigger_button(self, btn):
        if self._trig_btn and self._trig_sig:
            self._trig_btn.disconnect(self._trig_sig)
        self._trig_btn = btn
        self._trig_sig = btn.connect("size-allocate", self._on_alloc)

    def _on_alloc(self, widget, alloc):
        w, h = alloc.width, alloc.height
        if w <= 0 or h <= 0:
            return

        for btn in self._btns:
            btn.set_size_request(w, h)

        toplevel = widget.get_toplevel()
        if not toplevel:
            return
        coords = widget.translate_coordinates(toplevel, 0, 0)
        if coords is None:
            return
        x, y = coords
        tw = toplevel.get_allocated_width()

        self.margin = (
            f"{max(0, y + h + self._BTN_SPACING - self._TOP_OFFSET)}px "
            f"{max(0, tw - x - w - self._RIGHT_OFFSET)}px 0px 0px"
        )

    def open(self):
        if self._open:
            return
        self._cancel_timers()
        self._open = True
        self._set_trig_active(True)
        self._refresh_dyn()

        for rev in self._revealers:
            rev.set_reveal_child(False)

        self._cam_sub_rev.set_reveal_child(False)
        self._tools_sub_rev.set_reveal_child(False)

        self.set_visible(True)
        self.show_all()
        self._reveal_step(0)

    def close(self):
        if not self._open:
            return
        self._cancel_timers()
        self._open = False
        self._set_trig_active(False)

        for rev in self._revealers:
            rev.set_reveal_child(False)

        self._cam_sub_rev.set_reveal_child(False)
        self._tools_sub_rev.set_reveal_child(False)

        self._c_timer = GLib.timeout_add(self._CLOSE_DELAY, self._hide)

    def _hide(self):
        self._c_timer = None
        if not self._open:
            self.set_visible(False)
        return False

    def is_open(self):
        return self._open

    def _reveal_step(self, i):
        self._r_timer = None
        if not self._open or i >= len(self._revealers):
            return False
        self._revealers[i].set_reveal_child(True)
        if i + 1 < len(self._revealers):
            self._r_timer = GLib.timeout_add(self._REVEAL_STEP, self._reveal_step, i + 1)
        return False

    def _cancel_timers(self):
        for attr in ('_c_timer', '_r_timer'):
            if (t := getattr(self, attr)) is not None:
                GLib.source_remove(t)
                setattr(self, attr, None)

    def _set_trig_active(self, en):
        if not self._trig_btn:
            return
        ctx = self._trig_btn.get_style_context()
        (ctx.add_class if en else ctx.remove_class)(self._CSS_ACTIVE)

    def cleanup(self):
        self._cancel_timers()
        self._set_trig_active(False)
        if self._open:
            self._open = False
            self.set_visible(False)
        if self._trig_btn and self._trig_sig:
            self._trig_btn.disconnect(self._trig_sig)
        self._trig_btn = None