import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.eventbox import EventBox
from fabric.widgets.revealer import Revealer
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk

from modules.Bar.Workspaces.Bars.matrix import (
    axis_widgets,
    cancel_source,
    clamp_coords,
    client_ws_id,
    get_active_ws,
    ipc_json,
    state,
)


_ICON_SIZE = 16
_BTN_HOVER_EVENTS = Gdk.EventMask.ENTER_NOTIFY_MASK
_SLOT_HOVER_EVENTS = Gdk.EventMask.LEAVE_NOTIFY_MASK | Gdk.EventMask.BUTTON_PRESS_MASK
_FALLBACK_ICONS = (
    "application-x-executable",
    "application-default-icon",
    "window-new",
)

_conn = None
_win_hids: list[int] = []
_ready_hid = 0
_fetch_tid = 0
_theme_hid = 0
_ws_apps: dict[int, tuple[str, ...]] = {}
_pixbuf_cache: dict[str, GdkPixbuf.Pixbuf | None] = {}
_desktop_index: dict[str, Gio.Icon] | None = None


def _on_icon_theme_changed(*_):
    global _desktop_index
    _pixbuf_cache.clear()
    _desktop_index = None
    for w in tuple(axis_widgets):
        if not w._destroyed and isinstance(w, AppIconsMixin):
            w.refresh_app_icons(force_reload=True)


def _get_icon_theme() -> Gtk.IconTheme:
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


def _load_themed_name(theme: Gtk.IconTheme, name: str) -> GdkPixbuf.Pixbuf | None:
    if not theme.has_icon(name):
        return None
    try:
        return theme.load_icon(name, _ICON_SIZE, Gtk.IconLookupFlags.FORCE_SIZE)
    except GLib.Error:
        # В теме иконок может встретиться повреждённый SVG или отсутствующий файл
        return None


def _load_gio_icon(theme: Gtk.IconTheme, gicon: Gio.Icon) -> GdkPixbuf.Pixbuf | None:
    if isinstance(gicon, Gio.ThemedIcon):
        for name in gicon.get_names():
            pb = _load_themed_name(theme, name)
            if pb is not None:
                return pb
    elif isinstance(gicon, Gio.FileIcon):
        file_obj = gicon.get_file()
        path = file_obj.get_path() if file_obj is not None else None
        if path:
            try:
                return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, _ICON_SIZE, _ICON_SIZE, True)
            except GLib.Error:
                # Файл иконки приложения может быть удалён или иметь нечитаемый формат
                return None
    return None


def resolve_app_pixbuf(app_key: str) -> GdkPixbuf.Pixbuf | None:
    if app_key in _pixbuf_cache:
        return _pixbuf_cache[app_key]

    theme = _get_icon_theme()
    if theme is None:
        return None

    candidates = _candidate_names(app_key)
    for cand in candidates:
        pb = _load_themed_name(theme, cand)
        if pb is not None:
            _pixbuf_cache[app_key] = pb
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
                pb = _load_gio_icon(theme, gicon)
                if pb is not None:
                    _pixbuf_cache[app_key] = pb
                    return pb

    index = _build_desktop_index()
    for cand in candidates:
        gicon = index.get(cand.lower())
        if gicon is not None:
            pb = _load_gio_icon(theme, gicon)
            if pb is not None:
                _pixbuf_cache[app_key] = pb
                return pb

    for fallback in _FALLBACK_ICONS:
        pb = _load_themed_name(theme, fallback)
        if pb is not None:
            _pixbuf_cache[app_key] = pb
            return pb

    _pixbuf_cache[app_key] = None
    return None


def _extract_client_key(client: dict) -> str:
    for field in ("class", "initialClass", "initialTitle", "title"):
        val = client.get(field)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _parse_clients_map(clients: list) -> dict[int, tuple[str, ...]]:
    grouped: dict[int, dict[str, tuple[int, str]]] = {}
    for c in clients:
        if not isinstance(c, dict) or not c.get("mapped", True):
            continue
        ws_id = client_ws_id(c)
        if ws_id < 1:
            continue
        key = _extract_client_key(c)
        if not key:
            continue
        norm_key = key.lower()
        raw_addr = str(c.get("address") or "")
        try:
            addr_num = int(raw_addr, 16)
        except ValueError:
            # Нестандартный формат адреса окна в ответе IPC
            addr_num = 0

        ws_apps = grouped.setdefault(ws_id, {})
        prev = ws_apps.get(norm_key)
        if prev is None or addr_num < prev[0]:
            ws_apps[norm_key] = (addr_num, key)

    result: dict[int, tuple[str, ...]] = {}
    for ws_id, ws_apps in grouped.items():
        ordered = sorted(ws_apps.values(), key=lambda item: (item[0], item[1]))
        result[ws_id] = tuple(key for _, key in ordered)
    return result


