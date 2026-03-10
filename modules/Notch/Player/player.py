import os
import time
import hashlib
import threading
import re
import random
import json

import gi
from gi.repository import Gdk, Gio, GLib, Gtk

gi.require_version('Gst', '1.0')
from gi.repository import Gst
Gst.init(None)

from mutagen import File as MutagenFile

from fabric.core.service import Service, Signal, Property
from fabric.audio.service import Audio
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.circularprogressbar import CircularProgressBar
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.overlay import Overlay
from fabric.widgets.scale import Scale
from fabric.widgets.scrolledwindow import ScrolledWindow
from fabric.widgets.stack import Stack

import services.icons as icons
from services.mpris import MprisPlayer, MprisPlayerManager
from services.circle_image import CircleImage


_SL_H, _LBL_H = 20, 20
_WALL = GLib.build_filenamev([GLib.get_home_dir(), ".current.wall"])
_CACHE = GLib.get_user_cache_dir() + "/vidgex-shell"
_CACHE_DIR = f"{_CACHE}/covers"
_MODE_FILE = f"{_CACHE}/playback_mode.json"
os.makedirs(_CACHE_DIR, exist_ok=True)

def _cleanup_cache():
    try:
        now = time.time()
        for f in os.listdir(_CACHE_DIR):
            path = os.path.join(_CACHE_DIR, f)
            if os.path.isfile(path) and os.stat(path).st_mtime < now - 86400:
                os.remove(path)
    except Exception:
        pass

threading.Thread(target=_cleanup_cache, daemon=True).start()

_REPEAT_ONCE = f"{icons.repeat}<small><b>1</b></small>"

_AUDIO_EXTS = frozenset({
    '.mp3', '.flac', '.wav', '.ogg', '.m4a', '.aac',
    '.wma', '.opus', '.ape', '.alac',
})
_MUSIC_DIR = (
    GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_MUSIC)
    or os.path.join(GLib.get_home_dir(), "Music")
)

_GAP = 8


def _on_hover_enter(w, _):
    if win := w.get_window():
        win.set_cursor(Gdk.Cursor.new_from_name(w.get_display(), "pointer"))

def _on_hover_leave(w, _):
    if win := w.get_window():
        win.set_cursor(None)

def _hover(w):
    w.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
    w.connect("enter-notify-event", _on_hover_enter)
    w.connect("leave-notify-event", _on_hover_leave)

def _fex(p):
    return Gio.File.new_for_path(p).query_exists(None) if p else False

def _ext(p):
    if not p:
        return ""
    p = p.split('?')[0]
    b = GLib.path_get_basename(p)
    return "." + b.rsplit(".", 1)[-1] if "." in b else ""

def _load_playback_mode():
    try:
        if os.path.isfile(_MODE_FILE):
            with open(_MODE_FILE, "r") as f:
                data = json.load(f)
            return data.get("loop_status", "Playlist"), data.get("shuffle", False)
    except Exception:
        pass
    return "Playlist", False

def _save_playback_mode(loop_status, shuffle):
    try:
        os.makedirs(os.path.dirname(_MODE_FILE), exist_ok=True)
        with open(_MODE_FILE, "w") as f:
            json.dump({"loop_status": loop_status, "shuffle": shuffle}, f)
    except Exception:
        pass

def _mpris_instance_id(mp):
    return (getattr(mp, "player_instance", None)
            or getattr(mp, "player_name", None)
            or f"player_{id(mp)}")


class LocalPlayer(Service):
    @Signal
    def changed(self) -> None: ...

    def __init__(self):
        super().__init__()
        self.player_name = "Library"
        self.title = "Nothing Playing"
        self.artist = "¯\\_(ツ)_/¯"
        self.album = "Enjoy the silence"
        self.arturl = ""
        self.playback_status = "stopped"
        self.length = 0
        saved_loop, saved_shuffle = _load_playback_mode()
        self._loop_status = saved_loop
        self._shuffle = saved_shuffle
        self.can_seek = False
        self.can_pause = True
        self.can_go_next = False
        self.can_go_previous = False
        self.on_next_cb = None
        self.on_prev_cb = None
        self._playbin = Gst.ElementFactory.make("playbin", "local_playbin")
        if self._playbin:
            bus = self._playbin.get_bus()
            bus.add_signal_watch()
            bus.connect("message::eos", self._on_eos)

    @Property(str, "read-write", default_value="Playlist")
    def loop_status(self): return self._loop_status
    @loop_status.setter
    def loop_status(self, val):
        self._loop_status = val
        _save_playback_mode(self._loop_status, self._shuffle)

    @Property(bool, "read-write", default_value=False)
    def shuffle(self): return self._shuffle
    @shuffle.setter
    def shuffle(self, val):
        self._shuffle = val
        _save_playback_mode(self._loop_status, self._shuffle)

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
        self.title, self.artist = title, artist
        self.album = album if album else ""
        self.arturl, self.length = art_url, length_us
        self.playback_status, self.can_seek = "playing", True
        self._playbin.set_state(Gst.State.PLAYING)
        self.emit("changed")

    def stop(self):
        if self._playbin: self._playbin.set_state(Gst.State.NULL)
        self.title, self.artist = "Nothing Playing", "¯\\_(ツ)_/¯"
        self.album, self.arturl = "Enjoy the silence", ""
        self.length, self.playback_status = 0, "stopped"
        self.can_seek = self.can_go_next = self.can_go_previous = False
        self.emit("changed")

    def play_pause(self):
        if not self._playbin: return
        if self.playback_status == "playing":
            self._playbin.set_state(Gst.State.PAUSED); self.playback_status = "paused"
        elif self.playback_status == "paused":
            self._playbin.set_state(Gst.State.PLAYING); self.playback_status = "playing"
        self.emit("changed")

    def next(self):
        if self.on_next_cb: self.on_next_cb()

    def previous(self):
        if self.on_prev_cb: self.on_prev_cb()

    def _on_eos(self, bus, msg):
        GLib.idle_add(self.next)


