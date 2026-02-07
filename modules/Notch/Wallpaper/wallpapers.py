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
_WALLS_DIR = f"{_HOME}/.config/Vidgex-Shell/wallpapers/"
_CURRENT = f"{_HOME}/.current.wall"
_CACHE = f"{GLib.get_user_cache_dir()}/vidgex-shell"
_THUMBS = f"{_CACHE}/thumbnails"
_SCHEME_F = f"{_CACHE}/scheme"

_SZ = 180
_HSZ = 90.0
_VIS = 7
_HALF = 3
_SPC = 100
_ARC = 30
_ARC_K = _ARC / 16.0
_DEB = 300

_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")
_SCH = (
    ("scheme-tonal-spot", "Tonal Spot"), ("scheme-content", "Content"),
    ("scheme-expressive", "Expressive"), ("scheme-fidelity", "Fidelity"),
    ("scheme-fruit-salad", "Fruit Salad"), ("scheme-monochrome", "Monochrome"),
    ("scheme-neutral", "Neutral"), ("scheme-rainbow", "Rainbow"),
)
_DICE = (icons.dice_1, icons.dice_2, icons.dice_3, icons.dice_4, icons.dice_5, icons.dice_6)

# Предвычисленный порядок отрисовки (от дальних к ближним)
_STATIC_ORDER = (3, -3, 2, -2, 1, -1, 0)
_STATIC_ALPHA = (0.25, 0.25, 0.5, 0.5, 0.75, 0.75, 1.0)
_STATIC_SCALE = (0.55, 0.55, 0.7, 0.7, 0.85, 0.85, 1.0)
_STATIC_Y_OFF = (16.875, 16.875, 7.5, 7.5, 1.875, 1.875, 0.0)


