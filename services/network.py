from fabric.core.service import Property, Service, Signal
from fabric.utils import exec_shell_command_async, bulk_connect

import gi
from gi.repository import NM, GLib
gi.require_version('Gtk', '3.0')
gi.require_version('NM', '1.0')


class Wifi(Service):
    @Signal
    def changed(self) -> None: ...

    @Signal
    def enabled(self) -> bool: ...

    STRENGTH_ICONS = {
        80: "network-wireless-signal-excellent-symbolic",
        60: "network-wireless-signal-good-symbolic",
        40: "network-wireless-signal-ok-symbolic",
        20: "network-wireless-signal-weak-symbolic",
        0:  "network-wireless-signal-none-symbolic",
    }

    INTERNET_STATES = {
        NM.ActiveConnectionState.ACTIVATED: "activated",
        NM.ActiveConnectionState.ACTIVATING: "activating",
        NM.ActiveConnectionState.DEACTIVATING: "deactivating",
        NM.ActiveConnectionState.DEACTIVATED: "deactivated",
    }

    DEVICE_STATES = {
        NM.DeviceState.UNMANAGED: "unmanaged",
        NM.DeviceState.UNAVAILABLE: "unavailable",
        NM.DeviceState.DISCONNECTED: "disconnected",
        NM.DeviceState.PREPARE: "prepare",
        NM.DeviceState.CONFIG: "config",
        NM.DeviceState.NEED_AUTH: "need_auth",
        NM.DeviceState.IP_CONFIG: "ip_config",
        NM.DeviceState.IP_CHECK: "ip_check",
        NM.DeviceState.SECONDARIES: "secondaries",
        NM.DeviceState.ACTIVATED: "activated",
        NM.DeviceState.DEACTIVATING: "deactivating",
        NM.DeviceState.FAILED: "failed",
    }

    def __init__(self, client: NM.Client, device: NM.DeviceWifi, **kwargs):
        self._client: NM.Client = client
        self._device: NM.DeviceWifi = device
        self._ap: NM.AccessPoint | None = None
        self._ap_signal: int | None = None
        super().__init__(**kwargs)

        self._client.connect("notify::wireless-enabled", lambda *a: self._notify("enabled"))

        if self._device:
            bulk_connect(self._device, {
                "notify::active-access-point": lambda *a: self._activate_ap(),
                "access-point-added": lambda *a: self._notify("changed"),
                "access-point-removed": lambda *a: self._notify("changed"),
                "state-changed": lambda *a: self.ap_update(),
            })
            self._activate_ap()

    def _notify(self, name: str):
        self.notify(name)
        self.emit("changed")

    def ap_update(self):
        self._notify("changed")
        for prop in ["enabled", "internet", "strength", "frequency",
                     "access-points", "ssid", "state", "icon_name"]:
            self.notify(prop)

    def _activate_ap(self):
        if self._ap and self._ap_signal:
            self._ap.disconnect(self._ap_signal)
        self._ap = self._device.get_active_access_point()
        if self._ap:
            self._ap_signal = self._ap.connect("notify::strength", lambda *a: self.ap_update())

    def toggle_wifi(self):
        self.enabled = not self.enabled

    def scan(self):
        self._device.request_scan_async(None,
            lambda device, result: [device.request_scan_finish(result), self._notify("changed")]
        )

    @Property(bool, "read-write", default_value=False)
    def enabled(self) -> bool:
        return bool(self._client.wireless_get_enabled())

    @enabled.setter
    def enabled(self, value: bool):
        self._client.wireless_set_enabled(value)

    @Property(int, "readable")
    def strength(self) -> int:
        return self._ap.get_strength() if self._ap else -1

    @Property(str, "readable")
    def ssid(self) -> str:
        if not self._ap:
            return "Disconnected"
        ssid_data = self._ap.get_ssid().get_data() if self._ap.get_ssid() else None
        return NM.utils_ssid_to_utf8(ssid_data) if ssid_data else "Unknown"

    @Property(str, "readable")
    def icon_name(self) -> str:
        if not self._ap:
            return "network-wireless-disabled-symbolic"

        if self.internet == "activated":
            strength = min(80, 20 * round(self.strength / 20))
            return self.STRENGTH_ICONS.get(strength, "network-wireless-no-route-symbolic")
        if self.internet == "activating":
            return "network-wireless-acquiring-symbolic"

        return "network-wireless-offline-symbolic"

    @Property(int, "readable")
    def frequency(self) -> int:
        return self._ap.get_frequency() if self._ap else -1

    @Property(str, "readable")
    def internet(self) -> str:
        active = self._device.get_active_connection()
        state = active.get_state() if active else None
        return self.INTERNET_STATES.get(state, "unknown")

    @Property(list, "readable")
    def access_points(self) -> list[dict]:
        aps = self._device.get_access_points() if self._device else []
        result = []
        for ap in aps:
            ssid = NM.utils_ssid_to_utf8(ap.get_ssid().get_data()) if ap.get_ssid() else "Unknown"
            strength = ap.get_strength()
            icon = self.STRENGTH_ICONS.get(min(80, 20 * round(strength / 20)), "network-wireless-no-route-symbolic")
            result.append({
                "bssid": ap.get_bssid(),
                "last_seen": ap.get_last_seen(),
                "ssid": ssid,
                "active-ap": self._ap,
                "strength": strength,
                "frequency": ap.get_frequency(),
                "icon-name": icon,
            })
        return result

    @Property(str, "readable")
    def state(self) -> str:
        return self.DEVICE_STATES.get(self._device.get_state(), "unknown")


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

    def __init__(self, **kwargs):
        self._client = None
        self.wifi_device = None
        self.ethernet_device = None
        self._connection_callbacks: dict[str, dict] = {}
        super().__init__(**kwargs)
        self._initialize()

    @Property(str, "readable")
    def primary_device(self) -> str:
        if getattr(self.ethernet_device, 'internet', None) in ["activated", "activating"]:
            return "ethernet"
        if getattr(self.wifi_device, 'enabled', False) and getattr(self.wifi_device, 'ssid', None) not in [None, "Disconnected", "Выключено", "Не подключено"]:
            return "wifi"
        return "none"

    def _initialize(self):
        def worker(_):
            try:
                self._client = NM.Client.new(None)
                GLib.idle_add(self._on_initialized)
            except Exception:
                GLib.idle_add(self._create_fallback_client)
        GLib.Thread.new("nm-init", worker, None)

    def _on_initialized(self):
        self._initialize_devices()
        self.emit("device-ready")
        return False

    def _initialize_devices(self):
        wifi_dev = self._get_device(NM.DeviceType.WIFI)
        if wifi_dev:
            self.wifi_device = Wifi(self._client, wifi_dev)
            wifi_dev.connect("state-changed", self._on_wifi_state_changed)

        eth_dev = self._get_device(NM.DeviceType.ETHERNET)
        if eth_dev:
            self.ethernet_device = Ethernet(self._client, eth_dev)

    def _get_device(self, device_type: NM.DeviceType):
        devices = getattr(self._client, "get_devices", lambda: [])()
        return next((d for d in devices if d.get_device_type() == device_type), None)

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

    def _on_wifi_state_changed(self, device, new_state, *_):
        ssid = getattr(self.wifi_device, 'ssid', None)
        if new_state == NM.DeviceState.FAILED and ssid:
            self.emit("connection_error", ssid, f"Ошибка подключения (код: {device.get_state()})")

    def is_network_available(self, ssid: str) -> bool:
        aps = getattr(self.wifi_device, 'access_points', [])
        return any(ap.get("ssid") == ssid for ap in aps)

    def _get_connection_by_ssid(self, ssid: str):
        if not self._client: return None
        for conn in getattr(self._client, "get_connections", lambda: [])():
            if conn.get_connection_type() != "802-11-wireless":
                continue
            setting = conn.get_setting_wireless()
            if setting and setting.get_ssid():
                conn_ssid = NM.utils_ssid_to_utf8(setting.get_ssid().get_data())
                if conn_ssid == ssid:
                    return conn
        return None

    def _connect(self, ssid: str, connection, device=None):
        if not self._client or not connection:
            self._fire_error(ssid, "Клиент не инициализирован или сеть не найдена")
            return False

        self._connection_callbacks[ssid] = self._connection_callbacks.get(ssid, {})
        self._client.activate_connection_async(connection, device, None, None, None, None)
        return True

    def connect_to_saved_network(self, ssid: str, success_callback=None, error_callback=None) -> bool:
        self._connection_callbacks[ssid] = {'success_callback': success_callback, 'error_callback': error_callback}
        if not self.is_network_available(ssid):
            self._fire_error(ssid, f"Сеть '{ssid}' недоступна")
            return False
        conn = self._get_connection_by_ssid(ssid)
        return self._connect(ssid, conn, getattr(self.wifi_device, "_device", None))

    def connect_to_new_network(self, ssid: str, password: str, success_callback=None, error_callback=None) -> bool:
        """Проверяет подключение с паролем и только при успехе сохраняет профиль"""
        def callback(ok, output, error):
            if ok and ("activated" in output.lower() or "successfully activated" in output.lower()):
                # Подключение успешно - теперь можно уведомить об успехе
                if success_callback:
                    success_callback(ssid)
            else:
                # Удаляем профиль, если подключение не удалось
                self.delete_saved_network(ssid)
                # Проверяем тип ошибки для правильного сообщения
                error_msg = f"Неверный пароль для {ssid}" if (
                    "secret" in error.lower() or 
                    "password" in error.lower() or 
                    "auth" in error.lower() or 
                    "802.1X" in error.lower()
                ) else f"Ошибка подключения: {error}"
                
                # Также генерируем сигнал ошибки для UI
                self.emit("connection_error", ssid, error_msg)
                
                if error_callback:
                    error_callback(ssid, error_msg)
        
        # Сначала пробуем подключиться временным способом, чтобы проверить пароль
        # Если подключение успешно, NetworkManager автоматически создаст профиль
        cmd = f'nmcli device wifi connect "{ssid}" password "{password}"'
        exec_shell_command_async(cmd, callback)
        return True



    def _fire_error(self, ssid: str, msg: str):
        cb = self._connection_callbacks.get(ssid, {})
        if cb.get('error_callback'): cb['error_callback'](ssid, msg)

    def delete_saved_network(self, ssid: str) -> bool:
        conn = self._get_connection_by_ssid(ssid)
        if conn:
            conn.delete()
            return True
        return False

    def _get_current_connection_ssid(self):
        return getattr(self.wifi_device, 'ssid', None)