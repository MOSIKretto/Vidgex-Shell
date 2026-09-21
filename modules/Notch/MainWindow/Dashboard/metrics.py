import os
import threading
import time
import weakref
import subprocess
import psutil

from fabric.widgets.box import Box
from fabric.widgets.label import Label
from fabric.widgets.scale import Scale
from gi.repository import GLib

from modules.Notch.MainWindow.Dashboard.Network.network import NetworkClient
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
        'net_dl', 'net_ul',
        '_nr', '_ns', '_nt',
        'gpus', '_run'
    )

    def __init__(self):
        self.cpu    = self.mem = self.temp = 0.0
        self.net_dl = self.net_ul = 0.0
        self.disk   = [0.0]
        self.gpu    = []
        self.gpus   = []

        self._nr = self._ns = 0
        self._nt = time.monotonic()

        try:
            net = psutil.net_io_counters()
            self._nr, self._ns = net.bytes_recv, net.bytes_sent
        except Exception:
            pass

        self._detect_hw()

        self._run = True
        threading.Thread(target=self._worker, daemon=True).start()

    def _detect_hw(self):
        for i in range(8):
            amd_path = f'/sys/class/drm/card{i}/device/gpu_busy_percent'
            if os.path.exists(amd_path):
                self.gpus.append({'type': 'amd', 'name': 'AMD', 'path': amd_path})
                self.gpu.append(0.0)

        try:
            if subprocess.run(
                ['nvidia-smi'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            ).returncode == 0:
                out = subprocess.check_output(
                    ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                    text=True
                )
                for line in out.strip().split('\n'):
                    if line:
                        self.gpus.append({'type': 'nvidia', 'name': 'NVIDIA'})
                        self.gpu.append(0.0)
        except Exception:
            pass

        if os.system('which intel_gpu_top >/dev/null 2>&1') == 0:
            self.gpus.append({'type': 'intel', 'name': 'INTEL'})
            self.gpu.append(0.0)
            idx = len(self.gpu) - 1
            threading.Thread(target=self._intel_gpu_reader, args=(idx,), daemon=True).start()

    def _intel_gpu_reader(self, idx):
        try:
            proc = subprocess.Popen(
                ['intel_gpu_top', '-J', '-s', '2000'],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True
            )
            in_render = False
            for line in proc.stdout:
                if not self._run:
                    break
                if '"Render/3D"' in line or '"Render/3D/0"' in line:
                    in_render = True
                elif in_render and '"busy"' in line:
                    try:
                        val = float(line.split(':')[1].replace(',', '').strip())
                        self.gpu[idx] = max(0.0, min(100.0, val))
                    except Exception:
                        pass
                    in_render = False
        except Exception:
            pass

    def _worker(self):
        psutil.cpu_percent(interval=None)
        while self._run:
            if not _subs:
                GLib.idle_add(self.cleanup)
                break
            self._gather_metrics()
            GLib.idle_add(self._notify_ui)
            time.sleep(2.0)

    def _gather_metrics(self):
        try:
            self.cpu = psutil.cpu_percent(interval=None)
        except Exception:
            pass

        try:
            self.mem = psutil.virtual_memory().percent
        except Exception:
            pass

        try:
            self.disk[0] = psutil.disk_usage('/').percent
        except Exception:
            pass

        try:
            temps = psutil.sensors_temperatures()
            max_t = 0.0
            for name, entries in temps.items():
                for entry in entries:
                    if entry.current > max_t:
                        max_t = entry.current
            self.temp = max_t
        except Exception:
            pass

        now = time.monotonic()
        dt  = now - self._nt
        if dt > 0:
            try:
                net         = psutil.net_io_counters()
                self.net_dl = (net.bytes_recv - self._nr) / dt
                self.net_ul = (net.bytes_sent - self._ns) / dt
                self._nr, self._ns, self._nt = net.bytes_recv, net.bytes_sent, now
            except Exception:
                pass

        nv_idx = 0
        for i, gpu in enumerate(self.gpus):
            if gpu['type'] == 'amd':
                try:
                    with open(gpu['path'], 'r') as f:
                        self.gpu[i] = max(0.0, min(100.0, float(f.read().strip())))
                except Exception:
                    pass
            elif gpu['type'] == 'nvidia':
                try:
                    out = subprocess.check_output(
                        ['nvidia-smi', '--query-gpu=utilization.gpu', '--format=csv,noheader,nounits'],
                        text=True
                    )
                    vals = [float(x.strip()) for x in out.strip().split('\n') if x.strip()]
                    if nv_idx < len(vals):
                        self.gpu[i] = max(0.0, min(100.0, vals[nv_idx]))
                    nv_idx += 1
                except Exception:
                    pass

    def _notify_ui(self):
        for widget in _subs:
            if hasattr(widget, '_upd'):
                try:
                    widget._upd()
                except Exception:
                    pass
        return False

    def get_gpu_info(self):
        return self.gpus

    def cleanup(self):
        self._run = False
        self.gpu  = []
        self.disk = []
        self.gpus = []
        global _prov
        _prov = None


class SingularMetric:
    __slots__ = ('usage', 'label', 'box', '_last_v', '_tip_base')

    def __init__(self, id, name, icon):
        self._last_v   = -1
        self._tip_base = f'{icon} {name}'
        self.usage = Scale(
            name=f'{id}-usage', value=0.25, orientation='v',
            inverted=True, v_align='fill', v_expand=True
        )
        self.label = Label(name=f'{id}-label', markup=icon)
        self.box   = Box(
            name=f'{id}-box', orientation='v', spacing=8,
            children=[self.usage, self.label]
        )
        self.box.set_tooltip_markup(self._tip_base)
        self.usage.set_sensitive(False)

    def set_val(self, v, tip_suffix=None):
        if abs(self._last_v - v) > 0.005:
            self.usage.set_value(v)
            self._last_v = v
        if tip_suffix is not None:
            self.box.set_tooltip_markup(f'{self._tip_base}   <b>{tip_suffix}</b>')


class Metrics(Box):
    __slots__ = ('nc', 'net', 'temp', 'disk', 'ram', 'cpu', 'gpu')

    def __init__(self, **kwargs):
        super().__init__(
            name='metrics', spacing=8, h_align='center',
            v_align='fill', visible=True, all_visible=True
        )
        _sub(self)

        self.nc   = NetworkClient()
        self.net  = SingularMetric('net',  'NET',  icons.world_off)
        self.temp = SingularMetric('temp', 'TEMP', icons.temp)
        self.disk = [SingularMetric('disk', 'DISK', icons.disk)]
        self.ram  = SingularMetric('ram',  'RAM',  icons.memory)
        self.cpu  = SingularMetric('cpu',  'CPU',  icons.cpu)
        self.gpu  = [
            SingularMetric('gpu', g.get('name', 'GPU'), icons.gpu)
            for g in (_prov.get_gpu_info() if _prov else [])
        ]

        for m in (self.net, self.temp,) + tuple(self.disk) + (self.ram, self.cpu) + tuple(self.gpu):
            self.add(m.box)

    def _upd(self):
        if not _prov:
            return

        ed, wd    = self.nc.ethernet_device, self.nc.wifi_device
        net_val   = 0.0
        net_icon  = icons.world_off
        ssid_name = "Disconnected"
        net_pct   = 0

        if ed and ed.internet in ('activated', 'activating'):
            net_icon  = icons.world
            net_val   = 1.0
            ssid_name = "Ethernet"
            net_pct   = 100
        elif wd and getattr(wd, 'enabled', False):
            ssid = getattr(wd, 'ssid', None)
            if ssid and ssid not in ('Disconnected', 'Выключено', 'Не подключено'):
                s         = wd.strength
                net_icon  = (
                    icons.wifi_3 if s >= 75 else icons.wifi_2 if s >= 50 else
                    icons.wifi_1 if s >= 25 else icons.wifi_0
                )
                net_val   = s * 0.01
                ssid_name = ssid
                net_pct   = int(s)

        self.net.label.set_markup(net_icon)
        self.net.box.set_tooltip_markup(f'{net_icon} {ssid_name}   <b>{net_pct}%</b>')
        self.net.set_val(net_val)

        self.temp.set_val(
            min(_prov.temp / 120.0, 1.0),
            f'{int(_prov.temp + 0.5)}°C'
        )
        self.ram.set_val(_prov.mem * 0.01, f'{int(_prov.mem)}%')
        self.cpu.set_val(_prov.cpu * 0.01, f'{int(_prov.cpu)}%')

        for i, d in enumerate(self.disk):
            if i < len(_prov.disk):
                d.set_val(_prov.disk[i] * 0.01, f'{int(_prov.disk[i])}%')

        for i, g in enumerate(self.gpu):
            if i < len(_prov.gpu):
                g.set_val(_prov.gpu[i] * 0.01, f'{int(_prov.gpu[i])}%')