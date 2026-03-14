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

_PNG_SIG = b'\x89PNG\r\n\x1a\n'
_JPEG_SIG = b'\xff\xd8'
_GIF_SIGS = (b'GIF87a', b'GIF89a')
_BMP_SIG = b'BM'
_RIFF_SIG = b'RIFF'
_WEBP_SIG = b'WEBP'

_HOVER_MASK = Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
_SCROLL_MASK = Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK
_COVER_EVENTS = _HOVER_MASK | _SCROLL_MASK
_SCROLL_THRESHOLD = 5.0

# Оригинальные тайминги и скорости FPS
_ANIM_MS = 16 
_SPIN_STEP = 0.003 
_FLICK_INITIAL = 0.12
_FLICK_DECAY = 0.88
_FLICK_MIN = 0.001
_SNAP_FACTOR = 0.18
_SNAP_EPSILON = 0.005
_TAU = math.tau

_PROG_FPS = 16 
_SYNC_MS = 500

_OVERSHOOT_MIN = 0.035
_OVERSHOOT_MAX = 0.15

_SK_DUR_1, _SK_POW_1 = 0.32, 4.5
_SK_DUR_2, _SK_POW_2 = 0.24, 2.5
_SK_SCALE_MD, _SK_SCALE_LG = 1.3, 1.6

_SW_DUR_BASE, _SW_DUR_SCALE, _SW_POW = 0.25, 0.65, 2.0
_RISE_DUR, _RISE_POW, _RISE_MIN = 0.45, 5.0, 0.008
_CORR_THRESH, _CORR_DUR, _CORR_POW = 0.012, 0.20, 2.5

_V_ART_LINES = (
    "@№@        @@/    \\@@##    /@@@@@@@@\\         /@@@@@@*@@@@/   /@@@№№@@@@@/    @@@      /@|",
    " @@\\      @№|      @@@     @@@@###@@@\\      /@!58@@@@@@@/     @@@@#=@@@/      |@@\\     @@|",
    " @@#      #@/      @@@     @@@     \\@@\\    /@<@          /    @!?               \\@\\  *@/",
    "  @#@    @#|       |@|     @&@      @@@    @&@          /@    @@@@#@@@@@/        |&#@@/",
    "  @№\\    |@/       |#@     @@?      #@@    *!?         /@@    @@##@@@/           |#@@/",
    "   @\\@  /#|        |#@     @@@    /#@@/    @>@@       /@*@    @&&               /@№  @@\\",
    "    @@##@/         #@@     @@@@@@/@@@/      @@@@&@?@@@@@/     @@@&?\"@@@/      /@#     @!@\\",
    "     @@@/         #@@@\\    \\@@@>-@@@/         \\@,,@@@@@/      \\@@@@@@@@@@/    /@@      @@|",
)
_V_MAX_W = max(len(l) for l in _V_ART_LINES)
_V_ART = '\n'.join(l.ljust(_V_MAX_W) for l in _V_ART_LINES)

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
_GL_CHARS = tuple("░▒▓█▀▄▌▐@#$%&!?*=~")

os.makedirs(_CACHE_DIR, exist_ok=True)

def _cleanup_cache():
    try:
        cutoff = _time.time() - 86_400
        for e in os.scandir(_CACHE_DIR):
            if e.is_file() and e.stat().st_mtime < cutoff:
                os.remove(e.path)
    except Exception:
        pass

threading.Thread(target=_cleanup_cache, daemon=True).start()

_cursor_cache = {}

def _on_hover_enter(w, _e):
    if win := w.get_window():
        dsp = w.get_display()
        if dsp not in _cursor_cache:
            _cursor_cache[dsp] = Gdk.Cursor.new_from_name(dsp, "pointer")
        win.set_cursor(_cursor_cache[dsp])

def _on_hover_leave(w, _e):
    if win := w.get_window():
        win.set_cursor(None)

def _hover(w):
    w.add_events(_HOVER_MASK)
    w.connect("enter-notify-event", _on_hover_enter)
    w.connect("leave-notify-event", _on_hover_leave)

def _fex(p):
    return bool(p) and os.path.isfile(p)

def _ext(p):
    return os.path.splitext(p.split('?', 1)[0])[1] if p else ""

def _set_style(w, cls, add):
    (w.add_style_class if add else w.remove_style_class)(cls)

def _set_label(lbl, txt):
    ok = bool(txt and not txt.isspace())
    lbl.set_text(txt if ok else " ")
    lbl.set_opacity(1.0 if ok else 0.0)

def _is_valid_image(data):
    if len(data) < 8: return False
    h = data[:8]
    return h == _PNG_SIG or h[:2] == _JPEG_SIG or h[:2] == _BMP_SIG or h[:6] in _GIF_SIGS or (h[:4] == _RIFF_SIG and len(data) >= 12 and data[8:12] == _WEBP_SIG)

def _load_mode():
    try:
        if os.path.isfile(_MODE_FILE):
            with open(_MODE_FILE) as f:
                d = json.load(f)
            return d.get("loop_status", "None"), d.get("order_mode", "normal")
    except Exception: pass
    return "None", "normal"

def _save_mode(ls, om):
    try:
        with open(_MODE_FILE, "w") as f:
            json.dump({"loop_status": ls, "order_mode": om}, f)
    except Exception: pass

def _mpris_id(mp):
    return getattr(mp, "player_instance", None) or getattr(mp, "player_name", None) or f"player_{id(mp)}"

def _fmt_time(us):
    s = max(0, int(us)) // 1_000_000
    return f"{s // 60}:{s % 60:02}"

