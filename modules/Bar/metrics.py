import os
import threading
import time
import weakref
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.circularprogressbar import CircularProgressBar
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer
from fabric.widgets.scale import Scale
from fabric.utils import exec_shell_command_async
from gi.repository import GLib
import subprocess

from services.network import NetworkClient
from services.upower import UPowerManager
import services.icons as icons

_prov = None
_subs = weakref.WeakSet()


def _sub(widget):
    global _prov
    _subs.add(widget)
    if _prov is None:
        _prov = MetricsProvider()


class MetricsProvider:
    __slots__ = (
        'cpu', 'mem', 'disk', 'gpu', 'temp',
        'bat_pct', 'bat_chg', 'bat_time', 'net_dl', 'net_ul',
        '_ci', '_ct', '_nr', '_ns', '_nt',
        '_gt', '_gp', '_tp', '_up', '_dd', '_run'
    )

    def __init__(self):
        self.cpu = self.mem = self.temp = self.bat_pct = self.bat_time = 0.0
        self.net_dl = self.net_ul = 0.0
        self.disk, self.gpu = [0.0], []
        self.bat_chg = None
        self._ci = self._ct = self._nr = self._ns = 0
        self._nt = time.monotonic()
        self._gt, self._gp, self._tp = 0, [], None
        
        self._up = UPowerManager()
        self._dd = self._up.get_display_device()
        self._detect_hw()
        self._init_net()
        
        self._run = True
        threading.Thread(target=self._worker, daemon=True).start()

    def _detect_hw(self):
        for p in ('/sys/class/thermal/thermal_zone0/temp', '/sys/class/hwmon/hwmon0/temp1_input'):
            if os.path.exists(p):
                self._tp = p
                break

        try:
            if 'GPU' in subprocess.run(['nvidia-smi', '-L'], capture_output=True, text=True).stdout:
                self._gt, self.gpu = 1, [0.0]
                return
        except Exception: pass

        for i in range(8):
            amd_path = f'/sys/class/drm/card{i}/device/gpu_busy_percent'
            if os.path.exists(amd_path):
                try:
                    with open(amd_path) as f: int(f.read().strip())
                    self._gt, self._gp, self.gpu = 2, [amd_path], [0.0]
                    return
                except Exception: pass

        if os.system('which intel_gpu_top >/dev/null 2>&1') == 0:
            self._gt, self.gpu = 3, [0.0]

    def _init_net(self):
        try:
            with open('/proc/net/dev', 'r') as f:
                r = s = 0
                for ln in f.readlines()[2:]:
                    if 'lo:' not in ln:
                        p = ln.split()
                        r += int(p[1])
                        s += int(p[9])
                self._nr, self._ns = r, s
        except Exception: pass

    def _worker(self):
        while self._run:
            if not _subs:
                GLib.idle_add(self.cleanup)
                break
                
            self._gather_metrics()
            GLib.idle_add(self._notify_ui)
            time.sleep(2.0)

    def _gather_metrics(self):
        try:
            with open('/proc/stat', 'r') as f:
                p = f.readline().split()
            idle = int(p[4])
            total = sum(int(x) for x in p[1:8])
            di, dt = idle - self._ci, total - self._ct
            self._ci, self._ct = idle, total
            self.cpu = (1.0 - di / dt) * 100.0 if dt else 0.0
        except Exception: pass

        try:
            with open('/proc/meminfo', 'r') as f:
                txt = f.read()
                mt = int(txt[txt.find('MemTotal:'):].split()[1])
                ma = int(txt[txt.find('MemAvailable:'):].split()[1])
            self.mem = (1.0 - ma / mt) * 100.0 if mt else 0.0
        except Exception: pass

        try:
            st = os.statvfs('/')
            t = st.f_blocks * st.f_frsize
            self.disk[0] = (1.0 - (st.f_bfree * st.f_frsize) / t) * 100.0 if t else 0.0
        except Exception: pass

        if self._tp:
            try:
                with open(self._tp, 'r') as f:
                    self.temp = int(f.read()) * 0.001
            except Exception: pass

        if self._dd:
            try:
                bat = self._up.get_full_device_information(self._dd)
                if bat:
                    self.bat_pct, st = bat['Percentage'], bat['State']
                    self.bat_chg = st in (1, 4)
                    self.bat_time = bat['TimeToFull'] if self.bat_chg else bat['TimeToEmpty']
                else:
                    self.bat_pct = self.bat_time = 0.0
                    self.bat_chg = None
            except Exception: pass

        now = time.monotonic()
        dt = now - self._nt
        if dt > 0:
            try:
                with open('/proc/net/dev', 'r') as f:
                    r = s = 0
                    for ln in f.readlines()[2:]:
                        if 'lo:' not in ln:
                            p = ln.split()
                            r += int(p[1])
                            s += int(p[9])
                self.net_dl, self.net_ul = (r - self._nr) / dt, (s - self._ns) / dt
                self._nr, self._ns, self._nt = r, s, now
            except Exception: pass

        gt = self._gt
        if gt == 1:
            try:
                out = subprocess.run(['nvidia-smi', '--query-gpu=utilization.gpu', '--format=csv,noheader,nounits', '-i', '0'], capture_output=True, text=True).stdout
                self.gpu[0] = max(0.0, min(100.0, float(out.strip())))
            except Exception: pass
        elif gt == 2:
            try:
                with open(self._gp[0]) as f:
                    self.gpu[0] = max(0.0, min(100.0, float(f.read().strip())))
            except Exception: pass
        elif gt == 3:
            try:
                out = subprocess.run(['timeout', '0.4', 'intel_gpu_top', '-J', '-s', '100'], capture_output=True, text=True).stdout
                if out:
                    # Избавились от Regex. Быстрый строковый поиск
                    idx = out.rfind('"Render/3D"')
                    if idx != -1:
                        busy_idx = out.find('"busy":', idx)
                        self.gpu[0] = max(0.0, min(100.0, float(out[busy_idx+7:].split(',')[0].strip())))
                    else:
                        idx = out.rfind('"rc6"')
                        if idx != -1:
                            val_idx = out.find('"value":', idx)
                            rc6 = float(out[val_idx+8:].split(',')[0].strip())
                            self.gpu[0] = max(0.0, min(100.0, 100.0 - rc6))
            except Exception: pass

    def _notify_ui(self):
        for widget in _subs:
            if hasattr(widget, '_upd'):
                try: widget._upd()
                except Exception: pass
        return False

    def get_gpu_info(self):
        return [{}] if self._gt else []

    def cleanup(self):
        self._run = False
        self.gpu = self.disk = self._gp = []
        self._up = self._dd = None
        global _prov
        _prov = None


