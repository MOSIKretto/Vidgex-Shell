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
import services.icons as icons

_audio = None

def _get_audio():
    global _audio
    if _audio is None:
        _audio = Audio()
    return _audio

# Публичный алиас для совместимости
get_audio = _get_audio

_BTH = (75, 24)
_BIC = (icons.brightness_high, icons.brightness_medium, icons.brightness_low)

def _bicon(p):
    return _BIC[0] if p >= _BTH[0] else (_BIC[1] if p >= _BTH[1] else _BIC[2])

_IS = {"high": icons.vol_high, "medium": icons.vol_medium, "mute": icons.vol_off, "off": icons.vol_mute}
_IB = {"high": icons.bluetooth_connected, "medium": icons.bluetooth, "mute": icons.bluetooth_off, "off": icons.bluetooth_disconnected}


class VolumeSlider(Scale):
    __slots__ = ('audio', '_upd', '_s', '_hid')

    def __init__(self, **kwargs):
        super().__init__(name="control-slider", orientation="h", h_expand=True, h_align="fill",
                        has_origin=True, increments=(0.01, 0.1), **kwargs)
        self.audio = _get_audio()
        self._upd = False
        self._s = None
        self._hid = None

        self.add_style_class("vol")
        self.audio.connect("notify::speaker", self._new_spk)
        self.connect("value-changed", self._val_chg)
        self._new_spk()

    def _new_spk(self, *_):
        if self._s and self._hid:
            try:
                self._s.disconnect(self._hid)
            except:
                pass
        self._s = self.audio.speaker
        if self._s:
            self._hid = self._s.connect("changed", self._ui)
            self._ui()

    def _ui(self, *_):
        if not self._s:
            return
        self._upd = True
        nv = self._s.volume * 0.01
        if abs(self.value - nv) > 0.005:
            self.value = nv
            self.set_tooltip_text(f"{self._s.volume:.0f}%")
        ctx = self.get_style_context()
        has = "muted" in ctx.list_classes()
        if self._s.muted and not has:
            self.add_style_class("muted")
        elif not self._s.muted and has:
            self.remove_style_class("muted")
        self._upd = False

    def _val_chg(self, _):
        if self._upd or not self._s:
            return
        nv = self.value * 100
        if abs(self._s.volume - nv) > 0.5:
            self._s.volume = nv
            self.set_tooltip_text(f"{nv:.0f}%")

    def cleanup(self):
        if self._s and self._hid:
            try:
                self._s.disconnect(self._hid)
            except:
                pass
        self._s = None


class MicSlider(Scale):
    __slots__ = ('audio', '_upd', '_s', '_hid')

    def __init__(self, **kwargs):
        super().__init__(name="control-slider", orientation="h", h_expand=True, h_align="fill",
                        has_origin=True, increments=(0.01, 0.1), **kwargs)
        self.audio = _get_audio()
        self._upd = False
        self._s = None
        self._hid = None

        self.add_style_class("mic")
        self.audio.connect("notify::microphone", self._new_mic)
        self.connect("value-changed", self._val_chg)
        self._new_mic()

    def _new_mic(self, *_):
        if self._s and self._hid:
            try:
                self._s.disconnect(self._hid)
            except:
                pass
        self._s = self.audio.microphone
        if self._s:
            self._hid = self._s.connect("changed", self._ui)
            self._ui()

    def _ui(self, *_):
        if not self._s:
            return
        self._upd = True
        nv = self._s.volume * 0.01
        if abs(self.value - nv) > 0.005:
            self.value = nv
            self.set_tooltip_text(f"{self._s.volume:.0f}%")
        ctx = self.get_style_context()
        has = "muted" in ctx.list_classes()
        if self._s.muted and not has:
            self.add_style_class("muted")
        elif not self._s.muted and has:
            self.remove_style_class("muted")
        self._upd = False

    def _val_chg(self, _):
        if self._upd or not self._s:
            return
        nv = self.value * 100
        if abs(self._s.volume - nv) > 0.5:
            self._s.volume = nv
            self.set_tooltip_text(f"{nv:.0f}%")

    def cleanup(self):
        if self._s and self._hid:
            try:
                self._s.disconnect(self._hid)
            except:
                pass
        self._s = None


