import os
import time
import random
import hashlib
from concurrent.futures import ThreadPoolExecutor

import cairo
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.entry import Entry
from fabric.widgets.label import Label
from fabric.utils.helpers import exec_shell_command_async

import services.icons as icons

from modules.Notch.MainWindow.Wallpaper.colors import (
    apply_colors, _CURRENT, _CSS_OUT, _HYPR_OUT, _SCH,
)

_HOME = GLib.get_home_dir()
_CACHE = GLib.get_user_cache_dir() + "/vidgex-shell"

_THUMBS = _CACHE + "/thumbnails/"
_SCHEME_F = _CACHE + "/scheme"
_WALLDIR_F = _CACHE + "/walldir"
_PICK_PROMPT = "Select wallpaper folder"


def _load_walls_dir():
    if not os.path.exists(_WALLDIR_F):
        return None
    with open(_WALLDIR_F) as f:
        p = f.read().strip()
    if not p:
        return None
    return p if p.endswith("/") else p + "/"


_INITIAL_WALLS_DIR = _load_walls_dir()

_SCH_LEN = len(_SCH)
_EXT = frozenset((".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"))
_SUFFIX = "_r.png"
_SFXL = 6


def _safe_folder(p):
    if p:
        clean = os.path.realpath(p.rstrip("/"))
        if os.path.isdir(clean):
            return clean
    return os.path.realpath(_HOME.rstrip("/"))


_SZ = 180
_HSZ = 90.0
_NHSZ = -90.0
_SPC = 100.0
_ARC_K = 1.875
_LOAD_RNG = range(-4, 5)
_CR = 16
_FRAME_MS = 20
_DECAY = 0.65
_EPS = 0.015
_SPIN_STEP_CAP = 40

_ANG = (
    -1.5707963267948966, 0.0, 1.5707963267948966,
    3.141592653589793, 4.71238898038469,
)

_DRAW_ORDER = (4, -4, 3, -3, 2, -2, 1, -1, 0)

_STATIC = (
    (3,  0.55, 0.25, 16.875, False),
    (-3, 0.55, 0.25, 16.875, False),
    (2,  0.7,  0.5,   7.5,   False),
    (-2, 0.7,  0.5,   7.5,   False),
    (1,  0.85, 0.75,  1.875, False),
    (-1, 0.85, 0.75,  1.875, False),
    (0,  1.0,  1.0,   0.0,   True),
)

_DICE = (
    icons.dice_1, icons.dice_2, icons.dice_3,
    icons.dice_4, icons.dice_5, icons.dice_6,
)

_ARR_L = (
    "    _ ",
    "  /№; ",
    " /!#  ",
    "/@(&  ",
    " \\][ ",
    "  \\# ",
    "   ‾‾ ",
)
_ARR_R = (
    "_     ",
    ";$\\  ",
    " #!\\ ",
    " ?(@\\",
    " |#/  ",
    " //   ",
    "‾‾    ",
)
_ARR_LINES = (_ARR_L, _ARR_R)

_ARR_FONT_PT = 8
_ARR_FONT = Pango.FontDescription.from_string("monospace")
_ARR_FONT.set_size(_ARR_FONT_PT * Pango.SCALE)
_ARR_FONT.set_weight(Pango.Weight.BOLD)
_ARR_FONT_STR = _ARR_FONT.to_string()

_GL_FRAMES = 14
_GL_FRAME_MS = 35
_CF_BURSTS = 2

_GLITCH_CLASSES = (
    "glitch-shift-right",
    "glitch-shift-left",
    "glitch-flicker",
    "glitch-aberration",
    "glitch-heavy",
    "glitch-color-swap",
)

_NO_CLASSES = ()


def _apply_glitch(ctx, chance, active):
    if active:
        for cls in active:
            ctx.remove_class(cls)
    if random.random() >= chance:
        return _NO_CLASSES
    classes = random.sample(_GLITCH_CLASSES, 1 if random.random() > 0.4 else 2)
    for cls in classes:
        ctx.add_class(cls)
    return classes


_md5 = hashlib.md5
_CMAP = {}
_MD5_CACHE = {}


def _md5hex(s):
    v = _MD5_CACHE.get(s)
    if v is None:
        v = _md5(s.encode()).hexdigest()
        _MD5_CACHE[s] = v
    return v


def _rpath(c, x, y, w, h, r):
    xr = x + w - r
    yr = y + r
    yhr = y + h - r
    xrr = x + r
    a0, a1, a2, a3, a4 = _ANG
    c.new_path()
    c.arc(xr,  yr,  r, a0, a1)
    c.arc(xr,  yhr, r, a1, a2)
    c.arc(xrr, yhr, r, a2, a3)
    c.arc(xrr, yr,  r, a3, a4)
    c.close_path()


def _get_primary_hex(widget):
    ctx = widget.get_style_context()
    found, rgba = ctx.lookup_color("primary")
    if not found:
        rgba = ctx.get_color(Gtk.StateFlags.NORMAL)
    return (
        f"#{int(rgba.red * 255):02x}"
        f"{int(rgba.green * 255):02x}"
        f"{int(rgba.blue * 255):02x}"
    )


def _arr_set_art(lbl, lines):
    fg = _get_primary_hex(lbl)
    ml = []
    for line in lines:
        parts = []
        for ch in line:
            v = _CMAP.get(ch)
            if v is None:
                esc = (
                    ch.replace("&", "&amp;")
                      .replace("<", "&lt;")
                      .replace(">", "&gt;")
                )
                v = esc if ch == " " else f"<b>{esc}</b>"
                _CMAP[ch] = v
            parts.append(v)
        ml.append("".join(parts))
    inner = "\n".join(ml)
    lbl.set_markup(
        f'<span font_desc="{_ARR_FONT_STR}" foreground="{fg}">{inner}</span>'
    )


