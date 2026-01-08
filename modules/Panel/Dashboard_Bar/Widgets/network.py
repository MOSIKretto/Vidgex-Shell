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
import threading
from typing import Any, List, Dict, Optional
from dataclasses import dataclass
from enum import Enum
import weakref

import modules.icons as icons


class ConnectionState(Enum):
    """Состояния соединения"""
    DISCONNECTED = "disconnected"
    ACTIVATED = "activated"
    ACTIVATING = "activating"
    DEACTIVATING = "deactivating"
    DEACTIVATED = "deactivated"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass
class NetworkAccessPoint:
    """Структура данных для точки доступа"""
    bssid: str
    ssid: str
    strength: int
    frequency: int
    is_secured: bool
    is_active: bool
    last_seen: float = 0


class Wifi(Service):
    """Оптимизированный сервис WiFi с кэшированием и debounce"""
    
    @Signal
    def changed(self) -> None: ...
    
    @Signal  
    def enabled(self) -> bool: ...

    def __init__(self, client: NM.Client, device: NM.DeviceWifi, **kwargs):
        super().__init__(**kwargs)
        
        self._client = client
        self._device = device
        self._active_ap = None
        self._ap_signal_id = 0
        
        # Кэширование
        self._aps_cache: Optional[List[Dict[str, Any]]] = None
        self._cache_timestamp: float = 0
        self._cache_ttl: float = 3000  # 3 секунды
        
        # Оптимизация обновлений
        self._update_debounce_timer: Optional[int] = None
        self._update_interval: int = 1000  # 1 секунда
        self._last_update: float = 0
        self._needs_update: bool = False
        
        # Флаги состояния
        self._is_scanning: bool = False
        self._shutdown: bool = False
        
        # Сигналы
        self._handler_ids: List[int] = []
        
        # Инициализация
        self._setup_signal_handlers()
        self._on_active_ap_changed()
        
        # Таймер обновлений
        self._update_timer_id = GLib.timeout_add(
            self._update_interval, 
            self._conditional_update
        )
    
    def _setup_signal_handlers(self):
        """Настройка обработчиков сигналов с weakref"""
        if not self._device:
            return
        
        weak_self = weakref.ref(self)
        
        def make_handler(handler_name):
            def handler(*args):
                instance = weak_self()
                if instance:
                    getattr(instance, handler_name)(*args)
            return handler
        
        # Подключаем обработчики
        handlers = [
            ("notify::active-access-point", self._on_active_ap_changed),
            ("access-point-added", self._invalidate_cache),
            ("access-point-removed", self._invalidate_cache),
            ("state-changed", self._on_state_changed),
        ]
        
        for signal, callback in handlers:
            handler_id = self._device.connect(signal, make_handler(callback.__name__))
            self._handler_ids.append(handler_id)
    
    def _conditional_update(self) -> bool:
        """Условное обновление с debounce"""
        if self._shutdown:
            return False
        
        current_time = time.time() * 1000
        
        if self._needs_update and current_time - self._last_update >= self._update_interval:
            self._emit_changes()
            self._needs_update = False
            self._last_update = current_time
        
        return True
    
    def _schedule_update(self):
        """Планирование обновления"""
        if self._needs_update:
            return
        
        self._needs_update = True
        
        # Debounce: отменяем предыдущий таймер
        if self._update_debounce_timer:
            GLib.source_remove(self._update_debounce_timer)
        
        # Запускаем новый таймер
        def delayed_update():
            self._update_debounce_timer = None
            self._emit_changes()
            return False
        
        self._update_debounce_timer = GLib.timeout_add(50, delayed_update)
    
    def _on_active_ap_changed(self, *args):
        """Обработчик изменения активной точки доступа"""
        # Отключаем старый сигнал
        if self._active_ap and self._ap_signal_id:
            self._active_ap.disconnect(self._ap_signal_id)
            self._ap_signal_id = 0
        
        # Получаем новую активную точку
        self._active_ap = self._device.get_active_access_point() if self._device else None
        
        # Подключаемся к новой точке
        if self._active_ap:
            weak_self = weakref.ref(self)
            
            def on_strength_changed(*args):
                instance = weak_self()
                if instance:
                    instance._schedule_update()
            
            self._ap_signal_id = self._active_ap.connect(
                "notify::strength", 
                on_strength_changed
            )
        
        self._schedule_update()
    
    def _invalidate_cache(self, *args):
        """Инвалидация кэша"""
        self._aps_cache = None
        self._schedule_update()
    
    def _on_state_changed(self, *args):
        """Обработчик изменения состояния устройства"""
        self._schedule_update()
    
    def _on_wireless_enabled_changed(self, *args):
        """Обработчик изменения состояния WiFi"""
        self._schedule_update()
    
    def _emit_changes(self):
        """Эмитирование сигналов об изменениях"""
        if self._shutdown:
            return
        
        self.notify("enabled")
        self.notify("ssid")
        self.notify("strength")
        self.notify("icon-name")
        self.notify("internet")
        self.notify("state")
        self.emit("changed")
    
    def toggle_wifi(self):
        """Переключение WiFi"""
        if not self._client:
            return
        
        current_state = self._client.wireless_get_enabled()
        self._client.wireless_set_enabled(not current_state)
        
        GLib.timeout_add(500, self._force_refresh)
    
    def _force_refresh(self):
        """Принудительное обновление"""
        self._invalidate_cache()
        self._schedule_update()
        return False
    
    def scan(self):
        """Сканирование сетей"""
        if self._shutdown or not self._device or self._is_scanning:
            return
        
        self._is_scanning = True
        
        weak_self = weakref.ref(self)
        
        def on_scan_complete(device, result):
            instance = weak_self()
            if not instance:
                return
            
            try:
                device.request_scan_finish(result)
            finally:
                instance._is_scanning = False
                instance._invalidate_cache()
        
        self._device.request_scan_async(None, on_scan_complete)
    
    def _get_icon_name(self, strength: int, internet_state: str) -> str:
        """Получение имени иконки по силе сигнала и состоянию"""
        if not self.enabled or not self._active_ap:
            return "network-wireless-offline-symbolic"
        
        if internet_state == ConnectionState.ACTIVATED.value:
            if strength >= 80:
                return "network-wireless-signal-excellent-symbolic"
            elif strength >= 60:
                return "network-wireless-signal-good-symbolic"
            elif strength >= 40:
                return "network-wireless-signal-ok-symbolic"
            elif strength >= 20:
                return "network-wireless-signal-weak-symbolic"
            else:
                return "network-wireless-signal-none-symbolic"
        elif internet_state == ConnectionState.ACTIVATING.value:
            return "network-wireless-acquiring-symbolic"
        
        return "network-wireless-offline-symbolic"
    
    @Property(bool, "read-write", default_value=False)
    def enabled(self) -> bool:
        """Состояние WiFi (включен/выключен)"""
        if not self._client:
            return False
        return bool(self._client.wireless_get_enabled())
    
    @enabled.setter
    def enabled(self, value: bool):
        """Установка состояния WiFi"""
        if self._client:
            self._client.wireless_set_enabled(value)
            GLib.timeout_add(500, self._force_refresh)
    
    @Property(int, "readable")
    def strength(self) -> int:
        """Сила сигнала"""
        if not self._active_ap or not self.enabled:
            return 0
        return self._active_ap.get_strength()
    
    @Property(str, "readable")
    def icon_name(self) -> str:
        """Имя иконки"""
        return self._get_icon_name(self.strength, self.internet)
    
    @Property(str, "readable")
    def internet(self) -> str:
        """Состояние интернет-соединения"""
        if not self._device or not self.enabled:
            return ConnectionState.DISCONNECTED.value
        
        active_conn = self._device.get_active_connection()
        if not active_conn:
            return ConnectionState.DISCONNECTED.value
        
        state = active_conn.get_state()
        state_map = {
            NM.ActiveConnectionState.ACTIVATED: ConnectionState.ACTIVATED.value,
            NM.ActiveConnectionState.ACTIVATING: ConnectionState.ACTIVATING.value,
            NM.ActiveConnectionState.DEACTIVATING: ConnectionState.DEACTIVATING.value,
            NM.ActiveConnectionState.DEACTIVATED: ConnectionState.DEACTIVATED.value,
        }
        return state_map.get(state, "unknown")
    
    @Property(object, "readable")
    def access_points(self) -> List[Dict[str, Any]]:
        """Список доступных точек доступа с кэшированием"""
        current_time = time.time() * 1000
        
        # Используем кэш если он валиден
        if (self._aps_cache is not None and 
            current_time - self._cache_timestamp < self._cache_ttl):
            return self._aps_cache
        
        if not self.enabled or not self._device:
            self._aps_cache = []
            return self._aps_cache
        
        points = self._device.get_access_points()
        result = []
        
        for ap in points:
            ssid = ap.get_ssid()
            ssid_str = ""
            
            if ssid and ssid.get_data():
                try:
                    ssid_str = NM.utils_ssid_to_utf8(ssid.get_data())
                except:
                    ssid_str = "Unknown"
            
            strength = ap.get_strength()
            
            result.append({
                "bssid": ap.get_bssid() or "",
                "ssid": ssid_str,
                "active-ap": self._active_ap,
                "strength": strength,
                "frequency": ap.get_frequency(),
                "icon-name": self._get_icon_name(strength, ConnectionState.ACTIVATED.value),
                "is_secured": bool(ap.get_flags() & getattr(NM, '80211ApFlags', 0x0001).PRIVACY) 
                              if hasattr(NM, '80211ApFlags') else True,
            })
        
        self._aps_cache = result
        self._cache_timestamp = current_time
        return result
    
    @Property(str, "readable")
    def ssid(self) -> str:
        """SSID текущей сети"""
        if not self.enabled:
            return "Выключено"
        if not self._active_ap:
            return "Не подключено"
        
        ssid = self._active_ap.get_ssid()
        if not ssid or not ssid.get_data():
            return "Unknown"
        
        try:
            return NM.utils_ssid_to_utf8(ssid.get_data())
        except:
            return "Unknown"
    
    @Property(str, "readable")
    def state(self) -> str:
        """Состояние устройства"""
        if not self._device:
            return "unknown"
        
        state = self._device.get_state()
        state_map = {
            NM.DeviceState.UNMANAGED: "unmanaged",
            NM.DeviceState.UNAVAILABLE: ConnectionState.UNAVAILABLE.value,
            NM.DeviceState.DISCONNECTED: ConnectionState.DISCONNECTED.value,
            NM.DeviceState.ACTIVATED: ConnectionState.ACTIVATED.value,
            NM.DeviceState.DEACTIVATING: ConnectionState.DEACTIVATING.value,
            NM.DeviceState.FAILED: ConnectionState.FAILED.value,
        }
        return state_map.get(state, "unknown")
    
    def destroy(self):
        """Очистка ресурсов"""
        self._shutdown = True
        
        # Останавливаем таймеры
        if hasattr(self, '_update_timer_id') and self._update_timer_id:
            GLib.source_remove(self._update_timer_id)
            self._update_timer_id = None
        
        if self._update_debounce_timer:
            GLib.source_remove(self._update_debounce_timer)
            self._update_debounce_timer = None
        
        # Отключаем сигналы
        if self._active_ap and self._ap_signal_id:
            try:
                self._active_ap.disconnect(self._ap_signal_id)
            except:
                pass
            self._ap_signal_id = 0
        
        # Отключаем обработчики устройства
        if self._device and hasattr(self, '_handler_ids'):
            for handler_id in self._handler_ids:
                try:
                    self._device.disconnect(handler_id)
                except:
                    pass
            self._handler_ids.clear()
        
        # Отключаем обработчик клиента
        if hasattr(self, '_client_handler_id') and self._client_handler_id and self._client:
            try:
                self._client.disconnect(self._client_handler_id)
            except:
                pass
            self._client_handler_id = None
        
        # Очищаем кэши
        self._aps_cache = None
        
        # Очищаем ссылки
        self._client = None
        self._device = None
        self._active_ap = None
        
        super().destroy()


