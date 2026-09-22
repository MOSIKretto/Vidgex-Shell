import json
import random
import subprocess
import time as _time
from gi.repository import Gst, GLib, Gtk

from modules.Notch.MainWindow.Dashboard.Controls.volume import Volume

_NUM_BARS = 80
_FFT_BINS = 512
_FPS_MS = 20  # ~50 fps — не менять

_CLR_CYAN = (0.0, 0.95, 1.0, 0.85)
_CLR_RED = (1.0, 0.05, 0.35, 0.85)

_MONITOR_CACHE_TTL = 30.0
_monitor_cache = {"name": None, "ts": 0.0}


def _detect_system_monitor():
    """Находит монитор вывода звука (Sink Monitor) через доступные утилиты."""
    try:
        sink = subprocess.check_output(
            ["pactl", "get-default-sink"], stderr=subprocess.DEVNULL, text=True, timeout=0.15
        ).strip()
        if sink and not sink.startswith("@"):
            return f"{sink}.monitor"
    except Exception: pass

    try:
        out = subprocess.check_output(["pactl", "info"], stderr=subprocess.DEVNULL, text=True, timeout=0.15)
        for line in out.splitlines():
            if "Default Sink:" in line:
                s = line.split(":", 1)[1].strip()
                if s and not s.startswith("@"): return f"{s}.monitor"
    except Exception: pass

    try:
        out = subprocess.check_output(["pw-dump", "Node"], stderr=subprocess.DEVNULL, text=True, timeout=0.2)
        for n in json.loads(out):
            props = n.get("info", {}).get("props", {})
            if props.get("media.class") == "Audio/Sink":
                name = props.get("node.name")
                if name: return f"{name}.monitor"
    except Exception: pass

    try:
        out = subprocess.check_output(["pactl", "list", "short", "sources"],
                                       stderr=subprocess.DEVNULL, text=True, timeout=0.15)
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1].endswith(".monitor"): return parts[1]
    except Exception: pass

    return None


def _get_system_monitor():
    """
    Кэшированная версия детекта монитора звука.
    Раньше пересоздавалась при КАЖДОМ запуске GStreamer-пайплайна анализатора
    (до 4 вызовов внешних утилит каждый раз). Теперь результат живёт 30 секунд.
    """
    now = _time.monotonic()
    if _monitor_cache["name"] is not None and now - _monitor_cache["ts"] < _MONITOR_CACHE_TTL:
        return _monitor_cache["name"]
    name = _detect_system_monitor()
    _monitor_cache["name"], _monitor_cache["ts"] = name, now
    return name


def _read_wpctl_volume():
    try:
        out = subprocess.check_output(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
                                       stderr=subprocess.DEVNULL, text=True, timeout=0.1).strip()
        if "[MUTED]" in out: return 0.0
        for p in out.split()[1:]:
            try: return max(0.0, min(1.0, float(p)))
            except ValueError: continue
    except Exception: pass
    return 1.0


class _SystemVolumePoller:
    """
    Единый на весь процесс опрос системной громкости через wpctl.
    Раньше каждый GlitchVisualizer сам спавнил subprocess каждые 150мс —
    при нескольких вкладках плеера это было десятки fork/exec в секунду
    просто в фоне. Теперь это один процесс на всё приложение.
    """
    _inst = None

    def __init__(self):
        self.value = _read_wpctl_volume()
        self._subs = []
        GLib.timeout_add(400, self._poll)

    @classmethod
    def instance(cls):
        if cls._inst is None:
            cls._inst = cls()
        return cls._inst

    def subscribe(self, cb): self._subs.append(cb)

    def unsubscribe(self, cb):
        try: self._subs.remove(cb)
        except ValueError: pass

    def _poll(self):
        self.value = _read_wpctl_volume()
        for cb in list(self._subs): cb()
        return True


def _freq_for_u(u):
    if u <= 0.40: return 20.0 * (16.0 ** (u / 0.40))
    if u <= 0.60: return 320.0 * ((3500.0 / 320.0) ** ((u - 0.40) / 0.20))
    return 3500.0 * ((16500.0 / 3500.0) ** ((u - 0.60) / 0.40))


def _smooth_and_tilt(vals, n):
    out = [0.0] * n
    for i in range(n):
        prev = vals[i - 1] if i > 0 else vals[i]
        nxt = vals[i + 1] if i < n - 1 else vals[i]
        u = i / float(n - 1)
        out[i] = (vals[i] * 0.52 + (prev + nxt) * 0.24) * (1.0 + (u ** 1.3) * 2.2)
    return out


