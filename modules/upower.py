import dbus

class UPowerManager():

    def __init__(self):
        self.UPOWER_NAME = "org.freedesktop.UPower"
        self.UPOWER_PATH = "/org/freedesktop/UPower"
        self.DBUS_PROPERTIES = "org.freedesktop.DBus.Properties"
        self.bus = dbus.SystemBus()

    def detect_devices(self):
        upower_proxy = self.bus.get_object(self.UPOWER_NAME, self.UPOWER_PATH)
        upower_interface = dbus.Interface(upower_proxy, self.UPOWER_NAME)
        devices = upower_interface.EnumerateDevices()
        return devices

    def get_display_device(self):
        upower_proxy = self.bus.get_object(self.UPOWER_NAME, self.UPOWER_PATH)
        upower_interface = dbus.Interface(upower_proxy, self.UPOWER_NAME)
        dispdev = upower_interface.GetDisplayDevice()
        return dispdev

    def get_critical_action(self):
        upower_proxy = self.bus.get_object(self.UPOWER_NAME, self.UPOWER_PATH)
        upower_interface = dbus.Interface(upower_proxy, self.UPOWER_NAME)
        critical_action = upower_interface.GetCriticalAction()
        return critical_action

    def get_device_percentage(self, battery):
        battery_proxy = self.bus.get_object(self.UPOWER_NAME, battery)
        battery_proxy_interface = dbus.Interface(battery_proxy, self.DBUS_PROPERTIES)
        percentage = battery_proxy_interface.Get(self.UPOWER_NAME + ".Device", "Percentage")
        return percentage

    def get_full_device_information(self, battery):
        battery_proxy = self.bus.get_object(self.UPOWER_NAME, battery)
        battery_proxy_interface = dbus.Interface(battery_proxy, self.DBUS_PROPERTIES)
        all_properties = battery_proxy_interface.GetAll(self.UPOWER_NAME + ".Device")
        
        information_table = {}
        information_table['HasHistory'] = all_properties.get('HasHistory', False)
        information_table['HasStatistics'] = all_properties.get('HasStatistics', False)
        information_table['IsPresent'] = all_properties.get('IsPresent', False)
        information_table['IsRechargeable'] = all_properties.get('IsRechargeable', False)
        information_table['Online'] = all_properties.get('Online', False)
        information_table['PowerSupply'] = all_properties.get('PowerSupply', False)
        information_table['Capacity'] = all_properties.get('Capacity', 0.0)
        information_table['Energy'] = all_properties.get('Energy', 0.0)
        information_table['EnergyEmpty'] = all_properties.get('EnergyEmpty', 0.0)
        information_table['EnergyFull'] = all_properties.get('EnergyFull', 0.0)
        information_table['EnergyFullDesign'] = all_properties.get('EnergyFullDesign', 0.0)
        information_table['EnergyRate'] = all_properties.get('EnergyRate', 0.0)
        information_table['Luminosity'] = all_properties.get('Luminosity', 0.0)
        information_table['Percentage'] = all_properties.get('Percentage', 0.0)
        information_table['Temperature'] = all_properties.get('Temperature', 0.0)
        information_table['Voltage'] = all_properties.get('Voltage', 0.0)
        information_table['TimeToEmpty'] = all_properties.get('TimeToEmpty', 0)
        information_table['TimeToFull'] = all_properties.get('TimeToFull', 0)
        information_table['IconName'] = all_properties.get('IconName', '')
        information_table['Model'] = all_properties.get('Model', '')
        information_table['NativePath'] = all_properties.get('NativePath', '')
        information_table['Serial'] = all_properties.get('Serial', '')
        information_table['Vendor'] = all_properties.get('Vendor', '')
        information_table['State'] = all_properties.get('State', 0)
        information_table['Technology'] = all_properties.get('Technology', 0)
        information_table['Type'] = all_properties.get('Type', 0)
        information_table['WarningLevel'] = all_properties.get('WarningLevel', 0)
        information_table['UpdateTime'] = all_properties.get('UpdateTime', 0)

        return information_table

    def is_lid_present(self):
        upower_proxy = self.bus.get_object(self.UPOWER_NAME, self.UPOWER_PATH)
        upower_interface = dbus.Interface(upower_proxy, self.DBUS_PROPERTIES)
        is_lid_present = upower_interface.Get(self.UPOWER_NAME, 'LidIsPresent')
        return bool(is_lid_present)

    def is_lid_closed(self):
        upower_proxy = self.bus.get_object(self.UPOWER_NAME, self.UPOWER_PATH)
        upower_interface = dbus.Interface(upower_proxy, self.DBUS_PROPERTIES)
        is_lid_closed = upower_interface.Get(self.UPOWER_NAME, 'LidIsClosed')
        return bool(is_lid_closed)

    def on_battery(self):
        upower_proxy = self.bus.get_object(self.UPOWER_NAME, self.UPOWER_PATH)
        upower_interface = dbus.Interface(upower_proxy, self.DBUS_PROPERTIES)
        on_battery = upower_interface.Get(self.UPOWER_NAME, 'OnBattery')
        return bool(on_battery)

    def has_wakeup_capabilities(self):
        upower_proxy = self.bus.get_object(self.UPOWER_NAME, self.UPOWER_PATH + "/Wakeups")
        upower_interface = dbus.Interface(upower_proxy, self.DBUS_PROPERTIES)
        has_wakeup_capabilities = upower_interface.Get(self.UPOWER_NAME + '.Wakeups', 'HasCapability')
        return bool(has_wakeup_capabilities)

    def get_wakeups_data(self):
        upower_proxy = self.bus.get_object(self.UPOWER_NAME, self.UPOWER_PATH + "/Wakeups")
        upower_interface = dbus.Interface(upower_proxy, self.UPOWER_NAME + '.Wakeups')
        data = upower_interface.GetData()
        return data

    def get_wakeups_total(self):
        upower_proxy = self.bus.get_object(self.UPOWER_NAME, self.UPOWER_PATH + "/Wakeups")
        upower_interface = dbus.Interface(upower_proxy, self.UPOWER_NAME + '.Wakeups')
        data = upower_interface.GetTotal()
        return data

    def is_loading(self, battery):
        battery_proxy = self.bus.get_object(self.UPOWER_NAME, battery)
        battery_proxy_interface = dbus.Interface(battery_proxy, self.DBUS_PROPERTIES)
        state = battery_proxy_interface.Get(self.UPOWER_NAME + ".Device", "State")
        state_int = int(state)
        if state_int == 1:
            return True
        else:
            return False

    def get_state(self, battery):
        battery_proxy = self.bus.get_object(self.UPOWER_NAME, battery)
        battery_proxy_interface = dbus.Interface(battery_proxy, self.DBUS_PROPERTIES)
        state = battery_proxy_interface.Get(self.UPOWER_NAME + ".Device", "State")
        state_int = int(state)

        if state_int == 0:
            return "Unknown"
        elif state_int == 1:
            return "Loading"
        elif state_int == 2:
            return "Discharging"
        elif state_int == 3:
            return "Empty"
        elif state_int == 4:
            return "Fully charged"
        elif state_int == 5:
            return "Pending charge"
        elif state_int == 6:
            return "Pending discharge"
        else:
            return "Unknown"