from fabric.utils.helpers import exec_shell_command_async
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label

from gi.repository import Gdk, GLib, Gtk

import subprocess
import time
import weakref
from typing import Optional, List, Callable, Dict
from dataclasses import dataclass

import modules.icons as icons
from modules.network import NetworkClient


# Конфигурация
PANEL_THEME = "Notch"
BAR_POSITION = "Top"
PANEL_POSITION = "Top"

# Performance constants
PROCESS_CHECK_CACHE_TTL = 1.0  # seconds
PROCESS_CHECK_TIMEOUT = 2.0  # seconds


@dataclass(frozen=True)
class WifiStrengthThresholds:
    """WiFi signal strength thresholds."""
    WEAK: int = 25
    FAIR: int = 50
    GOOD: int = 75


# Process check cache
_process_check_cache: Dict[str, tuple[bool, float]] = {}


def add_hover_cursor(widget: Gtk.Widget) -> None:
    """Add pointer cursor on hover for a widget."""
    widget.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
    
    def on_enter(w, event):
        window = w.get_window()
        if window:
            cursor = Gdk.Cursor.new_from_name(w.get_display(), "pointer")
            window.set_cursor(cursor)
    
    def on_leave(w, event):
        window = w.get_window()
        if window:
            window.set_cursor(None)
    
    widget.connect("enter-notify-event", on_enter)
    widget.connect("leave-notify-event", on_leave)


def check_process_running(pattern: str, use_cache: bool = True) -> bool:
    """Check if a process matching pattern is running with caching."""
    current_time = time.time()
    
    # Check cache
    if use_cache and pattern in _process_check_cache:
        result, timestamp = _process_check_cache[pattern]
        if current_time - timestamp < PROCESS_CHECK_CACHE_TTL:
            return result
    
    # Execute check
    try:
        result = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True,
            timeout=PROCESS_CHECK_TIMEOUT
        )
        is_running = result.returncode == 0
        
        # Update cache
        _process_check_cache[pattern] = (is_running, current_time)
        
        # Clean old cache entries
        if len(_process_check_cache) > 10:
            expired_keys = [
                k for k, (_, ts) in _process_check_cache.items()
                if current_time - ts > PROCESS_CHECK_CACHE_TTL * 2
            ]
            for key in expired_keys:
                _process_check_cache.pop(key, None)
        
        return is_running
    except (subprocess.TimeoutExpired, Exception):
        return False


def run_in_thread(func: Callable, callback: Optional[Callable] = None) -> None:
    """Run function in a separate thread with optional callback."""
    def worker(user_data):
        result = func()
        if callback:
            GLib.idle_add(callback, result)
    
    GLib.Thread.new(None, worker, None)


class StyleManager:
    """Mixin-like helper for managing widget styles."""
    
    def __init__(self, widgets: List[Gtk.Widget]):
        self._styled_widgets = widgets
    
    def set_disabled(self, disabled: bool) -> None:
        """Set disabled state for all tracked widgets."""
        method = "add_style_class" if disabled else "remove_style_class"
        for widget in self._styled_widgets:
            if hasattr(widget, method):
                getattr(widget, method)("disabled")


class ButtonContentBuilder:
    """Builder for creating consistent button content layouts."""
    
    @staticmethod
    def create_labeled_box(label_widget: Label) -> Box:
        """Create a box with label aligned to start."""
        return Box(children=[label_widget, Box(h_expand=True)])
    
    @staticmethod
    def create_status_layout(
        icon: Label,
        title_label: Label,
        status_label: Label,
        spacing: int = 10
    ) -> Box:
        """Create standard status button layout."""
        title_box = ButtonContentBuilder.create_labeled_box(title_label)
        status_box = ButtonContentBuilder.create_labeled_box(status_label)
        
        text_box = Box(
            orientation="v",
            h_align="start",
            v_align="center",
            children=[title_box, status_box],
        )
        
        return Box(
            h_align="start",
            v_align="center",
            spacing=spacing,
            children=[icon, text_box],
        )


