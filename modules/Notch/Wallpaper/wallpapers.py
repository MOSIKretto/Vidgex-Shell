from fabric.utils.helpers import exec_shell_command_async
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.entry import Entry
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk

import modules.icons as icons

_HOME = GLib.get_home_dir()
_WALLPAPERS_DIR = f"{_HOME}/Wallpapers"
_CURRENT_WALL = f"{_HOME}/.current.wall"
_CACHE_DIR = f"{GLib.get_user_cache_dir()}/vidgex-shell"
_SCHEME_FILE = f"{_CACHE_DIR}/scheme"

_THUMBNAIL_SIZE = 110
_ITEM_PADDING = 6
_SPACING = 8
_MARGIN = 0
_COLUMNS = 8

_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")

_SCHEMES = (
    ("scheme-tonal-spot", "Tonal Spot"),
    ("scheme-content", "Content"),
    ("scheme-expressive", "Expressive"),
    ("scheme-fidelity", "Fidelity"),
    ("scheme-fruit-salad", "Fruit Salad"),
    ("scheme-monochrome", "Monochrome"),
    ("scheme-neutral", "Neutral"),
    ("scheme-rainbow", "Rainbow"),
)

_DICE_ICONS = (
    icons.dice_1, icons.dice_2, icons.dice_3,
    icons.dice_4, icons.dice_5, icons.dice_6,
)

_NAV_KEYS = frozenset((Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Right))
_ACT_KEYS = frozenset((Gdk.KEY_Return, Gdk.KEY_KP_Enter))


