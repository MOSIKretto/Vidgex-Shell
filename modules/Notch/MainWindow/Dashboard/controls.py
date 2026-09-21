import random
from fabric.widgets.box import Box
from fabric.widgets.label import Label
from gi.repository import GLib

from modules.Notch.MainWindow.Dashboard.Controls.brightness import (
    Brightness,
    BrightnessIcon,
    BrightnessSlider,
    BrightnessSmall,
)
from modules.Notch.MainWindow.Dashboard.Controls.microphone import (
    Microphone,
    MicIcon,
    MicSlider,
    MicSmall,
)
from modules.Notch.MainWindow.Dashboard.Controls.volume import (
    Volume,
    VolumeIcon,
    VolumeSlider,
    VolumeSmall,
    get_audio,
)
import services.icons as icons

__all__ = [
    "ControlSliders",
    "ControlSmall",
    "ControlOSD",
    "Brightness",
    "Volume",
    "Microphone",
    "get_audio",
]

_BTH = (75, 24)
_BIC = (icons.brightness_high, icons.brightness_medium, icons.brightness_low)

def _bicon(p: int) -> str:
    return _BIC[0] if p >= _BTH[0] else (_BIC[1] if p >= _BTH[1] else _BIC[2])

_IS = {"high": icons.vol_high, "medium": icons.vol_medium, "off": icons.vol_mute}
_IB = {"high": icons.bluetooth_connected, "medium": icons.bluetooth, "off": icons.bluetooth_disconnected}

_GL_FRAMES = 7
_GL_FRAME_MS = 35
_CLASSES = (
    "glitch-shift-right",
    "glitch-shift-left",
    "glitch-flicker",
    "glitch-aberration",
    "glitch-heavy",
    "glitch-color-swap",
)
_ASCII_CHARS = ("/", "#", "@", "&", "!", "$", "%", "*", ";", "?", "|", "\\")
DOT_ACTIVE = "●"
DOT_INACTIVE = "○"