def _ease_out(t, power):
    return 1.0 - (1.0 - t) ** power

def _clamp01(v):
    return max(0.0, min(1.0, v))


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
        self.title, self.artist, self.album, self.arturl = _DEFAULT_TITLE, _DEFAULT_ARTIST, _DEFAULT_ALBUM, ""
        self.playback_status = "stopped"
        self.length = 0
        self.can_seek = self.can_go_next = self.can_go_previous = False
        self.can_pause = True
        self.on_next_cb = self.on_prev_cb = None

        self._loop_status, self._order_mode = _load_mode()
        self._playbin = Gst.ElementFactory.make("playbin", "local_playbin")
        if self._playbin:
            bus = self._playbin.get_bus()
            bus.add_signal_watch()
            bus.connect("message::eos", self._on_eos)

    @Property(str, "read-write", default_value="None")
    def loop_status(self): return self._loop_status

    @loop_status.setter
    def loop_status(self, v):
        self._loop_status = v
        _save_mode(v, self._order_mode)

    @Property(str, "read-write", default_value="normal")
    def order_mode(self): return self._order_mode

    @order_mode.setter
    def order_mode(self, v):
        self._order_mode = v
        _save_mode(self._loop_status, v)

    @Property(bool, "read-write", default_value=False)
    def shuffle(self): return self._order_mode == "shuffle"

    @shuffle.setter
    def shuffle(self, v):
        self._order_mode = "shuffle" if v else "normal"
        _save_mode(self._loop_status, self._order_mode)

    @property
    def position(self):
        if self._playbin and self.playback_status in ("playing", "paused"):
            ok, pos = self._playbin.query_position(Gst.Format.TIME)
            if ok: return pos // 1000
        return 0

    def play_file(self, path, artist, title, album, length_us, art_url):
        if not self._playbin: return
        self._playbin.set_state(Gst.State.NULL)
        self._playbin.set_property("uri", GLib.filename_to_uri(path, None))
        self.title, self.artist, self.album, self.arturl = title, artist, album or "", art_url
        self.length = length_us
        self.playback_status = "playing"
        self.can_seek = True
        self._playbin.set_state(Gst.State.PLAYING)
        self.emit("changed")

    def stop(self):
        if self._playbin: self._playbin.set_state(Gst.State.NULL)
        self.title, self.artist, self.album, self.arturl = _DEFAULT_TITLE, _DEFAULT_ARTIST, _DEFAULT_ALBUM, ""
        self.length, self.playback_status = 0, "stopped"
        self.can_seek = self.can_go_next = self.can_go_previous = False
        self.emit("changed")

    def play_pause(self):
        if not self._playbin: return
        self.playback_status = "paused" if self.playback_status == "playing" else "playing"
        self._playbin.set_state(Gst.State.PAUSED if self.playback_status == "paused" else Gst.State.PLAYING)
        self.emit("changed")

    def next(self): (self.on_next_cb or (lambda: self.emit("next_requested")))()
    def previous(self): (self.on_prev_cb or (lambda: self.emit("previous_requested")))()

    def _on_eos(self, _bus, _msg):
        GLib.idle_add(self._replay if self._loop_status == "Track" else self.previous if self._order_mode == "reverse" else self.next)

    def _replay(self):
        if self._playbin: self._playbin.seek_simple(Gst.Format.TIME, _SEEK_FLAGS, 0)