class NetworkButton(Box):
    """Network status and control button."""
    
    WIFI_ICONS = (icons.wifi_0, icons.wifi_1, icons.wifi_2, icons.wifi_3)
    ANIMATION_ICONS = (
        icons.wifi_0, icons.wifi_1, icons.wifi_2,
        icons.wifi_3, icons.wifi_2, icons.wifi_1
    )
    ANIMATION_INTERVAL = 500
    THRESHOLDS = WifiStrengthThresholds()
    UPDATE_DEBOUNCE = 100  # ms
    
    def __init__(self, **kwargs):
        # Use weak references to avoid circular references
        widgets = kwargs.pop("widgets")
        self._widgets_ref = weakref.ref(widgets) if widgets else None
        
        notch = kwargs.pop("notch", None)
        self._notch_ref = weakref.ref(notch) if notch else None
        
        super().__init__(
            name="network-button",
            orientation="h",
            h_align="fill",
            v_align="fill",
            h_expand=True,
            v_expand=True,
            spacing=0,
        )
        
        self._network_client = NetworkClient()
        self._animation_timeout_id: Optional[int] = None
        self._update_timeout_id: Optional[int] = None
        self._animation_step = 0
        self._style_manager: Optional[StyleManager] = None
        self._signal_handlers = []
        
        self._create_ui()
        self._connect_signals()
        self._schedule_update()
    
    @property
    def _widgets_instance(self):
        return self._widgets_ref() if self._widgets_ref else None
    
    @property
    def _notch_instance(self):
        return self._notch_ref() if self._notch_ref else None
    
    def _schedule_update(self):
        """Schedule debounced update."""
        if self._update_timeout_id:
            GLib.source_remove(self._update_timeout_id)
        self._update_timeout_id = GLib.timeout_add(self.UPDATE_DEBOUNCE, self._do_update)
    
    def _do_update(self):
        """Perform actual update."""
        self._update_timeout_id = None
        self.update_state()
        return False

    def _create_ui(self) -> None:
        """Create UI components."""
        self.network_icon = Label(name="network-icon")
        self.network_label = Label(name="network-label", label="Wi-Fi", justification="left")
        self.network_ssid = Label(name="network-ssid", justification="left")
        
        content = ButtonContentBuilder.create_status_layout(
            self.network_icon,
            self.network_label,
            self.network_ssid
        )
        
        self.network_status_button = Button(
            name="network-status-button",
            h_expand=True,
            child=content,
            on_clicked=self._on_status_clicked,
        )
        add_hover_cursor(self.network_status_button)
        
        self.network_menu_label = Label(name="network-menu-label", markup=icons.chevron_right)
        self.network_menu_button = Button(
            name="network-menu-button",
            child=self.network_menu_label,
            on_clicked=self._on_menu_clicked,
        )
        add_hover_cursor(self.network_menu_button)
        
        self.add(self.network_status_button)
        self.add(self.network_menu_button)
        
        # Initialize style manager
        self._style_manager = StyleManager([
            self, self.network_icon, self.network_label,
            self.network_ssid, self.network_status_button,
            self.network_menu_button, self.network_menu_label
        ])

    def _connect_signals(self) -> None:
        """Connect signal handlers."""
        handler = self._network_client.connect('device-ready', self._on_wifi_ready)
        self._signal_handlers.append((self._network_client, handler))

    def _on_status_clicked(self, *args) -> None:
        """Toggle WiFi state."""
        wifi = self._network_client.wifi_device
        if wifi:
            wifi.toggle_wifi()

    def _on_menu_clicked(self, *args) -> None:
        """Open network menu."""
        notch = self._notch_instance
        if notch:
            notch.open_notch("network_applet")
        else:
            widgets = self._widgets_instance
            if widgets and hasattr(widgets, 'show_network_applet'):
                widgets.show_network_applet()

    def _on_wifi_ready(self, *args) -> None:
        """Handle WiFi device ready."""
        wifi = self._network_client.wifi_device
        if wifi:
            handler1 = wifi.connect('notify::enabled', lambda *_: self._schedule_update())
            handler2 = wifi.connect('notify::ssid', lambda *_: self._schedule_update())
            self._signal_handlers.extend([(wifi, handler1), (wifi, handler2)])
            self._schedule_update()

    def _get_wifi_icon(self, strength: int) -> str:
        """Get WiFi icon based on signal strength."""
        if strength < self.THRESHOLDS.WEAK:
            return self.WIFI_ICONS[0]
        elif strength < self.THRESHOLDS.FAIR:
            return self.WIFI_ICONS[1]
        elif strength < self.THRESHOLDS.GOOD:
            return self.WIFI_ICONS[2]
        return self.WIFI_ICONS[3]

    def _start_animation(self) -> None:
        """Start searching animation."""
        if self._animation_timeout_id is None:
            self._animation_step = 0
            self._animation_timeout_id = GLib.timeout_add(
                self.ANIMATION_INTERVAL,
                self._animate_searching
            )

    def _stop_animation(self) -> None:
        """Stop searching animation."""
        if self._animation_timeout_id is not None:
            GLib.source_remove(self._animation_timeout_id)
            self._animation_timeout_id = None

    def _animate_searching(self) -> bool:
        """Animation tick for searching state."""
        wifi = self._network_client.wifi_device
        
        if not wifi or not wifi.enabled:
            self._stop_animation()
            return False
        
        if wifi.state == "activated" and wifi.ssid != "Отключено":
            self._stop_animation()
            return False
        
        icon = self.ANIMATION_ICONS[self._animation_step]
        GLib.idle_add(self.network_icon.set_markup, icon)
        self._animation_step = (self._animation_step + 1) % len(self.ANIMATION_ICONS)
        return True

    def update_state(self, *args) -> None:
        """Update network button state."""
        wifi = self._network_client.wifi_device
        ethernet = self._network_client.ethernet_device
        
        # WiFi disabled
        if wifi and not wifi.enabled:
            self._stop_animation()
            self.network_icon.set_markup(icons.wifi_off)
            self.network_ssid.set_label("Выключено")
            if self._style_manager:
                self._style_manager.set_disabled(True)
            return
        
        if self._style_manager:
            self._style_manager.set_disabled(False)
        
        # Check primary device
        primary = getattr(self._network_client, 'primary_device', 'wireless')
        
        if primary == "wired":
            self._stop_animation()
            icon = icons.world if (ethernet and ethernet.internet == "activated") else icons.world_off
            self.network_icon.set_markup(icon)
            return
        
        # WiFi state
        if not wifi:
            self._stop_animation()
            self.network_icon.set_markup(icons.wifi_off)
            return
        
        if wifi.state == "activated" and wifi.ssid != "Отключено":
            self._stop_animation()
            self.network_ssid.set_label(wifi.ssid)
            self.network_icon.set_markup(self._get_wifi_icon(wifi.strength))
        else:
            self.network_ssid.set_label("Включено")
            self._start_animation()

    def cleanup(self) -> None:
        """Cleanup resources."""
        # Stop animations
        self._stop_animation()
        
        # Remove update timer
        if self._update_timeout_id:
            GLib.source_remove(self._update_timeout_id)
            self._update_timeout_id = None
        
        # Disconnect signal handlers
        for obj, handler_id in self._signal_handlers:
            try:
                obj.disconnect(handler_id)
            except:
                pass
        self._signal_handlers.clear()
        
        # Clear references
        self._widgets_ref = None
        self._notch_ref = None
        self._network_client = None
        self._style_manager = None