class SingularMetric:
    __slots__ = ('usage', 'label', 'box', '_last_v')

    def __init__(self, id, name, icon):
        self._last_v = -1
        self.usage = Scale(name=f'{id}-usage', value=0.25, orientation='v', inverted=True, v_align='fill', v_expand=True)
        self.label = Label(name=f'{id}-label', markup=icon)
        self.box = Box(name=f'{id}-box', orientation='v', spacing=8, children=[self.usage, self.label])
        self.box.set_tooltip_markup(f'{icon} {name}')
        self.usage.set_sensitive(False)

    def set_val(self, v):
        if abs(self._last_v - v) > 0.005:
            self.usage.set_value(v)
            self._last_v = v


class SingularMetricSmall:
    __slots__ = ('nm', 'ic', 'is_t', 'icon', 'circle', 'level', 'rev', 'box', '_lv', '_ll')

    def __init__(self, id, name, icon, is_temp=False):
        self.nm, self.ic, self.is_t = name, icon, is_temp
        self._lv, self._ll = -1, ""
        self.icon = Label(name='metrics-icon', markup=icon)
        self.circle = CircularProgressBar(name='metrics-circle', value=0, size=28, line_width=2, start_angle=150, end_angle=390, style_classes=id, child=self.icon)
        self.level = Label(name='metrics-level', style_classes=id, label='0°C' if is_temp else '0%')
        self.rev = Revealer(name=f'metrics-{id}-revealer', transition_duration=250, transition_type='slide-left', child=self.level, child_revealed=False)
        self.box = Box(name=f'metrics-{id}-box', orientation='h', spacing=0, children=[self.circle, self.rev])

    def update(self, val, lbl):
        if abs(self._lv - val) > 0.005:
            self.circle.set_value(val)
            self._lv = val
        if self._ll != lbl:
            self.level.set_label(lbl)
            self._ll = lbl

    def markup(self):
        return f'{self.ic} {self.nm}'


