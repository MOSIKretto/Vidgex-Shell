from mutagen import File as MutagenFile
import os
import time as _time
import hashlib
import threading
import json
import math
import random

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, Gdk, Gio, GLib, Gtk, Pango, PangoCairo
Gst.init(None)

from fabric.core.service import Service, Signal, Property
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.circularprogressbar import CircularProgressBar
from fabric.widgets.label import Label
from fabric.widgets.overlay import Overlay
from fabric.widgets.stack import Stack

import services.icons as icons
from modules.Notch.MainWindow.MusicPlayer.Player.mpris import MprisPlayer, MprisPlayerManager
from modules.Notch.MainWindow.MusicPlayer.Player.circleImage import CircleImage

_LBL_H = 20
_COVER_SIZE = 174
_PROG_SIZE = 200
_BTN_SIZE = 28
_PLAY_SIZE = 36

_NO_TIME = "--:-- / --:--"
_DEFAULT_TITLE = "Nothing Playing"
_DEFAULT_ARTIST = "¯\\_(ツ)_/¯"
_DEFAULT_ALBUM = "Enjoy the silence"

_CACHE_BASE = os.path.join(GLib.get_user_cache_dir(), "vidgex-shell")
_CACHE_DIR = os.path.join(_CACHE_BASE, "covers")
_MODE_FILE = os.path.join(_CACHE_BASE, "playback_mode.json")

_REPEAT_ONCE = f"{icons.repeat}<small><b>1</b></small>"
_VALID_COVER_EXT = frozenset({'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'})
_MAX_IMG_SIZE = 10 << 20
_SEEK_FLAGS = Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT
_SEEK_NS = 5_000_000_000
_SEEK_US = 5_000_000

_ORDER_NEXT_3 = {"normal": "reverse", "reverse": "shuffle", "shuffle": "normal"}
_ORDER_NEXT_2 = {"normal": "reverse", "reverse": "normal"}
_REPEAT_NEXT = {"None": "Playlist", "Playlist": "Track", "Track": "None"}

_IMG_SIGS = (
    (b'\x89PNG\r\n\x1a\n', 0, 8),
    (b'\xff\xd8', 0, 2),
    (b'BM', 0, 2),
    (b'GIF87a', 0, 6),
    (b'GIF89a', 0, 6),
)

_HOVER_MASK = Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
_SCROLL_MASK = Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK
_COVER_EVENTS = _HOVER_MASK | _SCROLL_MASK
_SCROLL_THRESHOLD = 5.0

_ANIM_MS = 16
_SPIN_STEP = 0.003
_FLICK_INITIAL = 0.12
_FLICK_DECAY = 0.88
_FLICK_MIN = 0.001
_TAU = math.tau

_PROG_FPS = 16
_SYNC_MS = 500

_OVERSHOOT_MIN = 0.035
_OVERSHOOT_MAX = 0.15

_SK_DUR_1 = 0.32
_SK_POW_1 = 4.5
_SK_DUR_2 = 0.24
_SK_POW_2 = 2.5
_SK_SCALE_MD = 1.3
_SK_SCALE_LG = 1.6

_SW_DUR_BASE = 0.25
_SW_DUR_SCALE = 0.65
_SW_POW = 2.0

_RISE_DUR = 0.45
_RISE_POW = 5.0
_RISE_MIN = 0.008

_CORR_THRESH = 0.012
_CORR_DUR = 0.20
_CORR_POW = 2.5

# ▶ FIX: порог обнаружения петли (replay) в _prog_tick
_REPLAY_PV_THRESH = 0.85
_REPLAY_TGT_THRESH = 0.05
_REPLAY_DIFF_THRESH = 0.5

_V_ART_LINES = [
    "@№@        @@/    \\@@##    /@@@@@@@@\\         /@@@@@@*@@@@/   /@@@№№@@@@@/    @@@      /@|",
    " @@\\      @№|      @@@     @@@@###@@@\\      /@!58@@@@@@@/     @@@@#=@@@/      |@@\\     @@|",
    " @@#      #@/      @@@     @@@     \\@@\\    /@<@          /    @!?               \\@\\  *@/",
    "  @#@    @#|       |@|     @&@      @@@    @&@          /@    @@@@#@@@@@/        |&#@@/",
    "  @№\\    |@/       |#@     @@?      #@@    *!?         /@@    @@##@@@/           |#@@/",
    "   @\\@  /#|        |#@     @@@    /#@@/    @>@@       /@*@    @&&               /@№  @@\\",
    "    @@##@/         #@@     @@@@@@/@@@/      @@@@&@?@@@@@/     @@@&?\"@@@/      /@#     @!@\\",
    "     @@@/         #@@@\\    \\@@@>-@@@/         \\@,,@@@@@/      \\@@@@@@@@@@/    /@@      @@|",
]
_V_MAX_W = max(len(l) for l in _V_ART_LINES)
_V_ART = '\n'.join(l.ljust(_V_MAX_W) for l in _V_ART_LINES)
_V_ART_PAD = [l.ljust(_V_MAX_W) for l in _V_ART_LINES]
_V_N_LINES = len(_V_ART_LINES)

_V_SCROLL_SPEED = 1.0
_V_GAP = 12

_GL_CHANCE = 0.015
_GL_DUR = (4, 18)
_GL_CD = 90
_GL_SHIFT_MAX = 15
_GL_SPLIT_MAX = 4.0
_GL_CORRUPT = 0.08
_GL_FLICKER = 0.12
_GL_BAR = 0.25
_GL_SPEED = (0.2, 3.5)
_GL_CHARS = "░▒▓█▀▄▌▐@#$%&!?*=~"
_GL_CHARS_LEN = len(_GL_CHARS)

os.makedirs(_CACHE_DIR, exist_ok=True)

def _cleanup_cache():
    try:
        cutoff = _time.time() - 86_400
        with os.scandir(_CACHE_DIR) as it:
            for e in it:
                if e.is_file() and e.stat().st_mtime < cutoff:
                    try:
                        os.remove(e.path)
                    except OSError:
                        pass
    except Exception:
        pass

threading.Thread(target=_cleanup_cache, daemon=True).start()

_cursor_cache: dict = {}

def _on_hover_enter(w, _e):
    win = w.get_window()
    if win:
        dsp = w.get_display()
        cur = _cursor_cache.get(dsp)
        if cur is None:
            cur = Gdk.Cursor.new_from_name(dsp, "pointer")
            _cursor_cache[dsp] = cur
        win.set_cursor(cur)

def _on_hover_leave(w, _e):
    win = w.get_window()
    if win:
        win.set_cursor(None)

def _hover(w):
    w.add_events(_HOVER_MASK)
    w.connect("enter-notify-event", _on_hover_enter)
    w.connect("leave-notify-event", _on_hover_leave)

def _fex(p):
    return bool(p) and os.path.isfile(p)

