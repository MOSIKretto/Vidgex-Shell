from fabric.widgets.box import Box

from modules.Notch.MainWindow.Dashboard.Controls.brightness import BrightnessIcon, BrightnessSlider
from modules.Notch.MainWindow.Dashboard.Controls.microphone import MicIcon, MicSlider
from modules.Notch.MainWindow.Dashboard.Controls.volume import VolumeIcon, VolumeSlider

from services.Controls.brightness import Brightness


__all__ = ["ControlSliders"]


class ControlSliders(Box):
    def __init__(self, **kwargs):
        super().__init__(name="control-sliders", spacing=8, **kwargs)

        if Brightness.get_initial().max_screen > 0:
            self.add(Box(spacing=0, h_expand=True, children=(BrightnessIcon(), BrightnessSlider())))
        self.add(Box(spacing=0, h_expand=True, children=(VolumeIcon(), VolumeSlider())))
        self.add(Box(spacing=0, h_expand=True, children=(MicIcon(), MicSlider())))
        self.show_all()