def solo_desk() -> bool:
    # Единственный стол матрицы: точка не нужна, вместо неё показываются иконки его приложений.
    return state.rows * state.cols == 1


def top_dots_displayed() -> bool:
    for w in axis_widgets:
        if not w._is_vertical and not w._destroyed:
            if w.get_mapped() and state.cols > 1:
                return True
    return False


def get_ws_app_keys(ws_id: int) -> tuple[str, ...]:
    return _ws_apps.get(ws_id, ())


def refresh_all_axis_icons(ws: int | None = None):
    if not axis_widgets:
        return
    active_ws = ws if ws is not None else get_active_ws(_conn)
    for w in tuple(axis_widgets):
        if not w._destroyed and isinstance(w, AppIconsMixin):
            w.refresh_app_icons(ws=active_ws)


def _run_clients_sync() -> bool:
    global _fetch_tid, _ws_apps
    _fetch_tid = 0
    if _conn is None or not _conn.ready or not axis_widgets:
        return False
    if not any(w.get_mapped() for w in axis_widgets if not w._destroyed):
        return False

    clients = ipc_json("j/clients", [], _conn)
    new_map = _parse_clients_map(clients)
    if new_map != _ws_apps:
        _ws_apps = new_map
        refresh_all_axis_icons()
    return False


def request_clients_sync(*_):
    global _fetch_tid
    if _fetch_tid or _conn is None or not axis_widgets:
        return
    _fetch_tid = GLib.idle_add(_run_clients_sync, priority=GLib.PRIORITY_DEFAULT_IDLE)


def _disconnect_app_conn():
    global _ready_hid, _fetch_tid
    _fetch_tid = cancel_source(_fetch_tid)
    if _conn is not None:
        if _ready_hid:
            _conn.disconnect(_ready_hid)
            _ready_hid = 0
        while _win_hids:
            _conn.disconnect(_win_hids.pop())


def bind_app_icons_conn(conn):
    global _conn, _ready_hid
    if conn is None:
        return
    if _conn is not conn:
        _disconnect_app_conn()
        _conn = conn

    if not _win_hids:
        for ev in (
            "event::openwindow",
            "event::closewindow",
            "event::movewindow",
        ):
            _win_hids.append(conn.connect(ev, request_clients_sync))

    if conn.ready:
        request_clients_sync()
    elif not _ready_hid:
        def _on_ready(*_):
            global _ready_hid
            if _ready_hid:
                conn.disconnect(_ready_hid)
                _ready_hid = 0
            if _conn is conn:
                request_clients_sync()

        _ready_hid = conn.connect("event::ready", _on_ready)


def release_app_icons_if_unused():
    global _conn, _ws_apps
    if any(isinstance(w, AppIconsMixin) for w in axis_widgets):
        return
    _disconnect_app_conn()
    _conn = None
    _ws_apps = {}


def resolve_slot_ws(is_vertical: bool, idx: int, active_ws: int) -> int:
    cur_row, cur_col = clamp_coords(active_ws)
    if is_vertical:
        return idx * state.cols + cur_col + 1
    return cur_row * state.cols + idx + 1


