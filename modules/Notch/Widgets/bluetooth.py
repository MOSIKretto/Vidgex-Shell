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
    """Безопасный вызов bluetoothctl."""
    try:
        r = subprocess.run(
            ["bluetoothctl"] + list(args),
            capture_output=True, timeout=timeout
        )
        return r.returncode == 0
    except Exception:
        return False


def _bt_known_addrs():
    """Получить адреса всех известных устройств через bluetoothctl."""
    addrs = set()
    try:
        r = subprocess.run(
            ["bluetoothctl", "devices"],
            capture_output=True, timeout=5, text=True
        )
        if r.returncode == 0:
            for line in r.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    addrs.add(parts[1])
    except Exception:
        pass
    return addrs


def _is_dev_known(dev):
    """Устройство известно системе (paired или trusted)."""
    try:
        if dev.paired:
            return True
    except Exception:
        pass
    try:
        if dev.trusted:
            return True
    except Exception:
        pass
    return False


class BluetoothDeviceSlot(CenterBox):
    def __init__(self, device: BluetoothDevice, client: BluetoothClient, on_pos_change=None):
        super().__init__(name="bluetooth-device-slot")
        self.dev = device
        self.client = client
        self._cb = on_pos_change

        self._lc = None
        self._lg = None
        self._lp = None
        self._lt = None
        self._watch_id = None
        self._busy = False
        self._destroyed = False

        self._act_box = Box(orientation="horizontal", spacing=4)

        try:
            icon_name = (device.icon_name or "bluetooth") + "-symbolic"
            dev_name = device.name or device.address or "Unknown"
        except Exception:
            icon_name = "bluetooth-symbolic"
            dev_name = "Device"

        self.start_children = Box(
            spacing=8,
            children=[
                Image(icon_name=icon_name, size=16),
                Label(
                    label=dev_name,
                    h_expand=True,
                    h_align="start",
                    ellipsization="end",
                ),
            ],
        )
        self.end_children = self._act_box

        self._sig_ids = [
            self.dev.connect("notify::connected", self._on_state_change),
            self.dev.connect("notify::connecting", self._on_state_change),
            self.dev.connect("notify::paired", self._on_state_change),
        ]

        # trusted сигнал может не существовать
        try:
            self._sig_ids.append(
                self.dev.connect("notify::trusted", self._on_state_change)
            )
        except Exception:
            pass

        try:
            self._sig_ids.append(
                self.dev.connect("notify::closed", self._on_closed)
            )
        except Exception:
            pass

        self._upd()

    def _on_closed(self, dev, *_):
        try:
            if dev.closed:
                self._cleanup_and_destroy()
        except Exception:
            self._cleanup_and_destroy()

    def _on_state_change(self, *_):
        if self._busy or self._destroyed:
            return
        GLib.idle_add(self._deferred_state_change)

    def _deferred_state_change(self):
        if self._destroyed:
            return False
        if self._cb:
            self._cb(self)
        self._upd()
        return False

    def is_known(self):
        """Устройство известно (paired или trusted)."""
        return _is_dev_known(self.dev)

    def _upd(self):
        if self._destroyed:
            return

        try:
            c = self.dev.connected
            g = self.dev.connecting
            p = self.dev.paired
        except Exception:
            return

        try:
            t = self.dev.trusted
        except Exception:
            t = False

        if self._lc == c and self._lg == g and self._lp == p and self._lt == t:
            return
        self._lc, self._lg, self._lp, self._lt = c, g, p, t

        for child in self._act_box.get_children():
            child.destroy()

        known = p or t

        # === Кнопка Connect / Disconnect ===
        btn = Button(name="bluetooth-connect")
        btn.connect("clicked", self._on_connect_click)

        if c:
            btn.set_label("Disconnect")
            btn.add_style_class("connected")
        elif g or self._busy:
            btn.set_label("...")
            btn.set_sensitive(False)
        else:
            btn.set_label("Connect")

        self._act_box.add(btn)

        # === Кнопка Delete (для known устройств) ===
        if known:
            btn_del = Button(
                name="bluetooth-delete",
                child=Label(name="bluetooth-delete-label", markup=icons.trash),
                tooltip_text="Forget device",
            )
            btn_del.connect("clicked", self._on_delete_click)
            self._act_box.add(btn_del)

        self._act_box.show_all()

    def _on_connect_click(self, btn):
        if self._busy or self._destroyed:
            return

        self._busy = True
        btn.set_sensitive(False)
        btn.set_label("...")

        addr = self.dev.address
        was_connected = self._lc
        was_known = self.is_known()

        threading.Thread(
            target=self._connect_thread,
            args=(addr, was_connected, was_known),
            daemon=True,
        ).start()

    def _connect_thread(self, addr, was_connected, was_known):
        try:
            if was_connected:
                _bt_cmd("disconnect", addr, timeout=8)
            else:
                if not was_known:
                    _bt_cmd("trust", addr, timeout=5)
                    _bt_cmd("pair", addr, timeout=15)
                    time.sleep(1.5)
                _bt_cmd("connect", addr, timeout=10)
        except Exception as e:
            print(f"BT connect error: {e}")
        finally:
            GLib.idle_add(self._connect_done)

    def _connect_done(self):
        self._busy = False
        self._lc = self._lg = self._lp = self._lt = None
        self._upd()
        if self._cb:
            self._cb(self)
        return False

    def _on_delete_click(self, btn):
        if self._busy or self._destroyed:
            return

        self._busy = True
        btn.set_sensitive(False)
        addr = self.dev.address

        threading.Thread(
            target=self._delete_thread,
            args=(addr,),
            daemon=True,
        ).start()

    def _delete_thread(self, addr):
        _bt_cmd("disconnect", addr, timeout=5)
        _bt_cmd("untrust", addr, timeout=5)
        time.sleep(0.3)
        _bt_cmd("remove", addr, timeout=5)
        GLib.idle_add(self._delete_done)

    def _delete_done(self):
        self._busy = False
        return False

    def _cleanup_and_destroy(self):
        if self._destroyed:
            return
        self._destroyed = True

        if self._watch_id:
            GLib.source_remove(self._watch_id)
            self._watch_id = None

        try:
            for sig_id in self._sig_ids:
                self.dev.disconnect(sig_id)
        except Exception:
            pass
        self._sig_ids.clear()

        self.destroy()


