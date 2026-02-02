import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from fabric.hyprland.widgets import get_hyprland_connection
from fabric.utils import exec_shell_command, exec_shell_command_async
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.eventbox import EventBox
from fabric.widgets.image import Image
from fabric.widgets.revealer import Revealer

from modules.corners import MyCorner
from services.icon_resolver import IconResolver
from services.wayland import WaylandWindow as Window


class Dock(Window):
    __gtype_name__ = "Dock"
    
    _MAX_DOTS = 5

    def __init__(self, monitor_id=0, integrated_mode=False, **kwargs):
        self.monitor_id = monitor_id
        self.integrated_mode = integrated_mode
        self._icon_scale = 0.035
        self._drag_active = False 
        self._mouse_over = False
        self._is_hidden = False
        self._pending_update = False
        self._pending_occlusion = False
        self._dock_width = self._dock_height = 0
        self._mon_x = self._mon_y = self._mon_w = self._mon_h = 0
        self.icon_size = 24
        
        self._custom_order = [] 

        super().__init__(
            name="dock-window",
            layer="top",
            anchor="bottom",
            margin="0px 0px 0px 0px",
            exclusivity="none",
            monitor=monitor_id,
            visible=False,
            **kwargs,
        )

        self.conn = get_hyprland_connection()
        self._app_resolver = IconResolver.get_default()

        self._init_ui()
        self._bind_events()

    def _parse(self, cmd):
        try:
            s = self.conn.send_command(cmd).reply.decode()
            return eval(s.replace("true", "True").replace("false", "False").replace("null", "None"))
        except Exception:
            return []

    def _init_ui(self):
        self.view = Box(name="viewport", spacing=0, orientation=Gtk.Orientation.HORIZONTAL)
        self.wrapper = Box(name="dock", children=[self.view], orientation=Gtk.Orientation.HORIZONTAL)

        if self.integrated_mode:
            self.add(self.wrapper)
            return

        self.dock_eb = EventBox()
        self.dock_eb.add(self.wrapper)
        self.dock_eb.connect("enter-notify-event", self._on_dock_enter)
        self.dock_eb.connect("leave-notify-event", self._on_dock_leave)

        dock_full = Box(
            name="dock-full",
            orientation=Gtk.Orientation.HORIZONTAL,
            h_expand=True,
            h_align="fill",
            children=[
                Box(
                    name="dock-corner-left", 
                    orientation=Gtk.Orientation.VERTICAL, 
                    h_align="start", 
                    children=[
                        Box(v_expand=True, v_align="fill"), 
                        MyCorner("bottom-right")
                    ]
                ),
                self.dock_eb,
                Box(
                    name="dock-corner-right", 
                    orientation=Gtk.Orientation.VERTICAL, 
                    h_align="end", 
                    children=[
                        Box(v_expand=True, v_align="fill"), 
                        MyCorner("bottom-left")
                    ]
                ),
            ],
        )

        self.revealer = Revealer(
            name="dock-revealer",
            transition_type="slide-up",
            child_revealed=True,
            child=dock_full,
        )

        activator = EventBox()
        activator.set_size_request(-1, 8)
        activator.connect("enter-notify-event", self._on_hover_enter)
        activator.connect("leave-notify-event", self._on_hover_leave)

        self.add(Box(
            orientation=Gtk.Orientation.VERTICAL,
            h_align="center",
            children=[activator, self.revealer],
        ))

        self.wrapper.connect("size-allocate", self._on_size_allocate)

    def _on_size_allocate(self, _, alloc):
        if not self._is_hidden and alloc.width > 10:
            self._dock_width, self._dock_height = alloc.width, alloc.height

    def _bind_events(self):
        c = self.conn
        c.connect("event::openwindow", self._schedule_update)
        c.connect("event::closewindow", self._schedule_update)
        c.connect("event::movewindow", self._schedule_occlusion)
        c.connect("event::workspace", self._schedule_occlusion)
        c.connect("event::activewindow", self._on_active_window)
        c.connect("event::changefloatingmode", self._schedule_occlusion)
        c.connect("event::fullscreen", self._schedule_occlusion)
        c.connect("event::monitoradded", self._update_monitor)
        c.connect("event::monitorremoved", self._update_monitor)

        if c.ready:
            self._on_ready()
        else:
            c.connect("event::ready", self._on_ready)

    def _on_ready(self, *_):
        self._update_monitor()
        self.show_all()
        GLib.timeout_add(150, self._do_full_update)

    def _schedule_update(self, *_):
        if not self._pending_update:
            self._pending_update = True
            GLib.idle_add(self._do_full_update)

    def _schedule_occlusion(self, *_):
        if not self.integrated_mode and not self._pending_occlusion:
            self._pending_occlusion = True
            GLib.idle_add(self._do_occlusion)

    def _on_active_window(self, *_):
        self._sync_active()
        self._schedule_occlusion()

    def _do_full_update(self):
        self._pending_update = False
        if self._drag_active:
            return False

        self._app_resolver.refresh()
        
        clients = self._parse("j/clients")
        self._rebuild(clients)
        if not self.integrated_mode:
            self._check_occlusion(clients)
        return False

    def _do_occlusion(self):
        self._pending_occlusion = False
        if not self.integrated_mode:
            self._check_occlusion(self._parse("j/clients"))
        return False

    def _update_monitor(self, *_):
        for m in self._parse("j/monitors"):
            if m.get("id") == self.monitor_id:
                self._mon_x, self._mon_y = m.get("x", 0), m.get("y", 0)
                self._mon_w, self._mon_h = m.get("width", 1920), m.get("height", 1080)
                new_size = int(self._mon_h * self._icon_scale)
                if abs(self.icon_size - new_size) > 2:
                    self.icon_size = new_size
                    self._schedule_update()
                return

    def _check_occlusion(self, clients):
        if not self._dock_width:
            return

        dw, dh = self._dock_width, self._dock_height or 60
        dx = self._mon_x + (self._mon_w - dw) // 2
        dy = self._mon_y + self._mon_h - dh
        dx2, dy2 = dx + dw, dy + dh

        ws = self._parse("j/activeworkspace")
        ws_id = ws.get("id", 0) if ws else 0

        overlap = False
        for w in clients:
            if w.get("hidden") or w.get("minimized"):
                continue
            
            w_ws = w.get("workspace", {})
            if (w_ws.get("id") if isinstance(w_ws, dict) else w_ws) != ws_id:
                continue
            
            if w.get("monitor") != self.monitor_id:
                continue

            pos, size = w.get("at"), w.get("size")
            if not pos or not size:
                continue

            wx, wy, ww, wh = pos[0], pos[1], size[0], size[1]
            if ww > 0 and wh > 0 and wx < dx2 and wx + ww > dx and wy < dy2 and wy + wh > dy:
                overlap = True
                break

        should_hide = overlap and not self._mouse_over and not self._drag_active
        if should_hide != self._is_hidden:
            self._is_hidden = should_hide
            self.revealer.set_reveal_child(not should_hide)

    def _rebuild(self, clients):
        """Перестроить dock с правильным сохранением оригинальных имён."""
        wins = {}
        
        for c in clients:
            # Сохраняем ОРИГИНАЛЬНЫЙ class (с регистром!)
            original_class = c.get("initialClass") or c.get("class") or ""
            title = c.get("title", "")
            
            if not original_class and not title:
                continue
            
            # Для группировки используем lowercase ключ
            raw = original_class or title
            key = raw.lower().split(" - ", 1)[0].strip()
            
            if key not in wins:
                wins[key] = {
                    "original": original_class or title,  # Сохраняем оригинал!
                    "instances": []
                }
            wins[key]["instances"].append(c)

        seen = set()
        candidates = []
        app_map = self._app_resolver.app_map
        
        for key, data in wins.items():
            if key in seen:
                continue
            seen.add(key)
            
            original = data["original"]  # Оригинальное имя с регистром
            insts = data["instances"]
            
            norm = self._app_resolver.norm_name(key)
            if norm != key:
                seen.add(norm)
            
            # Ищем app по разным вариантам
            app = (
                app_map.get(key) or 
                app_map.get(norm) or 
                app_map.get(original.lower()) or
                self._app_resolver.find_app(original)
            )
            
            unique_id = (app.name or app.window_class or key) if app else key
            
            candidates.append({
                "unique_id": unique_id,
                "app": app,
                "insts": insts,
                "key": key,
                "original": original,  # Передаём оригинал для иконки
            })

        existing_ids = set(c["unique_id"] for c in candidates)
        self._custom_order = [uid for uid in self._custom_order if uid in existing_ids]
        for cand in candidates:
            if cand["unique_id"] not in self._custom_order:
                self._custom_order.append(cand["unique_id"])

        candidates.sort(key=lambda x: self._custom_order.index(x["unique_id"]))

        for c in self.view.get_children():
            c.destroy()
            
        for item in candidates:
            btn = self._make_btn(
                item["app"], 
                item["insts"], 
                item["key"],
                item["original"],  # Передаём оригинальное имя
                item["unique_id"]
            )
            self.view.add(btn)

        self.view.show_all()
        self._sync_active()

    def _make_btn(self, app, insts, key, original_class, unique_id):
        """Создать кнопку с правильным поиском иконки."""
        name = (app.display_name or app.name) if app else None
        
        # Пробуем разные варианты для поиска иконки
        icon_pixbuf = None
        
        # 1. Сначала пробуем через app
        if app:
            icon_pixbuf = self._app_resolver.get_icon(original_class, self.icon_size, app)
        
        # 2. Если не нашли, пробуем оригинальный class
        if not icon_pixbuf:
            icon_pixbuf = self._app_resolver.get_icon(original_class, self.icon_size)
        
        # 3. Пробуем lowercase key
        if not icon_pixbuf and key != original_class.lower():
            icon_pixbuf = self._app_resolver.get_icon(key, self.icon_size)
        
        # 4. Fallback на default
        if not icon_pixbuf:
            try:
                icon_pixbuf = self._app_resolver.theme.load_icon(
                    "application-x-executable-symbolic",
                    self.icon_size,
                    Gtk.IconLookupFlags.FORCE_SIZE
                )
            except:
                pass
        
        icon_image = Image(
            pixbuf=icon_pixbuf,
            name="dock-icon-image",
        )
        
        icon_box = Box(
            name="dock-icon-box",
            orientation="v",
            h_align="center",
            v_align="end",
            children=[icon_image],
        )
        
        dots_box = Box(
            name="dock-dots",
            orientation="v",
            spacing=2,
            v_align="center",
        )
        
        num_instances = len(insts) if insts else 0
        num_dots = min(num_instances, self._MAX_DOTS)
        for _ in range(num_dots):
            dot = Box(name="dock-dot")
            dot.set_size_request(5, 5)
            dots_box.add(dot)
        
        content_box = Box(
            name="dock-icon",
            orientation="h",
            h_align="center",
            v_align="center",
            spacing=2,
        )
        
        icon_wrapper = Box(
            name="dock-icon-wrapper",
            orientation="v",
            h_align="center",
            v_align="end",
            spacing=4,
            children=[icon_box],
        )
        
        content_box.add(icon_wrapper)
        
        if num_dots > 0:
            dots_wrapper = Box(
                name="dock-dots-wrapper",
                orientation="v",
                v_align="center",
                children=[dots_box],
            )
            content_box.add(dots_wrapper)
        
        tooltip = name or (insts[0].get("title") if insts else None) or original_class
        
        btn = Button(
            child=content_box,
            tooltip_text=tooltip,
            name="dock-app-button",
        )
        
        btn._cls = key
        btn._original = original_class
        btn._app = app
        btn._insts = insts
        btn._icon_box = icon_box
        btn._unique_id = unique_id
        
        btn.connect("clicked", self._on_btn_click)
        btn.connect("enter-notify-event", self._on_btn_hover_enter)
        btn.connect("leave-notify-event", self._on_btn_hover_leave)
        
        if insts:
            btn.add_style_class("instance")

        self._setup_btn_dnd(btn)
        
        return btn

    def _setup_btn_dnd(self, btn):
        target_entry = Gtk.TargetEntry.new("text/plain", Gtk.TargetFlags.SAME_APP, 0)
        
        btn.drag_source_set(
            Gdk.ModifierType.BUTTON1_MASK,
            [target_entry],
            Gdk.DragAction.MOVE
        )
        
        btn.drag_dest_set(
            Gtk.DestDefaults.ALL,
            [target_entry],
            Gdk.DragAction.MOVE
        )
        
        btn.connect("drag-begin", self._on_drag_begin)
        btn.connect("drag-end", self._on_drag_end)
        btn.connect("drag-data-get", self._on_drag_data_get)
        btn.connect("drag-data-received", self._on_drag_data_received)
        btn.connect("drag-motion", self._on_drag_motion)

    def _on_drag_begin(self, btn, context):
        self._drag_active = True
        btn.add_style_class("dragging")
        
        try:
            if hasattr(btn, "_icon_box"):
                img = btn._icon_box.get_children()[0]
                pixbuf = img.get_pixbuf()
                if pixbuf:
                    Gtk.drag_set_icon_pixbuf(context, pixbuf, pixbuf.get_width() // 2, pixbuf.get_height() // 2)
        except:
            Gtk.drag_set_icon_default(context)

    def _on_drag_end(self, btn, context):
        self._drag_active = False
        btn.remove_style_class("dragging")
        GLib.idle_add(self._schedule_occlusion)

    def _on_drag_data_get(self, btn, context, selection_data, info, timestamp):
        uid = getattr(btn, "_unique_id", "")
        selection_data.set_text(uid, -1)

    def _on_drag_motion(self, widget, context, x, y, time):
        Gdk.drag_status(context, Gdk.DragAction.MOVE, time)
        return True

    def _on_drag_data_received(self, btn, context, x, y, selection_data, info, timestamp):
        source_id = selection_data.get_text()
        target_id = getattr(btn, "_unique_id", None)
        
        if not source_id or not target_id or source_id == target_id:
            context.finish(False, False, timestamp)
            return

        if source_id in self._custom_order and target_id in self._custom_order:
            old_idx = self._custom_order.index(source_id)
            new_idx = self._custom_order.index(target_id)
            
            self._custom_order.pop(old_idx)
            self._custom_order.insert(new_idx, source_id)
            
            src_btn = None
            children = self.view.get_children()
            for child in children:
                if getattr(child, "_unique_id", "") == source_id:
                    src_btn = child
                    break
            
            if src_btn:
                try:
                    tgt_idx = self.view.get_children().index(btn)
                    self.view.reorder_child(src_btn, tgt_idx)
                except ValueError:
                    pass
            
            context.finish(True, False, timestamp)
        else:
            context.finish(False, False, timestamp)

    def _on_btn_hover_enter(self, btn, event):
        self._mouse_over = True
        if self._drag_active:
            return False
        
        btn.add_style_class("hovered")
        if getattr(btn, "_icon_box", None):
            btn._icon_box.add_style_class("lifted")
        return False

    def _on_btn_hover_leave(self, btn, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        
        btn.remove_style_class("hovered")
        if getattr(btn, "_icon_box", None):
            btn._icon_box.remove_style_class("lifted")
        return False

    def _on_btn_click(self, btn):
        if self._drag_active:
            return
        
        app, insts = btn._app, btn._insts
        if not insts:
            if app and not app.launch():
                cmd = app.command_line or app.executable
                if cmd:
                    exec_shell_command_async(f"nohup {cmd} &")
            return

        aw = self._parse("j/activewindow")
        focused = aw.get("address", "") if aw else ""
        
        idx = next((i for i, x in enumerate(insts) if x["address"] == focused), -1)
        addr = insts[(idx + 1) % len(insts)]["address"]
        exec_shell_command(f"hyprctl dispatch focuswindow address:{addr}")

    def _sync_active(self):
        aw = self._parse("j/activewindow")
        active = self._app_resolver.norm_name(aw.get("initialClass") or aw.get("class", "")) if aw else ""
        
        for btn in self.view.get_children():
            cls = getattr(btn, "_cls", None)
            if cls and active and self._app_resolver.norm_name(cls) == active:
                btn.add_style_class("active")
            else:
                btn.remove_style_class("active")

    def _on_hover_enter(self, *_):
        if not self.integrated_mode:
            self._mouse_over = True
            self._is_hidden = False
            self.revealer.set_reveal_child(True)

    def _on_hover_leave(self, *_):
        if not self.integrated_mode:
            self._mouse_over = False
            self._schedule_occlusion()

    def _on_dock_enter(self, *_):
        if not self.integrated_mode:
            self._mouse_over = True
            self._is_hidden = False
            self.revealer.set_reveal_child(True)
        return True

    def _on_dock_leave(self, _, e):
        if self.integrated_mode or e.detail == Gdk.NotifyType.INFERIOR:
            return e.detail != Gdk.NotifyType.INFERIOR
        self._mouse_over = False
        self._schedule_occlusion()
        return True