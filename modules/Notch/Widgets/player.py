import os
import hashlib
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.circularprogressbar import CircularProgressBar
from fabric.widgets.label import Label
from fabric.widgets.overlay import Overlay
from fabric.widgets.stack import Stack

from gi.repository import Gdk, Gio, GLib, Gtk

import services.icons as icons
from services.mpris import MprisPlayer, MprisPlayerManager
from services.circle_image import CircleImage


_WALL = GLib.build_filenamev([GLib.get_home_dir(), ".current.wall"])
_CACHE_DIR = f"{GLib.get_user_cache_dir()}/vidgex-shell/covers"
os.makedirs(_CACHE_DIR, exist_ok=True)

def _on_hover_enter(w, _):
    if win := w.get_window(): win.set_cursor(Gdk.Cursor.new_from_name(w.get_display(), "pointer"))

def _on_hover_leave(w, _):
    if win := w.get_window(): win.set_cursor(None)

def _hover(w):
    w.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
    w.connect("enter-notify-event", _on_hover_enter)
    w.connect("leave-notify-event", _on_hover_leave)

def _fex(p):
    return Gio.File.new_for_path(p).query_exists(None) if p else False

def _ext(p):
    return "." + b.rsplit(".", 1)[-1] if p and "." in (b := GLib.path_get_basename(p)) else ""


