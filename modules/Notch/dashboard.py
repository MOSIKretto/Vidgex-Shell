from fabric.widgets.box import Box
from fabric.widgets.stack import Stack

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from modules.Notch.Mixer.mixer import Mixer
from modules.Notch.Wallpaper.wallpapers import WallpaperSelector
from modules.Notch.Widgets.widgets import Widgets


class Dashboard(Box):
    __slots__ = ('notch', 'widgets', 'wallpapers', 'mixer', 'stack', 'switcher')

    def __init__(self, **kwargs):
        super().__init__(name="dashboard", orientation="v", spacing=8, visible=True, all_visible=True)

        self.notch = kwargs["notch"]

        self.widgets = Widgets(notch=self.notch)
        self.wallpapers = WallpaperSelector()
        self.mixer = Mixer()

        self.stack = Stack(name="stack", transition_type="slide-left-right", v_expand=True)
        self.stack.set_homogeneous(False)

        self.switcher = Gtk.StackSwitcher(name="switcher", spacing=8)

        self.stack.add_titled(self.widgets, "widgets", "Dashboard")
        self.stack.add_titled(self.wallpapers, "wallpapers", "Wallpapers")
        self.stack.add_titled(self.mixer, "mixer", "Mixer")

        self.switcher.set_stack(self.stack)
        self.switcher.set_hexpand(True)
        self.switcher.set_homogeneous(True)
        self.switcher.set_can_focus(True)

        self.stack.connect("notify::visible-child", self._on_vis)

        self.add(self.switcher)
        self.add(self.stack)

        self.connect("button-release-event", lambda _, e: e.button == 3 and self.notch.close_notch())
        self.show_all()

    def _on_vis(self, stack, _):
        if stack.get_visible_child() == self.wallpapers:
            self.wallpapers._ent.set_text("")
            self.wallpapers._ent.grab_focus()

    def go_to_section(self, name):
        self.stack.set_visible_child(self.widgets if name == "widgets" else (self.wallpapers if name == "wallpapers" else self.mixer))

    def cleanup(self):
        for w in (self.widgets, self.wallpapers, self.mixer):
            if hasattr(w, 'cleanup'):
                w.cleanup()
        self.notch = None