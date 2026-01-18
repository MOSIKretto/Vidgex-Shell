from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.circularprogressbar import CircularProgressBar
from fabric.widgets.label import Label
from fabric.widgets.overlay import Overlay
from fabric.widgets.stack import Stack

from gi.repository import Gdk, Gio, GLib, Gtk

import os
import tempfile
import urllib.parse
import urllib.request

import modules.icons as icons
from services.mpris import MprisPlayer, MprisPlayerManager
from widgets.circle_image import CircleImage


WALLPAPER_PATH = os.path.expanduser("~/.current.wall")


def add_hover_cursor(widget):
    widget.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
    
    def on_enter(w, _):
        if win := w.get_window():
            win.set_cursor(Gdk.Cursor.new_from_name(w.get_display(), "pointer"))
        
    def on_leave(w, _):
        if win := w.get_window():
            win.set_cursor(None)
    
    widget.connect("enter-notify-event", on_enter)
    widget.connect("leave-notify-event", on_leave)


class PlayerBox(Box):
    def __init__(self, mpris_player=None):
        super().__init__(orientation="v", h_align="fill", spacing=0, h_expand=False, v_expand=True)
        self.mpris_player = mpris_player
        self._progress_timer_id = None
        self._wallpaper_monitor = None
        self._update_pending = False

        self.cover = CircleImage(
            name="player-cover",
            image_file=WALLPAPER_PATH,
            size=162,
            h_align="center",
            v_align="center",
        )
        self.cover_placeholder = CircleImage(
            name="player-cover",
            size=198,
            h_align="center",
            v_align="center",
        )
        self.title = Label(name="player-title", h_expand=True, h_align="fill", ellipsization="end", max_chars_width=1)
        self.album = Label(name="player-album", h_expand=True, h_align="fill", ellipsization="end", max_chars_width=1)
        self.artist = Label(name="player-artist", h_expand=True, h_align="fill", ellipsization="end", max_chars_width=1)
        self.progressbar = CircularProgressBar(
            name="player-progress",
            size=198,
            h_align="center",
            v_align="center",
            start_angle=180,
            end_angle=360,
        )
        self.time = Label(name="player-time", label="--:-- / --:--")
        
        self.overlay_container = CenterBox(
            name="player-overlay",
            center_children=[
                Overlay(
                    child=self.cover_placeholder,
                    overlays=[self.progressbar, self.cover],
                )
            ]
        )

        self.title.set_label("Nothing Playing")
        self.album.set_label("Enjoy the silence")
        self.artist.set_label("¯\\_(ツ)_/¯")

        # Создаем кнопки управления
        self.prev = self._create_btn(icons.prev)
        self.backward = self._create_btn(icons.skip_back)
        self.play_pause = self._create_btn(icons.play, ["play-pause"])
        self.forward = self._create_btn(icons.skip_forward)
        self.next = self._create_btn(icons.next)

        self.btn_box = CenterBox(
            name="player-btn-box",
            orientation="h",
            center_children=[
                Box(
                    orientation="h",
                    spacing=8,
                    h_expand=True,
                    h_align="fill",
                    children=[self.prev, self.backward, self.play_pause, self.forward, self.next]
                )
            ]
        )

        self.player_box = Box(
            name="player-box",
            orientation="v",
            v_align="center",
            spacing=4,
            children=[
                self.overlay_container,
                self.title,
                self.album,
                self.artist,
                self.btn_box,
                self.time,
            ],
        )
        self.add(self.player_box)

        if mpris_player:
            self._setup_player_controls()
        else:
            self._setup_no_player_state()

    def _create_btn(self, icon, style_classes=None):
        btn = Button(
            name="player-btn",
            child=Label(name="player-btn-label", markup=icon, style_classes=style_classes or []),
            style_classes=style_classes or [],
            h_expand=False, v_expand=False,
            h_align="center", v_align="center",
        )
        add_hover_cursor(btn)
        return btn

    def _setup_player_controls(self):
        self._apply_mpris_properties()
        self.prev.connect("clicked", lambda _: self.mpris_player.previous())
        self.play_pause.connect("clicked", lambda _: (self.mpris_player.play_pause(), self._update_play_pause_icon()))
        self.backward.connect("clicked", self._on_backward_clicked)
        self.forward.connect("clicked", self._on_forward_clicked)
        self.next.connect("clicked", lambda _: self.mpris_player.next())
        self.mpris_player.connect("changed", self._on_mpris_changed)

    def _setup_no_player_state(self):
        self.play_pause.get_child().set_markup(icons.stop)
        self.play_pause.add_style_class("stop")
        for btn in (self.backward, self.forward, self.prev, self.next):
            btn.add_style_class("disabled")
        self.progressbar.set_value(0.0)
        self.time.set_text("--:-- / --:--")

    def _apply_mpris_properties(self):
        mp = self.mpris_player
        
        # Обновляем текстовые поля
        for label, text in [(self.title, mp.title), (self.album, mp.album), (self.artist, mp.artist)]:
            has_text = bool(text and text.strip())
            label.set_visible(has_text)
            if has_text:
                label.set_text(text)

        # Обновляем обложку
        self._update_cover(mp.arturl)
        self._update_play_pause_icon()

        # Обновляем состояние элементов управления
        player_name = getattr(mp, "player_name", "").lower()
        can_seek = getattr(mp, "can_seek", False)

        if player_name == "firefox" or not can_seek:
            self.backward.add_style_class("disabled")
            self.forward.add_style_class("disabled")
            self.progressbar.set_value(0.0)
            self.time.set_text("--:-- / --:--")
            if self._progress_timer_id:
                GLib.source_remove(self._progress_timer_id)
                self._progress_timer_id = None
        else:
            self.backward.remove_style_class("disabled")
            self.forward.remove_style_class("disabled")
            self._start_progress_timer()

        # Обновляем prev/next
        if getattr(mp, "can_go_previous", False):
            self.prev.remove_style_class("disabled")
        else:
            self.prev.add_style_class("disabled")

        if getattr(mp, "can_go_next", False):
            self.next.remove_style_class("disabled")
        else:
            self.next.add_style_class("disabled")

    def _update_cover(self, arturl):
        if not arturl:
            self._set_cover_with_fallback()
            return

        parsed = urllib.parse.urlparse(arturl)
        if parsed.scheme == "file":
            self._set_cover_image(urllib.parse.unquote(parsed.path))
        elif parsed.scheme in ("http", "https"):
            self._download_artwork(arturl)
        else:
            self._set_cover_image(arturl)

    def _set_cover_with_fallback(self):
        self._set_cover_image(WALLPAPER_PATH)
        self._setup_wallpaper_monitor()

    def _setup_wallpaper_monitor(self):
        if self._wallpaper_monitor:
            return
        file_obj = Gio.File.new_for_path(WALLPAPER_PATH)
        self._wallpaper_monitor = file_obj.monitor_file(Gio.FileMonitorFlags.NONE, None)
        self._wallpaper_monitor.connect("changed", lambda *_: self.cover.set_image_from_file(WALLPAPER_PATH))

    def _start_progress_timer(self):
        if self._progress_timer_id:
            GLib.source_remove(self._progress_timer_id)
        self._progress_timer_id = GLib.timeout_add(1000, self._update_progress)
        self._update_progress()

    def _set_cover_image(self, image_path):
        if image_path and os.path.isfile(image_path):
            self.cover.set_image_from_file(image_path)
        else:
            self._set_cover_with_fallback()

    def _download_artwork(self, arturl):
        def worker(user_data):
            try:
                parsed = urllib.parse.urlparse(arturl)
                suffix = os.path.splitext(parsed.path)[1] or ".png"
                with urllib.request.urlopen(arturl, timeout=5) as response:
                    data = response.read()
                
                if len(data) > 5 * 1024 * 1024:
                    local_arturl = WALLPAPER_PATH
                else:
                    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                    temp_file.write(data)
                    temp_file.close()
                    local_arturl = temp_file.name
            except Exception:
                local_arturl = WALLPAPER_PATH
            
            GLib.idle_add(self._set_cover_image, local_arturl)

        GLib.Thread.new("artwork", worker, None)

    def _update_play_pause_icon(self):
        if self.mpris_player.playback_status == "playing":
            self.play_pause.get_child().set_markup(icons.pause)
            self.play_pause.add_style_class("playing")
        else:
            self.play_pause.get_child().set_markup(icons.play)
            self.play_pause.remove_style_class("playing")

    def _on_backward_clicked(self, button):
        if self.mpris_player and self.mpris_player.can_seek:
            if "disabled" not in self.backward.get_style_context().list_classes():
                self.mpris_player.position = max(0, self.mpris_player.position - 5000000)

    def _on_forward_clicked(self, button):
        if self.mpris_player and self.mpris_player.can_seek:
            if "disabled" not in self.forward.get_style_context().list_classes():
                self.mpris_player.position = self.mpris_player.position + 5000000

    def _update_progress(self):
        if not self.mpris_player:
            self._progress_timer_id = None
            return False

        try:
            current = self.mpris_player.position
            total = int(self.mpris_player.length or 0)
        except Exception:
            current, total = 0, 0

        if total <= 0:
            self.progressbar.set_value(0.0)
            self.time.set_text("--:-- / --:--")
        else:
            self.progressbar.set_value(current / total)
            self.time.set_text(f"{self._format_time(current)} / {self._format_time(total)}")
        
        return True

    def _format_time(self, us):
        seconds = int(us / 1000000)
        return f"{seconds // 60}:{seconds % 60:02}"

    def _on_mpris_changed(self, *args):
        if not self._update_pending:
            self._update_pending = True
            GLib.idle_add(self._apply_mpris_debounced)

    def _apply_mpris_debounced(self):
        if self.mpris_player:
            self._apply_mpris_properties()
        elif self._progress_timer_id:
            GLib.source_remove(self._progress_timer_id)
            self._progress_timer_id = None
        self._update_pending = False
        return False


