from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.circularprogressbar import CircularProgressBar
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer
from fabric.widgets.scale import Scale
from fabric.utils import exec_shell_command_async

from gi.repository import GLib, Gio

from services.network import NetworkClient
from services.upower import UPowerManager
import services.icons as icons

_STAT, _MEM, _NET = '/proc/stat', '/proc/meminfo', '/proc/net/dev'
_MT, _MA, _LO = b'MemTotal:', b'MemAvailable:', b'lo'

_prov = None
_subs = []


def _sub(cb):
    global _prov
    if cb not in _subs:
        _subs.append(cb)
    if _prov is None:
        _prov = MetricsProvider()


def _unsub(cb):
    global _prov
    try:
        _subs.remove(cb)
    except ValueError:
        pass
    if not _subs and _prov:
        _prov.cleanup()
        _prov = None


class MetricsProvider:
    __slots__ = (
        'cpu', 'mem', 'disk', 'gpu', 'temp',
        'bat_pct', 'bat_chg', 'bat_time',
        'net_dl', 'net_ul',
        '_pi', '_pt', '_nr', '_ns', '_nt',
        '_gt', '_gc', '_gp', '_tp',
        '_up', '_dd', '_gr', '_tid'
    )

    def __init__(self):
        self.cpu = self.mem = self.temp = 0.0
        self.disk = [0.0]
        self.gpu = []
        self.bat_pct = self.bat_time = 0.0
        self.bat_chg = None
        self.net_dl = self.net_ul = 0.0
        self._pi = self._pt = 0
        self._nr = self._ns = 0
        self._nt = GLib.get_monotonic_time()
        self._gt = self._gc = 0
        self._gp = []
        self._tp = None
        self._gr = False
        self._up = UPowerManager()
        self._dd = self._up.get_display_device()
        self._detect_hw()
        self._init_net()
        self._tid = GLib.timeout_add(2000, self._tick)

    def _detect_hw(self):
        ft, fe = GLib.file_test, GLib.FileTest.EXISTS
        for p in ('/sys/class/thermal/thermal_zone0/temp', '/sys/class/hwmon/hwmon0/temp1_input'):
            if ft(p, fe):
                self._tp = p
                break
        try:
            proc = Gio.Subprocess.new(
                ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
            _, out, _ = proc.communicate_utf8(None)
            if out and out.strip():
                n = out.count('\n') + (1 if out.strip() else 0)
                self._gt, self._gc = 1, n
                self.gpu = [0.0] * n
                return
        except:
            pass
        gp = self._gp
        for i in range(10):
            p = f'/sys/class/drm/card{i}/device/gpu_busy_percent'
            if ft(p, fe):
                gp.append(p)
        if gp:
            self._gt, self._gc = 2, len(gp)
            self.gpu = [0.0] * self._gc
            return
        for base in ('/sys/class/drm/card0/gt/gt0/rps_', '/sys/class/drm/card0/gt_'):
            cur, mx = base + 'cur_freq_mhz', base + 'max_freq_mhz'
            if ft(cur, fe) and ft(mx, fe):
                self._gp = [cur, mx]
                self._gt, self._gc = 3, 1
                self.gpu = [0.0]
                return

    def _init_net(self):
        try:
            with open(_NET, 'rb') as f:
                f.readline(); f.readline()
                nr = ns = 0
                for ln in f:
                    p = ln.split()
                    if not p[0].rstrip(b':').endswith(_LO):
                        nr += int(p[1]); ns += int(p[9])
                self._nr, self._ns = nr, ns
        except:
            pass

    def _tick(self):
        self._read_cpu()
        self._read_mem()
        self._read_disk()
        self._read_temp()
        self._read_gpu()
        self._read_bat()
        self._read_net()
        for cb in _subs[:]:
            try:
                cb()
            except:
                try:
                    _subs.remove(cb)
                except:
                    pass
        return True

    def _read_cpu(self):
        try:
            with open(_STAT, 'rb') as f:
                p = f.readline().split()
            idle, total = int(p[4]), sum(int(v) for v in p[1:8])
            di, dt = idle - self._pi, total - self._pt
            self._pi, self._pt = idle, total
            self.cpu = (1.0 - di / dt) * 100.0 if dt else 0.0
        except:
            pass

    def _read_mem(self):
        try:
            mt = ma = 0
            with open(_MEM, 'rb') as f:
                for ln in f:
                    if ln[:9] == _MT:
                        mt = int(ln.split()[1])
                    elif ln[:13] == _MA:
                        ma = int(ln.split()[1])
                        break
            self.mem = (1.0 - ma / mt) * 100.0 if mt else 0.0
        except:
            pass

    def _read_disk(self):
        try:
            info = Gio.File.new_for_path("/").query_filesystem_info("filesystem::size,filesystem::free", None)
            t = info.get_attribute_uint64("filesystem::size")
            fr = info.get_attribute_uint64("filesystem::free")
            self.disk[0] = (1.0 - fr / t) * 100.0 if t else 0.0
        except:
            pass

    def _read_temp(self):
        if self._tp:
            try:
                with open(self._tp, 'rb') as f:
                    self.temp = int(f.read()) * 0.001
            except:
                pass

    def _read_gpu(self):
        gt = self._gt
        if gt == 1:
            if not self._gr:
                self._gr = True
                try:
                    proc = Gio.Subprocess.new(
                        ['nvidia-smi', '--query-gpu=utilization.gpu', '--format=csv,noheader,nounits'],
                        Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
                    proc.communicate_utf8_async(None, None, self._nv_cb)
                except:
                    self._gr = False
        elif gt == 2:
            gpu = self.gpu
            for i, p in enumerate(self._gp):
                try:
                    with open(p, 'rb') as f:
                        v = float(f.read().strip())
                        gpu[i] = max(0.0, min(100.0, v))
                except:
                    pass
        elif gt == 3 and len(self._gp) == 2:
            try:
                with open(self._gp[0], 'rb') as f:
                    cur = int(f.read().strip())
                with open(self._gp[1], 'rb') as f:
                    mx = int(f.read().strip())
                self.gpu[0] = min(100.0, (cur / mx) * 100.0) if mx else 0.0
            except:
                pass

    def _nv_cb(self, proc, res):
        try:
            _, out, _ = proc.communicate_utf8_finish(res)
            if out:
                gpu, gc = self.gpu, len(self.gpu)
                for i, line in enumerate(out.strip().split('\n')):
                    if i >= gc:
                        break
                    try:
                        gpu[i] = max(0.0, min(100.0, float(line.strip())))
                    except:
                        pass
        except:
            pass
        self._gr = False

    def _read_bat(self):
        bat = self._up.get_full_device_information(self._dd)
        if bat:
            self.bat_pct = bat['Percentage']
            st = bat['State']
            self.bat_chg = st == 1 or st == 4
            self.bat_time = bat['TimeToFull'] if self.bat_chg else bat['TimeToEmpty']
        else:
            self.bat_pct = self.bat_time = 0.0
            self.bat_chg = None

    def _read_net(self):
        now = GLib.get_monotonic_time()
        dt = (now - self._nt) * 1e-6
        if dt <= 0:
            return
        recv = sent = 0
        try:
            with open(_NET, 'rb') as f:
                f.readline(); f.readline()
                for ln in f:
                    p = ln.split()
                    if not p[0].rstrip(b':').endswith(_LO):
                        recv += int(p[1]); sent += int(p[9])
        except:
            pass
        self.net_dl = (recv - self._nr) / dt
        self.net_ul = (sent - self._ns) / dt
        self._nr, self._ns, self._nt = recv, sent, now

    def get_gpu_info(self):
        return [{}] * (self._gc or 1) if self._gt else []

    def cleanup(self):
        if self._tid:
            GLib.source_remove(self._tid)
            self._tid = None
        self.gpu = []
        self.disk = []
        self._gp = []
        self._up = self._dd = None


class SingularMetric:
    __slots__ = ('usage', 'label', 'box')

    def __init__(self, id, name, icon):
        self.usage = Scale(name=f"{id}-usage", value=0.25, orientation='v',
                          inverted=True, v_align='fill', v_expand=True)
        self.label = Label(name=f"{id}-label", markup=icon)
        self.box = Box(name=f"{id}-box", orientation='v', spacing=8, children=[self.usage, self.label])
        self.box.set_tooltip_markup(f"{icon} {name}")


class SingularMetricSmall:
    __slots__ = ('nm', 'ic', 'is_t', 'icon', 'circle', 'level', 'rev', 'box')

    def __init__(self, id, name, icon, is_temp=False):
        self.nm, self.ic, self.is_t = name, icon, is_temp
        self.icon = Label(name="metrics-icon", markup=icon)
        self.circle = CircularProgressBar(
            name="metrics-circle", value=0, size=28, line_width=2,
            start_angle=150, end_angle=390, style_classes=id, child=self.icon)
        self.level = Label(name="metrics-level", style_classes=id, label="0°C" if is_temp else "0%")
        self.rev = Revealer(name=f"metrics-{id}-revealer", transition_duration=250,
                           transition_type="slide-left", child=self.level, child_revealed=False)
        self.box = Box(name=f"metrics-{id}-box", orientation="h", spacing=0, children=[self.circle, self.rev])

    def markup(self):
        return f"{self.ic} {self.nm}"


class Metrics(Box):
    __slots__ = ('temp', 'disk', 'ram', 'cpu', 'gpu')

    def __init__(self, **kwargs):
        super().__init__(name="metrics", spacing=8, h_align="center", v_align="fill", visible=True, all_visible=True)
        _sub(self._upd)
        self.temp = SingularMetric("temp", "ТЕМП", icons.temp)
        self.disk = [SingularMetric("disk", "ДИСК", icons.disk)]
        self.ram = SingularMetric("ram", "ОЗУ", icons.memory)
        self.cpu = SingularMetric("cpu", "ЦП", icons.cpu)
        gi = _prov.get_gpu_info() if _prov else []
        self.gpu = [SingularMetric("gpu", "GPU", icons.gpu) for _ in gi] if gi else []
        for m in (self.temp,) + tuple(self.disk) + (self.ram, self.cpu) + tuple(self.gpu):
            m.usage.set_sensitive(False)
            self.add(m.box)
        self.connect('destroy', lambda *_: _unsub(self._upd))

    def _upd(self):
        p = _prov
        if not p:
            return
        self.temp.usage.value = min(p.temp * 0.00666667, 1.0)
        self.ram.usage.value = p.mem * 0.01
        self.cpu.usage.value = p.cpu * 0.01
        for i, d in enumerate(self.disk):
            if i < len(p.disk):
                d.usage.value = p.disk[i] * 0.01
        for i, g in enumerate(self.gpu):
            if i < len(p.gpu):
                g.usage.value = p.gpu[i] * 0.01


class MetricsSmall(Button):
    __slots__ = ('temp', 'cpu', 'ram', 'disk', 'gpu', 'htim', 'hov', '_all')

    def __init__(self, **kwargs):
        super().__init__(name="metrics-small", **kwargs)
        _sub(self._upd)
        box = Box(spacing=0, orientation="h", visible=True, all_visible=True)
        self.temp = SingularMetricSmall("temp", "ТЕМП", icons.temp, True)
        self.disk = [SingularMetricSmall("disk", "ДИСК", icons.disk)]
        self.cpu = SingularMetricSmall("cpu", "ЦП", icons.cpu)
        self.ram = SingularMetricSmall("ram", "ОЗУ", icons.memory)
        gi = _prov.get_gpu_info() if _prov else []
        self.gpu = [SingularMetricSmall("gpu", "GPU", icons.gpu) for _ in gi] if gi else []
        self._all = [self.temp] + self.disk + [self.ram, self.cpu] + self.gpu
        for w in self._all:
            box.add(w.box)
            box.add(Box(name="metrics-sep"))
        self.add(box)
        self.htim, self.hov = None, 0
        self.connect("enter-notify-event", self._ent)
        self.connect("leave-notify-event", self._lv)
        self.connect('destroy', lambda *_: _unsub(self._upd))

    def _upd(self):
        p = _prov
        if not p:
            return
        t = self.temp
        t.circle.set_value(min(p.temp * 0.00666667, 1.0))
        t.level.set_label(f"{int(p.temp + 0.5)}°C")
        c = self.cpu
        c.circle.set_value(p.cpu * 0.01)
        c.level.set_label(f"{int(p.cpu)}%")
        r = self.ram
        r.circle.set_value(p.mem * 0.01)
        r.level.set_label(f"{int(p.mem)}%")
        for i, d in enumerate(self.disk):
            if i < len(p.disk):
                dv = p.disk[i]
                d.circle.set_value(dv * 0.01)
                d.level.set_label(f"{int(dv)}%")
        for i, g in enumerate(self.gpu):
            if i < len(p.gpu):
                gv = p.gpu[i]
                g.circle.set_value(gv * 0.01)
                g.level.set_label(f"{int(gv)}%")
        self.set_tooltip_markup(" - ".join(m.markup() for m in self._all))

    def _ent(self, *_):
        self.hov += 1
        if self.htim:
            GLib.source_remove(self.htim)
            self.htim = None
        for m in self._all:
            m.rev.set_reveal_child(True)

    def _lv(self, *_):
        self.hov = max(0, self.hov - 1)
        if self.hov:
            return
        if self.htim:
            GLib.source_remove(self.htim)
        self.htim = GLib.timeout_add(500, self._hide)

    def _hide(self):
        for m in self._all:
            m.rev.set_reveal_child(False)
        self.htim = None
        return False


class BatteryButton(Button):
    __slots__ = ('ic', 'cir', 'lv', 'rev', '_obc', '_lv', '_lc', '_lt')

    def __init__(self, on_battery_changed=None, **kwargs):
        super().__init__(name="metrics-small", **kwargs)
        self._obc = on_battery_changed
        self._lv, self._lc, self._lt = -1, None, -1
        self.ic = Label(name="metrics-icon", markup=icons.battery)
        self.cir = CircularProgressBar(
            name="metrics-circle", value=0, size=28, line_width=2,
            start_angle=150, end_angle=390, style_classes="bat", child=self.ic)
        self.lv = Label(name="metrics-level", style_classes="bat", label="100%")
        self.rev = Revealer(name="metrics-bat-revealer", transition_duration=250,
                           transition_type="slide-left", child=self.lv)
        self.add(Box(name="metrics-bat-box", orientation="h", spacing=0, children=[self.cir, self.rev]))
        _sub(self._upd)
        self.connect('destroy', lambda *_: _unsub(self._upd))

    def _upd(self):
        p = _prov
        if not p:
            return
        val, chg, bt = p.bat_pct, p.bat_chg, p.bat_time
        td = abs(bt - self._lt)
        if val == self._lv and chg == self._lc and td < 60:
            return
        self._lv, self._lc, self._lt = val, chg, bt
        pct, low = int(val), val <= 30
        self.set_visible(val != 0)
        self.cir.set_value(val * 0.01)
        self.lv.set_label(f"{pct}%")
        if low:
            self.cir.set_style("border: 3px solid #ffa500;")
            self.ic.set_style("color: #ffa500;")
            self.cir.add_style_class("battery-low")
            self.cir.remove_style_class("battery-normal")
        else:
            self.cir.set_style("border: 3px solid var(--green);")
            self.ic.set_style("color: #d3d3d3;")
            self.cir.add_style_class("battery-normal")
            self.cir.remove_style_class("battery-low")
        t = f"{int(bt)}сек" if bt < 60 else (f"{int(bt / 60)}мин" if bt < 3600 else f"{int(bt / 3600)}ч")
        if pct == 100:
            self.ic.set_markup(icons.battery)
            tip = f"{icons.bat_full} Полностью заряжено" + ("" if chg else f" - осталось {t}")
        elif chg:
            self.ic.set_markup(icons.charging)
            tip = f"{icons.bat_charging} Заряжается - осталось {t}"
        elif low:
            self.ic.set_markup(icons.alert)
            tip = f"{icons.bat_low} Низкий заряд - осталось {t}"
        else:
            self.ic.set_markup(icons.discharging)
            tip = f"{icons.bat_discharging} Разряжается - осталось {t}"
        self.set_tooltip_markup(tip)
        if self._obc:
            self._obc(val, chg)


class Battery(Box):
    __slots__ = ('btn', 'pmb', 'pmr', 'bs', 'bb', 'bp', 'mode', 'htim',
                 'auto', 'manual', 'last_chg', 'is_low')

    def __init__(self, **kwargs):
        super().__init__(orientation="h", spacing=0, **kwargs)
        self.set_name("battery-container")
        self.auto, self.manual, self.last_chg, self.is_low = True, False, None, False
        self.mode, self.htim = "balanced", None
        self.bs = self.bb = self.bp = None
        self.pmb = Box(name="power-mode-switcher", orientation="h", spacing=2)
        self._init_pm()
        self.btn = BatteryButton(on_battery_changed=self._on_bat)
        self.pmr = Revealer(name="metrics-power-modes-revealer", transition_duration=250,
                           transition_type="slide-left", child=self.pmb, child_revealed=False)
        self.add(self.btn)
        self.add(self.pmr)
        for w in (self, self.btn, self.pmb):
            w.connect("enter-notify-event", self._ent)
            w.connect("leave-notify-event", self._lv)

    def _on_bat(self, lvl, chg):
        if not self.auto:
            return
        if self.last_chg is not chg:
            self.last_chg, self.manual = chg, False
        cur_low = lvl <= 30
        if self.is_low is not cur_low:
            self.is_low = cur_low
        if self.manual:
            return
        if chg:
            tgt = "performance" if self.bp else "balanced"
        elif cur_low:
            tgt = "power-saver" if self.bs else "balanced"
        else:
            tgt = "balanced"
        if tgt != self.mode:
            self._apply(tgt)

    def _init_pm(self):
        profiles = ""
        try:
            proc = Gio.Subprocess.new(['powerprofilesctl', 'list'],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
            _, profiles, _ = proc.communicate_utf8(None)
        except:
            pass
        try:
            proc = Gio.Subprocess.new(['powerprofilesctl', 'get'],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
            _, out, _ = proc.communicate_utf8(None)
            if out and (m := out.strip()) in ("power-saver", "balanced", "performance"):
                self.mode = m
        except:
            pass
        modes = (
            ("power-saver", "battery-save", icons.power_saving, "Энергосбережение"),
            ("balanced", "battery-balanced", icons.power_balanced, "Сбалансированный"),
            ("performance", "battery-performance", icons.power_performance, "Производительный"),
        )
        for mode, name, icon, tip in modes:
            if mode in profiles:
                btn = Button(name=name, child=Label(name=f"{name}-label", markup=icon), tooltip_text=tip)
                btn.connect("clicked", lambda _, m=mode: self._set(m))
                btn.connect("enter-notify-event", self._ent)
                btn.connect("leave-notify-event", self._lv)
                self.pmb.add(btn)
                if mode == "power-saver":
                    self.bs = btn
                elif mode == "balanced":
                    self.bb = btn
                else:
                    self.bp = btn
        self._upd_styles()

    def _ent(self, *_):
        if self.htim:
            GLib.source_remove(self.htim)
            self.htim = None
        try:
            proc = Gio.Subprocess.new(['powerprofilesctl', 'get'],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE)
            proc.communicate_utf8_async(None, None, self._mode_cb)
        except:
            pass
        self.btn.rev.set_reveal_child(True)
        self.pmr.set_reveal_child(True)
        return False

    def _mode_cb(self, proc, res):
        try:
            _, out, _ = proc.communicate_utf8_finish(res)
            if out and (m := out.strip()) in ("power-saver", "balanced", "performance") and self.mode != m:
                self.mode = m
                self._upd_styles()
        except:
            pass

    def _lv(self, *_):
        if self.htim:
            GLib.source_remove(self.htim)
        self.htim = GLib.timeout_add(300, self._hide)
        return False

    def _hide(self):
        self.btn.rev.set_reveal_child(False)
        self.pmr.set_reveal_child(False)
        self.htim = None
        return False

    def _set(self, mode):
        self.manual = True
        self._apply(mode)

    def _apply(self, mode):
        self.mode = mode
        self._upd_styles()
        exec_shell_command_async(f"powerprofilesctl set {mode}")

    def _upd_styles(self):
        for btn in (self.bs, self.bb, self.bp):
            if btn:
                btn.remove_style_class("active")
        tb = self.bs if self.mode == "power-saver" else (self.bb if self.mode == "balanced" else self.bp)
        if tb:
            tb.add_style_class("active")

    def set_auto_mode(self, en):
        self.auto = en
        if en:
            self.manual = False
            if _prov:
                self._on_bat(_prov.bat_pct, _prov.bat_chg)


class NetworkApplet(Button):
    __slots__ = ('nc', 'hov', 'dl', 'ul', 'wl', 'dlr', 'ulr')

    def __init__(self, **kwargs):
        super().__init__(name="button-bar", **kwargs)
        self.nc = NetworkClient()
        self.hov = False
        self.dl = Label(name="download-label", markup="0 B/s")
        self.ul = Label(name="upload-label", markup="0 B/s")
        self.wl = Label(name="network-icon-label", markup=icons.world_off)
        self.dlr = Revealer(
            child=Box(children=[Label(name="download-icon-label", markup=icons.download), self.dl]),
            transition_type="slide-right", child_revealed=False)
        self.ulr = Revealer(
            child=Box(children=[self.ul, Label(name="upload-icon-label", markup=icons.upload)]),
            transition_type="slide-left", child_revealed=False)
        self.add(Box(orientation="h", children=[self.ulr, self.wl, self.dlr]))
        self.connect("enter-notify-event", self._ent)
        self.connect("leave-notify-event", self._lv)
        _sub(self._upd)
        self.connect('destroy', lambda *_: _unsub(self._upd))

    def _ent(self, *_):
        self.hov = True
        self.dlr.set_reveal_child(True)
        self.ulr.set_reveal_child(True)

    def _lv(self, *_):
        self.hov = False
        self.dlr.set_reveal_child(False)
        self.ulr.set_reveal_child(False)

    def _upd(self):
        p = _prov
        if not p:
            return
        self.dl.set_markup(self._fmt(p.net_dl))
        self.ul.set_markup(self._fmt(p.net_ul))
        self._upd_wifi()

    def _upd_wifi(self):
        nc = self.nc
        ed = nc.ethernet_device
        if ed and ed.internet in ("activated", "activating"):
            self.wl.set_markup(icons.world)
            self.set_tooltip_text("Ethernet")
            return
        wd = nc.wifi_device
        if not wd or not getattr(wd, "enabled", False):
            self.wl.set_markup(icons.world_off)
            self.set_tooltip_text("Disconnected")
            return
        ssid = getattr(wd, "ssid", None)
        if not ssid or ssid in ("Disconnected", "Выключено", "Не подключено"):
            self.wl.set_markup(icons.world_off)
            self.set_tooltip_text("Disconnected")
            return
        s = wd.strength
        self.wl.set_markup(icons.wifi_3 if s >= 75 else icons.wifi_2 if s >= 50 else icons.wifi_1 if s >= 25 else icons.wifi_0)
        self.set_tooltip_text(ssid)

    @staticmethod
    def _fmt(sp):
        return f"{sp:.0f} B/s" if sp < 1024 else (f"{sp * 0.0009765625:.1f} KB/s" if sp < 1048576 else f"{sp * 9.5367431640625e-07:.1f} MB/s")