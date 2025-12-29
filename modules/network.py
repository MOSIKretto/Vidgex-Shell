from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow
from fabric.widgets.window import Window
from fabric.core.service import Property, Service, Signal
from fabric.utils import exec_shell_command_async

import gi
from gi.repository import Gtk, NM, GLib
gi.require_version('Gtk', '3.0')
gi.require_version('NM', '1.0')

import time
import psutil
from typing import Any, List, Dict

import modules.icons as icons


class Wifi(Service):
    @Signal
    def changed(self) -> None: ...
    
    @Signal  
    def enabled(self) -> bool: ...

    def __init__(self, client: NM.Client, device: NM.DeviceWifi, **kwargs):
        self._client = client
        self._device = device
        self._active_ap = None
        self._ap_signal_id = 0
        
        # Кэширование и оптимизация
        self._cached_aps = None
        self._cache_timestamp = 0
        self._cache_ttl = 3000  # 3 секунды для кэша AP
        self._update_cooldown = 0
        self._update_interval = 1000  # 1 секунда между обновлениями
        
        # Флаги состояния
        self._needs_update = False
        self._is_scanning = False
        
        super().__init__(**kwargs)

        # Оптимизированные подключения сигналов
        if self._device:
            self._device_handler_ids = [
                self._device.connect("notify::active-access-point", self._on_active_ap_changed),
                self._device.connect("access-point-added", self._invalidate_cache),
                self._device.connect("access-point-removed", self._invalidate_cache),
                self._device.connect("state-changed", self._on_state_changed),
            ]
            self._on_active_ap_changed()
        
        self._client_handler_id = self._client.connect(
            "notify::wireless-enabled", 
            self._on_wireless_enabled_changed
        )
        
        # Единый таймер для всех обновлений
        self._update_timer_id = GLib.timeout_add(self._update_interval, self._conditional_update)

    def _conditional_update(self):
        """Условное обновление только при необходимости"""
        current_time = time.time() * 1000
        
        if self._needs_update and current_time - self._update_cooldown >= self._update_interval:
            self._emit_changes()
            self._needs_update = False
            self._update_cooldown = current_time
            
        return True

    def _schedule_update(self):
        """Планирование обновления вместо немедленного выполнения"""
        self._needs_update = True

    def _on_wireless_enabled_changed(self, *args):
        """Обработчик изменения состояния WiFi"""
        self._on_active_ap_changed()
        self._schedule_update()

    def _on_state_changed(self, *args):
        """Обработчик изменения состояния устройства"""
        self._on_active_ap_changed()
        self._schedule_update()

    def _invalidate_cache(self, *args):
        """Инвалидация кэша точек доступа"""
        self._cached_aps = None
        self._schedule_update()

    def _on_active_ap_changed(self, *args):
        """Обработчик изменения активной точки доступа"""
        # Отключаем старый сигнал
        if self._active_ap and self._ap_signal_id:
            self._active_ap.disconnect(self._ap_signal_id)
            self._ap_signal_id = 0
            
        # Устанавливаем новую активную точку
        self._active_ap = self._device.get_active_access_point() if self._device else None
        
        # Подключаемся к сигналу силы сигнала
        if self._active_ap: 
            self._ap_signal_id = self._active_ap.connect(
                "notify::strength", 
                lambda *a: self._schedule_update()
            )
        
        self._schedule_update()

    def toggle_wifi(self):
        """Включение/выключение WiFi"""
        if not self._client:
            return
            
        current_state = self._client.wireless_get_enabled()
        self._client.wireless_set_enabled(not current_state)
        
        # Планируем обновление через 500 мс
        GLib.timeout_add(500, self._force_refresh)

    def _force_refresh(self):
        """Принудительное обновление после переключения"""
        self._invalidate_cache()
        self._schedule_update()
        return False

    def scan(self):
        """Запуск сканирования сетей"""
        if self._device and not self._is_scanning:
            self._is_scanning = True
            self._device.request_scan_async(
                None, 
                lambda device, result: self._on_scan_complete(device, result)
            )

    def _on_scan_complete(self, device, result):
        """Обработчик завершения сканирования"""
        try:
            device.request_scan_finish(result)
        finally:
            self._is_scanning = False
            self._invalidate_cache()

    def _emit_changes(self):
        """Единый метод для уведомления об изменениях"""
        self.notify("enabled")
        self.notify("ssid") 
        self.notify("strength")
        self.notify("icon-name")
        self.notify("internet")
        self.notify("state")
        self.emit("changed")

    def _get_icon_name(self, strength: int, internet_state: str) -> str:
        """Получение имени иконки на основе силы сигнала и состояния"""
        if not self.enabled:
            return "network-wireless-offline-symbolic"
        if not self._active_ap:
            return "network-wireless-offline-symbolic"
            
        if internet_state == "activated":
            if strength >= 80: return "network-wireless-signal-excellent-symbolic"
            elif strength >= 60: return "network-wireless-signal-good-symbolic"
            elif strength >= 40: return "network-wireless-signal-ok-symbolic"
            elif strength >= 20: return "network-wireless-signal-weak-symbolic"
            else: return "network-wireless-signal-none-symbolic"
        elif internet_state == "activating": 
            return "network-wireless-acquiring-symbolic"
        return "network-wireless-offline-symbolic"

    @Property(bool, "read-write", default_value=False)
    def enabled(self) -> bool: 
        return bool(self._client.wireless_get_enabled() if self._client else False)
    
    @enabled.setter
    def enabled(self, value: bool): 
        if self._client:
            self._client.wireless_set_enabled(value)
            GLib.timeout_add(500, self._force_refresh)

    @Property(int, "readable")
    def strength(self) -> int: 
        return self._active_ap.get_strength() if (self._active_ap and self.enabled) else 0

    @Property(str, "readable")
    def icon_name(self) -> str: 
        return self._get_icon_name(self.strength, self.internet)

    @Property(str, "readable") 
    def internet(self) -> str:
        if not self._device or not self.enabled:
            return "disconnected"
            
        active_conn = self._device.get_active_connection()
        if not active_conn: 
            return "disconnected"
            
        state = active_conn.get_state()
        state_map = {
            NM.ActiveConnectionState.ACTIVATED: "activated",
            NM.ActiveConnectionState.ACTIVATING: "activating", 
            NM.ActiveConnectionState.DEACTIVATING: "deactivating",
            NM.ActiveConnectionState.DEACTIVATED: "deactivated"
        }
        return state_map.get(state, "unknown")

    @Property(object, "readable")
    def access_points(self) -> List[Dict[str, Any]]:
        """Кэшированное получение точек доступа"""
        current_time = time.time() * 1000
        
        # Проверяем актуальность кэша
        if (self._cached_aps is not None and 
            current_time - self._cache_timestamp < self._cache_ttl):
            return self._cached_aps
            
        if not self.enabled:
            self._cached_aps = []
            return self._cached_aps
            
        # Обновляем кэш
        points = self._device.get_access_points() if self._device else []
        self._cached_aps = []
        
        for ap in points:
            ssid = ap.get_ssid()
            ssid_str = NM.utils_ssid_to_utf8(ssid.get_data()) if ssid and ssid.get_data() else "Unknown"
            strength = ap.get_strength()
            
            self._cached_aps.append({
                "bssid": ap.get_bssid(), 
                "ssid": ssid_str,
                "active-ap": self._active_ap, 
                "strength": strength, 
                "frequency": ap.get_frequency(),
                "icon-name": self._get_icon_name(strength, "activated"),
                "is_secured": bool(ap.get_flags() & getattr(NM, '80211ApFlags', 0x0001).PRIVACY) if hasattr(NM, '80211ApFlags') else True,
            })
        
        self._cache_timestamp = current_time
        return self._cached_aps

    @Property(str, "readable")
    def ssid(self) -> str:
        if not self.enabled:
            return "Выключено"
        if not self._active_ap: 
            return "Не подключено"
            
        ssid = self._active_ap.get_ssid()
        return NM.utils_ssid_to_utf8(ssid.get_data()) if ssid and ssid.get_data() else "Unknown"

    @Property(str, "readable")
    def state(self) -> str:
        if not self._device:
            return "unknown"
            
        state = self._device.get_state()
        state_map = {
            NM.DeviceState.UNMANAGED: "unmanaged", 
            NM.DeviceState.UNAVAILABLE: "unavailable",
            NM.DeviceState.DISCONNECTED: "disconnected", 
            NM.DeviceState.ACTIVATED: "activated",
            NM.DeviceState.DEACTIVATING: "deactivating", 
            NM.DeviceState.FAILED: "failed"
        }
        return state_map.get(state, "unknown")

    def destroy(self):
        """Очистка ресурсов с отключением всех сигналов"""
        if hasattr(self, '_update_timer_id') and self._update_timer_id:
            GLib.source_remove(self._update_timer_id)
            self._update_timer_id = None
            
        if hasattr(self, '_device_handler_ids') and self._device:
            for handler_id in self._device_handler_ids:
                self._device.disconnect(handler_id)
            self._device_handler_ids = []
                
        if hasattr(self, '_client_handler_id') and self._client_handler_id and self._client:
            self._client.disconnect(self._client_handler_id)
            self._client_handler_id = None
            
        if self._ap_signal_id and self._active_ap:
            self._active_ap.disconnect(self._ap_signal_id)
            self._ap_signal_id = 0
        
        # Clear references
        self._device = None
        self._client = None
        self._active_ap = None
        self._cached_aps = None
            
        super().destroy()


