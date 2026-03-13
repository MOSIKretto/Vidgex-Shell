import gi
gi.require_version("Gray", "0.1")

from fabric.widgets.box import Box
from gi.repository import Gdk, GdkPixbuf, GLib, Gray, Gtk


class SystemTray(Box):
    __slots__ = ('_en', '_sz', '_btns', '_cnt', '_w')

    def __init__(self, pixel_size: int = 20, **kwargs) -> None:
        kwargs["visible"] = False
        super().__init__(name="systray", orientation=Gtk.Orientation.HORIZONTAL, spacing=8, **kwargs)
        self._en = True
        self._sz = pixel_size
        self._btns = {}
        self._cnt = 0
        self._w = Gray.Watcher()
        self._w.connect("item-added", self._on_add)

    def set_visible(self, v: bool):
        self._en = v
        super().set_visible(v and self._cnt > 0)

    def _pb(self, item):
        sz = self._sz
        try:
            pm = Gray.get_pixmap_for_pixmaps(item.get_icon_pixmaps(), sz)
            if pm:
                return pm.as_pixbuf(sz, GdkPixbuf.InterpType.HYPER)
        except Exception:
            pass

        nm = item.get_icon_name()
        if not nm:
            return None

        if "/" in nm:
            return GdkPixbuf.Pixbuf.new_from_file_at_scale(nm, sz, sz, True) if GLib.file_test(nm, GLib.FileTest.EXISTS) else None

        path = item.get_icon_theme_path()
        th = Gtk.IconTheme.new() if path else Gtk.IconTheme.get_default()
        if path:
            th.prepend_search_path(path)

        try:
            return th.load_icon(nm, sz, Gtk.IconLookupFlags.FORCE_SIZE)
        except GLib.Error:
            return None

    def _upd(self, item, *args):
        btn = args[-1]
        pb = None if item.get_status() == "Passive" else self._pb(item)

        if pb is None:
            if btn.get_visible():
                btn.hide()
            return

        if not btn.get_visible():
            btn.show()

        btn.get_image().set_from_pixbuf(pb)
        tip = getattr(item, 'get_tooltip_text', lambda: None)() or getattr(item, 'get_title', lambda: None)()
        btn.set_tooltip_text(tip) if tip else btn.set_has_tooltip(False)

    def _set_pointer_cursor(self, widget):
        win = widget.get_window()
        if win:
            cursor = Gdk.Cursor.new_from_name(widget.get_display(), "pointer")
            win.set_cursor(cursor)

    def _set_pointer_cursor_recursive(self, widget):
        """Устанавливает курсор-указатель на виджет и всех его потомков."""
        self._set_pointer_cursor(widget)
        if isinstance(widget, Gtk.Container):
            for child in widget.get_children():
                if child.get_realized():
                    self._set_pointer_cursor_recursive(child)
                else:
                    child.connect("realize", self._set_pointer_cursor)

    def _prepare_menu(self, menu):
        """Устанавливает курсор-указатель для всего меню и его пунктов."""
        menu.connect("realize", self._set_pointer_cursor)
        menu.connect("map", lambda m: self._set_pointer_cursor_recursive(m))
        # Для подменю — рекурсивно
        for item in menu.get_children():
            item.connect("realize", self._set_pointer_cursor)
            sub = item.get_submenu() if isinstance(item, Gtk.MenuItem) else None
            if isinstance(sub, Gtk.Menu):
                self._prepare_menu(sub)

    def _on_add(self, _, ident: str):
        item = self._w.get_item_for_identifier(ident)
        if not item:
            return

        if ident in self._btns:
            self._btns.pop(ident).destroy()
            self._cnt -= 1

        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_image(Gtk.Image())
        btn.connect("realize", self._set_pointer_cursor)
        self._upd(item, btn)

        btn.connect("button-press-event", self._click, item)
        item.connect("notify::icon-pixmaps", self._upd, btn)
        item.connect("notify::icon-name", self._upd, btn)
        item.connect("notify::status", self._upd, btn)
        try:
            item.connect("icon-changed", self._upd, btn)
        except TypeError:
            pass
        item.connect("removed", self._on_rm, ident)

        self._btns[ident] = btn
        self.add(btn)
        self._cnt += 1
        if self._en:
            super().set_visible(True)

    def _on_rm(self, _, ident):
        btn = self._btns.pop(ident, None)
        if btn:
            btn.destroy()
            self._cnt -= 1
            if self._cnt <= 0:
                super().set_visible(False)

    def _click(self, btn, ev, item):
        if ev.button == 1:
            item.activate(int(ev.x_root), int(ev.y_root))
        elif ev.button == 3:
            m = getattr(item, 'get_menu', lambda: None)()
            if isinstance(m, Gtk.Menu):
                self._prepare_menu(m)
                m.popup_at_widget(btn, Gdk.Gravity.SOUTH_WEST, Gdk.Gravity.NORTH_WEST, ev)
            elif cm := getattr(item, 'context_menu', None):
                cm(int(ev.x_root), int(ev.y_root))

    def cleanup(self):
        for btn in self._btns.values():
            btn.destroy()
        self._btns.clear()
        self._cnt = 0
        self._w = None