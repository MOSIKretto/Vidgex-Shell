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
_WALLS = f"{_HOME}/.config/Vidgex-Shell/wallpapers/"
_CURRENT = f"{_HOME}/.current.wall"
_CACHE = f"{GLib.get_user_cache_dir()}/vidgex-shell"
_THUMBS = f"{_CACHE}/thumbnails"
_SCHEME_F = f"{_CACHE}/scheme"

# Константы переведены во float там, где это нужно, для избежания кастов в рантайме
_SZ, _HSZ, _HALF, _SPC, _ARC_K = 180, 90.0, 3, 100.0, 1.875
_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"} # Set для O(1) поиска
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
_DRAW_ORDER = (4, -4, 3, -3, 2, -2, 1, -1, 0)

# Предварительно вычисленный путь для скругления углов (чтобы не считать математику дуг каждый раз)
def _apply_rounded_path(c, x, y, w, h, r):
    c.new_path()
    c.arc(x + w - r, y + r, r, -1.5708, 0)
    c.arc(x + w - r, y + h - r, r, 0, 1.5708)
    c.arc(x + r, y + h - r, r, 1.5708, 3.1416)
    c.arc(x + r, y + r, r, 3.1416, 4.7124)
    c.close_path()

class WallpaperCarousel(Gtk.DrawingArea):
    __slots__ = ('_files', '_files_lower', '_flt', '_th', '_ld', '_idx', '_off', '_by', '_bvy',
                 '_anim', '_bnc', '_dead', '_ldq', '_spl', '_spt', '_spd', '_spi',
                 '_spcb', '_clr', '_ph', '_ex', '_on_sel', '_on_nav', '_hashes')

    def __init__(self, on_select=None, on_navigate=None):
        super().__init__()
        self._files = []
        self._files_lower = []
        self._flt = []
        self._hashes = {}
        self._th = {}  # Теперь хранит нативные cairo.ImageSurface
        self._ld = set()
        self._idx = 0
        self._off = self._by = self._bvy = 0.0
        self._anim = self._bnc = self._dead = self._ldq = False
        self._spl = self._spt = self._spd = 0
        self._spi, self._spcb = 16, None
        self._clr = (1.0, 1.0, 1.0, 1.0)
        self._ph = None
        # Используем больше потоков для тяжелой генерации
        self._ex = ThreadPoolExecutor(max_workers=min(8, (os.cpu_count() or 2) + 2), thread_name_prefix="w_car")
        self._on_sel = on_select
        self._on_nav = on_navigate

        self.set_name("wallpaper-carousel")
        self.set_can_focus(True)
        self.add_events(Gdk.EventMask.KEY_PRESS_MASK | Gdk.EventMask.BUTTON_PRESS_MASK |
                        Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        
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
        _apply_rounded_path(c, 0, 0, _SZ, _SZ, 16)
        c.fill()
        self._ph = s # Храним как Surface, а не Pixbuf!

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
        self._files_lower = [name.casefold() for name in f]
        # Предрасчет хэшей
        self._hashes = {nm: hashlib.md5(nm.encode('utf-8')).hexdigest() for nm in f}
        self._rst(f.copy())

    def filter_files(self, q):
        if not q:
            self._rst(self._files.copy())
            return
        q_lower = q.casefold()
        self._rst([f for f, fl in zip(self._files, self._files_lower) if q_lower in fl])

    def _sched(self):
        if self._ldq or self._dead or self._bnc: return
        self._ldq = True
        GLib.idle_add(self._load)

    def _load(self):
        self._ldq = False
        if self._dead or not self._flt: return False
            
        n, cur = len(self._flt), self._idx
        need = {self._flt[(cur + i) % n] for i in range(-_HALF - 1, _HALF + 2)}
        
        # Удаляем лишние кэши
        for k in list(self._th.keys()):
            if k not in need: del self._th[k]
                
        # Ставим в очередь новые
        for nm in need - self._th.keys() - self._ld:
            self._ld.add(nm)
            self._ex.submit(self._ldth, nm)
        return False

    def _ldth(self, nm):
        if self._dead: return
            
        md5 = self._hashes.get(nm, "")
        cp = f"{_THUMBS}/{md5}_r.png"
        surf = None
        
        # Пытаемся загрузить уже вырезанную миниатюру (ОЧЕНЬ БЫСТРО)
        if os.path.exists(cp):
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file(cp)
                surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, _SZ, _SZ)
                ctx = cairo.Context(surf)
                Gdk.cairo_set_source_pixbuf(ctx, pb, 0, 0)
                ctx.paint()
            except GLib.Error:
                pass
                
        # Если нет миниатюры, делаем тяжелую работу (ОДИН РАЗ)
        if not surf:
            try:
                raw = GdkPixbuf.Pixbuf.new_from_file_at_scale(f"{_WALLS}/{nm}", _SZ, _SZ, True)
                if raw:
                    w, h = raw.get_width(), raw.get_height()
                    sq = raw if (w == _SZ and h == _SZ) else (
                        raw.scale_simple(_SZ, _SZ, GdkPixbuf.InterpType.BILINEAR) if w < _SZ or h < _SZ 
                        else raw.new_subpixbuf((w - _SZ) >> 1, (h - _SZ) >> 1, _SZ, _SZ)
                    )
                    
                    # Создаем Cairo Surface и сразу вырезаем скругления
                    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, _SZ, _SZ)
                    ct = cairo.Context(surf)
                    _apply_rounded_path(ct, 0, 0, _SZ, _SZ, 16)
                    ct.clip()
                    Gdk.cairo_set_source_pixbuf(ct, sq, 0, 0)
                    ct.paint()
                    
                    # Сохраняем на диск, чтобы больше никогда этого не делать
                    pb_out = Gdk.pixbuf_get_from_surface(surf, 0, 0, _SZ, _SZ)
                    pb_out.savev(cp, "png", [], [])
            except Exception:
                pass
                
        if not self._dead and surf:
            GLib.idle_add(self._onth, nm, surf)

    def _onth(self, nm, surf):
        self._ld.discard(nm)
        if self._dead or not surf: return False
            
        n, cur = len(self._flt), self._idx
        if nm in {self._flt[(cur + i) % n] for i in range(-_HALF - 1, _HALF + 2)}:
            self._th[nm] = surf
            if not self._bnc and not self._anim:
                self.queue_draw()
        return False

    def _draw(self, w, cr):
        # Экстремальная оптимизация метода отрисовки.
        # Никаких clip() или GdkPixbuf здесь нет, только чистый cairo на аппаратном уровне.
        a = w.get_allocation()
        flt, n = self._flt, len(self._flt)
        
        if not n:
            cr.set_source_rgba(0.6, 0.6, 0.6, 0.6)
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(16)
            t = "No wallpapers found"
            e = cr.text_extents(t)
            cr.move_to((a.width - e.width) * 0.5, (a.height + e.height) * 0.5)
            cr.show_text(t)
            return

        cx, cy = a.width * 0.5, a.height * 0.5 + 10.0
        off, idx, th, ph = self._off, self._idx, self._th, self._ph
        _abs = abs # Локальный кэш функции для скорости

        # Если анимации нет (состояние покоя) - ультра-быстрый путь
        if _abs(off) < 0.01:
            by = self._by
            for j in range(7):
                i = _ORD[j]
                surf = th.get(flt[(idx + i) % n], ph)
                y = cy + _YOF[j] - (by if j == 6 and by > 0 else 0)
                self._fast_card(cr, surf, cx + i * _SPC, y, _SCL[j], _ALP[j], j == 6)
            return

        # Кадры анимации (сглаженное движение)
        for i in _DRAW_ORDER:
            p = i + off
            d = _abs(p)
            if d > 4.2: continue
            al = 1.0 - d * 0.25
            if al <= 0.05: continue
                
            surf = th.get(flt[(idx + i) % n], ph)
            self._fast_card(cr, surf, cx + p * _SPC, cy + p * p * _ARC_K, 
                            max(0.4, 1.0 - d * 0.15), al, d < 0.15)

    def _fast_card(self, cr, surf, x, y, sc, al, sel):
        cr.save()
        cr.translate(x, y)
        cr.scale(sc, sc)
        
        # Рисуем саму картинку (уже скругленную в кэше!) - это моментально
        cr.set_source_surface(surf, -_HSZ, -_HSZ)
        cr.paint_with_alpha(al)
        
        # Обводка рисуется ТОЛЬКО для активного элемента, чтобы не тратить такты CPU
        if sel:
            cr.set_source_rgba(*self._clr[:3], 0.9 * al)
            cr.set_line_width(3)
            _apply_rounded_path(cr, -_HSZ, -_HSZ, _SZ, _SZ, 16)
            cr.stroke()
            
        cr.restore()

    def _key(self, _, e):
        k = e.keyval
        if k == Gdk.KEY_Left: self.nav(-1)
        elif k == Gdk.KEY_Right: self.nav(1)
        elif k in (Gdk.KEY_Return, Gdk.KEY_KP_Enter): self._sel()
        else: return False
        return True

    def _click(self, w, e):
        self.grab_focus()
        if e.button != 1: return False
        rx = e.x - w.get_allocation().width * 0.5
        if abs(rx) < _HSZ: self._sel()
        else: self.nav(-1 if rx < 0 else 1)
        return True

    def _scroll(self, _, e):
        d = 0
        if e.direction == Gdk.ScrollDirection.UP: d = -1
        elif e.direction == Gdk.ScrollDirection.DOWN: d = 1
        elif e.direction == Gdk.ScrollDirection.SMOOTH:
            _, dx, dy = e.get_scroll_deltas()
            d = ((1 if dx > 0 else -1) if abs(dx) > 0.5 else 0) if abs(dx) > abs(dy) else ((1 if dy > 0 else -1) if abs(dy) > 0.5 else 0)
            
        if d and not self._anim: self.nav(d)
        return True

    def nav(self, dr, anim=True):
        if not self._flt or (self._anim and anim and not self._spl): return
        self._idx = (self._idx + dr) % len(self._flt)
        if anim:
            self._off, self._anim = float(dr), True
            GLib.timeout_add(16, self._slide)
        else:
            self._off = 0.0
            self.queue_draw()
            self._sched()
            
        if self._on_nav: self._on_nav()

    def _slide(self):
        if self._dead: return False
        self._off *= 0.7
        if abs(self._off) < 0.01:
            self._off, self._anim = 0.0, False
            self._sched()
        self.queue_draw()
        return self._anim

    def spin(self, tgt, cb=None):
        if self._anim or not self._flt: return
        n, d = len(self._flt), 1 if GLib.random_int_range(0, 2) else -1
        dist = ((tgt - self._idx) % n) if d == 1 else ((self._idx - tgt) % n)
        self._spl = dist + GLib.random_int_range(2, 4) * n or n * 2
        self._spt, self._spd, self._spi, self._spcb = self._spl, d, 16, cb
        self._anim = True
        self._spst()

    def _spst(self):
        if self._dead: return False
        if self._spl <= 0:
            self._anim, self._off = False, 0.0
            self._sched()
            self.queue_draw()
            self._sel(emit=False)
            if self._spcb: self._spcb()
            self._spcb = None
            return False
            
        self.nav(self._spd, anim=False)
        self._spl -= 1
        p = 1.0 - self._spl / self._spt
        self._spi = 20 if p < 0.6 else 20 + int(((p - 0.6) / 0.4) ** 2 * 200)
        GLib.timeout_add(self._spi, self._spst)
        return False

    def _sel(self, emit=True):
        if not self._flt or self._bnc: return
        self._bnc, self._by, self._bvy = True, 0.0, 12.0
        GLib.timeout_add(16, self._bst)
        if emit and self._on_sel and (nm := self.cur()):
            GLib.timeout_add(150, self._emit_sel, nm)

    def _emit_sel(self, nm):
        if self._on_sel: self._on_sel(nm)
        return False

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

        os.makedirs(_WALLS, exist_ok=True)
        os.makedirs(_THUMBS, exist_ok=True)

        self._car = WallpaperCarousel(on_select=self._on_sel, on_navigate=self._ulbl)
        ew = Gtk.EventBox()
        ew.add(self._car)
        ew.connect("button-press-event", lambda *_: self._car.grab_focus())
        
        cb = Box(name="carousel-container", h_align="center", v_align="center")
        cb.pack_start(ew, True, True, 0)

        self._ent = Entry(name="search-entry-walls", placeholder="Search Wallpapers...", h_expand=True, h_align="fill")
        self._ent.connect("notify::text", self._on_search_changed)
        self._ent.connect("key-press-event", self._ekey)

        self._dd = Gtk.ComboBoxText(name="scheme-dropdown")
        for k, v in _SCH: self._dd.append(k, v)
        self._dd.set_active_id(self._ldsch())
        self._dd.connect("changed", self._schch)

        self._rb = Button(
            name="random-wall-button", 
            child=Label(name="random-wall-label", markup=_DICE[0]), 
            tooltip_text="Random Wallpaper"
        )
        self._rb.connect("clicked", self.random_wall)
        
        self._lbl = Label(name="wallpaper-name-label", label="Select a wallpaper")

        self.add(Box(spacing=8, children=[self._rb, self._ent, self._dd]))
        self.pack_start(cb, True, True, 0)
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
            # os.scandir работает в разы быстрее, чем os.listdir + проверки
            nf = [f.name for f in os.scandir(_WALLS) if f.is_file() and os.path.splitext(f.name)[1].lower() in _EXT]
            nf.sort()
            self._files = nf
            self._car.set_files(nf)
            self._ulbl()
        except OSError:
            pass

    def _ulbl(self):
        n = self._car.cur()
        n = n.rsplit('.', 1)[0] if n else None
        self._lbl.set_label(n[:47] + "..." if n and len(n) > 50 else n or "No wallpapers available")
        return False

    def _srch(self, t):
        if not self._dead:
            self._car.filter_files(t)
            self._ulbl()

    def _ekey(self, _, e):
        k = e.keyval
        if k in (Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            r = self._car._key(self._car, e)
            if k in (Gdk.KEY_Left, Gdk.KEY_Right): GLib.timeout_add(50, self._ulbl)
            return r
        if k == Gdk.KEY_Escape:
            self._ent.set_text("")
            return True
        return False

    def _on_sel(self, nm):
        self._apply(nm)
        self._ulbl()

    def _apply(self, nm, notify=False):
        if nm not in self._files: return False
            
        p, sch = f"{_WALLS}/{nm}", self._dd.get_active_id() or "scheme-tonal-spot"
        
        try:
            if os.path.lexists(_CURRENT): os.remove(_CURRENT)
            os.symlink(p, _CURRENT)
        except OSError: return False
            
        exec_shell_command_async(f'awww img "{p}" --type outer --transition-duration 0.5 --transition-step 255 --transition-fps 60')
        exec_shell_command_async(f'matugen image "{p}" --type {sch}')
        if notify:
            exec_shell_command_async(f"notify-send '🎲 Wallpaper' 'Random wallpaper set 🎨' -a 'Vidgex-Shell' -i '{p}' -e")
        return True

    def _schch(self, w):
        if s := w.get_active_id():
            try:
                with open(_SCHEME_F, 'w') as f: f.write(s)
                if os.path.islink(_CURRENT):
                    exec_shell_command_async(f'matugen image "{_CURRENT}" -t {s}')
            except OSError: pass

    def _roll(self):
        self._rb.get_child().set_markup(_DICE[GLib.random_int_range(0, 6)])

    def random_wall(self, _=None, ext=False):
        if not self._files: return
        i = GLib.random_int_range(0, len(self._files))
        if self._ent.get_text(): self._ent.set_text("")
        nm = self._files[i]
        self._car.spin(i, lambda: self._on_spin_done(nm, ext))

    def _on_spin_done(self, nm, ext):
        self._apply(nm, notify=ext)
        self._roll()
        self._ulbl()

    def _ldsch(self):
        try:
            with open(_SCHEME_F, 'r') as f:
                s = f.read().strip()
                if s in _SCH_K: return s
        except OSError: pass
        return "scheme-tonal-spot"

    def _watch(self):
        try:
            self._mon = Gio.File.new_for_path(_WALLS).monitor_directory(Gio.FileMonitorFlags.NONE, None)
            self._mon.connect("changed", lambda *_: self._deb("r", 1000, self._scan) if not self._dead else None)
        except GLib.Error: pass

    def _deb_wrapper(self, k, func, *args):
        self._pend.pop(k, None)
        func(*args) if args else func()
        return False

    def _deb(self, k, ms, func, *args):
        if k in self._pend: GLib.source_remove(self._pend[k])
        self._pend[k] = GLib.timeout_add(ms, self._deb_wrapper, k, func, *args)

    def _destroy(self, _):
        self._dead = True
        if self._mon: self._mon.cancel()
        for i in self._pend.values(): GLib.source_remove(i)
        self._pend.clear()
        self._car.cleanup()
        self._files = []