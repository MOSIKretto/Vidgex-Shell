from fabric.audio.service import Audio
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.circularprogressbar import CircularProgressBar
from fabric.widgets.eventbox import EventBox
from fabric.widgets.label import Label
from fabric.widgets.overlay import Overlay
from fabric.widgets.scale import Scale

from gi.repository import GLib

from services.brightness import Brightness 
import modules.icons as icons


class VolumeSlider(Scale):
    def __init__(self, **kwargs):
        super().__init__(name="control-slider", h_expand=True, **kwargs)
        self.audio = Audio()
        self._update_source_id = None
        
        self.audio.connect("notify::speaker", self._on_new_speaker)
        if self.audio.speaker:
            self.audio.speaker.connect("changed", self._on_speaker_changed)
        
        self.connect("value-changed", self._on_value_changed)
        self.add_style_class("vol")
        self._on_speaker_changed()

    def _on_new_speaker(self, *args):
        if self.audio.speaker:
            self.audio.speaker.connect("changed", self._on_speaker_changed)
            self._on_speaker_changed()

    def _on_value_changed(self, _):
        if self.audio.speaker:
            new_value = self.value * 100
            if self._update_source_id:
                GLib.source_remove(self._update_source_id)
            self._update_source_id = GLib.timeout_add(100, lambda: self._update_volume(new_value))

    def _update_volume(self, value):
        self.audio.speaker.volume = value
        self._update_source_id = None
        return False

    def _on_speaker_changed(self, *_):
        if not self.audio.speaker:
            return
        self.value = self.audio.speaker.volume / 100
        if self.audio.speaker.muted:
            self.add_style_class("muted")
        else:
            self.remove_style_class("muted")


class MicSlider(Scale):
    def __init__(self, **kwargs):
        super().__init__(name="control-slider", h_expand=True, **kwargs)
        self.audio = Audio()
        
        self.audio.connect("notify::microphone", self._on_new_microphone)
        if self.audio.microphone:
            self.audio.microphone.connect("changed", self._on_microphone_changed)
        
        self.connect("value-changed", self._on_value_changed)
        self.add_style_class("mic")
        self._on_microphone_changed()

    def _on_new_microphone(self, *args):
        if self.audio.microphone:
            self.audio.microphone.connect("changed", self._on_microphone_changed)
            self._on_microphone_changed()

    def _on_value_changed(self, _):
        if self.audio.microphone:
            self.audio.microphone.volume = self.value * 100

    def _on_microphone_changed(self, *_):
        if not self.audio.microphone:
            return
        self.value = self.audio.microphone.volume / 100
        if self.audio.microphone.muted:
            self.add_style_class("muted")
        else:
            self.remove_style_class("muted")


class BrightnessSlider(Scale):
    def __init__(self, **kwargs):
        super().__init__(name="control-slider", h_expand=True, **kwargs)
        self.client = Brightness.get_initial()
        if self.client.screen_brightness == -1:
            return

        self.set_range(0, self.client.max_screen)
        self.set_value(self.client.screen_brightness)
        self.add_style_class("brightness")
        self._update_source_id = None
        self._updating_from_brightness = False

        self.connect("change-value", self._on_scale_move)
        self.client.connect("screen", self._on_brightness_changed)

    def _on_scale_move(self, widget, scroll, moved_pos):
        if self._updating_from_brightness:
            return False
        
        if self._update_source_id:
            GLib.source_remove(self._update_source_id)
        self._update_source_id = GLib.timeout_add(100, lambda: self._update_brightness(moved_pos))
        return False

    def _update_brightness(self, value):
        if value != self.client.screen_brightness:
            self.client.screen_brightness = value
        self._update_source_id = None
        return False

    def _on_brightness_changed(self, client, _):
        self._updating_from_brightness = True
        self.set_value(self.client.screen_brightness)
        self._updating_from_brightness = False
        percentage = int((self.client.screen_brightness / self.client.max_screen) * 100)
        self.set_tooltip_text(f"{percentage}%")


