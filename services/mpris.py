from fabric.core.service import Property, Service, Signal
from fabric.utils import bulk_connect

import gi
from gi.repository import GLib

import contextlib
from loguru import logger

class PlayerctlImportError(ImportError):
    """An error to raise when playerctl is not installed."""
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
        for sn in ["playback-status", "loop-status", "shuffle", "volume", "seeked"]:
            self._signal_connectors[sn] = self._player.connect(
                sn,
                lambda *args, sn=sn: self.notifier(sn, args),
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
        for prop in [
            "metadata",
            "title",
            "artist",
            "arturl",
            "length",
        ]:
            GLib.idle_add(lambda p=prop: (notify_property(p), False))
        for prop in [
            "can-seek",
            "can-pause",
            "can-shuffle",
            "can-go-next",
            "can-go-previous",
        ]:
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
        for id in list(self._signal_connectors.values()):
            with contextlib.suppress(Exception):
                self._player.disconnect(id)
        del self._signal_connectors
        GLib.idle_add(lambda: (self.emit("exit", True), False))
        del self._player

    def toggle_shuffle(self):
        if self.can_shuffle:
            GLib.idle_add(lambda: (setattr(self, 'shuffle', not self.shuffle), False))

    def play_pause(self):
        if self.can_pause:
            GLib.idle_add(lambda: (self._player.play_pause(), False))

    def next(self):
        if self.can_go_next:
            GLib.idle_add(lambda: (self._player.next(), False))

    def previous(self):
        if self.can_go_previous:
            GLib.idle_add(lambda: (self._player.previous(), False))

    @Property(str, "readable")
    def player_name(self) -> int:
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

    @Property(str or None, "readable")
    def arturl(self) -> str | None:
        if "mpris:artUrl" in self.metadata.keys():
            return self.metadata["mpris:artUrl"]
        return None

    @Property(str or None, "readable")
    def length(self) -> str | None:
        if "mpris:length" in self.metadata.keys():
            return self.metadata["mpris:length"]
        return None

    @Property(str, "readable")
    def artist(self) -> str:
        artist = self._player.get_artist()
        if isinstance(artist, (list, tuple)):
            return ", ".join(artist)
        return artist

    @Property(str, "readable")
    def album(self) -> str:
        return self._player.get_album()

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
        self.notifier("shuffle")
        return self._player.set_shuffle(do_shuffle)

    @Property(str, "readable")
    def playback_status(self) -> str:
        return {
            Playerctl.PlaybackStatus.PAUSED: "paused",
            Playerctl.PlaybackStatus.PLAYING: "playing",
            Playerctl.PlaybackStatus.STOPPED: "stopped",
        }.get(self._player.get_property("playback_status"), "unknown")

    @Property(str, "read-write")
    def loop_status(self) -> str:
        return {
            Playerctl.LoopStatus.NONE: "none",
            Playerctl.LoopStatus.TRACK: "track",
            Playerctl.LoopStatus.PLAYLIST: "playlist",
        }.get(self._player.get_property("loop_status"), "unknown")

    @loop_status.setter
    def loop_status(self, status: str):
        loop_status = {
            "none": Playerctl.LoopStatus.NONE,
            "track": Playerctl.LoopStatus.TRACK,
            "playlist": Playerctl.LoopStatus.PLAYLIST,
        }.get(status)
        self._player.set_loop_status(loop_status) if loop_status else None

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
            self._player.set_shuffle(self._player.get_property("shuffle"))
            return True
        except Exception:
            return False

    @Property(bool, "readable", default_value=False)
    def can_loop(self) -> bool:
        try:
            self._player.set_shuffle(self._player.get_property("shuffle"))
            return True
        except Exception:
            return False


class MprisPlayerManager(Service):
    """A service to manage mpris players."""

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
                "name-appeared": self.on_name_appeard,
                "name-vanished": self.on_name_vanished,
            },
        )
        self.add_players()
        super().__init__(**kwargs)

    def on_name_appeard(self, manager, player_name: Playerctl.PlayerName):
        logger.info(f"[MprisPlayer] {player_name.name} appeared")
        new_player = Playerctl.Player.new_from_name(player_name)
        manager.manage_player(new_player)
        self.emit("player-appeared", new_player)

    def on_name_vanished(self, manager, player_name: Playerctl.PlayerName):
        logger.info(f"[MprisPlayer] {player_name.name} vanished")
        self.emit("player-vanished", player_name.name)

    def add_players(self):
        for player in self._manager.get_property("player-names"):
            self._manager.manage_player(Playerctl.Player.new_from_name(player))

    @Property(object, "readable")
    def players(self):
        return self._manager.get_property("players")