from fabric.utils.helpers import exec_shell_command_async
from fabric.widgets.box import Box
from fabric.widgets.label import Label
from fabric.widgets.button import Button
from fabric.widgets.revealer import Revealer

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib

from services.wayland import WaylandWindow as Window
import services.icons as icons


class PowerMenu(Window):
    _ACTIONS = (
        (icons.shutdown, "Shutdown", "systemctl poweroff"),
        (icons.reboot,   "Reboot",   "systemctl reboot"),
        (icons.logout,   "Logout",   "hyprctl dispatch exit"),
        (icons.suspend,  "Suspend",  "systemctl suspend"),
    )

    def __init__(self, monitor=0, **kw):
        super().__init__(
            exclusivity="none",
            layer="top",
            monitor=monitor,
            keyboard_mode="none",
        )
        self.set_name("power-menu-window")
        self.anchor = "top right"
        self.margin = "0px 0px 0px 0px"

        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_app_paintable(True)
            self.set_visual(visual)

        self._open_flag = False
        self._close_timer = 0
        self._reveal_timer = 0
        self._trigger_btn = None
        self._trigger_sig = 0
        self._hand = None
        self._default = None

        self._btns = []
        self._revealers = []
        self._build_ui()
        self.set_visible(False)

        exec_shell_command_async('hyprctl keyword layerrule "noanim, power-menu-window"')

    def _ensure_cursors(self):
        if self._hand is None:
            display = self.get_display()
            self._hand = Gdk.Cursor.new_from_name(display, "pointer")
            self._default = Gdk.Cursor.new_from_name(display, "default")

    def _set_toplevel_cursor(self, cursor):
        toplevel = self.get_toplevel()
        if toplevel:
            win = toplevel.get_window()
            if win:
                win.set_cursor(cursor)

    def _on_btn_enter(self, w, event):
        if event.detail != Gdk.NotifyType.INFERIOR:
            self._ensure_cursors()
            self._set_toplevel_cursor(self._hand)
        return False

    def _on_btn_leave(self, w, event):
        if event.detail != Gdk.NotifyType.INFERIOR:
            self._ensure_cursors()
            self._set_toplevel_cursor(self._default)
        return False

    def _build_ui(self):
        icons_box = Box(name="power-menu-icons", orientation="v", spacing=4)

        for icon_markup, tooltip, cmd in self._ACTIONS:
            btn = Button(
                child=Label(markup=icon_markup),
                tooltip_markup=tooltip,
                can_focus=False,
            )
            btn.get_style_context().add_class("power-icon-btn")
            btn.add_events(
                Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
            )
            btn.connect("enter-notify-event", self._on_btn_enter)
            btn.connect("leave-notify-event", self._on_btn_leave)
            btn.connect("clicked", self._on_clicked, cmd)
            self._btns.append(btn)

            rev = Revealer(
                transition_type="slide-down",
                child_revealed=False,
                child=btn,
            )
            rev.set_transition_duration(200)
            self._revealers.append(rev)
            icons_box.add(rev)

        self._wrapper = Box(
            name="power-menu-wrapper",
            orientation="v",
            children=[icons_box],
        )
        self.add(self._wrapper)

    def set_trigger_button(self, btn):
        if self._trigger_btn and self._trigger_sig:
            self._trigger_btn.disconnect(self._trigger_sig)
        self._trigger_btn = btn
        self._trigger_sig = btn.connect("size-allocate", self._on_trigger_alloc)

    def _on_trigger_alloc(self, widget, alloc):
        w, h = alloc.width, alloc.height
        if w <= 0 or h <= 0:
            return

        for b in self._btns:
            b.set_size_request(w, h)

        n = len(self._ACTIONS)
        self._wrapper.set_size_request(w, h * n + 4 * (n - 1))

        toplevel = widget.get_toplevel()
        if not toplevel:
            return

        ok, x, y = widget.translate_coordinates(toplevel, 0, 0)
        if not ok:
            return

        m_right = toplevel.get_allocated_width() - x - w
        m_top = y + h + 4
        self.margin = f"{m_top}px {m_right}px 0px 0px"

    def open(self):
        if self._open_flag:
            return
        self._cancel_all()
        self._open_flag = True
        self._set_danger(True)

        for rev in self._revealers:
            rev.set_reveal_child(False)

        self.set_visible(True)
        self.show_all()
        self._reveal_step(0)

    def _reveal_step(self, idx):
        self._reveal_timer = 0
        if not self._open_flag or idx >= len(self._revealers):
            return False
        self._revealers[idx].set_reveal_child(True)
        if idx + 1 < len(self._revealers):
            self._reveal_timer = GLib.timeout_add(45, self._reveal_step, idx + 1)
        return False

    def close(self):
        if not self._open_flag:
            return
        self._cancel_all()
        self._open_flag = False
        self._set_danger(False)

        for rev in self._revealers:
            rev.set_reveal_child(False)

        self._close_timer = GLib.timeout_add(250, self._hide)

    def _hide(self):
        self._close_timer = 0
        if not self._open_flag:
            self.set_visible(False)
        return False

    def _cancel_all(self):
        if self._close_timer:
            GLib.source_remove(self._close_timer)
            self._close_timer = 0
        if self._reveal_timer:
            GLib.source_remove(self._reveal_timer)
            self._reveal_timer = 0

    def _set_danger(self, active):
        if not self._trigger_btn:
            return
        sc = self._trigger_btn.get_style_context()
        if active:
            sc.add_class("power-active")
        else:
            sc.remove_class("power-active")

    def is_open(self):
        return self._open_flag

    def _on_clicked(self, _btn, cmd):
        self.close()
        exec_shell_command_async(cmd)

    def cleanup(self):
        self._cancel_all()
        self._set_danger(False)
        if self._open_flag:
            self._open_flag = False
            self.set_visible(False)
        if self._trigger_btn and self._trigger_sig:
            self._trigger_btn.disconnect(self._trigger_sig)
            self._trigger_sig = 0
        self._trigger_btn = None