class AudioSpectrum:
    """Индивидуальный анализатор спектра для каждого плеера."""

    def __init__(self, player=None, bands=_NUM_BARS):
        self.player = player
        self.bands = bands
        self._half = bands // 2
        self._raw_mags_l = [-80.0] * _FFT_BINS
        self._raw_mags_r = [-80.0] * _FFT_BINS
        self._peak = 0.35
        self._pipeline = None

        nyquist = 22050.0
        self._band_centers = [
            _freq_for_u(i / float(self._half - 1)) / nyquist * (_FFT_BINS - 1)
            for i in range(self._half)
        ]

    def is_local_player(self):
        return (hasattr(self.player, "_raw_mags_l")
                or getattr(self.player, "player_name", "") == "Library"
                or "local" in str(type(self.player)).lower())

    def is_playing(self):
        if not self.player: return False
        if getattr(self.player, '_dead', False) or getattr(self.player, 'is_dead', False): return False
        return str(getattr(self.player, 'playback_status', '')).strip().lower() == 'playing'

    def ensure_pipeline(self):
        if self.is_local_player():
            if self.is_playing():
                self._raw_mags_l = getattr(self.player, "_raw_mags_l", [-80.0] * _FFT_BINS)
                self._raw_mags_r = getattr(self.player, "_raw_mags_r", [-80.0] * _FFT_BINS)
            else:
                self._raw_mags_l = self._raw_mags_r = [-80.0] * _FFT_BINS
            return

        if not self.is_playing():
            self._stop_pipeline()
            self._raw_mags_l = self._raw_mags_r = [-80.0] * _FFT_BINS
            return

        if not self._pipeline: self._start_pipeline()

    def _start_pipeline(self):
        self._stop_pipeline()
        mon = _get_system_monitor()
        src_str = f'pulsesrc device="{mon}"' if mon else 'pulsesrc'
        pipe_str = (f'{src_str} ! audioconvert ! audioresample ! '
                    f'audio/x-raw,channels=2,rate=44100 ! '
                    f'spectrum bands={_FFT_BINS} threshold=-80 interval=20000000 ! fakesink sync=false')
        try:
            pipe = Gst.parse_launch(pipe_str)
            bus = pipe.get_bus()
            bus.add_signal_watch()
            bus.connect("message::element", self._on_bus_message)
            bus.connect("message::error", self._on_bus_error)
            pipe.set_state(Gst.State.PLAYING)
            self._pipeline = pipe
        except Exception:
            self._pipeline = None

    def _stop_pipeline(self):
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None

    def _on_bus_error(self, _bus, _msg): self._stop_pipeline()

    def _on_bus_message(self, _bus, msg):
        st = msg.get_structure()
        if not (st and st.get_name() == "spectrum"): return
        raw = st.get_value("magnitude")
        if not raw: return
        try:
            if isinstance(raw[0], (int, float)):
                self._raw_mags_l = self._raw_mags_r = [float(x) for x in raw]
            else:
                self._raw_mags_l = [float(x) for x in raw[0]]
                self._raw_mags_r = [float(x) for x in raw[1]] if len(raw) > 1 else self._raw_mags_l
        except Exception: pass

    def _interpolate_mag(self, mags, bin_idx):
        if not mags: return 0.0
        idx0 = min(len(mags) - 1, int(bin_idx))
        idx1 = min(len(mags) - 1, idx0 + 1)
        frac = bin_idx - idx0
        v = mags[idx0] * (1.0 - frac) + mags[idx1] * frac
        return max(0.0, min(1.0, (v + 72.0) / 58.0))

    def get_spectrum(self):
        self.ensure_pipeline()
        if not self.is_playing(): return [0.0] * self.bands

        left = [self._interpolate_mag(self._raw_mags_l, b) for b in self._band_centers]
        right = [self._interpolate_mag(self._raw_mags_r, b) for b in self._band_centers]
        bars_raw = _smooth_and_tilt(left, self._half) + _smooth_and_tilt(right, self._half)[::-1]

        cur_max = max(bars_raw) if bars_raw else 0.0
        self._peak = cur_max if cur_max > self._peak else max(0.20, self._peak * 0.985)
        gain = 1.0 / self._peak
        return [max(0.0, min(1.0, v * gain)) for v in bars_raw]

    def cleanup(self): self._stop_pipeline()


