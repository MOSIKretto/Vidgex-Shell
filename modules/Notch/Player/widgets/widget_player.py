from mutagen import File as MutagenFile
import os
import time
import hashlib
import threading
import json
import math

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, Gdk, Gio, GLib, Gtk
Gst.init(None)

from fabric.core.service import Service, Signal, Property
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.circularprogressbar import CircularProgressBar
from fabric.widgets.label import Label
from fabric.widgets.overlay import Overlay
from fabric.widgets.stack import Stack

import services.icons as icons
from modules.Notch.Player.mpris import MprisPlayer, MprisPlayerManager
from services.circle_image import CircleImage


_LBL_H      = 20
_COVER_SIZE = 174
_PROG_SIZE  = 200
_BTN_SIZE   = 28
_PLAY_SIZE  = 36

_NO_TIME        = "--:-- / --:--"
_DEFAULT_TITLE  = "Nothing Playing"
_DEFAULT_ARTIST = "¯\\_(ツ)_/¯"
_DEFAULT_ALBUM  = "Enjoy the silence"

_CACHE_BASE = os.path.join(GLib.get_user_cache_dir(), "vidgex-shell")
_CACHE_DIR  = os.path.join(_CACHE_BASE, "covers")
_MODE_FILE  = os.path.join(_CACHE_BASE, "playback_mode.json")
_WALL       = os.path.join(GLib.get_home_dir(), ".current.wall")

_REPEAT_ONCE     = f"{icons.repeat}<small><b>1</b></small>"
_VALID_COVER_EXT = frozenset({'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'})
_MAX_IMG_SIZE    = 10 << 20
_SEEK_FLAGS      = Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT
_SEEK_NS         = 5_000_000_000
_SEEK_US         = 5_000_000

_ORDER_NEXT_3 = {"normal": "reverse", "reverse": "shuffle", "shuffle": "normal"}
_ORDER_NEXT_2 = {"normal": "reverse", "reverse": "normal"}
_REPEAT_NEXT  = {"None": "Playlist", "Playlist": "Track", "Track": "None"}

_PNG_SIG  = b'\x89PNG\r\n\x1a\n'
_JPEG_SIG = b'\xff\xd8'
_GIF_SIGS = (b'GIF87a', b'GIF89a')
_BMP_SIG  = b'BM'
_RIFF_SIG = b'RIFF'
_WEBP_SIG = b'WEBP'

_HOVER_MASK       = Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
_SCROLL_MASK      = Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK
_COVER_EVENTS     = _HOVER_MASK | _SCROLL_MASK
_SCROLL_THRESHOLD = 5.0

_ANIM_MS       = 16
_SPIN_STEP     = 0.003
_FLICK_INITIAL = 0.12
_FLICK_DECAY   = 0.88
_FLICK_MIN     = 0.001
_SNAP_FACTOR   = 0.18
_SNAP_EPSILON  = 0.005
_TAU           = math.tau

os.makedirs(_CACHE_DIR, exist_ok=True)

def _cleanup_cache():
    try:
        cutoff = time.time() - 86_400
        with os.scandir(_CACHE_DIR) as it:
            for e in it:
                if e.is_file() and e.stat().st_mtime < cutoff:
                    os.remove(e.path)
    except Exception:
        pass

threading.Thread(target=_cleanup_cache, daemon=True).start()

_cursor_cache: dict = {}

def _on_hover_enter(w, _e):
    if win := w.get_window():
        dsp = w.get_display()
        cur = _cursor_cache.get(dsp)
        if cur is None:
            cur = Gdk.Cursor.new_from_name(dsp, "pointer")
            _cursor_cache[dsp] = cur
        win.set_cursor(cur)

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
    h = data[:8]
    return (h == _PNG_SIG
            or h[:2] == _JPEG_SIG
            or h[:2] == _BMP_SIG
            or h[:6] in _GIF_SIGS
            or (h[:4] == _RIFF_SIG and len(data) >= 12 and data[8:12] == _WEBP_SIG))

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


