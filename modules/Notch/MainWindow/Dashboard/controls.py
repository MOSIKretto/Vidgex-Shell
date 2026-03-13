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

_pointer_cursor: Gdk.Cursor | None = None
_default_cursor: Gdk.Cursor | None = None

def _get_cursors(display: Gdk.Display):
    global _pointer_cursor, _default_cursor
    if _pointer_cursor is None:
        _pointer_cursor = Gdk.Cursor.new_from_name(display, "pointer")
        _default_cursor = Gdk.Cursor.new_from_name(display, "default")
    return _pointer_cursor, _default_cursor

def _on_btn_enter(widget: Gtk.Widget, _event: Gdk.EventCrossing):
    win = widget.get_window()
    if win:
        pointer, _ = _get_cursors(win.get_display())
        win.set_cursor(pointer)
    return False

def _on_btn_leave(widget: Gtk.Widget, _event: Gdk.EventCrossing):
    win = widget.get_window()
    if win:
        _, default = _get_cursors(win.get_display())
        win.set_cursor(default)
    return False

def _setup_pointer_cursor(widget: Gtk.Widget):
    widget.add_events(
        Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK,
    )
    widget.connect("enter-notify-event", _on_btn_enter)
    widget.connect("leave-notify-event", _on_btn_leave)


class _AudioScale(Scale):
    __slots__ = ('audio', '_upd', '_s', '_hid', '_last_pct', '_type')

    def __init__(self, stream_type, style, **kwargs):
        super().__init__(name="control-slider", orientation="h", h_expand=True, h_align="fill", has_origin=True, increments=(0.01, 0.1), **kwargs)
        self.audio = get_audio()
        self._type = stream_type
        self._upd = False
        self._s = self._hid = None
        self._last_pct = -1

        self.add_style_class(style)
        self.audio.connect(f"notify::{stream_type}", self._new_stream)
        self.connect("value-changed", self._val_chg)
        self._new_stream()

    def _new_stream(self, *_):
        if self._s and self._hid:
            try: self._s.disconnect(self._hid)
            except Exception: pass
        self._s = getattr(self.audio, self._type)
        if self._s:
            self._hid = self._s.connect("changed", self._ui)
            self._ui()

    def _ui(self, *_):
        if not self._s: return
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
        nv = self.value * 100
        if abs(self._s.volume - nv) > 0.5:
            self._s.volume = nv
            pct = int(nv)
            if pct != self._last_pct:
                self.set_tooltip_text(f"{pct}%")
                self._last_pct = pct

    def cleanup(self):
        if self._s and self._hid:
            try: self._s.disconnect(self._hid)
            except Exception: pass
        self._s = self._hid = None


class _AudioSmall(Box):
    __slots__ = ('audio', '_s', '_hid', 'progress_bar', 'vol_label', '_last_vol', '_is_mic')

    def __init__(self, stream_type, box_name, prog_name, lbl_name, is_mic=False, **kwargs):
        super().__init__(name=box_name, **kwargs)
        self.audio = get_audio()
        self._is_mic = is_mic
        self._s = self._hid = None
        self._last_vol = -1

        self.progress_bar = CircularProgressBar(name=prog_name, size=28, line_width=2, start_angle=150, end_angle=390)
        self.vol_label = Label(name=lbl_name, markup=icons.mic if is_mic else icons.vol_high)
        self.add(Overlay(child=self.progress_bar, overlays=self.vol_label))

        self.audio.connect(f"notify::{stream_type}", self._new_stream)
        self._new_stream()

    def _new_stream(self, *_):
        if self._s and self._hid:
            try: self._s.disconnect(self._hid)
            except Exception: pass
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
        if self._s and self._hid:
            try: self._s.disconnect(self._hid)
            except Exception: pass
        self._s = self._hid = None