class WallpaperSelector(Box):
    """Виджет выбора обоев."""
    
    DEBOUNCE_SEARCH_MS = 400
    DEBOUNCE_SCROLL_MS = 250
    DEBOUNCE_FILE_MS = 500
    
    MAX_QUEUE_SIZE = 12
    MAX_VISIBLE = 32
    BUFFER_ROWS = 2

    def __init__(self, **kwargs):
        super().__init__(name="wallpapers", spacing=4, orientation="v", **kwargs)
        
        self.files = []
        self.selected_index = -1
        self.is_applying_scheme = False
        self._destroyed = False
        self._load_queue = []
        self._is_loading = False
        self._last_query = ""
        self._visible_range = (0, 100)
        self._pending_search = None
        self._pending_scroll = None
        self._pending_file_events = {}
        
        self._init_dirs()
        self._build_ui()
        self.connect("destroy", self._on_destroy)
        GLib.idle_add(self._deferred_init, priority=GLib.PRIORITY_LOW)

    def _init_dirs(self):
        for path in (_WALLPAPERS_DIR, _CACHE_DIR):
            gfile = Gio.File.new_for_path(path)
            if not gfile.query_exists():
                try:
                    gfile.make_directory_with_parents(None)
                except Exception:
                    pass

    def _build_ui(self):
        self.model = Gtk.ListStore(GdkPixbuf.Pixbuf, str)
        
        self.viewport = Gtk.IconView(
            name="wallpaper-icons",
            model=self.model,
            pixbuf_column=0,
            text_column=-1,
            columns=_COLUMNS,
            item_width=-1,
            item_padding=_ITEM_PADDING,
            column_spacing=_SPACING,
            row_spacing=_SPACING,
            margin=_MARGIN
        )
        self.viewport.set_activate_on_single_click(False)
        self.viewport.connect("item-activated", self._on_wallpaper_activated)

        self.scrolled_window = ScrolledWindow(
            name="scrolled-window",
            spacing=10,
            child=self.viewport,
            propagate_width=False,
            propagate_height=False,
        )
        self.scrolled_window.set_size_request(-1, 297)
        self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.search_entry = Entry(
            name="search-entry-walls",
            placeholder="Search Wallpapers...",
            h_expand=True,
            h_align="fill",
            xalign=0.5,
        )
        self.search_entry.connect("key-press-event", self._on_key_press)
        self.search_entry.connect("notify::text", self._on_search_changed)
        self.search_entry.connect("focus-out-event", self._on_focus_out)

        self.scheme_dropdown = Gtk.ComboBoxText(name="scheme-dropdown")
        self.scheme_dropdown.set_tooltip_text("Select color scheme")
        for key, name in _SCHEMES:
            self.scheme_dropdown.append(key, name)
        self.scheme_dropdown.set_active_id(self._load_scheme())
        self.scheme_dropdown.connect("changed", self._on_scheme_changed)

        self.random_btn = Button(
            name="random-wall-button",
            child=Label(name="random-wall-label", markup=_DICE_ICONS[0]),
            tooltip_text="Random Wallpaper",
        )
        self.random_btn.connect("clicked", self.set_random_wallpaper)

        header = Box(
            name="header-box",
            spacing=8,
            children=[self.random_btn, self.search_entry, self.scheme_dropdown],
        )
        self.add(header)
        self.pack_start(self.scrolled_window, True, True, 0)

    def _deferred_init(self):
        if self._destroyed:
            return False
        self._load_wallpapers()
        self._setup_monitor()
        self.show_all()
        self._randomize_dice_icon()
        self.search_entry.grab_focus()
        return False

    def _load_wallpapers(self):
        gfile = Gio.File.new_for_path(_WALLPAPERS_DIR)
        gfile.enumerate_children_async(
            "standard::name",
            Gio.FileQueryInfoFlags.NONE,
            GLib.PRIORITY_LOW,
            None,
            self._on_enum_done,
            []
        )

    def _on_enum_done(self, source, result, files):
        if self._destroyed:
            return
        try:
            enumerator = source.enumerate_children_finish(result)
            self._read_batch(enumerator, files)
        except Exception:
            self._finalize_files(files)

    def _read_batch(self, enumerator, files):
        if self._destroyed:
            return
        enumerator.next_files_async(
            100, GLib.PRIORITY_LOW, None,
            self._on_files_batch, (enumerator, files)
        )

    def _on_files_batch(self, source, result, user_data):
        if self._destroyed:
            return
        enumerator, files = user_data
        try:
            infos = source.next_files_finish(result)
            if not infos:
                enumerator.close_async(GLib.PRIORITY_LOW, None, None, None)
                self._finalize_files(files)
                return
            for info in infos:
                name = info.get_name()
                lower = name.lower()
                for ext in _IMAGE_EXTENSIONS:
                    if lower.endswith(ext):
                        normalized = lower.replace(" ", "-")
                        if normalized != name:
                            self._rename_file(name, normalized)
                        files.append(normalized)
                        break
            self._read_batch(enumerator, files)
        except Exception:
            self._finalize_files(files)

    def _rename_file(self, old, new):
        src = f"{_WALLPAPERS_DIR}/{old}"
        dst = f"{_WALLPAPERS_DIR}/{new}"
        try:
            Gio.File.new_for_path(src).move(
                Gio.File.new_for_path(dst),
                Gio.FileCopyFlags.NONE, None, None
            )
        except Exception:
            try:
                GLib.spawn_command_line_sync(f'mv "{src}" "{dst}"')
            except Exception:
                pass

    def _finalize_files(self, files):
        files.sort()
        self.files = files
        GLib.idle_add(self._populate_model, priority=GLib.PRIORITY_LOW)

    def _populate_model(self):
        if self._destroyed:
            return False
        self.viewport.set_model(None)
        self.model.clear()
        self._load_queue.clear()
        
        for filename in self.files:
            self.model.append((None, filename))
        
        self.viewport.set_model(self.model)
        self._load_visible_range(0, min(len(self.files), _COLUMNS * 3))
        return False

    def _load_visible_range(self, start, end):
        if self._destroyed:
            return
        for i in range(start, end):
            if i >= len(self.model):
                break
            try:
                iter_ = self.model.get_iter(Gtk.TreePath.new_from_indices([i]))
                if self.model.get_value(iter_, 0) is None:
                    filename = self.model.get_value(iter_, 1)
                    if filename and filename not in self._load_queue:
                        if len(self._load_queue) < self.MAX_QUEUE_SIZE:
                            self._load_queue.append((i, filename))
            except Exception:
                pass
        if self._load_queue and not self._is_loading:
            self._process_next()

    def _process_next(self):
        if self._destroyed or not self._load_queue:
            self._is_loading = False
            return
        self._is_loading = True
        idx, filename = self._load_queue.pop(0)
        GLib.idle_add(self._load_thumbnail, idx, filename, priority=GLib.PRIORITY_LOW)

    def _load_thumbnail(self, idx, filename):
        if self._destroyed:
            return False
        
        path = f"{_WALLPAPERS_DIR}/{filename}"
        if not Gio.File.new_for_path(path).query_exists():
            GLib.idle_add(self._process_next, priority=GLib.PRIORITY_LOW)
            return False
        
        pixbuf = None
        is_gif = filename.lower().endswith('.gif')
        
        if is_gif:
            try:
                animation = GdkPixbuf.PixbufAnimation.new_from_file(path)
                if animation:
                    if animation.is_static_image():
                        raw = animation.get_static_image()
                    else:
                        anim_iter = animation.get_iter(None)
                        raw = anim_iter.get_pixbuf() if anim_iter else None
                    if raw:
                        pixbuf = self._scale_pixbuf(raw)
            except Exception:
                pass
        
        if not pixbuf:
            try:
                raw = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    path, _THUMBNAIL_SIZE + 20, _THUMBNAIL_SIZE + 20, True
                )
                if raw:
                    pixbuf = self._scale_pixbuf(raw)
            except Exception:
                pass
        
        if not pixbuf:
            try:
                raw = GdkPixbuf.Pixbuf.new_from_file(path)
                if raw:
                    pixbuf = self._scale_pixbuf(raw)
            except Exception:
                pass
        
        if pixbuf and idx < len(self.model):
            try:
                iter_ = self.model.get_iter(Gtk.TreePath.new_from_indices([idx]))
                self.model.set_value(iter_, 0, pixbuf)
            except Exception:
                pass
        
        self._unload_distant()
        GLib.idle_add(self._process_next, priority=GLib.PRIORITY_LOW)
        return False

    def _scale_pixbuf(self, pixbuf):
        if not pixbuf:
            return None
        w, h = pixbuf.get_width(), pixbuf.get_height()
        if w <= 0 or h <= 0:
            return None
        
        size = _THUMBNAIL_SIZE
        scale = size / min(w, h)
        new_w = max(size, int(w * scale))
        new_h = max(size, int(h * scale))
        
        try:
            scaled = pixbuf.scale_simple(new_w, new_h, GdkPixbuf.InterpType.BILINEAR)
            if not scaled:
                return None
            if new_w > size or new_h > size:
                x = (new_w - size) // 2
                y = (new_h - size) // 2
                return scaled.new_subpixbuf(x, y, size, size)
            return scaled
        except Exception:
            return None

    def _unload_distant(self):
        start, end = self._visible_range
        threshold = _COLUMNS * 4
        count = 0
        
        for i in range(len(self.model)):
            if i < start - threshold or i > end + threshold:
                try:
                    iter_ = self.model.get_iter(Gtk.TreePath.new_from_indices([i]))
                    if self.model.get_value(iter_, 0) is not None:
                        self.model.set_value(iter_, 0, None)
                        count += 1
                        if count >= 10:
                            break
                except Exception:
                    pass

    def _setup_monitor(self):
        gfile = Gio.File.new_for_path(_WALLPAPERS_DIR)
        self.file_monitor = gfile.monitor_directory(Gio.FileMonitorFlags.NONE, None)
        self.file_monitor.connect("changed", self._on_dir_changed)
        self.scrolled_window.get_vadjustment().connect("value-changed", self._on_scroll)

    def _on_scroll(self, adj):
        if self._pending_scroll:
            GLib.source_remove(self._pending_scroll)
        self._pending_scroll = GLib.timeout_add(
            self.DEBOUNCE_SCROLL_MS, self._handle_scroll, adj
        )

    def _handle_scroll(self, adj):
        self._pending_scroll = None
        if self._destroyed:
            return False
        
        total = len(self.model)
        if total == 0:
            return False
        
        upper = adj.get_upper()
        page = adj.get_page_size()
        if upper <= page:
            return False
        
        row_height = _THUMBNAIL_SIZE + _SPACING
        first_row = int(adj.get_value() / row_height)
        visible_rows = int(page / row_height) + 1
        
        first_idx = first_row * _COLUMNS
        last_idx = min(total, (first_row + visible_rows + 1) * _COLUMNS)
        
        self._visible_range = (first_idx, last_idx)
        
        buffer = _COLUMNS * self.BUFFER_ROWS
        load_start = max(0, first_idx - buffer)
        load_end = min(total, last_idx + buffer)
        
        self._load_queue.clear()
        self._load_visible_range(load_start, load_end)
        return False

    def _on_dir_changed(self, monitor, file, other, event_type):
        if self._destroyed:
            return
        if event_type not in (
            Gio.FileMonitorEvent.DELETED,
            Gio.FileMonitorEvent.CREATED,
            Gio.FileMonitorEvent.CHANGES_DONE_HINT,
        ):
            return
        
        filename = file.get_basename()
        lower = filename.lower()
        is_image = any(lower.endswith(ext) for ext in _IMAGE_EXTENSIONS)
        if not is_image and event_type != Gio.FileMonitorEvent.DELETED:
            return
        
        if filename in self._pending_file_events:
            GLib.source_remove(self._pending_file_events[filename])
        self._pending_file_events[filename] = GLib.timeout_add(
            self.DEBOUNCE_FILE_MS, self._process_file_event, filename, event_type
        )

    def _process_file_event(self, filename, event_type):
        if self._destroyed:
            return False
        self._pending_file_events.pop(filename, None)
        if event_type == Gio.FileMonitorEvent.DELETED:
            self._handle_deleted(filename)
        else:
            self._handle_added(filename)
        return False

    def _handle_deleted(self, filename):
        normalized = filename.lower().replace(" ", "-")
        target = filename if filename in self.files else (normalized if normalized in self.files else None)
        if not target:
            return
        
        idx = self.files.index(target)
        self.files.remove(target)
        
        self._load_queue = [(i, f) for i, f in self._load_queue if f != target]
        
        try:
            iter_ = self.model.get_iter(Gtk.TreePath.new_from_indices([idx]))
            self.model.remove(iter_)
        except Exception:
            pass
        
        if self.selected_index >= len(self.model):
            self.selected_index = max(-1, len(self.model) - 1)

    def _handle_added(self, filename):
        lower = filename.lower()
        if not any(lower.endswith(ext) for ext in _IMAGE_EXTENSIONS):
            return
        
        normalized = lower.replace(" ", "-")
        if normalized != filename:
            self._rename_file(filename, normalized)
            filename = normalized
        
        if not Gio.File.new_for_path(f"{_WALLPAPERS_DIR}/{filename}").query_exists():
            return
        
        if filename in self.files:
            idx = self.files.index(filename)
            try:
                iter_ = self.model.get_iter(Gtk.TreePath.new_from_indices([idx]))
                self.model.set_value(iter_, 0, None)
            except Exception:
                pass
            self._load_queue.insert(0, (idx, filename))
            if not self._is_loading:
                self._process_next()
            return
        
        lo, hi = 0, len(self.files)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.files[mid] < filename:
                lo = mid + 1
            else:
                hi = mid
        
        self.files.insert(lo, filename)
        
        if self._last_query:
            q = self._last_query.casefold()
            if q not in filename.casefold():
                return
            pos = sum(1 for f in self.files[:lo] if q in f.casefold())
        else:
            pos = lo
        
        iter_ = self.model.insert(pos)
        self.model.set(iter_, 0, None, 1, filename)
        
        self._load_queue.insert(0, (pos, filename))
        if not self._is_loading:
            self._process_next()

    def _on_search_changed(self, entry, *args):
        text = entry.get_text()
        if text == self._last_query:
            return
        if self._pending_search:
            GLib.source_remove(self._pending_search)
        self._pending_search = GLib.timeout_add(
            self.DEBOUNCE_SEARCH_MS, self._apply_filter, text
        )

    def _apply_filter(self, query):
        self._pending_search = None
        if self._destroyed:
            return False
        
        self._last_query = query
        self._load_queue.clear()
        self._is_loading = False
        self._visible_range = (0, 100)
        
        self.viewport.set_model(None)
        self.model.clear()
        
        q = query.casefold() if query else None
        count = 0
        for filename in self.files:
            if not q or q in filename.casefold():
                self.model.append((None, filename))
                count += 1
        
        self.viewport.set_model(self.model)
        
        if count > 0:
            self._load_visible_range(0, min(count, _COLUMNS * 3))
        
        if not query:
            self.viewport.unselect_all()
            self.selected_index = -1
        elif count > 0:
            self._update_selection(0)
        return False

    def _on_key_press(self, widget, event):
        key = event.keyval
        if event.state & Gdk.ModifierType.SHIFT_MASK:
            return self._handle_scheme_nav(key)
        if key in _NAV_KEYS:
            self._move_selection(key)
            return True
        if key in _ACT_KEYS and self.selected_index >= 0:
            path = Gtk.TreePath.new_from_indices([self.selected_index])
            self._on_wallpaper_activated(self.viewport, path)
            return True
        return False

    def _handle_scheme_nav(self, key):
        current = self.scheme_dropdown.get_active()
        n = len(_SCHEMES)
        if key == Gdk.KEY_Up:
            self.scheme_dropdown.set_active((current - 1) % n)
            return True
        if key == Gdk.KEY_Down:
            self.scheme_dropdown.set_active((current + 1) % n)
            return True
        if key == Gdk.KEY_Right:
            self.scheme_dropdown.popup()
            return True
        return False

    def _move_selection(self, key):
        total = len(self.model)
        if total == 0:
            return
        current = self.selected_index
        if current < 0:
            new = 0 if key in (Gdk.KEY_Down, Gdk.KEY_Right) else total - 1
        elif key == Gdk.KEY_Up:
            new = current - _COLUMNS if current >= _COLUMNS else current
        elif key == Gdk.KEY_Down:
            new = current + _COLUMNS if current + _COLUMNS < total else current
        elif key == Gdk.KEY_Left:
            new = current - 1 if current > 0 else current
        else:
            new = current + 1 if current < total - 1 else current
        if 0 <= new < total and new != current:
            self._update_selection(new)

    def _update_selection(self, index):
        self.viewport.unselect_all()
        path = Gtk.TreePath.new_from_indices([index])
        self.viewport.select_path(path)
        self.viewport.scroll_to_path(path, False, 0.5, 0.5)
        self.selected_index = index

    def _on_focus_out(self, widget, event):
        if self.get_mapped():
            widget.grab_focus()
        return False

    def _on_wallpaper_activated(self, iconview, path):
        try:
            filename = self.model[path][1]
            self._apply_wallpaper(filename)
        except Exception:
            pass

    def _apply_wallpaper(self, filename, notify=False):
        if filename not in self.files:
            return False
        full_path = f"{_WALLPAPERS_DIR}/{filename}"
        scheme = self.scheme_dropdown.get_active_id()
        try:
            gfile = Gio.File.new_for_path(_CURRENT_WALL)
            if gfile.query_exists():
                gfile.delete(None)
            gfile.make_symbolic_link(full_path, None)
        except Exception:
            GLib.spawn_command_line_sync(f'ln -sf "{full_path}" "{_CURRENT_WALL}"')
        exec_shell_command_async(
            f'awww img "{full_path}" --type outer '
            f'--transition-duration 0.5 --transition-step 255 --transition-fps 60'
        )
        exec_shell_command_async(f'matugen image "{full_path}" --type {scheme}')
        if notify:
            exec_shell_command_async(
                f"notify-send '🎲 Wallpaper' 'Random wallpaper set 🎨' "
                f"-a 'Vidgex-Shell' -i '{full_path}' -e"
            )
        return True

    def _on_scheme_changed(self, widget):
        if self.is_applying_scheme:
            return
        scheme = widget.get_active_id()
        if not scheme:
            return
        self.is_applying_scheme = True
        try:
            self._save_scheme(scheme)
            gfile = Gio.File.new_for_path(_CURRENT_WALL)
            info = gfile.query_info(
                Gio.FILE_ATTRIBUTE_STANDARD_IS_SYMLINK,
                Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None
            )
            if info.get_is_symlink():
                exec_shell_command_async(f'matugen image "{_CURRENT_WALL}" -t {scheme}')
        except Exception:
            pass
        finally:
            self.is_applying_scheme = False

    def set_random_wallpaper(self, widget=None, external=False):
        if self.files:
            idx = GLib.random_int_range(0, len(self.files))
            if self._apply_wallpaper(self.files[idx], notify=external):
                self._randomize_dice_icon()

    def _randomize_dice_icon(self):
        idx = GLib.random_int_range(0, len(_DICE_ICONS))
        self.random_btn.get_child().set_markup(_DICE_ICONS[idx])

    def _load_scheme(self):
        try:
            gfile = Gio.File.new_for_path(_SCHEME_FILE)
            if gfile.query_exists():
                ok, data, _ = gfile.load_contents(None)
                if ok:
                    scheme = data.decode('utf-8').strip()
                    for key, _ in _SCHEMES:
                        if key == scheme:
                            return scheme
        except Exception:
            pass
        return "scheme-tonal-spot"

    def _save_scheme(self, scheme):
        try:
            Gio.File.new_for_path(_SCHEME_FILE).replace_contents(
                scheme.encode('utf-8'), None, False,
                Gio.FileCreateFlags.REPLACE_DESTINATION, None
            )
        except Exception:
            pass

    def _on_destroy(self, widget):
        self._destroyed = True
        for source_id in (self._pending_search, self._pending_scroll):
            if source_id:
                GLib.source_remove(source_id)
        for source_id in self._pending_file_events.values():
            GLib.source_remove(source_id)
        self._pending_file_events.clear()
        self._load_queue.clear()
        self.files.clear()
        self.model.clear()