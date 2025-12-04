from fabric.widgets.box import Box

import gi
gi.require_version("Gray", "0.1")
from gi.repository import Gdk, GdkPixbuf, GLib, Gray, Gtk

import logging
import os


logger = logging.getLogger(__name__)

class SystemTray(Box):
    def __init__(self, pixel_size: int = 20, **kwargs) -> None:
        super().__init__(
            name="systray",
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            **kwargs
        )
        self.enabled = True
        super().set_visible(False)
        self.pixel_size = pixel_size
        self.buttons_by_id = {}
        self.items_by_id = {}

        self.watcher = Gray.Watcher()
        self.watcher.connect("item-added", self.on_watcher_item_added)

    def set_visible(self, visible: bool):
        self.enabled = visible
        self._update_visibility()

    def _update_visibility(self):
        children = self.get_children()
        has_children = len(children) > 0
        should_show = self.enabled and has_children
        super().set_visible(should_show)

    def _get_item_pixbuf(self, item: Gray.Item) -> GdkPixbuf.Pixbuf:
        try:
            pm = Gray.get_pixmap_for_pixmaps(item.get_icon_pixmaps(), self.pixel_size)
            if pm:
                return pm.as_pixbuf(self.pixel_size, GdkPixbuf.InterpType.HYPER)

            name = item.get_icon_name()
            if name and os.path.exists(name):
                try:
                    return GdkPixbuf.Pixbuf.new_from_file_at_scale(
                        name, self.pixel_size, self.pixel_size, True
                    )
                except Exception as e:
                    logger.debug(f"Load icon from file failed: {e}; fallback to theme for '{name}'")

            theme = Gtk.IconTheme.new()
            path = item.get_icon_theme_path()
            if path:
                theme.prepend_search_path(path)
                
            return theme.load_icon(name, self.pixel_size, Gtk.IconLookupFlags.FORCE_SIZE)
            
        except GLib.Error as e:
            logger.debug(f"Icon load error {e}")
            default_theme = Gtk.IconTheme.get_default()
            return default_theme.load_icon(
                "image-missing", self.pixel_size, Gtk.IconLookupFlags.FORCE_SIZE
            )

    def _refresh_item_ui(self, item: Gray.Item, button: Gtk.Button):
        pixbuf = self._get_item_pixbuf(item)
        img = button.get_image()
        if isinstance(img, Gtk.Image):
            img.set_from_pixbuf(pixbuf)
        else:
            new_img = Gtk.Image.new_from_pixbuf(pixbuf)
            button.set_image(new_img)
            new_img.show()
            
        if hasattr(item, 'get_tooltip_text'):
            tip = item.get_tooltip_text()
        elif hasattr(item, 'get_title'):
            tip = item.get_title()
        else:
            tip = None
            
        if tip:
            button.set_tooltip_text(tip)
        else:
            button.set_has_tooltip(False)

    def on_watcher_item_added(self, _, identifier: str):
        item = self.watcher.get_item_for_identifier(identifier)
        if not item:
            return

        if identifier in self.buttons_by_id:
            old_button = self.buttons_by_id[identifier]
            old_button.destroy()
            del self.buttons_by_id[identifier]
            del self.items_by_id[identifier]

        btn = self.do_bake_item_button(item)
        self.buttons_by_id[identifier] = btn
        self.items_by_id[identifier] = item

        def refresh_icon_pixmaps(itm, pspec):
            self._refresh_item_ui(itm, btn)
            
        def refresh_icon_name(itm, pspec):
            self._refresh_item_ui(itm, btn)
            
        def refresh_icon_changed(itm):
            self._refresh_item_ui(itm, btn)
            
        def on_item_removed(itm):
            self.on_item_instance_removed(identifier, itm)

        item.connect("notify::icon-pixmaps", refresh_icon_pixmaps)
        item.connect("notify::icon-name", refresh_icon_name)

        try:
            item.connect("icon-changed", refresh_icon_changed)
        except TypeError:
            pass

        item.connect("removed", on_item_removed)

        self.add(btn)
        btn.show_all()
        self._update_visibility()

    def do_bake_item_button(self, item: Gray.Item) -> Gtk.Button:
        btn = Gtk.Button()
        
        def on_button_click(b, event):
            self.on_button_click(b, item, event)
            
        btn.connect("button-press-event", on_button_click)
        
        pixbuf = self._get_item_pixbuf(item)
        img = Gtk.Image.new_from_pixbuf(pixbuf)
        btn.set_image(img)
        
        if hasattr(item, 'get_tooltip_text'):
            tip = item.get_tooltip_text()
        else:
            tip = None
            
        if not tip and hasattr(item, 'get_title'):
            tip = item.get_title()
            
        if tip:
            btn.set_tooltip_text(tip)
            
        return btn

    def on_item_instance_removed(self, identifier: str, removed_item: Gray.Item):
        if identifier in self.items_by_id:
            if self.items_by_id[identifier] is removed_item:
                btn = self.buttons_by_id.get(identifier)
                if btn:
                    btn.destroy()
                del self.buttons_by_id[identifier]
                del self.items_by_id[identifier]
                self._update_visibility()

    def on_button_click(self, button: Gtk.Button, item: Gray.Item, event: Gdk.EventButton):
        if event.button == Gdk.BUTTON_PRIMARY:
            try:
                item.activate(int(event.x_root), int(event.y_root))
            except Exception as e:
                logger.error(f"Activate error: {e}")
        elif event.button == Gdk.BUTTON_SECONDARY:
            menu = None
            if hasattr(item, 'get_menu'):
                menu = item.get_menu()
                
            if isinstance(menu, Gtk.Menu):
                menu.popup_at_widget(button, Gdk.Gravity.SOUTH_WEST,
                                     Gdk.Gravity.NORTH_WEST, event)
            else:
                if hasattr(item, 'context_menu'):
                    cm = item.context_menu
                    try:
                        cm(int(event.x_root), int(event.y_root))
                    except Exception as e:
                        logger.error(f"ContextMenu error: {e}")