class PlayerBox(Box):
    __slots__ = ('mpris_player', '_ptid', '_wmon', '_upd', '_dcancel', '_sig_id',
                 'cover', 'cover_placeholder', 'title', 'album', 'artist',
                 'progressbar', 'time', 'overlay_container', 'prev', 'backward',
                 'play_pause', 'forward', 'next', 'btn_box', 'player_box')

    def __init__(self, mpris_player=None):
        super().__init__(orientation="v", h_align="fill", spacing=0, h_expand=False, v_expand=True)
        self.mpris_player = mpris_player
        self._ptid = self._wmon = self._dcancel = self._sig_id = None
        self._upd = False

        self.cover = CircleImage(name="player-cover", image_file=_WALL, size=162, h_align="center", v_align="center")
        self.cover_placeholder = CircleImage(name="player-cover", size=198, h_align="center", v_align="center")
        
        lbl_kw = {"h_expand": True, "h_align": "fill", "ellipsization": "end", "max_chars_width": 1}
        self.title = Label(name="player-title", **lbl_kw)
        self.album = Label(name="player-album", **lbl_kw)
        self.artist = Label(name="player-artist", **lbl_kw)
        
        self.progressbar = CircularProgressBar(name="player-progress", size=198, h_align="center", v_align="center", start_angle=180, end_angle=360)
        self.time = Label(name="player-time", label="--:-- / --:--")

        self.overlay_container = CenterBox(name="player-overlay", center_children=(Overlay(child=self.cover_placeholder, overlays=(self.progressbar, self.cover)),))

        self.title.set_label("Nothing Playing")
        self.album.set_label("Enjoy the silence")
        self.artist.set_label("¯\\_(ツ)_/¯")

        self.prev = self._mkbtn(icons.prev)
        self.backward = self._mkbtn(icons.skip_back)
        self.play_pause = self._mkbtn(icons.play, ("play-pause",))
        self.forward = self._mkbtn(icons.skip_forward)
        self.next = self._mkbtn(icons.next)

        self.btn_box = CenterBox(
            name="player-btn-box", orientation="h", 
            center_children=(Box(
                orientation="h", spacing=8, h_expand=True, h_align="fill",
                children=(self.prev, self.backward, self.play_pause, self.forward, self.next) # Кортеж!
            ),)
        )

        self.player_box = Box(
            name="player-box", orientation="v", v_align="center", spacing=4, 
            children=(self.overlay_container, self.title, self.album, self.artist, self.btn_box, self.time)
        )
        self.add(self.player_box)

        if mpris_player: self._setup_ctrl()
        else: self._setup_empty()

    def _mkbtn(self, icon, sc=None):
        btn = Button(
            name="player-btn", 
            child=Label(name="player-btn-label", markup=icon, style_classes=sc or ()),
            style_classes=sc or (), h_expand=False, v_expand=False, h_align="center", v_align="center"
        )
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
        for btn in (self.backward, self.forward, self.prev, self.next):
            btn.add_style_class("disabled")
        self.progressbar.set_value(0.0)
        self.time.set_text("--:-- / --:--")

    def _apply(self):
        mp = self.mpris_player
        for lbl, txt in ((self.title, mp.title), (self.album, mp.album), (self.artist, mp.artist)):
            has = bool(txt and txt.strip())
            lbl.set_visible(has)
            if has: lbl.set_text(txt)

        self._ucover(mp.arturl)
        self._uicon()

        can_seek = getattr(mp, "can_seek", False)
        if getattr(mp, "player_name", "").lower() == "firefox" or not can_seek:
            self.backward.add_style_class("disabled")
            self.forward.add_style_class("disabled")
            self.progressbar.set_value(0.0)
            self.time.set_text("--:-- / --:--")
            self._stop_ptimer()
        else:
            self.backward.remove_style_class("disabled")
            self.forward.remove_style_class("disabled")
            self._start_ptimer()

        (self.prev.remove_style_class if getattr(mp, "can_go_previous", False) else self.prev.add_style_class)("disabled")
        (self.next.remove_style_class if getattr(mp, "can_go_next", False) else self.next.add_style_class)("disabled")

    def _ucover(self, arturl):
        if not arturl:
            self._fallback()
            return
        
        s = GLib.uri_parse_scheme(arturl)
        if s == "file":
            self._set_img(GLib.uri_unescape_string(arturl[7:], None))
        elif s in ("http", "https"):
            self._dl_art(arturl)
        else:
            self._set_img(arturl)

    def _fallback(self):
        self._set_img(_WALL)
        if not self._wmon:
            self._wmon = Gio.File.new_for_path(_WALL).monitor_file(Gio.FileMonitorFlags.NONE, None)
            self._wmon.connect("changed", lambda *_: self.cover.set_image_from_file(_WALL))

    def _start_ptimer(self):
        self._stop_ptimer()
        if self.mpris_player and getattr(self.mpris_player, "playback_status", "") == "playing":
            self._ptid = GLib.timeout_add(1000, self._uprog)
            self._uprog()

    def _stop_ptimer(self):
        if self._ptid:
            GLib.source_remove(self._ptid)
            self._ptid = None

    def _set_img(self, p):
        self.cover.set_image_from_file(p) if _fex(p) else self._fallback()

    def _dl_art(self, url):
        md5 = hashlib.md5(url.encode()).hexdigest()
        cpath = f"{_CACHE_DIR}/{md5}{_ext(url) or '.png'}"
        
        if _fex(cpath):
            self._set_img(cpath)
            return

        if self._dcancel: self._dcancel.cancel()
        self._dcancel = Gio.Cancellable.new()
        Gio.File.new_for_uri(url).load_contents_async(self._dcancel, self._on_dl, cpath)

    def _on_dl(self, f, res, cpath):
        try:
            ok, data, _ = f.load_contents_finish(res)
            if ok and data and len(data) <= 5242880:
                Gio.File.new_for_path(cpath).replace_contents(data, None, False, Gio.FileCreateFlags.PRIVATE, None)
                GLib.idle_add(self._set_img, cpath)
                return
        except GLib.Error: pass
        GLib.idle_add(self._fallback)

    def _uicon(self):
        if getattr(self.mpris_player, "playback_status", "") == "playing":
            self.play_pause.get_child().set_markup(icons.pause)
            self.play_pause.add_style_class("playing")
            self._start_ptimer()
        else:
            self.play_pause.get_child().set_markup(icons.play)
            self.play_pause.remove_style_class("playing")
            self._stop_ptimer()

    def _bwd(self, _):
        if getattr(self.mpris_player, "can_seek", False) and not self.backward.has_style_class("disabled"):
            self.mpris_player.position = max(0, self.mpris_player.position - 5000000)

    def _fwd(self, _):
        if getattr(self.mpris_player, "can_seek", False) and not self.forward.has_style_class("disabled"):
            self.mpris_player.position += 5000000

    def _uprog(self):
        mp = self.mpris_player
        if not mp or getattr(mp, "playback_status", "") != "playing":
            self._ptid = None
            return False
            
        cur = getattr(mp, "position", 0)
        tot = int(getattr(mp, "length", 0) or 0)
        
        if tot <= 0:
            self.progressbar.set_value(0.0)
            self.time.set_text("--:-- / --:--")
        else:
            self.progressbar.set_value(cur / tot)
            cs, ts = cur // 1000000, tot // 1000000
            self.time.set_text(f"{cs // 60}:{cs % 60:02} / {ts // 60}:{ts % 60:02}")
        return True

    def _on_chg(self, *_):
        if not self._upd:
            self._upd = True
            GLib.idle_add(self._apply_deb)

    def _apply_deb(self):
        self._upd = False
        self._apply() if self.mpris_player else self._stop_ptimer()
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
            try: self.mpris_player.disconnect(self._sig_id)
            except Exception: pass
        self.mpris_player = None


