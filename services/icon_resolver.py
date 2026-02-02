import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, GObject, GdkPixbuf

from fabric.utils.helpers import get_desktop_applications


class IconResolver(GObject.GObject):
    __slots__ = (
        'default_icon', 'theme', '_desktop_dirs',
        '_icon_dirs', '_apps', '_app_map', '_initialized'
    )
    
    _instance = None
    _SUFFIXES = frozenset({'.bin', '.exe', '.so', '-bin', '-gtk', '-qt', '-wayland', '-x11', '-wrapped'})
    _EXTENSIONS = ('.png', '.svg', '.xpm', '.ico')
    _PREFIXES = ('org.', 'com.', 'net.', 'io.', 'dev.', 'app.')
    _NAME_SUFFIXES = ('-desktop', '-client', '-browser', '-app')
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, default_icon="application-x-executable-symbolic"):
        if self._initialized:
            return
        
        super().__init__()
        self.default_icon = default_icon
        self.theme = Gtk.IconTheme.get_default()
        self._desktop_dirs = tuple(self._scan_dirs("applications"))
        self._icon_dirs = tuple(self._scan_icon_dirs())
        self._apps = ()
        self._app_map = {}
        self._initialized = True
    
    @classmethod
    def get_default(cls):
        return cls()
    
    @staticmethod
    def _join(*parts):
        return '/'.join(p.strip('/') for p in parts if p)
    
    @staticmethod
    def _listdir(path):
        try:
            d = GLib.Dir.open(path, 0)
            result = []
            while (name := d.read_name()):
                result.append(name)
            return result
        except Exception:
            return []
    
    def _scan_dirs(self, subdir):
        dirs = []
        join, test = self._join, GLib.file_test
        is_dir = GLib.FileTest.IS_DIR
        
        for base in GLib.get_system_data_dirs():
            if (d := join(base, subdir)) and test(d, is_dir):
                dirs.append(d)
        
        if (user := GLib.get_user_data_dir()) and (d := join(user, subdir)) and test(d, is_dir):
            dirs.append(d)
        
        home = GLib.get_home_dir()
        for extra in (
            join(home, ".local/share/flatpak/exports/share", subdir),
            join("/var/lib/flatpak/exports/share", subdir),
            "/var/lib/snapd/desktop/applications" if subdir == "applications" else None
        ):
            if extra and test(extra, is_dir):
                dirs.append(extra)
        
        return dirs
    
    def _scan_icon_dirs(self):
        dirs = []
        test = GLib.file_test
        is_dir = GLib.FileTest.IS_DIR
        
        for d in ("/usr/share/pixmaps", "/usr/local/share/pixmaps"):
            if test(d, is_dir):
                dirs.append(d)
        
        dirs.extend(self._scan_dirs("icons"))
        return dirs
    
    def refresh(self):
        self._apps = tuple(get_desktop_applications())
        self._app_map = self._build_app_map()
    
    def _build_app_map(self):
        m = {}
        norm = self.norm_name
        
        for app in self._apps:
            keys = filter(None, (
                app.name,
                app.display_name,
                app.window_class,
                app.executable.rsplit("/", 1)[-1] if app.executable else None,
                app.command_line.split(maxsplit=1)[0].rsplit("/", 1)[-1] if app.command_line else None
            ))
            
            for raw in keys:
                k = raw.lower()
                if k not in m:
                    m[k] = app
                    nk = norm(k)
                    if nk != k and nk not in m:
                        m[nk] = app
        return m
    
    def norm_name(self, name):
        if not name:
            return ""
        n = name.lower().strip()
        for s in self._SUFFIXES:
            if n.endswith(s):
                return n[:-len(s)]
        return n
    
    def find_app(self, app_id):
        if not app_id:
            return None
        
        if not self._apps:
            self.refresh()
        
        al = app_id.lower()
        
        if (app := self._app_map.get(al)):
            return app
        
        norm = self.norm_name(al)
        if norm != al and (app := self._app_map.get(norm)):
            return app
        
        if "-" in al:
            base = al.split("-")[0]
            if (app := self._app_map.get(base)):
                return app
        
        for a in self._apps:
            if al in (
                (a.window_class or "").lower(),
                (a.name or "").lower(),
                (a.display_name or "").lower(),
                (a.executable or "").rsplit("/", 1)[-1].lower()
            ):
                return a
        return None
    
    def get_icon(self, app_id, size=24, app=None):
        return self._resolve_icon(app_id, size, app)
    
    @property
    def apps(self):
        if not self._apps:
            self.refresh()
        return self._apps
    
    @property
    def app_map(self):
        if not self._app_map:
            self.refresh()
        return self._app_map
    
    def _gen_variants(self, app_id):
        if not app_id:
            return ()
        
        v = {app_id, app_id.lower()}
        al = app_id.lower()
        v.add(self.norm_name(al))
        
        v.update((
            al.replace('-', '_'),
            al.replace('_', '-'),
            al.replace('.', '-'),
            al.replace('.', '_')
        ))
        
        if '.' in al:
            parts = al.split('.')
            v.add(parts[-1])
            if len(parts) > 2:
                v.add('.'.join(parts[-2:]))
                v.add('.'.join(parts[1:]))
        
        if '-' in al:
            parts = al.split('-')
            v.add(parts[0])
            v.add(parts[-1])
            if len(parts) > 1:
                v.add('-'.join(parts[:2]))
                v.add('-'.join(parts[:-1]))
        
        for prefix in self._PREFIXES:
            if al.startswith(prefix):
                v.add(al[len(prefix):])
                break
        
        for suffix in self._NAME_SUFFIXES:
            if al.endswith(suffix):
                v.add(al[:-len(suffix)])
                break
        
        parts = []
        start = 0
        for i in range(1, len(app_id)):
            if app_id[i].isupper() and (not app_id[i-1].isupper() or
                (i + 1 < len(app_id) and not app_id[i+1].isupper())):
                parts.append(app_id[start:i])
                start = i
        if parts:
            parts.append(app_id[start:])
            v.add(parts[0].lower())
            v.add('-'.join(p.lower() for p in parts))
        
        return tuple(x for x in v if x and len(x) > 1)
    
    def _find_desktop(self, app_id):
        variants = self._gen_variants(app_id)
        if not variants:
            return None
        
        names = set()
        for v in variants:
            vl = v.lower()
            names.update((f"{vl}.desktop", f"org.{vl}.desktop", f"com.{vl}.desktop"))
            if '.' not in vl:
                names.add(f"org.{vl}.{vl}.desktop")
        
        join = self._join
        
        for ddir in self._desktop_dirs:
            files = self._listdir(ddir)
            if not files:
                continue
            
            lower_map = {f.lower(): f for f in files}
            
            for name in names:
                if (orig := lower_map.get(name.lower())):
                    return join(ddir, orig)
            
            for v in variants:
                vl = v.lower()
                for fl, fo in lower_map.items():
                    if fl.endswith('.desktop') and vl in fl:
                        return join(ddir, fo)
        
        return None
    
    @staticmethod
    def _read_icon(path):
        try:
            kf = GLib.KeyFile.new()
            kf.load_from_file(path, GLib.KeyFileFlags.NONE)
            icon = kf.get_string(GLib.KEY_FILE_DESKTOP_GROUP, "Icon")
            return icon.strip() if icon else None
        except Exception:
            return None
    
    def _find_icon_file(self, app_id):
        variants = self._gen_variants(app_id)
        if not variants:
            return None
        
        targets = frozenset(f"{v.lower()}{ext}" for v in variants for ext in self._EXTENSIONS)
        join, test = self._join, GLib.file_test
        is_dir = GLib.FileTest.IS_DIR
        
        for idir in self._icon_dirs:
            if 'pixmaps' in idir:
                for f in self._listdir(idir):
                    if f.lower() in targets:
                        return join(idir, f)
            else:
                stack = [(idir, 0)]
                while stack:
                    cur, depth = stack.pop()
                    if depth > 3:
                        continue
                    for name in self._listdir(cur):
                        full = join(cur, name)
                        if test(full, is_dir):
                            stack.append((full, depth + 1))
                        elif name.lower() in targets:
                            return full
        
        return None
    
    def _get_icon_name(self, app_id):
        if not app_id:
            return self.default_icon
        
        app_id = app_id.strip()
        has = self.theme.has_icon
        
        if has(app_id):
            return app_id
        
        al = app_id.lower()
        if has(al):
            return al
        
        for v in self._gen_variants(app_id):
            if has(v):
                return v
        
        if (desktop := self._find_desktop(app_id)):
            if (icon := self._read_icon(desktop)):
                if icon.startswith('/') and GLib.file_test(icon, GLib.FileTest.EXISTS):
                    return icon
                if has(icon):
                    return icon
                if has(icon.lower()):
                    return icon.lower()
                if (found := self._find_icon_file(icon)):
                    return found
        
        if (found := self._find_icon_file(app_id)):
            return found
        
        return self.default_icon
    
    def _load_pixbuf(self, icon_name, size):
        try:
            if icon_name.startswith('/'):
                return GdkPixbuf.Pixbuf.new_from_file_at_scale(icon_name, size, size, True)
            return self.theme.load_icon(icon_name, size, Gtk.IconLookupFlags.FORCE_SIZE)
        except Exception:
            return None
    
    def _resolve_icon(self, app_id, size, desktop_app=None):
        pixbuf = None
        
        if desktop_app:
            try:
                pixbuf = desktop_app.get_icon_pixbuf(size=size)
            except Exception:
                pass
        
        if not pixbuf and app_id:
            icon_name = self._get_icon_name(app_id)
            pixbuf = self._load_pixbuf(icon_name, size)
        
        if not pixbuf and app_id and "-" in app_id:
            for base in (app_id.split("-")[0], app_id.rsplit("-", 1)[0]):
                if base and len(base) > 1:
                    icon_name = self._get_icon_name(base)
                    if (pixbuf := self._load_pixbuf(icon_name, size)):
                        break
        
        if not pixbuf:
            for name in (self.default_icon, "image-missing"):
                if (pixbuf := self._load_pixbuf(name, size)):
                    break
        
        if pixbuf and (pixbuf.get_width() != size or pixbuf.get_height() != size):
            pixbuf = pixbuf.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
        
        return pixbuf
    
    def get_icon_pixbuf(self, app_id, size=32):
        return self._resolve_icon(app_id, size)
    
    def get_icon_name(self, app_id):
        return self._get_icon_name(app_id)
    
    def resolve_icon(self, app_id, size, desktop_app=None):
        return self._resolve_icon(app_id, size, desktop_app)