class BluetoothConnections(Box):
    def __init__(self, **kwargs):
        w = kwargs.pop("widgets", None)
        super().__init__(name="bluetooth", spacing=4, orientation="vertical", **kwargs)

        self._w = w
        try:
            self._btns = w.buttons.bluetooth_button
        except Exception:
            self._btns = None

        self._le = None
        self._ls = None
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
        )
        if self._w:
            back_btn.connect("clicked", lambda *_: self._w.show_notif())

        content = Box(
            spacing=4,
            orientation="vertical",
            children=[
                Label(name="bluetooth-section", label="Connected"),
                self._saved_box,
                Label(name="bluetooth-section", label="Accessible"),
                self._access_box,
            ],
        )

        self.scroll = ScrolledWindow(
            name="bluetooth-devices",
            min_content_size=(-1, -1),
            child=content,
            v_expand=True,
            propagate_width=False,
            propagate_height=False,
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

        # Получаем список известных устройств из системы в фоне
        threading.Thread(target=self._prefetch_known, daemon=True).start()

        # Первая попытка загрузки с задержкой
        GLib.timeout_add(500, self._load_initial_devices)

    def _prefetch_known(self):
        """Получаем адреса из bluetoothctl devices до загрузки."""
        self._bt_system_addrs = _bt_known_addrs()

    def _load_initial_devices(self):
        self._load_retries += 1

        if not hasattr(self._cl, "devices") or not self._cl.devices:
            if self._load_retries < 6:
                GLib.timeout_add(1000, self._load_initial_devices)
            return False

        try:
            devs = sorted(
                self._cl.devices.values(),
                key=lambda d: (
                    not getattr(d, "connected", False),
                    not _is_dev_known(d),
                ),
            )
            for dev in devs:
                self._add_existing_dev(dev)
        except Exception as e:
            print(f"BT load error: {e}")
            if self._load_retries < 6:
                GLib.timeout_add(1000, self._load_initial_devices)

        return False

    def _add_dev_callback(self, client, address):
        if client:
            try:
                dev = client.get_device(address)
                GLib.idle_add(self._add_existing_dev, dev)
            except Exception:
                pass

    def _add_existing_dev(self, dev):
        if not dev:
            return False
        try:
            addr = dev.address
        except Exception:
            return False

        if addr in self._known_addrs:
            return False
        self._known_addrs.add(addr)

        slot = BluetoothDeviceSlot(dev, self._cl, on_pos_change=self._repos)
        slot.connect("destroy", lambda s: self._known_addrs.discard(addr))

        is_known = _is_dev_known(dev) or addr in self._bt_system_addrs

        target = self._saved_box if is_known else self._access_box
        target.add(slot)
        slot.show_all()
        return False

    def _repos(self, slot):
        if slot._destroyed:
            return
        try:
            is_known = slot.is_known()
        except Exception:
            return

        target = self._saved_box if is_known else self._access_box
        parent = slot.get_parent()

        if parent and parent != target:
            parent.remove(slot)
            target.add(slot)
            slot.show_all()

    def _on_en(self, *_):
        try:
            en = self._cl.enabled
        except Exception:
            return
        if self._le == en:
            return
        self._le = en

        if not self._btns:
            return
        try:
            b = self._btns
            m = "remove_style_class" if en else "add_style_class"

            for attr in ("bluetooth_status_text", "bluetooth_status_button", "bluetooth_icon"):
                item = getattr(b, attr, None)
                if item:
                    getattr(item, m)("disabled")

            txt = getattr(b, "bluetooth_status_text", None)
            if txt:
                txt.set_label("Enabled" if en else "Disabled")

            ico = getattr(b, "bluetooth_icon", None)
            if ico:
                ico.set_markup(icons.bluetooth if en else icons.bluetooth_off)
        except Exception:
            pass

    def _on_sc(self, *_):
        try:
            sc = self._cl.scanning
        except Exception:
            return
        if self._ls == sc:
            return
        self._ls = sc

        try:
            m = "add_style_class" if sc else "remove_style_class"
            getattr(self._sl, m)("scanning")
            getattr(self._sb, m)("scanning")
            self._sb.set_tooltip_text("Stop scanning" if sc else "Scan Bluetooth")
        except Exception:
            pass

    def cleanup(self):
        try:
            self._known_addrs.clear()
            self._bt_system_addrs.clear()
            for box in (self._saved_box, self._access_box):
                for c in box.get_children():
                    if hasattr(c, "_cleanup_and_destroy"):
                        c._cleanup_and_destroy()
                    else:
                        c.destroy()
            self._cl = None
            self._w = None
            self._btns = None
        except Exception:
            pass