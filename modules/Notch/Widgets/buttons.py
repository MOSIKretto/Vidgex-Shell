# buttons.py

from fabric.utils.helpers import exec_shell_command_async
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label

from gi.repository import Gdk, GLib, Gtk

import subprocess
import weakref
from typing import Optional, List, Callable
from dataclasses import dataclass

import modules.icons as icons
from modules.Notch.Widgets.Network.network import NetworkClient


@dataclass(frozen=True)
class WifiStrengthThresholds:
    WEAK: int = 25
    FAIR: int = 50
    GOOD: int = 75


def add_hover_cursor(widget: Gtk.Widget) -> None:
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


class ProcessMonitor:
    """Мониторинг процесса без кэша"""
    def __init__(self, pattern: str, timeout: float = 2.0):
        self.pattern = pattern
        self.timeout = timeout
        self._is_running: Optional[bool] = None
    
    def check(self) -> bool:
        try:
            result = subprocess.run(
                ["pgrep", "-f", self.pattern],
                capture_output=True,
                timeout=self.timeout
            )
            self._is_running = result.returncode == 0
        except (subprocess.TimeoutExpired, Exception):
            self._is_running = False
        return self._is_running
    
    @property
    def is_running(self) -> bool:
        return self._is_running if self._is_running is not None else self.check()


def run_in_thread(func: Callable, callback: Optional[Callable] = None) -> None:
    def worker(user_data):
        result = func()
        if callback:
            GLib.idle_add(callback, result)
    
    GLib.Thread.new(None, worker, None)


class StyleManager:
    def __init__(self, widgets: List[Gtk.Widget]):
        self._styled_widgets = widgets
    
    def set_disabled(self, disabled: bool) -> None:
        method = "add_style_class" if disabled else "remove_style_class"
        for widget in self._styled_widgets:
            if hasattr(widget, method):
                getattr(widget, method)("disabled")


class ButtonContentBuilder:
    @staticmethod
    def create_labeled_box(label_widget: Label) -> Box:
        return Box(children=[label_widget, Box(h_expand=True)])
    
    @staticmethod
    def create_status_layout(
        icon: Label,
        title_label: Label,
        status_label: Label,
        spacing: int = 10
    ) -> Box:
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


# ================== NetworkButton ==================