class Metrics(Box):
    __slots__ = ('temp', 'disk', 'ram', 'cpu', 'gpu')

    def __init__(self, **kwargs):
        super().__init__(name='metrics', spacing=8, h_align='center', v_align='fill', visible=True, all_visible=True)
        _sub(self)
        self.temp = SingularMetric('temp', 'ТЕМП', icons.temp)
        self.disk = [SingularMetric('disk', 'ДИСК', icons.disk)]
        self.ram = SingularMetric('ram', 'ОЗУ', icons.memory)
        self.cpu = SingularMetric('cpu', 'ЦП', icons.cpu)
        self.gpu = [SingularMetric('gpu', 'GPU', icons.gpu) for _ in (_prov.get_gpu_info() if _prov else [])]
                   
        for m in (self.temp,) + tuple(self.disk) + (self.ram, self.cpu) + tuple(self.gpu):
            self.add(m.box)

    def _upd(self):
        if not _prov: return
        self.temp.set_val(min(_prov.temp * 0.00666667, 1.0))
        self.ram.set_val(_prov.mem * 0.01)
        self.cpu.set_val(_prov.cpu * 0.01)
        
        for i, d in enumerate(self.disk):
            if i < len(_prov.disk): d.set_val(_prov.disk[i] * 0.01)
        for i, g in enumerate(self.gpu):
            if i < len(_prov.gpu): g.set_val(_prov.gpu[i] * 0.01)


class MetricsSmall(Button):
    __slots__ = ('temp', 'cpu', 'ram', 'disk', 'gpu', 'htim', 'hov', '_all')

    def __init__(self, **kwargs):
        super().__init__(name='metrics-small', **kwargs)
        _sub(self)
        box = Box(spacing=0, orientation='h', visible=True, all_visible=True)
        self.temp = SingularMetricSmall('temp', 'ТЕМП', icons.temp, True)
        self.disk = [SingularMetricSmall('disk', 'ДИСК', icons.disk)]
        self.cpu = SingularMetricSmall('cpu', 'ЦП', icons.cpu)
        self.ram = SingularMetricSmall('ram', 'ОЗУ', icons.memory)
        self.gpu = [SingularMetricSmall('gpu', 'GPU', icons.gpu) for _ in (_prov.get_gpu_info() if _prov else [])]
        self._all = [self.temp] + self.disk + [self.ram, self.cpu] + self.gpu
        
        for w in self._all:
            box.add(w.box)
            box.add(Box(name='metrics-sep'))
        self.add(box)
        self.htim, self.hov = None, 0
        
        self.connect('enter-notify-event', self._ent)
        self.connect('leave-notify-event', self._lv)

    def _upd(self):
        if not _prov: return
        
        self.temp.update(min(_prov.temp * 0.00666667, 1.0), f'{int(_prov.temp + 0.5)}°C')
        self.cpu.update(_prov.cpu * 0.01, f'{int(_prov.cpu)}%')
        self.ram.update(_prov.mem * 0.01, f'{int(_prov.mem)}%')
        
        for i, d in enumerate(self.disk):
            if i < len(_prov.disk): d.update(_prov.disk[i] * 0.01, f'{int(_prov.disk[i])}%')
        for i, g in enumerate(self.gpu):
            if i < len(_prov.gpu): g.update(_prov.gpu[i] * 0.01, f'{int(_prov.gpu[i])}%')
            
        self.set_tooltip_markup(' - '.join(m.markup() for m in self._all))

    def _ent(self, *_):
        self.hov += 1
        if self.htim:
            GLib.source_remove(self.htim)
            self.htim = None
        for m in self._all: m.rev.set_reveal_child(True)
        return False

    def _lv(self, *_):
        self.hov = max(0, self.hov - 1)
        if self.hov: return False
        if self.htim: GLib.source_remove(self.htim)
        self.htim = GLib.timeout_add(500, self._hide)
        return False

    def _hide(self):
        for m in self._all: m.rev.set_reveal_child(False)
        self.htim = None
        return False


