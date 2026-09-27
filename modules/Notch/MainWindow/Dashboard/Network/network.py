import subprocess

import gi
from gi.repository import NM, GLib
gi.require_version('NM', '1.0')

from fabric.core.service import Property, Service, Signal
from fabric.utils import bulk_connect

_NM80211_AP_SEC = getattr(NM, "80211ApSecurityFlags")
# KEY_MGMT_SAE (WPA3-Personal) появился в NetworkManager 1.12 (2018).
# На более старых установках libnm этого члена enum нет — getattr с
# default защищает именно от версионного расхождения внешней библиотеки,
# а не подменяет проверку состояния.
_KEY_MGMT_SAE = getattr(_NM80211_AP_SEC, "KEY_MGMT_SAE", 0)


class Wifi(Service):
    @Signal
    def changed(self) -> None: ...

    def __init__(self, client: NM.Client, device: NM.DeviceWifi, **kwargs):
        super().__init__(**kwargs)
        self._client = client
        self._device = device
        self._ap = None
        self._ap_signal = None
        self._destroyed = False

        self._client_signal = client.connect(
            "notify::wireless-enabled", lambda *_: self._notify("enabled"),
        )
        self._device_signals = bulk_connect(device, {
            "notify::active-access-point": lambda *_: self._activate_ap(),
            "access-point-added": lambda *_: self._notify("changed"),
            "access-point-removed": lambda *_: self._notify("changed"),
            "state-changed": lambda *_: self.ap_update(),
        })
        self._activate_ap()

    def cleanup(self):
        if self._destroyed:
            return
        self._destroyed = True
        if self._ap and self._ap_signal:
            self._ap.disconnect(self._ap_signal)
            self._ap_signal = None
        for handler_id in self._device_signals:
            self._device.disconnect(handler_id)
        self._client.disconnect(self._client_signal)

    def _notify(self, name: str):
        self.notify(name)
        self.emit("changed")

    def ap_update(self):
        self._notify("changed")
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
        def on_scanned(device, result, _user_data):
            try:
                device.request_scan_finish(result)
            except GLib.Error:
                # NM отклоняет запрос сканирования, если предыдущее сканирование
                # завершилось менее ~10с назад (rate-limit самого NetworkManager)
                return
            self._notify("changed")

        self._device.request_scan_async(None, on_scanned, None)

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
        if not self._ap: return "Disconnected"
        s = self._ap.get_ssid()
        return NM.utils_ssid_to_utf8(s.get_data()) if s else "Unknown"

    @Property(str, "readable")
    def icon_name(self) -> str:
        if not self._ap: return "network-wireless-disabled-symbolic"

        i = self.internet
        if i == "activated":
            s = self.strength
            if s >= 80: return "network-wireless-signal-excellent-symbolic"
            if s >= 60: return "network-wireless-signal-good-symbolic"
            if s >= 40: return "network-wireless-signal-ok-symbolic"
            if s >= 20: return "network-wireless-signal-weak-symbolic"
            return "network-wireless-signal-none-symbolic"
        return "network-wireless-acquiring-symbolic" if i == "activating" else "network-wireless-offline-symbolic"

    @Property(int, "readable")
    def frequency(self) -> int:
        return self._ap.get_frequency() if self._ap else -1

    @Property(str, "readable")
    def internet(self) -> str:
        a = self._device.get_active_connection()
        if not a: return "deactivated"
        s = a.get_state()
        if s == NM.ActiveConnectionState.ACTIVATED: return "activated"
        if s == NM.ActiveConnectionState.ACTIVATING: return "activating"
        if s == NM.ActiveConnectionState.DEACTIVATING: return "deactivating"
        return "deactivated"

    @Property(list, "readable")
    def access_points(self) -> list:
        device_aps = self._device.get_access_points() if self._device else []
        result = []
        for ap in device_aps:
            s = ap.get_strength()
            result.append({
                "bssid": ap.get_bssid(),
                "last_seen": ap.get_last_seen(),
                "ssid": NM.utils_ssid_to_utf8(ap.get_ssid().get_data()) if ap.get_ssid() else "Unknown",
                "active-ap": self._ap,
                "strength": s,
                "frequency": ap.get_frequency(),
                "icon-name": (
                    "network-wireless-signal-excellent-symbolic" if s >= 80 else
                    "network-wireless-signal-good-symbolic" if s >= 60 else
                    "network-wireless-signal-ok-symbolic" if s >= 40 else
                    "network-wireless-signal-weak-symbolic" if s >= 20 else
                    "network-wireless-signal-none-symbolic"
                )
            })
        return result

    @Property(str, "readable")
    def state(self) -> str:
        s = self._device.get_state()
        if s == NM.DeviceState.ACTIVATED: return "activated"
        if s == NM.DeviceState.DISCONNECTED: return "disconnected"
        if s == NM.DeviceState.UNAVAILABLE: return "unavailable"
        return "unknown"


