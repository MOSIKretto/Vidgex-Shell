from fabric.hyprland.widgets import get_hyprland_connection
from fabric.utils import exec_shell_command, exec_shell_command_async
from fabric.utils.helpers import get_desktop_applications
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.eventbox import EventBox
from fabric.widgets.image import Image
from fabric.widgets.revealer import Revealer
from gi.repository import Gdk, GLib, Gtk
import cairo
import json
from modules.corners import MyCorner
from utils.icon_resolver import IconResolver
from widgets.wayland import WaylandWindow as Window


def createSurfaceFromWidget(widget):
    alloc = widget.get_allocation()
    surface = cairo.ImageSurface(cairo.Format.ARGB32, alloc.width, alloc.height)
    cr = cairo.Context(surface)
    cr.set_source_rgba(255, 255, 255, 0)
    cr.rectangle(0, 0, alloc.width, alloc.height)
    cr.fill()
    widget.draw(cr)
    return surface


class Dock(Window):
    _instances = []
    
    def __init__(self, monitor_id=0, integrated_mode=False, **kwargs):
        self.monitor_id = monitor_id
        self.integrated_mode = integrated_mode
        self.icon_size = 38
        self.effective_occlusion_size = 36 + self.icon_size
        self.always_show = False if not self.integrated_mode else False

        if not self.integrated_mode:
            self.actual_dock_is_horizontal = True
            super().__init__(
                name="dock-window",
                layer="top",
                anchor="bottom",
                margin="0px 0px 0px 0px",
                exclusivity="auto" if self.always_show else "none",
                monitor=monitor_id,
                **kwargs,
            )
            Dock._instances.append(self)
        else:
            self.actual_dock_is_horizontal = True

        if not self.integrated_mode:
            self.set_margin("-8px 0px 0px 0px")

        self.conn = get_hyprland_connection()
        self.icon_resolver = IconResolver()
        self._all_apps = get_desktop_applications()
        self.app_identifiers = self._build_app_identifiers_map()
        self.hide_id = None
        self._drag_in_progress = False
        self.is_mouse_over_dock_area = False
        self._current_active_window = None
        self._prevent_occlusion = False
        self._forced_occlusion = False
        self._destroyed = False
        self._occlusion_timer_id = None
        self._event_handlers = []
        self.dock_geometry = None
        self.monitor_info = None
        self._monitor_info_timer = None

        self.view = Box(name="viewport", spacing=4, orientation=Gtk.Orientation.HORIZONTAL)
        self.wrapper = Box(name="dock", children=[self.view], orientation=Gtk.Orientation.HORIZONTAL)
        self.wrapper.add_style_class("pills")
        
        self.dock_eventbox = EventBox()
        self.dock_eventbox.add(self.wrapper)
        self.dock_eventbox.connect("enter-notify-event", self._on_dock_enter)
        self.dock_eventbox.connect("leave-notify-event", self._on_dock_leave)

        self.corner_left = Box(
            name="dock-corner-left", orientation=Gtk.Orientation.VERTICAL, h_align="start",
            children=[Box(v_expand=True, v_align="fill"), MyCorner("bottom-right")]
        )
        self.corner_right = Box(
            name="dock-corner-right", orientation=Gtk.Orientation.VERTICAL, h_align="end",
            children=[Box(v_expand=True, v_align="fill"), MyCorner("bottom-left")]
        )
        
        self.dock_full = Box(
            name="dock-full", orientation=Gtk.Orientation.HORIZONTAL, h_expand=True, h_align="fill",
            children=[self.corner_left, self.dock_eventbox, self.corner_right]
        )

        self.dock_revealer = Revealer(
            name="dock-revealer",
            transition_type="slide-up",
            transition_duration=250,
            child_revealed=False,
            child=self.dock_full
        )

        self.hover_activator = EventBox()
        self.hover_activator.set_size_request(-1, 1)
        self.hover_activator.connect("enter-notify-event", self._on_hover_enter)
        self.hover_activator.connect("leave-notify-event", self._on_hover_leave)

        self.main_box = Box(
            orientation=Gtk.Orientation.VERTICAL,
            children=[self.hover_activator, self.dock_revealer],
            h_align="center",
        )
        self.add(self.main_box)

        self.view.drag_source_set(
            Gdk.ModifierType.BUTTON1_MASK,
            [Gtk.TargetEntry.new("text/plain", Gtk.TargetFlags.SAME_APP, 0)],
            Gdk.DragAction.MOVE
        )
        self.view.drag_dest_set(
            Gtk.DestDefaults.ALL,
            [Gtk.TargetEntry.new("text/plain", Gtk.TargetFlags.SAME_APP, 0)],
            Gdk.DragAction.MOVE
        )
        self.view.connect("drag-data-get", self.on_drag_data_get)
        self.view.connect("drag-data-received", self.on_drag_data_received)
        self.view.connect("drag-begin", self.on_drag_begin)
        self.view.connect("drag-end", self.on_drag_end)

        self._monitor_info_timer = GLib.timeout_add(100, self._update_monitor_info)

        if self.conn.ready:
            self.update_dock()
            self.update_active_window()
            if not self.integrated_mode:
                self._occlusion_timer_id = GLib.timeout_add(500, self._check_occlusion_wrapper)
        else:
            self._event_handlers.append(self.conn.connect("event::ready", lambda *args: self._on_ready()))

        events = [
            ("event::openwindow", self._on_window_change),
            ("event::closewindow", self._on_window_change),
            ("event::activewindow", self.update_active_window),
        ]
        
        if not self.integrated_mode:
            events.extend([
                ("event::workspace", self._on_window_change),
                ("event::movewindow", self._on_window_change),
                ("event::resizewindow", self._on_window_change),
            ])
        
        for event_name, handler in events:
            self._event_handlers.append(self.conn.connect(event_name, handler))

    def _on_ready(self):
        if self._destroyed:
            return
        self.update_dock()
        self.update_active_window()
        if not self.integrated_mode:
            self._occlusion_timer_id = GLib.timeout_add(250, self._check_occlusion_wrapper)

    def _check_occlusion_wrapper(self):
        return False if self._destroyed else self.check_occlusion_state()

    def _update_monitor_info(self):
        if self._destroyed or not self.conn:
            return False
        try:
            monitors = json.loads(self.conn.send_command("j/monitors").reply.decode())
            for monitor in monitors:
                if monitor.get("id") == self.monitor_id:
                    self.monitor_info = monitor
                    break
        except Exception:
            pass
        return False

    def _update_dock_geometry(self):
        if not self.monitor_info:
            self._update_monitor_info()
            return False
        
        alloc = self.get_allocation()
        m = self.monitor_info
        
        self.dock_geometry = {
            'x': m.get("x", 0) + (m.get("width", 1920) - alloc.width) // 2,
            'y': m.get("y", 0) + m.get("height", 1200) - alloc.height - 50,
            'width': alloc.width,
            'height': alloc.height
        }
        return False

    def _on_window_change(self, *args):
        self.update_dock()
        if not self.integrated_mode:
            self.check_occlusion_state()

    def _build_app_identifiers_map(self):
        identifiers = {}
        for app in self._all_apps:
            for attr in ['name', 'display_name', 'window_class']:
                val = getattr(app, attr, None)
                if val:
                    identifiers[val.lower()] = app
            for attr in ['executable', 'command_line']:
                val = getattr(app, attr, None)
                if val:
                    identifiers[val.split('/')[-1].lower()] = app
        return identifiers

    def _normalize_window_class(self, class_name):
        if not class_name: 
            return ""
        normalized = class_name.lower()
        for suffix in (".bin", ".exe", ".so", "-bin", "-gtk"):
            if normalized.endswith(suffix):
                return normalized[:-len(suffix)]
        return normalized

    def _classes_match(self, class1, class2):
        return class1 and class2 and self._normalize_window_class(class1) == self._normalize_window_class(class2)

    def _is_window_overlapping_dock(self, window):
        if not self.dock_geometry:
            self._update_dock_geometry()
            if not self.dock_geometry:
                return False
        
        w_at, w_size = window.get("at", [0, 0]), window.get("size", [0, 0])
        dock = self.dock_geometry
        
        x_overlap = w_at[0] < dock['x'] + dock['width'] and w_at[0] + w_size[0] > dock['x']
        y_overlap = w_at[1] < dock['y'] - 30 + dock['height'] + 30 and w_at[1] + w_size[1] > dock['y'] - 30
        
        return x_overlap and y_overlap and window.get("monitor", -1) == self.monitor_id

    def check_occlusion_state(self):
        if self.integrated_mode or self._destroyed or not self.conn:
            return False

        self._update_dock_geometry()
        try:
            clients = json.loads(self.conn.send_command("j/clients").reply.decode())
            current_ws = json.loads(self.conn.send_command("j/activeworkspace").reply.decode()).get("id", 0)
        except Exception:
            return True
        
        try:
            overlapping = any(self._is_window_overlapping_dock(w) for w in clients 
                             if w.get("workspace", {}).get("id") == current_ws)
        except Exception:
            overlapping = False
        
        if overlapping and not self.is_mouse_over_dock_area and not self._drag_in_progress:
            if self.dock_revealer.get_reveal_child():
                self.dock_revealer.set_reveal_child(False)
            if not self.always_show:
                self.dock_full.add_style_class("occluded")
        else:
            if not self.dock_revealer.get_reveal_child():
                self.dock_revealer.set_reveal_child(True)
            self.dock_full.remove_style_class("occluded")
        
        return True

    def find_app(self, app_identifier):
        if not app_identifier: 
            return None
        if isinstance(app_identifier, dict):
            for key in ["window_class", "executable", "command_line", "name", "display_name"]:
                app = self.find_app_by_key(app_identifier.get(key))
                if app: 
                    return app
            return None
        return self.find_app_by_key(app_identifier)
    
    def find_app_by_key(self, key_value):
        if not key_value:
            return None
        normalized_id = str(key_value).lower()
        app = self.app_identifiers.get(normalized_id)
        if app:
            return app
        for app in self._all_apps:
            for attr in ['name', 'display_name', 'window_class', 'executable', 'command_line']:
                val = getattr(app, attr, None)
                if val and normalized_id in val.lower():
                    return app
        return None

    def create_button(self, app_identifier, instances, window_class=None):
        desktop_app = self.find_app(app_identifier)
        
        icon_img = None
        if desktop_app:
            icon_img = desktop_app.get_icon_pixbuf(size=self.icon_size)
        
        id_value = app_identifier["name"] if isinstance(app_identifier, dict) else app_identifier
        
        for icon_name in [id_value, "application-x-executable-symbolic", "image-missing"]:
            if not icon_img:
                icon_img = self.icon_resolver.get_icon_pixbuf(icon_name, self.icon_size)
            else:
                break
                
        display_name = desktop_app.display_name or desktop_app.name if desktop_app else None
        tooltip = display_name or (id_value if isinstance(id_value, str) else "Unknown")
        if not display_name and instances and instances[0].get("title"):
            tooltip = instances[0]["title"]

        button = Button(
            child=Box(name="dock-icon", orientation="v", h_align="center", children=[Image(pixbuf=icon_img)]), 
            on_clicked=lambda *a: self.handle_app(app_identifier, instances, desktop_app),
            tooltip_text=tooltip, 
            name="dock-app-button",
        )
        
        button.app_identifier = app_identifier
        button.desktop_app = desktop_app
        button.instances = instances
        button.window_class = window_class
        
        button.add_style_class("instance")

        button.drag_source_set(
            Gdk.ModifierType.BUTTON1_MASK,
            [Gtk.TargetEntry.new("text/plain", Gtk.TargetFlags.SAME_APP, 0)],
            Gdk.DragAction.MOVE
        )
        button.drag_dest_set(
            Gtk.DestDefaults.ALL,
            [Gtk.TargetEntry.new("text/plain", Gtk.TargetFlags.SAME_APP, 0)],
            Gdk.DragAction.MOVE
        )
        for signal, handler in [
            ("drag-begin", self.on_drag_begin),
            ("drag-end", self.on_drag_end),
            ("drag-data-get", self.on_drag_data_get),
            ("drag-data-received", self.on_drag_data_received),
            ("enter-notify-event", self._on_child_enter)
        ]:
            button.connect(signal, handler)
        
        return button

    def handle_app(self, app_identifier, instances, desktop_app=None):
        if self._destroyed or not self.conn:
            return
        if instances:
            try:
                focused = json.loads(self.conn.send_command("j/activewindow").reply.decode()).get("address", "")
            except Exception:
                return
            idx = next((i for i, inst in enumerate(instances) if inst["address"] == focused), -1)
            next_inst = instances[(idx + 1) % len(instances)]
            exec_shell_command(f"hyprctl dispatch focuswindow address:{next_inst['address']}")
            return
        
        if desktop_app and desktop_app.launch():
            return
        else:
            desktop_app = self.find_app(app_identifier)
            
        cmd_to_run = None
        if isinstance(app_identifier, dict):
            cmd_to_run = (app_identifier.get('command_line') or 
                         app_identifier.get('executable') or 
                         app_identifier.get('name'))
        elif isinstance(app_identifier, str): 
            cmd_to_run = app_identifier
        
        if cmd_to_run: 
            exec_shell_command_async(f"nohup {cmd_to_run} &")

    def update_active_window(self, *args):
        if self._destroyed or not self.conn:
            return
        try:
            active_window = json.loads(self.conn.send_command("j/activewindow").reply.decode())
            active_class = active_window.get("initialClass") or active_window.get("class") if active_window else None
            self._current_active_window = active_class
        except Exception:
            return

        for button in self.view.get_children():
            if hasattr(button, 'window_class') and button.window_class:
                if active_class and self._classes_match(active_class, button.window_class):
                    button.add_style_class("active")
                else:
                    button.remove_style_class("active")

    def update_dock(self, *args):
        if self._destroyed or not self.conn:
            return

        self._all_apps = get_desktop_applications()
        self.app_identifiers = self._build_app_identifiers_map()
        try:
            clients = json.loads(self.conn.send_command("j/clients").reply.decode())
        except Exception:
            return
        running_windows = {}

        for c in clients:
            window_id = c.get("initialClass", "").lower() or c.get("class", "").lower()
            if not window_id:
                title = c.get("title", "").lower()
                possible_name = title.split(" - ")[0].strip()
                window_id = possible_name if possible_name and len(possible_name) > 1 else title or "unknown-app"
            
            running_windows.setdefault(window_id, []).append(c)
            norm_id = self._normalize_window_class(window_id)
            if norm_id != window_id:
                running_windows.setdefault(norm_id, []).extend(running_windows[window_id])

        open_buttons = []
        used_window_classes = set()

        for class_name, instances in running_windows.items():
            if class_name in used_window_classes:
                continue
                
            app = (self.app_identifiers.get(class_name) or
                   self.app_identifiers.get(self._normalize_window_class(class_name)) or
                   self.find_app_by_key(class_name))
            
            if not app and instances and instances[0].get("title"):
                title = instances[0].get("title", "")
                potential_name = title.split(" - ")[0].strip()
                if len(potential_name) > 2:
                    app = self.find_app_by_key(potential_name)

            identifier = {
                "name": app.name,
                "display_name": app.display_name,
                "window_class": app.window_class,
                "executable": app.executable,
                "command_line": app.command_line
            } if app else class_name

            open_buttons.append(self.create_button(identifier, instances, class_name))
            used_window_classes.add(class_name)

        self.view.children = open_buttons
        if not self.integrated_mode:
            GLib.idle_add(self._update_size)

        self._drag_in_progress = False
        if not self.integrated_mode:
            self.check_occlusion_state()

        self.update_active_window()

    def _update_size(self):
        if self._destroyed or self.integrated_mode:
            return False
        width, _ = self.view.get_preferred_width()
        self.set_size_request(width, -1)
        self._update_dock_geometry()
        return False

    def _on_hover_enter(self, *args):
        if self.integrated_mode: 
            return
        self.is_mouse_over_dock_area = True
        if self.hide_id:
            GLib.source_remove(self.hide_id)
            self.hide_id = None
        self.dock_revealer.set_reveal_child(True)
        if not self.always_show:
            self.dock_full.remove_style_class("occluded")

    def _on_hover_leave(self, *args):
        if self.integrated_mode: 
            return
        self.is_mouse_over_dock_area = False
        self.check_occlusion_state()

    def _on_dock_enter(self, widget, event):
        if self.integrated_mode: 
            return True
        self.is_mouse_over_dock_area = True
        if self.hide_id:
            GLib.source_remove(self.hide_id)
            self.hide_id = None
        self.dock_revealer.set_reveal_child(True)
        if not self.always_show:
            self.dock_full.remove_style_class("occluded")
        return True

    def _on_dock_leave(self, widget, event):
        if self.integrated_mode or event.detail == Gdk.NotifyType.INFERIOR:
            return True
        self.is_mouse_over_dock_area = False
        self.check_occlusion_state()
        if not self.always_show:
            self.dock_full.add_style_class("occluded")
        return True

    def _on_child_enter(self, widget, event):
        if self.integrated_mode: 
            return False
        self.is_mouse_over_dock_area = True
        if self.hide_id:
            GLib.source_remove(self.hide_id)
            self.hide_id = None
        return False

    def on_drag_begin(self, widget, drag_context):
        self._drag_in_progress = True
        self.wrapper.add_style_class("drag-in-progress")
        Gtk.drag_set_icon_surface(drag_context, createSurfaceFromWidget(widget))

    def on_drag_end(self, widget, drag_context):
        if not self._drag_in_progress:
            return

        def process_drag_end():
            display = Gdk.Display.get_default()
            _, x, y, _ = display.get_pointer()
            
            alloc = self.view.get_allocation()
            if not (alloc.x <= x <= alloc.x + alloc.width and alloc.y <= y <= alloc.y + alloc.height):
                instances_dragged = getattr(widget, 'instances', [])
                if instances_dragged:
                    address = instances_dragged[0].get("address")
                    if address:
                        exec_shell_command(f"hyprctl dispatch focuswindow address:{address}")

            self._drag_in_progress = False
            self.wrapper.remove_style_class("drag-in-progress")
            if not self.integrated_mode:
                self.check_occlusion_state()

        GLib.idle_add(process_drag_end)

    def _find_drag_target(self, widget):
        children = self.view.get_children()
        while widget is not None and widget not in children:
            widget = widget.get_parent() if hasattr(widget, "get_parent") else None
        return widget

    def on_drag_data_get(self, widget, drag_context, data_obj, info, time):
        target = self._find_drag_target(widget.get_parent() if isinstance(widget, Box) else widget)
        if target is not None:
            data_obj.set_text(str(self.view.get_children().index(target)), -1)

    def on_drag_data_received(self, widget, drag_context, x, y, data_obj, info, time):
        target = self._find_drag_target(widget.get_parent() if isinstance(widget, Box) else widget)
        if target is None: 
            return
        children = self.view.get_children()
        source_index = int(data_obj.get_text())
        target_index = children.index(target)

        if source_index != target_index:
            children.insert(target_index, children.pop(source_index))
            self.view.children = children

    def force_occlusion(self):
        if self.integrated_mode:
            return
        self._saved_always_show = self.always_show
        self.always_show = False
        self._forced_occlusion = True
        if not self.is_mouse_over_dock_area:
            self.dock_revealer.set_reveal_child(False)
    
    def restore_from_occlusion(self):
        if self.integrated_mode:
            return
        self._forced_occlusion = False
        if hasattr(self, '_saved_always_show'):
            self.always_show = self._saved_always_show
            delattr(self, '_saved_always_show')
        self.check_occlusion_state()

    @staticmethod
    def update_visibility(visible):
        for dock in Dock._instances:
            dock.set_visible(visible)
            if visible:
                GLib.idle_add(dock.check_occlusion_state)
            elif hasattr(dock, 'dock_revealer') and dock.dock_revealer.get_reveal_child():
                dock.dock_revealer.set_reveal_child(False)

    def destroy(self):
        """Proper cleanup of all resources"""
        if self._destroyed:
            return
        self._destroyed = True

        for timer_attr in ['_occlusion_timer_id', 'hide_id', '_monitor_info_timer']:
            timer_id = getattr(self, timer_attr, None)
            if timer_id:
                GLib.source_remove(timer_id)
                setattr(self, timer_attr, None)

        if hasattr(self, '_event_handlers'):
            for handler_id in self._event_handlers:
                if self.conn:
                    try:
                        self.conn.disconnect(handler_id)
                    except Exception:
                        pass
            self._event_handlers.clear()

        if self in Dock._instances:
            Dock._instances.remove(self)

        self._all_apps = []
        self.app_identifiers.clear()
        self.conn = None
        self.icon_resolver = None
        
        super().destroy()
    
    def __del__(self):
        """Fallback cleanup"""
        if not self._destroyed:
            self.destroy()