class BatteryButton(Button):
    __slots__ = ('ic', 'cir', 'lv', 'rev', '_obc', '_lv', '_lc', '_lt', '_llow')

    def __init__(self, on_battery_changed=None, **kwargs):
        super().__init__(name='metrics-small', **kwargs)
        self._obc = on_battery_changed
        self._lv, self._lc, self._lt, self._llow = -1, None, -1, None
        
        self.ic = Label(name='metrics-icon', markup=icons.battery)
        self.cir = CircularProgressBar(name='metrics-circle', value=0, size=28, line_width=2, start_angle=150, end_angle=390, style_classes='bat', child=self.ic)
        self.lv = Label(name='metrics-level', style_classes='bat', label='100%')
        self.rev = Revealer(name='metrics-bat-revealer', transition_duration=250, transition_type='slide-left', child=self.lv)
        
        self.add(Box(name='metrics-bat-box', orientation='h', spacing=0, children=[self.cir, self.rev]))
        _sub(self)

    def _upd(self):
        if not _prov: return
        val, chg, bt = _prov.bat_pct, _prov.bat_chg, _prov.bat_time
        
        if val == self._lv and chg == self._lc and abs(bt - self._lt) < 60: return
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
            
        t = f'{int(bt)}сек' if bt < 60 else f'{int(bt/60)}мин' if bt < 3600 else f'{int(bt/3600)}ч'
        
        if pct == 100:
            self.ic.set_markup(icons.battery)
            tip = f'{icons.bat_full} Полностью заряжено' + ('' if chg else f' - осталось {t}')
        elif chg:
            self.ic.set_markup(icons.charging)
            tip = f'{icons.bat_charging} Заряжается - осталось {t}'
        elif low:
            self.ic.set_markup(icons.alert)
            tip = f'{icons.bat_low} Низкий заряд - осталось {t}'
        else:
            self.ic.set_markup(icons.discharging)
            tip = f'{icons.bat_discharging} Разряжается - осталось {t}'
            
        self.set_tooltip_markup(tip)
        if self._obc: self._obc(val, chg)


class Battery(Box):
    __slots__ = ('btn', 'pmb', 'pmr', 'bs', 'bb', 'bp', 'mode', 'htim', 'auto', 'manual', 'last_chg', 'is_low')

    def __init__(self, **kwargs):
        super().__init__(orientation='h', spacing=0, **kwargs)
        self.set_name('battery-container')
        self.auto, self.manual, self.last_chg, self.is_low = True, False, None, False
        self.mode, self.htim = 'balanced', None
        self.bs = self.bb = self.bp = None
        self.pmb = Box(name='power-mode-switcher', orientation='h', spacing=2)
        
        threading.Thread(target=self._init_pm_async, daemon=True).start()
        
        self.btn = BatteryButton(on_battery_changed=self._on_bat)
        self.pmr = Revealer(name='metrics-power-modes-revealer', transition_duration=250, transition_type='slide-left', child=self.pmb, child_revealed=False)
        self.add(self.btn)
        self.add(self.pmr)
        
        for w in (self, self.btn, self.pmb):
            w.connect('enter-notify-event', self._ent)
            w.connect('leave-notify-event', self._lv)

    def _init_pm_async(self):
        try:
            out = subprocess.run(['powerprofilesctl', 'get'], capture_output=True, text=True).stdout.strip()
            if out in ('power-saver', 'balanced', 'performance'):
                self.mode = out
        except Exception: pass
        GLib.idle_add(self._build_pm_ui)

    def _build_pm_ui(self):
        for mode, name, icon, tip in (
            ('power-saver', 'battery-save', icons.power_saving, 'Энергосбережение'),
            ('balanced', 'battery-balanced', icons.power_balanced, 'Сбалансированный'),
            ('performance', 'battery-performance', icons.power_performance, 'Производительный')
        ):
            btn = Button(name=name, child=Label(name=f'{name}-label', markup=icon), tooltip_text=tip)
            btn.connect('clicked', self._on_mode_btn_clicked, mode)
            btn.connect('enter-notify-event', self._ent)
            btn.connect('leave-notify-event', self._lv)
            self.pmb.add(btn)
            setattr(self, 'bs' if mode == 'power-saver' else 'bb' if mode == 'balanced' else 'bp', btn)
        
        self.pmb.show_all()
        self._upd_styles()

    def _on_bat(self, lvl, chg):
        if not self.auto: return
        if self.last_chg is not chg:
            self.last_chg, self.manual = chg, False
        self.is_low = lvl <= 30
        if self.manual: return
        
        tgt = ('performance' if self.bp else 'balanced') if chg else (('power-saver' if self.bs else 'balanced') if self.is_low else 'balanced')
        if tgt != self.mode: self._set(tgt, manual=False)

    def _on_mode_btn_clicked(self, btn, mode):
        self._set(mode, manual=True)

    def _ent(self, *_):
        if self.htim:
            GLib.source_remove(self.htim)
            self.htim = None
        self.btn.rev.set_reveal_child(True)
        self.pmr.set_reveal_child(True)
        return False

    def _lv(self, *_):
        if self.htim: GLib.source_remove(self.htim)
        self.htim = GLib.timeout_add(300, self._hide)
        return False

    def _hide(self):
        self.btn.rev.set_reveal_child(False)
        self.pmr.set_reveal_child(False)
        self.htim = None
        return False

    def _set(self, mode, manual=True):
        if manual: self.manual = True
        self.mode = mode
        self._upd_styles()
        exec_shell_command_async(f'powerprofilesctl set {mode}')

    def _upd_styles(self):
        for btn in (self.bs, self.bb, self.bp):
            if btn: btn.remove_style_class('active')
        tb = self.bs if self.mode == 'power-saver' else self.bb if self.mode == 'balanced' else self.bp
        if tb: tb.add_style_class('active')


