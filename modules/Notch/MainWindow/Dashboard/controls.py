from fabric.audio.service import Audio
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.circularprogressbar import CircularProgressBar
from fabric.widgets.eventbox import EventBox
from fabric.widgets.label import Label
from fabric.widgets.overlay import Overlay
from fabric.widgets.scale import Scale
from gi.repository import Gdk, Gtk, GLib

from modules.Notch.MainWindow.Dashboard.Controls.brightness import Brightness
import services.icons as icons


_audio = None

def get_audio():
    global _audio
    if _audio is None:
        _audio = Audio()
    return _audio

_BTH = (75, 24)
_BIC = (icons.brightness_high, icons.brightness_medium, icons.brightness_low)

def _bicon(p):
    return _BIC[0] if p >= _BTH[0] else (_BIC[1] if p >= _BTH[1] else _BIC[2])

_IS = {"high": icons.vol_high, "medium": icons.vol_medium, "mute": icons.vol_off, "off": icons.vol_mute}
_IB = {"high": icons.bluetooth_connected, "medium": icons.bluetooth, "mute": icons.bluetooth_off, "off": icons.bluetooth_disconnected}

_ANIM_STEPS = 25
_ANIM_INTERVAL_MS = 16

_CLICK_STEPS = 20
_CLICK_MS = 14

_pointer_cursor: Gdk.Cursor | None = None
_default_cursor: Gdk.Cursor | None = None

def _get_cursors(display: Gdk.Display):
    global _pointer_cursor, _default_cursor
    if _pointer_cursor is None:
        _pointer_cursor = Gdk.Cursor.new_from_name(display, "pointer")
        _default_cursor = Gdk.Cursor.new_from_name(display, "default")
    return _pointer_cursor, _default_cursor

def _on_btn_enter(widget: Gtk.Widget, _event: Gdk.EventCrossing):
    if win := widget.get_window():
        win.set_cursor(_get_cursors(win.get_display())[0])
    return False

def _on_btn_leave(widget: Gtk.Widget, _event: Gdk.EventCrossing):
    if win := widget.get_window():
        win.set_cursor(_get_cursors(win.get_display())[1])
    return False

def _setup_pointer_cursor(widget: Gtk.Widget):
    widget.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
    widget.connect("enter-notify-event", _on_btn_enter)
    widget.connect("leave-notify-event", _on_btn_leave)

