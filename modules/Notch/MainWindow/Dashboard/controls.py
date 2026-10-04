from fabric.widgets.box import Box

from modules.Notch.MainWindow.Dashboard.Controls.brightness import BrightnessIcon, BrightnessSlider
from modules.Notch.MainWindow.Dashboard.Controls.microphone import MicIcon, MicSlider
from modules.Notch.MainWindow.Dashboard.Controls.volume import VolumeIcon, VolumeSlider

from services.Controls.brightness import Brightness


class ControlSliders(Box):
    def __init__(self, **kwargs):
        super().__init__(name="control-sliders", spacing=8, **kwargs)

        self._rows = []

        if Brightness.get_initial().max_screen > 0:
            brightness_icon, brightness_slider = BrightnessIcon(), BrightnessSlider()
            self._rows += [brightness_icon, brightness_slider]
            self.add(Box(spacing=0, h_expand=True, children=(brightness_icon, brightness_slider)))

        volume_icon, volume_slider = VolumeIcon(), VolumeSlider()
        mic_icon, mic_slider = MicIcon(), MicSlider()
        self._rows += [volume_icon, volume_slider, mic_icon, mic_slider]

        self.add(Box(spacing=0, h_expand=True, children=(volume_icon, volume_slider)))
        self.add(Box(spacing=0, h_expand=True, children=(mic_icon, mic_slider)))
        self.show_all()

    def cleanup(self) -> None:
        for widget in self._rows:
            widget.cleanup()