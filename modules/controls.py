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

# Синглтон для Audio - избегаем множественных инстансов
_audio_instance = None

def get_audio():
    global _audio_instance
    if _audio_instance is None:
        _audio_instance = Audio()
    return _audio_instance


# Вспомогательные функции вместо миксинов
_BRIGHTNESS_THRESHOLDS = (75, 24)
_BRIGHTNESS_ICONS = (icons.brightness_high, icons.brightness_medium, icons.brightness_low)

def get_brightness_icon(percentage):
    if percentage >= _BRIGHTNESS_THRESHOLDS[0]:
        return _BRIGHTNESS_ICONS[0]
    elif percentage >= _BRIGHTNESS_THRESHOLDS[1]:
        return _BRIGHTNESS_ICONS[1]
    return _BRIGHTNESS_ICONS[2]


# Предвычисленные словари иконок для громкости
ICONS_STANDARD = {
    "high": icons.vol_high, "medium": icons.vol_medium,
    "mute": icons.vol_off, "off": icons.vol_mute
}
ICONS_BLUETOOTH = {
    "high": icons.bluetooth_connected, "medium": icons.bluetooth,
    "mute": icons.bluetooth_off, "off": icons.bluetooth_disconnected
}


class VolumeSlider(Scale):
    def __init__(self, **kwargs):
        super().__init__(name="control-slider", h_expand=True, **kwargs)
        self.audio = get_audio()
        self._update_source_id = None
        
        self.audio.connect("notify::speaker", self._on_new_speaker)
        self._connect_speaker()
        self.connect("value-changed", self._on_value_changed)
        self.add_style_class("vol")
        self._on_speaker_changed()

    def _connect_speaker(self):
        if self.audio.speaker:
            self.audio.speaker.connect("changed", self._on_speaker_changed)

    def _on_new_speaker(self, *args):
        if self.audio.speaker:
            self._connect_speaker()
            self._on_speaker_changed()

    def _on_value_changed(self, _):
        speaker = self.audio.speaker
        if not speaker:
            return
        new_value = self.value * 100
        if self._update_source_id:
            GLib.source_remove(self._update_source_id)
        self._update_source_id = GLib.timeout_add(100, self._update_volume, new_value)

    def _update_volume(self, value):
        if self.audio.speaker:
            self.audio.speaker.volume = value
        self._update_source_id = None
        return False

    def _on_speaker_changed(self, *_):
        speaker = self.audio.speaker
        if not speaker:
            return
        self.value = speaker.volume * 0.01
        if speaker.muted:
            self.add_style_class("muted")
        else:
            self.remove_style_class("muted")


class MicSlider(Scale):
    def __init__(self, **kwargs):
        super().__init__(name="control-slider", h_expand=True, **kwargs)
        self.audio = get_audio()
        
        self.audio.connect("notify::microphone", self._on_new_microphone)
        self._connect_microphone()
        self.connect("value-changed", self._on_value_changed)
        self.add_style_class("mic")
        self._on_microphone_changed()

    def _connect_microphone(self):
        if self.audio.microphone:
            self.audio.microphone.connect("changed", self._on_microphone_changed)

    def _on_new_microphone(self, *args):
        if self.audio.microphone:
            self._connect_microphone()
            self._on_microphone_changed()

    def _on_value_changed(self, _):
        mic = self.audio.microphone
        if mic:
            mic.volume = self.value * 100

    def _on_microphone_changed(self, *_):
        mic = self.audio.microphone
        if not mic:
            return
        self.value = mic.volume * 0.01
        if mic.muted:
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
        self._update_source_id = GLib.timeout_add(100, self._update_brightness, moved_pos)
        return False

    def _update_brightness(self, value):
        if value != self.client.screen_brightness:
            self.client.screen_brightness = value
        self._update_source_id = None
        return False

    def _on_brightness_changed(self, client, _):
        self._updating_from_brightness = True
        brightness = self.client.screen_brightness
        self.set_value(brightness)
        self._updating_from_brightness = False
        percentage = int(brightness * 100 / self.client.max_screen)
        self.set_tooltip_text(f"{percentage}%")


class BrightnessSmall(Box):
    def __init__(self, **kwargs):
        super().__init__(name="button-bar-brightness", **kwargs)
        self.brightness = Brightness.get_initial()
        if self.brightness.screen_brightness == -1:
            return

        self._update_source_id = None
        self._updating_from_brightness = False
        self._create_widgets()
        self.brightness.connect("screen", self._on_brightness_changed)
        self.progress_bar.connect("notify::value", self._on_progress_value_changed)
        self._on_brightness_changed()

    def _create_widgets(self):
        self.progress_bar = CircularProgressBar(
            name="button-brightness", size=28, line_width=2,
            start_angle=150, end_angle=390,
        )
        self.brightness_label = Label(name="brightness-label", markup=icons.brightness_high)
        brightness_button = Button(child=self.brightness_label)
        event_box = EventBox(
            child=Overlay(child=self.progress_bar, overlays=brightness_button)
        )
        self.add(event_box)

    def _on_progress_value_changed(self, widget, pspec):
        if self._updating_from_brightness:
            return
        new_brightness = int(widget.value * self.brightness.max_screen)
        
        if self._update_source_id:
            GLib.source_remove(self._update_source_id)
        self._update_source_id = GLib.timeout_add(100, self._set_brightness, new_brightness)

    def _set_brightness(self, value):
        self.brightness.screen_brightness = value
        self._update_source_id = None
        return False

    def _on_brightness_changed(self, *args):
        max_screen = self.brightness.max_screen
        if max_screen == -1:
            return
        normalized = self.brightness.screen_brightness / max_screen
        self._updating_from_brightness = True
        self.progress_bar.value = normalized
        self._updating_from_brightness = False

        percentage = int(normalized * 100)
        self.brightness_label.set_markup(get_brightness_icon(percentage))
        self.set_tooltip_text(f"Яркость: {percentage}%")