def _ease_out_cubic(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3

def _val_from_x(scale: Gtk.Widget, x: float) -> float:
    w = scale.get_allocation().width
    if w <= 0:
        return 0.0
    return max(0.0, min(1.0, x / w))


class _AudioScale(Scale):
    __slots__ = ('audio', '_upd', '_s', '_hid', '_audio_hid', '_last_pct', '_type',
                 '_canim_id', '_canim_s', '_canim_e', '_canim_n', '_pressed')

    def __init__(self, stream_type, style, **kwargs):
        super().__init__(name="control-slider", orientation="h", h_expand=True, h_align="fill", has_origin=True, increments=(0.01, 0.1), **kwargs)
        self.audio = get_audio()
        self._type = stream_type
        self._upd = False
        self._s = self._hid = self._audio_hid = None
        self._last_pct = -1

        self._canim_id = None
        self._canim_s = self._canim_e = 0.0
        self._canim_n = 0
        self._pressed = False

        self.add_style_class(style)
        self._audio_hid = self.audio.connect(f"notify::{stream_type}", self._new_stream)
        self.connect("value-changed", self._val_chg)
        self.connect("button-press-event", self._on_click_press)
        self.connect("button-release-event", self._on_click_release)
        self.connect("motion-notify-event", self._on_click_motion)
        self._new_stream()

    def _on_click_press(self, _, event):
        if event.button != 1:
            return False
        target = _val_from_x(self, event.x)
        current = self.value
        if abs(target - current) < 0.03:
            return False
        self._pressed = True
        self._cancel_canim()
        self._canim_s = current
        self._canim_e = target
        self._canim_n = 0
        self._canim_id = GLib.timeout_add(_CLICK_MS, self._canim_tick)
        return True

    def _on_click_release(self, _, event):
        if not self._pressed:
            return False
        self._pressed = False
        return True

    def _on_click_motion(self, _, event):
        if not self._pressed:
            return False
        self._cancel_canim()
        self.value = _val_from_x(self, event.x)
        return True

    def _canim_tick(self):
        self._canim_n += 1
        t = min(self._canim_n / float(_CLICK_STEPS), 1.0)
        self.value = self._canim_s + (self._canim_e - self._canim_s) * _ease_out_cubic(t)
        if self._canim_n >= _CLICK_STEPS:
            self._canim_id = None
            return False
        return True

    def _cancel_canim(self):
        if self._canim_id is not None:
            GLib.source_remove(self._canim_id)
            self._canim_id = None

    def _new_stream(self, *_):
        if self._s and self._hid:
            try: self._s.disconnect(self._hid)
            except Exception: pass
            self._hid = None
        self._s = getattr(self.audio, self._type)
        if self._s:
            self._hid = self._s.connect("changed", self._ui)
            self._ui()

    def _ui(self, *_):
        if not self._s: return
        if self._canim_id is not None or self._pressed: return

        self._upd = True

        nv = self._s.volume * 0.01
        if abs(self.value - nv) > 0.005:
            self.value = nv
            pct = int(self._s.volume)
            if pct != self._last_pct:
                self.set_tooltip_text(f"{pct}%")
                self._last_pct = pct

        self._upd = False

    def _val_chg(self, _):
        if self._upd or not self._s: return
        nv = self.value * 100.0
        if abs(self._s.volume - nv) > 0.5:
            self._s.volume = nv
            pct = int(nv)
            if pct != self._last_pct:
                self.set_tooltip_text(f"{pct}%")
                self._last_pct = pct

    def cleanup(self):
        self._cancel_canim()
        if self._audio_hid and self.audio:
            try: self.audio.disconnect(self._audio_hid)
            except Exception: pass
        if self._s and self._hid:
            try: self._s.disconnect(self._hid)
            except Exception: pass
        self._s = self._hid = self._audio_hid = self.audio = None


class _AudioSmall(Box):
    __slots__ = ('audio', '_s', '_hid', '_audio_hid', 'progress_bar', 'vol_label', '_last_vol', '_is_mic')

    def __init__(self, stream_type, box_name, prog_name, lbl_name, is_mic=False, **kwargs):
        super().__init__(name=box_name, **kwargs)
        self.audio = get_audio()
        self._is_mic = is_mic
        self._s = self._hid = self._audio_hid = None
        self._last_vol = -1

        self.progress_bar = CircularProgressBar(name=prog_name, size=28, line_width=2, start_angle=150, end_angle=390)
        self.vol_label = Label(name=lbl_name, markup=icons.mic if is_mic else icons.vol_high)
        self.add(Overlay(child=self.progress_bar, overlays=self.vol_label))

        self._audio_hid = self.audio.connect(f"notify::{stream_type}", self._new_stream)
        self._new_stream()

    def _new_stream(self, *_):
        if self._s and self._hid:
            try: self._s.disconnect(self._hid)
            except Exception: pass
            self._hid = None
        self._s = getattr(self.audio, "microphone" if self._is_mic else "speaker")
        if self._s:
            self._hid = self._s.connect("changed", self._ui)
            self._ui()

    def _ui(self, *_):
        if not self._s: return

        vn = self._s.volume * 0.01
        if abs(self.progress_bar.value - vn) > 0.005:
            self.progress_bar.value = vn

        v = int(self._s.volume)
        if v != self._last_vol:
            self._last_vol = v
            if self._is_mic:
                self.vol_label.set_markup(icons.mic if v >= 1 else icons.mic_mute)
                self.set_tooltip_text(f"Microphone: {v}%" if v > 0 else "Microphone off")
            else:
                im = _IB if "bluetooth" in getattr(self._s, "icon_name", "") else _IS
                self.vol_label.set_markup(im["high"] if v > 74 else (im["medium"] if v > 0 else im["off"]))
                self.set_tooltip_text(f"Volume: {v}%" if v > 0 else "Muted")

    def cleanup(self):
        if self._audio_hid and self.audio:
            try: self.audio.disconnect(self._audio_hid)
            except Exception: pass
        if self._s and self._hid:
            try: self._s.disconnect(self._hid)
            except Exception: pass
        self._s = self._hid = self._audio_hid = self.audio = None


class _AudioIcon(Box):
    __slots__ = ('audio', '_s', '_hid', '_audio_hid', 'vol_label', 'vol_button',
                 '_last_vol', '_is_mic', '_soft_muted', '_saved_vol',
                 '_anim_id', '_anim_start', '_anim_end', '_anim_step')

    def __init__(self, stream_type, box_name, lbl_name, is_mic=False, **kwargs):
        super().__init__(name=box_name, **kwargs)
        self.audio = get_audio()
        self._is_mic = is_mic
        self._s = self._hid = self._audio_hid = None
        self._last_vol = -1
        self._soft_muted = False
        self._saved_vol = 100.0
        self._anim_id = None
        self._anim_start = self._anim_end = self._anim_step = 0

        self.vol_label = Label(name=lbl_name, markup=icons.mic if is_mic else "")
        self.vol_button = Button(on_clicked=self._tog, child=self.vol_label)
        self.add(EventBox(child=self.vol_button, h_expand=True))

        _setup_pointer_cursor(self.vol_button)

        self._audio_hid = self.audio.connect(f"notify::{stream_type}", self._new_stream)
        self._new_stream()

    def _new_stream(self, *_):
        if self._s and self._hid:
            try: self._s.disconnect(self._hid)
            except Exception: pass
            self._hid = None
        self._s = getattr(self.audio, "microphone" if self._is_mic else "speaker")
        if self._s:
            self._hid = self._s.connect("changed", self._ui)
            self._ui()

    def _tog(self, *_):
        if not self._s: return

        if self._anim_id is not None:
            GLib.source_remove(self._anim_id)
            self._anim_id = None

        if not self._soft_muted:
            self._saved_vol = self._s.volume
            self._soft_muted = True
            self._run_animation(to_zero=True)
        else:
            self._soft_muted = False
            self._run_animation(to_zero=False)

    def _run_animation(self, to_zero: bool):
        if not self._s: return
        self._anim_start = self._s.volume
        self._anim_end = 0.0 if to_zero else self._saved_vol

        if abs(self._anim_start - self._anim_end) < 0.5:
            self._s.volume = self._anim_end
            return

        self._anim_step = 0
        self._anim_id = GLib.timeout_add(_ANIM_INTERVAL_MS, self._anim_tick)

    def _anim_tick(self):
        self._anim_step += 1
        t = min(self._anim_step / float(_ANIM_STEPS), 1.0)
        ease = _ease_out_cubic(t)

        if self._s:
            self._s.volume = self._anim_start + (self._anim_end - self._anim_start) * ease

        if self._anim_step >= _ANIM_STEPS:
            self._anim_id = None
            return False
        return True

    def _ui(self, *_):
        if not self._s:
            if not self._is_mic: self.vol_label.set_markup("")
            return

        v = int(self._s.volume)
        if v == self._last_vol: return
        self._last_vol = v

        if self._is_mic:
            self.vol_label.set_markup(icons.mic if v >= 1 else icons.mic_mute)
            self.set_tooltip_text(f"Microphone: {v}%" if v > 0 else "Microphone off")
        else:
            im = _IB if "bluetooth" in getattr(self._s, "icon_name", "") else _IS
            self.vol_label.set_markup(im["high"] if v > 74 else (im["medium"] if v > 0 else im["off"]))
            self.set_tooltip_text(f"Volume: {v}%" if v > 0 else "Muted")

    def cleanup(self):
        if self._anim_id is not None:
            GLib.source_remove(self._anim_id)
            self._anim_id = None
        if self._audio_hid and self.audio:
            try: self.audio.disconnect(self._audio_hid)
            except Exception: pass
        if self._s and self._hid:
            try: self._s.disconnect(self._hid)
            except Exception: pass
        self._s = self._hid = self._audio_hid = self.audio = None

class VolumeSlider(_AudioScale):
    def __init__(self, **kwargs): super().__init__("speaker", "vol", **kwargs)

class MicSlider(_AudioScale):
    def __init__(self, **kwargs): super().__init__("microphone", "mic", **kwargs)

class VolumeSmall(_AudioSmall):
    def __init__(self, **kwargs): super().__init__("speaker", "button-bar-vol", "button-volume", "vol-label", False, **kwargs)

class MicSmall(_AudioSmall):
    def __init__(self, **kwargs): super().__init__("microphone", "button-bar-mic", "button-mic", "mic-label", True, **kwargs)

class VolumeIcon(_AudioIcon):
    def __init__(self, **kwargs): super().__init__("speaker", "vol-icon", "vol-label-dash", False, **kwargs)

class MicIcon(_AudioIcon):
    def __init__(self, **kwargs): super().__init__("microphone", "mic-icon", "mic-label-dash", True, **kwargs)


class BrightnessSlider(Scale):
    __slots__ = ('client', '_upd', '_tid', '_target', '_last_pct', '_br_hid',
                 '_canim_id', '_canim_s', '_canim_e', '_canim_n', '_pressed')

    def __init__(self, **kwargs):
        super().__init__(name="control-slider", orientation="h", h_expand=True, h_align="fill", has_origin=True, increments=(0.01, 0.1), **kwargs)
        self.client = Brightness.get_initial()
        self._upd = False
        self._tid = self._br_hid = None
        self._target = self._last_pct = -1

        self._canim_id = None
        self._canim_s = self._canim_e = 0.0
        self._canim_n = 0
        self._pressed = False

        if self.client.max_screen <= 0:
            self.set_no_show_all(True)
            self.hide()
            return

        self.add_style_class("brightness")
        self.connect("value-changed", self._val_chg)
        self.connect("button-press-event", self._on_click_press)
        self.connect("button-release-event", self._on_click_release)
        self.connect("motion-notify-event", self._on_click_motion)
        self._br_hid = self.client.connect("screen", self._br_chg)
        self._br_chg(None, self.client.screen_brightness)

    def _on_click_press(self, _, event):
        if event.button != 1:
            return False
        target = _val_from_x(self, event.x)
        current = self.get_value()
        if abs(target - current) < 0.03:
            return False
        self._pressed = True
        self._cancel_canim()
        self._canim_s = current
        self._canim_e = target
        self._canim_n = 0
        self._canim_id = GLib.timeout_add(_CLICK_MS, self._canim_tick)
        return True

    def _on_click_release(self, _, event):
        if not self._pressed:
            return False
        self._pressed = False
        return True

    def _on_click_motion(self, _, event):
        if not self._pressed:
            return False
        self._cancel_canim()
        self.set_value(_val_from_x(self, event.x))
        return True

    def _canim_tick(self):
        self._canim_n += 1
        t = min(self._canim_n / float(_CLICK_STEPS), 1.0)
        self.set_value(self._canim_s + (self._canim_e - self._canim_s) * _ease_out_cubic(t))
        if self._canim_n >= _CLICK_STEPS:
            self._canim_id = None
            return False
        return True

    def _cancel_canim(self):
        if self._canim_id is not None:
            GLib.source_remove(self._canim_id)
            self._canim_id = None

    def _val_chg(self, _):
        if self._upd: return

        val = self.get_value()
        pct = int(val * 100)
        if pct != self._last_pct:
            self.set_tooltip_text(f"{pct}%")
            self._last_pct = pct

        self._target = int(val * self.client.max_screen)

        if self._canim_id is not None:
            if not self._tid:
                self._tid = GLib.timeout_add(30, self._apply)
        else:
            if self._tid: GLib.source_remove(self._tid)
            self._tid = GLib.timeout_add(30, self._apply)

    def _apply(self):
        self._tid = None
        if self._target >= 0 and self._target != self.client.screen_brightness:
            self.client.screen_brightness = self._target
        return False

    def _br_chg(self, _, cur):
        if self._tid or not getattr(self.client, '_valid', True) or self.client.max_screen <= 0: return
        if self._canim_id is not None or self._pressed: return

        n = cur / self.client.max_screen
        if abs(self.get_value() - n) < 0.008: return

        self._upd = True
        self.set_value(n)

        pct = int(n * 100)
        if pct != self._last_pct:
            self.set_tooltip_text(f"{pct}%")
            self._last_pct = pct

        self._upd = False

    def cleanup(self):
        self._cancel_canim()
        if self._tid:
            GLib.source_remove(self._tid)
            self._tid = None
        if self._br_hid and self.client:
            try: self.client.disconnect(self._br_hid)
            except Exception: pass
        self.client = self._br_hid = None


class BrightnessSmall(Box):
    __slots__ = ('brightness', 'progress_bar', 'brightness_label', '_last_pct', '_br_hid')

    def __init__(self, **kwargs):
        super().__init__(name="button-bar-brightness", **kwargs)
        self.brightness = Brightness.get_initial()
        self._last_pct = -1
        self._br_hid = None

        if self.brightness.screen_brightness == -1: return

        self.progress_bar = CircularProgressBar(name="button-brightness", size=28, line_width=2, start_angle=150, end_angle=390)
        self.brightness_label = Label(name="brightness-label", markup=icons.brightness_high)
        self.add(Overlay(child=self.progress_bar, overlays=self.brightness_label))

        self._br_hid = self.brightness.connect("screen", self._chg)
        self._chg()

    def _chg(self, *_):
        mx = self.brightness.max_screen
        if mx <= 0: return

        n = self.brightness.screen_brightness / mx
        if abs(self.progress_bar.value - n) > 0.005:
            self.progress_bar.value = n
            p = int(n * 100)
            if p != self._last_pct:
                self.brightness_label.set_markup(_bicon(p))
                self.set_tooltip_text(f"Brightness: {p}%")
                self._last_pct = p

    def cleanup(self):
        if self._br_hid and self.brightness:
            try: self.brightness.disconnect(self._br_hid)
            except Exception: pass
        self.brightness = self._br_hid = None


class BrightnessIcon(Box):
    __slots__ = ('brightness', 'brightness_label', '_btn', '_last_pct',
                 '_soft_muted', '_saved_brightness', '_anim_id', '_br_hid',
                 '_anim_start', '_anim_end', '_anim_step')

    def __init__(self, **kwargs):
        super().__init__(name="brightness-icon", **kwargs)
        self.brightness = Brightness.get_initial()
        self._last_pct = -1
        self._soft_muted = False
        self._saved_brightness = 0
        self._anim_id = self._br_hid = None
        self._anim_start = self._anim_end = self._anim_step = 0

        if self.brightness.screen_brightness == -1: return

        self.brightness_label = Label(name="brightness-label-dash", markup=icons.brightness_high)
        self._btn = Button(on_clicked=self._tog, child=self.brightness_label)
        self.add(EventBox(child=self._btn, h_expand=True))

        _setup_pointer_cursor(self._btn)

        self._br_hid = self.brightness.connect("screen", self._chg)
        self._chg()

    def _tog(self, *_):
        if self.brightness.max_screen <= 0: return

        if self._anim_id is not None:
            GLib.source_remove(self._anim_id)
            self._anim_id = None

        if not self._soft_muted:
            self._saved_brightness = self.brightness.screen_brightness
            self._soft_muted = True
            self._run_animation(to_zero=True)
        else:
            self._soft_muted = False
            self._run_animation(to_zero=False)

    def _run_animation(self, to_zero: bool):
        self._anim_start = float(self.brightness.screen_brightness)
        self._anim_end = 0.0 if to_zero else float(self._saved_brightness)

        if abs(self._anim_start - self._anim_end) < 1:
            self.brightness.screen_brightness = int(self._anim_end)
            return

        self._anim_step = 0
        self._anim_id = GLib.timeout_add(_ANIM_INTERVAL_MS, self._anim_tick)

    def _anim_tick(self):
        self._anim_step += 1
        t = min(self._anim_step / float(_ANIM_STEPS), 1.0)
        ease = _ease_out_cubic(t)

        self.brightness.screen_brightness = int(self._anim_start + (self._anim_end - self._anim_start) * ease)

        if self._anim_step >= _ANIM_STEPS:
            self._anim_id = None
            return False
        return True

    def _chg(self, *_):
        mx = self.brightness.max_screen
        if mx <= 0: return

        p = int(self.brightness.screen_brightness * 100 / mx)
        if p != self._last_pct:
            self.brightness_label.set_markup(_bicon(p))
            self.set_tooltip_text(f"Brightness: {p}%")
            self._last_pct = p

    def cleanup(self):
        if self._anim_id is not None:
            GLib.source_remove(self._anim_id)
            self._anim_id = None
        if self._br_hid and self.brightness:
            try: self.brightness.disconnect(self._br_hid)
            except Exception: pass
        self.brightness = self._br_hid = None


class ControlSliders(Box):
    __slots__ = ('_br', '_vol', '_mic')

    def __init__(self, **kwargs):
        super().__init__(name="control-sliders", spacing=8, **kwargs)

        br = Brightness.get_initial()

        if br.screen_brightness != -1:
            self._br = Box(spacing=0, h_expand=True, children=(BrightnessIcon(), BrightnessSlider()))
            self.add(self._br)
        else:
            self._br = None

        self._vol = Box(spacing=0, h_expand=True, children=(VolumeIcon(), VolumeSlider()))
        self._mic = Box(spacing=0, h_expand=True, children=(MicIcon(), MicSlider()))

        self.add(self._vol)
        self.add(self._mic)
        self.show_all()

    def cleanup(self):
        boxes = (self._vol, self._mic, self._br) if self._br else (self._vol, self._mic)
        for box in boxes:
            for c in box.get_children():
                try: c.cleanup()
                except AttributeError: pass


class ControlSmall(Box):
    __slots__ = ('_widgets',)

    def __init__(self, **kwargs):
        br = Brightness.get_initial()
        ch = ((BrightnessSmall(),) if br.screen_brightness != -1 else ()) + (VolumeSmall(), MicSmall())

        super().__init__(name="control-small", spacing=4, children=ch, **kwargs)
        self._widgets = ch
        self.show_all()

    def cleanup(self):
        for w in self._widgets:
            try: w.cleanup()
            except AttributeError: pass
        self._widgets = ()