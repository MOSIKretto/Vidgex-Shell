from fabric.utils.helpers import exec_shell_command_async
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.entry import Entry
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk

import random
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import threading

import modules.icons as icons

# Ленивый импорт PIL
_PIL_Image = None

def _get_pil_image():
    global _PIL_Image
    if _PIL_Image is None:
        from PIL import Image
        _PIL_Image = Image
    return _PIL_Image


class WallpaperSelector(Box):
    __slots__ = (
        'files', 'selected_index', 'is_applying_scheme', '_executor',
        '_load_lock', '_pending_search', '_pending_scroll', '_loading_set',
        'model', 'viewport', 'scrolled_window', 'search_entry',
        'scheme_dropdown', 'random_btn', 'file_monitor', '_last_query',
        '_schemes_tuple', '_visible_range'
    )
    
    WALLPAPERS_DIR = Path.home() / "Wallpapers"
    CURRENT_WALL = Path.home() / ".current.wall"
    SCHEME_FILE = Path(GLib.get_user_cache_dir()) / "vidgex-shell" / "scheme"
    THUMBNAIL_SIZE = 96
    IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"})
    
    DEBOUNCE_SEARCH_MS = 150
    DEBOUNCE_SCROLL_MS = 80
    MAX_WORKERS = 1
    BATCH_LOAD_SIZE = 6
    
    SCHEMES = {
        "scheme-tonal-spot": "Tonal Spot",
        "scheme-content": "Content",
        "scheme-expressive": "Expressive",
        "scheme-fidelity": "Fidelity",
        "scheme-fruit-salad": "Fruit Salad",
        "scheme-monochrome": "Monochrome",
        "scheme-neutral": "Neutral",
        "scheme-rainbow": "Rainbow",
    }
    
    DICE_ICONS = (
        icons.dice_1, icons.dice_2, icons.dice_3,
        icons.dice_4, icons.dice_5, icons.dice_6,
    )

    def __init__(self, **kwargs):
        super().__init__(
            name="wallpapers",
            spacing=4,
            orientation="v",
            **kwargs,
        )
        
        wd = self.WALLPAPERS_DIR
        if not wd.exists():
            wd.mkdir(parents=True, exist_ok=True)
        
        sf_parent = self.SCHEME_FILE.parent
        if not sf_parent.exists():
            sf_parent.mkdir(parents=True, exist_ok=True)
        
        self.files = []
        self.selected_index = -1
        self.is_applying_scheme = False
        self._load_lock = threading.Lock()
        self._loading_set = set()
        self._last_query = ""
        self._schemes_tuple = tuple(self.SCHEMES.keys())
        self._visible_range = (0, 0)
        self._pending_search = None
        self._pending_scroll = None
        self._executor = None
        
        self._create_ui()
        GLib.idle_add(self._deferred_init, priority=GLib.PRIORITY_LOW)

    def _get_executor(self):
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=self.MAX_WORKERS)
        return self._executor

    def _deferred_init(self):
        self._get_executor().submit(self._load_wallpapers)
        self._setup_file_monitor()
        self.show_all()
        self._randomize_dice_icon()
        self.search_entry.grab_focus()
        return False

    def _load_scheme(self):
        try:
            sf = self.SCHEME_FILE
            if sf.exists():
                scheme = sf.read_text(encoding='utf-8').strip()
                if scheme in self.SCHEMES:
                    return scheme
        except Exception:
            pass
        return "scheme-tonal-spot"

    def _save_scheme(self, scheme):
        try:
            self.SCHEME_FILE.write_text(scheme, encoding='utf-8')
        except Exception:
            pass

    def _create_ui(self):
        self.model = Gtk.ListStore(GdkPixbuf.Pixbuf, str)
        
        self.viewport = Gtk.IconView(
            name="wallpaper-icons",
            model=self.model,
            pixbuf_column=0,
            text_column=-1,
            item_width=0
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
            notify_text=lambda e, *_: self._debounced_filter(e.get_text()),
            on_key_press_event=self._on_key_press,
        )
        self.search_entry.props.xalign = 0.5
        self.search_entry.connect("focus-out-event", self._on_focus_out)

        self.scheme_dropdown = Gtk.ComboBoxText(name="scheme-dropdown")
        self.scheme_dropdown.set_tooltip_text("Select color scheme")
        for key, name in self.SCHEMES.items():
            self.scheme_dropdown.append(key, name)
        self.scheme_dropdown.set_active_id(self._load_scheme())
        self.scheme_dropdown.connect("changed", self._on_scheme_changed)

        self.random_btn = Button(
            name="random-wall-button",
            child=Label(name="random-wall-label", markup=icons.dice_1),
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

    def _load_wallpapers(self):
        extensions = self.IMAGE_EXTENSIONS
        wallpapers_dir = self.WALLPAPERS_DIR
        files = []
        
        try:
            for entry in wallpapers_dir.iterdir():
                if entry.is_file() and entry.suffix.lower() in extensions:
                    normalized = self._normalize_filename(entry)
                    if normalized:
                        files.append(normalized)
        except Exception:
            pass

        files.sort()
        
        with self._load_lock:
            self.files = files

        GLib.idle_add(self._populate_model, priority=GLib.PRIORITY_DEFAULT_IDLE)

    def _populate_model(self):
        model = self.model
        self.viewport.set_model(None)
        model.clear()
        
        for filename in self.files:
            model.append([None, filename])
        
        self.viewport.set_model(model)
        GLib.idle_add(self._load_initial_thumbnails, priority=GLib.PRIORITY_LOW)
        return False

    def _load_initial_thumbnails(self):
        files = self.files
        if files:
            self._load_thumbnail_batch(files[:self.BATCH_LOAD_SIZE])
        return False

    def _load_thumbnail_batch(self, filenames):
        loading = self._loading_set
        executor = self._get_executor()
        
        for filename in filenames:
            if filename not in loading:
                loading.add(filename)
                executor.submit(self._load_thumbnail, filename)

    def _normalize_filename(self, entry):
        name = entry.name
        new_name = name.lower().replace(" ", "-")
        if new_name != name:
            new_path = self.WALLPAPERS_DIR / new_name
            if not new_path.exists():
                try:
                    entry.rename(new_path)
                    return new_name
                except OSError:
                    pass
        return name

    def _load_thumbnail(self, filename):
        try:
            full_path = self.WALLPAPERS_DIR / filename
            if not full_path.exists():
                return
            
            pixbuf = self._generate_thumbnail(full_path)
            if pixbuf:
                GLib.idle_add(
                    self._update_thumbnail, 
                    filename, 
                    pixbuf,
                    priority=GLib.PRIORITY_LOW
                )
        except Exception:
            pass
        finally:
            self._loading_set.discard(filename)

    def _update_thumbnail(self, filename, pixbuf):
        model = self.model
        iter_ = model.get_iter_first()
        
        while iter_:
            if model.get_value(iter_, 1) == filename:
                model.set_value(iter_, 0, pixbuf)
                break
            iter_ = model.iter_next(iter_)
        
        return False

    def _generate_thumbnail(self, source):
        Image = _get_pil_image()
        thumb_size = self.THUMBNAIL_SIZE
        
        try:
            with Image.open(source) as img:
                w, h = img.size
                
                # Быстрое предварительное уменьшение для больших изображений
                max_dim = max(w, h)
                if max_dim > thumb_size * 4:
                    ratio = (thumb_size * 2) / max_dim
                    img = img.resize((int(w * ratio), int(h * ratio)), Image.Resampling.NEAREST)
                    w, h = img.size
                
                # Центрированная обрезка
                side = min(w, h)
                left = (w - side) >> 1
                top = (h - side) >> 1
                
                cropped = img.crop((left, top, left + side, top + side))
                
                if cropped.size[0] != thumb_size:
                    cropped = cropped.resize((thumb_size, thumb_size), Image.Resampling.BILINEAR)
                
                if cropped.mode != 'RGB':
                    cropped = cropped.convert('RGB')
                
                data = cropped.tobytes()
                width, height = cropped.size
                
                return GdkPixbuf.Pixbuf.new_from_data(
                    data,
                    GdkPixbuf.Colorspace.RGB,
                    False,
                    8,
                    width,
                    height,
                    width * 3
                )
        except Exception:
            return None

    def _debounced_filter(self, query):
        if query == self._last_query:
            return
        
        if self._pending_search:
            GLib.source_remove(self._pending_search)
        
        self._pending_search = GLib.timeout_add(
            self.DEBOUNCE_SEARCH_MS, 
            self._do_filter, 
            query
        )

    def _do_filter(self, query):
        self._pending_search = None
        self._last_query = query
        self._filter_viewport(query)
        return False

    def _filter_viewport(self, query=""):
        model = self.model
        files = self.files
        
        self.viewport.set_model(None)
        model.clear()
        
        if query:
            query_lower = query.casefold()
            for name in files:
                if query_lower in name.casefold():
                    model.append([None, name])
        else:
            for name in files:
                model.append([None, name])
        
        self.viewport.set_model(model)
        
        # Загружаем миниатюры для отфильтрованных
        visible = []
        iter_ = model.get_iter_first()
        count = 0
        while iter_ and count < self.BATCH_LOAD_SIZE:
            visible.append(model.get_value(iter_, 1))
            iter_ = model.iter_next(iter_)
            count += 1
        
        if visible:
            self._load_thumbnail_batch(visible)
        
        if not query.strip():
            self.viewport.unselect_all()
            self.selected_index = -1
        elif len(model) > 0:
            self._update_selection(0)

    def _apply_wallpaper(self, filename, notify=False):
        if filename not in self.files:
            return False
        
        full_path = self.WALLPAPERS_DIR / filename
        scheme = self.scheme_dropdown.get_active_id()
        
        try:
            current = self.CURRENT_WALL
            if current.exists() or current.is_symlink():
                current.unlink()
            current.symlink_to(full_path)
        except OSError:
            return False

        path_str = str(full_path)
        
        exec_shell_command_async(
            f'awww img "{path_str}" --type outer --transition-duration 0.5 '
            f'--transition-step 255 --transition-fps 60'
        )
        exec_shell_command_async(f'matugen image "{path_str}" --type {scheme}')

        if notify:
            exec_shell_command_async(
                f"notify-send '🎲 Wallpaper' 'Random wallpaper set 🎨' "
                f"-a 'Vidgex-Shell' -i '{path_str}' -e"
            )
        return True

    def _on_wallpaper_activated(self, iconview, path):
        try:
            filename = self.model[path][1]
            self._apply_wallpaper(filename)
        except (IndexError, TypeError):
            pass

    def _on_scheme_changed(self, widget):
        if self.is_applying_scheme:
            return
        
        scheme = widget.get_active_id()
        if not scheme:
            return
        
        self.is_applying_scheme = True
        try:
            self._save_scheme(scheme)
            current = self.CURRENT_WALL
            if current.is_symlink():
                try:
                    actual = current.resolve()
                    if actual.exists():
                        exec_shell_command_async(f'matugen image "{actual}" -t {scheme}')
                except OSError:
                    pass
        finally:
            self.is_applying_scheme = False

    def set_random_wallpaper(self, widget=None, external=False):
        files = self.files
        if files:
            filename = random.choice(files)
            if self._apply_wallpaper(filename, notify=external):
                self._randomize_dice_icon()

    def _randomize_dice_icon(self):
        label = self.random_btn.get_child()
        if isinstance(label, Label):
            label.set_markup(random.choice(self.DICE_ICONS))

    def _setup_file_monitor(self):
        gfile = Gio.File.new_for_path(str(self.WALLPAPERS_DIR))
        self.file_monitor = gfile.monitor_directory(Gio.FileMonitorFlags.NONE, None)
        self.file_monitor.connect("changed", self._on_dir_changed)
        
        vadj = self.scrolled_window.get_vadjustment()
        vadj.connect("value-changed", self._on_scroll_debounced)

    def _on_scroll_debounced(self, adjustment):
        if self._pending_scroll:
            GLib.source_remove(self._pending_scroll)
        
        self._pending_scroll = GLib.timeout_add(
            self.DEBOUNCE_SCROLL_MS, 
            self._on_scroll, 
            adjustment
        )

    def _on_scroll(self, adjustment):
        self._pending_scroll = None
        
        files = self.files
        total = len(files)
        if total == 0:
            return False
        
        value = adjustment.get_value()
        page_size = adjustment.get_page_size()
        upper = adjustment.get_upper()
        
        if upper <= 0:
            return False
        
        progress = value / upper
        visible_count = int(page_size / self.THUMBNAIL_SIZE * 5)
        
        first_idx = max(0, int(progress * total) - 2)
        last_idx = min(total, first_idx + visible_count + 4)
        
        if abs(first_idx - self._visible_range[0]) < 3:
            return False
        
        self._visible_range = (first_idx, last_idx)
        
        batch = []
        loading = self._loading_set
        
        for i in range(first_idx, min(last_idx, first_idx + self.BATCH_LOAD_SIZE)):
            if i < total:
                filename = files[i]
                if filename not in loading:
                    batch.append(filename)
        
        if batch:
            self._load_thumbnail_batch(batch)
        
        return False

    def _on_dir_changed(self, monitor, file, other_file, event_type):
        filename = file.get_basename()
        
        if event_type == Gio.FileMonitorEvent.DELETED:
            with self._load_lock:
                try:
                    self.files.remove(filename)
                except ValueError:
                    pass
            GLib.idle_add(
                self._filter_viewport, 
                self.search_entry.get_text(),
                priority=GLib.PRIORITY_LOW
            )
            
        elif event_type in (Gio.FileMonitorEvent.CREATED, Gio.FileMonitorEvent.CHANGED):
            suffix = Path(filename).suffix.lower()
            if suffix not in self.IMAGE_EXTENSIONS:
                return
                
            full_path = self.WALLPAPERS_DIR / filename
            new_name = filename.lower().replace(" ", "-")
            
            if new_name != filename:
                new_path = self.WALLPAPERS_DIR / new_name
                if not new_path.exists():
                    try:
                        full_path.rename(new_path)
                        filename = new_name
                    except OSError:
                        pass
            
            with self._load_lock:
                if filename not in self.files:
                    self.files.append(filename)
                    self.files.sort()
            
            loading = self._loading_set
            if filename not in loading:
                loading.add(filename)
                self._get_executor().submit(self._load_thumbnail, filename)

    def _on_key_press(self, widget, event):
        state = event.state
        key = event.keyval
        
        if state & Gdk.ModifierType.SHIFT_MASK:
            return self._handle_scheme_navigation(key)
        
        if key in (Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Right):
            self._move_selection(key)
            return True
        
        if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            idx = self.selected_index
            if idx >= 0:
                path = Gtk.TreePath.new_from_indices([idx])
                self._on_wallpaper_activated(self.viewport, path)
            return True
        
        return False

    def _handle_scheme_navigation(self, key):
        schemes = self._schemes_tuple
        current_id = self.scheme_dropdown.get_active_id()
        
        try:
            current_idx = schemes.index(current_id)
        except ValueError:
            current_idx = 0
            
        n = len(schemes)
        
        if key == Gdk.KEY_Up:
            self.scheme_dropdown.set_active((current_idx - 1) % n)
            return True
        elif key == Gdk.KEY_Down:
            self.scheme_dropdown.set_active((current_idx + 1) % n)
            return True
        elif key == Gdk.KEY_Right:
            self.scheme_dropdown.popup()
            return True
        return False

    def _move_selection(self, key):
        model = self.model
        total = len(model)
        if total == 0:
            return
            
        columns = self._get_columns_count()
        current = self.selected_index
        
        if current == -1:
            new_idx = 0 if key in (Gdk.KEY_Down, Gdk.KEY_Right) else total - 1
        elif key == Gdk.KEY_Up:
            new_idx = current - columns if current >= columns else current
        elif key == Gdk.KEY_Down:
            new_idx = current + columns if current + columns < total else current
        elif key == Gdk.KEY_Left:
            new_idx = current - 1 if current > 0 and current % columns != 0 else current
        elif key == Gdk.KEY_Right:
            new_idx = current + 1 if current < total - 1 and (current + 1) % columns != 0 else current
        else:
            return
        
        if 0 <= new_idx < total and new_idx != self.selected_index:
            self._update_selection(new_idx)

    def _get_columns_count(self):
        columns = self.viewport.get_columns()
        if columns > 0:
            return columns
        
        model = self.model
        total = len(model)
        if total == 0:
            return 1
        
        viewport = self.viewport
        first_path = Gtk.TreePath.new_from_indices([0])
        base_row = viewport.get_item_row(first_path)
        
        for i in range(1, min(total, 12)):
            path = Gtk.TreePath.new_from_indices([i])
            if viewport.get_item_row(path) > base_row:
                return max(1, i)
        
        return min(total, 6)

    def _update_selection(self, index):
        viewport = self.viewport
        viewport.unselect_all()
        path = Gtk.TreePath.new_from_indices([index])
        viewport.select_path(path)
        viewport.scroll_to_path(path, False, 0.5, 0.5)
        self.selected_index = index

    def _on_focus_out(self, widget, event):
        if self.get_mapped():
            widget.grab_focus()
        return False