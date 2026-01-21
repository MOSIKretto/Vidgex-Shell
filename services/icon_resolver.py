import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, GObject, GdkPixbuf


class IconResolver(GObject.GObject):
    __slots__ = ('default_icon', 'theme', '_desktop_dirs', '_icon_dirs')
    
    def __init__(self, default_icon: str = "application-x-executable-symbolic"):
        super().__init__()
        self.default_icon = default_icon
        self.theme = Gtk.IconTheme.get_default()
        
        # Директории для поиска .desktop файлов
        self._desktop_dirs = self._get_desktop_dirs()
        
        # Директории для поиска иконок напрямую
        self._icon_dirs = self._get_icon_dirs()

    def _join(self, *args) -> str:
        """Ручная реализация join для путей Unix без использования os или GLib"""
        parts = [str(arg) for arg in args if arg]
        if not parts:
            return ""
        
        path = parts[0]
        for part in parts[1:]:
            # Убираем слеш в конце текущего пути
            if path.endswith("/"):
                path = path[:-1]
            # Убираем слеш в начале добавляемой части
            if part.startswith("/"):
                part = part[1:]
            path = path + "/" + part
        return path

    def _get_desktop_dirs(self) -> list:
        """Получить все директории с .desktop файлами"""
        dirs = []
        
        # Системные директории
        for base in GLib.get_system_data_dirs():
            dirs.append(self._join(base, "applications"))
        
        # Пользовательская директория
        user_data = GLib.get_user_data_dir()
        if user_data:
            dirs.append(self._join(user_data, "applications"))
        
        # Flatpak директории
        home = GLib.get_home_dir()
        flatpak_user = self._join(home, ".local/share/flatpak/exports/share/applications")
        flatpak_sys = "/var/lib/flatpak/exports/share/applications"
        
        dirs.append(flatpak_user)
        dirs.append(flatpak_sys)
        
        # Snap директории
        snap_dir = "/var/lib/snapd/desktop/applications"
        if GLib.file_test(snap_dir, GLib.FileTest.EXISTS):
            dirs.append(snap_dir)
        
        return [d for d in dirs if GLib.file_test(d, GLib.FileTest.IS_DIR)]

    def _get_icon_dirs(self) -> list:
        """Получить директории с иконками"""
        dirs = []
        
        # Pixmaps
        dirs.append("/usr/share/pixmaps")
        dirs.append("/usr/local/share/pixmaps")
        
        # Иконки приложений
        for base in GLib.get_system_data_dirs():
            dirs.append(self._join(base, "icons"))
        
        user_data = GLib.get_user_data_dir()
        if user_data:
            dirs.append(self._join(user_data, "icons"))
        
        return [d for d in dirs if GLib.file_test(d, GLib.FileTest.IS_DIR)]

    def _normalize_name(self, name: str) -> str:
        """Нормализовать имя приложения"""
        if not name:
            return ""
        
        name = name.lower().strip()
        
        # Убираем расширения и суффиксы
        suffixes = ('.bin', '.exe', '.so', '-bin', '-gtk', '-qt', '-wayland', '-x11')
        for suffix in suffixes:
            if name.endswith(suffix):
                name = name[:-len(suffix)]
        
        return name

    def _camel_case_split(self, identifier: str) -> list:
        """Ручное разбиение CamelCase без regex"""
        parts = []
        if not identifier:
            return parts
            
        start = 0
        for i in range(1, len(identifier)):
            # Переход от строчной к заглавной (camelCase)
            if identifier[i].isupper() and not identifier[i-1].isupper():
                parts.append(identifier[start:i])
                start = i
            # Обработка аббревиатур (XMLHttp -> XML, Http)
            elif (identifier[i].isupper() and i + 1 < len(identifier) and 
                  not identifier[i+1].isupper()):
                if start != i:
                    parts.append(identifier[start:i])
                    start = i
                    
        parts.append(identifier[start:])
        return parts

    def _generate_name_variants(self, app_id: str) -> list:
        """Генерировать варианты имени для поиска"""
        app_id = self._normalize_name(app_id)
        if not app_id:
            return []
        
        variants = set()
        variants.add(app_id)
        
        # Замена разделителей
        variants.add(app_id.replace("-", "_"))
        variants.add(app_id.replace("_", "-"))
        variants.add(app_id.replace(".", "-"))
        variants.add(app_id.replace(".", "_"))
        
        # Разбор по точкам (org.kde.dolphin -> dolphin, kde.dolphin)
        parts = app_id.split(".")
        if len(parts) > 1:
            variants.add(parts[-1])  # последняя часть
            if len(parts) > 2:
                variants.add(".".join(parts[-2:]))  # последние 2 части
                variants.add(".".join(parts[1:]))   # без первой части (org/com)
        
        # Разбор по дефисам
        parts = app_id.split("-")
        if len(parts) > 1:
            variants.add(parts[0])  # первая часть
            variants.add("-".join(parts[:2]))  # первые 2 части
        
        # Удаление общих префиксов
        prefixes = ('org.', 'com.', 'net.', 'io.', 'dev.', 'app.')
        for prefix in prefixes:
            if app_id.startswith(prefix):
                variants.add(app_id[len(prefix):])
        
        # Удаление общих суффиксов из названия
        name_suffixes = ('-desktop', '-client', '-browser', '-app')
        for suffix in name_suffixes:
            if app_id.endswith(suffix):
                variants.add(app_id[:-len(suffix)])
        
        # Camel case разбор (например: TelegramDesktop -> telegram, desktop)
        camel_parts = self._camel_case_split(app_id)
        if len(camel_parts) > 1:
            variants.add(camel_parts[0].lower())
            variants.add("-".join(p.lower() for p in camel_parts))
        
        return [v for v in variants if v and len(v) > 1]

    def _listdir(self, path: str) -> list:
        """Аналог os.listdir на GLib"""
        files = []
        try:
            d = GLib.Dir.open(path, 0)
            while True:
                name = d.read_name()
                if not name:
                    break
                files.append(name)
        except Exception:
            pass
        return files

    def _find_desktop_file(self, app_id: str) -> str | None:
        """Найти .desktop файл для приложения"""
        variants = self._generate_name_variants(app_id)
        
        # Генерируем возможные имена .desktop файлов
        desktop_names = set()
        for variant in variants:
            desktop_names.add(f"{variant}.desktop")
            desktop_names.add(f"org.{variant}.desktop")
            desktop_names.add(f"com.{variant}.desktop")
            
            # Для имён типа "telegram" пробуем "org.telegram.desktop"
            parts = variant.split(".")
            if len(parts) == 1:
                desktop_names.add(f"org.{variant}.{variant}.desktop")
        
        # Ищем в директориях
        for desktop_dir in self._desktop_dirs:
            files = self._listdir(desktop_dir)
            if not files:
                continue
            
            files_lower = {f.lower(): f for f in files}
            
            for name in desktop_names:
                name_lower = name.lower()
                if name_lower in files_lower:
                    return self._join(desktop_dir, files_lower[name_lower])
            
            # Поиск по частичному совпадению
            for variant in variants:
                for file_lower, file_orig in files_lower.items():
                    if file_lower.endswith('.desktop') and variant in file_lower:
                        return self._join(desktop_dir, file_orig)
        
        return None

    def _get_icon_from_desktop(self, path: str) -> str | None:
        """Извлечь имя иконки из .desktop файла"""
        try:
            kf = GLib.KeyFile.new()
            kf.load_from_file(path, GLib.KeyFileFlags.NONE)
            icon = kf.get_string(GLib.KEY_FILE_DESKTOP_GROUP, "Icon")
            return icon.strip() if icon else None
        except Exception:
            return None

    def _recursive_find(self, base_dir: str, target_names: set, max_depth: int = 3, current_depth: int = 0) -> str | None:
        """Рекурсивный поиск файлов без os.walk"""
        if current_depth > max_depth:
            return None
            
        try:
            d = GLib.Dir.open(base_dir, 0)
            entries = []
            while True:
                name = d.read_name()
                if not name:
                    break
                entries.append(name)
        except Exception:
            return None
            
        # Сначала проверяем файлы в текущей директории
        files_lower = {}
        subdirs = []
        
        for name in entries:
            full_path = self._join(base_dir, name)
            if GLib.file_test(full_path, GLib.FileTest.IS_DIR):
                subdirs.append(full_path)
            else:
                files_lower[name.lower()] = full_path
                
        # Проверка совпадений
        for target in target_names:
            if target in files_lower:
                return files_lower[target]
                
        # Рекурсивный спуск
        for subdir in subdirs:
            result = self._recursive_find(subdir, target_names, max_depth, current_depth + 1)
            if result:
                return result
                
        return None

    def _find_icon_file(self, app_id: str) -> str | None:
        """Искать файл иконки напрямую в директориях"""
        variants = self._generate_name_variants(app_id)
        extensions = ('.png', '.svg', '.xpm', '.ico')
        
        # Подготовка списка имен файлов для поиска (name.ext) в нижнем регистре
        target_files = set()
        for variant in variants:
            for ext in extensions:
                target_files.add(f"{variant}{ext}".lower())

        for icon_dir in self._icon_dirs:
            if 'pixmaps' in icon_dir:
                # В pixmaps ищем плоским списком
                files = self._listdir(icon_dir)
                files_lower = {f.lower(): self._join(icon_dir, f) for f in files}
                for target in target_files:
                    if target in files_lower:
                        return files_lower[target]
            else:
                # В остальных папках ищем рекурсивно
                found = self._recursive_find(icon_dir, target_files)
                if found:
                    return found
        
        return None

    def _try_icon_theme_variants(self, app_id: str) -> str | None:
        """Попробовать найти иконку в теме по вариантам имени"""
        variants = self._generate_name_variants(app_id)
        
        for variant in variants:
            if self.theme.has_icon(variant):
                return variant
        
        return None

    def get_icon_name(self, app_id: str) -> str:
        """Получить имя иконки для app_id"""
        if not app_id:
            return self.default_icon
        
        app_id = app_id.strip()
        app_id_lower = app_id.lower()
        
        # 1. Прямая проверка в теме
        if self.theme.has_icon(app_id_lower):
            return app_id_lower
        
        if self.theme.has_icon(app_id):
            return app_id
        
        # 2. Проверка вариантов имени в теме
        icon_name = self._try_icon_theme_variants(app_id)
        if icon_name:
            return icon_name
        
        # 3. Поиск .desktop файла
        desktop_path = self._find_desktop_file(app_id)
        if desktop_path:
            icon = self._get_icon_from_desktop(desktop_path)
            if icon:
                # Если это путь к файлу (проверка на абсолютный путь строковым методом)
                if icon.startswith("/") and GLib.file_test(icon, GLib.FileTest.EXISTS):
                    return icon
                # Если это имя иконки
                if self.theme.has_icon(icon):
                    return icon
                # Попробуем поискать файл
                icon_file = self._find_icon_file(icon)
                if icon_file:
                    return icon_file
        
        # 4. Поиск файла иконки напрямую
        icon_file = self._find_icon_file(app_id)
        if icon_file:
            return icon_file
        
        return self.default_icon

    def get_icon_pixbuf(self, app_id: str, size: int = 32):
        """Получить pixbuf иконки"""
        icon_name = self.get_icon_name(app_id)
        
        try:
            # Если это путь к файлу (проверка через строку)
            if icon_name.startswith("/") and GLib.file_test(icon_name, GLib.FileTest.EXISTS):
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(icon_name)
                if pixbuf.get_width() != size or pixbuf.get_height() != size:
                    pixbuf = pixbuf.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
                return pixbuf
            
            # Иначе загружаем из темы
            return self.theme.load_icon(icon_name, size, Gtk.IconLookupFlags.FORCE_SIZE)
            
        except Exception:
            try:
                return self.theme.load_icon(self.default_icon, size, Gtk.IconLookupFlags.FORCE_SIZE)
            except Exception:
                return None

    def resolve_icon(self, app_id: str, size: int, desktop_app=None):
        """
        Единый метод получения иконки.
        Приоритет: desktop_app -> умный поиск -> fallback
        """
        pixbuf = None

        # 1. Пробуем получить из desktop_app
        if desktop_app:
            try:
                pixbuf = desktop_app.get_icon_pixbuf(size=size)
            except Exception:
                pass

        # 2. Пробуем умный поиск
        if not pixbuf and app_id:
            pixbuf = self.get_icon_pixbuf(app_id, size)

        # 3. Fallback на стандартную иконку
        if not pixbuf:
            try:
                pixbuf = self.theme.load_icon(self.default_icon, size, Gtk.IconLookupFlags.FORCE_SIZE)
            except Exception:
                pass

        # 4. Последний fallback
        if not pixbuf:
            try:
                pixbuf = self.theme.load_icon("image-missing", size, Gtk.IconLookupFlags.FORCE_SIZE)
            except Exception:
                pass

        # 5. Масштабирование если нужно
        if pixbuf and (pixbuf.get_width() != size or pixbuf.get_height() != size):
            pixbuf = pixbuf.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)

        return pixbuf