import json
import os
import random
import time

import gi
gi.require_version("Cvc", "1.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Gst", "1.0")
from gi.repository import Cvc, GLib, Gst, Gtk

from fabric.widgets.box import Box
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer


_TICKS = 35
_TICK_MS = 40
_DUCK_RATIO = 0.20
_FADE_MS = 16
_STEPS = 20

_TOL_RATIO = 0.01        # допуск на округление PipeWire (1% от максимума)
_GRACE_S = 0.5           # игнорировать эхо собственных записей
_PENDING_TTL_S = 30      # сколько ждать вернувшийся поток приложения
_MAX_PLAY_S = 20         # страховка, если EOS не придёт

_IDLE, _DUCK, _PLAY, _UNDUCK = range(4)

_APP_ID = "fabric.glyph.sound"
_STREAM_NAME = "fabric-glyph-sound"

_STREAM_PROPS_DESC = (
    f"props,application.id=(string){_APP_ID},"
    f"application.name=(string){_STREAM_NAME},"
    f"media.name=(string){_STREAM_NAME}"
)

_SOUND_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "Glyph", "glyph.wav"
)
_SOUND_URI = GLib.filename_to_uri(_SOUND_PATH, None)

_STATE_FILE = os.path.join(
    GLib.get_user_cache_dir(), "vidgex-shell", "glyph_duck.json"
)


