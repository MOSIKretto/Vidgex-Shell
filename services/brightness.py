from fabric.core.service import Property, Service, Signal
from fabric.utils import exec_shell_command_async, monitor_file
import os

class Brightness(Service):
    instance = None

    @staticmethod
    def get_initial():
        if not Brightness.instance: Brightness.instance = Brightness()
        return Brightness.instance

    @Signal
    def screen(self, value: int) -> None: ...

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Безопасный поиск устройства
        try:
            self.device = os.listdir("/sys/class/backlight")[0]
        except (IndexError, FileNotFoundError):
            self.device = None
            self.max_screen = -1
            return

        self.base_path = f"/sys/class/backlight/{self.device}"
        
        # Чтение максимальной яркости
        try:
            with open(f"{self.base_path}/max_brightness") as f:
                self.max_screen = int(f.read().strip())
        except:
            self.max_screen = -1
        
        # Мониторинг без лишних оберток
        self.monitor = monitor_file(f"{self.base_path}/brightness")
        self.monitor.connect("changed", lambda *_: self.emit("screen", self.screen_brightness))

    @Property(int, "read-write")
    def screen_brightness(self) -> int:
        if not getattr(self, 'base_path', None): return -1
        try:
            with open(f"{self.base_path}/brightness") as f: return int(f.read().strip())
        except: return -1

    @screen_brightness.setter
    def screen_brightness(self, value: int):
        if not getattr(self, 'device', None) or self.max_screen <= 0: return
        
        value = max(0, min(value, self.max_screen))
        exec_shell_command_async(f"brightnessctl --device '{self.device}' set {value}", None)
        
        self.emit("screen", int((value / self.max_screen) * 100))