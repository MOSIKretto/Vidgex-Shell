from fabric.core.service import Property, Service, Signal

import os
import base64
import hashlib
import time
import contextlib
import gi

gi.require_version("Playerctl", "2.0")
from gi.repository import GLib, Playerctl

_LOOP_TO_STR = {
    Playerctl.LoopStatus.NONE: "None",
    Playerctl.LoopStatus.TRACK: "Track",
    Playerctl.LoopStatus.PLAYLIST: "Playlist",
}
_STR_TO_LOOP = {v: k for k, v in _LOOP_TO_STR.items()}

_STATUS_TO_STR = {
    Playerctl.PlaybackStatus.PAUSED: "paused",
    Playerctl.PlaybackStatus.PLAYING: "playing",
    Playerctl.PlaybackStatus.STOPPED: "stopped",
}

_CACHE_DIR = os.path.join(GLib.get_user_cache_dir(), "vidgex-shell", "covers")
os.makedirs(_CACHE_DIR, exist_ok=True)

_ART_KEYS = ("mpris:artUrl", "xesam:artUrl")
_ALBUM_KEYS = ("xesam:album", "xesam:albumTitle", "album")

_NO_LOOP_PLAYERS = frozenset({
    'telegram', 'telegramdesktop', 'telegram-desktop',
    'ayugram', 'kotatogram', '64gram', 'nekogram', 'forkgram',
    'materialgram', 'unigram',
    'firefox', 'chromium', 'chrome', 'google-chrome', 'brave', 'brave-browser',
    'edge', 'microsoft-edge', 'opera', 'vivaldi', 'webkit', 'epiphany',
    'librewolf', 'waterfox', 'floorp', 'zen-browser',
    'discord', 'slack', 'teams', 'skype', 'zoom',
})

def _normalize_player_name(name: str) -> str:
    if not name: return ""
    base = name.split('.')[0].lower()
    if base.startswith('org.mpris.mediaplayer2.'): base = base[23:]
    return base.replace('-', '').replace('_', '')

def _is_telegram_like(name: str) -> bool:
    n = _normalize_player_name(name)
    telegram_names = ('telegram', 'ayugram', 'kotatogram', '64gram', 
                      'nekogram', 'forkgram', 'materialgram', 'unigram')
    return any(t in n for t in telegram_names)

def _is_browser_like(name: str) -> bool:
    n = _normalize_player_name(name)
    browser_names = ('firefox', 'chrome', 'chromium', 'brave', 'edge', 
                     'opera', 'vivaldi', 'webkit', 'epiphany', 'librewolf',
                     'waterfox', 'floorp', 'zen')
    return any(b in n for b in browser_names)

def _decode_data_uri(data_uri: str) -> tuple[bytes | None, str]:
    if not data_uri or not data_uri.startswith("data:"): return None, ""
    try:
        if ',' not in data_uri: return None, ""
        header, encoded_data = data_uri.split(',', 1)
        header = header[5:]
        parts = header.split(';')
        mime_type = parts[0] if parts else "image/png"
        is_base64 = 'base64' in parts
        ext_map = {
            'image/jpeg': '.jpg', 'image/jpg': '.jpg',
            'image/png': '.png', 'image/gif': '.gif',
            'image/webp': '.webp', 'image/bmp': '.bmp',
        }
        ext = ext_map.get(mime_type.lower(), '.png')
        if is_base64:
            encoded_data += "=" * ((4 - len(encoded_data) % 4) % 4)
            data = base64.b64decode(encoded_data)
        else:
            data = encoded_data.encode('utf-8')
        return data, ext
    except Exception:
        return None, ""

def _save_data_uri_to_cache(data_uri: str, cache_key: str) -> str:
    data, ext = _decode_data_uri(data_uri)
    if not data: return ""
    md5 = hashlib.md5(cache_key.encode()).hexdigest()
    cache_path = os.path.join(_CACHE_DIR, f"b64_{md5}{ext}")
    if os.path.isfile(cache_path): return cache_path
    try:
        with open(cache_path, 'wb') as f: f.write(data)
        return cache_path
    except Exception: return ""