class VolumeSmall(Box):
    def __init__(self, **kwargs):
        super().__init__(name="button-bar-vol", **kwargs)
        self.audio = get_audio()
        self._create_widgets()
        self._setup_connections()
        self._on_speaker_changed()

    def _create_widgets(self):
        self.progress_bar = CircularProgressBar(
            name="button-volume", size=28, line_width=2,
            start_angle=150, end_angle=390,
        )
        self.vol_label = Label(name="vol-label", markup=icons.vol_high)
        vol_button = Button(on_clicked=self._toggle_mute, child=self.vol_label)
        event_box = EventBox(
            child=Overlay(child=self.progress_bar, overlays=vol_button)
        )
        self.add(event_box)

    def _setup_connections(self):
        self.audio.connect("notify::speaker", self._on_new_speaker)
        if self.audio.speaker:
            self.audio.speaker.connect("changed", self._on_speaker_changed)

    def _on_new_speaker(self, *args):
        speaker = self.audio.speaker
        if speaker:
            speaker.connect("changed", self._on_speaker_changed)
            self._on_speaker_changed()

    def _toggle_mute(self, *args):
        speaker = self.audio.speaker
        if speaker:
            speaker.muted = not speaker.muted

    def _on_speaker_changed(self, *_):
        speaker = self.audio.speaker
        if not speaker:
            return
        
        is_bluetooth = "bluetooth" in speaker.icon_name
        icons_map = ICONS_BLUETOOTH if is_bluetooth else ICONS_STANDARD
        volume = speaker.volume
        
        self.progress_bar.value = volume * 0.01
        
        if speaker.muted:
            self.vol_label.set_markup(icons_map["mute"])
            self.progress_bar.add_style_class("muted")
            self.vol_label.add_style_class("muted")
            self.set_tooltip_text("Без звука")
        else:
            self.progress_bar.remove_style_class("muted")
            self.vol_label.remove_style_class("muted")
            self.set_tooltip_text(f"Громкость: {int(volume)}%")
            
            if volume > 74:
                icon_key = "high"
            elif volume > 0:
                icon_key = "medium"
            else:
                icon_key = "off"
            self.vol_label.set_markup(icons_map[icon_key])


class MicSmall(Box):
    def __init__(self, **kwargs):
        super().__init__(name="button-bar-mic", **kwargs)
        self.audio = get_audio()
        self._create_widgets()
        self._setup_connections()
        self._on_microphone_changed()

    def _create_widgets(self):
        self.progress_bar = CircularProgressBar(
            name="button-mic", size=28, line_width=2,
            start_angle=150, end_angle=390,
        )
        self.mic_label = Label(name="mic-label", markup=icons.mic)
        mic_button = Button(on_clicked=self._toggle_mute, child=self.mic_label)
        event_box = EventBox(child=Overlay(child=self.progress_bar, overlays=mic_button))
        self.add(event_box)

    def _setup_connections(self):
        self.audio.connect("notify::microphone", self._on_new_microphone)
        if self.audio.microphone:
            self.audio.microphone.connect("changed", self._on_microphone_changed)

    def _on_new_microphone(self, *args):
        mic = self.audio.microphone
        if mic:
            mic.connect("changed", self._on_microphone_changed)
            self._on_microphone_changed()

    def _toggle_mute(self, *args):
        mic = self.audio.microphone
        if mic:
            mic.muted = not mic.muted

    def _on_microphone_changed(self, *_):
        mic = self.audio.microphone
        if not mic:
            return
            
        if mic.muted:
            self.mic_label.set_markup(icons.mic_mute)
            self.progress_bar.add_style_class("muted")
            self.mic_label.add_style_class("muted")
            self.set_tooltip_text("Микрофон выключен")
            return
            
        self.progress_bar.remove_style_class("muted")
        self.mic_label.remove_style_class("muted")
        volume = mic.volume
        self.progress_bar.value = volume * 0.01
        self.set_tooltip_text(f"Микрофон: {int(volume)}%")
        self.mic_label.set_markup(icons.mic if volume >= 1 else icons.mic_mute)