class BrightnessSmall(Box):
    def __init__(self, **kwargs):
        super().__init__(name="button-bar-brightness", **kwargs)
        self.brightness = Brightness.get_initial()
        if self.brightness.screen_brightness == -1:
            return

        self._create_widgets()
        self._setup_connections()

    def _create_widgets(self):
        self.progress_bar = CircularProgressBar(
            name="button-brightness", size=28, line_width=2,
            start_angle=150, end_angle=390,
        )
        self.brightness_label = Label(name="brightness-label", markup=icons.brightness_high)
        self.brightness_button = Button(child=self.brightness_label)
        self.event_box = EventBox(
            child=Overlay(child=self.progress_bar, overlays=self.brightness_button)
        )
        self.add(self.event_box)

    def _setup_connections(self):
        self._update_source_id = None
        self._updating_from_brightness = False

        self.progress_bar.connect("notify::value", self._on_progress_value_changed)
        self.brightness.connect("screen", self._on_brightness_changed)
        self._on_brightness_changed()

    def _on_progress_value_changed(self, widget, pspec):
        if self._updating_from_brightness:
            return
        new_brightness = int(widget.value * self.brightness.max_screen)
        
        if self._update_source_id:
            GLib.source_remove(self._update_source_id)
        self._update_source_id = GLib.timeout_add(100, lambda: self._set_brightness(new_brightness))

    def _set_brightness(self, value):
        self.brightness.screen_brightness = value
        self._update_source_id = None
        return False

    def _on_brightness_changed(self, *args):
        if self.brightness.max_screen == -1:
            return
        normalized = self.brightness.screen_brightness / self.brightness.max_screen
        self._updating_from_brightness = True
        self.progress_bar.value = normalized
        self._updating_from_brightness = False

        brightness_percentage = int(normalized * 100)
        if brightness_percentage >= 75:
            self.brightness_label.set_markup(icons.brightness_high)
        elif brightness_percentage >= 24:
            self.brightness_label.set_markup(icons.brightness_medium)
        else:
            self.brightness_label.set_markup(icons.brightness_low)
        self.set_tooltip_text(f"Яркость: {brightness_percentage}%")


class VolumeSmall(Box):
    def __init__(self, **kwargs):
        super().__init__(name="button-bar-vol", **kwargs)
        self.audio = Audio()
        
        self._create_widgets()
        self._setup_connections()
        self._on_speaker_changed()

    def _create_widgets(self):
        self.progress_bar = CircularProgressBar(
            name="button-volume", size=28, line_width=2,
            start_angle=150, end_angle=390,
        )
        self.vol_label = Label(name="vol-label", markup=icons.vol_high)
        self.vol_button = Button(on_clicked=self._toggle_mute, child=self.vol_label)
        self.event_box = EventBox(
            child=Overlay(child=self.progress_bar, overlays=self.vol_button)
        )
        self.add(self.event_box)

    def _setup_connections(self):
        self.audio.connect("notify::speaker", self._on_new_speaker)
        if self.audio.speaker:
            self.audio.speaker.connect("changed", self._on_speaker_changed)

    def _on_new_speaker(self):
        if self.audio.speaker:
            self.audio.speaker.connect("changed", self._on_speaker_changed)
            self._on_speaker_changed()

    def _toggle_mute(self):
        current_stream = self.audio.speaker
        if current_stream:
            current_stream.muted = not current_stream.muted
            self._on_speaker_changed()

    def _on_speaker_changed(self, *_):
        if not self.audio.speaker:
            return
        
        if "bluetooth" in self.audio.speaker.icon_name:
            icons_map = {
                "high": icons.bluetooth_connected,
                "medium": icons.bluetooth,
                "mute": icons.bluetooth_off,
                "off": icons.bluetooth_disconnected
            }
        else:
            icons_map = {
                "high": icons.vol_high,
                "medium": icons.vol_medium,
                "mute": icons.vol_off,
                "off": icons.vol_mute
            }
        
        self.progress_bar.value = self.audio.speaker.volume / 100
        
        if self.audio.speaker.muted:
            self.vol_label.set_markup(icons_map["mute"])
            self.progress_bar.add_style_class("muted")
            self.vol_label.add_style_class("muted")
            self.set_tooltip_text("Без звука")
        else:
            self.progress_bar.remove_style_class("muted")
            self.vol_label.remove_style_class("muted")
            self.set_tooltip_text(f"Громкость: {round(self.audio.speaker.volume)}%")
            
            if self.audio.speaker.volume > 74:
                self.vol_label.set_markup(icons_map["high"])
            elif self.audio.speaker.volume > 0:
                self.vol_label.set_markup(icons_map["medium"])
            else:
                self.vol_label.set_markup(icons_map["off"])


