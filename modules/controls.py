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


class AudioDeviceWidget:
    def __init__(self, device_type):
        self.audio = Audio()
        self.device_type = device_type
        self.device = None
        self._setup_audio_connections()
    
    def _setup_audio_connections(self):
        self.audio.connect(f"notify::{self.device_type}", self._on_new_device)
        self.device = getattr(self.audio, self.device_type)
        if self.device:
            self.device.connect("changed", self._on_device_changed)
    
    def _on_new_device(self, *args):
        self.device = getattr(self.audio, self.device_type)
        if self.device:
            self.device.connect("changed", self._on_device_changed)
        self._on_device_changed()


class DelayedUpdateMixin:
    def _init_delayed_update(self):
        self._pending_value = None
        self._update_source_id = None
    
    def _schedule_update(self, callback, delay=100):
        if self._update_source_id:
            GLib.source_remove(self._update_source_id)
        self._update_source_id = GLib.timeout_add(delay, callback)
    
    def _cleanup_delayed_update(self):
        if self._update_source_id:
            GLib.source_remove(self._update_source_id)
            self._update_source_id = None


class MutedStyleMixin:
    def _update_muted_style(self, is_muted, widgets=None):
        if widgets is None:
            widgets = [self]
        
        for widget in widgets:
            if is_muted:
                widget.add_style_class("muted")
            else:
                widget.remove_style_class("muted")


class VolumeSlider(Scale, MutedStyleMixin):
    def __init__(self, **kwargs):
        super().__init__(name="control-slider", h_expand=True, **kwargs)
        self.audio = Audio()
        self._pending_value = None
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
            self._pending_value = self.value * 100
            if self._update_source_id:
                GLib.source_remove(self._update_source_id)
            self._update_source_id = GLib.timeout_add(100, self._update_volume_callback)

    def _update_volume_callback(self):
        if self._pending_value is not None and self.audio.speaker:
            self.audio.speaker.volume = self._pending_value
            self._pending_value = None
        self._update_source_id = None
        return False

    def _on_speaker_changed(self, *_):
        if not self.audio.speaker:
            return
        self.value = self.audio.speaker.volume / 100
        self._update_muted_style(self.audio.speaker.muted)


class MicSlider(Scale, MutedStyleMixin):
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
        self._update_muted_style(self.audio.microphone.muted)


class BrightnessSlider(Scale, DelayedUpdateMixin):
    def __init__(self, **kwargs):
        super().__init__(name="control-slider", h_expand=True, **kwargs)
        self.client = Brightness.get_initial()
        if self.client.screen_brightness == -1:
            self.destroy()
            return

        self.set_range(0, self.client.max_screen)
        self.set_value(self.client.screen_brightness)
        self.add_style_class("brightness")

        self._init_delayed_update()
        self._updating_from_brightness = False

        self.connect("change-value", self._on_scale_move)
        self.client.connect("screen", self._on_brightness_changed)

    def _on_scale_move(self, widget, scroll, moved_pos):
        if self._updating_from_brightness:
            return False
        self._pending_value = moved_pos
        self._schedule_update(self._update_brightness_callback)
        return False

    def _update_brightness_callback(self):
        if self._pending_value is not None:
            value_to_set = self._pending_value
            self._pending_value = None
            if value_to_set != self.client.screen_brightness:
                self.client.screen_brightness = value_to_set
            self._update_source_id = None
            return False
        self._update_source_id = None
        return False

    def _on_brightness_changed(self, client, _):
        self._updating_from_brightness = True
        self.set_value(self.client.screen_brightness)
        self._updating_from_brightness = False
        percentage = int((self.client.screen_brightness / self.client.max_screen) * 100)
        self.set_tooltip_text(f"{percentage}%")

    def destroy(self):
        self._cleanup_delayed_update()
        super().destroy()


class BrightnessSmall(Box, DelayedUpdateMixin):
    def __init__(self, **kwargs):
        super().__init__(name="button-bar-brightness", **kwargs)
        self.brightness = Brightness.get_initial()
        if self.brightness.screen_brightness == -1:
            self.destroy()
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
        self._init_delayed_update()
        self._updating_from_brightness = False

        self.progress_bar.connect("notify::value", self._on_progress_value_changed)
        self.brightness.connect("screen", self._on_brightness_changed)
        self._on_brightness_changed()

    def _on_progress_value_changed(self, widget, pspec):
        if self._updating_from_brightness:
            return
        new_norm = widget.value
        new_brightness = int(new_norm * self.brightness.max_screen)
        self._pending_value = new_brightness
        self._schedule_update(self._update_brightness_callback)

    def _update_brightness_callback(self):
        if self._pending_value is not None and self._pending_value != self.brightness.screen_brightness:
            self.brightness.screen_brightness = self._pending_value
            self._pending_value = None
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
        if brightness_percentage >= 75: self.brightness_label.set_markup(icons.brightness_high)
        elif brightness_percentage >= 24: self.brightness_label.set_markup(icons.brightness_medium)
        else: self.brightness_label.set_markup(icons.brightness_low)
        self.set_tooltip_text(f"Яркость: {brightness_percentage}%")

    def destroy(self):
        self._cleanup_delayed_update()
        super().destroy()


