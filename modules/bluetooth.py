from fabric.bluetooth import BluetoothClient
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow

from gi.repository import GLib

import subprocess
import json
from pathlib import Path
from typing import Dict, Set, List, Optional
from dataclasses import dataclass, asdict

import modules.icons as icons


@dataclass
class SavedDevice:
    """Data class for saved device information."""
    address: str
    name: str
    icon_name: str = "bluetooth"
    
    def to_dict(self) -> Dict[str, str]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> "SavedDevice":
        return cls(
            address=data.get("address", ""),
            name=data.get("name", "Неизвестное устройство"),
            icon_name=data.get("icon_name", "bluetooth")
        )


class DeviceSlotBase(CenterBox):
    """Base class for device slots with common functionality."""
    
    def __init__(self, parent: "BluetoothConnections", **kwargs):
        super().__init__(name="bluetooth-device", **kwargs)
        self.parent = parent
        
        self.connection_label = Label(
            name="bluetooth-connection",
            markup=icons.bluetooth_disconnected
        )
        self.action_box = Box(orientation="horizontal", spacing=4)
        
        self.delete_button = Button(
            name="bluetooth-delete",
            child=Label(name="bluetooth-delete-label", markup=icons.trash),
            on_clicked=self._on_delete_clicked,
            tooltip_text="Remove device"
        )

        # Подключаем обработчик нажатия для кнопки удаления
        if hasattr(self, 'delete_button'):
            self.delete_button.connect('button-press-event', self._on_delete_button_press)

    def _create_info_box(self, icon_name: str, device_name: str) -> Box:
        """Create standard info box layout."""
        info_box = Box(spacing=8, h_expand=True, h_align="fill")
        info_box.add(Image(icon_name=f"{icon_name}-symbolic", size=16))
        info_box.add(Label(
            label=device_name,
            h_expand=True,
            h_align="start",
            ellipsization="end"
        ))
        info_box.add(self.connection_label)
        return info_box

    def _clear_action_box(self):
        """Clear all children from action box."""
        for child in self.action_box.get_children():
            self.action_box.remove(child)

    def _on_delete_clicked(self, button):
        """Override in subclasses."""
        raise NotImplementedError

    def _on_delete_button_press(self, widget, event):
        """Анимация нажатия для кнопки удаления"""
        widget.add_style_class('pressed')
        GLib.timeout_add(150, lambda: widget.remove_style_class('pressed') if widget else False)
        return False


class BluetoothDeviceSlot(DeviceSlotBase):
    """Slot for active/discovered Bluetooth devices."""
    
    def __init__(self, device, client: BluetoothClient, parent: "BluetoothConnections", **kwargs):
        super().__init__(parent, **kwargs)
        self.device = device
        self.client = client
        self._is_saved = device.address in parent.saved_addresses

        # Connect signals
        self._signal_ids = [
            device.connect("changed", self._on_changed),
            device.connect("notify::closed", self._on_device_closed)
        ]

        # Create connect button
        self.connect_button = Button(
            name="bluetooth-connect",
            label="Connect",
            on_clicked=self._on_connect_clicked
        )
        
        if device.connected:
            self.connect_button.add_style_class("connected")

        # Build UI
        info_box = self._create_info_box(device.icon_name, device.name)
        self.start_children = [info_box]
        self.end_children = self.action_box

        # Initial update
        self._update_ui()
        self._update_action_buttons()
        self._ensure_correct_container(force=True)

        # Подключаем обработчик нажатия
        if hasattr(self, 'connect_button'):
            self.connect_button.connect('button-press-event', self._on_button_press)

    def _on_button_press(self, widget, event):
        """Анимация нажатия для кнопки подключения Bluetooth"""
        widget.add_style_class('pressed')
        GLib.timeout_add(150, lambda: widget.remove_style_class('pressed') if widget else False)
        return False

    def _on_device_closed(self, *args):
        """Handle device closure."""
        if self.device.closed:
            self._disconnect_signals()
            self.destroy()

    def _disconnect_signals(self):
        """Disconnect all signal handlers."""
        for signal_id in self._signal_ids:
            try:
                self.device.disconnect(signal_id)
            except Exception:
                pass
        self._signal_ids.clear()

    def _on_connect_clicked(self, button):
        """Toggle device connection."""
        self.device.set_connecting(not self.device.connected)

    def _on_changed(self, *args):
        """Handle device state changes."""
        self._update_ui()
        
        # Auto-save on first connection
        if self.device.connected and not self._is_saved:
            self._is_saved = True
            self.parent.add_saved_device(SavedDevice(
                address=self.device.address,
                name=self.device.name,
                icon_name=self.device.icon_name
            ))
            self._ensure_correct_container()

    def _update_ui(self):
        """Update UI based on device state."""
        # Connection icon
        icon = icons.bluetooth_connected if self.device.connected else icons.bluetooth_disconnected
        self.connection_label.set_markup(icon)
        
        # Button label
        if self.device.connecting:
            label = "Connecting..."
        elif self.device.connected:
            label = "Disconnect"
        else:
            label = "Connect"
        self.connect_button.set_label(label)
        
        # Button style
        if self.device.connected:
            self.connect_button.add_style_class("connected")
        else:
            self.connect_button.remove_style_class("connected")
        
        self._update_action_buttons()

    def _ensure_correct_container(self, force: bool = False):
        """Move slot to correct container based on saved status."""
        if not self.parent:
            return
        
        current = self.get_parent()
        target = self.parent.paired_box if self._is_saved else self.parent.available_box
        
        if current != target or force:
            if current:
                current.remove(self)
            target.add(self)

    def _update_action_buttons(self):
        """Update action buttons based on state."""
        self._clear_action_box()
        
        if self.parent.client.enabled:
            self.action_box.add(self.connect_button)
        
        if self._is_saved:
            self.action_box.add(self.delete_button)
        
        self.action_box.show_all()

    def _on_delete_clicked(self, button):
        self._is_saved = False
        self.parent.remove_saved_device(self.device.address)
        self._ensure_correct_container()
        
        if self.device.connected:
            self.device.set_connecting(False)
            GLib.timeout_add(500, self._remove_from_system)
        else:
            self._remove_from_system()
        

    def _remove_from_system(self) -> bool:
        """Remove device from system."""
        self.parent.remove_device_from_system(self.device.address)
        return False  # Don't repeat timeout