class _AudioIcon(Box):
    __slots__ = ('audio', '_s', '_hid', 'vol_label', 'vol_button',
                 '_last_vol', '_is_mic',
                 '_soft_muted', '_saved_vol', '_anim_id')

    def __init__(self, stream_type, box_name, lbl_name, is_mic=False, **kwargs):
        super().__init__(name=box_name, **kwargs)
        self.audio = get_audio()
        self._is_mic = is_mic
        self._s = self._hid = None
        self._last_vol = -1
        self._soft_muted = False
        self._saved_vol = 100.0
        self._anim_id = None

        self.vol_label = Label(name=lbl_name, markup=icons.mic if is_mic else "")
        self.vol_button = Button(on_clicked=self._tog, child=self.vol_label)
        self.add(EventBox(child=self.vol_button, h_expand=True))

        _setup_pointer_cursor(self.vol_button)

        self.audio.connect(f"notify::{stream_type}", self._new_stream)
        self._new_stream()

    def _new_stream(self, *_):
        if self._s and self._hid:
            try: self._s.disconnect(self._hid)
            except Exception: pass
        self._s = getattr(self.audio, "microphone" if self._is_mic else "speaker")
        if self._s:
            self._hid = self._s.connect("changed", self._ui)
            self._ui()

    def _tog(self, *_):
        if not self._s:
            return

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
        if not self._s:
            return

        start = self._s.volume
        end = 0.0 if to_zero else self._saved_vol

        if abs(start - end) < 0.5:
            self._s.volume = end
            return

        step = [0]

        def _tick():
            step[0] += 1
            t = min(step[0] / _ANIM_STEPS, 1.0)
            ease = 1.0 - (1.0 - t) ** 3

            if self._s:
                self._s.volume = start + (end - start) * ease

            if step[0] >= _ANIM_STEPS:
                self._anim_id = None
                return False
            return True

        self._anim_id = GLib.timeout_add(_ANIM_INTERVAL_MS, _tick)

    def _ui(self, *_):
        if not self._s:
            if not self._is_mic:
                self.vol_label.set_markup("")
            return

        v = int(self._s.volume)
        if v == self._last_vol:
            return
        self._last_vol = v

        if self._is_mic:
            self.vol_label.set_markup(icons.mic if v >= 1 else icons.mic_mute)
            self.set_tooltip_text(f"Microphone: {v}%" if v > 0 else "Microphone off")
        else:
            is_bt = "bluetooth" in getattr(self._s, "icon_name", "")
            im = _IB if is_bt else _IS
            self.vol_label.set_markup(
                im["high"] if v > 74 else (im["medium"] if v > 0 else im["off"])
            )
            self.set_tooltip_text(f"Volume: {v}%" if v > 0 else "Muted")

    def cleanup(self):
        if self._anim_id is not None:
            GLib.source_remove(self._anim_id)
            self._anim_id = None
        if self._s and self._hid:
            try: self._s.disconnect(self._hid)
            except Exception: pass
        self._s = self._hid = None


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
    __slots__ = ('client', '_upd', '_tid', '_target', '_last_pct')

    def __init__(self, **kwargs):
        super().__init__(name="control-slider", orientation="h", h_expand=True, h_align="fill", has_origin=True, increments=(0.01, 0.1), **kwargs)
        self.client = Brightness.get_initial()
        self._upd = False
        self._tid = None
        self._target = self._last_pct = -1

        if self.client.max_screen <= 0:
            self.set_no_show_all(True)
            self.hide()
            return

        self.add_style_class("brightness")
        self.connect("value-changed", self._val_chg)
        self.client.connect("screen", self._br_chg)
        self._br_chg(None, self.client.screen_brightness)

    def _val_chg(self, _):
        if self._upd: return

        val = self.get_value()
        pct = int(val * 100)
        if pct != self._last_pct:
            self.set_tooltip_text(f"{pct}%")
            self._last_pct = pct

        self._target = int(val * self.client.max_screen)

        if self._tid: GLib.source_remove(self._tid)
        self._tid = GLib.timeout_add(30, self._apply)

    def _apply(self):
        self._tid = None
        if self._target >= 0 and self._target != self.client.screen_brightness:
            self.client.screen_brightness = self._target
        return False

    def _br_chg(self, _, cur):
        if self._tid or not getattr(self.client, '_valid', True) or self.client.max_screen <= 0: return

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
        if self._tid:
            GLib.source_remove(self._tid)
            self._tid = None


class BrightnessSmall(Box):
    __slots__ = ('brightness', 'progress_bar', 'brightness_label', '_last_pct')

    def __init__(self, **kwargs):
        super().__init__(name="button-bar-brightness", **kwargs)
        self.brightness = Brightness.get_initial()
        self._last_pct = -1

        if self.brightness.screen_brightness == -1: return

        self.progress_bar = CircularProgressBar(name="button-brightness", size=28, line_width=2, start_angle=150, end_angle=390)
        self.brightness_label = Label(name="brightness-label", markup=icons.brightness_high)
        self.add(Overlay(child=self.progress_bar, overlays=self.brightness_label))

        self.brightness.connect("screen", self._chg)
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


class BrightnessIcon(Box):
    __slots__ = ('brightness', 'brightness_label', '_btn', '_last_pct',
                 '_soft_muted', '_saved_brightness', '_anim_id')

    def __init__(self, **kwargs):
        super().__init__(name="brightness-icon", **kwargs)
        self.brightness = Brightness.get_initial()
        self._last_pct = -1
        self._soft_muted = False
        self._saved_brightness = 0
        self._anim_id = None

        if self.brightness.screen_brightness == -1: return

        self.brightness_label = Label(name="brightness-label-dash", markup=icons.brightness_high)
        self._btn = Button(on_clicked=self._tog, child=self.brightness_label)
        self.add(EventBox(child=self._btn, h_expand=True))

        _setup_pointer_cursor(self._btn)

        self.brightness.connect("screen", self._chg)
        self._chg()

    def _tog(self, *_):
        mx = self.brightness.max_screen
        if mx <= 0:
            return

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
        mx = self.brightness.max_screen
        if mx <= 0:
            return

        start = float(self.brightness.screen_brightness)
        end = 0.0 if to_zero else float(self._saved_brightness)

        if abs(start - end) < 1:
            self.brightness.screen_brightness = int(end)
            return

        step = [0]

        def _tick():
            step[0] += 1
            t = min(step[0] / _ANIM_STEPS, 1.0)
            ease = 1.0 - (1.0 - t) ** 3

            self.brightness.screen_brightness = int(start + (end - start) * ease)

            if step[0] >= _ANIM_STEPS:
                self._anim_id = None
                return False
            return True

        self._anim_id = GLib.timeout_add(_ANIM_INTERVAL_MS, _tick)

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
                if hasattr(c, 'cleanup'): c.cleanup()


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
            if hasattr(w, 'cleanup'): w.cleanup()
        self._widgets = ()