def _ease(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


def _vol(stream) -> int:
    """Сырая громкость потока (get_volume() в этой сборке Cvc отсутствует)."""
    try:
        return int(stream.get_property("volume"))
    except Exception:
        return 0


def _mk(text: str) -> str:
    return f'<span font_family="monospace" weight="bold">{GLib.markup_escape_text(text)}</span>'


_BASE = _mk("███\n███")
_TIERS = (
    tuple(_mk(t) for t in ("▓▓▓\n▓▓▓", "▓█▓\n█▓█")),
    tuple(_mk(t) for t in ("▒▒▒\n▒▒▒", "▀▄▀\n▄▀▄")),
    tuple(_mk(t) for t in ("░░░\n░░░", "▚▞▚\n▞▚▞", "▞▚▞\n▚▞▚")),
)


class GlyphSound:
    __slots__ = (
        "_closed", "_state", "_step", "_tid", "_wait", "_ready", "_recover",
        "_wd", "_duck_vol", "_tol",
        "_sigs", "_saved", "_fade", "_pending", "_written",
        "_control", "_playbin", "_bus", "_props",
    )

    def __init__(self) -> None:
        self._closed = False
        self._state = _IDLE
        self._step = 0
        self._tid: int | None = None
        self._wd: int | None = None
        self._wait = False
        self._ready = False
        self._recover = False
        self._duck_vol = 0
        self._tol = 8
        self._sigs: dict[int, tuple[Cvc.MixerStream, int]] = {}
        self._saved: dict[int, int] = {}
        self._fade: dict[int, tuple[int, int]] = {}
        # ключ приложения -> (исходная громкость, дедлайн по monotonic)
        self._pending: dict[str, tuple[int, float]] = {}
        # sid -> время нашей последней записи громкости
        self._written: dict[int, float] = {}
        self._control: Cvc.MixerControl | None = None
        self._playbin = None
        self._bus = None
        self._props = None

        # Если прошлый запуск умер во время приглушения, вернём громкость.
        if os.path.exists(_STATE_FILE):
            self._recover = True
            GLib.idle_add(self._start_recover)

    # ───────────────────────── инициализация ─────────────────────────
    def _open(self) -> None:
        Gst.init(None)
        self._props, _ = Gst.Structure.from_string(_STREAM_PROPS_DESC)

        self._control = Cvc.MixerControl(name=_STREAM_NAME)
        self._control.connect("stream-added", self._on_stream_added)
        self._control.connect("stream-removed", self._on_stream_removed)
        self._control.connect("state-changed", self._on_state)

        self._playbin = Gst.ElementFactory.make("playbin", None)
        self._playbin.set_property("flags", 0x02)
        self._playbin.set_property("uri", _SOUND_URI)
        self._playbin.connect("element-setup", self._on_element_setup)
        self._bus = self._playbin.get_bus()
        self._bus.add_signal_watch()
        self._bus.connect("message::eos", self._stop)
        self._bus.connect("message::error", self._stop)

        self._control.open()

    def _start_recover(self) -> bool:
        if self._closed:
            return False
        if self._control is None:
            self._open()
        elif self._ready and self._recover:
            self._recover_now()
        return False

    def _calc_levels(self) -> None:
        norm = self._control.get_vol_max_norm()
        self._duck_vol = int(norm * _DUCK_RATIO)
        self._tol = max(8, int(norm * _TOL_RATIO))

    def _on_state(self, _control, state) -> None:
        if state != Cvc.MixerControlState.READY:
            return
        self._ready = True
        self._calc_levels()
        if self._recover:
            # ВАЖНО: до _begin, иначе 20% запишутся как исходные
            self._recover_now()
        if self._wait:
            self._begin()

    def _on_element_setup(self, _playbin, element) -> None:
        if element.find_property("stream-properties") is not None:
            element.set_property("stream-properties", self._props)
        if element.find_property("client-name") is not None:
            element.set_property("client-name", _STREAM_NAME)

    # ───────────────────────── хелперы ─────────────────────────
    @staticmethod
    def _is_own(stream) -> bool:
        return _APP_ID == stream.get_application_id() or _STREAM_NAME in (
            stream.get_name(), stream.get_description()
        )

    @staticmethod
    def _key(stream) -> str | None:
        return stream.get_application_id() or stream.get_name() or None

    def _set(self, stream, vol: int) -> None:
        vol = max(0, int(vol))
        if _vol(stream) != vol:
            # метка ставится ДО записи: notify::volume приходит синхронно
            self._written[stream.get_id()] = time.monotonic()
            stream.set_volume(vol)
            stream.push_volume()

    def _cancel(self) -> None:
        if self._tid is not None:
            GLib.source_remove(self._tid)
            self._tid = None

    def _arm_watchdog(self) -> None:
        self._disarm_watchdog()
        self._wd = GLib.timeout_add_seconds(_MAX_PLAY_S, self._on_watchdog)

    def _disarm_watchdog(self) -> None:
        if self._wd is not None:
            GLib.source_remove(self._wd)
            self._wd = None

    def _on_watchdog(self) -> bool:
        self._wd = None
        if self._state == _PLAY:
            self._playbin.set_state(Gst.State.NULL)
            self._unduck()
        return False

    def _start_fade(self, state: int) -> None:
        self._state = state
        self._step = 0
        self._tid = GLib.timeout_add(_FADE_MS, self._tick)

    # ───────────────────────── сохранение на диск ─────────────────────────
    def _persist(self) -> None:
        """Пишем исходные громкости на диск, пока они не возвращены."""
        now = time.monotonic()
        data: dict[str, int] = {}
        for sid, saved in self._saved.items():
            entry = self._sigs.get(sid)
            key = self._key(entry[0]) if entry else None
            if key:
                data[key] = max(saved, data.get(key, 0))
        for key, (saved, deadline) in self._pending.items():
            if deadline > now:
                data[key] = max(saved, data.get(key, 0))
        try:
            if data:
                os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
                tmp = _STATE_FILE + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(data, f)
                os.replace(tmp, _STATE_FILE)
            elif os.path.exists(_STATE_FILE):
                os.remove(_STATE_FILE)
        except OSError:
            pass

    @staticmethod
    def _load_state() -> dict[str, int]:
        try:
            with open(_STATE_FILE) as f:
                raw = json.load(f)
            return {str(k): int(v) for k, v in raw.items()}
        except (OSError, ValueError, AttributeError, TypeError):
            return {}

    def _recover_now(self) -> None:
        self._recover = False
        data = self._load_state()
        matched: set[str] = set()
        for stream in self._control.get_sink_inputs():
            if not isinstance(stream, Cvc.MixerSinkInput) or self._is_own(stream):
                continue
            key = self._key(stream)
            if key in data:
                matched.add(key)
                self._restore(stream, data[key])

        # приложения, которых пока нет (например, плеер ещё не запущен)
        deadline = time.monotonic() + _PENDING_TTL_S
        for key, saved in data.items():
            if key not in matched:
                self._pending[key] = (saved, deadline)
        if self._pending:
            GLib.timeout_add_seconds(_PENDING_TTL_S + 1, self._expire_pending)
        self._persist()  # удалит файл, если ждать нечего

    def _expire_pending(self) -> bool:
        if self._closed:
            return False
        now = time.monotonic()
        self._pending = {k: v for k, v in self._pending.items() if v[1] > now}
        self._persist()
        return False

    # ───────────────────────── восстановление потока ─────────────────────────
    def _restore(self, stream, saved: int) -> None:
        if _vol(stream) < saved - self._tol:
            self._set(stream, saved)
            if self._state == _IDLE:
                # звуковой сервер мог после нас применить свою запомненную громкость
                GLib.timeout_add(400, self._recheck, stream, saved)

    def _recheck(self, stream, saved: int) -> bool:
        try:
            if (
                not self._closed
                and self._state == _IDLE
                and _vol(stream) < saved - self._tol
            ):
                self._set(stream, saved)
        except Exception:
            pass
        return False

    # ───────────────────────── приглушение ─────────────────────────
    def _register(self, stream, immediate: bool) -> None:
        if not isinstance(stream, Cvc.MixerSinkInput) or self._is_own(stream):
            return
        sid = stream.get_id()
        if sid not in self._sigs:
            self._sigs[sid] = (stream, stream.connect("notify::volume", self._on_volume))

        cur = _vol(stream)
        if cur <= self._duck_vol + self._tol:
            return  # уже тихий (или уже приглушён нами)
        self._saved.setdefault(sid, cur)
        if immediate:
            self._set(stream, self._duck_vol)
        else:
            self._fade[sid] = (cur, self._duck_vol)

    def _on_volume(self, stream, _pspec) -> None:
        # пока играет звук, приложения не должны «перекрикивать» его
        if self._state != _PLAY:
            return
        sid = stream.get_id()
        # эхо наших собственных записей (с округлением сервера) пропускаем
        if time.monotonic() - self._written.get(sid, 0.0) < _GRACE_S:
            return
        target, cur = self._duck_vol, _vol(stream)
        if abs(cur - target) <= self._tol:
            return
        if cur > target:
            # приложение/пользователь поднял громкость: это новая «исходная»
            self._saved[sid] = cur
            self._set(stream, target)
            self._persist()
        elif sid in self._saved:
            self._saved[sid] = cur
            self._persist()

    def _on_stream_added(self, _control, sid: int) -> None:
        if not self._pending and self._state not in (_DUCK, _PLAY):
            return
        stream = self._control.lookup_stream_id(sid)
        if stream is None:
            return

        entry = self._pending.pop(self._key(stream), None)
        if entry is not None:
            saved, deadline = entry
            if deadline > time.monotonic():
                # восстанавливаем ДО регистрации, иначе 20% станут «исходными»
                self._restore(stream, saved)

        if self._state in (_DUCK, _PLAY):
            self._register(stream, self._state == _PLAY)
        self._persist()

    def _on_stream_removed(self, _control, sid: int) -> None:
        entry = self._sigs.pop(sid, None)
        saved = self._saved.pop(sid, None)
        self._fade.pop(sid, None)
        self._written.pop(sid, None)
        if entry is None:
            return
        stream, handler = entry
        try:
            stream.disconnect(handler)
        except Exception:
            pass
        key = self._key(stream)
        if saved is not None and key:
            self._pending[key] = (saved, time.monotonic() + _PENDING_TTL_S)
            GLib.timeout_add_seconds(_PENDING_TTL_S + 1, self._expire_pending)
            self._persist()

    def _tick(self) -> bool:
        self._step += 1
        progress = _ease(min(1.0, self._step / _STEPS))
        for sid, (a, b) in list(self._fade.items()):
            entry = self._sigs.get(sid)
            if entry is not None:
                self._set(entry[0], a + (b - a) * progress)
        if self._step < _STEPS:
            return True
        self._tid = None
        if self._state == _DUCK:
            self._play_now()
        else:
            self._idle()
        return False

    def _play_now(self) -> None:
        self._cancel()
        self._state = _PLAY
        self._arm_watchdog()
        self._playbin.set_state(Gst.State.NULL)
        if self._playbin.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self._unduck()

    def _unduck(self) -> None:
        self._cancel()
        self._disarm_watchdog()
        self._fade.clear()
        for sid, saved in self._saved.items():
            entry = self._sigs.get(sid)
            if entry is None:
                continue
            cur = _vol(entry[0])
            if cur != saved:
                self._fade[sid] = (cur, saved)
        if self._fade:
            self._start_fade(_UNDUCK)
        else:
            self._idle()

    def _idle(self, persist: bool = True) -> None:
        self._cancel()
        self._disarm_watchdog()
        self._state = _IDLE
        self._fade.clear()
        self._saved.clear()
        self._written.clear()
        for stream, handler in self._sigs.values():
            try:
                stream.disconnect(handler)
            except Exception:
                pass
        self._sigs.clear()
        if persist:
            self._persist()

    def _begin(self) -> None:
        self._wait = False
        self._calc_levels()
        playing = self._state == _PLAY
        self._cancel()
        self._fade.clear()
        for stream in self._control.get_sink_inputs():
            self._register(stream, playing)
        self._persist()
        if playing or not self._fade:
            self._play_now()
        else:
            self._start_fade(_DUCK)

    # ───────────────────────── публичный API ─────────────────────────
    def play(self) -> None:
        if self._closed:
            return
        if not os.path.exists(_SOUND_PATH):
            return
        if self._control is None:
            self._open()
            self._wait = True
        elif not self._ready:
            self._wait = True
        else:
            self._begin()

    def _stop(self, _bus, _msg) -> None:
        if self._state == _PLAY:
            self._playbin.set_state(Gst.State.NULL)
            self._unduck()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._wait = False
        self._cancel()
        self._disarm_watchdog()
        if self._control is None:
            return
        # Файл оставляем: если push_volume не успеет уйти при выходе,
        # громкость вернётся при следующем запуске.
        self._persist()
        self._bus.remove_signal_watch()
        self._playbin.set_state(Gst.State.NULL)
        for sid, saved in self._saved.items():
            entry = self._sigs.get(sid)
            if entry is not None:
                self._set(entry[0], saved)
        self._pending.clear()
        self._idle(persist=False)
        self._control.close()


class SideGlyph(Gtk.Overlay):
    def __init__(self, side: str, **kwargs):
        super().__init__(valign=Gtk.Align.FILL, vexpand=True, **kwargs)

        spacer = Box(v_expand=True)
        spacer.set_size_request(45, -1)
        self.add(spacer)

        left = side == "left"
        align = "end" if left else "start"

        self.lbl = Label(name=f"side-glyph-{side}", markup=_BASE, h_align=align, v_align="center")
        self.lbl.set_justify(Gtk.Justification.CENTER)

        self.revealer = Revealer(
            transition_type="slide-left" if left else "slide-right",
            transition_duration=250,
            h_align=align, v_align="center",
            child_revealed=False, child=self.lbl,
        )
        self.add_overlay(self.revealer)

        self._markup = _BASE
        self._left = 0
        self._tid: int | None = None
        self._pending = False

        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)
        self.connect("destroy", lambda _: self._cancel())
        self.show_all()

    def _cancel(self) -> None:
        if self._tid is not None:
            GLib.source_remove(self._tid)
            self._tid = None

    def _set_markup(self, markup: str) -> None:
        if markup != self._markup:
            self._markup = markup
            self.lbl.set_markup(markup)

    def trigger(self) -> None:
        if self.get_mapped():
            self._run()
        else:
            self._pending = True

    def _run(self) -> None:
        self._cancel()
        self._set_markup(_BASE)
        self.revealer.set_reveal_child(True)
        self._left = _TICKS
        self._tid = GLib.timeout_add(_TICK_MS, self._tick)

    def _tick(self) -> bool:
        self._left -= 1
        if self._left <= 0:
            self._tid = None
            self._set_markup(_BASE)
            self.revealer.set_reveal_child(False)
            return False
        ratio = self._left / _TICKS
        if random.random() < ratio:
            self._set_markup(random.choice(_TIERS[int(ratio * 3)]))
        else:
            self._set_markup(_BASE)
        return True

    def _on_map(self, _w) -> None:
        if self._pending:
            self._pending = False
            self._run()

    def _on_unmap(self, _w) -> None:
        self._pending = False
        self._cancel()
        self._set_markup(_BASE)
        self.revealer.set_reveal_child(False)