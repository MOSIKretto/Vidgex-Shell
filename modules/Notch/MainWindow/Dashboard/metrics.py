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


_WARMUP_SECONDS = 0.15

_TEMP_MIN = 40.0
_TEMP_MAX = 90.0

_RAGE_W_CPU = 0.30
_RAGE_W_MEM = 0.20
_RAGE_W_GPU = 0.20
_RAGE_W_TEMP = 0.15
_RAGE_W_DISK = 0.15

_RAGE_ALPHA_UP = 0.5
_RAGE_ALPHA_DOWN = 0.07

_RAGE_LEVELS = (
    (0.125, "happy"),
    (0.25, "smile"),
    (0.375, "empty"),
    (0.5, "annoyed"),
    (0.625, "sad"),
    (0.75, "sick"),
    (0.875, "angry"),
    (1.01, "dead"),
)


def _rage_icon(rage: float) -> str:
    for threshold, name in _RAGE_LEVELS:
        if rage < threshold:
            return getattr(icons, name)
    return icons.dead


class MetricsProvider:
    __slots__ = (
        'cpu', 'mem', 'disk', 'disk_busy', 'gpu', 'temp', 'rage',
        'gpus', '_nv', '_stop', '_intel_proc',
        '_hw_ready', '_ready_cbs', '_warmed',
        '_disk_io_prev_busy', '_disk_io_prev_ts',
    )

    def __init__(self):
        self.cpu = self.mem = self.temp = self.rage = self.disk_busy = 0.0
        self.disk = [0.0]
        self.gpu = []
        self.gpus = []
        self._nv = []
        self._warmed = False
        self._disk_io_prev_busy = None
        self._disk_io_prev_ts = None

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
            # Документированное поведение внешней утилиты: nvidia-smi может
            # отвечать с задержкой/ошибкой при переходе карты в
            # runtime-suspend или смене драйвера — это не повод падать.
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
        self._gather_metrics()
        GLib.idle_add(self._notify_ui)

        while not self._stop.wait(2.0):
            self._gather_metrics()
            GLib.idle_add(self._notify_ui)

        self._shutdown_intel_proc()

    def _shutdown_intel_proc(self) -> None:
        proc, self._intel_proc = self._intel_proc, None
        if proc is not None:
            proc.kill()
            proc.wait()

    def _gather_metrics(self) -> None:
        interval = None if self._warmed else _WARMUP_SECONDS
        self.cpu = psutil.cpu_percent(interval=interval)
        self._warmed = True

        self.mem = psutil.virtual_memory().percent
        self.disk[0] = psutil.disk_usage('/').percent

        max_t = 0.0
        try:
            sensors = psutil.sensors_temperatures()
        except NotImplementedError:
            sensors = {}
        for entries in sensors.values():
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
                    # sysfs-узел может на мгновение исчезнуть при переходе
                    # дискретной AMD-карты в runtime-suspend.
                    pass

        if self._nv:
            self._poll_nvidia()

        self._update_disk_busy()
        self._compute_rage()

    def _poll_nvidia(self) -> None:
        out = self._query_nvidia(
            ['--query-gpu=utilization.gpu', '--format=csv,noheader,nounits']
        )
        if not out:
            return
        vals = [float(x) for x in out.split() if x]
        for i, v in zip(self._nv, vals):
            self.gpu[i] = max(0.0, min(100.0, v))

    def _update_disk_busy(self) -> None:
        io = psutil.disk_io_counters()
        if io is None:
            return

        now = time.monotonic()
        busy_ms = io.read_time + io.write_time
        if self._disk_io_prev_ts is not None:
            dt_ms = (now - self._disk_io_prev_ts) * 1000.0
            if dt_ms > 0:
                delta = busy_ms - self._disk_io_prev_busy
                self.disk_busy = max(0.0, min(100.0, delta / dt_ms * 100.0))
        self._disk_io_prev_busy = busy_ms
        self._disk_io_prev_ts = now

    def _compute_rage(self) -> None:
        cpu_n = self.cpu / 100.0
        mem_n = self.mem / 100.0
        gpu_n = (sum(self.gpu) / len(self.gpu) / 100.0) if self.gpu else 0.0
        temp_n = max(0.0, min((self.temp - _TEMP_MIN) / (_TEMP_MAX - _TEMP_MIN), 1.0))
        disk_n = self.disk_busy / 100.0

        stress = (
            _RAGE_W_CPU * cpu_n
            + _RAGE_W_MEM * mem_n
            + _RAGE_W_GPU * gpu_n
            + _RAGE_W_TEMP * temp_n
            + _RAGE_W_DISK * disk_n
        )
        overheat_penalty = max(0.0, temp_n - 0.8) * 2.0
        stress = min(1.0, stress + overheat_penalty)

        alpha = _RAGE_ALPHA_UP if stress > self.rage else _RAGE_ALPHA_DOWN
        self.rage += (stress - self.rage) * alpha

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


