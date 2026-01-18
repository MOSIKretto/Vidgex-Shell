from fabric.bluetooth import BluetoothClient, BluetoothDevice
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow

import modules.icons as icons


class BluetoothDeviceSlot(CenterBox):
    def __init__(self, device: BluetoothDevice, **kwargs):
        super().__init__(name="bluetooth-device", **kwargs)
        self.device = device

        self.device.connect("changed", self._update_ui)
        self.device.connect("notify::closed", self._on_closed)

        self._connection_label = Label(name="bluetooth-connection")
        self._connect_button = Button(
            name="bluetooth-connect",
            on_clicked=self._on_connect_clicked
        )

        self.start_children = [
            Box(
                spacing=8,
                children=[
                    Image(icon_name=device.icon_name + "-symbolic", size=16),
                    Label(label=device.name, h_expand=True, h_align="start", ellipsization="end"),
                    self._connection_label,
                ],
            )
        ]
        self.end_children = self._connect_button
        self._update_ui()

    def _on_closed(self, device, _):
        if device.closed:
            self.destroy()

    def _on_connect_clicked(self, *args):
        self.device.set_connecting(not self.device.connected)

    def _update_ui(self, *args):
        connected = self.device.connected

        self._connection_label.set_markup(
            icons.bluetooth_connected if connected else icons.bluetooth_disconnected
        )

        if self.device.connecting:
            self._connect_button.set_label("Connecting...")
        else:
            self._connect_button.set_label("Disconnect" if connected else "Connect")

        if connected:
            self._connect_button.add_style_class("connected")
        else:
            self._connect_button.remove_style_class("connected")


class BluetoothConnections(Box):
    def __init__(self, **kwargs):
        widgets = kwargs.pop("widgets", None)
        if not widgets:
            raise ValueError("Widgets parameter is required")

        super().__init__(name="bluetooth", spacing=4, orientation="vertical", **kwargs)

        self._widgets = widgets
        self._btns = widgets.buttons.bluetooth_button
        self._device_slots = {}

        self._client = BluetoothClient(on_device_added=self._on_device_added)
        self._setup_ui()

        self._client.connect("notify::enabled", self._update_status_label)
        self._client.connect("notify::scanning", self._update_scan_label)

        self._update_status_label()
        self._update_scan_label()

    def _setup_ui(self):
        self._scan_label = Label(name="bluetooth-scan-label", markup=icons.radar)
        self._scan_button = Button(
            name="bluetooth-scan",
            child=self._scan_label,
            on_clicked=lambda *_: self._client.toggle_scan()
        )

        self._connected_box = Box(spacing=2, orientation="vertical")
        self._other_box = Box(spacing=2, orientation="vertical")

        content = Box(spacing=4, orientation="vertical", children=[
            Label(name="bluetooth-section", label="Connected"),
            self._connected_box,
            Label(name="bluetooth-section", label="Accessible"),
            self._other_box
        ])

        self.children = [
            CenterBox(
                name="bluetooth-header",
                start_children=Button(
                    name="bluetooth-back",
                    child=Label(name="bluetooth-back-label", markup=icons.chevron_left),
                    on_clicked=lambda *_: self._widgets.show_notif()
                ),
                center_children=Label(name="bluetooth-text", label="Bluetooth"),
                end_children=self._scan_button
            ),
            ScrolledWindow(
                name="bluetooth-devices",
                child=content,
                v_expand=True,
                propagate_width=False,
                propagate_height=False,
            ),
        ]

    def _update_status_label(self, *args):
        enabled = self._client.enabled

        targets = (
            self._btns.bluetooth_status_text,
            self._btns.bluetooth_status_button,
            self._btns.bluetooth_icon,
            self._btns.bluetooth_label,
            self._btns.bluetooth_menu_button,
            self._btns.bluetooth_menu_label
        )

        for widget in targets:
            if enabled:
                widget.remove_style_class("disabled")
            else:
                widget.add_style_class("disabled")

        self._btns.bluetooth_status_text.set_label("Enabled" if enabled else "Disabled")
        self._btns.bluetooth_icon.set_markup(icons.bluetooth if enabled else icons.bluetooth_off)

    def _on_device_added(self, client, address):
        device = client.get_device(address)
        if device:
            slot = BluetoothDeviceSlot(device)
            self._device_slots[address] = slot
            device.connect("changed", lambda *_: self._update_device_position(address))
            self._update_device_position(address)

    def _update_device_position(self, address):
        slot = self._device_slots.get(address)
        if not slot:
            return

        new_parent = self._connected_box if slot.device.connected else self._other_box

        if slot.get_parent() != new_parent:
            old_parent = slot.get_parent()
            if old_parent:
                old_parent.remove(slot)
            new_parent.add(slot)

    def _update_scan_label(self, *args):
        scanning = self._client.scanning

        if scanning:
            self._scan_label.add_style_class("scanning")
            self._scan_button.add_style_class("scanning")
            self._scan_button.set_tooltip_text("Stop scanning")
        else:
            self._scan_label.remove_style_class("scanning")
            self._scan_button.remove_style_class("scanning")
            self._scan_button.set_tooltip_text("Scan Bluetooth")