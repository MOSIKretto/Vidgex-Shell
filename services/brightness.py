from fabric.core.service import Property, Service, Signal
from fabric.utils import exec_shell_command_async, monitor_file

from gi.repository import GLib


class Brightness(Service):
    instance = None

    @staticmethod
    def get_initial():
        if Brightness.instance is None:
            Brightness.instance = Brightness()
        return Brightness.instance

    @Signal
    def screen(self, value: int): ...

    def __init__(self):
        super().__init__()
        
        self.device = None
        self.base_path = None
        self.max_screen = 0
        self._valid = False  # флаг, что backlight действительно работает

        try:
            backlight_dir = "/sys/class/backlight"
            if GLib.file_test(backlight_dir, GLib.FileTest.IS_DIR):
                dir_handle = GLib.Dir.open(backlight_dir, 0)
                name = dir_handle.read_name()
                if name:
                    self.device = name
                    self.base_path = f"{backlight_dir}/{name}"
        except Exception as e:
            print(f"[Brightness] Ошибка поиска backlight: {e}")

        if not self.device or not self.base_path:
            self.max_screen = -1
            return

        try:
            with open(f"{self.base_path}/max_brightness") as f:
                self.max_screen = int(f.read().strip())
        except Exception as e:
            print(f"[Brightness] Не удалось прочитать max_brightness: {e}")
            self.max_screen = -1
            return

        if self.max_screen <= 0:
            self.max_screen = -1
            return

        self._valid = True

        try:
            self.monitor = monitor_file(f"{self.base_path}/brightness")
            self.monitor.connect("changed", lambda *_: self._read_and_emit())
        except Exception as e:
            print(f"[Brightness] Не удалось запустить monitor_file: {e}")
            self._valid = False

        # Первичное чтение
        if self._valid:
            self._read_and_emit()

    def _read_and_emit(self):
        value = self.screen_brightness
        if value != -1:
            self.emit("screen", value)

    @Property(int, "read-write")
    def screen_brightness(self) -> int:
        if not self._valid:
            return -1
        try:
            with open(f"{self.base_path}/brightness") as f:
                return int(f.read().strip())
        except:
            return -1

    @screen_brightness.setter
    def screen_brightness(self, value: int):
        if not self._valid:
            return

        value = max(0, min(int(value), self.max_screen))
        
        try: exec_shell_command_async(f"brightnessctl --device='{self.device}' set {value}", None)
        except Exception as e: print(f"[Brightness] Ошибка brightnessctl: {e}")