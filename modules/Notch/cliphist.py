from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.entry import Entry
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow
from gi.repository import GdkPixbuf, GLib, Gio

import services.icons as icons
from services.list_navigation import ListNavigationMixin


class ClipHistory(ListNavigationMixin, Box):
    __slots__ = ('notch', 'sel', 'vp', 'ent', 'sw', '_it')

    def __init__(self, notch, **kw):
        super().__init__(name="clip-history", visible=False, all_visible=False, **kw)
        self.notch, self.sel, self._it = notch, -1, None
        self.vp = Box(name="viewport", spacing=4, orientation="v")
        self.ent = Entry(
            name="search-entry", 
            placeholder="Поиск в истории буфера...", 
            h_expand=True,
            notify_text=lambda e, *_: self._flt(e.get_text().lower()),
            on_activate=lambda *_: self._nav_activate(), 
            on_key_press_event=self._nav_key
        )
        self.ent.props.xalign = 0.5
        self.sw = ScrolledWindow(
            name="scrolled-window", 
            v_expand=True, 
            child=self.vp, 
            propagate_height=False
        )
        self.add(Box(
            name="launcher-box", 
            spacing=10, 
            h_expand=True, 
            orientation="v", 
            children=[
                Box(
                    name="header_box", 
                    spacing=10, 
                    children=[
                        Button(
                            name="clear-button", 
                            child=Label(
                                name="clear-label", 
                                markup=icons.trash
                            ), 
                            on_clicked=lambda *_: self._wipe()
                        ),
                        self.ent,
                        Button(
                            name="close-button", 
                            child=Label(
                                name="close-label", 
                                markup=icons.cancel
                            ), 
                            on_clicked=lambda *_: self.close()
                        )
                    ]
                ),
                self.sw
            ]
        ))

    def _run(self, args, inp=None):
        try:
            fl = Gio.SubprocessFlags.STDOUT_PIPE | (Gio.SubprocessFlags.STDIN_PIPE if inp else 0)
            _, o, _ = Gio.Subprocess.new(args, fl).communicate(GLib.Bytes.new(inp) if inp else None, None)
            return o.get_data() if o else b""
        except: return b""

    def _flt(self, s=""):
        self._nav_clear()
        if not self._it: return self._empty()
        n = 0
        for idx, cnt in self._it:
            if s and s not in cnt.lower(): continue
            self.vp.add(self._mk(idx, cnt)); n += 1
            if n >= 100: break
        if n: self.show_all(); GLib.idle_add(lambda: self._nav_usel(0) or False)
        else: self._empty()

    def _mk(self, idx, cnt):
        img = cnt.startswith("[[ binary data")
        ic = Image(name="clip-icon") if img else Label(name="clip-icon", markup=icons.clip_text)
        txt = "[Изображение]" if img else cnt[:100].strip()
        if img: GLib.idle_add(lambda i=idx: self._thumb(ic, i) or False)
        return Button(
            name="slot-button", 
            on_clicked=lambda *_, 
            i=idx: self._paste(i),
            child=Box(
                name="slot-box", 
                orientation="h", 
                spacing=10, 
                children=[
                    ic, 
                    Label(
                        name="clip-label", 
                        label=txt, 
                        ellipsization="end", 
                        h_align="start", 
                        h_expand=True
                    )
                ]
            )
        )

    def _thumb(self, w, idx):
        if not (d := self._run(["cliphist", "decode", idx])): return
        try:
            ld = GdkPixbuf.PixbufLoader(); ld.write(d); ld.close()
            if px := ld.get_pixbuf():
                sc = min(64 / px.get_width(), 64 / px.get_height(), 1)
                w.set_from_pixbuf(px.scale_simple(int(px.get_width() * sc), int(px.get_height() * sc), GdkPixbuf.InterpType.BILINEAR))
        except: pass

    def _paste(self, idx):
        if d := self._run(["cliphist", "decode", idx]): self._run(["wl-copy"], d)
        self.close()

    def _wipe(self):
        self._run(["cliphist", "wipe"]); self._it = None; self._flt()

    def _empty(self):
        self._nav_clear()
        self.vp.add(Box(
            name="no-clip-container", 
            v_expand=True, 
            h_expand=True, 
            orientation="v", 
            children=[
                Label(
                    name="no-clip", 
                    markup=icons.clipboard, 
                    v_align="center", 
                    h_align="center", 
                    v_expand=True, 
                    h_expand=True
                )
            ]
        ))
        self.show_all()

    def open(self):
        self.ent.set_text("")
        raw = self._run(["cliphist", "list"])
        self._it = [(p[0], p[1]) for ln in raw.decode(errors='ignore').splitlines() if len(p := ln.split('\t', 1)) == 2] if raw else None
        self._flt(); self.ent.grab_focus(); self.show_all()

    def close(self):
        self._nav_clear(); self._it = None; self.notch.close_notch()