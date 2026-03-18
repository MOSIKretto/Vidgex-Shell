import os
import random

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.entry import Entry
from fabric.widgets.label import Label
from fabric.utils.helpers import exec_shell_command_async
from gi.repository import Gdk, Gio, GLib, Gtk

from modules.Notch.MainWindow.Wallpaper.wallpaperConstants import (
    _ARR_LINES, _CSS_OUT, _CURRENT, _DICE, _EXT,
    _GL_FRAME_MS, _GL_FRAMES, _GL_RAND_MAX, _GL_RAND_MIN,
    _GL_REPEAT_CHANCE, _GLITCH_CLASSES,
    _HYPR_OUT, _SCH, _SCH_K, _SCH_LEN,
    _SCHEME_F, _SFXL, _SUFFIX, _THUMBS, _WALLS,
)
from modules.Notch.MainWindow.Wallpaper.wallpaperUtils import (
    _arr_set_art, _md5hex, _setup_pointer_cursor,
)
from modules.Notch.MainWindow.Wallpaper.wallpaperCarousel import WallpaperCarousel
from modules.Notch.MainWindow.Wallpaper.wallpaperColors import apply_colors


_NAV_KEYS = frozenset((Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Return, Gdk.KEY_KP_Enter))
_LR_KEYS = frozenset((Gdk.KEY_Left, Gdk.KEY_Right))

_src_rm = GLib.source_remove
_t_add = GLib.timeout_add
_ri_range = GLib.random_int_range


