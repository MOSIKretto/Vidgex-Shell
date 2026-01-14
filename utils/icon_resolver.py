import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, GObject

import json, threading, pickle, lzma
from pathlib import Path
from collections import OrderedDict


class IconResolver(GObject.GObject):
    def __init__(self, default_icon="application-x-executable-symbolic"):
        super().__init__()
        self._default_icon = default_icon
        self._icon_cache = OrderedDict() # app_id -> icon_name
        self._desktop_cache = {}        # app_id -> path
        self._pixbuf_cache = {}         # (name, size) -> pixbuf
        self._pixbuf_usage = 0
        self._lock = threading.Lock()
        
        self._theme = Gtk.IconTheme.get_default()
        self._theme.connect('changed', self.clear_caches)
        
        cache_dir = Path(GLib.get_user_cache_dir()) / "vidgex-shell"
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._icon_cache_file = cache_dir / "icons.json"
        self._desktop_cache_file = cache_dir / "desktop_cache.lzma"
        
        threading.Thread(target=self._load_all, daemon=True).start()
        GLib.timeout_add_seconds(300, self._build_index)

    def _load_all(self):
        """Загрузка всех кэшей в одном потоке"""
        try:
            if self._icon_cache_file.exists():
                self._icon_cache.update(json.loads(self._icon_cache_file.read_text()))
            if self._desktop_cache_file.exists():
                with lzma.open(self._desktop_cache_file, 'rb') as f:
                    self._desktop_cache.update(pickle.load(f))
        except: pass
        self._build_index()

    def _build_index(self):
        """Быстрое построение индекса desktop-файлов через GLib"""
        new_index = {}
        paths = [Path(d) / "applications" for d in GLib.get_system_data_dirs()]
        paths.append(Path(GLib.get_user_data_dir()) / "applications")
        
        for p in filter(lambda x: x.exists(), paths):
            for entry in p.glob("*.desktop"):
                stem = entry.stem.lower()
                new_index[stem] = str(entry)
                # Добавляем варианты без дефисов для поиска
                if '-' in stem: new_index[stem.replace('-', '')] = str(entry)
        
        with self._lock: self._desktop_cache.update(new_index)
        return True

    def get_icon_name(self, app_id: str) -> str:
        app_id = app_id.lower()
        if app_id in self._icon_cache:
            self._icon_cache.move_to_end(app_id)
            return self._icon_cache[app_id]

        icon_name = self._resolve(app_id)
        with self._lock:
            self._icon_cache[app_id] = icon_name
            if len(self._icon_cache) > 200: self._icon_cache.popitem(last=False)
        
        GLib.idle_add(self._save_caches)
        return icon_name

    def _resolve(self, app_id: str) -> str:
        # 1. Тема / 2. Desktop файл
        if self._theme.has_icon(app_id): return app_id
        
        path = self._desktop_cache.get(app_id) or self._desktop_cache.get(app_id.replace('-', ''))
        if path:
            kf = GLib.KeyFile.new()
            try:
                kf.load_from_file(path, GLib.KeyFileFlags.NONE)
                icon = kf.get_string(GLib.KEY_FILE_DESKTOP_GROUP, "Icon")
                if icon and (self._theme.has_icon(icon) or Path(icon).exists()):
                    return icon
            except: pass
            
        return self._default_icon

    def get_icon_pixbuf(self, app_id: str, size: int = 32):
        name = self.get_icon_name(app_id)
        key = (name, size)
        
        if key in self._pixbuf_cache: return self._pixbuf_cache[key]

        try:
            pix = self._theme.load_icon(name, size, Gtk.IconLookupFlags.FORCE_SIZE)
        except:
            pix = self._theme.load_icon(self._default_icon, size, Gtk.IconLookupFlags.FORCE_SIZE)

        if pix:
            with self._lock:
                self._pixbuf_cache[key] = pix
                self._pixbuf_usage += pix.get_width() * pix.get_height() * 4
                if self._pixbuf_usage > 40 * 1024 * 1024: self.clear_caches(False)
        return pix

    def _save_caches(self):
        """Сохранение данных (вызывать через idle_add или отдельный поток)"""
        try:
            self._icon_cache_file.write_text(json.dumps(dict(self._icon_cache)))
            with lzma.open(self._desktop_cache_file, 'wb') as f:
                pickle.dump(self._desktop_cache, f)
        except: pass

    def clear_caches(self, full=True, *args):
        with self._lock:
            self._pixbuf_cache.clear()
            self._pixbuf_usage = 0
            if full: self._icon_cache.clear()

    def cleanup(self):
        self._save_caches()
        self.clear_caches()