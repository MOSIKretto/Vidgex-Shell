import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow
from fabric.widgets.stack import Stack

import services.icons as icons
from services.bluetooth import bluez, KnownStore, GhostDevice, get_dev_name, display_name, Subscriptions


_YES_NO = {None: "Unknown", True: "Yes", False: "No"}
_PAGE_TITLES = {"main": "Bluetooth", "saved": "Saved Devices"}


def _cancel(source):
    if source is not None:
        GLib.source_remove(source)

def _cls(widget, name: str, on: bool = True):
    ctx = widget.get_style_context()
    (ctx.add_class if on else ctx.remove_class)(name)

def _styled(widget, *names):
    for name in names:
        _cls(widget, name)
    return widget

def _header_button(name: str, icon: str, tooltip: str, handler):
    return Button(
        name=name, child=Label(markup=icon, name=f"{name}-label"),
        tooltip_text=tooltip, on_clicked=handler,
    )

def _section(title: str, child):
    return Box(
        orientation="v", spacing=4,
        children=(Label(label=title, h_align="start", name="section-title"), child),
    )

def _scroll(child):
    window = ScrolledWindow(
        min_content_size=(-1, -1), child=child,
        h_expand=True, v_expand=True, propagate_width=False, propagate_height=False,
    )
    return window


def _status_page(icon: str, text: str, button_label: str, handler):
    glyph = _styled(Label(markup=f"<span size='32768'>{icon}</span>"), "bluetooth-off-icon")
    message = _styled(Label(label=text), "bluetooth-off-label")
    button = _styled(
        Button(label=button_label, h_align="center", on_clicked=handler), "bluetooth-turn-on-btn",
    )
    return Box(
        orientation="v", v_align="center", h_align="center", spacing=12, v_expand=True,
        children=(glyph, message, button),
    )


class BTSlot(Gtk.EventBox):
    def __init__(self, parent_bt):
        super().__init__()
        self.parent_bt = parent_bt
        self.dev = None
        self.connect("button-press-event", self._on_click)

        self.main_box = _styled(CenterBox(), "pixel-slot")
        self.icon = Image(size=24)
        self.name_lbl = Label(h_expand=True, h_align="start", ellipsization="end")
        self.status_lbl = Label(h_expand=True, h_align="start", name="dim-label")

        self.btn_settings = _styled(
            Button(
                child=Label(markup=icons.settings),
                tooltip_text="Device Settings",
                on_clicked=self._on_settings,
            ),
            "pixel-icon-button", "settings-btn",
        )
        self.btn_settings.set_valign(Gtk.Align.CENTER)

        self.main_box.add_start(Box(
            spacing=12, v_align="center",
            children=(self.icon, Box(orientation="v", children=(self.name_lbl, self.status_lbl))),
        ))
        self.main_box.add_end(self.btn_settings)
        self.add(self.main_box)
        self.show_all()

    def update(self, dev) -> None:
        self.dev = dev
        connected = dev.connected
        known = self.parent_bt.is_known(dev.address)

        self.icon.set_from_icon_name(f"{dev.icon_name or 'bluetooth'}-symbolic", 24)
        self.name_lbl.set_label(display_name(dev))
        self.btn_settings.set_visible(known or connected)

        for widget, css in (
            (self.main_box, "active-slot"),
            (self.icon, "active-icon"),
            (self.btn_settings, "active-settings-btn"),
        ):
            _cls(widget, css, connected)

        if dev.connecting:
            status = "Disconnecting..." if connected else "Connecting..."
        elif connected:
            status = "Connected"
        else:
            status = "Saved" if known else "Available"
        self.status_lbl.set_label(status)

    def _on_click(self, _widget, _event) -> None:
        status = self.parent_bt.toggle_connection(self.dev)
        if status is not None:
            self.status_lbl.set_label(status)

    def _on_settings(self, _btn) -> None:
        self.parent_bt.open_settings(self.dev)


