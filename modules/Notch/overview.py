from utils.hyprland_service import Hyprland
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.eventbox import EventBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk, GLib, GdkPixbuf

import json
import modules.icons as icons
from utils.icon_resolver import IconResolver


# Синлгтоны для экономии ресурсов
connection = Hyprland()
icon_res = IconResolver()
TARGET = [Gtk.TargetEntry.new("text/plain", Gtk.TargetFlags.SAME_APP, 0)]

class HyprlandWindowButton(Button):
    __slots__ = ("addr", "w_id", "pos") # Экономия ОЗУ: запрет на создание __dict__

    def __init__(self, win, scale, m_x, m_y):
        self.addr = win["address"]
        self.w_id = win["workspace"]["id"]
        
        # Расчет геометрии без лишних объектов
        w, h = win["size"]
        if win.get("transform", 0) in (1, 3): w, h = h, w
        
        sw, sh = int(w * scale), int(h * scale)
        isize = int(min(sw, sh) * 0.5)

        super().__init__(
            name="overview-client-box",
            image=Image(pixbuf=self._get_px(win["initialClass"], isize)),
            tooltip_text=win["title"][:100],
            size=(sw, sh),
        )
        
        self.pos = (int(abs(win["at"][0] - m_x) * scale), int(abs(win["at"][1] - m_y) * scale))
        
        # Drag and Drop: легкая реализация
        self.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, TARGET, Gdk.DragAction.COPY)
        self.connect("drag-data-get", self._on_drag_data)
        self.connect("clicked", self._on_click)

    def _get_px(self, cls, size):
        # Быстрое получение иконки без перебора всех приложений в системе
        px = icon_res.get_icon_pixbuf(cls, size) or icon_res.get_icon_pixbuf("image-missing", size)
        return px.scale_simple(size, size, GdkPixbuf.InterpType.NEAREST) if px else None

    def _on_click(self, _):
        connection.send_command(f"/dispatch workspace {self.w_id}")
        connection.send_command(f"/dispatch focuswindow address:{self.addr}")

    def _on_drag_data(self, _widget, _context, data, _info, _time):
        data.set_text(self.addr, -1)

class WorkspaceEventBox(EventBox):
    __slots__ = ("ws_id",)
    def __init__(self, ws_id, content, ws_empty):
        super().__init__(name="overview-workspace-bg")
        self.ws_id = ws_id
        self.add(content)
        
        self.drag_dest_set(Gtk.DestDefaults.ALL, TARGET, Gdk.DragAction.COPY)
        self.connect("drag-data-received", self._on_drop)
        if not ws_empty:
            self.connect("button-press-event", self._on_click)

    def _on_click(self, _, event):
        if event.button == 1: # Только ЛКМ для перехода
            connection.send_command(f"/dispatch workspace {self.ws_id}")

    def _on_drop(self, _w, _ctx, _x, _y, data, _info, _time):
        addr = data.get_data().decode()
        connection.send_command(f"/dispatch movetoworkspacesilent {self.ws_id},address:{addr}")

class Overview(Box):
    __slots__ = ("mon_id", "upd_id")
    def __init__(self, monitor_id=0, **kwargs):
        super().__init__(name="overview", orientation="v", spacing=8, **kwargs)
        self.mon_id = monitor_id
        self.upd_id = 0
        
        # Подписка на ключевые события для обновления
        for ev in ("openwindow", "closewindow", "movewindow", "windowtitle"):
            connection.connect(ev, self.schedule_update)
        self._update_ui()

    def schedule_update(self, *args):
        if self.upd_id: GLib.source_remove(self.upd_id)
        # Debounce обновления для защиты CPU от спама событий
        self.upd_id = GLib.timeout_add(150, self._perform_update)

    def _perform_update(self):
        self._update_ui()
        self.upd_id = 0
        return False

    def _update_ui(self):
        # Эффективная очистка
        for child in self.get_children(): self.remove(child)
        
        # Прямой парсинг данных без создания промежуточных классов-оберток
        m_data = json.loads(connection.send_command("j/monitors").reply.decode())
        cur_m = next((m for m in m_data if m["id"] == self.mon_id), m_data[0])
        scale = 0.1 * cur_m.get("scale", 1.0)
        
        wins = json.loads(connection.send_command("j/clients").reply.decode())
        ws_map = {}
        for w in wins:
            wid = w["workspace"]["id"]
            if 1 <= wid <= 9: ws_map.setdefault(wid, []).append(w)

        # Создание сетки
        for r in range(3):
            hbox = Box(spacing=8, orientation="h")
            self.add(hbox)
            for c in range(3):
                ws_id = r * 3 + c + 1
                hbox.add(self._build_ws(ws_id, ws_map.get(ws_id, []), cur_m, scale))
        self.show_all()

    def _build_ws(self, ws_id, ws_wins, cur_m, scale):
        fixed = Gtk.Fixed()
        for w in ws_wins:
            btn = HyprlandWindowButton(w, scale, cur_m["x"], cur_m["y"])
            fixed.put(btn, *btn.pos)

        inner = Box(orientation="v", spacing=4)
        inner.set_size_request(int(cur_m["width"] * scale), int(cur_m["height"] * scale))
        
        if not ws_wins:
            lbl_empty = Label(markup=icons.circle_plus, name="overview-add-label")
            lbl_empty.set_opacity(0.4)
            inner.pack_start(lbl_empty, True, True, 0)
        else:
            inner.add(fixed)

        ws_box = Box(name="overview-workspace-box", orientation="v", spacing=4)
        ws_box.add(Label(label=f"Workspace {ws_id}", name="overview-workspace-label"))
        ws_box.add(WorkspaceEventBox(ws_id, inner, not ws_wins))
        return ws_box

    def destroy(self):
        if self.upd_id: GLib.source_remove(self.upd_id)
        super().destroy()