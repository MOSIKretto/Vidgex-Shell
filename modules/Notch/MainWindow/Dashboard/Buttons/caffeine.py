from pywayland.client import Display
from pywayland.protocol.idle_inhibit_unstable_v1 import ZwpIdleInhibitManagerV1
from pywayland.protocol.wayland import WlCompositor


class Caffeine:
    __slots__ = ("display", "surface", "inhibit_manager", "inhibitor")

    def __init__(self):
        self.display = None
        self.surface = None
        self.inhibit_manager = None
        self.inhibitor = None

    def _on_global(self, wl_registry, id_num: int, iface_name: str, version: int) -> None:
        if iface_name == "wl_compositor":
            self.surface = wl_registry.bind(id_num, WlCompositor, version).create_surface()
        elif iface_name == "zwp_idle_inhibit_manager_v1":
            self.inhibit_manager = wl_registry.bind(id_num, ZwpIdleInhibitManagerV1, version)

    def enable(self) -> bool:
        if self.inhibitor is not None:
            return True

        display = Display()
        try:
            display.connect()
        except ValueError:
            return False
        self.display = display

        registry = display.get_registry()
        registry.dispatcher["global"] = self._on_global
        display.roundtrip()

        if self.surface is None or self.inhibit_manager is None:
            self.disable()
            return False

        self.inhibitor = self.inhibit_manager.create_inhibitor(self.surface)
        display.roundtrip()
        return True

    def disable(self) -> None:
        if self.inhibitor is not None:
            self.inhibitor.destroy()
        if self.display is not None:
            self.display.disconnect()
        self.display = self.surface = self.inhibit_manager = self.inhibitor = None

    def toggle(self) -> bool:
        if self.is_enabled:
            self.disable()
            return False
        return self.enable()

    @property
    def is_enabled(self) -> bool:
        return self.inhibitor is not None

    def shutdown(self) -> None:
        self.disable()

    def __enter__(self):
        self.enable()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disable()