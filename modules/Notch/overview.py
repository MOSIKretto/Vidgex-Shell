import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk

from fabric.hyprland.service import Hyprland
from fabric.utils.helpers import get_desktop_applications
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.eventbox import EventBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label

import modules.icons as icons
from services.icon_resolver import IconResolver


screen = Gdk.Screen.get_default()
CURRENT_WIDTH = screen.get_width()
CURRENT_HEIGHT = screen.get_height()

BASE_SCALE = 0.1
TARGET = [Gtk.TargetEntry.new("text/plain", Gtk.TargetFlags.SAME_APP, 0)]

connection = Hyprland()
icon_resolver = IconResolver()


class HyprlandWindowButton(Button):
    __slots__ = ("address", "app_id", "title", "size", "desktop_app", "_icon_pixbuf")

    def __init__(self, overview, title, address, app_id, size, transform=0):
        self.address = address
        self.app_id = app_id
        self.title = title
        self.size = size
        self.desktop_app = overview.find_app(app_id)
        
        icon_size = int(min(size) * 0.5)
        self._icon_pixbuf = icon_resolver.resolve_icon(app_id, icon_size, self.desktop_app)

        super().__init__(
            name="overview-client-box",
            image=Image(pixbuf=self._icon_pixbuf),
            tooltip_text=title,
            size=size,
            on_clicked=self._focus,
            on_button_press_event=self._on_button_press,
            on_drag_data_get=self._on_drag_data_get,
            on_drag_begin=self._on_drag_begin,
        )

        self.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, TARGET, Gdk.DragAction.COPY)

    def _focus(self, *_):
        connection.send_command(f"/dispatch focuswindow address:{self.address}")

    def _on_button_press(self, _, event):
        if event.button == 3:
            connection.send_command(f"/dispatch closewindow address:{self.address}")

    def _on_drag_data_get(self, _, __, data, *___):
        data.set_text(self.address, len(self.address))

    def _on_drag_begin(self, _, context):
        if self._icon_pixbuf:
            Gtk.drag_set_icon_pixbuf(context, self._icon_pixbuf, 0, 0)
        else:
            Gtk.drag_set_icon_default(context)


class WorkspaceEventBox(EventBox):
    def __init__(self, workspace_id, fixed, width, height):
        child = fixed if fixed else Label(
            name="overview-add-label",
            markup=icons.circle_plus,
            h_expand=True,
            v_expand=True,
        )
        
        super().__init__(
            name="overview-workspace-bg",
            size=(int(width * BASE_SCALE), int(height * BASE_SCALE)),
            child=child,
            on_drag_data_received=lambda _w, _c, _x, _y, data, *_: (
                connection.send_command(
                    f"/dispatch movetoworkspacesilent {workspace_id},address:{data.get_data().decode()}"
                )
            ),
        )

        self.drag_dest_set(Gtk.DestDefaults.ALL, TARGET, Gdk.DragAction.COPY)

        if fixed:
            fixed.show_all()


class Overview(Box):
    def __init__(self, monitor_id=0, **kwargs):
        super().__init__(
            name="overview",
            orientation="v",
            spacing=8,
            **kwargs,
        )

        self.monitor_id = monitor_id
        self.workspace_start = 1
        self.workspace_end = 9

        self.clients = {}
        self.workspace_boxes = {}
        self._all_apps = []

        connection.connect("event::openwindow", self.update)
        connection.connect("event::closewindow", self.update)
        connection.connect("event::movewindow", self.update)

        self.update()

    def _load_apps(self):
        self._all_apps = get_desktop_applications()

    def find_app(self, app_id):
        if not app_id:
            return None

        app_id_lower = app_id.lower()
        
        for app in self._all_apps:
            if app.window_class and app.window_class.lower() == app_id_lower:
                return app
            if app.name and app.name.lower() == app_id_lower:
                return app
            if app.display_name and app.display_name.lower() == app_id_lower:
                return app
            if app.executable and app.executable.split("/")[-1].lower() == app_id_lower:
                return app
        return None

    def update(self, *_):
        self._load_apps()

        for btn in self.clients.values():
            btn.destroy()
        self.clients.clear()

        for box in self.workspace_boxes.values():
            box.destroy()
        self.workspace_boxes.clear()

        self.children = []

        try:
            # Пробуем разные методы получения данных
            monitors_data = connection.get_monitors()
            print(f"[DEBUG] monitors type: {type(monitors_data)}, data: {monitors_data}")
        except Exception as e:
            print(f"[ERROR] get_monitors failed: {e}")
            # Fallback на старый метод
            import json
            monitors_data = json.loads(connection.send_command("j/monitors").reply)

        try:
            clients_data = connection.get_clients()
            print(f"[DEBUG] clients type: {type(clients_data)}, count: {len(clients_data) if isinstance(clients_data, list) else 'N/A'}")
        except Exception as e:
            print(f"[ERROR] get_clients failed: {e}")
            import json
            clients_data = json.loads(connection.send_command("j/clients").reply)

        monitors = {
            m["id"]: (m["x"], m["y"], m["transform"])
            for m in monitors_data
        }

        clients = clients_data

        rows = [Box(spacing=8) for _ in range(3)]
        self.children = rows

        for client in clients:
            w_id = client["workspace"]["id"]
            if not (self.workspace_start <= w_id <= self.workspace_end):
                continue

            mx, my, transform = monitors[client["monitor"]]

            btn = HyprlandWindowButton(
                self,
                client["title"],
                client["address"],
                client["initialClass"],
                (
                    int(client["size"][0] * BASE_SCALE),
                    int(client["size"][1] * BASE_SCALE),
                ),
                transform,
            )

            self.clients[client["address"]] = btn

            fixed = self.workspace_boxes.setdefault(w_id, Gtk.Fixed())
            fixed.put(
                btn,
                int(abs(client["at"][0] - mx) * BASE_SCALE),
                int(abs(client["at"][1] - my) * BASE_SCALE),
            )

        for w_id in range(self.workspace_start, self.workspace_end + 1):
            idx = w_id - self.workspace_start
            row = rows[idx // 3]

            row.add(
                Box(
                    name="overview-workspace-box",
                    orientation="vertical",
                    children=[
                        Label(
                            name="overview-workspace-label",
                            label=f"Workspace {w_id}",
                        ),
                        WorkspaceEventBox(
                            w_id,
                            self.workspace_boxes.get(w_id),
                            CURRENT_WIDTH,
                            CURRENT_HEIGHT,
                        ),
                    ],
                )
            )