import os
import hashlib
from concurrent.futures import ThreadPoolExecutor
import cairo

from fabric.utils.helpers import exec_shell_command_async
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.entry import Entry
from fabric.widgets.label import Label

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk
import services.icons as icons


_HOME = GLib.get_home_dir()
_WALLS = _HOME + "/.config/Vidgex-Shell/wallpapers/"
_CURRENT = _HOME + "/.current.wall"
_CACHE = GLib.get_user_cache_dir() + "/vidgex-shell"
_THUMBS = _CACHE + "/thumbnails/"
_SCHEME_F = _CACHE + "/scheme"

_SZ = 180
_HSZ = 90.0
_NHSZ = -90.0
_SPC = 100.0
_ARC_K = 1.875
_LOAD_RNG = range(-4, 5)
_CR = 16
_SUFFIX = "_r.png"
_SFXL = 6
_EXT = frozenset((".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"))
_ANG = (-1.5707963267948966, 0.0, 1.5707963267948966, 3.141592653589793, 4.71238898038469)

_SCH = (
    ("scheme-tonal-spot", "Tonal Spot"), ("scheme-content", "Content"),
    ("scheme-expressive", "Expressive"), ("scheme-fidelity", "Fidelity"),
    ("scheme-fruit-salad", "Fruit Salad"), ("scheme-monochrome", "Monochrome"),
    ("scheme-neutral", "Neutral"), ("scheme-rainbow", "Rainbow"),
)
_SCH_K = frozenset(k for k, _ in _SCH)
_SCH_LEN = len(_SCH)
_DICE = (icons.dice_1, icons.dice_2, icons.dice_3, icons.dice_4, icons.dice_5, icons.dice_6)

_DRAW_ORDER = (4, -4, 3, -3, 2, -2, 1, -1, 0)
_STATIC = (
    (3, 0.55, 0.25, 16.875, False), (-3, 0.55, 0.25, 16.875, False),
    (2, 0.7, 0.5, 7.5, False), (-2, 0.7, 0.5, 7.5, False),
    (1, 0.85, 0.75, 1.875, False), (-1, 0.85, 0.75, 1.875, False),
    (0, 1.0, 1.0, 0.0, True),
)


def _md5hex(s):
    return hashlib.md5(s.encode()).hexdigest()


def _rpath(c, x, y, w, h, r):
    xr, yr, yhr, xrr = x + w - r, y + r, y + h - r, x + r
    a = _ANG
    c.new_path()
    c.arc(xr, yr, r, a[0], a[1])
    c.arc(xr, yhr, r, a[1], a[2])
    c.arc(xrr, yhr, r, a[2], a[3])
    c.arc(xrr, yr, r, a[3], a[4])
    c.close_path()