class DotSlot:
    __slots__ = (
        "owner",
        "btn",
        "icons_box",
        "revealer",
        "inner_box",
        "event_box",
        "hovered",
        "_target_keys",
        "_rendered_keys",
        "_force_pending",
        "_images",
        "_hids",
    )

    def __init__(self, owner, btn: Button, is_vertical: bool):
        self.owner = owner
        self.btn = btn
        self.hovered = False
        self._target_keys: tuple[str, ...] = ()
        self._rendered_keys: tuple[str, ...] = ()
        self._force_pending = False
        self._images: list[Gtk.Image] = []
        self._hids: list[tuple[object, int]] = []

        orient = "v" if is_vertical else "h"
        transition = "slide-down" if is_vertical else "slide-right"
        cls_suffix = "left" if is_vertical else "top"

        self.icons_box = Box(
            orientation=orient,
            spacing=4,
            h_align="center",
            v_align="center",
        )
        self.icons_box.add_style_class("ws-app-icons")
        self.icons_box.add_style_class(f"ws-app-icons-{cls_suffix}")

        self.revealer = Revealer(
            transition_type=transition,
            transition_duration=250,
            child_revealed=False,
            child=self.icons_box,
        )
        self.inner_box = Box(
            orientation=orient,
            spacing=0,
            h_align="center",
            v_align="center",
            children=[self.btn, self.revealer],
        )
        self.inner_box.add_style_class("ws-dot-slot")

        self.event_box = EventBox(child=self.inner_box)
        self.event_box.add_events(_SLOT_HOVER_EVENTS)
        self.btn.add_events(_BTN_HOVER_EVENTS)
        # Видимостью точки управляет слот (show_all родителя её не трогает):
        # на единственном столе точка скрыта, остаются только иконки.
        self.btn.set_no_show_all(True)

        self._hids.append((self.btn, self.btn.connect("enter-notify-event", self._on_dot_enter)))
        self._hids.append((self.event_box, self.event_box.connect("leave-notify-event", self._on_slot_leave)))
        self._hids.append((self.event_box, self.event_box.connect("button-press-event", self._on_button_press)))
        self._hids.append((self.revealer, self.revealer.connect("notify::child-revealed", self._on_child_revealed)))
        self._sync_btn_visible()

    def _sync_btn_visible(self):
        # Уходящая точка (глитч удаления) остаётся видимой до конца анимации.
        hidden = solo_desk() and not state.order_actions_visible and self.btn not in self.owner._glitch_removing
        self.btn.set_visible(not hidden)

    def _drop_hover(self):
        if self.hovered:
            self.hovered = False
            self.btn.remove_style_class("slot-hovered")

    def _set_hovered(self, value: bool):
        if self.hovered == value:
            return
        self.hovered = value
        if value:
            self.btn.add_style_class("slot-hovered")
        else:
            self.btn.remove_style_class("slot-hovered")
        self.sync_reveal()

    def _on_dot_enter(self, _, event) -> bool:
        if not state.order_actions_visible and event.detail != Gdk.NotifyType.INFERIOR:
            self._set_hovered(True)
        return False

    def _on_slot_leave(self, _, event) -> bool:
        if event.detail != Gdk.NotifyType.INFERIOR:
            self._set_hovered(False)
        return False

    def _on_button_press(self, _, event) -> bool:
        if event.button == 1 and event.type == Gdk.EventType.BUTTON_PRESS and self.hovered:
            self.owner._on_dot_button_clicked(self.btn)
            return True
        return False

    def _on_child_revealed(self, revealer, _):
        # Очищаем или обновляем виджеты иконок только после того, как анимация закрытия полностью завершилась
        if not revealer.get_reveal_child() and not revealer.get_child_revealed():
            if self._rendered_keys != self._target_keys or self._force_pending:
                self._apply_images(self._target_keys, force=self._force_pending)

    def _apply_images(self, app_keys: tuple[str, ...], force: bool = False):
        if app_keys == self._rendered_keys and not force:
            return
        self._rendered_keys = app_keys
        self._force_pending = False

        while len(self._images) > len(app_keys):
            img = self._images.pop()
            self.icons_box.remove(img)
            img.destroy()

        for i, key in enumerate(app_keys):
            pb = resolve_app_pixbuf(key)
            if i < len(self._images):
                img = self._images[i]
                if pb is not None:
                    img.set_from_pixbuf(pb)
                else:
                    img.set_from_icon_name("application-x-executable", Gtk.IconSize.MENU)
            else:
                img = (
                    Gtk.Image.new_from_pixbuf(pb)
                    if pb is not None
                    else Gtk.Image.new_from_icon_name("application-x-executable", Gtk.IconSize.MENU)
                )
                img.get_style_context().add_class("ws-app-icon")
                img.show()
                self._images.append(img)
                self.icons_box.add(img)

    def _compute_should_show(self) -> bool:
        if (
            self.owner._destroyed
            or state.order_actions_visible
            or self.btn in self.owner._glitch_removing
        ):
            self._drop_hover()
            return False

        if solo_desk():
            # Единственный стол: иконки показываются всегда, без наведения, и только на верхней оси
            # (боковая панель на одном столе скрыта).
            self._drop_hover()
            return bool(self._target_keys) and not self.owner._is_vertical

        if not self._target_keys:
            return False

        idx = self.owner.dots.index(self.btn) if self.btn in self.owner.dots else -1
        is_active = idx >= 0 and idx == self.owner._active_idx

        if not is_active:
            return self.hovered
        if not self.owner._is_vertical:
            return top_dots_displayed()
        return not top_dots_displayed()

    def set_apps(self, app_keys: tuple[str, ...], force_reload: bool = False):
        self._target_keys = app_keys
        if force_reload:
            self._force_pending = True
        self.sync_reveal()

    def sync_reveal(self):
        self._sync_btn_visible()
        should_show = self._compute_should_show()
        if should_show:
            self._apply_images(self._target_keys, force=self._force_pending)
            self.revealer.set_reveal_child(True)
        else:
            # Не удаляем иконки из контейнера во время задвижки Revealer,
            # иначе его ширина/высота схлопывается в 0 мгновенно без плавной анимации.
            self.revealer.set_reveal_child(False)
            if not self.revealer.get_child_revealed():
                self._apply_images(self._target_keys, force=self._force_pending)

    def destroy(self):
        while self._hids:
            obj, hid = self._hids.pop()
            obj.disconnect(hid)
        self._images.clear()
        self._target_keys = ()
        self._rendered_keys = ()
        self.event_box.destroy()


