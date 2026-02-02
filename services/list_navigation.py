from gi.repository import Gdk, GLib

class ListNavigationMixin:
    """Миксин для вертикальной навигации по списку."""
    
    _NAV_KEYS = {Gdk.KEY_Down: 1, Gdk.KEY_Up: -1}
    _PG_KEYS = {Gdk.KEY_Page_Down: 1, Gdk.KEY_Page_Up: -1}
    _ACT_KEYS = (Gdk.KEY_Return, Gdk.KEY_KP_Enter)

    def _nav_key(self, _, e):
        k = e.keyval
        if k in self._NAV_KEYS: return self._nav_mov(self._NAV_KEYS[k]) or True
        if k in self._PG_KEYS: return self._nav_pg(self._PG_KEYS[k]) or True
        if k in self._ACT_KEYS: return self._nav_activate() or True
        if k == Gdk.KEY_Escape: return self.close() or True
        return False

    def _nav_mov(self, d):
        if ch := self.vp.get_children():
            self._nav_usel(max(0, min((0 if self.sel < 0 else self.sel) + d, len(ch) - 1)))

    def _nav_usel(self, i):
        ch = self.vp.get_children()
        if not ch or not 0 <= i < len(ch): return
        if 0 <= self.sel < len(ch):
            ch[self.sel].get_style_context().remove_class("selected")
        self.sel, btn = i, ch[i]
        btn.get_style_context().add_class("selected")
        GLib.idle_add(lambda: self._nav_scr(btn) or False)

    def _nav_scr(self, btn):
        adj, al = self.sw.get_vadjustment(), btn.get_allocation()
        if not al.height: return
        top, pg = adj.get_value(), adj.get_page_size()
        if al.y < top: adj.set_value(al.y)
        elif al.y + al.height > top + pg: adj.set_value(al.y + al.height - pg)

    def _nav_pg(self, d):
        adj, pg = self.sw.get_vadjustment(), self.sw.get_vadjustment().get_page_size()
        adj.set_value(max(0, min(adj.get_value() + d * pg, adj.get_upper() - pg)))
        top = adj.get_value()
        for i, btn in enumerate(self.vp.get_children()):
            al = btn.get_allocation()
            if al.height and top <= al.y <= top + pg - al.height:
                self._nav_usel(i); break

    def _nav_activate(self):
        if ch := self.vp.get_children():
            btn = ch[max(0, self.sel)]
            btn.get_style_context().add_class("activated")
            GLib.timeout_add(80, lambda: btn.clicked() or False)

    def _nav_clear(self):
        [c.destroy() for c in self.vp.get_children()]; self.sel = -1


class HorizontalNavigationMixin:
    """Миксин для горизонтальной навигации."""
    
    _H_KEYS = {Gdk.KEY_Left: -1, Gdk.KEY_Up: -1, Gdk.KEY_Right: 1, Gdk.KEY_Down: 1}

    def _hnav_init(self):
        self._nav_idx = getattr(self, '_nav_idx', 0)
        self._nav_items = getattr(self, '_nav_items', [])

    def _hnav_key(self, _, e):
        k = e.keyval
        if k in self._H_KEYS: return self._hnav_mov(self._H_KEYS[k]) or True
        if k in (Gdk.KEY_Return, Gdk.KEY_KP_Enter): return self._hnav_activate() or True
        if k == Gdk.KEY_Escape: return self._hnav_close() or True
        return k == Gdk.KEY_space

    def _hnav_mov(self, d):
        if self._nav_items and (n := max(0, min(self._nav_idx + d, len(self._nav_items) - 1))) != self._nav_idx:
            self._hnav_usel(n)

    def _hnav_usel(self, i):
        if not self._nav_items or not 0 <= i < len(self._nav_items): return
        if 0 <= self._nav_idx < len(self._nav_items):
            self._nav_items[self._nav_idx].get_style_context().remove_class("focused")
        self._nav_idx, btn = i, self._nav_items[i]
        btn.get_style_context().add_class("focused"); btn.grab_focus()

    def _hnav_activate(self):
        if self._nav_items and 0 <= self._nav_idx < len(self._nav_items):
            self._nav_items[self._nav_idx].clicked()

    def _hnav_close(self):
        if n := getattr(self, 'notch', None): n.close_notch()

    def _hnav_focus_first(self):
        if self._nav_items: self._hnav_usel(0)