class GlitchVisualizer(Gtk.DrawingArea):
    """
    Фоновый визуализатор музыки, строго привязанный к громкости своего трека.
    Активен, только если реально играет привязанный к нему плеер.
    """

    def __init__(self, player=None, local_player=None, bands=_NUM_BARS):
        super().__init__()
        self.player = player
        self.local_player = local_player
        self.bands = bands
        self.set_halign(Gtk.Align.FILL); self.set_valign(Gtk.Align.FILL)
        self.set_hexpand(True); self.set_vexpand(True)
        self.set_size_request(-1, 200)

        self.set_name("glitch-visualizer")
        self.get_style_context().add_class("glitch-visualizer")
        self._inject_css()
        self.show()

        self.analyzer = AudioSpectrum(player=player, bands=bands)
        self.bars = [0.0] * bands
        self.peaks = [0.0] * bands
        self.peak_fall = [0.0] * bands

        self._active = False
        self._glitch_intensity = self._tear_y = self._tear_h = self._tear_shift = 0.0

        self._vol_target = self._vol_current = 1.0
        self._vol_svc = self._vol_sig_id = None
        self._poller = None
        self._init_volume_tracker()

        self.connect("draw", self._on_draw)
        self._timer_id = None

    def _inject_css(self):
        try:
            provider = Gtk.CssProvider()
            provider.load_from_data(b"""
            #glitch-visualizer, .glitch-visualizer {
                background-color: transparent;
                color: var(--primary);
            }
            """)
            self.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 50)
        except Exception: pass

    def _init_volume_tracker(self):
        if Volume is not None:
            try:
                self._vol_svc = Volume.get_initial()
                self._vol_sig_id = self._vol_svc.connect("changed", self._on_vol_changed)
                self._update_volume()
                return
            except Exception: pass

        # Fallback: единый на всё приложение поллер wpctl вместо
        # собственного subprocess-таймера на каждый визуализатор.
        self._poller = _SystemVolumePoller.instance()
        self._poller.subscribe(self._on_vol_changed)
        self._update_volume()

    def _on_vol_changed(self, *_): self._update_volume()

    @staticmethod
    def _norm(v, default=1.0):
        if v is None: return default
        try:
            v = float(v)
            return max(0.0, min(1.0, v / 100.0 if v > 1.0 else v))
        except Exception:
            return default

    def _update_volume(self):
        p_vol = self._norm(getattr(self.player, "volume", None)) if self.player else 1.0

        if self._vol_svc:
            s_vol = 0.0 if getattr(self._vol_svc, "muted", False) else self._norm(getattr(self._vol_svc, "volume", 100))
        elif self._poller:
            s_vol = self._poller.value
        else:
            s_vol = 1.0

        self._vol_target = max(0.0, min(1.0, p_vol * s_vol))
        if self._vol_target > 0.001 and self.is_source_playing():
            self._ensure_timer()

    def _ensure_timer(self):
        if self._timer_id is None:
            self._timer_id = GLib.timeout_add(_FPS_MS, self._tick)

    def is_source_playing(self):
        """Играет ли конкретный плеер, к которому привязан визуализатор."""
        if not self.player: return False
        if getattr(self.player, '_dead', False) or getattr(self.player, 'is_dead', False): return False
        return str(getattr(self.player, 'playback_status', '')).strip().lower() == 'playing'

    def set_active(self, active: bool):
        active = bool(active)
        if self._active == active: return
        self._active = active
        if active: self._ensure_timer()

    def _decay_bars(self):
        has_energy = False
        for i in range(self.bands):
            self.bars[i] = max(0.0, self.bars[i] * 0.65 - 0.05)
            self.peaks[i] = max(0.0, self.peaks[i] * 0.65 - 0.05)
            if self.bars[i] > 0.001 or self.peaks[i] > 0.001: has_energy = True
        self._glitch_intensity = 0.0
        return has_energy

    def _tick(self):
        self._vol_current += (self._vol_target - self._vol_current) * 0.25
        if abs(self._vol_current - self._vol_target) < 0.005: self._vol_current = self._vol_target

        playing = self.is_source_playing() and self._active

        if not playing or self._vol_current <= 0.001:
            has_energy = self._decay_bars()
            self.queue_draw()
            if not has_energy:
                self.bars = [0.0] * self.bands
                self.peaks = [0.0] * self.bands
                if self._vol_target <= 0.001 or not playing:
                    self._timer_id = None
                    return False
            return True

        targets = self.analyzer.get_spectrum()
        half = self.bands // 2
        bass = targets[:16] + targets[-16:]
        treble = targets[half - 16:half + 16]
        bass_e = sum(bass) / len(bass) if bass else 0.0
        treble_e = sum(treble) / len(treble) if treble else 0.0

        audible = self._vol_current >= 0.15
        both_active = audible and bass_e > 0.48 and treble_e > 0.48
        both_extreme = audible and bass_e > 0.65 and treble_e > 0.65

        if both_extreme and random.random() < 0.45:
            self._glitch_intensity = random.uniform(0.75, 1.0)
            self._tear_y = random.uniform(0.08, 0.85)
            self._tear_h = random.uniform(0.06, 0.22)
            self._tear_shift = random.choice([-18, -12, 12, 18])
        elif both_active and random.random() < 0.25:
            self._glitch_intensity = random.uniform(0.35, 0.60)
            self._tear_shift = random.choice([-8, 8])
        else:
            self._glitch_intensity *= 0.66
            if self._glitch_intensity < 0.02:
                self._glitch_intensity = 0.0
                self._tear_shift = 0.0

        for i in range(self.bands):
            t = targets[i]
            self.bars[i] = t if t >= self.bars[i] else max(0.0, self.bars[i] * 0.82 - 0.015)
            if self.bars[i] >= self.peaks[i]:
                self.peaks[i] = self.bars[i]
                self.peak_fall[i] = 0.003
            else:
                self.peaks[i] = max(0.0, self.peaks[i] - self.peak_fall[i])
                self.peak_fall[i] += 0.0055

        self.queue_draw()
        return True

    def _on_draw(self, _widget, cr):
        if not self.get_visible() or not self.get_window() or self._vol_current <= 0.001:
            return False

        alloc = self.get_allocation()
        w, h = alloc.width, alloc.height
        if w <= 0 or h <= 0: return False

        style_ctx = self.get_style_context()
        ok, primary = style_ctx.lookup_color("primary")
        if ok and primary:
            r, g, b = primary.red, primary.green, primary.blue
        else:
            rgba = style_ctx.get_color(Gtk.StateFlags.NORMAL)
            r, g, b = rgba.red, rgba.green, rgba.blue

        is_glitch = self._glitch_intensity > 0.05

        if is_glitch and abs(self._tear_shift) > 0.1:
            y_start, y_len = self._tear_y * h, self._tear_h * h

            cr.save(); cr.rectangle(0, y_start, w, y_len); cr.clip()
            cr.translate(self._tear_shift, 0)
            self._render_bars(cr, w, h, r, g, b, 0.90, is_glitch)
            cr.restore()

            cr.save()
            cr.rectangle(0, 0, w, y_start)
            cr.rectangle(0, y_start + y_len, w, h - (y_start + y_len))
            cr.clip()
            self._render_bars(cr, w, h, r, g, b, 0.85, is_glitch)
            cr.restore()
        else:
            self._render_bars(cr, w, h, r, g, b, 0.88, is_glitch)

        return False

    def _render_bars(self, cr, w, h, r, g, b, alpha, is_glitch):
        spacing = 1.5
        bar_w = max(1.5, (w - (self.bands - 1) * spacing) / float(self.bands))
        max_bar_h = float(h) * self._vol_current
        bottom_y = h

        if max_bar_h < 1.0 or self._vol_current <= 0.001: return

        if is_glitch:
            shift = int(2 + self._glitch_intensity * 6)
            for dx, clr in ((-shift, _CLR_CYAN), (shift, _CLR_RED)):
                cr.save(); cr.translate(dx, 0)
                self._draw_bars_pass(cr, bar_w, spacing, max_bar_h, bottom_y, clr[0], clr[1], clr[2], 0.75)
                cr.restore()

        self._draw_bars_pass(cr, bar_w, spacing, max_bar_h, bottom_y, r, g, b, alpha)
        self._draw_peak_elements(cr, bar_w, spacing, max_bar_h, bottom_y, is_glitch)

    def _draw_bars_pass(self, cr, bar_w, spacing, max_bar_h, bottom_y, r, g, b, alpha):
        cr.set_source_rgba(r, g, b, alpha)
        seg_h, seg_space = 3, 1

        for i in range(self.bands):
            bh = self.bars[i] * max_bar_h
            if bh < 1.0: continue
            bx = i * (bar_w + spacing)
            by = bottom_y - bh

            y_cur = bottom_y
            while y_cur - seg_h >= by:
                cr.rectangle(bx, y_cur - seg_h, bar_w, seg_h)
                y_cur -= (seg_h + seg_space)
            if y_cur > by:
                cr.rectangle(bx, by, bar_w, y_cur - by)

        cr.fill()

    def _draw_peak_elements(self, cr, bar_w, spacing, max_bar_h, bottom_y, is_glitch):
        for i in range(self.bands):
            ph = self.peaks[i] * max_bar_h
            if ph < 2.0: continue
            bx, by = i * (bar_w + spacing), bottom_y - ph
            cr.rectangle(bx, by - 2, bar_w, 1.5)
            cr.set_source_rgba(*(_CLR_RED[:3] + (0.95,)) if is_glitch else (1.0, 1.0, 1.0, 0.85))
            cr.fill()

    def cleanup(self):
        if self._timer_id: GLib.source_remove(self._timer_id); self._timer_id = None
        if self._poller:
            self._poller.unsubscribe(self._on_vol_changed)
            self._poller = None
        if self._vol_svc and self._vol_sig_id:
            try: self._vol_svc.disconnect(self._vol_sig_id)
            except Exception: pass
            self._vol_svc = None
        if self.analyzer:
            self.analyzer.cleanup()
            self.analyzer = None