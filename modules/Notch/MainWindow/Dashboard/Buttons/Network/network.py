from bisect import bisect_right

import gi

gi.require_version("NM", "1.0")
from gi.repository import GLib, NM

from fabric.core.service import Property, Service, Signal


_SEC = getattr(NM, "80211ApSecurityFlags")
_AP_FLAGS = getattr(NM, "80211ApFlags")

_CONN_STATES = {
    NM.ActiveConnectionState.ACTIVATED: "activated",
    NM.ActiveConnectionState.ACTIVATING: "activating",
    NM.ActiveConnectionState.DEACTIVATING: "deactivating",
}
_DEVICE_STATES = {
    NM.DeviceState.ACTIVATED: "activated",
    NM.DeviceState.DISCONNECTED: "disconnected",
    NM.DeviceState.UNAVAILABLE: "unavailable",
}
_SIGNAL_THRESHOLDS = (20, 40, 60, 80)
_SIGNAL_ICONS = tuple(
    f"network-wireless-signal-{level}-symbolic"
    for level in ("none", "weak", "ok", "good", "excellent")
)
_WIFI_STATE_ICONS = {"activating": "network-wireless-acquiring-symbolic"}


def strength_icon(strength: int) -> str:
    return _SIGNAL_ICONS[bisect_right(_SIGNAL_THRESHOLDS, strength)]

def _ssid(raw) -> str:
    if raw is None or raw.get_size() == 0:
        return ""
    return NM.utils_ssid_to_utf8(raw.get_data())

def _ssid_is(raw, ssid: str) -> bool:
    return bool(ssid) and _ssid(raw) == ssid

def _key_mgmt(ap) -> str | None:
    flags = ap.get_wpa_flags() | ap.get_rsn_flags()
    if flags & _SEC.KEY_MGMT_802_1X:
        return "wpa-eap"
    if flags & _SEC.KEY_MGMT_SAE:
        return "sae"
    if flags & _SEC.KEY_MGMT_PSK:
        return "wpa-psk"
    return None

def _is_secured(ap) -> bool:
    return bool(ap.get_flags() & _AP_FLAGS.PRIVACY or ap.get_wpa_flags() or ap.get_rsn_flags())

def _conn_state(device) -> str:
    conn = device.get_active_connection()
    return "deactivated" if conn is None else _CONN_STATES.get(conn.get_state(), "deactivated")

def _best_aps(device) -> dict:
    best = {}
    for ap in device.get_access_points():
        ssid = _ssid(ap.get_ssid())
        if not ssid:
            continue
        current = best.get(ssid)
        if current is None or ap.get_strength() > current.get_strength():
            best[ssid] = ap
    return best


class Subscriptions:
    __slots__ = ("_items",)

    def __init__(self):
        self._items = []

    def connect(self, obj, signal: str, callback) -> None:
        self._items.append((obj, obj.connect(signal, callback)))

    def release(self, target) -> None:
        kept = []
        for obj, handler_id in self._items:
            if obj is target:
                obj.disconnect(handler_id)
            else:
                kept.append((obj, handler_id))
        self._items = kept

    def rebind(self, old, new, signal: str, callback):
        if new is not old:
            if old is not None:
                self.release(old)
            if new is not None:
                self.connect(new, signal, callback)
        return new

    def clear(self) -> None:
        while self._items:
            obj, handler_id = self._items.pop()
            obj.disconnect(handler_id)


class _Subscriber(Service):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._subs = Subscriptions()
        self._destroyed = False

    def _connect(self, obj, signal: str, callback):
        self._subs.connect(obj, signal, callback)

    def _release(self, target):
        self._subs.release(target)

    def cleanup(self):
        self._destroyed = True
        self._subs.clear()


class _NMDevice(_Subscriber):
    def __init__(self, device: NM.Device, **kwargs):
        super().__init__(**kwargs)
        self._device = device

    def _compute(self) -> dict:
        raise NotImplementedError

    def _refresh(self, *_) -> None:
        fresh = self._compute()
        changed = [name for name, value in fresh.items() if self._cache[name] != value]
        if not changed:
            return
        self._cache = fresh
        for name in changed:
            self.notify(name)
        self.emit("changed")