class NetworkButton(Box):
    WIFI_ICONS = (icons.wifi_0, icons.wifi_1, icons.wifi_2, icons.wifi_3)
    ANIMATION_ICONS = (icons.wifi_0, icons.wifi_1, icons.wifi_2, icons.wifi_3, icons.wifi_2, icons.wifi_1)
    ANIMATION_INTERVAL = 500
    THRESHOLDS = WifiStrengthThresholds()
    UPDATE_DEBOUNCE = 100
    
    def __init__(self, **kwargs):
        widgets = kwargs.pop("widgets", None)
        self._widgets_ref = weakref.ref(widgets) if widgets else None
        notch = kwargs.pop("notch", None)
        self._notch_ref = weakref.ref(notch) if notch else None
        
        super().__init__()
        
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
        if self._update_timeout_id:
            GLib.source_remove(self._update_timeout_id)
        self._update_timeout_id = GLib.timeout_add(self.UPDATE_DEBOUNCE, self._do_update)
    
    def _do_update(self):
        self._update_timeout_id = None
        self.update_state()
        return False

    def _create_ui(self):
        self.network_icon = Label(name="network-icon")
        self.network_label = Label(name="network-label", label="Wi-Fi", justification="left")
        self.network_ssid = Label(name="network-ssid", justification="left")
        
        content = ButtonContentBuilder.create_status_layout(
            self.network_icon, self.network_label, self.network_ssid
        )
        self.network_status_button = Button(
            name="network-status-button",
            h_expand=True,
            child=content,
            on_clicked=self._on_status_clicked
        )
        add_hover_cursor(self.network_status_button)
        
        self.network_menu_label = Label(name="network-menu-label", markup=icons.chevron_right)
        self.network_menu_button = Button(
            name="network-menu-button",
            child=self.network_menu_label,
            on_clicked=self._on_menu_clicked
        )
        add_hover_cursor(self.network_menu_button)
        
        self.add(self.network_status_button)
        self.add(self.network_menu_button)
        
        self._style_manager = StyleManager([
            self, self.network_icon, self.network_label,
            self.network_ssid, self.network_status_button,
            self.network_menu_button, self.network_menu_label
        ])

    def _connect_signals(self):
        handler = self._network_client.connect('device-ready', self._on_wifi_ready)
        self._signal_handlers.append((self._network_client, handler))

    def _on_status_clicked(self, *args):
        wifi = self._network_client.wifi_device
        if wifi:
            wifi.toggle_wifi()

    def _on_menu_clicked(self, *args):
        notch = self._notch_instance
        if notch:
            notch.open_notch("network_applet")
        else:
            widgets = self._widgets_instance
            if widgets and hasattr(widgets, 'show_network_applet'):
                widgets.show_network_applet()

    def _on_wifi_ready(self, *args):
        wifi = self._network_client.wifi_device
        if wifi:
            handler1 = wifi.connect('notify::enabled', lambda *_: self._schedule_update())
            handler2 = wifi.connect('notify::ssid', lambda *_: self._schedule_update())
            self._signal_handlers.extend([(wifi, handler1), (wifi, handler2)])
            self._schedule_update()

    def _get_wifi_icon(self, strength: int) -> str:
        if strength < self.THRESHOLDS.WEAK:
            return self.WIFI_ICONS[0]
        elif strength < self.THRESHOLDS.FAIR:
            return self.WIFI_ICONS[1]
        elif strength < self.THRESHOLDS.GOOD:
            return self.WIFI_ICONS[2]
        return self.WIFI_ICONS[3]

    def _start_animation(self):
        if self._animation_timeout_id is None:
            self._animation_step = 0
            self._animation_timeout_id = GLib.timeout_add(
                self.ANIMATION_INTERVAL,
                self._animate_searching
            )

    def _stop_animation(self):
        if self._animation_timeout_id is not None:
            GLib.source_remove(self._animation_timeout_id)
            self._animation_timeout_id = None

    def _animate_searching(self) -> bool:
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

    def update_state(self):
        wifi = self._network_client.wifi_device
        ethernet = self._network_client.ethernet_device
        
        if wifi and not wifi.enabled:
            self._stop_animation()
            self.network_icon.set_markup(icons.wifi_off)
            self.network_ssid.set_label("Выключено")
            if self._style_manager:
                self._style_manager.set_disabled(True)
            return
        
        if self._style_manager:
            self._style_manager.set_disabled(False)
        
        primary = getattr(self._network_client, 'primary_device', 'wireless')
        if primary == "wired":
            self._stop_animation()
            icon = icons.world if (ethernet and ethernet.internet == "activated") else icons.world_off
            self.network_icon.set_markup(icon)
            return
        
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

    def cleanup(self):
        self._stop_animation()
        for obj, handler_id in self._signal_handlers:
            try: obj.disconnect(handler_id)
            except: pass
        self._signal_handlers.clear()
        self._widgets_ref = None
        self._notch_ref = None
        self._network_client = None
        self._style_manager = None


# ================== BluetoothButton ==================

