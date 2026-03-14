import cairo
from concurrent.futures import ThreadPoolExecutor

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from modules.Notch.MainWindow.Wallpaper.wallpaperConstants import (
    _ARC_K, _CR, _DRAW_ORDER, _HSZ,
    _LOAD_RNG, _NHSZ, _SPC, _STATIC,
    _SUFFIX, _SZ, _THUMBS, _WALLS,
)
from modules.Notch.MainWindow.Wallpaper.wallpaperUtils import _md5hex, _rpath


class WallpaperCarousel(Gtk.DrawingArea):
    __slots__ = (
        "_files", "_flt", "_th", "_ld", "_idx", "_off",
        "_by", "_bvy", "_anim", "_bnc", "_dead", "_ldq",
        "_spl", "_spt", "_spd", "_spi", "_spcb",
        "_clr", "_ph", "_ex", "_on_sel", "_on_nav",
    )

    def __init__(self, on_select=None, on_navigate=None):
        super().__init__()
        self._files = self._flt = ()
        self._th = {}
        self._ld = set()
        self._idx = 0
        self._off = self._by = self._bvy = 0.0
        self._anim = self._bnc = self._dead = self._ldq = False
        self._spl = self._spt = self._spd = 0
        self._spi = 16
        self._spcb = None
        self._clr = (1.0, 1.0, 1.0, 1.0)
        self._ph = None
        self._ex = ThreadPoolExecutor(max_workers=2, thread_name_prefix="w")
        self._on_sel = on_select
        self._on_nav = on_navigate

        self.set_name("wallpaper-carousel")
        self.set_can_focus(True)
        self.add_events(
            Gdk.EventMask.KEY_PRESS_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.SCROLL_MASK
            | Gdk.EventMask.SMOOTH_SCROLL_MASK
        )
        self.connect("draw", self._draw)
        self.connect("key-press-event", self._key)
        self.connect("button-press-event", self._click)
        self.connect("scroll-event", self._scroll)
        self.connect("realize", self._on_realize)
        self.connect("style-updated", self._on_style_updated)
        self.set_size_request(800, 280)

    def _on_realize(self, *_):
        self._mkph()
        self._uclr()
        self._sched()

    def _on_style_updated(self, *_):
        self._uclr()

    def _mkph(self):
        s = cairo.ImageSurface(cairo.FORMAT_ARGB32, _SZ, _SZ)
        c = cairo.Context(s)
        c.set_source_rgba(0.27, 0.27, 0.27, 0.5)
        _rpath(c, 0, 0, _SZ, _SZ, _CR)
        c.fill()
        self._ph = s

    def _uclr(self):
        c = self.get_style_context().get_color(Gtk.StateFlags.NORMAL)
        self._clr = (c.red, c.green, c.blue, c.alpha)

    def _rst(self, f):
        self._flt = f
        self._idx = 0 if f else -1
        self._off = 0.0
        self._th.clear()
        self._ld.clear()
        if self.get_realized() and f:
            self._sched()
        self.queue_draw()

    def set_files(self, f):
        self._files = f
        self._rst(f)

    def filter_files(self, q):
        if not q:
            self._rst(self._files)
            return
        ql = q.casefold()
        self._rst(tuple(f for f in self._files if ql in f.casefold()))

    def _sched(self):
        if self._ldq or self._dead or self._bnc:
            return
        self._ldq = True
        GLib.idle_add(self._load)

    def _load(self):
        self._ldq = False
        flt = self._flt
        if self._dead or not flt:
            return False

        n, cur = len(flt), self._idx
        need = {flt[(cur + i) % n] for i in _LOAD_RNG}

        th = self._th
        for k in list(th):
            if k not in need:
                del th[k]

        ld, submit, ldth = self._ld, self._ex.submit, self._ldth
        for nm in need:
            if nm not in th and nm not in ld:
                ld.add(nm)
                submit(ldth, nm)
        return False

    def _ldth(self, nm):
        if self._dead:
            return

        cp = _THUMBS + _md5hex(nm) + _SUFFIX
        surf = None

        try:
            surf = cairo.ImageSurface.create_from_png(cp)
            if surf.get_width() != _SZ or surf.get_height() != _SZ:
                surf = None
        except Exception:
            pass

        if not surf:
            try:
                raw = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    _WALLS + nm, _SZ, _SZ, True
                )
                if raw:
                    w, h = raw.get_width(), raw.get_height()
                    if w == _SZ and h == _SZ:
                        sq = raw
                    elif w < _SZ or h < _SZ:
                        sq = raw.scale_simple(
                            _SZ, _SZ, GdkPixbuf.InterpType.BILINEAR
                        )
                    else:
                        sq = raw.new_subpixbuf(
                            (w - _SZ) >> 1, (h - _SZ) >> 1, _SZ, _SZ
                        )

                    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, _SZ, _SZ)
                    ct = cairo.Context(surf)
                    _rpath(ct, 0, 0, _SZ, _SZ, _CR)
                    ct.clip()
                    Gdk.cairo_set_source_pixbuf(ct, sq, 0, 0)
                    ct.paint()
                    del ct, sq, raw
                    surf.write_to_png(cp)
            except Exception:
                surf = None

        if not self._dead and surf:
            GLib.idle_add(self._onth, nm, surf)

    def _onth(self, nm, surf):
        self._ld.discard(nm)
        if self._dead or not surf:
            return False

        flt = self._flt
        n = len(flt)
        if not n:
            return False

        cur = self._idx
        if any(flt[(cur + i) % n] == nm for i in _LOAD_RNG):
            self._th[nm] = surf
            if not self._bnc and not self._anim:
                self.queue_draw()
        return False

    def _draw(self, w, cr):
        alloc = w.get_allocation()
        flt = self._flt
        n = len(flt)

        if not n:
            cr.set_source_rgba(0.6, 0.6, 0.6, 0.6)
            cr.select_font_face(
                "Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL
            )
            cr.set_font_size(16)
            t = "No wallpapers found"
            e = cr.text_extents(t)
            cr.move_to(
                (alloc.width - e.width) * 0.5,
                (alloc.height + e.height) * 0.5,
            )
            cr.show_text(t)
            return

        cx = alloc.width * 0.5
        cy = alloc.height * 0.5 + 10.0
        off = self._off
        idx = self._idx
        th_get = self._th.get
        ph = self._ph
        fc = self._fast_card

        if -0.01 < off < 0.01:
            by = self._by
            for i, sc, al, yof, sel in _STATIC:
                s = th_get(flt[(idx + i) % n], ph)
                y = cy + yof
                if sel and by > 0:
                    y -= by
                fc(cr, s, cx + i * _SPC, y, sc, al, sel)
            return

        for i in _DRAW_ORDER:
            p = i + off
            d = p if p >= 0 else -p
            if d > 4.2:
                continue
            al = 1.0 - d * 0.25
            if al <= 0.05:
                continue
            fc(
                cr,
                th_get(flt[(idx + i) % n], ph),
                cx + p * _SPC,
                cy + p * p * _ARC_K,
                max(0.4, 1.0 - d * 0.15),
                al,
                d < 0.15,
            )

    def _fast_card(self, cr, surf, x, y, sc, al, sel):
        cr.save()
        cr.translate(x, y)
        cr.scale(sc, sc)
        cr.set_source_surface(surf, _NHSZ, _NHSZ)
        cr.paint_with_alpha(al)
        if sel:
            clr = self._clr
            cr.set_source_rgba(clr[0], clr[1], clr[2], 0.9 * al)
            cr.set_line_width(3)
            _rpath(cr, _NHSZ, _NHSZ, _SZ, _SZ, _CR)
            cr.stroke()
        cr.restore()

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
        if -_HSZ < rx < _HSZ:
            self._sel()
        else:
            self.nav(-1 if rx < 0 else 1)
        return True

    def _scroll(self, _, e):
        d = 0
        direction = e.direction
        if direction == Gdk.ScrollDirection.UP:
            d = -1
        elif direction == Gdk.ScrollDirection.DOWN:
            d = 1
        elif direction == Gdk.ScrollDirection.SMOOTH:
            _, dx, dy = e.get_scroll_deltas()
            adx, ady = abs(dx), abs(dy)
            if adx > ady:
                if adx > 0.5:
                    d = 1 if dx > 0 else -1
            elif ady > 0.5:
                d = 1 if dy > 0 else -1
        if d and not self._anim:
            self.nav(d)
        return True

    def nav(self, dr, anim=True):
        if not self._flt or (self._anim and anim and not self._spl):
            return
        self._idx = (self._idx + dr) % len(self._flt)
        if anim:
            self._off = float(dr)
            self._anim = True
            GLib.timeout_add(16, self._slide)
        else:
            self._off = 0.0
            self.queue_draw()
            self._sched()
        if self._on_nav:
            self._on_nav(dr)

    def _slide(self):
        if self._dead:
            return False
        off = self._off * 0.7
        if -0.01 < off < 0.01:
            self._off = 0.0
            self._anim = False
            self._sched()
            self.queue_draw()
            return False
        self._off = off
        self.queue_draw()
        return True

    def spin(self, tgt, cb=None):
        if self._anim or not self._flt:
            return
        n = len(self._flt)
        d = 1 if GLib.random_int_range(0, 2) else -1
        dist = (
            ((tgt - self._idx) % n)
            if d == 1
            else ((self._idx - tgt) % n)
        )
        self._spl = dist + GLib.random_int_range(2, 4) * n or n * 2
        self._spt = self._spl
        self._spd = d
        self._spi = 16
        self._spcb = cb
        self._anim = True
        self._spst()

    def _spst(self):
        if self._dead:
            return False
        if self._spl <= 0:
            self._anim = False
            self._off = 0.0
            self._sched()
            self.queue_draw()
            self._sel(emit=False)
            cb = self._spcb
            self._spcb = None
            if cb:
                cb()
            return False
        self.nav(self._spd, anim=False)
        self._spl -= 1
        p = 1.0 - self._spl / self._spt
        self._spi = (
            20 if p < 0.6 else 20 + int(((p - 0.6) * 2.5) ** 2 * 200)
        )
        GLib.timeout_add(self._spi, self._spst)
        return False

    def _sel(self, emit=True):
        if not self._flt or self._bnc:
            return
        self._bnc = True
        self._by = 0.0
        self._bvy = 12.0
        GLib.timeout_add(16, self._bst)
        if emit and self._on_sel:
            nm = self.cur()
            if nm:
                GLib.timeout_add(150, self._emit_sel, nm)

    def _emit_sel(self, nm):
        if self._on_sel:
            self._on_sel(nm)
        return False

    def _bst(self):
        if self._dead:
            self._bnc = False
            return False
        by = self._by + self._bvy
        bvy = self._bvy - 1.5
        if by <= 0:
            bvy_abs = -bvy * 0.5
            if bvy_abs < 4.0:
                self._bnc = False
                self._by = 0.0
                GLib.idle_add(self._sched)
                self.queue_draw()
                return False
            self._by = 0.0
            self._bvy = bvy_abs
        else:
            self._by = by
            self._bvy = bvy
        self.queue_draw()
        return True

    def cur(self):
        flt = self._flt
        idx = self._idx
        return flt[idx] if flt and 0 <= idx < len(flt) else None

    def cleanup(self):
        self._dead = True
        self._ex.shutdown(wait=False, cancel_futures=True)
        self._th.clear()
        self._ld.clear()
        self._files = self._flt = ()
        self._ph = None