class BrightnessSlider(Scale):
    __slots__ = ('client', '_upd', '_tid', '_target')

    def __init__(self, **kwargs):
        super().__init__(name="control-slider", orientation="h", h_expand=True, h_align="fill",
                        has_origin=True, increments=(0.01, 0.1), **kwargs)
        self.client = Brightness.get_initial()
        self._upd = False
        self._tid = None
        self._target = -1

        if self.client.max_screen <= 0:
            self.set_no_show_all(True)
            self.hide()
            return

        self.add_style_class("brightness")
        self.connect("value-changed", self._val_chg)
        self.client.connect("screen", self._br_chg)
        self._br_chg(None, self.client.screen_brightness)

    def _val_chg(self, _):
        if self._upd:
            return
        
        val = self.get_value()
        self.set_tooltip_text(f"{int(val * 100)}%")
        self._target = int(val * self.client.max_screen)
        
        # Debounce: отменяем предыдущий таймер и ставим новый
        if self._tid:
            GLib.source_remove(self._tid)
        self._tid = GLib.timeout_add(30, self._apply)

    def _apply(self):
        self._tid = None
        if self._target >= 0 and self._target != self.client.screen_brightness:
            self.client.screen_brightness = self._target
        return False

    def _br_chg(self, _, cur):
        # Игнорируем пока есть pending изменение от пользователя
        if self._tid:
            return
        if not self.client._valid or self.client.max_screen <= 0:
            return
        
        n = cur / self.client.max_screen
        # Игнорируем мелкие изменения (уже близко к текущему)
        if abs(self.get_value() - n) < 0.008:
            return
        
        self._upd = True
        self.set_value(n)
        self.set_tooltip_text(f"{int(n * 100)}%")
        self._upd = False

    def cleanup(self):
        if self._tid:
            GLib.source_remove(self._tid)
            self._tid = None


class BrightnessSmall(Box):
    __slots__ = ('brightness', 'progress_bar', 'brightness_label')

    def __init__(self, **kwargs):
        super().__init__(name="button-bar-brightness", **kwargs)
        self.brightness = Brightness.get_initial()
        if self.brightness.screen_brightness == -1:
            return

        self.progress_bar = CircularProgressBar(name="button-brightness", size=28, line_width=2,
                                                 start_angle=150, end_angle=390)
        self.brightness_label = Label(name="brightness-label", markup=icons.brightness_high)
        self.add(Overlay(child=self.progress_bar, overlays=self.brightness_label))

        self.brightness.connect("screen", self._chg)
        self._chg()

    def _chg(self, *_):
        mx = self.brightness.max_screen
        if mx <= 0:
            return
        n = self.brightness.screen_brightness / mx
        if abs(self.progress_bar.value - n) > 0.005:
            self.progress_bar.value = n
            p = int(n * 100)
            self.brightness_label.set_markup(_bicon(p))
            self.set_tooltip_text(f"Яркость: {p}%")


class VolumeSmall(Box):
    __slots__ = ('audio', '_s', '_hid', 'progress_bar', 'vol_label')

    def __init__(self, **kwargs):
        super().__init__(name="button-bar-vol", **kwargs)
        self.audio = _get_audio()
        self._s = None
        self._hid = None

        self.progress_bar = CircularProgressBar(name="button-volume", size=28, line_width=2,
                                                 start_angle=150, end_angle=390)
        self.vol_label = Label(name="vol-label", markup=icons.vol_high)
        self.add(Overlay(child=self.progress_bar, overlays=self.vol_label))

        self.audio.connect("notify::speaker", self._new_spk)
        self._new_spk()

    def _new_spk(self, *_):
        if self._s and self._hid:
            try:
                self._s.disconnect(self._hid)
            except:
                pass
        self._s = self.audio.speaker
        if self._s:
            self._hid = self._s.connect("changed", self._ui)
            self._ui()

    def _ui(self, *_):
        s = self._s
        if not s:
            return

        vn = s.volume * 0.01
        if abs(self.progress_bar.value - vn) > 0.005:
            self.progress_bar.value = vn

        is_bt = "bluetooth" in s.icon_name
        im = _IB if is_bt else _IS

        if s.muted:
            self.vol_label.set_markup(im["mute"])
            self.progress_bar.add_style_class("muted")
            self.vol_label.add_style_class("muted")
            self.set_tooltip_text("Без звука")
        else:
            self.progress_bar.remove_style_class("muted")
            self.vol_label.remove_style_class("muted")
            self.set_tooltip_text(f"Громкость: {int(s.volume)}%")
            self.vol_label.set_markup(im["high"] if s.volume > 74 else (im["medium"] if s.volume > 0 else im["off"]))

    def cleanup(self):
        if self._s and self._hid:
            try:
                self._s.disconnect(self._hid)
            except:
                pass
        self._s = None


