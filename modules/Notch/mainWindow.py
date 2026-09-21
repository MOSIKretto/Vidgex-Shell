from fabric.widgets.box import Box
from fabric.widgets.stack import Stack

import gi
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk

from modules.Notch.MainWindow.musicPlayer import Player
from modules.Notch.MainWindow.wallpapers import WallpaperSelector
from modules.Notch.MainWindow.dashboard import Dashboard

_NAV_ITEMS = ("dashboard", "player", "wallpapers", "close")


class MainWindow(Box):
    __slots__ = (
        'notch', 'dashboard', 'wallpapers', 'player', 
        'stack', 'switcher', '_sections', 'close_button',
        '_size_group', '_cur_idx'
    )

    def __init__(self, **kwargs):
        self.notch = kwargs.get("notch")
        self._cur_idx = 0

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
        self.switcher.set_homogeneous(False)
        self.switcher.set_can_focus(False)

        self.close_button = Gtk.Button(name="close-notch-button")
        self.close_button.set_can_focus(False)
        self.close_button.get_style_context().remove_class("image-button")
        self.close_button.set_relief(Gtk.ReliefStyle.NONE)

        close_icon = Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
        self.close_button.add(close_icon)
        self.close_button.connect("clicked", lambda _: self.notch.close_notch() if self.notch else None)

        self.switcher.pack_end(self.close_button, False, False, 0)

        self.stack.add_titled(self.dashboard, "dashboard", "Dashboard")
        self.stack.add_titled(self.player, "player", "Player")
        self.stack.add_titled(self.wallpapers, "wallpapers", "Wallpapers")

        self._size_group = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        for child in self.switcher.get_children():
            child.set_can_focus(False)
            if child is not self.close_button:
                self.switcher.set_child_packing(child, True, True, 0, Gtk.PackType.START)
                self._size_group.add_widget(child)

        self.stack.connect("notify::visible-child", self._on_stack_child_changed)

        super().__init__(
            name="dashboard",
            orientation="v",
            spacing=8,
            visible=True,
            all_visible=True,
            children=(self.switcher, self.stack)
        )

        self.set_can_focus(True)
        self.connect("key-press-event", self._on_key_press)
        self.connect("button-release-event", self._on_btn_rel)
        self.show_all()

    def _on_stack_child_changed(self, stack, _):
        cur_child = stack.get_visible_child()
        for idx, name in enumerate(_NAV_ITEMS[:3]):
            if self._sections.get(name) is cur_child:
                self._cur_idx = idx
                self.switcher.get_style_context().remove_class("close-focused")
                self.close_button.get_style_context().remove_class("focused")
                if self.notch:
                    self.notch._cw = name
                break

    def _set_nav_index(self, idx: int):
        self._cur_idx = idx
        sw_ctx = self.switcher.get_style_context()
        btn_ctx = self.close_button.get_style_context()

        if idx < 3:
            sw_ctx.remove_class("close-focused")
            btn_ctx.remove_class("focused")
            section_name = _NAV_ITEMS[idx]
            if tgt := self._sections.get(section_name):
                self.stack.set_visible_child(tgt)
            if self.notch:
                self.notch._cw = section_name
        else:
            sw_ctx.add_class("close-focused")
            btn_ctx.add_class("focused")

    def _on_key_press(self, _, event):
        top = self.get_toplevel()
        if top and hasattr(top, "get_focus"):
            focus = top.get_focus()
            if isinstance(focus, Gtk.Entry) and focus.get_can_focus():
                return False

        if event.keyval in (Gdk.KEY_Up, Gdk.KEY_Down):
            return True

        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            if self._cur_idx == 3 and self.notch:
                self.notch.close_notch()
                return True

        if event.keyval not in (Gdk.KEY_Left, Gdk.KEY_Right):
            return False

        step = -1 if event.keyval == Gdk.KEY_Left else 1
        new_idx = (self._cur_idx + step) % len(_NAV_ITEMS)
        self._set_nav_index(new_idx)
        return True

    def _on_btn_rel(self, _, e):
        if e.button == 3:
            self.notch.close_notch()

    def go_to_section(self, name: str):
        if name in _NAV_ITEMS[:3]:
            idx = _NAV_ITEMS.index(name)
            self._set_nav_index(idx)
        elif tgt := self._sections.get(name):
            self.stack.set_visible_child(tgt)
        self.grab_focus()

    def cleanup(self):
        for w in (self.dashboard, self.wallpapers, self.player):
            try:
                w.cleanup()
            except AttributeError:
                pass
        self.notch = None
        self._sections = None
        self._size_group = None