class LocalPlayer(Service):
    @Signal
    def changed(self) -> None: ...
    @Signal
    def next_requested(self) -> None: ...
    @Signal
    def previous_requested(self) -> None: ...

    def __init__(self):
        super().__init__()
        self.player_name     = "Library"
        self.title           = _DEFAULT_TITLE
        self.artist          = _DEFAULT_ARTIST
        self.album           = _DEFAULT_ALBUM
        self.arturl          = ""
        self.playback_status = "stopped"
        self.length          = 0
        self.can_seek        = False
        self.can_pause       = True
        self.can_go_next     = False
        self.can_go_previous = False
        self.on_next_cb      = None
        self.on_prev_cb      = None

        ls, om = _load_mode()
        self._loop_status = ls
        self._order_mode  = om

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
        pb.set_property("uri", GLib.filename_to_uri(path, None))
        self.title, self.artist = title, artist
        self.album   = album or ""
        self.arturl  = art_url
        self.length  = length_us
        self.playback_status = "playing"
        self.can_seek = True
        pb.set_state(Gst.State.PLAYING)
        self.emit("changed")

    def stop(self):
        if self._playbin:
            self._playbin.set_state(Gst.State.NULL)
        self.title, self.artist, self.album = _DEFAULT_TITLE, _DEFAULT_ARTIST, _DEFAULT_ALBUM
        self.arturl = ""
        self.length = 0
        self.playback_status = "stopped"
        self.can_seek = self.can_go_next = self.can_go_previous = False
        self.emit("changed")

    def play_pause(self):
        pb = self._playbin
        if not pb:
            return
        if self.playback_status == "playing":
            pb.set_state(Gst.State.PAUSED)
            self.playback_status = "paused"
        elif self.playback_status == "paused":
            pb.set_state(Gst.State.PLAYING)
            self.playback_status = "playing"
        self.emit("changed")

    def next(self):
        (self.on_next_cb or (lambda: self.emit("next_requested")))()

    def previous(self):
        (self.on_prev_cb or (lambda: self.emit("previous_requested")))()

    def _on_eos(self, _bus, _msg):
        if self._loop_status == "Track":
            GLib.idle_add(self._replay)
        elif self._order_mode == "reverse":
            GLib.idle_add(self.previous)
        else:
            GLib.idle_add(self.next)

    def _replay(self):
        if self._playbin:
            self._playbin.seek_simple(Gst.Format.TIME, _SEEK_FLAGS, 0)