class PlayerBox(Box):
    __slots__ = (
        'mpris_player', '_ptid', '_wmon', '_upd', '_dcancel', '_sig_id',
        'cover', 'cover_placeholder', 'title', 'album', 'artist',
        'progressbar', 'time', 'mode_btn', 'overlay_container',
        'prev', 'backward', 'play_pause', 'forward', 'next',
        'btn_box', 'player_box', 'info_box', '_cover_fallback_tried',
        '_last_track_id', '_last_length',
    )

    def __init__(self, mpris_player=None):
        super().__init__(
            orientation="h", h_align="fill", v_align="fill",
            spacing=0, h_expand=True, v_expand=True,
        )
        self.mpris_player = mpris_player
        self._ptid = self._wmon = self._dcancel = self._sig_id = None
        self._upd = False
        self._cover_fallback_tried = False
        self._last_track_id = ""
        self._last_length = 0
        self._track_change_time = 0.0

        COVER_SIZE, PROG_SIZE = 174, 200

        self.cover = CircleImage(name="player-cover", image_file=_WALL, size=COVER_SIZE, h_align="center", v_align="center")
        self.cover_placeholder = CircleImage(name="player-cover", size=PROG_SIZE, h_align="center", v_align="center")

        lbl_kw = {"h_expand": False, "h_align": "center", "ellipsization": "end", "max_chars_width": 20, "justify": Gtk.Justification.CENTER}
        self.title = Label(name="player-title", **lbl_kw)
        self.album = Label(name="player-album", **lbl_kw)
        self.artist = Label(name="player-artist", **lbl_kw)
        for lbl in (self.title, self.album, self.artist):
            lbl.set_size_request(-1, _LBL_H)

        self.progressbar = CircularProgressBar(name="player-progress", size=PROG_SIZE, h_align="center", v_align="center", start_angle=180, end_angle=360)
        self.time = Label(name="player-time", label="--:-- / --:--")

        self.overlay_container = Box(
            name="player-overlay", orientation="v", h_expand=True, v_expand=True, h_align="center", v_align="center",
            children=(Overlay(child=self.cover_placeholder, overlays=(self.progressbar, self.cover)),),
        )
        self.overlay_container.set_size_request(PROG_SIZE, PROG_SIZE)

        self.title.set_label("Nothing Playing")
        self.album.set_label("Enjoy the silence")
        self.artist.set_label("¯\\_(ツ)_/¯")

        BTN_SIZE, PLAY_SIZE = 28, 36
        self.prev = self._mkbtn(icons.prev)
        self.backward = self._mkbtn(icons.skip_back)
        self.play_pause = self._mkbtn(icons.play, ("play-pause",))
        self.forward = self._mkbtn(icons.skip_forward)
        self.next = self._mkbtn(icons.next)
        self.mode_btn = self._mkbtn(icons.repeat, ("mode",))
        self.mode_btn.set_tooltip_text("Loop All")
        self.mode_btn.connect("clicked", self._toggle_mode)

        for b, s in ((self.prev, BTN_SIZE), (self.backward, BTN_SIZE), (self.play_pause, PLAY_SIZE),
                      (self.forward, BTN_SIZE), (self.next, BTN_SIZE), (self.mode_btn, BTN_SIZE)):
            b.set_size_request(s, s)

        self.btn_box = Box(name="player-btn-box", orientation="h", spacing=4, h_expand=False, v_expand=False, h_align="center", v_align="center",
                           children=(self.prev, self.backward, self.play_pause, self.forward, self.next))

        self.info_box = Box(name="player-info-box", orientation="v", spacing=4, h_expand=True, v_expand=True, h_align="center", v_align="center",
                            children=(self.title, self.album, self.artist, self.btn_box, self.time, self.mode_btn))
        self.info_box.set_size_request(200, -1)

        self.player_box = Box(name="player-box", orientation="h", spacing=0, h_expand=True, v_expand=True,
                              h_align="fill", v_align="fill", homogeneous=True,
                              children=(self.overlay_container, self.info_box))
        self.add(self.player_box)

        if mpris_player:
            self._setup_ctrl()
        else:
            self._setup_empty()

    def _can_toggle_mode(self) -> bool:
        mp = self.mpris_player
        if not mp:
            return False
        if isinstance(mp, LocalPlayer):
            return True
        if isinstance(mp, MprisPlayer):
            if getattr(mp, 'is_limited', False):
                return False
            can_loop = getattr(mp, 'can_set_loop_status', False)
            can_shuffle = getattr(mp, 'can_shuffle', False)
            return can_loop or can_shuffle
        return False

    def _toggle_mode(self, *_):
        mp = self.mpris_player
        if not mp or not self._can_toggle_mode():
            return
        
        can_loop, can_shuffle = True, True
        if isinstance(mp, MprisPlayer):
            if getattr(mp, 'is_limited', False): return
            can_loop = getattr(mp, 'can_set_loop_status', False)
            can_shuffle = getattr(mp, 'can_shuffle', False)
        
        if not can_loop and not can_shuffle:
            return
        
        ls = getattr(mp, "loop_status", "Playlist") if can_loop else "Playlist"
        shuff = getattr(mp, "shuffle", False) if can_shuffle else False
        
        try:
            if can_loop and can_shuffle:
                if ls == "Playlist" and not shuff: mp.shuffle = True
                elif ls == "Playlist" and shuff: mp.shuffle = False; mp.loop_status = "Track"
                else: mp.loop_status = "Playlist"; mp.shuffle = False
            elif can_loop and not can_shuffle:
                if ls == "Playlist": mp.loop_status = "Track"
                elif ls == "Track": mp.loop_status = "None"
                else: mp.loop_status = "Playlist"
            elif can_shuffle and not can_loop:
                mp.shuffle = not shuff
        except Exception:
            pass
        
        try:
            mp.emit("changed")
        except Exception:
            pass

    def _mkbtn(self, icon, sc=None):
        btn = Button(name="player-btn", child=Label(name="player-btn-label", markup=icon, style_classes=sc or ()),
                     style_classes=sc or (), h_expand=False, v_expand=False, h_align="center", v_align="center")
        _hover(btn)
        return btn

    def _setup_ctrl(self):
        self._apply()
        self.prev.connect("clicked", lambda _: self.mpris_player.previous())
        self.play_pause.connect("clicked", lambda _: (self.mpris_player.play_pause(), self._uicon()))
        self.backward.connect("clicked", self._bwd)
        self.forward.connect("clicked", self._fwd)
        self.next.connect("clicked", lambda _: self.mpris_player.next())
        self._sig_id = self.mpris_player.connect("changed", self._on_chg)

    def _setup_empty(self):
        self.play_pause.get_child().set_markup(icons.stop)
        self.play_pause.add_style_class("stop")
        for btn in (self.backward, self.forward, self.prev, self.next, self.mode_btn):
            btn.add_style_class("disabled")
        self.progressbar.set_value(0.0)
        self.time.set_text("--:-- / --:--")

    def _apply(self):
        mp = self.mpris_player
        if not mp:
            return

        if isinstance(mp, MprisPlayer) and getattr(mp, '_dead', False):
            self._setup_empty()
            return

        current_track_id = getattr(mp, 'track_id', '') or f"{mp.title}_{mp.artist}"
        if current_track_id != self._last_track_id:
            self._last_track_id = current_track_id
            self._cover_fallback_tried = False

        for lbl, txt in ((self.title, mp.title), (self.album, mp.album), (self.artist, mp.artist)):
            if txt and txt.strip():
                lbl.set_text(txt)
                lbl.set_opacity(1.0)
            else:
                lbl.set_text(" ")
                lbl.set_opacity(0.0)

        self._ucover(mp.arturl)
        self._uicon()
        self._update_mode_button(mp)
        self._update_control_buttons(mp)

    def _uprog(self):
        mp = self.mpris_player
        if not mp:
            self._ptid = None
            return False
        
        status = getattr(mp, "playback_status", "")
        if status != "playing":
            self._ptid = None
            return False
        
        cur = getattr(mp, "position", 0)
        tot = int(getattr(mp, "length", 0) or 0)
        
        if tot != self._last_length and tot > 0:
            self._last_length = tot
        
        if tot <= 0:
            self.progressbar.set_value(0.0)
            self.time.set_text("--:-- / --:--")
        else:
            safe_cur = min(cur, tot)
            progress = max(0.0, safe_cur / tot)
            self.progressbar.set_value(progress)
            
            cs, ts = safe_cur // 1_000_000, tot // 1_000_000
            self.time.set_text(f"{cs // 60}:{cs % 60:02} / {ts // 60}:{ts % 60:02}")
        
        return True

    def _update_mode_button(self, mp):
        can_loop, can_shuffle, is_limited = True, True, False
        
        if isinstance(mp, MprisPlayer):
            is_limited = getattr(mp, 'is_limited', False)
            can_loop = getattr(mp, 'can_set_loop_status', False) and not is_limited
            can_shuffle = getattr(mp, 'can_shuffle', False) and not is_limited
        
        ml = self.mode_btn.get_child()
        
        if is_limited or (not can_loop and not can_shuffle):
            ml.set_markup(icons.repeat)
            self.mode_btn.set_tooltip_text("Not available")
            self.mode_btn.add_style_class("disabled")
            self.mode_btn.remove_style_class("active")
            return
        
        ls = getattr(mp, "loop_status", "Playlist") if can_loop else "Playlist"
        shuff = getattr(mp, "shuffle", False) if can_shuffle else False
        
        if can_shuffle and shuff:
            ml.set_markup(icons.shuffle)
            self.mode_btn.set_tooltip_text("Shuffle")
            self.mode_btn.add_style_class("active")
            self.mode_btn.remove_style_class("disabled")
        elif can_loop and ls == "Track":
            ml.set_markup(_REPEAT_ONCE)
            self.mode_btn.set_tooltip_text("Loop Track")
            self.mode_btn.add_style_class("active")
            self.mode_btn.remove_style_class("disabled")
        elif can_loop and ls == "None":
            ml.set_markup(icons.repeat)
            self.mode_btn.set_tooltip_text("No Loop")
            self.mode_btn.remove_style_class("active")
            self.mode_btn.remove_style_class("disabled")
        else:
            ml.set_markup(icons.repeat)
            self.mode_btn.set_tooltip_text("Loop All")
            self.mode_btn.remove_style_class("active")
            self.mode_btn.remove_style_class("disabled")

    def _update_control_buttons(self, mp):
        can_seek = getattr(mp, "can_seek", False)
        status = getattr(mp, "playback_status", "stopped")
        
        seek_disabled = status == "stopped" or not can_seek
        
        if seek_disabled:
            self.backward.add_style_class("disabled")
            self.forward.add_style_class("disabled")
            if status == "stopped":
                self.progressbar.set_value(0.0)
                self.time.set_text("--:-- / --:--")
                self._stop_ptimer()
        else:
            self.backward.remove_style_class("disabled")
            self.forward.remove_style_class("disabled")
            self._start_ptimer()

        can_prev = getattr(mp, "can_go_previous", False)
        can_next = getattr(mp, "can_go_next", False)
        (self.prev.remove_style_class if can_prev else self.prev.add_style_class)("disabled")
        (self.next.remove_style_class if can_next else self.next.add_style_class)("disabled")

        can_play = getattr(mp, "can_play", True)
        can_pause = getattr(mp, "can_pause", True)
        if not can_play and not can_pause:
            self.play_pause.add_style_class("disabled")
        else:
            self.play_pause.remove_style_class("disabled")

    def _ucover(self, arturl):
        self._cover_fallback_tried = False
        
        if not arturl:
            self._try_extract_cover()
            return
            
        s = GLib.uri_parse_scheme(arturl)
        if s == "file":
            path = GLib.uri_unescape_string(arturl[7:], None)
            self._set_img(path)
        elif s in ("http", "https"):
            self._dl_art(arturl)
        else:
            if arturl.startswith('/'):
                self._set_img(arturl)
            else:
                self._fallback()

    def _try_extract_cover(self):
        if self._cover_fallback_tried:
            self._fallback()
            return
            
        self._cover_fallback_tried = True
        mp = self.mpris_player
        
        if not mp:
            self._fallback()
            return
        
        track_url = ""
        if isinstance(mp, MprisPlayer):
            track_url = getattr(mp, 'url', '') or ''
        
        if track_url.startswith("file://"):
            filepath = GLib.uri_unescape_string(track_url[7:], None)
            if filepath and os.path.isfile(filepath):
                threading.Thread(target=self._extract_cover_async, args=(filepath,), daemon=True).start()
                return
        
        self._fallback()

    def _extract_cover_async(self, filepath):
        try:
            md5 = hashlib.md5(filepath.encode()).hexdigest()
            cpath = f"{_CACHE_DIR}/extracted_{md5}.png"
            
            if _fex(cpath):
                GLib.idle_add(self._set_img, cpath)
                return
            
            audio = MutagenFile(filepath)
            if not audio:
                GLib.idle_add(self._fallback)
                return
            
            art_data = None
            if hasattr(audio, 'tags') and audio.tags:
                for key, tag in audio.tags.items():
                    if key.startswith('APIC'):
                        art_data = tag.data
                        break
            if not art_data and hasattr(audio, 'pictures') and audio.pictures:
                art_data = audio.pictures[0].data
            if not art_data and 'covr' in audio:
                art_data = bytes(audio['covr'][0])
            
            if art_data:
                with open(cpath, 'wb') as f: f.write(art_data)
                GLib.idle_add(self._set_img, cpath)
            else:
                GLib.idle_add(self._fallback)
        except Exception:
            GLib.idle_add(self._fallback)

    def _fallback(self):
        self._set_img(_WALL)
        if not self._wmon:
            self._wmon = Gio.File.new_for_path(_WALL).monitor_file(Gio.FileMonitorFlags.NONE, None)
            self._wmon.connect("changed", lambda *_: self.cover.set_image_from_file(_WALL))

    def _set_img(self, p):
        if _fex(p):
            self.cover.set_image_from_file(p)
        else:
            self._fallback()

    def _dl_art(self, url):
        md5 = hashlib.md5(url.encode()).hexdigest()
        ext = _ext(url) or '.png'
        if ext.lower() not in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp'):
            ext = '.png'
        cpath = f"{_CACHE_DIR}/{md5}{ext}"
        
        if _fex(cpath):
            self._set_img(cpath)
            return
            
        if self._dcancel:
            self._dcancel.cancel()
        self._dcancel = Gio.Cancellable.new()
        Gio.File.new_for_uri(url).load_contents_async(self._dcancel, self._on_dl, cpath)

    def _on_dl(self, f, res, cpath):
        try:
            ok, data, _ = f.load_contents_finish(res)
            if ok and data and len(data) <= 10 * 1024 * 1024:
                if self._is_valid_image(data):
                    gfile = Gio.File.new_for_path(cpath)
                    gfile.replace_contents_bytes_async(
                        GLib.Bytes.new(data), None, False, 
                        Gio.FileCreateFlags.PRIVATE, self._dcancel,
                        self._on_dl_save_finish, cpath
                    )
                    return
        except GLib.Error:
            pass
        GLib.idle_add(self._try_extract_cover)

    def _on_dl_save_finish(self, gfile, res, cpath):
        try:
            if gfile.replace_contents_finish(res)[0]:
                GLib.idle_add(self._set_img, cpath)
                return
        except GLib.Error:
            pass
        GLib.idle_add(self._try_extract_cover)

    def _is_valid_image(self, data: bytes) -> bool:
        if len(data) < 8: return False
        if data[:8] == b'\x89PNG\r\n\x1a\n': return True
        if data[:2] == b'\xff\xd8': return True
        if data[:6] in (b'GIF87a', b'GIF89a'): return True
        if data[:4] == b'RIFF' and data[8:12] == b'WEBP': return True
        if data[:2] == b'BM': return True
        return False

    def _start_ptimer(self):
        self._stop_ptimer()
        if self.mpris_player and getattr(self.mpris_player, "playback_status", "") == "playing":
            self._ptid = GLib.timeout_add(500, self._uprog)
            self._uprog()

    def _stop_ptimer(self):
        if self._ptid:
            GLib.source_remove(self._ptid)
            self._ptid = None

    def _uprog(self):
        mp = self.mpris_player
        if not mp:
            self._ptid = None
            return False
        
        status = getattr(mp, "playback_status", "")
        if status != "playing":
            self._ptid = None
            return False
        
        cur = getattr(mp, "position", 0)
        tot = int(getattr(mp, "length", 0) or 0)
        
        time_since_change = time.time() - getattr(self, '_track_change_time', 0)
        if time_since_change < 2.5:
            if cur > 3_000_000:
                cur = 0

        if tot != self._last_length and tot > 0:
            self._last_length = tot
        
        if tot <= 0:
            self.progressbar.set_value(0.0)
            self.time.set_text("--:-- / --:--")
        else:
            if cur > tot:
                cur = 0
                
            progress = max(0.0, cur / tot)
            self.progressbar.set_value(progress)
            
            cs, ts = cur // 1_000_000, tot // 1_000_000
            self.time.set_text(f"{cs // 60}:{cs % 60:02} / {ts // 60}:{ts % 60:02}")
        
        return True

    def _uicon(self):
        mp = self.mpris_player
        status = getattr(mp, "playback_status", "") if mp else ""
        
        if status == "playing":
            self.play_pause.get_child().set_markup(icons.pause)
            self.play_pause.add_style_class("playing")
            self._start_ptimer()
        else:
            self.play_pause.get_child().set_markup(icons.play)
            self.play_pause.remove_style_class("playing")
            self._stop_ptimer()

    def _is_disabled(self, btn):
        return btn.get_style_context().has_class("disabled")

    def _bwd(self, _):
        mp = self.mpris_player
        if not mp or self._is_disabled(self.backward):
            return
        if isinstance(mp, LocalPlayer) and mp._playbin and mp.can_seek:
            ok, cur_ns = mp._playbin.query_position(Gst.Format.TIME)
            if ok:
                mp._playbin.seek_simple(
                    Gst.Format.TIME,
                    Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                    max(0, cur_ns - 5_000_000_000))
        elif isinstance(mp, MprisPlayer) and mp.can_seek:
            mp.seek(-5_000_000)

    def _fwd(self, _):
        mp = self.mpris_player
        if not mp or self._is_disabled(self.forward):
            return
        if isinstance(mp, LocalPlayer) and mp._playbin and mp.can_seek:
            ok, cur_ns = mp._playbin.query_position(Gst.Format.TIME)
            if ok:
                max_ns = mp.length * 1000 if mp.length > 0 else cur_ns + 5_000_000_000
                mp._playbin.seek_simple(
                    Gst.Format.TIME,
                    Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                    min(cur_ns + 5_000_000_000, max_ns))
        elif isinstance(mp, MprisPlayer) and mp.can_seek:
            mp.seek(5_000_000)

    def _on_chg(self, *_):
        if not self._upd:
            self._upd = True
            GLib.idle_add(self._apply_deb)

    def _apply_deb(self):
        self._upd = False
        if self.mpris_player:
            self._apply()
        else:
            self._stop_ptimer()
        return False

    def cleanup(self):
        self._stop_ptimer()
        if self._dcancel:
            self._dcancel.cancel()
            self._dcancel = None
        if self._wmon:
            self._wmon.cancel()
            self._wmon = None
        if self.mpris_player and self._sig_id:
            try:
                self.mpris_player.disconnect(self._sig_id)
            except Exception:
                pass
        self.mpris_player = None