class Player(Box):
    __slots__ = ('player_stack', 'switcher', 'mpris_manager')

    def __init__(self):
        super().__init__(name="player", orientation="v", h_align="fill", spacing=0, h_expand=False, v_expand=True)

        self.player_stack = Stack(name="player-stack", transition_type="slide-left-right", transition_duration=500, v_align="center", v_expand=True)
        self.switcher = Gtk.StackSwitcher(name="player-switcher", spacing=8, halign=Gtk.Align.CENTER, stack=self.player_stack)

        self.mpris_manager = MprisPlayerManager()
        
        if players := self.mpris_manager.players:
            for p in players:
                mp = MprisPlayer(p)
                self.player_stack.add_titled(PlayerBox(mpris_player=mp), mp.player_name, mp.player_name)
        else:
            self.player_stack.add_titled(PlayerBox(), "nothing", "Nothing Playing")

        self.mpris_manager.connect("player-appeared", self._on_appear)
        self.mpris_manager.connect("player-vanished", self._on_vanish)

        self.add(self.player_stack)
        self.add(self.switcher)
        GLib.idle_add(self._repl_labels)

    def _on_appear(self, mgr, player):
        ch = self.player_stack.get_children()
        if len(ch) == 1 and not getattr(ch[0], "mpris_player", None):
            self.player_stack.remove(ch[0])
        mp = MprisPlayer(player)
        self.player_stack.add_titled(PlayerBox(mpris_player=mp), mp.player_name, mp.player_name)
        GLib.idle_add(self._repl_labels)

    def _on_vanish(self, mgr, pn):
        for c in self.player_stack.get_children():
            if getattr(c, "mpris_player", None) and c.mpris_player.player_name == pn:
                c.cleanup()
                self.player_stack.remove(c)
                break
                
        if not any(getattr(c, "mpris_player", None) for c in self.player_stack.get_children()):
            self.player_stack.add_titled(PlayerBox(), "nothing", "Nothing Playing")
        GLib.idle_add(self._repl_labels)

    def _repl_labels(self):
        for btn in self.switcher.get_children():
            if isinstance(btn, Gtk.ToggleButton):
                for c in btn.get_children():
                    if isinstance(c, Gtk.Label) and c.get_text() != icons.disc:
                        btn.remove(c)
                        lbl = Label(name="player-label", markup=icons.disc)
                        btn.add(lbl)
                        lbl.show_all()
                        break
        return False

    def cleanup(self):
        for c in self.player_stack.get_children():
            if hasattr(c, 'cleanup'): c.cleanup()
        self.mpris_manager = None


