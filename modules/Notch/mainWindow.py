from fabric.widgets.box import Box
from fabric.widgets.stack import Stack

import gi
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk

from modules.Notch.MainWindow.musicPlayer import Player
from modules.Notch.MainWindow.wallpapers import WallpaperSelector
from modules.Notch.MainWindow.dashboard import Dashboard


class MainWindow(Box):
    __slots__ = ('notch', 'dashboard', 'wallpapers', 'player', 'stack', 'switcher', '_sections')

    def __init__(self, **kwargs):
        self.notch = kwargs.get("notch")

        self.dashboard = Dashboard(notch=self.notch)
        self.wallpapers = WallpaperSelector()
        self.player = Player()

        self._sections = {
            "dashboard": self.dashboard,
            "player": self.player,
            "wallpapers": self.wallpapers,
        }

        self.stack = Stack(name="stack", transition_type="slide-left-right", v_expand=True)
        self.stack.set_homogeneous(False)

        self.switcher = Gtk.StackSwitcher(name="switcher", spacing=8)
        self.switcher.set_stack(self.stack)
        self.switcher.set_hexpand(True)
        self.switcher.set_homogeneous(True)
        self.switcher.set_can_focus(True)

        self.stack.add_titled(self.dashboard, "dashboard", "Dashboard")
        self.stack.add_titled(self.player, "player", "Player")
        self.stack.add_titled(self.wallpapers, "wallpapers", "Wallpapers")

        self.stack.connect("notify::visible-child", self._on_vis)

        super().__init__(
            name="dashboard",
            orientation="v",
            spacing=8,
            visible=True,
            all_visible=True,
            children=(self.switcher, self.stack)
        )

        self.switcher.connect("realize", self._set_tab_cursors)

        self.connect("button-release-event", self._on_btn_rel)
        self.show_all()

    def _set_tab_cursors(self, switcher):
        hand = Gdk.Cursor.new_from_name(switcher.get_display(), "pointer")
        for child in switcher.get_children():
            child.add_events(
                Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
            )
            child.connect(
                "enter-notify-event",
                lambda w, e, c=hand: e.window.set_cursor(c) or False
            )
            child.connect(
                "leave-notify-event",
                lambda w, e: e.window.set_cursor(None) or False
            )

    def _on_btn_rel(self, _, e):
        if e.button == 3:
            self.notch.close_notch()

    def _on_vis(self, stack, _):
        if stack.get_visible_child() is self.wallpapers:
            ent = self.wallpapers._ent
            ent.set_text("")
            ent.grab_focus()

    def go_to_section(self, name: str):
        if tgt := self._sections.get(name):
            self.stack.set_visible_child(tgt)

    def cleanup(self):
        for w in (self.dashboard, self.wallpapers, self.player):
            try:
                w.cleanup()
            except AttributeError:
                pass
        self.notch = None
        self._sections = None