class PlayerBox(Box):
    __slots__ = (
        'mpris_player', '_sig_id', '_exit_sig_id',
        'cover', 'cover_placeholder', '_cover_box',
        'title', 'album', 'artist', 'progressbar', 'time',
        'prev', 'backward', 'play_pause', 'forward', 'next',
        'shuffle_btn', 'repeat_btn', 'mode_box',
        'btn_box', 'info_box', 'player_box', 'overlay_container',
        '_angle', '_anim_id', '_spinning', '_flick_v', '_snapping',
        '_last_art', '_extract_tried', '_is_wall', '_dcancel', '_wmon',
        '_ptid', '_upd', '_scroll_acc',
        '_local_order',
    )

    def __init__(self, mpris_player=None):
        super().__init__(
            orientation="h", h_align="fill", v_align="fill",
            spacing=0, h_expand=True, v_expand=True,
        )
        self.mpris_player = mpris_player

        self._sig_id = self._exit_sig_id = None
        self._ptid   = self._anim_id = None
        self._dcancel = self._wmon = None
        self._upd = False

        self._last_art      = None
        self._extract_tried = False
        self._is_wall       = True

        self._angle    = 0.0
        self._spinning = False
        self._flick_v  = 0.0
        self._snapping = False

        self._scroll_acc = 0.0
        self._local_order = "normal"

        self.cover = CircleImage(
            name="player-cover", image_file=_WALL,
            size=_COVER_SIZE, h_align="center", v_align="center",
        )

        cb = self._cover_box = Gtk.EventBox()
        cb.set_visible_window(False)
        cb.set_above_child(True)
        cb.set_halign(Gtk.Align.CENTER)
        cb.set_valign(Gtk.Align.CENTER)
        cb.add(self.cover)
        cb.add_events(_COVER_EVENTS | Gdk.EventMask.BUTTON_PRESS_MASK)
        cb.connect("draw",               self._on_cover_draw)
        cb.connect("scroll-event",       self._on_scroll)
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
        self.title  = Label(name="player-title",  **lkw)
        self.album  = Label(name="player-album",  **lkw)
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

        self.prev       = self._btn(icons.prev)
        self.backward   = self._btn(icons.skip_back)
        self.play_pause = self._btn(icons.play, ("play-pause",))
        self.forward    = self._btn(icons.skip_forward)
        self.next       = self._btn(icons.next)

        self.shuffle_btn = self._btn(icons.shuffle, ("mode",))
        self.shuffle_btn.set_tooltip_text("Order")
        self.shuffle_btn.connect("clicked", self._toggle_order)

        self.repeat_btn = self._btn(icons.repeat, ("mode",))
        self.repeat_btn.set_tooltip_text("Repeat")
        self.repeat_btn.connect("clicked", self._toggle_repeat)

        for b, s in ((self.prev, _BTN_SIZE), (self.backward, _BTN_SIZE),
                      (self.play_pause, _PLAY_SIZE),
                      (self.forward, _BTN_SIZE), (self.next, _BTN_SIZE),
                      (self.shuffle_btn, _BTN_SIZE),
                      (self.repeat_btn, _BTN_SIZE)):
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

        if mpris_player:
            self._wire()
            self._ptid_start()
        else:
            self._setup_empty()

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
        if isinstance(mp, LocalPlayer):
            return mp._order_mode
        return self._local_order

    def _is_reversed(self):
        return self._get_order() == "reverse"

    def _on_cover_draw(self, w, cr):
        child = w.get_child()
        if not child or not child.get_visible():
            return True
        a = self._angle
        if a:
            alloc = w.get_allocation()
            cx, cy = alloc.width * 0.5, alloc.height * 0.5
            cr.save()
            cr.translate(cx, cy)
            cr.rotate(a)
            cr.translate(-cx, -cy)
            w.propagate_draw(child, cr)
            cr.restore()
        else:
            w.propagate_draw(child, cr)
        return True

    def _ensure_anim(self):
        if self._anim_id is None:
            self._anim_id = GLib.timeout_add(_ANIM_MS, self._anim_tick)

    def _anim_tick(self):
        if self._snapping:
            a = self._angle
            if a <= math.pi:
                a *= 1.0 - _SNAP_FACTOR
                done = a < _SNAP_EPSILON
            else:
                a += (_TAU - a) * _SNAP_FACTOR
                done = a >= _TAU - _SNAP_EPSILON
            if done:
                self._angle = 0.0
                self._snapping = False
                self._maybe_spin()
            else:
                self._angle = a
        else:
            a = self._angle
            if self._spinning:
                a += _SPIN_STEP
            v = self._flick_v
            if v:
                a += v
                v *= _FLICK_DECAY
                self._flick_v = v if abs(v) >= _FLICK_MIN else 0.0
            self._angle = a % _TAU

        self._cover_box.queue_draw()

        alive = self._spinning or self._snapping or bool(self._flick_v)
        if not alive:
            self._anim_id = None
        return alive

    def _spin_on(self):
        self._spinning = True
        self._ensure_anim()

    def _spin_off(self):
        self._spinning = False

    def _flick(self, d):
        self._snapping = False
        self._flick_v = _FLICK_INITIAL * d
        self._ensure_anim()

    def _snap(self):
        self._spinning = False
        self._flick_v = 0.0
        if self._angle == 0.0:
            self._maybe_spin()
            return
        self._snapping = True
        self._ensure_anim()

    def _anim_off(self):
        self._spinning = self._snapping = False
        self._flick_v = 0.0
        aid = self._anim_id
        if aid is not None:
            GLib.source_remove(aid)
            self._anim_id = None

    def _maybe_spin(self):
        mp = self.mpris_player
        if mp and getattr(mp, "playback_status", "") == "playing" and not self._is_wall:
            self._spin_on()

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
            self._scroll_acc += dy
            if self._scroll_acc <= -_SCROLL_THRESHOLD:
                self._seek(1)
                self._scroll_acc = 0.0
            elif self._scroll_acc >= _SCROLL_THRESHOLD:
                self._seek(-1)
                self._scroll_acc = 0.0
        return True

    def _on_cover_click(self, _w, ev):
        if ev.button == 1:
            mp = self.mpris_player
            if mp:
                mp.play_pause()
        return True

    def _ucover(self, arturl):
        if arturl == self._last_art:
            return
        self._last_art = arturl
        self._extract_tried = False
        self._angle = 0.0
        self._anim_off()
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
            self._wall()

    def _try_extract(self):
        if self._extract_tried:
            self._wall()
            return
        self._extract_tried = True
        mp = self.mpris_player
        url = (getattr(mp, 'url', '') or '') if isinstance(mp, MprisPlayer) else ''
        if url.startswith("file://"):
            path = GLib.uri_unescape_string(url[7:], None)
            if path and os.path.isfile(path):
                threading.Thread(target=self._extract_bg, args=(path,), daemon=True).start()
                return
        self._wall()

    def _extract_bg(self, path):
        try:
            h = hashlib.md5(path.encode()).hexdigest()
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
        GLib.idle_add(self._wall)

    @staticmethod
    def _cover_bytes(audio):
        tags = getattr(audio, 'tags', None)
        if tags:
            for k in tags:
                if k.startswith('APIC'):
                    return tags[k].data
        pics = getattr(audio, 'pictures', None)
        if pics:
            return pics[0].data
        covr = audio.get('covr')
        return bytes(covr[0]) if covr else None

    def _wall(self):
        self._anim_off()
        self._angle = 0.0
        self._is_wall = True
        self._cover_box.queue_draw()
        self.cover.set_image_from_file(_WALL)
        if not self._wmon:
            gf = Gio.File.new_for_path(_WALL)
            self._wmon = gf.monitor_file(Gio.FileMonitorFlags.NONE, None)
            self._wmon.connect("changed",
                               lambda *_: self.cover.set_image_from_file(_WALL))

    def _set_img(self, p):
        if _fex(p):
            self.cover.set_image_from_file(p)
            self._is_wall = (p == _WALL)
        else:
            self._wall()

    def _dl_art(self, url):
        h = hashlib.md5(url.encode()).hexdigest()
        ext = _ext(url) or '.png'
        if ext.lower() not in _VALID_COVER_EXT:
            ext = '.png'
        cp = os.path.join(_CACHE_DIR, f"{h}{ext}")
        if _fex(cp):
            self._set_img(cp)
            return
        if self._dcancel:
            self._dcancel.cancel()
        self._dcancel = Gio.Cancellable.new()
        Gio.File.new_for_uri(url).load_contents_async(
            self._dcancel, self._on_dl, cp)

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
        self.prev.connect("clicked",       lambda _: self._do_prev())
        self.next.connect("clicked",       lambda _: self._do_next())
        self.play_pause.connect("clicked",  lambda _: mp.play_pause())
        self.backward.connect("clicked",    lambda _: self._seek(-1))
        self.forward.connect("clicked",     lambda _: self._seek(1))
        self._sig_id = mp.connect("changed", self._on_changed)

    def _do_prev(self):
        mp = self.mpris_player
        if not mp:
            return
        if self._is_reversed():
            mp.next()
        else:
            mp.previous()

    def _do_next(self):
        mp = self.mpris_player
        if not mp:
            return
        if self._is_reversed():
            mp.previous()
        else:
            mp.next()

    def _setup_empty(self):
        self._anim_off()
        self._angle = 0.0
        self._cover_box.queue_draw()
        self.play_pause.get_child().set_markup(icons.stop)
        self.play_pause.add_style_class("stop")
        for b in (self.backward, self.forward, self.prev, self.next,
                  self.shuffle_btn, self.repeat_btn):
            b.add_style_class("disabled")
        self.progressbar.set_value(0.0)
        self.time.set_text(_NO_TIME)

    def _seek(self, direction):
        mp = self.mpris_player
        if not mp:
            return
        ok = at0 = False

        if isinstance(mp, LocalPlayer):
            pb = mp._playbin
            if pb and mp.can_seek:
                good, pos = pb.query_position(Gst.Format.TIME)
                if good:
                    if direction > 0:
                        cap = mp.length * 1000 if mp.length > 0 else pos + _SEEK_NS
                        tgt = min(pos + _SEEK_NS, cap)
                    else:
                        tgt = max(0, pos - _SEEK_NS)
                        at0 = tgt == 0
                    pb.seek_simple(Gst.Format.TIME, _SEEK_FLAGS, tgt)
                    ok = True
        elif isinstance(mp, MprisPlayer) and mp.can_seek:
            if direction < 0:
                at0 = (getattr(mp, "position", 0) or 0) <= _SEEK_US
            mp.seek(_SEEK_US * direction)
            ok = True

        if ok and not self._is_wall:
            if at0 and direction < 0:
                self._snap()
            else:
                self._flick(float(direction))

    def _toggle_order(self, *_):
        mp = self.mpris_player
        if not mp:
            return

        if isinstance(mp, LocalPlayer):
            cur = mp._order_mode
            mp.order_mode = _ORDER_NEXT_3.get(cur, "normal")
            mp.emit("changed")
            return

        if isinstance(mp, MprisPlayer):
            if getattr(mp, '_dead', False) or getattr(mp, 'is_limited', False):
                return

            can_sh = getattr(mp, 'can_shuffle', False)
            cur = self._local_order
            nxt = (_ORDER_NEXT_3 if can_sh else _ORDER_NEXT_2).get(cur, "normal")

            was_shuffle = (cur == "shuffle")
            now_shuffle = (nxt == "shuffle")

            self._local_order = nxt

            if can_sh and was_shuffle != now_shuffle:
                def _safe_set():
                    try:
                        if not getattr(mp, '_dead', False) and mp._player:
                            mp.shuffle = now_shuffle
                    except Exception:
                        pass
                    return False
                GLib.idle_add(_safe_set)

            self._refresh()

    def _toggle_repeat(self, *_):
        mp = self.mpris_player
        if not mp:
            return

        if isinstance(mp, LocalPlayer):
            ls = mp._loop_status
            mp.loop_status = _REPEAT_NEXT.get(ls, "None")
            mp.emit("changed")
            return

        if isinstance(mp, MprisPlayer):
            if getattr(mp, '_dead', False) or getattr(mp, 'is_limited', False):
                return
            if not getattr(mp, 'can_set_loop_status', False):
                return

            ls = getattr(mp, "loop_status", "None")
            new_ls = _REPEAT_NEXT.get(ls, "None")

            def _safe_set():
                try:
                    if not getattr(mp, '_dead', False) and mp._player:
                        mp.loop_status = new_ls
                except Exception:
                    pass
                return False
            GLib.idle_add(_safe_set)

    def _refresh(self):
        mp = self.mpris_player
        if not mp:
            return
        if isinstance(mp, MprisPlayer) and getattr(mp, '_dead', False):
            self._setup_empty()
            return

        if isinstance(mp, MprisPlayer):
            self._sync_order(mp)

        _set_label(self.title,  mp.title)
        _set_label(self.album,  mp.album)
        _set_label(self.artist, mp.artist)
        self._ucover(mp.arturl)
        self._uicon()
        self._umode(mp)
        self._ubtn(mp)

    def _sync_order(self, mp):
        try:
            if not getattr(mp, 'can_shuffle', False):
                return
            mpris_sh = getattr(mp, "shuffle", False)
        except Exception:
            return
        lo = self._local_order
        if mpris_sh and lo != "shuffle":
            self._local_order = "shuffle"
        elif not mpris_sh and lo == "shuffle":
            self._local_order = "normal"

    def _uicon(self):
        mp = self.mpris_player
        playing = mp is not None and getattr(mp, "playback_status", "") == "playing"
        self.play_pause.get_child().set_markup(icons.pause if playing else icons.play)
        _set_style(self.play_pause, "playing", playing)
        if playing and not self._is_wall and not self._snapping:
            self._spin_on()
        else:
            self._spin_off()

    def _umode(self, mp):
        self._umode_order(mp)
        self._umode_repeat(mp)

    def _umode_order(self, mp):
        sb = self.shuffle_btn
        sl = sb.get_child()

        if isinstance(mp, MprisPlayer):
            if getattr(mp, 'is_limited', False):
                sl.set_markup(icons.shuffle)
                sb.set_tooltip_text("Not available")
                sb.add_style_class("disabled")
                sb.remove_style_class("active")
                return

        om = self._get_order()
        if om == "reverse":
            sl.set_markup(icons.reverse_order)
            sb.set_tooltip_text("Reverse")
            _set_style(sb, "active", True)
        elif om == "shuffle":
            sl.set_markup(icons.shuffle)
            sb.set_tooltip_text("Shuffle")
            _set_style(sb, "active", True)
        else:
            sl.set_markup(icons.shuffle)
            sb.set_tooltip_text("Order")
            _set_style(sb, "active", False)
        sb.remove_style_class("disabled")

    def _umode_repeat(self, mp):
        rb = self.repeat_btn
        rl = rb.get_child()
        is_m = isinstance(mp, MprisPlayer)
        ltd  = is_m and getattr(mp, 'is_limited', False)
        cl   = not is_m or (getattr(mp, 'can_set_loop_status', False) and not ltd)

        if not cl:
            rl.set_markup(icons.repeat)
            rb.set_tooltip_text("Not available")
            rb.add_style_class("disabled")
            rb.remove_style_class("active")
            return

        ls = getattr(mp, "loop_status", "None")
        if ls == "Playlist":
            rl.set_markup(icons.repeat)
            rb.set_tooltip_text("Repeat All")
            _set_style(rb, "active", True)
        elif ls == "Track":
            rl.set_markup(_REPEAT_ONCE)
            rb.set_tooltip_text("Repeat Track")
            _set_style(rb, "active", True)
        else:
            rl.set_markup(icons.repeat)
            rb.set_tooltip_text("Repeat")
            _set_style(rb, "active", False)
        rb.remove_style_class("disabled")

    def _ubtn(self, mp):
        can_seek = getattr(mp, "can_seek", False)
        status   = getattr(mp, "playback_status", "stopped")
        stopped  = status == "stopped"

        _set_style(self.backward, "disabled", stopped or not can_seek)
        _set_style(self.forward,  "disabled", stopped or not can_seek)

        if stopped:
            self.progressbar.set_value(0.0)
            self.time.set_text(_NO_TIME)

        if isinstance(mp, LocalPlayer):
            live = status in ("playing", "paused")
            cp = cn = live
        else:
            cp = getattr(mp, "can_go_previous", True)
            cn = getattr(mp, "can_go_next", True)

        _set_style(self.prev, "disabled", not cp)
        _set_style(self.next, "disabled", not cn)
        _set_style(self.play_pause, "disabled",
                   not getattr(mp, "can_play", True) and not getattr(mp, "can_pause", True))

    def _ptid_start(self):
        if not self._ptid:
            self._ptid = GLib.timeout_add(500, self._ptick)
            self._ptick()

    def _ptid_stop(self):
        t = self._ptid
        if t:
            GLib.source_remove(t)
            self._ptid = None

    def _ptick(self):
        mp = self.mpris_player
        if not mp or getattr(mp, '_dead', False):
            self._ptid = None
            return False
        cur = getattr(mp, "position", 0) or 0
        tot = getattr(mp, "length", 0)  or 0
        if tot <= 0:
            self.progressbar.set_value(0.0)
            self.time.set_text(_NO_TIME)
        else:
            s = min(cur, tot)
            self.progressbar.set_value(max(0.0, min(1.0, s / tot)))
            cs, ts = int(s) // 1_000_000, int(tot) // 1_000_000
            self.time.set_text(f"{cs // 60}:{cs % 60:02} / {ts // 60}:{ts % 60:02}")
        return True

    def _on_changed(self, *_):
        if not self._upd:
            self._upd = True
            GLib.idle_add(self._flush)

    def _flush(self):
        self._upd = False
        if self.mpris_player:
            self._refresh()
        return False

    def cleanup(self):
        self._anim_off()
        self._ptid_stop()
        dc = self._dcancel
        if dc:
            dc.cancel()
            self._dcancel = None
        wm = self._wmon
        if wm:
            wm.cancel()
            self._wmon = None
        mp, sid = self.mpris_player, self._sig_id
        if mp and sid:
            try:
                mp.disconnect(sid)
            except Exception:
                pass
        self.mpris_player = None


