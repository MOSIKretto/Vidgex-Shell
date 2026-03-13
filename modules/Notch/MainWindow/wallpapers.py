import os
import random

from fabric.utils.helpers import exec_shell_command_async
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.entry import Entry
from fabric.widgets.label import Label

from gi.repository import Gdk, Gio, GLib, Gtk

from modules.Notch.MainWindow.Wallpaper.wallpaperConstants import (
    _AGL_FRAME_MS, _AGL_FRAMES, _AGL_RAND_MAX,
    _AGL_RAND_MIN, _ARR_LINES, _CURRENT,
    _DICE, _EXT, _SCH, _SCH_K, _SCH_LEN,
    _SCHEME_F, _SFXL, _SUFFIX, _THUMBS, _WALLS,
)
from modules.Notch.MainWindow.Wallpaper.wallpaperUtils import  _arr_glitch_lines, _arr_set_art, _md5hex, _setup_pointer_cursor
from modules.Notch.MainWindow.Wallpaper.wallpaperCarousel import WallpaperCarousel


class WallpaperSelector(Box):
    __slots__ = (
        "_dead", "_pend", "_files", "_mon", "_car", "_ent",
        "_sch_btn", "_sch_rev", "_sch_idx", "_rb", "_lbl",
        "_agl_lbls",
        "_agl_on", "_agl_rem", "_agl_tid",
        "_agl_rand_tid",
    )

    def __init__(self, **kw):
        super().__init__(
            name="wallpapers", spacing=4, orientation="v", **kw,
        )
        self._dead = False
        self._pend = {}
        self._files = ()
        self._mon = None

        self._agl_on = [False, False]
        self._agl_rem = [0, 0]
        self._agl_tid = [None, None]
        self._agl_rand_tid = None

        os.makedirs(_WALLS, exist_ok=True)
        os.makedirs(_THUMBS, exist_ok=True)

        self._car = WallpaperCarousel(
            on_select=self._on_sel,
            on_navigate=self._on_nav,
        )
        ew = Gtk.EventBox()
        ew.add(self._car)
        ew.connect("button-press-event", lambda *_: self._car.grab_focus())

        lbl_l = Label(name="carousel-arrow-label")
        lbl_l.set_halign(Gtk.Align.START)
        lbl_l.set_valign(Gtk.Align.CENTER)
        lbl_l.set_can_focus(False)

        lbl_r = Label(name="carousel-arrow-label")
        lbl_r.set_halign(Gtk.Align.END)
        lbl_r.set_valign(Gtk.Align.CENTER)
        lbl_r.set_can_focus(False)

        self._agl_lbls = (lbl_l, lbl_r)

        lbl_l.connect(
            "style-updated",
            lambda *_: self._arr_render(0) if not self._agl_on[0] else None,
        )
        lbl_r.connect(
            "style-updated",
            lambda *_: self._arr_render(1) if not self._agl_on[1] else None,
        )

        car_ov = Gtk.Overlay()
        car_ov.add(ew)
        car_ov.add_overlay(lbl_l)
        car_ov.add_overlay(lbl_r)
        car_ov.set_overlay_pass_through(lbl_l, True)
        car_ov.set_overlay_pass_through(lbl_r, True)

        cb = Box(
            name="carousel-container",
            orientation="h",
            h_align="center",
            v_align="center",
        )
        cb.pack_start(car_ov, True, True, 0)

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
            btn.connect(
                "clicked",
                lambda _, idx=i: self._on_list_item_clicked(idx),
            )
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

    def _on_realize_full(self, *_):
        self._arr_render(0)
        self._arr_render(1)
        self._arr_schedule_rand()
        if not self._files:
            self._scan()

    def _arr_render(self, idx):
        _arr_set_art(self._agl_lbls[idx], _ARR_LINES[idx])

    def _on_nav(self, direction):
        self._ulbl()
        if not self._car._spl:
            self._arr_glitch(0 if direction < 0 else 1)

    def _arr_glitch(self, idx):
        self._agl_on[idx] = True
        self._agl_rem[idx] = _AGL_FRAMES
        if self._agl_tid[idx]:
            GLib.source_remove(self._agl_tid[idx])
        self._agl_tid[idx] = GLib.timeout_add(
            _AGL_FRAME_MS, self._arr_gl_tick, idx,
        )

    def _arr_gl_tick(self, idx):
        total = _AGL_FRAMES
        progress = 1.0 - self._agl_rem[idx] / total
        glitched = _arr_glitch_lines(_ARR_LINES[idx], progress)
        _arr_set_art(self._agl_lbls[idx], glitched)
        self._agl_rem[idx] -= 1
        if self._agl_rem[idx] <= 0:
            self._agl_on[idx] = False
            self._agl_tid[idx] = None
            self._arr_render(idx)
            return False
        return True

    def _arr_schedule_rand(self):
        if self._dead:
            return
        delay = random.randint(_AGL_RAND_MIN, _AGL_RAND_MAX)
        self._agl_rand_tid = GLib.timeout_add_seconds(
            delay, self._arr_fire_rand,
        )

    def _arr_fire_rand(self):
        self._agl_rand_tid = None
        if self._dead:
            return False
        pick = random.randint(0, 2)
        if pick in (0, 2) and not self._agl_on[0]:
            self._arr_glitch(0)
        if pick in (1, 2) and not self._agl_on[1]:
            self._arr_glitch(1)
        self._arr_schedule_rand()
        return False

    def _on_search_changed(self, entry, _):
        self._deb("s", 300, self._srch, entry.get_text())

    def _srch(self, t):
        if not self._dead:
            self._car.filter_files(t)
            self._ulbl()

    def _ekey(self, _, e):
        k = e.keyval
        if k in (
            Gdk.KEY_Left, Gdk.KEY_Right,
            Gdk.KEY_Return, Gdk.KEY_KP_Enter,
        ):
            return self._car._key(self._car, e)
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
                    f'matugen image "{_CURRENT}" -t {sch_id}',
                )
        except OSError:
            pass

    def _ldsch(self):
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
            for entry in os.scandir(_THUMBS):
                if not entry.is_file(follow_symlinks=False):
                    continue
                name = entry.name
                if name.endswith(_SUFFIX) and name[:-_SFXL] in valid:
                    continue
                try:
                    os.remove(entry.path)
                except OSError:
                    pass
        except OSError:
            pass

    def _watch(self):
        try:
            self._mon = Gio.File.new_for_path(_WALLS).monitor_directory(
                Gio.FileMonitorFlags.NONE, None,
            )
            self._mon.connect(
                "changed",
                lambda *_: (
                    self._deb("r", 1000, self._scan)
                    if not self._dead
                    else None
                ),
            )
        except GLib.Error:
            pass

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

    def _ulbl(self):
        nm = self._car.cur()
        if nm:
            nm = nm.rsplit(".", 1)[0]
            if len(nm) > 50:
                nm = nm[:47] + "..."
        self._lbl.set_label(nm or "No wallpapers available")
        return False

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
        for i in (0, 1):
            tid = self._agl_tid[i]
            if tid:
                GLib.source_remove(tid)
                self._agl_tid[i] = None
        if self._agl_rand_tid:
            GLib.source_remove(self._agl_rand_tid)
            self._agl_rand_tid = None
        if self._mon:
            self._mon.cancel()
        for sid in self._pend.values():
            GLib.source_remove(sid)
        self._pend.clear()
        self._car.cleanup()
        self._files = ()