class MprisPlayer(Service):
    @Signal
    def changed(self) -> None: ...

    @Signal
    def exit(self) -> None: ...

    def __init__(self, player: Playerctl.Player, **kwargs):
        super().__init__(**kwargs)
        self._player: Playerctl.Player | None = player
        self._signal_connectors: dict = {}
        self._dead: bool = False

        try:
            self._name = player.get_property("player-name") or ""
        except Exception:
            self._name = ""

        try:
            self._instance = player.get_property("player-instance") or self._name
        except Exception:
            self._instance = self._name

        self._normalized_name = _normalize_player_name(self._name)
        self._is_telegram = _is_telegram_like(self._name)
        self._is_browser = _is_browser_like(self._name)
        self._is_limited = self._normalized_name in _NO_LOOP_PLAYERS or self._is_telegram or self._is_browser

        self._cached_arturl: str = ""
        self._cached_art_key: str = ""

        self._last_track_hash = ""
        self._fake_pos_start = 0.0
        self._use_fake_pos = False
        self._stale_length = -1
        self._track_change_time = 0.0

        for sn in ["playback-status", "loop-status", "shuffle", "volume", "seeked"]:
            self._signal_connectors[sn] = self._player.connect(
                sn, lambda *args, sn=sn: self._notifier(sn)
            )

        self._signal_connectors["metadata"] = self._player.connect(
            "metadata", lambda *args: self._update_status()
        )
        self._signal_connectors["exit"] = self._player.connect(
            "exit", self._on_exit
        )

        GLib.idle_add(self._update_status_once)

    def _update_status(self):
        if not self._dead:
            current_hash = f"{self.track_id}_{self.title}"
            
            if not getattr(self, '_last_track_hash', ""):
                self._last_track_hash = current_hash
            elif current_hash != self._last_track_hash:
                self._last_track_hash = current_hash
                self._fake_pos_start = time.time()
                self._use_fake_pos = True
                self._stale_length = self.length
                self._track_change_time = time.time()

        def notify_property(prop):
            if hasattr(self, prop) and not self._dead:
                self._notifier(prop)
                
        for prop in ["metadata", "title", "artist", "album", "arturl", "length", "position"]:
            GLib.idle_add(lambda p=prop: (notify_property(p), False))

    def _update_status_once(self):
        if self._dead: return False
        for prop in ["metadata", "title", "artist", "album", "arturl", "length", "position", "playback_status"]:
            self._notifier(prop)
        return False

    def _notifier(self, name: str):
        if self._dead: return
        def notify_and_emit():
            if not self._dead:
                self.notify(name)
                self.emit("changed")
            return False
        GLib.idle_add(notify_and_emit, priority=GLib.PRIORITY_DEFAULT_IDLE)

    def _on_exit(self, *_):
        self._mark_dead()

    def _mark_dead(self):
        if self._dead: return
        self._dead = True
        
        for id in list(self._signal_connectors.values()):
            with contextlib.suppress(Exception):
                if self._player:
                    self._player.disconnect(id)
        
        self._signal_connectors.clear()
        self._player = None
        GLib.idle_add(lambda: (self.emit("exit"), False))

    def _get_metadata(self) -> dict:
        if not self._player or self._dead: return {}
        try:
            meta = self._player.get_property("metadata")
            if meta:
                result = {}
                for k in meta.keys():
                    try:
                        val = meta[k]
                        if hasattr(val, 'unpack'): val = val.unpack()
                        result[k] = val
                    except Exception:
                        result[k] = meta[k]
                return result
        except Exception: pass
        return {}

    def _get_metadata_value(self, meta: dict, key: str) -> str:
        if key not in meta: return ""
        val = meta[key]
        if val is None: return ""
        if isinstance(val, (list, tuple)): return ", ".join(str(x).strip() for x in val if x)
        return str(val).strip()

    @property
    def is_dead(self) -> bool: return self._dead

    def play_pause(self):
        if self._player and not self._dead and self.can_pause:
            GLib.idle_add(lambda: (self._player.play_pause(), False))

    def play(self):
        if self._player and not self._dead:
            GLib.idle_add(lambda: (self._player.play(), False))

    def pause(self):
        if self._player and not self._dead and self.can_pause:
            GLib.idle_add(lambda: (self._player.pause(), False))

    def stop(self):
        if self._player and not self._dead:
            GLib.idle_add(lambda: (self._player.stop(), False))

    def next(self):
        if self._player and not self._dead and self.can_go_next:
            GLib.idle_add(lambda: (self._player.next(), False))

    def previous(self):
        if self._player and not self._dead and self.can_go_previous:
            GLib.idle_add(lambda: (self._player.previous(), False))

    def seek(self, offset_us: int):
        if self._player and not self._dead and self.can_seek:
            GLib.idle_add(lambda: (self._player.seek(offset_us), False))

    def set_position(self, position_us: int):
        if self._player and not self._dead and self.can_seek:
            GLib.idle_add(lambda: (self._player.set_position(position_us), False))

    @Property(str, "readable", default_value="")
    def player_name(self) -> str: return self._name

    @Property(str, "readable", default_value="")
    def player_instance(self) -> str: return self._instance

    @Property(bool, "readable", default_value=False)
    def is_telegram(self) -> bool: return self._is_telegram

    @Property(bool, "readable", default_value=False)
    def is_browser(self) -> bool: return self._is_browser

    @Property(bool, "readable", default_value=False)
    def is_limited(self) -> bool: return self._is_limited

    @Property(str, "readable", default_value="")
    def title(self) -> str:
        if not self._player or self._dead: return ""
        try:
            t = self._player.get_title()
            if t and isinstance(t, str) and t.strip(): return t.strip()
        except Exception: pass
        meta = self._get_metadata()
        title = self._get_metadata_value(meta, "xesam:title")
        if title: return title
        if self._is_telegram:
            for key in ("title", "xesam:name", "xesam:displayName"):
                val = self._get_metadata_value(meta, key)
                if val: return val
        return ""

    @Property(str, "readable", default_value="")
    def artist(self) -> str:
        if not self._player or self._dead: return ""
        try:
            a = self._player.get_artist()
            if a:
                if isinstance(a, (list, tuple)): return ", ".join(str(x).strip() for x in a if x)
                elif str(a).strip(): return str(a).strip()
        except Exception: pass
        meta = self._get_metadata()
        for key in ("xesam:artist", "xesam:albumArtist", "xesam:composer", "artist"):
            val = self._get_metadata_value(meta, key)
            if val: return val
        return ""

    @Property(str, "readable", default_value="")
    def album(self) -> str:
        if not self._player or self._dead: return ""
        try:
            a = self._player.get_album()
            if a and str(a).strip(): return str(a).strip()
        except Exception: pass
        meta = self._get_metadata()
        for key in _ALBUM_KEYS:
            val = self._get_metadata_value(meta, key)
            if val: return val
        return ""

    @Property(str, "readable", default_value="")
    def arturl(self) -> str:
        if not self._player or self._dead: return ""
        meta = self._get_metadata()
        raw_art_url = ""
        for key in _ART_KEYS:
            val = self._get_metadata_value(meta, key)
            if val: raw_art_url = val; break
        if not raw_art_url: return ""
        
        # Браузеры (Spotify Web) часто шлют Base64
        if raw_art_url.startswith("data:"):
            cache_key = f"{self.title}_{self.artist}_{len(raw_art_url)}"
            if cache_key == self._cached_art_key and self._cached_arturl:
                return self._cached_arturl
            file_path = _save_data_uri_to_cache(raw_art_url, cache_key)
            if file_path:
                self._cached_art_key = cache_key
                self._cached_arturl = f"file://{file_path}"
                return self._cached_arturl
            return ""
            
        if raw_art_url.startswith(('http://', 'https://', 'file://', '/')):
            return raw_art_url
            
        if raw_art_url.startswith('open.spotify.com'):
            return 'https://' + raw_art_url
            
        return ""

    @Property(str, "readable", default_value="")
    def track_id(self) -> str:
        return self._get_metadata_value(self._get_metadata(), "mpris:trackid")

    @Property(str, "readable", default_value="")
    def url(self) -> str:
        return self._get_metadata_value(self._get_metadata(), "xesam:url")

    @Property(int, "readable", default_value=0)
    def length(self) -> int:
        if not self._player or self._dead: return 0
        try:
            meta = self._get_metadata()
            if "mpris:length" in meta and meta["mpris:length"] is not None:
                l = int(meta["mpris:length"])
                
                stale = getattr(self, '_stale_length', -1)
                if stale > 0 and l == stale:
                    if time.time() - getattr(self, '_track_change_time', 0) < 3.0:
                        return 0
                        
                return l
        except Exception: pass
        return 0

    @Property(int, "read-write", default_value=0)
    def position(self) -> int:
        if not self._player or self._dead: return 0
        try:
            real_pos = int(self._player.get_property("position") or 0)
            length = self.length

            if getattr(self, '_use_fake_pos', False):
                elapsed_us = int((time.time() - self._fake_pos_start) * 1_000_000)
                if real_pos < 4_000_000 or abs(real_pos - elapsed_us) < 2_000_000:
                    self._use_fake_pos = False
                else:
                    return min(elapsed_us, length) if length > 0 else elapsed_us

            if length > 0 and real_pos > length + 2_000_000:
                return 0
                
            return real_pos
        except Exception:
            return 0
            
    @position.setter
    def position(self, new_pos: int):
        self.set_position(new_pos)

    @Property(str, "readable", default_value="stopped")
    def playback_status(self) -> str:
        if not self._player or self._dead: return "stopped"
        try:
            raw = self._player.get_property("playback_status")
            return _STATUS_TO_STR.get(raw, "stopped")
        except Exception:
            return "stopped"

    @Property(str, "read-write", default_value="None")
    def loop_status(self) -> str:
        if not self._player or self._dead or self._is_limited: return "None"
        try:
            raw = self._player.get_property("loop_status")
            return _LOOP_TO_STR.get(raw, "None") if raw is not None else "None"
        except Exception: return "None"

    @loop_status.setter
    def loop_status(self, value: str):
        if not self._player or self._dead or self._is_limited: return
        ls = _STR_TO_LOOP.get(value)
        if ls is None: return
        def _set_loop():
            try: self._player.set_loop_status(ls)
            except Exception: pass
        GLib.idle_add(_set_loop)

    @Property(bool, "readable", default_value=False)
    def can_set_loop_status(self) -> bool:
        if not self._player or self._dead or self._is_limited: return False
        try: return self._player.get_property("loop_status") is not None
        except Exception: return False

    @Property(bool, "read-write", default_value=False)
    def shuffle(self) -> bool:
        if not self._player or self._dead or self._is_limited: return False
        try:
            sh = self._player.get_property("shuffle")
            return bool(sh) if sh is not None else False
        except Exception: return False

    @shuffle.setter
    def shuffle(self, value: bool):
        if not self._player or self._dead or self._is_limited: return
        def _set_shuffle():
            try: self._player.set_shuffle(value)
            except Exception: pass
        GLib.idle_add(_set_shuffle)

    @Property(bool, "readable", default_value=False)
    def can_shuffle(self) -> bool:
        if not self._player or self._dead or self._is_limited: return False
        try: return self._player.get_property("shuffle") is not None
        except Exception: return False

    @Property(bool, "readable", default_value=False)
    def can_go_next(self) -> bool:
        if not self._player or self._dead: return False
        try: return bool(self._player.get_property("can_go_next"))
        except Exception: return False

    @Property(bool, "readable", default_value=False)
    def can_go_previous(self) -> bool:
        if not self._player or self._dead: return False
        try: return bool(self._player.get_property("can_go_previous"))
        except Exception: return False

    @Property(bool, "readable", default_value=False)
    def can_seek(self) -> bool:
        if not self._player or self._dead: return False
        try: return bool(self._player.get_property("can_seek"))
        except Exception: return False

    @Property(bool, "readable", default_value=False)
    def can_pause(self) -> bool:
        if not self._player or self._dead: return False
        try: return bool(self._player.get_property("can_pause"))
        except Exception: return False

    @Property(object, "readable")
    def metadata(self) -> dict:
        return self._get_metadata()


