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

from modules.cliphist import ClipHistory
from modules.corners import MyCorner
from modules.dashboard import Dashboard
from modules.launcher import AppLauncher
from modules.overview import Overview
from modules.power import PowerMenu
from modules.tools import Toolbox
from utils.icon_resolver import IconResolver
from widgets.wayland import WaylandWindow as Window


class Notch(Window):
    def __init__(self, monitor_id=0, **kwargs):
        self.monitor_id = monitor_id
        self._bar_ref = None

        super().__init__(
            name="notch",
            layer="top",
            anchor=self._get_anchor(),
            margin="-40px 0px 0px 0px",
            keyboard_mode="none",
            exclusivity="none",
            visible=True,
            all_visible=True,
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
            'external_call': False
        }
        self._timers = []
        self._signal_handlers = []
        
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

    def _get_anchor(self):        
        if "Top" == "start": return "top start"
        elif "Top" == "end": return "top end"
        else: return "top"

    def _init_components(self):
        self.monitor_manager = None
        try:
            from utils.monitor_manager import get_monitor_manager
            self.monitor_manager = get_monitor_manager()
        except:
            pass
        
        self.icon_resolver = IconResolver()
        self._icon_cache = {}
        
        if not hasattr(Notch, '_shared_app_identifiers_cache'):
            Notch._shared_app_identifiers_cache = self._build_app_identifiers_cache()
        self._app_identifiers_cache = Notch._shared_app_identifiers_cache

    def _build_app_identifiers_cache(self):
        cache = {}
        try:
            apps = get_desktop_applications()
            for app in apps:
                identifiers = self._extract_app_identifiers(app)
                for identifier in identifiers:
                    if identifier and identifier.lower() not in cache:
                        cache[identifier.lower()] = app
        except:
            pass
        return cache

    def _extract_app_identifiers(self, app):
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
        self.dashboard = Dashboard(notch=self)
        self.launcher = AppLauncher(notch=self)
        self.overview = Overview(monitor_id=self.monitor_id)
        self.power = PowerMenu(notch=self)
        self.cliphist = ClipHistory(notch=self)
        self.tools = Toolbox(notch=self)

        if hasattr(self.dashboard, 'widgets'):
            self._cache_dashboard_widgets()

        self._setup_active_window()
        self._setup_main_stack()
        self._setup_layout()

    def _cache_dashboard_widgets(self):
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
            orientation="h",
            h_align="center",
            v_align="center",
            start_children=self.corner_left,
            center_children=self.stack,
            end_children=self.corner_right,
        )
        self.notch_box.add_style_class("notch")

        if "Notch":
            transition_type = "slide-down"
        else:
            transition_type = "slide-up"
        
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
            handler1 = conn.connect("event::activewindow", self._on_active_changed_debounced)
            handler2 = conn.connect("event::workspace", self._on_active_changed_debounced)
            self._hypr_conn_handlers = [handler1, handler2]
            self._signal_handlers.extend([(conn, h) for h in [handler1, handler2]])
        
        timer_id = GLib.timeout_add(500, self._update_window_display)
        self._window_update_timer = timer_id
        self._timers.append(timer_id)
        
        self.notch_revealer.set_reveal_child(True)
    
    def _on_active_changed_debounced(self, conn, event):
        if self._debounce_timer:
            GLib.source_remove(self._debounce_timer)
        self._debounce_timer = GLib.timeout_add(100, self._on_active_changed, conn, event)
        return False

    def _on_active_changed(self, conn, event):
        GLib.idle_add(self._update_window_display)

    def _finalize_initialization(self):
        self.show_all()
        GLib.idle_add(self._perform_post_show_optimizations)

    def _perform_post_show_optimizations(self):
        if hasattr(self, 'get_window'):
            window = self.get_window()
            if window:
                window.set_event_compression(True)

    def open_notch(self, widget_name):
        if not widget_name:
            return
            
        if self.monitor_manager and not self._handle_monitor_focus(widget_name):
            return
            
        self._open_notch_internal(widget_name)

    def toggle_notch(self, widget_name):
        if not widget_name:
            return
            
        if self._state['notch_open'] and self._state['current_widget'] == widget_name:
            self.close_notch()
            return
        
        if self.monitor_manager and not self._handle_monitor_focus(widget_name):
            return
            
        self._open_notch_internal(widget_name)

    def _open_notch_internal(self, widget_name):
        self.notch_revealer.set_reveal_child(True)
        self.notch_box.add_style_class("open")
        self.stack.add_style_class("open")
        self.set_keyboard_mode("exclusive")

        if widget_name == "network_applet": self._show_widgets('nwconnections')
        elif widget_name == "bluetooth": self._show_widgets('btdevices')
        elif widget_name == "dashboard": self._show_widgets('nhistory')
        elif widget_name == "wallpapers": self._show_otherboards("wallpapers")
        elif widget_name == "overview": self._show_other("overview")
        elif widget_name == "power": self._show_other("power")
        elif widget_name == "tools": self._show_other("tools")
        elif widget_name == "mixer": self._show_otherboards("mixer")
        elif widget_name == "cliphist": self._show_cliphist()
        elif widget_name == "launcher": self._show_launcher()
        
        self._state['notch_open'] = True
        self._state['current_widget'] = widget_name

    def _show_widgets(self, widgets):
        self.stack.set_visible_child(self.dashboard)
        self.dashboard.go_to_section("widgets")
        if hasattr(self, 'applet_stack') and hasattr(self, widgets):
            self.applet_stack.set_visible_child(eval(f"self.{widgets}"))

    def _show_otherboards(self, board):
        self.stack.set_visible_child(self.dashboard)
        self.dashboard.go_to_section(board)

    def _show_other(self, other):
        self.stack.set_visible_child(eval(f"self.{other}"))

    def _show_cliphist(self):
        self.stack.set_visible_child(self.cliphist)
        GLib.idle_add(self.cliphist.open)

    def _show_launcher(self):
        self.stack.set_visible_child(self.launcher)
        self.launcher.open_launcher()
        entry = self.launcher.search_entry
        entry.set_text("")
        entry.grab_focus()


    def close_notch(self):
        if not self._state['notch_open']:
            return
            
        try:
            if self.monitor_manager:
                self.monitor_manager.set_notch_state(self.monitor_id, False)
        except:
            pass
            
        self.set_keyboard_mode("none")
        self.notch_box.remove_style_class("open")
        self.stack.remove_style_class("open")

        if self.bar:
            try:
                self.bar.revealer_right.set_reveal_child(True)
                self.bar.revealer_left.set_reveal_child(True)
            except:
                pass
        
        if hasattr(self, 'applet_stack') and hasattr(self, 'nhistory'):
            self.applet_stack.set_visible_child(self.nhistory)
            
        self._state['notch_open'] = False
        self._state['current_widget'] = None
        self.stack.set_visible_child(self.compact)

    def _handle_monitor_focus(self, widget_name):
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
        except:
            return True

    _subprocess_cache = {}
    _cache_timestamps = {}

    def _get_real_focused_monitor_id(self):
        cache_key = "focused_monitor"
        current_time = time.time()

        if cache_key in self._subprocess_cache:
            cached_time = self._cache_timestamps.get(cache_key, 0)
            if current_time - cached_time < 2.0:
                return self._subprocess_cache[cache_key]
            else:
                del self._subprocess_cache[cache_key]
                del self._cache_timestamps[cache_key]

        try:
            result = subprocess.run(
                ["hyprctl", "monitors", "-j"],
                capture_output=True,
                text=True,
                timeout=0.3
            )
            if result.returncode == 0:
                monitors = json.loads(result.stdout)
                for i, monitor in enumerate(monitors):
                    if monitor.get('focused', False):
                        self._subprocess_cache[cache_key] = i
                        self._cache_timestamps[cache_key] = current_time
                        return i
        except:
            pass

        return None

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
            workspace_id, window_title = self._get_current_window_and_workspace()
            window_class = self._get_current_window_class()
            
            self.workspace_label.set_label(f"Workspace {workspace_id}")
            
            if window_class and window_class.strip():
                self._set_icon_from_hyprland()
                self.window_icon.show()
                self.active_window_container.set_spacing(8)
            else:
                self.window_icon.hide()
                self.active_window_container.set_spacing(0)
                
        except:
            self._set_fallback_display()

    def _get_current_window_and_workspace(self):
        try:
            if get_hyprland_connection():
                # Получаем данные активного окна
                window_data = json.loads(get_hyprland_connection().send_command("j/activewindow").reply.decode())
                window_title = window_data.get("title", "")
                
                # Получаем данные активного рабочего пространства
                workspace_data = json.loads(get_hyprland_connection().send_command("j/activeworkspace").reply.decode())
                workspace_id = workspace_data.get("id", 1)
                
                return workspace_id, window_title
        except Exception:
            pass
        return 1, ""

    def _set_icon_from_hyprland(self):
        try:
            if not get_hyprland_connection():
                return
                
            window_data = json.loads(get_hyprland_connection().send_command("j/activewindow").reply.decode())
            app_id = window_data.get("initialClass") or window_data.get("class", "")
            
            if not app_id:
                return
                
            icon = self._get_icon_pixbuf(app_id)
            if icon:
                self.window_icon.set_from_pixbuf(icon)
            else:
                self._set_fallback_icon()
                
        except:
            pass

    def _get_icon_pixbuf(self, app_id):
        if not app_id:
            return None
            
        if app_id in self._icon_cache:
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
            self._icon_cache[app_id] = icon
            if len(self._icon_cache) > 50:
                del self._icon_cache[next(iter(self._icon_cache))]
            
        return icon

    def _set_fallback_icon(self):
        fallback_icons = ["application-x-executable", "application-x-executable-symbolic"]
        for icon_name in fallback_icons:
            try:
                self.window_icon.set_from_icon_name(icon_name, 20)
                break
            except:
                continue

    def _set_fallback_display(self):
        workspace_id, _ = self._get_current_window_and_workspace()
        self.workspace_label.set_label(f"Workspace {workspace_id}")
        self.window_icon.hide()
        self.active_window_container.set_spacing(0)

    def _get_current_window_class(self):
        try:
            conn = get_hyprland_connection()
            if conn:
                data = json.loads(conn.send_command("j/activewindow").reply.decode())
                return data.get("initialClass") or data.get("class", "")
        except:
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
        if hasattr(self, '_timers'):
            for timer_id in self._timers:
                try:
                    GLib.source_remove(timer_id)
                except:
                    pass
            self._timers = []
        
        if hasattr(self, '_debounce_timer') and self._debounce_timer:
            try:
                GLib.source_remove(self._debounce_timer)
            except:
                pass
            self._debounce_timer = None
        
        if hasattr(self, '_signal_handlers'):
            for conn, handler_id in self._signal_handlers:
                try:
                    conn.disconnect(handler_id)
                except:
                    pass
            self._signal_handlers = []
        
        if hasattr(self, '_icon_cache'):
            self._icon_cache.clear()
        
        self.monitor_manager = None
        self.icon_resolver = None
        self._bar_ref = None
        
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