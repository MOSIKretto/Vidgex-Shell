from fabric.utils import DesktopApp, get_desktop_applications, idle_add, remove_handler
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.entry import Entry
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow
from gi.repository import Gdk, GLib

import modules.icons as icons

tooltip_close = "<b>Close</b>"


class AppLauncher(Box):
    def __init__(self, **kwargs):
        super().__init__(
            name="app-launcher",
            visible=False,
            all_visible=False,
            **kwargs,
        )

        self.notch = kwargs["notch"]
        self.selected_index = -1
        self._arranger_handler = 0
        self._all_apps = get_desktop_applications()

        self.viewport = Box(
            name="viewport",
            spacing=4,
            orientation="v",
        )

        self.search_entry = Entry(
            name="search-entry",
            placeholder="Search Applications...",
            h_expand=True,
            notify_text=self.on_text_changed,
            on_activate=lambda *_: self.launch_selected(),
            on_key_press_event=self.on_key_press,
        )
        self.search_entry.props.xalign = 0.5

        self.scrolled_window = ScrolledWindow(
            name="scrolled-window",
            spacing=10,
            h_expand=True,
            v_expand=True,
            h_align="fill",
            v_align="fill",
            child=self.viewport,
            propagate_width=False,
            propagate_height=False,
        )

        self.header_box = Box(
            name="header_box",
            spacing=10,
            orientation="h",
            children=[
                self.search_entry,
                Button(
                    name="close-button",
                    tooltip_markup=tooltip_close,
                    child=Label(name="close-label", markup=icons.cancel),
                    on_clicked=lambda *_: self.close_launcher(),
                ),
            ],
        )

        self.launcher_box = Box(
            name="launcher-box",
            spacing=10,
            h_expand=True,
            orientation="v",
            children=[
                self.header_box,
                self.scrolled_window,
            ],
        )

        self.add(self.launcher_box)
        self.show_all()

    def open_launcher(self):
        self._all_apps = get_desktop_applications()
        self.arrange_viewport()
        GLib.idle_add(lambda: self.search_entry.grab_focus() or False)

    def close_launcher(self):
        self.viewport.children = []
        self.selected_index = -1
        self.notch.close_notch()

    def on_text_changed(self, entry, *_):
        self.arrange_viewport(entry.get_text())

    def arrange_viewport(self, query: str = ""):
        remove_handler(self._arranger_handler) if self._arranger_handler else None
        self.viewport.children = []
        self.selected_index = -1

        apps = sorted(
            [
                app
                for app in self._all_apps
                if query.casefold()
                in (
                    (app.display_name or "")
                    + " "
                    + (app.name or "")
                    + " "
                    + (app.generic_name or "")
                    + " "
                    + (app.command_line or "")
                ).casefold()
            ],
            key=lambda app: (app.display_name or "").casefold(),
        )

        self._arranger_handler = idle_add(
            lambda it: self.add_next_application(it),
            iter(apps),
            pin=True,
        )
        
        if apps:
            GLib.idle_add(lambda: self.update_selection(0) or False)

    def add_next_application(self, apps_iter):
        """Добавляет следующее приложение из итератора в viewport."""
        app = next(apps_iter, None)
        if not app:
            return False

        self.viewport.add(self.bake_application_slot(app))
        return True

    def bake_application_slot(self, app: DesktopApp) -> Button:
        return Button(
            name="slot-button",
            child=Box(
                name="slot-box",
                orientation="h",
                spacing=10,
                children=[
                    Image(
                        name="app-icon",
                        pixbuf=app.get_icon_pixbuf(size=24),
                        h_align="start",
                    ),
                    Label(
                        name="app-label",
                        label=app.display_name or "Unknown",
                        ellipsization="end",
                        v_align="center",
                        h_align="center",
                    ),
                    Label(
                        name="app-desc",
                        label=app.description or "",
                        ellipsization="end",
                        v_align="center",
                        h_align="start",
                        h_expand=True,
                    ),
                ],
            ),
            tooltip_text=app.description,
            on_clicked=lambda *_: (app.launch(), self.close_launcher()),
        )

    def on_key_press(self, widget, event):
        if event.keyval == Gdk.KEY_Down:
            self.move_selection(1)
            return True

        if event.keyval == Gdk.KEY_Up:
            self.move_selection(-1)
            return True

        if event.keyval == Gdk.KEY_Page_Down:
            self.page_scroll(1)
            return True

        if event.keyval == Gdk.KEY_Page_Up:
            self.page_scroll(-1)
            return True

        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.launch_selected()
            return True

        if event.keyval == Gdk.KEY_Escape:
            self.close_launcher()
            return True

        return False
    
    def move_selection(self, delta: int):
        children = self.viewport.get_children()
        if not children:
            return

        if self.selected_index == -1:
            new_index = 0
        else:
            new_index = max(
                0, min(self.selected_index + delta, len(children) - 1)
            )

        self.update_selection(new_index)

    def update_selection(self, index: int):
        children = self.viewport.get_children()
        if not children:
            return

        if 0 <= self.selected_index < len(children):
            children[self.selected_index].get_style_context().remove_class("selected")

        self.selected_index = index
        children[index].get_style_context().add_class("selected")
        self.scroll_to_selected(children[index])

    def scroll_to_selected(self, button):
        def _scroll():
            adj = self.scrolled_window.get_vadjustment()
            alloc = button.get_allocation()
            if alloc.height == 0:
                return False

            top = adj.get_value()
            bottom = top + adj.get_page_size()

            if alloc.y < top:
                adj.set_value(alloc.y)
            elif alloc.y + alloc.height > bottom:
                adj.set_value(alloc.y + alloc.height - adj.get_page_size())

            return False

        GLib.idle_add(_scroll)

    def page_scroll(self, direction: int):
        adj = self.scrolled_window.get_vadjustment()
        page = adj.get_page_size()
        value = adj.get_value()
        upper = adj.get_upper()

        new_value = value + direction * page
        new_value = max(0, min(new_value, upper - page))
        adj.set_value(new_value)

        self.sync_selection_with_scroll()

    def sync_selection_with_scroll(self):
        children = self.viewport.get_children()
        if not children:
            return

        adj = self.scrolled_window.get_vadjustment()
        top = adj.get_value()
        bottom = top + adj.get_page_size()

        for i, btn in enumerate(children):
            alloc = btn.get_allocation()
            if alloc.height == 0:
                continue

            if alloc.y >= top and alloc.y + alloc.height <= bottom:
                self.update_selection(i)
                break

    def launch_selected(self):
        children = self.viewport.get_children()
        if not children:
            return

        index = self.selected_index if self.selected_index != -1 else 0
        children[index].clicked()