class MicSmall(Box):
    __slots__ = ('audio', '_s', '_hid', 'progress_bar', 'mic_label')

    def __init__(self, **kwargs):
        super().__init__(name="button-bar-mic", **kwargs)
        self.audio = _get_audio()
        self._s = None
        self._hid = None

        self.progress_bar = CircularProgressBar(name="button-mic", size=28, line_width=2,
                                                 start_angle=150, end_angle=390)
        self.mic_label = Label(name="mic-label", markup=icons.mic)
        self.add(Overlay(child=self.progress_bar, overlays=self.mic_label))

        self.audio.connect("notify::microphone", self._new_mic)
        self._new_mic()

    def _new_mic(self, *_):
        if self._s and self._hid:
            try:
                self._s.disconnect(self._hid)
            except:
                pass
        self._s = self.audio.microphone
        if self._s:
            self._hid = self._s.connect("changed", self._ui)
            self._ui()

    def _ui(self, *_):
        m = self._s
        if not m:
            return

        vn = m.volume * 0.01
        if abs(self.progress_bar.value - vn) > 0.005:
            self.progress_bar.value = vn

        if m.muted:
            self.mic_label.set_markup(icons.mic_mute)
            self.progress_bar.add_style_class("muted")
            self.mic_label.add_style_class("muted")
            self.set_tooltip_text("Микрофон выключен")
        else:
            self.progress_bar.remove_style_class("muted")
            self.mic_label.remove_style_class("muted")
            self.set_tooltip_text(f"Микрофон: {int(m.volume)}%")
            self.mic_label.set_markup(icons.mic if m.volume >= 1 else icons.mic_mute)

    def cleanup(self):
        if self._s and self._hid:
            try:
                self._s.disconnect(self._hid)
            except:
                pass
        self._s = None


class BrightnessIcon(Box):
    __slots__ = ('brightness', 'brightness_label')

    def __init__(self, **kwargs):
        super().__init__(name="brightness-icon", **kwargs)
        self.brightness = Brightness.get_initial()
        if self.brightness.screen_brightness == -1:
            return

        self.brightness_label = Label(name="brightness-label-dash", markup=icons.brightness_high)
        self.add(EventBox(child=Button(child=self.brightness_label), h_expand=True))

        self.brightness.connect("screen", self._chg)
        self._chg()

    def _chg(self, *_):
        mx = self.brightness.max_screen
        if mx <= 0:
            return
        p = int(self.brightness.screen_brightness * 100 / mx)
        self.brightness_label.set_markup(_bicon(p))
        self.set_tooltip_text(f"Яркость: {p}%")