class Ethernet(Service):
    @Signal
    def changed(self) -> None: ...

    def __init__(self, client: NM.Client, device: NM.DeviceEthernet, **kwargs) -> None:
        super().__init__(**kwargs)
        self._client = client
        self._device = device
        self._device.connect("notify::active-connection", self._on_property_change)

    def _on_property_change(self, *args):
        self.emit("changed")

    @Property(str, "readable")
    def internet(self) -> str:
        active_conn = self._device.get_active_connection()
        if not active_conn: 
            return "disconnected"
            
        state = active_conn.get_state()
        if state == NM.ActiveConnectionState.ACTIVATED: return "activated"
        if state == NM.ActiveConnectionState.ACTIVATING: return "activating"
        return "disconnected"

    @Property(str, "readable")
    def icon_name(self) -> str:
        return "network-wired-symbolic" if self.internet == "activated" else "network-wired-disconnected-symbolic"


class NetworkClient(Service):
    @Signal
    def device_ready(self) -> None: ...
    
    @Signal  
    def connection_error(self, ssid: str, error_message: str) -> None: ...

    @Property(str, "readable")
    def primary_device(self) -> str:
        """Определяет основное сетевое устройство"""
        # Безопасная проверка Ethernet
        if (self.ethernet_device and 
            hasattr(self.ethernet_device, 'internet') and
            self.ethernet_device.internet in ["activated", "activating"]):
            return "ethernet"
        
        # Безопасная проверка WiFi
        elif (self.wifi_device and 
              hasattr(self.wifi_device, 'enabled') and
              hasattr(self.wifi_device, 'ssid') and
              self.wifi_device.enabled and
              self.wifi_device.ssid not in ["Disconnected", "Выключено", "Не подключено"]):
            return "wifi"
        
        return "none"

    def __init__(self, **kwargs):
        self._client = None
        self.wifi_device = None
        self.ethernet_device = None
        self._connection_callbacks = {}
        
        super().__init__(**kwargs)
        self._initialize()

    def _initialize(self):
        """Initialize network client"""
        def worker(user_data):
            try:
                self._client = NM.Client.new(None)
                GLib.idle_add(self._on_initialized)
            except Exception:
                GLib.idle_add(self._create_fallback_client)
        
        GLib.Thread.new("nm-init", worker, None)

    def _on_initialized(self):
        """Handle successful initialization"""
        self._initialize_devices()
        self.emit("device-ready")
        return False

    def _initialize_devices(self):
        wifi_device = self._get_device(NM.DeviceType.WIFI)
        if wifi_device:
            self.wifi_device = Wifi(self._client, wifi_device)
            wifi_device.connect("state-changed", self._on_wifi_state_changed)

        ethernet_device = self._get_device(NM.DeviceType.ETHERNET)
        if ethernet_device:
            self.ethernet_device = Ethernet(self._client, ethernet_device)

    def _get_device(self, device_type: NM.DeviceType):
        if not self._client:
            return None
        return next((x for x in self._client.get_devices() if x.get_device_type() == device_type), None)

    def _create_fallback_client(self):
        self.wifi_device = type('FallbackWifi', (), {
            'enabled': False,
            'strength': 0,
            'ssid': 'Disconnected',
            'access_points': [],
            'scan': lambda: None,
            'destroy': lambda: None,
        })()
        GLib.idle_add(lambda: self.emit("device-ready"))

    def _on_wifi_state_changed(self, device, new_state, old_state, reason):
        current_ssid = self._get_current_connection_ssid()
        
        if new_state == NM.DeviceState.FAILED and current_ssid:
            self.emit("connection_error", current_ssid, f"Ошибка подключения (код: {reason})")

    def _get_current_connection_ssid(self):
        return self.wifi_device.ssid if hasattr(self, 'wifi_device') and self.wifi_device else None

    def is_network_available(self, ssid: str) -> bool:
        if not hasattr(self, 'wifi_device') or not self.wifi_device:
            return False
        try:
            available_networks = getattr(self.wifi_device, 'access_points', [])
            return any(network.get("ssid") == ssid for network in available_networks)
        except Exception:
            return False

    def _get_connection_by_ssid(self, ssid: str):
        if not self._client:
            return None
        try:
            connections = self._client.get_connections()
            for connection in connections:
                if connection.get_connection_type() == '802-11-wireless':
                    setting_wireless = connection.get_setting_wireless()
                    if setting_wireless and setting_wireless.get_ssid():
                        connection_ssid = NM.utils_ssid_to_utf8(setting_wireless.get_ssid().get_data())
                        if connection_ssid == ssid: 
                            return connection
        except Exception:
            pass
        return None

    def connect_to_saved_network(self, ssid: str, success_callback=None, error_callback=None) -> bool:
        if not self._client:
            if error_callback: 
                error_callback(ssid, "Клиент не инициализирован")
            return False
            
        if not self.is_network_available(ssid):
            if error_callback: 
                error_callback(ssid, f"Сеть '{ssid}' недоступна")
            return False

        target_connection = self._get_connection_by_ssid(ssid)
        if target_connection is None:
            if error_callback: 
                error_callback(ssid, f"Сохраненная сеть '{ssid}' не найдена")
            return False

        self._connection_callbacks[ssid] = {
            'success_callback': success_callback, 
            'error_callback': error_callback
        }
        
        device = self.wifi_device._device if hasattr(self.wifi_device, '_device') else None
        self._client.activate_connection_async(target_connection, device, None, None, None, None)
        return True

    def connect_to_new_network(self, ssid: str, password: str, success_callback=None, error_callback=None) -> bool:
        self._connection_callbacks[ssid] = {
            'success_callback': success_callback, 
            'error_callback': error_callback
        }
        
        command = f"nmcli device wifi connect \"{ssid}\" password \"{password}\""
        exec_shell_command_async(command, lambda success, output, error: 
            self._on_new_network_connected(ssid, success, output, error))
        return True

    def _on_new_network_connected(self, ssid: str, success: bool, output: str, error: str):
        if not success:
            callback_data = self._connection_callbacks.get(ssid)
            if callback_data and callback_data.get('error_callback'):
                callback_data['error_callback'](ssid, f"Ошибка подключения: {error}")
            self._connection_callbacks.pop(ssid, None)

    def delete_saved_network(self, ssid: str) -> bool:
        if not self._client: 
            return False
        target_connection = self._get_connection_by_ssid(ssid)
        if target_connection:
            target_connection.delete()
            return True
        return False