class Ethernet(Service):
    """Сервис Ethernet соединения"""
    
    @Signal
    def changed(self) -> None: ...

    def __init__(self, client: NM.Client, device: NM.DeviceEthernet, **kwargs) -> None:
        super().__init__(**kwargs)
        self._client = client
        self._device = device
        
        if self._device:
            self._device.connect("notify::active-connection", self._on_property_change)
    
    def _on_property_change(self, *args):
        """Обработчик изменения свойства"""
        self.emit("changed")
    
    @Property(str, "readable")
    def internet(self) -> str:
        """Состояние соединения"""
        if not self._device:
            return ConnectionState.DISCONNECTED.value
        
        active_conn = self._device.get_active_connection()
        if not active_conn:
            return ConnectionState.DISCONNECTED.value
        
        state = active_conn.get_state()
        if state == NM.ActiveConnectionState.ACTIVATED:
            return ConnectionState.ACTIVATED.value
        if state == NM.ActiveConnectionState.ACTIVATING:
            return ConnectionState.ACTIVATING.value
        
        return ConnectionState.DISCONNECTED.value
    
    @Property(str, "readable")
    def icon_name(self) -> str:
        """Иконка Ethernet"""
        return ("network-wired-symbolic" 
                if self.internet == ConnectionState.ACTIVATED.value 
                else "network-wired-disconnected-symbolic")