def _ext(p):
    if not p:
        return ""
    _, e = os.path.splitext(p.split('?', 1)[0])
    return e

def _set_style(w, cls, add):
    (w.add_style_class if add else w.remove_style_class)(cls)

def _set_label(lbl, txt):
    ok = bool(txt and not txt.isspace())
    lbl.set_text(txt if ok else " ")
    lbl.set_opacity(1.0 if ok else 0.0)

def _is_valid_image(data):
    if len(data) < 8:
        return False
    for sig, off, length in _IMG_SIGS:
        if data[off:length] == sig:
            return True
    return len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP'

def _load_mode():
    try:
        if os.path.isfile(_MODE_FILE):
            with open(_MODE_FILE) as f:
                d = json.load(f)
            return d.get("loop_status", "None"), d.get("order_mode", "normal")
    except Exception:
        pass
    return "None", "normal"

def _save_mode(ls, om):
    try:
        os.makedirs(os.path.dirname(_MODE_FILE), exist_ok=True)
        with open(_MODE_FILE, "w") as f:
            json.dump({"loop_status": ls, "order_mode": om}, f)
    except Exception:
        pass

def _mpris_id(mp):
    return (getattr(mp, "player_instance", None)
            or getattr(mp, "player_name", None)
            or f"player_{id(mp)}")

def _fmt_time(us):
    s = max(0, int(us)) // 1_000_000
    return f"{s // 60}:{s % 60:02}"

def _ease_out(t, power):
    return 1.0 - (1.0 - t) ** power


class LocalPlayer(Service):
    @Signal
    def changed(self) -> None: ...
    @Signal
    def next_requested(self) -> None: ...
    @Signal
    def previous_requested(self) -> None: ...

    def __init__(self):
        super().__init__()
        self.player_name = "Library"
        self.title = _DEFAULT_TITLE
        self.artist = _DEFAULT_ARTIST
        self.album = _DEFAULT_ALBUM
        self.url = ""
        self.arturl = ""
        self.playback_status = "stopped"
        self.length = 0
        self.can_seek = False
        self.can_pause = True
        self.can_go_next = False
        self.can_go_previous = False
        self.on_next_cb = None
        self.on_prev_cb = None
        self._replaying = False  # ▶ FIX: флаг для плавной анимации при повторе трека

        ls, om = _load_mode()
        self._loop_status = ls
        self._order_mode = om

        self._playbin = Gst.ElementFactory.make("playbin", "local_playbin")
        if self._playbin:
            bus = self._playbin.get_bus()
            bus.add_signal_watch()
            bus.connect("message::eos", self._on_eos)

    @Property(str, "read-write", default_value="None")
    def loop_status(self):
        return self._loop_status

    @loop_status.setter
    def loop_status(self, v):
        self._loop_status = v
        _save_mode(v, self._order_mode)

    @Property(str, "read-write", default_value="normal")
    def order_mode(self):
        return self._order_mode

    @order_mode.setter
    def order_mode(self, v):
        self._order_mode = v
        _save_mode(self._loop_status, v)

    @Property(bool, "read-write", default_value=False)
    def shuffle(self):
        return self._order_mode == "shuffle"

    @shuffle.setter
    def shuffle(self, v):
        self._order_mode = "shuffle" if v else "normal"
        _save_mode(self._loop_status, self._order_mode)

    @property
    def position(self):
        pb = self._playbin
        if pb and self.playback_status in ("playing", "paused"):
            ok, pos = pb.query_position(Gst.Format.TIME)
            if ok:
                return pos // 1000
        return 0

    def play_file(self, path, artist, title, album, length_us, art_url):
        pb = self._playbin
        if not pb:
            return
        pb.set_state(Gst.State.NULL)
        uri = GLib.filename_to_uri(path, None)
        pb.set_property("uri", uri)
        
        self.url = uri
        self.title = title
        self.artist = artist
        self.album = album or ""
        self.arturl = art_url
        self.length = length_us
        self.playback_status = "playing"
        self.can_seek = True
        pb.set_state(Gst.State.PLAYING)
        self.emit("changed")

    def stop(self):
        if self._playbin:
            self._playbin.set_state(Gst.State.NULL)
        self.title = _DEFAULT_TITLE
        self.artist = _DEFAULT_ARTIST
        self.album = _DEFAULT_ALBUM
        self.url = ""
        self.arturl = ""
        self.length = 0
        self.playback_status = "stopped"
        self.can_seek = self.can_go_next = self.can_go_previous = False
        self.emit("changed")

    def play_pause(self):
        pb = self._playbin
        if not pb:
            return
        st = self.playback_status
        if st == "playing":
            pb.set_state(Gst.State.PAUSED)
            self.playback_status = "paused"
        elif st == "paused":
            pb.set_state(Gst.State.PLAYING)
            self.playback_status = "playing"
        self.emit("changed")

    def next(self):
        (self.on_next_cb or (lambda: self.emit("next_requested")))()

    def previous(self):
        (self.on_prev_cb or (lambda: self.emit("previous_requested")))()

    def _on_eos(self, _bus, _msg):
        ls = self._loop_status
        if ls == "Track":
            GLib.idle_add(self._replay)
        elif self._order_mode == "reverse":
            GLib.idle_add(self.previous)
        else:
            GLib.idle_add(self.next)

    # ▶ FIX: устанавливаем флаг и сигналим changed для плавной анимации
    def _replay(self):
        if self._playbin:
            self._replaying = True
            self._playbin.seek_simple(Gst.Format.TIME, _SEEK_FLAGS, 0)
            self.emit("changed")


