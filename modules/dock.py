import json
import cairo
from typing import Dict, List, Optional

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from fabric.hyprland.widgets import get_hyprland_connection
from fabric.utils import exec_shell_command, exec_shell_command_async
from fabric.utils.helpers import get_desktop_applications
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.eventbox import EventBox
from fabric.widgets.image import Image
from fabric.widgets.revealer import Revealer

from widgets.corners import MyCorner
from services.icon_resolver import IconResolver
from widgets.wayland import WaylandWindow as Window


def create_surface_from_widget(widget: Gtk.Widget) -> cairo.ImageSurface:
    alloc = widget.get_allocation()
    surface = cairo.ImageSurface(cairo.Format.ARGB32, alloc.width, alloc.height)
    cr = cairo.Context(surface)
    cr.set_source_rgba(255, 255, 255, 0)
    cr.rectangle(0, 0, alloc.width, alloc.height)
    cr.fill()
    widget.draw(cr)
    return surface


class Dock(Window):
    def __init__(self, monitor_id: int = 0, integrated_mode: bool = False, **kwargs):
        self.monitor_id = monitor_id
        self.integrated_mode = integrated_mode
        self.always_show = False

        self.icon_size = 24
        self.icon_scale_factor = 0.035

        exclusivity = "auto" if (not integrated_mode and self.always_show) else "none"

        super().__init__(
            name="dock-window",
            layer="top",
            anchor="bottom",
            margin="0px 0px 0px 0px",
            exclusivity=exclusivity,
            monitor=monitor_id,
            visible=False,
            **kwargs,
        )

        self.conn = get_hyprland_connection()
        self.icon_resolver = IconResolver()

        self._all_apps = get_desktop_applications()
        self.app_identifiers = self._build_app_identifiers_map()

        self._drag_in_progress = False
        self.is_mouse_over_dock_area = False
        self._current_active_window_class = None

        self._update_pending = False
        self._occlusion_pending = False

        self.dock_geometry = None
        self.monitor_info = None

        self._setup_ui()
        self._setup_event_handlers()
        self._update_monitor_info_once()

    def _setup_ui(self):
        self.view = Box(name="viewport", spacing=0, orientation=Gtk.Orientation.HORIZONTAL)
        self.wrapper = Box(name="dock", children=[self.view], orientation=Gtk.Orientation.HORIZONTAL)

        if self.integrated_mode:
            self.add(self.wrapper)
        else:
            self._setup_dock_window()

    def _setup_dock_window(self):
        self.dock_eventbox = EventBox()
        self.dock_eventbox.add(self.wrapper)
        self.dock_eventbox.connect("enter-notify-event", self._on_dock_enter)
        self.dock_eventbox.connect("leave-notify-event", self._on_dock_leave)

        self.corner_left = Box(
            name="dock-corner-left",
            orientation=Gtk.Orientation.VERTICAL,
            h_align="start",
            children=[Box(v_expand=True, v_align="fill"), MyCorner("bottom-right")]
        )
        self.corner_right = Box(
            name="dock-corner-right",
            orientation=Gtk.Orientation.VERTICAL,
            h_align="end",
            children=[Box(v_expand=True, v_align="fill"), MyCorner("bottom-left")]
        )

        self.dock_full = Box(
            name="dock-full",
            orientation=Gtk.Orientation.HORIZONTAL,
            h_expand=True,
            h_align="fill",
            children=[self.corner_left, self.dock_eventbox, self.corner_right]
        )

        self.dock_revealer = Revealer(
            name="dock-revealer",
            transition_type="slide-up",
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
        self._setup_drag_drop()

    def _setup_drag_drop(self):
        target_entry = Gtk.TargetEntry.new("text/plain", Gtk.TargetFlags.SAME_APP, 0)
        self.view.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, [target_entry], Gdk.DragAction.MOVE)
        self.view.drag_dest_set(Gtk.DestDefaults.ALL, [target_entry], Gdk.DragAction.MOVE)
        self.view.connect("drag-data-get", self._on_drag_data_get)
        self.view.connect("drag-data-received", self._on_drag_data_received)
        self.view.connect("drag-begin", self._on_drag_begin)
        self.view.connect("drag-end", self._on_drag_end)

    def _setup_event_handlers(self):
        events = [
            ("event::openwindow", self._schedule_full_update),
            ("event::closewindow", self._schedule_full_update),
            ("event::movewindow", self._schedule_occlusion_check),
            ("event::resizewindow", self._schedule_occlusion_check),
            ("event::workspace", self._schedule_occlusion_check),
            ("event::activewindow", self._on_active_window_event),
            ("event::monitoradded", self._update_monitor_info_once),
            ("event::monitorremoved", self._update_monitor_info_once)
        ]

        for event, handler in events:
            self.conn.connect(event, handler)

        if self.conn.ready:
            self._on_ready()
        else:
            self.conn.connect("event::ready", lambda *_: self._on_ready())

    def _on_ready(self):
        self.show_all()
        self._schedule_full_update()

    def _schedule_full_update(self, *_):
        if self._update_pending:
            return
        self._update_pending = True
        GLib.idle_add(self._perform_full_update)

    def _schedule_occlusion_check(self, *_):
        if self.integrated_mode or self._occlusion_pending:
            return
        self._occlusion_pending = True
        GLib.idle_add(self._perform_occlusion_check)

    def _on_active_window_event(self, *_):
        self._update_active_window_state()

    def _perform_full_update(self):
        self._update_pending = False
        clients = self._get_hyprland_json("j/clients")
        self._rebuild_dock_icons(clients)

        if not self.integrated_mode:
            self._perform_occlusion_logic(clients)

        return False

    def _perform_occlusion_check(self):
        self._occlusion_pending = False
        if self.integrated_mode:
            return False

        clients = self._get_hyprland_json("j/clients")
        self._perform_occlusion_logic(clients)
        return False

    def _get_hyprland_json(self, command: str):
        try:
            return json.loads(self.conn.send_command(command).reply.decode())
        except Exception:
            return []

    def _update_monitor_info_once(self, *_):
        try:
            monitors = self._get_hyprland_json("j/monitors")
            for monitor in monitors:
                if monitor.get("id") == self.monitor_id:
                    self.monitor_info = monitor

                    m_height = self.monitor_info.get("height", 1080)
                    calculated_size = int(m_height * self.icon_scale_factor)

                    if abs(self.icon_size - calculated_size) > 2:
                        self.icon_size = calculated_size
                        self._schedule_full_update()

                    self.dock_geometry = None
                    break
        except Exception:
            pass

    def _ensure_geometry(self):
        if self.dock_geometry:
            return

        if not self.monitor_info:
            self._update_monitor_info_once()
            if not self.monitor_info:
                return

        alloc = self.get_allocation()
        m_x = self.monitor_info.get("x", 0)
        m_y = self.monitor_info.get("y", 0)
        m_w = self.monitor_info.get("width")
        m_h = self.monitor_info.get("height")

        if not m_w or not m_h:
            return

        estimated_dock_height = self.icon_size + 24
        dock_w = alloc.width
        pos_x = m_x + (m_w - dock_w) // 2
        pos_y = m_y + m_h - estimated_dock_height

        self.dock_geometry = {
            'x': pos_x,
            'y': pos_y,
            'w': dock_w,
            'h': estimated_dock_height
        }

    def _perform_occlusion_logic(self, clients):
        if self.integrated_mode:
            return

        self._ensure_geometry()

        if not self.dock_geometry:
            return

        try:
            active_ws_data = json.loads(self.conn.send_command("j/activeworkspace").reply.decode())
            current_ws_id = active_ws_data.get("id", 0)
        except Exception:
            current_ws_id = 0

        d_x, d_y = self.dock_geometry['x'], self.dock_geometry['y']
        d_w, d_h = self.dock_geometry['w'], self.dock_geometry['h']

        overlap = False
        for win in clients:
            if win.get("workspace", {}).get("id") != current_ws_id:
                continue
            if win.get("monitor") != self.monitor_id:
                continue
            if win.get("floating") and not win.get("fullscreen"):
                continue

            w_at = win.get("at", [0, 0])
            w_sz = win.get("size", [0, 0])

            if (w_at[0] < d_x + d_w and w_at[0] + w_sz[0] > d_x and
                w_at[1] < d_y + d_h and w_at[1] + w_sz[1] > d_y):
                overlap = True
                break

        should_hide = overlap and not self.is_mouse_over_dock_area and not self._drag_in_progress
        is_revealed = self.dock_revealer.get_reveal_child()

        if should_hide and is_revealed:
            self.dock_revealer.set_reveal_child(False)
        elif not should_hide and not is_revealed:
            self.dock_revealer.set_reveal_child(True)

    def _build_app_identifiers_map(self) -> Dict:
        identifiers = {}
        for app in self._all_apps:
            keys = [app.name, app.display_name, app.window_class]
            if app.executable:
                keys.append(app.executable.split('/')[-1])
            if app.command_line:
                keys.append(app.command_line.split()[0].split('/')[-1])
            for k in keys:
                if k:
                    identifiers[str(k).lower()] = app
        return identifiers

    def _normalize_class(self, name: Optional[str]) -> str:
        if not name:
            return ""
        n = name.lower()
        suffixes = (".bin", ".exe", ".so", "-bin", "-gtk")
        for s in suffixes:
            if n.endswith(s):
                return n[:-len(s)]
        return n

    def _rebuild_dock_icons(self, clients):
        running_windows = {}
        for c in clients:
            raw_id = c.get("initialClass") or c.get("class") or c.get("title", "")
            if not raw_id:
                continue
            raw_id_lower = raw_id.lower()
            if " - " in raw_id_lower:
                raw_id_lower = raw_id_lower.split(" - ")[0].strip()
            running_windows.setdefault(raw_id_lower, []).append(c)
            norm = self._normalize_class(raw_id_lower)
            if norm != raw_id_lower:
                running_windows.setdefault(norm, []).extend(running_windows[raw_id_lower])

        new_children = []
        processed_classes = set()

        for class_name, instances in running_windows.items():
            if class_name in processed_classes:
                continue

            app = self.app_identifiers.get(class_name)
            if not app:
                app = self.app_identifiers.get(self._normalize_class(class_name))

            btn = self._create_button(app, instances, class_name)
            new_children.append(btn)
            processed_classes.add(class_name)

        for child in self.view.get_children():
            child.destroy()

        self.view.children = new_children

        if not self.integrated_mode:
            self.dock_geometry = None

        self._update_active_window_state()

    def _create_button(self, desktop_app, instances: List, window_class: str) -> Button:
        app_id = ""
        display_name = None

        if desktop_app:
            app_id = desktop_app.name or desktop_app.window_class or ""
            display_name = desktop_app.display_name or desktop_app.name

        if not app_id:
            app_id = window_class

        # Единый механизм получения иконки
        icon_pixbuf = self.icon_resolver.resolve_icon(app_id, self.icon_size, desktop_app)

        content = Box(
            name="dock-icon",
            orientation="v",
            h_align="center",
            children=[Image(pixbuf=icon_pixbuf)]
        )

        tooltip = display_name or app_id
        if not display_name and instances and instances[0].get("title"):
            tooltip = instances[0]["title"]

        btn = Button(
            child=content,
            on_clicked=lambda *_: self._handle_app_click(desktop_app, instances),
            tooltip_text=tooltip,
            name="dock-app-button",
        )

        btn.app_data = {
            "app": desktop_app,
            "instances": instances,
            "win_class": window_class
        }

        if instances:
            btn.add_style_class("instance")

        self._setup_btn_dnd(btn)

        return btn

    def _setup_btn_dnd(self, btn: Button):
        target = Gtk.TargetEntry.new("text/plain", Gtk.TargetFlags.SAME_APP, 0)
        btn.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, [target], Gdk.DragAction.MOVE)
        btn.drag_dest_set(Gtk.DestDefaults.ALL, [target], Gdk.DragAction.MOVE)
        btn.connect("drag-begin", self._on_drag_begin)
        btn.connect("drag-end", self._on_drag_end)
        btn.connect("drag-data-get", self._on_drag_data_get)
        btn.connect("drag-data-received", self._on_drag_data_received)
        btn.connect("enter-notify-event", self._on_child_enter)

    def _handle_app_click(self, desktop_app, instances: List):
        if not instances:
            if desktop_app:
                if not desktop_app.launch():
                    cmd = desktop_app.command_line or desktop_app.executable
                    if cmd:
                        exec_shell_command_async(f"nohup {cmd} &")
        else:
            try:
                focused = json.loads(self.conn.send_command("j/activewindow").reply.decode())
                focused_addr = focused.get("address", "")
            except Exception:
                focused_addr = ""

            idx = -1
            for i, inst in enumerate(instances):
                if inst["address"] == focused_addr:
                    idx = i
                    break

            next_inst = instances[(idx + 1) % len(instances)]
            exec_shell_command(f"hyprctl dispatch focuswindow address:{next_inst['address']}")

    def _update_active_window_state(self):
        try:
            aw = json.loads(self.conn.send_command("j/activewindow").reply.decode())
            cls = aw.get("initialClass") or aw.get("class")
            self._current_active_window_class = self._normalize_class(cls) if cls else None
        except Exception:
            self._current_active_window_class = None

        active = self._current_active_window_class
        for btn in self.view.get_children():
            btn_data = getattr(btn, "app_data", {})
            win_class = btn_data.get("win_class")
            is_active = win_class and active and self._normalize_class(win_class) == active

            if is_active:
                btn.add_style_class("active")
            else:
                btn.remove_style_class("active")

    def _on_hover_enter(self, *_):
        if self.integrated_mode:
            return
        self.is_mouse_over_dock_area = True
        self.dock_revealer.set_reveal_child(True)

    def _on_hover_leave(self, *_):
        if self.integrated_mode:
            return
        self.is_mouse_over_dock_area = False
        self._schedule_occlusion_check()

    def _on_dock_enter(self, w, e):
        if self.integrated_mode:
            return True
        self.is_mouse_over_dock_area = True
        self.dock_revealer.set_reveal_child(True)
        return True

    def _on_dock_leave(self, w, e):
        if self.integrated_mode:
            return True
        if e.detail == Gdk.NotifyType.INFERIOR:
            return False
        self.is_mouse_over_dock_area = False
        self._schedule_occlusion_check()
        return True

    def _on_child_enter(self, w, e):
        if not self.integrated_mode:
            self.is_mouse_over_dock_area = True
        return False

    def _on_drag_begin(self, widget, context):
        self._drag_in_progress = True
        Gtk.drag_set_icon_surface(context, create_surface_from_widget(widget))

    def _on_drag_end(self, widget, context):
        def finalize_drag():
            self._drag_in_progress = False
            if not self.integrated_mode:
                self._schedule_occlusion_check()
            return False
        GLib.idle_add(finalize_drag)

    def _on_drag_data_get(self, widget, context, data, info, time):
        target = widget
        while target and not isinstance(target, Button):
            target = target.get_parent()
        children = self.view.get_children()
        if target and target in children:
            idx = children.index(target)
            data.set_text(str(idx), -1)

    def _on_drag_data_received(self, widget, context, x, y, data, info, time):
        try:
            src_idx = int(data.get_text())
        except (ValueError, TypeError):
            return

        target = widget
        while target and not isinstance(target, Button):
            target = target.get_parent()

        children = self.view.get_children()
        if target not in children:
            return

        tgt_idx = children.index(target)
        if src_idx != tgt_idx:
            item = children.pop(src_idx)
            children.insert(tgt_idx, item)
            for child in children:
                self.view.remove(child)
                self.view.add(child)
            self.view.show_all()