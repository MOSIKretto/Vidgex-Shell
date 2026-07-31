import threading
import subprocess

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.circularprogressbar import CircularProgressBar
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer
from gi.repository import GLib, Gio, Gdk

from modules.Bar.Battery.upower import UPowerManager
import services.icons as icons


_bat_subs  = []
_up        = None
_dd        = None
_bat_pct   = 0.0
_bat_chg   = None
_bat_time  = 0.0
_bat_timer = None
_cursor_hand = None



def _hov(w):
    def sc(widget, _, is_hovered):
        global _cursor_hand
        if not _cursor_hand:
            _cursor_hand = Gdk.Cursor.new_from_name(widget.get_display(), "hand2")
        if win := widget.get_window():
            win.set_cursor(_cursor_hand if is_hovered else None)
    w.connect("enter-notify-event", sc, True)
    w.connect("leave-notify-event", sc, False)


def _bat_sub(widget):
    global _up, _dd, _bat_timer
    _bat_subs.append(widget)
    if _up is None:
        _up = UPowerManager()
        _dd = _up.get_display_device()
        _bat_timer = GLib.timeout_add_seconds(5, _bat_tick)


def _bat_tick():
    global _bat_pct, _bat_chg, _bat_time
    if not _bat_subs:
        return False
    try:
        if _dd:
            bat = _up.get_full_device_information(_dd)
            if bat:
                _bat_pct  = bat['Percentage']
                st        = bat['State']
                _bat_chg  = st in (1, 4)
                _bat_time = bat['TimeToFull'] if _bat_chg else bat['TimeToEmpty']
            else:
                _bat_pct = _bat_time = 0.0
                _bat_chg = None
    except Exception:
        pass

    for widget in _bat_subs:
        if hasattr(widget, '_upd'):
            try:
                widget._upd()
            except Exception:
                pass
    return True


class BatteryButton(Button):
    __slots__ = ('ic', 'cir', 'lv', 'rev', '_obc', '_lv', '_lc', '_lt', '_llow')

    def __init__(self, on_battery_changed=None, **kwargs):
        super().__init__(name='metrics-small', **kwargs)
        self._obc = on_battery_changed
        self._lv, self._lc, self._lt, self._llow = -1, None, -1, None

        self.ic  = Label(name='metrics-icon', markup=icons.battery)
        self.cir = CircularProgressBar(
            name='metrics-circle', value=0, size=28, line_width=2,
            start_angle=150, end_angle=390, style_classes='bat', child=self.ic
        )
        self.lv  = Label(name='metrics-level', style_classes='bat', label='100%')
        self.rev = Revealer(
            name='metrics-bat-revealer', transition_duration=250,
            transition_type='slide-left', child=self.lv
        )

        self.add(Box(
            name='metrics-bat-box', orientation='h', spacing=0,
            children=[self.cir, self.rev]
        ))
        _bat_sub(self)

    def _upd(self):
        val, chg, bt = _bat_pct, _bat_chg, _bat_time

        if val == self._lv and chg == self._lc and abs(bt - self._lt) < 60:
            return
        self._lv, self._lc, self._lt = val, chg, bt

        pct, low = int(val), val <= 30
        self.set_visible(val != 0)
        self.cir.set_value(val * 0.01)
        self.lv.set_label(f'{pct}%')

        if low != self._llow:
            self._llow = low
            if low:
                self.cir.set_style('border: 3px solid #ffa500;')
                self.ic.set_style('color: #ffa500;')
                self.cir.add_style_class('battery-low')
                self.cir.remove_style_class('battery-normal')
            else:
                self.cir.set_style('border: 3px solid var(--green);')
                self.ic.set_style('color: #d3d3d3;')
                self.cir.add_style_class('battery-normal')
                self.cir.remove_style_class('battery-low')

        t = (
            f'{int(bt)}sec'    if bt < 60   else
            f'{int(bt/60)}min' if bt < 3600 else
            f'{int(bt/3600)}h'
        )

        if pct == 100:
            self.ic.set_markup(icons.battery)
            tip = f'{icons.bat_full} Fully charged' + ('' if chg else f' - {t} remaining')
        elif chg:
            self.ic.set_markup(icons.charging)
            tip = f'{icons.bat_charging} Charging - {t} remaining'
        elif low:
            self.ic.set_markup(icons.alert)
            tip = f'{icons.bat_low} Low battery - {t} remaining'
        else:
            self.ic.set_markup(icons.discharging)
            tip = f'{icons.bat_discharging} Discharging - {t} remaining'

        self.set_tooltip_markup(tip)
        if self._obc:
            self._obc(val, chg)


