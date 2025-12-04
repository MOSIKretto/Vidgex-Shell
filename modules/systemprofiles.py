from fabric.utils.helpers import exec_shell_command_async
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label

import subprocess

import modules.icons as icons


class Systemprofiles(Box):
    def __init__(self, **kwargs):
        super().__init__(
            name="systemprofiles",
            orientation="h",
            spacing=3,
        )

        self.bat_save = None
        self.bat_balanced = None
        self.bat_perf = None

        children = []

        try:
            result = subprocess.run(
                ["powerprofilesctl", "list"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
            )
            available_profiles = result.stdout
        except subprocess.CalledProcessError:
            available_profiles = ""
        except FileNotFoundError:
            available_profiles = ""

        if "power-saver" in available_profiles:
            def set_power_saver(*_):
                self.set_power_mode("power-saver")
                
            self.bat_save = Button(
                name="battery-save",
                child=Label(name="battery-save-label", markup=icons.power_saving),
                on_clicked=set_power_saver,
                tooltip_text="Power saving mode",
            )
            children.append(self.bat_save)

        if "balanced" in available_profiles:
            def set_balanced(*_):
                self.set_power_mode("balanced")
                
            self.bat_balanced = Button(
                name="battery-balanced",
                child=Label(name="battery-balanced-label", markup=icons.power_balanced),
                on_clicked=set_balanced,
                tooltip_text="Balanced mode",
            )
            children.append(self.bat_balanced)

        if "performance" in available_profiles:
            def set_performance(*_):
                self.set_power_mode("performance")
                
            self.bat_perf = Button(
                name="battery-performance",
                child=Label(name="battery-performance-label", markup=icons.power_performance),
                on_clicked=set_performance,
                tooltip_text="Performance mode",
            )
            children.append(self.bat_perf)

        if children:
            container = Box(
                name="power-mode-switcher",
                orientation="h",
                spacing=4,
                children=children,
            )
            self.add(container)

        self.get_current_power_mode()
        self.hide_timer = None
        self.hover_counter = 0

    def get_current_power_mode(self):
        try:
            result = subprocess.run(
                ["powerprofilesctl", "get"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
            )

            output = result.stdout.strip()

            if output == "power-saver":
                self.current_mode = "power-saver"
            elif output == "balanced":
                self.current_mode = "balanced"
            elif output == "performance":
                self.current_mode = "performance"
            else:
                self.current_mode = "balanced"

            self.update_button_styles()

        except subprocess.CalledProcessError as err:
            print(f"Command failed: {err}")
            self.current_mode = "balanced"

        except Exception as err:
            print(f"Error retrieving current power mode: {err}")
            self.current_mode = "balanced"

    def set_power_mode(self, mode):
        commands = {
            "power-saver": "powerprofilesctl set power-saver",
            "balanced": "powerprofilesctl set balanced",
            "performance": "powerprofilesctl set performance",
        }
        
        if mode in commands:
            try:
                exec_shell_command_async(commands[mode])
                self.current_mode = mode
                self.update_button_styles()
            except Exception as err:
                print(f"Error setting power mode: {err}")

    def update_button_styles(self):
        if self.bat_save:
            self.bat_save.remove_style_class("active")
        if self.bat_balanced:
            self.bat_balanced.remove_style_class("active")
        if self.bat_perf:
            self.bat_perf.remove_style_class("active")

        if self.current_mode == "power-saver":
            if self.bat_save:
                self.bat_save.add_style_class("active")
        elif self.current_mode == "balanced":
            if self.bat_balanced:
                self.bat_balanced.add_style_class("active")
        elif self.current_mode == "performance":
            if self.bat_perf:
                self.bat_perf.add_style_class("active")