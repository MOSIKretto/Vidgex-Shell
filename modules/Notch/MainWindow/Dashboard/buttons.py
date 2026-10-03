from bisect import bisect_right
import os
from signal import SIGKILL

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("NM", "1.0")
from gi.repository import GLib, Gtk, NM

from fabric.bluetooth import BluetoothClient
from fabric.core.service import Property, Service, Signal
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label

import services.icons as icons

from modules.Notch.MainWindow.Dashboard.Buttons.caffeine import Caffeine
from modules.Notch.MainWindow.Dashboard.Buttons.workTime import WorkTime
from modules.Notch.MainWindow.Dashboard.Buttons.timer import _TimerSplitButton, _cancel, _dis, _content
from modules.Notch.MainWindow.Dashboard.Buttons.network import NetworkConnections
from modules.Notch.MainWindow.Dashboard.Buttons.bluetooth import BluetoothConnections


_TH = (25, 50, 75)
_WI = (icons.wifi_0, icons.wifi_1, icons.wifi_2, icons.wifi_3)
_AN = (icons.wifi_0, icons.wifi_1, icons.wifi_2, icons.wifi_3, icons.wifi_2, icons.wifi_1)

# Имена начинаются с цифры — через точку недоступны.
_SEC = getattr(NM, "80211ApSecurityFlags")
_AP_FLAGS = getattr(NM, "80211ApFlags")

_DISCONNECTED = "Disconnected"
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
    """Список подписок (объект, handler_id). Чистый Python без GObject в MRO — поэтому __slots__."""
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

    def _rebind(self, old, new, signal: str, callback):
        return self._subs.rebind(old, new, signal, callback)

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
        self._ap_list_source = _cancel(self._ap_list_source)
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
            "ssid": _DISCONNECTED if ap is None else _ssid(ap.get_ssid()),
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
        self._ap = self._rebind(
            self._ap, self._device.get_active_access_point(), "notify::strength", self._refresh,
        )

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
            # NM ограничивает частоту сканирований и отклоняет слишком частые запросы.
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
        elif wifi is not None and wifi.enabled and wifi.ssid not in ("", _DISCONNECTED):
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
        # Клиент создаётся асинхронно: до готовности профилей нет.
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

    def _done_callback(self, finish, ssid: str, success_cb, error_cb):
        def on_done(source, result):
            if self._destroyed:
                return
            try:
                finish(source, result)
            except GLib.Error as error:
                # NM отклонил операцию: отказ polkit, занятое устройство, профиль удалён по ходу дела.
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
            # Отказ polkit/секрет-агента, профиль без секции безопасности (открытая сеть)
            # или профиль удалён в процессе запроса.
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
        if ap is not None:
            freq = ap.get_frequency()
            key_mgmt = _key_mgmt(ap)
            details["strength"] = f"{ap.get_strength()}%"
            details["frequency"] = f"{freq / 1000:.1f} GHz"
            details["type"] = "Wi-Fi 5 / 6" if freq > 5000 else "Wi-Fi 4"
            details["security"] = key_mgmt.upper() if key_mgmt else ("Secured" if _is_secured(ap) else "Open")
        elif (
            (saved := self._find_saved(ssid)) is not None
            and (s_sec := saved.get_setting_wireless_security()) is not None
        ):
            details["security"] = (s_sec.get_key_mgmt() or "Secured").upper()

        if details["connected"] and (ip4 := device.get_ip4_config()) is not None:
            addresses = ip4.get_addresses()
            if addresses:
                details["ip"] = addresses[0].get_address()
            details["gateway"] = ip4.get_gateway() or "N/A"
            if nameservers := ip4.get_nameservers():
                details["dns"] = ", ".join(nameservers)
        return details


def _pids(name: str) -> list[int]:
    found = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/comm") as f:
                if f.read().strip() == name:
                    found.append(int(entry))
        except (FileNotFoundError, ProcessLookupError):
            # Процесс завершился между listdir и чтением: ENOENT или ESRCH.
            pass
    return found


def _kill(pids: list[int]) -> None:
    for pid in pids:
        try:
            os.kill(pid, SIGKILL)
        except ProcessLookupError:
            # Процесс успел завершиться сам.
            pass


def _text_label(name: str, **kwargs) -> Label:
    return Label(name=name, xalign=0, h_align="start", justification="left", **kwargs)


