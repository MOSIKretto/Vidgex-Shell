from fabric.hyprland.widgets import get_hyprland_connection
from fabric.utils.helpers import get_desktop_applications
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
import time
import functools
from typing import Optional, Dict, Any, List, Set
from collections import OrderedDict

from modules.Panel.Dashboard_Bar.dashboard import Dashboard
from modules.Panel.cliphist import ClipHistory
from widgets.corners import MyCorner
from modules.Panel.launcher import AppLauncher
from modules.Panel.overview import Overview
from modules.Panel.power import PowerMenu
from modules.Panel.tools import Toolbox
from utils.icon_resolver import IconResolver
from widgets.wayland import WaylandWindow as Window


def debounce(delay: int):
    """Оптимизированный декоратор для дебаунса вызовов функций"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            timer_attr = f'_debounce_{func.__name__}'
            
            if hasattr(self, timer_attr):
                timer_id = getattr(self, timer_attr)
                if timer_id:
                    GLib.source_remove(timer_id)
                    if timer_id in self._timers:
                        self._timers.remove(timer_id)
            
            def call_func():
                try:
                    result = func(self, *args, **kwargs)
                finally:
                    setattr(self, timer_attr, None)
                return result
            
            timer_id = GLib.timeout_add(delay, call_func)
            setattr(self, timer_attr, timer_id)
            self._timers.add(timer_id)
            
            return False
        return wrapper
    return decorator


class Notch(Window):
    # Статические кэши с LRU стратегией
    _shared_app_identifiers_cache: Optional[Dict[str, Any]] = None
    _app_cache_timestamp: float = 0
    _subprocess_cache = OrderedDict()
    _CACHE_MAX_SIZE = 50
    _CACHE_TIMEOUT = 2.0
    
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
        self._timers: Set[int] = set()
        self._signal_handlers: List[tuple] = []
        self._keybindings: List[int] = []
        
        self._init_components()
        self._setup_widgets()
        self._setup_keybindings()
        self._setup_monitor_specific()
        
        GLib.idle_add(self._finalize_initialization)
    
    @property
    def bar(self):
        if self._bar_ref:
            return self._bar_ref()
        return None
    
    @bar.setter
    def bar(self, value):
        if value:
            self._bar_ref = weakref.ref(value)
        else:
            self._bar_ref = None

    def _init_components(self):
        self.monitor_manager = None
        from utils.monitor_manager import get_monitor_manager
        self.monitor_manager = get_monitor_manager()
        
        self.icon_resolver = IconResolver()
        self._icon_cache = OrderedDict()
        self._MAX_ICON_CACHE = 30
        
        # Единый кэш идентификаторов приложений для всех инстансов
        if Notch._shared_app_identifiers_cache is None:
            Notch._shared_app_identifiers_cache = self._build_app_identifiers_cache()
            Notch._app_cache_timestamp = time.time()
        self._app_identifiers_cache = Notch._shared_app_identifiers_cache

    def _build_app_identifiers_cache(self) -> Dict[str, Any]:
        """Оптимизированное создание кэша идентификаторов приложений"""
        cache = {}
        apps = get_desktop_applications()
        
        for app in apps:
            # Быстрое извлечение идентификаторов
            identifiers = []
            
            # Оптимизированный порядок проверки наиболее вероятных атрибутов
            if hasattr(app, 'window_class') and app.window_class:
                identifiers.append(app.window_class.lower())
            if hasattr(app, 'name') and app.name:
                identifiers.append(app.name.lower())
            if hasattr(app, 'executable') and app.executable:
                # Извлекаем имя файла без пути
                exe_name = app.executable.split('/')[-1].lower()
                if exe_name:
                    identifiers.append(exe_name)
            if hasattr(app, 'display_name') and app.display_name:
                identifiers.append(app.display_name.lower())
            
            # Добавляем все идентификаторы в кэш
            for identifier in identifiers:
                if identifier and identifier not in cache:
                    cache[identifier] = app
        
        return cache

    def _setup_widgets(self):
        """Ленивая инициализация виджетов с отложенной загрузкой"""
        self.dashboard = Dashboard(notch=self)
        self.launcher = AppLauncher(notch=self)
        self.overview = Overview(monitor_id=self.monitor_id)
        self.power = PowerMenu(notch=self)
        self.cliphist = ClipHistory(notch=self)
        self.tools = Toolbox(notch=self)

        # Отложенная инициализация виджетов дашборда
        self.nhistory = None
        self.applet_stack = None
        self.btdevices = None
        self.nwconnections = None

        self._setup_active_window()
        self._setup_main_stack()
        self._setup_layout()

    def _cache_dashboard_widgets(self):
        """Ленивое кэширование виджетов дашборда"""
        if not hasattr(self.dashboard, 'widgets'):
            return
            
        widgets = self.dashboard.widgets
        self.nhistory = getattr(widgets, 'notification_history', None)
        self.applet_stack = getattr(widgets, 'applet_stack', None)
        self.btdevices = getattr(widgets, 'bluetooth', None)
        self.nwconnections = getattr(widgets, 'network_connections', None)

    def _setup_active_window(self):
        self.window_icon = Image(
            name="notch-window-icon", 
            icon_name="application-x-executable", 
            icon_size=20
        )

        self.workspace_label = Label(
            name="workspace-label",
            label="Workspace 1"  # Значение по умолчанию
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

        self.active_window_box.connect("button-press-event", self._on_active_window_click)

    def _setup_main_stack(self):
        self._setup_compact_stack()
        self._setup_main_stack_container()
        self._apply_stack_styling()
        self._set_widget_sizes()

    def _setup_compact_stack(self):
        self.compact_stack = Stack(
            name="notch-compact-stack",
            transition_type="slide-up-down", 
            transition_duration=100,
        )
    
        self.compact_stack.add_named(self.active_window_box, "window")

        self.compact = Gtk.EventBox(name="notch-compact")
        self.compact.set_visible(True)
        self.compact.add(self.compact_stack)

        self.compact.connect("button-press-event", self._on_active_window_click)
        self.compact.connect("enter-notify-event", self._on_button_enter)
        self.compact.connect("leave-notify-event", self._on_button_leave)

    def _setup_main_stack_container(self):
        self.stack = Stack(
            name="notch-content",
            transition_type="crossfade", 
            transition_duration=200,  # Уменьшено с 250
        )

        self.stack.add_named(self.compact, "compact")
        self.stack.add_named(self.launcher, "launcher")
        self.stack.add_named(self.dashboard, "dashboard")
        self.stack.add_named(self.overview, "overview")
        self.stack.add_named(self.power, "power")
        self.stack.add_named(self.tools, "tools")
        self.stack.add_named(self.cliphist, "cliphist")

    def _apply_stack_styling(self):
        self.stack.add_style_class("panel")
        self.stack.add_style_class("bottom")
        self.stack.add_style_class("Top")

    def _set_widget_sizes(self):
        self.compact.set_size_request(260, 40)
        self.launcher.set_size_request(480, 244)
        self.cliphist.set_size_request(480, 244)
        self.dashboard.set_size_request(1093, 472)

        if hasattr(self.stack, 'set_interpolate_size'):
            self.stack.set_interpolate_size(True)
        if hasattr(self.stack, 'set_homogeneous'):  
            self.stack.set_homogeneous(False)

    def _setup_layout(self):
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

        self.notch_box = CenterBox(
            name="notch-box",
            start_children=self.corner_left,
            center_children=self.stack,
            end_children=self.corner_right,
        )
        self.notch_box.add_style_class("notch")
        
        self.notch_revealer = Revealer(
            name="notch-revealer",
            transition_duration=200,  # Уменьшено с 250
            child_revealed=True,
            child=self.notch_box,
        )
        self.notch_revealer.set_size_request(-1, 1)

        self.notch_complete = Box(
            name="notch-complete",
            children=[self.notch_revealer]
        )

        self.notch_wrap = Box(name="notch-wrap", children=[self.notch_complete])

        self.hover_eventbox = Gtk.EventBox(name="notch-hover-eventbox")
        self.hover_eventbox.add(self.notch_wrap)
        self.hover_eventbox.set_visible(True)
        self.hover_eventbox.set_size_request(260, 4)
        
        hover_mask = Gdk.EventMask.ENTER_NOTIFY_MASK
        hover_mask |= Gdk.EventMask.LEAVE_NOTIFY_MASK
        self.hover_eventbox.add_events(hover_mask)
        
        self.hover_eventbox.connect("enter-notify-event", self._on_notch_hover_enter)
        self.hover_eventbox.connect("leave-notify-event", self._on_notch_hover_leave)
        self.add(self.hover_eventbox)

    def _setup_keybindings(self):
        def close_handler(*args):
            self.close_notch()
        key_id = self.add_keybinding("Escape", close_handler)
        if key_id:
            self._keybindings.append(key_id)

    def _setup_monitor_specific(self):
        self._state['current_window_class'] = self._get_current_window_class()
        self._window_update_timer = None
        self._hypr_conn_handlers = []
        
        self.connect("key-press-event", self._on_key_press)
        
        conn = get_hyprland_connection()
        if conn:
            # Используем дебаунс для обработки событий
            handler1 = conn.connect("event::activewindow", 
                                   self._on_active_changed_debounced)
            handler2 = conn.connect("event::workspace", 
                                   self._on_active_changed_debounced)
            self._hypr_conn_handlers = [handler1, handler2]
            self._signal_handlers.extend([(conn, handler1), (conn, handler2)])
        
        if self.get_visible():
            self._start_window_updates()
    
    def _start_window_updates(self):
        """Запустить обновления окна с оптимизированным интервалом"""
        if self._window_update_timer:
            GLib.source_remove(self._window_update_timer)
        
        # Увеличиваем интервал обновления до 2 секунд для экономии ресурсов
        self._window_update_timer = GLib.timeout_add_seconds(2, self._update_window_display)
        self._timers.add(self._window_update_timer)
    
    def _stop_window_updates(self):
        """Остановить обновления окна"""
        if self._window_update_timer:
            GLib.source_remove(self._window_update_timer)
            self._timers.discard(self._window_update_timer)
            self._window_update_timer = None

    @debounce(150)  # Увеличено с 100 для уменьшения нагрузки
    def _on_active_changed_debounced(self, conn, event):
        if self._alive:
            self._update_window_display()
        return False

    def _finalize_initialization(self):
        if not self._alive:
            return False
        
        self.show_all()
        
        # Отложенная оптимизация после отображения
        GLib.timeout_add(100, self._perform_post_show_optimizations)
        return False

    def _perform_post_show_optimizations(self):
        if not self._alive:
            return False
            
        if hasattr(self, 'get_window'):
            window = self.get_window()
            if window:
                window.set_event_compression(True)
        return False

    def open_notch(self, widget_name: str):
        if not self._alive:
            return
            
        if self.monitor_manager and self._handle_monitor_focus(widget_name):
            return
            
        self._open_notch_internal(widget_name)

    def toggle_notch(self, widget_name: str):
        if not self._alive:
            return
            
        if self._state['notch_open'] and self._state['current_widget'] == widget_name:
            self.close_notch()
            return
            
        self._open_notch_internal(widget_name)

    def _open_notch_internal(self, widget_name: str):
        if not self._alive:
            return
            
        # Останавливаем обновления окна для экономии ресурсов
        self._stop_window_updates()
        
        # Ленивая инициализация виджетов дашборда
        if widget_name in ["network_applet", "bluetooth", "dashboard"]:
            self._cache_dashboard_widgets()
        
        self.notch_revealer.set_reveal_child(True)
        self.notch_box.add_style_class("open")
        self.stack.add_style_class("open")
        self.set_keyboard_mode("exclusive")

        # Оптимизированный выбор виджета
        widget_handlers = {
            "network_applet": lambda: self._show_widgets('nwconnections'),
            "bluetooth": lambda: self._show_widgets('btdevices'),
            "dashboard": lambda: self._show_widgets('nhistory'),
            "wallpapers": lambda: self._show_otherboards("wallpapers"),
            "overview": lambda: self._show_other("overview"),
            "power": lambda: self._show_other("power"),
            "tools": lambda: self._show_other("tools"),
            "mixer": lambda: self._show_otherboards("mixer"),
            "cliphist": lambda: self._show_cliphist(),
            "launcher": lambda: self._show_launcher(),
        }
        
        handler = widget_handlers.get(widget_name)
        if handler:
            handler()
        
        self._state['notch_open'] = True
        self._state['current_widget'] = widget_name

        if self.bar:
            self.bar.revealer_right.set_reveal_child(False)
            self.bar.revealer_left.set_reveal_child(False)

    def _show_widgets(self, widgets: str):
        if not self._alive:
            return
            
        self.stack.set_visible_child(self.dashboard)
        self.dashboard.go_to_section("widgets")
        
        widget = getattr(self, widgets, None)
        if widget and self.applet_stack:
            self.applet_stack.set_visible_child(widget)

    def _show_otherboards(self, board: str):
        if not self._alive:
            return
            
        self.stack.set_visible_child(self.dashboard)
        self.dashboard.go_to_section(board)

    def _show_other(self, other: str):
        if not self._alive:
            return
            
        widget = getattr(self, other, None)
        if widget:
            self.stack.set_visible_child(widget)

    def _show_cliphist(self):
        if not self._alive:
            return
            
        self.stack.set_visible_child(self.cliphist)
        if hasattr(self.cliphist, 'open'):
            GLib.idle_add(self.cliphist.open)

    def _show_launcher(self):
        if not self._alive:
            return
            
        self.stack.set_visible_child(self.launcher)
        self.launcher.open_launcher()
        entry = self.launcher.search_entry
        if entry:
            entry.set_text("")
            entry.grab_focus()

    def close_notch(self):
        if not self._alive:
            return
            
        if self.monitor_manager:
            self.monitor_manager.set_notch_state(self.monitor_id, False)
            
        self.set_keyboard_mode("none")
        self.notch_box.remove_style_class("open")
        self.stack.remove_style_class("open")

        if self.bar:
            self.bar.revealer_right.set_reveal_child(True)
            self.bar.revealer_left.set_reveal_child(True)
        
        if self.applet_stack and self.nhistory:
            self.applet_stack.set_visible_child(self.nhistory)
            
        self._state['notch_open'] = False
        self._state['current_widget'] = None
        self.stack.set_visible_child(self.compact)
        
        # Возобновляем обновления с увеличенным интервалом
        self._start_window_updates()

    def _handle_monitor_focus(self, widget_name: str) -> bool:
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

    @classmethod
    def _cleanup_class_cache(cls):
        """Очистка классового кэша с LRU стратегией"""
        current_time = time.time()
        
        # Очистка по времени
        keys_to_remove = []
        for key in list(cls._subprocess_cache.keys()):
            if current_time - cls._subprocess_cache.get('_timestamp_' + key, 0) >= cls._CACHE_TIMEOUT:
                keys_to_remove.append(key)
        
        for key in keys_to_remove:
            cls._subprocess_cache.pop(key, None)
            cls._subprocess_cache.pop('_timestamp_' + key, None)
        
        # Очистка по размеру (LRU)
        while len(cls._subprocess_cache) > cls._CACHE_MAX_SIZE * 2:  # *2 из-за timestamp записей
            cls._subprocess_cache.popitem(last=False)

    def _get_real_focused_monitor_id(self) -> Optional[int]:
        if not self._alive:
            return None
            
        cache_key = "focused_monitor"
        current_time = time.time()

        Notch._cleanup_class_cache()

        if cache_key in Notch._subprocess_cache:
            cached_time = Notch._subprocess_cache.get('_timestamp_' + cache_key, 0)
            if current_time - cached_time < 2.0:
                return Notch._subprocess_cache[cache_key]

        try:
            result = subprocess.run(
                ["hyprctl", "monitors", "-j"],
                capture_output=True,
                text=True,
                timeout=0.2  # Уменьшено с 0.3
            )
            if result.returncode == 0:
                monitors = json.loads(result.stdout)
                for i, monitor in enumerate(monitors):
                    if monitor.get('focused', False):
                        Notch._subprocess_cache[cache_key] = i
                        Notch._subprocess_cache['_timestamp_' + cache_key] = current_time
                        # Перемещаем в конец для LRU
                        Notch._subprocess_cache.move_to_end(cache_key)
                        Notch._subprocess_cache.move_to_end('_timestamp_' + cache_key)
                        return i
        except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError):
            pass

        return None

    def _on_active_window_click(self, widget, event) -> bool:
        if self._alive:
            self.toggle_notch("dashboard")
            return True
        return False

    def _on_button_enter(self, widget, event) -> bool:
        if not self._alive:
            return False
            
        self._state['hovered'] = True
        if widget.get_window():
            display = Gdk.Display.get_default()
            if display:
                cursor = Gdk.Cursor.new_for_display(display, Gdk.CursorType.HAND2)
                widget.get_window().set_cursor(cursor)
        return True

    def _on_button_leave(self, widget, event) -> bool:
        if not self._alive:
            return False
            
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self._state['hovered'] = False
        if widget.get_window():
            widget.get_window().set_cursor(None)
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
        if not self._alive or self._state['scrolling']:
            return True

        children = self.compact_stack.get_children()
        if not children:
            return False
            
        current_child = self.compact_stack.get_visible_child()
        current_idx = children.index(current_child) if current_child in children else 0
        
        if event.direction == Gdk.ScrollDirection.UP:
            new_idx = current_idx - 1
            if new_idx < 0:
                new_idx = len(children) - 1
        elif event.direction == Gdk.ScrollDirection.DOWN:
            new_idx = current_idx + 1
            if new_idx >= len(children):
                new_idx = 0
        else:
            return False
            
        self.compact_stack.set_visible_child(children[new_idx])
        self._state['scrolling'] = True
        
        def reset_scrolling():
            self._state['scrolling'] = False
            return False
            
        timer_id = GLib.timeout_add(500, reset_scrolling)
        self._timers.add(timer_id)
        return True

    def _update_window_display(self) -> bool:
        if not self._alive:
            return False
            
        try:
            workspace_id, window_title = self._get_current_window_and_workspace()
            window_class = self._get_current_window_class()
            
            # Быстрая проверка изменений
            if (workspace_id == self._state['last_workspace_id'] and
                window_title == self._state['last_window_title'] and
                window_class == self._state['last_window_class']):
                return True  # Нет изменений, пропускаем обновление
            
            # Обновляем состояние
            self._state['last_workspace_id'] = workspace_id
            self._state['last_window_title'] = window_title
            self._state['last_window_class'] = window_class
            
            # Обновляем UI только если нужно
            self.workspace_label.set_label(f"Workspace {workspace_id}")
            
            if window_class and window_class.strip():
                self._set_icon_from_hyprland()
                self.window_icon.show()
                self.active_window_container.set_spacing(8)
            else:
                self.window_icon.hide()
                self.active_window_container.set_spacing(0)
                
        except Exception:
            self._set_fallback_display()
            
        return True

    def _get_current_window_and_workspace(self):
        conn = get_hyprland_connection()
        if conn:
            try:
                # Комбинированный запрос для уменьшения вызовов
                active_data = conn.send_command("j/activewindow").reply.decode()
                workspace_data = conn.send_command("j/activeworkspace").reply.decode()
                
                window_data = json.loads(active_data)
                window_title = window_data.get("title", "")
                
                workspace_json = json.loads(workspace_data)
                workspace_id = workspace_json.get("id", 1)
                
                return workspace_id, window_title
            except (json.JSONDecodeError, AttributeError, KeyError, UnicodeDecodeError):
                pass
        
        return 1, ""

    def _set_icon_from_hyprland(self):
        try:
            conn = get_hyprland_connection()
            if conn:
                window_data = json.loads(conn.send_command("j/activewindow").reply.decode())
                app_id = window_data.get("initialClass") or window_data.get("class", "")
                
                # Устанавливаем дефолтную иконку
                self.window_icon.set_from_icon_name("application-x-executable-symbolic", 20)
                
                # Пробуем загрузить иконку
                icon = self._get_icon_pixbuf(app_id)
                if icon:
                    self.window_icon.set_from_pixbuf(icon)
                    
        except (json.JSONDecodeError, AttributeError, KeyError, UnicodeDecodeError):
            pass

    def _get_icon_pixbuf(self, app_id: str):
        if not app_id or not self._alive:
            return None
            
        # Проверка LRU кэша
        if app_id in self._icon_cache:
            # Перемещаем в конец (самую новую)
            self._icon_cache.move_to_end(app_id)
            return self._icon_cache[app_id]
        
        app_key = app_id.lower()
        icon = None
        
        # Поиск в кэше приложений
        if app_key in self._app_identifiers_cache:
            app = self._app_identifiers_cache[app_key]
            if hasattr(app, 'get_icon_pixbuf'):
                icon = app.get_icon_pixbuf(size=20)

        # Поиск через icon_resolver (только если не нашли в кэше)
        if not icon and self.icon_resolver:
            icon = self.icon_resolver.get_icon_pixbuf(app_id, 20)

        if not icon and "-" in app_id:
            first_part = app_id.split("-")[0]
            icon = self.icon_resolver.get_icon_pixbuf(first_part, 20)
        
        if icon:
            # Добавляем в LRU кэш
            self._icon_cache[app_id] = icon
            
            # Ограничиваем размер кэша
            if len(self._icon_cache) > self._MAX_ICON_CACHE:
                self._icon_cache.popitem(last=False)
            
        return icon

    def _set_fallback_display(self):
        workspace_id, _ = self._get_current_window_and_workspace()
        self.workspace_label.set_label(f"Workspace {workspace_id}")
        self.window_icon.hide()
        self.active_window_container.set_spacing(0)

    def _get_current_window_class(self):
        conn = get_hyprland_connection()
        if conn:
            try:
                data = json.loads(conn.send_command("j/activewindow").reply.decode())
                return data.get("initialClass") or data.get("class", "")
            except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
                pass
        
        return ""

    def _on_key_press(self, widget, event) -> bool:
        if not self._alive:
            return False
            
        if event.keyval == Gdk.KEY_Escape:
            self.close_notch()
            return True

        return False

    def toggle_hidden(self):
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
        if not self._alive:
            return
            
        self._alive = False
        
        # Останавливаем все таймеры
        for timer_id in list(self._timers):
            try:
                GLib.source_remove(timer_id)
            except:
                pass
        self._timers.clear()
        
        # Останавливаем обновления окна
        self._stop_window_updates()
        
        # Очищаем дебаунс таймеры
        for attr_name in dir(self):
            if attr_name.startswith('_debounce_'):
                timer_id = getattr(self, attr_name)
                if timer_id:
                    try:
                        GLib.source_remove(timer_id)
                    except:
                        pass
                setattr(self, attr_name, None)
        
        # Отключаем keybindings
        for key_id in self._keybindings:
            try:
                self.remove_keybinding(key_id)
            except:
                pass
        self._keybindings.clear()
        
        # Отключаем signal handlers
        for conn, handler_id in self._signal_handlers:
            try:
                conn.disconnect(handler_id)
            except:
                pass
        self._signal_handlers.clear()
        
        # Отключаем Hyprland connection handlers
        conn = get_hyprland_connection()
        if conn and self._hypr_conn_handlers:
            for handler_id in self._hypr_conn_handlers:
                try:
                    conn.disconnect(handler_id)
                except:
                    pass
        self._hypr_conn_handlers.clear()
        
        # Очищаем кэши
        self._icon_cache.clear()
        
        # Очищаем под-виджеты
        widgets_to_destroy = ['dashboard', 'launcher', 'overview', 'power', 'cliphist', 'tools']
        for widget_name in widgets_to_destroy:
            widget = getattr(self, widget_name, None)
            if widget and hasattr(widget, 'destroy'):
                try:
                    widget.destroy()
                except:
                    pass
            setattr(self, widget_name, None)
        
        # Очищаем ссылки
        self.monitor_manager = None
        self.icon_resolver = None
        self._bar_ref = None
        
        # Очищаем классовые кэши
        self._cleanup_class_cache()
        
        try:
            super().destroy()
        except:
            pass