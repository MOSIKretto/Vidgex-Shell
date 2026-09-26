import json
import os
import threading

from gi.repository import GLib, Gtk
from fabric.bluetooth import BluetoothClient
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow
from fabric.widgets.stack import Stack

import services.icons as icons


CACHE_DIR = os.path.expanduser("~/.cache/vidgex-shell")
KNOWN_DEVICES_FILE = os.path.join(CACHE_DIR, "bluetooth_known.json")

_known_lock = threading.Lock()
_known_write_seq = 0


def _load_known_devices() -> dict:
    if os.path.exists(KNOWN_DEVICES_FILE):
        with open(KNOWN_DEVICES_FILE, "r") as f:
            return json.load(f)
    return {}

def _save_known_devices(devices: dict) -> None:
    global _known_write_seq
    snapshot = dict(devices)
    _known_write_seq += 1
    seq = _known_write_seq

    def worker():
        with _known_lock:
            if seq != _known_write_seq:
                return
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(KNOWN_DEVICES_FILE, "w") as f:
                json.dump(snapshot, f, indent=2)

    threading.Thread(target=worker, daemon=True).start()

def _run_bt_cmd(cmd_str, callback=None):
    def _on_exit(pid, _status, *_args):
        GLib.spawn_close_pid(pid)
        if callback:
            GLib.idle_add(callback)

    pid, _, _, _ = GLib.spawn_async(
        ["/bin/sh", "-c", cmd_str],
        flags=GLib.SpawnFlags.DO_NOT_REAP_CHILD,
    )
    GLib.child_watch_add(GLib.PRIORITY_DEFAULT, pid, _on_exit)

def _is_currently_visible(dev) -> bool:
    proxy = dev.device.get_property("proxy")
    return proxy.get_cached_property("RSSI") is not None

def _get_dev_name(dev):
    addr = (dev.address or "").upper()
    name = dev.alias or dev.name

    if not name or str(name).strip() in ("", "Unknown", "unknown"):
        return None

    clean_name = str(name).replace(":", "").replace("-", "").strip().upper()
    clean_addr = addr.replace(":", "").replace("-", "").strip().upper()

    if clean_name == clean_addr:
        return None

    return str(name)


class _GhostDevice:
    __slots__ = ("address", "name")

    alias = None
    icon_name = None
    connected = False
    paired = None
    trusted = None

    def __init__(self, address: str, name: str):
        self.address = address
        self.name = name


class BTSlot(Gtk.EventBox):
    def __init__(self, parent_bt):
        super().__init__()
        self.parent_bt = parent_bt
        self.dev = None
        self.list_type = "avail"

        self.connect("button-press-event", self._on_click)

        self.main_box = CenterBox()
        self.main_box.get_style_context().add_class("pixel-slot")

        self.icon = Image(size=24)
        self.name_lbl = Label(h_expand=True, h_align="start", ellipsization="end")
        self.status_lbl = Label(h_expand=True, h_align="start", name="dim-label")

        text_box = Box(orientation="v", children=(self.name_lbl, self.status_lbl))
        start_box = Box(spacing=12, v_align="center", children=(self.icon, text_box))

        self.btn_settings = Button(
            child=Label(markup=icons.settings),
            tooltip_text="Device Settings",
            on_clicked=self._on_settings,
        )
        self.btn_settings.get_style_context().add_class("pixel-icon-button")
        self.btn_settings.get_style_context().add_class("settings-btn")
        self.btn_settings.set_valign(Gtk.Align.CENTER)

        self.main_box.add_start(start_box)
        self.main_box.add_end(self.btn_settings)
        self.add(self.main_box)

    def update(self, dev, list_type):
        self.dev = dev
        self.list_type = list_type

        icon_name = f"{dev.icon_name or 'bluetooth'}-symbolic"
        self.icon.set_from_icon_name(icon_name, 24)

        dev_name = _get_dev_name(dev) or dev.name or "Unknown"
        self.name_lbl.set_label(dev_name)

        self._upd()

    def _upd(self):
        if not self.dev:
            return

        connected = self.dev.connected
        known = self.parent_bt.is_known(self.dev.address) if self.parent_bt else False

        self.btn_settings.set_visible(known or connected)

        main_ctx = self.main_box.get_style_context()
        icon_ctx = self.icon.get_style_context()
        btn_ctx = self.btn_settings.get_style_context()

        if connected:
            self.status_lbl.set_label("Connected")
            main_ctx.add_class("active-slot")
            icon_ctx.add_class("active-icon")
            btn_ctx.add_class("active-settings-btn")
        else:
            self.status_lbl.set_label("Saved" if known else "Available")
            main_ctx.remove_class("active-slot")
            icon_ctx.remove_class("active-icon")
            btn_ctx.remove_class("active-settings-btn")

    def _on_click(self, _widget, _event):
        if not self.dev or not self.dev.address:
            return

        if isinstance(self.dev, _GhostDevice):
            self.status_lbl.set_label("Connecting...")
            _run_bt_cmd(f"bluetoothctl connect {self.dev.address}", self._safe_refresh)
            return
        
        if self.dev.connected:
            self.status_lbl.set_label("Disconnecting...")
            self.dev.connected = False
        else:
            self.status_lbl.set_label("Connecting...")
            if self.parent_bt:
                self.parent_bt.mark_known(self.dev.address, _get_dev_name(self.dev) or self.dev.name)
            self.dev.connected = True

    def _safe_refresh(self):
        if self.parent_bt:
            return self.parent_bt._req_ref()
        return False

    def _on_settings(self, _btn):
        if self.parent_bt:
            self.parent_bt.open_settings(self)


