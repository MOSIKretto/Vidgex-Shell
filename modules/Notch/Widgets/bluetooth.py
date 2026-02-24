import subprocess
import time
import threading

from gi.repository import GLib
from fabric.bluetooth import BluetoothClient, BluetoothDevice
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow

import services.icons as icons


def _bt_cmd(*args, timeout=10):
    try:
        r = subprocess.run(
            ["bluetoothctl", *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout
        )
        return r.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _bt_known_addrs():
    addrs = set()
    try:
        r = subprocess.run(
            ["bluetoothctl", "devices"],
            capture_output=True, timeout=5, text=True
        )
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                parts = line.split(maxsplit=2)
                if len(parts) >= 2:
                    addrs.add(parts[1])
    except Exception:
        pass
    return addrs


def _is_dev_known(dev):
    return getattr(dev, "paired", False) or getattr(dev, "trusted", False)


class BluetoothDeviceSlot(CenterBox):
    def __init__(self, device: BluetoothDevice, client: BluetoothClient, on_pos_change=None):
        super().__init__(name="bluetooth-device-slot")
        self.dev = device
        self.client = client
        self._cb = on_pos_change

        self._lc = self._lg = self._lp = self._lt = None
        self._busy = False
        self._destroyed = False

        icon_name = f"{getattr(device, 'icon_name', 'bluetooth')}-symbolic"
        dev_name = getattr(device, 'name', None) or getattr(device, 'address', 'Unknown')

        self.start_children = Box(
            spacing=8,
            children=[
                Image(icon_name=icon_name, size=16),
                Label(label=dev_name, h_expand=True, h_align="start", ellipsization="end"),
            ],
        )

        self._act_box = Box(orientation="horizontal", spacing=4)
        
        self.btn_conn = Button(name="bluetooth-connect", on_clicked=self._on_connect_click)
        self.btn_del = Button(
            name="bluetooth-delete",
            child=Label(name="bluetooth-delete-label", markup=icons.trash),
            tooltip_text="Forget device",
            on_clicked=self._on_delete_click
        )
        
        self._act_box.add(self.btn_conn)
        self._act_box.add(self.btn_del)
        self.end_children = self._act_box

        self._sig_ids = []
        for sig in ("notify::connected", "notify::connecting", "notify::paired", "notify::trusted", "notify::closed"):
            try:
                if sig == "notify::closed":
                    handler_id = self.dev.connect(sig, self._on_closed)
                else:
                    handler_id = self.dev.connect(sig, self._on_state_change)
                self._sig_ids.append(handler_id)
            except TypeError:
                pass

        self._upd()

    def _on_closed(self, dev, *_):
        if getattr(dev, "closed", False):
            GLib.idle_add(self._cleanup_and_destroy)

    def _on_state_change(self, *_):
        if not self._busy and not self._destroyed:
            GLib.idle_add(self._deferred_state_change)

    def _deferred_state_change(self):
        if not self._destroyed:
            if self._cb:
                self._cb(self)
            self._upd()
        return False

    def is_known(self):
        return _is_dev_known(self.dev)

    def _upd(self):
        if self._destroyed:
            return

        c = getattr(self.dev, "connected", False)
        g = getattr(self.dev, "connecting", False)
        p = getattr(self.dev, "paired", False)
        t = getattr(self.dev, "trusted", False)

        if (self._lc, self._lg, self._lp, self._lt) == (c, g, p, t):
            return
        self._lc, self._lg, self._lp, self._lt = c, g, p, t

        known = p or t

        if c:
            self.btn_conn.set_label("Disconnect")
            self.btn_conn.add_style_class("connected")
            self.btn_conn.set_sensitive(not self._busy)
        elif g or self._busy:
            self.btn_conn.set_label("...")
            self.btn_conn.remove_style_class("connected")
            self.btn_conn.set_sensitive(False)
        else:
            self.btn_conn.set_label("Connect")
            self.btn_conn.remove_style_class("connected")
            self.btn_conn.set_sensitive(True)

        self.btn_del.set_visible(known)
        self.btn_del.set_sensitive(not self._busy)

    def _on_connect_click(self, btn):
        if self._busy or self._destroyed: return
        self._busy = True
        self._upd()

        threading.Thread(
            target=self._connect_thread,
            args=(self.dev.address, self._lc, self.is_known()),
            daemon=True,
        ).start()

    def _connect_thread(self, addr, was_connected, was_known):
        if was_connected:
            _bt_cmd("disconnect", addr, timeout=8)
        else:
            if not was_known:
                _bt_cmd("trust", addr, timeout=5)
                _bt_cmd("pair", addr, timeout=15)
                time.sleep(1)
            _bt_cmd("connect", addr, timeout=10)
        GLib.idle_add(self._op_done)

    def _on_delete_click(self, btn):
        if self._busy or self._destroyed: return
        self._busy = True
        self._upd()

        threading.Thread(target=self._delete_thread, args=(self.dev.address,), daemon=True).start()

    def _delete_thread(self, addr):
        _bt_cmd("disconnect", addr, timeout=5)
        _bt_cmd("untrust", addr, timeout=5)
        _bt_cmd("remove", addr, timeout=5)
        GLib.idle_add(self._op_done)

    def _op_done(self):
        self._busy = False
        self._lc = self._lg = self._lp = self._lt = None
        self._upd()
        if self._cb:
            self._cb(self)
        return False

    def _cleanup_and_destroy(self):
        if self._destroyed: return
        self._destroyed = True

        for sig_id in self._sig_ids:
            if self.dev.handler_is_connected(sig_id):
                self.dev.disconnect(sig_id)
        self._sig_ids.clear()
        self.destroy()


class BluetoothConnections(Box):
    def __init__(self, **kwargs):
        self._w = kwargs.pop("widgets", None)
        super().__init__(name="bluetooth", spacing=4, orientation="vertical", **kwargs)

        self._btns = getattr(self._w.buttons, "bluetooth_button", None) if self._w else None
        self._le = self._ls = None
        self._known_addrs = set()
        self._bt_system_addrs = set()
        self._load_retries = 0

        try:
            self._cl = BluetoothClient(on_device_added=self._add_dev_callback)
        except Exception as e:
            print(f"BT Client Error: {e}")
            return

        self._sl = Label(name="bluetooth-scan-label", markup=icons.radar)
        self._sb = Button(
            name="bluetooth-scan",
            child=self._sl,
            on_clicked=lambda *_: self._cl.toggle_scan(),
        )

        self._saved_box = Box(spacing=2, orientation="vertical")
        self._access_box = Box(spacing=2, orientation="vertical")

        back_btn = Button(
            name="bluetooth-back",
            child=Label(name="bluetooth-back-label", markup=icons.chevron_left),
            on_clicked=lambda *_: self._w.show_notif() if self._w else None
        )

        self.scroll = ScrolledWindow(
            name="bluetooth-devices",
            min_content_size=(-1, -1),
            v_expand=True,
            propagate_width=False,
            propagate_height=False,
            child=Box(
                spacing=4,
                orientation="vertical",
                children=[
                    Label(name="bluetooth-section", label="Connected"), self._saved_box,
                    Label(name="bluetooth-section", label="Accessible"), self._access_box,
                ],
            )
        )

        self.children = [
            CenterBox(
                name="bluetooth-header",
                start_children=back_btn,
                center_children=Label(name="bluetooth-text", label="Bluetooth"),
                end_children=self._sb,
            ),
            self.scroll,
        ]

        self._cl.connect("notify::enabled", self._on_en)
        self._cl.connect("notify::scanning", self._on_sc)
        self._on_en()
        self._on_sc()

        threading.Thread(target=self._prefetch_known, daemon=True).start()
        GLib.timeout_add(500, self._load_initial_devices)

    def _prefetch_known(self):
        self._bt_system_addrs = _bt_known_addrs()

    def _load_initial_devices(self):
        devices = getattr(self._cl, "devices", None)
        if not devices:
            self._load_retries += 1
            return self._load_retries < 6

        devs = sorted(
            devices.values(),
            key=lambda d: (not getattr(d, "connected", False), not _is_dev_known(d))
        )
        for dev in devs:
            self._add_existing_dev(dev)
            
        return False

    def _add_dev_callback(self, client, address):
        if client:
            dev = client.get_device(address)
            if dev:
                GLib.idle_add(self._add_existing_dev, dev)

    def _add_existing_dev(self, dev):
        addr = getattr(dev, "address", None)
        if not addr or addr in self._known_addrs:
            return False
            
        self._known_addrs.add(addr)
        slot = BluetoothDeviceSlot(dev, self._cl, on_pos_change=self._repos)
        slot.connect("destroy", lambda s: self._known_addrs.discard(addr))

        target = self._saved_box if (_is_dev_known(dev) or addr in self._bt_system_addrs) else self._access_box
        target.add(slot)
        return False

    def _repos(self, slot):
        if slot._destroyed: return
        target = self._saved_box if slot.is_known() else self._access_box
        parent = slot.get_parent()

        if parent and parent != target:
            parent.remove(slot)
            target.add(slot)

    def _on_en(self, *_):
        en = getattr(self._cl, "enabled", False)
        if self._le == en: return
        self._le = en

        if not self._btns: return
        
        m = "remove_style_class" if en else "add_style_class"
        for attr in ("bluetooth_status_text", "bluetooth_status_button", "bluetooth_icon"):
            item = getattr(self._btns, attr, None)
            if item: getattr(item, m)("disabled")

        txt = getattr(self._btns, "bluetooth_status_text", None)
        if txt: txt.set_label("Enabled" if en else "Disabled")

        ico = getattr(self._btns, "bluetooth_icon", None)
        if ico: ico.set_markup(icons.bluetooth if en else icons.bluetooth_off)

    def _on_sc(self, *_):
        sc = getattr(self._cl, "scanning", False)
        if self._ls == sc: return
        self._ls = sc

        m = "add_style_class" if sc else "remove_style_class"
        getattr(self._sl, m)("scanning")
        getattr(self._sb, m)("scanning")
        self._sb.set_tooltip_text("Stop scanning" if sc else "Scan Bluetooth")

    def cleanup(self):
        self._known_addrs.clear()
        self._bt_system_addrs.clear()
        
        for box in (self._saved_box, self._access_box):
            for c in box.get_children():
                if hasattr(c, "_cleanup_and_destroy"):
                    c._cleanup_and_destroy()
                else:
                    c.destroy()
        
        self._cl = self._w = self._btns = None