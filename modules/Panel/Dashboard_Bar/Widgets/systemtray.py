from fabric.widgets.box import Box

import gi
gi.require_version("Gray", "0.1")
from gi.repository import Gdk, GdkPixbuf, GLib, Gray, Gtk

import os


class SystemTray(Box):
    def __init__(self, pixel_size: int = 20, **kwargs) -> None:
        super().__init__(name="systray", spacing=8)
        self.enabled = True
        self.pixel_size = pixel_size
        self.buttons_by_id = {}
        self.items_by_id = {}
        self._item_signal_handlers = {}
        
        super().set_visible(False)
        
        self.watcher = Gray.Watcher()
        self._watcher_handler_id = self.watcher.connect("item-added", self._on_item_added)

    def set_visible(self, visible: bool):
        self.enabled = visible
        self._update_visibility()

    def _update_visibility(self):
        should_show = self.enabled and len(self.get_children()) > 0
        super().set_visible(should_show)

    def _get_item_pixbuf(self, item: Gray.Item) -> GdkPixbuf.Pixbuf:
        pm = Gray.get_pixmap_for_pixmaps(item.get_icon_pixmaps(), self.pixel_size)
        if pm:
            return pm.as_pixbuf(self.pixel_size, GdkPixbuf.InterpType.HYPER)

        name = item.get_icon_name()
        if name and os.path.exists(name):
            return GdkPixbuf.Pixbuf.new_from_file_at_scale(
                name, self.pixel_size, self.pixel_size, True
            )

        theme = Gtk.IconTheme.new()
        if item.get_icon_theme_path():
            theme.prepend_search_path(item.get_icon_theme_path())
        
        if name:
            return theme.load_icon(name, self.pixel_size, Gtk.IconLookupFlags.FORCE_SIZE)
           
        try:
            return theme.load_icon("application-x-executable", self.pixel_size, Gtk.IconLookupFlags.FORCE_SIZE)
        except GLib.Error:
            return GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, self.pixel_size, self.pixel_size)

    def _refresh_item_ui(self, item: Gray.Item, button: Gtk.Button):
        pixbuf = self._get_item_pixbuf(item)
        img = button.get_image()
        if isinstance(img, Gtk.Image):
            img.set_from_pixbuf(pixbuf)
        else:
            button.set_image(Gtk.Image.new_from_pixbuf(pixbuf))
            
        tooltip = getattr(item, 'get_tooltip_text', lambda: None)() or getattr(item, 'get_title', lambda: None)()
        button.set_tooltip_text(tooltip) if tooltip else button.set_has_tooltip(False)

    def _on_item_added(self, _, identifier: str):
        item = self.watcher.get_item_for_identifier(identifier)
        if not item:
            return

        self._remove_existing_item(identifier)
        
        btn = self._create_item_button(item)
        self.buttons_by_id[identifier] = btn
        self.items_by_id[identifier] = item

        self._connect_item_signals(item, btn, identifier)
        self._add_button_to_tray(btn, identifier)

    def _remove_existing_item(self, identifier: str):
        if identifier in self.buttons_by_id:
            self.buttons_by_id[identifier].destroy()
            del self.buttons_by_id[identifier]
            del self.items_by_id[identifier]

    def _create_item_button(self, item: Gray.Item) -> Gtk.Button:
        btn = Gtk.Button()
        btn.connect("button-press-event", lambda b, e: self._on_button_click(b, item, e))
        btn.set_image(Gtk.Image.new_from_pixbuf(self._get_item_pixbuf(item)))
        
        tooltip = getattr(item, 'get_tooltip_text', lambda: None)() or getattr(item, 'get_title', lambda: None)()
        if tooltip:
            btn.set_tooltip_text(tooltip)
            
        return btn

    def _connect_item_signals(self, item: Gray.Item, button: Gtk.Button, identifier: str):
        refresh_callback = lambda itm: self._refresh_item_ui(itm, button)
        remove_callback = lambda itm: self._on_item_removed(identifier, itm)
        
        handlers = [
            item.connect("notify::icon-pixmaps", refresh_callback),
            item.connect("notify::icon-name", refresh_callback),
            item.connect("icon-changed", refresh_callback),
            item.connect("removed", remove_callback),
        ]
        self._item_signal_handlers[identifier] = (item, handlers)

    def _add_button_to_tray(self, button: Gtk.Button, identifier: str):
        self.add(button)
        button.show_all()
        self._update_visibility()

    def _on_item_removed(self, identifier: str, removed_item: Gray.Item):
        if identifier in self.items_by_id and self.items_by_id[identifier] is removed_item:
            if identifier in self._item_signal_handlers:
                item, handlers = self._item_signal_handlers[identifier]
                for handler_id in handlers:
                    item.disconnect(handler_id)

                del self._item_signal_handlers[identifier]
            
            self.buttons_by_id[identifier].destroy()
            del self.buttons_by_id[identifier]
            del self.items_by_id[identifier]
            self._update_visibility()

    def _on_button_click(self, button: Gtk.Button, item: Gray.Item, event: Gdk.EventButton):
        if event.button == Gdk.BUTTON_PRIMARY:
            item.activate(int(event.x_root), int(event.y_root))
            
            menu = getattr(item, 'get_menu', lambda: None)()
            if isinstance(menu, Gtk.Menu):
                menu.popup_at_widget(button, Gdk.Gravity.SOUTH_WEST, Gdk.Gravity.NORTH_WEST, event)
            elif hasattr(item, 'context_menu'):
                item.context_menu(int(event.x_root), int(event.y_root))

    def destroy(self):
        if hasattr(self, 'watcher') and self.watcher:
            if hasattr(self, '_watcher_handler_id'):
                self.watcher.disconnect(self._watcher_handler_id)
            else:
                self.watcher.disconnect_by_func(self._on_item_added)

            self.watcher = None
        
        for identifier, (item, handlers) in list(self._item_signal_handlers.items()):
            for handler_id in handlers:
                item.disconnect(handler_id)

        self._item_signal_handlers.clear()
        
        for identifier in list(self.buttons_by_id.keys()):
            if identifier in self.buttons_by_id:
                self.buttons_by_id[identifier].destroy()
        
        self.buttons_by_id.clear()
        self.items_by_id.clear()
        
        super().destroy()