class MediaPlayer(Box):
    __slots__ = ('player_stack', 'switcher', 'mpris_manager', 'player_overlay', 
                 'local_player', '_health_check_id', '_player_states')

    def __init__(self, local_player=None):
        super().__init__(
            name="player", orientation="v",
            h_align="fill", v_align="fill",
            spacing=0, h_expand=True,
            v_expand=False,
        )
        self.local_player = local_player
        self._health_check_id = None
        self._player_states = {}

        self.player_stack = Stack(
            name="player-stack", transition_type="slide-left-right", transition_duration=500,
            h_align="fill", v_align="fill", h_expand=True, v_expand=True,
        )

        self.switcher = Gtk.StackSwitcher(name="player-switcher", spacing=8, stack=self.player_stack)
        self.switcher.set_halign(Gtk.Align.CENTER)
        self.switcher.set_valign(Gtk.Align.END)
        self.switcher.set_hexpand(True)
        self.switcher.set_margin_bottom(16)
        self.switcher.set_margin_start(15)
        self.switcher.set_margin_end(0)

        self.mpris_manager = MprisPlayerManager()

        if self.local_player:
            self.player_stack.add_titled(
                PlayerBox(mpris_player=self.local_player),
                self.local_player.player_name, self.local_player.player_name,
            )

        if players := self.mpris_manager.players:
            for p in players:
                self._add_mpris_player(p)
        elif not self.local_player:
            self.player_stack.add_titled(PlayerBox(), "nothing", "Nothing Playing")

        self.mpris_manager.connect("player-appeared", self._on_appear)
        self.mpris_manager.connect("player-vanished", self._on_vanish)

        self.player_overlay = Overlay(
            child=self.player_stack, overlays=(self.switcher,),
            h_expand=True, v_expand=True, h_align="fill", v_align="fill",
        )
        self.add(self.player_overlay)
        GLib.idle_add(self._repl_labels)
        
        self._health_check_id = GLib.timeout_add(1000, self._check_players_health)

    def _add_mpris_player(self, player):
        mp = MprisPlayer(player)
        iid = _mpris_instance_id(mp)
        
        existing = {self.player_stack.child_get_property(c, "name")
                    for c in self.player_stack.get_children()}
        if iid in existing:
            iid = f"{iid}_{id(mp)}"
        
        pb = PlayerBox(mpris_player=mp)
        self.player_stack.add_titled(pb, iid, mp.player_name)
        
        exit_sig = mp.connect("exit", self._on_player_exit, pb)
        setattr(pb, '_exit_sig_id', exit_sig)
        
        return pb

    def _on_player_exit(self, mp, player_box):
        GLib.idle_add(self._remove_player_box, player_box)

    def _check_players_health(self):
        to_remove = []
        active_players = 0

        for child in self.player_stack.get_children():
            name = self.player_stack.child_get_property(child, "name")
            if name == "nothing":
                continue
                
            mp = getattr(child, 'mpris_player', None)
            if not mp:
                continue
            
            status = getattr(mp, 'playback_status', 'stopped')
            old_status = self._player_states.get(name, "stopped")
            
            if isinstance(mp, LocalPlayer):
                active_players += 1
                if status == "playing" and old_status != "playing":
                    self.player_stack.set_visible_child_name(name)
                self._player_states[name] = status
                continue
            
            if isinstance(mp, MprisPlayer):
                if mp.is_dead:
                    to_remove.append(child)
                    continue
                
                is_ghost = status == "stopped" and not mp.title.strip()
                
                if is_ghost:
                    child.hide()
                    self._player_states[name] = "ghost"
                else:
                    child.show()
                    active_players += 1
                    
                    if status == "playing" and old_status != "playing":
                        self.player_stack.set_visible_child_name(name)
                        
                    self._player_states[name] = status
        
        for child in to_remove:
            self._remove_player_box(child)

        nothing_child = None
        for c in self.player_stack.get_children():
            if self.player_stack.child_get_property(c, "name") == "nothing":
                nothing_child = c
                break
                
        if active_players == 0:
            if not nothing_child and not self.local_player:
                pb = PlayerBox()
                self.player_stack.add_titled(pb, "nothing", "Nothing Playing")
                nothing_child = pb
            if nothing_child:
                nothing_child.show()
                self.player_stack.set_visible_child_name("nothing")
        else:
            if nothing_child:
                nothing_child.hide()

        current_child = self.player_stack.get_visible_child()
        if current_child and not current_child.get_visible() and active_players > 0:
            for c in self.player_stack.get_children():
                if c.get_visible() and self.player_stack.child_get_property(c, "name") != "nothing":
                    self.player_stack.set_visible_child_name(self.player_stack.child_get_property(c, "name"))
                    break
        
        GLib.idle_add(self._repl_labels)
        return True

    def _remove_player_box(self, player_box):
        if player_box not in self.player_stack.get_children():
            return False
        
        name = self.player_stack.child_get_property(player_box, "name")
        if name in self._player_states:
            del self._player_states[name]
        
        mp = getattr(player_box, 'mpris_player', None)
        exit_sig = getattr(player_box, '_exit_sig_id', None)
        if mp and exit_sig:
            try: mp.disconnect(exit_sig)
            except Exception: pass
            
        if mp and isinstance(mp, MprisPlayer):
            mp._mark_dead()
        
        player_box.cleanup()
        self.player_stack.remove(player_box)
        GLib.idle_add(self._repl_labels)
        return False

    def switch_to_local(self):
        if self.local_player:
            self.player_stack.set_visible_child_name(self.local_player.player_name)

    def _on_appear(self, mgr, player):
        self._add_mpris_player(player)
        GLib.idle_add(self._repl_labels)

    def _on_vanish(self, mgr, vanished_id):
        for c in self.player_stack.get_children():
            mp = getattr(c, "mpris_player", None)
            if not mp or isinstance(mp, LocalPlayer):
                continue
            
            mp_instance = getattr(mp, "player_instance", "")
            mp_name = getattr(mp, "player_name", "")
            
            if vanished_id in (mp_instance, mp_name):
                self._remove_player_box(c)
                break
        
        GLib.idle_add(self._repl_labels)

    def _repl_labels(self):
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
        if self._health_check_id:
            GLib.source_remove(self._health_check_id)
            self._health_check_id = None
        
        for c in self.player_stack.get_children():
            if hasattr(c, 'cleanup'):
                c.cleanup()
        self.mpris_manager = None


