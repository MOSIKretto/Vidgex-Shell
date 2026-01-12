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

    def __init__(self, client: NM.Client, device: NM.DeviceWifi, **kwargs):
        self._client = client
        self._device = device
        self._ap = None
        self._ap_signal = None
        super().__init__(**kwargs)

        self._client.connect("notify::wireless-enabled", lambda *_: self._notify("enabled"))

        if self._device:
            bulk_connect(self._device, {
                "notify::active-access-point": lambda *_: self._activate_ap(),
                "access-point-added": lambda *_: self._notify("changed"),
                "access-point-removed": lambda *_: self._notify("changed"),
                "state-changed": lambda *_: self.ap_update(),
            })
            self._activate_ap()

    def _notify(self, name: str):
        self.notify(name)
        self.emit("changed")

    def ap_update(self):
        self._notify("changed")
        # Direct notifications without creating intermediate objects
        self.notify("enabled")
        self.notify("internet")
        self.notify("strength")
        self.notify("frequency")
        self.notify("access-points")
        self.notify("ssid")
        self.notify("state")
        self.notify("icon_name")

    def _activate_ap(self):
        if self._ap and self._ap_signal:
            self._ap.disconnect(self._ap_signal)
        self._ap = self._device.get_active_access_point()
        if self._ap:
            self._ap_signal = self._ap.connect("notify::strength", lambda *_: self.ap_update())

    def toggle_wifi(self):
        self.enabled = not self.enabled

    def scan(self):
        self._device.request_scan_async(None, lambda dev, res: [dev.request_scan_finish(res), self._notify("changed")])

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
        ssid_bytes = self._ap.get_ssid()
        return NM.utils_ssid_to_utf8(ssid_bytes.get_data()) if ssid_bytes else "Unknown"

    @Property(str, "readable")
    def icon_name(self) -> str:
        if not self._ap: 
            return "network-wireless-disabled-symbolic"
        
        internet_state = self.internet
        if internet_state == "activated":
            strength = self.strength
            if strength >= 80: 
                return "network-wireless-signal-excellent-symbolic"
            if strength >= 60: 
                return "network-wireless-signal-good-symbolic"
            if strength >= 40: 
                return "network-wireless-signal-ok-symbolic"
            if strength >= 20: 
                return "network-wireless-signal-weak-symbolic"
            return "network-wireless-signal-none-symbolic"
        return ("network-wireless-acquiring-symbolic" 
                if internet_state == "activating" 
                else "network-wireless-offline-symbolic")

    @Property(int, "readable")
    def frequency(self) -> int:
        return self._ap.get_frequency() if self._ap else -1

    @Property(str, "readable")
    def internet(self) -> str:
        active_conn = self._device.get_active_connection()
        if not active_conn: 
            return "deactivated"
        state = active_conn.get_state()
        if state == NM.ActiveConnectionState.ACTIVATED: 
            return "activated"
        if state == NM.ActiveConnectionState.ACTIVATING: 
            return "activating"
        if state == NM.ActiveConnectionState.DEACTIVATING: 
            return "deactivating"
        return "deactivated"

    @Property(list, "readable")
    def access_points(self) -> list:
        # Optimized generator to save memory
        access_points = self._device.get_access_points() if self._device else []
        result = []
        for ap in access_points:
            strength = ap.get_strength()
            result.append({
                "bssid": ap.get_bssid(),
                "last_seen": ap.get_last_seen(),
                "ssid": NM.utils_ssid_to_utf8(ap.get_ssid().get_data()) if ap.get_ssid() else "Unknown",
                "active-ap": self._ap,
                "strength": strength,
                "frequency": ap.get_frequency(),
                "icon-name": self._get_signal_icon(strength)
            })
        return result

    def _get_signal_icon(self, strength: int) -> str:
        """Helper method to determine icon based on signal strength."""
        if strength >= 80:
            return "network-wireless-signal-excellent-symbolic"
        if strength >= 60:
            return "network-wireless-signal-good-symbolic"
        if strength >= 40:
            return "network-wireless-signal-ok-symbolic"
        if strength >= 20:
            return "network-wireless-signal-weak-symbolic"
        return "network-wireless-signal-none-symbolic"

    @Property(str, "readable")
    def state(self) -> str:
        device_state = self._device.get_state()
        if device_state == NM.DeviceState.ACTIVATED: 
            return "activated"
        if device_state == NM.DeviceState.DISCONNECTED: 
            return "disconnected"
        if device_state == NM.DeviceState.UNAVAILABLE: 
            return "unavailable"
        return "unknown"

