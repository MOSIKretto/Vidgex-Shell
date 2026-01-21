from fabric.widgets.box import Box
from fabric.widgets.stack import Stack

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from modules.Notch.Mixer.mixer import Mixer
from modules.Notch.Wallpaper.wallpapers import WallpaperSelector
from modules.Notch.Widgets.widgets import Widgets


class Dashboard(Box):
    def __init__(self, **kwargs):
        super().__init__(
            name="dashboard",
            orientation="v",
            spacing=8,
            visible=True,
            all_visible=True,
        )

        self.notch = kwargs["notch"]

        self.widgets = Widgets(notch=self.notch)
        self.wallpapers = WallpaperSelector()
        self.mixer = Mixer()

        self.stack = Stack(
            name="stack",
            transition_type="slide-left-right",
            v_expand=True,
        )

        self.stack.set_homogeneous(False)

        self.switcher = Gtk.StackSwitcher(
            name="switcher",
            spacing=8,
        )

        self.stack.add_titled(self.widgets, "widgets", "Dashboard")
        self.stack.add_titled(self.wallpapers, "wallpapers", "Wallpapers")
        self.stack.add_titled(self.mixer, "mixer", "Mixer")

        self.switcher.set_stack(self.stack)
        self.switcher.set_hexpand(True)
        self.switcher.set_homogeneous(True)
        self.switcher.set_can_focus(True)

        self.stack.connect("notify::visible-child", self.on_visible_child_changed)

        self.add(self.switcher)
        self.add(self.stack)

        self.connect(
            "button-release-event",
            lambda widget, event: (event.button == 3 and self.notch.close_notch()),
        )
        self.show_all()

    def on_visible_child_changed(self, stack, param):
        visible = stack.get_visible_child()
        if visible == self.wallpapers:
            self.wallpapers.search_entry.set_text("")
            self.wallpapers.search_entry.grab_focus()

    def go_to_section(self, section_name):
        if section_name == "widgets": self.stack.set_visible_child(self.widgets)
        elif section_name == "wallpapers": self.stack.set_visible_child(self.wallpapers)
        else: self.stack.set_visible_child(self.mixer)