class MprisPlayerManager(Service):
    @Signal
    def player_appeared(self, player: object) -> None: ...

    @Signal
    def player_vanished(self, player_id: str) -> None: ...

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._manager = Playerctl.PlayerManager.new()
        
        self._sig_appeared = self._manager.connect("name-appeared", self._on_appeared)
        self._sig_vanished = self._manager.connect("name-vanished", self._on_vanished)
        
        self._add_existing_players()

    def _add_existing_players(self):
        try:
            for player_name in self._manager.get_property("player-names") or []:
                player = Playerctl.Player.new_from_name(player_name)
                self._manager.manage_player(player)
        except Exception:
            pass

    def _on_appeared(self, manager, player_name):
        try:
            new_player = Playerctl.Player.new_from_name(player_name)
            manager.manage_player(new_player)
            self.emit("player-appeared", new_player)
        except Exception:
            pass

    def _on_vanished(self, manager, player_name):
        pid = str(player_name)
        if pid:
            self.emit("player-vanished", pid)

    @Property(object, "readable")
    def players(self):
        if not self._manager: return []
        try: return self._manager.get_property("players") or []
        except Exception: return []

    @Property(list, "readable")
    def player_names(self):
        if not self._manager: return []
        try:
            names = self._manager.get_property("player-names") or []
            return [getattr(n, "name", str(n)) for n in names]
        except Exception: return []

    def destroy(self):
        if self._manager:
            try:
                self._manager.disconnect(self._sig_appeared)
                self._manager.disconnect(self._sig_vanished)
            except Exception: pass
            self._manager = None