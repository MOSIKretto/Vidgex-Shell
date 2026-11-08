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
    if _desktop_index is None:
        index: dict[str, Gio.Icon] = {}
        for app in Gio.AppInfo.get_all():
            icon = app.get_icon()
            if icon is None:
                continue
            stem = (app.get_id() or "").removesuffix(".desktop")
            keys = [
                stem,
                stem.rsplit(".", 1)[-1],
                app.get_name(),
                (app.get_executable() or "").rsplit("/", 1)[-1],
            ]
            if isinstance(app, Gio.DesktopAppInfo):
                keys.append(app.get_startup_wm_class())
            for k in keys:
                if k:
                    index.setdefault(k.lower(), icon)
        _desktop_index = index
    return _desktop_index

def _candidate_names(app_key: str) -> list[str]:
    raw = app_key.strip()
    if not raw:
        return []
    low = raw.lower()
    names = [raw, low, low.replace(" ", "-"), low.replace("_", "-")]
    if "." in low:
        parts = [p for p in low.split(".") if p]
        if parts:
            names.append(
                parts[-2] if parts[-1] == "desktop" and len(parts) >= 2 else parts[-1]
            )
    return list(dict.fromkeys(n for n in names if n))

def _load_themed_name(theme: Gtk.IconTheme, name: str, size: int = DEFAULT_ICON_SIZE) -> GdkPixbuf.Pixbuf | None:
    info = theme.lookup_icon(name, size, Gtk.IconLookupFlags.FORCE_SIZE)
    if info is None:
        return None
    try:
        return info.load_icon()
    except GLib.Error:
        return None

def _load_gio_icon(theme: Gtk.IconTheme, gicon: Gio.Icon, size: int = DEFAULT_ICON_SIZE) -> GdkPixbuf.Pixbuf | None:
    if isinstance(gicon, Gio.ThemedIcon):
        for name in gicon.get_names():
            pb = _load_themed_name(theme, name, size)
            if pb is not None:
                return pb
    elif isinstance(gicon, Gio.FileIcon):
        path = gicon.get_file().get_path()
        if path:
            try:
                return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size, size, True)
            except GLib.Error:
                return None
    return None

def _attempts(theme: Gtk.IconTheme, app_key: str, size: int):
    names = _candidate_names(app_key)

    for name in names:
        yield _load_themed_name(theme, name, size)

    for name in names:
        try:
            info = Gio.DesktopAppInfo.new(f"{name}.desktop")
        except TypeError:
            # Gio.DesktopAppInfo.new бросает TypeError в ряде версий PyGObject, если .desktop не найден
            continue
        icon = info.get_icon() if info is not None else None
        if icon is not None:
            yield _load_gio_icon(theme, icon, size)

    # Индекс строится только если все попытки выше уже провалились
    index = _build_desktop_index()
    for name in names:
        icon = index.get(name.lower())
        if icon is not None:
            yield _load_gio_icon(theme, icon, size)

    for fallback in FALLBACK_ICONS:
        yield _load_themed_name(theme, fallback, size)


def resolve_app_pixbuf(app_key: str, size: int = DEFAULT_ICON_SIZE) -> GdkPixbuf.Pixbuf | None:
    key = (app_key, size)
    if key in _pixbuf_cache:
        return _pixbuf_cache[key]

    theme = _get_icon_theme()
    if theme is None:
        return None

    pb = next((p for p in _attempts(theme, app_key, size) if p is not None), None)
    _pixbuf_cache[key] = pb
    return pb