class MicSmall(Box):
    def __init__(self, **kwargs):
        super().__init__(name="button-bar-mic", **kwargs)
        self.audio = Audio()
        
        self._create_widgets()
        self._setup_connections()
        self._on_microphone_changed()

    def _create_widgets(self):
        self.progress_bar = CircularProgressBar(
            name="button-mic", size=28, line_width=2,
            start_angle=150, end_angle=390,
        )
        self.mic_label = Label(name="mic-label", markup=icons.mic)
        self.mic_button = Button(on_clicked=self._toggle_mute, child=self.mic_label)
        self.event_box = EventBox(child=Overlay(child=self.progress_bar, overlays=self.mic_button))
        self.add(self.event_box)

    def _setup_connections(self):
        self.audio.connect("notify::microphone", self._on_new_microphone)
        if self.audio.microphone:
            self.audio.microphone.connect("changed", self._on_microphone_changed)

    def _on_new_microphone(self):
        if self.audio.microphone:
            self.audio.microphone.connect("changed", self._on_microphone_changed)
            self._on_microphone_changed()

    def _toggle_mute(self):
        current_stream = self.audio.microphone
        if current_stream:
            current_stream.muted = not current_stream.muted
            self._on_microphone_changed()

    def _on_microphone_changed(self, *_):
        if not self.audio.microphone:
            return
            
        if self.audio.microphone.muted:
            self.mic_label.set_markup(icons.mic_mute)
            self.progress_bar.add_style_class("muted")
            self.mic_label.add_style_class("muted")
            self.set_tooltip_text("Микрофон выключен")
            return
            
        self.progress_bar.remove_style_class("muted")
        self.mic_label.remove_style_class("muted")
        self.progress_bar.value = self.audio.microphone.volume / 100
        self.set_tooltip_text(f"Микрофон: {round(self.audio.microphone.volume)}%")
        
        if self.audio.microphone.volume >= 1:
            self.mic_label.set_markup(icons.mic)
        else:
            self.mic_label.set_markup(icons.mic_mute)


class BrightnessIcon(Box):
    def __init__(self, **kwargs):
        super().__init__(name="brightness-icon", **kwargs)
        self.brightness = Brightness.get_initial()
        if self.brightness.screen_brightness == -1:
            return
            
        self._create_widgets()
        self._setup_connections()
        self._on_brightness_changed()
    
    def _create_widgets(self):
        self.brightness_label = Label(name="brightness-label-dash", markup=icons.brightness_high)
        self.brightness_button = Button(child=self.brightness_label)
        self.event_box = EventBox(child=self.brightness_button, h_expand=True)
        self.add(self.event_box)
    
    def _setup_connections(self):
        self.brightness.connect("screen", self._on_brightness_changed)
    
    def _on_brightness_changed(self, *args):
        if self.brightness.max_screen == -1:
            return
            
        normalized = self.brightness.screen_brightness / self.brightness.max_screen
        brightness_percentage = int(normalized * 100)
        
        if brightness_percentage >= 75:
            self.brightness_label.set_markup(icons.brightness_high)
        elif brightness_percentage >= 24:
            self.brightness_label.set_markup(icons.brightness_medium)
        else:
            self.brightness_label.set_markup(icons.brightness_low)
        self.set_tooltip_text(f"Яркость: {brightness_percentage}%")