class Ethernet(Service):
    @Signal
    def changed(self) -> None: ...

    def __init__(self, client: NM.Client, device: NM.DeviceEthernet, **kwargs) -> None:
        super().__init__(**kwargs)
        self._client = client
        self._device = device
        self._device.connect("notify::active-connection", lambda *_: self.emit("changed"))

    @Property(str, "readable")
    def internet(self) -> str:
        active_conn = self._device.get_active_connection()
        if not active_conn: 
            return "disconnected"
        state = active_conn.get_state()
        if state == NM.ActiveConnectionState.ACTIVATED: 
            return "activated"
        if state == NM.ActiveConnectionState.ACTIVATING: 
            return "activating"
        return "disconnected"

    @Property(str, "readable")
    def icon_name(self) -> str:
        return ("network-wired-symbolic" 
                if self.internet == "activated" 
                else "network-wired-disconnected-symbolic")

class NetworkClient(Service):
    @Signal
    def device_ready(self) -> None: ...
    @Signal  
    def connection_error(self, ssid: str, error_message: str) -> None: ...

    def __init__(self, **kwargs):
        self._client = None
        self.wifi_device = None
        self.ethernet_device = None
        super().__init__(**kwargs)
        # Инициализация напрямую через поток для отзывчивости
        GLib.Thread.new(None, self._init_worker, None)

    def _init_worker(self, _):
        try:
            self._client = NM.Client.new(None)
            GLib.idle_add(self._setup)
        except Exception:
            GLib.idle_add(self._setup_fallback)

    def _setup(self):
        # Find devices without unnecessary abstractions
        devices = self._client.get_devices() or []
        for device in devices:
            dev_type = device.get_device_type()
            if dev_type == NM.DeviceType.WIFI and not self.wifi_device:
                self.wifi_device = Wifi(self._client, device)
                device.connect("state-changed", self._on_wifi_fail)
            elif dev_type == NM.DeviceType.ETHERNET and not self.ethernet_device:
                self.ethernet_device = Ethernet(self._client, device)
        self.emit("device-ready")

    def _setup_fallback(self):
        # Lightweight object instead of entire class
        self.wifi_device = type('Fallback', (), {
            'enabled': False, 
            'strength': 0, 
            'ssid': 'Disconnected', 
            'access_points': [], 
            'scan': lambda: None
        })()
        self.emit("device-ready")

    @Property(str, "readable")
    def primary_device(self) -> str:
        if (self.ethernet_device and 
            self.ethernet_device.internet in ("activated", "activating")):
            return "ethernet"
        if (self.wifi_device and 
            getattr(self.wifi_device, 'enabled', False) and 
            getattr(self.wifi_device, 'ssid', "") not in ("", "Disconnected")):
            return "wifi"
        return "none"

    def _on_wifi_fail(self, dev, state, *_):
        if state == NM.DeviceState.FAILED:
            ssid = getattr(self.wifi_device, 'ssid', "Unknown")
            self.emit("connection_error", ssid, "Error")

    def is_network_available(self, ssid: str) -> bool:
        return any(ap.get("ssid") == ssid for ap in getattr(self.wifi_device, 'access_points', []))

    def connect_to_saved_network(self, ssid: str, success_cb=None, error_cb=None) -> bool:
        if not self._client or not self.is_network_available(ssid): 
            return False
        for conn in (self._client.get_connections() or []):
            if conn.get_connection_type() == "802-11-wireless":
                wireless_setting = conn.get_setting_wireless()
                if (wireless_setting and 
                    NM.utils_ssid_to_utf8(wireless_setting.get_ssid().get_data()) == ssid):
                    self._client.activate_connection_async(
                        conn, 
                        getattr(self.wifi_device, "_device", None), 
                        None, 
                        None, 
                        None, 
                        None
                    )
                    if success_cb: 
                        success_cb()
                    return True
        return False

    def connect_to_new_network(self, ssid: str, password: str, success_cb=None, error_cb=None) -> bool:
        exec_shell_command_async(
            f'nmcli dev wifi connect "{ssid}" password "{password}"', 
            lambda ok, out, err: (
                success_cb() if ok and success_cb 
                else error_cb(ssid, err) if error_cb 
                else None
            )
        )
        return True

    def delete_saved_network(self, ssid: str) -> bool:
        for conn in (self._client.get_connections() if self._client else []):
            if conn.get_connection_type() == "802-11-wireless":
                wireless_setting = conn.get_setting_wireless()
                if (wireless_setting and 
                    NM.utils_ssid_to_utf8(wireless_setting.get_ssid().get_data()) == ssid):
                    conn.delete()
                    return True
        return False