class Player(Box):
    def __init__(self):
        super().__init__(name="player", orientation="v", h_align="fill", spacing=0, h_expand=False, v_expand=True)
        
        self.player_stack = Stack(
            name="player-stack",
            transition_type="slide-left-right",
            transition_duration=500,
            v_align="center",
            v_expand=True,
        )
        self.switcher = Gtk.StackSwitcher(name="player-switcher", spacing=8)
        self.switcher.set_stack(self.player_stack)
        self.switcher.set_halign(Gtk.Align.CENTER)

        self.mpris_manager = MprisPlayerManager()
        
        players = self.mpris_manager.players
        if players:
            for p in players:
                mp = MprisPlayer(p)
                self.player_stack.add_titled(PlayerBox(mpris_player=mp), mp.player_name, mp.player_name)
        else:
            self.player_stack.add_titled(PlayerBox(), "nothing", "Nothing Playing")

        self.mpris_manager.connect("player-appeared", self._on_player_appeared)
        self.mpris_manager.connect("player-vanished", self._on_player_vanished)
        
        self.add(self.player_stack)
        self.add(self.switcher)
        GLib.idle_add(self._replace_switcher_labels)

    def _on_player_appeared(self, manager, player):
        children = self.player_stack.get_children()
        if len(children) == 1 and not getattr(children[0], "mpris_player", None):
            self.player_stack.remove(children[0])
        
        mp = MprisPlayer(player)
        self.player_stack.add_titled(PlayerBox(mpris_player=mp), mp.player_name, mp.player_name)
        GLib.idle_add(self._replace_switcher_labels)

    def _on_player_vanished(self, manager, player_name):
        for child in self.player_stack.get_children():
            if getattr(child, "mpris_player", None) and child.mpris_player.player_name == player_name:
                self.player_stack.remove(child)
                break
        
        if not any(getattr(c, "mpris_player", None) for c in self.player_stack.get_children()):
            self.player_stack.add_titled(PlayerBox(), "nothing", "Nothing Playing")
        
        GLib.idle_add(self._replace_switcher_labels)

    def _replace_switcher_labels(self):
        for btn in self.switcher.get_children():
            if isinstance(btn, Gtk.ToggleButton):
                for child in btn.get_children():
                    if isinstance(child, Gtk.Label):
                        btn.remove(child)
                        new_label = Label(name="player-label", markup=icons.disc)
                        btn.add(new_label)
                        new_label.show_all()
                        break
        return False