class WallpaperCarousel(Gtk.DrawingArea):
    __slots__ = (
        '_files', '_flt', '_th', '_ld', '_idx', '_off',
        '_by', '_bvy', '_anim', '_bnc', '_dead', '_ldq',
        '_spl', '_spt', '_spd', '_spi', '_spcb',
        '_clr', '_ph', '_ex', '_on_sel', '_on_nav',
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

    # ── thumbnail pipeline ──────────────────────────────────

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

    # ── drawing ─────────────────────────────────────────────

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

    # ── input ───────────────────────────────────────────────

    def _key(self, _, e):
        k = e.keyval
        if k == Gdk.KEY_Left:
            self.nav(-1)
        elif k == Gdk.KEY_Right:
            self.nav(1)
        elif k == Gdk.KEY_Return or k == Gdk.KEY_KP_Enter:
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

    # ── navigation & animation ──────────────────────────────

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
            self._on_nav()

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
        dist = ((tgt - self._idx) % n) if d == 1 else ((self._idx - tgt) % n)
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
        self._spi = 20 if p < 0.6 else 20 + int(((p - 0.6) * 2.5) ** 2 * 200)
        GLib.timeout_add(self._spi, self._spst)
        return False

    # ── selection & bounce ──────────────────────────────────

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


class WallpaperSelector(Box):
    __slots__ = (
        '_dead', '_pend', '_files', '_mon', '_car', '_ent',
        '_sch_btn', '_sch_rev', '_sch_idx', '_rb', '_lbl',
    )

    def __init__(self, **kw):
        super().__init__(name="wallpapers", spacing=4, orientation="v", **kw)
        self._dead = False
        self._pend = {}
        self._files = ()
        self._mon = None

        os.makedirs(_WALLS, exist_ok=True)
        os.makedirs(_THUMBS, exist_ok=True)

        self._car = WallpaperCarousel(
            on_select=self._on_sel, on_navigate=self._ulbl
        )
        ew = Gtk.EventBox()
        ew.add(self._car)
        ew.connect("button-press-event", lambda *_: self._car.grab_focus())

        cb = Box(name="carousel-container", h_align="center", v_align="center")
        cb.pack_start(ew, True, True, 0)

        self._ent = Entry(
            name="search-entry-walls",
            placeholder="Search Wallpapers...",
            h_expand=True,
            h_align="fill",
        )
        self._ent.connect("notify::text", self._on_search_changed)
        self._ent.connect("key-press-event", self._ekey)

        self._sch_idx = 0
        cur_sch_id = self._ldsch()
        for i, (k, _) in enumerate(_SCH):
            if k == cur_sch_id:
                self._sch_idx = i
                break

        self._sch_btn = Button(
            name="scheme-dropdown-btn",
            label=_SCH[self._sch_idx][1],
            tooltip_text="Click to select scheme, or scroll",
        )

        self._sch_rev = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN
        )
        self._sch_rev.set_halign(Gtk.Align.END)
        self._sch_rev.set_valign(Gtk.Align.START)

        list_box = Box(orientation="v", name="scheme-list-container")
        for i, (_, v) in enumerate(_SCH):
            btn = Button(label=v, name="scheme-list-item")
            btn.get_child().set_halign(Gtk.Align.START)
            btn.connect("clicked", lambda _, idx=i: self._on_list_item_clicked(idx))
            list_box.add(btn)
        self._sch_rev.add(list_box)

        self._sch_btn.connect(
            "clicked",
            lambda *_: self._sch_rev.set_reveal_child(
                not self._sch_rev.get_reveal_child()
            ),
        )
        self._sch_btn.add_events(Gdk.EventMask.SCROLL_MASK)
        self._sch_btn.connect("scroll-event", self._on_sch_scroll)

        self._rb = Button(
            name="random-wall-button",
            child=Label(name="random-wall-label", markup=_DICE[0]),
            tooltip_text="Random Wallpaper",
        )
        self._rb.connect("clicked", self.random_wall)

        self._lbl = Label(name="wallpaper-name-label", label="Select a wallpaper")

        header = Box(spacing=8, children=[self._rb, self._ent, self._sch_btn])

        overlay = Gtk.Overlay()
        overlay.add(cb)
        overlay.add_overlay(self._sch_rev)

        self.add(header)
        self.pack_start(overlay, True, True, 0)
        self.add(self._lbl)
        self._roll()

        self.connect("destroy", self._destroy)
        self.connect("realize", lambda *_: self._scan() if not self._files else None)
        self._scan()
        self._watch()

    def _on_search_changed(self, entry, _):
        self._deb("s", 300, self._srch, entry.get_text())

    def _scan(self):
        try:
            nf = tuple(sorted(
                e.name
                for e in os.scandir(_WALLS)
                if e.is_file(follow_symlinks=False)
                and os.path.splitext(e.name)[1].lower() in _EXT
            ))
            self._files = nf
            self._car.set_files(nf)
            self._ulbl()
            self._car._ex.submit(self._clean_thumbs, nf)
        except OSError:
            pass

    @staticmethod
    def _clean_thumbs(files):
        try:
            valid = frozenset(_md5hex(nm) for nm in files)
            for entry in os.scandir(_THUMBS):
                if not entry.is_file(follow_symlinks=False):
                    continue
                name = entry.name
                if name.endswith(_SUFFIX):
                    if name[:-_SFXL] in valid:
                        continue
                try:
                    os.remove(entry.path)
                except OSError:
                    pass
        except OSError:
            pass

    def _ulbl(self):
        nm = self._car.cur()
        if nm:
            nm = nm.rsplit(".", 1)[0]
            if len(nm) > 50:
                nm = nm[:47] + "..."
        self._lbl.set_label(nm or "No wallpapers available")
        return False

    def _srch(self, t):
        if not self._dead:
            self._car.filter_files(t)
            self._ulbl()

    def _ekey(self, _, e):
        k = e.keyval
        if k in (Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            r = self._car._key(self._car, e)
            if k == Gdk.KEY_Left or k == Gdk.KEY_Right:
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

        p = _WALLS + nm
        sch = _SCH[self._sch_idx][0]

        try:
            if os.path.lexists(_CURRENT):
                os.remove(_CURRENT)
            os.symlink(p, _CURRENT)
        except OSError:
            return False

        exec_shell_command_async(
            f'awww img "{p}" --type outer --transition-duration 0.5'
            f" --transition-step 255 --transition-fps 60"
        )
        exec_shell_command_async(f'matugen image "{p}" --type {sch}')
        if notify:
            exec_shell_command_async(
                f"notify-send '🎲 Wallpaper' 'Random wallpaper set 🎨'"
                f" -a 'Vidgex-Shell' -i '{p}' -e"
            )
        return True

    def _on_list_item_clicked(self, idx):
        self._sch_rev.set_reveal_child(False)
        if self._sch_idx != idx:
            self._set_scheme(idx)

    def _on_sch_scroll(self, _, e):
        if e.direction == Gdk.ScrollDirection.UP:
            self._set_scheme(self._sch_idx - 1)
        elif e.direction == Gdk.ScrollDirection.DOWN:
            self._set_scheme(self._sch_idx + 1)
        return True

    def _set_scheme(self, idx):
        self._sch_idx = idx % _SCH_LEN
        sch_id, sch_name = _SCH[self._sch_idx]
        self._sch_btn.set_label(sch_name)

        try:
            with open(_SCHEME_F, "w") as f:
                f.write(sch_id)
            if os.path.exists(_CURRENT):
                exec_shell_command_async(
                    f'matugen image "{_CURRENT}" -t {sch_id}'
                )
        except OSError:
            pass

    def _roll(self):
        self._rb.get_child().set_markup(_DICE[GLib.random_int_range(0, 6)])

    def random_wall(self, _=None, ext=False):
        if not self._files:
            return
        i = GLib.random_int_range(0, len(self._files))
        if self._ent.get_text():
            self._ent.set_text("")
        nm = self._files[i]
        self._car.spin(i, lambda: self._on_spin_done(nm, ext))

    def _on_spin_done(self, nm, ext):
        self._apply(nm, notify=ext)
        self._roll()
        self._ulbl()

    def _ldsch(self):
        try:
            with open(_SCHEME_F) as f:
                s = f.read().strip()
                if s in _SCH_K:
                    return s
        except OSError:
            pass
        return "scheme-tonal-spot"

    def _watch(self):
        try:
            self._mon = Gio.File.new_for_path(_WALLS).monitor_directory(
                Gio.FileMonitorFlags.NONE, None
            )
            self._mon.connect(
                "changed",
                lambda *_: self._deb("r", 1000, self._scan)
                if not self._dead
                else None,
            )
        except GLib.Error:
            pass

    def _deb(self, k, ms, func, *args):
        old = self._pend.get(k)
        if old:
            GLib.source_remove(old)
        self._pend[k] = GLib.timeout_add(ms, self._deb_run, k, func, *args)

    def _deb_run(self, k, func, *args):
        self._pend.pop(k, None)
        func(*args) if args else func()
        return False

    def _destroy(self, _):
        self._dead = True
        if self._mon:
            self._mon.cancel()
        for sid in self._pend.values():
            GLib.source_remove(sid)
        self._pend.clear()
        self._car.cleanup()
        self._files = ()