class BluetoothConnections(Box):
    def __init__(self, widgets, **kwargs):
        self._w = widgets
        super().__init__(
            name="bluetooth",
            spacing=4,
            orientation="vertical",
            h_expand=True,
            v_expand=True,
            v_align="fill",
            **kwargs,
        )

        self._cl = widgets.bluetooth_client

        self._rid = self._scan_id = None
        self._destroyed = False
        self._dirty = False
        self.current_settings_dev = None
        self._previous_page = "main"
        self._slots: dict[str, list[BTSlot]] = {"connected": [], "avail": [], "saved": []}
        self._watched: dict[str, object] = {}
        self._known = KnownStore()
        self._subs = Subscriptions()

        self._build()
        self._subs.connect(self._cl, "notify::enabled", self._on_enabled)
        self._subs.connect(self._cl, "notify::state", self._on_enabled)
        self._subs.connect(self._cl, "device-added", self._sched)
        self._subs.connect(self._cl, "device-removed", self._on_device_removed)
        self.connect("map", self._on_map)
        self.connect("destroy", lambda *_: self.cleanup())
        self._on_enabled()

    def _build(self) -> None:
        self.scan_btn = _header_button("bluetooth-scan", icons.radar, "Scan", self._on_scan)
        self.saved_btn = _header_button(
            "bluetooth-saved", icons.save, "Saved Devices", lambda *_: self._toggle_page("saved"),
        )
        back = Button(
            name="bluetooth-back",
            child=Label(markup=icons.chevron_left, name="bluetooth-back-label"),
            on_clicked=self._on_back_click,
        )
        self.header_title = Label(label="Bluetooth", v_align="center", name="header-title")

        header = CenterBox(
            start_children=(back,),
            center_children=(self.header_title,),
            end_children=(Box(
                spacing=4, orientation="horizontal",
                children=(self.saved_btn, self.scan_btn),
            ),),
        )
        header.set_margin_bottom(8)
        self.add(header)

        self.stack = Stack(
            transition_type="crossfade", h_expand=True, v_expand=True, v_align="fill",
        )
        self.add(self.stack)
        self.stack.add_named(
            _status_page(icons.bluetooth_off, "Bluetooth is disabled", "Turn On", self._turn_on_bt),
            "off",
        )

        self.lists_stack = Stack(
            transition_type="slide-left-right", h_expand=True, v_expand=True, v_align="fill",
        )

        self.connected_box = Box(spacing=2, orientation="vertical")
        self.avail_box = Box(spacing=2, orientation="vertical")
        self.saved_box = Box(spacing=2, orientation="vertical")

        avail_empty = _status_page(icons.radar, "No devices found", "Scan", self._on_scan)
        avail_empty.set_margin_top(24)
        avail_empty.set_margin_bottom(24)

        self.avail_stack = Stack(transition_type="crossfade", h_expand=True, v_expand=True)
        self.avail_stack.add_named(self.avail_box, "list")
        self.avail_stack.add_named(avail_empty, "empty")
        self.avail_section = _section("Available Devices", self.avail_stack)

        self.main_scroll = _scroll(Box(
            spacing=4, orientation="vertical", children=(self.connected_box, self.avail_section),
        ))
        self.saved_scroll = _scroll(Box(
            spacing=4, orientation="vertical", children=(_section("Saved Devices", self.saved_box),),
        ))
        self.settings_scroll = self._build_settings_page()
        for name, page in (
            ("main", self.main_scroll), ("saved", self.saved_scroll), ("settings", self.settings_scroll),
        ):
            self.lists_stack.add_named(page, name)

        self.stack.add_named(self.lists_stack, "on")

    def _build_settings_page(self):
        def action(icon, text, handler, *extra):
            return _styled(
                Button(
                    child=Label(markup=f"<span size='large'>{icon}</span> {text}"),
                    on_clicked=handler,
                ),
                "net-action-btn", *extra,
            )

        self.btn_bt_forget = action(icons.trash, "Remove", self._do_forget, "net-forget")
        self.btn_bt_disconnect = action(icons.cancel, "Disconnect", self._do_disconnect_or_connect)
        self.lbl_bt_disconnect = self.btn_bt_disconnect.get_child()

        actions_box = Box(
            orientation="horizontal", spacing=8, h_align="center", h_expand=True,
            children=(self.btn_bt_forget, self.btn_bt_disconnect),
        )
        actions_box.set_margin_bottom(12)

        self._info = {}
        info_group = _styled(Box(orientation="vertical", spacing=2), "net-info-group")
        for key, title in (("address", "MAC Address"), ("paired", "Paired"), ("trusted", "Trusted")):
            value = Label(label="-", h_align="end", selectable=key == "address")
            self._info[key] = value
            row = _styled(Box(orientation="horizontal"), "net-info-row")
            row.pack_start(Label(label=title, h_align="start", name="dim-label"), True, True, 0)
            row.pack_end(value, False, False, 0)
            info_group.add(row)

        settings_box = Box(orientation="vertical", spacing=8, h_expand=True)
        settings_box.set_margin_start(12)
        settings_box.set_margin_end(12)
        for child in (actions_box, info_group):
            settings_box.add(child)
        return _scroll(settings_box)

    def is_known(self, address: str) -> bool:
        return address in self._known.devices

    def mark_known(self, address: str, name: str) -> None:
        if self._known.mark(address, name):
            self._sched()

    def toggle_connection(self, dev) -> str | None:
        if isinstance(dev, GhostDevice):
            bluez.connect_device(self._cl.address, dev.address, lambda _ok: self.request_refresh())
            return "Connecting..."
        if dev.connecting:
            return None
        if dev.connected:
            dev.connected = False
            return "Disconnecting..."
        self.mark_known(dev.address, display_name(dev))
        dev.connected = True
        return "Connecting..."

    def open_settings(self, dev) -> None:
        self._previous_page = self.lists_stack.get_visible_child_name()
        self.current_settings_dev = dev

        icon, text = (icons.cancel, "Disconnect") if dev.connected else (icons.accept, "Connect")
        self.lbl_bt_disconnect.set_markup(f"<span size='large'>{icon}</span> {text}")

        shown = {
            "address": dev.address,
            "paired": _YES_NO[dev.paired],
            "trusted": _YES_NO[dev.trusted],
        }
        for key, label in self._info.items():
            label.set_label(shown[key])

        self._show_page("settings", display_name(dev))

    def _show_page(self, name: str, title: str | None = None) -> None:
        self.lists_stack.set_visible_child_name(name)
        self.header_title.set_label(title or _PAGE_TITLES[name])
        for btn in (self.scan_btn, self.saved_btn):
            btn.set_visible(name != "settings")
        _cls(self.saved_btn, "pressed", name == "saved")

    def _toggle_page(self, target: str) -> None:
        current = self.lists_stack.get_visible_child_name()
        self._show_page("main" if current == target else target)

    def _on_back_click(self, _btn) -> None:
        page = self.lists_stack.get_visible_child_name()
        if page == "settings":
            self._show_page(self._previous_page)
        elif page == "saved":
            self._show_page("main")
        else:
            self._w.show_notif()

    def _do_forget(self, _btn) -> None:
        address = self.current_settings_dev.address
        bluez.remove_device(self._cl.address, address, lambda _ok: self._on_forgotten(address))
        self._show_page(self._previous_page)

    def _on_forgotten(self, address: str) -> None:
        self._known.forget(address)
        self.request_refresh()

    def _do_disconnect_or_connect(self, _btn) -> None:
        if self.toggle_connection(self.current_settings_dev) is not None:
            self._show_page(self._previous_page)

    def _turn_on_bt(self, *_args) -> None:
        self._cl.powered = True

    def _sync_enabled(self) -> bool:
        enabled = self._cl.enabled
        self.stack.set_visible_child_name("on" if enabled else "off")
        for btn in (self.scan_btn, self.saved_btn):
            btn.set_sensitive(enabled)
        if not enabled and self.lists_stack.get_visible_child_name() != "main":
            self._show_page("main")
            self.current_settings_dev = None
        return enabled

    def _on_enabled(self, *_args) -> None:
        self._sync_enabled()
        self._sched()

    def _set_scanning(self, on: bool) -> None:
        for widget in (self.scan_btn, self.scan_btn.get_child()):
            _cls(widget, "scanning", on)

    def _on_scan(self, _btn) -> None:
        if self._scan_id is not None or not self._cl.enabled:
            return
        self._set_scanning(True)
        self._cl.scanning = True
        self._scan_id = GLib.timeout_add_seconds(4, self._on_scan_done)

    def _on_scan_done(self) -> bool:
        self._scan_id = None
        self._set_scanning(False)
        self._cl.scanning = False
        self.request_refresh()
        return False

    def _sync_devices(self, devices) -> None:
        live = {dev.address: dev for dev in devices}
        for address, old in list(self._watched.items()):
            if live.get(address) is not old:
                self._subs.release(self._watched.pop(address))
        for address, dev in live.items():
            if address not in self._watched:
                self._subs.connect(dev, "changed", self._sched)
                self._watched[address] = dev

    def _on_device_removed(self, _client, address: str) -> None:
        dev = self._watched.pop(address, None)
        if dev is not None:
            self._subs.release(dev)
        self._sched()

    def _sched(self, *_args) -> None:
        if self._rid is None:
            self.request_refresh()

    def request_refresh(self, delay: int = 300) -> None:
        if self._destroyed:
            return
        if not self.get_mapped():
            self._dirty = True
            return
        _cancel(self._rid)
        self._rid = GLib.timeout_add(delay, self._ref)

    def _on_map(self, *_args) -> None:
        if self._dirty:
            self._dirty = False
            self.request_refresh(0)

    def _ref(self) -> bool:
        self._rid = None
        if self._destroyed:
            return False
        if not self.get_mapped():
            self._dirty = True
            return False
        if not self._sync_enabled():
            return False

        devices = self._cl.devices
        self._sync_devices(devices)

        connected: list = []
        available: list = []
        saved: list = []
        live_addrs: set = set()

        for dev in devices:
            if get_dev_name(dev) is None:
                continue
            addr = dev.address
            live_addrs.add(addr)

            if (dev.paired or dev.trusted) and not self.is_known(addr):
                self._known.mark(addr, display_name(dev))

            (connected if dev.connected else available).append(dev)
            if self.is_known(addr):
                saved.append(dev)

        for addr, name in self._known.devices.items():
            if addr not in live_addrs:
                saved.append(GhostDevice(addr, name))

        name_key = lambda d: display_name(d).lower()
        connected.sort(key=name_key)
        available.sort(key=name_key)
        saved.sort(key=lambda d: (not d.connected, name_key(d)))

        self._ubox(self.connected_box, self._slots["connected"], connected)
        self._ubox(self.avail_box, self._slots["avail"], available)
        self._ubox(self.saved_box, self._slots["saved"], saved)

        self.connected_box.set_visible(bool(connected))
        self.avail_stack.set_visible_child_name("list" if available else "empty")
        return False

    def _ubox(self, box, pool: list, devices: list) -> None:
        while len(pool) < len(devices):
            slot = BTSlot(self)
            pool.append(slot)
            box.add(slot)
        for slot in pool[len(devices):]:
            slot.hide()
        for slot, dev in zip(pool, devices):
            slot.update(dev)
            slot.show()

    def cleanup(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True

        self._rid = _cancel(self._rid)
        if self._scan_id is not None:
            self._cl.scanning = False
            self._scan_id = _cancel(self._scan_id)

        self._subs.clear()
        self._watched.clear()
        for pool in self._slots.values():
            for slot in pool:
                slot.destroy()
            pool.clear()
        self._cl = self._w = None