class BluetoothConnections(Box):
    def __init__(self, **kwargs):
        self._w = kwargs.pop("widgets", None)
        super().__init__(
            name="bluetooth",
            spacing=4,
            orientation="vertical",
            h_expand=True,
            v_expand=True,
            v_align="fill",
            **kwargs,
        )

        self._btns = self._w.buttons.bluetooth_button

        self._slots = {"connected": [], "avail": [], "saved": []}
        self._rid = None
        self._scan_tid = None
        self._turnon_tid = None
        self._rssi_poll_tid = None
        self._scan = False
        self._scan_seen_addrs = set()
        self.current_settings_dev = None
        self._previous_page = "main"
        self._cl = None
        self._en_hid = None
        self._added_hid = None
        self._removed_hid = None
        self._dev_signal_hids = {}
        self._known_devices = _load_known_devices()
        self._destroyed = False
        self.connect("destroy", lambda *_: self.cleanup())

        try:
            self._cl = BluetoothClient()
        except GLib.Error:
            return

        self._build()

        self._en_hid = self._cl.connect("notify::enabled", self._on_en)
        self._added_hid = self._cl.connect("device-added", self._sched)
        self._removed_hid = self._cl.connect("device-removed", self._on_device_removed)

        self._on_en()
        self._sched()

    def _build(self):
        back_btn = Button(
            name="bluetooth-back",
            child=Label(markup=icons.chevron_left, name="bluetooth-back-label"),
        )
        back_btn.connect("clicked", self._on_back_click)

        self.header_title = Label(label="Bluetooth", v_align="center", name="header-title")

        self.saved_btn = Button(
            name="bluetooth-saved",
            child=Label(markup=icons.save, name="bluetooth-saved-label"),
            tooltip_text="Saved Devices",
            on_clicked=self._on_saved_toggle,
        )

        self._sc_lbl = Label(markup=icons.radar, name="bluetooth-scan-label")
        self._sc_btn = Button(
            name="bluetooth-scan",
            child=self._sc_lbl,
            tooltip_text="Scan",
            on_clicked=self._on_scan_toggle,
        )

        header_end = Box(spacing=4, orientation="horizontal", children=(self.saved_btn, self._sc_btn))
        header = CenterBox(
            start_children=(back_btn,),
            center_children=(self.header_title,),
            end_children=(header_end,),
        )
        header.set_margin_bottom(8)
        self.add(header)

        self.stack = Stack(transition_type="crossfade", h_expand=True, v_expand=True, v_align="fill")
        self.add(self.stack)

        off_box = Box(orientation="v", v_align="center", h_align="center", spacing=12, v_expand=True)
        off_icon = Label(markup=f"<span size='32768'>{icons.bluetooth_off}</span>")
        off_icon.get_style_context().add_class("bluetooth-off-icon")
        off_box.add(off_icon)

        off_label = Label(label="Bluetooth is disabled")
        off_label.get_style_context().add_class("bluetooth-off-label")
        off_box.add(off_label)

        btn_turn_on = Button(label="Turn On", h_align="center", on_clicked=self._turn_on_bt)
        btn_turn_on.get_style_context().add_class("bluetooth-turn-on-btn")
        off_box.add(btn_turn_on)

        self.stack.add_named(off_box, "off")

        self.lists_stack = Stack(
            transition_type="slide-left-right", h_expand=True, v_expand=True, v_align="fill",
        )

        self.connected_box = Box(spacing=2, orientation="vertical")
        self.avail_box = Box(spacing=2, orientation="vertical")

        self.avail_empty = Box(
            orientation="v", v_align="center", h_align="center", spacing=12, v_expand=True,
        )
        self.avail_empty.set_margin_top(24)
        self.avail_empty.set_margin_bottom(24)

        empty_icon = Label(markup=f"<span size='32768'>{icons.radar}</span>")
        empty_icon.get_style_context().add_class("bluetooth-off-icon")
        self.avail_empty.add(empty_icon)

        empty_lbl = Label(label="No devices found")
        empty_lbl.get_style_context().add_class("bluetooth-off-label")
        self.avail_empty.add(empty_lbl)

        btn_scan = Button(label="Scan", h_align="center", on_clicked=self._on_scan_toggle)
        btn_scan.get_style_context().add_class("bluetooth-turn-on-btn")
        self.avail_empty.add(btn_scan)

        self.avail_stack = Stack(transition_type="crossfade", h_expand=True, v_expand=True)
        self.avail_stack.add_named(self.avail_box, "list")
        self.avail_stack.add_named(self.avail_empty, "empty")

        self.avail_section = Box(
            orientation="v",
            spacing=4,
            children=(
                Label(label="Available Devices", h_align="start", name="section-title"),
                self.avail_stack,
            ),
        )

        self.main_scroll = ScrolledWindow(
            name="bluetooth-devices",
            min_content_size=(-1, -1),
            child=Box(spacing=4, orientation="vertical", children=[self.connected_box, self.avail_section]),
            h_expand=True, v_expand=True, propagate_width=False, propagate_height=False,
        )
        self.main_scroll.set_overlay_scrolling(False)

        self.saved_box = Box(spacing=2, orientation="vertical")
        self.saved_section = Box(
            orientation="v",
            spacing=4,
            children=(
                Label(label="Saved Devices", h_align="start", name="section-title"),
                self.saved_box,
            ),
        )
        self.saved_scroll = ScrolledWindow(
            name="bluetooth-devices",
            min_content_size=(-1, -1),
            child=Box(spacing=4, orientation="vertical", children=[self.saved_section]),
            h_expand=True, v_expand=True, propagate_width=False, propagate_height=False,
        )
        self.saved_scroll.set_overlay_scrolling(False)

        self.settings_scroll = self._build_settings_page()

        self.lists_stack.add_named(self.main_scroll, "main")
        self.lists_stack.add_named(self.saved_scroll, "saved")
        self.lists_stack.add_named(self.settings_scroll, "settings")

        self.stack.add_named(self.lists_stack, "on")

    def _build_settings_page(self):
        settings_box = Box(orientation="vertical", spacing=8, h_expand=True)
        settings_box.set_margin_start(12)
        settings_box.set_margin_end(12)

        actions_box = Box(orientation="horizontal", spacing=8, h_align="center", h_expand=True)

        lbl_forget = Label(markup=f"<span size='large'>{icons.trash}</span> Remove")
        self.btn_bt_forget = Button(child=lbl_forget, on_clicked=self._do_forget)
        self.btn_bt_forget.get_style_context().add_class("net-action-btn")
        self.btn_bt_forget.get_style_context().add_class("net-forget")

        self.lbl_bt_disconnect = Label(
            markup=f"<span size='large'>{icons.cancel}</span> Disconnect",
        )
        self.btn_bt_disconnect = Button(
            child=self.lbl_bt_disconnect, on_clicked=self._do_disconnect_or_connect,
        )
        self.btn_bt_disconnect.get_style_context().add_class("net-action-btn")

        actions_box.add(self.btn_bt_forget)
        actions_box.add(self.btn_bt_disconnect)
        actions_box.set_margin_bottom(12)
        settings_box.add(actions_box)

        self.lbl_bt_addr = Label(label="-", h_align="end", selectable=True)
        self.lbl_bt_paired = Label(label="-", h_align="end")
        self.lbl_bt_trusted = Label(label="-", h_align="end")

        info_group = Box(orientation="vertical", spacing=2)
        info_group.get_style_context().add_class("net-info-group")

        def add_row(title, val_widget):
            row = Box(orientation="horizontal")
            row.get_style_context().add_class("net-info-row")
            row.pack_start(Label(label=title, h_align="start", name="dim-label"), True, True, 0)
            row.pack_end(val_widget, False, False, 0)
            info_group.add(row)

        add_row("MAC Address", self.lbl_bt_addr)
        add_row("Paired", self.lbl_bt_paired)
        add_row("Trusted", self.lbl_bt_trusted)

        settings_box.add(info_group)

        scroll = ScrolledWindow(
            name="bluetooth-devices",
            min_content_size=(-1, -1),
            child=settings_box,
            h_expand=True, v_expand=True, propagate_width=False, propagate_height=False,
        )
        scroll.set_overlay_scrolling(False)
        return scroll

    def open_settings(self, slot):
        if not slot or not slot.dev:
            return

        self._previous_page = self.lists_stack.get_visible_child_name()
        self.current_settings_dev = slot.dev

        self.header_title.set_label(slot.name_lbl.get_label())

        is_conn = slot.dev.connected
        if is_conn:
            self.lbl_bt_disconnect.set_markup(
                f"<span size='large'>{icons.cancel}</span> Disconnect",
            )
        else:
            self.lbl_bt_disconnect.set_markup(
                f"<span size='large'>{icons.accept}</span> Connect",
            )

        self.lbl_bt_addr.set_label(slot.dev.address or "Unknown")

        if isinstance(slot.dev, _GhostDevice):
            self.lbl_bt_paired.set_label("Unknown")
            self.lbl_bt_trusted.set_label("Unknown")
        else:
            self.lbl_bt_paired.set_label("Yes" if slot.dev.paired else "No")
            self.lbl_bt_trusted.set_label("Yes" if slot.dev.trusted else "No")

        self._sc_btn.set_visible(False)
        self.saved_btn.set_visible(False)
        self.lists_stack.set_visible_child_name("settings")

    def _do_forget(self, _btn):
        dev = self.current_settings_dev
        if not dev:
            return
        addr = dev.address
        if not addr:
            return
        if addr in self._known_devices:
            del self._known_devices[addr]
            _save_known_devices(self._known_devices)
        _run_bt_cmd(
            f"bluetoothctl disconnect {addr} ; bluetoothctl untrust {addr} ; bluetoothctl remove {addr}",
            self._req_ref,
        )
        self._on_back_click(None)

    def _do_disconnect_or_connect(self, _btn):
        dev = self.current_settings_dev
        if not dev or not dev.address:
            return

        if isinstance(dev, _GhostDevice):
            _run_bt_cmd(f"bluetoothctl connect {dev.address}", self._req_ref)
            self._on_back_click(None)
            return

        # См. комментарий в BTSlot._on_click — тот же нативный путь.
        if not dev.connected:
            self.mark_known(dev.address, _get_dev_name(dev) or dev.name)
        dev.connected = not dev.connected
        self._on_back_click(None)

    def _on_back_click(self, _btn):
        curr = self.lists_stack.get_visible_child_name()
        if curr == "settings":
            self.lists_stack.set_visible_child_name(self._previous_page)
            if self._previous_page == "saved":
                self.header_title.set_label("Saved Devices")
                self.saved_btn.add_style_class("pressed")
            else:
                self.header_title.set_label("Bluetooth")
                self.saved_btn.remove_style_class("pressed")
            self._sc_btn.set_visible(True)
            self.saved_btn.set_visible(True)
        elif curr == "saved":
            self.lists_stack.set_visible_child_name("main")
            self.header_title.set_label("Bluetooth")
            self.saved_btn.remove_style_class("pressed")
        else:
            if self._destroyed:
                return
            self._w.show_notif()

    def _on_saved_toggle(self, btn):
        if self.lists_stack.get_visible_child_name() == "main":
            self.lists_stack.set_visible_child_name("saved")
            self.header_title.set_label("Saved Devices")
            btn.add_style_class("pressed")
        else:
            self.lists_stack.set_visible_child_name("main")
            self.header_title.set_label("Bluetooth")
            btn.remove_style_class("pressed")

    def is_known(self, address: str) -> bool:
        return bool(address) and address in self._known_devices

    def mark_known(self, address: str, name: str):
        if not address:
            return
        resolved_name = name or address
        if self._known_devices.get(address) == resolved_name:
            return
        self._known_devices[address] = resolved_name
        _save_known_devices(self._known_devices)
        self._sched()

    def _attach_dev_signals(self, dev):
        dev_id = id(dev)
        if dev_id in self._dev_signal_hids:
            return
        hid = dev.connect("changed", self._sched)
        self._dev_signal_hids[dev_id] = (dev, hid)

    def _on_device_removed(self, _client, address):
        for dev_id, (dev, hid) in list(self._dev_signal_hids.items()):
            if dev.address == address:
                dev.disconnect(hid)
                del self._dev_signal_hids[dev_id]
        self._scan_seen_addrs.discard(address)
        self._sched()

    def _sched(self, *_args):
        if self._destroyed:
            return
        if self._rid is None:
            self._rid = GLib.timeout_add(300, self._ref)

    def _req_ref(self):
        self._sched()
        return False

    def _ref(self) -> bool:
        self._rid = None
        if self._destroyed or not self._cl:
            return False

        enabled = self._cl.enabled
        self.stack.set_visible_child_name("on" if enabled else "off")
        if not enabled:
            return False

        dev_list = self._cl.devices

        connected_devs, available_devs, saved_devs = [], [], []
        live_addrs = set()

        for dev in dev_list:
            if not _get_dev_name(dev):
                continue

            if dev.address:
                live_addrs.add(dev.address)

            self._attach_dev_signals(dev)

            if dev.address and (dev.paired or dev.trusted) and dev.address not in self._known_devices:
                self._known_devices[dev.address] = _get_dev_name(dev) or dev.name or dev.address
                _save_known_devices(self._known_devices)

            is_conn = dev.connected
            known = self.is_known(dev.address)

            if is_conn:
                connected_devs.append(dev)
            if known:
                saved_devs.append(dev)
                if dev.address and _is_currently_visible(dev):
                    self._scan_seen_addrs.add(dev.address)
                if not is_conn and dev.address in self._scan_seen_addrs:
                    available_devs.append(dev)
            elif not is_conn:
                available_devs.append(dev)

        for addr, name in self._known_devices.items():
            if addr not in live_addrs:
                saved_devs.append(_GhostDevice(addr, name))

        name_key = lambda d: (_get_dev_name(d) or "").lower()
        connected_devs.sort(key=name_key)
        available_devs.sort(key=name_key)
        saved_devs.sort(key=lambda d: (not d.connected, (_get_dev_name(d) or "").lower()))

        self._ubox(self.connected_box, self._slots["connected"], connected_devs, "connected")
        self._ubox(self.avail_box, self._slots["avail"], available_devs, "avail")
        self._ubox(self.saved_box, self._slots["saved"], saved_devs, "saved")

        self._update_visibility()
        return False

    def _ubox(self, box, pool, data, list_type):
        needed = len(data)

        while len(pool) < needed:
            slot = BTSlot(self)
            pool.append(slot)
            box.add(slot)

        for i in range(needed, len(pool)):
            pool[i].hide()

        for i, dev in enumerate(data):
            pool[i].update(dev, list_type)
            pool[i].show_all()

    def _update_visibility(self):
        def count_visible(box):
            return sum(1 for c in box.get_children() if c.get_visible())

        self.connected_box.set_visible(count_visible(self.connected_box) > 0)
        self.avail_section.set_visible(True)

        if count_visible(self.avail_box) > 0:
            self.avail_stack.set_visible_child_name("list")
        else:
            self.avail_stack.set_visible_child_name("empty")

    def _get_pwr(self):
        rfkill_dir = "/sys/class/rfkill/"
        if not os.path.isdir(rfkill_dir):
            return False
        for entry in os.listdir(rfkill_dir):
            type_path = os.path.join(rfkill_dir, entry, "type")
            state_path = os.path.join(rfkill_dir, entry, "state")
            if not os.path.exists(type_path):
                continue
            with open(type_path, "r") as f:
                if f.read().strip() != "bluetooth":
                    continue
            if not os.path.exists(state_path):
                continue
            with open(state_path, "r") as sf:
                return sf.read().strip() == "1"
        return False

    def _turn_on_bt(self, *_args):
        if not self._get_pwr():
            self._btns._on_toggle_click()
        self._turnon_tid = GLib.timeout_add(350, self._on_en)

    def _on_scan_toggle(self, *_args):
        if self._scan:
            return

        if not self._get_pwr():
            self._turn_on_bt()
            self._scan_tid = GLib.timeout_add(800, self._do_scan)
        else:
            self._do_scan()

    def _do_scan(self) -> bool:
        self._scan_tid = None
        if self._destroyed:
            return False

        self._scan = True
        self._scan_seen_addrs = set()
        self._sc_lbl.get_style_context().add_class("scanning")
        self._sc_btn.get_style_context().add_class("scanning")

        self._cl.scanning = True

        self._rssi_poll_tid = GLib.timeout_add(1000, self._poll_rssi)
        self._scan_tid = GLib.timeout_add(4000, self._stop_scan)
        return False

    def _poll_rssi(self) -> bool:
        if self._destroyed or not self._scan:
            self._rssi_poll_tid = None
            return False
        self._sched()
        return True

    def _stop_scan(self) -> bool:
        self._scan_tid = None
        if self._destroyed:
            return False

        self._scan = False
        self._sc_lbl.get_style_context().remove_class("scanning")
        self._sc_btn.get_style_context().remove_class("scanning")

        self._cl.scanning = False
        self._sched()
        return False

    def _on_en(self, *_args) -> bool:
        self._turnon_tid = None
        if self._destroyed:
            return False

        enabled = self._cl.enabled
        self.stack.set_visible_child_name("on" if enabled else "off")

        if enabled:
            self._sched()

        GLib.idle_add(self._btns.update_state)
        return False

    def cleanup(self):
        if self._destroyed:
            return
        self._destroyed = True

        if self._rid:
            GLib.source_remove(self._rid)
            self._rid = None
        if self._scan_tid:
            GLib.source_remove(self._scan_tid)
            self._scan_tid = None
        if self._turnon_tid:
            GLib.source_remove(self._turnon_tid)
            self._turnon_tid = None
        if self._rssi_poll_tid:
            GLib.source_remove(self._rssi_poll_tid)
            self._rssi_poll_tid = None

        if self._cl:
            if self._en_hid:
                self._cl.disconnect(self._en_hid)
            if self._added_hid:
                self._cl.disconnect(self._added_hid)
            if self._removed_hid:
                self._cl.disconnect(self._removed_hid)

        for dev, hid in self._dev_signal_hids.values():
            dev.disconnect(hid)
        self._dev_signal_hids.clear()

        for pool in self._slots.values():
            for slot in pool:
                slot.parent_bt = None
                slot.destroy()
            pool.clear()

        self._cl = None
        self._w = None
        self._btns = None