def _short_path(p):
    if not p:
        return _PICK_PROMPT
    p = p.rstrip("/")
    if p == _HOME:
        p = "~"
    elif p.startswith(_HOME + "/"):
        p = "~" + p[len(_HOME):]
    if len(p) > 32:
        return p[:14] + "…" + p[-17:]
    return p


def _remove_source(tid):
    if tid is not None:
        GLib.source_remove(tid)


class FixedBox(Box):
    __gtype_name__ = "FixedBox"

    def __init__(self, fixed_width=380, **kw):
        super().__init__(**kw)
        self._fixed_width = fixed_width
        self.set_size_request(fixed_width, -1)

    def do_get_preferred_width(self):
        return self._fixed_width, self._fixed_width

    def do_get_preferred_width_for_height(self, _height):
        return self._fixed_width, self._fixed_width


_NAV_KEYS = frozenset((Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Return, Gdk.KEY_KP_Enter))
_t_add = GLib.timeout_add
_ri_range = GLib.random_int_range
_EMPTY = ()
_SIZE_UNITS = ("B", "KB", "MB", "GB", "TB")
_ICON_PX = 16


def _fmt_size(n):
    v = float(n)
    for unit in _SIZE_UNITS:
        if v < 1024.0 or unit == _SIZE_UNITS[-1]:
            return f"{v:.0f} {unit}" if unit == "B" else f"{v:.1f} {unit}"
        v /= 1024.0


