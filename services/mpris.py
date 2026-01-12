from fabric.core.service import Property, Service, Signal
from fabric.utils import bulk_connect

import gi
from gi.repository import GLib

import contextlib


class PlayerctlImportError(ImportError):
    def __init__(self, *args):
        super().__init__(
            "Playerctl is not installed, please install it first",
            *args,
        )

try:
    gi.require_version("Playerctl", "2.0")
    from gi.repository import Playerctl
except ValueError:
    raise PlayerctlImportError


class MprisPlayer(Service):
    """A service to manage a mpris player."""

    @Signal
    def exit(self, value: bool) -> bool: ...

    @Signal
    def changed(self) -> None: ...

    def __init__(
        self,
        player: Playerctl.Player,
        **kwargs,
    ):
        self._signal_connectors: dict = {}
        self._player: Playerctl.Player = player
        super().__init__(**kwargs)
        # Connect to player signals
        for signal_name in ["playback-status", "loop-status", "shuffle", "volume", "seeked"]:
            self._signal_connectors[signal_name] = self._player.connect(
                signal_name,
                lambda *args, sn=signal_name: self.notifier(sn, args),
            )

        self._signal_connectors["exit"] = self._player.connect(
            "exit",
            self.on_player_exit,
        )
        self._signal_connectors["metadata"] = self._player.connect(
            "metadata",
            lambda *args: self.update_status(),
        )
        GLib.idle_add(lambda *args: self.update_status_once())

    def update_status(self):
        def notify_property(prop):
            if self.get_property(prop) is not None:
                self.notifier(prop)

        # Properties that depend on metadata
        metadata_props = [
            "metadata",
            "title",
            "artist",
            "arturl",
            "length",
        ]
        
        # Properties that don't depend on metadata
        general_props = [
            "can-seek",
            "can-pause",
            "can-shuffle",
            "can-go-next",
            "can-go-previous",
        ]

        # Schedule property updates
        for prop in metadata_props:
            GLib.idle_add(lambda p=prop: (notify_property(p), False))
        
        for prop in general_props:
            GLib.idle_add(lambda p=prop: (self.notifier(p), False))

    def update_status_once(self):
        def notify_all():
            for prop in self.list_properties():
                self.notifier(prop.name)
            return False
        GLib.idle_add(notify_all, priority=GLib.PRIORITY_DEFAULT_IDLE)

    def notifier(self, name: str, args=None):
        def notify_and_emit():
            self.notify(name)
            self.emit("changed")
            return False
        GLib.idle_add(notify_and_emit, priority=GLib.PRIORITY_DEFAULT_IDLE)

    def on_player_exit(self, player):
        for handler_id in list(self._signal_connectors.values()):
            with contextlib.suppress(Exception):
                self._player.disconnect(handler_id)
        self._signal_connectors.clear()
        GLib.idle_add(lambda: (self.emit("exit", True), False))
        self._player = None

    def toggle_shuffle(self):
        if self.can_shuffle:
            GLib.idle_add(lambda: setattr(self, 'shuffle', not self.shuffle))

    def play_pause(self):
        if self.can_pause:
            GLib.idle_add(lambda: self._player.play_pause())

    def next(self):
        if self.can_go_next:
            GLib.idle_add(lambda: self._player.next())

    def previous(self):
        if self.can_go_previous:
            GLib.idle_add(lambda: self._player.previous())

    @Property(str, "readable")
    def player_name(self) -> str:
        return self._player.get_property("player-name")

    @Property(int, "read-write", default_value=0)
    def position(self) -> int:
        return self._player.get_property("position")

    @position.setter
    def position(self, new_pos: int):
        self._player.set_position(new_pos)

    @Property(object, "readable")
    def metadata(self) -> dict:
        return self._player.get_property("metadata")

    @Property(str, "readable")
    def arturl(self) -> str:
        metadata = self.metadata
        return metadata.get("mpris:artUrl", None)

    @Property(str, "readable")
    def length(self) -> str:
        metadata = self.metadata
        return metadata.get("mpris:length", None)

    @Property(str, "readable")
    def artist(self) -> str:
        artist = self._player.get_artist()
        if isinstance(artist, (list, tuple)):
            return ", ".join(artist)
        return artist if artist else ""

    @Property(str, "readable")
    def album(self) -> str:
        album = self._player.get_album()
        return album if album else ""

    @Property(str, "readable")
    def title(self) -> str:
        if self._player is None:
            return ""
        title_data = self._player.get_title()
        return title_data if isinstance(title_data, str) else ""

    @Property(bool, "read-write", default_value=False)
    def shuffle(self) -> bool:
        return self._player.get_property("shuffle")

    @shuffle.setter
    def shuffle(self, do_shuffle: bool):
        result = self._player.set_shuffle(do_shuffle)
        self.notifier("shuffle")
        return result

    @Property(str, "readable")
    def playback_status(self) -> str:
        status_map = {
            Playerctl.PlaybackStatus.PAUSED: "paused",
            Playerctl.PlaybackStatus.PLAYING: "playing",
            Playerctl.PlaybackStatus.STOPPED: "stopped",
        }
        raw_status = self._player.get_property("playback_status")
        return status_map.get(raw_status, "unknown")

    @Property(str, "read-write")
    def loop_status(self) -> str:
        status_map = {
            Playerctl.LoopStatus.NONE: "none",
            Playerctl.LoopStatus.TRACK: "track",
            Playerctl.LoopStatus.PLAYLIST: "playlist",
        }
        raw_status = self._player.get_property("loop_status")
        return status_map.get(raw_status, "unknown")

    @loop_status.setter
    def loop_status(self, status: str):
        loop_status_map = {
            "none": Playerctl.LoopStatus.NONE,
            "track": Playerctl.LoopStatus.TRACK,
            "playlist": Playerctl.LoopStatus.PLAYLIST,
        }
        mapped_status = loop_status_map.get(status)
        if mapped_status:
            self._player.set_loop_status(mapped_status)

    @Property(bool, "readable", default_value=False)
    def can_go_next(self) -> bool:
        return self._player.get_property("can_go_next")

    @Property(bool, "readable", default_value=False)
    def can_go_previous(self) -> bool:
        return self._player.get_property("can_go_previous")

    @Property(bool, "readable", default_value=False)
    def can_seek(self) -> bool:
        return self._player.get_property("can_seek")

    @Property(bool, "readable", default_value=False)
    def can_pause(self) -> bool:
        return self._player.get_property("can_pause")

    @Property(bool, "readable", default_value=False)
    def can_shuffle(self) -> bool:
        try:
            current_shuffle = self._player.get_property("shuffle")
            self._player.set_shuffle(current_shuffle)
            return True
        except Exception:
            return False

    @Property(bool, "readable", default_value=False)
    def can_loop(self) -> bool:
        try:
            current_loop = self._player.get_property("loop_status")
            self._player.set_loop_status(current_loop)
            return True
        except Exception:
            return False


