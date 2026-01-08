from fabric.hyprland.service import Hyprland
from fabric.utils.helpers import get_desktop_applications
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.eventbox import EventBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk, GLib, GdkPixbuf

import json
import cairo
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

import modules.icons as icons
from utils.icon_resolver import IconResolver


BASE_SCALE = 0.1
TARGET = [Gtk.TargetEntry.new("text/plain", Gtk.TargetFlags.SAME_APP, 0)]
UPDATE_DELAY_MS = 150
MAX_WORKSPACES = 9
ICON_CACHE_SIZE = 100

screen = Gdk.Screen.get_default()
CURRENT_WIDTH = screen.get_width()
CURRENT_HEIGHT = screen.get_height()

icon_resolver = IconResolver()
connection = Hyprland()


@dataclass
class WindowInfo:
    address: str
    title: str
    app_id: str
    workspace_id: int
    monitor_id: int
    position: Tuple[int, int]
    size: Tuple[int, int]
    transform: int


@dataclass
class MonitorInfo:
    width: int
    height: int
    scale: float = 1.0
    x: int = 0
    y: int = 0


class IconCache:
    def __init__(self, max_size: int = ICON_CACHE_SIZE):
        self._cache: Dict[Tuple[str, int], GdkPixbuf.Pixbuf] = {}
        self._max_size = max_size
        self._access_order: List[Tuple[str, int]] = []
    
    def get(self, app_id: str, size: int) -> Optional[GdkPixbuf.Pixbuf]:
        key = (app_id, size)
        if key in self._cache:
            self._access_order.remove(key)
            self._access_order.append(key)
            return self._cache[key]
        return None
    
    def put(self, app_id: str, size: int, pixbuf: GdkPixbuf.Pixbuf):
        key = (app_id, size)
        if key in self._cache:
            self._access_order.remove(key)
        
        while len(self._cache) >= self._max_size and self._access_order:
            old_key = self._access_order.pop(0)
            del self._cache[old_key]
        
        self._cache[key] = pixbuf
        self._access_order.append(key)
    
    def clear(self):
        self._cache.clear()
        self._access_order.clear()


class WindowManager:
    def __init__(self):
        self._cached_clients = None
        self._cached_monitors = None
    
    def get_monitors(self) -> Dict[int, MonitorInfo]:
        monitors_data = connection.send_command("j/monitors").reply.decode()
        monitors_json = json.loads(monitors_data)
        
        monitors = {}
        for monitor in monitors_json:
            monitors[monitor["id"]] = MonitorInfo(
                width=monitor["width"],
                height=monitor["height"],
                scale=monitor.get("scale", 1.0),
                x=monitor["x"],
                y=monitor["y"]
            )
        
        self._cached_monitors = monitors
        return monitors
    
    def get_windows(self) -> List[WindowInfo]:
        clients_data = connection.send_command("j/clients").reply.decode()
        clients_json = json.loads(clients_data)
        monitors = self.get_monitors()
        
        windows = []
        for client in clients_json:
            monitors.get(client["monitor"], MonitorInfo(CURRENT_WIDTH, CURRENT_HEIGHT))
            windows.append(WindowInfo(
                address=client["address"],
                title=client["title"],
                app_id=client["initialClass"],
                workspace_id=client["workspace"]["id"],
                monitor_id=client["monitor"],
                position=tuple(client["at"]),
                size=tuple(client["size"]),
                transform=client.get("transform", 0)
            ))
        
        self._cached_clients = windows
        return windows
    
    def clear_cache(self):
        self._cached_clients = None
        self._cached_monitors = None