class WallpaperSelector(Box):
    __slots__ = (
        "_dead", "_pend", "_files", "_mon", "_car", "_ent",
        "_sch_btn", "_sch_rev", "_sch_idx", "_rb", "_lbl",
        "_arr_lbls",
        "_gl_active_lbl", "_gl_rem_lbl", "_gl_total_lbl", "_gl_tid_lbl",
        "_gl_active_L", "_gl_rem_L", "_gl_total_L", "_gl_tid_L",
        "_gl_active_R", "_gl_rem_R", "_gl_total_R", "_gl_tid_R",
        "_gl_rand_tid",
    )

    def __init__(self, **kw):
        super().__init__(
            name="wallpapers", spacing=4, orientation="v", **kw,
        )
        self._dead = False
        self._pend = {}

        self._files = ()
        self._mon = None

        self._gl_active_lbl = False
        self._gl_rem_lbl = 0
        self._gl_total_lbl = _GL_FRAMES
        self._gl_tid_lbl = None

        self._gl_active_L = False
        self._gl_rem_L = 0
        self._gl_total_L = _GL_FRAMES
        self._gl_tid_L = None

        self._gl_active_R = False
        self._gl_rem_R = 0
        self._gl_total_R = _GL_FRAMES
        self._gl_tid_R = None

        self._gl_rand_tid = None

        os.makedirs(_WALLS, exist_ok=True)
        os.makedirs(_THUMBS, exist_ok=True)

        self._car = WallpaperCarousel(
            on_select=self._on_sel, on_navigate=self._on_nav,
        )
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

        lbl_l.connect("style-updated", self._on_style_l)
        lbl_r.connect("style-updated", self._on_style_r)

        car_ov = Gtk.Overlay()
        car_ov.add(ew)
        car_ov.add_overlay(lbl_l)
        car_ov.add_overlay(lbl_r)
        car_ov.set_overlay_pass_through(lbl_l, True)
        car_ov.set_overlay_pass_through(lbl_r, True)

        cb = Box(
            name="carousel-container", orientation="h",
            h_align="center", v_align="center",
        )
        cb.pack_start(car_ov, True, True, 0)

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
        _setup_pointer_cursor(self._sch_btn)

        self._sch_rev = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
        )
        self._sch_rev.set_halign(Gtk.Align.END)
        self._sch_rev.set_valign(Gtk.Align.START)

        list_box = Box(orientation="v", name="scheme-list-container")
        for i, (_, v) in enumerate(_SCH):
            btn = Button(label=v, name="scheme-list-item")
            btn.get_child().set_halign(Gtk.Align.START)
            btn._si = i
            btn.connect("clicked", self._on_sch_item_click)
            _setup_pointer_cursor(btn)
            list_box.add(btn)
        self._sch_rev.add(list_box)

        self._sch_btn.connect("clicked", self._on_sch_btn_clicked)
        self._sch_btn.add_events(Gdk.EventMask.SCROLL_MASK)
        self._sch_btn.connect("scroll-event", self._on_sch_scroll)

        self._rb = Button(
            name="random-wall-button",
            child=Label(name="random-wall-label", markup=_DICE[0]),
            tooltip_text="Random Wallpaper",
        )
        self._rb.connect("clicked", self.random_wall)
        _setup_pointer_cursor(self._rb)

        self._lbl = Label(
            name="wallpaper-name-label", label="Select a wallpaper",
        )

        header = Box(spacing=8, children=[self._rb, self._ent, self._sch_btn])

        overlay = Gtk.Overlay()
        overlay.add(cb)
        overlay.add_overlay(self._sch_rev)

        self.add(header)
        self.pack_start(overlay, True, True, 0)
        self.add(self._lbl)

        self._roll()

        self.connect("destroy", self._destroy)
        self.connect("realize", self._on_realize_full)
        self._scan()
        self._watch()

    def _arr_render(self, idx):
        _arr_set_art(self._arr_lbls[idx], _ARR_LINES[idx])

    def _on_style_l(self, *_):
        self._arr_render(0)

    def _on_style_r(self, *_):
        self._arr_render(1)

    def _on_realize_full(self, *_):
        self._arr_render(0)
        self._arr_render(1)
        if not self._files:
            self._scan()
        self._schedule_random_glitch()

    def _clear_glitch_widget(self, widget):
        ctx = widget.get_style_context()
        for cls in _GLITCH_CLASSES:
            ctx.remove_class(cls)

    def _start_glitch_for(self, which):
        """which: 'lbl', 'L', 'R'"""
        setattr(self, f"_gl_rem_{which}", _GL_FRAMES)
        setattr(self, f"_gl_total_{which}", _GL_FRAMES)
        setattr(self, f"_gl_active_{which}", True)
        old_tid = getattr(self, f"_gl_tid_{which}")
        if old_tid:
            GLib.source_remove(old_tid)
        tid = GLib.timeout_add(
            _GL_FRAME_MS,
            self._gl_tick_for, which,
        )
        setattr(self, f"_gl_tid_{which}", tid)

    def _gl_tick_for(self, which):
        rem = getattr(self, f"_gl_rem_{which}")
        total = getattr(self, f"_gl_total_{which}")
        progress = 1.0 - rem / total

        if which == "lbl":
            widget = self._lbl
        elif which == "L":
            widget = self._arr_lbls[0]
        else:
            widget = self._arr_lbls[1]

        self._clear_glitch_widget(widget)

        if random.random() > progress:
            count = 1 if random.random() > 0.4 else 2
            chosen = random.sample(_GLITCH_CLASSES, count)
            for cls in chosen:
                widget.get_style_context().add_class(cls)

        rem -= 1
        setattr(self, f"_gl_rem_{which}", rem)

        if rem <= 0:
            setattr(self, f"_gl_active_{which}", False)
            setattr(self, f"_gl_tid_{which}", None)
            self._clear_glitch_widget(widget)
            if random.random() < _GL_REPEAT_CHANCE:
                self._start_glitch_for(which)
            return False

        return True

    def _schedule_random_glitch(self):
        delay = random.randint(_GL_RAND_MIN, _GL_RAND_MAX)
        self._gl_rand_tid = GLib.timeout_add_seconds(
            delay, self._fire_random_glitch,
        )

    def _fire_random_glitch(self):
        self._gl_rand_tid = None
        if not self._gl_active_lbl:
            self._start_glitch_for("lbl")
        if not self._gl_active_L:
            self._start_glitch_for("L")
        if not self._gl_active_R:
            self._start_glitch_for("R")
        self._schedule_random_glitch()
        return False

    def _on_car_click(self, *_):
        self._car.grab_focus()

    def _on_sch_item_click(self, btn):
        self._on_list_item_clicked(btn._si)

    def _on_dir_changed(self, *_):
        if not self._dead:
            self._deb("r", 1000, self._scan)

    def _on_nav(self, direction=None):
        self._ulbl()
        if direction is not None:
            if direction < 0:
                if not self._gl_active_L:
                    self._start_glitch_for("L")
            else:
                if not self._gl_active_R:
                    self._start_glitch_for("R")

    def _on_search_changed(self, *_):
        self._deb("s", 300, self._do_search)

    def _do_search(self):
        if not self._dead:
            self._car.filter_files(self._ent.get_text())
            self._ulbl()

    def _ekey(self, _, e):
        k = e.keyval
        if k in _NAV_KEYS:
            r = self._car._key(self._car, e)
            if k in _LR_KEYS:
                _t_add(50, self._ulbl)
            return r
        if k == Gdk.KEY_Escape:
            self._ent.set_text("")
            return True
        return False

    def _on_sch_btn_clicked(self, *_):
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
        try:
            with open(_SCHEME_F, "w") as f:
                f.write(sch_id)
        except OSError:
            pass
        nm = self._car.cur()
        if nm and nm in self._files:
            p = _WALLS + nm
        elif os.path.exists(_CURRENT):
            p = os.path.realpath(_CURRENT)
        else:
            return
        self._car._ex.submit(self._gen_colors, p, sch_id)

    @staticmethod
    def _ldsch():
        try:
            with open(_SCHEME_F) as f:
                s = f.read().strip()
                if s in _SCH_K:
                    return s
        except OSError:
            pass
        return "scheme-tonal-spot"

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
            scandir = os.scandir
            remove = os.remove
            for entry in scandir(_THUMBS):
                if not entry.is_file(follow_symlinks=False):
                    continue
                name = entry.name
                if name.endswith(_SUFFIX) and name[:-_SFXL] in valid:
                    continue
                try:
                    remove(entry.path)
                except OSError:
                    pass
        except OSError:
            pass

    def _watch(self):
        try:
            self._mon = Gio.File.new_for_path(_WALLS).monitor_directory(
                Gio.FileMonitorFlags.NONE, None,
            )
            self._mon.connect("changed", self._on_dir_changed)
        except GLib.Error:
            pass

    def _on_sel(self, nm):
        self._apply(nm)
        self._ulbl()

    def _apply(self, nm, notify=False):
        if nm not in self._files:
            return False
        p = _WALLS + nm
        sch_id = _SCH[self._sch_idx][0]
        try:
            if os.path.lexists(_CURRENT):
                os.remove(_CURRENT)
            os.symlink(p, _CURRENT)
        except OSError:
            return False
        exec_shell_command_async(
            f'awww img "{p}" -t fade'
            f' --transition-duration 0.5'
            f' --transition-step 255'
            f' --transition-fps 60'
        )
        self._car._ex.submit(self._gen_colors, p, sch_id)
        if notify:
            exec_shell_command_async(
                f"notify-send '🎲 Wallpaper' 'Random wallpaper set 🎨'"
                f" -a 'Vidgex-Shell' -i '{p}' -e"
            )
        return True

    def _gen_colors(self, image_path, scheme_id):
        if self._dead:
            return
        try:
            apply_colors(image_path, scheme_id, _CSS_OUT, _HYPR_OUT)
            GLib.idle_add(self._reload_css)
        except Exception as e:
            print(f"[WallpaperSelector] color gen failed: {e}")

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
        self._roll()
        self._ulbl()

    def _ulbl(self, *_):
        nm = self._car.cur()
        if nm:
            dot = nm.rfind(".")
            if dot >= 0:
                nm = nm[:dot]
            if len(nm) > 50:
                nm = nm[:47] + "..."
        self._lbl.set_label(nm or "No wallpapers available")
        return False

    def _deb(self, k, ms, func):
        old = self._pend.get(k)
        if old:
            _src_rm(old)
        self._pend[k] = _t_add(ms, self._deb_run, k, func)

    def _deb_run(self, k, func):
        self._pend.pop(k, None)
        func()
        return False

    def _destroy(self, _):
        self._dead = True
        mon = self._mon
        if mon:
            mon.cancel()
        for attr in ('_gl_tid_lbl', '_gl_tid_L', '_gl_tid_R', '_gl_rand_tid'):
            tid = getattr(self, attr, None)
            if tid:
                _src_rm(tid)
                setattr(self, attr, None)
        pend = self._pend
        for sid in pend.values():
            _src_rm(sid)
        pend.clear()
        self._car.cleanup()
        self._files = ()