class VolumeIcon(Box):
    __slots__ = ('audio', '_s', '_hid', 'vol_label', 'vol_button')

    def __init__(self, **kwargs):
        super().__init__(name="vol-icon", **kwargs)
        self.audio = _get_audio()
        self._s = None
        self._hid = None

        self.vol_label = Label(name="vol-label-dash", markup="")
        self.vol_button = Button(on_clicked=self._tog, child=self.vol_label)
        self.add(EventBox(child=self.vol_button, h_expand=True))

        self.audio.connect("notify::speaker", self._new_spk)
        self._new_spk()

    def _new_spk(self, *_):
        if self._s and self._hid:
            try:
                self._s.disconnect(self._hid)
            except:
                pass
        self._s = self.audio.speaker
        if self._s:
            self._hid = self._s.connect("changed", self._ui)
            self._ui()

    def _tog(self, *_):
        if self._s:
            self._s.muted = not self._s.muted

    def _ui(self, *_):
        s = self._s
        if not s:
            self.vol_label.set_markup("")
            self._mstyle(False)
            return

        self.vol_label.set_markup(icons.headphones)
        if s.muted:
            self._mstyle(True)
            self.set_tooltip_text("Без звука")
        else:
            self._mstyle(False)
            self.set_tooltip_text(f"Громкость: {int(s.volume)}%")

    def _mstyle(self, m):
        has = "muted" in self.get_style_context().list_classes()
        if m and not has:
            self.add_style_class("muted")
            self.vol_label.add_style_class("muted")
            self.vol_button.add_style_class("muted")
        elif not m and has:
            self.remove_style_class("muted")
            self.vol_label.remove_style_class("muted")
            self.vol_button.remove_style_class("muted")

    def cleanup(self):
        if self._s and self._hid:
            try:
                self._s.disconnect(self._hid)
            except:
                pass
        self._s = None


class MicIcon(Box):
    __slots__ = ('audio', '_s', '_hid', 'mic_label', '_btn')

    def __init__(self, **kwargs):
        super().__init__(name="mic-icon", **kwargs)
        self.audio = _get_audio()
        self._s = None
        self._hid = None

        self.mic_label = Label(name="mic-label-dash", markup=icons.mic)
        self._btn = Button(on_clicked=self._tog, child=self.mic_label)
        self.add(EventBox(child=self._btn, h_expand=True))

        self.audio.connect("notify::microphone", self._new_mic)
        self._new_mic()

    def _new_mic(self, *_):
        if self._s and self._hid:
            try:
                self._s.disconnect(self._hid)
            except:
                pass
        self._s = self.audio.microphone
        if self._s:
            self._hid = self._s.connect("changed", self._ui)
            self._ui()

    def _tog(self, *_):
        if self._s:
            self._s.muted = not self._s.muted

    def _ui(self, *_):
        m = self._s
        if not m:
            return

        has = "muted" in self.get_style_context().list_classes()

        if m.muted:
            self.mic_label.set_markup(icons.mic_mute)
            if not has:
                self.add_style_class("muted")
                self.mic_label.add_style_class("muted")
            self.set_tooltip_text("Микрофон выключен")
        else:
            if has:
                self.remove_style_class("muted")
                self.mic_label.remove_style_class("muted")
            v = int(m.volume)
            self.set_tooltip_text(f"Микрофон: {v}%")
            self.mic_label.set_markup(icons.mic if v >= 1 else icons.mic_mute)

    def cleanup(self):
        if self._s and self._hid:
            try:
                self._s.disconnect(self._hid)
            except:
                pass
        self._s = None


class ControlSliders(Box):
    __slots__ = ('_br', '_vol', '_mic')

    def __init__(self, **kwargs):
        super().__init__(name="control-sliders", spacing=8, **kwargs)

        br = Brightness.get_initial()

        if br.screen_brightness != -1:
            self._br = Box(spacing=0, h_expand=True, children=[BrightnessIcon(), BrightnessSlider()])
            self.add(self._br)
        else:
            self._br = None

        self._vol = Box(spacing=0, h_expand=True, children=[VolumeIcon(), VolumeSlider()])
        self._mic = Box(spacing=0, h_expand=True, children=[MicIcon(), MicSlider()])

        self.add(self._vol)
        self.add(self._mic)
        self.show_all()

    def cleanup(self):
        for box in (self._vol, self._mic):
            for c in box.get_children():
                if hasattr(c, 'cleanup'):
                    c.cleanup()


class ControlSmall(Box):
    __slots__ = ('_widgets',)

    def __init__(self, **kwargs):
        br = Brightness.get_initial()
        ch = []
        if br.screen_brightness != -1:
            ch.append(BrightnessSmall())
        ch.extend([VolumeSmall(), MicSmall()])

        super().__init__(name="control-small", spacing=4, children=ch, **kwargs)
        self._widgets = ch
        self.show_all()

    def cleanup(self):
        for w in self._widgets:
            if hasattr(w, 'cleanup'):
                w.cleanup()
        self._widgets = []