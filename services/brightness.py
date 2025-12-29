from fabric.core.service import Property, Service, Signal
from fabric.utils import exec_shell_command_async, monitor_file

import os

def exec_brightnessctl_async(args: str):
    def callback(result):
        pass
    
    exec_shell_command_async(f"brightnessctl {args}", callback)

screen_device = ""
devices = os.listdir("/sys/class/backlight")
if devices:
    screen_device = devices[0]

class Brightness(Service):
    instance = None

    @staticmethod
    def get_initial():
        if Brightness.instance is None:
            Brightness.instance = Brightness()
        return Brightness.instance

    @Signal
    def screen(self, value: int) -> None:
        """Signal emitted when screen brightness changes."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.screen_backlight_path = f"/sys/class/backlight/{screen_device}"

        self.max_screen = self._read_max_brightness()

        if not screen_device:
            return

        self.screen_monitor = monitor_file(f"{self.screen_backlight_path}/brightness")

        def on_screen_changed(monitor, file, *args):
            file_data = file.load_bytes()[0].get_data()
            brightness_value = int(file_data)
            rounded_value = round(brightness_value)
            self.emit("screen", rounded_value)

        self.screen_monitor.connect("changed", on_screen_changed)

    def _read_max_brightness(self):
        max_brightness_path = f"{self.screen_backlight_path}/max_brightness"
        if not os.path.exists(max_brightness_path):
            return -1

        with open(max_brightness_path) as f:
            content = f.readline()
            return int(content)

    @Property(int, "read-write")
    def screen_brightness(self) -> int:
        brightness_path = f"{self.screen_backlight_path}/brightness"
        if not os.path.exists(brightness_path):
            return -1

        with open(brightness_path) as f:
            content = f.readline()
            return int(content)

    @screen_brightness.setter
    def screen_brightness(self, value: int):
        if value < 0:
            value = 0
        elif value > self.max_screen:
            value = self.max_screen

        exec_brightnessctl_async(f"--device '{screen_device}' set {value}")
        percentage = (value / self.max_screen) * 100
        self.emit("screen", int(percentage))