class BluetoothButton(Box):
    def __init__(self, **kwargs):
        self._widgets = kwargs.pop("widgets", None)
        self._notch_instance = kwargs.pop("notch", None)
        super().__init__()
        
        self._is_bluetooth_enabled = False
        self._create_ui()
        self._setup_signals()
        self.update_state()
    
    def _setup_signals(self):
        if hasattr(self._widgets, 'bluetooth'):
            bt_client = self._widgets.bluetooth
            if hasattr(bt_client, 'client'):
                try:
                    if hasattr(bt_client.client, 'connect'):
                        bt_client.client.connect('power-changed', self._on_power_changed)
                        bt_client.client.connect('device-added', self._on_device_changed)
                        bt_client.client.connect('device-removed', self._on_device_changed)
                except Exception:
                    pass
    
    def _get_bluetooth_power_state(self) -> bool:
        try:
            monitor = ProcessMonitor("bluetoothd")
            running = monitor.check()
            if running:
                result = subprocess.run(
                    ["bluetoothctl", "show"], capture_output=True, text=True, timeout=2
                )
                if result.returncode == 0:
                    for line in result.stdout.splitlines():
                        if "Powered:" in line:
                            return "yes" in line
            result = subprocess.run(["rfkill", "list", "bluetooth"], capture_output=True, text=True, timeout=2)
            if result.returncode == 0:
                return "unblocked" in result.stdout.lower()
        except Exception:
            return False
        return False
    
    def _toggle_bluetooth_power(self) -> bool:
        try:
            monitor = ProcessMonitor("bluetoothd")
            is_running = monitor.check()
            if is_running:
                current_state = self._get_bluetooth_power_state()
                new_state = "off" if current_state else "on"
                subprocess.run(["bluetoothctl", "power", new_state], capture_output=True, timeout=3)
                return not current_state
            current_state = self._get_bluetooth_power_state()
            action = "unblock" if not current_state else "block"
            subprocess.run(["rfkill", action, "bluetooth"], capture_output=True, timeout=3)
            return not current_state
        except Exception:
            return self._is_bluetooth_enabled
    
    def _create_ui(self):
        self.bluetooth_icon = Label(name="bluetooth-icon")
        self.bluetooth_label = Label(name="bluetooth-label", label="Bluetooth", justification="left")
        self.bluetooth_status_text = Label(name="bluetooth-status", justification="left")
        
        content = ButtonContentBuilder.create_status_layout(
            self.bluetooth_icon,
            self.bluetooth_label,
            self.bluetooth_status_text
        )
        self.bluetooth_status_button = Button(
            name="bluetooth-status-button",
            h_expand=True,
            child=content,
            on_clicked=lambda *_: run_in_thread(self._toggle_bluetooth_power, self._update_ui),
        )
        add_hover_cursor(self.bluetooth_status_button)
        
        self.bluetooth_menu_label = Label(name="bluetooth-menu-label", markup=icons.chevron_right)
        self.bluetooth_menu_button = Button(
            name="bluetooth-menu-button",
            child=self.bluetooth_menu_label,
            on_clicked=lambda *_: self._open_bt_menu(),
        )
        add_hover_cursor(self.bluetooth_menu_button)
        
        self.add(self.bluetooth_status_button)
        self.add(self.bluetooth_menu_button)
    
    def _update_ui(self, is_enabled: Optional[bool] = None):
        if is_enabled is None:
            is_enabled = self._get_bluetooth_power_state()
        self._is_bluetooth_enabled = is_enabled
        self.bluetooth_icon.set_markup(icons.bluetooth if is_enabled else icons.bluetooth_off)
        self.bluetooth_status_text.set_label("Включено" if is_enabled else "Выключено")
    
    def _on_power_changed(self, client, power_state):
        self._update_ui(power_state)
    
    def _on_device_changed(self, *args):
        self.update_state()
    
    def update_state(self):
        run_in_thread(self._get_bluetooth_power_state, self._update_ui)
    
    def _open_bt_menu(self):
        if self._notch_instance:
            self._notch_instance.open_notch("bluetooth")
        elif hasattr(self._widgets, 'show_bt'):
            self._widgets.show_bt()
        else:
            try: subprocess.Popen(["blueman-manager"])
            except Exception: pass


# ================== ToggleServiceButton, NightModeButton, CaffeineButton ==================

