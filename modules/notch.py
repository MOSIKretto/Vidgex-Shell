from fabric.hyprland.widgets import ( 
    HyprlandLanguage as Language,
    HyprlandWorkspaces as Workspaces,
    WorkspaceButton, 
    get_hyprland_connection
)
from fabric.utils.helpers import get_desktop_applications
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.datetime import DateTime
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer
from fabric.widgets.stack import Stack

from gi.repository import Gdk, GLib, Gtk

import os
import json
import subprocess
import weakref
from typing import Optional, Dict, Set, Any
from collections import OrderedDict
from functools import lru_cache

import modules.icons as icons
from modules.cliphist import ClipHistory
from modules.controls import ControlSmall
from modules.corners import MyCorner
from modules.dashboard import Dashboard
from modules.launcher import AppLauncher
from modules.metrics import Battery, MetricsSmall, NetworkApplet
from modules.overview import Overview
from modules.power import PowerMenu
from modules.systemtray import SystemTray
from modules.tools import Toolbox
from utils.icon_resolver import IconResolver
from widgets.wayland import WaylandWindow as Window


CHINESE_NUMERALS = ["一", "二", "三", "四", "五", "六", "七", "八", "九"]

# Performance optimization constants
MAX_ICON_CACHE_SIZE = 50
MAX_SUBPROCESS_CACHE_SIZE = 100  # Лимит для subprocess кэша
WINDOW_UPDATE_INTERVAL = 500
SUBPROCESS_TIMEOUT = 0.3
DEBOUNCE_DELAY = 100  # ms
CACHE_TTL = 2.0  # seconds

# LRU Cache for subprocess calls with size limit
_subprocess_cache = OrderedDict()
_cache_timestamps = {}


