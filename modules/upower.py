import dbus

class UPowerManager:
    def __init__(self):
        self.UPOWER_NAME = "org.freedesktop.UPower"
        self.UPOWER_PATH = "/org/freedesktop/UPower"
        self.DBUS_PROPERTIES = "org.freedesktop.DBus.Properties"
        self.bus = dbus.SystemBus()
    
    # Вспомогательные методы для получения объектов
    def _get_upower_object(self):
        return self.bus.get_object(self.UPOWER_NAME, self.UPOWER_PATH)
    
    def _get_upower_interface(self):
        return dbus.Interface(self._get_upower_object(), self.UPOWER_NAME)
    
    def _get_properties_interface(self, path):
        return dbus.Interface(self.bus.get_object(self.UPOWER_NAME, path), self.DBUS_PROPERTIES)
    
    def _get_upower_properties(self):
        return dbus.Interface(self._get_upower_object(), self.DBUS_PROPERTIES)
    
    def _get_wakeups_object(self):
        return self.bus.get_object(self.UPOWER_NAME, f"{self.UPOWER_PATH}/Wakeups")
    
    # Основные методы
    def detect_devices(self):
        return self._get_upower_interface().EnumerateDevices()
    
    def get_display_device(self):
        return self._get_upower_interface().GetDisplayDevice()
    
    def get_critical_action(self):
        return self._get_upower_interface().GetCriticalAction()
    
    def get_device_percentage(self, battery):
        props = self._get_properties_interface(battery)
        return props.Get(f"{self.UPOWER_NAME}.Device", "Percentage")
    
    def get_full_device_information(self, battery):
        props = self._get_properties_interface(battery)
        all_props = props.GetAll(f"{self.UPOWER_NAME}.Device")
        
        # Список всех полей с их значениями по умолчанию
        fields = [
            ('HasHistory', False), ('HasStatistics', False), ('IsPresent', False),
            ('IsRechargeable', False), ('Online', False), ('PowerSupply', False),
            ('Capacity', 0.0), ('Energy', 0.0), ('EnergyEmpty', 0.0), ('EnergyFull', 0.0),
            ('EnergyFullDesign', 0.0), ('EnergyRate', 0.0), ('Luminosity', 0.0),
            ('Percentage', 0.0), ('Temperature', 0.0), ('Voltage', 0.0),
            ('TimeToEmpty', 0), ('TimeToFull', 0), ('IconName', ''),
            ('Model', ''), ('NativePath', ''), ('Serial', ''), ('Vendor', ''),
            ('State', 0), ('Technology', 0), ('Type', 0), ('WarningLevel', 0),
            ('UpdateTime', 0)
        ]
        
        return {field: all_props.get(field, default) for field, default in fields}
    
    def is_lid_present(self):
        return bool(self._get_upower_properties().Get(self.UPOWER_NAME, 'LidIsPresent'))
    
    def is_lid_closed(self):
        return bool(self._get_upower_properties().Get(self.UPOWER_NAME, 'LidIsClosed'))
    
    def on_battery(self):
        return bool(self._get_upower_properties().Get(self.UPOWER_NAME, 'OnBattery'))
    
    def has_wakeup_capabilities(self):
        wakeups_obj = self._get_wakeups_object()
        wakeups_props = dbus.Interface(wakeups_obj, self.DBUS_PROPERTIES)
        return bool(wakeups_props.Get(f"{self.UPOWER_NAME}.Wakeups", 'HasCapability'))
    
    def get_wakeups_data(self):
        wakeups_obj = self._get_wakeups_object()
        wakeups_interface = dbus.Interface(wakeups_obj, f"{self.UPOWER_NAME}.Wakeups")
        return wakeups_interface.GetData()
    
    def get_wakeups_total(self):
        wakeups_obj = self._get_wakeups_object()
        wakeups_interface = dbus.Interface(wakeups_obj, f"{self.UPOWER_NAME}.Wakeups")
        return wakeups_interface.GetTotal()
    
    def is_loading(self, battery):
        props = self._get_properties_interface(battery)
        state = int(props.Get(f"{self.UPOWER_NAME}.Device", "State"))
        return state == 1
    
    def get_state(self, battery):
        props = self._get_properties_interface(battery)
        state = int(props.Get(f"{self.UPOWER_NAME}.Device", "State"))
        
        states = {
            0: "Unknown",
            1: "Loading",
            2: "Discharging",
            3: "Empty",
            4: "Fully charged",
            5: "Pending charge",
            6: "Pending discharge"
        }
        
        return states.get(state, "Unknown")