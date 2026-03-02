import gi
gi.require_version("Gtk", "3.0")
gi.require_version('GtkLayerShell', '0.1')
from gi.repository import Gdk, GLib, Gtk, Gio, GtkLayerShell

import os
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Tuple

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.eventbox import EventBox
from fabric.widgets.image import Image
from fabric.widgets.revealer import Revealer
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow

from services.wayland import WaylandWindow as Window

from modules.Explorer.navigation import NavigationMixin
from modules.Explorer.DnD import DnDMixin
from modules.Explorer.devices import DevicesMixin
from modules.Explorer.clipboard import ClipboardMixin
from modules.Explorer.app_chooser import AppChooserMixin
from modules.Explorer.actions import ActionsMixin
from modules.Explorer.terminal import TerminalMixin


_FOLDER_ICONS = {
    "Documents": "folder-documents-symbolic",
    "Downloads": "folder-download-symbolic",
    "Music": "folder-music-symbolic",
    "Pictures": "folder-pictures-symbolic",
    "Videos": "folder-videos-symbolic",
    "Desktop": "user-desktop-symbolic",
}

_SIZE_UNITS = ('B', 'KB', 'MB', 'GB', 'TB')

_XDG_FOLDERS = (
    (GLib.UserDirectory.DIRECTORY_DESKTOP, "user-desktop-symbolic", "Desktop"),
    (GLib.UserDirectory.DIRECTORY_DOCUMENTS, "folder-documents-symbolic", "Documents"),
    (GLib.UserDirectory.DIRECTORY_DOWNLOAD, "folder-download-symbolic", "Downloads"),
    (GLib.UserDirectory.DIRECTORY_MUSIC, "folder-music-symbolic", "Music"),
    (GLib.UserDirectory.DIRECTORY_PICTURES, "folder-pictures-symbolic", "Pictures"),
    (GLib.UserDirectory.DIRECTORY_VIDEOS, "folder-videos-symbolic", "Videos"),
)

_ARCHIVE_EXT_SIMPLE = frozenset({
    '.zip', '.tar', '.rar', '.7z', '.gz', '.bz2', '.xz',
    '.zst', '.lz4', '.lzma', '.sz',
})

_ARCHIVE_EXT_COMPOUND = (
    '.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tbz',
    '.tar.xz', '.txz', '.tar.zst', '.tzst',
    '.tar.lz4', '.tlz4', '.tar.lzma', '.tlzma', '.tar.sz',
)

_COMPRESSION_FORMATS = [
    (".zip", "ZIP Archive"), (".7z", "7-Zip Archive"), (".tar", "Tar Archive"),
    (".tar.gz", "Tar + Gzip"), (".tgz", "Tar + Gzip (.tgz)"),
    (".tar.bz2", "Tar + Bzip2"), (".tbz2", "Tar + Bzip2 (.tbz2)"),
    (".tar.xz", "Tar + XZ"), (".txz", "Tar + XZ (.txz)"),
    (".tar.zst", "Tar + Zstd"), (".tzst", "Tar + Zstd (.tzst)"),
    (".tar.lz4", "Tar + LZ4"), (".tar.lzma", "Tar + LZMA"),
    (".tar.sz", "Tar + Snappy"), (".gz", "Gzip"), (".bz2", "Bzip2"),
    (".xz", "XZ"), (".zst", "Zstd"), (".lz4", "LZ4"),
    (".lzma", "LZMA"), (".sz", "Snappy"),
]

_DRAG_ACTIONS = Gdk.DragAction.COPY | Gdk.DragAction.MOVE

_ACTIVATOR_EVENTS = Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK

_EXPLORER_EVENTS = (
    Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
    | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.BUTTON_PRESS_MASK
    | Gdk.EventMask.BUTTON_RELEASE_MASK
)


