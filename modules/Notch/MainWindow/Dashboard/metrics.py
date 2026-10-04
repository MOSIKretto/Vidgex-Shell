import os
import shutil
import subprocess
import threading
import time
import weakref
import psutil

from fabric.widgets.box import Box
from fabric.widgets.label import Label
from fabric.widgets.scale import Scale

from gi.repository import GLib

import services.icons as icons


_prov: "MetricsProvider | None" = None
_subs: "weakref.WeakSet" = weakref.WeakSet()


def _sub(widget) -> "MetricsProvider":
    global _prov
    _subs.add(widget)
    if _prov is None:
        _prov = MetricsProvider()
    return _prov

def _unsub(widget) -> None:
    global _prov
    _subs.discard(widget)
    if not _subs and _prov is not None:
        prov, _prov = _prov, None
        prov.cleanup()

def _fmt_speed(bps: float) -> str:
    bits = bps * 8.0
    if bits >= 1_000_000_000:
        return f"{bits / 1_000_000_000:.1f} Gbit/s"
    if bits >= 1_000_000:
        return f"{bits / 1_000_000:.1f} Mbit/s"
    if bits >= 1_000:
        return f"{bits / 1_000:.0f} Kbit/s"
    return f"{bits:.0f} bit/s"


_NET_MAX = 2_500_000.0
_WARMUP_SECONDS = 0.15


def _net_norm(bps: float) -> float:
    if bps <= 0:
        return 0.0
    return min(bps / _NET_MAX, 1.0)