class MixerSlider(Scale):
    def __init__(self, stream, **kwargs):
        super().__init__(name="control-slider", orientation="h", h_expand=True, h_align="fill",
                         has_origin=True, increments=(0.01, 0.1), style_classes=("no-icon",), **kwargs)
        self.stream, self._upd = stream, False
        self.set_draw_value(False); self.set_size_request(100, _SL_H)
        v = stream.volume; self._last_vol = int(v + 0.5); self._muted_style = stream.muted
        self.set_value(v * 0.01); self.set_tooltip_text(f"{self._last_vol}%")
        self.connect("value-changed", self._on_val)
        self._sig = stream.connect("changed", self._on_strm)
        t = getattr(stream, "type", "").lower()
        self.add_style_class("mic" if "microphone" in t or "input" in t else "vol")
        if self._muted_style: self.add_style_class("muted")

    def _on_val(self, _):
        if self._upd: return
        if s := self.stream:
            nv = self.value * 100.0
            if abs(s.volume - nv) > 0.5:
                s.volume = nv; pct = int(nv + 0.5)
                if pct != self._last_vol: self.set_tooltip_text(f"{pct}%"); self._last_vol = pct

    def _on_strm(self, s):
        self._upd = True; v = s.volume; self.value = v * 0.01; pct = int(v + 0.5)
        if pct != self._last_vol: self.set_tooltip_text(f"{pct}%"); self._last_vol = pct
        m = s.muted
        if m and not self._muted_style: self.add_style_class("muted"); self._muted_style = True
        elif not m and self._muted_style: self.remove_style_class("muted"); self._muted_style = False
        self._upd = False

    def cleanup(self):
        if self.stream and self._sig:
            try: self.stream.disconnect(self._sig)
            except Exception: pass
        self.stream = None