class PlayerSmall(CenterBox):
    def __init__(self):
        super().__init__(name="player-small", orientation="h", h_align="fill", v_align="center")
        
        self._display_options = ["title", "artist"]
        self._display_index = 0
        self._restore_timer = None

        self.mpris_icon = Button(
            name="compact-mpris-icon",
            h_align="center", v_align="center",
            child=Label(name="compact-mpris-icon-label", markup=icons.disc)
        )
        self.mpris_icon.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.mpris_icon.connect("button-press-event", self._on_icon_button_press)
        add_hover_cursor(self.mpris_icon)

        self.mpris_label = Label(
            name="compact-mpris-label",
            label="Nothing Playing",
            ellipsization="end",
            max_chars_width=26,
            h_align="center",
        )
        
        self.mpris_button = Button(
            name="compact-mpris-button",
            h_align="center", v_align="center",
            child=Label(name="compact-mpris-button-label", markup=icons.play)
        )
        self.mpris_button.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.mpris_button.connect("button-press-event", self._on_play_pause_button_press)
        add_hover_cursor(self.mpris_button)

        self.center_stack = Stack(
            name="compact-mpris",
            transition_type="crossfade",
            transition_duration=100,
            v_align="center", v_expand=False,
            children=[self.mpris_label],
        )

        self.add(CenterBox(
            name="compact-mpris",
            orientation="h",
            h_expand=True, h_align="fill",
            v_align="center", v_expand=False,
            start_children=self.mpris_icon,
            center_children=self.center_stack,
            end_children=self.mpris_button,
        ))

        self.mpris_manager = MprisPlayerManager()
        self.mpris_player = None
        self.current_index = 0

        players = self.mpris_manager.players
        if players:
            self.mpris_player = MprisPlayer(players[0])
            self.mpris_player.connect("changed", lambda *_: self._apply_mpris_properties())
        
        self._apply_mpris_properties()

        self.mpris_manager.connect("player-appeared", self._on_player_appeared)
        self.mpris_manager.connect("player-vanished", self._on_player_vanished)

    def _apply_mpris_properties(self):
        if not self.mpris_player:
            self.mpris_label.set_text("Nothing Playing")
            self.mpris_button.get_child().set_markup(icons.stop)
            self.mpris_icon.get_child().set_markup(icons.disc)
            return

        mp = self.mpris_player
        self.mpris_icon.get_child().set_markup(icons.disc)
        self._update_play_pause_icon()

        if self._display_options[self._display_index] == "title":
            text = mp.title if mp.title and mp.title.strip() else "Nothing Playing"
        else:
            text = mp.artist if mp.artist else "Nothing Playing"
        
        self.mpris_label.set_text(text)

    def _on_icon_button_press(self, widget, event):
        if event.type != Gdk.EventType.BUTTON_PRESS:
            return True

        players = self.mpris_manager.players
        if not players:
            return True

        if event.button == 2:
            self._display_index = (self._display_index + 1) % len(self._display_options)
            self._apply_mpris_properties()
        elif event.button == 1:
            self.current_index = (self.current_index + 1) % len(players)
            self._switch_player(players)
        elif event.button == 3:
            self.current_index = (self.current_index - 1) % len(players)
            self._switch_player(players)
        
        return True

    def _switch_player(self, players):
        self.mpris_player = MprisPlayer(players[self.current_index])
        self.mpris_player.connect("changed", lambda *_: self._apply_mpris_properties())
        self._apply_mpris_properties()

    def _on_play_pause_button_press(self, widget, event):
        if event.type != Gdk.EventType.BUTTON_PRESS or not self.mpris_player:
            return True

        if event.button == 1:
            self.mpris_player.previous()
            self._show_temp_icon(icons.prev)
        elif event.button == 3:
            self.mpris_player.next()
            self._show_temp_icon(icons.next)
        elif event.button == 2:
            self.mpris_player.play_pause()
            self._update_play_pause_icon()
        
        return True

    def _show_temp_icon(self, icon):
        self.mpris_button.get_child().set_markup(icon)
        if self._restore_timer:
            GLib.source_remove(self._restore_timer)
        self._restore_timer = GLib.timeout_add(500, self._restore_play_pause_icon)

    def _restore_play_pause_icon(self):
        self._update_play_pause_icon()
        self._restore_timer = None
        return False

    def _update_play_pause_icon(self):
        if self.mpris_player and self.mpris_player.playback_status == "playing":
            self.mpris_button.get_child().set_markup(icons.pause)
        else:
            self.mpris_button.get_child().set_markup(icons.play)

    def _on_player_appeared(self, manager, player):
        if not self.mpris_player:
            self.mpris_player = MprisPlayer(player)
            self.mpris_player.connect("changed", lambda *_: self._apply_mpris_properties())
            self._apply_mpris_properties()

    def _on_player_vanished(self, manager, player_name):
        if self.mpris_player and self.mpris_player.player_name == player_name:
            players = self.mpris_manager.players
            if players:
                self.current_index = self.current_index % len(players)
                self.mpris_player = MprisPlayer(players[self.current_index])
                self.mpris_player.connect("changed", lambda *_: self._apply_mpris_properties())
            else:
                self.mpris_player = None
        elif not self.mpris_manager.players:
            self.mpris_player = None
        
        self._apply_mpris_properties()