class SavedDeviceSlot(DeviceSlotBase):
    """Slot for saved but not currently discovered devices."""
    
    def __init__(self, device_info: SavedDevice, parent: "BluetoothConnections", **kwargs):
        super().__init__(parent, **kwargs)
        self.device_info = device_info

        # Build UI
        info_box = self._create_info_box(device_info.icon_name, device_info.name)
        self.start_children = [info_box]
        self.end_children = self.action_box
        
        self._update_action_buttons()

    def _update_action_buttons(self):
        """Update action buttons."""
        self._clear_action_box()
        self.action_box.add(self.delete_button)
        self.action_box.show_all()

    def _on_delete_clicked(self, button):
        self.parent.remove_saved_device(self.device_info.address)
        
        if self.parent.client.enabled:
            self.parent.remove_device_from_system(self.device_info.address)
        
        self.destroy()
        

class BluetoothConnections(Box):
    """Main Bluetooth connections manager widget."""
    
    CONFIG_PATH = Path.home() / ".cache" / "ax-shell" / "bluetooth.json"
    
    def __init__(self, **kwargs):
        self.widgets = kwargs.pop("widgets")
        
        super().__init__(
            name="bluetooth",
            spacing=4,
            orientation="vertical",
            **kwargs,
        )

        self._init_state()
        self._init_button_refs()
        self._init_client()
        self._create_ui()
        self._connect_signals()
        self._initial_refresh()

    def _init_state(self):
        """Initialize state variables."""
        self._last_scan_state: Optional[bool] = None
        self._last_bt_state: Optional[bool] = None
        self._scan_in_progress = False
        
        # Load saved devices
        self._saved_devices: List[SavedDevice] = self._load_saved_devices()
        self.saved_addresses: Set[str] = {d.address for d in self._saved_devices}

    def _init_button_refs(self):
        """Store references to external button widgets."""
        buttons = self.widgets.buttons.bluetooth_button
        self._bt_widgets = {
            "status_text": buttons.bluetooth_status_text,
            "status_button": buttons.bluetooth_status_button,
            "icon": buttons.bluetooth_icon,
            "label": buttons.bluetooth_label,
            "menu_button": buttons.bluetooth_menu_button,
            "menu_label": buttons.bluetooth_menu_label,
        }

    def _init_client(self):
        """Initialize Bluetooth client."""
        self.client = BluetoothClient(on_device_added=self._on_device_added)

    def _create_ui(self):
        """Create UI components."""
        # Scan button
        self.scan_label = Label(name="bluetooth-scan-label", markup=icons.radar)
        self.scan_button = Button(
            name="bluetooth-scan",
            child=self.scan_label,
            tooltip_text="Scanning Bluetooth",
            on_clicked=self._on_scan_clicked
        )
        
        # Back button
        back_button = Button(
            name="bluetooth-back",
            child=Label(name="bluetooth-back-label", markup=icons.chevron_left),
            on_clicked=lambda *_: self.widgets.show_notif()
        )

        # Подключаем обработчики нажатия
        self.scan_button.connect('button-press-event', self._on_button_press)
        back_button.connect('button-press-event', self._on_button_press)

        # Header
        header = CenterBox(name="bluetooth-header")
        header.set_start_children(back_button)
        header.set_center_children(Label(name="bluetooth-text", label="Bluetooth"))
        header.set_end_children(self.scan_button)

        # Device containers
        self.paired_box = Box(spacing=2, orientation="vertical")
        self.available_box = Box(spacing=2, orientation="vertical")

        # Content
        content = Box(spacing=4, orientation="vertical")
        content.add(Label(name="bluetooth-section", label="Saved devices"))
        content.add(self.paired_box)
        content.add(Label(name="bluetooth-section", label="Open devices"))
        content.add(self.available_box)

        # Scrolled window
        scrolled = ScrolledWindow(
            name="bluetooth-devices",
            min_content_size=(-1, -1),
            child=content,
            v_expand=True,
            propagate_width=False,
            propagate_height=False,
        )

        self.add(header)
        self.add(scrolled)

    def _connect_signals(self):
        """Connect all signal handlers."""
        self._bt_widgets["status_button"].connect("clicked", self._on_bt_toggle)
        self.client.connect("notify::enabled", self._on_bt_status_changed)
        self.client.connect("notify::scanning", self._on_scanning_changed)

    def _initial_refresh(self):
        """Perform initial refresh."""
        self._refresh_devices()
        self._on_bt_status_changed()
        self.client.notify("scanning")
        self.client.notify("enabled")

    # === Device Management ===
    
    def _load_saved_devices(self) -> List[SavedDevice]:
        """Load saved devices from config file."""
        try:
            self.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            
            if self.CONFIG_PATH.exists():
                with open(self.CONFIG_PATH) as f:
                    data = json.load(f)
                    return [SavedDevice.from_dict(d) for d in data.get("saved_devices", [])]
            else:
                self._save_devices_to_file([])
                return []
        except Exception as e:
            return []
        
    def _on_button_press(self, widget, event):
        """Обработчик нажатия кнопки с анимацией"""
        # Добавляем класс для анимации нажатия
        widget.add_style_class('pressed')
        
        # Удаляем класс через 150мс для повторной анимации
        GLib.timeout_add(150, lambda: widget.remove_style_class('pressed') if widget else False)
        
        return False

    def _save_devices_to_file(self, devices: List[SavedDevice]):
        with open(self.CONFIG_PATH, 'w') as f:
            json.dump({"saved_devices": [d.to_dict() for d in devices]}, f, indent=2)

    def add_saved_device(self, device: SavedDevice):
        """Add or update a saved device."""
        # Update existing or add new
        for i, d in enumerate(self._saved_devices):
            if d.address == device.address:
                self._saved_devices[i] = device
                break
        else:
            self._saved_devices.append(device)
        
        self.saved_addresses.add(device.address)
        self._save_devices_to_file(self._saved_devices)

    def remove_saved_device(self, address: str):
        """Remove a saved device."""
        self._saved_devices = [d for d in self._saved_devices if d.address != address]
        self.saved_addresses.discard(address)
        self._save_devices_to_file(self._saved_devices)

    def remove_device_from_system(self, address: str):
        # Try client API first
        device = self.client.get_device(address)
        if device and hasattr(self.client, 'remove_device'):
            if self.client.remove_device(device):
                return
        
        # Fallback to bluetoothctl
        subprocess.run(
            ["bluetoothctl", "remove", address],
            capture_output=True,
            text=True,
            timeout=10
        )\
    
    def _refresh_devices(self):
        """Refresh all device lists."""
        # Clear containers
        for child in self.paired_box.get_children():
            child.destroy()
        for child in self.available_box.get_children():
            child.destroy()
        
        # Get discovered device addresses
        discovered: Set[str] = set()
        if self.client.enabled:
            for device in self.client.get_devices():
                discovered.add(device.address)
        
        # Show saved devices
        for saved_device in self._saved_devices:
            if self.client.enabled and saved_device.address in discovered:
                device = self.client.get_device(saved_device.address)
                if device:
                    slot = BluetoothDeviceSlot(device, self.client, self)
                    self.paired_box.add(slot)
                    continue
            
            # Device not discovered - show saved slot
            slot = SavedDeviceSlot(saved_device, self)
            self.paired_box.add(slot)
        
        # Show available (non-saved) devices
        if self.client.enabled:
            self._populate_available_devices(discovered)
        
        self.paired_box.show_all()
        self.available_box.show_all()

    def _populate_available_devices(self, discovered: Set[str]):
        """Populate available devices box."""
        for device in self.client.get_devices():
            if device.address not in self.saved_addresses:
                slot = BluetoothDeviceSlot(device, self.client, self)
                self.available_box.add(slot)

    def _get_all_slots(self) -> List[DeviceSlotBase]:
        """Get all device slots from both containers."""
        return (
            list(self.paired_box.get_children()) +
            list(self.available_box.get_children())
        )

    def _update_all_action_buttons(self):
        """Update action buttons on all slots."""
        for slot in self._get_all_slots():
            if hasattr(slot, '_update_action_buttons'):
                slot._update_action_buttons()

    def _on_bt_toggle(self, button):
        """Toggle Bluetooth enabled state."""
        self.client.enabled = not self.client.enabled

    def _on_bt_status_changed(self, *args):
        """Handle Bluetooth status changes."""
        if self._last_bt_state == self.client.enabled:
            return
        
        self._last_bt_state = self.client.enabled
        
        self._update_status_label()
        self._update_main_button_style()
        self._refresh_devices()
        self._update_all_action_buttons()
        self._update_scan_button()

    def _on_scanning_changed(self, *args):
        """Handle scanning state changes."""
        self._update_scan_button()

    def _on_scan_clicked(self, button):
        """Handle scan button click."""
        if not self.client.enabled or self._scan_in_progress:
            return
        
        self._scan_in_progress = True
        self.client.toggle_scan()
        GLib.timeout_add(500, self._reset_scan_flag)

    def _reset_scan_flag(self) -> bool:
        """Reset scan click flag."""
        self._scan_in_progress = False
        return False

    def _on_device_added(self, client, address: str):
        """Handle new device discovery."""
        if not self.client.enabled:
            return
        
        device = client.get_device(address)
        if not device:
            return
        
        # Check if slot already exists
        for slot in self._get_all_slots():
            if hasattr(slot, 'device') and slot.device.address == address:
                return
        
        # Replace SavedDeviceSlot with BluetoothDeviceSlot if needed
        if address in self.saved_addresses:
            for slot in self.paired_box.get_children():
                if isinstance(slot, SavedDeviceSlot) and slot.device_info.address == address:
                    slot.destroy()
                    break
            
            new_slot = BluetoothDeviceSlot(device, client, self)
            self.paired_box.add(new_slot)
        else:
            new_slot = BluetoothDeviceSlot(device, client, self)
            self.available_box.add(new_slot)
        
        self.paired_box.show_all()
        self.available_box.show_all()

    # === UI Updates ===

    def _update_status_label(self):
        """Update status text label."""
        text = "Включен" if self.client.enabled else "Выключен"
        self._bt_widgets["status_text"].set_label(text)

    def _update_main_button_style(self):
        """Update main Bluetooth button styling."""
        enabled = self.client.enabled
        method = "remove_style_class" if enabled else "add_style_class"
        icon = icons.bluetooth if enabled else icons.bluetooth_off
        
        for widget in self._bt_widgets.values():
            if hasattr(widget, method):
                getattr(widget, method)("disabled")
        
        self._bt_widgets["icon"].set_markup(icon)

    def _update_scan_button(self):
        """Update scan button state and tooltip."""
        if not self.client.enabled:
            self.scan_label.remove_style_class("scanning")
            self.scan_button.remove_style_class("scanning")
            self.scan_button.set_tooltip_text("Bluetooth выключен")
            self._last_scan_state = None
            return
        
        scanning = self.client.scanning
        if self._last_scan_state == scanning:
            return
        
        self._last_scan_state = scanning
        
        method = "add_style_class" if scanning else "remove_style_class"
        getattr(self.scan_label, method)("scanning")
        getattr(self.scan_button, method)("scanning")
        
        tooltip = ("Stop scanning" if scanning else "Scanning Bluetooth")
        self.scan_button.set_tooltip_text(tooltip)

    # === Public API ===

    def connect_to_device(self, address: str) -> bool:
        """Connect or disconnect from a device by address."""
        if not self.client.enabled:
            return False
        
        try:
            device = self.client.get_device(address)
            if device:
                device.set_connecting(not device.connected)
                return True
            return False
        except Exception:
            return False

    def get_connected_devices(self) -> List[str]:
        """Get list of connected device addresses."""
        connected = []
        if self.client.enabled:
            try:
                for device in self.client.get_devices():
                    if device.connected:
                        connected.append(device.address)
            except Exception:
                pass
        return connected

    def is_any_device_connected(self) -> bool:
        """Check if any device is currently connected."""
        return len(self.get_connected_devices()) > 0

    def cleanup(self):
        """Cleanup resources."""
        # Disconnect all slot signals
        for slot in self._get_all_slots():
            if hasattr(slot, '_disconnect_signals'):
                slot._disconnect_signals()
            slot.destroy()

    def __del__(self):
        """Destructor."""
        try:
            self.cleanup()
        except Exception:
            pass