class MixerSlot(Box):
    def __init__(self, stream):
        super().__init__(name="mixer-slot", orientation="v", spacing=2, h_expand=True, v_expand=False)
        self.stream = stream; v = int(stream.volume + 0.5)
        self.desc_lbl = Label(name="mixer-stream-desc", label=stream.description, h_expand=True, h_align="start", v_align="center", ellipsization="end")
        self.pct_lbl = Label(name="mixer-stream-pct", label=f"{v}%", h_expand=False, h_align="end", v_align="center", width_chars=4)
        top_row = CenterBox(name="mixer-slot-header", start_children=self.desc_lbl, end_children=self.pct_lbl, h_expand=True)
        self.slider = MixerSlider(stream)
        self.add(top_row); self.add(self.slider)
        self._sig_id = stream.connect("changed", self._on_stream_changed)

    def _on_stream_changed(self, st):
        new_v = int(st.volume + 0.5)
        if self.pct_lbl.get_label() != f"{new_v}%": self.pct_lbl.set_label(f"{new_v}%")
        if self.desc_lbl.get_label() != st.description: self.desc_lbl.set_label(st.description)

    def cleanup(self):
        if self.stream and self._sig_id:
            try: self.stream.disconnect(self._sig_id)
            except Exception: pass
        self.slider.cleanup(); self.stream = None