class PlayerSmall(CenterBox):
    __slots__ = ('_dopts', '_didx', '_rtimer', '_sig_id', 'mpris_icon', 'mpris_label', 
                 'mpris_button', 'center_stack', 'mpris_manager', 'mpris_player', 'current_index')

    def __init__(self):
        super().__init__(name="player-small", orientation="h", h_align="fill", v_align="center")

        self._dopts = ("title", "artist")
        self._didx, self.current_index = 0, 0
        self._rtimer = self._sig_id = self.mpris_player = None

        self.mpris_icon = Button(name="compact-mpris-icon", h_align="center", v_align="center", child=Label(name="compact-mpris-icon-label", markup=icons.disc))
        self.mpris_icon.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.mpris_icon.connect("button-press-event", self._on_icon)
        _hover(self.mpris_icon)

        self.mpris_label = Label(name="compact-mpris-label", label="Nothing Playing", ellipsization="end", max_chars_width=26, h_align="center")

        self.mpris_button = Button(name="compact-mpris-button", h_align="center", v_align="center", child=Label(name="compact-mpris-button-label", markup=icons.play))
        self.mpris_button.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.mpris_button.connect("button-press-event", self._on_pp)
        _hover(self.mpris_button)

        self.center_stack = Stack(name="compact-mpris", transition_type="crossfade", transition_duration=100, v_align="center", v_expand=False, children=(self.mpris_label,))

        self.add(CenterBox(
            name="compact-mpris", orientation="h", h_expand=True, h_align="fill", v_align="center", v_expand=False, 
            start_children=self.mpris_icon, center_children=self.center_stack, end_children=self.mpris_button
        ))

        self.mpris_manager = MprisPlayerManager()
        if players := self.mpris_manager.players:
            self._bind_player(MprisPlayer(players[0]))

        self._apply()
        self.mpris_manager.connect("player-appeared", self._on_appear)
        self.mpris_manager.connect("player-vanished", self._on_vanish)

    def _bind_player(self, player):
        if self.mpris_player and self._sig_id:
            try: self.mpris_player.disconnect(self._sig_id)
            except Exception: pass
            
        self.mpris_player = player
        if self.mpris_player:
            self._sig_id = self.mpris_player.connect("changed", lambda *_: self._apply())

    def _apply(self):
        if not self.mpris_player:
            self.mpris_label.set_text("Nothing Playing")
            self.mpris_button.get_child().set_markup(icons.stop)
            self.mpris_icon.get_child().set_markup(icons.disc)
            return

        mp = self.mpris_player
        self.mpris_icon.get_child().set_markup(icons.disc)
        self._uicon()

        txt = mp.title if self._dopts[self._didx] == "title" else mp.artist
        self.mpris_label.set_text(txt if txt and txt.strip() else "Nothing Playing")

    def _on_icon(self, w, e):
        if e.type != Gdk.EventType.BUTTON_PRESS or not (players := self.mpris_manager.players):
            return True

        if e.button == 2:
            self._didx = (self._didx + 1) % len(self._dopts)
            self._apply()
        else:
            self.current_index = (self.current_index + (1 if e.button == 1 else -1)) % len(players)
            self._bind_player(MprisPlayer(players[self.current_index]))
            self._apply()
        return True

    def _on_pp(self, w, e):
        if e.type != Gdk.EventType.BUTTON_PRESS or not self.mpris_player: return True

        if e.button == 1:
            self.mpris_player.previous()
            self._temp_icon(icons.prev)
        elif e.button == 3:
            self.mpris_player.next()
            self._temp_icon(icons.next)
        elif e.button == 2:
            self.mpris_player.play_pause()
            self._uicon()
        return True

    def _temp_icon(self, icon):
        self.mpris_button.get_child().set_markup(icon)
        if self._rtimer: GLib.source_remove(self._rtimer)
        self._rtimer = GLib.timeout_add(500, self._restore_icon)

    def _restore_icon(self):
        self._uicon()
        self._rtimer = None
        return False

    def _uicon(self):
        if getattr(self.mpris_player, "playback_status", "") == "playing":
            self.mpris_button.get_child().set_markup(icons.pause)
        else:
            self.mpris_button.get_child().set_markup(icons.play)

    def _on_appear(self, mgr, player):
        if not self.mpris_player:
            self._bind_player(MprisPlayer(player))
            self._apply()

    def _on_vanish(self, mgr, pn):
        if self.mpris_player and self.mpris_player.player_name == pn:
            if players := self.mpris_manager.players:
                self.current_index %= len(players)
                self._bind_player(MprisPlayer(players[self.current_index]))
            else:
                self._bind_player(None)
        elif not self.mpris_manager.players:
            self._bind_player(None)
        self._apply()

    def cleanup(self):
        if self._rtimer:
            GLib.source_remove(self._rtimer)
            self._rtimer = None
        self._bind_player(None)
        self.mpris_manager = None