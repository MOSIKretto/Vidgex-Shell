from fabric.utils.helpers import exec_shell_command_async
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label

import modules.icons as icons


class PowerMenu(Box):
    __slots__ = ("notch")

    def __init__(self, notch, **kwargs):
        super().__init__(
            name="power-menu",
            spacing=4,
            **kwargs,
        )
        self.notch = notch

        for data in (
            ("Lock", icons.lock, "loginctl lock-session"),
            ("Suspend", icons.suspend, "systemctl suspend"),
            ("Logout", icons.logout, "hyprctl dispatch exit"),
            ("Reboot", icons.reboot, "systemctl reboot"),
            ("Shutdown", icons.shutdown, "systemctl poweroff"),
        ):
            self.add(
                Button(
                    name="power-menu-button",
                    tooltip_markup=data[0],
                    child=Label(name="button-label", markup=data[1]),
                    on_clicked=lambda *_, c=data[2]: self._run(c),
                )
            )

    def _run(self, cmd: str):
        exec_shell_command_async(cmd)
        if self.notch is not None:
            self.notch.close_notch()