class BluetoothButton(Box):
    """Bluetooth status and control button."""
    
    def __init__(self, **kwargs):
        self._widgets = kwargs.pop("widgets")
        self._notch_instance = kwargs.pop("notch", None)
        
        super().__init__(
            name="bluetooth-button",
            orientation="h",
            h_align="fill",
            v_align="fill",
            h_expand=True,
            v_expand=True,
        )
        
        self._create_ui()

    def _create_ui(self) -> None:
        """Create UI components."""
        self.bluetooth_icon = Label(name="bluetooth-icon", markup=icons.bluetooth)
        self.bluetooth_label = Label(name="bluetooth-label", label="Bluetooth", justification="left")
        self.bluetooth_status_text = Label(name="bluetooth-status", label="Выключено", justification="left")
        
        content = ButtonContentBuilder.create_status_layout(
            self.bluetooth_icon,
            self.bluetooth_label,
            self.bluetooth_status_text
        )
        
        self.bluetooth_status_button = Button(
            name="bluetooth-status-button",
            h_expand=True,
            child=content,
            on_clicked=self._on_status_clicked,
        )
        add_hover_cursor(self.bluetooth_status_button)
        
        self.bluetooth_menu_label = Label(name="bluetooth-menu-label", markup=icons.chevron_right)
        self.bluetooth_menu_button = Button(
            name="bluetooth-menu-button",
            child=self.bluetooth_menu_label,
            on_clicked=self._on_menu_clicked,
        )
        add_hover_cursor(self.bluetooth_menu_button)
        
        self.add(self.bluetooth_status_button)
        self.add(self.bluetooth_menu_button)

    def _on_status_clicked(self, *args) -> None:
        """Toggle Bluetooth power."""
        self._widgets.bluetooth.client.toggle_power()

    def _on_menu_clicked(self, *args) -> None:
        """Open Bluetooth menu."""
        if self._notch_instance:
            self._notch_instance.open_notch("bluetooth")
        elif hasattr(self._widgets, 'show_bt'):
            self._widgets.show_bt()