class ControlOSD(Box):
    """Единичный OSD индикатор: иконка, 10 точек с ASCII-глитчем и процентное значение."""
    __slots__ = (
        '_icon_lbl', '_val_lbl', '_dots', '_dots_box',
        '_dot_rem', '_dot_tid', '_dot_target',
        '_dev_levels', '_dev_dots', '_dev_muted', '_cur_dev',
        '_on_changed', '_init_done', '_hids',
    )

    def __init__(self, on_changed=None, **kwargs):
        super().__init__(
            name="control-osd",
            orientation="h",
            spacing=8,
            h_align="center",
            v_align="center",
            **kwargs
        )

        self._on_changed = on_changed
        self._init_done = False
        self._hids = []
        self._cur_dev = "speaker"

        # 1. Иконка контрола
        self._icon_lbl = Label(name="osd-icon-label", markup=icons.vol_high, v_align="center")
        self._icon_lbl.set_yalign(0.5)
        icon_box = Box(name="osd-icon-box", v_align="center", children=[self._icon_lbl])
        self.add(icon_box)

        # 2. 10 крупных точек (строгая моноширина)
        self._dots = []
        self._dots_box = Box(name="osd-dots-box", orientation="h", spacing=2, v_align="center")
        
        self._dot_rem = [0] * 10
        self._dot_tid = [None] * 10
        self._dot_target = [False] * 10

        for _ in range(10):
            lbl = Label(name="osd-dot", label=DOT_INACTIVE, v_align="center")
            lbl.set_width_chars(1)
            lbl.set_max_width_chars(1)
            lbl.set_xalign(0.5)
            lbl.set_yalign(0.5)
            lbl.get_style_context().add_class("osd-dot")
            lbl.get_style_context().add_class("inactive")
            self._dots.append(lbl)
            self._dots_box.add(lbl)

        self.add(self._dots_box)

        # 3. Числовое значение процента
        self._val_lbl = Label(name="osd-value-label", label="0%", v_align="center")
        self._val_lbl.set_yalign(0.5)
        self.add(self._val_lbl)

        self._dev_levels = {"speaker": 0, "microphone": 0, "screen": 0}
        self._dev_dots = {"speaker": 0, "microphone": 0, "screen": 0}
        self._dev_muted = {"speaker": False, "microphone": False, "screen": False}

        # Подключение к шинам управления звуком и яркостью
        vol = Volume.get_initial()
        mic = Microphone.get_initial()
        br = Brightness.get_initial()

        self._hids.append(vol.connect("changed", lambda _, v: self._check_change("speaker", v, vol.muted)))
        self._hids.append(mic.connect("changed", lambda _, v: self._check_change("microphone", v, mic.muted)))
        if br.screen_brightness != -1:
            self._hids.append(br.connect("screen", lambda _, v: self._check_change(
                "screen", int(v * 100 / br.max_screen) if br.max_screen > 0 else 0, False
            )))

        # Первоначальная инициализация
        self._dev_levels["speaker"] = vol.volume
        self._dev_dots["speaker"] = max(0, min(10, int(round(vol.volume / 10.0))))
        self._dev_levels["microphone"] = mic.volume
        self._dev_dots["microphone"] = max(0, min(10, int(round(mic.volume / 10.0))))
        if br.screen_brightness != -1:
            p = int(br.screen_brightness * 100 / br.max_screen) if br.max_screen > 0 else 0
            self._dev_levels["screen"] = p
            self._dev_dots["screen"] = max(0, min(10, int(round(p / 10.0))))

        self._update_display("speaker", vol.volume, vol.muted, is_init=True)
        GLib.idle_add(self._mark_init_done)
        self.show_all()

    def _mark_init_done(self):
        self._init_done = True
        return False

    def _start_dot_glitch(self, idx: int, target_active: bool):
        if idx < 0 or idx >= 10:
            return
        if self._dot_tid[idx]:
            GLib.source_remove(self._dot_tid[idx])
            self._dot_tid[idx] = None

        self._dot_target[idx] = target_active
        self._dot_rem[idx] = _GL_FRAMES
        self._dot_tid[idx] = GLib.timeout_add(_GL_FRAME_MS, self._glitch_tick, idx)

    def _glitch_tick(self, idx: int) -> bool:
        if idx >= 10:
            return False
        dot = self._dots[idx]
        ctx = dot.get_style_context()
        for cls in _CLASSES:
            ctx.remove_class(cls)

        self._dot_rem[idx] -= 1
        if self._dot_rem[idx] <= 0:
            self._dot_tid[idx] = None
            is_act = self._dot_target[idx]
            dot.set_text(DOT_ACTIVE if is_act else DOT_INACTIVE)
            if is_act:
                ctx.add_class("active")
                ctx.remove_class("inactive")
            else:
                ctx.add_class("inactive")
                ctx.remove_class("active")
            return False

        # Кадр ASCII-глитча
        char = random.choice(_ASCII_CHARS)
        dot.set_text(char)
        ctx.remove_class("active")
        ctx.remove_class("inactive")
        count = 1 if random.random() > 0.4 else 2
        for cls in random.sample(_CLASSES, count):
            ctx.add_class(cls)
        return True

    def _check_change(self, dev: str, val: int, is_muted: bool = False):
        if not self._init_done or val is None:
            return
        prev = self._dev_levels.get(dev)
        prev_muted = self._dev_muted.get(dev)
        if prev is not None and (abs(val - prev) >= 1 or is_muted != prev_muted):
            self._dev_muted[dev] = is_muted
            self._update_display(dev, val, is_muted)

    def _update_display(self, dev: str, val: int, is_muted: bool = False, is_init: bool = False):
        # 1. Иконка контрола
        if dev == "speaker":
            vol = Volume.get_initial()
            im = _IB if vol.is_bluetooth else _IS
            icon = im["off"] if (is_muted or val <= 0) else (im["high"] if val > 74 else im["medium"])
        elif dev == "microphone":
            icon = icons.mic if (val > 0 and not is_muted) else icons.mic_mute
        else:
            icon = _bicon(val)

        self._icon_lbl.set_markup(icon)

        # 2. Числовые проценты
        effective_val = 0 if is_muted else val
        self._val_lbl.set_label(f"{effective_val}%")

        new_dots = max(0, min(10, int(round(effective_val / 10.0))))
        old_dots = self._dev_dots.get(dev, 0)
        old_val = self._dev_levels.get(dev, 0)

        # Сброс и переключение устройства
        if self._cur_dev != dev:
            self._cur_dev = dev
            for i in range(10):
                if self._dot_tid[i]:
                    GLib.source_remove(self._dot_tid[i])
                    self._dot_tid[i] = None
                is_act = i < old_dots
                dot = self._dots[i]
                ctx = dot.get_style_context()
                for cls in _CLASSES:
                    ctx.remove_class(cls)
                dot.set_text(DOT_ACTIVE if is_act else DOT_INACTIVE)
                ctx.add_class("active" if is_act else "inactive")
                ctx.remove_class("inactive" if is_act else "active")

        if is_init or not self._init_done:
            self._dev_dots[dev] = new_dots
            self._dev_levels[dev] = effective_val
            for i in range(10):
                is_act = i < new_dots
                dot = self._dots[i]
                ctx = dot.get_style_context()
                dot.set_text(DOT_ACTIVE if is_act else DOT_INACTIVE)
                ctx.add_class("active" if is_act else "inactive")
                ctx.remove_class("inactive" if is_act else "active")
            return

        # 3. Запуск точечного глитча
        if new_dots > old_dots:
            for i in range(old_dots, new_dots):
                self._start_dot_glitch(i, target_active=True)
        elif new_dots < old_dots:
            for i in range(new_dots, old_dots):
                self._start_dot_glitch(i, target_active=False)
        elif effective_val != old_val:
            target_dot = min(9, max(0, new_dots - 1))
            self._start_dot_glitch(target_dot, target_active=(target_dot < new_dots))

        self._dev_dots[dev] = new_dots
        self._dev_levels[dev] = effective_val

        if self._on_changed:
            self._on_changed()

    def cleanup(self):
        for i in range(10):
            if self._dot_tid[i]:
                GLib.source_remove(self._dot_tid[i])
                self._dot_tid[i] = None

        vol = Volume.get_initial()
        mic = Microphone.get_initial()
        br = Brightness.get_initial()
        for hid in self._hids:
            for s in (vol, mic, br):
                try: s.disconnect(hid)
                except Exception: pass
        self._hids.clear()


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
                if hasattr(c, "cleanup"):
                    c.cleanup()


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
            if hasattr(w, "cleanup"):
                w.cleanup()
        self._widgets = ()