class MixerSection(Box):
    def __init__(self, title: str, **kwargs):
        self._tl = Label(name="mixer-section-title", label=title, h_expand=True, h_align="start", v_expand=False)
        self._cb = Box(name="mixer-content", orientation="v", spacing=4, h_expand=True, v_expand=False)
        self._cb.set_margin_end(4); self._cb.set_margin_bottom(4)
        self._sw_slots = {}

        self.scroll = ScrolledWindow(
            name=f"{title.lower()}-scrolled", child=self._cb,
            h_expand=True, v_expand=True,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_width=False, propagate_height=False,
        )
        self.scroll.set_overlay_scrolling(True)

        super().__init__(name="mixer-section", orientation="v", spacing=4,
                         h_expand=True, v_expand=True, children=(self._tl, self.scroll), **kwargs)

    def update_streams(self, streams):
        cb, ow, nw, cids = self._cb, self._sw_slots, {}, set()
        for s in streams:
            sid = id(s); cids.add(sid)
            if sid in ow: nw[sid] = ow[sid]
            else: slot = MixerSlot(s); nw[sid] = slot; cb.add(slot)
        for sid, slot in ow.items():
            if sid not in cids: cb.remove(slot); slot.cleanup(); slot.destroy()
        self._sw_slots = nw
        if len(ow) != len(nw) or ow.keys() != nw.keys(): cb.show_all()

    def cleanup(self):
        for slot in self._sw_slots.values(): slot.cleanup(); slot.destroy()
        self._sw_slots.clear(); self._cb.children = ()