class ToggleServiceButton(Button):
    """Base class for service toggle buttons."""
    
    # Override in subclasses
    PROCESS_PATTERN: str = ""
    START_COMMAND: str = ""
    STOP_COMMAND: str = ""
    BUTTON_NAME: str = ""
    ICON: str = ""
    LABEL_TEXT: str = ""
    ENABLED_TEXT: str = "Включено"
    DISABLED_TEXT: str = "Выключено"
    
    def __init__(self):
        self._icon_label = Label(name=f"{self.BUTTON_NAME}-icon", markup=self.ICON)
        self._title_label = Label(name=f"{self.BUTTON_NAME}-label", label=self.LABEL_TEXT, justification="left")
        self._status_label = Label(name=f"{self.BUTTON_NAME}-status", label=self.DISABLED_TEXT, justification="left")
        
        content = ButtonContentBuilder.create_status_layout(
            self._icon_label,
            self._title_label,
            self._status_label
        )
        
        super().__init__(
            name=f"{self.BUTTON_NAME}-button",
            h_expand=True,
            child=content,
            on_clicked=self._on_clicked,
        )
        
        add_hover_cursor(self)
        
        self._style_manager = StyleManager([
            self, self._icon_label, self._title_label, self._status_label
        ])
        
        self._check_state()

    def _on_clicked(self, *args) -> None:
        """Handle button click."""
        run_in_thread(self._toggle_service, self._update_ui)

    def _toggle_service(self) -> bool:
        """Toggle service state. Returns True if now enabled."""
        is_running = check_process_running(self.PROCESS_PATTERN)
        
        if is_running:
            exec_shell_command_async(self.STOP_COMMAND)
            return False
        else:
            exec_shell_command_async(self.START_COMMAND)
            return True

    def _check_state(self) -> None:
        """Check current service state."""
        run_in_thread(
            lambda: check_process_running(self.PROCESS_PATTERN),
            self._update_ui
        )

    def _update_ui(self, is_enabled: bool) -> bool:
        """Update UI based on service state."""
        self._status_label.set_label(self.ENABLED_TEXT if is_enabled else self.DISABLED_TEXT)
        self._style_manager.set_disabled(not is_enabled)
        return False

    def update_state(self, *args) -> None:
        """Public method to refresh state."""
        self._check_state()


class NightModeButton(ToggleServiceButton):
    """Night mode (hyprsunset) toggle button."""
    
    PROCESS_PATTERN = "hyprsunset"
    START_COMMAND = "hyprsunset -t 3500"
    STOP_COMMAND = "pkill hyprsunset"
    BUTTON_NAME = "night-mode"
    ICON = icons.night
    LABEL_TEXT = "Ночной режим"