def _title_box(*children) -> Box:
    return Box(orientation="v", h_align="start", v_align="center", children=children)


class _StatusButton(Box):
    """Каркас кнопки статуса: иконка, заголовок, подзаголовок в revealer и кнопка меню."""

    def __init__(self, widgets, client, prefix: str, title: str, icon_markup: str):
        super().__init__(name=f"{prefix}-button")
        self._w = widgets
        self._cl = client
        self._uid = None
        self._destroyed = False
        self._subs = Subscriptions()

        self._icon = Label(name=f"{prefix}-icon", markup=icon_markup)
        self._subtitle = _text_label(f"{prefix}-ssid")
        self._revealer = Gtk.Revealer(
            halign=Gtk.Align.START,
            valign=Gtk.Align.CENTER,
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            transition_duration=400,
        )
        self._revealer.add(self._subtitle)

        title_label = _text_label(f"{prefix}-label", label=title)
        status_button = Button(
            name=f"{prefix}-status-button",
            h_expand=True,
            child=_content(self._icon, _title_box(title_label, self._revealer)),
            on_clicked=self._on_status_click,
        )
        menu_label = Label(name=f"{prefix}-menu-label", markup=icons.chevron_right)
        menu_button = Button(
            name=f"{prefix}-menu-button",
            child=menu_label,
            on_clicked=self._on_menu_click,
        )
        self.add(status_button)
        self.add(menu_button)

        self._sw = (
            self, self._icon, title_label, self._subtitle, status_button, menu_button, menu_label,
        )
        self.connect("destroy", lambda *_: self.cleanup())

    def _toggle(self) -> None:
        raise NotImplementedError

    def _open_menu(self) -> None:
        raise NotImplementedError

    def update_state(self) -> None:
        raise NotImplementedError

    def _on_status_click(self, *_) -> None:
        if not self._destroyed:
            self._toggle()

    def _on_menu_click(self, *_) -> None:
        if not self._destroyed:
            self._open_menu()

    def _sched(self, *_) -> None:
        # Серия событий (скан, RSSI) схлопывается в одно обновление не чаще раза в 100 мс.
        if self._uid is None:
            self._uid = GLib.timeout_add(100, self._flush)

    def _flush(self) -> bool:
        self._uid = None
        self.update_state()
        return False

    def _show_subtitle(self, text: str) -> None:
        self._subtitle.set_label(text[:6].rstrip() + "..." if len(text) > 6 else text)
        self._revealer.set_reveal_child(True)

    def _hide_subtitle(self) -> None:
        self._revealer.set_reveal_child(False)

    def cleanup(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        self._uid = _cancel(self._uid)
        self._subs.clear()
        self._cl = self._w = None


class NetworkButton(_StatusButton):
    def __init__(self, widgets=None):
        super().__init__(widgets, widgets.network_client, "network", "Wi-Fi", icons.wifi_off)
        self._aid = None
        self._ast = 0
        self._last_ico = icons.wifi_off
        self._wifi = None
        self._subs.connect(self._cl, "device-ready", self._on_ready)
        self._subs.connect(self._cl, "notify::primary-device", self._sched)
        self._on_ready()

    def _toggle(self) -> None:
        wifi = self._cl.wifi_device
        if wifi is not None:
            wifi.toggle_wifi()

    def _open_menu(self) -> None:
        self._w.show_network_applet()

    def _on_ready(self, *_) -> None:
        # Wi-Fi сервис может смениться (hot-plug адаптера): переподписываемся один раз.
        self._wifi = self._subs.rebind(self._wifi, self._cl.wifi_device, "changed", self._sched)
        self._sched()

    def _set_icon(self, markup: str) -> None:
        if self._last_ico != markup:
            self._icon.set_markup(markup)
            self._last_ico = markup

    def _start_anim(self) -> None:
        if self._aid is None:
            self._ast = 0
            self._aid = GLib.timeout_add(500, self._anim)

    def _stop_anim(self) -> None:
        self._aid = _cancel(self._aid)

    def _anim(self) -> bool:
        if self._destroyed:
            self._aid = None
            return False
        # Остановка — по событиям Wi-Fi/клиента через update_state, не по опросу здесь.
        self._set_icon(_AN[self._ast])
        self._ast = (self._ast + 1) % len(_AN)
        return True

    def _show_static(self, icon: str) -> None:
        self._stop_anim()
        self._set_icon(icon)
        self._hide_subtitle()

    def update_state(self) -> None:
        if self._destroyed:
            return

        wifi, eth = self._cl.wifi_device, self._cl.ethernet_device
        disabled = wifi is not None and not wifi.enabled
        _dis(self._sw, disabled)

        if disabled:
            static = icons.wifi_off
        elif self._cl.primary_device == "ethernet":
            # primary == "ethernet" гарантирует, что сервис Ethernet существует.
            static = icons.world if eth.internet == "activated" else icons.world_off
        elif wifi is None:
            static = icons.wifi_off
        else:
            static = None
        if static is not None:
            self._show_static(static)
            return

        internet = wifi.internet
        if wifi.ssid in ("", _DISCONNECTED) or internet not in ("activating", "activated"):
            self._hide_subtitle()
            self._start_anim()
            return

        # SSID показываем, как только AP известна; иконка анимируется до activated.
        self._show_subtitle(wifi.ssid)
        if internet == "activated":
            self._stop_anim()
            self._set_icon(_WI[bisect_right(_TH, wifi.strength)])
        else:
            self._start_anim()

    def cleanup(self) -> None:
        self._stop_anim()
        self._wifi = None
        super().cleanup()


class BluetoothButton(_StatusButton):
    def __init__(self, widgets=None):
        super().__init__(widgets, widgets.bluetooth_client, "bluetooth", "Bluetooth", icons.bluetooth_off)
        # None — «ещё не отрисовано»: первый update_state обязан применить и иконку, и _dis.
        self._en = None
        self._pending_id = None
        # BluetoothClient.notifier эмитит "changed" после каждого notify (enabled, state,
        # devices, connected-devices), а device-added/removed завершаются notifier("devices").
        # Поэтому одной подписки достаточно.
        self._subs.connect(self._cl, "changed", self._sched)
        self._sched()

    def _toggle(self) -> None:
        if self._pending_id is not None:
            return
        # toggle_power переключает именно powered (enabled считается из state и включает
        # turning-on), поэтому цель берём от powered и ДО вызова.
        target = not self._cl.powered
        self._cl.toggle_power()
        self._upd_ui(target, ())
        # Пока BlueZ переключает питание, события клиента не перетирают оптимистичный UI.
        self._pending_id = GLib.timeout_add(1000, self._clear_pending)

    def _open_menu(self) -> None:
        self._w.show_bt()

    def _clear_pending(self) -> bool:
        self._pending_id = None
        self.update_state()
        return False

    def _upd_ui(self, en: bool, connected) -> None:
        if self._en != en:
            self._en = en
            self._icon.set_markup(icons.bluetooth if en else icons.bluetooth_off)
            _dis(self._sw, not en)

        if not (en and connected):
            self._hide_subtitle()
            return

        dev = min(connected, key=lambda d: (d.alias or d.name or "").lower())
        self._show_subtitle(dev.alias or dev.name or dev.address)

    def update_state(self, *_) -> bool:
        if self._destroyed:
            return False
        if self._pending_id is None:
            self._upd_ui(self._cl.enabled, self._cl.connected_devices)
        return False

    def cleanup(self) -> None:
        self._pending_id = _cancel(self._pending_id)
        super().cleanup()


class _StateTimerButton(_TimerSplitButton):
    """Кнопка с таймером, чьё состояние определяется внешним источником (процесс, Wayland)."""

    def _sync_active(self, active: bool) -> None:
        if active and not self._is_running:
            self.start_timer()
        elif not active and self._is_running:
            self.stop_timer()
        else:
            self._dis_ui(not active)


class NightModeButton(_StateTimerButton):
    PAT, START = "hyprsunset", "hyprsunset -t 3500"

    def __init__(self):
        super().__init__(
            name_prefix="night-mode",
            icon_markup=icons.night,
            title_widget=_title_box(
                _text_label("night-mode-label", label="Night"),
                _text_label("night-mode-label", label="Mode"),
            ),
            default_seconds=3600,
        )
        self.update_state()

    def _on_status_click(self, *_):
        pids = _pids(self.PAT)
        if pids:
            _kill(pids)
            self.stop_timer()
        else:
            GLib.spawn_command_line_async(self.START)
            self.start_timer()

    def _on_timer_finished(self):
        _kill(_pids(self.PAT))

    def update_state(self, *_):
        self._sync_active(bool(_pids(self.PAT)))

    def cleanup(self):
        if self._destroyed:
            return
        super().cleanup()
        _kill(_pids(self.PAT))


class CaffeineButton(_StateTimerButton):
    def __init__(self):
        self._caffeine = Caffeine()
        super().__init__(
            name_prefix="caffeine",
            icon_markup=icons.coffee,
            title_widget=_title_box(_text_label("caffeine-label", label="Caffeine")),
            default_seconds=3600,
        )
        self.update_state()

    def _on_status_click(self, *_):
        if self._caffeine.toggle():
            self.start_timer()
        else:
            self.stop_timer()

    def _on_timer_finished(self):
        self._caffeine.disable()

    def update_state(self, *_):
        self._sync_active(self._caffeine.is_enabled)
        return False

    def cleanup(self):
        if self._destroyed:
            return
        super().cleanup()
        self._caffeine.disable()


class WorkTimeButton(_TimerSplitButton):
    def __init__(self):
        self._work_time = WorkTime(update_callback=self._on_work_time_update)
        super().__init__(
            name_prefix="work-time",
            icon_markup=icons.clock,
            title_widget=_title_box(
                _text_label("work-time-label", label="Work"),
                _text_label("work-time-label", label="Time"),
            ),
            default_seconds=self._work_time.WORK_TIME,
        )
        self._push_duration()
        self.update_state()

    def _push_duration(self) -> None:
        self._work_time.WORK_TIME = self._duration
        self._work_time.remaining = self._duration

    def _on_status_click(self, *_):
        self._work_time.toggle()
        self.update_state()

    def _on_work_time_update(self, status_text: str, is_running: bool):
        # WorkTime.stop() из cleanup() дёргает колбэк уже у разрушаемой кнопки.
        if self._destroyed:
            return
        self._is_running = is_running
        self._remaining = self._work_time.remaining
        self._dis_ui(not is_running)
        self.update_timer_display()
        if is_running:
            self._trigger_tick_pulse()

    def _on_timer_scroll(self, widget, event) -> bool:
        if self._is_running:
            return True
        res = super()._on_timer_scroll(widget, event)
        self._push_duration()
        return res

    def update_state(self, *_):
        is_run = self._work_time.is_running
        self._is_running = is_run
        self._dis_ui(not is_run)
        self._remaining = self._work_time.remaining if is_run else self._duration
        self.update_timer_display()
        return False

    def cleanup(self):
        if self._destroyed:
            return
        super().cleanup()
        self._work_time.stop()


class Buttons(Gtk.Grid):
    def __init__(self, dashboard=None):
        super().__init__(name="buttons-grid")

        self.set_row_homogeneous(True)
        self.set_column_homogeneous(True)
        self.set_row_spacing(4)
        self.set_column_spacing(4)
        self.set_vexpand(False)

        self._dashboard = dashboard
        self._destroyed = False
        self.network_client = NetworkClient()
        self.bluetooth_client = BluetoothClient()
        self.network_connections = NetworkConnections(widgets=self)
        self.bluetooth = BluetoothConnections(widgets=self)

        self.network_button = NetworkButton(widgets=self)
        self.bluetooth_button = BluetoothButton(widgets=self)
        self.night_mode_button = NightModeButton()
        self.caffeine_button = CaffeineButton()
        self.work_time_button = WorkTimeButton()

        for i, btn in enumerate((
            self.network_button, self.bluetooth_button, self.night_mode_button,
            self.caffeine_button, self.work_time_button,
        )):
            self.attach(btn, i, 0, 1, 1)

        self.connect("destroy", lambda *_: self.cleanup())
        self.show_all()

    def show_network_applet(self) -> None:
        self._dashboard.show_network_applet()

    def show_bt(self) -> None:
        self._dashboard.show_bt()

    def show_notif(self) -> None:
        self._dashboard.show_notif()

    def cleanup(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        for child in self.get_children():
            child.cleanup()
        self.bluetooth.cleanup()
        self.network_connections.cleanup()
        self.network_client.cleanup()