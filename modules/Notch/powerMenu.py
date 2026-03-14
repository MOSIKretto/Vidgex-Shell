from fabric.utils.helpers import exec_shell_command_async
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label

import gi
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk

import services.icons as icons
from services.listNavigation import HorizontalNavigationMixin


class PowerMenu(HorizontalNavigationMixin, Box):
    __slots__ = ('notch', '_nav_items', '_nav_idx', '_hand', '_default')
    _ITEMS = (
        ("Lock", icons.lock, "loginctl lock-session"),
        ("Suspend", icons.suspend, "systemctl suspend"),
        ("Logout", icons.logout, "hyprctl dispatch exit"),
        ("Reboot", icons.reboot, "systemctl reboot"),
        ("Shutdown", icons.shutdown, "systemctl poweroff")
    )

    def __init__(self, notch, **kw):
        super().__init__(name="power-menu", spacing=4, **kw)
        self.notch, self._nav_items, self._nav_idx = notch, [], 0
        self._hand = None
        self._default = None

        for tip, ic, cmd in self._ITEMS:
            btn = Button(
                name="power-menu-button",
                tooltip_markup=tip,
                child=Label(name="button-label", markup=ic),
                on_clicked=(lambda c: lambda *_: (exec_shell_command_async(c), self._hnav_close()))(cmd)
            )
            btn.add_events(
                Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
            )
            btn.connect("enter-notify-event", self._on_btn_enter)
            btn.connect("leave-notify-event", self._on_btn_leave)
            self._nav_items.append(btn)
            self.add(btn)

        self.connect("key-press-event", self._hnav_key)
        self.connect("map", self._on_map)

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

    def _on_map(self, *_):
        self._hnav_focus_first()
        self._ensure_cursors()
        self._set_toplevel_cursor(self._default)

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

    def cleanup(self):
        self.notch = None