class CaffeineButton(ToggleServiceButton):
    """Caffeine (screen wake lock) toggle button."""
    
    PROCESS_PATTERN = "ax-inhibit"
    START_COMMAND = "python ~/.config/Ax-Shell/scripts/inhibit.py"
    STOP_COMMAND = "pkill -f ax-inhibit"
    BUTTON_NAME = "caffeine"
    ICON = icons.coffee
    LABEL_TEXT = "Caffeine"
    
    def __init__(self):
        self._inhibit_process: Optional[subprocess.Popen] = None
        super().__init__()

    def _toggle_service(self) -> bool:
        """Toggle caffeine with process management."""
        try:
            # Check if our managed process is running
            if self._inhibit_process and self._inhibit_process.poll() is None:
                self._inhibit_process.terminate()
                try:
                    self._inhibit_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._inhibit_process.kill()
                self._inhibit_process = None
                return False
            
            # Check if process is running externally
            if check_process_running(self.PROCESS_PATTERN):
                exec_shell_command_async(self.STOP_COMMAND)
                return False
            
            # Start new process
            self._inhibit_process = subprocess.Popen(
                self.START_COMMAND,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return True
            
        except Exception as e:
            print(f"Caffeine toggle error: {e}")
            return False

    def toggle_external(self, notify: bool = True) -> None:
        """Toggle with optional notification (for external calls)."""
        def _toggle_and_notify() -> bool:
            is_enabled = self._toggle_service()
            
            if notify:
                status = "Включено ☀️" if is_enabled else "Выключено 💤"
                exec_shell_command_async(
                    f"notify-send '☕ Caffeine' '{status}' -a 'Ax-Shell' -e"
                )
            
            return is_enabled
        
        run_in_thread(_toggle_and_notify, self._update_ui)

    def cleanup(self) -> None:
        """Cleanup managed process."""
        if self._inhibit_process and self._inhibit_process.poll() is None:
            self._inhibit_process.terminate()
            try:
                self._inhibit_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._inhibit_process.kill()
            self._inhibit_process = None


class Buttons(Gtk.Grid):
    """Main buttons grid container."""
    
    def __init__(self, **kwargs):
        super().__init__(name="buttons-grid")
        
        self._widgets = kwargs.pop("widgets")
        self._notch_instance = kwargs.pop("notch", None)
        
        self._configure_grid()
        self._create_buttons()
        self._layout_buttons()
        self.show_all()

    def _configure_grid(self) -> None:
        """Configure grid properties."""
        self.set_row_homogeneous(True)
        self.set_column_homogeneous(True)
        self.set_row_spacing(4)
        self.set_column_spacing(4)
        self.set_vexpand(False)

    def _create_buttons(self) -> None:
        """Create all button instances."""
        self.network_button = NetworkButton(
            widgets=self._widgets,
            notch=self._notch_instance
        )
        self.bluetooth_button = BluetoothButton(
            widgets=self._widgets,
            notch=self._notch_instance
        )
        self.night_mode_button = NightModeButton()
        self.caffeine_button = CaffeineButton()

    def _layout_buttons(self) -> None:
        """Layout buttons based on panel configuration."""
        if self._is_vertical_layout():
            # 2x2 grid for vertical panels
            self.attach(self.network_button, 0, 0, 1, 1)
            self.attach(self.bluetooth_button, 1, 0, 1, 1)
            self.attach(self.night_mode_button, 0, 1, 1, 1)
            self.attach(self.caffeine_button, 1, 1, 1, 1)
        else:
            # 1x4 horizontal row
            self.attach(self.network_button, 0, 0, 1, 1)
            self.attach(self.bluetooth_button, 1, 0, 1, 1)
            self.attach(self.night_mode_button, 2, 0, 1, 1)
            self.attach(self.caffeine_button, 3, 0, 1, 1)

    @staticmethod
    def _is_vertical_layout() -> bool:
        """Check if vertical layout should be used."""
        return (
            PANEL_THEME == "Panel" and
            (BAR_POSITION in ("Left", "Right") or PANEL_POSITION in ("Start", "End"))
        )

    def refresh_all_states(self) -> None:
        """Refresh state of all buttons."""
        self.network_button.update_state()
        self.night_mode_button.update_state()
        self.caffeine_button.update_state()

    def cleanup(self) -> None:
        """Cleanup all button resources."""
        if hasattr(self.network_button, 'cleanup'):
            self.network_button.cleanup()
        if hasattr(self.caffeine_button, 'cleanup'):
            self.caffeine_button.cleanup()

    def __del__(self):
        """Destructor."""
        try:
            self.cleanup()
        except Exception:
            pass