class WallpaperCarousel(Gtk.DrawingArea):
    def __init__(self, on_select=None, on_navigate=None):
        super().__init__()
        self._files, self._filtered, self._thumbs, self._loading = [], [], {}, set()
        self._idx, self._off, self._by, self._bvy = 0, 0.0, 0.0, 0.0
        self._anim, self._bounce, self._dead, self._load_q = False, False, False, False
        self._spin_l, self._spin_t, self._spin_d, self._spin_i, self._spin_cb = 0, 0, 0, 16, None
        self._primary, self._ph = (1.0, 1.0, 1.0, 1.0), None
        self._exec = ThreadPoolExecutor(max_workers=2, thread_name_prefix="crs")
        self._on_sel, self._on_nav = on_select, on_navigate

        self.set_name("wallpaper-carousel")
        self.set_can_focus(True)
        self.add_events(Gdk.EventMask.KEY_PRESS_MASK | Gdk.EventMask.BUTTON_PRESS_MASK |
                        Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        self.connect("draw", self._draw)
        self.connect("key-press-event", self._key)
        self.connect("button-press-event", self._click)
        self.connect("scroll-event", self._scroll)
        self.connect("realize", lambda _: (self._upd_color(), self._sched()))
        self.connect("style-updated", lambda _: self._upd_color())
        self._mk_ph()
        self.set_size_request(800, 280)

    def _mk_ph(self):
        s = cairo.ImageSurface(cairo.FORMAT_ARGB32, _SZ, _SZ)
        c = cairo.Context(s)
        c.set_source_rgba(0.27, 0.27, 0.27, 0.5)
        self._rnd(c, 0, 0, _SZ, _SZ, 16)
        c.fill()
        self._ph = Gdk.pixbuf_get_from_surface(s, 0, 0, _SZ, _SZ)

    def _upd_color(self):
        c = self.get_style_context().get_color(Gtk.StateFlags.NORMAL)
        self._primary = (c.red, c.green, c.blue, c.alpha)

    def _reset(self, files):
        self._files, self._filtered = files[:], files[:]
        self._idx, self._off = (0 if files else -1), 0.0
        self._thumbs.clear()
        self._loading.clear()
        if self.get_realized() and files:
            self._sched()
        self.queue_draw()

    def set_files(self, f): self._reset(f)

    def filter_files(self, q):
        self._reset([f for f in self._files if q.casefold() in f.casefold()] if q else self._files[:])

    def _sched(self):
        if self._load_q or self._dead or self._bounce:
            return
        self._load_q = True
        GLib.idle_add(self._load)

    def _load(self):
        self._load_q = False
        if self._dead or not self._filtered:
            return False
        n, cur = len(self._filtered), self._idx
        need = {self._filtered[(cur + i) % n] for i in range(-_HALF - 1, _HALF + 2)}
        for k in [k for k in self._thumbs if k not in need]:
            del self._thumbs[k]
        for nm in need - self._thumbs.keys() - self._loading:
            self._loading.add(nm)
            self._exec.submit(self._load_th, nm)
        return False

    def _cache_p(self, n):
        return f"{_THUMBS}/{GLib.compute_checksum_for_string(GLib.ChecksumType.MD5, n, -1)}_r.png"

    def _load_th(self, nm):
        if self._dead:
            return
        cp, pb = self._cache_p(nm), None
        try:
            if Gio.File.new_for_path(cp).query_exists():
                pb = GdkPixbuf.Pixbuf.new_from_file(cp)
        except GLib.Error:
            pass
        if not pb:
            try:
                raw = GdkPixbuf.Pixbuf.new_from_file_at_scale(f"{_WALLS_DIR}/{nm}", _SZ, _SZ, True)
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
            except (GLib.Error, Exception):
                pass
        if not self._dead:
            GLib.idle_add(self._on_th, nm, pb)

    def _on_th(self, nm, pb):
        self._loading.discard(nm)
        if self._dead or not pb:
            return False
        n, cur = len(self._filtered), self._idx
        if nm in {self._filtered[(cur + i) % n] for i in range(-_HALF - 1, _HALF + 2)}:
            self._thumbs[nm] = pb
            if not self._bounce and not self._anim:
                self.queue_draw()
        return False

    def _draw(self, w, cr):
        a = w.get_allocation()
        filtered = self._filtered
        if not filtered:
            cr.set_source_rgba(0.6, 0.6, 0.6, 0.6)
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(16)
            t = "No wallpapers found"
            e = cr.text_extents(t)
            cr.move_to((a.width - e.width) * 0.5, (a.height + e.height) * 0.5)
            cr.show_text(t)
            return

        cx, cy = a.width * 0.5, a.height * 0.5 + 10
        n, off = len(filtered), self._off
        idx, thumbs, ph = self._idx, self._thumbs, self._ph

        # Быстрый путь для bounce/idle (offset ≈ 0)
        if abs(off) < 0.01:
            by = self._by
            for j in range(7):
                i = _STATIC_ORDER[j]
                nm = filtered[(idx + i) % n]
                pb = thumbs.get(nm, ph)
                if pb:
                    y = cy + _STATIC_Y_OFF[j]
                    if j == 6 and by > 0:
                        y -= by
                    self._card(cr, pb, cx + i * _SPC, y, _STATIC_SCALE[j], _STATIC_ALPHA[j], j == 6)
            return

        # Путь со sliding анимацией
        items = []
        for i in range(-4, 5):
            p = i + off
            d = abs(p)
            if d > 4.2:
                continue
            al = 1.0 - d * 0.25
            if al <= 0.05:
                continue
            nm = filtered[(idx + i) % n]
            sc = max(0.4, 1.0 - d * 0.15)
            items.append((d, nm, cx + p * _SPC, cy + p * p * _ARC_K, sc, al, d < 0.15))

        for _, nm, x, y, sc, al, isc in sorted(items, reverse=True):
            pb = thumbs.get(nm, ph)
            if pb:
                self._card(cr, pb, x, y, sc, al, isc)

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
            r, g, b, _ = self._primary
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
        if not self._filtered or (self._anim and anim and not self._spin_l):
            return
        self._idx = (self._idx + dr) % len(self._filtered)
        if anim:
            self._off, self._anim = float(dr), True
            GLib.timeout_add(16, self._slide)
        else:
            self._off = 0.0
            self.queue_draw()
        if not anim:
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
        if self._anim or not self._filtered:
            return
        n, d = len(self._filtered), 1 if GLib.random_int_range(0, 2) else -1
        dist = ((tgt - self._idx) % n) if d == 1 else ((self._idx - tgt) % n)
        t = dist + GLib.random_int_range(2, 4) * n or n * 2
        self._spin_l, self._spin_t, self._spin_d, self._spin_i, self._spin_cb = t, t, d, 16, cb
        self._anim = True
        self._spin_step()

    def _spin_step(self):
        if self._dead:
            return False
        if self._spin_l <= 0:
            self._anim, self._off = False, 0.0
            self._sched()
            self.queue_draw()
            self._sel(emit=False)
            if self._spin_cb:
                self._spin_cb()
            self._spin_cb = None
            return False
        self.nav(self._spin_d, anim=False)
        self._spin_l -= 1
        p = 1.0 - self._spin_l / self._spin_t
        self._spin_i = 20 if p < 0.6 else 20 + int(((p - 0.6) / 0.4) ** 2 * 200)
        GLib.timeout_add(self._spin_i, self._spin_step)
        return False

    def _sel(self, emit=True):
        if not self._filtered or self._bounce:
            return
        self._bounce, self._by, self._bvy = True, 0.0, 12.0
        GLib.timeout_add(16, self._bstep)
        if emit and self._on_sel and (nm := self.cur()):
            GLib.timeout_add(150, lambda: self._on_sel(nm) or False)

    def _bstep(self):
        if self._dead:
            self._bounce = False
            return False
        by = self._by + self._bvy
        bvy = self._bvy - 1.5
        if by <= 0:
            bvy_abs = -bvy * 0.5
            if bvy_abs < 4.0:
                self._bounce, self._by = False, 0.0
                GLib.idle_add(self._sched)
                self.queue_draw()
                return False
            self._by, self._bvy = 0.0, bvy_abs
        else:
            self._by, self._bvy = by, bvy
        self.queue_draw()
        return True

    def cur(self):
        return self._filtered[self._idx] if self._filtered and 0 <= self._idx < len(self._filtered) else None

    def idx(self): return self._idx

    def set_idx(self, i):
        if self._filtered and 0 <= i < len(self._filtered):
            self._idx, self._off = i, 0.0
            self._thumbs.clear()
            self._loading.clear()
            self._sched()
            self.queue_draw()
            if self._on_nav:
                self._on_nav()

    def cleanup(self):
        self._dead = True
        self._exec.shutdown(wait=False, cancel_futures=True)
        self._thumbs.clear()
        self._loading.clear()
        self._ph = None


class WallpaperSelector(Box):
    def __init__(self, **kw):
        super().__init__(name="wallpapers", spacing=4, orientation="v", **kw)
        self._dead, self._pend, self._files, self._mon = False, {}, [], None
        self._ensure()
        self._build()
        self.connect("destroy", self._destroy)
        self.connect("realize", lambda _: self._scan() if not self._files else None)
        self._scan()
        self._watch()

    def _ensure(self):
        for p in (_WALLS_DIR, _THUMBS):
            f = Gio.File.new_for_path(p)
            if not f.query_exists():
                try:
                    f.make_directory_with_parents(None)
                except GLib.Error:
                    pass

    def _build(self):
        self._car = WallpaperCarousel(on_select=self._on_sel, on_navigate=self._upd_lbl)
        ew = Gtk.EventBox()
        ew.add(self._car)
        ew.connect("button-press-event", lambda *_: self._car.grab_focus())
        cb = Box(name="carousel-container", h_align="center", v_align="center")
        cb.pack_start(ew, True, True, 0)

        self._ent = Entry(name="search-entry-walls", placeholder="Search Wallpapers...", h_expand=True, h_align="fill")
        self._ent.connect("notify::text", lambda e, _: self._deb("s", _DEB, self._srch, e.get_text()))
        self._ent.connect("key-press-event", self._ekey)

        self._dd = Gtk.ComboBoxText(name="scheme-dropdown")
        for k, v in _SCH:
            self._dd.append(k, v)
        self._dd.set_active_id(self._ld_sch())
        self._dd.connect("changed", self._sch_ch)

        self._rb = Button(name="random-wall-button", child=Label(name="random-wall-label", markup=_DICE[0]), tooltip_text="Random Wallpaper")
        self._rb.connect("clicked", self.random_wall)
        self._lbl = Label(name="wallpaper-name-label", label="Select a wallpaper")

        self.add(Box(spacing=8, children=[self._rb, self._ent, self._dd]))
        self.pack_start(cb, True, True, 0)
        self.add(self._lbl)
        self._roll()

    def _scan(self):
        nf = []
        try:
            d = GLib.Dir.open(_WALLS_DIR, 0)
            while (nm := d.read_name()):
                if nm.lower().endswith(_EXT):
                    nf.append(nm)
        except GLib.Error:
            pass
        nf.sort()
        self._files = nf
        self._car.set_files(nf)
        self._upd_lbl()

    def _upd_lbl(self):
        n = self._car.cur()
        self._lbl.set_label(n[:47] + "..." if n and len(n) > 50 else n or "No wallpapers available")

    def _srch(self, t):
        if not self._dead:
            self._car.filter_files(t)
            self._upd_lbl()

    def _ekey(self, _, e):
        k = e.keyval
        if k in (Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            r = self._car._key(self._car, e)
            if k in (Gdk.KEY_Left, Gdk.KEY_Right):
                GLib.timeout_add(50, self._upd_lbl)
            return r
        if k == Gdk.KEY_Escape:
            self._ent.set_text("")
            return True
        return False

    def _on_sel(self, nm):
        self._apply(nm)
        self._upd_lbl()

    def _apply(self, nm, notify=False):
        if nm not in self._files:
            return False
        p, sch = f"{_WALLS_DIR}/{nm}", self._dd.get_active_id() or "scheme-tonal-spot"
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

    def _sch_ch(self, w):
        if s := w.get_active_id():
            self._sv_sch(s)
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
        self._car.spin(i, lambda: (self._apply(nm, notify=ext) and self._roll(), self._upd_lbl()))

    def _ld_sch(self):
        try:
            f = Gio.File.new_for_path(_SCHEME_F)
            if f.query_exists():
                ok, d, _ = f.load_contents(None)
                if ok and (s := d.decode().strip()) in [k for k, _ in _SCH]:
                    return s
        except GLib.Error:
            pass
        return "scheme-tonal-spot"

    def _sv_sch(self, s):
        try:
            Gio.File.new_for_path(_SCHEME_F).replace_contents(s.encode(), None, False, Gio.FileCreateFlags.REPLACE_DESTINATION, None)
        except GLib.Error:
            pass

    def _watch(self):
        try:
            self._mon = Gio.File.new_for_path(_WALLS_DIR).monitor_directory(Gio.FileMonitorFlags.NONE, None)
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
        for i in self._pend.values():
            GLib.source_remove(i)
        self._car.cleanup()
        self._files.clear()