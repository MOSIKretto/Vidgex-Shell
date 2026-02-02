from fabric.utils.helpers import exec_shell_command_async
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label

import services.icons as icons
from services.list_navigation import HorizontalNavigationMixin


class PowerMenu(HorizontalNavigationMixin, Box):
    __slots__ = ('notch', '_nav_items', '_nav_idx')
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
        for tip, ic, cmd in self._ITEMS:
            btn = Button(
                name="power-menu-button", 
                tooltip_markup=tip,
                child=Label(name="button-label", markup=ic),
                on_clicked=(lambda c: lambda *_: (exec_shell_command_async(c), self._hnav_close()))(cmd)
            )
            self._nav_items.append(btn); self.add(btn)
        self.connect("key-press-event", self._hnav_key)
        self.connect("map", lambda *_: self._hnav_focus_first())

    def cleanup(self): self.notch = None