class NetworkClient(Service):
    """Клиент для управления сетевыми соединениями"""
    
    @Signal
    def device_ready(self) -> None: ...
    
    @Signal
    def connection_error(self, ssid: str, error_message: str) -> None: ...

    @Property(str, "readable")
    def primary_device(self) -> str:
        """Основное сетевое устройство"""
        if (self.ethernet_device and 
            hasattr(self.ethernet_device, 'internet') and
            self.ethernet_device.internet in [ConnectionState.ACTIVATED.value, 
                                            ConnectionState.ACTIVATING.value]):
            return "ethernet"
        
        elif (self.wifi_device and hasattr(self.wifi_device, 'enabled') and
              hasattr(self.wifi_device, 'ssid') and self.wifi_device.enabled and
              self.wifi_device.ssid not in ["Disconnected", "Выключено", "Не подключено"]):
            return "wifi"
        
        return "none"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
        self._client: Optional[NM.Client] = None
        self.wifi_device: Optional[Wifi] = None
        self.ethernet_device: Optional[Ethernet] = None
        self._connection_callbacks: Dict[str, Dict] = {}
        self._shutdown: bool = False
        
        # Асинхронная инициализация
        self._initialize_async()
    
    def _initialize_async(self):
        """Асинхронная инициализация"""
        def init_worker():
            try:
                client = NM.Client.new(None)
                GLib.idle_add(self._on_client_initialized, client)
            except Exception as e:
                print(f"Failed to initialize NetworkManager client: {e}")
                GLib.idle_add(self._create_fallback_client)
        
        thread = threading.Thread(target=init_worker, daemon=True)
        thread.start()
    
    def _on_client_initialized(self, client: NM.Client):
        """Обработчик успешной инициализации клиента"""
        if self._shutdown:
            return
        
        self._client = client
        self._initialize_devices()
        self.emit("device-ready")
    
    def _initialize_devices(self):
        """Инициализация сетевых устройств"""
        if not self._client:
            return
        
        # WiFi устройство
        wifi_device = self._get_device(NM.DeviceType.WIFI)
        if wifi_device:
            self.wifi_device = Wifi(self._client, wifi_device)
            
            # Подключаем обработчик состояния
            weak_self = weakref.ref(self)
            
            def on_wifi_state_changed(device, new_state, old_state, reason):
                instance = weak_self()
                if instance:
                    instance._on_wifi_state_changed(device, new_state, old_state, reason)
            
            wifi_device.connect("state-changed", on_wifi_state_changed)
        
        # Ethernet устройство
        ethernet_device = self._get_device(NM.DeviceType.ETHERNET)
        if ethernet_device:
            self.ethernet_device = Ethernet(self._client, ethernet_device)
    
    def _get_device(self, device_type: NM.DeviceType) -> Optional[NM.Device]:
        """Получение устройства по типу"""
        if not self._client:
            return None
        
        for device in self._client.get_devices():
            if device.get_device_type() == device_type:
                return device
        
        return None
    
    def _create_fallback_client(self):
        """Создание fallback клиента"""
        class FallbackWifi:
            enabled = False
            strength = 0
            ssid = 'Disconnected'
            access_points = []
            
            def scan(self): pass
            def destroy(self): pass
        
        self.wifi_device = FallbackWifi()
        self.emit("device-ready")
    
    def _on_wifi_state_changed(self, device, new_state, old_state, reason):
        """Обработчик изменения состояния WiFi"""
        current_ssid = self._get_current_connection_ssid()
        
        if new_state == NM.DeviceState.FAILED and current_ssid:
            self.emit("connection_error", current_ssid, f"Ошибка подключения (код: {reason})")
    
    def _get_current_connection_ssid(self) -> Optional[str]:
        """Получение SSID текущего соединения"""
        if not hasattr(self, 'wifi_device') or not self.wifi_device:
            return None
        
        return getattr(self.wifi_device, 'ssid', None)
    
    def is_network_available(self, ssid: str) -> bool:
        """Проверка доступности сети"""
        if not hasattr(self, 'wifi_device') or not self.wifi_device:
            return False
        
        try:
            available_networks = getattr(self.wifi_device, 'access_points', [])
            return any(network.get("ssid") == ssid for network in available_networks)
        except Exception:
            return False
    
    def _get_connection_by_ssid(self, ssid: str) -> Optional[NM.Connection]:
        """Получение сохраненного соединения по SSID"""
        if not self._client:
            return None
        
        connections = self._client.get_connections()
        for connection in connections:
            if connection.get_connection_type() == '802-11-wireless':
                setting_wireless = connection.get_setting_wireless()
                if setting_wireless and setting_wireless.get_ssid():
                    connection_ssid = NM.utils_ssid_to_utf8(
                        setting_wireless.get_ssid().get_data()
                    )
                    if connection_ssid == ssid:
                        return connection
        
        return None
    
    def connect_to_saved_network(self, ssid: str, success_callback=None, 
                               error_callback=None) -> bool:
        """Подключение к сохраненной сети"""
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
        
        # Сохраняем колбэки
        self._connection_callbacks[ssid] = {
            'success_callback': success_callback,
            'error_callback': error_callback
        }
        
        # Получаем устройство WiFi
        device = None
        if hasattr(self.wifi_device, '_device'):
            device = self.wifi_device._device
        
        # Активируем соединение
        self._client.activate_connection_async(target_connection, device, None, None, None, None)
        return True
    
    def connect_to_new_network(self, ssid: str, password: str, 
                             success_callback=None, error_callback=None) -> bool:
        """Подключение к новой сети"""
        self._connection_callbacks[ssid] = {
            'success_callback': success_callback,
            'error_callback': error_callback
        }
        
        command = f"nmcli device wifi connect \"{ssid}\" password \"{password}\""
        
        def on_command_complete(success, output, error):
            if not success:
                callback_data = self._connection_callbacks.get(ssid)
                if callback_data and callback_data.get('error_callback'):
                    callback_data['error_callback'](ssid, f"Ошибка подключения: {error}")
                self._connection_callbacks.pop(ssid, None)
        
        exec_shell_command_async(command, on_command_complete)
        return True
    
    def delete_saved_network(self, ssid: str) -> bool:
        """Удаление сохраненной сети"""
        if not self._client:
            return False
        
        target_connection = self._get_connection_by_ssid(ssid)
        if target_connection:
            target_connection.delete()
            return True
        
        return False
    
    def destroy(self):
        """Очистка ресурсов"""
        self._shutdown = True
        
        # Уничтожаем устройства
        if self.wifi_device and hasattr(self.wifi_device, 'destroy'):
            self.wifi_device.destroy()
        
        if self.ethernet_device and hasattr(self.ethernet_device, 'destroy'):
            self.ethernet_device.destroy()
        
        # Очищаем кэши
        self._connection_callbacks.clear()
        
        # Очищаем ссылки
        self._client = None
        self.wifi_device = None
        self.ethernet_device = None
        
        super().destroy()