class TrackList(Box):
    __slots__ = ('_rows', '_playing_btn', '_search_entry', '_search_overlay',
                 '_search_placeholder', '_list_box', '_count_lbl',
                 '_mon', '_pend', '_dead', 'local_player', 'media_player_ref',
                 '_current_path')

    def __init__(self, local_player, media_player_ref):
        super().__init__(name="track-list", orientation="v", spacing=4,
                         h_align="fill", v_align="fill", h_expand=False, v_expand=True)
        self.set_size_request(400, -1)
        self._rows, self._playing_btn, self._current_path = [], None, None
        self._mon, self._pend, self._dead = None, {}, False
        self.local_player, self.media_player_ref = local_player, media_player_ref
        self.local_player.on_next_cb = self._play_next
        self.local_player.on_prev_cb = self._play_prev

        self._count_lbl = Label(name="track-count", label="scanning...", h_align="end", h_expand=True)
        header = Box(name="track-header", orientation="h", spacing=8, h_expand=True, h_align="fill",
                     children=(Label(name="track-icon", markup=icons.disc),
                               Label(name="track-title", label="Music Library", h_align="start"), self._count_lbl))

        self._search_entry = Gtk.SearchEntry(name="track-search")
        self._search_entry.set_hexpand(True)
        self._search_entry.set_halign(Gtk.Align.FILL)
        self._search_entry.set_alignment(0.0)
        self._search_entry.set_placeholder_text("")

        self._search_placeholder = Gtk.Label(name="track-search-placeholder")
        self._search_placeholder.set_label("Search tracks...")
        self._search_placeholder.set_halign(Gtk.Align.START)
        self._search_placeholder.set_valign(Gtk.Align.CENTER)

        self._search_overlay = Gtk.Overlay()
        self._search_overlay.add(self._search_entry)
        self._search_overlay.add_overlay(self._search_placeholder)
        self._search_overlay.set_overlay_pass_through(self._search_placeholder, True)

        self._search_entry.connect("search-changed", self._on_search)
        self._search_entry.connect("key-press-event", self._on_search_key_press)

        self._list_box = Box(name="track-content", orientation="v", spacing=2, h_expand=True, v_expand=False)
        sw = ScrolledWindow(name="track-scrolled", child=self._list_box, h_expand=True, v_expand=True,
                            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC, hscrollbar_policy=Gtk.PolicyType.NEVER,
                            propagate_width=False, propagate_height=False, min_content_size=(150, 100))
        sw.set_overlay_scrolling(False)
        self.add(header); self.add(self._search_overlay); self.add(sw)
        threading.Thread(target=self._scan, daemon=True).start(); self._watch()

    def _make_centered_state(self, icon: str, text: str, name: str) -> Box:
        inner = Box(
            orientation="v", h_align="center", v_align="center", spacing=12,
            children=[
                Image(icon_name=icon, icon_size=48, name="explorer-empty-icon"),
                Label(name="explorer-empty-label", label=text),
            ])
        wrapper = Box(
            name=name, orientation="v",
            h_expand=True, v_expand=True, h_align="fill", v_align="fill")
        wrapper.set_size_request(-1, -1)
        wrapper.pack_start(Box(v_expand=True), True, True, 0)
        wrapper.pack_start(inner, False, False, 0)
        wrapper.pack_start(Box(v_expand=True), True, True, 0)
        return wrapper

    def _on_search_key_press(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self._search_entry.set_text("")
            self._clear_search_focus()
            return True
        return False

    def _clear_search_focus(self):
        toplevel = self.get_toplevel()
        if toplevel and hasattr(toplevel, 'set_focus'):
            toplevel.set_focus(None)

    def _is_click_on_search(self, root_widget, event_x, event_y):
        try:
            ok, sx, sy = root_widget.translate_coordinates(
                self._search_entry, int(event_x), int(event_y))
            if ok:
                alloc = self._search_entry.get_allocation()
                return 0 <= sx <= alloc.width and 0 <= sy <= alloc.height
        except Exception:
            pass
        return False

    def _watch(self):
        if not os.path.isdir(_MUSIC_DIR): return
        try:
            self._mon = Gio.File.new_for_path(_MUSIC_DIR).monitor_directory(Gio.FileMonitorFlags.NONE, None)
            self._mon.connect("changed", lambda *_: GLib.idle_add(self._on_dir_changed) if not self._dead else None)
        except GLib.Error: pass

    def _on_dir_changed(self):
        old = self._pend.get("music")
        if old: GLib.source_remove(old)
        self._pend["music"] = GLib.timeout_add(1000, lambda: (threading.Thread(target=self._scan, daemon=True).start(), False)[1])
        return False

    def _extract_cover(self, audio, filepath):
        if not audio: return ""
        md5 = hashlib.md5(filepath.encode()).hexdigest()
        cpath = f"{_CACHE_DIR}/loc_{md5}.png"
        if _fex(cpath): return GLib.filename_to_uri(cpath, None)
        try:
            art_data = None
            if hasattr(audio, 'tags') and audio.tags:
                for key, tag in audio.tags.items():
                    if key.startswith('APIC'): art_data = tag.data; break
            if not art_data and hasattr(audio, 'pictures') and audio.pictures: art_data = audio.pictures[0].data
            if not art_data and 'covr' in audio: art_data = audio['covr'][0]
            if art_data:
                with open(cpath, 'wb') as f: f.write(art_data)
                return GLib.filename_to_uri(cpath, None)
        except Exception: pass
        return ""

    def _get_metadata(self, filepath, filename):
        artist, title, album, art_url, secs = "", "", "", "", 0
        try:
            audio = MutagenFile(filepath)
            if audio:
                if hasattr(audio, 'info') and hasattr(audio.info, 'length'): secs = int(audio.info.length)
                art_url = self._extract_cover(audio, filepath)
                easy_audio = MutagenFile(filepath, easy=True)
                if easy_audio:
                    artist, title, album = easy_audio.get("artist", [""])[0], easy_audio.get("title", [""])[0], easy_audio.get("album", [""])[0]
        except Exception: pass
        if not artist or not title:
            clean = re.sub(r'^(\d+[\s\.\-_]+)+', '', filename).strip()
            if " - " in clean: parts = clean.split(" - ", 1); fa, ft = parts[0].strip(), parts[1].strip()
            elif "-" in clean: parts = clean.split("-", 1); fa, ft = parts[0].strip(), parts[1].strip()
            else: fa, ft = "Unknown", clean
            if not artist: artist = fa
            if not title: title = ft
        if not title: title = filename
        return artist, title, album, f"{secs // 60}:{secs % 60:02d}" if secs > 0 else "--:--", secs * 1000000, art_url

    def _scan(self):
        tracks = []
        if os.path.isdir(_MUSIC_DIR):
            for root, dirs, files in os.walk(_MUSIC_DIR):
                dirs.sort()
                for f in sorted(files):
                    if os.path.splitext(f)[1].lower() in _AUDIO_EXTS:
                        full = os.path.join(root, f)
                        a, t, al, ds, lu, au = self._get_metadata(full, os.path.splitext(f)[0])
                        tracks.append((a, t, al, ds, f"{a} {t} {al}".lower(), full, lu, au))
        GLib.idle_add(self._populate, tracks)

    def _populate(self, tracks):
        for ch in [*self._list_box.get_children()]: self._list_box.remove(ch); ch.destroy()
        self._rows.clear()
        if not tracks:
            empty_state = self._make_centered_state("folder-open-symbolic", "Folder music is empty", "explorer-empty-state")
            self._list_box.add(empty_state)
            self._count_lbl.set_text("0 tracks")
            self._list_box.show_all()
            return

        for artist, title, album, duration_str, search_str, full, length_us, art_url in tracks:
            t_esc = GLib.markup_escape_text(title, -1)
            if artist and artist.lower() not in ("unknown", "unknown artist"):
                a_esc = GLib.markup_escape_text(artist, -1)
                dm = f"<b>{a_esc}</b>  <span alpha='50%'>—</span>  {t_esc}"
            else: dm = t_esc
            info_lbl = Label(name="track-name", markup=dm, h_expand=True, h_align="start", v_align="center", ellipsization="end", max_chars_width=50)
            dur_lbl = Label(name="track-duration", label=duration_str, h_expand=False, h_align="end", v_align="center")
            btn = Button(name="track-row", child=Box(orientation="h", h_expand=True, children=[info_lbl, dur_lbl]),
                         h_expand=True, h_align="fill", tooltip_text=full)
            btn.connect("clicked", lambda _, p=full: self._play_by_path(p)); _hover(btn)
            self._rows.append((btn, search_str, full, artist, title, album, length_us, art_url))
            self._list_box.add(btn)
        self._count_lbl.set_text(f"{len(tracks)} tracks")
        self._list_box.show_all(); self._update_nav_buttons()

    def _stop_current(self):
        self.local_player.stop(); self._current_path = None
        if self._playing_btn: self._playing_btn.remove_style_class("playing"); self._playing_btn = None
        self._update_nav_buttons()

    def _play_by_path(self, path, force=False):
        for row in self._rows:
            if row[2] == path:
                btn, _, full, artist, title, album, length_us, art_url = row
                if self._current_path == full and not force: self._stop_current(); return
                self._current_path = full
                if self._playing_btn: self._playing_btn.remove_style_class("playing")
                btn.add_style_class("playing"); self._playing_btn = btn
                self.media_player_ref.switch_to_local(); self._update_nav_buttons()
                self.local_player.play_file(full, artist, title, album, length_us, art_url); return

    def _get_visible_rows(self): return [r for r in self._rows if r[0].get_visible()]

    def _update_nav_buttons(self):
        vis = self._get_visible_rows()
        self.local_player.can_go_previous = self.local_player.can_go_next = len(vis) > 0
        self.local_player.emit("changed")

    def _find_current_idx(self, vis):
        for i, row in enumerate(vis):
            if row[2] == self._current_path: return i
        return -1

    def _play_next(self):
        vis = self._get_visible_rows()
        if not vis: return
        ls, shuff, idx = self.local_player.loop_status, self.local_player.shuffle, self._find_current_idx(vis)
        if ls == "Track": self._play_by_path(vis[idx][2] if idx != -1 else vis[0][2], force=True)
        elif shuff: self._play_by_path(vis[random.randint(0, len(vis) - 1)][2], force=True)
        else: self._play_by_path(vis[(idx + 1) % len(vis)][2] if idx != -1 else vis[0][2], force=True)

    def _play_prev(self):
        vis = self._get_visible_rows()
        if not vis: return
        ls, shuff, idx = self.local_player.loop_status, self.local_player.shuffle, self._find_current_idx(vis)
        if ls == "Track": self._play_by_path(vis[idx][2] if idx != -1 else vis[-1][2], force=True)
        elif shuff: self._play_by_path(vis[random.randint(0, len(vis) - 1)][2], force=True)
        else: self._play_by_path(vis[(idx - 1) % len(vis)][2] if idx != -1 else vis[-1][2], force=True)

    def _on_search(self, entry):
        q = entry.get_text().lower().strip()
        self._search_placeholder.set_visible(not q)
        vis = 0
        for btn, search_str, *_ in self._rows:
            v = not q or q in search_str; btn.set_visible(v)
            if v: vis += 1
        total = len(self._rows)
        self._count_lbl.set_text(f"{vis}/{total}" if q else f"{total} tracks")
        self._update_nav_buttons()

    def cleanup(self):
        self._dead = True
        if self._mon:
            try: self._mon.cancel()
            except Exception: pass
            self._mon = None
        for tid in self._pend.values():
            try: GLib.source_remove(tid)
            except Exception: pass
        self._pend.clear(); self._stop_current(); self._rows.clear()


class Player(Box):
    __slots__ = ('audio', '_out', '_inp', '_sigs', 'local_player',
                 'media_player', 'track_list', '_toplevel_click_sig')

    def __init__(self, **kwargs):
        super().__init__(
            name="dash-player", orientation="h",
            spacing=_GAP,
            homogeneous=False, h_align="fill", v_align="fill",
            h_expand=True, v_expand=True, visible=True, **kwargs,
        )
        self._sigs = []
        self._toplevel_click_sig = None
        self.local_player = LocalPlayer()
        self.media_player = MediaPlayer(local_player=self.local_player)
        self.track_list = TrackList(local_player=self.local_player, media_player_ref=self.media_player)

        try:
            self.audio = Audio()
        except Exception as e:
            mw = Label(label=f"Audio unavailable: {e}", h_align="center", v_align="center", h_expand=True, v_expand=True)
            left_col = Box(name="player-left", orientation="v", spacing=_GAP,
                           h_align="fill", v_align="fill", h_expand=True, v_expand=True,
                           children=(self.media_player, mw))
            left_col.set_size_request(400, -1)
            self.add(left_col); self.add(self.track_list)
            self.set_size_request(800, 350); self.media_player.set_size_request(-1, 200)
            self.audio = None
            self.connect("realize", self._on_realize)
            self.show_all(); return

        self._out = MixerSection("Outputs")
        self._inp = MixerSection("Inputs")

        mixer_row = Box(name="mixer-row", orientation="h", spacing=_GAP,
                        h_expand=True, v_expand=True, homogeneous=True,
                        children=(self._out, self._inp))

        left_col = Box(name="player-left", orientation="v",
                       spacing=_GAP,
                       h_align="fill", v_align="fill",
                       h_expand=True, v_expand=True,
                       children=(self.media_player, mixer_row))
        left_col.set_size_request(400, -1)

        self.add(left_col)
        self.add(self.track_list)

        self.set_size_request(800, 350)
        self.media_player.set_size_request(-1, 200)

        a, u = self.audio, self._upd
        for sig in ("changed", "stream-added", "stream-removed"):
            self._sigs.append((a, a.connect(sig, u)))
        self._upd()
        self.connect("realize", self._on_realize)
        self.show_all()

    def _on_realize(self, widget):
        GLib.idle_add(self._connect_toplevel_click)

    def _connect_toplevel_click(self):
        toplevel = self.get_toplevel()
        if toplevel and isinstance(toplevel, Gtk.Window) and not self._toplevel_click_sig:
            toplevel.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            self._toplevel_click_sig = toplevel.connect(
                "button-press-event", self._on_toplevel_click)
        return False

    def _on_toplevel_click(self, widget, event):
        search = self.track_list._search_entry
        if not search.has_focus():
            return False
        if self.track_list._is_click_on_search(widget, event.x, event.y):
            return False
        toplevel = self.get_toplevel()
        if toplevel and hasattr(toplevel, 'set_focus'):
            toplevel.set_focus(None)
        return False

    def _upd(self, *_):
        if not (a := self.audio): return
        outs = [a.speaker] if a.speaker else []; outs.extend(a.applications or ())
        ins = [a.microphone] if a.microphone else []; ins.extend(a.recorders or ())
        self._out.update_streams(outs); self._inp.update_streams(ins)

    def cleanup(self):
        if self._toplevel_click_sig:
            try:
                toplevel = self.get_toplevel()
                if toplevel:
                    toplevel.disconnect(self._toplevel_click_sig)
            except Exception:
                pass
            self._toplevel_click_sig = None

        for obj, sig in self._sigs:
            try: obj.disconnect(sig)
            except Exception: pass
        self._sigs.clear()
        if out := getattr(self, '_out', None): out.cleanup()
        if inp := getattr(self, '_inp', None): inp.cleanup()
        if mp := getattr(self, 'media_player', None): mp.cleanup()
        if tl := getattr(self, 'track_list', None): tl.cleanup()
        self.audio = None