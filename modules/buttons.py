from fabric.utils.helpers import exec_shell_command_async
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label
from gi.repository import Gdk, GLib, Gtk
import subprocess, time, weakref
from modules import icons
from modules.network import NetworkClient


WIFI_STRENGTH = type('', (), {'WEAK':25, 'FAIR':50, 'GOOD':75})()
_process_check_cache = {}


def add_hover_cursor(widget):
    widget.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
    
    def set_cursor_on_enter(widget, event):
        window = widget.get_window()
        if window:
            cursor = Gdk.Cursor.new_from_name(widget.get_display(), "pointer")
            window.set_cursor(cursor)
    
    def clear_cursor_on_leave(widget, event):
        window = widget.get_window()
        if window:
            window.set_cursor(None)
    
    widget.connect("enter-notify-event", set_cursor_on_enter)
    widget.connect("leave-notify-event", clear_cursor_on_leave)


def check_process_running(pattern, use_cache=True):
    current_time = time.time()
    if use_cache and pattern in _process_check_cache:
        result, timestamp = _process_check_cache[pattern]
        if current_time - timestamp < 1.0:
            return result
    
    result = subprocess.run(
        ["pgrep", "-f", pattern],
        capture_output=True,
        timeout=2.0
    )
    is_running = result.returncode == 0
    _process_check_cache[pattern] = (is_running, current_time)
    
    if len(_process_check_cache) > 10:
        expired_keys = [
            k for k, (_, ts) in _process_check_cache.items()
            if current_time - ts > 1.0 * 2
        ]
        for key in expired_keys:
            _process_check_cache.pop(key, None)
    return is_running


def run_in_thread(func, callback=None):
    def worker(user_data):
        result = func()
        if callback:
            GLib.idle_add(callback, result)
    GLib.Thread.new(None, worker, None)


class StyleManager:
    def __init__(self, widgets):
        self._widgets = widgets
    
    def set_disabled(self, disabled):
        method_name = "add_style_class" if disabled else "remove_style_class"
        for widget in self._widgets:
            method = getattr(widget, method_name)
            method("disabled")


