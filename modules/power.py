from fabric.utils.helpers import exec_shell_command_async
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label

import modules.icons as icons


POWER_ACTIONS = {
    "lock": {"tooltip": "Lock", "icon": icons.lock, "command": "loginctl lock-session"},
    "suspend": {"tooltip": "Suspend", "icon": icons.suspend, "command": "systemctl suspend"},
    "logout": {"tooltip": "Logout", "icon": icons.logout, "command": "hyprctl dispatch exit"},
    "reboot": {"tooltip": "Reboot", "icon": icons.reboot, "command": "systemctl reboot"},
    "shutdown": {"tooltip": "Shutdown", "icon": icons.shutdown, "command": "systemctl poweroff"},
}

class PowerMenu(Box):
    def __init__(self, notch, **kwargs):
        super().__init__(
            name="power-menu",
            spacing=4,
            visible=True,
            **kwargs,
        )

        self.notch = notch
        self.buttons = []
        
        for name, data in POWER_ACTIONS.items():
            button = self._create_power_button(name, data)
            self.buttons.append(button)
            self.add(button)
            setattr(self, f"btn_{name}", button)

    def _create_power_button(self, name: str, data: dict) -> Button:
        on_clicked_method = getattr(self, name)
        
        return Button(
            name="power-menu-button",
            h_expand=False,
            v_expand=False,
            h_align="center",
            v_align="center",
            tooltip_markup=data["tooltip"],
            child=Label(name="button-label", markup=data["icon"]),
            on_clicked=on_clicked_method,
        )

    def _execute_command(self, command: str):
        exec_shell_command_async(command)
        self.notch.close_notch()

    def lock(self):
        self._execute_command(POWER_ACTIONS["lock"]["command"])

    def suspend(self):
        self._execute_command(POWER_ACTIONS["suspend"]["command"])

    def logout(self):
        self._execute_command(POWER_ACTIONS["logout"]["command"])

    def reboot(self):
        self._execute_command(POWER_ACTIONS["reboot"]["command"])

    def shutdown(self): 
        self._execute_command(POWER_ACTIONS["shutdown"]["command"])