class PasswordDialog(Window):
    def __init__(self, parent_window, ssid, network_client, connect_callback):
        super().__init__(title=f"Подключение к {ssid}", default_size=(400, 200), modal=True, visible=False)
        self.ssid, self.network_client, self.connect_callback = ssid, network_client, connect_callback
        
        main_box = Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=24)
        main_box.add(Label(label=f"Введите пароль для сети '{ssid}'", h_align="center"))
        
        password_box = Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        password_label = Label(label="Пароль:", h_align="start")
        
        self.password_entry = Gtk.Entry()
        self.password_entry.set_placeholder_text("Введите пароль")
        self.password_entry.set_visibility(False)
        self.password_entry.set_invisible_char('•')
        self.password_entry.set_activates_default(True)
        
        show_password_box = Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.show_password_check = Gtk.CheckButton(label="Показать пароль")
        self.show_password_check.connect("toggled", self._on_show_password_toggled)
        show_password_box.add(self.show_password_check)
        
        password_box.add(password_label)
        password_box.add(self.password_entry)
        password_box.add(show_password_box)
        main_box.add(password_box)
        
        buttons_box = Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12, h_align="end")
        cancel_button = Button(label="Отмена", on_clicked=self.destroy)
        self.connect_button = Button(label="Подключиться", name="suggested-action", on_clicked=self._on_connect_clicked)
        self.connect_button.set_sensitive(False)
        
        self.password_entry.connect("changed", self._on_password_changed)
        buttons_box.add(cancel_button)
        buttons_box.add(self.connect_button)
        main_box.add(buttons_box)
        
        self.add(main_box)
        self.show_all()
    
    def _on_show_password_toggled(self, check_button):
        self.password_entry.set_visibility(check_button.get_active())
    
    def _on_password_changed(self, entry):
        self.connect_button.set_sensitive(len(entry.get_text()) > 0)
    
    def _on_connect_clicked(self, button):
        password = self.password_entry.get_text()
        if not password: 
            return
        self.connect_button.set_sensitive(False)
        self.connect_button.set_label("Подключение...")
        self.connect_callback(self.ssid, password)
        self.destroy()