class ApplicationManager:
    def __init__(self):
        self._all_apps = []
        self._app_identifiers = {}
        self._last_update = 0
        self._update_interval = 30
        
        self._load_applications()
    
    def _load_applications(self):
        current_time = GLib.get_monotonic_time() / 1000000
        
        if current_time - self._last_update > self._update_interval:
            self._all_apps = get_desktop_applications()
            self._app_identifiers = self._build_app_identifiers_map()
            self._last_update = current_time
    
    def _normalize_window_class(self, class_name: str) -> str:
        normalized = class_name.lower()
        
        for suffix in [".bin", ".exe", ".so", "-bin", "-gtk", ".AppImage"]:
            if normalized.endswith(suffix):
                normalized = normalized[:-len(suffix)]
        
        return normalized
    
    def _build_app_identifiers_map(self) -> Dict[str, object]:
        identifiers = {}
        
        for app in self._all_apps:
            keys = set()
            
            if app.name: keys.add(app.name.lower())
            if app.display_name: keys.add(app.display_name.lower())
            if app.window_class:
                keys.add(app.window_class.lower())
                keys.add(self._normalize_window_class(app.window_class))
            
            if app.executable:
                exe_basename = app.executable.split('/')[-1].lower()
                keys.add(exe_basename)
                keys.add(self._normalize_window_class(exe_basename))
            
            if app.command_line:
                cmd_first = app.command_line.split()[0] if app.command_line.split() else ""
                if cmd_first:
                    cmd_base = cmd_first.split('/')[-1].lower()
                    keys.add(cmd_base)
                    keys.add(self._normalize_window_class(cmd_base))
            
            for key in keys:
                if key and key not in identifiers:
                    identifiers[key] = app

        return identifiers
    
    def find_app(self, app_identifier: str) -> Optional[object]:
        
        self._load_applications()
        
        normalized_id = str(app_identifier).lower()
        
        if normalized_id in self._app_identifiers:
            return self._app_identifiers[normalized_id]
        
        norm_id = self._normalize_window_class(normalized_id)
        if norm_id in self._app_identifiers:
            return self._app_identifiers[norm_id]
        
        for app in self._all_apps:
            app_attrs = [
                app.name and app.name.lower(),
                app.window_class and app.window_class.lower(),
                app.display_name and app.display_name.lower(),
                app.executable and app.executable.split('/')[-1].lower(),
            ]
            
            if any(attr == normalized_id or attr == norm_id for attr in app_attrs if attr):
                return app
        
        return None

def create_surface_from_widget(widget: Gtk.Widget) -> cairo.ImageSurface:
    alloc = widget.get_allocation()
    surface = cairo.ImageSurface(
        cairo.Format.ARGB32,
        alloc.width,
        alloc.height,
    )
    
    cr = cairo.Context(surface)
    cr.set_operator(cairo.OPERATOR_CLEAR)
    cr.paint()
    cr.set_operator(cairo.OPERATOR_OVER)
    
    widget.draw(cr)
    return surface