class ToggleServiceButton(Button):
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
            self._icon_label, self._title_label, self._status_label
        )
        
        super().__init__(
            name=f"{self.BUTTON_NAME}-button",
            h_expand=True,
            child=content,
            on_clicked=self._on_clicked,
        )
        
        add_hover_cursor(self)
        
        self._style_manager = StyleManager([self, self._icon_label, self._title_label, self._status_label])
        self.update_state()

    def _on_clicked(self, *args):
        run_in_thread(self._toggle_service, self._update_ui)

    def _toggle_service(self) -> bool:
        monitor = ProcessMonitor(self.PROCESS_PATTERN)
        is_running = monitor.check()
        if is_running:
            exec_shell_command_async(self.STOP_COMMAND)
            return False
        else:
            exec_shell_command_async(self.START_COMMAND)
            return True

    def update_state(self, *args):
        monitor = ProcessMonitor(self.PROCESS_PATTERN)
        run_in_thread(lambda: monitor.check(), self._update_ui)

    def _update_ui(self, is_enabled: bool):
        self._status_label.set_label(self.ENABLED_TEXT if is_enabled else self.DISABLED_TEXT)
        self._style_manager.set_disabled(not is_enabled)
        return False


class NightModeButton(ToggleServiceButton):
    PROCESS_PATTERN = "hyprsunset"
    START_COMMAND = "hyprsunset -t 3500"
    STOP_COMMAND = "pkill hyprsunset"
    BUTTON_NAME = "night-mode"
    ICON = icons.night
    LABEL_TEXT = "Ночной режим"


class CaffeineButton(ToggleServiceButton):
    PROCESS_PATTERN = "vidgex-inhibit"
    START_COMMAND = "python ~/.config/Vidgex-Shell/scripts/inhibit.py"
    STOP_COMMAND = "pkill -f vidgex-inhibit"
    BUTTON_NAME = "caffeine"
    ICON = icons.coffee
    LABEL_TEXT = "Caffeine"
    
    def __init__(self):
        self._inhibit_process: Optional[subprocess.Popen] = None
        super().__init__()

    def _toggle_service(self) -> bool:
        try:
            if self._inhibit_process and self._inhibit_process.poll() is None:
                self._inhibit_process.terminate()
                try: self._inhibit_process.wait(timeout=5)
                except subprocess.TimeoutExpired: self._inhibit_process.kill()
                self._inhibit_process = None
                return False
            
            monitor = ProcessMonitor(self.PROCESS_PATTERN)
            if monitor.check():
                exec_shell_command_async(self.STOP_COMMAND)
                return False
            
            self._inhibit_process = subprocess.Popen(
                self.START_COMMAND,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return True
        except Exception:
            return False

# ================== Buttons Grid Container ==================

class Buttons(Gtk.Grid):
    def __init__(self, **kwargs):
        super().__init__(name="buttons-grid")
        self._widgets = kwargs.pop("widgets", None)
        self._notch_instance = kwargs.pop("notch", None)
        self._configure_grid()
        self._create_buttons()
        self._layout_buttons()
        self.show_all()

    def _configure_grid(self):
        self.set_row_homogeneous(True)
        self.set_column_homogeneous(True)
        self.set_row_spacing(4)
        self.set_column_spacing(4)
        self.set_vexpand(False)

    def _create_buttons(self):
        self.network_button = NetworkButton(widgets=self._widgets, notch=self._notch_instance)
        self.bluetooth_button = BluetoothButton(widgets=self._widgets, notch=self._notch_instance)
        self.night_mode_button = NightModeButton()
        self.caffeine_button = CaffeineButton()

    def _layout_buttons(self):
        self.attach(self.network_button, 0, 0, 1, 1)
        self.attach(self.bluetooth_button, 1, 0, 1, 1)
        self.attach(self.night_mode_button, 2, 0, 1, 1)
        self.attach(self.caffeine_button, 3, 0, 1, 1)

    def refresh_all_states(self):
        self.network_button.update_state()
        self.bluetooth_button.update_state()
        self.night_mode_button.update_state()
        self.caffeine_button.update_state()

    def cleanup(self):
        if hasattr(self.network_button, 'cleanup'): self.network_button.cleanup()
        if hasattr(self.caffeine_button, 'cleanup'): self.caffeine_button.cleanup()

    def __del__(self):
        try:
            self.cleanup()
        except Exception:
            pass
