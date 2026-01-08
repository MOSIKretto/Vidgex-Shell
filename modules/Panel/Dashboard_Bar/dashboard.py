from fabric.widgets.box import Box
from fabric.widgets.stack import Stack

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from modules.Panel.Dashboard_Bar.Mixer.mixer import Mixer
from modules.Panel.Dashboard_Bar.Wallpaper.wallpapers import WallpaperSelector
from modules.Panel.Dashboard_Bar.Widgets.widgets import Widgets


class Dashboard(Box):
    def __init__(self, **kwargs):
        super().__init__(
            name="dashboard",
            orientation="v",
            spacing=8,
            visible=True,
            all_visible=True,
        )

        self.notch = kwargs.get("notch")

        self.widgets = Widgets(notch=self.notch)
        self.wallpapers = WallpaperSelector()
        self.mixer = Mixer()

        self.stack = Stack(
            name="stack",
            transition_type="slide-left-right",
            transition_duration=500,
        )

        self.stack.set_homogeneous(False)

        self.switcher = Gtk.StackSwitcher(
            name="switcher",
            spacing=8,
        )

        self.stack.add_titled(self.widgets, "widgets", "Виджеты")
        self.stack.add_titled(self.wallpapers, "wallpapers", "Обои")
        self.stack.add_titled(self.mixer, "mixer", "Аудио")

        self.switcher.set_stack(self.stack)
        self.switcher.set_hexpand(True)
        self.switcher.set_homogeneous(True)
        self.switcher.set_can_focus(True)

        self.stack.connect("notify::visible-child", self.on_visible_child_changed)

        self.add(self.switcher)
        self.add(self.stack)

        self.connect("button-release-event", self._on_right_click)
        self.show_all()

    def _on_right_click(self, widget, event):
        if event.button == 3:
            self.notch.close_notch()

    def go_to_next_child(self):
        children = self.stack.get_children()
        current_index = self.get_current_index(children)
        next_index = (current_index + 1) % len(children)
        self.stack.set_visible_child(children[next_index])

    def go_to_previous_child(self):
        children = self.stack.get_children()
        current_index = self.get_current_index(children)
        previous_index = (current_index - 1 + len(children)) % len(children)
        self.stack.set_visible_child(children[previous_index])

    def get_current_index(self, children):
        current_child = self.stack.get_visible_child()
        return children.index(current_child) if current_child in children else -1

    def on_visible_child_changed(self, stack, param):
        visible = stack.get_visible_child()
        if visible == self.wallpapers:
            self.wallpapers.search_entry.set_text("")
            self.wallpapers.search_entry.grab_focus()

    def go_to_section(self, section_name):
        section_map = {
            "widgets": self.widgets,
            "wallpapers": self.wallpapers,
            "mixer": self.mixer,
        }
        widget = section_map.get(section_name)
        if widget:
            self.stack.set_visible_child(widget)

    def destroy(self):
        if hasattr(self, 'stack') and self.stack:
            self.stack.disconnect_by_func(self.on_visible_child_changed)
        
        for widget_name in ['widgets', 'wallpapers', 'mixer']:
            widget = getattr(self, widget_name, None)
            if widget:
                if hasattr(widget, 'destroy'):
                    widget.destroy()
                elif hasattr(widget, 'cleanup'):
                    widget.cleanup()
                setattr(self, widget_name, None)
        
        self.notch = None
        super().destroy()