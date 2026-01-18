from fabric.hyprland.widgets import get_hyprland_connection
from fabric.widgets.box import Box
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer
from fabric.widgets.stack import Stack
from gi.repository import Gdk, GLib, Gtk

from modules.Notch.dashboard import Dashboard
from modules.Notch.cliphist import ClipHistory
from modules.Notch.launcher import AppLauncher
from modules.Notch.overview import Overview
from modules.Notch.power import PowerMenu
from modules.Notch.tools import Toolbox

from services.icon_resolver import IconResolver
from widgets.corners import MyCorner
from widgets.wayland import WaylandWindow as Window


class Notch(Window):
    """Оптимизированный виджет Notch с ленивой загрузкой."""

    def __init__(self, **kwargs):
        self._bar_ref = None

        super().__init__(
            anchor="top",
            margin="-40px 0px 0px 0px",
            monitor=0,
        )
        
        if 'bar' in kwargs:
            self._bar_ref = kwargs['bar']
        
        # Кэш для ленивых виджетов
        self._widgets_cache = {}
        
        # Кэш соединения
        self._conn = get_hyprland_connection()
        
        self.icon_resolver = IconResolver()
        
        # Состояние
        self._active_signals = []
        self._current_widget = None
        self._last_workspace_id = None
        self._last_window_title = ""
        self._last_window_class = ""
        
        self._setup_ui()
        self._connect_signals()
        
        GLib.idle_add(self._finalize_setup)

    # --- Lazy Loading Properties ---

    @property
    def dashboard(self):
        if 'dashboard' not in self._widgets_cache:
            widget = Dashboard(notch=self)
            widget.set_size_request(1093, 472)
            self.stack.add_named(widget, "dashboard")
            self._widgets_cache['dashboard'] = widget
        return self._widgets_cache['dashboard']

    @property
    def launcher(self):
        if 'launcher' not in self._widgets_cache:
            widget = AppLauncher(notch=self)
            widget.set_size_request(480, 244)
            self.stack.add_named(widget, "launcher")
            self._widgets_cache['launcher'] = widget
        return self._widgets_cache['launcher']

    @property
    def overview(self):
        if 'overview' not in self._widgets_cache:
            widget = Overview()
            self.stack.add_named(widget, "overview")
            self._widgets_cache['overview'] = widget
        return self._widgets_cache['overview']

    @property
    def power(self):
        if 'power' not in self._widgets_cache:
            widget = PowerMenu(notch=self)
            self.stack.add_named(widget, "power")
            self._widgets_cache['power'] = widget
        return self._widgets_cache['power']

    @property
    def cliphist(self):
        if 'cliphist' not in self._widgets_cache:
            widget = ClipHistory(notch=self)
            widget.set_size_request(480, 244)
            self.stack.add_named(widget, "cliphist")
            self._widgets_cache['cliphist'] = widget
        return self._widgets_cache['cliphist']

    @property
    def tools(self):
        if 'tools' not in self._widgets_cache:
            widget = Toolbox(notch=self)
            self.stack.add_named(widget, "tools")
            self._widgets_cache['tools'] = widget
        return self._widgets_cache['tools']

    def _setup_ui(self):
        """Настройка пользовательского интерфейса."""
        # Активное окно
        self.window_icon = Image(
            name="notch-window-icon", 
            icon_name="application-x-executable", 
            icon_size=20
        )
        self.workspace_label = Label(
            name="workspace-label",
            label="Workspace 1"
        )
        
        self.active_window_container = Box(
            name="active-window-container",
            spacing=8,
            children=[self.window_icon, self.workspace_label],
        )
        
        self.active_window_box = Box(
            name="active-window-box",
            h_align="center",
            children=[self.active_window_container],
        )
        
        # Компактный вид
        self.compact_stack = Stack(
            name="notch-compact-stack",
            transition_type="slide-up-down", 
        )
        self.compact_stack.add_named(self.active_window_box, "window")
        
        self.compact = Gtk.EventBox(name="notch-compact")
        self.compact.set_visible(True)
        self.compact.add(self.compact_stack)
        self.compact.set_size_request(260, 40)
        
        # Основной стек
        self.stack = Stack(
            name="notch-content",
            transition_type="crossfade", 
            transition_duration=200,
        )
        
        self.stack.add_named(self.compact, "compact")
        
        for style in ["panel", "bottom", "Top"]:
            self.stack.add_style_class(style)
        
        if hasattr(self.stack, 'set_interpolate_size'):
            self.stack.set_interpolate_size(True)
        if hasattr(self.stack, 'set_homogeneous'):
            self.stack.set_homogeneous(False)
        
        # Угловые элементы
        self.corner_left = Box(
            name="notch-corner-left",
            orientation="v",
            h_align="start",
            children=[MyCorner("top-right")]
        )
        
        self.corner_right = Box(
            name="notch-corner-right", 
            orientation="v",
            h_align="end",
            children=[MyCorner("top-left")]
        )
        
        # Основной контейнер
        self.notch_box = CenterBox(
            name="notch-box",
            start_children=self.corner_left,
            center_children=self.stack,
            end_children=self.corner_right,
        )
        self.notch_box.add_style_class("notch")
        
        # Revealer
        self.notch_revealer = Revealer(
            name="notch-revealer",
            child_revealed=True,
            child=self.notch_box,
        )
        self.notch_revealer.set_size_request(-1, 1)
        
        # Обертки
        self.notch_complete = Box(
            name="notch-complete",
            children=[self.notch_revealer]
        )
        
        self.notch_wrap = Box(name="notch-wrap", children=[self.notch_complete])
        
        # Hover area
        self.hover_eventbox = Gtk.EventBox(name="notch-hover-eventbox")
        self.hover_eventbox.add(self.notch_wrap)
        self.hover_eventbox.set_visible(True)
        self.hover_eventbox.set_size_request(260, 4)
        
        self.add(self.hover_eventbox)

    def _connect_signals(self):
        """Подключение сигналов."""
        # Мышь
        self.compact.connect("button-press-event", self._on_window_click)
        self.compact.connect("enter-notify-event", self._on_button_enter)
        self.compact.connect("leave-notify-event", self._on_button_leave)
        self.compact.connect("scroll-event", self._on_compact_scroll)
        self.active_window_box.connect("button-press-event", self._on_window_click)
        
        self.hover_eventbox.add_events(
            Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.hover_eventbox.connect("enter-notify-event", self._on_notch_hover)
        self.hover_eventbox.connect("leave-notify-event", self._on_notch_hover)
        
        # Клавиатура
        self.connect("key-press-event", self._on_key_press)
        self.add_keybinding("Escape", lambda *args: self.close_notch())
        
        # Hyprland
        if self._conn:
            for event in ("event::activewindow", "event::workspace"):
                handler_id = self._conn.connect(event, lambda *_: self._update_window_display())
                self._active_signals.append((self._conn, handler_id))

    def _finalize_setup(self):
        """Завершение настройки."""
        self.show_all()
        self._update_window_display()
        return False

    def open_notch(self, widget_name: str):
        """Открывает notch с указанным виджетом."""
        self._open_notch_internal(widget_name)

    def toggle_notch(self, widget_name: str):
        """Переключает состояние notch."""
        if self._current_widget == widget_name:
            self.close_notch()
        else:
            self._open_notch_internal(widget_name)

    def _open_notch_internal(self, widget_name: str):
        """Внутренняя реализация открытия notch."""
        self.notch_revealer.set_reveal_child(True)
        self.notch_box.add_style_class("open")
        self.stack.add_style_class("open")
        self.set_keyboard_mode("exclusive")
        
        # Обработчики виджетов
        if widget_name == "network_applet":
            self._show_dashboard_widget("network_applet")
        elif widget_name == "bluetooth":
            self._show_dashboard_widget("bluetooth")
        elif widget_name == "dashboard":
            self._show_dashboard_widget("notification_history")
        elif widget_name == "wallpapers":
            self._show_dashboard_board("wallpapers")
        elif widget_name == "mixer":
            self._show_dashboard_board("mixer")
        elif widget_name == "overview":
            self.stack.set_visible_child(self.overview)
        elif widget_name == "power":
            self.stack.set_visible_child(self.power)
        elif widget_name == "tools":
            self.stack.set_visible_child(self.tools)
        elif widget_name == "cliphist":
            self._show_cliphist()
        elif widget_name == "launcher":
            self._show_launcher()
        
        self._current_widget = widget_name
        
        # Скрываем панель
        self._set_bar_revealers(False)

    def _show_dashboard_widget(self, widget_name: str):
        """Показывает виджет из дашборда."""
        db = self.dashboard
        self.stack.set_visible_child(db)
        db.go_to_section("widgets")
        
        widgets = getattr(db, 'widgets', None)
        if not widgets:
            return
            
        applet_stack = getattr(widgets, 'applet_stack', None)
        if not applet_stack:
            return

        if widget_name == "network_applet":
            target = getattr(widgets, 'network_connections', None)
        elif widget_name == "bluetooth":
            target = getattr(widgets, 'bluetooth', None)
        elif widget_name == "notification_history":
            target = getattr(widgets, 'notification_history', None)
        else:
            target = None
            
        if target:
            applet_stack.set_visible_child(target)

    def _show_dashboard_board(self, board: str):
        """Показывает раздел дашборда."""
        self.stack.set_visible_child(self.dashboard)
        self.dashboard.go_to_section(board)

    def _show_cliphist(self):
        """Показывает историю буфера обмена."""
        ch = self.cliphist
        self.stack.set_visible_child(ch)
        if hasattr(ch, 'open'):
            GLib.idle_add(ch.open)

    def _show_launcher(self):
        """Показывает лаунчер приложений."""
        ln = self.launcher
        self.stack.set_visible_child(ln)
        ln.open_launcher()
        
        entry = getattr(ln, 'search_entry', None)
        if entry:
            entry.set_text("")
            entry.grab_focus()

    def _set_bar_revealers(self, visible: bool):
        """Устанавливает видимость панели."""
        if not self._bar_ref:
            return
        for attr in ('revealer_right', 'revealer_left'):
            revealer = getattr(self._bar_ref, attr, None)
            if revealer:
                revealer.set_reveal_child(visible)

    def close_notch(self):
        """Закрывает notch."""
        self.set_keyboard_mode("none")
        self.notch_box.remove_style_class("open")
        self.stack.remove_style_class("open")
        
        # Восстанавливаем панель
        self._set_bar_revealers(True)
        
        # Восстанавливаем дашборд если был создан
        db = self._widgets_cache.get('dashboard')
        if db and hasattr(db, 'widgets'):
            widgets = db.widgets
            nh = getattr(widgets, 'notification_history', None)
            stack = getattr(widgets, 'applet_stack', None)
            if nh and stack:
                stack.set_visible_child(nh)
        
        self._current_widget = None
        self.stack.set_visible_child(self.compact)
        self._update_window_display()

    # --- Event Handlers ---

    def _on_window_click(self, widget, event):
        self.toggle_notch("dashboard")
        return True

    def _on_button_enter(self, widget, event):
        win = widget.get_window()
        if win:
            display = Gdk.Display.get_default()
            if display:
                cursor = Gdk.Cursor.new_for_display(display, Gdk.CursorType.HAND2)
                win.set_cursor(cursor)
        return True

    def _on_button_leave(self, widget, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        win = widget.get_window()
        if win:
            win.set_cursor(None)
        return True

    def _on_notch_hover(self, widget, event):
        return False

    def _on_compact_scroll(self, widget, event):
        children = self.compact_stack.get_children()
        if not children:
            return False
            
        current_child = self.compact_stack.get_visible_child()
        try:
            current_idx = children.index(current_child)
        except ValueError:
            current_idx = 0
        
        if event.direction == Gdk.ScrollDirection.UP:
            new_idx = (current_idx - 1) % len(children)
        elif event.direction == Gdk.ScrollDirection.DOWN:
            new_idx = (current_idx + 1) % len(children)
        else:
            return False
            
        self.compact_stack.set_visible_child(children[new_idx])
        return True

    def _on_key_press(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.close_notch()
            return True
        return False

    # --- Window Display ---

    def _update_window_display(self):
        """Обновляет отображение информации об окне."""
        if self._current_widget is not None:
            return

        workspace_id, window_title, window_class = self._get_current_window_and_workspace()
        
        # Проверяем изменения
        if (workspace_id == self._last_workspace_id and
            window_title == self._last_window_title and
            window_class == self._last_window_class):
            return
        
        self._last_workspace_id = workspace_id
        self._last_window_title = window_title
        self._last_window_class = window_class
        
        self.workspace_label.set_label(f"Workspace {workspace_id}")
        
        if window_class and window_class.strip():
            self._update_icon(window_class)
            if not self.window_icon.get_visible():
                self.window_icon.show()
                self.active_window_container.set_spacing(8)
        else:
            if self.window_icon.get_visible():
                self.window_icon.hide()
                self.active_window_container.set_spacing(0)

    def _get_current_window_and_workspace(self):
        """Получает информацию о текущем окне и рабочем пространстве."""
        workspace_id = 1
        window_title = ""
        window_class = ""

        if not self._conn:
            return workspace_id, window_title, window_class

        try:
            reply = self._conn.send_command("j/activewindow").reply
            if reply:
                data = reply.decode('utf-8', errors='ignore')
                
                # class
                idx = data.find('"class":')
                if idx != -1:
                    start = data.find('"', idx + 8) + 1
                    end = data.find('"', start)
                    if start > 0 and end > start:
                        window_class = data[start:end]

                # title
                idx = data.find('"title":')
                if idx != -1:
                    start = data.find('"', idx + 8) + 1
                    end = data.find('"', start)
                    if start > 0 and end > start:
                        window_title = data[start:end]
                
                # initialClass fallback
                if not window_class:
                    idx = data.find('"initialClass":')
                    if idx != -1:
                        start = data.find('"', idx + 15) + 1
                        end = data.find('"', start)
                        if start > 0 and end > start:
                            window_class = data[start:end]
                
                # workspace id
                idx = data.find('"workspace":')
                if idx != -1:
                    id_idx = data.find('"id":', idx)
                    if id_idx != -1:
                        start = id_idx + 5
                        end = data.find(',', start)
                        if end == -1:
                            end = data.find('}', start)
                        if end != -1:
                            try:
                                workspace_id = int(data[start:end].strip())
                                return workspace_id, window_title, window_class
                            except ValueError:
                                pass

            # Fallback для workspace
            ws_reply = self._conn.send_command("j/activeworkspace").reply
            if ws_reply:
                ws_data = ws_reply.decode('utf-8', errors='ignore')
                idx = ws_data.find('"id":')
                if idx != -1:
                    start = idx + 5
                    end = ws_data.find(',', start)
                    if end == -1:
                        end = ws_data.find('}', start)
                    if end != -1:
                        try:
                            workspace_id = int(ws_data[start:end].strip())
                        except ValueError:
                            pass

        except Exception:
            pass
        
        return workspace_id, window_title, window_class

    def _update_icon(self, app_id: str):
        """Обновляет иконку приложения."""
        icon = self._get_icon_pixbuf(app_id)
        if icon:
            self.window_icon.set_from_pixbuf(icon)
        else:
            self.window_icon.set_from_icon_name("application-x-executable-symbolic", 20)

    def _get_icon_pixbuf(self, app_id: str):
        """Получает pixbuf иконки."""
        if not app_id or not self.icon_resolver:
            return None
        
        icon = self.icon_resolver.get_icon_pixbuf(app_id, 20)
        
        if not icon and "-" in app_id:
            icon = self.icon_resolver.get_icon_pixbuf(app_id.split("-")[0], 20)
        
        return icon

    def toggle_hidden(self):
        """Переключает видимость notch."""
        visible = self.get_visible()
        self.set_visible(not visible)
        
        if not visible:
            self._update_window_display()