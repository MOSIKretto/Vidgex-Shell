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
    DRAG_SCROLL_INTERVAL = 16  # ~60 FPS


    def __init__(self, monitor_id: int = 0, **kwargs):
        self.monitor_id = monitor_id
        self._mon_w = 1920
        self._mon_h = 1080
        self._top_margin_closed = 50

        # Visibility / panel state
        self._is_pinned = False
        self._is_hidden = True
        self._cursor_inside = False
        self._menu_open = False
        self._is_loading = False
        self._pending_hide: Optional[int] = None

        # Navigation
        home = Path.home()
        self._current_path = home
        self._history: List[Path] = [home]
        self._history_index = 0
        self._show_hidden = False
        self._navigation_lock = False
        self._navigation_lock_timer: Optional[int] = None

        # File monitor
        self._file_monitor: Optional[Gio.FileMonitor] = None
        self._pending_refresh: Optional[int] = None
        self._refresh_debounce_ms = 250

        # DnD state
        self._dnd_targets = [
            Gtk.TargetEntry.new("text/uri-list", 0, self.TARGET_URI_LIST),
            Gtk.TargetEntry.new("text/plain", 0, self.TARGET_TEXT),
        ]
        self._drag_source_path: Optional[Path] = None
        self._drag_in_progress = False
        self._drag_over_explorer = False
        self._post_drag_grace = False
        self._post_drag_timer: Optional[int] = None
        self._drag_hover_timer: Optional[int] = None
        self._drag_hover_path: Optional[Path] = None
        self._drag_hover_widget: Optional[Gtk.Widget] = None
        self._pending_drop_source: Optional[Path] = None
        self._pending_drop_target: Optional[Path] = None
        self._pending_hover_timer: Optional[int] = None
        self._pending_hover_path: Optional[Path] = None
        self._folder_widgets: List[Tuple[Gtk.Widget, Path]] = []
        
        # DnD Auto-Scroll State
        self._drag_scroll_timer: Optional[int] = None
        self._drag_scroll_speed: float = 0.0

        # Activator
        self._activator_hover_timer: Optional[int] = None
        self._cursor_over_activator = False

        # Trash
        self._trash_path = home / ".local/share/Trash/files"
        self._trash_info_path = home / ".local/share/Trash/info"

        # Devices
        self._volume_monitor: Optional[Gio.VolumeMonitor] = None
        self._devices_container: Optional[Box] = None
        self._current_mount: Optional[Gio.Mount] = None
        self._current_mount_path: Optional[Path] = None
        self._current_mount_name: Optional[str] = None

        # Rename
        self._rename_widget: Optional[Gtk.Box] = None
        self._rename_path: Optional[Path] = None

        # Clipboard
        self._clipboard_paths: List[Path] = []
        self._clipboard_is_cut: bool = False

        # App chooser
        self._app_chooser_active: bool = False
        self._app_chooser_path: Optional[Path] = None
        self._app_chooser_content_type: Optional[str] = None
        self._app_chooser_recommended: List = []
        self._app_chooser_other: List = []
        self._app_chooser_remaining: List = []
        self._app_list_container: Optional[Gtk.Box] = None
        self._app_search_entry: Optional[Gtk.SearchEntry] = None

        # Archives
        self._archive_extensions_simple = frozenset({
            '.zip', '.tar', '.rar', '.7z', '.gz', '.bz2', '.xz',
            '.zst', '.lz4', '.lzma', '.sz',
        })
        self._archive_extensions_compound = (
            '.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tbz',
            '.tar.xz', '.txz', '.tar.zst', '.tzst',
            '.tar.lz4', '.tlz4', '.tar.lzma', '.tlzma', '.tar.sz',
        )
        self._compression_formats = [
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

        # Bookmarks
        self._bookmarks: List[Tuple[str, str, Path]] = [
            ("user-home-symbolic", "Home", home),
            ("user-desktop-symbolic", "Desktop", home / "Desktop"),
            ("folder-documents-symbolic", "Documents", home / "Documents"),
            ("folder-download-symbolic", "Downloads", home / "Downloads"),
            ("folder-pictures-symbolic", "Pictures", home / "Pictures"),
            ("folder-music-symbolic", "Music", home / "Music"),
            ("folder-videos-symbolic", "Videos", home / "Videos"),
            ("drive-harddisk-symbolic", "Root", Path("/")),
        ]

        super().__init__(
            name="explorer-window", layer="overlay", anchor="left top bottom",
            margin="0px 0px 0px 0px", exclusivity="none", monitor=monitor_id,
            visible=False, **kwargs,
        )

        self._icon_theme = Gtk.IconTheme.get_default()
        self._update_monitor()
        self._init_ui()
        GLib.timeout_add(100, self._delayed_show)

    def _should_stay_visible(self) -> bool:
        return (self._is_pinned or self._menu_open or
                self._drag_in_progress or self._drag_over_explorer or
                self._pending_drop_source or self._post_drag_grace or
                self._navigation_lock)

    def _should_block_margin_restore(self) -> bool:
        return (self._is_pinned or self._drag_in_progress or
                self._drag_over_explorer or self._pending_drop_source or
                self._post_drag_grace)

    def _is_in_trash(self) -> bool:
        try:
            return self._current_path.resolve() == self._trash_path.resolve()
        except Exception:
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
            margin = 0 if is_open else self._top_margin_closed
            GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, margin)
        except Exception as e:
            print(f"Margin error: {e}")

    def _set_keyboard_interactive(self, enabled: bool):
        try:
            mode = GtkLayerShell.KeyboardMode.EXCLUSIVE if enabled else GtkLayerShell.KeyboardMode.NONE
            GtkLayerShell.set_keyboard_mode(self, mode)
        except Exception as e:
            print(f"Keyboard mode error: {e}")

    def _update_monitor(self):
        display = Gdk.Display.get_default()
        if display:
            monitor = display.get_monitor(self.monitor_id)
            if monitor:
                geom = monitor.get_geometry()
                self._mon_w = geom.width
                self._mon_h = geom.height
        self._top_margin_closed = int(self._mon_h * 0.08)

    def _delayed_show(self):
        self.show_all()
        self._navigate_to(self._current_path)
        self._setup_volume_monitor()
        return False

    def _init_ui(self):
        self.activator = EventBox(name="explorer-activator")
        self.activator.add(Box(style="background: transparent;"))
        
        self.activator.set_size_request(15, -1)
        self.activator.set_valign(Gtk.Align.FILL)
        self.activator.set_margin_top(self._top_margin_closed)
        
        self.activator.connect("enter-notify-event", self._on_activator_enter)
        self.activator.connect("leave-notify-event", self._on_activator_leave)
        self._setup_activator_drop_target()

        self._build_explorer_content()

        self.explorer_eb = EventBox(name="explorer-eventbox")
        self.explorer_eb.add(self.explorer_box)
        self.explorer_eb.add_events(
            Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK)
        
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


    def _get_cursor_position(self) -> Optional[Tuple[int, int]]:
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

    def _is_cursor_over_explorer(self) -> bool:
        pos = self._get_cursor_position()
        if not pos:
            return self._cursor_inside
        
        x, y = pos
        try:
            window = self.get_window()
            if not window:
                return self._cursor_inside
                
            origin = window.get_origin()
            win_x, win_y = (origin[1], origin[2]) if len(origin) == 3 else origin

            if self.revealer.get_child_revealed():
                alloc = self.explorer_box.get_allocation()
                ok, ex, ey = self.explorer_box.translate_coordinates(self, 0, 0)
                if ok:
                    ax, ay = win_x + ex, win_y + ey
                    if ax <= x <= ax + alloc.width and ay <= y <= ay + alloc.height:
                        return True

            alloc = self.activator.get_allocation()
            ok, ex, ey = self.activator.translate_coordinates(self, 0, 0)
            if ok:
                ax, ay = win_x + ex, win_y + ey
                if ax <= x <= ax + alloc.width and ay <= y <= ay + alloc.height:
                    return True
                    
        except Exception:
            pass
        return False

    def _find_folder_at_position(self, root_x: int, root_y: int) -> Optional[Path]:
        try:
            window = self.get_window()
            if not window:
                return None
            origin = window.get_origin()
            win_x, win_y = (origin[1], origin[2]) if len(origin) == 3 else origin
        except Exception:
            return None

        for widget, path in self._folder_widgets:
            try:
                if not widget.get_mapped():
                    continue
                alloc = widget.get_allocation()
                ok, wx, wy = widget.translate_coordinates(self, 0, 0)
                if not ok:
                    continue
                ax, ay = win_x + wx, win_y + wy
                if ax <= root_x <= ax + alloc.width and ay <= root_y <= ay + alloc.height:
                    return path
            except Exception:
                continue
        return None

    def _cancel_activator_hover_timer(self):
        if self._activator_hover_timer:
            GLib.source_remove(self._activator_hover_timer)
            self._activator_hover_timer = None

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
        if event.detail == Gdk.NotifyType.INFERIOR:
            return True
            
        if not self.revealer.get_child_revealed():
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
        if self._pending_hide:
            GLib.source_remove(self._pending_hide)
            self._pending_hide = None

    def _do_hide(self):
        self._pending_hide = None
        if self._should_stay_visible() or self._is_cursor_over_explorer():
            self._cursor_inside = True
            return False
            
        self._dismiss_focus()
        self.revealer.set_reveal_child(False)
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

    def _build_explorer_content(self):
        target_width = int(self._mon_w * 0.38)
        self._explorer_width = max(550, min(target_width, 900))

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

        self.stack = Gtk.Stack()
        self.stack.set_homogeneous(False)
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)
        self.stack.add_named(content_box, "files")
        self.stack.add_named(terminal_view, "terminal")

        self.files_status_bar = self._build_status_bar()
        self.terminal_tab_bar = self._build_terminal_tab_bar()

        self.bottom_bar_stack = Gtk.Stack()
        self.bottom_bar_stack.set_homogeneous(False)
        self.bottom_bar_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.bottom_bar_stack.add_named(self.files_status_bar, "files")
        self.bottom_bar_stack.add_named(self.terminal_tab_bar, "terminal")

        self.explorer_box = Box(
            name="explorer-container", orientation="v", v_expand=True,
            children=[self.header, self.path_bar, self.stack, self.bottom_bar_stack])
        self.explorer_box.set_size_request(self._explorer_width, -1)

    def _build_status_bar(self):
        self.status_label = Label(name="explorer-status-label", label="", h_align="start", h_expand=True)
        self.dnd_indicator = Label(name="explorer-dnd-indicator", label="", h_align="center")
        self.size_label = Label(name="explorer-size-label", label="", h_align="end")
        
        return Box(
            name="explorer-status-bar", orientation="h",
            children=[self.status_label, self.dnd_indicator, self.size_label])

    def _build_header(self):
        self.btn_back = Button(
            name="explorer-nav-btn", child=Image(icon_name="go-previous-symbolic", icon_size=16),
            tooltip_text="Back", on_clicked=self._on_back_clicked)
        self.btn_forward = Button(
            name="explorer-nav-btn", child=Image(icon_name="go-next-symbolic", icon_size=16),
            tooltip_text="Forward", on_clicked=self._on_forward_clicked)
        self.btn_up = Button(
            name="explorer-nav-btn", child=Image(icon_name="go-up-symbolic", icon_size=16),
            tooltip_text="Parent folder", on_clicked=self._on_up_clicked)
        self.btn_home = Button(
            name="explorer-nav-btn", child=Image(icon_name="go-home-symbolic", icon_size=16),
            tooltip_text="Home", on_clicked=self._on_home_clicked)

        for btn in (self.btn_back, self.btn_forward, self.btn_up, self.btn_home):
            btn.connect("button-press-event", self._clear_focus_on_click)

        nav_box = Box(
            name="explorer-nav-box", orientation="h", spacing=4,
            children=[self.btn_back, self.btn_forward, self.btn_up, self.btn_home])

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
            name="explorer-eject-header-btn", child=Image(icon_name="media-eject-symbolic", icon_size=16),
            tooltip_text="Eject device", on_clicked=self._on_header_eject_clicked)
        self.btn_eject.set_no_show_all(True)
        self.btn_eject.hide()

        self.btn_terminal = Button(
            name="explorer-nav-btn", child=Image(icon_name="utilities-terminal-symbolic", icon_size=16),
            tooltip_text="Open Terminal", on_clicked=self._on_terminal_clicked)
        self.btn_hidden = Button(
            name="explorer-nav-btn", child=Image(icon_name="view-more-symbolic", icon_size=16),
            tooltip_text="Show hidden files", on_clicked=self._on_toggle_hidden)
        self.btn_pin = Button(
            name="explorer-nav-btn", child=Image(icon_name="view-pin-symbolic", icon_size=16),
            tooltip_text="Pin explorer", on_clicked=self._on_pin_clicked)

        for btn in (self.btn_eject, self.btn_terminal, self.btn_hidden, self.btn_pin):
            btn.connect("button-press-event", self._clear_focus_on_click)

        action_box = Box(
            name="explorer-nav-box", orientation="h", spacing=4,
            children=[self.btn_eject, self.btn_terminal, self.btn_hidden, self.btn_pin])

        return Box(
            name="explorer-header", orientation="h", spacing=8,
            children=[nav_box, self.search_overlay, action_box])

    def _on_search_clicked(self, widget, event):
        if event.button != 1:
            return False
            
        self._set_keyboard_interactive(True)
        self._cancel_pending_hide()

        def force_focus():
            self.present()
            self.set_focus(self.search_entry)
            self.search_entry.grab_focus()
            return False

        GLib.idle_add(force_focus)
        return False

    def _on_search_focus_out(self, widget, event):
        if not self._is_terminal_open():
            self._set_keyboard_interactive(False)
        return False

    def _on_search_key_press(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.search_entry.set_text("")
            self._clear_search_focus()
            return True
            
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self._is_terminal_open():
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

        has_files = False
        visible = 0
        for child in self.files_container.get_children():
            if hasattr(child, '_path'):
                has_files = True
                match = text_lower in child._path.name.lower()
                child.set_visible(match)
                visible += match

        self._show_search_empty_state(has_files and text_lower and visible == 0)

    def _show_search_empty_state(self, show: bool):
        search_empty = next((c for c in self.files_container.get_children() 
                             if c.get_name() == "explorer-search-empty-state"), None)

        if show:
            if not search_empty:
                inner = Box(
                    orientation="v", h_align="center", v_align="center", spacing=12,
                    children=[
                        Image(icon_name="edit-find-symbolic", icon_size=48, name="explorer-empty-icon"),
                        Label(name="explorer-empty-label", label="No results found"),
                    ])
                search_empty = Box(
                    name="explorer-search-empty-state", orientation="v",
                    h_expand=True, v_expand=True, h_align="fill", v_align="fill")
                search_empty.set_size_request(-1, -1)
                search_empty.pack_start(Box(v_expand=True), True, True, 0)
                search_empty.pack_start(inner, False, False, 0)
                search_empty.pack_start(Box(v_expand=True), True, True, 0)
                self.files_container.pack_start(search_empty, True, True, 0)
            search_empty.show_all()
        elif search_empty:
            search_empty.hide()

    def _build_path_bar(self):
        self.path_container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.path_container.set_name("explorer-path-container")

        path_icon = Image(icon_name="folder-symbolic", icon_size=16, name="explorer-path-icon")

        self.path_scroll = Gtk.ScrolledWindow()
        self.path_scroll.set_name("explorer-path-scroll")
        self.path_scroll.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        self.path_scroll.set_min_content_height(28)
        self.path_scroll.set_propagate_natural_width(False)

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

        path_wrapper = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        path_wrapper.set_hexpand(True)
        path_wrapper.pack_start(scroll_eb, True, True, 0)

        bar = Box(name="explorer-path-bar", orientation="h", spacing=4)
        bar.pack_start(path_icon, False, False, 0)
        bar.pack_start(path_wrapper, True, True, 0)
        return bar

    def _on_path_scroll(self, widget, event):
        adj = self.path_scroll.get_hadjustment()
        if not adj:
            return False

        delta = 0
        if event.direction == Gdk.ScrollDirection.UP: delta = -30
        elif event.direction == Gdk.ScrollDirection.DOWN: delta = 30
        elif event.direction == Gdk.ScrollDirection.LEFT: delta = -30
        elif event.direction == Gdk.ScrollDirection.RIGHT: delta = 30
        elif event.direction == Gdk.ScrollDirection.SMOOTH:
            _, dx, dy = event.get_scroll_deltas()
            delta = int((dx if dx else dy) * 30)

        if delta:
            adj.set_value(max(adj.get_lower(),
                              min(adj.get_value() + delta,
                                  adj.get_upper() - adj.get_page_size())))
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
        for i, part in enumerate(parts):
            if i > 0:
                sep = Gtk.Label(label="/")
                sep.set_name("explorer-path-separator")
                pack(sep, False, False, 0)

            name = "/" if part == Path("/") else part.name
            lbl = Gtk.Label(label=name)
            lbl.set_name("explorer-path-part-label")
            btn = Gtk.Button()
            btn.set_name("explorer-path-part")
            btn.add(lbl)
            btn._path = part
            btn.connect("clicked", lambda b: (self._switch_to_files(), self._on_path_part_clicked(b)))
            btn.connect("button-press-event", self._clear_focus_on_click)
            self._setup_path_part_as_drop_target(btn, part)
            pack(btn, False, False, 0)

        container.show_all()
        GLib.idle_add(self._scroll_path_to_end)

    def _scroll_path_to_end(self):
        try:
            adj = self.path_scroll.get_hadjustment()
            if adj:
                adj.set_value(adj.get_upper() - adj.get_page_size())
        except Exception:
            pass
        return False

    def _build_sidebar(self):
        self.sidebar_content = Box(name="explorer-sidebar-content", orientation="v", spacing=2)
        self.sidebar_content.add(Label(name="explorer-sidebar-header", label="Places", h_align="start"))

        for icon_name, label, path in self._bookmarks:
            self.sidebar_content.add(self._create_bookmark_button(icon_name, label, path))

        self.trash_button_container = Box(name="explorer-trash-container", orientation="v")
        self._update_trash_button()
        self.sidebar_content.add(self.trash_button_container)

        separator = Box(name="explorer-sidebar-separator")
        separator.set_size_request(-1, 1)
        self.sidebar_content.add(separator)

        self.sidebar_content.add(Label(name="explorer-sidebar-header", label="Devices", h_align="start"))
        self._devices_container = Box(name="explorer-devices-container", orientation="v", spacing=2)
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
        btn.connect("clicked", lambda b: (self._switch_to_files(), self._on_bookmark_clicked(b)))
        btn.connect("button-press-event", self._clear_focus_on_click)
        btn.get_style_context().add_class("directory")
        btn.show_all()
        self._setup_drop_target(btn, target_path=path)
        return btn

    def _create_trash_button(self):
        return self._create_bookmark_button("user-trash-symbolic", "Trash", self._trash_path)

    def _create_clear_trash_button(self):
        icon = Gtk.Image.new_from_icon_name("user-trash-full-symbolic", Gtk.IconSize.LARGE_TOOLBAR)
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
        self.files_container = Box(name="explorer-files-container", orientation="v", spacing=2)
        self.files_container.set_hexpand(True)
        self.files_container.set_vexpand(True)

        self.files_eventbox = Gtk.EventBox()
        self.files_eventbox.add(self.files_container)
        self.files_eventbox.connect("button-press-event", self._on_files_area_button_press)
        self.files_eventbox.set_hexpand(True)
        self.files_eventbox.set_vexpand(True)

        self.files_scrolled = ScrolledWindow(
            name="explorer-file-view", h_scrollbar_policy="never",
            v_scrollbar_policy="automatic", child=self.files_eventbox, v_expand=True, h_expand=True)
            
        self.files_scrolled.set_min_content_height(100)

        self.files_scrolled.drag_dest_set(
            Gtk.DestDefaults.MOTION | Gtk.DestDefaults.HIGHLIGHT | Gtk.DestDefaults.DROP,
            self._dnd_targets,
            Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
            
        self.files_scrolled.connect("drag-motion", self._on_file_view_drag_motion)
        self.files_scrolled.connect("drag-leave", self._on_file_view_drag_leave)
        self.files_scrolled.connect("drag-drop", self._h_dst_drop, None)
        self.files_scrolled.connect("drag-data-received", self._h_dst_recv, None)

        self.app_chooser_box = Box(name="explorer-file-view", orientation="v", spacing=4)
        self.app_chooser_box.set_hexpand(True)
        self.app_chooser_box.set_vexpand(True)

        self.file_view_stack = Gtk.Stack()
        self.file_view_stack.set_homogeneous(False) # ИСПРАВЛЕНИЕ: Отключаем блокировку высоты
        self.file_view_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.file_view_stack.set_transition_duration(150)
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

        state = Gdk.Keymap.get_default().get_modifier_state()
        is_copy = self._algo_is_copy(state)
        Gdk.drag_status(
            context,
            Gdk.DragAction.COPY if is_copy else Gdk.DragAction.MOVE,
            time)

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

        try:
            items = list(self._current_path.iterdir())
        except OSError:
            self._is_loading = False
            return

        if not self._show_hidden:
            items = [i for i in items if not i.name.startswith('.')]

        items.sort(key=lambda x: (x.name.startswith('.'), x.is_file(), x.name.lower()))

        if not items:
            self._show_empty_state()
        else:
            add = container.add
            fw = self._folder_widgets.append
            for item in items:
                row = self._create_file_row(item)
                add(row)
                if row._is_dir:
                    fw((row, item))
            container.show_all()
            self._on_search_changed(self.search_entry)

        dirs = sum(1 for i in items if i.is_dir())
        self.status_label.set_label(f"{dirs} folders, {len(items) - dirs} files")
        self._is_loading = False

    def _show_empty_state(self):
        in_trash = self._is_in_trash()
        inner = Box(
            orientation="v", h_align="center", v_align="center", spacing=12,
            children=[
                Image(icon_name="user-trash-symbolic" if in_trash else "folder-open-symbolic",
                      icon_size=48, name="explorer-empty-icon"),
                Label(name="explorer-empty-label",
                      label="Trash is empty" if in_trash else "Folder is empty"),
            ])
            
        wrapper = Box(
            name="explorer-empty-state", orientation="v",
            h_expand=True, v_expand=True, h_align="fill", v_align="fill")
            
        # ИСПРАВЛЕНИЕ: Убрали жесткие 350px
        wrapper.set_size_request(-1, -1)
        wrapper.pack_start(Box(v_expand=True), True, True, 0)
        wrapper.pack_start(inner, False, False, 0)
        wrapper.pack_start(Box(v_expand=True), True, True, 0)
        
        self.files_container.pack_start(wrapper, True, True, 0)
        self.files_container.show_all()

    def _create_file_row(self, path):
        is_dir = path.is_dir()

        icon = Image(
            icon_name=self._get_icon_for_path(path, is_dir),
            icon_size=24, name="explorer-file-icon")
            
        name_label = Label(
            name="explorer-file-name", label=path.name,
            h_align="start", h_expand=True, ellipsize="end")

        size_label = Label(name="explorer-file-size", label="", h_align="end")

        try:
            st = path.stat()
            date_text = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            st_size = st.st_size
        except OSError:
            date_text = ""
            st_size = -1

        if is_dir:
            size_label.set_label(self._format_item_count(self._count_folder_items(path)))
        else:
            size_label.set_label(self._format_size(st_size) if st_size >= 0 else "—")

        date_label = Label(name="explorer-file-date", label=date_text, h_align="end")

        content = Box(
            orientation="h", spacing=8,
            children=[icon, name_label, size_label, date_label])

        btn = Button(name="explorer-file-row", child=content)
        btn._path = path
        btn._is_dir = is_dir

        def on_click(button):
            if button._is_dir:
                self._switch_to_files()
            self._on_file_clicked(button)

        btn.connect("clicked", on_click)
        btn.connect("button-press-event", self._clear_focus_on_click)
        btn.connect("button-press-event", self._on_file_button_press)
        
        self._setup_drag_source(btn, path)
        if is_dir:
            self._setup_drop_target(btn, target_path=path)
            btn.get_style_context().add_class("directory")
            
        return btn

    @staticmethod
    def _get_icon_for_path(path, is_dir=None):
        if is_dir is None:
            is_dir = path.is_dir()
        if is_dir:
            return _FOLDER_ICONS.get(path.name, "folder-symbolic")
            
        try:
            content_type, _ = Gio.content_type_guess(str(path), None)
            if content_type:
                gi_icon = Gio.content_type_get_symbolic_icon(content_type)
                if isinstance(gi_icon, Gio.ThemedIcon):
                    theme = Gtk.IconTheme.get_default()
                    for name in gi_icon.get_names():
                        if theme.has_icon(name):
                            return name
        except Exception:
            pass
            
        return "text-x-generic-symbolic"

    @staticmethod
    def _count_folder_items(path):
        try:
            count = 0
            with os.scandir(path) as it:
                for _ in it:
                    count += 1
            return count
        except (PermissionError, OSError):
            return -1

    @staticmethod
    def _format_item_count(count):
        if count < 0: return "—"
        if count == 0: return "empty"
        return "1 item" if count == 1 else f"{count} items"

    @staticmethod
    def _format_size(size):
        if not isinstance(size, (int, float)) or size < 0:
            return "—"
        if size == 0:
            return "0 B"
        s = float(size)
        idx = 0
        while s >= 1024.0 and idx < 4:
            s /= 1024.0
            idx += 1
        return f"{int(s)} B" if idx == 0 else f"{s:.1f} {_SIZE_UNITS[idx]}"

    def _get_unique_path(self, path):
        if not path.exists():
            return path
        stem, suffix, parent = path.stem, path.suffix, path.parent
        i = 1
        while True:
            candidate = parent / f"{stem} ({i}){suffix}"
            if not candidate.exists():
                return candidate
            i += 1

    def destroy(self):
        self._close_rename_widget()
        self._close_app_chooser()
        self._monitor_cleanup()
        self._cancel_pending_hide()
        self._hover_cancel()
        self._grace_cancel()
        self._pending_cleanup()
        self._cancel_activator_hover_timer()
        self._scroll_stop()
        
        if getattr(self, '_navigation_lock_timer', None):
            GLib.source_remove(self._navigation_lock_timer)
            
        self._volume_monitor = None
        super().destroy()