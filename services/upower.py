import dbus

class UPowerManager:
    # Выносим статические данные в класс для экономии памяти и ЦП (не пересоздаем каждый раз)
    _DEFAULTS = {
        'HasHistory': False, 'HasStatistics': False, 'IsPresent': False,
        'IsRechargeable': False, 'Online': False, 'PowerSupply': False,
        'Capacity': 0.0, 'Energy': 0.0, 'EnergyEmpty': 0.0, 'EnergyFull': 0.0,
        'EnergyFullDesign': 0.0, 'EnergyRate': 0.0, 'Luminosity': 0.0,
        'Percentage': 0.0, 'Temperature': 0.0, 'Voltage': 0.0,
        'TimeToEmpty': 0, 'TimeToFull': 0, 'IconName': '',
        'Model': '', 'NativePath': '', 'Serial': '', 'Vendor': '',
        'State': 0, 'Technology': 0, 'Type': 0, 'WarningLevel': 0,
        'UpdateTime': 0
    }
    
    _STATES = (
        "Unknown", "Loading", "Discharging", "Empty", 
        "Fully charged", "Pending charge", "Pending discharge"
    )

    def __init__(self):
        self.bus = dbus.SystemBus()
        self.up_name = "org.freedesktop.UPower"
        self.up_path = "/org/freedesktop/UPower"
        self.props_iface = "org.freedesktop.DBus.Properties"

    def _get_prop(self, path, iface, prop):
        """Минималистичный помощник для получения свойств."""
        obj = self.bus.get_object(self.up_name, path)
        return obj.Get(iface, prop, dbus_interface=self.props_iface)

    def detect_devices(self):
        return self.bus.get_object(self.up_name, self.up_path).EnumerateDevices(dbus_interface=self.up_name)
    
    def get_display_device(self):
        return self.bus.get_object(self.up_name, self.up_path).GetDisplayDevice(dbus_interface=self.up_name)
    
    def get_critical_action(self):
        return self.bus.get_object(self.up_name, self.up_path).GetCriticalAction(dbus_interface=self.up_name)
    
    def get_device_percentage(self, battery):
        return self._get_prop(battery, f"{self.up_name}.Device", "Percentage")
    
    def get_full_device_information(self, battery):
        obj = self.bus.get_object(self.up_name, battery)
        all_props = obj.GetAll(f"{self.up_name}.Device", dbus_interface=self.props_iface)
        # Используем заранее созданный словарь _DEFAULTS для скорости
        return {key: all_props.get(key, default) for key, default in self._DEFAULTS.items()}
    
    def is_lid_present(self):
        return bool(self._get_prop(self.up_path, self.up_name, 'LidIsPresent'))
    
    def is_lid_closed(self):
        return bool(self._get_prop(self.up_path, self.up_name, 'LidIsClosed'))
    
    def on_battery(self):
        return bool(self._get_prop(self.up_path, self.up_name, 'OnBattery'))
    
    def has_wakeup_capabilities(self):
        return bool(self._get_prop(f"{self.up_path}/Wakeups", f"{self.up_name}.Wakeups", 'HasCapability'))
    
    def get_wakeups_data(self):
        return self.bus.get_object(self.up_name, f"{self.up_path}/Wakeups").GetData(dbus_interface=f"{self.up_name}.Wakeups")
    
    def get_wakeups_total(self):
        return self.bus.get_object(self.up_name, f"{self.up_path}/Wakeups").GetTotal(dbus_interface=f"{self.up_name}.Wakeups")
    
    def is_loading(self, battery):
        return int(self._get_prop(battery, f"{self.up_name}.Device", "State")) == 1
    
    def get_state(self, battery):
        state = int(self._get_prop(battery, f"{self.up_name}.Device", "State"))
        if 0 <= state < len(self._STATES):
            return self._STATES[state]
        return "Unknown"