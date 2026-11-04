import json
import os
import threading

from gi.repository import Gio, GLib

CACHE_DIR = os.path.expanduser("~/.cache/vidgex-shell")
KNOWN_DEVICES_FILE = os.path.join(CACHE_DIR, "bluetooth_known.json")

_ADDR_SEPARATORS = str.maketrans("", "", ":-")
_PLACEHOLDER_NAMES = ("", "Unknown", "unknown")

_BLUEZ_BUS_NAME = "org.bluez"
_BLUEZ_ADAPTER_IFACE = "org.bluez.Adapter1"
_BLUEZ_DEVICE_IFACE = "org.bluez.Device1"
_BLUEZ_REPLY_TYPE = GLib.VariantType.new("(a{oa{sa{sv}}})")

_SLOT_BY_TYPE = {
    "Headset": "headphones",
    "Headphones": "headphones",
    "Audio device": "headphones",
    "Keyboard": "keyboard",
    "Mouse": "mouse",
}

_BATTERY_TYPE_PERCENTAGE = 1


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

    def clear(self) -> None:
        while self._items:
            obj, handler_id = self._items.pop()
            obj.disconnect(handler_id)


def get_dev_name(dev) -> str | None:
    name = dev.alias or dev.name
    if not name or name.strip() in _PLACEHOLDER_NAMES:
        return None
    if name.translate(_ADDR_SEPARATORS).strip().upper() == dev.address.translate(_ADDR_SEPARATORS).upper():
        return None
    return name


def display_name(dev) -> str:
    return get_dev_name(dev) or dev.name


def device_slot(dev) -> str | None:
    return _SLOT_BY_TYPE.get(dev.type)


def battery_percentage(dev) -> float | None:
    """Процент заряда устройства, если он реально известен (BlueZ Battery1
    отдаёт тип PERCENTAGE), иначе None — вместо выдуманного числа по
    неизвестной шкале coarse-уровня."""
    if dev.device.get_property("battery-type") != _BATTERY_TYPE_PERCENTAGE:
        return None
    return dev.battery_percentage


class GhostDevice:
    __slots__ = ("address", "name")

    alias = None
    icon_name = None
    connected = False
    connecting = False
    paired = None
    trusted = None

    def __init__(self, address: str, name: str):
        self.address = address
        self.name = name


class KnownStore:
    __slots__ = ("devices", "_writing", "_dirty")

    def __init__(self):
        self.devices: dict[str, str] = {}
        if os.path.exists(KNOWN_DEVICES_FILE):
            try:
                with open(KNOWN_DEVICES_FILE, "r") as f:
                    self.devices = json.load(f)
            except (OSError, json.JSONDecodeError):
                self.devices = {}
        self._writing = self._dirty = False

    def mark(self, address: str, name: str) -> bool:
        if self.devices.get(address) == name:
            return False
        self.devices[address] = name
        self._save()
        return True

    def forget(self, address: str) -> None:
        if self.devices.pop(address, None) is not None:
            self._save()

    def _save(self) -> None:
        if self._writing:
            self._dirty = True
            return
        self._writing = True
        data = json.dumps(self.devices, indent=2)
        threading.Thread(target=self._write, args=(data,), daemon=True).start()

    def _write(self, data: str) -> None:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            tmp = KNOWN_DEVICES_FILE + ".tmp"
            with open(tmp, "w") as f:
                f.write(data)
            os.replace(tmp, KNOWN_DEVICES_FILE)
        finally:
            GLib.idle_add(self._on_written)

    def _on_written(self) -> bool:
        self._writing = False
        if self._dirty:
            self._dirty = False
            self._save()
        return False


class BlueZ:
    __slots__ = ("_bus", "_adapter_path", "_bus_waiters")

    def __init__(self):
        self._bus = None
        self._adapter_path = None
        self._bus_waiters = []

    def _with_bus(self, request):
        if self._bus is not None:
            request(self._bus)
            return
        self._bus_waiters.append(request)
        if len(self._bus_waiters) == 1:
            Gio.bus_get(Gio.BusType.SYSTEM, None, self._on_bus_ready)

    def _on_bus_ready(self, _source, result):
        waiters, self._bus_waiters = self._bus_waiters, []
        try:
            self._bus = Gio.bus_get_finish(result)
        except GLib.Error:
            return
        for request in waiters:
            request(self._bus)

    def _with_adapter_path(self, adapter_address: str, request):
        if self._adapter_path is not None:
            request(self._adapter_path)
            return
        self._with_bus(lambda bus: self._lookup_adapter(bus, adapter_address, request))

    def _lookup_adapter(self, bus, adapter_address, request):
        bus.call(
            _BLUEZ_BUS_NAME, "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects",
            None, _BLUEZ_REPLY_TYPE, Gio.DBusCallFlags.NONE, -1, None,
            lambda source, res: self._on_managed_objects(source, res, adapter_address, request),
        )

    def _on_managed_objects(self, bus, result, adapter_address, request):
        try:
            (objects,) = bus.call_finish(result).unpack()
        except GLib.Error:
            return
        for path, ifaces in objects.items():
            adapter = ifaces.get(_BLUEZ_ADAPTER_IFACE)
            if adapter is not None and adapter.get("Address") == adapter_address:
                self._adapter_path = path
                request(path)
                return

    @staticmethod
    def _device_path(adapter_path: str, address: str) -> str:
        return f"{adapter_path}/dev_{address.replace(':', '_')}"

    def _call_finish(self, source, result, callback):
        try:
            source.call_finish(result)
            callback(True)
        except GLib.Error:
            callback(False)

    def connect_device(self, adapter_address: str, address: str, callback) -> None:
        def do_connect(adapter_path):
            self._bus.call(
                _BLUEZ_BUS_NAME, self._device_path(adapter_path, address),
                _BLUEZ_DEVICE_IFACE, "Connect", None, None, Gio.DBusCallFlags.NONE, -1, None,
                lambda source, res: self._call_finish(source, res, callback),
            )
        self._with_adapter_path(adapter_address, do_connect)

    def remove_device(self, adapter_address: str, address: str, callback) -> None:
        def do_remove(adapter_path):
            device_path = GLib.Variant("(o)", (self._device_path(adapter_path, address),))
            self._bus.call(
                _BLUEZ_BUS_NAME, adapter_path, _BLUEZ_ADAPTER_IFACE, "RemoveDevice",
                device_path, None, Gio.DBusCallFlags.NONE, -1, None,
                lambda source, res: self._call_finish(source, res, callback),
            )
        self._with_adapter_path(adapter_address, do_remove)


bluez = BlueZ()