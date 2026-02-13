import gi
gi.require_version("Gtk", "3.0")
gi.require_version('GtkLayerShell', '0.1')  # ← ДОБАВИТЬ
from gi.repository import Gdk, GLib, Gtk, Gio, GtkLayerShell  # ← ДОБАВИТЬ GtkLayerShell

import os
import shutil
import subprocess
import threading
import urllib.parse
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
from fabric.utils import exec_shell_command_async

from services.wayland import WaylandWindow as Window


class Explorer(Window):
    __gtype_name__ = "Explorer"

    TARGET_URI_LIST = 0
    TARGET_TEXT = 1
    
    DRAG_HOVER_OPEN_DELAY = 800
    POST_DRAG_GRACE_PERIOD = 600
    ACTIVATOR_HOVER_DELAY = 1000
    
    DRAG_SCROLL_MARGIN = 250
    DRAG_SCROLL_SPEED_SLOW = 40
    DRAG_SCROLL_SPEED_FAST = 120
    DRAG_SCROLL_INTERVAL = 20

    def __init__(self, monitor_id: int = 0, **kwargs):
        self.monitor_id = monitor_id
        self._is_pinned = False
        self._is_hidden = True
        self._pending_hide: Optional[int] = None
        self._mon_w = 1920
        self._mon_h = 1080
        
        self._current_path = Path.home()
        self._history: List[Path] = [self._current_path]
        self._history_index = 0
        self._show_hidden = False
        
        self._navigation_lock = False
        self._navigation_lock_timer: Optional[int] = None
        
        self._file_monitor: Optional[Gio.FileMonitor] = None
        self._pending_refresh: Optional[int] = None
        self._refresh_debounce_ms = 250
        
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
        
        self._trash_path = Path.home() / ".local/share/Trash/files"
        self._trash_info_path = Path.home() / ".local/share/Trash/info"
        
        self._volume_monitor: Optional[Gio.VolumeMonitor] = None
        self._devices_container: Optional[Box] = None
        
        self._current_mount: Optional[Gio.Mount] = None
        self._current_mount_path: Optional[Path] = None
        self._current_mount_name: Optional[str] = None
        
        self._is_loading = False
        self._menu_open = False
        self._cursor_inside = False
        
        self._activator_hover_timer: Optional[int] = None
        self._cursor_over_activator = False
        
        self._drag_scroll_timer: Optional[int] = None
        self._drag_scroll_speed: int = 0
        
        # Inline rename state
        self._rename_widget: Optional[Gtk.Box] = None
        self._rename_path: Optional[Path] = None
        
        # Archive extensions
        self._archive_extensions_simple = {
            '.zip', '.tar', '.rar', '.7z', '.gz', '.bz2', '.xz', 
            '.zst', '.lz4', '.lzma', '.sz'
        }
        self._archive_extensions_compound = [
            '.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tbz',
            '.tar.xz', '.txz', '.tar.zst', '.tzst',
            '.tar.lz4', '.tlz4', '.tar.lzma', '.tlzma', '.tar.sz',
        ]
        
        self._bookmarks: List[Tuple[str, str, Path]] = [
            ("user-home-symbolic", "Home", Path.home()),
            ("user-desktop-symbolic", "Desktop", Path.home() / "Desktop"),
            ("folder-documents-symbolic", "Documents", Path.home() / "Documents"),
            ("folder-download-symbolic", "Downloads", Path.home() / "Downloads"),
            ("folder-pictures-symbolic", "Pictures", Path.home() / "Pictures"),
            ("folder-music-symbolic", "Music", Path.home() / "Music"),
            ("folder-videos-symbolic", "Videos", Path.home() / "Videos"),
            ("drive-harddisk-symbolic", "Root", Path("/")),
        ]

        super().__init__(
            name="explorer-window",
            layer="top",
            anchor="left top bottom",
            margin="0px 0px 0px 0px",
            exclusivity="none",
            monitor=monitor_id,
            visible=False,
            **kwargs,
        )

        self._icon_theme = Gtk.IconTheme.get_default()
        self._update_monitor()
        self._init_ui()
        
        GLib.timeout_add(100, self._delayed_show)

    # ──────────────────────────────────────────────
    #  Keyboard interactivity for layer-shell
    # ──────────────────────────────────────────────
    def _set_keyboard_interactive(self, enabled: bool):
        """Enable or disable keyboard input for this layer-shell surface."""
        try:
            if enabled:
                # Try EXCLUSIVE first for guaranteed keyboard grab
                GtkLayerShell.set_keyboard_mode(
                    self, GtkLayerShell.KeyboardMode.EXCLUSIVE
                )
            else:
                GtkLayerShell.set_keyboard_mode(
                    self, GtkLayerShell.KeyboardMode.NONE
                )
        except Exception as e:
            print(f"keyboard mode error: {e}")

    def _delayed_show(self) -> bool:
        self.show_all()
        self._navigate_to(self._current_path)
        self._setup_volume_monitor()
        return False

    def _update_monitor(self):
        display = Gdk.Display.get_default()
        if display:
            monitor = display.get_monitor(self.monitor_id)
            if monitor:
                geom = monitor.get_geometry()
                self._mon_w = geom.width
                self._mon_h = geom.height

    def _init_ui(self):
        self.activator = EventBox(name="explorer-activator")
        self.activator.set_size_request(8, -1)
        self.activator.connect("enter-notify-event", self._on_activator_enter)
        self.activator.connect("leave-notify-event", self._on_activator_leave)
        
        self._setup_activator_drop_target()
        self._build_explorer_content()
        
        self.explorer_eb = EventBox(name="explorer-eventbox")
        self.explorer_eb.add(self.explorer_box)
        
        self.explorer_eb.add_events(
            Gdk.EventMask.ENTER_NOTIFY_MASK |
            Gdk.EventMask.LEAVE_NOTIFY_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK |
            Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.BUTTON_RELEASE_MASK
        )
        
        self.explorer_eb.connect("enter-notify-event", self._on_explorer_enter)
        self.explorer_eb.connect("leave-notify-event", self._on_explorer_leave)
        self.explorer_eb.connect("motion-notify-event", self._on_explorer_motion)
        self.explorer_eb.connect("button-release-event", self._on_explorer_button_release)
        
        self._setup_explorer_drop_tracking()
        
        self.revealer = Revealer(
            name="explorer-revealer",
            transition_type="slide-right",
            transition_duration=350,
            child_revealed=False,
            child=self.explorer_eb,
        )
        
        main_box = Box(
            name="explorer-main",
            orientation="h",
            v_expand=True,
            children=[self.activator, self.revealer],
        )
        
        self.add(main_box)

    def _setup_activator_drop_target(self):
        self.activator.drag_dest_set(
            Gtk.DestDefaults.MOTION,
            self._dnd_targets,
            Gdk.DragAction.COPY | Gdk.DragAction.MOVE
        )
        self.activator.connect("drag-motion", self._on_activator_drag_motion)
        self.activator.connect("drag-leave", self._on_activator_drag_leave)

    def _setup_explorer_drop_tracking(self):
        self.explorer_eb.drag_dest_set(
            Gtk.DestDefaults.MOTION,
            self._dnd_targets,
            Gdk.DragAction.COPY | Gdk.DragAction.MOVE
        )
        self.explorer_eb.connect("drag-motion", self._on_explorer_drag_motion)
        self.explorer_eb.connect("drag-leave", self._on_explorer_drag_leave)

    def _on_activator_drag_motion(self, widget, context, x, y, time) -> bool:
        self._cancel_pending_hide()
        self._cancel_activator_hover_timer()
        self._drag_over_explorer = True
        if not self.revealer.get_child_revealed():
            self.revealer.set_reveal_child(True)
        Gdk.drag_status(context, Gdk.DragAction.COPY, time)
        return True

    def _on_activator_drag_leave(self, widget, context, time):
        GLib.timeout_add(50, self._check_drag_still_over)

    def _on_explorer_drag_motion(self, widget, context, x, y, time) -> bool:
        self._cancel_pending_hide()
        self._drag_over_explorer = True
        try:
            success, file_view_x, file_view_y = widget.translate_coordinates(self.file_view, x, y)
            if success:
                self._update_drag_scroll(file_view_y)
        except:
            pass
        return False

    def _on_explorer_drag_leave(self, widget, context, time):
        self._drag_over_explorer = False
        self._stop_drag_scroll()
        GLib.timeout_add(100, self._check_drag_hide)

    def _check_drag_still_over(self) -> bool:
        if not self._drag_over_explorer and not self._is_pinned:
            if not self._is_cursor_over_explorer() and not self._pending_drop_source:
                self._schedule_hide()
        return False

    def _check_drag_hide(self) -> bool:
        if self._is_pinned or self._pending_drop_source or self._post_drag_grace or self._navigation_lock:
            return False
        if not self._is_cursor_over_explorer():
            self._schedule_hide()
        return False

    def _cancel_activator_hover_timer(self):
        if self._activator_hover_timer:
            GLib.source_remove(self._activator_hover_timer)
            self._activator_hover_timer = None

    def _on_activator_enter(self, widget, event) -> bool:
        self._cancel_pending_hide()
        self._cursor_inside = True
        self._cursor_over_activator = True
        self._cancel_activator_hover_timer()
        self._activator_hover_timer = GLib.timeout_add(
            self.ACTIVATOR_HOVER_DELAY, 
            self._on_activator_hover_timeout
        )
        return True

    def _on_activator_leave(self, widget, event) -> bool:
        self._cursor_over_activator = False
        self._cancel_activator_hover_timer()
        if event.detail == Gdk.NotifyType.INFERIOR:
            return True
        if not self.revealer.get_child_revealed():
            self._cursor_inside = False
        return True

    def _on_activator_hover_timeout(self) -> bool:
        self._activator_hover_timer = None
        if self._cursor_over_activator:
            self.revealer.set_reveal_child(True)
        return False

    def _update_drag_scroll(self, y: int):
        try:
            alloc = self.file_view.get_allocation()
            height = alloc.height
            if y < 0 or y > height:
                self._stop_drag_scroll()
                return
            if y < self.DRAG_SCROLL_MARGIN:
                distance_from_edge = y
                speed = -self.DRAG_SCROLL_SPEED_FAST if distance_from_edge < self.DRAG_SCROLL_MARGIN // 2 else -self.DRAG_SCROLL_SPEED_SLOW
            elif y > height - self.DRAG_SCROLL_MARGIN:
                distance_from_edge = height - y
                speed = self.DRAG_SCROLL_SPEED_FAST if distance_from_edge < self.DRAG_SCROLL_MARGIN // 2 else self.DRAG_SCROLL_SPEED_SLOW
            else:
                speed = 0
            self._drag_scroll_speed = speed
            if speed != 0:
                if self._drag_scroll_timer is None:
                    self._do_drag_scroll()
                    self._drag_scroll_timer = GLib.timeout_add(self.DRAG_SCROLL_INTERVAL, self._do_drag_scroll)
            else:
                self._stop_drag_scroll()
        except:
            pass

    def _stop_drag_scroll(self):
        if self._drag_scroll_timer:
            GLib.source_remove(self._drag_scroll_timer)
            self._drag_scroll_timer = None
        self._drag_scroll_speed = 0

    def _do_drag_scroll(self) -> bool:
        if self._drag_scroll_speed == 0:
            self._drag_scroll_timer = None
            return False
        try:
            adj = self.file_view.get_vadjustment()
            if adj:
                current = adj.get_value()
                new_value = current + self._drag_scroll_speed
                lower = adj.get_lower()
                upper = adj.get_upper() - adj.get_page_size()
                new_value = max(lower, min(new_value, upper))
                adj.set_value(new_value)
        except:
            pass
        return True

    def _build_explorer_content(self):
        self._explorer_width = int(self._mon_w * 0.40)
        self.header = self._build_header()
        self.path_bar = self._build_path_bar()
        self.sidebar = self._build_sidebar()
        self.file_view = self._build_file_view()
        self.status_bar = self._build_status_bar()
        
        content_box = Box(
            name="explorer-content",
            orientation="h",
            h_expand=True,
            v_expand=True,
            children=[self.sidebar, self.file_view],
        )
        
        self.explorer_box = Box(
            name="explorer-container",
            orientation="v",
            v_expand=True,
            children=[self.header, self.path_bar, content_box, self.status_bar],
        )
        self.explorer_box.set_size_request(self._explorer_width, -1)

    def _build_header(self) -> Box:
        self.btn_back = Button(
            name="explorer-nav-btn",
            child=Image(icon_name="go-previous-symbolic", icon_size=16),
            tooltip_text="Back",
            on_clicked=self._on_back_clicked,
        )
        self.btn_forward = Button(
            name="explorer-nav-btn",
            child=Image(icon_name="go-next-symbolic", icon_size=16),
            tooltip_text="Forward",
            on_clicked=self._on_forward_clicked,
        )
        self.btn_up = Button(
            name="explorer-nav-btn",
            child=Image(icon_name="go-up-symbolic", icon_size=16),
            tooltip_text="Parent folder",
            on_clicked=self._on_up_clicked,
        )
        self.btn_home = Button(
            name="explorer-nav-btn",
            child=Image(icon_name="go-home-symbolic", icon_size=16),
            tooltip_text="Home",
            on_clicked=self._on_home_clicked,
        )
        
        nav_box = Box(name="explorer-nav-box", orientation="h", spacing=4,
                      children=[self.btn_back, self.btn_forward, self.btn_up, self.btn_home])
        
        self.title_label = Label(name="explorer-title", label="Files", h_expand=True, h_align="start")
        
        self.btn_eject = Button(
            name="explorer-eject-header-btn",
            child=Image(icon_name="media-eject-symbolic", icon_size=16),
            tooltip_text="Eject device",
            on_clicked=self._on_header_eject_clicked,
        )
        self.btn_eject.set_no_show_all(True)
        self.btn_eject.hide()
        
        self.btn_hidden = Button(
            name="explorer-nav-btn",
            child=Image(icon_name="view-more-symbolic", icon_size=16),
            tooltip_text="Show hidden files",
            on_clicked=self._on_toggle_hidden,
        )
        self.btn_pin = Button(
            name="explorer-nav-btn",
            child=Image(icon_name="view-pin-symbolic", icon_size=16),
            tooltip_text="Pin explorer",
            on_clicked=self._on_pin_clicked,
        )
        
        action_box = Box(name="explorer-nav-box", orientation="h", spacing=4,
                         children=[self.btn_eject, self.btn_hidden, self.btn_pin])
        
        return Box(name="explorer-header", orientation="h", spacing=8,
                   children=[nav_box, self.title_label, action_box])

    def _build_path_bar(self) -> Box:
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
        scroll_eb.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        scroll_eb.connect("scroll-event", self._on_path_scroll)
        
        path_wrapper = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        path_wrapper.set_hexpand(True)
        path_wrapper.pack_start(scroll_eb, True, True, 0)
        
        path_bar = Box(name="explorer-path-bar", orientation="h", spacing=4)
        path_bar.pack_start(path_icon, False, False, 0)
        path_bar.pack_start(path_wrapper, True, True, 0)
        
        return path_bar

    def _on_path_scroll(self, widget, event) -> bool:
        adj = self.path_scroll.get_hadjustment()
        if not adj:
            return False
        delta = 0
        if event.direction == Gdk.ScrollDirection.UP:
            delta = -30
        elif event.direction == Gdk.ScrollDirection.DOWN:
            delta = 30
        elif event.direction == Gdk.ScrollDirection.LEFT:
            delta = -30
        elif event.direction == Gdk.ScrollDirection.RIGHT:
            delta = 30
        elif event.direction == Gdk.ScrollDirection.SMOOTH:
            _, dx, dy = event.get_scroll_deltas()
            delta = (dx if dx != 0 else dy) * 30
        if delta != 0:
            new_value = adj.get_value() + delta
            new_value = max(adj.get_lower(), min(new_value, adj.get_upper() - adj.get_page_size()))
            adj.set_value(new_value)
            return True
        return False

    def _update_path_bar(self):
        for child in self.path_container.get_children():
            child.destroy()
        self.title_label.set_label("Trash" if self._is_in_trash() else (self._current_path.name or "Root"))
        path = self._current_path
        parts = []
        while path != path.parent:
            parts.append(path)
            path = path.parent
        parts.append(Path("/"))
        parts.reverse()
        for i, part in enumerate(parts):
            if i > 0:
                sep = Gtk.Label(label="/")
                sep.set_name("explorer-path-separator")
                self.path_container.pack_start(sep, False, False, 0)
            name = "/" if part == Path("/") else part.name
            label = Gtk.Label(label=name)
            label.set_name("explorer-path-part-label")
            btn = Gtk.Button()
            btn.set_name("explorer-path-part")
            btn.add(label)
            btn._path = part
            btn.connect("clicked", self._on_path_part_clicked)
            self._setup_path_part_as_drop_target(btn, part)
            self.path_container.pack_start(btn, False, False, 0)
        self.path_container.show_all()
        GLib.idle_add(self._scroll_path_to_end)

    def _scroll_path_to_end(self) -> bool:
        try:
            adj = self.path_scroll.get_hadjustment()
            if adj:
                adj.set_value(adj.get_upper() - adj.get_page_size())
        except:
            pass
        return False

    def _on_path_part_clicked(self, btn):
        self._set_navigation_lock()
        if self._pending_drop_source:
            return
        self._navigate_to(btn._path)

    def _setup_path_part_as_drop_target(self, widget: Gtk.Widget, path: Path):
        widget.drag_dest_set(
            Gtk.DestDefaults.MOTION | Gtk.DestDefaults.HIGHLIGHT | Gtk.DestDefaults.DROP,
            self._dnd_targets,
            Gdk.DragAction.COPY | Gdk.DragAction.MOVE
        )
        widget._drop_path = path
        widget.connect("drag-motion", self._on_path_part_drag_motion)
        widget.connect("drag-leave", self._on_path_part_drag_leave)
        widget.connect("drag-drop", self._on_path_part_drag_drop)
        widget.connect("drag-data-received", self._on_path_part_drag_data_received)

    def _on_path_part_drag_motion(self, widget, context, x, y, time) -> bool:
        target_path = widget._drop_path
        self._cancel_pending_hide()
        self._drag_over_explorer = True
        widget.get_style_context().add_class("drag-over")
        if target_path and target_path.is_dir() and self._drag_hover_path != target_path:
            self._start_drag_hover_timer(target_path, widget)
        state = Gdk.Keymap.get_default().get_modifier_state()
        action = Gdk.DragAction.COPY if state & Gdk.ModifierType.CONTROL_MASK else Gdk.DragAction.MOVE
        Gdk.drag_status(context, action, time)
        self._update_dnd_indicator(action, target_path.name or "Root")
        return True

    def _on_path_part_drag_leave(self, widget, context, time):
        widget.get_style_context().remove_class("drag-over")
        widget.get_style_context().remove_class("drag-hover-pending")
        if hasattr(widget, '_drop_path') and self._drag_hover_path == widget._drop_path:
            self._cancel_drag_hover_timer()

    def _on_path_part_drag_drop(self, widget, context, x, y, time) -> bool:
        self._cancel_drag_hover_timer()
        target = widget.drag_dest_find_target(context, None)
        if target:
            widget.drag_get_data(context, target, time)
            return True
        return False

    def _on_path_part_drag_data_received(self, widget, context, x, y, selection, info, time):
        target_path = getattr(widget, '_drop_path', None)
        widget.get_style_context().remove_class("drag-over")
        self._cancel_drag_hover_timer()
        if target_path:
            self._handle_drop_data(context, selection, info, time, target_path)
        else:
            Gtk.drag_finish(context, False, False, time)

    def _update_dnd_indicator(self, action, dest_name: str):
        action_text = "Copy" if action == Gdk.DragAction.COPY else "Move"
        self.dnd_indicator.set_label(f"{action_text} to {dest_name}")
        self.dnd_indicator.get_style_context().add_class("active")
        if action == Gdk.DragAction.COPY:
            self.dnd_indicator.get_style_context().remove_class("move")
            self.dnd_indicator.get_style_context().add_class("copy")
        else:
            self.dnd_indicator.get_style_context().remove_class("copy")
            self.dnd_indicator.get_style_context().add_class("move")

    def _handle_drop_data(self, context, selection, info, time, dest_folder):
        if not dest_folder or not dest_folder.is_dir():
            Gtk.drag_finish(context, False, False, time)
            return
        uris = selection.get_uris()
        if not uris:
            text = selection.get_text()
            if text:
                uris = [text.strip() if text.startswith("file://") else Path(text.strip()).as_uri()]
        if not uris:
            Gtk.drag_finish(context, False, False, time)
            return
        action = context.get_selected_action()
        is_move = action == Gdk.DragAction.MOVE
        processed = 0
        for uri in uris:
            try:
                src_path = self._uri_to_path(uri)
                if not src_path or not src_path.exists():
                    continue
                dest_path = dest_folder / src_path.name
                if src_path == dest_path:
                    continue
                dest_path = self._get_unique_path(dest_path)
                if is_move:
                    shutil.move(str(src_path), str(dest_path))
                else:
                    if src_path.is_dir():
                        shutil.copytree(str(src_path), str(dest_path))
                    else:
                        shutil.copy2(str(src_path), str(dest_path))
                processed += 1
            except Exception as e:
                print(f"DnD error: {e}")
        Gtk.drag_finish(context, processed > 0, is_move and processed > 0, time)
        if processed:
            self.status_label.set_label(f"{'Moved' if is_move else 'Copied'} {processed} item(s)")

    def _build_sidebar(self) -> ScrolledWindow:
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
        return ScrolledWindow(name="explorer-sidebar", h_scrollbar_policy="never",
                              v_scrollbar_policy="automatic", min_content_width=160, child=self.sidebar_content)

    def _create_bookmark_button(self, icon_name: str, label_text: str, path: Path) -> Gtk.Button:
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
        btn.connect("clicked", self._on_bookmark_clicked)
        btn.get_style_context().add_class("directory")
        btn.show_all()
        self._setup_drop_target(btn, target_path=path)
        return btn

    def _on_bookmark_clicked(self, btn):
        self._set_navigation_lock()
        if self._pending_drop_source:
            return
        if hasattr(btn, '_path') and btn._path:
            self._navigate_to(btn._path)

    def _create_trash_button(self) -> Gtk.Button:
        return self._create_bookmark_button("user-trash-symbolic", "Trash", self._trash_path)

    def _create_clear_trash_button(self) -> Gtk.Button:
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
        btn.show_all()
        return btn

    def _update_trash_button(self):
        for child in self.trash_button_container.get_children():
            child.destroy()
        btn = self._create_clear_trash_button() if self._is_in_trash() else self._create_trash_button()
        self.trash_button_container.add(btn)
        self.trash_button_container.show_all()

    def _setup_volume_monitor(self):
        try:
            self._volume_monitor = Gio.VolumeMonitor.get()
            self._volume_monitor.connect("mount-added", self._on_mount_changed)
            self._volume_monitor.connect("mount-removed", self._on_mount_changed)
            self._volume_monitor.connect("volume-added", self._on_volume_changed)
            self._volume_monitor.connect("volume-removed", self._on_volume_changed)
            self._volume_monitor.connect("drive-connected", self._on_drive_changed)
            self._volume_monitor.connect("drive-disconnected", self._on_drive_changed)
            self._refresh_devices()
        except Exception as e:
            print(f"Error setting up volume monitor: {e}")

    def _on_mount_changed(self, monitor, mount):
        GLib.idle_add(self._refresh_devices)
        GLib.idle_add(self._update_eject_button)

    def _on_volume_changed(self, monitor, volume):
        GLib.idle_add(self._refresh_devices)

    def _on_drive_changed(self, monitor, drive):
        GLib.idle_add(self._refresh_devices)

    def _refresh_devices(self) -> bool:
        if not self._devices_container:
            return False
        for child in self._devices_container.get_children():
            child.destroy()
        self._populate_devices(self._devices_container)
        self._devices_container.show_all()
        return False

    def _populate_devices(self, container):
        if not self._volume_monitor:
            return
        added_identifiers = set()
        try:
            mounts = self._volume_monitor.get_mounts()
            for mount in mounts:
                try:
                    root = mount.get_root()
                    if not root:
                        continue
                    path_str = root.get_path()
                    if not path_str:
                        continue
                    path = Path(path_str)
                    if path == Path("/"):
                        continue
                    if any(str(path).startswith(p) for p in ["/boot", "/snap", "/var/snap"]):
                        continue
                    identifier = f"mount:{path_str}"
                    if identifier in added_identifiers:
                        continue
                    added_identifiers.add(identifier)
                    volume = mount.get_volume()
                    if volume:
                        vol_id = volume.get_identifier("uuid") or volume.get_name()
                        if vol_id:
                            added_identifiers.add(f"volume:{vol_id}")
                    name = mount.get_name() or path.name or "Unknown"
                    icon_name = self._get_mount_icon(mount)
                    row = self._create_device_row(icon_name, name, path, mount)
                    container.add(row)
                except:
                    continue
            volumes = self._volume_monitor.get_volumes()
            for volume in volumes:
                try:
                    mount = volume.get_mount()
                    if mount:
                        continue
                    vol_id = volume.get_identifier("uuid") or volume.get_name() or str(id(volume))
                    identifier = f"volume:{vol_id}"
                    if identifier in added_identifiers:
                        continue
                    added_identifiers.add(identifier)
                    if not volume.can_mount():
                        continue
                    name = volume.get_name() or "Unknown Volume"
                    icon_name = self._get_volume_icon(volume)
                    row = self._create_unmounted_volume_row(icon_name, name, volume)
                    container.add(row)
                except:
                    continue
        except:
            pass

    def _get_mount_icon(self, mount) -> str:
        try:
            icon = mount.get_icon()
            if icon and isinstance(icon, Gio.ThemedIcon):
                names = icon.get_names()
                if names:
                    for name in names:
                        if "symbolic" in name:
                            return name
                    return names[0]
        except:
            pass
        return "drive-removable-media-symbolic"

    def _get_volume_icon(self, volume) -> str:
        try:
            icon = volume.get_icon()
            if icon and isinstance(icon, Gio.ThemedIcon):
                names = icon.get_names()
                if names:
                    for name in names:
                        if "symbolic" in name:
                            return name
                    return names[0]
        except:
            pass
        return "drive-removable-media-symbolic"

    def _create_device_row(self, icon_name: str, label_text: str, path: Path, mount) -> Gtk.Button:
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
        btn._mount = mount
        btn._device_name = label_text
        btn.connect("clicked", self._on_device_nav_clicked)
        btn.get_style_context().add_class("directory")
        btn.get_style_context().add_class("device")
        btn.show_all()
        self._setup_drop_target(btn, target_path=path)
        return btn

    def _create_unmounted_volume_row(self, icon_name: str, label_text: str, volume) -> Gtk.Button:
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
        btn._volume = volume
        btn._device_name = label_text
        btn.set_tooltip_text(f"Click to mount {label_text}")
        btn.connect("clicked", self._on_mount_volume_clicked)
        btn.get_style_context().add_class("directory")
        btn.get_style_context().add_class("unmounted")
        btn.show_all()
        return btn

    def _on_device_nav_clicked(self, btn):
        self._set_navigation_lock()
        if self._pending_drop_source:
            return
        if hasattr(btn, '_path') and btn._path:
            self._navigate_to(btn._path)

    def _on_mount_volume_clicked(self, button):
        self._set_navigation_lock()
        volume = getattr(button, '_volume', None)
        name = getattr(button, '_device_name', 'volume')
        if not volume:
            self.status_label.set_label("No volume to mount")
            return
        button.set_sensitive(False)
        self.status_label.set_label(f"Mounting {name}...")
        try:
            volume.mount(Gio.MountMountFlags.NONE, None, None, self._on_mount_finished, (name, button, volume))
        except Exception as e:
            self.status_label.set_label(f"Mount error: {e}")
            button.set_sensitive(True)

    def _on_mount_finished(self, volume, result, user_data):
        name, button, vol = user_data
        try:
            vol.mount_finish(result)
            GLib.idle_add(lambda: self.status_label.set_label(f"Mounted: {name}"))
            def navigate_after_mount():
                mount = vol.get_mount()
                if mount:
                    root = mount.get_root()
                    if root:
                        path_str = root.get_path()
                        if path_str:
                            self._navigate_to(Path(path_str))
                return False
            GLib.timeout_add(100, navigate_after_mount)
        except Exception as e:
            GLib.idle_add(lambda: self.status_label.set_label(f"Mount failed: {str(e)}"))
            GLib.idle_add(lambda: button.set_sensitive(True) if button else None)

    def _find_mount_for_path(self, path: Path):
        if not self._volume_monitor:
            return None, None, None
        try:
            path_resolved = path.resolve()
            mounts = self._volume_monitor.get_mounts()
            best_match, best_path, best_name, best_len = None, None, None, 0
            for mount in mounts:
                try:
                    root = mount.get_root()
                    if not root:
                        continue
                    mount_path_str = root.get_path()
                    if not mount_path_str:
                        continue
                    mount_path = Path(mount_path_str).resolve()
                    if mount_path == Path("/"):
                        continue
                    mount_str = str(mount_path)
                    path_str = str(path_resolved)
                    if path_str == mount_str or path_str.startswith(mount_str + "/"):
                        if len(mount_str) > best_len:
                            best_match = mount
                            best_path = mount_path
                            best_name = mount.get_name() or mount_path.name
                            best_len = len(mount_str)
                except:
                    continue
            return best_match, best_path, best_name
        except:
            return None, None, None

    def _update_eject_button(self):
        mount, mount_path, mount_name = self._find_mount_for_path(self._current_path)
        if mount and (mount.can_eject() or mount.can_unmount()):
            self._current_mount = mount
            self._current_mount_path = mount_path
            self._current_mount_name = mount_name
            self.btn_eject.set_tooltip_text(f"Eject {mount_name}")
            self.btn_eject.show()
        else:
            self._current_mount = None
            self._current_mount_path = None
            self._current_mount_name = None
            self.btn_eject.hide()

    def _on_header_eject_clicked(self, btn):
        self._set_navigation_lock()
        if not self._current_mount:
            self.status_label.set_label("No device to eject")
            return
        mount = self._current_mount
        name = self._current_mount_name or "device"
        self._navigate_to(Path.home())
        btn.set_sensitive(False)
        self.status_label.set_label(f"Ejecting {name}...")
        try:
            if mount.can_eject():
                mount.eject_with_operation(Gio.MountUnmountFlags.NONE, None, None, self._on_header_eject_finished, (name, btn))
            elif mount.can_unmount():
                mount.unmount_with_operation(Gio.MountUnmountFlags.NONE, None, None, self._on_header_unmount_finished, (name, btn))
            else:
                self.status_label.set_label(f"Cannot eject {name}")
                btn.set_sensitive(True)
        except Exception as e:
            self.status_label.set_label(f"Error: {e}")
            btn.set_sensitive(True)

    def _on_header_eject_finished(self, mount, result, user_data):
        name, button = user_data
        try:
            mount.eject_with_operation_finish(result)
            GLib.idle_add(lambda: self.status_label.set_label(f"Ejected: {name}"))
        except Exception as e:
            GLib.idle_add(lambda: self.status_label.set_label(f"Eject failed: {str(e)}"))
        GLib.idle_add(lambda: button.set_sensitive(True) if button else None)

    def _on_header_unmount_finished(self, mount, result, user_data):
        name, button = user_data
        try:
            mount.unmount_with_operation_finish(result)
            GLib.idle_add(lambda: self.status_label.set_label(f"Unmounted: {name}"))
        except Exception as e:
            GLib.idle_add(lambda: self.status_label.set_label(f"Unmount failed: {str(e)}"))
        GLib.idle_add(lambda: button.set_sensitive(True) if button else None)

    def _build_file_view(self) -> ScrolledWindow:
        self.files_container = Box(name="explorer-files-container", orientation="v", spacing=2)
        scrolled = ScrolledWindow(
            name="explorer-file-view",
            h_scrollbar_policy="never",
            v_scrollbar_policy="always",
            min_content_size=(-1, -1),
            child=self.files_container,
            v_expand=True,
            h_expand=True,
            propagate_width=False,
            propagate_height=False
        )
        scrolled.drag_dest_set(
            Gtk.DestDefaults.MOTION | Gtk.DestDefaults.HIGHLIGHT | Gtk.DestDefaults.DROP,
            self._dnd_targets,
            Gdk.DragAction.COPY | Gdk.DragAction.MOVE
        )
        scrolled.connect("drag-motion", self._on_file_view_drag_motion)
        scrolled.connect("drag-leave", self._on_file_view_drag_leave)
        scrolled.connect("drag-drop", self._on_drag_drop, None)
        scrolled.connect("drag-data-received", self._on_drag_data_received, None)
        return scrolled

    def _on_file_view_drag_motion(self, widget, context, x, y, time) -> bool:
        self._cancel_pending_hide()
        self._drag_over_explorer = True
        self._update_drag_scroll(y)
        state = Gdk.Keymap.get_default().get_modifier_state()
        action = Gdk.DragAction.COPY if state & Gdk.ModifierType.CONTROL_MASK else Gdk.DragAction.MOVE
        Gdk.drag_status(context, action, time)
        dest_name = self._current_path.name or "here"
        self._update_dnd_indicator(action, dest_name)
        return True

    def _on_file_view_drag_leave(self, widget, context, time):
        self._stop_drag_scroll()

    def _build_status_bar(self) -> Box:
        self.status_label = Label(name="explorer-status-label", label="", h_align="start", h_expand=True)
        self.dnd_indicator = Label(name="explorer-dnd-indicator", label="", h_align="center")
        self.size_label = Label(name="explorer-size-label", label="", h_align="end")
        return Box(name="explorer-status-bar", orientation="h",
                   children=[self.status_label, self.dnd_indicator, self.size_label])

    def _get_cursor_position(self):
        try:
            display = Gdk.Display.get_default()
            if display:
                seat = display.get_default_seat()
                if seat:
                    pointer = seat.get_pointer()
                    if pointer:
                        _, x, y = pointer.get_position()
                        return (x, y)
        except:
            pass
        return None

    def _is_cursor_over_explorer(self) -> bool:
        cursor_pos = self._get_cursor_position()
        if not cursor_pos:
            return self._cursor_inside
        x, y = cursor_pos
        try:
            window = self.get_window()
            if not window:
                return self._cursor_inside
            origin = window.get_origin()
            if isinstance(origin, tuple) and len(origin) == 3:
                win_x, win_y = origin[1], origin[2]
            elif isinstance(origin, tuple) and len(origin) == 2:
                win_x, win_y = origin
            else:
                return self._cursor_inside
            if self.revealer.get_child_revealed():
                try:
                    alloc = self.explorer_box.get_allocation()
                    success, ex, ey = self.explorer_box.translate_coordinates(self, 0, 0)
                    if success:
                        abs_x = win_x + ex
                        abs_y = win_y + ey
                        if abs_x <= x <= abs_x + alloc.width and abs_y <= y <= abs_y + alloc.height:
                            return True
                except:
                    pass
            try:
                act_alloc = self.activator.get_allocation()
                success, ax, ay = self.activator.translate_coordinates(self, 0, 0)
                if success:
                    abs_ax = win_x + ax
                    abs_ay = win_y + ay
                    if abs_ax <= x <= abs_ax + act_alloc.width and abs_ay <= y <= abs_ay + act_alloc.height:
                        return True
            except:
                pass
        except:
            pass
        return False

    def _find_folder_at_position(self, root_x: float, root_y: float):
        try:
            window = self.get_window()
            if not window:
                return None
            origin = window.get_origin()
            win_x, win_y = (origin[1], origin[2]) if len(origin) == 3 else origin
        except:
            return None
        for widget, path in self._folder_widgets:
            try:
                if not widget.get_mapped():
                    continue
                alloc = widget.get_allocation()
                success, wx, wy = widget.translate_coordinates(self, 0, 0)
                if not success:
                    continue
                abs_x = win_x + wx
                abs_y = win_y + wy
                if abs_x <= root_x <= abs_x + alloc.width and abs_y <= root_y <= abs_y + alloc.height:
                    return path
            except:
                continue
        return None

    def _set_navigation_lock(self):
        self._navigation_lock = True
        self._cancel_pending_hide()
        if self._navigation_lock_timer:
            GLib.source_remove(self._navigation_lock_timer)
        self._navigation_lock_timer = GLib.timeout_add(500, self._clear_navigation_lock)

    def _clear_navigation_lock(self) -> bool:
        self._navigation_lock = False
        self._navigation_lock_timer = None
        return False

    def _start_post_drag_grace(self):
        self._cancel_post_drag_grace()
        self._post_drag_grace = True
        self._post_drag_timer = GLib.timeout_add(self.POST_DRAG_GRACE_PERIOD, self._end_post_drag_grace)

    def _cancel_post_drag_grace(self):
        if self._post_drag_timer:
            GLib.source_remove(self._post_drag_timer)
            self._post_drag_timer = None

    def _end_post_drag_grace(self) -> bool:
        self._post_drag_timer = None
        self._post_drag_grace = False
        if self._is_pinned or self._menu_open or self._drag_in_progress or self._pending_drop_source or self._navigation_lock:
            return False
        if self._is_cursor_over_explorer():
            self._cursor_inside = True
        else:
            self._cursor_inside = False
            self._schedule_hide()
        return False

    def _start_drag_hover_timer(self, path: Path, widget: Gtk.Widget = None):
        self._cancel_drag_hover_timer()
        
        if path == self._current_path:
            return
            
        if self._drag_source_path and path == self._drag_source_path:
            return

        self._drag_hover_path = path
        self._drag_hover_widget = widget
        if widget:
            widget.get_style_context().add_class("drag-hover-pending")
        self._drag_hover_timer = GLib.timeout_add(self.DRAG_HOVER_OPEN_DELAY, self._on_drag_hover_timeout)

    def _cancel_drag_hover_timer(self):
        if self._drag_hover_timer:
            GLib.source_remove(self._drag_hover_timer)
            self._drag_hover_timer = None
        if self._drag_hover_widget:
            try:
                self._drag_hover_widget.get_style_context().remove_class("drag-hover-pending")
            except:
                pass
            self._drag_hover_widget = None
        self._drag_hover_path = None

    def _on_drag_hover_timeout(self) -> bool:
        self._drag_hover_timer = None
        path = self._drag_hover_path
        widget = self._drag_hover_widget
        if widget:
            try:
                widget.get_style_context().remove_class("drag-hover-pending")
            except:
                pass
        self._drag_hover_widget = None
        self._drag_hover_path = None
        if not path or not path.is_dir() or path == self._current_path:
            return False
        source_path = self._drag_source_path
        was_dragging = self._drag_in_progress
        if was_dragging and source_path and source_path.exists():
            self._pending_drop_source = source_path
            self._pending_drop_target = None
            self._start_pending_drop_mode()
        self._navigate_to(path)
        return False

    def _start_pending_drop_mode(self):
        self.file_view.get_style_context().add_class("drag-over")
        dest_name = self._current_path.name or "here"
        self.dnd_indicator.set_label(f"Release to drop in {dest_name}")
        self.dnd_indicator.get_style_context().add_class("active")
        self.dnd_indicator.get_style_context().add_class("move")

    def _update_pending_drop_from_motion(self, root_x: float, root_y: float, state):
        if not self._pending_drop_source:
            return
        folder = self._find_folder_at_position(root_x, root_y)
        is_copy = bool(state & Gdk.ModifierType.CONTROL_MASK)
        action_word = "copy" if is_copy else "move"
        self.dnd_indicator.get_style_context().remove_class("copy" if not is_copy else "move")
        self.dnd_indicator.get_style_context().add_class("copy" if is_copy else "move")
        if folder and folder != self._current_path:
            dest_name = folder.name
            self.dnd_indicator.set_label(f"Release to {action_word} to {dest_name}")
            self._pending_drop_target = folder
            if self._pending_hover_path != folder:
                self._start_pending_hover_timer(folder)
            self._highlight_folder(folder)
        else:
            dest_name = self._current_path.name or "here"
            self.dnd_indicator.set_label(f"Release to {action_word} to {dest_name}")
            self._pending_drop_target = None
            self._cancel_pending_hover_timer()
            self._clear_folder_highlights()

    def _start_pending_hover_timer(self, path: Path):
        self._cancel_pending_hover_timer()
        self._pending_hover_path = path
        self._pending_hover_timer = GLib.timeout_add(self.DRAG_HOVER_OPEN_DELAY, self._on_pending_hover_timeout)

    def _cancel_pending_hover_timer(self):
        if self._pending_hover_timer:
            GLib.source_remove(self._pending_hover_timer)
            self._pending_hover_timer = None
        self._pending_hover_path = None

    def _on_pending_hover_timeout(self) -> bool:
        self._pending_hover_timer = None
        path = self._pending_hover_path
        self._pending_hover_path = None
        if not path or not path.is_dir() or path == self._current_path:
            return False
        if not self._pending_drop_source:
            return False
        self._clear_folder_highlights()
        self._navigate_to(path)
        dest_name = self._current_path.name or "here"
        self.dnd_indicator.set_label(f"Release to drop in {dest_name}")
        self._pending_drop_target = None
        return False

    def _execute_pending_drop(self, is_copy: bool):
        if not self._pending_drop_source:
            return
        source = self._pending_drop_source
        target = self._pending_drop_target or self._current_path
        self._cleanup_pending_drop()
        try:
            if source.parent.resolve() == target.resolve():
                self.status_label.set_label("Already in this folder")
                return
            if source.resolve() == target.resolve():
                self.status_label.set_label("Cannot move into itself")
                return
            try:
                target.resolve().relative_to(source.resolve())
                self.status_label.set_label("Cannot move into subfolder")
                return
            except ValueError:
                pass
            dest_path = self._get_unique_path(target / source.name)
            if is_copy:
                if source.is_dir():
                    shutil.copytree(str(source), str(dest_path))
                else:
                    shutil.copy2(str(source), str(dest_path))
                self.status_label.set_label(f"Copied: {source.name}")
            else:
                shutil.move(str(source), str(dest_path))
                self.status_label.set_label(f"Moved: {source.name}")
        except Exception as e:
            self.status_label.set_label(f"Error: {e}")

    def _highlight_folder(self, path: Path):
        for widget, widget_path in self._folder_widgets:
            try:
                if widget_path == path:
                    widget.get_style_context().add_class("drag-over")
                else:
                    widget.get_style_context().remove_class("drag-over")
            except:
                pass

    def _clear_folder_highlights(self):
        for widget, _ in self._folder_widgets:
            try:
                widget.get_style_context().remove_class("drag-over")
            except:
                pass

    def _cleanup_pending_drop(self):
        self._pending_drop_source = None
        self._pending_drop_target = None
        self._cancel_pending_hover_timer()
        self._clear_folder_highlights()
        self.file_view.get_style_context().remove_class("drag-over")
        self.dnd_indicator.set_label("")
        self.dnd_indicator.get_style_context().remove_class("active")
        self.dnd_indicator.get_style_context().remove_class("copy")
        self.dnd_indicator.get_style_context().remove_class("move")
        self._drag_in_progress = False
        self._drag_over_explorer = False
        self._start_post_drag_grace()

    def _on_explorer_motion(self, widget, event) -> bool:
        self._cursor_inside = True
        self._cancel_pending_hide()
        if self._pending_drop_source:
            state = event.state
            if isinstance(state, tuple):
                state = state[1]
            if not (state & Gdk.ModifierType.BUTTON1_MASK):
                is_copy = bool(state & Gdk.ModifierType.CONTROL_MASK)
                self._execute_pending_drop(is_copy)
                return True
            self._update_pending_drop_from_motion(event.x_root, event.y_root, state)
            try:
                success, file_view_x, file_view_y = widget.translate_coordinates(self.file_view, int(event.x), int(event.y))
                if success:
                    self._update_drag_scroll(file_view_y)
            except:
                pass
            return True
        return False

    def _on_explorer_button_release(self, widget, event) -> bool:
        if event.button == 1 and self._pending_drop_source:
            is_copy = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
            folder = self._find_folder_at_position(event.x_root, event.y_root)
            if folder and folder != self._current_path:
                self._pending_drop_target = folder
            self._execute_pending_drop(is_copy)
            return True
        return False

    def _setup_drag_source(self, widget: Gtk.Widget, path: Path):
        widget.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, self._dnd_targets,
                               Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        widget.connect("drag-begin", self._on_drag_begin, path)
        widget.connect("drag-data-get", self._on_drag_data_get, path)
        widget.connect("drag-end", self._on_drag_end)
        widget.connect("drag-failed", self._on_drag_failed)

    def _setup_drop_target(self, widget: Gtk.Widget, target_path: Path = None):
        widget.drag_dest_set(Gtk.DestDefaults.MOTION | Gtk.DestDefaults.HIGHLIGHT | Gtk.DestDefaults.DROP,
                             self._dnd_targets, Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        widget.connect("drag-motion", self._on_drag_motion, target_path)
        widget.connect("drag-leave", self._on_drag_leave, target_path)
        widget.connect("drag-drop", self._on_drag_drop, target_path)
        widget.connect("drag-data-received", self._on_drag_data_received, target_path)

    def _on_drag_begin(self, widget, context, path):
        self._drag_source_path = path
        self._drag_in_progress = True
        self._drag_over_explorer = True
        self._cancel_pending_hide()
        self._cancel_post_drag_grace()
        
        try:
            icon_size = 48
            icon_name = self._get_icon_for_path(path)
            full_color_icon_name = icon_name.replace("-symbolic", "")
            
            pixbuf = None
            try:
                pixbuf = self._icon_theme.load_icon(full_color_icon_name, icon_size, 0)
            except:
                pass
                
            if not pixbuf:
                fallback_name = "folder" if path.is_dir() else "text-x-generic"
                try:
                    pixbuf = self._icon_theme.load_icon(fallback_name, icon_size, 0)
                except:
                    pass

            if pixbuf:
                hot_x = icon_size // 2
                hot_y = icon_size // 2
                Gtk.drag_set_icon_pixbuf(context, pixbuf, hot_x, hot_y)
            else:
                Gtk.drag_set_icon_default(context)

        except Exception:
            Gtk.drag_set_icon_default(context)

        widget.get_style_context().add_class("dragging")
        self.dnd_indicator.set_label(f"Dragging: {path.name}")
        self.dnd_indicator.get_style_context().add_class("active")

    def _on_drag_data_get(self, widget, context, selection, info, time, path):
        uri = path.as_uri()
        selection.set_uris([uri]) if info == self.TARGET_URI_LIST else selection.set_text(str(path), -1)

    def _on_drag_end(self, widget, context):
        widget.get_style_context().remove_class("dragging")
        self._stop_drag_scroll()
        if self._pending_drop_source:
            self._drag_in_progress = False
            return
        self._drag_source_path = None
        self._drag_in_progress = False
        self.dnd_indicator.set_label("")
        self.dnd_indicator.get_style_context().remove_class("active")
        self.dnd_indicator.get_style_context().remove_class("copy")
        self.dnd_indicator.get_style_context().remove_class("move")
        self._cancel_drag_hover_timer()
        self._start_post_drag_grace()

    def _on_drag_failed(self, widget, context, result) -> bool:
        widget.get_style_context().remove_class("dragging")
        self._stop_drag_scroll()
        if self._pending_drop_source:
            self._drag_in_progress = False
            return False
        self._drag_source_path = None
        self._drag_in_progress = False
        self.dnd_indicator.set_label("")
        self.dnd_indicator.get_style_context().remove_class("active")
        self._cancel_drag_hover_timer()
        self._start_post_drag_grace()
        return False

    def _on_drag_motion(self, widget, context, x, y, time, target_path=None) -> bool:
        self._cancel_pending_hide()
        self._drag_over_explorer = True
        widget.get_style_context().add_class("drag-over")
        try:
            success, file_view_x, file_view_y = widget.translate_coordinates(self.file_view, x, y)
            if success:
                self._update_drag_scroll(file_view_y)
        except:
            pass
        if target_path and target_path.is_dir() and self._drag_hover_path != target_path:
            self._start_drag_hover_timer(target_path, widget)
        elif not target_path or not target_path.is_dir():
            self._cancel_drag_hover_timer()
        state = Gdk.Keymap.get_default().get_modifier_state()
        action = Gdk.DragAction.COPY if state & Gdk.ModifierType.CONTROL_MASK else Gdk.DragAction.MOVE
        Gdk.drag_status(context, action, time)
        dest_name = (target_path.name if target_path else self._current_path.name) or "Root"
        self._update_dnd_indicator(action, dest_name)
        return True

    def _on_drag_leave(self, widget, context, time, target_path=None):
        widget.get_style_context().remove_class("drag-over")
        widget.get_style_context().remove_class("drag-hover-pending")
        if target_path and self._drag_hover_path == target_path:
            self._cancel_drag_hover_timer()

    def _on_drag_drop(self, widget, context, x, y, time, target_path=None) -> bool:
        self._cancel_drag_hover_timer()
        self._stop_drag_scroll()
        target = widget.drag_dest_find_target(context, None)
        if target:
            widget.drag_get_data(context, target, time)
            return True
        return False

    def _on_drag_data_received(self, widget, context, x, y, selection, info, time, target_path=None):
        widget.get_style_context().remove_class("drag-over")
        self._cancel_drag_hover_timer()
        self._handle_drop_data(context, selection, info, time, target_path or self._current_path)

    def _uri_to_path(self, uri: str):
        try:
            return Path(urllib.parse.unquote(uri[7:])) if uri.startswith("file://") else Path(uri)
        except:
            return None

    def _get_unique_path(self, path: Path) -> Path:
        if not path.exists():
            return path
        base, ext, parent = path.stem, path.suffix, path.parent
        for i in range(1, 100):
            new_path = parent / f"{base} ({i}){ext}"
            if not new_path.exists():
                return new_path
        return path

    def _setup_file_monitor(self):
        self._cleanup_file_monitor()
        try:
            gfile = Gio.File.new_for_path(str(self._current_path))
            self._file_monitor = gfile.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
            self._file_monitor.connect("changed", self._on_directory_changed)
        except:
            pass

    def _cleanup_file_monitor(self):
        if self._file_monitor:
            try:
                self._file_monitor.cancel()
            except:
                pass
            self._file_monitor = None
        if self._pending_refresh:
            GLib.source_remove(self._pending_refresh)
            self._pending_refresh = None

    def _on_directory_changed(self, monitor, file, other_file, event_type):
        if event_type != Gio.FileMonitorEvent.ATTRIBUTE_CHANGED:
            if self._pending_refresh:
                GLib.source_remove(self._pending_refresh)
            self._pending_refresh = GLib.timeout_add(self._refresh_debounce_ms, self._do_refresh)

    def _do_refresh(self) -> bool:
        self._pending_refresh = None
        if not self._is_loading:
            self._load_directory()
        return False

    def _navigate_to(self, path: Path):
        if self._is_loading:
            return
        self._close_rename_widget()
        self._set_navigation_lock()
        path = Path(path)
        if not path.exists() or not path.is_dir():
            return
        try:
            list(path.iterdir())
        except:
            return
        self._current_path = path
        if self._history_index < len(self._history) - 1:
            self._history = self._history[:self._history_index + 1]
        if not self._history or self._history[-1] != path:
            self._history.append(path)
            self._history_index = len(self._history) - 1
        self._update_navigation_buttons()
        self._update_path_bar()
        self._update_trash_button()
        self._update_eject_button()
        self._load_directory()
        self._setup_file_monitor()

    def _update_navigation_buttons(self):
        self.btn_back.set_sensitive(self._history_index > 0)
        self.btn_forward.set_sensitive(self._history_index < len(self._history) - 1)
        self.btn_up.set_sensitive(self._current_path != Path("/"))

    def _is_in_trash(self) -> bool:
        try:
            return self._current_path.resolve() == self._trash_path.resolve()
        except:
            return False

    def _load_directory(self):
        if self._is_loading:
            return
        self._is_loading = True
        self._folder_widgets.clear()
        for child in self.files_container.get_children():
            child.destroy()
        try:
            items = list(self._current_path.iterdir())
        except:
            self._is_loading = False
            return
        if not self._show_hidden:
            items = [i for i in items if not i.name.startswith('.')]
        items.sort(key=lambda x: (x.name.startswith('.'), x.is_file(), x.name.lower()))
        if not items:
            self._show_empty_state()
        else:
            for item in items:
                row = self._create_file_row(item)
                self.files_container.add(row)
                if item.is_dir():
                    self._folder_widgets.append((row, item))
            self.files_container.show_all()
        dirs = sum(1 for i in items if i.is_dir())
        files = len(items) - dirs
        self.status_label.set_label(f"{dirs} folders, {files} files")
        self._is_loading = False

    def _show_empty_state(self):
        icon_name = "user-trash-symbolic" if self._is_in_trash() else "folder-open-symbolic"
        label_text = "Trash is empty" if self._is_in_trash() else "Folder is empty"
        empty_box = Box(name="explorer-empty-state", orientation="v", h_expand=True, v_expand=True,
                        h_align="center", v_align="center", spacing=12,
                        children=[Image(icon_name=icon_name, icon_size=48, name="explorer-empty-icon"),
                                  Label(name="explorer-empty-label", label=label_text)])
        self.files_container.pack_start(empty_box, True, True, 0)
        self.files_container.show_all()

    def _create_file_row(self, path: Path) -> Button:
        is_dir = path.is_dir()
        icon = Image(icon_name=self._get_icon_for_path(path), icon_size=24, name="explorer-file-icon")
        name_label = Label(name="explorer-file-name", label=path.name, h_align="start", h_expand=True, ellipsize="end")
        size_text = ""
        if not is_dir:
            try:
                size_text = self._format_size(path.stat().st_size)
            except:
                pass
        size_label = Label(name="explorer-file-size", label=size_text, h_align="end")
        try:
            date_text = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        except:
            date_text = ""
        date_label = Label(name="explorer-file-date", label=date_text, h_align="end")
        content = Box(orientation="h", spacing=8, children=[icon, name_label, size_label, date_label])
        btn = Button(name="explorer-file-row", child=content)
        btn._path = path
        btn._is_dir = is_dir
        btn.connect("clicked", self._on_file_clicked)
        btn.connect("button-press-event", self._on_file_button_press)
        self._setup_drag_source(btn, path)
        if is_dir:
            self._setup_drop_target(btn, target_path=path)
            btn.get_style_context().add_class("directory")
        return btn

    def _get_icon_for_path(self, path: Path) -> str:
        if path.is_dir():
            return {"Documents": "folder-documents-symbolic", "Downloads": "folder-download-symbolic",
                    "Music": "folder-music-symbolic", "Pictures": "folder-pictures-symbolic",
                    "Videos": "folder-videos-symbolic", "Desktop": "user-desktop-symbolic"
                   }.get(path.name, "folder-symbolic")
        name_lower = path.name.lower()
        for compound_ext in ['.tar.gz', '.tar.bz2', '.tar.xz', '.tar.zst']:
            if name_lower.endswith(compound_ext):
                return "package-x-generic-symbolic"
        ext = path.suffix.lower()
        return {".png": "image-x-generic-symbolic", ".jpg": "image-x-generic-symbolic",
                ".jpeg": "image-x-generic-symbolic", ".gif": "image-x-generic-symbolic",
                ".webp": "image-x-generic-symbolic", ".svg": "image-x-generic-symbolic",
                ".mp4": "video-x-generic-symbolic", ".mkv": "video-x-generic-symbolic",
                ".mp3": "audio-x-generic-symbolic", ".flac": "audio-x-generic-symbolic",
                ".pdf": "x-office-document-symbolic", ".txt": "text-x-generic-symbolic",
                ".zip": "package-x-generic-symbolic", ".rar": "package-x-generic-symbolic",
                ".7z": "package-x-generic-symbolic", ".tar": "package-x-generic-symbolic",
                ".gz": "package-x-generic-symbolic", ".xz": "package-x-generic-symbolic",
                ".py": "text-x-script-symbolic", ".sh": "text-x-script-symbolic",
               }.get(ext, "text-x-generic-symbolic")

    def _format_size(self, size: int) -> str:
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.1f} {unit}" if unit != 'B' else f"{size} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    def _on_file_clicked(self, btn):
        self._set_navigation_lock()
        if self._pending_drop_source:
            return
        if btn._is_dir:
            self._navigate_to(btn._path)
        else:
            exec_shell_command_async(f'xdg-open "{btn._path}"')

    def _on_file_button_press(self, btn, event) -> bool:
        if event.button == 3 and not self._pending_drop_source:
            self._show_context_menu(btn, event)
            return True
        return False

    def _is_archive(self, path: Path) -> bool:
        if path.is_dir():
            return False
        name_lower = path.name.lower()
        for ext in self._archive_extensions_compound:
            if name_lower.endswith(ext):
                return True
        return path.suffix.lower() in self._archive_extensions_simple

    def _extract_archive(self, path: Path):
        self._set_navigation_lock()
        if not path.exists():
            self.status_label.set_label("File not found")
            return
        archive_dir = path.parent
        archive_name = path.name
        self.status_label.set_label(f"Extracting: {archive_name}...")
        def do_extract():
            try:
                result = subprocess.run(
                    ["ouch", "decompress", archive_name, "--yes"],
                    cwd=str(archive_dir),
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                def update_ui():
                    if result.returncode == 0:
                        self.status_label.set_label(f"Extracted: {archive_name}")
                        self._load_directory()
                    else:
                        error = result.stderr.strip() or result.stdout.strip() or "Unknown error"
                        self.status_label.set_label(f"Failed: {error[:40]}")
                    return False
                GLib.idle_add(update_ui)
            except subprocess.TimeoutExpired:
                GLib.idle_add(lambda: self.status_label.set_label("Extract timed out"))
            except FileNotFoundError:
                GLib.idle_add(lambda: self.status_label.set_label("ouch not installed"))
            except Exception as e:
                GLib.idle_add(lambda: self.status_label.set_label(f"Error: {str(e)[:40]}"))
        thread = threading.Thread(target=do_extract, daemon=True)
        thread.start()

    # ==================== INLINE RENAME ====================

    def _show_rename_inline(self, path: Path, file_row: Gtk.Widget):
        self._close_rename_widget()
        self._rename_path = path
        self._menu_open = True
        self._cancel_pending_hide()
        
        # Enable keyboard FIRST
        self._set_keyboard_interactive(True)
        
        self._rename_widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._rename_widget.set_name("explorer-rename-container")
        
        entry = Gtk.Entry()
        entry.set_name("explorer-rename-entry")
        entry.set_text(path.name)
        entry.set_can_focus(True)
        
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_box.set_name("explorer-rename-buttons")
        btn_box.set_halign(Gtk.Align.END)
        
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.set_name("explorer-rename-btn")
        cancel_btn.get_style_context().add_class("cancel")
        cancel_btn.connect("clicked", lambda _: self._close_rename_widget())
        
        confirm_btn = Gtk.Button(label="Rename")
        confirm_btn.set_name("explorer-rename-btn")
        confirm_btn.get_style_context().add_class("confirm")
        confirm_btn.set_sensitive(False)
        confirm_btn.connect("clicked", lambda _: self._do_rename_inline(entry.get_text()))
        
        original_name = path.name
        def on_text_changed(entry_widget):
            new_text = entry_widget.get_text().strip()
            has_change = new_text != original_name and len(new_text) > 0
            confirm_btn.set_sensitive(has_change)
        
        entry.connect("changed", on_text_changed)
        
        def on_entry_activate(entry_widget):
            if confirm_btn.get_sensitive():
                self._do_rename_inline(entry_widget.get_text())
        
        entry.connect("activate", on_entry_activate)
        entry.connect("key-press-event", self._on_rename_key_press)
        
        btn_box.pack_start(cancel_btn, False, False, 0)
        btn_box.pack_start(confirm_btn, False, False, 0)
        
        self._rename_widget.pack_start(entry, False, False, 0)
        self._rename_widget.pack_start(btn_box, False, False, 0)
        
        parent = file_row.get_parent()
        if parent:
            children = parent.get_children()
            idx = children.index(file_row) if file_row in children else -1
            if idx >= 0:
                parent.pack_start(self._rename_widget, False, False, 0)
                parent.reorder_child(self._rename_widget, idx + 1)
        
        self._rename_widget.show_all()
        
        # ◄ Store entry reference for focus
        self._rename_entry = entry
        
        # Prepare selection bounds
        is_file = path.is_file()
        has_dot = '.' in path.name and not path.name.startswith('.')
        last_dot = path.name.rfind('.') if has_dot else -1
        
        def force_focus():
            try:
                # Force window to take keyboard focus
                self.present()
                
                # Set focus at window level
                self.set_focus(self._rename_entry)
                
                # Also try direct grab
                self._rename_entry.grab_focus()
                
                # Select appropriate region
                if is_file and last_dot > 0:
                    self._rename_entry.select_region(0, last_dot)
                else:
                    self._rename_entry.select_region(0, len(path.name))
            except Exception as e:
                print(f"Focus error: {e}")
            return False
        
        # Multiple attempts with increasing delays
        GLib.idle_add(force_focus)
        GLib.timeout_add(50, force_focus)
        GLib.timeout_add(100, force_focus)
        GLib.timeout_add(200, force_focus)

    def _on_rename_key_press(self, entry, event) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self._close_rename_widget()
            return True
        return False

    def _close_rename_widget(self):
        if self._rename_widget:
            self._rename_widget.destroy()
            self._rename_widget = None
        self._rename_path = None
        self._menu_open = False
        # ──── Disable keyboard when rename is done ────              # ◄ NEW
        self._set_keyboard_interactive(False)

    def _do_rename_inline(self, new_name: str):
        if not self._rename_path:
            self._close_rename_widget()
            return
        old_path = self._rename_path
        new_name = new_name.strip()
        self._close_rename_widget()
        try:
            if not new_name:
                self.status_label.set_label("Error: Name cannot be empty")
                return
            if '/' in new_name or '\0' in new_name:
                self.status_label.set_label("Error: Invalid characters in name")
                return
            if new_name == old_path.name:
                return
            new_path = old_path.parent / new_name
            if new_path.exists():
                self.status_label.set_label(f"Error: '{new_name}' already exists")
                return
            old_path.rename(new_path)
            self.status_label.set_label(f"Renamed to: {new_name}")
            self._load_directory()
        except PermissionError:
            self.status_label.set_label("Error: Permission denied")
        except Exception as e:
            self.status_label.set_label(f"Rename failed: {str(e)[:30]}")

    # ==================== CONTEXT MENU ====================

    def _show_context_menu(self, btn, event):
        self._menu_open = True
        self._cancel_pending_hide()
        self._close_rename_widget()
        
        menu = Gtk.Menu()
        menu.set_name("explorer-context-menu")
        menu.connect("deactivate", lambda m: setattr(self, '_menu_open', False))
        
        path = btn._path
        
        open_item = Gtk.MenuItem(label="Open")
        open_item.connect("activate", lambda _: self._on_file_clicked(btn))
        menu.append(open_item)
        
        rename_item = Gtk.MenuItem(label="Rename")
        rename_item.connect("activate", lambda _, b=btn, p=path: self._show_rename_inline(p, b))
        menu.append(rename_item)
        
        term_dir = path if btn._is_dir else path.parent
        term_item = Gtk.MenuItem(label="Open in Terminal")
        term_item.connect("activate", lambda _, d=term_dir: exec_shell_command_async(
            f'{os.environ.get("TERMINAL", "kitty")} --working-directory "{d}"'))
        menu.append(term_item)
        
        menu.append(Gtk.SeparatorMenuItem())
        
        copy_item = Gtk.MenuItem(label="Copy Path")
        copy_item.connect("activate", lambda _: Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(str(path), -1))
        menu.append(copy_item)
        
        if self._is_archive(path):
            extract_item = Gtk.MenuItem(label="Extract")
            extract_item.connect("activate", lambda _, p=path: self._extract_archive(p))
            menu.append(extract_item)
        
        menu.append(Gtk.SeparatorMenuItem())
        
        if self._is_in_trash():
            restore = Gtk.MenuItem(label="Restore")
            restore.connect("activate", lambda _, p=path: self._restore_from_trash(p))
            menu.append(restore)
            delete = Gtk.MenuItem(label="Delete Permanently")
            delete.connect("activate", lambda _, p=path: self._delete_permanently(p))
            menu.append(delete)
        else:
            trash = Gtk.MenuItem(label="Move to Trash")
            trash.connect("activate", lambda _, p=path: self._move_to_trash(p))
            menu.append(trash)
        
        menu.show_all()
        menu.popup_at_pointer(event)

    def _move_to_trash(self, path):
        try:
            Gio.File.new_for_path(str(path)).trash(None)
            self.status_label.set_label(f"Moved to trash: {path.name}")
        except Exception as e:
            self.status_label.set_label(f"Error: {e}")

    def _restore_from_trash(self, path):
        try:
            info_file = self._trash_info_path / f"{path.name}.trashinfo"
            if info_file.exists():
                with open(info_file) as f:
                    for line in f:
                        if line.startswith("Path="):
                            orig = Path(urllib.parse.unquote(line[5:].strip()))
                            orig.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(path), str(self._get_unique_path(orig)))
                            info_file.unlink()
                            self.status_label.set_label(f"Restored: {path.name}")
                            return
            shutil.move(str(path), str(self._get_unique_path(Path.home() / path.name)))
            self.status_label.set_label(f"Restored to Home: {path.name}")
        except Exception as e:
            self.status_label.set_label(f"Error: {e}")

    def _delete_permanently(self, path):
        try:
            shutil.rmtree(str(path)) if path.is_dir() else path.unlink()
            info_file = self._trash_info_path / f"{path.name}.trashinfo"
            if info_file.exists():
                info_file.unlink()
            self.status_label.set_label(f"Deleted: {path.name}")
        except Exception as e:
            self.status_label.set_label(f"Error: {e}")

    def _on_clear_trash_clicked(self, btn):
        self._set_navigation_lock()
        try:
            count = 0
            if self._trash_path.exists():
                for item in self._trash_path.iterdir():
                    try:
                        shutil.rmtree(str(item)) if item.is_dir() else item.unlink()
                        count += 1
                    except:
                        pass
            if self._trash_info_path.exists():
                for item in self._trash_info_path.iterdir():
                    try:
                        item.unlink()
                    except:
                        pass
            self.status_label.set_label(f"Trash emptied: {count} items")
            self._load_directory()
        except Exception as e:
            self.status_label.set_label(f"Error: {e}")

    # ==================== NAV HANDLERS ====================

    def _on_back_clicked(self, btn):
        self._set_navigation_lock()
        if self._pending_drop_source:
            return
        if self._history_index > 0:
            self._history_index -= 1
            self._navigate_to(self._history[self._history_index])

    def _on_forward_clicked(self, btn):
        self._set_navigation_lock()
        if self._pending_drop_source:
            return
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            self._navigate_to(self._history[self._history_index])

    def _on_up_clicked(self, btn):
        self._set_navigation_lock()
        if self._pending_drop_source:
            return
        parent = self._current_path.parent
        if parent != self._current_path:
            self._navigate_to(parent)

    def _on_home_clicked(self, btn):
        self._set_navigation_lock()
        if self._pending_drop_source:
            return
        self._navigate_to(Path.home())

    def _on_toggle_hidden(self, btn):
        self._set_navigation_lock()
        if self._pending_drop_source:
            return
        self._show_hidden = not self._show_hidden
        if self._show_hidden:
            btn.get_style_context().add_class("active")
        else:
            btn.get_style_context().remove_class("active")
        self._load_directory()

    def _on_pin_clicked(self, btn):
        self._set_navigation_lock()
        self._is_pinned = not self._is_pinned
        if self._is_pinned:
            btn.get_style_context().add_class("active")
            self._cancel_pending_hide()
        else:
            btn.get_style_context().remove_class("active")

    # ==================== VISIBILITY ====================

    def _on_explorer_enter(self, widget, event) -> bool:
        self._cancel_pending_hide()
        self._cancel_activator_hover_timer()
        self._cursor_inside = True
        return True

    def _on_explorer_leave(self, widget, event) -> bool:
        if event.detail == Gdk.NotifyType.INFERIOR:
            return True
        self._cursor_inside = False
        if (self._is_pinned or self._menu_open or self._drag_in_progress or 
            self._pending_drop_source or self._post_drag_grace or self._navigation_lock):
            return True
        self._schedule_hide()
        return True

    def _schedule_hide(self):
        self._cancel_pending_hide()
        self._pending_hide = GLib.timeout_add(300, self._do_hide)

    def _cancel_pending_hide(self):
        if self._pending_hide:
            GLib.source_remove(self._pending_hide)
            self._pending_hide = None

    def _do_hide(self) -> bool:
        self._pending_hide = None
        if (self._is_pinned or self._menu_open or self._drag_in_progress or 
            self._pending_drop_source or self._post_drag_grace or self._navigation_lock):
            return False
        if self._is_cursor_over_explorer():
            self._cursor_inside = True
            return False
        self.revealer.set_reveal_child(False)
        return False

    def destroy(self):
        self._close_rename_widget()
        self._cleanup_file_monitor()
        self._cancel_pending_hide()
        self._cancel_drag_hover_timer()
        self._cancel_post_drag_grace()
        self._cleanup_pending_drop()
        self._cancel_activator_hover_timer()
        self._stop_drag_scroll()
        if self._navigation_lock_timer:
            GLib.source_remove(self._navigation_lock_timer)
        self._volume_monitor = None
        super().destroy()