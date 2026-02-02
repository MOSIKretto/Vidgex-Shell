from fabric.utils.helpers import exec_shell_command_async
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.entry import Entry
from fabric.widgets.label import Label

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk
from concurrent.futures import ThreadPoolExecutor

import cairo
import services.icons as icons

_HOME = GLib.get_home_dir()
_WALLS = f"{_HOME}/.config/Vidgex-Shell/wallpapers/"
_CURRENT = f"{_HOME}/.current.wall"
_CACHE = f"{GLib.get_user_cache_dir()}/vidgex-shell"
_THUMBS = f"{_CACHE}/thumbnails"
_SCHEME_F = f"{_CACHE}/scheme"

_SZ, _HSZ, _HALF, _SPC, _ARC_K = 180, 90.0, 3, 100, 1.875
_EXT = frozenset((".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"))
_SCH = (
    ("scheme-tonal-spot", "Tonal Spot"), ("scheme-content", "Content"),
    ("scheme-expressive", "Expressive"), ("scheme-fidelity", "Fidelity"),
    ("scheme-fruit-salad", "Fruit Salad"), ("scheme-monochrome", "Monochrome"),
    ("scheme-neutral", "Neutral"), ("scheme-rainbow", "Rainbow"),
)
_SCH_K = frozenset(k for k, _ in _SCH)
_DICE = (icons.dice_1, icons.dice_2, icons.dice_3, icons.dice_4, icons.dice_5, icons.dice_6)
_ORD = (3, -3, 2, -2, 1, -1, 0)
_ALP = (0.25, 0.25, 0.5, 0.5, 0.75, 0.75, 1.0)
_SCL = (0.55, 0.55, 0.7, 0.7, 0.85, 0.85, 1.0)
_YOF = (16.875, 16.875, 7.5, 7.5, 1.875, 1.875, 0.0)