class NetworkApplet(Button):
    __slots__ = ('nc', 'hov', 'dl', 'ul', 'wl', 'dlr', 'ulr', '_ldl', '_lul')

    def __init__(self, **kwargs):
        super().__init__(name='button-bar', **kwargs)
        self.nc, self.hov = NetworkClient(), False
        self._ldl = self._lul = ""
        
        self.dl = Label(name='download-label', markup='0 B/s')
        self.ul = Label(name='upload-label', markup='0 B/s')
        self.wl = Label(name='network-icon-label', markup=icons.world_off)
        self.dlr = Revealer(child=Box(children=[Label(name='download-icon-label', markup=icons.download), self.dl]), transition_type='slide-right', child_revealed=False)
        self.ulr = Revealer(child=Box(children=[self.ul, Label(name='upload-icon-label', markup=icons.upload)]), transition_type='slide-left', child_revealed=False)
            
        self.add(Box(orientation='h', children=[self.ulr, self.wl, self.dlr]))
        self.connect('enter-notify-event', self._ent)
        self.connect('leave-notify-event', self._lv)
        _sub(self)

    def _ent(self, *_):
        self.hov = True
        self.dlr.set_reveal_child(True)
        self.ulr.set_reveal_child(True)
        return False

    def _lv(self, *_):
        self.hov = False
        self.dlr.set_reveal_child(False)
        self.ulr.set_reveal_child(False)
        return False

    def _upd(self):
        if not _prov: return
        
        # ОПТИМИЗАЦИЯ: Не форматируем строки и не дергаем GTK, если виджет скрыт (Revealer закрыт)
        if self.hov or self.dlr.get_child_revealed():
            fdl = self._fmt(_prov.net_dl)
            ful = self._fmt(_prov.net_ul)
            if self._ldl != fdl:
                self.dl.set_markup(fdl)
                self._ldl = fdl
            if self._lul != ful:
                self.ul.set_markup(ful)
                self._lul = ful
        
        ed, wd = self.nc.ethernet_device, self.nc.wifi_device
        if ed and ed.internet in ('activated', 'activating'):
            self.wl.set_markup(icons.world)
            self.set_tooltip_text('Ethernet')
        elif not wd or not getattr(wd, 'enabled', False):
            self.wl.set_markup(icons.world_off)
            self.set_tooltip_text('Disconnected')
        else:
            ssid = getattr(wd, 'ssid', None)
            if not ssid or ssid in ('Disconnected', 'Выключено', 'Не подключено'):
                self.wl.set_markup(icons.world_off)
                self.set_tooltip_text('Disconnected')
            else:
                s = wd.strength
                self.wl.set_markup(icons.wifi_3 if s >= 75 else icons.wifi_2 if s >= 50 else icons.wifi_1 if s >= 25 else icons.wifi_0)
                self.set_tooltip_text(ssid)

    @staticmethod
    def _fmt(sp):
        return (f'{sp:.0f} B/s' if sp < 1024 else f'{sp * 0.0009765625:.1f} KB/s' if sp < 1048576 else f'{sp * 9.5367431640625e-07:.1f} MB/s')