import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, GObject, GdkPixbuf
import os
import re


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

    def _get_desktop_dirs(self) -> list:
        """Получить все директории с .desktop файлами"""
        dirs = []
        
        # Системные директории
        for base in GLib.get_system_data_dirs():
            dirs.append(os.path.join(base, "applications"))
        
        # Пользовательская директория
        user_data = GLib.get_user_data_dir()
        if user_data:
            dirs.append(os.path.join(user_data, "applications"))
        
        # Flatpak директории
        flatpak_dirs = [
            os.path.expanduser("~/.local/share/flatpak/exports/share/applications"),
            "/var/lib/flatpak/exports/share/applications"
        ]
        dirs.extend(flatpak_dirs)
        
        # Snap директории
        snap_dir = "/var/lib/snapd/desktop/applications"
        if os.path.exists(snap_dir):
            dirs.append(snap_dir)
        
        return [d for d in dirs if os.path.isdir(d)]

    def _get_icon_dirs(self) -> list:
        """Получить директории с иконками"""
        dirs = []
        
        # Pixmaps
        dirs.append("/usr/share/pixmaps")
        dirs.append("/usr/local/share/pixmaps")
        
        # Иконки приложений
        for base in GLib.get_system_data_dirs():
            dirs.append(os.path.join(base, "icons"))
        
        user_data = GLib.get_user_data_dir()
        if user_data:
            dirs.append(os.path.join(user_data, "icons"))
        
        return [d for d in dirs if os.path.isdir(d)]

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
        camel_parts = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)', app_id)
        if len(camel_parts) > 1:
            variants.add(camel_parts[0].lower())
            variants.add("-".join(p.lower() for p in camel_parts))
        
        return [v for v in variants if v and len(v) > 1]

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
            try:
                files = os.listdir(desktop_dir)
            except OSError:
                continue
            
            files_lower = {f.lower(): f for f in files}
            
            for name in desktop_names:
                name_lower = name.lower()
                if name_lower in files_lower:
                    return os.path.join(desktop_dir, files_lower[name_lower])
            
            # Поиск по частичному совпадению
            for variant in variants:
                for file_lower, file_orig in files_lower.items():
                    if file_lower.endswith('.desktop') and variant in file_lower:
                        return os.path.join(desktop_dir, file_orig)
        
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

    def _find_icon_file(self, app_id: str) -> str | None:
        """Искать файл иконки напрямую в директориях"""
        variants = self._generate_name_variants(app_id)
        extensions = ('.png', '.svg', '.xpm', '.ico')
        
        for icon_dir in self._icon_dirs:
            try:
                # Поиск в корне директории (pixmaps)
                if 'pixmaps' in icon_dir:
                    try:
                        files = os.listdir(icon_dir)
                        files_lower = {f.lower(): os.path.join(icon_dir, f) for f in files}
                        
                        for variant in variants:
                            for ext in extensions:
                                check_name = f"{variant}{ext}"
                                if check_name in files_lower:
                                    return files_lower[check_name]
                    except OSError:
                        continue
                
                # Рекурсивный поиск в hicolor и других темах
                for root, dirs, files in os.walk(icon_dir):
                    files_lower = {f.lower(): os.path.join(root, f) for f in files}
                    for variant in variants:
                        for ext in extensions:
                            check_name = f"{variant}{ext}"
                            if check_name in files_lower:
                                return files_lower[check_name]
                    
                    # Ограничиваем глубину поиска
                    if root.count(os.sep) - icon_dir.count(os.sep) > 3:
                        dirs.clear()
                        
            except OSError:
                continue
        
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
                # Если это путь к файлу
                if os.path.isabs(icon) and os.path.exists(icon):
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
            # Если это путь к файлу
            if os.path.isabs(icon_name) and os.path.exists(icon_name):
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