def _fmt_date(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _make_row_icon(is_dir):
    if is_dir:
        return Label(markup=icons.folder)

    img = Gtk.Image.new_from_icon_name(
        "text-x-generic-symbolic", Gtk.IconSize.MENU
    )
    img.set_pixel_size(_ICON_PX)
    return img


class _FolderRow(Gtk.ListBoxRow):
    def __init__(self, name, full_path, is_dir, size, mtime, kind):
        super().__init__()
        self.set_name("dir-row")
        self.set_can_focus(False)
        self.entry_name = name
        self.full_path = full_path
        self.is_dir = is_dir

        ctx = self.get_style_context()
        ctx.add_class("dir-row-dir" if is_dir else "dir-row-file")

        icon = _make_row_icon(is_dir)
        icon.set_name("dir-row-icon")

        name_lbl = Label(name="dir-row-name", label=name, h_align="start", h_expand=True)
        name_lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        name_lbl.set_hexpand(True)
        name_lbl.set_halign(Gtk.Align.FILL)

        size_lbl = Label(
            name="dir-row-size",
            label=("" if is_dir else _fmt_size(size)),
            h_align="end",
        )
        size_lbl.set_width_chars(8)
        size_lbl.set_xalign(1.0)

        type_lbl = Label(name="dir-row-type", label=kind, h_align="end")
        type_lbl.set_width_chars(8)
        type_lbl.set_xalign(1.0)

        date_lbl = Label(
            name="dir-row-date",
            label=_fmt_date(mtime),
            h_align="end",
        )
        date_lbl.set_width_chars(13)
        date_lbl.set_xalign(1.0)

        row_box = Box(name="dir-row-box", spacing=8, orientation="h")
        row_box.pack_start(icon, False, False, 0)
        row_box.pack_start(name_lbl, True, True, 0)
        row_box.pack_start(size_lbl, False, False, 0)
        row_box.pack_start(type_lbl, False, False, 0)
        row_box.pack_start(date_lbl, False, False, 0)
        self.add(row_box)

        if not is_dir:
            self.set_selectable(False)
            self.set_activatable(False)


class FolderBrowser(Box):

    def __init__(self, on_folder_changed=None):
        super().__init__(orientation="v", spacing=0)
        self._cur = None
        self._on_folder_changed = on_folder_changed

        self._list = Gtk.ListBox()
        self._list.set_can_focus(False)
        self._list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._list.set_activate_on_single_click(False)
        self._list.connect("row-activated", self._on_row_activated)
        self._list.set_name("dir-listbox")

        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_name("dir-scroll")
        self._scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroll.set_shadow_type(Gtk.ShadowType.NONE)
        self._scroll.set_hexpand(True)
        self._scroll.set_vexpand(True)
        self._scroll.set_size_request(350, 140)
        self._scroll.add(self._list)

        self._empty_lbl = Label(
            name="dir-empty-label", label="Empty folder",
            h_align="center", v_align="center",
        )
        self._empty_lbl.set_no_show_all(True)

        overlay = Gtk.Overlay()
        overlay.add(self._scroll)
        overlay.add_overlay(self._empty_lbl)
        overlay.set_overlay_pass_through(self._empty_lbl, True)

        self.pack_start(overlay, True, True, 0)
        self.show_all()
        self._empty_lbl.hide()

    def set_current_folder(self, path):
        if not path:
            return
        real = os.path.realpath(path.rstrip("/"))
        if not os.path.isdir(real):
            return
        self._cur = real
        self._reload()
        if self._on_folder_changed:
            self._on_folder_changed(real)

    def get_current_folder(self):
        return self._cur

    def get_filename(self):
        row = self._list.get_selected_row()
        if isinstance(row, _FolderRow) and row.is_dir:
            return row.full_path
        return self._cur

    def _clear_rows(self):
        for child in list(self._list.get_children()):
            self._list.remove(child)

    def _reload(self):
        self._clear_rows()
        cur = self._cur
        if not cur:
            return
        entries = list(os.scandir(cur))

        dirs, dot_dirs, files = [], [], []
        for e in entries:
            name = e.name
            is_dir = e.is_dir(follow_symlinks=True)
            st = e.stat(follow_symlinks=True)
            mtime, size = st.st_mtime, st.st_size
            if is_dir:
                entry = (name, e.path, True, size, mtime, "Folder")
                (dot_dirs if name.startswith(".") else dirs).append(entry)
            else:
                ext = os.path.splitext(name)[1].lstrip(".").upper()
                files.append((name, e.path, False, size, mtime,
                              ext if ext else "File"))

        dirs.sort(key=lambda t: t[0].casefold())
        dot_dirs.sort(key=lambda t: t[0].casefold())
        files.sort(key=lambda t: t[0].casefold())

        ordered = dirs + dot_dirs + files
        if not ordered:
            self._empty_lbl.set_label("Empty folder")
            self._empty_lbl.show()
            return
        self._empty_lbl.hide()

        for name, path, is_dir, size, mtime, kind in ordered:
            row = _FolderRow(name, path, is_dir, size, mtime, kind)
            self._list.add(row)
        self._list.show_all()

    def _on_row_activated(self, _lb, row):
        if not isinstance(row, _FolderRow) or not row.is_dir:
            return
        self.set_current_folder(row.full_path)


class WallpaperCarousel(Gtk.DrawingArea):
    def __init__(self, on_select=None, on_navigate=None):
        super().__init__()
        self._files = self._flt = _EMPTY
        self._th = {}
        self._ld = {}
        self._gen = 0
        self._idx = 0
        self._off = 0.0
        self._anim = self._dead = self._ldq = False
        self._spl = self._spt = self._spd = 0
        self._spcb = None
        self._clr = (1.0, 1.0, 1.0, 1.0)
        self._ph = None
        self._walls_dir = None
        self._thumb_ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wall-thumb")
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
        self.set_size_request(800, 260)

    def set_walls_dir(self, path):
        self._walls_dir = path

    def _on_realize(self, *_a):
        self._mkph()
        self._uclr()
        self._sched()

    def _on_style_updated(self, *_a):
        self._uclr()

    def _mkph(self):
        s = cairo.ImageSurface(cairo.FORMAT_ARGB32, _SZ, _SZ)
        c = cairo.Context(s)
        c.set_source_rgba(0.27, 0.27, 0.27, 0.5)
        _rpath(c, 0, 0, _SZ, _SZ, _CR)
        c.fill()
        self._ph = s

    def _uclr(self):
        ctx = self.get_style_context()
        found, rgba = ctx.lookup_color("primary")
        if found:
            self._clr = (rgba.red, rgba.green, rgba.blue, rgba.alpha)
            return
        c = ctx.get_color(Gtk.StateFlags.NORMAL)
        self._clr = (c.red, c.green, c.blue, c.alpha)

    def _rst(self, f, target_nm=None):
        self._gen += 1
        self._flt = f
        if not f:
            self._idx = -1
        elif target_nm and target_nm in f:
            self._idx = f.index(target_nm)
        else:
            self._idx = 0
        self._off = 0.0
        for surf in self._th.values():
            surf.finish()
        self._th.clear()
        for fut in self._ld.values():
            fut.cancel()
        self._ld.clear()
        if self.get_realized() and f:
            self._sched()
        self.queue_draw()

    def set_files(self, f, target_nm=None):
        self._files = f
        self._rst(f, target_nm)

    def filter_files(self, q):
        cur = self.cur()
        if not q:
            self._rst(self._files, target_nm=cur)
            return
        ql = q.casefold()
        matched = tuple(f for f in self._files if ql in f.casefold())
        self._rst(matched, target_nm=cur)

    def _sched(self):
        if self._ldq or self._dead:
            return
        self._ldq = True
        GLib.idle_add(self._load)

    def _load(self):
        self._ldq = False
        flt = self._flt
        if self._dead or not flt:
            return False
        n, cur, th = len(flt), self._idx, self._th
        gen = self._gen
        ld = self._ld
        submit = self._thumb_ex.submit
        needed = set()
        for i in _LOAD_RNG:
            nm = flt[(cur + i) % n]
            needed.add(nm)
            if nm not in th and nm not in ld:
                ld[nm] = submit(self._ldth, nm, gen)
        for k in [k for k in th if k not in needed]:
            th.pop(k).finish()
        for k in [k for k in ld if k not in needed]:
            ld.pop(k).cancel()
        return False

    def _ldth(self, nm, gen):
        if self._dead or gen != self._gen:
            return
        walls_dir = self._walls_dir
        cp = _THUMBS + _md5hex(nm) + _SUFFIX
        if os.path.exists(cp) and os.path.getsize(cp) > 0:
            surf = cairo.ImageSurface.create_from_png(cp)
        else:
            full_path = walls_dir + nm
            raw = GdkPixbuf.Pixbuf.new_from_file_at_scale(full_path, _SZ, _SZ, True)
            w, h = raw.get_width(), raw.get_height()
            if w == _SZ and h == _SZ:
                sq = raw
            elif w < _SZ or h < _SZ:
                sq = raw.scale_simple(_SZ, _SZ, GdkPixbuf.InterpType.BILINEAR)
            else:
                sq = raw.new_subpixbuf((w - _SZ) >> 1, (h - _SZ) >> 1, _SZ, _SZ)

            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, _SZ, _SZ)
            ct = cairo.Context(surf)
            _rpath(ct, 0, 0, _SZ, _SZ, _CR)
            ct.clip()
            Gdk.cairo_set_source_pixbuf(ct, sq, 0, 0)
            ct.paint()
            surf.write_to_png(cp)

        if self._dead or gen != self._gen:
            surf.finish()
            return
        GLib.idle_add(self._onth, nm, surf, gen)

    def _onth(self, nm, surf, gen):
        self._ld.pop(nm, None)
        if self._dead or gen != self._gen:
            surf.finish()
            return False
        flt = self._flt
        n = len(flt)
        if not n:
            surf.finish()
            return False
        cur = self._idx
        for i in _LOAD_RNG:
            if flt[(cur + i) % n] == nm:
                old = self._th.get(nm)
                if old is not None and old is not surf:
                    old.finish()
                self._th[nm] = surf
                if not self._anim:
                    self.queue_draw()
                return False
        surf.finish()
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
            cr.set_font_size(15)
            t = "No wallpapers found"
            e = cr.text_extents(t)
            cr.move_to(
                (alloc.width - e.width) * 0.5,
                (alloc.height + e.height) * 0.5,
            )
            cr.show_text(t)
            return
        cx = alloc.width * 0.5
        cy = alloc.height * 0.5 + 8.0
        off = self._off
        idx = self._idx
        th_get = self._th.get
        ph = self._ph
        fc = self._fast_card

        if -_EPS < off < _EPS:
            for i, sc, al, yof, sel in _STATIC:
                s = th_get(flt[(idx + i) % n], ph)
                fc(cr, s, cx + i * _SPC, cy + yof, sc, al, sel)
            return

        for i in _DRAW_ORDER:
            p = i + off
            d = p if p >= 0.0 else -p
            if d > 4.2:
                continue
            al = 1.0 - d * 0.25
            if al <= 0.05:
                continue
            sc = 1.0 - d * 0.15
            if sc < 0.4:
                sc = 0.4
            fc(
                cr,
                th_get(flt[(idx + i) % n], ph),
                cx + p * _SPC,
                cy + p * p * _ARC_K,
                sc, al,
                d < 0.15,
            )

    def _fast_card(self, cr, surf, x, y, sc, al, sel):
        if not surf:
            return
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
            self.nav(-1, glitch=True)
        elif k == Gdk.KEY_Right:
            self.nav(1, glitch=True)
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
            self.nav(-1 if rx < 0.0 else 1, glitch=True)
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
            adx = dx if dx >= 0.0 else -dx
            ady = dy if dy >= 0.0 else -dy
            if adx > ady:
                if adx > 0.5:
                    d = 1 if dx > 0.0 else -1
            elif ady > 0.5:
                d = 1 if dy > 0.0 else -1
        if d and not self._anim:
            self.nav(d, glitch=True)
        return True

    def nav(self, dr, anim=True, glitch=False):
        if not self._flt or (self._anim and anim and not self._spl):
            return
        self._idx = (self._idx + dr) % len(self._flt)
        if anim:
            self._off = float(dr)
            self._anim = True
            GLib.timeout_add(_FRAME_MS, self._slide)
        else:
            self._off = 0.0
            self.queue_draw()
        self._sched()
        if self._on_nav:
            self._on_nav(dr, glitch)

    def _slide(self):
        if self._dead:
            return False
        off = self._off * _DECAY
        if -_EPS < off < _EPS:
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
        if n <= 1:
            if cb:
                cb()
            return
        fwd = (tgt - self._idx) % n
        bwd = (self._idx - tgt) % n
        d = 1 if fwd <= bwd else -1
        dist = fwd if d == 1 else bwd
        total = dist + GLib.random_int_range(1, 3) * n
        if total > _SPIN_STEP_CAP:
            skip = total - _SPIN_STEP_CAP
            self._idx = (self._idx + d * skip) % n
            total = _SPIN_STEP_CAP
        self._spl = total
        self._spt = total
        self._spd = d
        self._spcb = cb
        self._anim = True
        self._sched()
        self._spst()

    def _spst(self):
        if self._dead:
            return False
        if self._spl <= 0:
            self._anim = False
            self._off = 0.0
            self._sched()
            self.queue_draw()
            cb = self._spcb
            self._spcb = None
            if cb:
                cb()
            return False
        self.nav(self._spd, anim=False)
        self._spl -= 1
        p = 1.0 - self._spl / self._spt
        interval = 22 if p < 0.6 else 22 + int(((p - 0.6) * 2.5) ** 2 * 200)
        GLib.timeout_add(interval, self._spst)
        return False

    def _sel(self):
        nm = self.cur()
        if nm and self._on_sel:
            self._on_sel(nm)

    def cur(self):
        flt = self._flt
        idx = self._idx
        return flt[idx] if flt and 0 <= idx < len(flt) else None

    def cleanup(self):
        self._dead = True
        self._thumb_ex.shutdown(wait=False, cancel_futures=True)
        for surf in self._th.values():
            surf.finish()
        self._th.clear()
        self._ld.clear()
        self._files = self._flt = _EMPTY
        if self._ph is not None:
            self._ph.finish()
            self._ph = None