class MediaPlayer(Box):
    __slots__ = (
        'player_stack', 'switcher', 'mpris_manager', 'player_overlay',
        'local_player', '_hc_id', '_states', '_repl',
    )

    def __init__(self, local_player=None):
        super().__init__(
            name="player", orientation="v",
            h_align="fill", v_align="fill",
            spacing=0, h_expand=True, v_expand=False,
        )
        self.local_player = local_player
        self._hc_id  = None
        self._states = {}
        self._repl   = False

        self.player_stack = Stack(
            name="player-stack", transition_type="slide-left-right",
            transition_duration=500, h_align="fill", v_align="fill",
            h_expand=True, v_expand=True,
        )

        sw = self.switcher = Gtk.StackSwitcher(
            name="player-switcher", spacing=8, stack=self.player_stack)
        sw.set_halign(Gtk.Align.CENTER)
        sw.set_valign(Gtk.Align.END)
        sw.set_hexpand(True)
        sw.set_margin_bottom(16)
        sw.set_margin_start(15)

        mgr = self.mpris_manager = MprisPlayerManager()

        if local_player:
            self.player_stack.add_titled(
                PlayerBox(mpris_player=local_player),
                local_player.player_name, local_player.player_name)

        players = mgr.players
        if players:
            for p in players:
                self._add(p)
        elif not local_player:
            self.player_stack.add_titled(PlayerBox(), "nothing", _DEFAULT_TITLE)

        mgr.connect("player-appeared", self._on_appear)
        mgr.connect("player-vanished", self._on_vanish)

        self.player_overlay = Overlay(
            child=self.player_stack, overlays=(self.switcher,),
            h_expand=True, v_expand=True, h_align="fill", v_align="fill")
        self.add(self.player_overlay)
        self._schedule_icons()
        self._hc_id = GLib.timeout_add(1000, self._health)

    def _add(self, player):
        mp  = MprisPlayer(player)
        iid = _mpris_id(mp)
        used = {self.player_stack.child_get_property(c, "name")
                for c in self.player_stack.get_children()}
        if iid in used:
            iid = f"{iid}_{id(mp)}"
        pb = PlayerBox(mpris_player=mp)
        self.player_stack.add_titled(pb, iid, mp.player_name)
        pb._exit_sig_id = mp.connect("exit", self._on_exit, pb)
        return pb

    def _remove(self, pb):
        if pb not in self.player_stack.get_children():
            return False
        name = self.player_stack.child_get_property(pb, "name")
        self._states.pop(name, None)
        mp   = getattr(pb, 'mpris_player', None)
        esig = getattr(pb, '_exit_sig_id', None)
        if mp and esig:
            try:
                mp.disconnect(esig)
            except Exception:
                pass
        if mp and isinstance(mp, MprisPlayer):
            mp._mark_dead()
        pb.cleanup()
        self.player_stack.remove(pb)
        self._schedule_icons()
        return False

    def switch_to_local(self):
        lp = self.local_player
        if lp:
            self.player_stack.set_visible_child_name(lp.player_name)

    def _on_appear(self, _mgr, player):
        self._add(player)
        self._schedule_icons()

    def _on_vanish(self, _mgr, vid):
        for c in self.player_stack.get_children():
            mp = getattr(c, "mpris_player", None)
            if not mp or isinstance(mp, LocalPlayer):
                continue
            if vid in (getattr(mp, "player_instance", ""),
                       getattr(mp, "player_name", "")):
                self._remove(c)
                break
        self._schedule_icons()

    def _on_exit(self, _mp, pb):
        GLib.idle_add(self._remove, pb)

    def _health(self):
        stk      = self.player_stack
        children = stk.get_children()
        gprop    = stk.child_get_property
        drop     = []
        active   = 0
        nothing  = None
        goto     = None

        for ch in children:
            nm = gprop(ch, "name")
            if nm == "nothing":
                nothing = ch
                continue
            mp = getattr(ch, 'mpris_player', None)
            if not mp:
                continue
            st  = getattr(mp, 'playback_status', 'stopped')
            old = self._states.get(nm, "stopped")

            if isinstance(mp, LocalPlayer):
                active += 1
                if st == "playing" and old != "playing":
                    goto = nm
                self._states[nm] = st
            elif isinstance(mp, MprisPlayer):
                if mp.is_dead:
                    drop.append(ch)
                elif st == "stopped" and not mp.title.strip():
                    ch.hide()
                    self._states[nm] = "ghost"
                else:
                    ch.show()
                    active += 1
                    if st == "playing" and old != "playing":
                        goto = nm
                    self._states[nm] = st

        for ch in drop:
            self._remove(ch)

        if goto:
            stk.set_visible_child_name(goto)

        if active == 0:
            if not nothing and not self.local_player:
                nothing = PlayerBox()
                stk.add_titled(nothing, "nothing", _DEFAULT_TITLE)
            if nothing:
                nothing.show()
                stk.set_visible_child_name("nothing")
        elif nothing:
            nothing.hide()

        cur = stk.get_visible_child()
        if cur and not cur.get_visible() and active > 0:
            for c in stk.get_children():
                if c.get_visible() and gprop(c, "name") != "nothing":
                    stk.set_visible_child_name(gprop(c, "name"))
                    break

        self._schedule_icons()
        return True

    def _schedule_icons(self):
        if not self._repl:
            self._repl = True
            GLib.idle_add(self._apply_icons)

    def _apply_icons(self):
        self._repl = False
        disc = icons.disc
        for btn in self.switcher.get_children():
            if isinstance(btn, Gtk.ToggleButton) and btn.get_visible():
                for c in btn.get_children():
                    if isinstance(c, Gtk.Label) and c.get_text() != disc:
                        btn.remove(c)
                        lbl = Label(name="player-label", markup=disc)
                        btn.add(lbl)
                        lbl.show_all()
                        break
        return False

    def cleanup(self):
        hid = self._hc_id
        if hid:
            GLib.source_remove(hid)
            self._hc_id = None
        for c in self.player_stack.get_children():
            if hasattr(c, 'cleanup'):
                c.cleanup()
        self.mpris_manager = None