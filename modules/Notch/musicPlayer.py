from itertools import chain

from gi.repository import Gdk, Gtk

from fabric.audio.service import Audio
from fabric.widgets.box import Box
from fabric.widgets.label import Label

from modules.Notch.MusicPlayer.player import LocalPlayer, MediaPlayer
from modules.Notch.MusicPlayer.mixer import MixerSection
from modules.Notch.MusicPlayer.musicLibrary import TrackList


_GAP = 8
_AUDIO_SIGNALS = ("changed", "stream-added", "stream-removed")

class Player(Box):
    __slots__ = (
        "audio",
        "_out",
        "_inp",
        "_sigs",
        "local_player",
        "media_player",
        "track_list",
        "_toplevel_click_sig",
    )

    def __init__(self, **kwargs):
        super().__init__(
            name="dash-player",
            orientation="h",
            spacing=_GAP,
            homogeneous=False,
            h_align="fill",
            v_align="fill",
            h_expand=True,
            v_expand=True,
            visible=True,
            **kwargs,
        )
        self._sigs = []
        self._toplevel_click_sig = None
        self._out = None
        self._inp = None

        local_player = LocalPlayer()
        self.local_player = local_player

        media_player = MediaPlayer(local_player=local_player)
        self.media_player = media_player

        self.track_list = TrackList(
            local_player=local_player,
            media_player_ref=media_player,
        )

        left_col = Box(
            name="player-left",
            orientation="v",
            spacing=_GAP,
            h_align="fill",
            v_align="fill",
            h_expand=True,
            v_expand=True,
            children=(media_player, self._build_mixer()),
        )
        left_col.set_size_request(400, -1)

        self.add(left_col)
        self.add(self.track_list)

        self.set_size_request(800, 350)
        media_player.set_size_request(-1, 200)

        self.connect("realize", self._on_realize)
        self.show_all()

    def _build_mixer(self):
        try:
            audio = Audio()
        except Exception as e:
            self.audio = None
            return Label(
                label=f"Audio unavailable: {e}",
                h_align="center",
                v_align="center",
                h_expand=True,
                v_expand=True,
            )

        self.audio = audio
        out = self._out = MixerSection("Outputs")
        inp = self._inp = MixerSection("Inputs")

        upd = self._upd
        sigs_append = self._sigs.append
        for sig in _AUDIO_SIGNALS:
            sigs_append((audio, audio.connect(sig, upd)))
        upd()

        return Box(
            name="mixer-row",
            orientation="h",
            spacing=_GAP,
            h_expand=True,
            v_expand=True,
            homogeneous=True,
            children=(out, inp),
        )

    def _upd(self, *_args):
        audio = self.audio
        if not audio:
            return

        speaker = audio.speaker
        mic = audio.microphone
        apps = audio.applications or ()
        recs = audio.recorders or ()

        self._out.update_streams(
            chain((speaker,), apps) if speaker else apps,
        )
        self._inp.update_streams(
            chain((mic,), recs) if mic else recs,
        )

    def _on_realize(self, _widget):
        if self._toplevel_click_sig:
            return
        toplevel = self.get_toplevel()
        if isinstance(toplevel, Gtk.Window):
            toplevel.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            self._toplevel_click_sig = toplevel.connect(
                "button-press-event", self._on_toplevel_click,
            )

    def _on_toplevel_click(self, widget, event):
        track_list = self.track_list
        search = track_list._search_entry
        if (
            search.has_focus()
            and not track_list._is_click_on_search(widget, event.x, event.y)
        ):
            widget.set_focus(None)
        return False

    def cleanup(self):
        if sig := self._toplevel_click_sig:
            self._toplevel_click_sig = None
            try:
                toplevel = self.get_toplevel()
                if toplevel:
                    toplevel.disconnect(sig)
            except Exception:
                pass

        for obj, sid in self._sigs:
            try:
                obj.disconnect(sid)
            except Exception:
                pass
        self._sigs.clear()

        if self._out:
            self._out.cleanup()
        if self._inp:
            self._inp.cleanup()

        self.media_player.cleanup()
        self.track_list.cleanup()
        self.audio = None