class Explorer(
    NavigationMixin, DnDMixin, ActionsMixin, DevicesMixin,
    ClipboardMixin, AppChooserMixin, TerminalMixin, Window,
):
    __gtype_name__ = "Explorer"

    TARGET_URI_LIST = 0
    TARGET_TEXT = 1

    DRAG_HOVER_OPEN_DELAY = 800
    POST_DRAG_GRACE_PERIOD = 600
    ACTIVATOR_HOVER_DELAY = 1000

    DRAG_SCROLL_MARGIN = 300
    DRAG_SCROLL_SPEED_SLOW = 20
    DRAG_SCROLL_SPEED_FAST = 50
    DRAG_SCROLL_INTERVAL = 16

    _TIMER_ATTRS = (
        '_pending_hide', '_navigation_lock_timer', '_pending_refresh',
        '_post_drag_timer', '_drag_hover_timer', '_pending_hover_timer',
        '_drag_scroll_timer', '_activator_hover_timer',
    )

    def __init__(self, monitor_id: int = 0, **kwargs):
        self.monitor_id = monitor_id
        self._mon_w = 1920
        self._mon_h = 1080
        self._top_margin_closed = 50

        self._is_pinned = False
        self._is_hidden = True
        self._cursor_inside = False
        self._menu_open = False
        self._is_loading = False
        self._show_hidden = False
        self._navigation_lock = False
        self._drag_in_progress = False
        self._drag_over_explorer = False
        self._post_drag_grace = False
        self._cursor_over_activator = False
        self._clipboard_is_cut = False
        self._app_chooser_active = False
        self._force_path_scroll = False
        self._ui_built = False

        for attr in self._TIMER_ATTRS:
            setattr(self, attr, None)

        self._refresh_debounce_ms = 250

        home = Path.home()
        self._current_path = home
        self._history: List[Path] = [home]
        self._history_index = 0

        self._dnd_targets = None
        self._drag_source_path = None
        self._drag_hover_path = None
        self._drag_hover_widget = None
        self._pending_drop_source = None
        self._pending_drop_target = None
        self._pending_hover_path = None
        self._drag_scroll_speed = 0.0
        self._folder_widgets: List[Tuple[Gtk.Widget, Path]] = []

        self._file_monitor = None
        self._volume_monitor = None
        self._devices_container = None
        self._current_mount = None
        self._current_mount_path = None
        self._current_mount_name = None

        self._rename_widget = None
        self._rename_path = None
        self._clipboard_paths: List[Path] = []

        self._app_chooser_path = None
        self._app_chooser_content_type = None
        self._app_chooser_recommended: List = []
        self._app_chooser_other: List = []
        self._app_chooser_remaining: List = []
        self._app_list_container = None
        self._app_search_entry = None

        self._trash_path = home / ".local/share/Trash/files"
        self._trash_info_path = home / ".local/share/Trash/info"

        self._archive_extensions_simple = _ARCHIVE_EXT_SIMPLE
        self._archive_extensions_compound = _ARCHIVE_EXT_COMPOUND
        self._compression_formats = _COMPRESSION_FORMATS

        self._xdg_icon_map = {}
        self._bookmarks: List[Tuple[str, str, Path]] = [
            ("user-home-symbolic", "Home", home),
        ]
        self._init_xdg_bookmarks(home)
        self._bookmarks.append(("drive-harddisk-symbolic", "Root", Path("/")))

        self._icon_cache = {}

        super().__init__(
            name="explorer-window", layer="overlay", anchor="left top bottom",
            margin="0px 0px 0px 0px", exclusivity="none", monitor=monitor_id,
            visible=False, **kwargs,
        )

        self._icon_theme = Gtk.IconTheme.get_default()
        self._update_monitor()
        self._init_ui()
        GLib.idle_add(self._delayed_show)

    @property
    def _dnd_target_entries(self):
        if self._dnd_targets is None:
            self._dnd_targets = [
                Gtk.TargetEntry.new("text/uri-list", 0, self.TARGET_URI_LIST),
                Gtk.TargetEntry.new("text/plain", 0, self.TARGET_TEXT),
            ]
        return self._dnd_targets

    def _init_xdg_bookmarks(self, home: Path):
        for glib_dir, icon, fallback_name in _XDG_FOLDERS:
            dir_path_str = GLib.get_user_special_dir(glib_dir)
            path = Path(dir_path_str) if dir_path_str else home / fallback_name
            name = path.name.capitalize() if dir_path_str else fallback_name

            if not dir_path_str and not path.exists():
                continue

            self._bookmarks.append((icon, name, path))
            self._xdg_icon_map[path] = icon

    def _cancel_timer(self, attr: str):
        tid = getattr(self, attr, None)
        if tid:
            GLib.source_remove(tid)
            setattr(self, attr, None)

    def _cancel_all_timers(self):
        for attr in self._TIMER_ATTRS:
            tid = getattr(self, attr, None)
            if tid:
                GLib.source_remove(tid)
                setattr(self, attr, None)

    def _should_stay_visible(self) -> bool:
        return (self._is_pinned or self._menu_open or
                self._drag_in_progress or self._drag_over_explorer or
                self._pending_drop_source is not None or
                self._post_drag_grace or self._navigation_lock)

    def _should_block_margin_restore(self) -> bool:
        return (self._is_pinned or self._drag_in_progress or
                self._drag_over_explorer or
                self._pending_drop_source is not None or
                self._post_drag_grace)

    def _is_in_trash(self) -> bool:
        try:
            return self._current_path.resolve() == self._trash_path.resolve()
        except OSError:
            return False

    def _on_back_clicked(self, _):
        if self._is_terminal_open():
            self._terminal_prev_tab()
        elif not self._pending_drop_source:
            self._history_go(-1)

    def _on_forward_clicked(self, _):
        if self._is_terminal_open():
            self._terminal_next_tab()
        elif not self._pending_drop_source:
            self._history_go(+1)

    def _on_home_clicked(self, _):
        if self._is_terminal_open():
            self._terminal_home_tab()
        elif not self._pending_drop_source:
            self._navigate_to(Path.home())

    def _clear_search_focus(self):
        if self.search_entry.has_focus():
            self.set_focus(None)
            if not self._is_terminal_open():
                self._set_keyboard_interactive(False)

    def _clear_focus_on_click(self, widget, event):
        self._clear_search_focus()
        return False

    def _dismiss_focus(self):
        if self._is_terminal_open() or self.search_entry.has_focus():
            self.set_focus(None)
            self._set_keyboard_interactive(False)

    def _update_window_margin(self, is_open: bool):
        try:
            GtkLayerShell.set_margin(
                self, GtkLayerShell.Edge.TOP,
                0 if is_open else self._top_margin_closed)
        except Exception:
            pass

    def _set_keyboard_interactive(self, enabled: bool):
        try:
            GtkLayerShell.set_keyboard_mode(
                self,
                GtkLayerShell.KeyboardMode.EXCLUSIVE if enabled
                else GtkLayerShell.KeyboardMode.NONE)
        except Exception:
            pass

    def _update_monitor(self):
        display = Gdk.Display.get_default()
        if display:
            monitor = display.get_monitor(self.monitor_id)
            if monitor:
                geom = monitor.get_geometry()
                self._mon_w, self._mon_h = geom.width, geom.height
        self._top_margin_closed = int(self._mon_h * 0.08)

    def _delayed_show(self):
        self.show_all()
        self._navigate_to(self._current_path)
        self._setup_volume_monitor()
        return False

    def _get_cursor_position(self) -> Optional[Tuple[int, int]]:
        if not self.revealer.get_child_revealed():
            return None
        try:
            seat = Gdk.Display.get_default().get_default_seat()
            if seat:
                ptr = seat.get_pointer()
                if ptr:
                    _, x, y = ptr.get_position()
                    return x, y
        except Exception:
            pass
        return None

    def _get_window_origin(self) -> Optional[Tuple[int, int]]:
        try:
            window = self.get_window()
            if window:
                origin = window.get_origin()
                return (origin[1], origin[2]) if len(origin) == 3 else origin
        except Exception:
            pass
        return None

    def _widget_contains_point(self, widget, win_x, win_y, px, py) -> bool:
        try:
            if not widget.get_mapped():
                return False
            alloc = widget.get_allocation()
            ok, wx, wy = widget.translate_coordinates(self, 0, 0)
            if not ok:
                return False
            ax, ay = win_x + wx, win_y + wy
            return ax <= px <= ax + alloc.width and ay <= py <= ay + alloc.height
        except Exception:
            return False

    def _is_cursor_over_explorer(self) -> bool:
        pos = self._get_cursor_position()
        origin = self._get_window_origin()

        if not pos or not origin:
            return self._cursor_inside

        x, y = pos
        win_x, win_y = origin

        if self.revealer.get_child_revealed() and self._widget_contains_point(self.explorer_box, win_x, win_y, x, y):
            return True

        return self._widget_contains_point(self.activator, win_x, win_y, x, y)

    def _find_folder_at_position(self, root_x: int, root_y: int) -> Optional[Path]:
        origin = self._get_window_origin()
        if not origin:
            return None
        win_x, win_y = origin

        for widget, path in self._folder_widgets:
            if self._widget_contains_point(widget, win_x, win_y, root_x, root_y):
                return path
        return None

    def _cancel_activator_hover_timer(self):
        self._cancel_timer('_activator_hover_timer')

    def _on_activator_enter(self, widget, event):
        self._cancel_pending_hide()
        self._cursor_inside = True
        self._cursor_over_activator = True
        self._cancel_activator_hover_timer()
        self._activator_hover_timer = GLib.timeout_add(
            self.ACTIVATOR_HOVER_DELAY, self._on_activator_hover_timeout)
        return True

    def _on_activator_leave(self, widget, event):
        self._cursor_over_activator = False
        self._cancel_activator_hover_timer()
        if event.detail != Gdk.NotifyType.INFERIOR and not self.revealer.get_child_revealed():
            self._cursor_inside = False
        return True

    def _on_activator_hover_timeout(self):
        self._activator_hover_timer = None
        if not self._cursor_over_activator:
            return False

        self._update_window_margin(True)
        self.revealer.set_reveal_child(True)

        if self._is_terminal_open():
            self._set_keyboard_interactive(True)
            tid = self.active_terminal_id
            if tid and tid in self.terminals:
                GLib.idle_add(self.terminals[tid]['vte'].grab_focus)
        return False

    def _on_explorer_enter(self, widget, event):
        self._cancel_pending_hide()
        self._cancel_activator_hover_timer()
        self._cursor_inside = True
        return True

    def _on_explorer_leave(self, widget, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return True
        self._cursor_inside = False
        self._schedule_hide()
        return True

    def _schedule_hide(self):
        self._cancel_pending_hide()
        self._pending_hide = GLib.timeout_add(300, self._do_hide)

    def _cancel_pending_hide(self):
        self._cancel_timer('_pending_hide')

    def _do_hide(self):
        self._pending_hide = None
        if self._should_stay_visible() or self._is_cursor_over_explorer():
            self._cursor_inside = True
            return False

        self._dismiss_focus()
        self.revealer.set_reveal_child(False)
        self._icon_cache.clear()
        GLib.timeout_add(350, self._check_and_restore_margin)
        return False

    def _force_restore_margin(self):
        if self._should_block_margin_restore():
            return False
        self._menu_open = False
        self._navigation_lock = False
        self._dismiss_focus()
        if self.revealer.get_child_revealed():
            self.revealer.set_reveal_child(False)
        self._update_window_margin(False)
        return False

    def _check_and_restore_margin(self):
        if self._should_block_margin_restore():
            return False
        if not self.revealer.get_child_revealed() and not self._cursor_inside:
            self._update_window_margin(False)
        else:
            self._force_restore_margin()
        return False

    def _on_explorer_button_press(self, widget, event):
        if event.button in (1, 3) and self.search_entry.has_focus():
            try:
                alloc = self.search_entry.get_allocation()
                ok, ex, ey = self.explorer_eb.translate_coordinates(
                    self.search_entry, int(event.x), int(event.y))
                if ok and 0 <= ex <= alloc.width and 0 <= ey <= alloc.height:
                    return False
            except Exception:
                pass
            self._clear_search_focus()
        return False

    def _init_ui(self):
        self.activator = EventBox(name="explorer-activator")
        self.activator.add(Box(style="background: transparent;"))
        self.activator.set_size_request(15, -1)
        self.activator.set_valign(Gtk.Align.FILL)
        self.activator.set_margin_top(self._top_margin_closed)
        self.activator.add_events(_ACTIVATOR_EVENTS)
        self.activator.connect("enter-notify-event", self._on_activator_enter)
        self.activator.connect("leave-notify-event", self._on_activator_leave)
        self._setup_activator_drop_target()

        self._build_explorer_content()

        self.explorer_eb = EventBox(name="explorer-eventbox")
        self.explorer_eb.add(self.explorer_box)
        self.explorer_eb.add_events(_EXPLORER_EVENTS)
        self.explorer_eb.connect("enter-notify-event", self._on_explorer_enter)
        self.explorer_eb.connect("leave-notify-event", self._on_explorer_leave)
        self.explorer_eb.connect("motion-notify-event", self._on_explorer_motion)
        self.explorer_eb.connect("button-release-event", self._on_explorer_button_release)
        self.explorer_eb.connect("button-press-event", self._on_explorer_button_press)
        self._setup_explorer_drop_tracking()

        self.revealer = Revealer(
            name="explorer-revealer", transition_type="slide-right",
            transition_duration=350, child_revealed=False, child=self.explorer_eb)

        self.add(Box(
            name="explorer-main", orientation="h", v_expand=True,
            children=[self.activator, self.revealer]))
        self._update_window_margin(False)

    def _build_explorer_content(self):
        target_w = int(self._mon_w * 0.38)
        self._explorer_width = max(550, min(target_w, 900))

        self.header = self._build_header()
        self.path_bar = self._build_path_bar()
        self.sidebar = self._build_sidebar()

        file_panel = self._build_file_view()
        self.file_view = self.files_scrolled

        content_box = Box(
            name="explorer-content", orientation="h",
            h_expand=True, v_expand=True,
            children=[self.sidebar, file_panel])

        terminal_view = self._build_terminal_view()

        self.stack = self._make_stack(Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(content_box, "files")
        self.stack.add_named(terminal_view, "terminal")

        self.files_status_bar = self._build_status_bar()
        self.terminal_tab_bar = self._build_terminal_tab_bar()

        self.bottom_bar_stack = self._make_stack(
            Gtk.StackTransitionType.CROSSFADE, expand=False)
        self.bottom_bar_stack.add_named(self.files_status_bar, "files")
        self.bottom_bar_stack.add_named(self.terminal_tab_bar, "terminal")

        self.explorer_box = Box(
            name="explorer-container", orientation="v", v_expand=True,
            children=[self.header, self.path_bar, self.stack, self.bottom_bar_stack])
        self.explorer_box.set_size_request(self._explorer_width, -1)

    @staticmethod
    def _make_stack(transition=Gtk.StackTransitionType.CROSSFADE, duration=200, expand=True):
        s = Gtk.Stack()
        s.set_homogeneous(False)
        s.set_transition_type(transition)
        s.set_transition_duration(duration)
        if expand:
            s.set_hexpand(True)
            s.set_vexpand(True)
        return s

    def _build_status_bar(self):
        self.status_label = Label(
            name="explorer-status-label", label="", h_align="start", h_expand=True)
        self.dnd_indicator = Label(
            name="explorer-dnd-indicator", label="", h_align="center")
        self.size_label = Label(
            name="explorer-size-label", label="", h_align="end")
        return Box(
            name="explorer-status-bar", orientation="h",
            children=[self.status_label, self.dnd_indicator, self.size_label])

    def _build_header(self):
        nav_defs = [
            ("go-previous-symbolic", "Back", self._on_back_clicked),
            ("go-next-symbolic", "Forward", self._on_forward_clicked),
            ("go-up-symbolic", "Parent folder", self._on_up_clicked),
            ("go-home-symbolic", "Home", self._on_home_clicked),
        ]
        nav_buttons = []
        handler_ref = self._clear_focus_on_click
        for icon, tip, handler in nav_defs:
            btn = Button(
                name="explorer-nav-btn",
                child=Image(icon_name=icon, icon_size=16),
                tooltip_text=tip, on_clicked=handler)
            btn.connect("button-press-event", handler_ref)
            nav_buttons.append(btn)

        self.btn_back, self.btn_forward, self.btn_up, self.btn_home = nav_buttons
        nav_box = Box(name="explorer-nav-box", orientation="h", spacing=4, children=nav_buttons)

        self.search_entry = Gtk.SearchEntry(name="explorer-search-entry")
        self.search_entry.set_hexpand(True)
        self.search_entry.set_halign(Gtk.Align.FILL)
        self.search_entry.set_alignment(0.0)

        self.folder_label = Gtk.Label(name="explorer-search-placeholder")
        self.folder_label.set_halign(Gtk.Align.END)
        self.folder_label.set_valign(Gtk.Align.CENTER)

        self.search_overlay = Gtk.Overlay()
        self.search_overlay.add(self.search_entry)
        self.search_overlay.add_overlay(self.folder_label)
        self.search_overlay.set_overlay_pass_through(self.folder_label, True)

        self.search_entry.connect("button-press-event", self._on_search_clicked)
        self.search_entry.connect("focus-out-event", self._on_search_focus_out)
        self.search_entry.connect("key-press-event", self._on_search_key_press)
        self.search_entry.connect("search-changed", self._on_search_changed)

        self.btn_eject = Button(
            name="explorer-eject-header-btn",
            child=Image(icon_name="media-eject-symbolic", icon_size=16),
            tooltip_text="Eject device", on_clicked=self._on_header_eject_clicked)
        self.btn_eject.set_no_show_all(True)
        self.btn_eject.hide()

        action_defs = [
            ("utilities-terminal-symbolic", "Open Terminal", self._on_terminal_clicked, "btn_terminal"),
            ("view-more-symbolic", "Show hidden files", self._on_toggle_hidden, "btn_hidden"),
            ("view-pin-symbolic", "Pin explorer", self._on_pin_clicked, "btn_pin"),
        ]
        action_btns = [self.btn_eject]
        for icon, tip, handler, attr in action_defs:
            btn = Button(
                name="explorer-nav-btn",
                child=Image(icon_name=icon, icon_size=16),
                tooltip_text=tip, on_clicked=handler)
            btn.connect("button-press-event", handler_ref)
            setattr(self, attr, btn)
            action_btns.append(btn)

        self.btn_eject.connect("button-press-event", handler_ref)

        action_box = Box(name="explorer-nav-box", orientation="h", spacing=4, children=action_btns)

        return Box(
            name="explorer-header", orientation="h", spacing=8,
            children=[nav_box, self.search_overlay, action_box])

    def _on_search_clicked(self, widget, event):
        if event.button != 1:
            return False
        self._set_keyboard_interactive(True)
        self._cancel_pending_hide()
        GLib.idle_add(lambda: (self.present(), self.set_focus(self.search_entry),
                               self.search_entry.grab_focus()) or False)
        return False

    def _on_search_focus_out(self, widget, event):
        if not self._is_terminal_open():
            self._set_keyboard_interactive(False)
        return False

    def _on_search_key_press(self, widget, event):
        kv = event.keyval
        if kv == Gdk.KEY_Escape:
            self.search_entry.set_text("")
            self._clear_search_focus()
            return True
        if kv in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self._is_terminal_open():
            self._terminal_search_next()
            return True
        return False

    def _on_search_changed(self, entry):
        text = entry.get_text()

        if self._is_terminal_open():
            self.folder_label.set_visible(not text)
            self._do_terminal_search(text)
            return

        text_lower = text.lower()
        self.folder_label.set_visible(not text_lower)

        has_files = visible = 0
        for child in self.files_container.get_children():
            key = getattr(child, '_search_key', None)
            if key is not None:
                has_files = 1
                match = text_lower in key
                child.set_visible(match)
                visible += match

        self._show_search_empty_state(bool(has_files and text_lower and not visible))

    def _show_search_empty_state(self, show: bool):
        children = self.files_container.get_children()
        search_empty = None
        for c in children:
            if c.get_name() == "explorer-search-empty-state":
                search_empty = c
                break

        if show:
            if not search_empty:
                search_empty = self._make_centered_state(
                    "edit-find-symbolic", "No results found", "explorer-search-empty-state")
                self.files_container.pack_start(search_empty, True, True, 0)
            search_empty.show_all()
        elif search_empty:
            search_empty.hide()

    def _make_centered_state(self, icon: str, text: str, name: str) -> Box:
        inner = Box(
            orientation="v", h_align="center", v_align="center", spacing=12,
            children=[
                Image(icon_name=icon, icon_size=48, name="explorer-empty-icon"),
                Label(name="explorer-empty-label", label=text),
            ])
        wrapper = Box(
            name=name, orientation="v",
            h_expand=True, v_expand=True, h_align="fill", v_align="fill")
        wrapper.set_size_request(-1, -1)
        wrapper.pack_start(Box(v_expand=True), True, True, 0)
        wrapper.pack_start(inner, False, False, 0)
        wrapper.pack_start(Box(v_expand=True), True, True, 0)
        return wrapper

    def _build_path_bar(self):
        self.path_container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.path_container.set_name("explorer-path-container")

        path_icon = Image(icon_name="folder-symbolic", icon_size=16, name="explorer-path-icon")

        self.path_scroll = Gtk.ScrolledWindow()
        self.path_scroll.set_name("explorer-path-scroll")
        self.path_scroll.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        self.path_scroll.set_min_content_height(28)
        self.path_scroll.set_hexpand(True)
        self.path_scroll.set_propagate_natural_width(False)

        adj = self.path_scroll.get_hadjustment()
        adj.connect("changed", self._on_path_adj_changed)

        viewport = Gtk.Viewport()
        viewport.set_shadow_type(Gtk.ShadowType.NONE)
        viewport.add(self.path_container)
        self.path_scroll.add(viewport)

        scroll_eb = Gtk.EventBox()
        scroll_eb.add(self.path_scroll)
        scroll_eb.add_events(
            Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK)
        scroll_eb.connect("scroll-event", self._on_path_scroll)
        scroll_eb.connect("button-press-event", self._clear_focus_on_click)

        bar = Box(name="explorer-path-bar", orientation="h", spacing=4)
        bar.pack_start(path_icon, False, False, 0)
        bar.pack_start(scroll_eb, True, True, 0)
        return bar

    def _on_path_adj_changed(self, adj):
        if self._force_path_scroll:
            adj.set_value(max(adj.get_lower(), adj.get_upper() - adj.get_page_size()))
            self._force_path_scroll = False

    def _on_path_scroll(self, widget, event):
        adj = self.path_scroll.get_hadjustment()
        if not adj:
            return False

        step = 30
        d = event.direction
        if d in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.LEFT):
            delta = -step
        elif d in (Gdk.ScrollDirection.DOWN, Gdk.ScrollDirection.RIGHT):
            delta = step
        elif d == Gdk.ScrollDirection.SMOOTH:
            _, dx, dy = event.get_scroll_deltas()
            delta = int((dx or dy) * step)
        else:
            return False

        if delta:
            lower = adj.get_lower()
            upper_bound = adj.get_upper() - adj.get_page_size()
            new_val = adj.get_value() + delta
            if new_val < lower:
                new_val = lower
            elif new_val > upper_bound:
                new_val = upper_bound
            adj.set_value(new_val)
            return True
        return False

    def _update_path_bar(self):
        container = self.path_container
        for child in container.get_children():
            child.destroy()

        cur = self._current_path
        folder_name = "Trash" if self._is_in_trash() else (cur.name or "Root")
        self.folder_label.set_label(folder_name)
        self.search_entry.set_text("")
        self.folder_label.show()

        parts = []
        p = cur
        while p != p.parent:
            parts.append(p)
            p = p.parent
        parts.append(Path("/"))
        parts.reverse()

        pack = container.pack_start
        switch_and_click = lambda b: (self._switch_to_files(), self._on_path_part_clicked(b))
        focus_handler = self._clear_focus_on_click

        for i, part in enumerate(parts):
            if i:
                sep = Gtk.Label(label="/")
                sep.set_name("explorer-path-separator")
                pack(sep, False, False, 0)

            lbl = Gtk.Label(label="/" if part == Path("/") else part.name)
            lbl.set_name("explorer-path-part-label")
            btn = Gtk.Button()
            btn.set_name("explorer-path-part")
            btn.add(lbl)
            btn._path = part
            btn.connect("clicked", lambda b: switch_and_click(b))
            btn.connect("button-press-event", focus_handler)
            self._setup_path_part_as_drop_target(btn, part)
            pack(btn, False, False, 0)

        self._force_path_scroll = True
        container.show_all()

    def _scroll_path_to_end(self):
        try:
            adj = self.path_scroll.get_hadjustment()
            if adj:
                adj.set_value(adj.get_upper() - adj.get_page_size())
        except Exception:
            pass
        return False

    def _build_sidebar(self):
        self.sidebar_content = Box(
            name="explorer-sidebar-content", orientation="v", spacing=2)
        self.sidebar_content.add(
            Label(name="explorer-sidebar-header", label="Places", h_align="start"))

        for icon_name, label, path in self._bookmarks:
            self.sidebar_content.add(self._create_bookmark_button(icon_name, label, path))

        self.trash_button_container = Box(name="explorer-trash-container", orientation="v")
        self._update_trash_button()
        self.sidebar_content.add(self.trash_button_container)

        sep = Box(name="explorer-sidebar-separator")
        sep.set_size_request(-1, 1)
        self.sidebar_content.add(sep)

        self.sidebar_content.add(
            Label(name="explorer-sidebar-header", label="Devices", h_align="start"))
        self._devices_container = Box(
            name="explorer-devices-container", orientation="v", spacing=2)
        self.sidebar_content.add(self._devices_container)

        self.sidebar_eb = Gtk.EventBox()
        self.sidebar_eb.add(self.sidebar_content)
        self.sidebar_eb.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.sidebar_eb.connect("button-press-event", self._clear_focus_on_click)

        return ScrolledWindow(
            name="explorer-sidebar", h_scrollbar_policy="never",
            v_scrollbar_policy="automatic", min_content_width=130,
            v_expand=True, min_content_height=100,
            child=self.sidebar_eb)

    def _create_bookmark_button(self, icon_name, label_text, path):
        icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.LARGE_TOOLBAR)
        icon.set_pixel_size(24)
        icon.set_name("explorer-bookmark-icon")

        name_label = Gtk.Label(label=label_text)
        name_label.set_name("explorer-file-name")
        name_label.set_halign(Gtk.Align.START)
        name_label.set_hexpand(True)
        name_label.set_ellipsize(3)

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        content.pack_start(icon, False, False, 0)
        content.pack_start(name_label, True, True, 0)

        btn = Gtk.Button()
        btn.set_name("explorer-file-row")
        btn.add(content)
        btn._path = path
        btn.connect("clicked",
                     lambda b: (self._switch_to_files(), self._on_bookmark_clicked(b)))
        btn.connect("button-press-event", self._clear_focus_on_click)
        btn.get_style_context().add_class("directory")
        btn.show_all()
        self._setup_drop_target(btn, target_path=path)
        return btn

    def _create_trash_button(self):
        return self._create_bookmark_button("user-trash-symbolic", "Trash", self._trash_path)

    def _create_clear_trash_button(self):
        icon = Gtk.Image.new_from_icon_name(
            "user-trash-full-symbolic", Gtk.IconSize.LARGE_TOOLBAR)
        icon.set_pixel_size(24)
        icon.set_name("explorer-clear-icon")

        name_label = Gtk.Label(label="Empty Trash")
        name_label.set_name("explorer-clear-label")
        name_label.set_halign(Gtk.Align.CENTER)
        name_label.set_hexpand(True)

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        content.pack_start(icon, False, False, 0)
        content.pack_start(name_label, True, True, 0)

        btn = Gtk.Button()
        btn.set_name("explorer-clear-trash-btn")
        btn.add(content)
        btn.connect("clicked", self._on_clear_trash_clicked)
        btn.connect("button-press-event", self._clear_focus_on_click)
        btn.show_all()
        return btn

    def _update_trash_button(self):
        container = self.trash_button_container
        for child in container.get_children():
            child.destroy()
        btn = self._create_clear_trash_button() if self._is_in_trash() else self._create_trash_button()
        container.add(btn)
        container.show_all()

    def _build_file_view(self):
        self.files_container = Box(
            name="explorer-files-container", orientation="v", spacing=2)
        self.files_container.set_hexpand(True)
        self.files_container.set_vexpand(True)

        self.files_eventbox = Gtk.EventBox()
        self.files_eventbox.add(self.files_container)
        self.files_eventbox.connect("button-press-event", self._on_files_area_button_press)
        self.files_eventbox.set_hexpand(True)
        self.files_eventbox.set_vexpand(True)

        self.files_scrolled = ScrolledWindow(
            name="explorer-file-view", h_scrollbar_policy="never",
            v_scrollbar_policy="automatic", child=self.files_eventbox,
            v_expand=True, h_expand=True)
        self.files_scrolled.set_min_content_height(100)

        targets = self._dnd_target_entries
        self.files_scrolled.drag_dest_set(
            Gtk.DestDefaults.MOTION | Gtk.DestDefaults.HIGHLIGHT | Gtk.DestDefaults.DROP,
            targets, _DRAG_ACTIONS)
        self.files_scrolled.connect("drag-motion", self._on_file_view_drag_motion)
        self.files_scrolled.connect("drag-leave", self._on_file_view_drag_leave)
        self.files_scrolled.connect("drag-drop", self._h_dst_drop, None)
        self.files_scrolled.connect("drag-data-received", self._h_dst_recv, None)

        self.app_chooser_box = Box(
            name="explorer-file-view", orientation="v", spacing=4)
        self.app_chooser_box.set_hexpand(True)
        self.app_chooser_box.set_vexpand(True)

        self.file_view_stack = self._make_stack(
            Gtk.StackTransitionType.CROSSFADE, 150)
        self.file_view_stack.add_named(self.files_scrolled, "files")
        self.file_view_stack.add_named(self.app_chooser_box, "app_chooser")

        return self.file_view_stack

    def _on_files_area_button_press(self, widget, event):
        self._clear_search_focus()
        if event.button == 3:
            self._show_background_context_menu(event)
            return True
        return False

    def _on_file_view_drag_motion(self, widget, context, x, y, time):
        self._cancel_pending_hide()
        self._drag_over_explorer = True
        self._scroll_update(y)

        is_copy = self._algo_is_copy(Gdk.Keymap.get_default().get_modifier_state())
        Gdk.drag_status(
            context,
            Gdk.DragAction.COPY if is_copy else Gdk.DragAction.MOVE, time)

        name = self._current_path.name or "here"
        self._vis_indicator(f"{self._algo_action(is_copy)} to {name}", is_copy)
        return True

    def _on_file_view_drag_leave(self, widget, context, time):
        self._scroll_stop()

    def _load_directory(self):
        if self._is_loading:
            return
        self._is_loading = True

        self._folder_widgets.clear()
        container = self.files_container
        for child in container.get_children():
            child.destroy()

        items = []
        show_hidden = self._show_hidden
        try:
            with os.scandir(self._current_path) as it:
                for entry in it:
                    if not show_hidden and entry.name.startswith('.'):
                        continue
                    items.append(entry)
        except OSError:
            self._is_loading = False
            return

        items.sort(key=lambda x: (x.name.startswith('.'), x.is_file(), x.name.lower()))

        if not items:
            self._show_empty_state()
        else:
            add = container.add
            fw_append = self._folder_widgets.append
            for entry in items:
                row = self._create_file_row(entry)
                add(row)
                if row._is_dir:
                    fw_append((row, row._path))
            container.show_all()
            self._on_search_changed(self.search_entry)

        dir_count = sum(1 for i in items if i.is_dir())
        self.status_label.set_label(f"{dir_count} folders, {len(items) - dir_count} files")
        self._is_loading = False

    def _show_empty_state(self):
        in_trash = self._is_in_trash()
        icon = "user-trash-symbolic" if in_trash else "folder-open-symbolic"
        text = "Trash is empty" if in_trash else "Folder is empty"
        wrapper = self._make_centered_state(icon, text, "explorer-empty-state")
        self.files_container.pack_start(wrapper, True, True, 0)
        self.files_container.show_all()

    def _create_file_row(self, entry: os.DirEntry):
        is_dir = entry.is_dir()
        path = Path(entry.path)

        icon = Image(
            icon_name=self._get_icon_for_path(path, is_dir),
            icon_size=24, name="explorer-file-icon")

        name_label = Label(
            name="explorer-file-name", label=entry.name,
            h_align="start", h_expand=True, ellipsize="end")

        try:
            st = entry.stat()
            date_text = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            st_size = st.st_size
        except OSError:
            date_text = ""
            st_size = -1

        if is_dir:
            size_text = self._format_item_count(self._count_folder_items(path))
        else:
            size_text = self._format_size(st_size) if st_size >= 0 else "—"

        size_label = Label(name="explorer-file-size", label=size_text, h_align="end")

        date_label = Label(name="explorer-file-date", label=date_text, h_align="end")
        date_label.set_margin_start(24)

        content = Box(
            orientation="h", spacing=8,
            children=[icon, name_label, size_label, date_label])

        btn = Button(name="explorer-file-row", child=content)
        btn._path = path
        btn._is_dir = is_dir
        btn._search_key = entry.name.lower()

        btn.connect("clicked",
                     lambda b: (b._is_dir and self._switch_to_files(), self._on_file_clicked(b)))
        btn.connect("button-press-event", self._clear_focus_on_click)
        btn.connect("button-press-event", self._on_file_button_press)

        self._setup_drag_source(btn, path)
        if is_dir:
            self._setup_drop_target(btn, target_path=path)
            btn.get_style_context().add_class("directory")

        return btn

    def _get_icon_for_path(self, path: Path, is_dir: bool = None) -> str:
        if is_dir is None:
            is_dir = path.is_dir()
        if is_dir:
            return self._xdg_icon_map.get(path, _FOLDER_ICONS.get(path.name, "folder-symbolic"))

        suffix = path.suffix.lower()
        cached = self._icon_cache.get(suffix)
        if cached is not None:
            return cached

        icon_name = "text-x-generic-symbolic"
        try:
            content_type, _ = Gio.content_type_guess(str(path), None)
            if content_type:
                gi_icon = Gio.content_type_get_symbolic_icon(content_type)
                if isinstance(gi_icon, Gio.ThemedIcon):
                    theme = self._icon_theme
                    for name in gi_icon.get_names():
                        if theme.has_icon(name):
                            icon_name = name
                            break
        except Exception:
            pass

        self._icon_cache[suffix] = icon_name
        return icon_name

    @staticmethod
    def _count_folder_items(path: Path) -> int:
        try:
            count = 0
            with os.scandir(path) as it:
                for _ in it:
                    count += 1
            return count
        except OSError:
            return -1

    @staticmethod
    def _format_item_count(count: int) -> str:
        if count < 0:
            return "—"
        if count == 0:
            return "empty"
        return "1 item" if count == 1 else f"{count} items"

    @staticmethod
    def _format_size(size) -> str:
        if not isinstance(size, (int, float)) or size < 0:
            return "—"
        if size == 0:
            return "0 B"
        s = float(size)
        for i, unit in enumerate(_SIZE_UNITS):
            if s < 1024.0 or i == len(_SIZE_UNITS) - 1:
                return f"{int(s)} B" if i == 0 else f"{s:.1f} {unit}"
            s /= 1024.0
        return "—"

    def _get_unique_path(self, path: Path) -> Path:
        if not path.exists():
            return path
        stem, suffix, parent = path.stem, path.suffix, path.parent
        i = 1
        candidate = parent / f"{stem} ({i}){suffix}"
        while candidate.exists():
            i += 1
            candidate = parent / f"{stem} ({i}){suffix}"
        return candidate

    def destroy(self):
        self._close_rename_widget()
        self._close_app_chooser()
        self._monitor_cleanup()
        self._cancel_all_timers()
        self._hover_cancel()
        self._grace_cancel()
        self._pending_cleanup()
        self._scroll_stop()
        self._volume_monitor = None
        self._icon_cache.clear()
        self._folder_widgets.clear()
        super().destroy()