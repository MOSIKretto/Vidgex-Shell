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
        
        # Прямая привязка сигналов без создания лишних оберток
        self.device.connect("changed", self._update_ui)
        self.device.connect("notify::closed", self._on_closed)

        self._connection_label = Label(name="bluetooth-connection")
        self._connect_button = Button(
            name="bluetooth-connect",
            on_clicked=self._on_connect_clicked
        )

        # Компоновка без промежуточных переменных
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
        dev = self.device
        conn = dev.connected
        
        # Обновление иконки и стиля кнопки
        self._connection_label.set_markup(icons.bluetooth_connected if conn else icons.bluetooth_disconnected)
        
        if dev.connecting:
            self._connect_button.set_label("Connecting...")
        else:
            self._connect_button.set_label("Disconnect" if conn else "Connect")

        if conn:
            self._connect_button.add_style_class("connected")
        else:
            self._connect_button.remove_style_class("connected")

class BluetoothConnections(Box):
    def __init__(self, **kwargs):
        # Извлекаем widgets сразу, чтобы не хранить весь kwargs
        widgets = kwargs.pop("widgets", None)
        if not widgets:
            raise ValueError("Widgets parameter is required")
            
        super().__init__(name="bluetooth", spacing=4, orientation="vertical", **kwargs)
        
        self._widgets = widgets
        self._btns = widgets.buttons.bluetooth_button
        self._device_slots = {}
        
        self._client = BluetoothClient(on_device_added=self._on_device_added)
        self._setup_ui()
        
        # Подписки на изменения состояния
        self._client.connect("notify::enabled", self._update_status_label)
        self._client.connect("notify::scanning", self._update_scan_label)
        
        # Начальное состояние
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
        btns = self._btns
        
        # Список таргетов для массового обновления стилей (без словарей)
        targets = (btns.bluetooth_status_text, btns.bluetooth_status_button, 
                   btns.bluetooth_icon, btns.bluetooth_label, 
                   btns.bluetooth_menu_button, btns.bluetooth_menu_label)
        
        for w in targets:
            w.remove_style_class("disabled") if enabled else w.add_style_class("disabled")
        
        btns.bluetooth_status_text.set_label("Enabled" if enabled else "Disabled")
        btns.bluetooth_icon.set_markup(icons.bluetooth if enabled else icons.bluetooth_off)

    def _on_device_added(self, client, address):
        if device := client.get_device(address):
            slot = BluetoothDeviceSlot(device)
            self._device_slots[address] = slot
            # Передаем адрес через замыкание для корректной позиции
            device.connect("changed", lambda *_: self._update_device_position(address))
            self._update_device_position(address)

    def _update_device_position(self, address):
        if not (slot := self._device_slots.get(address)):
            return
        
        new_parent = self._connected_box if slot.device.connected else self._other_box
        
        # ОПТИМИЗАЦИЯ: перемещаем только если родитель действительно изменился
        if slot.get_parent() != new_parent:
            if old_parent := slot.get_parent():
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

    def destroy(self):
        # Останавливаем сканирование перед уничтожением для экономии CPU
        if self._client and getattr(self._client, 'scanning', False):
            self._client.toggle_scan()
        
        for slot in self._device_slots.values():
            slot.destroy()
            
        self._device_slots.clear()
        self._widgets = None
        self._btns = None
        self._client = None
        super().destroy()