class PlayerBox(Box):
    __slots__ = (
        'mpris_player', '_sig_id', '_exit_sig_id', '_is_local',
        'cover', 'cover_placeholder', '_cover_box',
        'title', 'album', 'artist', 'progressbar', 'time',
        'prev', 'backward', 'play_pause', 'forward', 'next',
        'shuffle_btn', 'repeat_btn', 'mode_box',
        'btn_box', 'info_box', 'player_box', 'overlay_container',
        '_angle', '_anim_id', '_spinning', '_flick_v',
        '_last_art', '_last_track_id', '_extract_tried', '_is_wall', '_dcancel',
        '_upd', '_scroll_acc', '_local_order',
        '_v_offset', '_v_scroll_id',
        '_g_on', '_g_rem', '_g_cd', '_g_shifts', '_g_split',
        '_pv', '_last_pv', '_ptimer', '_stimer',
        '_kpos', '_ktime', '_klen', '_kplay',
        '_tkey', '_last_time_txt',
        '_a_active', '_a_t0', '_a_from', '_a_to',
        '_a_dur', '_a_pow', '_a_chain', '_a_done',
        '_v_layout', '_v_font', '_v_lw', '_v_lh', '_v_block', '_v_best_pt',
    )

    def __init__(self, mpris_player=None):
        super().__init__(
            orientation="h", h_align="fill", v_align="fill",
            spacing=0, h_expand=True, v_expand=True,
        )
        mp = self.mpris_player = mpris_player
        self._is_local = isinstance(mp, LocalPlayer)

        self._sig_id = self._exit_sig_id = None
        self._anim_id = self._dcancel = None
        self._upd = False

        self._last_art = None
        self._last_track_id = None
        self._extract_tried = False
        self._is_wall = True
        self._angle = 0.0
        self._spinning = False
        self._flick_v = 0.0
        self._scroll_acc = 0.0
        self._local_order = "normal"

        self._v_offset = 0.0
        self._v_scroll_id = None
        self._v_layout = self._v_font = None
        self._v_lw = self._v_lh = self._v_block = 0
        self._v_best_pt = 0

        self._g_on = False
        self._g_rem = self._g_cd = 0
        self._g_shifts = []
        self._g_split = 0.0

        self._pv = self._last_pv = 0.0
        self._ptimer = self._stimer = None
        self._kpos = self._klen = 0
        self._ktime = _time.monotonic()
        self._kplay = False
        self._tkey = None
        self._last_time_txt = ""

        self._a_active = False
        self._a_t0 = self._a_from = self._a_to = 0.0
        self._a_dur = 0.3
        self._a_pow = 4.0
        self._a_chain = []
        self._a_done = 'live'

        self.cover = CircleImage(
            name="player-cover", size=_COVER_SIZE,
            h_align="center", v_align="center",
        )

        cb = self._cover_box = Gtk.EventBox()
        cb.set_visible_window(False)
        cb.set_above_child(True)
        cb.set_halign(Gtk.Align.CENTER)
        cb.set_valign(Gtk.Align.CENTER)
        cb.set_size_request(_COVER_SIZE, _COVER_SIZE)
        cb.add(self.cover)
        cb.add_events(_COVER_EVENTS | Gdk.EventMask.BUTTON_PRESS_MASK)
        cb.connect("draw", self._on_cover_draw)
        cb.connect("scroll-event", self._on_scroll)
        cb.connect("button-press-event", self._on_cover_click)
        cb.connect("enter-notify-event", _on_hover_enter)
        cb.connect("leave-notify-event", _on_hover_leave)
        cb.show_all()

        self.cover_placeholder = CircleImage(
            name="player-cover", size=_PROG_SIZE,
            h_align="center", v_align="center",
        )

        lkw = dict(
            h_expand=False, h_align="center",
            ellipsization="end", max_chars_width=20,
            justify=Gtk.Justification.CENTER,
        )
        self.title = Label(name="player-title", **lkw)
        self.album = Label(name="player-album", **lkw)
        self.artist = Label(name="player-artist", **lkw)
        for lb in (self.title, self.album, self.artist):
            lb.set_size_request(-1, _LBL_H)
        self.title.set_label(_DEFAULT_TITLE)
        self.album.set_label(_DEFAULT_ALBUM)
        self.artist.set_label(_DEFAULT_ARTIST)

        self.progressbar = CircularProgressBar(
            name="player-progress", size=_PROG_SIZE,
            h_align="center", v_align="center",
            start_angle=180, end_angle=360,
        )
        self.time = Label(name="player-time", label=_NO_TIME)

        self.overlay_container = Box(
            name="player-overlay", orientation="v",
            h_expand=True, v_expand=True,
            h_align="center", v_align="center",
            children=(Overlay(
                child=self.cover_placeholder,
                overlays=(self.progressbar, self._cover_box),
            ),),
        )
        self.overlay_container.set_size_request(_PROG_SIZE, _PROG_SIZE)

        self.prev = self._btn(icons.prev)
        self.backward = self._btn(icons.skip_back)
        self.play_pause = self._btn(icons.play, ("play-pause",))
        self.forward = self._btn(icons.skip_forward)
        self.next = self._btn(icons.next)

        self.shuffle_btn = self._btn(icons.shuffle, ("mode",))
        self.shuffle_btn.set_tooltip_text("Order")
        self.shuffle_btn.connect("clicked", self._toggle_order)

        self.repeat_btn = self._btn(icons.repeat, ("mode",))
        self.repeat_btn.set_tooltip_text("Repeat")
        self.repeat_btn.connect("clicked", self._toggle_repeat)

        for b, s in ((self.prev, _BTN_SIZE), (self.backward, _BTN_SIZE),
                      (self.play_pause, _PLAY_SIZE),
                      (self.forward, _BTN_SIZE), (self.next, _BTN_SIZE),
                      (self.shuffle_btn, _BTN_SIZE), (self.repeat_btn, _BTN_SIZE)):
            b.set_size_request(s, s)

        self.mode_box = Box(
            name="player-mode-box", orientation="h", spacing=8,
            h_expand=False, v_expand=False, h_align="center", v_align="center",
            children=(self.shuffle_btn, self.repeat_btn),
        )
        self.btn_box = Box(
            name="player-btn-box", orientation="h", spacing=4,
            h_expand=False, v_expand=False, h_align="center", v_align="center",
            children=(self.prev, self.backward, self.play_pause,
                      self.forward, self.next),
        )
        self.info_box = Box(
            name="player-info-box", orientation="v", spacing=4,
            h_expand=True, v_expand=True, h_align="center", v_align="center",
            children=(self.title, self.album, self.artist,
                      self.btn_box, self.time, self.mode_box),
        )
        self.info_box.set_size_request(200, -1)

        self.player_box = Box(
            name="player-box", orientation="h", spacing=0,
            h_expand=True, v_expand=True, h_align="fill", v_align="fill",
            homogeneous=True,
            children=(self.overlay_container, self.info_box),
        )
        self.add(self.player_box)

        if mp:
            self._wire()
            self._prog_start()
        else:
            self._setup_empty()

        if self._is_wall:
            self._v_start_scroll()

    @staticmethod
    def _btn(icon, sc=()):
        b = Button(
            name="player-btn",
            child=Label(name="player-btn-label", markup=icon, style_classes=sc),
            style_classes=sc,
            h_expand=False, v_expand=False, h_align="center", v_align="center",
        )
        _hover(b)
        return b

    def _get_order(self):
        mp = self.mpris_player
        if self._is_local:
            return mp._order_mode
        return self._local_order

    def _is_reversed(self):
        return self._get_order() == "reverse"

    def _check_spin_state(self):
        if self._is_wall:
            self._spinning = False
            return

        mp = self.mpris_player
        playing = mp and getattr(mp, "playback_status", "") == "playing"

        if playing:
            self._spinning = True
            self._ensure_anim()
        else:
            self._spinning = False

    def _on_cover_draw(self, w, cr):
        if self._is_wall:
            alloc = w.get_allocation()
            self._draw_v(cr, alloc.width, alloc.height)
            return True
        child = w.get_child()
        if not child or not child.get_visible():
            return True
        angle = self._angle
        if angle:
            alloc = w.get_allocation()
            cx, cy = alloc.width * 0.5, alloc.height * 0.5
            cr.save()
            cr.translate(cx, cy)
            cr.rotate(angle)
            cr.translate(-cx, -cy)
            w.propagate_draw(child, cr)
            cr.restore()
        else:
            w.propagate_draw(child, cr)
        return True

    def _draw_v(self, cr, w, h):
        rgba = self.artist.get_style_context().get_color(Gtk.StateFlags.NORMAL)
        if rgba.alpha < 0.01:
            r, g, b, a = 0.55, 0.55, 0.55, 1.0
        else:
            r, g, b, a = rgba.red, rgba.green, rgba.blue, rgba.alpha

        layout = self._v_layout
        font = self._v_font
        if layout is None:
            layout = PangoCairo.create_layout(cr)
            font = Pango.FontDescription.from_string("monospace bold")
            self._v_layout = layout
            self._v_font = font
            self._v_best_pt = 0

        pad = 0.08
        max_h = h * (1.0 - 2.0 * pad)
        best = self._v_best_pt
        if best == 0 or True:
            best = 4
            layout.set_text(_V_ART, -1)
            for pt in range(4, 40):
                font.set_size(pt * Pango.SCALE)
                layout.set_font_description(font)
                _, lh = layout.get_pixel_size()
                if lh <= max_h:
                    best = pt
                else:
                    break
            self._v_best_pt = best

        font.set_size(best * Pango.SCALE)
        layout.set_font_description(font)
        layout.set_text(_V_ART, -1)
        lw, lh = layout.get_pixel_size()

        gap_layout = PangoCairo.create_layout(cr)
        gap_layout.set_font_description(font)
        gap_layout.set_text(" " * _V_GAP, -1)
        gw, _ = gap_layout.get_pixel_size()

        block = lw + gw
        off = self._v_offset % block if block > 0 else 0.0
        y0 = (h - lh) * 0.5

        cr.save()
        cx, cy = w * 0.5, h * 0.5
        radius = min(w, h) * 0.5
        cr.arc(cx, cy, radius, 0, _TAU)
        cr.clip()

        if self._g_on and self._g_shifts:
            self._draw_glitch(cr, w, h, font, r, g, b, a, block, off, y0, lh)
        else:
            cr.set_source_rgba(r, g, b, a)
            x = -off
            while x < w:
                cr.move_to(x, y0)
                PangoCairo.show_layout(cr, layout)
                x += block

        cr.restore()

    def _draw_glitch(self, cr, w, h, font, r, g, b, a, block, off, y0, total_h):
        n = _V_N_LINES
        shifts = self._g_shifts
        split = self._g_split
        line_h = total_h / n
        rand = random.random
        randint = random.randint

        ll = PangoCairo.create_layout(cr)
        ll.set_font_description(font)

        for i in range(n):
            if rand() < _GL_FLICKER:
                continue
            shift = shifts[i] if i < len(shifts) else 0
            ly = y0 + i * line_h
            txt = _V_ART_PAD[i]
            if abs(shift) > 3:
                chars = list(txt)
                gl_chars = _GL_CHARS
                gl_len = _GL_CHARS_LEN
                corrupt_rate = _GL_CORRUPT
                for j in range(len(chars)):
                    if chars[j] != ' ' and rand() < corrupt_rate:
                        chars[j] = gl_chars[randint(0, gl_len - 1)]
                txt = ''.join(chars)
            ll.set_text(txt, -1)
            x = -off + shift
            while x < w:
                if split > 0.5 and shift != 0:
                    cr.set_source_rgba(min(1.0, r + 0.4), g * 0.15, b * 0.15, 0.55)
                    cr.move_to(x - split, ly)
                    PangoCairo.show_layout(cr, ll)
                    cr.set_source_rgba(r * 0.15, g * 0.15, min(1.0, b + 0.4), 0.55)
                    cr.move_to(x + split, ly)
                    PangoCairo.show_layout(cr, ll)
                cr.set_source_rgba(r, g, b, a)
                cr.move_to(x, ly)
                PangoCairo.show_layout(cr, ll)
                x += block
        if rand() < _GL_BAR:
            cr.set_source_rgba(r, g, b, random.uniform(0.08, 0.25))
            bar_y = random.uniform(0, h)
            cr.rectangle(0, bar_y, w, random.uniform(2, 8))
            cr.fill()

    def _v_start_scroll(self):
        if not self._v_scroll_id:
            self._v_scroll_id = GLib.timeout_add(_ANIM_MS, self._v_scroll_tick)

    def _v_stop_scroll(self):
        sid = self._v_scroll_id
        if sid:
            GLib.source_remove(sid)
            self._v_scroll_id = None

    def _v_scroll_tick(self):
        rand = random.random
        if self._g_on:
            self._v_offset += _V_SCROLL_SPEED * random.uniform(*_GL_SPEED)
            self._g_rem -= 1
            if self._g_rem <= 0:
                self._g_on = False
                self._g_cd = _GL_CD
                self._g_shifts = []
            else:
                n = _V_N_LINES
                self._g_shifts = [
                    random.randint(-_GL_SHIFT_MAX, _GL_SHIFT_MAX)
                    if rand() < 0.35 else 0
                    for _ in range(n)
                ]
                self._g_split = random.uniform(0, _GL_SPLIT_MAX)
        else:
            self._v_offset += _V_SCROLL_SPEED
            if self._g_cd > 0:
                self._g_cd -= 1
            elif rand() < _GL_CHANCE:
                self._g_on = True
                self._g_rem = random.randint(*_GL_DUR)

        self._cover_box.queue_draw()
        return True

    def _ensure_anim(self):
        if self._anim_id is None:
            self._anim_id = GLib.timeout_add(_ANIM_MS, self._anim_tick)

    def _anim_tick(self):
        spinning = self._spinning
        flick_v = self._flick_v
        angle = self._angle

        if spinning:
            angle += _SPIN_STEP
        if flick_v:
            angle += flick_v
            flick_v *= _FLICK_DECAY
            if abs(flick_v) < _FLICK_MIN:
                flick_v = 0.0
            self._flick_v = flick_v
            
        angle %= _TAU

        self._angle = angle
        self._cover_box.queue_draw()

        alive = spinning or bool(flick_v)
        if not alive:
            self._anim_id = None
        return alive

    def _on_scroll(self, _w, ev):
        d = ev.direction
        if d == Gdk.ScrollDirection.UP:
            self._seek(1)
            return True
        if d == Gdk.ScrollDirection.DOWN:
            self._seek(-1)
            return True
        ok, _dx, dy = ev.get_scroll_deltas()
        if ok and dy:
            acc = self._scroll_acc + dy
            if acc <= -_SCROLL_THRESHOLD:
                self._seek(1)
                acc = 0.0
            elif acc >= _SCROLL_THRESHOLD:
                self._seek(-1)
                acc = 0.0
            self._scroll_acc = acc
        return True

    def _on_cover_click(self, _w, ev):
        if ev.button == 1 and self.mpris_player:
            self.mpris_player.play_pause()
        return True

    def _ucover(self, track_id, arturl):
        if track_id == self._last_track_id and arturl == self._last_art:
            return
            
        self._last_track_id = track_id
        self._last_art = arturl
        self._extract_tried = False
        
        self._angle = 0.0
        self._spinning = False
        self._flick_v = 0.0
        aid = self._anim_id
        if aid is not None:
            GLib.source_remove(aid)
            self._anim_id = None
            
        self._scroll_acc = 0.0

        if not arturl:
            self._try_extract()
            return
            
        scheme = GLib.uri_parse_scheme(arturl)
        if scheme == "file":
            self._set_img(GLib.uri_unescape_string(arturl[7:], None))
        elif scheme in ("http", "https"):
            self._dl_art(arturl)
        elif arturl.startswith('/'):
            self._set_img(arturl)
        else:
            self._placeholder()

    def _try_extract(self):
        if self._extract_tried:
            self._placeholder()
            return
        self._extract_tried = True
        mp = self.mpris_player
        
        url = getattr(mp, 'url', '') or getattr(mp, 'arturl', '')
        
        if url.startswith("file://"):
            try:
                gfile = Gio.File.new_for_uri(url)
                path = gfile.get_path()
                if path and os.path.isfile(path):
                    threading.Thread(target=self._extract_bg, args=(path,), daemon=True).start()
                    return
            except Exception:
                pass
                
        self._placeholder()

    def _extract_bg(self, path):
        try:
            h = hashlib.md5(path.encode('utf-8')).hexdigest()
            cp = os.path.join(_CACHE_DIR, f"ex_{h}.png")
            
            if _fex(cp):
                GLib.idle_add(self._set_img, cp)
                return
            
            af = MutagenFile(path)
            if af:
                data = self._cover_bytes(af)
                if data:
                    with open(cp, 'wb') as f:
                        f.write(data)
                    GLib.idle_add(self._set_img, cp)
                    return
        except Exception:
            pass
        GLib.idle_add(self._placeholder)

    @staticmethod
    def _cover_bytes(audio):
        try:
            if hasattr(audio, 'pictures') and audio.pictures:
                return audio.pictures[0].data
            
            tags = getattr(audio, 'tags', None)
            if not tags:
                return None
                
            for k in tags:
                if k.startswith('APIC'):
                    return tags[k].data
                    
            if 'covr' in tags and tags['covr']:
                return bytes(tags['covr'][0])
                
            if 'WM/Picture' in tags and tags['WM/Picture']:
                pic = tags['WM/Picture'][0]
                if hasattr(pic, 'value'):
                    return pic.value
        except Exception:
            pass
        return None

    def _placeholder(self):
        self._spinning = False
        self._flick_v = 0.0
        aid = self._anim_id
        if aid is not None:
            GLib.source_remove(aid)
            self._anim_id = None
        self._angle = 0.0
        self._is_wall = True
        self._v_offset = 0.0
        self._g_on = False
        self._g_rem = self._g_cd = 0
        self._g_shifts = []
        self._g_split = 0.0
        self._v_start_scroll()
        self._cover_box.queue_draw()

    def _set_img(self, p):
        if _fex(p):
            self.cover.set_image_from_file(p)
            self._is_wall = False
            self._v_stop_scroll()
            self._cover_box.queue_draw()
            self._check_spin_state() 
        else:
            self._placeholder()

    def _dl_art(self, url):
        h = hashlib.md5(url.encode('utf-8')).hexdigest()
        ext = _ext(url) or '.png'
        if ext.lower() not in _VALID_COVER_EXT:
            ext = '.png'
        cp = os.path.join(_CACHE_DIR, f"{h}{ext}")
        if _fex(cp):
            self._set_img(cp)
            return
        dc = self._dcancel
        if dc:
            dc.cancel()
        self._dcancel = dc = Gio.Cancellable.new()
        Gio.File.new_for_uri(url).load_contents_async(dc, self._on_dl, cp)

    def _on_dl(self, f, res, cp):
        try:
            ok, data, _ = f.load_contents_finish(res)
            if ok and data and len(data) <= _MAX_IMG_SIZE and _is_valid_image(data):
                Gio.File.new_for_path(cp).replace_contents_bytes_async(
                    GLib.Bytes.new(data), None, False,
                    Gio.FileCreateFlags.PRIVATE, self._dcancel,
                    self._on_dl_save, cp)
                return
        except GLib.Error:
            pass
        GLib.idle_add(self._try_extract)

    def _on_dl_save(self, gf, res, cp):
        try:
            if gf.replace_contents_finish(res)[0]:
                GLib.idle_add(self._set_img, cp)
                return
        except GLib.Error:
            pass
        GLib.idle_add(self._try_extract)

    def _wire(self):
        self._refresh()
        mp = self.mpris_player
        self.prev.connect("clicked", lambda _: self._do_prev())
        self.next.connect("clicked", lambda _: self._do_next())
        self.play_pause.connect("clicked", lambda _: mp.play_pause())
        self.backward.connect("clicked", lambda _: self._seek(-1))
        self.forward.connect("clicked", lambda _: self._seek(1))
        self._sig_id = mp.connect("changed", self._on_changed)

    def _do_prev(self):
        mp = self.mpris_player
        if mp:
            (mp.next if self._is_reversed() else mp.previous)()

    def _do_next(self):
        mp = self.mpris_player
        if mp:
            (mp.previous if self._is_reversed() else mp.next)()

    def _setup_empty(self):
        self._spinning = False
        self._flick_v = 0.0
        aid = self._anim_id
        if aid is not None:
            GLib.source_remove(aid)
            self._anim_id = None
        self._angle = 0.0
        self._cover_box.queue_draw()
        self.play_pause.get_child().set_markup(icons.stop)
        self.play_pause.add_style_class("stop")
        for b in (self.backward, self.forward, self.prev, self.next,
                  self.shuffle_btn, self.repeat_btn):
            b.add_style_class("disabled")
        self.progressbar.set_value(0.0)
        self.progressbar.queue_draw()
        self.time.set_text(_NO_TIME)
        self._pv = self._last_pv = 0.0
        self._a_active = False
        self._a_chain = []
        self._kpos = self._klen = 0
        self._kplay = False
        self._tkey = None
        self._last_time_txt = ""

    def _begin_seg(self, to, dur, power):
        self._a_active = True
        self._a_t0 = _time.monotonic()
        self._a_from = self._pv
        t = to
        if t < 0.0: t = 0.0
        elif t > 1.0: t = 1.0
        self._a_to = t
        self._a_dur = max(0.016, dur)
        self._a_pow = power

    def _run_chain(self, segments, on_done='live'):
        if not segments:
            return
        self._a_chain = list(segments[1:])
        self._a_done = on_done
        s = segments[0]
        self._begin_seg(s[0], s[1], s[2])

    def _cancel_anim(self):
        self._a_active = False
        self._a_chain = []

    def _prog_start(self):
        if not self._ptimer:
            self._ptimer = GLib.timeout_add(_PROG_FPS, self._prog_tick)
        if not self._stimer:
            self._stimer = GLib.timeout_add(_SYNC_MS, self._sync_tick)
        self._force_sync()

    def _prog_stop(self):
        for attr in ('_ptimer', '_stimer'):
            tid = getattr(self, attr, None)
            if tid:
                GLib.source_remove(tid)
                setattr(self, attr, None)

    def _force_sync(self):
        mp = self.mpris_player
        if not mp:
            return
        self._kpos = getattr(mp, 'position', 0) or 0
        self._ktime = _time.monotonic()
        self._klen = getattr(mp, 'length', 0) or 0
        self._kplay = getattr(mp, 'playback_status', '') == 'playing'

    def _sync_tick(self):
        mp = self.mpris_player
        if not mp or getattr(mp, '_dead', False):
            self._stimer = None
            return False
        self._kplay = getattr(mp, 'playback_status', '') == 'playing'
        self._klen = getattr(mp, 'length', 0) or 0
        if not self._a_active:
            self._kpos = getattr(mp, 'position', 0) or 0
            self._ktime = _time.monotonic()
        return True

    def _prog_tick(self):
        mp = self.mpris_player
        if not mp or getattr(mp, '_dead', False):
            self._ptimer = None
            return False

        tot = self._klen
        if tot <= 0:
            if self._pv != 0.0:
                self._pv = self._last_pv = 0.0
                self.progressbar.set_value(0.0)
                self.progressbar.queue_draw()
            if self._last_time_txt != _NO_TIME:
                self._last_time_txt = _NO_TIME
                self.time.set_text(_NO_TIME)
            return True

        now = _time.monotonic()
        pv = self._pv

        if self._a_active:
            elapsed = now - self._a_t0
            dur = self._a_dur
            t = elapsed / dur if elapsed < dur else 1.0
            eased = _ease_out(t, self._a_pow)
            a_from = self._a_from
            pv = a_from + (self._a_to - a_from) * eased

            if t >= 1.0:
                pv = self._a_to
                chain = self._a_chain
                if chain:
                    seg = chain.pop(0)
                    self._begin_seg(seg[0], seg[1], seg[2])
                else:
                    self._a_active = False
                    if self._a_done == 'rise':
                        self._force_sync()
                        klen, kpos = self._klen, self._kpos
                        if klen > 0 and kpos > 0:
                            tgt = kpos / klen
                            if tgt > 1.0:
                                tgt = 1.0
                            if tgt > _RISE_MIN:
                                self._run_chain([(tgt, _RISE_DUR, _RISE_POW)], on_done='live')
                            else:
                                self._kpos = int(pv * tot)
                                self._ktime = now
                        else:
                            self._kpos = int(pv * tot)
                            self._ktime = now
                    else:
                        self._kpos = int(pv * tot)
                        self._ktime = now
            self._pv = pv
        else:
            if self._kplay:
                predicted = self._kpos + (now - self._ktime) * 1_000_000
                target = predicted / tot
            else:
                target = self._kpos / tot
            if target < 0.0:
                target = 0.0
            elif target > 1.0:
                target = 1.0

            diff = target - pv
            adiff = -diff if diff < 0 else diff  # ▶ FIX: abs без вызова

            # ▶ FIX: обнаружение петли/повтора трека (прогресс был у конца,
            #   а цель вдруг у начала) — плавная анимация вместо резкого скачка.
            #   Работает и для MPRIS-плееров, у которых нет флага _replaying.
            if (pv > _REPLAY_PV_THRESH
                    and target < _REPLAY_TGT_THRESH
                    and diff < -_REPLAY_DIFF_THRESH):
                sw_dur = _SW_DUR_BASE + pv * _SW_DUR_SCALE
                self._run_chain([(0.0, sw_dur, _SW_POW)], on_done='live')
            elif adiff > _CORR_THRESH:
                self._run_chain([(target, _CORR_DUR, _CORR_POW)], on_done='live')
            else:
                pv = target
                self._pv = pv

        if pv < 0.0:
            pv = 0.0
        elif pv > 1.0:
            pv = 1.0

        if pv != self._last_pv:
            self._last_pv = pv
            self.progressbar.set_value(pv)
            self.progressbar.queue_draw()

        cur_us = int(pv * tot)
        txt = f"{_fmt_time(cur_us)} / {_fmt_time(tot)}"
        if txt != self._last_time_txt:
            self._last_time_txt = txt
            self.time.set_text(txt)
        return True

    def _update_prog_state(self, mp):
        status = getattr(mp, 'playback_status', 'stopped')
        self._kplay = status == 'playing'
        self._klen = getattr(mp, 'length', 0) or 0

        if status == 'stopped':
            self._pv = self._last_pv = 0.0
            self._cancel_anim()
            self.progressbar.set_value(0.0)
            self.progressbar.queue_draw()
            if self._last_time_txt != _NO_TIME:
                self._last_time_txt = _NO_TIME
                self.time.set_text(_NO_TIME)
            self._tkey = None
            return

        new_key = (mp.title, mp.artist, getattr(mp, 'arturl', ''), getattr(mp, 'url', ''))

        if self._tkey is not None and new_key != self._tkey:
            # Трек сменился — плавный спуск к 0, затем подъём
            if self._pv > 0.012:
                dur = _SW_DUR_BASE + self._pv * _SW_DUR_SCALE
                self._run_chain([(0.0, dur, _SW_POW)], on_done='rise')
            else:
                self._pv = self._last_pv = 0.0
                self._cancel_anim()
                self._force_sync()
        # ▶ FIX: тот же трек, но повтор (replay) — плавная анимация к 0
        elif self._is_local and getattr(mp, '_replaying', False):
            mp._replaying = False
            if self._pv > 0.012:
                dur = _SW_DUR_BASE + self._pv * _SW_DUR_SCALE
                self._run_chain([(0.0, dur, _SW_POW)], on_done='rise')
            else:
                self._pv = self._last_pv = 0.0
                self._cancel_anim()
                self._force_sync()
        elif not self._a_active:
            self._kpos = getattr(mp, 'position', 0) or 0
            self._ktime = _time.monotonic()

        self._tkey = new_key

    # ▶ FIX: симметричная логика — прокрутка за границу трека
    #   в обоих направлениях переключает трек, а не зацикливает.
    def _seek(self, direction):
        mp = self.mpris_player
        if not mp:
            return
        ok = False
        new_us = None

        if self._is_local:
            pb = mp._playbin
            if pb and mp.can_seek:
                good, pos = pb.query_position(Gst.Format.TIME)
                if good:
                    length_ns = mp.length * 1000

                    # Прокрутка назад за 0:00 → предыдущий трек
                    if direction < 0 and pos <= 1_000_000_000:
                        self._do_prev()
                        return

                    # ▶ FIX: прокрутка вперёд за конец → следующий трек
                    if direction > 0 and length_ns > 0 and (length_ns - pos) <= 1_000_000_000:
                        self._do_next()
                        return

                    tgt = (min(pos + _SEEK_NS, length_ns) if direction > 0
                           else max(0, pos - _SEEK_NS))
                    pb.seek_simple(Gst.Format.TIME, _SEEK_FLAGS, tgt)
                    new_us = tgt // 1000
                    ok = True
        elif isinstance(mp, MprisPlayer) and mp.can_seek:
            cur_pos = getattr(mp, "position", 0) or 0

            # Прокрутка назад за 0:00 → предыдущий трек
            if direction < 0 and cur_pos <= 1_000_000:
                self._do_prev()
                return

            # ▶ FIX: прокрутка вперёд за конец → следующий трек
            if direction > 0 and self._klen > 0 and (self._klen - cur_pos) <= 1_000_000:
                self._do_next()
                return

            mp.seek(_SEEK_US * direction)
            new_us = max(0, cur_pos + _SEEK_US * direction)
            if self._klen > 0:
                new_us = min(new_us, self._klen)
            ok = True

        if ok and new_us is not None:
            if self._klen > 0:
                self._kpos = int(new_us)
                self._ktime = _time.monotonic()
                target = max(0.0, min(1.0, new_us / self._klen))

                over_pct = random.uniform(_OVERSHOOT_MIN, _OVERSHOOT_MAX)
                over = max(0.0, min(1.0, target + (1.0 if direction > 0 else -1.0) * over_pct))

                dist = abs(target - self._pv)
                if dist < 0.05: d1, d2 = _SK_DUR_1, _SK_DUR_2
                elif dist < 0.15: d1, d2 = _SK_DUR_1 * _SK_SCALE_MD, _SK_DUR_2 * _SK_SCALE_MD
                else: d1, d2 = _SK_DUR_1 * _SK_SCALE_LG, _SK_DUR_2 * _SK_SCALE_LG

                self._run_chain([(over, d1, _SK_POW_1), (target, d2, _SK_POW_2)], on_done='live')

        if ok and not self._is_wall:
            self._flick_v = _FLICK_INITIAL * float(direction)
            self._ensure_anim()
            self._check_spin_state()

    def _toggle_order(self, *_):
        mp = self.mpris_player
        if not mp: return

        if self._is_local:
            mp.order_mode = _ORDER_NEXT_3.get(mp._order_mode, "normal")
            mp.emit("changed")
            return

        if isinstance(mp, MprisPlayer) and not getattr(mp, '_dead', False) and not getattr(mp, 'is_limited', False):
            can_sh = getattr(mp, 'can_shuffle', False)
            nxt = (_ORDER_NEXT_3 if can_sh else _ORDER_NEXT_2).get(self._local_order, "normal")
            if can_sh and (self._local_order == "shuffle") != (nxt == "shuffle"):
                GLib.idle_add(lambda: setattr(mp, 'shuffle', nxt == "shuffle") or False)
            self._local_order = nxt
            self._refresh()

    def _toggle_repeat(self, *_):
        mp = self.mpris_player
        if not mp: return

        if self._is_local:
            mp.loop_status = _REPEAT_NEXT.get(mp._loop_status, "None")
            mp.emit("changed")
            return

        if isinstance(mp, MprisPlayer) and not getattr(mp, '_dead', False) and getattr(mp, 'can_set_loop_status', False):
            new_ls = _REPEAT_NEXT.get(getattr(mp, "loop_status", "None"), "None")
            GLib.idle_add(lambda: setattr(mp, 'loop_status', new_ls) or False)

    def _refresh(self):
        mp = self.mpris_player
        if not mp:
            return
        if getattr(mp, '_dead', False):
            return self._setup_empty()

        if isinstance(mp, MprisPlayer) and getattr(mp, 'can_shuffle', False):
            mpris_sh = getattr(mp, "shuffle", False)
            if mpris_sh and self._local_order != "shuffle": self._local_order = "shuffle"
            elif not mpris_sh and self._local_order == "shuffle": self._local_order = "normal"

        _set_label(self.title, mp.title)
        _set_label(self.album, mp.album)
        _set_label(self.artist, mp.artist)
        
        self._ucover(getattr(mp, 'url', '') or getattr(mp, 'title', ''), getattr(mp, 'arturl', ''))

        playing = getattr(mp, "playback_status", "") == "playing"
        self.play_pause.get_child().set_markup(icons.pause if playing else icons.play)
        _set_style(self.play_pause, "playing", playing)
        
        self._check_spin_state()
        self._umode(mp)
        self._ubtn(mp)
        self._update_prog_state(mp)

    def _umode(self, mp):
        sb, rb = self.shuffle_btn, self.repeat_btn
        is_mpris, ltd = isinstance(mp, MprisPlayer), getattr(mp, 'is_limited', False)

        if is_mpris and ltd:
            sb.get_child().set_markup(icons.shuffle); sb.set_tooltip_text("Not available")
            _set_style(sb, "disabled", True); _set_style(sb, "active", False)
        else:
            om = self._get_order()
            sb.get_child().set_markup(icons.shuffle if om == "shuffle" else icons.reverse_order)
            sb.set_tooltip_text("Shuffle" if om == "shuffle" else ("Reverse" if om == "reverse" else "Order"))
            _set_style(sb, "disabled", False); _set_style(sb, "active", om != "normal")

        if not (not is_mpris or (getattr(mp, 'can_set_loop_status', False) and not ltd)):
            rb.get_child().set_markup(icons.repeat); rb.set_tooltip_text("Not available")
            _set_style(rb, "disabled", True); _set_style(rb, "active", False)
        else:
            ls = getattr(mp, "loop_status", "None")
            rb.get_child().set_markup(_REPEAT_ONCE if ls == "Track" else icons.repeat)
            rb.set_tooltip_text("Repeat Track" if ls == "Track" else ("Repeat All" if ls == "Playlist" else "Repeat"))
            _set_style(rb, "disabled", False); _set_style(rb, "active", ls != "None")

    def _ubtn(self, mp):
        csk, st = getattr(mp, "can_seek", False), getattr(mp, "playback_status", "stopped")
        _set_style(self.backward, "disabled", st == "stopped" or not csk)
        _set_style(self.forward, "disabled", st == "stopped" or not csk)

        if self._is_local:
            cp = cn = st in ("playing", "paused")
        else:
            cp, cn = getattr(mp, "can_go_previous", True), getattr(mp, "can_go_next", True)

        _set_style(self.prev, "disabled", not cp)
        _set_style(self.next, "disabled", not cn)
        _set_style(self.play_pause, "disabled", not getattr(mp, "can_play", True) and not getattr(mp, "can_pause", True))

    def _on_changed(self, *_):
        if not self._upd:
            self._upd = True
            GLib.idle_add(self._flush)

    def _flush(self):
        self._upd = False
        if self.mpris_player: self._refresh()
        return False

    def cleanup(self):
        self._spinning = False
        self._flick_v = 0.0
        if self._anim_id is not None: GLib.source_remove(self._anim_id); self._anim_id = None
        self._v_stop_scroll()
        self._prog_stop()
        self._cancel_anim()
        if self._dcancel: self._dcancel.cancel(); self._dcancel = None
        if self.mpris_player and self._sig_id:
            try: self.mpris_player.disconnect(self._sig_id)
            except Exception: pass
        self.mpris_player = None