class Battery(Box):
    __slots__ = (
        'btn', 'pmb', 'pmr', 'mode', 'auto', 'manual',
        'last_chg', 'is_low', '_timer_id', '_auto_pending_mode',
        '_mode_btns', '_is_hovered', '_ui_ready', '_current_lvl', '_current_chg'
    )

    def __init__(self, **kwargs):
        super().__init__(orientation='h', spacing=0, **kwargs)
        self.set_name('battery-container')

        self.auto     = True
        self.manual   = False
        self.last_chg = None
        self.is_low   = False
        self.mode     = 'balanced'

        self._ui_ready          = False
        self._current_lvl       = None
        self._current_chg       = None
        self._timer_id          = None
        self._auto_pending_mode = None
        self._is_hovered        = False
        self._mode_btns         = {}

        self.pmb = Box(name='power-mode-switcher', orientation='h', spacing=2)
        threading.Thread(target=self._init_pm_async, daemon=True).start()

        self.btn = BatteryButton(on_battery_changed=self._on_bat)
        self.pmr = Revealer(
            name='metrics-power-modes-revealer',
            transition_duration=250,
            transition_type='slide-left',
            child=self.pmb,
            child_revealed=False
        )

        self.add(self.btn)
        self.add(self.pmr)

        for w in (self, self.btn, self.pmb):
            w.connect('enter-notify-event', self._ent)
            w.connect('leave-notify-event', self._lv)

    def _init_pm_async(self):
        try:
            proc = subprocess.Popen(
                ['powerprofilesctl', 'get'],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True
            )
            out, _ = proc.communicate()
            out = out.strip()
            if out in ('power-saver', 'balanced', 'performance'):
                self.mode = out
        except Exception:
            pass
        GLib.idle_add(self._build_pm_ui)

    def _build_pm_ui(self):
        profiles = (
            ('power-saver',  'battery-save',       icons.power_saving,      'Saving'),
            ('balanced',     'battery-balanced',    icons.power_balanced,    'Balanced'),
            ('performance',  'battery-performance', icons.power_performance, 'Performance'),
        )

        for mode, name, icon, tip in profiles:
            btn = Button(
                name=name,
                child=Label(name=f'{name}-label', markup=icon),
                tooltip_text=tip
            )
            btn.connect('clicked', self._on_mode_btn_clicked, mode)
            btn.connect('enter-notify-event', self._ent)
            btn.connect('leave-notify-event', self._lv)
            _hov(btn)
            self.pmb.add(btn)
            self._mode_btns[mode] = btn

        self.pmb.show_all()
        self._ui_ready = True

        if self._current_chg is not None:
            self._sync_initial_state()
        else:
            self._upd_styles()

    def _sync_initial_state(self):
        self.last_chg = self._current_chg
        self.is_low   = self._current_lvl <= 30
        tgt = 'performance' if self._current_chg else ('power-saver' if self.is_low else 'balanced')

        if self.mode != tgt:
            self.mode = tgt
            try:
                Gio.Subprocess.new(['powerprofilesctl', 'set', tgt], Gio.SubprocessFlags.NONE)
            except Exception:
                pass

        self._upd_styles()

    def _on_bat(self, lvl, chg):
        self._current_lvl = lvl
        self._current_chg = chg

        if not self._ui_ready:
            return

        if self.last_chg is None:
            self._sync_initial_state()
            return

        if not self.auto:
            return

        is_currently_low   = lvl <= 30
        hardware_triggered = False

        if self.last_chg is not chg:
            self.last_chg      = chg
            self.manual        = False
            hardware_triggered = True

        if not chg and is_currently_low and not self.is_low:
            self.manual        = False
            hardware_triggered = True

        self.is_low = is_currently_low

        if self.manual and not hardware_triggered:
            return

        tgt = 'performance' if chg else ('power-saver' if self.is_low else 'balanced')

        if tgt != self.mode and tgt != self._auto_pending_mode:
            self._start_auto_switch_sequence(tgt)

    def _start_auto_switch_sequence(self, mode):
        if self._timer_id:
            GLib.source_remove(self._timer_id)

        self._auto_pending_mode = mode
        self.btn.rev.set_reveal_child(True)
        self.pmr.set_reveal_child(True)
        self._timer_id = GLib.timeout_add(300, self._apply_auto_switch)

    def _apply_auto_switch(self):
        if not self._auto_pending_mode:
            return False

        mode      = self._auto_pending_mode
        self.mode = mode
        self._upd_styles()

        try:
            Gio.Subprocess.new(['powerprofilesctl', 'set', mode], Gio.SubprocessFlags.NONE)
        except Exception:
            pass

        self._auto_pending_mode = None

        if not self._is_hovered:
            self._timer_id = GLib.timeout_add(2000, self._hide)
        return False

    def _on_mode_btn_clicked(self, btn, mode):
        if self.mode == mode:
            return

        self.manual = True
        self.mode   = mode
        self._upd_styles()

        try:
            Gio.Subprocess.new(['powerprofilesctl', 'set', mode], Gio.SubprocessFlags.NONE)
        except Exception:
            pass

    def _ent(self, *_):
        self._is_hovered = True

        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

        if self._auto_pending_mode:
            self._apply_auto_switch()

        self.btn.rev.set_reveal_child(True)
        self.pmr.set_reveal_child(True)
        return False

    def _lv(self, *_):
        self._is_hovered = False

        if self._timer_id:
            GLib.source_remove(self._timer_id)

        self._timer_id = GLib.timeout_add(300, self._hide)
        return False

    def _hide(self):
        if self._auto_pending_mode or self._is_hovered:
            return False

        self.btn.rev.set_reveal_child(False)
        self.pmr.set_reveal_child(False)
        self._timer_id = None
        return False

    def _upd_styles(self):
        for mode, btn in self._mode_btns.items():
            if mode == self.mode:
                btn.add_style_class('active')
            else:
                btn.remove_style_class('active')