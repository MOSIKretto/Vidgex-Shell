from fabric.core.service import Property, Service, Signal
from fabric.utils import exec_shell_command_async, monitor_file

from gi.repository import GLib

import os
from loguru import logger

from utils.colors import Colors


def exec_brightnessctl_async(args: str):
    def callback(result):
        pass

    exec_shell_command_async(f"brightnessctl {args}", callback)


# Discover screen backlight device
screen_device = ""
try:
    devices = os.listdir("/sys/class/backlight")
    if devices:
        screen_device = devices[0]
    else:
        screen_device = ""
except FileNotFoundError:
    logger.error(
        f"{Colors.ERROR}No backlight devices found, brightness control disabled"
    )
    screen_device = ""


class Brightness(Service):
    """Service to manage screen brightness levels."""

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

        # Path for screen backlight control
        self.screen_backlight_path = f"/sys/class/backlight/{screen_device}"

        # Initialize maximum brightness level
        self.max_screen = self.do_read_max_brightness(self.screen_backlight_path)

        if screen_device == "":
            return

        # Monitor screen brightness file
        self.screen_monitor = monitor_file(f"{self.screen_backlight_path}/brightness")

        def on_screen_changed(monitor, file, *args):
            file_data = file.load_bytes()[0].get_data()
            brightness_value = int(file_data)
            rounded_value = round(brightness_value)
            self.emit("screen", rounded_value)

        self.screen_monitor.connect("changed", on_screen_changed)

        # Log the initialization of the service
        logger.info(
            f"{Colors.INFO}Brightness service initialized for device: {screen_device}"
        )

    def do_read_max_brightness(self, path: str) -> int:
        # Reads the maximum brightness value from the specified path.
        max_brightness_path = os.path.join(path, "max_brightness")
        if not os.path.exists(max_brightness_path):
            return -1

        with open(max_brightness_path) as f:
            content = f.readline()
            return int(content)

    @Property(int, "read-write")
    def screen_brightness(self) -> int:
        # Property to get or set the screen brightness.
        brightness_path = os.path.join(self.screen_backlight_path, "brightness")
        if not os.path.exists(brightness_path):
            logger.warning(
                f"{Colors.WARNING}Brightness file does not exist: {brightness_path}"
            )
            return -1

        with open(brightness_path) as f:
            content = f.readline()
            return int(content)

    @screen_brightness.setter
    def screen_brightness(self, value: int):
        # Setter for screen brightness property.
        if value < 0:
            value = 0
        if value > self.max_screen:
            value = self.max_screen

        try:
            exec_brightnessctl_async(f"--device '{screen_device}' set {value}")
            percentage = (value / self.max_screen) * 100
            self.emit("screen", int(percentage))
            logger.info(
                f"{Colors.INFO}Set screen brightness to {value} "
                f"(out of {self.max_screen})"
            )
        except GLib.Error as e:
            logger.error(f"{Colors.ERROR}Error setting screen brightness: {e.message}")
        except Exception as e:
            logger.exception(f"Unexpected error setting screen brightness: {e}")