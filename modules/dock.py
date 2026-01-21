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


class Dock(Window):
    __gtype_name__ = "Dock"
    
    _SUFFIXES = (".bin", ".exe", ".so", "-bin", "-gtk")
    _DND_TARGET = Gtk.TargetEntry.new("text/plain", Gtk.TargetFlags.SAME_APP, 0)

    def __init__(self, monitor_id=0, integrated_mode=False, **kwargs):
        self.monitor_id = monitor_id
        self.integrated_mode = integrated_mode
        self._icon_scale = 0.035
        self._drag_active = self._mouse_over = self._is_hidden = False
        self._pending_update = self._pending_occlusion = False
        self._dock_width = self._dock_height = 0
        self._mon_x = self._mon_y = self._mon_w = self._mon_h = 0
        self.icon_size = 24

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
        self.icon_resolver = IconResolver()
        self._app_map = self._build_app_map()

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
                Box(name="dock-corner-left", orientation=Gtk.Orientation.VERTICAL, h_align="start",
                    children=[Box(v_expand=True, v_align="fill"), MyCorner("bottom-right")]),
                self.dock_eb,
                Box(name="dock-corner-right", orientation=Gtk.Orientation.VERTICAL, h_align="end",
                    children=[Box(v_expand=True, v_align="fill"), MyCorner("bottom-left")]),
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
        self._setup_dnd()

    def _on_size_allocate(self, _, alloc):
        if not self._is_hidden and alloc.width > 10:
            self._dock_width, self._dock_height = alloc.width, alloc.height

    def _setup_dnd(self):
        t = self._DND_TARGET
        self.view.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, [t], Gdk.DragAction.MOVE)
        self.view.drag_dest_set(Gtk.DestDefaults.ALL, [t], Gdk.DragAction.MOVE)
        self.view.connect("drag-data-get", self._on_dnd_get)
        self.view.connect("drag-data-received", self._on_dnd_recv)
        self.view.connect("drag-begin", self._on_dnd_begin)
        self.view.connect("drag-end", self._on_dnd_end)

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

        # Вычисляем позицию дока
        dw, dh = self._dock_width, self._dock_height or 60
        dx = self._mon_x + (self._mon_w - dw) // 2
        dy = self._mon_y + self._mon_h - dh
        dx2, dy2 = dx + dw, dy + dh

        # Получаем активный воркспейс
        ws = self._parse("j/activeworkspace")
        ws_id = ws.get("id", 0) if ws else 0

        # Проверяем перекрытие
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

    def _build_app_map(self):
        m = {}
        for app in get_desktop_applications():
            name_l = app.name.lower() if app.name else None
            disp_l = app.display_name.lower() if app.display_name else None
            wc_l = app.window_class.lower() if app.window_class else None
            
            for k in (name_l, disp_l, wc_l):
                if k:
                    m[k] = app
            
            if app.executable:
                m[app.executable.rsplit("/", 1)[-1].lower()] = app
            if app.command_line:
                m[app.command_line.split()[0].rsplit("/", 1)[-1].lower()] = app
        return m

    def _norm(self, name):
        if not name:
            return ""
        n = name.lower()
        for s in self._SUFFIXES:
            if n.endswith(s):
                return n[:-len(s)]
        return n

    def _rebuild(self, clients):
        # Группируем окна по классу
        wins = {}
        for c in clients:
            raw = c.get("initialClass") or c.get("class") or c.get("title", "")
            if raw:
                key = raw.lower().split(" - ", 1)[0].strip()
                wins.setdefault(key, []).append(c)

        # Создаём кнопки
        seen = set()
        new_btns = []
        for cls, insts in wins.items():
            if cls in seen:
                continue
            seen.add(cls)
            norm = self._norm(cls)
            if norm != cls:
                seen.add(norm)
            app = self._app_map.get(cls) or self._app_map.get(norm)
            new_btns.append(self._make_btn(app, insts, cls))

        # Заменяем содержимое
        for c in self.view.get_children():
            c.destroy()
        for btn in new_btns:
            self.view.add(btn)

        self._sync_active()

    def _make_btn(self, app, insts, cls):
        app_id = (app.name or app.window_class or cls) if app else cls
        name = (app.display_name or app.name) if app else None
        
        btn = Button(
            child=Box(
                name="dock-icon",
                orientation="v",
                h_align="center",
                children=[Image(pixbuf=self.icon_resolver.resolve_icon(app_id, self.icon_size, app))],
            ),
            tooltip_text=name or (insts[0].get("title") if insts else None) or app_id,
            name="dock-app-button",
        )
        btn._cls = cls
        btn._app = app
        btn._insts = insts
        btn.connect("clicked", self._on_btn_click)
        
        if insts:
            btn.add_style_class("instance")

        # DnD
        t = self._DND_TARGET
        btn.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, [t], Gdk.DragAction.MOVE)
        btn.drag_dest_set(Gtk.DestDefaults.ALL, [t], Gdk.DragAction.MOVE)
        btn.connect("drag-begin", self._on_dnd_begin)
        btn.connect("drag-end", self._on_dnd_end)
        btn.connect("drag-data-get", self._on_dnd_get)
        btn.connect("drag-data-received", self._on_dnd_recv)
        btn.connect("enter-notify-event", lambda *_: setattr(self, "_mouse_over", True) or False)
        
        return btn

    def _on_btn_click(self, btn):
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
        active = self._norm(aw.get("initialClass") or aw.get("class", "")) if aw else ""
        
        for btn in self.view.get_children():
            cls = getattr(btn, "_cls", None)
            if cls and active and self._norm(cls) == active:
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

    def _on_dnd_begin(self, widget, ctx):
        self._drag_active = True
        child = widget.get_child()
        if child:
            for c in child.get_children():
                pb = getattr(c, "get_pixbuf", lambda: None)()
                if pb:
                    Gtk.drag_set_icon_pixbuf(ctx, pb, pb.get_width() >> 1, pb.get_height() >> 1)
                    return
        Gtk.drag_set_icon_default(ctx)

    def _on_dnd_end(self, *_):
        GLib.idle_add(self._finish_drag)

    def _finish_drag(self):
        self._drag_active = False
        self._schedule_occlusion()
        return False

    def _on_dnd_get(self, widget, _, data, *args):
        target = widget
        while target and not isinstance(target, Button):
            target = target.get_parent()
        children = self.view.get_children()
        if target in children:
            data.set_text(str(children.index(target)), -1)

    def _on_dnd_recv(self, widget, _, x, y, data, *args):
        try:
            src = int(data.get_text())
        except (ValueError, TypeError):
            return

        target = widget
        while target and not isinstance(target, Button):
            target = target.get_parent()

        children = self.view.get_children()
        if target not in children:
            return

        tgt = children.index(target)
        if src != tgt:
            item = children.pop(src)
            children.insert(tgt, item)
            for c in children:
                self.view.remove(c)
                self.view.add(c)
            self.view.show_all()