class SingularMetric:
    __slots__ = (
        'usage', 'label', 'box', '_last_v', '_last_suffix',
        '_tip_base', '_name', '_icon',
    )

    def __init__(self, id, name, icon, css_class=None):
        self._last_v = -1
        self._last_suffix = None
        self._name = name
        self._icon = icon
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
        if css_class is not None:
            self.usage.get_style_context().add_class(f'{css_class}-usage')
            self.label.get_style_context().add_class(f'{css_class}-label')
            self.box.get_style_context().add_class(f'{css_class}-box')
        self.box.set_tooltip_markup(self._tip_base)
        self.usage.set_sensitive(False)

    def set_icon(self, icon: str) -> None:
        if icon == self._icon:
            return
        self._icon = icon
        self.label.set_markup(icon)
        self._tip_base = f'{icon} {self._name}'
        self._last_suffix = None

    def set_val(self, v, tip_suffix=None):
        if abs(self._last_v - v) > 0.005:
            self.usage.set_value(v)
            self._last_v = v
        if tip_suffix is not None and tip_suffix != self._last_suffix:
            self._last_suffix = tip_suffix
            self.box.set_tooltip_markup(f'{self._tip_base}   <b>{tip_suffix}</b>')


class Metrics(Box):
    def __init__(self, widgets=None, **kwargs):
        box_opts = dict(
            name='metrics', spacing=8, h_align='center',
            v_align='fill', visible=True, all_visible=True,
        )
        box_opts.update(kwargs)
        super().__init__(**box_opts)

        self._destroyed = False
        self._gpu_built = False

        self.rage = SingularMetric('rage', 'RAGE', icons.happy)
        self.temp = SingularMetric('temp', 'TEMP', icons.temp)
        self.disk = [SingularMetric('disk', 'DISK', icons.disk)]
        self.ram  = SingularMetric('ram',  'RAM',  icons.memory)
        self.cpu  = SingularMetric('cpu',  'CPU',  icons.cpu)
        self.gpu = []

        for m in (self.rage, self.temp, *self.disk, self.ram, self.cpu):
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
        for i, g in enumerate(prov.get_gpu_info()):
            sm = SingularMetric(f'gpu{i}', g['name'], icons.gpu, css_class='gpu')
            self.gpu.append(sm)
            self.add(sm.box)
            sm.box.show_all()

    def _upd(self):
        prov = _prov

        self.rage.set_icon(_rage_icon(prov.rage))
        self.rage.set_val(min(prov.rage, 1.0), f'{int(prov.rage * 100 + 0.5)}%')
        self.temp.set_val(min(prov.temp / 120.0, 1.0), f'{int(prov.temp + 0.5)}°C')
        self.ram.set_val(prov.mem * 0.01, f'{int(prov.mem)}%')
        self.cpu.set_val(prov.cpu * 0.01, f'{int(prov.cpu)}%')

        for d, v in zip(self.disk, prov.disk):
            d.set_val(v * 0.01, f'{int(v)}%')

        for g, v in zip(self.gpu, prov.gpu):
            g.set_val(v * 0.01, f'{int(v)}%')