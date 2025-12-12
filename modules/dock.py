from fabric.hyprland.widgets import get_hyprland_connection
from fabric.utils import exec_shell_command, exec_shell_command_async, idle_add
from fabric.utils.helpers import get_desktop_applications
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.eventbox import EventBox
from fabric.widgets.image import Image
from fabric.widgets.revealer import Revealer

from gi.repository import Gdk, GLib, Gtk

import json
import cairo

from modules.corners import MyCorner
from utils.icon_resolver import IconResolver
from widgets.wayland import WaylandWindow as Window


def createSurfaceFromWidget(widget):
    alloc = widget.get_allocation()
    surface = cairo.ImageSurface(
        cairo.Format.ARGB32,
        alloc.width,
        alloc.height,
    )
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

        # Инициализация
        self._all_apps = get_desktop_applications()
        self.app_identifiers = self._build_app_identifiers_map()

        self.hide_id = None
        self._drag_in_progress = False
        self.is_mouse_over_dock_area = False
        self._current_active_window = None
        self._prevent_occlusion = False
        self._forced_occlusion = False

        # Флаг для проверки уничтожения
        self._destroyed = False

        # Для отслеживания таймеров и обработчиков событий
        self._occlusion_timer_id = None
        self._event_handlers = []

        # Для отслеживания геометрии дока
        self.dock_geometry = None
        self.monitor_info = None

        # Создание виджетов
        self._setup_ui()

        # Подписка на события
        self._setup_event_handlers()

    def _setup_ui(self):
        """Инициализация UI компонентов"""
        self.view = Box(name="viewport", spacing=4)
        self.wrapper = Box(name="dock", children=[self.view])
        self.wrapper.set_orientation(Gtk.Orientation.HORIZONTAL)
        self.view.set_orientation(Gtk.Orientation.HORIZONTAL)

        self.wrapper.add_style_class("pills")
        self._setup_dock_window()

    def _setup_dock_window(self):
        """Настройка док-окна (не integrated режим)"""
        self.dock_eventbox = EventBox()
        self.dock_eventbox.add(self.wrapper)
        self.dock_eventbox.connect("enter-notify-event", self._on_dock_enter)
        self.dock_eventbox.connect("leave-notify-event", self._on_dock_leave)

        # Углы
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

        """Настройка перетаскивания"""
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

        # Получаем информацию о мониторе после создания UI
        GLib.timeout_add(100, self._update_monitor_info)

    def _setup_event_handlers(self):
        """Настройка обработчиков событий"""
        if self.conn.ready:
            self.update_dock()
            self.update_active_window()
            if not self.integrated_mode:
                self._occlusion_timer_id = GLib.timeout_add(500, self._check_occlusion_wrapper)
        else:
            handler_id = self.conn.connect("event::ready", lambda *args: self._on_ready())
            self._event_handlers.append(handler_id)

        # Основные события - сохраняем handler_id для последующего disconnect
        handler_id = self.conn.connect("event::openwindow", self._on_window_change)
        self._event_handlers.append(handler_id)

        handler_id = self.conn.connect("event::closewindow", self._on_window_change)
        self._event_handlers.append(handler_id)

        handler_id = self.conn.connect("event::activewindow", self.update_active_window)
        self._event_handlers.append(handler_id)

        if not self.integrated_mode:
            handler_id = self.conn.connect("event::workspace", self._on_window_change)
            self._event_handlers.append(handler_id)

            handler_id = self.conn.connect("event::movewindow", self._on_window_change)
            self._event_handlers.append(handler_id)

            handler_id = self.conn.connect("event::resizewindow", self._on_window_change)
            self._event_handlers.append(handler_id)

    def _on_ready(self):
        """Обработчик события ready"""
        if self._destroyed:
            return
        self.update_dock()
        self.update_active_window()
        if not self.integrated_mode:
            self._occlusion_timer_id = GLib.timeout_add(250, self._check_occlusion_wrapper)

    def _check_occlusion_wrapper(self):
        """Обертка для check_occlusion_state с проверкой _destroyed"""
        if self._destroyed:
            return False
        return self.check_occlusion_state()

    def _update_monitor_info(self):
        """Получение информации о мониторе для корректного расчета координат"""
        try:
            monitors = json.loads(self.conn.send_command("j/monitors").reply.decode())
            for monitor in monitors:
                if monitor.get("id") == self.monitor_id:
                    self.monitor_info = monitor
                    break
        except:
            self.monitor_info = None
        return False

    def _update_dock_geometry(self):
        """Обновление геометрии дока в координатах экрана"""
        if not self.monitor_info:
            self._update_monitor_info()
            return
        
        # Получаем размеры дока
        alloc = self.get_allocation()
        dock_width = alloc.width
        dock_height = alloc.height
        
        # Получаем позицию монитора
        monitor_x = self.monitor_info.get("x", 0)
        monitor_y = self.monitor_info.get("y", 0)
        monitor_width = self.monitor_info.get("width", 1920)
        monitor_height = self.monitor_info.get("height", 1200)
        
        # Вычисляем позицию дока (по центру снизу)
        # Учитываем визуальное представление из CSS: док находится выше из-за margin и padding
        dock_x = monitor_x + (monitor_width - dock_width) // 2
        dock_y = monitor_y + monitor_height - dock_height - 50  # Увеличиваем отступ для учета визуальной высоты
        
        self.dock_geometry = {
            'x': dock_x,
            'y': dock_y, 
            'width': dock_width,
            'height': dock_height # Увеличиваем высоту для учета визуальной области
        }

    def _on_window_change(self, *args):
        self.update_dock()
        if not self.integrated_mode:
            self.check_occlusion_state()

    def _build_app_identifiers_map(self):
        """Создание карты идентификаторов приложений"""
        identifiers = {}
        for app in self._all_apps:
            if app.name: identifiers[app.name.lower()] = app
            if app.display_name: identifiers[app.display_name.lower()] = app
            if app.window_class: identifiers[app.window_class.lower()] = app
            if app.executable: identifiers[app.executable.split('/')[-1].lower()] = app
            if app.command_line: identifiers[app.command_line.split()[0].split('/')[-1].lower()] = app
        return identifiers

    def _normalize_window_class(self, class_name):
        """Нормализация имени класса окна"""
        if not class_name: 
            return ""
        
        normalized = class_name.lower()
        suffixes = (".bin", ".exe", ".so", "-bin", "-gtk")
        
        for suffix in suffixes:
            if normalized.endswith(suffix):
                normalized = normalized[:-len(suffix)]
                break
                
        return normalized

    def _classes_match(self, class1, class2):
        """Проверка совпадения классов окон"""
        if not class1 or not class2: 
            return False
        norm1 = self._normalize_window_class(class1)
        norm2 = self._normalize_window_class(class2)
        return norm1 == norm2

    def _is_window_overlapping_dock(self, window):
        """Проверяет, перекрывает ли окно область дока (исправленная версия с учетом CSS)"""
        if not self.dock_geometry:
            self._update_dock_geometry()
            if not self.dock_geometry:
                return False
        
        try:
            # Получаем геометрию окна из Hyprland
            window_at = window.get("at", [0, 0])
            window_size = window.get("size", [0, 0])
            
            window_x, window_y = window_at
            window_width, window_height = window_size
            
            # Получаем геометрию дока (уже с учетом визуального представления)
            dock_x = self.dock_geometry['x']
            dock_y = self.dock_geometry['y']
            dock_width = self.dock_geometry['width']
            dock_height = self.dock_geometry['height']
            
            # Расширяем область дока для учета визуальных эффектов
            # Док визуально находится выше из-за margin и padding в CSS
            visual_dock_y = dock_y - 30  # Сдвигаем область проверки выше
            visual_dock_height = dock_height + 30  # Увеличиваем высоту для учета визуальной области
            
            # Проверяем пересечение по оси X
            x_overlap = (window_x < dock_x + dock_width) and (window_x + window_width > dock_x)
            
            # Проверяем пересечение по оси Y с учетом визуального представления
            # Окно перекрывает док если оно находится в визуальной области дока
            y_overlap = (window_y < visual_dock_y + visual_dock_height) and (window_y + window_height > visual_dock_y)
            
            # Окно перекрывает док если есть пересечение по обеим осям
            # и окно находится на том же мониторе
            window_monitor = window.get("monitor", -1)
            if window_monitor != self.monitor_id:
                return False
                
            return x_overlap and y_overlap
            
        except Exception as e:
            print(f"Error checking window overlap: {e}")
            return False

    def check_occlusion_state(self):
        """Проверка состояния окклюзии дока на основе перекрытия окон"""
        if self.integrated_mode:
            return False

        # Обновляем геометрию дока
        self._update_dock_geometry()

        # Получаем все окна
        clients = self.get_clients()
        current_ws = self.get_workspace()
        
        # Фильтруем окна на текущем рабочем столе
        ws_clients = [w for w in clients if w.get("workspace", {}).get("id") == current_ws]
        
        # Проверяем, есть ли окна, перекрывающие док
        overlapping_window_exists = False
        for window in ws_clients:
            if self._is_window_overlapping_dock(window):
                overlapping_window_exists = True
                break
        
        # ОСНОВНАЯ ЛОГИКА:
        # Док скрывается только если есть окно, перекрывающее его, И курсор не над доком
        if overlapping_window_exists and not self.is_mouse_over_dock_area and not self._drag_in_progress:
            if self.dock_revealer.get_reveal_child():
                self.dock_revealer.set_reveal_child(False)
            if not self.always_show:
                self.dock_full.add_style_class("occluded")
        else:
            if not self.dock_revealer.get_reveal_child():
                self.dock_revealer.set_reveal_child(True)
            self.dock_full.remove_style_class("occluded")
        
        return True

    def update_app_map(self):
        """Обновление карты приложений"""
        self._all_apps = get_desktop_applications()
        self.app_identifiers = self._build_app_identifiers_map()

    def find_app(self, app_identifier):
        """Поиск приложения по идентификатору"""
        if not app_identifier: 
            return None
        if isinstance(app_identifier, dict):
            for key in ["window_class", "executable", "command_line", "name", "display_name"]:
                if key in app_identifier and app_identifier[key]:
                    app = self.find_app_by_key(app_identifier[key])
                    if app: 
                        return app
            return None
        return self.find_app_by_key(app_identifier)
    
    def find_app_by_key(self, key_value):
        """Поиск приложения по ключу"""
        if not key_value:  return None
        normalized_id = str(key_value).lower()
        if normalized_id in self.app_identifiers: return self.app_identifiers[normalized_id]
        for app in self._all_apps:
            if app.name and normalized_id in app.name.lower():  return app
            if app.display_name and normalized_id in app.display_name.lower(): 
                return app
            if app.window_class and normalized_id in app.window_class.lower(): 
                return app
            if app.executable and normalized_id in app.executable.lower(): 
                return app
            if app.command_line and normalized_id in app.command_line.lower(): 
                return app
        return None

    def create_button(self, app_identifier, instances, window_class=None):
        """Создание кнопки приложения для дока"""
        desktop_app = self.find_app(app_identifier)
        icon_img = None
        display_name = None
        
        if desktop_app:
            icon_img = desktop_app.get_icon_pixbuf(size=self.icon_size) 
            display_name = desktop_app.display_name or desktop_app.name
        
        id_value = app_identifier["name"] if isinstance(app_identifier, dict) else app_identifier
        
        if not icon_img:
            icon_img = self.icon_resolver.get_icon_pixbuf(id_value, self.icon_size) 
        
        if not icon_img:
            icon_img = self.icon_resolver.get_icon_pixbuf("application-x-executable-symbolic", self.icon_size) 
            if not icon_img:
                icon_img = self.icon_resolver.get_icon_pixbuf("image-missing", self.icon_size) 
                
        items = [Image(pixbuf=icon_img)]
        tooltip = display_name or (id_value if isinstance(id_value, str) else "Unknown")
        if not display_name and instances and instances[0].get("title"):
            tooltip = instances[0]["title"]

        button = Button(
            child=Box(name="dock-icon", orientation="v", h_align="center", children=items), 
            on_clicked=lambda *a: self.handle_app(app_identifier, instances, desktop_app),
            tooltip_text=tooltip, 
            name="dock-app-button",
        )
        button.app_identifier = app_identifier
        button.desktop_app = desktop_app
        button.instances = instances
        button.window_class = window_class
        
        if instances: 
            button.add_style_class("instance")

        # Настройка drag & drop для кнопки
        button.drag_source_set(
            Gdk.ModifierType.BUTTON1_MASK,
            [Gtk.TargetEntry.new("text/plain", Gtk.TargetFlags.SAME_APP, 0)],
            Gdk.DragAction.MOVE
        )
        button.connect("drag-begin", self.on_drag_begin)
        button.connect("drag-end", self.on_drag_end)
        button.drag_dest_set(
            Gtk.DestDefaults.ALL,
            [Gtk.TargetEntry.new("text/plain", Gtk.TargetFlags.SAME_APP, 0)],
            Gdk.DragAction.MOVE
        )
        button.connect("drag-data-get", self.on_drag_data_get)
        button.connect("drag-data-received", self.on_drag_data_received)
        button.connect("enter-notify-event", self._on_child_enter)
        
        return button

    def handle_app(self, app_identifier, instances, desktop_app=None):
        """Обработчик клика по приложению"""
        if not instances:
            if not desktop_app: 
                desktop_app = self.find_app(app_identifier)
            if desktop_app:
                launch_success = desktop_app.launch()
                if not launch_success:
                    if desktop_app.command_line: 
                        exec_shell_command_async(f"nohup {desktop_app.command_line} &")
                    elif desktop_app.executable: 
                        exec_shell_command_async(f"nohup {desktop_app.executable} &")
            else:
                cmd_to_run = None
                if isinstance(app_identifier, dict):
                    if "command_line" in app_identifier and app_identifier["command_line"]: 
                        cmd_to_run = app_identifier['command_line']
                    elif "executable" in app_identifier and app_identifier["executable"]: 
                        cmd_to_run = app_identifier['executable']
                    elif "name" in app_identifier and app_identifier["name"]: 
                        cmd_to_run = app_identifier['name']
                elif isinstance(app_identifier, str): 
                    cmd_to_run = app_identifier
                if cmd_to_run: 
                    exec_shell_command_async(f"nohup {cmd_to_run} &")
        else:
            focused = self.get_focused()
            idx = next((i for i, inst in enumerate(instances) if inst["address"] == focused), -1)
            next_inst = instances[(idx + 1) % len(instances)]
            exec_shell_command(f"hyprctl dispatch focuswindow address:{next_inst['address']}")

    def update_active_window(self, *args):
        """Обновление подсветки активного окна"""
        try:
            active_window = json.loads(self.conn.send_command("j/activewindow").reply.decode())
        except:
            active_window = None

        active_class = None
        if active_window and isinstance(active_window, dict):
            active_class = active_window.get("initialClass") or active_window.get("class")
        
        self._current_active_window = active_class

        # Обновляем стили кнопок
        for button in self.view.get_children():
            if hasattr(button, 'window_class') and button.window_class:
                if active_class and self._classes_match(active_class, button.window_class):
                    button.add_style_class("active")
                else:
                    button.remove_style_class("active")

    def update_dock(self, *args):
        """Обновление содержимого дока"""
        if self._destroyed:
            return

        self.update_app_map()

        clients = self.get_clients()

        running_windows = {}
        for c in clients:
            window_id = None
            if class_name := c.get("initialClass", "").lower():
                window_id = class_name
            elif class_name := c.get("class", "").lower():
                window_id = class_name
            elif title := c.get("title", "").lower():
                possible_name = title.split(" - ")[0].strip()
                if possible_name and len(possible_name) > 1:
                    window_id = possible_name
                else:
                    window_id = title
            if not window_id:
                window_id = "unknown-app"
            running_windows.setdefault(window_id, []).append(c)
            normalized_id = self._normalize_window_class(window_id)
            if normalized_id != window_id:
                running_windows.setdefault(normalized_id, []).extend(running_windows[window_id])

        open_buttons = []
        used_window_classes = set()

        for class_name, instances in running_windows.items():
            if class_name not in used_window_classes:
                app = self.app_identifiers.get(class_name)
                if not app:
                    norm_class = self._normalize_window_class(class_name)
                    app = self.app_identifiers.get(norm_class)
                if not app:
                    app = self.find_app_by_key(class_name)
                if not app and instances and instances[0].get("title"):
                    title = instances[0].get("title", "")
                    potential_name = title.split(" - ")[0].strip()
                    if len(potential_name) > 2:
                        app = self.find_app_by_key(potential_name)

                if app:
                    app_data_obj = {
                        "name": app.name,
                        "display_name": app.display_name,
                        "window_class": app.window_class,
                        "executable": app.executable,
                        "command_line": app.command_line
                    }
                    identifier = app_data_obj
                else:
                    identifier = class_name
                open_buttons.append(self.create_button(identifier, instances, class_name))
                used_window_classes.add(class_name)

        self.view.children = open_buttons
        if not self.integrated_mode:
            idle_add(self._update_size)

        self._drag_in_progress = False
        if not self.integrated_mode:
            self.check_occlusion_state()

        self.update_active_window()

    def _update_size(self):
        """Обновление размера дока"""
        if self._destroyed or self.integrated_mode:
            return False
        width, _ = self.view.get_preferred_width()
        self.set_size_request(width, -1)
        # Обновляем геометрию после изменения размера
        self._update_dock_geometry()
        return False

    def get_clients(self):
        """Получение списка клиентов Hyprland"""
        try: 
            return json.loads(self.conn.send_command("j/clients").reply.decode())
        except json.JSONDecodeError: 
            return []

    def get_focused(self):
        """Получение активного окна"""
        try: 
            return json.loads(self.conn.send_command("j/activewindow").reply.decode()).get("address", "")
        except json.JSONDecodeError: 
            return ""

    def get_workspace(self):
        """Получение текущего рабочего пространства"""
        try: 
            return json.loads(self.conn.send_command("j/activeworkspace").reply.decode()).get("id", 0)
        except json.JSONDecodeError: 
            return 0

    # Обработчики событий мыши
    def _on_hover_enter(self, *args):
        if self.integrated_mode: 
            return
            
        self.is_mouse_over_dock_area = True
        if self.hide_id:
            GLib.source_remove(self.hide_id)
            self.hide_id = None
        
        # При наведении всегда показываем док
        self.dock_revealer.set_reveal_child(True)
        if not self.always_show:
            self.dock_full.remove_style_class("occluded")

    def _on_hover_leave(self, *args):
        if self.integrated_mode: 
            return
            
        self.is_mouse_over_dock_area = False
        # При уходе курсора проверяем, нужно ли скрывать док
        self.check_occlusion_state()

    def _on_dock_enter(self, widget, event):
        if self.integrated_mode: 
            return True
            
        self.is_mouse_over_dock_area = True
        if self.hide_id:
            GLib.source_remove(self.hide_id)
            self.hide_id = None
        
        # При наведении на сам док всегда показываем его
        self.dock_revealer.set_reveal_child(True)
        if not self.always_show:
            self.dock_full.remove_style_class("occluded")
        return True

    def _on_dock_leave(self, widget, event):
        if self.integrated_mode: 
            return True
            
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False

        self.is_mouse_over_dock_area = False
        # При уходе курсора с дока проверяем, нужно ли скрывать
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

    # Методы drag & drop
    def on_drag_begin(self, widget, drag_context):
        """Начало перетаскивания"""
        self._drag_in_progress = True
        self.wrapper.add_style_class("drag-in-progress")
        Gtk.drag_set_icon_surface(drag_context, createSurfaceFromWidget(widget))

    def on_drag_end(self, widget, drag_context):
        """Конец перетаскивания"""
        if not self._drag_in_progress:
            return

        def process_drag_end():
            display = Gdk.Display.get_default()
            _, x, y, _ = display.get_pointer()
            
            alloc = self.view.get_allocation()
            widget_x = alloc.x
            widget_y = alloc.y
            widget_width = alloc.width
            widget_height = alloc.height
            
            if not (widget_x <= x <= widget_x + widget_width and widget_y <= y <= widget_y + widget_height):
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
        """Поиск цели перетаскивания"""
        children = self.view.get_children()
        while widget is not None and widget not in children:
            widget = widget.get_parent() if hasattr(widget, "get_parent") else None
        return widget

    def on_drag_data_get(self, widget, drag_context, data_obj, info, time):
        """Получение данных при перетаскивании"""
        target = self._find_drag_target(widget.get_parent() if isinstance(widget, Box) else widget)
        if target is not None:
            index = self.view.get_children().index(target)
            data_obj.set_text(str(index), -1)

    def on_drag_data_received(self, widget, drag_context, x, y, data_obj, info, time):
        """Получение данных при завершении перетаскивания"""
        target = self._find_drag_target(widget.get_parent() if isinstance(widget, Box) else widget)
        if target is None: 
            return
        try: 
            source_index = int(data_obj.get_text())
        except (TypeError, ValueError): 
            return

        children = self.view.get_children()
        try: 
            target_index = children.index(target)
        except ValueError: 
            return

        if source_index != target_index:
            child_item_to_move = children.pop(source_index) 
            children.insert(target_index, child_item_to_move)
            self.view.children = children

    # Методы управления окклюзией
    def force_occlusion(self):
        """Принудительное включение окклюзии"""
        if self.integrated_mode:
            return
        self._saved_always_show = self.always_show
        self.always_show = False
        self._forced_occlusion = True
        if not self.is_mouse_over_dock_area:
            self.dock_revealer.set_reveal_child(False)
    
    def restore_from_occlusion(self):
        """Восстановление из окклюзии"""
        if self.integrated_mode:
            return
        self._forced_occlusion = False
        if hasattr(self, '_saved_always_show'):
            self.always_show = self._saved_always_show
            delattr(self, '_saved_always_show')
        self.check_occlusion_state()

    @staticmethod
    def update_visibility(visible):
        """Статический метод обновления видимости всех доков"""
        for dock in Dock._instances:
            dock.set_visible(visible)
            if visible:
                GLib.idle_add(dock.check_occlusion_state)
            else:
                if hasattr(dock, 'dock_revealer') and dock.dock_revealer.get_reveal_child():
                    dock.dock_revealer.set_reveal_child(False)

    def destroy(self):
        """Очистка ресурсов при уничтожении дока"""
        if self._destroyed:
            return

        self._destroyed = True

        # Останавливаем таймер окклюзии
        if self._occlusion_timer_id:
            GLib.source_remove(self._occlusion_timer_id)
            self._occlusion_timer_id = None

        # Останавливаем таймер скрытия
        if self.hide_id:
            GLib.source_remove(self.hide_id)
            self.hide_id = None

        # Отключаем все event handlers
        for handler_id in self._event_handlers:
            try:
                self.conn.disconnect(handler_id)
            except Exception:
                pass
        self._event_handlers.clear()

        # Удаляем из списка инстансов
        if self in Dock._instances:
            Dock._instances.remove(self)

        # Очищаем кэш приложений
        self._all_apps = []
        self.app_identifiers.clear()

    def __del__(self):
        """Деструктор для обеспечения очистки"""
        try:
            self.destroy()
        except Exception:
            pass