class AppIconsMixin:
    def __init__(self, conn, *args, **kwargs):
        self._slots: dict[Button, DotSlot] = {}
        self._last_ws = 1
        super().__init__(conn, *args, **kwargs)
        self._connect(self, "map", self._on_icons_map)
        bind_app_icons_conn(conn)
        request_clients_sync()

    def _create_dot(self) -> Button:
        btn = super()._create_dot()
        self._slots[btn] = DotSlot(self, btn, self._is_vertical)
        return btn

    def _mount_dot_widget(self, btn: Button):
        slot = self._slots[btn]
        self.dots.append(btn)
        self.dots_box.add(slot.event_box)
        slot.event_box.show_all()

    def _unmount_dot_widget(self, btn: Button):
        slot = self._slots.pop(btn, None)
        if slot is not None:
            self.dots_box.remove(slot.event_box)
            slot.destroy()
        else:
            super()._unmount_dot_widget(btn)

    def _on_dot_glitch_state_changed(self, btn: Button, removing: bool):
        slot = self._slots.get(btn)
        if slot is not None:
            slot.sync_reveal()

    def _on_icons_map(self, *_):
        request_clients_sync()
        refresh_all_axis_icons(ws=self._last_ws)

    def _on_unmap(self, *_):
        super()._on_unmap()
        for slot in self._slots.values():
            slot._set_hovered(False)
        if not self._is_vertical:
            refresh_all_axis_icons(ws=self._last_ws)

    def _solo_apps_shown(self) -> bool:
        # Единственный стол с приложениями: ряд точек раскрывается ради иконок, а не точек.
        return (
            not self._is_vertical
            and solo_desk()
            and not state.order_actions_visible
            and bool(get_ws_app_keys(resolve_slot_ws(False, 0, self._last_ws)))
        )

    def _dots_revealed(self) -> bool:
        return self._solo_apps_shown() or super()._dots_revealed()

    def refresh_app_icons(self, ws: int | None = None, force_reload: bool = False):
        if self._destroyed or self.dots_box is None:
            return
        active_ws = ws if ws is not None else self._last_ws
        target = self._axis_count()
        for idx, btn in enumerate(self.dots):
            slot = self._slots.get(btn)
            if slot is None:
                continue
            if idx >= target or btn in self._glitch_removing:
                slot.set_apps((), force_reload=force_reload)
                continue
            slot_ws = resolve_slot_ws(self._is_vertical, idx, active_ws)
            slot.set_apps(get_ws_app_keys(slot_ws), force_reload=force_reload)
        # Список приложений приходит асинхронно: на единственном столе от него зависит раскрытие ряда.
        self._sync_dots_reveal()

    def sync_visibility(self):
        if self._destroyed:
            return
        super().sync_visibility()
        refresh_all_axis_icons(ws=self._last_ws)

    def _update_ui(self, *_, ws: int | None = None):
        if self._destroyed or self.dots_box is None:
            return
        if ws is None:
            ws = get_active_ws(self.conn)
        self._last_ws = ws
        super()._update_ui(ws=ws)
        self.refresh_app_icons(ws=ws)

    def cleanup(self):
        if self._destroyed:
            return
        super().cleanup()
        for btn in tuple(self._slots):
            slot = self._slots.pop(btn)
            slot.destroy()
        release_app_icons_if_unused()
        refresh_all_axis_icons()