class Wifi(_NMDevice):
    @Signal
    def changed(self) -> None: ...

    @Signal
    def failed(self) -> None: ...

    def __init__(self, client: NM.Client, device: NM.DeviceWifi, **kwargs):
        super().__init__(device, **kwargs)
        self._client = client
        self._ap = None
        self._ap_list_source = None
        self._track_ap()
        self._cache = self._compute()
        self._connect(client, "notify::wireless-enabled", self._refresh)
        self._connect(device, "notify::active-access-point", self._on_active_ap)
        self._connect(device, "state-changed", self._on_state_changed)
        for signal in ("access-point-added", "access-point-removed"):
            self._connect(device, signal, self._schedule_ap_list)

    def cleanup(self):
        if self._ap_list_source is not None:
            GLib.source_remove(self._ap_list_source)
            self._ap_list_source = None
        self._ap = None
        super().cleanup()

    def _compute(self) -> dict:
        ap = self._ap
        internet = _conn_state(self._device)
        strength = -1 if ap is None else ap.get_strength()
        if ap is None:
            icon = "network-wireless-disabled-symbolic"
        elif internet == "activated":
            icon = strength_icon(strength)
        else:
            icon = _WIFI_STATE_ICONS.get(internet, "network-wireless-offline-symbolic")
        return {
            "enabled": self._client.wireless_get_enabled(),
            "internet": internet,
            "strength": strength,
            "frequency": -1 if ap is None else ap.get_frequency(),
            "ssid": "Disconnected" if ap is None else _ssid(ap.get_ssid()),
            "state": _DEVICE_STATES.get(self._device.get_state(), "unknown"),
            "icon-name": icon,
        }

    def _schedule_ap_list(self, *_):
        if self._ap_list_source is None:
            self._ap_list_source = GLib.idle_add(self._flush_ap_list)

    def _flush_ap_list(self) -> bool:
        self._ap_list_source = None
        self.notify("access-points")
        self.emit("changed")
        return False

    def _track_ap(self):
        if self._ap is not None:
            self._release(self._ap)
        self._ap = self._device.get_active_access_point()
        if self._ap is not None:
            self._connect(self._ap, "notify::strength", self._refresh)

    def _on_active_ap(self, *_):
        self._track_ap()
        self._refresh()
        self._schedule_ap_list()

    def _on_state_changed(self, _device, state, *_):
        self._refresh()
        if state == NM.DeviceState.FAILED:
            self.emit("failed")

    def ap_update(self):
        self._refresh()

    def toggle_wifi(self):
        self.enabled = not self.enabled

    def scan(self):
        self._device.request_scan_async(None, self._on_scanned)

    def _on_scanned(self, device, result):
        if self._destroyed:
            return
        try:
            device.request_scan_finish(result)
        except GLib.Error:
            return
        self._schedule_ap_list()

    @Property(bool, "read-write", default_value=False)
    def enabled(self) -> bool:
        return self._cache["enabled"]

    @enabled.setter
    def enabled(self, value: bool):
        self._client.wireless_set_enabled(value)

    @Property(int, "readable")
    def strength(self) -> int:
        return self._cache["strength"]

    @Property(int, "readable")
    def frequency(self) -> int:
        return self._cache["frequency"]

    @Property(str, "readable")
    def ssid(self) -> str:
        return self._cache["ssid"]

    @Property(str, "readable")
    def internet(self) -> str:
        return self._cache["internet"]

    @Property(str, "readable")
    def state(self) -> str:
        return self._cache["state"]

    @Property(str, "readable")
    def icon_name(self) -> str:
        return self._cache["icon-name"]

    @Property(list, "readable")
    def access_points(self) -> list:
        return [self._ap_info(ssid, ap) for ssid, ap in _best_aps(self._device).items()]

    def _ap_info(self, ssid: str, ap) -> dict:
        strength = ap.get_strength()
        return {
            "bssid": ap.get_bssid(),
            "last_seen": ap.get_last_seen(),
            "ssid": ssid,
            "active-ap": self._ap,
            "strength": strength,
            "frequency": ap.get_frequency(),
            "is_secured": _is_secured(ap),
            "icon-name": strength_icon(strength),
        }


