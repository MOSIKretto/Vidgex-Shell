from fabric.widgets.box import Box
import gi
gi.require_version("Gray", "0.1")
from gi.repository import Gdk, GdkPixbuf, Gray, Gtk


class SystemTray(Box):
    def __init__(self, pixel_size: int = 20, **kwargs) -> None:
        super().__init__(name="systray", spacing=8, visible=False, **kwargs)
        self.pixel_size = pixel_size
        self._data = {}  # Оставляем только для отслеживания существующих ID
        
        self.watcher = Gray.Watcher()
        self.watcher.connect("item-added", self._on_item_added)

    def set_visible(self, visible: bool):
        if self.get_visible() != visible:
            super().set_visible(visible and bool(self._data))

    def _update_visibility(self):
        super().set_visible(bool(self._data))

    def _get_pix(self, item):
        pm = Gray.get_pixmap_for_pixmaps(item.get_icon_pixmaps(), self.pixel_size)
        if pm:
            return pm.as_pixbuf(self.pixel_size, GdkPixbuf.InterpType.BILINEAR)

        name = item.get_icon_name() or "application-x-executable"
        
        if name.startswith("/"):
            return GdkPixbuf.Pixbuf.new_from_file_at_scale(name, self.pixel_size, self.pixel_size, True)

        theme = Gtk.IconTheme.get_default()
        path = item.get_icon_theme_path()
        if path and path not in theme.get_search_path():
            theme.append_search_path(path)

        icon_info = theme.lookup_icon(name, self.pixel_size, Gtk.IconLookupFlags.FORCE_SIZE)
        if not icon_info:
            icon_info = theme.lookup_icon("application-x-executable", self.pixel_size, Gtk.IconLookupFlags.FORCE_SIZE)
        
        return icon_info.load_icon()

    def _refresh(self, item, btn):
        btn.get_image().set_from_pixbuf(self._get_pix(item))
        title = item.get_title()
        btn.set_tooltip_text(title)
        btn.set_has_tooltip(bool(title))

    def _on_item_added(self, _, identifier):
        if identifier in self._data: 
            return
        
        item = self.watcher.get_item_for_identifier(identifier)
        if not item: 
            return

        btn = Gtk.Button()
        btn.set_image(Gtk.Image())
        self._refresh(item, btn)

        btn.connect("button-press-event", self._on_click, item)
        
        h = [
            item.connect("notify::icon-pixmaps", lambda *_: self._refresh(item, btn)),
            item.connect("notify::icon-name", lambda *_: self._refresh(item, btn)),
            item.connect("icon-changed", lambda *_: self._refresh(item, btn)),
            item.connect("removed", lambda *_: self._on_item_removed(identifier))
        ]
        
        self._data[identifier] = (btn, item, h)
        self.add(btn)
        btn.show()
        self._update_visibility()

    def _on_item_removed(self, identifier):
        res = self._data.pop(identifier, None)
        if res:
            btn, item, handlers = res
            for h_id in handlers:
                item.disconnect(h_id)
            btn.destroy()
            self._update_visibility()

    def _on_click(self, btn, event, item):
        x, y = int(event.x_root), int(event.y_root)
        
        if event.button == 1:
            item.activate(x, y)
            return

        menu = getattr(item, "get_menu", lambda: None)()
        if menu:
            menu.popup_at_widget(btn, Gdk.Gravity.SOUTH_WEST, Gdk.Gravity.NORTH_WEST, event)
        else:
            ctx = getattr(item, "context_menu", None)
            if ctx: ctx(x, y)

    def destroy(self):
        for identifier in list(self._data.keys()):
            self._on_item_removed(identifier)
        self.watcher = None
        super().destroy()