class PasswordDialog(Window):
    """Диалог ввода пароля WiFi"""
    
    def __init__(self, parent_window, ssid: str, network_client: NetworkClient, 
                 connect_callback):
        super().__init__(
            title=f"Подключение к {ssid}",
            default_size=(400, 200),
            modal=True,
            transient_for=parent_window,
            decorated=False,
            skip_taskbar_hint=True,
            visible=False
        )
        
        self.ssid = ssid
        self.network_client = network_client
        self.connect_callback = connect_callback
        
        self._setup_ui()
        self.show_all()
    
    def _setup_ui(self):
        """Настройка UI диалога"""
        main_box = Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin=24
        )
        
        # Заголовок
        title_label = Label(
            label=f"Введите пароль для сети '{self.ssid}'",
            h_align="center"
        )
        main_box.add(title_label)
        
        # Поле пароля
        password_box = Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8
        )
        
        password_label = Label(
            label="Пароль:",
            h_align="start"
        )
        
        self.password_entry = Gtk.Entry()
        self.password_entry.set_placeholder_text("Введите пароль")
        self.password_entry.set_visibility(False)
        self.password_entry.set_invisible_char('•')
        self.password_entry.set_activates_default(True)
        
        # Checkbox показа пароля
        show_password_box = Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8
        )
        
        self.show_password_check = Gtk.CheckButton(label="Показать пароль")
        self.show_password_check.connect("toggled", self._on_show_password_toggled)
        
        show_password_box.add(self.show_password_check)
        
        # Добавляем элементы
        password_box.add(password_label)
        password_box.add(self.password_entry)
        password_box.add(show_password_box)
        main_box.add(password_box)
        
        # Кнопки
        buttons_box = Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
            h_align="end"
        )
        
        cancel_button = Button(
            label="Отмена",
            on_clicked=self.destroy
        )
        
        self.connect_button = Button(
            label="Подключиться",
            name="suggested-action",
            on_clicked=self._on_connect_clicked
        )
        self.connect_button.set_sensitive(False)
        
        # Обработчики
        self.password_entry.connect("changed", self._on_password_changed)
        
        # Добавляем кнопки
        buttons_box.add(cancel_button)
        buttons_box.add(self.connect_button)
        main_box.add(buttons_box)
        
        self.add(main_box)
    
    def _on_show_password_toggled(self, check_button):
        """Обработчик переключения видимости пароля"""
        self.password_entry.set_visibility(check_button.get_active())
    
    def _on_password_changed(self, entry):
        """Обработчик изменения пароля"""
        self.connect_button.set_sensitive(len(entry.get_text()) > 0)
    
    def _on_connect_clicked(self, button):
        """Обработчик нажатия кнопки подключения"""
        password = self.password_entry.get_text()
        if not password:
            return
        
        self.connect_button.set_sensitive(False)
        self.connect_button.set_label("Подключение...")
        self.connect_callback(self.ssid, password)
        self.destroy()


