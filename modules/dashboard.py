from fabric.widgets.box import Box
from fabric.widgets.label import Label
from fabric.widgets.stack import Stack

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

import modules.icons as icons
from modules.mixer import Mixer
from modules.wallpapers import WallpaperSelector
from modules.widgets import Widgets


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

    def _setup_switcher_icons(self):
        icon_map = {
            "Виджеты": {"icon": icons.widgets, "name": "widgets"},
            "Обои": {"icon": icons.wallpapers, "name": "wallpapers"},
            "Аудио": {"icon": icons.speaker, "name": "mixer"},
        }

        buttons = self.switcher.get_children()
        for btn in buttons:
            if isinstance(btn, Gtk.ToggleButton):
                for child in btn.get_children():
                    if isinstance(child, Gtk.Label):
                        label_text = child.get_text()
                        if label_text in icon_map:
                            details = icon_map[label_text]
                            btn.remove(child)
                            new_label = Label(
                                name=f"switcher-icon-{details['name']}", 
                                markup=details["icon"]
                            )
                            btn.add(new_label)
                            new_label.show_all()
        return False

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
        if section_name == "widgets":
            self.stack.set_visible_child(self.widgets)
        elif section_name == "wallpapers":
            self.stack.set_visible_child(self.wallpapers)
        elif section_name == "mixer":
            self.stack.set_visible_child(self.mixer)