class HyprlandWindowButton(Button):
    def __init__(
        self,
        app_manager: ApplicationManager,
        icon_cache: IconCache,
        window_info: WindowInfo,
        scale: float,
        monitors: Dict[int, MonitorInfo]
    ):
        self.window_info = window_info
        self.app_manager = app_manager
        self.icon_cache = icon_cache
        self.scale = scale
        
        if window_info.transform in [0, 2]:
            self.display_size = (
                int(window_info.size[0] * scale),
                int(window_info.size[1] * scale)
            )
        else:
            self.display_size = (
                int(window_info.size[1] * scale),
                int(window_info.size[0] * scale)
            )
        
        icon_size = int(min(self.display_size) * 0.5)
        icon_pixbuf = self._get_icon_pixbuf(icon_size)
        
        super().__init__(
            name="overview-client-box",
            image=Image(pixbuf=icon_pixbuf),
            tooltip_text=window_info.title,
            size=self.display_size,
            on_clicked=self._on_button_click,
            on_button_press_event=self._on_button_press,
            on_drag_data_get=self._on_drag_data_get,
            on_drag_begin=self._on_drag_begin,
        )
        
        self.drag_source_set(
            start_button_mask=Gdk.ModifierType.BUTTON1_MASK,
            targets=TARGET,
            actions=Gdk.DragAction.COPY,
        )
        
        self.connect("key_press_event", self._on_key_press)
    
    def _get_icon_pixbuf(self, size: int) -> GdkPixbuf.Pixbuf:
        cached = self.icon_cache.get(self.window_info.app_id, size)
        if cached:
            return cached
        
        desktop_app = self.app_manager.find_app(self.window_info.app_id)
        
        icon_pixbuf = None
        if desktop_app and hasattr(desktop_app, 'get_icon_pixbuf'):
            icon_pixbuf = desktop_app.get_icon_pixbuf(size=size)
        
        if not icon_pixbuf:
            icon_pixbuf = icon_resolver.get_icon_pixbuf(self.window_info.app_id, size)
        
        if not icon_pixbuf:
            for fallback_icon in ["application-x-executable-symbolic", "image-missing"]:
                icon_pixbuf = icon_resolver.get_icon_pixbuf(fallback_icon, size)
                if icon_pixbuf:
                    break
        
        if icon_pixbuf and (icon_pixbuf.get_width() != size or icon_pixbuf.get_height() != size):
            icon_pixbuf = icon_pixbuf.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
        
        if icon_pixbuf:
            self.icon_cache.put(self.window_info.app_id, size, icon_pixbuf)
        
        return icon_pixbuf or self._create_empty_pixbuf(size)
    
    def _create_empty_pixbuf(self, size: int) -> GdkPixbuf.Pixbuf:
        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, size, size)
        pixbuf.fill(0x00000000)
        return pixbuf
    
    def _on_button_press(self, widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button == 3:
            connection.send_command(f"/dispatch closewindow address:{self.window_info.address}")
            return True
        return False
    
    def _on_key_press(self, widget: Gtk.Widget, event: Gdk.EventKey) -> bool:
        if event.get_state() & Gdk.ModifierType.SHIFT_MASK:
            keyval = event.keyval
            if keyval in [Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space]:
                connection.send_command(f"/dispatch closewindow address:{self.window_info.address}")
                return True
        return False
    
    def _on_drag_data_get(self, widget: Gtk.Widget, context: Gdk.DragContext, data: Gtk.SelectionData, info: int, time: int):
        data.set_text(self.window_info.address, -1)
    
    def _on_drag_begin(self, widget: Gtk.Widget, context: Gdk.DragContext):
        surface = create_surface_from_widget(self)
        Gtk.drag_set_icon_surface(context, surface)
    
    def _on_button_click(self, button: Gtk.Button):
        commands = [
            f"/dispatch workspace {self.window_info.workspace_id}",
            f"/dispatch focuswindow address:{self.window_info.address}"
        ]
        
        for cmd in commands:
            connection.send_command(cmd)


class WorkspaceEventBox(EventBox):
    def __init__(self, workspace_id: int, has_windows: bool, fixed: Optional[Gtk.Fixed] = None, monitor_info: Optional[MonitorInfo] = None):
        self.workspace_id = workspace_id
        self.has_windows = has_windows
        
        if monitor_info:
            width = monitor_info.width
            height = monitor_info.height
            scale = monitor_info.scale
        else:
            width = CURRENT_WIDTH
            height = CURRENT_HEIGHT
            scale = 1.0
        
        scaled_width = int(width * BASE_SCALE * scale)
        scaled_height = int(height * BASE_SCALE * scale)
        
        main_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main_container.set_size_request(scaled_width, scaled_height)
        
        if has_windows and fixed and fixed.get_children():
            self.fixed = fixed
            main_container.add(fixed)
            fixed.show()
        else:
            self.fixed = None
            plus_label = Label(
                name="overview-add-label",
                h_expand=True,
                v_expand=True,
                markup=icons.circle_plus,
            )
            main_container.add(plus_label)
            plus_label.show()
            
            plus_label.set_opacity(0.5)
        
        super().__init__(
            name="overview-workspace-bg",
            child=main_container,
            on_drag_data_received=self._on_drag_data_received,
            on_button_press_event=self._on_button_press,
        )
        
        self.drag_dest_set(
            Gtk.DestDefaults.ALL,
            TARGET,
            Gdk.DragAction.COPY,
        )
    
    def _on_drag_data_received(self, widget: Gtk.Widget, context: Gdk.DragContext, x: int, y: int, data: Gtk.SelectionData, info: int, time: int):
        connection.send_command(f"/dispatch movetoworkspacesilent {self.workspace_id},address:{data.get_data().decode()}")
    
    def _on_button_press(self, widget: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button == 1 and self.has_windows:
            connection.send_command(f"/dispatch workspace {self.workspace_id}")
            return True
        return False


class Overview(Box):
    def __init__(self, monitor_id: int = 0, **kwargs):
        super().__init__(name="overview", orientation="v", spacing=8, **kwargs)
        
        self.monitor_id = monitor_id
        self.window_manager = WindowManager()
        self.app_manager = ApplicationManager()
        self.icon_cache = IconCache()
        
        self.workspace_start, self.workspace_end = self._get_workspace_range()
        
        self.workspace_boxes: Dict[int, Gtk.Fixed] = {}
        self.window_buttons: Dict[str, HyprlandWindowButton] = {}
        
        self._update_pending = False
        self._update_timer_id = 0
        
        self._connect_events()
        self.schedule_update()
    
    def _get_workspace_range(self) -> Tuple[int, int]:
        try:
            from utils.monitor_manager import get_monitor_manager
            monitor_manager = get_monitor_manager()
            workspace_range = monitor_manager.get_workspace_range_for_monitor(self.monitor_id)
            start, end = workspace_range[0], workspace_range[1]
            
            if end - start + 1 > MAX_WORKSPACES:
                end = start + MAX_WORKSPACES - 1
                
            return start, end
        except ImportError:
            return 1, min(9, MAX_WORKSPACES)
    
    def _connect_events(self):
        events = [
            ("event::openwindow", self.schedule_update),
            ("event::closewindow", self.schedule_update),
            ("event::movewindow", self.schedule_update),
            ("event::windowtitle", self.schedule_update),
            ("event::changefloatingmode", self.schedule_update),
        ]
        
        for event, handler in events:
            connection.connect(event, handler)
    
    def schedule_update(self, *args):
        if self._update_pending:
            return
        
        self._update_pending = True
        
        if self._update_timer_id:
            GLib.source_remove(self._update_timer_id)
        
        self._update_timer_id = GLib.timeout_add(
            UPDATE_DELAY_MS, 
            self._perform_update
        )
    
    def _perform_update(self) -> bool:
        self._update_pending = False
        self._update_timer_id = 0
        
        self._update_ui()
        return False
    
    def _update_ui(self):
        self._clear_ui()
        
        monitors = self.window_manager.get_monitors()
        windows = self.window_manager.get_windows()
        current_monitor_info = monitors.get(self.monitor_id, MonitorInfo(CURRENT_WIDTH, CURRENT_HEIGHT))
        
        workspace_windows: Dict[int, List[WindowInfo]] = {}
        for window in windows:
            if self.workspace_start <= window.workspace_id <= self.workspace_end:
                if window.workspace_id not in workspace_windows:
                    workspace_windows[window.workspace_id] = []
                workspace_windows[window.workspace_id].append(window)
        
        rows = 3
        cols = 3
        row_boxes = [Box(spacing=8, orientation="h") for _ in range(rows)]
        
        for row_box in row_boxes:
            self.add(row_box)
        
        for idx, workspace_id in enumerate(range(self.workspace_start, self.workspace_end + 1)):
            if idx >= rows * cols:
                break
                
            row = idx // cols
            col = idx % cols
            
            fixed_container = Gtk.Fixed()
            self.workspace_boxes[workspace_id] = fixed_container
            
            has_windows = workspace_id in workspace_windows and len(workspace_windows[workspace_id]) > 0
            
            if has_windows:
                for window in workspace_windows[workspace_id]:
                    self._add_window_to_container(window, fixed_container, monitors, current_monitor_info.scale)
            
            workspace_widget = self._create_workspace_widget(workspace_id, has_windows, fixed_container, current_monitor_info)
            row_boxes[row].add(workspace_widget)
        
        self.show_all()
    
    def _add_window_to_container(self, window: WindowInfo, container: Gtk.Fixed, monitors: Dict[int, MonitorInfo], scale: float):
        monitor_info = monitors.get(window.monitor_id, MonitorInfo(CURRENT_WIDTH, CURRENT_HEIGHT))
        effective_scale = BASE_SCALE * scale
        
        pos_x = abs(window.position[0] - monitor_info.x) * effective_scale
        pos_y = abs(window.position[1] - monitor_info.y) * effective_scale
        
        button = HyprlandWindowButton(
            app_manager=self.app_manager,
            icon_cache=self.icon_cache,
            window_info=window,
            scale=effective_scale,
            monitors=monitors
        )
        
        container.put(button, int(pos_x), int(pos_y))
        self.window_buttons[window.address] = button
    
    def _create_workspace_widget(self, workspace_id: int, has_windows: bool, fixed_container: Gtk.Fixed, monitor_info: MonitorInfo) -> Box:
        workspace_label = Label(
            name="overview-workspace-label", 
            label=f"Workspace {workspace_id}"
        )
        
        if not has_windows:
            workspace_label.set_opacity(0.5)
        
        workspace_event_box = WorkspaceEventBox(
            workspace_id=workspace_id,
            has_windows=has_windows,
            fixed=fixed_container,
            monitor_info=monitor_info
        )
        
        return Box(
            name="overview-workspace-box",
            orientation="vertical",
            spacing=4,
            children=[workspace_label, workspace_event_box],
        )
    
    def _clear_ui(self):
        for child in self.get_children():
            self.remove(child)
        
        for button in self.window_buttons.values():
            button.destroy()
        self.window_buttons.clear()
        
        for fixed in self.workspace_boxes.values():
            fixed.destroy()
        self.workspace_boxes.clear()
    
    def destroy(self):
        if self._update_timer_id:
            GLib.source_remove(self._update_timer_id)
        
        self._clear_ui()
        self.icon_cache.clear()
        super().destroy()