class MediaPlayer(Box):
    __slots__ = ('player_stack', 'switcher', 'mpris_manager', 'player_overlay', 'local_player', '_hc_id', '_states', '_repl')

    def __init__(self, local_player=None):
        super().__init__(name="player", orientation="v", h_align="fill", v_align="fill", spacing=0, h_expand=True, v_expand=False)
        self.local_player = local_player
        self._hc_id = None
        self._states = {}
        self._repl = False

        self.player_stack = Stack(name="player-stack", transition_type="slide-left-right", transition_duration=500, h_align="fill", v_align="fill", h_expand=True, v_expand=True)
        self.switcher = Gtk.StackSwitcher(name="player-switcher", spacing=8, stack=self.player_stack)
        self.switcher.set_halign(Gtk.Align.CENTER)
        self.switcher.set_valign(Gtk.Align.END)
        self.switcher.set_hexpand(True)
        self.switcher.set_margin_bottom(16)
        self.switcher.set_margin_start(15)

        self.mpris_manager = mgr = MprisPlayerManager()

        if local_player:
            self.player_stack.add_titled(PlayerBox(mpris_player=local_player), local_player.player_name, local_player.player_name)

        if mgr.players:
            for p in mgr.players: self._add(p)
        elif not local_player:
            self.player_stack.add_titled(PlayerBox(), "nothing", _DEFAULT_TITLE)

        mgr.connect("player-appeared", lambda _, p: [self._add(p), self._schedule_icons()])
        mgr.connect("player-vanished", self._on_vanish)

        self.add(Overlay(child=self.player_stack, overlays=(self.switcher,), h_expand=True, v_expand=True, h_align="fill", v_align="fill"))
        self._schedule_icons()
        self._hc_id = GLib.timeout_add(1000, self._health)

    def _add(self, player):
        mp = MprisPlayer(player)
        iid = _mpris_id(mp)
        if iid in {self.player_stack.child_get_property(c, "name") for c in self.player_stack.get_children()}:
            iid = f"{iid}_{id(mp)}"
        pb = PlayerBox(mpris_player=mp)
        self.player_stack.add_titled(pb, iid, mp.player_name)
        pb._exit_sig_id = mp.connect("exit", lambda mp, pb: GLib.idle_add(self._remove, pb), pb)
        return pb

    def _remove(self, pb):
        if pb not in self.player_stack.get_children(): return False
        self._states.pop(self.player_stack.child_get_property(pb, "name"), None)
        mp = getattr(pb, 'mpris_player', None)
        if mp:
            if getattr(pb, '_exit_sig_id', None):
                try: mp.disconnect(pb._exit_sig_id)
                except Exception: pass
            if isinstance(mp, MprisPlayer): mp._mark_dead()
        pb.cleanup()
        self.player_stack.remove(pb)
        self._schedule_icons()
        return False

    def switch_to_local(self):
        if self.local_player: self.player_stack.set_visible_child_name(self.local_player.player_name)

    def _on_vanish(self, _mgr, vid):
        for c in self.player_stack.get_children():
            mp = getattr(c, "mpris_player", None)
            if mp and not isinstance(mp, LocalPlayer) and vid in (getattr(mp, "player_instance", ""), getattr(mp, "player_name", "")):
                self._remove(c)
                break
        self._schedule_icons()

    def _health(self):
        drop, active, nothing, goto = [], 0, None, None
        for ch in self.player_stack.get_children():
            nm = self.player_stack.child_get_property(ch, "name")
            if nm == "nothing":
                nothing = ch
                continue
            mp = getattr(ch, 'mpris_player', None)
            if not mp: continue
            
            st = getattr(mp, 'playback_status', 'stopped')
            if isinstance(mp, LocalPlayer):
                active += 1
                if st == "playing" and self._states.get(nm) != "playing": goto = nm
                self._states[nm] = st
            elif isinstance(mp, MprisPlayer):
                if mp.is_dead: drop.append(ch)
                elif st == "stopped" and not mp.title.strip(): ch.hide(); self._states[nm] = "ghost"
                else:
                    ch.show()
                    active += 1
                    if st == "playing" and self._states.get(nm) != "playing": goto = nm
                    self._states[nm] = st

        for ch in drop: self._remove(ch)
        if goto: self.player_stack.set_visible_child_name(goto)

        if active == 0:
            if not nothing and not self.local_player:
                nothing = PlayerBox()
                self.player_stack.add_titled(nothing, "nothing", _DEFAULT_TITLE)
            if nothing:
                nothing.show()
                self.player_stack.set_visible_child_name("nothing")
        elif nothing: nothing.hide()

        cur = self.player_stack.get_visible_child()
        if cur and not cur.get_visible() and active > 0:
            for c in self.player_stack.get_children():
                if c.get_visible() and self.player_stack.child_get_property(c, "name") != "nothing":
                    self.player_stack.set_visible_child_name(self.player_stack.child_get_property(c, "name"))
                    break

        self._schedule_icons()
        return True

    def _schedule_icons(self):
        if not self._repl:
            self._repl = True
            GLib.idle_add(self._apply_icons)

    def _apply_icons(self):
        self._repl = False
        for btn in self.switcher.get_children():
            if isinstance(btn, Gtk.ToggleButton) and btn.get_visible():
                for c in btn.get_children():
                    if isinstance(c, Gtk.Label) and c.get_text() != icons.disc:
                        btn.remove(c)
                        lbl = Label(name="player-label", markup=icons.disc)
                        btn.add(lbl)
                        lbl.show_all()
                        break
        return False

    def cleanup(self):
        if self._hc_id: GLib.source_remove(self._hc_id); self._hc_id = None
        for c in self.player_stack.get_children():
            if hasattr(c, 'cleanup'): c.cleanup()
        self.mpris_manager = None