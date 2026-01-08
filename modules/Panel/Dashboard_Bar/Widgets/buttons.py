from fabric.utils.helpers import exec_shell_command_async
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label
from gi.repository import GLib, Gtk
import subprocess
from modules import icons
from modules.Panel.Dashboard_Bar.Widgets.network import NetworkClient
from modules.common import add_hover_cursor, check_process_running, run_in_thread


WIFI_STRENGTH = type('', (), {'WEAK': 25, 'FAIR': 50, 'GOOD': 75})()


class StyleManager:
    __slots__ = ('_widgets',)
    
    def __init__(self, widgets):
        self._widgets = widgets
    
    def set_disabled(self, disabled):
        method_name = "add_style_class" if disabled else "remove_style_class"
        for widget in self._widgets:
            getattr(widget, method_name)("disabled")


def create_status_layout(icon, title, status, spacing=10):
    text_box = Box(
        orientation="v",
        h_align="start",
        v_align="center",
        children=[
            Box(children=[title, Box(h_expand=True)]),
            Box(children=[status, Box(h_expand=True)])
        ],
    )
    return Box(
        h_align="start",
        v_align="center",
        spacing=spacing,
        children=[icon, text_box],
    )


class NetworkButton(Box):
    __slots__ = ('_widgets', '_notch_instance', '_network_client', '_animation_id', '_update_id', 
                '_animation_step', 'network_icon', 'network_label', 'network_ssid', 
                'network_status_button', 'network_menu_label', 'network_menu_button', '_style_manager',
                '_signal_handler_ids')
    
    WIFI_ICONS = (icons.wifi_0, icons.wifi_1, icons.wifi_2, icons.wifi_3)
    ANIMATION_ICONS = (icons.wifi_0, icons.wifi_1, icons.wifi_2, icons.wifi_3, icons.wifi_2, icons.wifi_1)
    
    def __init__(self, **kwargs):
        self._widgets = kwargs.pop("widgets", None)
        self._notch_instance = kwargs.pop("notch", None)
        super().__init__(name="network-button")
        
        self._network_client = NetworkClient()
        self._animation_id = None
        self._update_id = None
        self._animation_step = 0
        self._signal_handler_ids = []
        
        self._create_ui()
        handler_id = self._network_client.connect('device-ready', lambda *_: self._schedule_update())
        self._signal_handler_ids.append(handler_id)
    
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
        if wifi_device := self._network_client.wifi_device:
            wifi_device.toggle_wifi()

    def _on_menu_clicked(self, *args):
        if self._notch_instance:
            self._notch_instance.open_notch("network_applet")
        elif self._widgets and hasattr(self._widgets, 'show_network_applet'):
            self._widgets.show_network_applet()

    def _get_wifi_icon(self, strength):
        if strength < WIFI_STRENGTH.WEAK: return self.WIFI_ICONS[0]
        elif strength < WIFI_STRENGTH.FAIR: return self.WIFI_ICONS[1]
        elif strength < WIFI_STRENGTH.GOOD: return self.WIFI_ICONS[2]
        return self.WIFI_ICONS[3]

    def _animate_searching(self):
        if not (wifi_device := self._network_client.wifi_device) or not wifi_device.enabled:
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
        
        for handler_id in self._signal_handler_ids:
            if self._network_client:
                self._network_client.disconnect(handler_id)

        self._signal_handler_ids.clear()
        
        if self._network_client and hasattr(self._network_client, 'cleanup'):
            self._network_client.cleanup()

        self._network_client = None
        self._widgets = None
        self._notch_instance = None