class WifiNetworkSlot(CenterBox):
    """Слот для отображения WiFi сети"""
    
    def __init__(self, network_data: Dict[str, Any], network_client: NetworkClient,
                 parent_window, is_saved: bool = False, is_connected: bool = False, **kwargs):
        super().__init__(name="wifi-network-slot", **kwargs)
        
        self.network_data = network_data
        self.network_client = network_client
        self.parent_window = parent_window
        self.is_saved = is_saved
        self.is_connected = is_connected
        
        # Таймеры
        self._pressed_timers: List[int] = []
        self._restore_timer: Optional[int] = None
        
        # UI элементы
        self._setup_ui()
    
    def _setup_ui(self):
        """Настройка UI слота"""
        ssid = self.network_data.get("ssid", "Unknown")
        strength = self.network_data.get("strength", 0)
        
        # Иконка сети
        self.network_icon = Image(
            icon_name=self.network_data.get("icon-name", "network-wireless-signal-none-symbolic"),
            size=16
        )
        
        # Название сети
        self.network_label = Label(
            label=ssid,
            h_expand=True,
            h_align="start",
            ellipsization="end"
        )
        
        # Сила сигнала
        self.strength_label = Label(label=f"{strength}%")
        
        # Кнопки и виджеты действий
        self.connect_button = Button(
            name="wifi-connect",
            label="Подключиться",
            on_clicked=self.on_connect_clicked
        )
        
        self.delete_button = Button(
            name="wifi-delete",
            child=Label(name="wifi-delete-label", markup=icons.trash),
            on_clicked=self.on_delete_clicked,
            tooltip_text="Удалить сеть"
        )
        
        self.connected_label = Label(
            label="Подключено",
            name="wifi-connected-label"
        )
        
        self.unavailable_button = Button(
            name="wifi-unavailable",
            label="Недоступна",
            sensitive=False,
            can_focus=False
        )
        self.unavailable_button.add_style_class("unavailable-network")
        
        # Левая часть (иконка + название + сила сигнала)
        self.start_children = Box(
            spacing=8,
            h_expand=True,
            h_align="fill",
            children=[self.network_icon, self.network_label, self.strength_label]
        )
        
        # Правая часть (действия)
        self._setup_action_widgets()
    
    def _setup_action_widgets(self):
        """Настройка виджетов действий"""
        ssid = self.network_data.get("ssid")
        is_available = False
        
        if ssid and hasattr(self.network_client, 'is_network_available'):
            is_available = self.network_client.is_network_available(ssid)
        
        # Определяем виджеты действий
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
        
        # Правая часть
        self.end_children = Box(
            orientation="horizontal",
            spacing=4,
            children=action_widgets
        )
    
    def on_connect_clicked(self, button):
        """Обработчик клика по кнопке подключения"""
        ssid = self.network_data.get("ssid")
        if not ssid:
            return
        
        # Проверяем доступность сети
        is_available = False
        if hasattr(self.network_client, 'is_network_available'):
            is_available = self.network_client.is_network_available(ssid)
        
        if not is_available:
            self._show_error("Сеть недоступна")
            return
        
        # Подключаемся к сети
        if self.is_saved:
            self._connect_to_saved_network(ssid)
        else:
            PasswordDialog(
                self.parent_window,
                ssid,
                self.network_client,
                self._on_password_entered
            )
    
    def _connect_to_saved_network(self, ssid: str):
        """Подключение к сохраненной сети"""
        self._set_connecting_state(True)
        
        success = False
        if hasattr(self.network_client, 'connect_to_saved_network'):
            success = self.network_client.connect_to_saved_network(
                ssid,
                self._on_connection_success,
                self._on_connection_error_callback
            )
        
        if not success:
            self._set_connecting_state(False)
    
    def _on_password_entered(self, ssid: str, password: str):
        """Обработчик введенного пароля"""
        self._set_connecting_state(True)
        
        success = False
        if hasattr(self.network_client, 'connect_to_new_network'):
            success = self.network_client.connect_to_new_network(
                ssid,
                password,
                self._on_connection_success,
                self._on_connection_error_callback
            )
        
        if not success:
            self._set_connecting_state(False)
    
    def _set_connecting_state(self, connecting: bool):
        """Установка состояния подключения"""
        if not hasattr(self, 'connect_button'):
            return
        
        if connecting:
            self.connect_button.set_sensitive(False)
            self.connect_button.set_label("Подключение...")
        else:
            self.connect_button.set_sensitive(True)
            self.connect_button.set_label("Подключиться")
    
    def _on_connection_success(self, ssid: str):
        """Обработчик успешного подключения"""
        self._set_connecting_state(False)
    
    def _on_connection_error_callback(self, ssid: str, error_message: str):
        """Обработчик ошибки подключения"""
        self._show_error(error_message)
    
    def _show_error(self, error_message: str):
        """Показать ошибку"""
        self._set_connecting_state(False)
        
        if not hasattr(self, 'connect_button'):
            return
        
        original_text = self.connect_button.get_label()
        self.connect_button.set_label(f"Ошибка: {error_message}")
        self.connect_button.set_sensitive(False)
        
        # Восстановление через 3 секунды
        if self._restore_timer:
            GLib.source_remove(self._restore_timer)
        
        def restore():
            self.connect_button.set_label(original_text)
            self.connect_button.set_sensitive(True)
            self._restore_timer = None
            return False
        
        self._restore_timer = GLib.timeout_add(3000, restore)
    
    def on_delete_clicked(self, button):
        """Обработчик удаления сети"""
        ssid = self.network_data.get("ssid")
        if ssid and hasattr(self.network_client, 'delete_saved_network'):
            self.network_client.delete_saved_network(ssid)
    
    def destroy(self):
        """Очистка ресурсов"""
        # Останавливаем таймеры
        if self._restore_timer:
            GLib.source_remove(self._restore_timer)
            self._restore_timer = None
        
        for timer_id in self._pressed_timers:
            GLib.source_remove(timer_id)
        self._pressed_timers.clear()
        
        super().destroy()


