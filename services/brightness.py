import os
from typing import Optional
from fabric.core.service import Property, Service, Signal
from fabric.utils import exec_shell_command_async, monitor_file


class Brightness(Service):
    instance = None

    @staticmethod
    def get_initial():
        if not Brightness.instance:
            Brightness.instance = Brightness()
        return Brightness.instance

    @Signal
    def screen(self, value: int) -> None: ...

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        backlight_devices = os.listdir("/sys/class/backlight")
        self.device: Optional[str] = next(iter(backlight_devices), None)
        if not self.device:
            self.max_screen = -1
            return

        self.base_path = f"/sys/class/backlight/{self.device}"
        self.max_screen = self._read_int("max_brightness")
        
        self.monitor = monitor_file(f"{self.base_path}/brightness")
        self.handler_id = self.monitor.connect("changed", self._on_changed)

    def _read_int(self, filename: str) -> int:
        try:
            with open(f"{self.base_path}/{filename}") as f:
                return int(f.read().strip())
        except (IOError, ValueError, AttributeError):
            return -1

    def _on_changed(self, monitor, file, *args):
        val = self._read_int("brightness")
        if val != -1:
            self.emit("screen", val)

    @Property(int, "read-write")
    def screen_brightness(self) -> int:
        return self._read_int("brightness")

    @screen_brightness.setter
    def screen_brightness(self, value: int):
        bounded_value = max(0, min(value, self.max_screen))
        exec_shell_command_async(f"brightnessctl --device '{self.device}' set {bounded_value}", None)
        if self.max_screen > 0:
            percentage = int((bounded_value / self.max_screen) * 100)
            self.emit("screen", percentage)
        else:
            self.emit("screen", 0)

    def destroy(self):
        if hasattr(self, 'monitor') and self.monitor:
            self.monitor.disconnect(self.handler_id)
            self.monitor.cancel()