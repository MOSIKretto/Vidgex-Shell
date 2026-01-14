from fabric.hyprland.widgets import get_hyprland_connection
from fabric.widgets.box import Box
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer
from fabric.widgets.stack import Stack
from gi.repository import Gdk, GLib, Gtk
import json
import subprocess
import weakref
import functools
from typing import Optional, List, Set, Tuple
import gc


from modules.Notch.dashboard import Dashboard
from modules.Notch.cliphist import ClipHistory
from modules.Notch.launcher import AppLauncher
from modules.Notch.overview import Overview
from modules.Notch.power import PowerMenu
from modules.Notch.tools import Toolbox

from utils.icon_resolver import IconResolver

from widgets.corners import MyCorner
from widgets.wayland import WaylandWindow as Window


def debounce(delay: int):
    """Декоратор для устранения дребезга вызовов функций."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            timer_attr = f'_debounce_{func.__name__}'
            
            if hasattr(self, timer_attr):
                timer_id = getattr(self, timer_attr)
                if timer_id and timer_id in self._timers:
                    GLib.source_remove(timer_id)
                    self._timers.discard(timer_id)
            
            def call_func():
                try:
                    timer_id = getattr(self, timer_attr, None)
                    if timer_id in self._timers:
                        self._timers.discard(timer_id)
                    return func(self, *args, **kwargs)
                except Exception:
                    return False
                finally:
                    if hasattr(self, timer_attr):
                        delattr(self, timer_attr)
            
            timer_id = GLib.timeout_add(delay, call_func)
            setattr(self, timer_attr, timer_id)
            self._timers.add(timer_id)
            
            return False
        return wrapper
    return decorator


class Notch(Window):
    """Виджет уведомлений и быстрого доступа."""
    
    # Ленивая инициализация компонентов
    _LAZY_WIDGETS = {
        'dashboard': lambda self: Dashboard(notch=self),
        'launcher': lambda self: AppLauncher(notch=self),
        'overview': lambda self: Overview(monitor_id=self.monitor_id),
        'power': lambda self: PowerMenu(notch=self),
        'cliphist': lambda self: ClipHistory(notch=self),
        'tools': lambda self: Toolbox(notch=self),
    }
    
    def __init__(self, monitor_id: int = 0, **kwargs):
        self.monitor_id = monitor_id
        self._bar_ref = None
        self._alive = True

        super().__init__(
            anchor="top",
            margin="-40px 0px 0px 0px",
            monitor=self.monitor_id,
        )
        
        if 'bar' in kwargs:
            self.bar = kwargs['bar']
            
        # Состояние виджета
        self._state = {
            'typed_buffer': "",
            'transitioning': False,
            'notch_open': False,
            'scrolling': False,
            'hovered': False,
            'hidden': False,
            'current_window_class': "",
            'current_widget': None,
            'external_call': False,
            'last_workspace_id': None,
            'last_window_title': "",
            'last_window_class': ""
        }
        
        # Управление ресурсами
        self._timers: Set[int] = set()
        self._signal_handlers: List[tuple] = []
        self._keybindings: List[int] = []
        
        # Lazy-loaded widgets будут храниться здесь
        self._widgets_cache = {}

        self._initialize()
    
    # --- Getters для lazy widgets ---
    @property
    def dashboard(self):
        if 'dashboard' not in self._widgets_cache:
            self._widgets_cache['dashboard'] = self._LAZY_WIDGETS['dashboard'](self)
        return self._widgets_cache['dashboard']

    @property
    def launcher(self):
        if 'launcher' not in self._widgets_cache:
            self._widgets_cache['launcher'] = self._LAZY_WIDGETS['launcher'](self)
        return self._widgets_cache['launcher']

    @property
    def overview(self):
        if 'overview' not in self._widgets_cache:
            self._widgets_cache['overview'] = self._LAZY_WIDGETS['overview'](self)
        return self._widgets_cache['overview']

    @property
    def power(self):
        if 'power' not in self._widgets_cache:
            self._widgets_cache['power'] = self._LAZY_WIDGETS['power'](self)
        return self._widgets_cache['power']

    @property
    def cliphist(self):
        if 'cliphist' not in self._widgets_cache:
            self._widgets_cache['cliphist'] = self._LAZY_WIDGETS['cliphist'](self)
        return self._widgets_cache['cliphist']

    @property
    def tools(self):
        if 'tools' not in self._widgets_cache:
            self._widgets_cache['tools'] = self._LAZY_WIDGETS['tools'](self)
        return self._widgets_cache['tools']
    # --- Конец Getters ---
    
    @property
    def bar(self):
        """Слабая ссылка на родительскую панель."""
        return self._bar_ref() if self._bar_ref else None
    
    @bar.setter
    def bar(self, value):
        self._bar_ref = weakref.ref(value) if value else None

    def _initialize(self):
        """Инициализация компонентов."""
        from utils.monitor_manager import get_monitor_manager
        self.monitor_manager = get_monitor_manager()
        
        self.icon_resolver = IconResolver()
        
        self._setup_widgets()
        self._setup_keybindings()
        self._setup_monitor_specific()
        
        GLib.idle_add(self._finalize)

    def _setup_widgets(self):
        """Инициализация всех виджетов."""
        # Основные виджеты теперь создаются лениво через property
        self._setup_active_window()
        self._setup_main_stack()
        self._setup_layout()

    def _setup_active_window(self):
        """Настройка отображения активного окна."""
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

        # Один обработчик для всех кликов
        self.active_window_box.connect("button-press-event", 
                                     self._on_window_click)

    def _setup_main_stack(self):
        """Настройка основного стека виджетов."""
        # Компактный вид
        self.compact_stack = Stack(
            name="notch-compact-stack",
            transition_type="slide-up-down", 
            transition_duration=100,
        )
        self.compact_stack.add_named(self.active_window_box, "window")

        self.compact = Gtk.EventBox(name="notch-compact")
        self.compact.set_visible(True)
        self.compact.add(self.compact_stack)

        # Подключаем все события одним вызовом
        event_handlers = [
            ("button-press-event", self._on_window_click),
            ("enter-notify-event", self._on_button_enter),
            ("leave-notify-event", self._on_button_leave),
            ("scroll-event", self._on_compact_scroll)
        ]
        
        for signal, handler in event_handlers:
            self.compact.connect(signal, handler)

        # Основной стек
        self.stack = Stack(
            name="notch-content",
            transition_type="crossfade", 
            transition_duration=200,
        )

        # Добавляем все виджеты
        widgets_to_add = [
            ("compact", self.compact),
            ("launcher", self.launcher), # Используем property
            ("dashboard", self.dashboard), # Используем property
            ("overview", self.overview), # Используем property
            ("power", self.power), # Используем property
            ("tools", self.tools), # Используем property
            ("cliphist", self.cliphist) # Используем property
        ]
        
        for name, widget in widgets_to_add:
            self.stack.add_named(widget, name)

        # Устанавливаем стили
        for style in ["panel", "bottom", "Top"]:
            self.stack.add_style_class(style)

        # Устанавливаем размеры
        self.compact.set_size_request(260, 40)
        self.launcher.set_size_request(480, 244)
        self.cliphist.set_size_request(480, 244)
        self.dashboard.set_size_request(1093, 472)

        # Оптимизации стека
        if hasattr(self.stack, 'set_interpolate_size'):
            self.stack.set_interpolate_size(True)
        if hasattr(self.stack, 'set_homogeneous'):
            self.stack.set_homogeneous(False)

    def _setup_layout(self):
        """Настройка макета виджета."""
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
        
        # Revealer для анимаций
        self.notch_revealer = Revealer(
            name="notch-revealer",
            child_revealed=True,
            child=self.notch_box,
        )
        self.notch_revealer.set_size_request(-1, 1)

        # Обертка
        self.notch_complete = Box(
            name="notch-complete",
            children=[self.notch_revealer]
        )

        self.notch_wrap = Box(name="notch-wrap", children=[self.notch_complete])

        # Область для обработки наведения
        self.hover_eventbox = Gtk.EventBox(name="notch-hover-eventbox")
        self.hover_eventbox.add(self.notch_wrap)
        self.hover_eventbox.set_visible(True)
        self.hover_eventbox.set_size_request(260, 4)
        
        # Подключаем события наведения
        hover_mask = Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
        self.hover_eventbox.add_events(hover_mask)
        self.hover_eventbox.connect("enter-notify-event", self._on_notch_hover_enter)
        self.hover_eventbox.connect("leave-notify-event", self._on_notch_hover_leave)
        
        self.add(self.hover_eventbox)

    def _setup_keybindings(self):
        """Настройка горячих клавиш."""
        def close_handler(*args):
            self.close_notch()
        
        key_id = self.add_keybinding("Escape", close_handler)
        if key_id:
            self._keybindings.append(key_id)

    def _setup_monitor_specific(self):
        """Настройка специфичная для монитора."""
        self._state['current_window_class'] = self._get_current_window_class()
        self._window_update_timer = None
        
        self.connect("key-press-event", self._on_key_press)
        
        # Подключаемся к событиям Hyprland
        conn = get_hyprland_connection()
        if conn:
            events = [
                ("event::activewindow", self._on_active_changed_debounced),
                ("event::workspace", self._on_active_changed_debounced)
            ]
            
            for event, handler in events:
                handler_id = conn.connect(event, handler)
                self._signal_handlers.append((conn, handler_id))
        
        if self.get_visible():
            self._start_window_updates()
    
    def _start_window_updates(self):
        """Запускает периодическое обновление информации об окне."""
        self._stop_window_updates()
        
        self._window_update_timer = GLib.timeout_add_seconds(2, self._update_window_display)
        self._timers.add(self._window_update_timer)

    def _stop_window_updates(self):
        """Останавливает обновление информации об окне."""
        if self._window_update_timer and self._window_update_timer in self._timers:
            GLib.source_remove(self._window_update_timer)
            self._timers.discard(self._window_update_timer)
        self._window_update_timer = None

    @debounce(150)
    def _on_active_changed_debounced(self, conn, event):
        """Обработчик изменений активного окна с защитой от дребезга."""
        if self._alive:
            self._update_window_display()
        return False

    def _finalize(self):
        """Финальная настройка после отображения."""
        if not self._alive:
            return False
        
        self.show_all()
        GLib.timeout_add(100, self._optimize_window)
        return False

    def _optimize_window(self):
        """Оптимизация окна после отображения."""
        if not self._alive:
            return False
            
        window = self.get_window()
        if window:
            window.set_event_compression(True)
        return False

    # Основные публичные методы
    def open_notch(self, widget_name: str):
        """Открывает notch с указанным виджетом."""
        if not self._alive:
            return
            
        if self.monitor_manager and self._handle_monitor_focus(widget_name):
            return
            
        self._open_notch_internal(widget_name)

    def toggle_notch(self, widget_name: str):
        """Переключает состояние notch для указанного виджета."""
        if not self._alive:
            return
            
        if (self._state['notch_open'] and 
            self._state['current_widget'] == widget_name):
            self.close_notch()
            return
            
        self._open_notch_internal(widget_name)

    def _open_notch_internal(self, widget_name: str):
        """Внутренняя реализация открытия notch."""
        if not self._alive:
            return
            
        self._stop_window_updates()
        
        # Настройка состояния
        self.notch_revealer.set_reveal_child(True)
        self.notch_box.add_style_class("open")
        self.stack.add_style_class("open")
        self.set_keyboard_mode("exclusive")

        # Отображение соответствующего виджета
        widget_handlers = {
            "network_applet": lambda: self._show_dashboard_widget("network_applet"),
            "bluetooth": lambda: self._show_dashboard_widget("bluetooth"),
            "dashboard": lambda: self._show_dashboard_widget("notification_history"),
            "wallpapers": lambda: self._show_dashboard_board("wallpapers"),
            "overview": lambda: self._show_simple_widget("overview"),
            "power": lambda: self._show_simple_widget("power"),
            "tools": lambda: self._show_simple_widget("tools"),
            "mixer": lambda: self._show_dashboard_board("mixer"),
            "cliphist": lambda: self._show_cliphist(),
            "launcher": lambda: self._show_launcher()
        }
        
        handler = widget_handlers.get(widget_name)
        if handler:
            handler()

        self._state['notch_open'] = True
        self._state['current_widget'] = widget_name

        # Скрываем панель
        if self.bar:
            for attr in ['revealer_right', 'revealer_left']:
                revealer = getattr(self.bar, attr, None)
                if revealer:
                    revealer.set_reveal_child(False)

    def _show_dashboard_widget(self, widget_name: str):
        """Показывает виджет из дашборда."""
        if not self._alive or not self.dashboard: # Теперь использует property
            return
            
        self.stack.set_visible_child(self.dashboard)
        self.dashboard.go_to_section("widgets")
        
        widgets = getattr(self.dashboard, 'widgets', None)
        applet_stack = getattr(widgets, 'applet_stack', None) if widgets else None
        
        if widget_name == "network_applet":
            widget = getattr(widgets, 'network_connections', None) if widgets else None
        elif widget_name == "bluetooth":
            widget = getattr(widgets, 'bluetooth', None) if widgets else None
        elif widget_name == "notification_history":
            widget = getattr(widgets, 'notification_history', None) if widgets else None
        else:
            widget = None
            
        if widget and applet_stack:
            applet_stack.set_visible_child(widget)

    def _show_dashboard_board(self, board: str):
        """Показывает раздел дашборда."""
        if not self._alive:
            return
        self.stack.set_visible_child(self.dashboard) # Теперь использует property
        self.dashboard.go_to_section(board)

    def _show_simple_widget(self, widget_name: str):
        """Показывает простой виджет."""
        if not self._alive:
            return
        widget = getattr(self, widget_name, None)
        if widget:
            self.stack.set_visible_child(widget)

    def _show_cliphist(self):
        """Показывает историю буфера обмена."""
        if not self._alive:
            return
            
        self.stack.set_visible_child(self.cliphist) # Теперь использует property
        if hasattr(self.cliphist, 'open'):
            GLib.idle_add(self.cliphist.open)

    def _show_launcher(self):
        """Показывает лаунчер приложений."""
        if not self._alive:
            return
            
        self.stack.set_visible_child(self.launcher) # Теперь использует property
        self.launcher.open_launcher()
        
        entry = getattr(self.launcher, 'search_entry', None)
        if entry:
            entry.set_text("")
            entry.grab_focus()

    def close_notch(self):
        """Закрывает notch."""
        if not self._alive:
            return
            
        if self.monitor_manager:
            self.monitor_manager.set_notch_state(self.monitor_id, False)
            
        self.set_keyboard_mode("none")
        self.notch_box.remove_style_class("open")
        self.stack.remove_style_class("open")

        # Восстанавливаем панель
        if self.bar:
            for attr in ['revealer_right', 'revealer_left']:
                revealer = getattr(self.bar, attr, None)
                if revealer:
                    revealer.set_reveal_child(True)
        
        # Восстанавливаем состояние дашборда
        if self.dashboard: # Теперь использует property
            widgets = getattr(self.dashboard, 'widgets', None)
            nhistory = getattr(widgets, 'notification_history', None) if widgets else None
            applet_stack = getattr(widgets, 'applet_stack', None) if widgets else None
            
            if applet_stack and nhistory:
                applet_stack.set_visible_child(nhistory)
            
        self._state['notch_open'] = False
        self._state['current_widget'] = None
        self.stack.set_visible_child(self.compact)
        
        self._start_window_updates()

    def _handle_monitor_focus(self, widget_name: str) -> bool:
        """Обрабатывает переключение между мониторами."""
        real_focus = self._get_real_focused_monitor_id()
        if real_focus is not None and self.monitor_manager:
            self.monitor_manager._focused_monitor_id = real_focus
                
        focused_id = self.monitor_manager.get_focused_monitor_id()
        if focused_id != self.monitor_id:
            self.close_notch()
            focused_notch = self.monitor_manager.get_instance(focused_id, 'notch')
            if focused_notch:
                focused_notch._open_notch_internal(widget_name)
            return True
            
        self.monitor_manager.close_all_notches_except(self.monitor_id)
        self.monitor_manager.set_notch_state(self.monitor_id, True, widget_name)
        return False

    def _get_real_focused_monitor_id(self) -> Optional[int]:
        """Получает ID монитора в фокусе."""
        if not self._alive:
            return None

        try:
            result = subprocess.run(
                ["hyprctl", "monitors", "-j"],
                capture_output=True,
                text=True,
                timeout=0.2
            )
            
            if result.returncode == 0:
                monitors = json.loads(result.stdout)
                for i, monitor in enumerate(monitors):
                    if monitor.get('focused', False):
                        return i
        except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError):
            pass

        return None

    # Обработчики событий
    def _on_window_click(self, widget, event) -> bool:
        """Обработчик клика по окну."""
        if self._alive:
            self.toggle_notch("dashboard")
            return True
        return False

    def _on_button_enter(self, widget, event) -> bool:
        """Обработчик наведения мыши."""
        if not self._alive:
            return False
            
        self._state['hovered'] = True
        
        win = widget.get_window()
        if win:
            display = Gdk.Display.get_default()
            if display:
                cursor = Gdk.Cursor.new_for_display(display, Gdk.CursorType.HAND2)
                win.set_cursor(cursor)
        return True

    def _on_button_leave(self, widget, event) -> bool:
        """Обработчик ухода мыши."""
        if not self._alive or event.detail == Gdk.NotifyType.INFERIOR:
            return False
            
        self._state['hovered'] = False
        win = widget.get_window()
        if win:
            win.set_cursor(None)
        return True

    def _on_notch_hover_enter(self, widget, event) -> bool:
        if self._alive:
            self._state['hovered'] = True
        return False

    def _on_notch_hover_leave(self, widget, event) -> bool:
        if self._alive:
            self._state['hovered'] = False
        return False

    def _on_compact_scroll(self, widget, event) -> bool:
        """Обработчик прокрутки компактного вида."""
        if not self._alive or self._state['scrolling']:
            return True

        children = self.compact_stack.get_children()
        if not children:
            return False
            
        current_child = self.compact_stack.get_visible_child()
        current_idx = children.index(current_child) if current_child else 0
        
        # Определяем направление прокрутки
        if event.direction == Gdk.ScrollDirection.UP:
            new_idx = (current_idx - 1) % len(children)
        elif event.direction == Gdk.ScrollDirection.DOWN:
            new_idx = (current_idx + 1) % len(children)
        else:
            return False
            
        self.compact_stack.set_visible_child(children[new_idx])
        self._state['scrolling'] = True
        
        # Сбрасываем состояние через 500 мс
        def reset_scroll():
            self._state['scrolling'] = False
            return False
            
        GLib.timeout_add(500, reset_scroll)
        return True

    def _update_window_display(self) -> bool:
        """Обновляет отображение информации об окне."""
        if not self._alive:
            return False
            
        try:
            workspace_id, window_title, window_class = \
                self._get_current_window_and_workspace()
            
            # Проверяем, изменились ли данные
            if (workspace_id == self._state['last_workspace_id'] and
                window_title == self._state['last_window_title'] and
                window_class == self._state['last_window_class']):
                return True
            
            # Обновляем состояние
            self._state.update({
                'last_workspace_id': workspace_id,
                'last_window_title': window_title,
                'last_window_class': window_class
            })
            
            self.workspace_label.set_label(f"Workspace {workspace_id}")
            
            # Обновляем иконку
            if window_class and window_class.strip():
                self._update_icon(window_class)
                self.window_icon.show()
                self.active_window_container.set_spacing(8)
            else:
                self.window_icon.hide()
                self.active_window_container.set_spacing(0)
                
        except Exception:
            # Обработка ошибок
            self.workspace_label.set_label("Workspace 1")
            self.window_icon.hide()
            self.active_window_container.set_spacing(0)
            
        return True

    def _get_current_window_and_workspace(self) -> Tuple[int, str, str]:
        """Получает информацию о текущем окне и рабочем пространстве."""
        conn = get_hyprland_connection()
        workspace_id = 1
        window_title = ""
        window_class = ""

        if conn:
            try:
                # Получаем активное окно
                reply = conn.send_command("j/activewindow").reply
                if reply:
                    window_data = json.loads(reply.decode())
                    window_title = window_data.get("title", "")
                    window_class = (window_data.get("initialClass") or 
                                   window_data.get("class", ""))
                    
                    # Получаем рабочее пространство из окна или отдельно
                    ws_info = window_data.get("workspace", {})
                    if isinstance(ws_info, dict) and "id" in ws_info:
                        workspace_id = ws_info["id"]
                    else:
                        ws_reply = conn.send_command("j/activeworkspace").reply
                        ws_data = json.loads(ws_reply.decode())
                        workspace_id = ws_data.get("id", 1)
                else:
                    # Если нет активного окна, получаем рабочее пространство
                    ws_reply = conn.send_command("j/activeworkspace").reply
                    ws_data = json.loads(ws_reply.decode())
                    workspace_id = ws_data.get("id", 1)

            except (json.JSONDecodeError, AttributeError, KeyError, 
                    UnicodeDecodeError):
                pass
        
        return workspace_id, window_title, window_class

    def _get_current_window_class(self):
        """Получает класс текущего окна."""
        _, _, w_class = self._get_current_window_and_workspace()
        return w_class

    def _update_icon(self, app_id: str):
        """Обновляет иконку приложения."""
        # Устанавливаем иконку по умолчанию
        self.window_icon.set_from_icon_name("application-x-executable-symbolic", 20)
        
        # Пытаемся получить иконку приложения
        icon = self._get_icon_pixbuf(app_id)
        if icon:
            self.window_icon.set_from_pixbuf(icon)

    def _get_icon_pixbuf(self, app_id: str):
        """Получает пиксбуф иконки приложения."""
        if not app_id or not self._alive or not self.icon_resolver:
            return None
        
        # Ищем через резолвер иконок
        icon = self.icon_resolver.get_icon_pixbuf(app_id, 20)

        # Пытаемся найти по первой части идентификатора
        if not icon and "-" in app_id:
            first_part = app_id.split("-")[0]
            icon = self.icon_resolver.get_icon_pixbuf(first_part, 20)
        
        return icon

    def _on_key_press(self, widget, event) -> bool:
        """Обработчик нажатия клавиш."""
        if not self._alive:
            return False
            
        if event.keyval == Gdk.KEY_Escape:
            self.close_notch()
            return True

        return False

    def toggle_hidden(self):
        """Переключает видимость notch."""
        if not self._alive:
            return
            
        self._state['hidden'] = not self._state['hidden']
        visible = not self._state['hidden']
        self.set_visible(visible)
        
        if visible:
            self._start_window_updates()
        else:
            self._stop_window_updates()

    def destroy(self):
        """Уничтожает виджет и освобождает ресурсы."""
        if not self._alive:
            return
            
        self._alive = False

        # Останавливаем все таймеры
        for timer_id in list(self._timers):
            if timer_id:
                GLib.source_remove(timer_id)
        self._timers.clear()
        
        self._stop_window_updates()

        # Очищаем debounce таймеры
        for attr in list(self.__dict__.keys()):
            if attr.startswith('_debounce_'):
                t_id = getattr(self, attr)
                if isinstance(t_id, int) and t_id in self._timers:
                    GLib.source_remove(t_id)
                delattr(self, attr)

        # Отключаем все сигналы
        for conn, handler_id in self._signal_handlers:
            try:
                conn.disconnect(handler_id)
            except:
                pass
        self._signal_handlers.clear()

        # Отключаем привязки клавиш
        for key_id in self._keybindings:
            try:
                self.remove_keybinding(key_id)
            except:
                pass
        self._keybindings.clear()

        # Уничтожаем лениво загруженные виджеты
        for name, widget in self._widgets_cache.items():
            if widget:
                # Разрываем связи
                if hasattr(widget, 'notch'):
                    widget.notch = None
                # Уничтожаем
                try:
                    widget.destroy()
                except:
                    pass

        # Очищаем кэш
        self._widgets_cache.clear()

        # Очищаем ссылки
        self._bar_ref = None
        self.monitor_manager = None
        self.icon_resolver = None

        # Вызываем родительский destroy
        super().destroy()

        # Принудительный сбор мусора
        gc.collect()