class NetworkConnections(Box):
    """Виджет сетевых соединений с оптимизациями"""
    
    def __init__(self, **kwargs):
        super().__init__(
            name="network-connections",
            spacing=4,
            orientation="vertical",
            **kwargs
        )
        
        # Инициализация атрибутов
        self.widgets = kwargs.get("widgets", None)
        self.network_client = None
        
        # Кэширование
        self._cached_networks = None
        self._last_refresh = 0
        self._refresh_delay = 500
        
        # Таймеры
        self._refresh_timeout_id = None
        self._periodic_refresh_id = None
        self._speed_timer_id = None
        self._pressed_timers = []
        
        # Флаги состояния
        self._scan_click_in_progress = False
        self._shutdown = False
        
        # Статистика сети
        self.downloading = False
        self.uploading = False
        self.last_counters = None
        self.last_time = time.time()
        
        # Инициализация
        self._init_network_client()
        self._setup_ui()
        self._start_timers()
    
    def _init_network_client(self):
        """Инициализация сетевого клиента"""
        try:
            self.network_client = NetworkClient()
            
            # Подключаем обработчики
            if hasattr(self.network_client, 'connect'):
                self.network_client.connect("device-ready", self.on_device_ready)
        except Exception as e:
            print(f"Failed to initialize network client: {e}")
            self._create_fallback_client()
    
    def _create_fallback_client(self):
        """Создание fallback клиента"""
        class FallbackNetworkClient:
            wifi_device = None
            
            def is_network_available(self, ssid):
                return False
        
        self.network_client = FallbackNetworkClient()
        GLib.idle_add(self.on_device_ready)
    
    def _setup_ui(self):
        """Настройка UI"""
        # Заголовок с кнопками
        header = self._create_header()
        
        # Списки сетей
        networks_content = self._create_networks_content()
        
        # Прокручиваемое окно
        scrolled_window = ScrolledWindow(
            name="network-scrolled-window",
            min_content_size=(-1, -1),
            child=networks_content,
            v_expand=True,
            propagate_width=False,
            propagate_height=False
        )
        
        # Добавляем все элементы
        self.children = [header, scrolled_window]
    
    def _create_header(self):
        """Создание заголовка с кнопками"""
        # Иконки загрузки/отдачи
        self.download_label = Label(name="download-label", markup="0 Б/с")
        self.upload_label = Label(name="upload-label", markup="0 Б/с")
        
        self.download_icon = Label(name="download-icon-label", markup=icons.download)
        self.upload_icon = Label(name="upload-icon-label", markup=icons.upload)
        
        # Контейнеры иконок
        self.download_box = Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=4,
            children=[self.download_icon, self.download_label]
        )
        
        self.upload_box = Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=4,
            children=[self.upload_label, self.upload_icon]
        )
        
        # Кнопки
        self.scan_label = Label(name="network-scan-label", markup=icons.radar)
        self.scan_button = Button(
            name="network-scan",
            child=self.scan_label,
            tooltip_text="Сканировать Wi-Fi сети",
            on_clicked=self.on_scan_clicked
        )
        
        self.back_button = Button(
            name="network-back",
            child=Label(name="network-back-label", markup=icons.chevron_left),
            on_clicked=lambda *_: self.widgets.show_notif() if self.widgets else None
        )
        
        # Центральная часть
        center_content = Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
            h_align="center"
        )
        
        left_box = Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=4,
            v_align="center"
        )
        left_box.add(self.upload_box)
        
        center_box = Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=2,
            v_align="center"
        )
        center_box.add(Label(name="network-text", label="Wi-Fi"))
        
        right_box = Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=4,
            v_align="center"
        )
        right_box.add(self.download_box)
        
        center_content.add(left_box)
        center_content.add(center_box)
        center_content.add(right_box)
        
        # Header
        return CenterBox(
            name="network-header",
            start_children=self.back_button,
            center_children=center_content,
            end_children=self.scan_button
        )
    
    def _create_networks_content(self):
        """Создание контента со списком сетей"""
        content_box = Box(
            spacing=4,
            orientation="vertical"
        )
        
        # Сохраненные сети
        content_box.add(Label(name="network-section", label="Сохраненные сети"))
        
        self.saved_box = Box(
            name="saved-box",
            spacing=2,
            orientation="vertical"
        )
        content_box.add(self.saved_box)
        
        # Доступные сети
        content_box.add(Label(name="network-section", label="Доступные сети"))
        
        self.available_box = Box(
            name="available-box",
            spacing=2,
            orientation="vertical"
        )
        content_box.add(self.available_box)
        
        return content_box
    
    def _start_timers(self):
        """Запуск таймеров"""
        # Таймер скорости сети
        self._speed_timer_id = GLib.timeout_add(1000, self.update_network_speeds)
        
        # Таймер периодического обновления
        self._periodic_refresh_id = GLib.timeout_add(5000, self._periodic_refresh)
    
    def _periodic_refresh(self):
        """Периодическое обновление"""
        if not self._shutdown:
            self._schedule_refresh()
        return not self._shutdown
    
    def _schedule_refresh(self):
        """Планирование обновления"""
        if self._refresh_timeout_id:
            GLib.source_remove(self._refresh_timeout_id)
        
        def refresh():
            self._refresh_timeout_id = None
            self.refresh_networks()
            return False
        
        self._refresh_timeout_id = GLib.timeout_add(self._refresh_delay, refresh)
    
    def on_device_ready(self, client=None):
        """Обработчик готовности устройства"""
        if (hasattr(self, 'network_client') and 
            hasattr(self.network_client, 'wifi_device') and 
            self.network_client.wifi_device):
            
            # Подключаем обработчик изменений
            if hasattr(self.network_client.wifi_device, 'connect'):
                weak_self = weakref.ref(self)
                
                def on_changed(*args):
                    instance = weak_self()
                    if instance:
                        instance._schedule_refresh()
                
                self.network_client.wifi_device.connect("changed", on_changed)
            
            # Первоначальное обновление
            self._schedule_refresh()
    
    def on_scan_clicked(self, button):
        """Обработчик сканирования сетей"""
        if self._scan_click_in_progress:
            return
        
        self._scan_click_in_progress = True
        
        # Визуальные изменения
        self.scan_label.add_style_class("scanning")
        self.scan_button.add_style_class("scanning")
        self.scan_button.set_tooltip_text("Сканирование...")
        self.scan_button.set_sensitive(False)
        
        # Запуск сканирования
        if (hasattr(self, 'network_client') and 
            hasattr(self.network_client, 'wifi_device') and 
            self.network_client.wifi_device):
            
            if getattr(self.network_client.wifi_device, 'enabled', False):
                self.network_client.wifi_device.scan()
        
        # Восстановление кнопки через 3 секунды
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
        """Получение списка сохраненных сетей"""
        saved_networks = []
        
        if (hasattr(self, 'network_client') and 
            hasattr(self.network_client, '_client') and 
            self.network_client._client):
            
            try:
                connections = self.network_client._client.get_connections()
                for connection in connections:
                    if connection.get_connection_type() == '802-11-wireless':
                        setting = connection.get_setting_wireless()
                        if setting and setting.get_ssid():
                            ssid = NM.utils_ssid_to_utf8(setting.get_ssid().get_data())
                            if ssid and ssid not in saved_networks:
                                saved_networks.append(ssid)
            except Exception:
                pass
        
        return saved_networks
    
    def get_current_network_ssid(self):
        """Получение SSID текущей сети"""
        if (hasattr(self, 'network_client') and 
            hasattr(self.network_client, 'wifi_device') and 
            self.network_client.wifi_device):
            
            ssid = getattr(self.network_client.wifi_device, 'ssid', None)
            if ssid and ssid not in ["Disconnected", "Выключено", "Не подключено"]:
                return ssid
        
        return None
    
    def refresh_networks(self):
        """Обновление списка сетей"""
        if self._shutdown:
            return
        
        current_ssid = self.get_current_network_ssid()
        saved_networks = self.get_saved_networks()
        
        # Получаем доступные сети
        available_networks = []
        wifi_enabled = False
        
        if (hasattr(self, 'network_client') and 
            hasattr(self.network_client, 'wifi_device') and 
            self.network_client.wifi_device):
            
            wifi_enabled = getattr(self.network_client.wifi_device, 'enabled', False)
            if wifi_enabled:
                try:
                    available_networks = self.network_client.wifi_device.access_points
                except Exception:
                    available_networks = []
        
        # Проверяем изменения
        current_state = {
            'current_ssid': current_ssid,
            'saved_networks': tuple(saved_networks),
            'available_count': len(available_networks),
            'enabled': wifi_enabled
        }
        
        if self._cached_networks == current_state:
            return
        
        self._cached_networks = current_state
        
        # Очищаем старые списки
        for child in list(self.saved_box.get_children()):
            if hasattr(child, 'destroy'):
                child.destroy()
            self.saved_box.remove(child)
        
        for child in list(self.available_box.get_children()):
            if hasattr(child, 'destroy'):
                child.destroy()
            self.available_box.remove(child)
        
        # Создаем слоты для сетей
        self._create_network_slots(current_ssid, saved_networks, available_networks)
        
        # Обновляем отображение
        self.saved_box.show_all()
        self.available_box.show_all()
    
    def _create_network_slots(self, current_ssid, saved_networks, available_networks):
        """Создание слотов для сетей"""
        # Создаем словарь доступных сетей для быстрого доступа
        available_dict = {}
        for ap in available_networks:
            ssid = ap.get("ssid")
            if ssid:
                available_dict[ssid] = ap
        
        # Текущая сеть
        if current_ssid and current_ssid in saved_networks:
            ap_data = available_dict.get(current_ssid, {
                "ssid": current_ssid,
                "strength": 0,
                "is_secured": True,
                "icon-name": "network-wireless-signal-excellent-symbolic"
            })
            slot = WifiNetworkSlot(
                ap_data,
                self.network_client,
                self.get_toplevel(),
                is_saved=True,
                is_connected=True
            )
            self.saved_box.add(slot)
        
        # Другие сохраненные сети
        for ssid in saved_networks:
            if ssid == current_ssid:
                continue
            
            ap_data = available_dict.get(ssid, {
                "ssid": ssid,
                "strength": 0,
                "is_secured": True,
                "icon-name": "network-wireless-signal-none-symbolic"
            })
            slot = WifiNetworkSlot(
                ap_data,
                self.network_client,
                self.get_toplevel(),
                is_saved=True,
                is_connected=False
            )
            self.saved_box.add(slot)
        
        # Доступные (не сохраненные) сети
        for ap in available_networks:
            ssid = ap.get("ssid")
            if ssid and ssid not in saved_networks and ssid != current_ssid:
                slot = WifiNetworkSlot(
                    ap,
                    self.network_client,
                    self.get_toplevel(),
                    is_saved=False,
                    is_connected=False
                )
                self.available_box.add(slot)
    
    def update_network_speeds(self):
        """Обновление скорости сети"""
        if self._shutdown:
            return False
        
        try:
            # Инициализация при первом запуске
            if self.last_counters is None:
                self.last_counters = psutil.net_io_counters()
                self.last_time = time.time()
                return True
            
            # Вычисление скорости
            current_time = time.time()
            elapsed = current_time - self.last_time
            
            if elapsed <= 0:
                return True
            
            current_counters = psutil.net_io_counters()
            
            download_speed = (current_counters.bytes_recv - self.last_counters.bytes_recv) / elapsed
            upload_speed = (current_counters.bytes_sent - self.last_counters.bytes_sent) / elapsed
            
            # Форматирование
            download_str = self._format_speed(download_speed)
            upload_str = self._format_speed(upload_speed)
            
            # Обновление UI
            self.download_label.set_markup(download_str)
            self.upload_label.set_markup(upload_str)
            
            # Определение активных загрузок
            self.downloading = download_speed >= 2e6  # 2 MB/s
            self.uploading = upload_speed >= 2e6  # 2 MB/s
            
            # Обновление стилей
            if self.downloading:
                self._download_urgent()
            elif self.uploading:
                self._upload_urgent()
            else:
                self._remove_urgent()
            
            # Tooltip
            tooltip_text = f"Скачивание: {download_str}\nОтправка: {upload_str}"
            self.download_box.set_tooltip_text(tooltip_text)
            self.upload_box.set_tooltip_text(tooltip_text)
            
            # Обновление счетчиков
            self.last_counters = current_counters
            self.last_time = current_time
            
        except Exception as e:
            # Скрываем элементы при ошибке
            self.download_label.set_visible(False)
            self.upload_label.set_visible(False)
            self.download_icon.set_visible(False)
            self.upload_icon.set_visible(False)
        
        return True
    
    def _format_speed(self, speed: float) -> str:
        """Форматирование скорости"""
        if speed < 1024:
            return f"{speed:.0f} Б/с"
        elif speed < 1024 * 1024:
            return f"{speed / 1024:.1f} КБ/с"
        else:
            return f"{speed / (1024 * 1024):.1f} МБ/с"
    
    def _download_urgent(self):
        """Стиль активной загрузки"""
        self.download_label.add_style_class("urgent")
        self.download_icon.add_style_class("urgent")
        self.upload_icon.remove_style_class("urgent")
        self.upload_label.remove_style_class("urgent")
    
    def _upload_urgent(self):
        """Стиль активной отдачи"""
        self.upload_label.add_style_class("urgent")
        self.upload_icon.add_style_class("urgent")
        self.download_icon.remove_style_class("urgent")
        self.download_label.remove_style_class("urgent")
    
    def _remove_urgent(self):
        """Удаление стилей активности"""
        self.download_label.remove_style_class("urgent")
        self.upload_label.remove_style_class("urgent")
        self.download_icon.remove_style_class("urgent")
        self.upload_icon.remove_style_class("urgent")
    
    def destroy(self):
        """Очистка ресурсов"""
        self._shutdown = True
        
        # Останавливаем таймеры
        if self._refresh_timeout_id:
            GLib.source_remove(self._refresh_timeout_id)
            self._refresh_timeout_id = None
        
        if self._periodic_refresh_id:
            GLib.source_remove(self._periodic_refresh_id)
            self._periodic_refresh_id = None
        
        if self._speed_timer_id:
            GLib.source_remove(self._speed_timer_id)
            self._speed_timer_id = None
        
        # Очищаем таймеры кнопок
        for timer_id in self._pressed_timers:
            GLib.source_remove(timer_id)
        self._pressed_timers.clear()
        
        # Уничтожаем сетевой клиент
        if self.network_client and hasattr(self.network_client, 'destroy'):
            self.network_client.destroy()
        
        # Очищаем списки сетей
        for child in list(self.saved_box.get_children()):
            if hasattr(child, 'destroy'):
                child.destroy()
        
        for child in list(self.available_box.get_children()):
            if hasattr(child, 'destroy'):
                child.destroy()
        
        # Очищаем кэши
        self._cached_networks = None
        self.last_counters = None
        
        # Очищаем ссылки
        self.network_client = None
        self.widgets = None
        
        super().destroy()