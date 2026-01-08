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
        self.device.connect("changed", lambda *_: self._update_ui())
        self.device.connect(
            "notify::closed", 
            lambda *_: device.closed and self.destroy()
        )

        self._connection_label = Label(
            name="bluetooth-connection",
            markup=icons.bluetooth_disconnected
        )
        self._connect_button = Button(
            name="bluetooth-connect",
            label="Connect",
            on_clicked=lambda *_: device.set_connecting(not device.connected),
            style_classes=["connected"] if device.connected else None,
        )

        self.start_children = [
            Box(
                spacing=8,
                children=[
                    Image(
                        icon_name=f"{device.icon_name}-symbolic", 
                        size=16
                    ),
                    Label(
                        label=device.name, 
                        h_expand=True, 
                        h_align="start", 
                        ellipsization="end"
                    ),
                    self._connection_label,
                ],
            )
        ]
        self.end_children = self._connect_button
        
        self._update_ui()

    def _update_ui(self):
        self._connection_label.set_markup(
            icons.bluetooth_connected 
            if self.device.connected 
            else icons.bluetooth_disconnected
        )

        if self.device.connecting: self._connect_button.set_label("Connecting...")
        else: self._connect_button.set_label("Disconnect" if self.device.connected else "Connect")

        if self.device.connected: self._connect_button.add_style_class("connected")
        else: self._connect_button.remove_style_class("connected")


class BluetoothConnections(Box):
    def __init__(self, **kwargs):
        super().__init__(
            name="bluetooth",
            spacing=4,
            orientation="vertical",
            **kwargs,
        )

        widgets = kwargs.get("widgets")
        if not widgets:
            raise ValueError("Widgets parameter is required")
            
        self._widgets = widgets
        self._buttons = widgets.buttons.bluetooth_button
        
        self._bt_widgets = {
            'status_text': self._buttons.bluetooth_status_text,
            'status_button': self._buttons.bluetooth_status_button,
            'icon': self._buttons.bluetooth_icon,
            'label': self._buttons.bluetooth_label,
            'menu_button': self._buttons.bluetooth_menu_button,
            'menu_label': self._buttons.bluetooth_menu_label,
        }

        self._client = BluetoothClient(on_device_added=lambda c, addr: self._on_device_added(c, addr))
        self._device_slots = {}
        
        self._setup_ui()
        
        self._client.connect("notify::enabled", lambda *_: self._update_status_label())
        self._client.connect("notify::scanning", lambda *_: self._update_scan_label())
        
        self._client.notify("scanning")
        self._client.notify("enabled")

    def _setup_ui(self):
        self._scan_label = Label(
            name="bluetooth-scan-label", 
            markup=icons.radar
        )
        self._scan_button = Button(
            name="bluetooth-scan",
            child=self._scan_label,
            tooltip_text="Scan Bluetooth",
            on_clicked=lambda *_: self._client.toggle_scan()
        )
        self._back_button = Button(
            name="bluetooth-back",
            child=Label(name="bluetooth-back-label", markup=icons.chevron_left),
            on_clicked=lambda *_: self._widgets.show_notif()
        )

        self._connected_box = Box(spacing=2, orientation="vertical")
        self._other_box = Box(spacing=2, orientation="vertical")
        
        content_box = Box(spacing=4, orientation="vertical")
        content_box.add(Label(name="bluetooth-section", label="Connected"))
        content_box.add(self._connected_box)
        content_box.add(Label(name="bluetooth-section", label="Accessible"))
        content_box.add(self._other_box)

        self.children = [
            CenterBox(
                name="bluetooth-header",
                start_children=self._back_button,
                center_children=Label(name="bluetooth-text", label="Bluetooth"),
                end_children=self._scan_button
            ),
            ScrolledWindow(
                name="bluetooth-devices",
                min_content_size=(-1, -1),
                child=content_box,
                v_expand=True,
                propagate_width=False,
                propagate_height=False,
            ),
        ]

    def _update_status_label(self):
        is_enabled = self._client.enabled
        
        for widget in self._bt_widgets.values():
            if is_enabled:
                widget.remove_style_class("disabled")
            else:
                widget.add_style_class("disabled")
        
        self._bt_widgets['status_text'].set_label("Enabled" if is_enabled else "Disabled")
        self._bt_widgets['icon'].set_markup(icons.bluetooth if is_enabled else icons.bluetooth_off)

    def _on_device_added(self, client: BluetoothClient, address: str):
        device = client.get_device(address)
        if not device:
            return
        
        slot = BluetoothDeviceSlot(device)
        self._device_slots[address] = slot
        
        slot.device.connect("changed", lambda *_, addr=address: self._update_device_position(addr))
        
        self._update_device_position(address)

    def _update_device_position(self, address: str):
        slot = self._device_slots.get(address)
        if not slot:
            return
        
        device = slot.device
        
        current_parent = slot.get_parent()
        if current_parent == self._connected_box:
            self._connected_box.remove(slot)
        elif current_parent == self._other_box:
            self._other_box.remove(slot)
        
        if device.connected: self._connected_box.add(slot)
        else: self._other_box.add(slot)

    def _update_scan_label(self):
        is_scanning = self._client.scanning
        
        if is_scanning:
            self._scan_label.add_style_class("scanning")
            self._scan_button.add_style_class("scanning")
            self._scan_button.set_tooltip_text("Stop scanning")
        else:
            self._scan_label.remove_style_class("scanning")
            self._scan_button.remove_style_class("scanning")
            self._scan_button.set_tooltip_text("Scan Bluetooth")

    def destroy(self):
        if self._client and hasattr(self._client, 'scanning') and self._client.scanning:
            self._client.toggle_scan()
        
        for address, slot in list(self._device_slots.items()):
            slot.destroy()
        self._device_slots.clear()
        
        self._client = None
        self._widgets = None
        self._bt_widgets.clear()
        
        super().destroy()