class WifiNetworkSlot(CenterBox):
    def __init__(self, network_data, network_client, parent_window, is_saved=False, is_connected=False, **kwargs):
        super().__init__(name="wifi-network-slot", **kwargs)
        self.network_data, self.network_client, self.parent_window = network_data, network_client, parent_window
        self.is_saved, self.is_connected = is_saved, is_connected
        
        ssid, strength = network_data.get("ssid", "Unknown"), network_data.get("strength", 0)
        self.network_icon = Image(icon_name=network_data.get("icon-name", "network-wireless-signal-none-symbolic"), size=16)
        self.network_label = Label(label=ssid, h_expand=True, h_align="start", ellipsization="end")
        self.strength_label = Label(label=f"{strength}%")
        
        self.connect_button = Button(name="wifi-connect", label="Подключиться", on_clicked=self.on_connect_clicked)
        self.delete_button = Button(name="wifi-delete", child=Label(name="wifi-delete-label", markup=icons.trash),
                                  on_clicked=self.on_delete_clicked, tooltip_text="Удалить сеть")
        self.connected_label = Label(label="Подключено", name="wifi-connected-label")
        self.unavailable_button = Button(name="wifi-unavailable", label="Недоступна", sensitive=False, can_focus=False)
        self.unavailable_button.add_style_class("unavailable-network")
        
        self.start_children = Box(spacing=8, h_expand=True, h_align="fill", children=[
            self.network_icon, self.network_label, self.strength_label])
        
        self._setup_action_widgets()
        
        if hasattr(self.network_client, 'connect'):
            self.network_client.connect("connection_error", self._on_connection_error)

        # Подключаем обработчики нажатия
        if hasattr(self, 'connect_button'):
            self.connect_button.connect('button-press-event', self._on_button_press)
        if hasattr(self, 'delete_button'):
            self.delete_button.connect('button-press-event', self._on_delete_button_press)

    def _setup_action_widgets(self):
        ssid = self.network_data.get("ssid")
        is_available = self.network_client.is_network_available(ssid) if hasattr(self.network_client, 'is_network_available') and ssid else False
        
        if self.is_connected:
            action_widgets = [self.connected_label, self.delete_button]
        elif not is_available and self.is_saved:
            action_widgets = [self.unavailable_button, self.delete_button]
        elif not is_available:
            action_widgets = [self.unavailable_button]
        elif self.is_saved:
            action_widgets = [self.connect_button, self.delete_button]
        else:
            action_widgets = [self.connect_button]
            
        self.end_children = Box(orientation="horizontal", spacing=4, children=action_widgets)

    def _on_button_press(self, widget, event):
        """Анимация нажатия для кнопки подключения Wi-Fi"""
        widget.add_style_class('pressed')
        GLib.timeout_add(150, lambda: widget.remove_style_class('pressed') if widget else False)
        return False

    def _on_delete_button_press(self, widget, event):
        """Анимация нажатия для кнопки удаления Wi-Fi"""
        widget.add_style_class('pressed')
        GLib.timeout_add(150, lambda: widget.remove_style_class('pressed') if widget else False)
        return False

    def on_connect_clicked(self, button):
        ssid = self.network_data.get("ssid")
        if not ssid: 
            return
            
        is_available = self.network_client.is_network_available(ssid) if hasattr(self.network_client, 'is_network_available') else False
        if not is_available: 
            return self._show_error("Сеть недоступна")
            
        if self.is_saved: 
            self._connect_to_saved_network(ssid)
        else: 
            PasswordDialog(self.parent_window, ssid, self.network_client, self._on_password_entered)

    def _connect_to_saved_network(self, ssid):
        self._set_connecting_state(True)
        success = self.network_client.connect_to_saved_network(ssid, self._on_connection_success, self._on_connection_error_callback) \
            if hasattr(self.network_client, 'connect_to_saved_network') else False
        if not success: 
            self._set_connecting_state(False)

    def _on_password_entered(self, ssid, password):
        self._set_connecting_state(True)
        success = self.network_client.connect_to_new_network(ssid, password, self._on_connection_success, self._on_connection_error_callback) \
            if hasattr(self.network_client, 'connect_to_new_network') else False
        if not success: 
            self._set_connecting_state(False)

    def _set_connecting_state(self, connecting):
        if connecting: 
            self.connect_button.set_sensitive(False)
            self.connect_button.set_label("Подключение...")
        else: 
            self.connect_button.set_sensitive(True)
            self.connect_button.set_label("Подключиться")

    def _on_connection_success(self, ssid): 
        pass

    def _on_connection_error_callback(self, ssid, error_message): 
        self._show_error(error_message)

    def _on_connection_error(self, client, ssid, error_message):
        if ssid == self.network_data.get("ssid"): 
            self._show_error(error_message)

    def _show_error(self, error_message):
        self._set_connecting_state(False)
        original_text = self.connect_button.get_label()
        self.connect_button.set_label(f"Ошибка: {error_message}")
        self.connect_button.set_sensitive(False)
        GLib.timeout_add(3000, self._restore_connect_button, original_text)

    def _restore_connect_button(self, original_text):
        self.connect_button.set_label(original_text)
        self.connect_button.set_sensitive(True)
        return False

    def on_delete_clicked(self, button):
        ssid = self.network_data.get("ssid")
        if ssid and hasattr(self.network_client, 'delete_saved_network'):
            self.network_client.delete_saved_network(ssid)