class VolumeIcon(Box):
    def __init__(self, **kwargs):
        super().__init__(name="vol-icon", **kwargs)
        self.audio = Audio()
        
        self._create_widgets()
        self._setup_connections()

    def _create_widgets(self):
        self.vol_label = Label(name="vol-label-dash", markup="")
        self.vol_button = Button(on_clicked=self._toggle_mute, child=self.vol_label)
        self.event_box = EventBox(child=self.vol_button, h_expand=True)
        self.add(self.event_box)

    def _setup_connections(self):
        self.audio.connect("notify::speaker", self._on_new_speaker)
        if self.audio.speaker:
            self.audio.speaker.connect("changed", self._on_speaker_changed)

    def _on_new_speaker(self, *args):
        if self.audio.speaker:
            self.audio.speaker.connect("changed", self._on_speaker_changed)
            self._on_speaker_changed()
            
    def _toggle_mute(self, event):
        current_stream = self.audio.speaker
        if current_stream:
            current_stream.muted = not current_stream.muted
            self._on_speaker_changed()

    def _on_speaker_changed(self, *_):
        if not self.audio.speaker:
            self.vol_label.set_markup("")
            self.remove_style_class("muted")
            self.vol_label.remove_style_class("muted")
            self.vol_button.remove_style_class("muted")
            self.set_tooltip_text("Нет аудиоустройства")
            return

        if self.audio.speaker.muted:
            self.vol_label.set_markup(icons.headphones)
            self.add_style_class("muted")
            self.vol_label.add_style_class("muted")
            self.vol_button.add_style_class("muted")
            self.set_tooltip_text("Без звука")
        else:
            self.remove_style_class("muted")
            self.vol_label.remove_style_class("muted")
            self.vol_button.remove_style_class("muted")
            self._update_device_icon()
            self.set_tooltip_text(f"Громкость: {round(self.audio.speaker.volume)}%")

    def _update_device_icon(self):
        if not self.audio.speaker:
            self.vol_label.set_markup("")
            return True

        if self.audio.speaker.muted:
            return True

        try:
            device_type = self.audio.speaker.port.type
            if device_type == 'headphones':
                self.vol_label.set_markup(icons.headphones)
            elif device_type == 'speaker':
                self.vol_label.set_markup(icons.headphones)
            else:
                self.vol_label.set_markup(icons.headphones)
        except AttributeError:
            self.vol_label.set_markup(icons.headphones)

        return True


class MicIcon(Box):
    def __init__(self, **kwargs):
        super().__init__(name="mic-icon", **kwargs)
        self.audio = Audio()
        
        self._create_widgets()
        self._setup_connections()
        self._on_microphone_changed()
        
    def _create_widgets(self):
        self.mic_label = Label(name="mic-label-dash", markup=icons.mic)
        self.mic_button = Button(on_clicked=self._toggle_mute, child=self.mic_label)
        self.event_box = EventBox(child=self.mic_button, h_expand=True)
        self.add(self.event_box)
    
    def _setup_connections(self):
        self.audio.connect("notify::microphone", self._on_new_microphone)
        if self.audio.microphone:
            self.audio.microphone.connect("changed", self._on_microphone_changed)
            
    def _on_new_microphone(self, *args):
        if self.audio.microphone:
            self.audio.microphone.connect("changed", self._on_microphone_changed)
            self._on_microphone_changed()
            
    def _toggle_mute(self, event):
        current_stream = self.audio.microphone
        if current_stream:
            current_stream.muted = not current_stream.muted
            self._on_microphone_changed()
                
    def _on_microphone_changed(self, *_):
        if not self.audio.microphone:
            return
        if self.audio.microphone.muted:
            self.mic_label.set_markup(icons.mic_mute)
            self.add_style_class("muted")
            self.mic_label.add_style_class("muted")
            self.set_tooltip_text("Микрофон выключен")
            return
            
        self.remove_style_class("muted")
        self.mic_label.remove_style_class("muted")
        self.set_tooltip_text(f"Микрофон: {round(self.audio.microphone.volume)}%")
        
        if self.audio.microphone.volume >= 1:
            self.mic_label.set_markup(icons.mic)
        else:
            self.mic_label.set_markup(icons.mic_mute)


class ControlSliders(Box):
    def __init__(self, **kwargs):
        super().__init__(name="control-sliders", spacing=8, **kwargs)
        
        brightness = Brightness.get_initial()
        
        if brightness.screen_brightness != -1:
            brightness_row = Box(spacing=0, h_expand=True)
            brightness_row.add(BrightnessIcon())
            brightness_row.add(BrightnessSlider())
            self.add(brightness_row)
            
        volume_row = Box(spacing=0, h_expand=True)
        volume_row.add(VolumeIcon())
        volume_row.add(VolumeSlider())
        self.add(volume_row)
        
        mic_row = Box(spacing=0, h_expand=True)
        mic_row.add(MicIcon())
        mic_row.add(MicSlider())
        self.add(mic_row)
        
        self.show_all()


class ControlSmall(Box):
    def __init__(self, **kwargs):
        brightness = Brightness.get_initial()
        children = []
        if brightness.screen_brightness != -1:
            children.append(BrightnessSmall())
        children.extend([VolumeSmall(), MicSmall()])
        super().__init__(
            name="control-small",
            spacing=4,
            children=children,
            **kwargs,
        )
        self.show_all()