def create_status_layout(icon, title, status, spacing=10):
    title_box = Box(children=[title, Box(h_expand=True)])
    status_box = Box(children=[status, Box(h_expand=True)])
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
    WIFI_ICONS = (icons.wifi_0, icons.wifi_1, icons.wifi_2, icons.wifi_3)
    ANIMATION_ICONS = (icons.wifi_0, icons.wifi_1, icons.wifi_2, icons.wifi_3, icons.wifi_2, icons.wifi_1)
    
    def __init__(self, **kwargs):
        self._widgets_ref = weakref.ref(kwargs.pop("widgets")) if "widgets" in kwargs else None
        self._notch_ref = weakref.ref(kwargs.pop("notch")) if "notch" in kwargs else None
        super().__init__(
            name="network-button"
        )
        self._network_client = NetworkClient()
        self._animation_id = None
        self._update_id = None
        self._animation_step = 0
        self._create_ui()
        self._network_client.connect('device-ready', self._on_wifi_ready)
        self._schedule_update()
    
    def _schedule_update(self):
        if self._update_id:
            GLib.source_remove(self._update_id)
        self._update_id = GLib.timeout_add(100, self._do_update)
    
    def _do_update(self):
        self._update_id = None
        self.update_state()
        return False
    
    def _create_ui(self):
        self.network_icon = Label(name="network-icon")
        self.network_label = Label(name="network-label", label="Wi-Fi")
        self.network_ssid = Label(name="network-ssid")
        
        self.network_status_button = Button(
            name="network-status-button",
            h_expand=True,
            child=create_status_layout(self.network_icon, self.network_label, self.network_ssid),
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
        self._style_manager = StyleManager([
            self, self.network_icon, self.network_label, self.network_ssid,
            self.network_status_button, self.network_menu_button, self.network_menu_label
        ])
    
    def _on_status_clicked(self, *args):
        wifi_device = self._network_client.wifi_device
        if wifi_device:
            wifi_device.toggle_wifi()
    
    def _on_menu_clicked(self, *args):
        notch_instance = self._notch_ref() if self._notch_ref else None
        if notch_instance:
            notch_instance.open_notch("network_applet")
        else:
            widgets_instance = self._widgets_ref()
            if widgets_instance and hasattr(widgets_instance, 'show_network_applet'):
                widgets_instance.show_network_applet()
    
    def _on_wifi_ready(self, *args):
        wifi_device = self._network_client.wifi_device
        if wifi_device:
            wifi_device.connect('notify::enabled', lambda *_: self._schedule_update())
            wifi_device.connect('notify::ssid', lambda *_: self._schedule_update())
            self._schedule_update()
    
    def _get_wifi_icon(self, strength):
        if strength < WIFI_STRENGTH.WEAK: return self.WIFI_ICONS[0]
        elif strength < WIFI_STRENGTH.FAIR: return self.WIFI_ICONS[1]
        elif strength < WIFI_STRENGTH.GOOD: return self.WIFI_ICONS[2]
        else: return self.WIFI_ICONS[3]
    
    def _animate_searching(self):
        wifi_device = self._network_client.wifi_device
        if not wifi_device or not wifi_device.enabled:
            self._animation_id = None
            return False
        if wifi_device.state == "activated" and wifi_device.ssid != "Отключено":
            self._animation_id = None
            return False
        self.network_icon.set_markup(self.ANIMATION_ICONS[self._animation_step])
        self._animation_step = (self._animation_step + 1) % len(self.ANIMATION_ICONS)
        return True
    
    def update_state(self, *args):
        wifi_device = self._network_client.wifi_device
        ethernet_device = self._network_client.ethernet_device
        
        if wifi_device and not wifi_device.enabled:
            if self._animation_id:
                GLib.source_remove(self._animation_id)
                self._animation_id = None
            self.network_icon.set_markup(icons.wifi_off)
            self.network_ssid.set_label("Выключено")
            if self._style_manager:
                self._style_manager.set_disabled(True)
            return
        
        if self._style_manager:
            self._style_manager.set_disabled(False)
        
        primary_device = getattr(self._network_client, 'primary_device', 'wireless')
        if primary_device == "wired":
            if self._animation_id:
                GLib.source_remove(self._animation_id)
                self._animation_id = None
            icon = icons.world if (ethernet_device and ethernet_device.internet == "activated") else icons.world_off
            self.network_icon.set_markup(icon)
            return
        
        if not wifi_device:
            if self._animation_id:
                GLib.source_remove(self._animation_id)
                self._animation_id = None
            self.network_icon.set_markup(icons.wifi_off)
            return
        
        if wifi_device.state == "activated" and wifi_device.ssid != "Отключено":
            if self._animation_id:
                GLib.source_remove(self._animation_id)
                self._animation_id = None
            self.network_ssid.set_label(wifi_device.ssid)
            self.network_icon.set_markup(self._get_wifi_icon(wifi_device.strength))
        else:
            self.network_ssid.set_label("Включено")
            if not self._animation_id:
                self._animation_step = 0
                self._animation_id = GLib.timeout_add(500, self._animate_searching)
    
    def cleanup(self):
        if self._animation_id:
            GLib.source_remove(self._animation_id)
            self._animation_id = None
        if self._update_id:
            GLib.source_remove(self._update_id)
            self._update_id = None


class BluetoothButton(Box):
    def __init__(self, **kwargs):
        self._widgets = kwargs.pop("widgets")
        self._notch_instance = kwargs.pop("notch", None)
        super().__init__(
            name="bluetooth-button",
            orientation="h"
        )
        
        self.bluetooth_icon = Label(name="bluetooth-icon", markup=icons.bluetooth)
        self.bluetooth_label = Label(name="bluetooth-label", label="Bluetooth")
        self.bluetooth_status_text = Label(name="bluetooth-status", label="Выключено")
        
        self.bluetooth_status_button = Button(
            name="bluetooth-status-button",
            h_expand=True,
            child=create_status_layout(self.bluetooth_icon, self.bluetooth_label, self.bluetooth_status_text),
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
    
    def _on_status_clicked(self, *args):
        self._widgets.bluetooth.client.toggle_power()
    
    def _on_menu_clicked(self, *args):
        if self._notch_instance:
            self._notch_instance.open_notch("bluetooth")
        elif hasattr(self._widgets, 'show_bt'):
            self._widgets.show_bt()


class ToggleServiceButton(Button):
    PROCESS_PATTERN = START_COMMAND = STOP_COMMAND = BUTTON_NAME = ICON = ""
    LABEL_TEXT, ENABLED_TEXT, DISABLED_TEXT = "", "Включено", "Выключено"
    
    def __init__(self):
        self._icon_label = Label(name=f"{self.BUTTON_NAME}-icon", markup=self.ICON)
        self._title_label = Label(name=f"{self.BUTTON_NAME}-label", label=self.LABEL_TEXT)
        self._status_label = Label(name=f"{self.BUTTON_NAME}-status", label=self.DISABLED_TEXT)
        
        super().__init__(
            name=f"{self.BUTTON_NAME}-button",
            h_expand=True,
            child=create_status_layout(self._icon_label, self._title_label, self._status_label),
            on_clicked=self._on_clicked,
        )
        add_hover_cursor(self)
        self._style_manager = StyleManager([
            self, self._icon_label, self._title_label, self._status_label
        ])
        run_in_thread(lambda: check_process_running(self.PROCESS_PATTERN), self._update_ui)
    
    def _on_clicked(self, *args):
        run_in_thread(self._toggle_service, self._update_ui)
    
    def _toggle_service(self):
        is_running = check_process_running(self.PROCESS_PATTERN)
        if is_running:
            exec_shell_command_async(self.STOP_COMMAND)
            return False
        else:
            exec_shell_command_async(self.START_COMMAND)
            return True
    
    def _update_ui(self, enabled):
        self._status_label.set_label(self.ENABLED_TEXT if enabled else self.DISABLED_TEXT)
        self._style_manager.set_disabled(not enabled)
        return False


class NightModeButton(ToggleServiceButton):
    PROCESS_PATTERN = "hyprsunset"
    START_COMMAND = "hyprsunset -t 3500"
    STOP_COMMAND = "pkill hyprsunset"
    BUTTON_NAME = "night-mode"
    ICON = icons.night
    LABEL_TEXT = "Ночной режим"


class CaffeineButton(ToggleServiceButton):
    PROCESS_PATTERN = "ax-inhibit"
    START_COMMAND = "python ~/.config/Ax-Shell/scripts/inhibit.py"
    STOP_COMMAND = "pkill -f ax-inhibit"
    BUTTON_NAME = "caffeine"
    ICON = icons.coffee
    LABEL_TEXT = "Caffeine"
    
    def __init__(self):
        self._proc = None
        super().__init__()
    
    def _toggle_service(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
            return False
        
        if check_process_running(self.PROCESS_PATTERN):
            exec_shell_command_async(self.STOP_COMMAND)
            return False
        
        self._proc = subprocess.Popen(
            self.START_COMMAND,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return True
    
    def cleanup(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None


class Buttons(Gtk.Grid):
    def __init__(self, **kwargs):
        super().__init__(name="buttons-grid")
        self._widgets = kwargs.pop("widgets")
        self._notch_instance = kwargs.pop("notch", None)
        self.set_row_homogeneous(True)
        self.set_column_homogeneous(True)
        self.set_row_spacing(4)
        self.set_column_spacing(4)
        self._create_buttons()
        self._layout_buttons()
        self.show_all()
    
    def _create_buttons(self):
        self.network_button = NetworkButton(widgets=self._widgets, notch=self._notch_instance)
        self.bluetooth_button = BluetoothButton(widgets=self._widgets, notch=self._notch_instance)
        self.night_mode_button = NightModeButton()
        self.caffeine_button = CaffeineButton()
    
    def _layout_buttons(self):
        positions = [(0,0), (1,0), (2,0), (3,0)]
        buttons = [self.network_button, self.bluetooth_button, self.night_mode_button, self.caffeine_button]
        for (col, row), button in zip(positions, buttons):
            self.attach(button, col, row, 1, 1)
    
    def refresh_all_states(self):
        self.network_button.run_in_thread(lambda: check_process_running(self.PROCESS_PATTERN), self._update_ui)
        self.night_mode_button.run_in_thread(lambda: check_process_running(self.PROCESS_PATTERN), self._update_ui)
        self.caffeine_button.run_in_thread(lambda: check_process_running(self.PROCESS_PATTERN), self._update_ui)
    
    def __del__(self):
        if hasattr(self.network_button, 'cleanup'):
            self.network_button.cleanup()
        if hasattr(self.caffeine_button, 'cleanup'):
            self.caffeine_button.cleanup()