class BluetoothButton(Box):
    __slots__ = ('_widgets', '_notch_instance', 'bluetooth_icon', 'bluetooth_label', 
                'bluetooth_status_text', 'bluetooth_status_button', 'bluetooth_menu_label', 'bluetooth_menu_button')
    
    def __init__(self, **kwargs):
        self._widgets = kwargs.pop("widgets")
        self._notch_instance = kwargs.pop("notch", None)
        super().__init__(name="bluetooth-button", orientation="h")
        
        self.bluetooth_icon = Label(name="bluetooth-icon", markup=icons.bluetooth)
        self.bluetooth_label = Label(name="bluetooth-label", label="Bluetooth")
        self.bluetooth_status_text = Label(name="bluetooth-status", label="Выключено")
        
        self.bluetooth_status_button = Button(
            name="bluetooth-status-button",
            h_expand=True,
            child=create_status_layout(self.bluetooth_icon, self.bluetooth_label, self.bluetooth_status_text),
            on_clicked=lambda *_: self._widgets.bluetooth.client.toggle_power(),
        )
        add_hover_cursor(self.bluetooth_status_button)
        
        self.bluetooth_menu_label = Label(name="bluetooth-menu-label", markup=icons.chevron_right)
        self.bluetooth_menu_button = Button(
            name="bluetooth-menu-button",
            child=self.bluetooth_menu_label,
            on_clicked=lambda *_: (
                self._notch_instance.open_notch("bluetooth") 
                if self._notch_instance 
                else self._widgets.show_bt()
            ),
        )
        add_hover_cursor(self.bluetooth_menu_button)
        
        self.add(self.bluetooth_status_button)
        self.add(self.bluetooth_menu_button)


class ToggleServiceButton(Button):
    __slots__ = ('_icon_label', '_title_label', '_status_label', '_style_manager')
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
            on_clicked=lambda *_: run_in_thread(self._toggle_service, self._update_ui),
        )
        add_hover_cursor(self)
        self._style_manager = StyleManager([self, self._icon_label, self._title_label, self._status_label])
        run_in_thread(lambda: check_process_running(self.PROCESS_PATTERN), self._update_ui)
    
    def _toggle_service(self):
        is_running = check_process_running(self.PROCESS_PATTERN)
        exec_shell_command_async(self.STOP_COMMAND if is_running else self.START_COMMAND)
        return not is_running
    
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
    __slots__ = ('_proc',)
    PROCESS_PATTERN = "vidgex-inhibit"
    START_COMMAND = "python ~/.config/Vidgex-Shell/scripts/inhibit.py"
    STOP_COMMAND = "pkill -f vidgex-inhibit"
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
    __slots__ = ('_widgets', '_notch_instance', 'network_button', 'bluetooth_button', 
                'night_mode_button', 'caffeine_button', '_cleaned_up')
    
    def __init__(self, **kwargs):
        super().__init__(name="buttons-grid")
        self._widgets = kwargs.pop("widgets")
        self._notch_instance = kwargs.pop("notch", None)
        self._cleaned_up = False
        
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
        if hasattr(self, 'night_mode_button'):
            run_in_thread(
                lambda: check_process_running(self.night_mode_button.PROCESS_PATTERN),
                self.night_mode_button._update_ui
            )
        if hasattr(self, 'caffeine_button'):
            run_in_thread(
                lambda: check_process_running(self.caffeine_button.PROCESS_PATTERN),
                self.caffeine_button._update_ui
            )
    
    def cleanup(self):
        if self._cleaned_up:
            return
        self._cleaned_up = True
        
        if hasattr(self, 'network_button') and self.network_button:
            self.network_button.cleanup()
            self.network_button = None
        
        if hasattr(self, 'caffeine_button') and self.caffeine_button:
            self.caffeine_button.cleanup()
            self.caffeine_button = None
        
        if hasattr(self, 'bluetooth_button') and self.bluetooth_button:
            if hasattr(self.bluetooth_button, 'cleanup'):
                self.bluetooth_button.cleanup()
            
            self.bluetooth_button = None
        
        if hasattr(self, 'night_mode_button') and self.night_mode_button:
            if hasattr(self.night_mode_button, 'cleanup'):
                self.night_mode_button.cleanup()
            
            self.night_mode_button = None
        
        self._widgets = None
        self._notch_instance = None
    
    def destroy(self):
        self.cleanup()
        super().destroy()
    
    def __del__(self):
        self.cleanup()