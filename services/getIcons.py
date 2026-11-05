"""Общий резолвер иконок приложений по class окна.

Используется и баром (иконки на точках воркспейсов), и нотчем
(иконка активного окна). Матчинг идёт по нескольким ключам:
имя иконки в теме, StartupWMClass, имя desktop-файла, executable.
"""

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk


DEFAULT_ICON_SIZE = 16
FALLBACK_ICONS = (
    "application-x-executable",
    "application-default-icon",
    "window-new",
)

_pixbuf_cache: dict[tuple[str, int], GdkPixbuf.Pixbuf | None] = {}
_desktop_index: dict[str, Gio.Icon] | None = None
_theme_hid = 0
_theme_listeners: list = []


def add_theme_changed_listener(callback) -> None:
    """Подписаться на смену темы иконок (кеши уже сброшены к моменту вызова)."""
    _theme_listeners.append(callback)


def _on_icon_theme_changed(*_) -> None:
    global _desktop_index
    _pixbuf_cache.clear()
    _desktop_index = None
    for callback in tuple(_theme_listeners):
        callback()


def _get_icon_theme() -> Gtk.IconTheme | None:
    global _theme_hid
    theme = Gtk.IconTheme.get_default()
    if theme is not None and not _theme_hid:
        _theme_hid = theme.connect("changed", _on_icon_theme_changed)
    return theme


def _build_desktop_index() -> dict[str, Gio.Icon]:
    global _desktop_index
    if _desktop_index is not None:
        return _desktop_index

    index: dict[str, Gio.Icon] = {}
    for app in Gio.AppInfo.get_all():
        icon = app.get_icon()
        if icon is None:
            continue
        keys = []
        app_id = app.get_id()
        if app_id:
            stem = app_id[:-8] if app_id.endswith(".desktop") else app_id
            keys.append(stem)
            if "." in stem:
                keys.append(stem.rsplit(".", 1)[-1])
        if isinstance(app, Gio.DesktopAppInfo):
            wm_class = app.get_startup_wm_class()
            if wm_class:
                keys.append(wm_class)
        exe = app.get_executable()
        if exe:
            keys.append(exe.rsplit("/", 1)[-1])
        name = app.get_name()
        if name:
            keys.append(name)

        for k in keys:
            low = k.lower()
            if low not in index:
                index[low] = icon
    _desktop_index = index
    return index


def _candidate_names(app_key: str) -> list[str]:
    raw = app_key.strip()
    if not raw:
        return []
    low = raw.lower()
    candidates = [
        raw,
        low,
        low.replace(" ", "-"),
        low.replace("_", "-"),
    ]
    if "." in low:
        parts = [p for p in low.split(".") if p]
        if parts:
            tail = parts[-1]
            if tail == "desktop" and len(parts) >= 2:
                candidates.append(parts[-2])
            else:
                candidates.append(tail)
    seen = set()
    result = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            result.append(c)
    return result


def _load_themed_name(
    theme: Gtk.IconTheme, name: str, size: int = DEFAULT_ICON_SIZE
) -> GdkPixbuf.Pixbuf | None:
    if not theme.has_icon(name):
        return None
    try:
        return theme.load_icon(name, size, Gtk.IconLookupFlags.FORCE_SIZE)
    except GLib.Error:
        # В теме иконок может встретиться повреждённый SVG или отсутствующий файл
        return None


def _load_gio_icon(
    theme: Gtk.IconTheme, gicon: Gio.Icon, size: int = DEFAULT_ICON_SIZE
) -> GdkPixbuf.Pixbuf | None:
    if isinstance(gicon, Gio.ThemedIcon):
        for name in gicon.get_names():
            pb = _load_themed_name(theme, name, size)
            if pb is not None:
                return pb
    elif isinstance(gicon, Gio.FileIcon):
        file_obj = gicon.get_file()
        path = file_obj.get_path() if file_obj is not None else None
        if path:
            try:
                return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size, size, True)
            except GLib.Error:
                # Файл иконки приложения может быть удалён или иметь нечитаемый формат
                return None
    return None


def resolve_app_pixbuf(
    app_key: str, size: int = DEFAULT_ICON_SIZE
) -> GdkPixbuf.Pixbuf | None:
    cache_key = (app_key, size)
    if cache_key in _pixbuf_cache:
        return _pixbuf_cache[cache_key]

    theme = _get_icon_theme()
    if theme is None:
        return None

    candidates = _candidate_names(app_key)
    for cand in candidates:
        pb = _load_themed_name(theme, cand, size)
        if pb is not None:
            _pixbuf_cache[cache_key] = pb
            return pb

    for cand in candidates:
        try:
            desktop_info = Gio.DesktopAppInfo.new(f"{cand}.desktop")
        except TypeError:
            # Gio.DesktopAppInfo.new бросает TypeError в ряде версий PyGObject, если .desktop не найден
            desktop_info = None
        if desktop_info is not None:
            gicon = desktop_info.get_icon()
            if gicon is not None:
                pb = _load_gio_icon(theme, gicon, size)
                if pb is not None:
                    _pixbuf_cache[cache_key] = pb
                    return pb

    index = _build_desktop_index()
    for cand in candidates:
        gicon = index.get(cand.lower())
        if gicon is not None:
            pb = _load_gio_icon(theme, gicon, size)
            if pb is not None:
                _pixbuf_cache[cache_key] = pb
                return pb

    for fallback in FALLBACK_ICONS:
        pb = _load_themed_name(theme, fallback, size)
        if pb is not None:
            _pixbuf_cache[cache_key] = pb
            return pb

    _pixbuf_cache[cache_key] = None
    return None
