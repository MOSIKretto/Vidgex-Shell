import gi
gi.require_version("GnomeBluetooth", "3.0")
from gi.repository import GnomeBluetooth, GLib

client = GnomeBluetooth.Client.new()
loop = GLib.MainLoop()

def check(*_):
    for dev in client.get_devices():
        proxy = dev.get_property("proxy")
        print("path:", proxy.get_object_path(), flush=True)
        print("iface:", proxy.get_interface_name(), flush=True)
        props = proxy.get_cached_property_names()
        print("cached props:", list(props) if props else None, flush=True)
    return True

GLib.timeout_add(2000, check)
loop.run()