class Ethernet(Service):
    @Signal
    def changed(self) -> None: ...

    def __init__(self, client: NM.Client, device: NM.DeviceEthernet, **kwargs) -> None:
        super().__init__(**kwargs)
        self._client = client
        self._device = device
        self._destroyed = False
        self._device_signal = device.connect(
            "notify::active-connection", lambda *_: self.emit("changed"),
        )

    def cleanup(self):
        if self._destroyed:
            return
        self._destroyed = True
        self._device.disconnect(self._device_signal)

    @Property(str, "readable")
    def internet(self) -> str:
        a = self._device.get_active_connection()
        if not a: return "disconnected"
        s = a.get_state()
        return "activated" if s == NM.ActiveConnectionState.ACTIVATED else "activating" if s == NM.ActiveConnectionState.ACTIVATING else "disconnected"

    @Property(str, "readable")
    def icon_name(self) -> str:
        return "network-wired-symbolic" if self.internet == "activated" else "network-wired-disconnected-symbolic"


class NetworkClient(Service):
    @Signal
    def device_ready(self) -> None: ...
    @Signal
    def connection_error(self, ssid: str, error_message: str) -> None: ...

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._client = None
        self.wifi_device = None
        self.ethernet_device = None
        self._wifi_fail_signal = None
        self._destroyed = False
        GLib.idle_add(self._init_worker)

    def _init_worker(self, *_args) -> bool:
        self._client = NM.Client.new(None)
        self._setup()
        return False

    def _setup(self):
        for device in self._client.get_devices() or []:
            device_type = device.get_device_type()
            if device_type == NM.DeviceType.WIFI and not self.wifi_device:
                self.wifi_device = Wifi(self._client, device)
                self._wifi_fail_signal = device.connect("state-changed", self._on_wifi_fail)
            elif device_type == NM.DeviceType.ETHERNET and not self.ethernet_device:
                self.ethernet_device = Ethernet(self._client, device)
        self.emit("device-ready")

    def cleanup(self):
        if self._destroyed:
            return
        self._destroyed = True
        if self.wifi_device:
            self.wifi_device._device.disconnect(self._wifi_fail_signal)
            self.wifi_device.cleanup()
        if self.ethernet_device:
            self.ethernet_device.cleanup()

    @Property(str, "readable")
    def primary_device(self) -> str:
        if self.ethernet_device and self.ethernet_device.internet in ("activated", "activating"):
            return "ethernet"
        if self.wifi_device and self.wifi_device.enabled and self.wifi_device.ssid not in ("", "Disconnected"):
            return "wifi"
        return "none"

    def _on_wifi_fail(self, device, state, *_args):
        if state == NM.DeviceState.FAILED:
            self.emit("connection_error", self.wifi_device.ssid, "Error")

    def is_network_available(self, ssid: str) -> bool:
        if not self.wifi_device:
            return False
        return any(ap["ssid"] == ssid for ap in self.wifi_device.access_points)

    def connect_to_saved_network(self, ssid: str, success_cb=None, error_cb=None) -> bool:
        if not self._client or not self.is_network_available(ssid):
            return False

        device = self.wifi_device._device
        for connection in self._client.get_connections():
            if connection.get_connection_type() != "802-11-wireless":
                continue
            s_wifi = connection.get_setting_wireless()
            if not s_wifi:
                continue
            if NM.utils_ssid_to_utf8(s_wifi.get_ssid().get_data()) != ssid:
                continue

            def on_activated(client, result, _user_data):
                try:
                    client.activate_connection_finish(result)
                except GLib.Error as error:
                    # сохранённый пароль отвергнут точкой доступа, либо сеть пропала
                    # из зоны видимости между проверкой доступности и активацией
                    if error_cb:
                        error_cb(ssid, error.message)
                    return
                if success_cb:
                    success_cb(ssid)

            self._client.activate_connection_async(connection, device, None, None, on_activated, None)
            return True
        return False

    def connect_to_new_network(self, ssid: str, password: str, success_cb=None, error_cb=None) -> bool:
        if not self._client or not self.wifi_device:
            return False

        device = self.wifi_device._device
        ap = next(
            (a for a in device.get_access_points()
             if a.get_ssid() and NM.utils_ssid_to_utf8(a.get_ssid().get_data()) == ssid),
            None,
        )
        if ap is None:
            return False

        security_flags = ap.get_wpa_flags() | ap.get_rsn_flags()
        if security_flags & _NM80211_AP_SEC.KEY_MGMT_802_1X:
            if error_cb:
                error_cb(ssid, "Enterprise (802.1X) networks require credentials beyond a password")
            return False
        if security_flags & _KEY_MGMT_SAE:
            key_mgmt = "sae"
        elif security_flags & _NM80211_AP_SEC.KEY_MGMT_PSK:
            key_mgmt = "wpa-psk"
        else:
            # Осознанно не поддерживаем WEP: он практически не встречается в реальном
            # эфире с ~2010-х, а его ключ хранится/кодируется иначе (wep-key0), чем PSK —
            # усложнять путь ради вымершего протокола не оправдано.
            if error_cb:
                error_cb(ssid, "Open or WEP networks are not supported via password entry")
            return False

        connection = NM.SimpleConnection.new()

        s_con = NM.SettingConnection.new()
        s_con.set_property(NM.SETTING_CONNECTION_ID, ssid)
        s_con.set_property(NM.SETTING_CONNECTION_TYPE, "802-11-wireless")
        connection.add_setting(s_con)

        s_wifi = NM.SettingWireless.new()
        s_wifi.set_property(NM.SETTING_WIRELESS_SSID, GLib.Bytes.new(ssid.encode("utf-8")))
        s_wifi.set_property(NM.SETTING_WIRELESS_MODE, "infrastructure")
        connection.add_setting(s_wifi)

        s_sec = NM.SettingWirelessSecurity.new()
        s_sec.set_property(NM.SETTING_WIRELESS_SECURITY_KEY_MGMT, key_mgmt)
        s_sec.set_property(NM.SETTING_WIRELESS_SECURITY_PSK, password)
        connection.add_setting(s_sec)

        def on_activated(client, result, _user_data):
            try:
                client.add_and_activate_connection_finish(result)
            except GLib.Error as error:
                # неверный пароль или точка доступа пропала во время хендшейка
                if error_cb:
                    error_cb(ssid, error.message)
                return
            if success_cb:
                success_cb(ssid)

        self._client.add_and_activate_connection_async(
            connection, device, ap.get_path(), None, on_activated, None,
        )
        return True

    def delete_saved_network(self, ssid: str) -> bool:
        if not self._client:
            return False
        for c in self._client.get_connections():
            if c.get_connection_type() != "802-11-wireless":
                continue
            s_w = c.get_setting_wireless()
            if not s_w or NM.utils_ssid_to_utf8(s_w.get_ssid().get_data()) != ssid:
                continue
            try:
                c.delete()
            except GLib.Error:
                # профиль уже удалён другим клиентом NM (например, nm-applet)
                # между нашей проверкой и вызовом delete()
                return False
            return True
        return False

    def disconnect_network(self) -> bool:
        if not self.wifi_device:
            return False
        active_conn = self.wifi_device._device.get_active_connection()
        if not active_conn:
            return False
        try:
            self._client.deactivate_connection(active_conn, None)
        except GLib.Error:
            # соединение уже деактивировано параллельно (точка доступа пропала,
            # либо деактивация уже была инициирована другим клиентом) до того,
            # как наш запрос дошёл до D-Bus
            return False
        return True

    def get_network_password(self, ssid: str) -> str:
        try:
            result = subprocess.check_output(
                ["nmcli", "-s", "-g", "802-11-wireless-security.psk", "connection", "show", ssid],
                stderr=subprocess.DEVNULL,
            ).decode("utf-8").strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            # профиль без PSK (открытая сеть) либо nmcli не установлен в системе
            return ""
        return result

    def get_network_details(self, ssid: str) -> dict:
        details = {
            "connected": False,
            "strength": "Unknown",
            "frequency": "Unknown",
            "security": "Open",
            "type": "Unknown",
            "ip": "N/A",
            "gateway": "N/A",
            "dns": "N/A",
        }

        if self._client:
            for c in self._client.get_connections():
                s_w = c.get_setting_wireless()
                if s_w and NM.utils_ssid_to_utf8(s_w.get_ssid().get_data()) == ssid:
                    s_sec = c.get_setting_wireless_security()
                    if s_sec:
                        key_mgmt = s_sec.get_key_mgmt()
                        details["security"] = str(key_mgmt).upper() if key_mgmt else "Secured"
                    break

        if not self.wifi_device:
            return details

        dev = self.wifi_device._device
        is_active = False

        active_ap = dev.get_active_access_point()
        if active_ap and active_ap.get_ssid():
            active_ssid = NM.utils_ssid_to_utf8(active_ap.get_ssid().get_data())
            if active_ssid == ssid:
                is_active = True
                details["connected"] = True

        for ap in dev.get_access_points():
            if ap.get_ssid() and NM.utils_ssid_to_utf8(ap.get_ssid().get_data()) == ssid:
                details["strength"] = f"{ap.get_strength()}%"
                freq = ap.get_frequency()
                details["frequency"] = f"{freq/1000:.1f} GHz" if freq > 0 else "Unknown"

                if freq > 5000: details["type"] = "Wi-Fi 5 / 6"
                elif freq > 0: details["type"] = "Wi-Fi 4"
                break

        if is_active:
            active_conn = dev.get_active_connection()
            if active_conn:
                ip4 = active_conn.get_ip4_config()
                if ip4:
                    addrs = ip4.get_addresses()
                    if addrs: details["ip"] = addrs[0].get_address()

                    gw = ip4.get_gateway()
                    details["gateway"] = gw if gw else "N/A"

                    dns_list = ip4.get_nameservers()
                    if dns_list: details["dns"] = ", ".join(dns_list)

        return details