class MprisPlayerManager(Service):
    @Signal
    def player_appeared(self, player: Playerctl.Player) -> Playerctl.Player: ...

    @Signal
    def player_vanished(self, player_name: str) -> str: ...

    def __init__(
        self,
        **kwargs,
    ):
        self._manager = Playerctl.PlayerManager.new()
        bulk_connect(
            self._manager,
            {
                "name-appeared": self.on_name_appeared,
                "name-vanished": self.on_name_vanished,
            },
        )
        self.add_players()
        super().__init__(**kwargs)

    def on_name_appeared(self, manager, player_name: Playerctl.PlayerName):
        new_player = Playerctl.Player.new_from_name(player_name)
        manager.manage_player(new_player)
        self.emit("player-appeared", new_player)

    def on_name_vanished(self, manager, player_name: Playerctl.PlayerName):
        self.emit("player-vanished", player_name.name)

    def add_players(self):
        for player in self._manager.get_property("player-names"):
            self._manager.manage_player(Playerctl.Player.new_from_name(player))

    @Property(object, "readable")
    def players(self):
        return self._manager.get_property("players")
    
    def destroy(self):
        """Cleanup resources"""
        if hasattr(self, '_manager') and self._manager:
            self._manager.disconnect_by_func(self.on_name_appeared)
            self._manager.disconnect_by_func(self.on_name_vanished)
            self._manager = None