class Notch(Window):
    def __init__(self, monitor_id: int = 0, **kwargs):
        self.monitor_id = monitor_id
        
        # Store weak reference to bar to avoid circular reference
        self._bar_ref = None
        
        self._theme_config = self._precompute_theme_config()

        super().__init__(
            name="notch",
            layer="top",
            anchor=self._get_anchor(),
            margin=self._get_margin(),
            keyboard_mode="none",
            exclusivity="none" if self._theme_config['is_notch'] else "normal",
            visible=True,
            all_visible=True,
            monitor=self.monitor_id,
        )
        
        # Use property for bar to manage weak reference
        bar = kwargs.get('bar')
        if bar:
            self.bar = bar
            
        self._state = self._initialize_state()
        
        # Track resources for cleanup
        self._timers = []
        self._signal_handlers = []
        
        self._init_components()
        self._setup_widgets()
        self._setup_keybindings()
        self._setup_monitor_specific()
        
        GLib.idle_add(self._finalize_initialization)
    
    @property
    def bar(self):
        """Get bar instance from weak reference."""
        return self._bar_ref() if self._bar_ref else None
    
    @bar.setter
    def bar(self, value):
        """Set bar instance using weak reference."""
        self._bar_ref = weakref.ref(value) if value else None

    def _precompute_theme_config(self) -> Dict:
        config = {}
        config['is_notch'] = "Notch"
        config['is_panel'] = "Notch" == "Panel"
        config['is_bottom'] = "Top" == "Bottom"
        config['is_top'] = "Top"
        config['position'] = "top"
        return config

    def _initialize_state(self) -> Dict:
        return {
            'typed_buffer': "",
            'transitioning': False,
            'notch_open': False,
            'scrolling': False,
            'hovered': False,
            'hidden': False,
            'current_window_class': "",
            'current_widget': None,
            'external_call': False
        }

    def _get_anchor(self) -> str:
        config = self._theme_config
        
        if config['is_notch']:
            return "top"
        
        if config['is_top']:
            if config['position'] == "start":
                return "top start"
            elif config['position'] == "end":
                return "top end"
            else:
                return "top"
        
        if config['is_bottom']:
            if config['position'] == "start":
                return "bottom start"
            elif config['position'] == "end":
                return "bottom end"
            else:
                return "bottom"
        
        return "top"

    def _get_margin(self) -> str:
        config = self._theme_config
        
        if config['is_panel'] or config['is_bottom']:
            return "0px 0px 0px 0px"
        
        return "-40px 0px 0px 0px"

    def _init_components(self):
        self.monitor_manager = self._get_monitor_manager()
        self.icon_resolver = IconResolver()
        self._icon_cache = OrderedDict()
        
        if not hasattr(Notch, '_shared_app_identifiers_cache'):
            Notch._shared_app_identifiers_cache = self._build_app_identifiers_cache()
        self._app_identifiers_cache = Notch._shared_app_identifiers_cache

    def _get_monitor_manager(self):
        try:
            from utils.monitor_manager import get_monitor_manager
            return get_monitor_manager()
        except (ImportError, AttributeError):
            return None

    def _build_app_identifiers_cache(self) -> Dict:
        cache = {}
        try:
            apps = get_desktop_applications()
            for app in apps:
                identifiers = self._extract_app_identifiers(app)
                for identifier in identifiers:
                    if identifier and identifier.lower() not in cache:
                        cache[identifier.lower()] = app
        except Exception:
            pass
        return cache

    def _extract_app_identifiers(self, app) -> Set[str]:
        identifiers = set()
        attrs = ['name', 'display_name', 'window_class', 'executable']
        for attr in attrs:
            if hasattr(app, attr):
                value = getattr(app, attr)
                if value:
                    if attr == 'executable':
                        parts = value.split('/')
                        if parts:
                            identifiers.add(parts[-1])
                    else:
                        identifiers.add(value)
        return identifiers

    def _setup_widgets(self):
        self._lazy_init_heavy_components()
        self._setup_active_window()
        self._setup_main_stack()
        self._setup_layout()

    def _lazy_init_heavy_components(self):
        self.dashboard = Dashboard(notch=self)
        self.launcher = AppLauncher(notch=self)
        self.overview = Overview(monitor_id=self.monitor_id)
        self.power = PowerMenu(notch=self)
        self.cliphist = ClipHistory(notch=self)
        self.tools = Toolbox(notch=self)

        if hasattr(self.dashboard, 'widgets'):
            self._cache_dashboard_widgets()

    def _cache_dashboard_widgets(self):
        widgets = self.dashboard.widgets
        self.nhistory = getattr(widgets, 'notification_history', None)
        self.applet_stack = getattr(widgets, 'applet_stack', None)
        self.btdevices = getattr(widgets, 'bluetooth', None)
        self.nwconnections = getattr(widgets, 'network_connections', None)
        
        if self.btdevices:
            self.btdevices.set_visible(False)
        if self.nwconnections:
            self.nwconnections.set_visible(False)

    def _setup_active_window(self):
        self.window_icon = Image(
            name="notch-window-icon", 
            icon_name="application-x-executable", 
            icon_size=20
        )
        
        self.window_icon.hide()

        self.workspace_label = Label(
            name="workspace-label"
        )

        self.active_window_container = Box(
            name="active-window-container",
            orientation="h",
            spacing=8,
            h_align="center",
            v_align="center",
            children=[self.window_icon, self.workspace_label],
        )

        self.active_window_box = Box(
            name="active-window-box",
            h_align="center",
            v_align="center",
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
            v_expand=True, 
            h_expand=True,
            transition_type="slide-up-down", 
            transition_duration=100,
        )

        self.compact_stack.add_named(self.active_window_box, "window")
        self.compact_stack.set_visible_child_name("window")

        self.compact = Gtk.EventBox(name="notch-compact")
        self.compact.set_visible(True)
        self.compact.add(self.compact_stack)
        
        event_mask = Gdk.EventMask.BUTTON_PRESS_MASK
        event_mask |= Gdk.EventMask.SCROLL_MASK
        event_mask |= Gdk.EventMask.SMOOTH_SCROLL_MASK
        self.compact.add_events(event_mask)
        
        self.compact.connect("scroll-event", self._on_compact_scroll)
        self.compact.connect("button-press-event", self._on_active_window_click)
        self.compact.connect("enter-notify-event", self._on_button_enter)
        self.compact.connect("leave-notify-event", self._on_button_leave)

    def _setup_main_stack_container(self):
        self.stack = Stack(
            name="notch-content",
            v_expand=True, 
            h_expand=True,
            transition_type="crossfade", 
            transition_duration=250,
        )

        # Оригинальная последовательность добавления виджетов
        self.stack.add_named(self.compact, "compact")
        self.stack.add_named(self.launcher, "launcher")
        self.stack.add_named(self.dashboard, "dashboard")
        self.stack.add_named(self.overview, "overview")
        self.stack.add_named(self.power, "power")
        self.stack.add_named(self.tools, "tools")
        self.stack.add_named(self.cliphist, "cliphist")

    def _apply_stack_styling(self):
        if self._theme_config['is_panel']:
            self.stack.add_style_class("panel")
            if self._theme_config['is_bottom']:
                self.stack.add_style_class("bottom")
            else:
                self.stack.add_style_class("top")
            self.stack.add_style_class(self._theme_config['position'])
        
        if "Pills" in ["Dense", "Edge"] and not self._theme_config['is_bottom']:
            self.stack.add_style_class("invert")

    def _set_widget_sizes(self):
        # Оригинальные размеры без изменений
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
            orientation="h",
            h_align="center",
            v_align="center",
            start_children=self.corner_left,
            center_children=self.stack,
            end_children=self.corner_right,
        )
        self.notch_box.add_style_class("notch")

        if self._theme_config['is_notch']:
            transition_type = "slide-down"
        elif self._theme_config['is_bottom']:
            transition_type = "slide-up"
        else:
            transition_type = "slide-down"
        
        self.notch_revealer = Revealer(
            name="notch-revealer",
            transition_type=transition_type,
            transition_duration=250,
            child_revealed=True,
            child=self.notch_box,
        )
        self.notch_revealer.set_size_request(-1, 1)

        self.notch_complete = Box(
            name="notch-complete",
            orientation="h",
            children=[self.notch_revealer]
        )

        self.notch_wrap = Box(name="notch-wrap", children=[self.notch_complete])

        if self._theme_config['is_notch']:
            self._setup_notch_hover_handling()
        else:
            self.add(self.notch_wrap)
            self.corner_left.set_visible(False)
            self.corner_right.set_visible(False)

    def _setup_notch_hover_handling(self):
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
        self.add_keybinding("Escape", close_handler)

    def _setup_monitor_specific(self):
        self._state['current_window_class'] = self._get_current_window_class()
        self._window_update_timer = None
        self._hypr_conn_handlers = []
        self._debounce_timer = None
        
        self.connect("key-press-event", self._on_key_press)
        
        conn = get_hyprland_connection()
        if conn:
            handler1 = conn.connect("event::activewindow", self._on_active_window_changed_debounced)
            handler2 = conn.connect("event::workspace", self._on_workspace_changed_debounced)
            self._hypr_conn_handlers = [handler1, handler2]
            self._signal_handlers.extend([(conn, h) for h in [handler1, handler2]])
        
        timer_id = GLib.timeout_add(WINDOW_UPDATE_INTERVAL, self._update_window_display)
        self._window_update_timer = timer_id
        self._timers.append(timer_id)
        
        if self._theme_config['is_notch']:
            self.notch_revealer.set_reveal_child(True)
        else:
            self.notch_revealer.set_reveal_child(False)
    
    def _on_active_window_changed_debounced(self, conn, event):
        """Debounced version of active window change handler."""
        if self._debounce_timer:
            GLib.source_remove(self._debounce_timer)
        self._debounce_timer = GLib.timeout_add(DEBOUNCE_DELAY, self._on_active_window_changed, conn, event)
        return False
    
    def _on_workspace_changed_debounced(self, conn, event):
        """Debounced version of workspace change handler."""
        if self._debounce_timer:
            GLib.source_remove(self._debounce_timer)
        self._debounce_timer = GLib.timeout_add(DEBOUNCE_DELAY, self._on_workspace_changed, conn, event)
        return False

    def _on_active_window_changed(self, conn, event):
        GLib.idle_add(self._update_window_display)

    def _on_workspace_changed(self, conn, event):
        GLib.idle_add(self._update_window_display)

    def _finalize_initialization(self):
        self.show_all()
        GLib.idle_add(self._perform_post_show_optimizations)

    def _perform_post_show_optimizations(self):
        if hasattr(self, 'get_window'):
            window = self.get_window()
            if window:
                window.set_event_compression(True)

    def open_notch(self, widget_name: str):
        if not widget_name:
            return
            
        if self.monitor_manager and not self._handle_monitor_focus(widget_name):
            return
            
        self._open_notch_internal(widget_name)

    def toggle_notch(self, widget_name: str):
        if not widget_name:
            return
            
        if self._state['notch_open'] and self._state['current_widget'] == widget_name:
            self.close_notch()
            return
        
        if self.monitor_manager and not self._handle_monitor_focus(widget_name):
            return
            
        self._open_notch_internal(widget_name)

    def _open_notch_internal(self, widget_name: str):
        self.notch_revealer.set_reveal_child(True)
        self.notch_box.add_style_class("open")
        self.stack.add_style_class("open")
        self.set_keyboard_mode("exclusive")

        if widget_name == "network_applet":
            self._show_network_applet()
        elif widget_name == "bluetooth":
            self._show_bluetooth()
        elif widget_name == "dashboard":
            self._show_dashboard()
        elif widget_name == "wallpapers":
            self._show_wallpapers()
        elif widget_name == "mixer":
            self._show_mixer()
        elif widget_name == "cliphist":
            self._show_cliphist()
        elif widget_name == "launcher":
            self._show_launcher()
        elif widget_name == "overview":
            self._show_overview()
        elif widget_name == "power":
            self._show_power()
        elif widget_name == "tools":
            self._show_tools()
        else:
            self._show_dashboard_default()
        
        self._state['notch_open'] = True
        self._state['current_widget'] = widget_name
        
        self._manage_bar_visibility()

    def _show_network_applet(self):
        self.stack.set_visible_child(self.dashboard)
        self.dashboard.go_to_section("widgets")
        if hasattr(self, 'applet_stack') and hasattr(self, 'nwconnections'):
            self.applet_stack.set_visible_child(self.nwconnections)

    def _show_bluetooth(self):
        self.stack.set_visible_child(self.dashboard)
        self.dashboard.go_to_section("widgets")
        if hasattr(self, 'applet_stack') and hasattr(self, 'btdevices'):
            self.applet_stack.set_visible_child(self.btdevices)

    def _show_dashboard(self):
        self.stack.set_visible_child(self.dashboard)
        self.dashboard.go_to_section("widgets")
        if hasattr(self, 'applet_stack') and hasattr(self, 'nhistory'):
            self.applet_stack.set_visible_child(self.nhistory)

    def _show_wallpapers(self):
        self.stack.set_visible_child(self.dashboard)
        self.dashboard.go_to_section("wallpapers")

    def _show_mixer(self):
        self.stack.set_visible_child(self.dashboard)
        self.dashboard.go_to_section("mixer")

    def _show_cliphist(self):
        self.stack.set_visible_child(self.cliphist)
        GLib.idle_add(self.cliphist.open)

    def _show_launcher(self):
        self.stack.set_visible_child(self.launcher)
        self.launcher.open_launcher()
        entry = self.launcher.search_entry
        entry.set_text("")
        entry.grab_focus()

    def _show_overview(self):
        self.stack.set_visible_child(self.overview)

    def _show_power(self):
        self.stack.set_visible_child(self.power)

    def _show_tools(self):
        self.stack.set_visible_child(self.tools)

    def _show_dashboard_default(self):
        self.stack.set_visible_child(self.dashboard)
        self.dashboard.go_to_section("widgets")
        if hasattr(self, 'applet_stack') and hasattr(self, 'nhistory'):
            self.applet_stack.set_visible_child(self.nhistory)

    def close_notch(self):
        if not self._state['notch_open']:
            return
            
        try:
            if self.monitor_manager:
                self.monitor_manager.set_notch_state(self.monitor_id, False)
        except Exception:
            pass
            
        self.set_keyboard_mode("none")
        self.notch_box.remove_style_class("open")
        self.stack.remove_style_class("open")

        if self.bar:
            try:
                self.bar.revealer_right.set_reveal_child(True)
                self.bar.revealer_left.set_reveal_child(True)
            except Exception:
                pass
        
        if hasattr(self, 'applet_stack') and hasattr(self, 'nhistory'):
            self.applet_stack.set_visible_child(self.nhistory)
            
        self._state['notch_open'] = False
        self._state['current_widget'] = None
        self.stack.set_visible_child(self.compact)
        
        if not self._theme_config['is_notch']:
            self.notch_revealer.set_reveal_child(False)

    def _handle_monitor_focus(self, widget_name: str) -> bool:
        try:
            real_focus = self._get_real_focused_monitor_id()
            if real_focus is not None:
                self.monitor_manager._focused_monitor_id = real_focus
                    
            focused_id = self.monitor_manager.get_focused_monitor_id()
            if focused_id != self.monitor_id:
                self.close_notch()
                focused_notch = self.monitor_manager.get_instance(focused_id, 'notch')
                if focused_notch:
                    focused_notch._open_notch_internal(widget_name)
                return False
                
            self.monitor_manager.close_all_notches_except(self.monitor_id)
            self.monitor_manager.set_notch_state(self.monitor_id, True, widget_name)
            return True
        except Exception:
            return True

    def _get_real_focused_monitor_id(self) -> Optional[int]:
        """Get focused monitor ID with caching."""
        return self._cached_subprocess_call(
            "focused_monitor",
            ["hyprctl", "monitors", "-j"],
            self._parse_focused_monitor
        )
    
    def _parse_focused_monitor(self, stdout: str) -> Optional[int]:
        """Parse focused monitor from hyprctl output."""
        try:
            monitors = json.loads(stdout)
            for i, monitor in enumerate(monitors):
                if monitor.get('focused', False):
                    return i
        except (json.JSONDecodeError, Exception):
            pass
        return None
    
    def _cached_subprocess_call(self, cache_key: str, cmd: list, parser: callable = None) -> Any:
        """Execute subprocess with LRU caching."""
        import time
        current_time = time.time()

        # Check cache with TTL
        if cache_key in _subprocess_cache:
            cached_time = _cache_timestamps.get(cache_key, 0)
            if current_time - cached_time < CACHE_TTL:
                # Move to end (most recently used) for LRU
                _subprocess_cache.move_to_end(cache_key)
                return _subprocess_cache[cache_key]
            else:
                # TTL expired, remove from cache
                del _subprocess_cache[cache_key]
                del _cache_timestamps[cache_key]

        # Execute command
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT
            )
            if result.returncode == 0:
                value = parser(result.stdout) if parser else result.stdout

                # Add to cache with LRU eviction
                _subprocess_cache[cache_key] = value
                _cache_timestamps[cache_key] = current_time

                # Evict oldest entry if cache exceeds limit
                if len(_subprocess_cache) > MAX_SUBPROCESS_CACHE_SIZE:
                    oldest_key = next(iter(_subprocess_cache))
                    del _subprocess_cache[oldest_key]
                    _cache_timestamps.pop(oldest_key, None)

                return value
        except (subprocess.TimeoutExpired, Exception):
            pass

        return None

    def _manage_bar_visibility(self):
        if self.bar:
            is_panel_bottom = self._theme_config['is_panel'] and "Top"
            is_bottom_notch = self._theme_config['is_bottom'] and self._theme_config['is_notch']
            if not is_panel_bottom and not is_bottom_notch:
                self.bar.revealer_right.set_reveal_child(False)
                self.bar.revealer_left.set_reveal_child(False)

    def _on_active_window_click(self, widget, event):
        self.toggle_notch("dashboard")
        return True

    def _on_button_enter(self, widget, event):
        self._state['hovered'] = True
        if widget.get_window():
            cursor = Gdk.Cursor.new_for_display(
                Gdk.Display.get_default(), 
                Gdk.CursorType.HAND2
            )
            widget.get_window().set_cursor(cursor)
        return True

    def _on_button_leave(self, widget, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self._state['hovered'] = False
        if widget.get_window():
            widget.get_window().set_cursor(None)
        return True

    def _on_notch_hover_enter(self, widget, event):
        self._state['hovered'] = True
        return False

    def _on_notch_hover_leave(self, widget, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self._state['hovered'] = False
        return False

    def _on_compact_scroll(self, widget, event):
        if self._state['scrolling']:
            return True

        children = self.compact_stack.get_children()
        current_child = self.compact_stack.get_visible_child()
        if current_child in children:
            current_idx = children.index(current_child)
        else:
            current_idx = 0
        
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
            
        GLib.timeout_add(500, reset_scrolling)
        return True

    def _update_window_display(self, *args):
        try:
            workspace_id = self._get_current_workspace_id()
            window_class = self._get_current_window_class()
            title = self._get_current_window_title()
            
            self.workspace_label.set_label(f"Workspace {workspace_id}")
            
            # Упрощенная проверка - если есть window_class, пробуем показать иконку
            if window_class and window_class.strip():
                self._set_icon_from_hyprland()
                self.window_icon.show()
                self.active_window_container.set_spacing(8)
            else:
                # Нет активного окна - скрываем иконку
                self.window_icon.hide()
                self.active_window_container.set_spacing(0)
                
        except Exception:
            self._set_fallback_display()

    def _get_current_window_title(self) -> str:
        try:
            conn = get_hyprland_connection()
            if conn:
                window_data = json.loads(conn.send_command("j/activewindow").reply.decode())
                return window_data.get("title", "")
        except Exception:
            pass
        return ""

    def _get_current_workspace_id(self) -> int:
        try:
            conn = get_hyprland_connection()
            if conn:
                workspace_data = json.loads(conn.send_command("j/activeworkspace").reply.decode())
                return workspace_data.get("id", 1)
        except Exception:
            pass
        return 1

    def _set_icon_from_hyprland(self):
        try:
            conn = get_hyprland_connection()
            if not conn:
                return
                
            window_data = json.loads(conn.send_command("j/activewindow").reply.decode())
            app_id = window_data.get("initialClass") or window_data.get("class", "")
            
            # Минимальная проверка - только пустая строка
            if not app_id:
                return
                
            icon = self._get_icon_pixbuf(app_id)
            if icon:
                self.window_icon.set_from_pixbuf(icon)
            else:
                self._set_fallback_icon()
                
        except Exception:
            # При любой ошибке просто не меняем иконку
            pass

    def _get_icon_pixbuf(self, app_id: str):
        if not app_id:
            return None
            
        # LRU cache check
        if app_id in self._icon_cache:
            self._icon_cache.move_to_end(app_id)
            return self._icon_cache[app_id]
            
        icon = None
        
        app_key = app_id.lower()
        if app_key in self._app_identifiers_cache:
            app = self._app_identifiers_cache[app_key]
            icon = app.get_icon_pixbuf(size=20)

        if not icon:
            icon = self.icon_resolver.get_icon_pixbuf(app_id, 20)

        if not icon and "-" in app_id:
            first_part = app_id.split("-")[0]
            icon = self.icon_resolver.get_icon_pixbuf(first_part, 20)
        
        if icon:
            # Add to cache with LRU eviction
            self._icon_cache[app_id] = icon
            if len(self._icon_cache) > MAX_ICON_CACHE_SIZE:
                self._icon_cache.popitem(last=False)
            
        return icon

    def _set_fallback_icon(self):
        fallback_icons = ["application-x-executable", "application-x-executable-symbolic"]
        for icon_name in fallback_icons:
            try:
                self.window_icon.set_from_icon_name(icon_name, 20)
                break
            except Exception:
                continue

    def _set_fallback_display(self):
        workspace_id = self._get_current_workspace_id()
        self.workspace_label.set_label(f"Workspace {workspace_id}")
        self.window_icon.hide()  # Гарантируем, что иконка скрыта
        self.active_window_container.set_spacing(0)

    def _get_current_window_class(self) -> str:
        try:
            conn = get_hyprland_connection()
            if conn:
                data = json.loads(conn.send_command("j/activewindow").reply.decode())
                return data.get("initialClass") or data.get("class", "")
        except Exception:
            pass
        return ""

    def _on_key_press(self, widget, event):
        keyval = event.keyval
        
        if keyval == Gdk.KEY_Escape:
            self.close_notch()
            return True

        return False

    def toggle_hidden(self):
        self._state['hidden'] = not self._state['hidden']
        self.set_visible(not self._state['hidden'])

    def destroy(self):
        """Clean up all resources properly."""
        # Remove all timers
        if hasattr(self, '_timers'):
            for timer_id in self._timers:
                try:
                    GLib.source_remove(timer_id)
                except:
                    pass
            self._timers.clear()
        
        # Remove debounce timer
        if hasattr(self, '_debounce_timer') and self._debounce_timer:
            try:
                GLib.source_remove(self._debounce_timer)
            except:
                pass
            self._debounce_timer = None
        
        # Disconnect signal handlers
        if hasattr(self, '_signal_handlers'):
            for conn, handler_id in self._signal_handlers:
                try:
                    conn.disconnect(handler_id)
                except:
                    pass
            self._signal_handlers.clear()
        
        # Clear icon cache
        if hasattr(self, '_icon_cache'):
            self._icon_cache.clear()
        
        # Clear references
        self.monitor_manager = None
        self.icon_resolver = None
        self._bar_ref = None  # Clear weak reference
        
        # Clean up heavy widgets
        for widget_name in ['dashboard', 'launcher', 'overview', 'power', 'cliphist', 'tools']:
            if hasattr(self, widget_name):
                widget = getattr(self, widget_name)
                if hasattr(widget, 'destroy'):
                    try:
                        widget.destroy()
                    except:
                        pass
                setattr(self, widget_name, None)
        
        super().destroy()


class Bar(Window):
    def __init__(self, monitor_id: int = 0, **kwargs):
        self.monitor_id = monitor_id
        
        super().__init__(
            name="bar", 
            layer="top", 
            exclusivity="auto", 
            visible=True,
            all_visible=True, 
            monitor=monitor_id,
        )

        self._setup_appearance()
        
        # Use weak reference for notch to avoid circular reference
        self._notch_ref = None
        notch = kwargs.get("notch", None)
        if notch:
            self.notch = notch
        
        self._state = {
            'menu_visible': False,
            'hidden': False
        }
        
        # Track resources for cleanup
        self._signal_handlers = []
        
        self._init_components()
        self._setup_layout()
        self._apply_theme_and_styles()
    
    @property
    def notch(self):
        """Get notch instance from weak reference."""
        return self._notch_ref() if self._notch_ref else None
    
    @notch.setter
    def notch(self, value):
        """Set notch instance using weak reference."""
        self._notch_ref = weakref.ref(value) if value else None

    def _init_components(self):
        self._init_workspaces()
        self._init_language_widget()
        self._init_power_battery_widget()
        self._init_widgets()

    def _init_workspaces(self):
        start_ws = self.monitor_id * 9 + 1
        ws_range = range(start_ws, start_ws + 9)
        
        ws_common_params = {
            "invert_scroll": True,
            "empty_scroll": True,
            "v_align": "fill",
            "orientation": "h",
            "buttons_factory": None if True else Workspaces.default_buttons_factory
        }
        
        buttons = []
        for i in ws_range:
            button = WorkspaceButton(
                h_expand=False,
                v_expand=False,
                h_align="center",
                v_align="center",
                id=i,
                label=None,
            )
            buttons.append(button)
        
        self.workspaces = Workspaces(
            name="workspaces",
            spacing=8,
            buttons=buttons,
            **ws_common_params
        )

        num_buttons = []
        for i in ws_range:
            label_text = str(i)
                
            button = WorkspaceButton(
                h_expand=False,
                v_expand=False,
                h_align="center",
                v_align="center",
                id=i,
                label=label_text,
            )
            num_buttons.append(button)

        spacing_val = 0

        self.workspaces_num = Workspaces(
            name="workspaces-num",
            spacing=spacing_val,
            buttons=num_buttons,
            **ws_common_params
        )

        ws_child = self.workspaces

        self.ws_container = Box(
            name="workspaces-container",
            children=ws_child,
        )

    def _init_language_widget(self):
        self.lang_label = Label(name="lang-label")
        self.language_indicator = Box(
            name="language-indicator", 
            h_align="center", 
            v_align="center",
            children=[self.lang_label]
        )
        self.on_language_switch()
        
        conn = get_hyprland_connection()
        if conn:
            handler = conn.connect("event::activelayout", self.on_language_switch)
            self._signal_handlers.append((conn, handler))

    def _init_power_battery_widget(self):
        self.power_battery_container = Box(
            name="power-battery-container",
            orientation="h",
        )
        
        self._init_datetime_widget()
        self.power_battery_container.add(self.date_time)
        self.power_battery_container.add(self.language_indicator)
        
        self.battery = Battery()
        self.button_power = Button(
            name="button-bar", 
            tooltip_markup="<b>Меню питания</b>",
            on_clicked=self.power_menu,
            child=Label(name="button-bar-label", markup=icons.shutdown)
        )
        
        self.power_battery_container.add(self.battery)
        self.power_battery_container.add(self.button_power)
        
        def on_enter(w, e):
            cursor = Gdk.Cursor.new_from_name(w.get_display(), "hand2")
            w.get_window().set_cursor(cursor)
            
        def on_leave(w, e):
            w.get_window().set_cursor(None)
            
        self.button_power.connect("enter-notify-event", on_enter)
        self.button_power.connect("leave-notify-event", on_leave)

    def _init_widgets(self):
        self.systray = SystemTray()
        
        self.network_container = Box(name="network-container")
        self.network = NetworkApplet()
        self.network_container.add(self.network)
        
        self.control = ControlSmall()
        self.metrics = MetricsSmall()
        
        self._init_datetime_widget()
        self._setup_revealers()
        self.apply_component_props()

    def _init_datetime_widget(self):
        time_format = "%H:%M"

        self.date_time = DateTime(
            name="date-time",
            formatters=[time_format],
            h_align="center",
            v_align="center",
            h_expand=True,
            v_expand=True,
        )

    def _setup_revealers(self):
        left_widgets_box = Box(name="bar-revealer-box", orientation="h", spacing=4)
        
        left_widgets_box.add(self.systray)
        left_widgets_box.add(self.network_container)
        
        right_box = Box(name="bar-revealer-box", orientation="h", spacing=4)
        right_box.add(self.metrics)
        right_box.add(self.control)
        
        self.revealer_left = Revealer(
            name="bar-revealer",
            transition_type="slide-right",
            child_revealed=True,
            child=left_widgets_box,
        )
        
        self.revealer_right = Revealer(
            name="bar-revealer",
            transition_type="slide-left",
            child_revealed=True,
            child=right_box,
        )

        self.boxed_revealer_left = Box(name="boxed-revealer", children=[self.revealer_left])
        self.boxed_revealer_right = Box(name="boxed-revealer", children=[self.revealer_right])

    def _setup_appearance(self):
        self.set_anchor("left top right")
        self.set_margin("-4px -4px -8px -4px")
        self._load_css_colors()

    def _setup_layout(self):
        start_children = [self.ws_container, self.boxed_revealer_left]
        end_children = [self.boxed_revealer_right, self.power_battery_container]

        start_container = Box(name="start-container", spacing=4, children=start_children)
        end_container = Box(name="end-container", spacing=4, children=end_children)

        self.bar_inner = CenterBox(
            name="bar-inner", 
            orientation=Gtk.Orientation.HORIZONTAL,
            start_children=start_container,
            end_children=end_container
        )
        self.children = self.bar_inner

    def _apply_theme_and_styles(self):
        theme_classes = ["pills", "dense", "edge", "edgecenter"]
        for tc in theme_classes:
            self.bar_inner.remove_style_class(tc)

        current_style = "pills"
            
        self.bar_inner.add_style_class(current_style)
        self.bar_inner.add_style_class("top")

    def _load_css_colors(self):
        css_path = os.path.expanduser(f"~/.config/ax-shell/styles/colors.css")
        if os.path.exists(css_path):
            provider = Gtk.CssProvider()
            provider.load_from_path(css_path)
            screen = Gdk.Screen.get_default()
            self.get_style_context().add_provider_for_screen(
                screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def apply_component_props(self):
        self.component_visibility = {
            "button_apps": True,
            "systray": True,
            "control": True,
            "network": True,
            "button_tools": True,
            "sysprofiles": True,
            "button_overview": True,
            "ws_container": True,
            "battery": True,
            "metrics": True,
            "language": True,
            "date_time": True,
            "button_power": True,
        }

        components = {
            "systray": self.systray,
            "control": self.control,
            "ws_container": self.ws_container,
            "network": self.network,
            "network_container": self.network_container,
            "battery": self.battery, 
            "metrics": self.metrics, 
            "language_indicator": self.language_indicator, 
            "date_time": self.date_time,
            "button_power": self.button_power,
            "power_battery_container": self.power_battery_container,
            "revealer_left": self.revealer_left,
            "revealer_right": self.revealer_right
        }
        
        for name, widget in components.items():
            if name in self.component_visibility:
                visible = self.component_visibility[name]
                widget.set_visible(visible)

    def power_menu(self, *args):
        if self.notch: 
            self.notch.open_notch("power")

    @lru_cache(maxsize=10)
    def _get_short_lang(self, lang_data: str) -> str:
        """Get short language code with caching."""
        if len(lang_data) >= 3:
            return lang_data[:3].upper()
        return lang_data.upper()
    
    def on_language_switch(self, *args):
        """Handle language switch event."""
        if len(args) > 1:
            event = args[1]
            if event and event.data and len(event.data) > 1:
                lang_data = event.data[1]
            else:
                lang_data = Language().get_label()
        else:
            lang_data = Language().get_label()
        
        short_lang = self._get_short_lang(lang_data)
        self.lang_label.set_label(short_lang)

    def toggle_hidden(self):
        """Toggle bar visibility."""
        self._state['hidden'] = not self._state['hidden']
        if self._state['hidden']:
            self.bar_inner.add_style_class("hidden")
        else:
            self.bar_inner.remove_style_class("hidden")
    
    def destroy(self):
        """Clean up all resources."""
        # Disconnect signal handlers
        if hasattr(self, '_signal_handlers'):
            for conn, handler_id in self._signal_handlers:
                try:
                    conn.disconnect(handler_id)
                except:
                    pass
            self._signal_handlers.clear()
        
        # Clear references
        self._notch_ref = None
        
        # Clean up components
        for component_name in ['systray', 'network', 'control', 'metrics', 'battery']:
            if hasattr(self, component_name):
                component = getattr(self, component_name)
                if hasattr(component, 'cleanup'):
                    try:
                        component.cleanup()
                    except:
                        pass
                elif hasattr(component, 'destroy'):
                    try:
                        component.destroy()
                    except:
                        pass
                setattr(self, component_name, None)
        
        super().destroy()
