from fabric.core.service import Property, Service, Signal

import os
import base64
import hashlib
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
_TITLE_FALLBACK_KEYS = ("xesam:title", "title", "xesam:name", "xesam:displayName")
_ARTIST_FALLBACK_KEYS = ("xesam:artist", "xesam:albumArtist", "xesam:composer", "artist")


def _decode_data_uri(data_uri: str) -> tuple[bytes | None, str]:
    if not data_uri or not data_uri.startswith("data:"):
        return None, ""
    try:
        if ',' not in data_uri:
            return None, ""
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
    if not data:
        return ""
    md5 = hashlib.md5(cache_key.encode()).hexdigest()
    cache_path = os.path.join(_CACHE_DIR, f"b64_{md5}{ext}")
    if os.path.isfile(cache_path):
        return cache_path
    try:
        with open(cache_path, 'wb') as f:
            f.write(data)
        return cache_path
    except Exception:
        return ""


def _probe_property(player: Playerctl.Player, prop: str):
    """Попытка получить свойство плеера, None если не поддерживается."""
    try:
        val = player.get_property(prop)
        return val
    except Exception:
        return None


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

        self._cached_arturl: str = ""
        self._cached_art_key: str = ""

        # Кешируем поддержку loop/shuffle при инициализации,
        # но разрешаем повторный probe если первый раз вернул None
        # (плеер мог ещё не полностью инициализироваться).
        self._supports_loop: bool | None = None
        self._supports_shuffle: bool | None = None
        self._probed_loop: bool = False
        self._probed_shuffle: bool = False

        signals = {
            "playback-status": lambda *args: self._notifier("playback-status"),
            "loop-status": lambda *args: self._on_loop_changed(),
            "shuffle": lambda *args: self._on_shuffle_changed(),
            "volume": lambda *args: self._notifier("volume"),
            "seeked": lambda *args: self._notifier("position"),
            "metadata": lambda *args: self._update_status(),
            "exit": self._on_exit,
        }

        for sn, cb in signals.items():
            try:
                self._signal_connectors[sn] = self._player.connect(sn, cb)
            except Exception:
                pass

        GLib.idle_add(self._update_status_once)

    # ── capability probing ──────────────────────────────────────────

    def _probe_loop_support(self) -> bool:
        """Динамически определяет, поддерживает ли плеер loop_status."""
        if self._dead or not self._player:
            return False
        try:
            val = self._player.get_property("loop_status")
            return val is not None
        except Exception:
            return False

    def _probe_shuffle_support(self) -> bool:
        """Динамически определяет, поддерживает ли плеер shuffle."""
        if self._dead or not self._player:
            return False
        try:
            val = self._player.get_property("shuffle")
            return val is not None
        except Exception:
            return False

    def _check_loop_support(self) -> bool:
        # Если уже успешно определили поддержку — используем кеш.
        # Если ранее probe дал False — пробуем ещё раз (ленивый re-probe).
        if self._supports_loop is True:
            return True
        result = self._probe_loop_support()
        if result:
            self._supports_loop = True
        return result

    def _check_shuffle_support(self) -> bool:
        if self._supports_shuffle is True:
            return True
        result = self._probe_shuffle_support()
        if result:
            self._supports_shuffle = True
        return result

    # ── signal handlers ─────────────────────────────────────────────

    def _on_loop_changed(self):
        # Раз получили сигнал loop-status — значит точно поддерживается.
        self._supports_loop = True
        self._notifier("loop-status")

    def _on_shuffle_changed(self):
        self._supports_shuffle = True
        self._notifier("shuffle")

    def _update_status(self):
        if self._dead:
            return
        for prop in ("metadata", "title", "artist", "album",
                     "arturl", "length", "position"):
            GLib.idle_add(lambda p=prop: (self._notifier(p), False))

    def _update_status_once(self):
        if self._dead:
            return False
        for prop in ("metadata", "title", "artist", "album",
                     "arturl", "length", "position", "playback_status"):
            self._notifier(prop)
        return False

    def _notifier(self, name: str):
        if self._dead:
            return

        def notify_and_emit():
            if not self._dead:
                self.notify(name)
                self.emit("changed")
            return False

        GLib.idle_add(notify_and_emit, priority=GLib.PRIORITY_DEFAULT_IDLE)

    def _on_exit(self, *_):
        self._mark_dead()

    def _mark_dead(self):
        if self._dead:
            return
        self._dead = True

        for sid in list(self._signal_connectors.values()):
            with contextlib.suppress(Exception):
                if self._player:
                    self._player.disconnect(sid)

        self._signal_connectors.clear()
        self._player = None
        GLib.idle_add(lambda: (self.emit("exit"), False))

    # ── metadata helpers ────────────────────────────────────────────

    def _get_metadata(self) -> dict:
        if not self._player or self._dead:
            return {}
        try:
            meta = self._player.get_property("metadata")
            if meta:
                result = {}
                for k in meta.keys():
                    try:
                        val = meta[k]
                        if hasattr(val, 'unpack'):
                            val = val.unpack()
                        result[k] = val
                    except Exception:
                        result[k] = meta[k]
                return result
        except Exception:
            pass
        return {}

    def _get_metadata_value(self, meta: dict, key: str) -> str:
        if key not in meta:
            return ""
        val = meta[key]
        if val is None:
            return ""
        if isinstance(val, (list, tuple)):
            return ", ".join(str(x).strip() for x in val if x)
        return str(val).strip()

    # ── public state ────────────────────────────────────────────────

    @property
    def is_dead(self) -> bool:
        return self._dead

    # ── transport controls ──────────────────────────────────────────

    def play_pause(self):
        if self._player and not self._dead:
            GLib.idle_add(lambda: (self._player.play_pause(), False))

    def play(self):
        if self._player and not self._dead:
            GLib.idle_add(lambda: (self._player.play(), False))

    def pause(self):
        if self._player and not self._dead:
            GLib.idle_add(lambda: (self._player.pause(), False))

    def stop(self):
        if self._player and not self._dead:
            GLib.idle_add(lambda: (self._player.stop(), False))

    def next(self):
        if self._player and not self._dead:
            GLib.idle_add(lambda: (self._player.next(), False))

    def previous(self):
        if self._player and not self._dead:
            GLib.idle_add(lambda: (self._player.previous(), False))

    def seek(self, offset_us: int):
        if self._player and not self._dead:
            GLib.idle_add(lambda: (self._player.seek(offset_us), False))

    def set_position(self, position_us: int):
        if self._player and not self._dead:
            GLib.idle_add(lambda: (self._player.set_position(position_us), False))

    # ── properties ──────────────────────────────────────────────────

    @Property(str, "readable", default_value="")
    def player_name(self) -> str:
        return self._name

    @Property(str, "readable", default_value="")
    def player_instance(self) -> str:
        return self._instance

    @Property(str, "readable", default_value="")
    def title(self) -> str:
        if not self._player or self._dead:
            return ""
        try:
            t = self._player.get_title()
            if t and isinstance(t, str) and t.strip():
                return t.strip()
        except Exception:
            pass
        meta = self._get_metadata()
        for key in _TITLE_FALLBACK_KEYS:
            val = self._get_metadata_value(meta, key)
            if val:
                return val
        return ""

    @Property(str, "readable", default_value="")
    def artist(self) -> str:
        if not self._player or self._dead:
            return ""
        try:
            a = self._player.get_artist()
            if a:
                if isinstance(a, (list, tuple)):
                    return ", ".join(str(x).strip() for x in a if x)
                elif str(a).strip():
                    return str(a).strip()
        except Exception:
            pass
        meta = self._get_metadata()
        for key in _ARTIST_FALLBACK_KEYS:
            val = self._get_metadata_value(meta, key)
            if val:
                return val
        return ""

    @Property(str, "readable", default_value="")
    def album(self) -> str:
        if not self._player or self._dead:
            return ""
        try:
            a = self._player.get_album()
            if a and str(a).strip():
                return str(a).strip()
        except Exception:
            pass
        meta = self._get_metadata()
        for key in _ALBUM_KEYS:
            val = self._get_metadata_value(meta, key)
            if val:
                return val
        return ""

    @Property(str, "readable", default_value="")
    def arturl(self) -> str:
        if not self._player or self._dead:
            return ""
        meta = self._get_metadata()
        raw_art_url = ""
        for key in _ART_KEYS:
            val = self._get_metadata_value(meta, key)
            if val:
                raw_art_url = val
                break

        if not raw_art_url:
            return ""

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
        if not self._player or self._dead:
            return 0
        try:
            meta = self._get_metadata()
            val = meta.get("mpris:length")
            return int(val) if val else 0
        except Exception:
            return 0

    @Property(int, "read-write", default_value=0)
    def position(self) -> int:
        if not self._player or self._dead:
            return 0
        try:
            pos = self._player.get_position()
            return int(pos) if pos else 0
        except Exception:
            return 0

    @position.setter
    def position(self, new_pos: int):
        self.set_position(new_pos)

    @Property(str, "readable", default_value="stopped")
    def playback_status(self) -> str:
        if not self._player or self._dead:
            return "stopped"
        try:
            raw = self._player.get_property("playback_status")
            return _STATUS_TO_STR.get(raw, "stopped")
        except Exception:
            return "stopped"

    @Property(str, "read-write", default_value="None")
    def loop_status(self) -> str:
        if not self._player or self._dead:
            return "None"
        if not self._check_loop_support():
            return "None"
        try:
            raw = self._player.get_property("loop_status")
            return _LOOP_TO_STR.get(raw, "None") if raw is not None else "None"
        except Exception:
            return "None"

    @loop_status.setter
    def loop_status(self, value: str):
        if not self._player or self._dead:
            return
        if not self._check_loop_support():
            return
        ls = _STR_TO_LOOP.get(value)
        if ls is None:
            return

        def _set_loop():
            try:
                self._player.set_loop_status(ls)
            except Exception:
                pass

        GLib.idle_add(_set_loop)

    @Property(bool, "readable", default_value=False)
    def can_set_loop_status(self) -> bool:
        if not self._player or self._dead:
            return False
        return self._check_loop_support()

    @Property(bool, "read-write", default_value=False)
    def shuffle(self) -> bool:
        if not self._player or self._dead:
            return False
        if not self._check_shuffle_support():
            return False
        try:
            sh = self._player.get_property("shuffle")
            return bool(sh) if sh is not None else False
        except Exception:
            return False

    @shuffle.setter
    def shuffle(self, value: bool):
        if not self._player or self._dead:
            return
        if not self._check_shuffle_support():
            return

        def _set_shuffle():
            try:
                self._player.set_shuffle(value)
            except Exception:
                pass

        GLib.idle_add(_set_shuffle)

    @Property(bool, "readable", default_value=False)
    def can_shuffle(self) -> bool:
        if not self._player or self._dead:
            return False
        return self._check_shuffle_support()

    @Property(bool, "readable", default_value=False)
    def can_go_next(self) -> bool:
        if not self._player or self._dead:
            return False
        try:
            return bool(self._player.get_property("can_go_next"))
        except Exception:
            return False

    @Property(bool, "readable", default_value=False)
    def can_go_previous(self) -> bool:
        if not self._player or self._dead:
            return False
        try:
            return bool(self._player.get_property("can_go_previous"))
        except Exception:
            return False

    @Property(bool, "readable", default_value=False)
    def can_seek(self) -> bool:
        if not self._player or self._dead:
            return False
        try:
            return bool(self._player.get_property("can_seek"))
        except Exception:
            return False

    @Property(bool, "readable", default_value=False)
    def can_pause(self) -> bool:
        if not self._player or self._dead:
            return False
        try:
            return bool(self._player.get_property("can_pause"))
        except Exception:
            return False

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

        self._sig_appeared = self._manager.connect(
            "name-appeared", self._on_appeared
        )
        self._sig_vanished = self._manager.connect(
            "name-vanished", self._on_vanished
        )

        self._add_existing_players()

    def _add_existing_players(self):
        try:
            for player_name in self._manager.get_property("player-names") or []:
                player = Playerctl.Player.new_from_name(player_name)
                self._manager.manage_player(player)
                self.emit("player-appeared", player)
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
        try:
            pid = player_name.instance if player_name.instance else player_name.name
        except Exception:
            pid = getattr(player_name, "name", str(player_name))

        if pid:
            self.emit("player-vanished", str(pid))

    @Property(object, "readable")
    def players(self):
        if not self._manager:
            return []
        try:
            return self._manager.get_property("players") or []
        except Exception:
            return []

    @Property(list, "readable")
    def player_names(self):
        if not self._manager:
            return []
        try:
            names = self._manager.get_property("player-names") or []
            return [getattr(n, "name", str(n)) for n in names]
        except Exception:
            return []

    def destroy(self):
        if self._manager:
            try:
                self._manager.disconnect(self._sig_appeared)
                self._manager.disconnect(self._sig_vanished)
            except Exception:
                pass
            self._manager = None