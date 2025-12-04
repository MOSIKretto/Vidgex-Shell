from fabric.hyprland import Hyprland

import gi
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk

import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

import json


class HyprlandWithMonitors(Hyprland):
    def __init__(self, commands_only=False, **kwargs):
        self.display = Gdk.Display.get_default()
        super().__init__(commands_only, **kwargs)

    # Add new arguments
    def get_all_monitors(self):
        monitors = json.loads(self.send_command("j/monitors").reply)
        result = {}
        for monitor in monitors:
            result[monitor["id"]] = monitor["name"]
        return result

    def get_gdk_monitor_id_from_name(self, plug_name):
        for i in range(self.display.get_n_monitors()):
            monitor_name = self.display.get_default_screen().get_monitor_plug_name(i)
            if monitor_name == plug_name:
                return i
        return None

    def get_gdk_monitor_id(self, hyprland_id):
        monitors = self.get_all_monitors()
        if hyprland_id in monitors:
            return self.get_gdk_monitor_id_from_name(monitors[hyprland_id])
        return None

    def get_current_gdk_monitor_id(self):
        active_workspace = json.loads(self.send_command("j/activeworkspace").reply)
        return self.get_gdk_monitor_id_from_name(active_workspace["monitor"])