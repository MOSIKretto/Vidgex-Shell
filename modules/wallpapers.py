from fabric.utils.helpers import exec_shell_command_async
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.entry import Entry
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk

import hashlib
import os
import random
import shutil
import json
from pathlib import Path
from PIL import Image
from functools import lru_cache
from typing import Optional, Dict, List
from concurrent.futures import ThreadPoolExecutor
import threading

import modules.icons as icons


class WallpaperSelector(Box):
    """Optimized wallpaper selector with caching and async loading."""
    
    # Class-level constants
    CACHE_DIR = Path(GLib.get_user_cache_dir()) / "ax-shell"
    THUMBS_DIR = CACHE_DIR / "thumbs"
    CONFIG_FILE = CACHE_DIR / "wallpaper_config.json"
    WALLPAPERS_DIR = Path.home() / "Wallpapers"
    CURRENT_WALL = Path.home() / ".current.wall"
    THUMBNAIL_SIZE = 96
    MAX_RECENT = 10
    IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"})
    
    SCHEMES: Dict[str, str] = {
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
            h_expand=False,
            v_expand=False,
            **kwargs,
        )
        
        self._init_directories()
        self._init_state()
        self._create_ui()
        self._load_wallpapers_async()
        self._setup_file_monitor()
        
        self.show_all()
        self._randomize_dice_icon()
        self.search_entry.grab_focus()

    def _init_directories(self):
        """Initialize required directories and cleanup old cache."""
        old_cache = self.CACHE_DIR / "wallpapers"
        if old_cache.exists():
            shutil.rmtree(old_cache)
        
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.THUMBS_DIR.mkdir(parents=True, exist_ok=True)
        self.WALLPAPERS_DIR.mkdir(parents=True, exist_ok=True)

    def _init_state(self):
        """Initialize state variables."""
        self.files: List[str] = []
        self.thumbnails: Dict[str, GdkPixbuf.Pixbuf] = {}
        self.selected_index = -1
        self.is_applying_scheme = False
        self.config = self._load_config()
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._load_lock = threading.Lock()

    def _load_config(self) -> Dict:
        """Load configuration with defaults."""
        defaults = {
            "current_wallpaper": None,
            "color_scheme": "scheme-tonal-spot",
            "recent_wallpapers": []
        }
        
        try:
            if self.CONFIG_FILE.exists():
                with open(self.CONFIG_FILE) as f:
                    config = json.load(f)
                return {**defaults, **config}
        except (json.JSONDecodeError, IOError) as e:
            print(f"Config load error: {e}")
        
        return defaults

    def _save_config(self):
        """Save configuration to file."""
        try:
            with open(self.CONFIG_FILE, 'w') as f:
                json.dump(self.config, f, indent=2)
        except IOError as e:
            print(f"Config save error: {e}")

    def _create_ui(self):
        """Create UI components."""
        # Model and IconView
        self.model = Gtk.ListStore(GdkPixbuf.Pixbuf, str)
        self.viewport = Gtk.IconView(
            name="wallpaper-icons",
            model=self.model,
            pixbuf_column=0,
            text_column=-1,
            item_width=0
        )
        self.viewport.connect("item-activated", self._on_wallpaper_activated)

        # Scrolled window
        self.scrolled_window = ScrolledWindow(
            name="scrolled-window",
            spacing=10,
            h_expand=True,
            v_expand=True,
            h_align="fill",
            v_align="fill",
            child=self.viewport,
            propagate_width=False,
            propagate_height=False,
        )
        self.scrolled_window.set_size_request(-1, 297)

        # Search entry
        self.search_entry = Entry(
            name="search-entry-walls",
            placeholder="Search Wallpapers...",
            h_expand=True,
            h_align="fill",
            notify_text=lambda e, *_: self._filter_viewport(e.get_text()),
            on_key_press_event=self._on_key_press,
        )
        self.search_entry.props.xalign = 0.5
        self.search_entry.connect("focus-out-event", self._on_focus_out)

        # Scheme dropdown
        self.scheme_dropdown = Gtk.ComboBoxText(name="scheme-dropdown")
        self.scheme_dropdown.set_tooltip_text("Select color scheme")
        for key, name in self.SCHEMES.items():
            self.scheme_dropdown.append(key, name)
        self.scheme_dropdown.set_active_id(self.config.get("color_scheme", "scheme-tonal-spot"))
        self.scheme_dropdown.connect("changed", self._on_scheme_changed)

        # Random button
        self.random_btn = Button(
            name="random-wall-button",
            child=Label(name="random-wall-label", markup=icons.dice_1),
            tooltip_text="Random Wallpaper",
        )
        self.random_btn.connect("clicked", self.set_random_wallpaper)

        # Layout
        header = Box(
            name="header-box",
            spacing=8,
            orientation="h",
            children=[self.random_btn, self.search_entry, self.scheme_dropdown],
        )
        self.add(header)
        self.pack_start(self.scrolled_window, True, True, 0)

    def _load_wallpapers_async(self):
        """Load wallpapers asynchronously."""
        self._executor.submit(self._load_wallpapers)

    def _load_wallpapers(self):
        """Load wallpapers from directory."""
        try:
            files = []
            for entry in self.WALLPAPERS_DIR.iterdir():
                if entry.is_file() and self._is_image(entry.name):
                    normalized = self._normalize_filename(entry)
                    if normalized:
                        files.append(normalized)
            
            with self._load_lock:
                self.files = sorted(files)
            
            # Load thumbnails in batches
            for filename in self.files:
                self._executor.submit(self._load_thumbnail, filename)
            
            # Apply saved config after loading
            GLib.idle_add(self._apply_saved_config)
            
        except OSError as e:
            print(f"Error loading wallpapers: {e}")

    def _normalize_filename(self, entry: Path) -> Optional[str]:
        """Normalize filename to lowercase with hyphens."""
        name = entry.name
        new_name = name.lower().replace(" ", "-")
        
        if new_name != name:
            try:
                entry.rename(self.WALLPAPERS_DIR / new_name)
                return new_name
            except OSError:
                return name
        return name

    def _load_thumbnail(self, filename: str):
        """Load or generate thumbnail for a file."""
        cache_path = self._get_cache_path(filename)
        full_path = self.WALLPAPERS_DIR / filename
        
        # Generate if not cached
        if not cache_path.exists():
            if not self._generate_thumbnail(full_path, cache_path):
                return
        
        # Load pixbuf
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(cache_path))
            with self._load_lock:
                self.thumbnails[filename] = pixbuf
            GLib.idle_add(self._add_to_model, pixbuf, filename)
        except GLib.Error as e:
            print(f"Thumbnail load error for {filename}: {e}")

    def _generate_thumbnail(self, source: Path, dest: Path) -> bool:
        """Generate thumbnail from source image."""
        try:
            with Image.open(source) as img:
                # Center crop to square
                w, h = img.size
                side = min(w, h)
                left, top = (w - side) // 2, (h - side) // 2
                
                img_cropped = img.crop((left, top, left + side, top + side))
                img_cropped.thumbnail(
                    (self.THUMBNAIL_SIZE, self.THUMBNAIL_SIZE),
                    Image.Resampling.LANCZOS
                )
                img_cropped.save(dest, "PNG", optimize=True)
                return True
        except Exception as e:
            print(f"Thumbnail generation error: {e}")
            return False

    def _add_to_model(self, pixbuf: GdkPixbuf.Pixbuf, filename: str):
        """Add thumbnail to model (must be called from main thread)."""
        self.model.append([pixbuf, filename])

    def _filter_viewport(self, query: str = ""):
        """Filter and display wallpapers matching query."""
        self.model.clear()
        query_lower = query.casefold()
        
        filtered = sorted(
            ((name, pb) for name, pb in self.thumbnails.items() 
             if query_lower in name.casefold()),
            key=lambda x: x[0].lower()
        )
        
        for name, pixbuf in filtered:
            self.model.append([pixbuf, name])
        
        # Update selection
        if not query.strip():
            self.viewport.unselect_all()
            self.selected_index = -1
        elif len(self.model) > 0:
            self._update_selection(0)

    @lru_cache(maxsize=256)
    def _get_cache_path(self, filename: str) -> Path:
        """Get cache path for filename."""
        file_hash = hashlib.md5(filename.encode()).hexdigest()
        return self.CACHE_DIR / f"{file_hash}.png"

    @staticmethod
    def _is_image(filename: str) -> bool:
        """Check if file is a supported image."""
        return Path(filename).suffix.lower() in WallpaperSelector.IMAGE_EXTENSIONS

    def _apply_saved_config(self):
        """Apply saved wallpaper configuration."""
        saved = self.config.get("current_wallpaper")
        if saved and Path(saved).exists():
            name = Path(saved).name
            if name in self.files:
                self._apply_wallpaper(name, notify=False)

    def _apply_wallpaper(self, filename: str, notify: bool = False) -> bool:
        """Apply wallpaper and color scheme."""
        if filename not in self.files:
            return False
        
        full_path = self.WALLPAPERS_DIR / filename
        scheme = self.scheme_dropdown.get_active_id()
        
        # Update symlink
        try:
            if self.CURRENT_WALL.exists() or self.CURRENT_WALL.is_symlink():
                self.CURRENT_WALL.unlink()
            self.CURRENT_WALL.symlink_to(full_path)
        except OSError as e:
            print(f"Symlink error: {e}")
            return False
        
        # Apply wallpaper
        exec_shell_command_async(
            f'awww img "{full_path}" -t --transition-duration 1.5 '
            f'--transition-step 255 --transition-fps 60 -f Nearest'
        )
        
        # Apply color scheme
        exec_shell_command_async(f'matugen image "{full_path}" -t {scheme}')
        
        # Update config
        self.config["current_wallpaper"] = str(full_path)
        self.config["color_scheme"] = scheme
        
        # Update recent list
        recent = self.config.get("recent_wallpapers", [])
        if str(full_path) in recent:
            recent.remove(str(full_path))
        recent.insert(0, str(full_path))
        self.config["recent_wallpapers"] = recent[:self.MAX_RECENT]
        
        self._save_config()
        
        if notify:
            exec_shell_command_async(
                f"notify-send '🎲 Wallpaper' 'Random wallpaper set 🎨' "
                f"-a 'Ax-Shell' -i '{full_path}' -e"
            )
        
        return True

    def _on_wallpaper_activated(self, iconview, path):
        """Handle wallpaper selection."""
        filename = self.model[path][1]
        self._apply_wallpaper(filename)

    def _on_scheme_changed(self, widget):
        """Handle scheme change."""
        if self.is_applying_scheme:
            return
        
        scheme = widget.get_active_id()
        if not scheme:
            return
        
        self.is_applying_scheme = True
        try:
            self.config["color_scheme"] = scheme
            self._save_config()
            
            if self.CURRENT_WALL.is_symlink():
                actual = self.CURRENT_WALL.resolve()
                if actual.exists():
                    exec_shell_command_async(f'matugen image "{actual}" -t {scheme}')
        finally:
            self.is_applying_scheme = False

    def set_random_wallpaper(self, widget=None, external: bool = False):
        """Set a random wallpaper."""
        if not self.files:
            return
        
        filename = random.choice(self.files)
        if self._apply_wallpaper(filename, notify=external):
            self._randomize_dice_icon()

    def _randomize_dice_icon(self):
        """Randomize dice button icon."""
        label = self.random_btn.get_child()
        if isinstance(label, Label):
            label.set_markup(random.choice(self.DICE_ICONS))

    def _setup_file_monitor(self):
        """Setup directory monitoring."""
        try:
            gfile = Gio.File.new_for_path(str(self.WALLPAPERS_DIR))
            self.file_monitor = gfile.monitor_directory(Gio.FileMonitorFlags.NONE, None)
            self.file_monitor.connect("changed", self._on_dir_changed)
        except GLib.Error as e:
                        print(f"File monitor error: {e}")

    def _on_dir_changed(self, monitor, file, other_file, event_type):
        """Handle directory changes."""
        filename = file.get_basename()
        
        if event_type == Gio.FileMonitorEvent.DELETED:
            self._handle_file_deleted(filename)
        elif event_type == Gio.FileMonitorEvent.CREATED:
            self._handle_file_created(filename)
        elif event_type == Gio.FileMonitorEvent.CHANGED:
            self._handle_file_changed(filename)

    def _handle_file_deleted(self, filename: str):
        """Handle deleted file."""
        if filename not in self.files:
            return
        
        with self._load_lock:
            self.files.remove(filename)
            self.thumbnails.pop(filename, None)
        
        # Remove cached thumbnail
        cache_path = self._get_cache_path(filename)
        if cache_path.exists():
            try:
                cache_path.unlink()
            except OSError:
                pass
        
        GLib.idle_add(self._filter_viewport, self.search_entry.get_text())

    def _handle_file_created(self, filename: str):
        """Handle created file."""
        if not self._is_image(filename):
            return
        
        # Normalize filename
        full_path = self.WALLPAPERS_DIR / filename
        new_name = filename.lower().replace(" ", "-")
        
        if new_name != filename:
            try:
                new_path = self.WALLPAPERS_DIR / new_name
                full_path.rename(new_path)
                filename = new_name
            except OSError:
                pass
        
        if filename in self.files:
            return
        
        with self._load_lock:
            self.files.append(filename)
            self.files.sort()
        
        # Load thumbnail async
        self._executor.submit(self._load_thumbnail, filename)

    def _handle_file_changed(self, filename: str):
        """Handle changed file."""
        if not self._is_image(filename) or filename not in self.files:
            return
        
        # Clear cache and reload
        cache_path = self._get_cache_path(filename)
        if cache_path.exists():
            try:
                cache_path.unlink()
            except OSError:
                pass
        
        # Clear from lru_cache
        self._get_cache_path.cache_clear()
        
        with self._load_lock:
            self.thumbnails.pop(filename, None)
        
        self._executor.submit(self._load_thumbnail, filename)

    def _on_key_press(self, widget, event):
        """Handle key press events."""
        state = event.state
        key = event.keyval
        
        # Shift + Arrow keys for scheme navigation
        if state & Gdk.ModifierType.SHIFT_MASK:
            return self._handle_scheme_navigation(key)
        
        # Arrow keys for wallpaper navigation
        if key in (Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Left, Gdk.KEY_Right):
            self._move_selection(key)
            return True
        
        # Enter to apply wallpaper
        if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if self.selected_index >= 0:
                path = Gtk.TreePath.new_from_indices([self.selected_index])
                self._on_wallpaper_activated(self.viewport, path)
            return True
        
        return False

    def _handle_scheme_navigation(self, key: int) -> bool:
        """Handle scheme dropdown navigation with Shift+Arrow."""
        schemes_list = list(self.SCHEMES.keys())
        current_id = self.scheme_dropdown.get_active_id()
        
        try:
            current_idx = schemes_list.index(current_id) if current_id in schemes_list else 0
        except ValueError:
            current_idx = 0
        
        if key == Gdk.KEY_Up:
            new_idx = (current_idx - 1) % len(schemes_list)
            self.scheme_dropdown.set_active(new_idx)
            return True
        elif key == Gdk.KEY_Down:
            new_idx = (current_idx + 1) % len(schemes_list)
            self.scheme_dropdown.set_active(new_idx)
            return True
        elif key == Gdk.KEY_Right:
            self.scheme_dropdown.popup()
            return True
        
        return False

    def _move_selection(self, key: int):
        """Move selection in 2D grid."""
        total = len(self.model)
        if total == 0:
            return
        
        columns = self._get_columns_count(total)
        current = self.selected_index
        new_idx = current
        
        if current == -1:
            # No selection - select first or last based on direction
            if key in (Gdk.KEY_Down, Gdk.KEY_Right):
                new_idx = 0
            else:
                new_idx = total - 1
        else:
            if key == Gdk.KEY_Up:
                new_idx = current - columns if current >= columns else current
            elif key == Gdk.KEY_Down:
                new_idx = current + columns if current + columns < total else current
            elif key == Gdk.KEY_Left:
                new_idx = current - 1 if current > 0 and current % columns != 0 else current
            elif key == Gdk.KEY_Right:
                new_idx = current + 1 if current < total - 1 and (current + 1) % columns != 0 else current
        
        if 0 <= new_idx < total and new_idx != self.selected_index:
            self._update_selection(new_idx)

    def _get_columns_count(self, total: int) -> int:
        """Calculate number of columns in icon view."""
        columns = self.viewport.get_columns()
        
        if columns <= 0 and total > 0:
            try:
                first_path = Gtk.TreePath.new_from_indices([0])
                base_row = self.viewport.get_item_row(first_path)
                
                for i in range(1, total):
                    path = Gtk.TreePath.new_from_indices([i])
                    if self.viewport.get_item_row(path) > base_row:
                        return max(1, i)
                
                return total
            except Exception:
                return 1
        
        return max(1, columns)

    def _update_selection(self, index: int):
        """Update viewport selection."""
        self.viewport.unselect_all()
        path = Gtk.TreePath.new_from_indices([index])
        self.viewport.select_path(path)
        self.viewport.scroll_to_path(path, False, 0.5, 0.5)
        self.selected_index = index

    def _on_focus_out(self, widget, event):
        """Keep focus on search entry when widget is visible."""
        if self.get_mapped():
            widget.grab_focus()
        return False

    def cleanup(self):
        """Cleanup resources."""
        self._executor.shutdown(wait=False)
        if hasattr(self, 'file_monitor'):
            self.file_monitor.cancel()

    def __del__(self):
        """Destructor to ensure cleanup."""
        self.cleanup()