class Ethernet(_NMDevice):
    @Signal
    def changed(self) -> None: ...

    def __init__(self, device: NM.DeviceEthernet, **kwargs):
        super().__init__(device, **kwargs)
        self._cache = self._compute()
        self._connect(device, "notify::active-connection", self._refresh)
        self._connect(device, "state-changed", self._refresh)

    def _compute(self) -> dict:
        state = _conn_state(self._device)
        internet = state if state in ("activated", "activating") else "disconnected"
        return {
            "internet": internet,
            "icon-name": (
                "network-wired-symbolic" if internet == "activated"
                else "network-wired-disconnected-symbolic"
            ),
        }

    @Property(str, "readable")
    def internet(self) -> str:
        return self._cache["internet"]

    @Property(str, "readable")
    def icon_name(self) -> str:
        return self._cache["icon-name"]


class NetworkClient(_Subscriber):
    @Signal
    def device_ready(self) -> None: ...

    @Signal
    def connection_error(self, ssid: str, error_message: str) -> None: ...

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._client = None
        self.wifi_device = None
        self.ethernet_device = None
        self._primary = "none"
        NM.Client.new_async(None, self._on_client_ready)

    def cleanup(self):
        for service in (self.wifi_device, self.ethernet_device):
            if service is not None:
                service.cleanup()
        super().cleanup()

    def _on_client_ready(self, _source, result):
        if self._destroyed:
            return
        self._client = NM.Client.new_finish(result)
        self._connect(self._client, "device-added", lambda *_: self._on_devices_changed())
        self._connect(self._client, "device-removed", lambda _client, device: self._on_devices_changed(device))
        self._sync_devices()
        self.emit("device-ready")

    def _on_devices_changed(self, removed=None):
        before = (self.wifi_device, self.ethernet_device)
        self._sync_devices(removed)
        if (self.wifi_device, self.ethernet_device) != before:
            self.emit("device-ready")

    def _sync_devices(self, removed=None):
        devices = [d for d in self._client.get_devices() if d is not removed]
        self.wifi_device = self._reconcile(self.wifi_device, devices, NM.DeviceType.WIFI, self._make_wifi)
        self.ethernet_device = self._reconcile(self.ethernet_device, devices, NM.DeviceType.ETHERNET, Ethernet)
        self._update_primary()

    def _reconcile(self, current, devices, device_type, factory):
        if current is not None and current._device in devices:
            return current
        if current is not None:
            self._release(current)
            current.cleanup()
        device = next((d for d in devices if d.get_device_type() == device_type), None)
        if device is None:
            return None
        service = factory(device)
        self._connect(service, "changed", self._update_primary)
        return service

    def _make_wifi(self, device):
        wifi = Wifi(self._client, device)
        self._connect(wifi, "failed", self._on_wifi_failed)
        return wifi

    def _on_wifi_failed(self, *_):
        self.emit("connection-error", self.wifi_device.ssid, "Error")

    def _update_primary(self, *_):
        ethernet, wifi = self.ethernet_device, self.wifi_device
        if ethernet is not None and ethernet.internet in ("activated", "activating"):
            primary = "ethernet"
        elif wifi is not None and wifi.enabled and wifi.ssid not in ("", "Disconnected"):
            primary = "wifi"
        else:
            primary = "none"
        if primary != self._primary:
            self._primary = primary
            self.notify("primary-device")

    @Property(str, "readable")
    def primary_device(self) -> str:
        return self._primary

    def _find_ap(self, ssid: str):
        return max(
            (ap for ap in self.wifi_device._device.get_access_points() if _ssid_is(ap.get_ssid(), ssid)),
            key=lambda ap: ap.get_strength(),
            default=None,
        )

    def _wifi_profiles(self):
        if self._client is None:
            return
        for conn in self._client.get_connections():
            setting = conn.get_setting_wireless()
            if setting is None:
                continue
            ssid = _ssid(setting.get_ssid())
            if ssid:
                yield ssid, conn

    def _find_saved(self, ssid: str):
        return next((conn for name, conn in self._wifi_profiles() if name == ssid), None)

    @staticmethod
    def _done_callback(finish, ssid: str, success_cb, error_cb):
        def on_done(source, result):
            try:
                finish(source, result)
            except GLib.Error as error:
                if error_cb:
                    error_cb(ssid, error.message)
                return
            if success_cb:
                success_cb(ssid)
        return on_done

    def saved_networks(self) -> list[str]:
        latest = {}
        for ssid, conn in self._wifi_profiles():
            stamp = conn.get_setting_connection().get_timestamp()
            latest[ssid] = max(stamp, latest.get(ssid, 0))
        return sorted(latest, key=latest.get, reverse=True)

    def is_network_available(self, ssid: str) -> bool:
        return self.wifi_device is not None and self._find_ap(ssid) is not None

    def connect_to_saved_network(self, ssid: str, success_cb=None, error_cb=None) -> bool:
        if not self.is_network_available(ssid):
            return False
        connection = self._find_saved(ssid)
        if connection is None:
            return False
        self._client.activate_connection_async(
            connection, self.wifi_device._device, None, None,
            self._done_callback(NM.Client.activate_connection_finish, ssid, success_cb, error_cb),
        )
        return True

    def connect_to_new_network(self, ssid: str, password: str, success_cb=None, error_cb=None) -> bool:
        if self.wifi_device is None:
            return False
        ap = self._find_ap(ssid)
        if ap is None:
            return False

        key_mgmt = _key_mgmt(ap)
        if key_mgmt == "wpa-eap" or (key_mgmt is None and _is_secured(ap)):
            return False

        connection = NM.SimpleConnection.new()
        connection.add_setting(NM.SettingConnection(id=ssid, type="802-11-wireless"))
        connection.add_setting(NM.SettingWireless(ssid=ap.get_ssid(), mode="infrastructure"))
        if key_mgmt is not None:
            connection.add_setting(NM.SettingWirelessSecurity(key_mgmt=key_mgmt, psk=password or None))

        self._client.add_and_activate_connection_async(
            connection, self.wifi_device._device, ap.get_path(), None,
            self._done_callback(NM.Client.add_and_activate_connection_finish, ssid, success_cb, error_cb),
        )
        return True

    def delete_saved_network(self, ssid: str, success_cb=None, error_cb=None) -> bool:
        connection = self._find_saved(ssid)
        if connection is None:
            return False
        connection.delete_async(
            None, self._done_callback(NM.RemoteConnection.delete_finish, ssid, success_cb, error_cb),
        )
        return True

    def disconnect_network(self, success_cb=None, error_cb=None) -> bool:
        if self.wifi_device is None:
            return False
        active = self.wifi_device._device.get_active_connection()
        if active is None:
            return False
        self._client.deactivate_connection_async(
            active, None,
            self._done_callback(
                NM.Client.deactivate_connection_finish, self.wifi_device.ssid, success_cb, error_cb,
            ),
        )
        return True

    def get_network_password(self, ssid: str) -> str:
        connection = self._find_saved(ssid)
        if connection is None:
            return ""
        name = NM.SETTING_WIRELESS_SECURITY_SETTING_NAME
        try:
            secrets = connection.get_secrets(name)
        except GLib.Error:
            return ""
        return secrets.unpack().get(name, {}).get("psk", "")

    def get_network_details(self, ssid: str) -> dict:
        details = {
            "connected": False, "strength": "Unknown", "frequency": "Unknown",
            "security": "Open", "type": "Unknown", "ip": "N/A", "gateway": "N/A", "dns": "N/A",
        }
        if self.wifi_device is None:
            return details

        device = self.wifi_device._device
        active_ap = device.get_active_access_point()
        details["connected"] = active_ap is not None and _ssid_is(active_ap.get_ssid(), ssid)

        ap = self._find_ap(ssid)
        saved = self._find_saved(ssid)
        if ap is not None:
            freq = ap.get_frequency()
            key_mgmt = _key_mgmt(ap)
            details["strength"] = f"{ap.get_strength()}%"
            details["frequency"] = f"{freq / 1000:.1f} GHz"
            details["type"] = "Wi-Fi 5 / 6" if freq > 5000 else "Wi-Fi 4"
            details["security"] = key_mgmt.upper() if key_mgmt else ("Secured" if _is_secured(ap) else "Open")
        elif saved is not None and (s_sec := saved.get_setting_wireless_security()) is not None:
            details["security"] = (s_sec.get_key_mgmt() or "Secured").upper()

        if details["connected"] and (ip4 := device.get_ip4_config()) is not None:
            addresses = ip4.get_addresses()
            if addresses:
                details["ip"] = addresses[0].get_address()
            details["gateway"] = ip4.get_gateway() or "N/A"
            if nameservers := ip4.get_nameservers():
                details["dns"] = ", ".join(nameservers)
        return details