class MetricsProvider:
    __slots__ = (
        'cpu', 'mem', 'disk', 'gpu', 'temp',
        'net_dl', 'net_ul',
        '_nr', '_ns', '_nt',
        'gpus', '_nv', '_stop', '_intel_proc',
        '_hw_ready', '_ready_cbs',
    )

    def __init__(self):
        self.cpu = self.mem = self.temp = 0.0
        self.net_dl = self.net_ul = 0.0
        self.disk = [0.0]
        self.gpu = []
        self.gpus = []
        self._nv = []
        self._nr = self._ns = 0
        self._nt = time.monotonic()

        self._stop = threading.Event()
        self._intel_proc = None
        self._hw_ready = False
        self._ready_cbs = []

        threading.Thread(target=self._worker, daemon=True).start()

    def on_hw_ready(self, callback) -> None:
        if self._hw_ready:
            callback()
        else:
            self._ready_cbs.append(callback)

    def _hw_detected(self) -> bool:
        self._hw_ready = True
        callbacks, self._ready_cbs = self._ready_cbs, []
        for cb in callbacks:
            cb()
        return False

    def _detect_hw(self) -> None:
        for i in range(8):
            amd_path = f'/sys/class/drm/card{i}/device/gpu_busy_percent'
            if os.path.exists(amd_path):
                self.gpus.append({'type': 'amd', 'name': 'AMD', 'path': amd_path})
                self.gpu.append(0.0)

        if shutil.which('nvidia-smi'):
            out = self._query_nvidia(['--query-gpu=name', '--format=csv,noheader'])
            for line in out.splitlines():
                if line.strip():
                    self._nv.append(len(self.gpus))
                    self.gpus.append({'type': 'nvidia', 'name': 'NVIDIA'})
                    self.gpu.append(0.0)

        if shutil.which('intel_gpu_top'):
            self.gpus.append({'type': 'intel', 'name': 'INTEL'})
            self.gpu.append(0.0)
            idx = len(self.gpu) - 1
            self._intel_proc = subprocess.Popen(
                ['intel_gpu_top', '-J', '-s', '2000'],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            threading.Thread(
                target=self._intel_gpu_reader,
                args=(idx, self._intel_proc.stdout),
                daemon=True,
            ).start()

    @staticmethod
    def _query_nvidia(args: list) -> str:
        try:
            return subprocess.check_output(['nvidia-smi', *args], text=True, timeout=2)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return ''

    def _intel_gpu_reader(self, idx: int, stdout) -> None:
        in_render = False
        with stdout:
            for line in stdout:
                if '"Render/3D"' in line or '"Render/3D/0"' in line:
                    in_render = True
                elif in_render and '"busy"' in line:
                    try:
                        val = float(line.split(':')[1].replace(',', '').strip())
                    except (ValueError, IndexError):
                        in_render = False
                        continue
                    self.gpu[idx] = max(0.0, min(100.0, val))
                    in_render = False

    def _worker(self) -> None:
        self._detect_hw()
        GLib.idle_add(self._hw_detected)
        self._gather_metrics(first=True)
        GLib.idle_add(self._notify_ui)

        while not self._stop.wait(2.0):
            self._gather_metrics()
            GLib.idle_add(self._notify_ui)

    def _gather_metrics(self, first: bool = False) -> None:
        if first:
            net_before = psutil.net_io_counters()
            self.cpu = psutil.cpu_percent(interval=_WARMUP_SECONDS)
            net_after = psutil.net_io_counters()
            self.net_dl = max(0.0, (net_after.bytes_recv - net_before.bytes_recv) / _WARMUP_SECONDS)
            self.net_ul = max(0.0, (net_after.bytes_sent - net_before.bytes_sent) / _WARMUP_SECONDS)
            self._nr, self._ns, self._nt = net_after.bytes_recv, net_after.bytes_sent, time.monotonic()
        else:
            self.cpu = psutil.cpu_percent(interval=None)
            now = time.monotonic()
            dt = now - self._nt
            if dt > 0:
                net = psutil.net_io_counters()
                self.net_dl = max(0.0, (net.bytes_recv - self._nr) / dt)
                self.net_ul = max(0.0, (net.bytes_sent - self._ns) / dt)
                self._nr, self._ns, self._nt = net.bytes_recv, net.bytes_sent, now

        self.mem = psutil.virtual_memory().percent
        self.disk[0] = psutil.disk_usage('/').percent

        max_t = 0.0
        for entries in psutil.sensors_temperatures().values():
            for entry in entries:
                if entry.current > max_t:
                    max_t = entry.current
        self.temp = max_t

        for i, gpu in enumerate(self.gpus):
            if gpu['type'] == 'amd':
                try:
                    with open(gpu['path'], 'r') as f:
                        self.gpu[i] = max(0.0, min(100.0, float(f.read().strip())))
                except OSError:
                    pass

        if self._nv:
            self._poll_nvidia()

    def _poll_nvidia(self) -> None:
        out = self._query_nvidia(
            ['--query-gpu=utilization.gpu', '--format=csv,noheader,nounits']
        )
        if not out:
            return
        vals = [float(x) for x in out.split() if x]
        for i, v in zip(self._nv, vals):
            self.gpu[i] = max(0.0, min(100.0, v))

    def _notify_ui(self) -> bool:
        if self._stop.is_set():
            return False
        for widget in _subs:
            widget._upd()
        return False

    def get_gpu_info(self):
        return self.gpus

    def cleanup(self) -> None:
        self._stop.set()
        proc, self._intel_proc = self._intel_proc, None
        if proc is not None:
            proc.kill()
            proc.wait()


class SingularMetric:
    __slots__ = ('usage', 'label', 'box', '_last_v', '_last_suffix', '_tip_base')

    def __init__(self, id, name, icon):
        self._last_v = -1
        self._last_suffix = None
        self._tip_base = f'{icon} {name}'
        self.usage = Scale(
            name=f'{id}-usage', value=0.25, orientation='v',
            inverted=True, v_align='fill', v_expand=True,
        )
        self.label = Label(name=f'{id}-label', markup=icon)
        self.box = Box(
            name=f'{id}-box', orientation='v', spacing=8,
            children=[self.usage, self.label],
        )
        self.box.set_tooltip_markup(self._tip_base)
        self.usage.set_sensitive(False)

    def set_val(self, v, tip_suffix=None):
        if abs(self._last_v - v) > 0.005:
            self.usage.set_value(v)
            self._last_v = v
        if tip_suffix is not None and tip_suffix != self._last_suffix:
            self._last_suffix = tip_suffix
            self.box.set_tooltip_markup(f'{self._tip_base}   <b>{tip_suffix}</b>')


class Metrics(Box):
    def __init__(self, widgets=None, **kwargs):
        super().__init__(
            name='metrics', spacing=8, h_align='center',
            v_align='fill', visible=True, all_visible=True,
        )
        self._destroyed = False
        self._gpu_built = False

        self.net  = SingularMetric('net',  'NET',  icons.world)
        self.temp = SingularMetric('temp', 'TEMP', icons.temp)
        self.disk = [SingularMetric('disk', 'DISK', icons.disk)]
        self.ram  = SingularMetric('ram',  'RAM',  icons.memory)
        self.cpu  = SingularMetric('cpu',  'CPU',  icons.cpu)
        self.gpu = []

        for m in (self.net, self.temp, *self.disk, self.ram, self.cpu):
            self.add(m.box)

        self.connect("destroy", self._on_destroy)
        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)

    def _on_map(self, *_args) -> None:
        prov = _sub(self)
        if not self._gpu_built:
            prov.on_hw_ready(lambda: self._build_gpu_widgets(prov))

    def _on_unmap(self, *_args) -> None:
        _unsub(self)

    def _on_destroy(self, *_args) -> None:
        self._destroyed = True
        _unsub(self)

    def _build_gpu_widgets(self, prov: "MetricsProvider") -> None:
        if self._destroyed or self._gpu_built:
            return
        self._gpu_built = True
        for g in prov.get_gpu_info():
            sm = SingularMetric('gpu', g['name'], icons.gpu)
            self.gpu.append(sm)
            self.add(sm.box)
            sm.box.show_all()

    def _upd(self):
        prov = _prov

        total = prov.net_dl + prov.net_ul
        self.net.set_val(_net_norm(total), _fmt_speed(total))

        self.temp.set_val(min(prov.temp / 120.0, 1.0), f'{int(prov.temp + 0.5)}°C')
        self.ram.set_val(prov.mem * 0.01, f'{int(prov.mem)}%')
        self.cpu.set_val(prov.cpu * 0.01, f'{int(prov.cpu)}%')

        for d, v in zip(self.disk, prov.disk):
            d.set_val(v * 0.01, f'{int(v)}%')

        for g, v in zip(self.gpu, prov.gpu):
            g.set_val(v * 0.01, f'{int(v)}%')