class BrightnessIcon(Box):
    def __init__(self, **kwargs):
        super().__init__(name="brightness-icon", **kwargs)
        self.brightness = Brightness.get_initial()
        if self.brightness.screen_brightness == -1:
            return
            
        self.brightness_label = Label(name="brightness-label-dash", markup=icons.brightness_high)
        brightness_button = Button(child=self.brightness_label)
        self.add(EventBox(child=brightness_button, h_expand=True))
        self.brightness.connect("screen", self._on_brightness_changed)
        self._on_brightness_changed()
    
    def _on_brightness_changed(self, *args):
        max_screen = self.brightness.max_screen
        if max_screen == -1:
            return
            
        percentage = int(self.brightness.screen_brightness * 100 / max_screen)
        self.brightness_label.set_markup(get_brightness_icon(percentage))
        self.set_tooltip_text(f"Яркость: {percentage}%")


class VolumeIcon(Box):
    def __init__(self, **kwargs):
        super().__init__(name="vol-icon", **kwargs)
        self.audio = get_audio()
        
        self.vol_label = Label(name="vol-label-dash", markup="")
        self.vol_button = Button(on_clicked=self._toggle_mute, child=self.vol_label)
        self.add(EventBox(child=self.vol_button, h_expand=True))
        
        self.audio.connect("notify::speaker", self._on_new_speaker)
        if self.audio.speaker:
            self.audio.speaker.connect("changed", self._on_speaker_changed)
            self._on_speaker_changed()

    def _on_new_speaker(self, *args):
        speaker = self.audio.speaker
        if speaker:
            speaker.connect("changed", self._on_speaker_changed)
            self._on_speaker_changed()
            
    def _toggle_mute(self, *args):
        speaker = self.audio.speaker
        if speaker:
            speaker.muted = not speaker.muted

    def _on_speaker_changed(self, *_):
        speaker = self.audio.speaker
        if not speaker:
            self.vol_label.set_markup("")
            self._set_muted_style(False)
            self.set_tooltip_text("Нет аудиоустройства")
            return

        if speaker.muted:
            self.vol_label.set_markup(icons.headphones)
            self._set_muted_style(True)
            self.set_tooltip_text("Без звука")
        else:
            self._set_muted_style(False)
            self.vol_label.set_markup(icons.headphones)
            self.set_tooltip_text(f"Громкость: {int(speaker.volume)}%")

    def _set_muted_style(self, muted):
        if muted:
            self.add_style_class("muted")
            self.vol_label.add_style_class("muted")
            self.vol_button.add_style_class("muted")
        else:
            self.remove_style_class("muted")
            self.vol_label.remove_style_class("muted")
            self.vol_button.remove_style_class("muted")


class MicIcon(Box):
    def __init__(self, **kwargs):
        super().__init__(name="mic-icon", **kwargs)
        self.audio = get_audio()
        
        self.mic_label = Label(name="mic-label-dash", markup=icons.mic)
        mic_button = Button(on_clicked=self._toggle_mute, child=self.mic_label)
        self.add(EventBox(child=mic_button, h_expand=True))
        
        self.audio.connect("notify::microphone", self._on_new_microphone)
        if self.audio.microphone:
            self.audio.microphone.connect("changed", self._on_microphone_changed)
        self._on_microphone_changed()
            
    def _on_new_microphone(self, *args):
        mic = self.audio.microphone
        if mic:
            mic.connect("changed", self._on_microphone_changed)
            self._on_microphone_changed()
            
    def _toggle_mute(self, *args):
        mic = self.audio.microphone
        if mic:
            mic.muted = not mic.muted
                
    def _on_microphone_changed(self, *_):
        mic = self.audio.microphone
        if not mic:
            return
            
        if mic.muted:
            self.mic_label.set_markup(icons.mic_mute)
            self.add_style_class("muted")
            self.mic_label.add_style_class("muted")
            self.set_tooltip_text("Микрофон выключен")
            return
            
        self.remove_style_class("muted")
        self.mic_label.remove_style_class("muted")
        volume = mic.volume
        self.set_tooltip_text(f"Микрофон: {int(volume)}%")
        self.mic_label.set_markup(icons.mic if volume >= 1 else icons.mic_mute)


class ControlSliders(Box):
    def __init__(self, **kwargs):
        super().__init__(name="control-sliders", spacing=8, **kwargs)
        
        brightness = Brightness.get_initial()
        
        if brightness.screen_brightness != -1:
            brightness_row = Box(spacing=0, h_expand=True, children=[BrightnessIcon(), BrightnessSlider()])
            self.add(brightness_row)
            
        volume_row = Box(spacing=0, h_expand=True, children=[VolumeIcon(), VolumeSlider()])
        self.add(volume_row)
        
        mic_row = Box(spacing=0, h_expand=True, children=[MicIcon(), MicSlider()])
        self.add(mic_row)
        
        self.show_all()


class ControlSmall(Box):
    def __init__(self, **kwargs):
        brightness = Brightness.get_initial()
        children = []
        if brightness.screen_brightness != -1:
            children.append(BrightnessSmall())
        children.extend([VolumeSmall(), MicSmall()])
        super().__init__(name="control-small", spacing=4, children=children, **kwargs)
        self.show_all()