class PlayerBox(Box):
    __slots__ = (
        'mpris_player', '_sig_id', '_exit_sig_id', 'cover', 'cover_placeholder', '_cover_box',
        'title', 'album', 'artist', 'progressbar', 'time', 'prev', 'backward', 'play_pause', 'forward', 'next',
        'shuffle_btn', 'repeat_btn', 'mode_box', 'btn_box', 'info_box', 'player_box', 'overlay_container',
        '_angle', '_anim_id', '_spinning', '_flick_v', '_snapping', '_last_art', '_extract_tried', '_is_wall',
        '_dcancel', '_upd', '_scroll_acc', '_local_order', '_v_offset', '_v_scroll_id',
        '_g_on', '_g_rem', '_g_cd', '_g_shifts', '_g_split', '_pv', '_ptimer', '_stimer',
        '_kpos', '_ktime', '_klen', '_kplay', '_tkey', '_last_time_txt',
        '_a_active', '_a_t0', '_a_from', '_a_to', '_a_dur', '_a_pow', '_a_chain', '_a_done',
        '_v_cached_layout', '_v_cached_size', '_v_font_desc', '_v_gap_w', '_v_line_h'
    )

    def __init__(self, mpris_player=None):
        super().__init__(orientation="h", h_align="fill", v_align="fill", spacing=0, h_expand=True, v_expand=True)
        self.mpris_player = mpris_player

        self._sig_id = self._exit_sig_id = self._anim_id = self._dcancel = None
        self._upd = self._extract_tried = self._spinning = self._snapping = self._g_on = self._a_active = self._kplay = False
        self._is_wall = True
        self._angle = self._flick_v = self._scroll_acc = self._v_offset = self._g_split = self._pv = self._a_t0 = self._a_from = self._a_to = self._ktime = 0.0
        self._g_rem = self._g_cd = self._kpos = self._klen = 0
        self._local_order, self._a_done = "normal", 'live'
        self._last_art = self._v_scroll_id = self._ptimer = self._stimer = self._tkey = None
        self._g_shifts, self._a_chain = [], []
        self._last_time_txt = ""
        self._a_dur, self._a_pow = 0.3, 4.0

        self._v_cached_layout = None
        self._v_cached_size = None
        self._v_font_desc = None
        self._v_gap_w = 0
        self._v_line_h = 0

        self.cover = CircleImage(name="player-cover", size=_COVER_SIZE, h_align="center", v_align="center")
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

        self.cover_placeholder = CircleImage(name="player-cover", size=_PROG_SIZE, h_align="center", v_align="center")

        lkw = dict(h_expand=False, h_align="center", ellipsization="end", max_chars_width=20, justify=Gtk.Justification.CENTER)
        self.title = Label(name="player-title", **lkw)
        self.album = Label(name="player-album", **lkw)
        self.artist = Label(name="player-artist", **lkw)
        for lb in (self.title, self.album, self.artist): lb.set_size_request(-1, _LBL_H)
        self.title.set_label(_DEFAULT_TITLE)
        self.album.set_label(_DEFAULT_ALBUM)
        self.artist.set_label(_DEFAULT_ARTIST)

        self.progressbar = CircularProgressBar(name="player-progress", size=_PROG_SIZE, h_align="center", v_align="center", start_angle=180, end_angle=360)
        self.time = Label(name="player-time", label=_NO_TIME)

        self.overlay_container = Box(name="player-overlay", orientation="v", h_expand=True, v_expand=True, h_align="center", v_align="center",
                                     children=(Overlay(child=self.cover_placeholder, overlays=(self.progressbar, self._cover_box)),))
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

        for b, s in ((self.prev, _BTN_SIZE), (self.backward, _BTN_SIZE), (self.play_pause, _PLAY_SIZE),
                     (self.forward, _BTN_SIZE), (self.next, _BTN_SIZE), (self.shuffle_btn, _BTN_SIZE), (self.repeat_btn, _BTN_SIZE)):
            b.set_size_request(s, s)

        self.mode_box = Box(name="player-mode-box", orientation="h", spacing=8, h_expand=False, v_expand=False, h_align="center", v_align="center", children=(self.shuffle_btn, self.repeat_btn))
        self.btn_box = Box(name="player-btn-box", orientation="h", spacing=4, h_expand=False, v_expand=False, h_align="center", v_align="center", children=(self.prev, self.backward, self.play_pause, self.forward, self.next))
        self.info_box = Box(name="player-info-box", orientation="v", spacing=4, h_expand=True, v_expand=True, h_align="center", v_align="center", children=(self.title, self.album, self.artist, self.btn_box, self.time, self.mode_box))
        self.info_box.set_size_request(200, -1)

        self.player_box = Box(name="player-box", orientation="h", spacing=0, h_expand=True, v_expand=True, h_align="fill", v_align="fill", homogeneous=True, children=(self.overlay_container, self.info_box))
        self.add(self.player_box)

        if mpris_player: self._wire(); self._prog_start()
        else: self._setup_empty()
        if self._is_wall: self._v_start_scroll()

    @staticmethod
    def _btn(icon, sc=()):
        b = Button(name="player-btn", child=Label(name="player-btn-label", markup=icon, style_classes=sc), style_classes=sc, h_expand=False, v_expand=False, h_align="center", v_align="center")
        _hover(b)
        return b

    def _get_order(self): return self.mpris_player._order_mode if isinstance(self.mpris_player, LocalPlayer) else self._local_order
    def _is_reversed(self): return self._get_order() == "reverse"

    def _on_cover_draw(self, w, cr):
        if self._is_wall:
            alloc = w.get_allocation()
            self._draw_v(cr, alloc.width, alloc.height)
            return True

        child = w.get_child()
        if not child or not child.get_visible(): return True
        
        a = self._angle
        if a:
            alloc = w.get_allocation()
            cx, cy = alloc.width * 0.5, alloc.height * 0.5
            cr.save()
            cr.translate(cx, cy); cr.rotate(a); cr.translate(-cx, -cy)
            w.propagate_draw(child, cr)
            cr.restore()
        else:
            w.propagate_draw(child, cr)
        return True

    def _draw_v(self, cr, w, h):
        rgba = self.artist.get_style_context().get_color(Gtk.StateFlags.NORMAL)
        r, g, b, a = (0.55, 0.55, 0.55, 1.0) if rgba.alpha < 0.01 else (rgba.red, rgba.green, rgba.blue, rgba.alpha)

        if self._v_cached_size != (w, h):
            layout = PangoCairo.create_layout(cr)
            font = Pango.FontDescription.from_string("monospace bold")
            max_h = h * (1.0 - 2.0 * 0.08)
            best = 4
            layout.set_text(_V_ART, -1)
            for pt in range(4, 40):
                font.set_size(pt * Pango.SCALE)
                layout.set_font_description(font)
                if layout.get_pixel_size()[1] <= max_h: best = pt
                else: break

            font.set_size(best * Pango.SCALE)
            layout.set_font_description(font)
            layout.set_text(_V_ART, -1)
            
            gap_layout = PangoCairo.create_layout(cr)
            gap_layout.set_font_description(font)
            gap_layout.set_text(" " * _V_GAP, -1)
            
            self._v_cached_size = (w, h)
            self._v_cached_layout = layout
            self._v_font_desc = font
            self._v_gap_w = gap_layout.get_pixel_size()[0]
            self._v_line_h = layout.get_pixel_size()[1] / len(_V_ART_LINES)

        layout = self._v_cached_layout
        lw, lh = layout.get_pixel_size()
        block = lw + self._v_gap_w
        off = self._v_offset % block if block > 0 else 0.0
        y0 = (h - lh) * 0.5

        cr.save()
        cr.arc(w * 0.5, h * 0.5, min(w, h) * 0.5, 0, _TAU)
        cr.clip()

        if self._g_on and self._g_shifts:
            self._draw_glitch(cr, w, h, r, g, b, a, block, off, y0)
        else:
            cr.set_source_rgba(r, g, b, a)
            x = -off
            while x < w:
                cr.move_to(x, y0)
                PangoCairo.show_layout(cr, layout)
                x += block

        cr.restore()

    def _draw_glitch(self, cr, w, h, r, g, b, a, block, off, y0):
        shifts = self._g_shifts
        split = self._g_split
        ll = PangoCairo.create_layout(cr)
        ll.set_font_description(self._v_font_desc)

        for i, line in enumerate(_V_ART_LINES):
            if random.random() < _GL_FLICKER: continue
            shift = shifts[i] if i < len(shifts) else 0
            ly = y0 + i * self._v_line_h
            txt = line.ljust(_V_MAX_W)
            
            if abs(shift) > 3:
                txt_list = list(txt)
                for j, c in enumerate(txt_list):
                    if c != ' ' and random.random() < _GL_CORRUPT: txt_list[j] = random.choice(_GL_CHARS)
                txt = "".join(txt_list)
                
            ll.set_text(txt, -1)
            x = -off + shift
            while x < w:
                if split > 0.5 and shift != 0:
                    cr.set_source_rgba(min(1.0, r + 0.4), g * 0.15, b * 0.15, 0.55)
                    cr.move_to(x - split, ly); PangoCairo.show_layout(cr, ll)
                    cr.set_source_rgba(r * 0.15, g * 0.15, min(1.0, b + 0.4), 0.55)
                    cr.move_to(x + split, ly); PangoCairo.show_layout(cr, ll)
                cr.set_source_rgba(r, g, b, a)
                cr.move_to(x, ly); PangoCairo.show_layout(cr, ll)
                x += block

        if random.random() < _GL_BAR:
            cr.set_source_rgba(r, g, b, random.uniform(0.08, 0.25))
            cr.rectangle(0, random.uniform(0, h), w, random.uniform(2, 8))
            cr.fill()

    def _v_start_scroll(self):
        if not self._v_scroll_id: self._v_scroll_id = GLib.timeout_add(_ANIM_MS, self._v_scroll_tick)

    def _v_stop_scroll(self):
        if self._v_scroll_id: GLib.source_remove(self._v_scroll_id); self._v_scroll_id = None

    def _v_scroll_tick(self):
        if self._g_on:
            self._v_offset += _V_SCROLL_SPEED * random.uniform(*_GL_SPEED)
            self._g_rem -= 1
            if self._g_rem <= 0:
                self._g_on = False; self._g_cd = _GL_CD; self._g_shifts = []
            else:
                self._g_shifts = [random.randint(-_GL_SHIFT_MAX, _GL_SHIFT_MAX) if random.random() < 0.35 else 0 for _ in range(len(_V_ART_LINES))]
                self._g_split = random.uniform(0, _GL_SPLIT_MAX)
        else:
            self._v_offset += _V_SCROLL_SPEED
            if self._g_cd > 0: self._g_cd -= 1
            elif random.random() < _GL_CHANCE:
                self._g_on, self._g_rem = True, random.randint(*_GL_DUR)

        self._cover_box.queue_draw()
        return True

    def _ensure_anim(self):
        if self._anim_id is None: self._anim_id = GLib.timeout_add(_ANIM_MS, self._anim_tick)

    def _anim_tick(self):
        if self._snapping:
            if self._angle <= math.pi:
                self._angle *= 1.0 - _SNAP_FACTOR
                if self._angle < _SNAP_EPSILON: self._snapping = False; self._angle = 0.0; self._maybe_spin()
            else:
                self._angle += (_TAU - self._angle) * _SNAP_FACTOR
                if self._angle >= _TAU - _SNAP_EPSILON: self._snapping = False; self._angle = 0.0; self._maybe_spin()
        else:
            if self._spinning: self._angle += _SPIN_STEP
            if self._flick_v:
                self._angle += self._flick_v
                self._flick_v *= _FLICK_DECAY
                if abs(self._flick_v) < _FLICK_MIN: self._flick_v = 0.0
            self._angle %= _TAU

        self._cover_box.queue_draw()
        if not (self._spinning or self._snapping or self._flick_v):
            self._anim_id = None
            return False
        return True

    def _spin_on(self): self._spinning = True; self._ensure_anim()
    def _spin_off(self): self._spinning = False
    def _flick(self, d): self._snapping, self._flick_v = False, _FLICK_INITIAL * d; self._ensure_anim()
    def _snap(self):
        self._spinning, self._flick_v = False, 0.0
        if self._angle == 0.0: self._maybe_spin()
        else: self._snapping = True; self._ensure_anim()

    def _anim_off(self):
        self._spinning = self._snapping = False; self._flick_v = 0.0
        if self._anim_id: GLib.source_remove(self._anim_id); self._anim_id = None

    def _maybe_spin(self):
        if self.mpris_player and getattr(self.mpris_player, "playback_status", "") == "playing" and not self._is_wall: self._spin_on()

    def _on_scroll(self, _w, ev):
        d = ev.direction
        if d == Gdk.ScrollDirection.UP: self._seek(1); return True
        if d == Gdk.ScrollDirection.DOWN: self._seek(-1); return True
        ok, _dx, dy = ev.get_scroll_deltas()
        if ok and dy:
            self._scroll_acc += dy
            if self._scroll_acc <= -_SCROLL_THRESHOLD: self._seek(1); self._scroll_acc = 0.0
            elif self._scroll_acc >= _SCROLL_THRESHOLD: self._seek(-1); self._scroll_acc = 0.0
        return True

    def _on_cover_click(self, _w, ev):
        if ev.button == 1 and self.mpris_player: self.mpris_player.play_pause()
        return True

    def _ucover(self, arturl):
        if arturl == self._last_art: return
        self._last_art = arturl
        self._extract_tried = False
        self._angle, self._scroll_acc = 0.0, 0.0
        self._anim_off()

        if not arturl: self._try_extract(); return
        scheme = GLib.uri_parse_scheme(arturl)
        if scheme == "file": self._set_img(GLib.uri_unescape_string(arturl[7:], None))
        elif scheme in ("http", "https"): self._dl_art(arturl)
        elif arturl.startswith('/'): self._set_img(arturl)
        else: self._placeholder()

    def _try_extract(self):
        if self._extract_tried: self._placeholder(); return
        self._extract_tried = True
        url = getattr(self.mpris_player, 'url', '') if isinstance(self.mpris_player, MprisPlayer) else ''
        if url and url.startswith("file://"):
            path = GLib.uri_unescape_string(url[7:], None)
            if path and os.path.isfile(path): threading.Thread(target=self._extract_bg, args=(path,), daemon=True).start(); return
        self._placeholder()

    def _extract_bg(self, path):
        try:
            h = hashlib.md5(path.encode()).hexdigest()
            cp = os.path.join(_CACHE_DIR, f"ex_{h}.png")
            if _fex(cp): GLib.idle_add(self._set_img, cp); return
            af = MutagenFile(path)
            if af:
                data = self._cover_bytes(af)
                del af 
                if data:
                    with open(cp, 'wb') as f: f.write(data)
                    del data
                    GLib.idle_add(self._set_img, cp); return
        except Exception: pass
        GLib.idle_add(self._placeholder)

    @staticmethod
    def _cover_bytes(audio):
        if tags := getattr(audio, 'tags', None):
            for k, v in tags.items():
                if k.startswith('APIC'): return v.data
        if pics := getattr(audio, 'pictures', None): return pics[0].data
        if covr := audio.get('covr'): return bytes(covr[0])
        return None

    def _placeholder(self):
        self._anim_off()
        self._angle, self._v_offset, self._g_split = 0.0, 0.0, 0.0
        self._is_wall = True
        self._g_on, self._g_rem, self._g_cd, self._g_shifts = False, 0, 0, []
        self._v_start_scroll()
        self._cover_box.queue_draw()

    def _set_img(self, p):
        if _fex(p):
            self.cover.set_image_from_file(p)
            self._is_wall = False
            self._v_stop_scroll()
            self._cover_box.queue_draw()
        else: self._placeholder()

    def _dl_art(self, url):
        h = hashlib.md5(url.encode()).hexdigest()
        ext = _ext(url) or '.png'
        if ext.lower() not in _VALID_COVER_EXT: ext = '.png'
        cp = os.path.join(_CACHE_DIR, f"{h}{ext}")
        if _fex(cp): self._set_img(cp); return
        if self._dcancel: self._dcancel.cancel()
        self._dcancel = Gio.Cancellable.new()
        Gio.File.new_for_uri(url).load_contents_async(self._dcancel, self._on_dl, cp)

    def _on_dl(self, f, res, cp):
        try:
            ok, data, _ = f.load_contents_finish(res)
            if ok and data and len(data) <= _MAX_IMG_SIZE and _is_valid_image(data):
                Gio.File.new_for_path(cp).replace_contents_bytes_async(GLib.Bytes.new(data), None, False, Gio.FileCreateFlags.PRIVATE, self._dcancel, self._on_dl_save, cp)
                return
        except GLib.Error: pass
        GLib.idle_add(self._try_extract)

    def _on_dl_save(self, gf, res, cp):
        try:
            if gf.replace_contents_finish(res)[0]: GLib.idle_add(self._set_img, cp); return
        except GLib.Error: pass
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
        if self.mpris_player: self.mpris_player.next() if self._is_reversed() else self.mpris_player.previous()
    def _do_next(self):
        if self.mpris_player: self.mpris_player.previous() if self._is_reversed() else self.mpris_player.next()

    def _setup_empty(self):
        self._anim_off()
        self._angle = 0.0
        self._cover_box.queue_draw()
        self.play_pause.get_child().set_markup(icons.stop)
        self.play_pause.add_style_class("stop")
        for b in (self.backward, self.forward, self.prev, self.next, self.shuffle_btn, self.repeat_btn): b.add_style_class("disabled")
        self.progressbar.set_value(0.0)
        self.time.set_text(_NO_TIME)
        self._pv, self._a_active, self._a_chain, self._kpos, self._klen, self._kplay, self._tkey, self._last_time_txt = 0.0, False, [], 0, 0, False, None, ""

    def _begin_seg(self, to, dur, power):
        self._a_active, self._a_t0, self._a_from, self._a_to, self._a_dur, self._a_pow = True, _time.monotonic(), self._pv, _clamp01(to), max(0.016, dur), power

    def _run_chain(self, segments, on_done='live'):
        if not segments: return
        self._a_chain, self._a_done = list(segments[1:]), on_done
        first = segments[0]
        self._begin_seg(first[0], first[1], first[2])

    def _cancel_anim(self): self._a_active, self._a_chain = False, []

    def _prog_start(self):
        if not self._ptimer: self._ptimer = GLib.timeout_add(_PROG_FPS, self._prog_tick)
        if not self._stimer: self._stimer = GLib.timeout_add(_SYNC_MS, self._sync_tick)
        self._force_sync()

    def _prog_stop(self):
        for attr in ('_ptimer', '_stimer'):
            if tid := getattr(self, attr, None): GLib.source_remove(tid); setattr(self, attr, None)

    def _force_sync(self):
        if mp := self.mpris_player:
            self._kpos, self._ktime, self._klen, self._kplay = getattr(mp, 'position', 0) or 0, _time.monotonic(), getattr(mp, 'length', 0) or 0, getattr(mp, 'playback_status', '') == 'playing'

    def _sync_tick(self):
        mp = self.mpris_player
        if not mp or getattr(mp, '_dead', False): self._stimer = None; return False
        self._kplay, self._klen = getattr(mp, 'playback_status', '') == 'playing', getattr(mp, 'length', 0) or 0
        if not self._a_active: self._kpos, self._ktime = getattr(mp, 'position', 0) or 0, _time.monotonic()
        return True

    def _prog_tick(self):
        mp = self.mpris_player
        if not mp or getattr(mp, '_dead', False): self._ptimer = None; return False
        tot = self._klen
        if tot <= 0:
            if self._pv != 0.0: self._pv = 0.0; self.progressbar.set_value(0.0); self.progressbar.queue_draw()
            self._set_time_text(_NO_TIME); return True
        
        now = _time.monotonic()
        if self._a_active:
            t = min(1.0, (now - self._a_t0) / self._a_dur)
            self._pv = self._a_from + (self._a_to - self._a_from) * _ease_out(t, self._a_pow)
            if t >= 1.0:
                self._pv = self._a_to
                if self._a_chain: seg = self._a_chain.pop(0); self._begin_seg(*seg)
                else:
                    self._a_active = False
                    if self._a_done == 'rise':
                        self._force_sync()
                        if self._klen > 0 and self._kpos > 0:
                            tgt = _clamp01(self._kpos / self._klen)
                            if tgt > _RISE_MIN: self._run_chain([(tgt, _RISE_DUR, _RISE_POW)], on_done='live')
                            else: self._anchor_at(now, tot)
                        else: self._anchor_at(now, tot)
                    else: self._anchor_at(now, tot)
        else:
            target = _clamp01((self._kpos + ((now - self._ktime) * 1_000_000)) / tot) if self._kplay else _clamp01(self._kpos / tot)
            if abs(target - self._pv) > _CORR_THRESH: self._run_chain([(target, _CORR_DUR, _CORR_POW)], on_done='live')
            else: self._pv = target

        val = _clamp01(self._pv)
        self.progressbar.set_value(val)
        txt = f"{_fmt_time(int(val * tot))} / {_fmt_time(tot)}"
        self._set_time_text(txt)
        return True

    def _anchor_at(self, now, tot): self._kpos, self._ktime = int(self._pv * tot), now

    def _set_time_text(self, txt):
        if txt != self._last_time_txt: self._last_time_txt = txt; self.time.set_text(txt)

    def _update_prog_state(self, mp):
        status = getattr(mp, 'playback_status', 'stopped')
        self._kplay, self._klen = status == 'playing', getattr(mp, 'length', 0) or 0
        if status == 'stopped':
            self._pv, self._tkey = 0.0, None
            self._cancel_anim(); self.progressbar.set_value(0.0)
            self._set_time_text(_NO_TIME); return

        new_key = (mp.title, mp.artist, getattr(mp, 'arturl', ''))
        if self._tkey is not None and new_key != self._tkey:
            if self._pv > 0.012: self._run_chain([(0.0, _SW_DUR_BASE + self._pv * _SW_DUR_SCALE, _SW_POW)], on_done='rise')
            else: self._pv = 0.0; self._cancel_anim(); self._force_sync()
        elif not self._a_active: self._kpos, self._ktime = getattr(mp, 'position', 0) or 0, _time.monotonic()
        self._tkey = new_key

    def _seek(self, direction):
        mp = self.mpris_player
        if not mp: return
        ok, at0, new_us = False, False, None
        if isinstance(mp, LocalPlayer) and mp._playbin and mp.can_seek:
            good, pos = mp._playbin.query_position(Gst.Format.TIME)
            if good:
                tgt = min(pos + _SEEK_NS, mp.length * 1000 if mp.length > 0 else pos + _SEEK_NS) if direction > 0 else max(0, pos - _SEEK_NS)
                at0 = tgt == 0
                mp._playbin.seek_simple(Gst.Format.TIME, _SEEK_FLAGS, tgt)
                new_us, ok = tgt // 1000, True
        elif isinstance(mp, MprisPlayer) and mp.can_seek:
            cur_pos = getattr(mp, "position", 0) or 0
            if direction < 0: at0 = cur_pos <= _SEEK_US
            mp.seek(_SEEK_US * direction)
            new_us = min(max(0, cur_pos + _SEEK_US * direction), self._klen) if self._klen > 0 else max(0, cur_pos + _SEEK_US * direction)
            ok = True

        if ok and new_us is not None and self._klen > 0:
            self._kpos, self._ktime = int(new_us), _time.monotonic()
            target = _clamp01(new_us / self._klen)
            over = _clamp01(target + (1.0 if direction > 0 else -1.0) * random.uniform(_OVERSHOOT_MIN, _OVERSHOOT_MAX))
            dist = abs(target - self._pv)
            d1, d2 = (_SK_DUR_1, _SK_DUR_2) if dist < 0.05 else (_SK_DUR_1 * _SK_SCALE_MD, _SK_DUR_2 * _SK_SCALE_MD) if dist < 0.15 else (_SK_DUR_1 * _SK_SCALE_LG, _SK_DUR_2 * _SK_SCALE_LG)
            self._run_chain([(over, d1, _SK_POW_1), (target, d2, _SK_POW_2)], on_done='live')

        if ok and not self._is_wall: self._snap() if at0 and direction < 0 else self._flick(float(direction))

    def _toggle_order(self, *_):
        mp = self.mpris_player
        if not mp: return
        if isinstance(mp, LocalPlayer):
            mp.order_mode = _ORDER_NEXT_3.get(mp._order_mode, "normal")
            mp.emit("changed")
            return
        if getattr(mp, '_dead', False) or getattr(mp, 'is_limited', False): return
        can_sh = getattr(mp, 'can_shuffle', False)
        nxt = (_ORDER_NEXT_3 if can_sh else _ORDER_NEXT_2).get(self._local_order, "normal")
        was_shuffle, now_shuffle = self._local_order == "shuffle", nxt == "shuffle"
        self._local_order = nxt
        if can_sh and was_shuffle != now_shuffle:
            GLib.idle_add(lambda: (setattr(mp, 'shuffle', now_shuffle) if mp._player else None) or False)
        self._refresh()

    def _toggle_repeat(self, *_):
        mp = self.mpris_player
        if not mp: return
        if isinstance(mp, LocalPlayer):
            mp.loop_status = _REPEAT_NEXT.get(mp._loop_status, "None")
            mp.emit("changed")
            return
        if getattr(mp, '_dead', False) or getattr(mp, 'is_limited', False) or not getattr(mp, 'can_set_loop_status', False): return
        new_ls = _REPEAT_NEXT.get(getattr(mp, "loop_status", "None"), "None")
        GLib.idle_add(lambda: (setattr(mp, 'loop_status', new_ls) if mp._player else None) or False)

    def _refresh(self):
        mp = self.mpris_player
        if not mp: return
        if isinstance(mp, MprisPlayer) and getattr(mp, '_dead', False): self._setup_empty(); return
        if isinstance(mp, MprisPlayer): self._sync_order(mp)
        _set_label(self.title, mp.title); _set_label(self.album, mp.album); _set_label(self.artist, mp.artist)
        self._ucover(mp.arturl); self._uicon(); self._umode(mp); self._ubtn(mp); self._update_prog_state(mp)

    def _sync_order(self, mp):
        if not getattr(mp, 'can_shuffle', False): return
        mpris_sh = getattr(mp, "shuffle", False)
        if mpris_sh and self._local_order != "shuffle": self._local_order = "shuffle"
        elif not mpris_sh and self._local_order == "shuffle": self._local_order = "normal"

    def _uicon(self):
        playing = self.mpris_player and getattr(self.mpris_player, "playback_status", "") == "playing"
        self.play_pause.get_child().set_markup(icons.pause if playing else icons.play)
        _set_style(self.play_pause, "playing", playing)
        self._spin_on() if playing and not self._is_wall and not self._snapping else self._spin_off()

    def _umode(self, mp):
        sb, sl, om = self.shuffle_btn, self.shuffle_btn.get_child(), self._get_order()
        if isinstance(mp, MprisPlayer) and getattr(mp, 'is_limited', False):
            sl.set_markup(icons.shuffle); sb.set_tooltip_text("Not available"); sb.add_style_class("disabled"); sb.remove_style_class("active")
        else:
            sl.set_markup(icons.reverse_order if om in ("reverse", "normal") else icons.shuffle)
            sb.set_tooltip_text("Reverse" if om == "reverse" else "Shuffle" if om == "shuffle" else "Order")
            _set_style(sb, "active", om in ("reverse", "shuffle"))
            sb.remove_style_class("disabled")

        rb, rl = self.repeat_btn, self.repeat_btn.get_child()
        cl = not isinstance(mp, MprisPlayer) or (getattr(mp, 'can_set_loop_status', False) and not getattr(mp, 'is_limited', False))
        if not cl:
            rl.set_markup(icons.repeat); rb.set_tooltip_text("Not available"); rb.add_style_class("disabled"); rb.remove_style_class("active")
        else:
            ls = getattr(mp, "loop_status", "None")
            rl.set_markup(icons.repeat if ls in ("Playlist", "None") else _REPEAT_ONCE)
            rb.set_tooltip_text("Repeat All" if ls == "Playlist" else "Repeat Track" if ls == "Track" else "Repeat")
            _set_style(rb, "active", ls in ("Playlist", "Track"))
            rb.remove_style_class("disabled")

    def _ubtn(self, mp):
        can_seek, status = getattr(mp, "can_seek", False), getattr(mp, "playback_status", "stopped")
        _set_style(self.backward, "disabled", status == "stopped" or not can_seek)
        _set_style(self.forward, "disabled", status == "stopped" or not can_seek)
        cp, cn = (status in ("playing", "paused"), status in ("playing", "paused")) if isinstance(mp, LocalPlayer) else (getattr(mp, "can_go_previous", True), getattr(mp, "can_go_next", True))
        _set_style(self.prev, "disabled", not cp); _set_style(self.next, "disabled", not cn)
        _set_style(self.play_pause, "disabled", not getattr(mp, "can_play", True) and not getattr(mp, "can_pause", True))

    def _on_changed(self, *_):
        if not self._upd: self._upd = True; GLib.idle_add(self._flush)

    def _flush(self):
        self._upd = False
        if self.mpris_player: self._refresh()
        return False

    def cleanup(self):
        self._anim_off(); self._v_stop_scroll(); self._prog_stop(); self._cancel_anim()
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
        self._hc_id, self._states, self._repl = None, {}, False

        self.player_stack = Stack(name="player-stack", transition_type="slide-left-right", transition_duration=500, h_align="fill", v_align="fill", h_expand=True, v_expand=True)
        self.switcher = Gtk.StackSwitcher(name="player-switcher", spacing=8, stack=self.player_stack)
        self.switcher.set_halign(Gtk.Align.CENTER); self.switcher.set_valign(Gtk.Align.END); self.switcher.set_hexpand(True); self.switcher.set_margin_bottom(16); self.switcher.set_margin_start(15)

        mgr = self.mpris_manager = MprisPlayerManager()
        if local_player: self.player_stack.add_titled(PlayerBox(mpris_player=local_player), local_player.player_name, local_player.player_name)
        if players := mgr.players: [self._add(p) for p in players]
        elif not local_player: self.player_stack.add_titled(PlayerBox(), "nothing", _DEFAULT_TITLE)

        mgr.connect("player-appeared", self._on_appear); mgr.connect("player-vanished", self._on_vanish)

        self.player_overlay = Overlay(child=self.player_stack, overlays=(self.switcher,), h_expand=True, v_expand=True, h_align="fill", v_align="fill")
        self.add(self.player_overlay)
        self._schedule_icons()
        
        self._hc_id = GLib.timeout_add(1000, self._health)

    def _add(self, player):
        mp = MprisPlayer(player)
        iid = _mpris_id(mp)
        if iid in {self.player_stack.child_get_property(c, "name") for c in self.player_stack.get_children()}: iid = f"{iid}_{id(mp)}"
        pb = PlayerBox(mpris_player=mp)
        self.player_stack.add_titled(pb, iid, mp.player_name)
        pb._exit_sig_id = mp.connect("exit", self._on_exit, pb)
        return pb

    def _remove(self, pb):
        if pb not in self.player_stack.get_children(): return False
        self._states.pop(self.player_stack.child_get_property(pb, "name"), None)
        if mp := getattr(pb, 'mpris_player', None):
            if esig := getattr(pb, '_exit_sig_id', None):
                try: mp.disconnect(esig)
                except Exception: pass
            if isinstance(mp, MprisPlayer): mp._mark_dead()
        pb.cleanup()
        self.player_stack.remove(pb)
        self._schedule_icons()
        return False

    def switch_to_local(self):
        if self.local_player: self.player_stack.set_visible_child_name(self.local_player.player_name)

    def _on_appear(self, _mgr, player): self._add(player); self._schedule_icons()

    def _on_vanish(self, _mgr, vid):
        for c in self.player_stack.get_children():
            mp = getattr(c, "mpris_player", None)
            if mp and not isinstance(mp, LocalPlayer) and vid in (getattr(mp, "player_instance", ""), getattr(mp, "player_name", "")):
                self._remove(c); break
        self._schedule_icons()

    def _on_exit(self, _mp, pb): GLib.idle_add(self._remove, pb)

    def _health(self):
        stk, gprop = self.player_stack, self.player_stack.child_get_property
        drop, active, nothing, goto = [], 0, None, None

        for ch in stk.get_children():
            nm = gprop(ch, "name")
            if nm == "nothing": nothing = ch; continue
            mp = getattr(ch, 'mpris_player', None)
            if not mp: continue
            
            st, old = getattr(mp, 'playback_status', 'stopped'), self._states.get(nm, "stopped")
            if isinstance(mp, LocalPlayer):
                active += 1
                if st == "playing" and old != "playing": goto = nm
                self._states[nm] = st
            elif isinstance(mp, MprisPlayer):
                if mp.is_dead: drop.append(ch)
                elif st == "stopped" and not mp.title.strip(): ch.hide(); self._states[nm] = "ghost"
                else:
                    ch.show()
                    active += 1
                    if st == "playing" and old != "playing": goto = nm
                    self._states[nm] = st

        for ch in drop: self._remove(ch)
        if goto: stk.set_visible_child_name(goto)

        if active == 0:
            if not nothing and not self.local_player: nothing = PlayerBox(); stk.add_titled(nothing, "nothing", _DEFAULT_TITLE)
            if nothing: nothing.show(); stk.set_visible_child_name("nothing")
        elif nothing: nothing.hide()

        cur = stk.get_visible_child()
        if cur and not cur.get_visible() and active > 0:
            for c in stk.get_children():
                if c.get_visible() and gprop(c, "name") != "nothing": stk.set_visible_child_name(gprop(c, "name")); break

        self._schedule_icons()
        return True

    def _schedule_icons(self):
        if not self._repl: self._repl = True; GLib.idle_add(self._apply_icons)

    def _apply_icons(self):
        self._repl = False
        disc = icons.disc
        for btn in self.switcher.get_children():
            if isinstance(btn, Gtk.ToggleButton) and btn.get_visible():
                for c in btn.get_children():
                    if isinstance(c, Gtk.Label) and c.get_text() != disc:
                        btn.remove(c); lbl = Label(name="player-label", markup=disc); btn.add(lbl); lbl.show_all(); break
        return False

    def cleanup(self):
        if self._hc_id: GLib.source_remove(self._hc_id); self._hc_id = None
        for c in self.player_stack.get_children():
            if hasattr(c, 'cleanup'): c.cleanup()
        self.mpris_manager = None