class WallpaperSelector(Box):

    _MIN_APPLY_INTERVAL = 0.15

    def __init__(self, **kw):
        super().__init__(
            name="wallpapers", spacing=0, orientation="v",
            v_expand=True, v_align="fill", **kw,
        )
        self._dead = False
        self._pend = {}
        self._files = ()
        self._mon = None
        self._walls = _INITIAL_WALLS_DIR
        self._last_apply = 0.0

        self._gl_rem = [0, 0]
        self._gl_tid = [None, None]
        self._gl_active = [_NO_CLASSES, _NO_CLASSES]
        self._cf_tid = None
        self._cf_frame = 0

        self._io_ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wall-io")
        self._color_ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wall-color")

        os.makedirs(_THUMBS, exist_ok=True)
        if self._walls:
            os.makedirs(self._walls, exist_ok=True)

        self._car = WallpaperCarousel(
            on_select=self._on_sel, on_navigate=self._on_nav,
        )
        self._car.set_walls_dir(self._walls)
        ew = Gtk.EventBox()
        ew.add(self._car)
        ew.connect("button-press-event", self._on_car_click)

        lbl_l = Label(name="carousel-arrow-label")
        lbl_l.set_halign(Gtk.Align.START)
        lbl_l.set_valign(Gtk.Align.CENTER)
        lbl_l.set_can_focus(False)

        lbl_r = Label(name="carousel-arrow-label")
        lbl_r.set_halign(Gtk.Align.END)
        lbl_r.set_valign(Gtk.Align.CENTER)
        lbl_r.set_can_focus(False)

        self._arr_lbls = (lbl_l, lbl_r)

        lbl_l.connect("style-updated", lambda *_: self._arr_render(0))
        lbl_r.connect("style-updated", lambda *_: self._arr_render(1))

        car_ov = Gtk.Overlay()
        car_ov.add(ew)
        car_ov.add_overlay(lbl_l)
        car_ov.add_overlay(lbl_r)
        car_ov.set_overlay_pass_through(lbl_l, True)
        car_ov.set_overlay_pass_through(lbl_r, True)

        car_box = Box(
            name="carousel-container", orientation="h",
            h_align="center", v_align="center", v_expand=True,
        )
        car_box.pack_start(car_ov, True, True, 0)

        self._ent = Entry(
            name="search-entry-walls", placeholder="Search Wallpapers...",
            h_expand=True, h_align="fill",
        )
        self._ent.connect("notify::text", self._on_search_changed)
        self._ent.connect("key-press-event", self._ekey)

        self._sch_idx = 0
        cur = self._ldsch()
        for i, (k, _) in enumerate(_SCH):
            if k == cur:
                self._sch_idx = i
                break

        self._sch_btn = Button(
            name="scheme-dropdown-btn", label=_SCH[self._sch_idx][1],
            tooltip_text="Click to select scheme, or scroll",
        )
        self._sch_btn.connect("clicked", self._on_sch_btn_clicked)
        self._sch_btn.add_events(Gdk.EventMask.SCROLL_MASK)
        self._sch_btn.connect("scroll-event", self._on_sch_scroll)

        self._sch_rev = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
        )
        self._sch_rev.set_transition_duration(200)
        self._sch_rev.set_halign(Gtk.Align.END)
        self._sch_rev.set_valign(Gtk.Align.START)
        self._sch_rev.set_reveal_child(False)

        list_box = Box(orientation="v", name="scheme-list-container")
        self._sch_items = []
        for i, (_, v) in enumerate(_SCH):
            btn = Button(label=v, name="scheme-list-item")
            btn.get_child().set_halign(Gtk.Align.START)
            btn._si = i
            btn.connect("clicked", self._on_sch_item_click)
            list_box.add(btn)
            self._sch_items.append(btn)

        list_box.show_all()
        self._sch_rev.add(list_box)

        self._rb = Button(
            name="random-wall-button",
            child=Label(name="random-wall-label", markup=_DICE[0]),
            tooltip_text="Random Wallpaper",
        )
        self._rb.connect("clicked", self.random_wall)

        header = Box(
            name="wallpapers-header", spacing=8, orientation="h",
            children=[self._rb, self._ent, self._sch_btn],
        )

        self._lbl = Label(
            name="wallpaper-name-label", label="Select a wallpaper",
            h_align="start", h_expand=True,
        )
        self._lbl.set_hexpand(True)
        self._lbl.set_halign(Gtk.Align.FILL)

        dir_icon = Label(name="wallpaper-dir-icon", markup=icons.folder)

        self._dir_path_lbl = Label(
            name="wallpaper-dir-path",
            label=_short_path(self._walls) if self._walls else _PICK_PROMPT,
            h_align="start", h_expand=True,
        )
        self._dir_path_lbl.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self._dir_path_lbl.set_hexpand(True)
        self._dir_path_lbl.set_halign(Gtk.Align.FILL)

        dir_inner = Box(spacing=6, orientation="h")
        dir_inner.pack_start(dir_icon, False, False, 0)
        dir_inner.pack_start(self._dir_path_lbl, True, True, 0)
        dir_inner.set_hexpand(True)
        dir_inner.set_halign(Gtk.Align.FILL)

        self._dir_btn = Button(
            name="wallpaper-dir-button", child=dir_inner,
            tooltip_text=self._walls or _PICK_PROMPT,
        )
        self._dir_btn.connect("clicked", self._toggle_dir_chooser)

        label_row = Box(spacing=8, orientation="h", h_expand=True, h_align="fill")
        label_row.set_homogeneous(True)
        label_row.set_hexpand(True)
        label_row.pack_start(self._lbl, True, True, 0)
        label_row.pack_start(self._dir_btn, True, True, 0)

        self._path_scroll = Gtk.ScrolledWindow()
        self._path_scroll.set_name("custom-path-bar-scroll")
        self._path_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self._path_scroll.set_overlay_scrolling(True)
        self._path_scroll.set_shadow_type(Gtk.ShadowType.NONE)
        self._path_scroll.set_size_request(-1, -1)
        self._path_scroll.set_hexpand(True)
        self._path_scroll.set_vexpand(False)
        self._path_scroll.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        self._path_scroll.connect("scroll-event", self._on_path_scroll)

        self._path_box = Box(name="custom-path-bar-box", orientation="h", spacing=2)
        self._path_scroll.add(self._path_box)

        self._dir_chooser = FolderBrowser(on_folder_changed=self._on_chooser_folder_changed)
        self._dir_chooser.set_hexpand(True)
        self._dir_chooser.set_vexpand(True)

        init_dir = _safe_folder(self._walls)
        self._dir_chooser.set_current_folder(init_dir)

        picker_icon = Label(name="dir-chooser-header-icon", markup=icons.folder)
        picker_title = Label(
            name="dir-chooser-header-title", label="Choose Wallpapers Folder",
            h_align="start", h_expand=True,
        )
        picker_header = Box(name="dir-chooser-header", spacing=6, orientation="h")
        picker_header.pack_start(picker_icon, False, False, 0)
        picker_header.pack_start(picker_title, True, True, 0)

        cancel_btn = Button(name="dir-chooser-cancel", label="Cancel")
        cancel_btn.connect("clicked", lambda *_: self._close_dir_chooser())

        apply_btn = Button(name="dir-chooser-apply", label="Select")
        apply_btn.connect("clicked", self._on_dir_apply)

        btn_row = Box(
            name="dir-chooser-btnrow", spacing=8,
            orientation="h", h_align="end",
        )
        btn_row.pack_start(cancel_btn, False, False, 0)
        btn_row.pack_start(apply_btn, False, False, 0)

        dialog_card = FixedBox(
            fixed_width=380, name="dir-dialog-card",
            orientation="v", spacing=0,
        )
        dialog_card.pack_start(picker_header, False, False, 0)
        dialog_card.pack_start(self._path_scroll, False, False, 0)
        dialog_card.pack_start(self._dir_chooser, True, True, 0)
        dialog_card.pack_start(btn_row, False, False, 0)

        self._dir_revealer = Gtk.Revealer()
        self._dir_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self._dir_revealer.set_transition_duration(220)
        self._dir_revealer.set_halign(Gtk.Align.END)
        self._dir_revealer.set_valign(Gtk.Align.END)
        self._dir_revealer.set_reveal_child(False)
        self._dir_revealer.set_hexpand(False)
        self._dir_revealer.add(dialog_card)

        mid_overlay = Gtk.Overlay()
        mid_overlay.add(car_box)
        mid_overlay.add_overlay(self._sch_rev)
        mid_overlay.add_overlay(self._dir_revealer)

        self.pack_start(header, False, False, 0)
        self.pack_start(mid_overlay, True, True, 0)
        self.pack_start(label_row, False, False, 0)

        self.connect("destroy", self._destroy)
        self.connect("realize", self._on_realize_full)
        self._scan()
        self._watch()

    def _on_path_scroll(self, widget, event):
        adj = widget.get_hadjustment()
        if not adj:
            return False
        step = 35.0
        cur = adj.get_value()
        low = adj.get_lower()
        upp = max(low, adj.get_upper() - adj.get_page_size())
        if event.direction == Gdk.ScrollDirection.UP:
            adj.set_value(max(low, cur - step))
            return True
        if event.direction == Gdk.ScrollDirection.DOWN:
            adj.set_value(min(upp, cur + step))
            return True
        if event.direction == Gdk.ScrollDirection.SMOOTH:
            _, dx, dy = event.get_scroll_deltas()
            d = dy if dy != 0 else dx
            adj.set_value(min(upp, max(low, cur + d * step)))
            return True
        return False

    def _render_custom_path(self, cur_path):
        for child in self._path_box.get_children():
            self._path_box.remove(child)

        cur_norm = os.path.realpath(cur_path).rstrip("/")
        home_norm = os.path.realpath(_HOME).rstrip("/")

        segments = [("~", home_norm)]
        if cur_norm != home_norm and cur_norm.startswith(home_norm + "/"):
            rel = cur_norm[len(home_norm) + 1:]
            parts = rel.split("/")
            acc = home_norm
            for part in parts:
                acc = os.path.join(acc, part)
                segments.append((part, acc))

        n = len(segments)
        for i, (name, target) in enumerate(segments):
            btn = Button(name="custom-path-btn", label=name)
            if i == n - 1:
                btn.get_style_context().add_class("current")

            clean_target = os.path.realpath(target.rstrip("/"))
            btn.connect("clicked", lambda _, p=clean_target: self._dir_chooser.set_current_folder(p))
            self._path_box.pack_start(btn, False, False, 0)

            if i < n - 1:
                sep = Label(name="custom-path-sep", label="/")
                self._path_box.pack_start(sep, False, False, 0)

        self._path_box.show_all()

        def _scroll_end():
            adj = self._path_scroll.get_hadjustment()
            if adj:
                adj.set_value(max(adj.get_lower(), adj.get_upper() - adj.get_page_size()))
            return False

        GLib.idle_add(_scroll_end)

    def _arr_render(self, idx):
        _arr_set_art(self._arr_lbls[idx], _ARR_LINES[idx])

    def _on_realize_full(self, *_):
        self._arr_render(0)
        self._arr_render(1)

    def _start_glitch(self, idx):
        old = self._gl_tid[idx]
        if old is not None:
            _remove_source(old)
            self._gl_tid[idx] = None
        self._gl_rem[idx] = _GL_FRAMES
        self._gl_tid[idx] = GLib.timeout_add(_GL_FRAME_MS, self._gl_tick, idx)

    def _gl_tick(self, idx):
        ctx = self._arr_lbls[idx].get_style_context()
        rem = self._gl_rem[idx]
        self._gl_active[idx] = _apply_glitch(ctx, rem / _GL_FRAMES, self._gl_active[idx])
        rem -= 1
        if rem <= 0:
            self._gl_tid[idx] = None
            for cls in self._gl_active[idx]:
                ctx.remove_class(cls)
            self._gl_active[idx] = _NO_CLASSES
            return False
        self._gl_rem[idx] = rem
        return True

    def _start_confirm(self):
        for i in (0, 1):
            tid = self._gl_tid[i]
            if tid is not None:
                _remove_source(tid)
                self._gl_tid[i] = None
        if self._cf_tid is not None:
            _remove_source(self._cf_tid)
        self._cf_frame = 0
        self._cf_tid = GLib.timeout_add(_GL_FRAME_MS, self._cf_tick)

    def _cf_tick(self):
        frame_in_burst = self._cf_frame % _GL_FRAMES
        progress = 1.0 - frame_in_burst / _GL_FRAMES

        active = self._gl_active
        for i, lbl in enumerate(self._arr_lbls):
            active[i] = _apply_glitch(lbl.get_style_context(), progress, active[i])

        self._cf_frame += 1
        if self._cf_frame >= _GL_FRAMES * _CF_BURSTS:
            self._cf_tid = None
            for i, lbl in enumerate(self._arr_lbls):
                ctx = lbl.get_style_context()
                for cls in active[i]:
                    ctx.remove_class(cls)
                active[i] = _NO_CLASSES
            return False
        return True

    def _on_car_click(self, *_):
        self._car.grab_focus()
        if self._sch_rev.get_reveal_child():
            self._sch_rev.set_reveal_child(False)
            self._sch_btn.get_style_context().remove_class("open")
        if self._dir_revealer.get_reveal_child():
            self._close_dir_chooser()

    def _on_nav(self, direction, glitch):
        self._ulbl()
        if glitch:
            self._start_glitch(0 if direction < 0 else 1)

    def _on_sel(self, nm):
        self._apply(nm)
        self._ulbl()
        self._start_confirm()

    def _on_sch_item_click(self, btn):
        self._on_list_item_clicked(btn._si)

    def _on_sch_btn_clicked(self, *_):
        if self._dir_revealer.get_reveal_child():
            self._close_dir_chooser()
        revealed = not self._sch_rev.get_reveal_child()
        self._sch_rev.set_reveal_child(revealed)
        ctx = self._sch_btn.get_style_context()
        if revealed:
            ctx.add_class("open")
        else:
            ctx.remove_class("open")

    def _on_list_item_clicked(self, idx):
        self._sch_rev.set_reveal_child(False)
        self._sch_btn.get_style_context().remove_class("open")
        if self._sch_idx != idx:
            self._set_scheme(idx)

    def _on_sch_scroll(self, _, e):
        d = e.direction
        if d == Gdk.ScrollDirection.UP:
            self._set_scheme(self._sch_idx - 1)
        elif d == Gdk.ScrollDirection.DOWN:
            self._set_scheme(self._sch_idx + 1)
        return True

    def _set_scheme(self, idx):
        self._sch_idx = idx % _SCH_LEN
        sch_id, sch_name = _SCH[self._sch_idx]
        self._sch_btn.set_label(sch_name)

        with open(_SCHEME_F, "w") as f:
            f.write(sch_id)

        nm = self._car.cur()
        p = (self._walls + nm) if (nm and self._walls) else os.path.realpath(_CURRENT)
        if os.path.exists(p):
            self._color_ex.submit(self._gen_colors, p, sch_id)

    @staticmethod
    def _ldsch():
        if not os.path.exists(_SCHEME_F):
            return _SCH[0][0]
        with open(_SCHEME_F) as f:
            val = f.read().strip()
        return val if val else _SCH[0][0]

    def _on_chooser_folder_changed(self, path):
        if not path:
            return
        cur_norm = os.path.realpath(path.rstrip("/"))
        home_norm = os.path.realpath(_HOME.rstrip("/"))

        if cur_norm != home_norm and not cur_norm.startswith(home_norm + "/"):
            safe_dir = _safe_folder(self._walls)
            GLib.idle_add(self._dir_chooser.set_current_folder, safe_dir)
            return

        self._render_custom_path(cur_norm)

    def _toggle_dir_chooser(self, *_):
        if self._sch_rev.get_reveal_child():
            self._sch_rev.set_reveal_child(False)
            self._sch_btn.get_style_context().remove_class("open")

        if self._dir_revealer.get_reveal_child():
            self._close_dir_chooser()
        else:
            target_dir = _safe_folder(self._walls)
            self._dir_chooser.set_current_folder(target_dir)
            self._dir_btn.get_style_context().add_class("open")
            self._dir_revealer.set_reveal_child(True)

    def _close_dir_chooser(self):
        self._dir_btn.get_style_context().remove_class("open")
        self._dir_revealer.set_reveal_child(False)

    def _on_dir_apply(self, *_):
        folder = self._dir_chooser.get_filename()
        self._close_dir_chooser()
        if not folder:
            return
        self._set_walls_dir(folder)

    def _set_walls_dir(self, path):
        path = path if path.endswith("/") else path + "/"
        if path == self._walls:
            return
        self._walls = path
        self._car.set_walls_dir(path)

        with open(_WALLDIR_F, "w") as f:
            f.write(path)

        self._dir_btn.set_tooltip_text(path)
        self._dir_path_lbl.set_label(_short_path(path))
        if self._mon:
            self._mon.cancel()
            self._mon = None
        self._watch()
        self._scan()

    def _on_dir_changed(self, *_):
        if not self._dead:
            self._deb("r", 1000, self._scan)

    def _on_search_changed(self, *_):
        self._deb("s", 300, self._do_search)

    def _do_search(self):
        if not self._dead:
            self._car.filter_files(self._ent.get_text())
            self._ulbl()

    def _ekey(self, _, e):
        k = e.keyval
        if k == Gdk.KEY_Escape:
            if self._sch_rev.get_reveal_child():
                self._sch_rev.set_reveal_child(False)
                self._sch_btn.get_style_context().remove_class("open")
                return True
            if self._dir_revealer.get_reveal_child():
                self._close_dir_chooser()
                return True
            self._ent.set_text("")
            return True
        if k in _NAV_KEYS:
            return self._car._key(self._car, e)
        return False

    def _scan(self):
        if not self._walls or not os.path.isdir(self._walls):
            if self._files:
                self._files = ()
                self._car.set_files(())
                self._ulbl()
            return
        nf = tuple(sorted(
            e.name
            for e in os.scandir(self._walls)
            if e.is_file(follow_symlinks=False)
            and os.path.splitext(e.name)[1].lower() in _EXT
        ))

        if nf == self._files:
            return

        self._files = nf

        target_wall = self._car.cur()
        if not target_wall and os.path.exists(_CURRENT):
            target_wall = os.path.basename(os.path.realpath(_CURRENT))

        self._car.set_files(nf, target_nm=target_wall)
        self._ulbl()
        if nf and not self._dead:
            self._io_ex.submit(self._clean_thumbs, nf)

    @staticmethod
    def _clean_thumbs(files):
        valid = frozenset(_md5hex(nm) for nm in files)
        entries = list(os.scandir(_THUMBS))
        for entry in entries:
            name = entry.name
            if name.endswith(_SUFFIX) and name[:-_SFXL] in valid:
                continue
            if entry.is_file(follow_symlinks=False):
                os.remove(entry.path)

    def _watch(self):
        if not self._walls or not os.path.isdir(self._walls):
            return
        mon = Gio.File.new_for_path(self._walls).monitor_directory(
            Gio.FileMonitorFlags.NONE, None,
        )
        mon.connect("changed", self._on_dir_changed)
        self._mon = mon

    def _apply(self, nm, notify=False):
        if not self._walls:
            return

        now = time.monotonic()
        if now - self._last_apply < self._MIN_APPLY_INTERVAL:
            return
        self._last_apply = now

        p = self._walls + nm
        if not os.path.exists(p):
            return

        sch_id = _SCH[self._sch_idx][0]

        tmp_link = f"{_CURRENT}.{os.getpid()}.tmp"
        if os.path.lexists(tmp_link):
            os.remove(tmp_link)
        os.symlink(p, tmp_link)
        os.replace(tmp_link, _CURRENT)

        exec_shell_command_async(
            f'awww img "{p}" -t fade'
            f' --transition-duration 0.5'
            f' --transition-step 255'
            f' --transition-fps 60'
        )
        self._color_ex.submit(self._gen_colors, p, sch_id)
        if notify:
            GLib.spawn_command_line_async(f"notify-send 'Wallpaper' 'Random wallpaper set' -a 'Vidgex-Shell' -i '{p}' -e")

    def _gen_colors(self, image_path, scheme_id):
        if self._dead or not image_path or not os.path.exists(image_path):
            return
        apply_colors(image_path, scheme_id, _CSS_OUT, _HYPR_OUT)
        GLib.idle_add(self._reload_css)

    @staticmethod
    def _reload_css():
        exec_shell_command_async(
            "fabric-cli exec vidgex-shell 'app.set_css()'"
        )
        return False

    def _roll(self):
        self._rb.get_child().set_markup(_DICE[_ri_range(0, 6)])

    def random_wall(self, _=None, ext=False):
        files = self._files
        if not files:
            return
        i = _ri_range(0, len(files))
        if self._ent.get_text():
            self._ent.set_text("")
        nm = files[i]
        self._car.spin(i, lambda: self._on_spin_done(nm, ext))

    def _on_spin_done(self, nm, ext):
        self._apply(nm, notify=ext)
        self._ulbl()
        self._start_confirm()
        self._roll()

    def _ulbl(self, *_):
        nm = self._car.cur()
        if nm:
            dot = nm.rfind(".")
            if dot >= 0:
                nm = nm[:dot]
            if len(nm) > 42:
                nm = nm[:39] + "..."
            self._lbl.set_label(nm)
        else:
            self._lbl.set_label(
                _PICK_PROMPT if not self._walls else "No wallpapers available"
            )
        return False

    def _deb(self, k, ms, func):
        old = self._pend.get(k)
        if old is not None:
            _remove_source(old)
        self._pend[k] = _t_add(ms, self._deb_run, k, func)

    def _deb_run(self, k, func):
        self._pend.pop(k, None)
        func()
        return False

    def _destroy(self, _):
        self._dead = True
        if self._mon:
            self._mon.cancel()
            self._mon = None
        for tid in (self._gl_tid[0], self._gl_tid[1], self._cf_tid):
            _remove_source(tid)
        self._gl_tid = [None, None]
        self._cf_tid = None
        for sid in self._pend.values():
            _remove_source(sid)
        self._pend.clear()
        self._car.cleanup()
        self._io_ex.shutdown(wait=False, cancel_futures=True)
        self._color_ex.shutdown(wait=False, cancel_futures=True)
        self._files = ()