class WallpaperCarousel(Gtk.DrawingArea):
    __slots__ = ('_files', '_flt', '_th', '_ld', '_idx', '_off', '_by', '_bvy',
                 '_anim', '_bnc', '_dead', '_ldq', '_spl', '_spt', '_spd', '_spi',
                 '_spcb', '_clr', '_ph', '_ex', '_on_sel', '_on_nav')

    def __init__(self, on_select=None, on_navigate=None):
        super().__init__()
        self._files = self._flt = []
        self._th, self._ld = {}, set()
        self._idx = 0
        self._off = self._by = self._bvy = 0.0
        self._anim = self._bnc = self._dead = self._ldq = False
        self._spl = self._spt = self._spd = 0
        self._spi, self._spcb = 16, None
        self._clr = (1.0, 1.0, 1.0, 1.0)
        self._ph = None
        self._ex = ThreadPoolExecutor(max_workers=2, thread_name_prefix="c")
        self._on_sel, self._on_nav = on_select, on_navigate

        self.set_name("wallpaper-carousel")
        self.set_can_focus(True)
        self.add_events(Gdk.EventMask.KEY_PRESS_MASK | Gdk.EventMask.BUTTON_PRESS_MASK |
                        Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        self.connect("draw", self._draw)
        self.connect("key-press-event", self._key)
        self.connect("button-press-event", self._click)
        self.connect("scroll-event", self._scroll)
        self.connect("realize", lambda _: (self._mkph(), self._uclr(), self._sched()))
        self.connect("style-updated", lambda _: self._uclr())
        self.set_size_request(800, 280)

    def _mkph(self):
        s = cairo.ImageSurface(cairo.FORMAT_ARGB32, _SZ, _SZ)
        c = cairo.Context(s)
        c.set_source_rgba(0.27, 0.27, 0.27, 0.5)
        self._rnd(c, 0, 0, _SZ, _SZ, 16)
        c.fill()
        self._ph = Gdk.pixbuf_get_from_surface(s, 0, 0, _SZ, _SZ)

    def _uclr(self):
        c = self.get_style_context().get_color(Gtk.StateFlags.NORMAL)
        self._clr = (c.red, c.green, c.blue, c.alpha)

    def _rst(self, f):
        self._files = self._flt = f
        self._idx = 0 if f else -1
        self._off = 0.0
        self._th.clear()
        self._ld.clear()
        if self.get_realized() and f:
            self._sched()
        self.queue_draw()

    def set_files(self, f):
        self._rst(f[:])

    def filter_files(self, q):
        self._rst([f for f in self._files if q.casefold() in f.casefold()] if q else self._files[:])

    def _sched(self):
        if self._ldq or self._dead or self._bnc:
            return
        self._ldq = True
        GLib.idle_add(self._load)

    def _load(self):
        self._ldq = False
        if self._dead or not self._flt:
            return False
        n, cur = len(self._flt), self._idx
        need = {self._flt[(cur + i) % n] for i in range(-_HALF - 1, _HALF + 2)}
        for k in [k for k in self._th if k not in need]:
            del self._th[k]
        for nm in need - self._th.keys() - self._ld:
            self._ld.add(nm)
            self._ex.submit(self._ldth, nm)
        return False

    def _ldth(self, nm):
        if self._dead:
            return
        cp = f"{_THUMBS}/{GLib.compute_checksum_for_string(GLib.ChecksumType.MD5, nm, -1)}_r.png"
        pb = None
        try:
            if Gio.File.new_for_path(cp).query_exists():
                pb = GdkPixbuf.Pixbuf.new_from_file(cp)
        except GLib.Error:
            pass
        if not pb:
            try:
                raw = GdkPixbuf.Pixbuf.new_from_file_at_scale(f"{_WALLS}/{nm}", _SZ, _SZ, True)
                if raw:
                    w, h = raw.get_width(), raw.get_height()
                    sq = raw if w == _SZ == h else (
                        raw.scale_simple(_SZ, _SZ, GdkPixbuf.InterpType.BILINEAR) if w < _SZ or h < _SZ
                        else raw.new_subpixbuf((w - _SZ) >> 1, (h - _SZ) >> 1, _SZ, _SZ))
                    sf = cairo.ImageSurface(cairo.FORMAT_ARGB32, _SZ, _SZ)
                    ct = cairo.Context(sf)
                    self._rnd(ct, 0, 0, _SZ, _SZ, 16)
                    ct.clip()
                    Gdk.cairo_set_source_pixbuf(ct, sq, 0, 0)
                    ct.paint()
                    pb = Gdk.pixbuf_get_from_surface(sf, 0, 0, _SZ, _SZ)
                    try:
                        pb.savev(cp, "png", [], [])
                    except GLib.Error:
                        pass
            except Exception:
                pass
        if not self._dead:
            GLib.idle_add(self._onth, nm, pb)

    def _onth(self, nm, pb):
        self._ld.discard(nm)
        if self._dead or not pb:
            return False
        n, cur = len(self._flt), self._idx
        if nm in {self._flt[(cur + i) % n] for i in range(-_HALF - 1, _HALF + 2)}:
            self._th[nm] = pb
            if not self._bnc and not self._anim:
                self.queue_draw()
        return False

    def _draw(self, w, cr):
        a = w.get_allocation()
        flt = self._flt
        if not flt:
            cr.set_source_rgba(0.6, 0.6, 0.6, 0.6)
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(16)
            t, e = "No wallpapers found", cr.text_extents("No wallpapers found")
            cr.move_to((a.width - e.width) * 0.5, (a.height + e.height) * 0.5)
            cr.show_text(t)
            return

        cx, cy = a.width * 0.5, a.height * 0.5 + 10
        n, off, idx, th, ph = len(flt), self._off, self._idx, self._th, self._ph

        if abs(off) < 0.01:
            by = self._by
            for j in range(7):
                i = _ORD[j]
                pb = th.get(flt[(idx + i) % n], ph)
                if pb:
                    y = cy + _YOF[j] - (by if j == 6 and by > 0 else 0)
                    self._card(cr, pb, cx + i * _SPC, y, _SCL[j], _ALP[j], j == 6)
            return

        items = []
        for i in range(-4, 5):
            p, d = i + off, abs(i + off)
            if d > 4.2:
                continue
            al = 1.0 - d * 0.25
            if al <= 0.05:
                continue
            items.append((d, th.get(flt[(idx + i) % n], ph), cx + p * _SPC, cy + p * p * _ARC_K, max(0.4, 1.0 - d * 0.15), al, d < 0.15))

        for _, pb, x, y, sc, al, sel in sorted(items, reverse=True):
            if pb:
                self._card(cr, pb, x, y, sc, al, sel)

    def _card(self, cr, pb, x, y, sc, al, sel):
        cr.save()
        cr.translate(x, y)
        cr.scale(sc, sc)
        if al > 0.1:
            o = 8 if sel else 5
            cr.set_source_rgba(0, 0, 0, (0.4 if sel else 0.2) * al)
            self._rnd(cr, -_HSZ + o, -_HSZ + o, _SZ, _SZ, 16)
            cr.fill()
        Gdk.cairo_set_source_pixbuf(cr, pb, -_HSZ, -_HSZ)
        cr.paint_with_alpha(al)
        if sel:
            r, g, b, _ = self._clr
            cr.set_source_rgba(r, g, b, 0.3 * al)
            cr.set_line_width(6)
            self._rnd(cr, -_HSZ - 2, -_HSZ - 2, _SZ + 4, _SZ + 4, 18)
            cr.stroke()
            cr.set_source_rgba(r, g, b, 0.9 * al)
            cr.set_line_width(3)
            self._rnd(cr, -_HSZ, -_HSZ, _SZ, _SZ, 16)
            cr.stroke()
        cr.restore()

    def _rnd(self, c, x, y, w, h, r):
        c.new_path()
        c.arc(x + w - r, y + r, r, -1.5708, 0)
        c.arc(x + w - r, y + h - r, r, 0, 1.5708)
        c.arc(x + r, y + h - r, r, 1.5708, 3.1416)
        c.arc(x + r, y + r, r, 3.1416, 4.7124)
        c.close_path()

    def _key(self, _, e):
        k = e.keyval
        if k == Gdk.KEY_Left:
            self.nav(-1)
        elif k == Gdk.KEY_Right:
            self.nav(1)
        elif k in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._sel()
        else:
            return False
        return True

    def _click(self, w, e):
        self.grab_focus()
        if e.button != 1:
            return False
        rx = e.x - w.get_allocation().width * 0.5
        self._sel() if abs(rx) < _HSZ else self.nav(-1 if rx < 0 else 1)
        return True

    def _scroll(self, _, e):
        d = 0
        if e.direction == Gdk.ScrollDirection.UP:
            d = -1
        elif e.direction == Gdk.ScrollDirection.DOWN:
            d = 1
        elif e.direction == Gdk.ScrollDirection.SMOOTH:
            _, dx, dy = e.get_scroll_deltas()
            d = ((1 if dx > 0 else -1) if abs(dx) > 0.5 else 0) if abs(dx) > abs(dy) else ((1 if dy > 0 else -1) if abs(dy) > 0.5 else 0)
        if d and not self._anim:
            self.nav(d)
        return True

    def nav(self, dr, anim=True):
        if not self._flt or (self._anim and anim and not self._spl):
            return
        self._idx = (self._idx + dr) % len(self._flt)
        if anim:
            self._off, self._anim = float(dr), True
            GLib.timeout_add(16, self._slide)
        else:
            self._off = 0.0
            self.queue_draw()
            self._sched()
        if self._on_nav:
            self._on_nav()

    def _slide(self):
        if self._dead:
            return False
        self._off *= 0.7
        if abs(self._off) < 0.01:
            self._off, self._anim = 0.0, False
            self._sched()
        self.queue_draw()
        return self._anim

    def spin(self, tgt, cb=None):
        if self._anim or not self._flt:
            return
        n, d = len(self._flt), 1 if GLib.random_int_range(0, 2) else -1
        dist = ((tgt - self._idx) % n) if d == 1 else ((self._idx - tgt) % n)
        self._spl = dist + GLib.random_int_range(2, 4) * n or n * 2
        self._spt, self._spd, self._spi, self._spcb = self._spl, d, 16, cb
        self._anim = True
        self._spst()

    def _spst(self):
        if self._dead:
            return False
        if self._spl <= 0:
            self._anim, self._off = False, 0.0
            self._sched()
            self.queue_draw()
            self._sel(emit=False)
            if self._spcb:
                self._spcb()
            self._spcb = None
            return False
        self.nav(self._spd, anim=False)
        self._spl -= 1
        p = 1.0 - self._spl / self._spt
        self._spi = 20 if p < 0.6 else 20 + int(((p - 0.6) / 0.4) ** 2 * 200)
        GLib.timeout_add(self._spi, self._spst)
        return False

    def _sel(self, emit=True):
        if not self._flt or self._bnc:
            return
        self._bnc, self._by, self._bvy = True, 0.0, 12.0
        GLib.timeout_add(16, self._bst)
        if emit and self._on_sel and (nm := self.cur()):
            GLib.timeout_add(150, lambda: self._on_sel(nm) or False)

    def _bst(self):
        if self._dead:
            self._bnc = False
            return False
        by, bvy = self._by + self._bvy, self._bvy - 1.5
        if by <= 0:
            bvy_abs = -bvy * 0.5
            if bvy_abs < 4.0:
                self._bnc, self._by = False, 0.0
                GLib.idle_add(self._sched)
                self.queue_draw()
                return False
            self._by, self._bvy = 0.0, bvy_abs
        else:
            self._by, self._bvy = by, bvy
        self.queue_draw()
        return True

    def cur(self):
        return self._flt[self._idx] if self._flt and 0 <= self._idx < len(self._flt) else None

    def idx(self):
        return self._idx

    def set_idx(self, i):
        if self._flt and 0 <= i < len(self._flt):
            self._idx, self._off = i, 0.0
            self._th.clear()
            self._ld.clear()
            self._sched()
            self.queue_draw()
            if self._on_nav:
                self._on_nav()

    def cleanup(self):
        self._dead = True
        self._ex.shutdown(wait=False, cancel_futures=True)
        self._th.clear()
        self._ld.clear()
        self._files = self._flt = []
        self._ph = None


class WallpaperSelector(Box):
    __slots__ = ('_dead', '_pend', '_files', '_mon', '_car', '_ent', '_dd', '_rb', '_lbl')

    def __init__(self, **kw):
        super().__init__(name="wallpapers", spacing=4, orientation="v", **kw)
        self._dead, self._pend, self._files, self._mon = False, {}, [], None

        for p in (_WALLS, _THUMBS):
            f = Gio.File.new_for_path(p)
            if not f.query_exists():
                try:
                    f.make_directory_with_parents(None)
                except GLib.Error:
                    pass

        self._car = WallpaperCarousel(on_select=self._on_sel, on_navigate=self._ulbl)
        ew = Gtk.EventBox()
        ew.add(self._car)
        ew.connect("button-press-event", lambda *_: self._car.grab_focus())
        cb = Box(name="carousel-container", h_align="center", v_align="center")
        cb.pack_start(ew, True, True, 0)

        self._ent = Entry(name="search-entry-walls", placeholder="Search Wallpapers...", h_expand=True, h_align="fill")
        self._ent.connect("notify::text", lambda e, _: self._deb("s", 300, self._srch, e.get_text()))
        self._ent.connect("key-press-event", self._ekey)

        self._dd = Gtk.ComboBoxText(name="scheme-dropdown")
        for k, v in _SCH:
            self._dd.append(k, v)
        self._dd.set_active_id(self._ldsch())
        self._dd.connect("changed", self._schch)

        self._rb = Button(name="random-wall-button", child=Label(name="random-wall-label", markup=_DICE[0]), tooltip_text="Random Wallpaper")
        self._rb.connect("clicked", self.random_wall)
        self._lbl = Label(name="wallpaper-name-label", label="Select a wallpaper")

        self.add(Box(spacing=8, children=[self._rb, self._ent, self._dd]))
        self.pack_start(cb, True, True, 0)
        self.add(self._lbl)
        self._roll()

        self.connect("destroy", self._destroy)
        self.connect("realize", lambda _: self._scan() if not self._files else None)
        self._scan()
        self._watch()

    def _scan(self):
        nf = []
        try:
            d = GLib.Dir.open(_WALLS, 0)
            while (nm := d.read_name()):
                if any(nm.lower().endswith(e) for e in _EXT):
                    nf.append(nm)
        except GLib.Error:
            pass
        nf.sort()
        self._files = nf
        self._car.set_files(nf)
        self._ulbl()

    def _ulbl(self):
        n = self._car.cur()
        self._lbl.set_label(n[:47] + "..." if n and len(n) > 50 else n or "No wallpapers available")

    def _srch(self, t):
        if not self._dead:
            self._car.filter_files(t)
            self._ulbl()

    def _ekey(self, _, e):
        k = e.keyval
        if k in (Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            r = self._car._key(self._car, e)
            if k in (Gdk.KEY_Left, Gdk.KEY_Right):
                GLib.timeout_add(50, self._ulbl)
            return r
        if k == Gdk.KEY_Escape:
            self._ent.set_text("")
            return True
        return False

    def _on_sel(self, nm):
        self._apply(nm)
        self._ulbl()

    def _apply(self, nm, notify=False):
        if nm not in self._files:
            return False
        p, sch = f"{_WALLS}/{nm}", self._dd.get_active_id() or "scheme-tonal-spot"
        try:
            f = Gio.File.new_for_path(_CURRENT)
            if f.query_exists():
                f.delete(None)
            f.make_symbolic_link(p, None)
        except GLib.Error:
            try:
                GLib.spawn_command_line_sync(f'ln -sf "{p}" "{_CURRENT}"')
            except GLib.Error:
                return False
        exec_shell_command_async(f'awww img "{p}" --type outer --transition-duration 0.5 --transition-step 255 --transition-fps 60')
        exec_shell_command_async(f'matugen image "{p}" --type {sch}')
        if notify:
            exec_shell_command_async(f"notify-send '🎲 Wallpaper' 'Random wallpaper set 🎨' -a 'Vidgex-Shell' -i '{p}' -e")
        return True

    def _schch(self, w):
        if s := w.get_active_id():
            try:
                Gio.File.new_for_path(_SCHEME_F).replace_contents(s.encode(), None, False, Gio.FileCreateFlags.REPLACE_DESTINATION, None)
            except GLib.Error:
                pass
            try:
                f = Gio.File.new_for_path(_CURRENT)
                if f.query_info("standard::is-symlink", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None).get_is_symlink():
                    exec_shell_command_async(f'matugen image "{_CURRENT}" -t {s}')
            except GLib.Error:
                pass

    def _roll(self):
        self._rb.get_child().set_markup(_DICE[GLib.random_int_range(0, 6)])

    def random_wall(self, _=None, ext=False):
        if not self._files:
            return
        i, nm = GLib.random_int_range(0, len(self._files)), None
        if self._ent.get_text():
            self._ent.set_text("")
        nm = self._files[i]
        self._car.spin(i, lambda: (self._apply(nm, notify=ext), self._roll(), self._ulbl()))

    def _ldsch(self):
        try:
            f = Gio.File.new_for_path(_SCHEME_F)
            if f.query_exists():
                ok, d, _ = f.load_contents(None)
                if ok and (s := d.decode().strip()) in _SCH_K:
                    return s
        except GLib.Error:
            pass
        return "scheme-tonal-spot"

    def _watch(self):
        try:
            self._mon = Gio.File.new_for_path(_WALLS).monitor_directory(Gio.FileMonitorFlags.NONE, None)
            self._mon.connect("changed", lambda *_: self._deb("r", 1000, self._scan) if not self._dead else None)
        except GLib.Error:
            pass

    def _deb(self, k, ms, f, *a):
        if k in self._pend:
            GLib.source_remove(self._pend[k])
        self._pend[k] = GLib.timeout_add(ms, lambda: (self._pend.pop(k, None), f(*a) if a else f()) or False)

    def _destroy(self, _):
        self._dead = True
        if self._mon:
            self._mon.cancel()
            self._mon = None
        for i in self._pend.values():
            GLib.source_remove(i)
        self._pend.clear()
        self._car.cleanup()
        self._files = []