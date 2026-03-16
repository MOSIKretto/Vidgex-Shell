from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.entry import Entry
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow
from gi.repository import GdkPixbuf, GLib, Gio

import services.icons as icons
from services.listNavigation import ListNavigationMixin


class ClipHistory(ListNavigationMixin, Box):
    MAX_ITEMS = 100
    DEBOUNCE_MS = 150
    THUMB_SIZE = 64
    BATCH_SIZE = 25

    __slots__ = (
        'notch', 'sel', 'vp', 'ent', 'sw',
        '_entries',
        '_filter_id',
        '_batch_id',
        '_thumb_cache',
    )

    def __init__(self, notch, **kw):
        super().__init__(
            name="clip-history",
            visible=False,
            all_visible=False,
            **kw,
        )
        self.notch = notch
        self.sel = -1

        self._entries = []
        self._filter_id = 0
        self._batch_id = 0
        self._thumb_cache = {}

        self.vp = Box(name="viewport", spacing=4, orientation="v")

        self.ent = Entry(
            name="search-entry",
            placeholder="Searching the buffer history...",
            h_expand=True,
            notify_text=lambda e, *_: self._schedule_filter(e.get_text()),
            on_activate=lambda *_: self._nav_activate(),
            on_key_press_event=self._nav_key,
        )
        self.ent.props.xalign = 0.5

        self.sw = ScrolledWindow(
            name="scrolled-window",
            v_expand=True,
            child=self.vp,
            propagate_height=False,
        )

        self.add(Box(
            name="launcher-box",
            spacing=10,
            h_expand=True,
            orientation="v",
            children=[
                Box(name="header_box", spacing=10, children=[
                    Button(
                        name="clear-button",
                        child=Label(name="clear-label", markup=icons.trash),
                        on_clicked=lambda *_: self._wipe(),
                    ),
                    self.ent,
                    Button(
                        name="close-button",
                        child=Label(name="close-label", markup=icons.cancel),
                        on_clicked=lambda *_: self.close(),
                    ),
                ]),
                self.sw,
            ],
        ))

    def _schedule_filter(self, text: str):
        if self._filter_id:
            GLib.source_remove(self._filter_id)
        self._filter_id = GLib.timeout_add(
            self.DEBOUNCE_MS,
            self._apply_filter,
            text.lower(),
        )

    def _apply_filter(self, query=""):
        self._filter_id = 0
        self._cancel_batch()
        self._nav_clear()

        if not self._entries:
            self._show_empty()
            return False

        filtered = [
            e for e in self._entries
            if not query or query in e[1].lower()
        ][:self.MAX_ITEMS]

        if not filtered:
            self._show_empty()
            return False

        self._add_batch(filtered, 0)
        return False

    def _add_batch(self, items, start):
        end = min(start + self.BATCH_SIZE, len(items))

        for i in range(start, end):
            idx, cnt, is_img = items[i]
            self.vp.add(self._make_row(idx, cnt, is_img))

        self.show_all()

        if start == 0:
            self._nav_usel(0)

        if end < len(items):
            self._batch_id = GLib.idle_add(
                self._add_batch, items, end,
            )

    def _cancel_batch(self):
        if self._batch_id:
            GLib.source_remove(self._batch_id)
            self._batch_id = 0

    def _make_row(self, idx, content, is_image):
        if is_image:
            icon = Image(name="clip-icon")
            if idx in self._thumb_cache:
                icon.set_from_pixbuf(self._thumb_cache[idx])
            else:
                self._load_thumb_async(icon, idx)
            text = "[Image]"
        else:
            icon = Label(name="clip-icon", markup=icons.clip_text)
            text = content[:100].strip()

        return Button(
            name="slot-button",
            on_clicked=lambda *_, i=idx: self._paste(i),
            child=Box(
                name="slot-box",
                orientation="h",
                spacing=10,
                children=[
                    icon,
                    Label(
                        name="clip-label",
                        label=text,
                        ellipsization="end",
                        h_align="start",
                        h_expand=True,
                    ),
                ],
            ),
        )

    def _load_thumb_async(self, widget, idx):
        try:
            proc = Gio.Subprocess.new(
                ["cliphist", "decode", idx],
                Gio.SubprocessFlags.STDOUT_PIPE,
            )
            proc.communicate_async(
                None, None,
                lambda p, res, w=widget, i=idx: self._on_thumb_ready(p, res, w, i),
            )
        except Exception:
            pass

    def _on_thumb_ready(self, proc, result, widget, idx):
        try:
            _, stdout, _ = proc.communicate_finish(result)
            data = stdout.get_data() if stdout else None
            if not data:
                return

            loader = GdkPixbuf.PixbufLoader()
            loader.write(data)
            loader.close()

            px = loader.get_pixbuf()
            if not px:
                return

            s = self.THUMB_SIZE
            scale = min(s / px.get_width(), s / px.get_height(), 1.0)
            scaled = px.scale_simple(
                int(px.get_width() * scale),
                int(px.get_height() * scale),
                GdkPixbuf.InterpType.BILINEAR,
            )

            self._thumb_cache[idx] = scaled

            if widget.get_parent() is not None:
                widget.set_from_pixbuf(scaled)
        except Exception:
            pass

    def _run_sync(self, args, stdin_data=None):
        try:
            flags = Gio.SubprocessFlags.STDOUT_PIPE
            if stdin_data:
                flags |= Gio.SubprocessFlags.STDIN_PIPE

            proc = Gio.Subprocess.new(args, flags)
            _, out, _ = proc.communicate(
                GLib.Bytes.new(stdin_data) if stdin_data else None,
                None,
            )
            return out.get_data() if out else b""
        except Exception:
            return b""

    def _paste(self, idx):
        data = self._run_sync(["cliphist", "decode", idx])
        if data:
            self._run_sync(["wl-copy"], data)
        self.close()

    def _wipe(self):
        self._run_sync(["cliphist", "wipe"])
        self._entries.clear()
        self._thumb_cache.clear()
        self._apply_filter()

    def _show_empty(self):
        self._nav_clear()
        self.vp.add(Box(
            name="no-clip-container",
            v_expand=True, h_expand=True,
            orientation="v",
            children=[Label(
                name="no-clip",
                markup=icons.clipboard,
                v_align="center", h_align="center",
                v_expand=True, h_expand=True,
            )],
        ))
        self.show_all()

    def _parse_list(self, raw: str):
        entries = []
        for line in raw.split('\n'):
            parts = line.split('\t', 1)
            if len(parts) == 2:
                idx, cnt = parts
                entries.append((idx, cnt, cnt.startswith("[[ binary data")))
        return entries

    def open(self):
        self.ent.set_text("")
        self.show_all()
        self.ent.grab_focus()

        try:
            proc = Gio.Subprocess.new(
                ["cliphist", "list"],
                Gio.SubprocessFlags.STDOUT_PIPE,
            )
            proc.communicate_async(None, None, self._on_list_loaded)
        except Exception:
            self._entries = []
            self._show_empty()

    def _on_list_loaded(self, proc, result):
        try:
            _, stdout, _ = proc.communicate_finish(result)
            raw = stdout.get_data().decode("utf-8", errors="ignore") if stdout else ""
            self._entries = self._parse_list(raw)
        except Exception:
            self._entries = []

        self._apply_filter(self.ent.get_text().lower())

    def close(self):
        self._cancel_batch()
        if self._filter_id:
            GLib.source_remove(self._filter_id)
            self._filter_id = 0

        self._nav_clear()

        self._entries.clear()
        self._thumb_cache.clear()

        self.notch.close_notch()