class VolumeSmall(Box, MutedStyleMixin):
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
            self._update_muted_style(True, [self.progress_bar, self.vol_label])
            self.set_tooltip_text("Без звука")
        else:
            self._update_muted_style(False, [self.progress_bar, self.vol_label])
            self.set_tooltip_text(f"Громкость: {round(self.audio.speaker.volume)}%")
            
            if self.audio.speaker.volume > 74:
                self.vol_label.set_markup(icons_map["high"])
            elif self.audio.speaker.volume > 0:
                self.vol_label.set_markup(icons_map["medium"])
            else:
                self.vol_label.set_markup(icons_map["off"])


class MicSmall(Box, MutedStyleMixin):
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
            self._update_muted_style(True, [self.progress_bar, self.mic_label])
            self.set_tooltip_text("Микрофон выключен")
            return
            
        self._update_muted_style(False, [self.progress_bar, self.mic_label])
        self.progress_bar.value = self.audio.microphone.volume / 100
        self.set_tooltip_text(f"Микрофон: {round(self.audio.microphone.volume)}%")
        
        if self.audio.microphone.volume >= 1:
            self.mic_label.set_markup(icons.mic)
        else:
            self.mic_label.set_markup(icons.mic_mute)


class BrightnessIcon(Box, DelayedUpdateMixin):
    def __init__(self, **kwargs):
        super().__init__(name="brightness-icon", **kwargs)
        self.brightness = Brightness.get_initial()
        if self.brightness.screen_brightness == -1:
            self.destroy()
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
        self._init_delayed_update()
        self._updating_from_brightness = False
        self.brightness.connect("screen", self._on_brightness_changed)
    
    def _on_brightness_changed(self, *args):
        if self.brightness.max_screen == -1:
            return
            
        self._updating_from_brightness = True
        normalized = self.brightness.screen_brightness / self.brightness.max_screen
        brightness_percentage = int(normalized * 100)
        
        if brightness_percentage >= 75:
            self.brightness_label.set_markup(icons.brightness_high)
        elif brightness_percentage >= 24:
            self.brightness_label.set_markup(icons.brightness_medium)
        else:
            self.brightness_label.set_markup(icons.brightness_low)
        self.set_tooltip_text(f"Яркость: {brightness_percentage}%")
        self._updating_from_brightness = False
        
    def destroy(self):
        self._cleanup_delayed_update()
        super().destroy()


class VolumeIcon(Box, MutedStyleMixin):
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
        self._pending_value = None
        self._update_source_id = None
        self._periodic_update_source_id = GLib.timeout_add_seconds(2, self._update_device_icon)

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
            self._remove_muted_style()
            self.set_tooltip_text("Нет аудиоустройства")
            return

        if self.audio.speaker.muted:
            self.vol_label.set_markup(icons.headphones)
            self._add_muted_style()
            self.set_tooltip_text("Без звука")
        else:
            self._remove_muted_style()
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

    def _add_muted_style(self):
        self._update_muted_style(True, [self, self.vol_label, self.vol_button])

    def _remove_muted_style(self):
        self._update_muted_style(False, [self, self.vol_label, self.vol_button])

    def destroy(self):
        if self._update_source_id:
            GLib.source_remove(self._update_source_id)
        if self._periodic_update_source_id:
            GLib.source_remove(self._periodic_update_source_id)
        super().destroy()


class MicIcon(Box, MutedStyleMixin):
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
        self._pending_value = None
        self._update_source_id = None
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
            self._update_muted_style(True, [self, self.mic_label])
            self.set_tooltip_text("Микрофон выключен")
            return
            
        self._update_muted_style(False, [self, self.mic_label])
        self.set_tooltip_text(f"Микрофон: {round(self.audio.microphone.volume)}%")
        
        if self.audio.microphone.volume >= 1:
            self.mic_label.set_markup(icons.mic)
        else:
            self.mic_label.set_markup(icons.mic_mute)
            
    def destroy(self):
        if self._update_source_id:
            GLib.source_remove(self._update_source_id)
        super().destroy()


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