class NetworkConnections(Box):
    def __init__(self, **kwargs):
        super().__init__(name="network-connections", spacing=4, orientation="vertical", **kwargs)
        
        try:
            self.network_client = NetworkClient()
        except Exception:
            self.network_client = type('FallbackNetworkClient', (), {
                'wifi_device': None,
                'is_network_available': lambda x: False,
            })()
            
        self.widgets = kwargs["widgets"]
        self._refresh_timeout_id, self._periodic_refresh_id = None, None
        self._refresh_delay, self._scan_click_in_progress, self._cached_networks = 500, False, None

        # Флаги для цветового выделения
        self.downloading = False
        self.uploading = False

        # Создаем метки для скорости с цветовым выделением
        self.download_label = Label(name="download-label", markup="0 Б/с")
        self.upload_label = Label(name="upload-label", markup="0 Б/с")
        
        # Иконки для скачивания и отправки
        self.download_icon = Label(name="download-icon-label", markup=icons.download)
        self.upload_icon = Label(name="upload-icon-label", markup=icons.upload)
        
        # Контейнеры для скоростей (только иконки + метки, без текста)
        self.download_box = Box(
            orientation=Gtk.Orientation.HORIZONTAL, 
            spacing=4,
            children=[self.download_icon, self.download_label]
        )
        self.upload_box = Box(
            orientation=Gtk.Orientation.HORIZONTAL, 
            spacing=4,
            children=[self.upload_label, self.upload_icon]  # upload слева, download справа
        )

        self.scan_label = Label(name="network-scan-label", markup=icons.radar)
        self.scan_button = Button(name="network-scan", child=self.scan_label, 
                                tooltip_text="Сканировать Wi-Fi сети", on_clicked=self.on_scan_clicked)
        self.back_button = Button(name="network-back", 
                                child=Label(name="network-back-label", markup=icons.chevron_left), 
                                on_clicked=lambda *_: self.widgets.show_notif())

        self.saved_box = Box(name="saved-box", spacing=2, orientation="vertical")
        self.available_box = Box(name="available-box", spacing=2, orientation="vertical")

        content_box = Box(spacing=4, orientation="vertical")
        content_box.add(Label(name="network-section", label="Сохраненные сети"))
        content_box.add(self.saved_box)
        content_box.add(Label(name="network-section", label="Доступные сети"))
        content_box.add(self.available_box)

        # Создаем центральный контейнер - компактный вариант
        center_content = Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12, h_align="center")
        
        # Левая часть: отправка (только иконка + значение)
        left_box = Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, v_align="center")
        left_box.add(self.upload_box)
        
        # Центральная часть: WiFi сети
        center_box = Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, v_align="center")
        center_box.add(Label(name="network-text", label="Wi-Fi"))
        
        # Правая часть: скачивание (только иконка + значение)
        right_box = Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, v_align="center")
        right_box.add(self.download_box)

        # Подключаем обработчики нажатия к кнопкам
        self.scan_button.connect('button-press-event', self._on_button_press)
        self.back_button.connect('button-press-event', self._on_button_press)
            
        # Добавляем все в центральный контейнер
        center_content.add(left_box)
        center_content.add(center_box)
        center_content.add(right_box)

        self.children = [
            CenterBox(
                name="network-header", 
                start_children=self.back_button, 
                center_children=center_content,
                end_children=self.scan_button
            ),
            ScrolledWindow(
                name="bluetooth-devices", 
                min_content_size=(-1, -1), 
                child=content_box,
                v_expand=True, 
                propagate_width=False, 
                propagate_height=False
            ),
        ]

        # Инициализация мониторинга сети
        self.last_counters = None
        self.last_time = time.time()
        self._init_network_counters()
        
        # Запуск обновления скоростей
        self._speed_timer_id = GLib.timeout_add(1000, self.update_network_speeds)

        if hasattr(self.network_client, 'connect'):
            self.network_client.connect("device-ready", self.on_device_ready)
        
        if hasattr(self.network_client, 'wifi_device') and self.network_client.wifi_device: 
            GLib.idle_add(self.on_device_ready)

    def _on_button_press(self, widget, event):
        """Обработчик нажатия кнопки с анимацией"""
        # Добавляем класс для анимации нажатия
        widget.add_style_class('pressed')
        
        # Удаляем класс через 150мс для повторной анимации
        GLib.timeout_add(150, lambda: widget.remove_style_class('pressed') if widget else False)
        
        return False 

    def _init_network_counters(self):
        """Инициализация счетчиков сети"""
        try:
            self.last_counters = psutil.net_io_counters()
            self.last_time = time.time()
        except ImportError:
            self.last_counters = None
            print("Ошибка: psutil не установлен. Установите: pip install psutil")

    def update_network_speeds(self):
        """Обновление скоростей сети с цветовым выделением"""
        if self.last_counters is None:
            try:
                self._init_network_counters()
            except:
                return True
            
        try:
            current_time = time.time()
            elapsed = current_time - self.last_time
            current_counters = psutil.net_io_counters()
            
            if elapsed > 0:
                download_speed = (current_counters.bytes_recv - self.last_counters.bytes_recv) / elapsed
                upload_speed = (current_counters.bytes_sent - self.last_counters.bytes_sent) / elapsed
                
                download_str = self.format_speed(download_speed)
                upload_str = self.format_speed(upload_speed)
                
                self.download_label.set_markup(download_str)
                self.upload_label.set_markup(upload_str)

                # Определяем активность по порогам
                self.downloading = (download_speed >= 2e6)   
                self.uploading = (upload_speed >= 2e6)

                # Применяем цветовое выделение
                if self.downloading: self.download_urgent()
                elif self.uploading: self.upload_urgent()
                else: self.remove_urgent()

                # Обновляем tooltip
                tooltip_text = f"Скачивание: {download_str}\nОтправка: {upload_str}"
                self.download_box.set_tooltip_text(tooltip_text)
                self.upload_box.set_tooltip_text(tooltip_text)

            self.last_counters = current_counters
            self.last_time = current_time
            
        except Exception as e:
            # В случае ошибки скрываем метки скоростей
            self.download_label.set_visible(False)
            self.upload_label.set_visible(False)
            self.download_icon.set_visible(False)
            self.upload_icon.set_visible(False)
            
        return True

    def format_speed(self, speed):
        """Форматирование скорости"""
        if speed < 1024: return f"{speed:.0f} B/s"
        elif speed < 1024 * 1024: return f"{speed / 1024:.1f} KB/s"
        else: return f"{speed / (1024 * 1024):.1f} MB/s"

    def upload_urgent(self):
        """Цветовое выделение для активной отправки"""
        self.upload_label.add_style_class("urgent")
        self.upload_icon.add_style_class("urgent")
        self.download_icon.remove_style_class("urgent")
        self.download_label.remove_style_class("urgent")

    def download_urgent(self):
        """Цветовое выделение для активного скачивания"""
        self.download_label.add_style_class("urgent")
        self.download_icon.add_style_class("urgent")
        self.upload_icon.remove_style_class("urgent")
        self.upload_label.remove_style_class("urgent")

    def remove_urgent(self):
        """Удаление цветового выделения"""
        self.download_label.remove_style_class("urgent")
        self.upload_label.remove_style_class("urgent")
        self.download_icon.remove_style_class("urgent")
        self.upload_icon.remove_style_class("urgent")

    def on_device_ready(self, client=None):
        """Обработчик готовности устройства"""
        if hasattr(self.network_client, 'wifi_device') and self.network_client.wifi_device:
            if hasattr(self.network_client.wifi_device, 'connect'):
                self.network_client.wifi_device.connect("changed", self._schedule_refresh)
            self._start_periodic_refresh()
            self._schedule_refresh()

    def _start_periodic_refresh(self):
        """Запуск периодического обновления"""
        if self._periodic_refresh_id is None:
            self._periodic_refresh_id = GLib.timeout_add(self._refresh_delay, self._do_complete_refresh)

    def _schedule_refresh(self, device=None):
        """Планирование обновления"""
        if not self._refresh_timeout_id:
            self._refresh_timeout_id = GLib.timeout_add(self._refresh_delay, self._do_complete_refresh)

    def _do_complete_refresh(self):
        """Полное обновление"""
        self._refresh_timeout_id = None
        self.refresh_networks()
        self._update_external_widgets()
        return True

    def _update_external_widgets(self):
        """Обновление внешних виджетов"""
        current_ssid = self.get_current_network_ssid()
        strength = self._get_connection_strength()
        enabled = self._is_wifi_enabled()
        
        display_text = current_ssid if current_ssid and current_ssid != "Disconnected" and enabled else "Не подключено"
        if not enabled:
            display_text = "Выключено"
        
        if hasattr(self.widgets, 'update_network_display'):
            self.widgets.update_network_display(display_text, strength, enabled)

    def _is_wifi_enabled(self):
        """Проверка включен ли WiFi"""
        try:
            return bool(self.network_client.wifi_device and getattr(self.network_client.wifi_device, 'enabled', False))
        except Exception:
            return False

    def _get_connection_strength(self):
        """Получение силы сигнала"""
        try:
            return getattr(self.network_client.wifi_device, 'strength', 0) if self.network_client.wifi_device else 0
        except Exception:
            return 0

    def on_scan_clicked(self, button):
        """Обработчик клика по сканированию"""
        if self._scan_click_in_progress: 
            return
        self._scan_click_in_progress = True
        self.scan_label.add_style_class("scanning")
        self.scan_button.add_style_class("scanning")
        self.scan_button.set_tooltip_text("Сканирование...")
        self.scan_button.set_sensitive(False)
        
        if (hasattr(self.network_client, 'wifi_device') and 
            self.network_client.wifi_device and 
            getattr(self.network_client.wifi_device, 'enabled', False)): 
            self.network_client.wifi_device.scan()
                
        GLib.timeout_add(3000, self._reset_scan_button)

    def _reset_scan_button(self):
        """Сброс состояния кнопки сканирования"""
        self.scan_label.remove_style_class("scanning")
        self.scan_button.remove_style_class("scanning")
        self.scan_button.set_tooltip_text("Сканировать Wi-Fi сети")
        self.scan_button.set_sensitive(True)
        self._scan_click_in_progress = False
        return False

    def get_saved_networks(self):
        """Получение сохраненных сетей"""
        saved_networks = []
        if (hasattr(self.network_client, '_client') and 
            self.network_client._client):
            connections = self.network_client._client.get_connections()
            for connection in connections:
                if connection.get_connection_type() == '802-11-wireless':
                    setting = connection.get_setting_wireless()
                    if setting and setting.get_ssid():
                        ssid = NM.utils_ssid_to_utf8(setting.get_ssid().get_data())
                        if ssid and ssid not in saved_networks: 
                            saved_networks.append(ssid)
        return saved_networks

    def get_current_network_ssid(self):
        """Получение текущего SSID"""
        if (hasattr(self.network_client, 'wifi_device') and 
            self.network_client.wifi_device and 
            hasattr(self.network_client.wifi_device, 'ssid') and
            self.network_client.wifi_device.ssid and 
            self.network_client.wifi_device.ssid not in ["Disconnected", "Выключено", "Не подключено"]):
            return self.network_client.wifi_device.ssid
        return None

    def refresh_networks(self):
        """Обновление списка сетей"""
        current_ssid, saved_networks = self.get_current_network_ssid(), self.get_saved_networks()
        enabled = self._is_wifi_enabled()
        
        available_networks = []
        if enabled and hasattr(self.network_client, 'wifi_device') and self.network_client.wifi_device:
            available_networks = self.network_client.wifi_device.access_points
        
        current_state = {
            'current_ssid': current_ssid, 
            'saved_networks': tuple(saved_networks), 
            'available_count': len(available_networks),
            'enabled': enabled
        }
        
        if self._cached_networks == current_state: 
            return
            
        self._cached_networks = current_state
        
        for child in self.saved_box.get_children(): child.destroy()
        for child in self.available_box.get_children(): child.destroy()
        
        available_networks_dict = {ap.get("ssid"): ap for ap in available_networks if ap.get("ssid")}
        self._create_network_slots(current_ssid, saved_networks, available_networks_dict, available_networks)
        
        self.saved_box.show_all()
        self.available_box.show_all()

    def _create_network_slots(self, current_ssid, saved_networks, available_networks_dict, available_networks):
        """Создание слотов сетей"""
        if current_ssid and current_ssid in saved_networks:
            ap_data = available_networks_dict.get(current_ssid, {
                "ssid": current_ssid, 
                "strength": 0, 
                "is_secured": True, 
                "icon-name": "network-wireless-signal-excellent-symbolic"
            })
            self.saved_box.add(WifiNetworkSlot(ap_data, self.network_client, self.get_toplevel(), 
                                             is_saved=True, is_connected=True))

        for ssid in saved_networks:
            if ssid == current_ssid:
                continue
            ap_data = available_networks_dict.get(ssid, {
                "ssid": ssid, 
                "strength": 0, 
                "is_secured": True, 
                "icon-name": "network-wireless-signal-none-symbolic"
            })
            self.saved_box.add(WifiNetworkSlot(ap_data, self.network_client, self.get_toplevel(), 
                                             is_saved=True, is_connected=False))

        for ap_data in available_networks:
            ssid = ap_data.get("ssid")
            if ssid and ssid not in saved_networks and ssid != current_ssid:
                self.available_box.add(WifiNetworkSlot(ap_data, self.network_client, self.get_toplevel(), is_saved=False, is_connected=False))

    def destroy(self):
        """Очистка ресурсов"""
        if hasattr(self, '_refresh_timeout_id') and self._refresh_timeout_id:
            GLib.source_remove(self._refresh_timeout_id)
            self._refresh_timeout_id = None
        if hasattr(self, '_periodic_refresh_id') and self._periodic_refresh_id:
            GLib.source_remove(self._periodic_refresh_id)
            self._periodic_refresh_id = None
        if hasattr(self, '_speed_timer_id') and self._speed_timer_id:
            GLib.source_remove(self._speed_timer_id)
            self._speed_timer_id = None
        
        # Disconnect wifi device signals
        if hasattr(self, 'network_client') and hasattr(self.network_client, 'wifi_device'):
            if hasattr(self.network_client.wifi_device, 'destroy'):
                self.network_client.wifi_device.destroy()
        
        # Clear references
        self.network_client = None
        self.last_counters = None
        self._cached_networks = None
        
        super().destroy()
    
    def __del__(self):
        self.destroy()