from pywayland.client import Display
from pywayland.protocol.idle_inhibit_unstable_v1 import ZwpIdleInhibitManagerV1
from pywayland.protocol.wayland import WlCompositor


class Caffeine:
    __slots__ = ("display", "surface", "inhibit_manager", "inhibitor", "_is_active")

    def __init__(self):
        self.display = None
        self.surface = None
        self.inhibit_manager = None
        self.inhibitor = None
        self._is_active = False

    def _handle_registry_global(self, wl_registry, id_num: int, iface_name: str, version: int) -> None:
        if iface_name == "wl_compositor":
            compositor = wl_registry.bind(id_num, WlCompositor, version)
            self.surface = compositor.create_surface()
        elif iface_name == "zwp_idle_inhibit_manager_v1":
            self.inhibit_manager = wl_registry.bind(id_num, ZwpIdleInhibitManagerV1, version)

    def enable(self) -> bool:
        if self._is_active:
            return True

        self.display = Display()
        self.display.connect()

        registry = self.display.get_registry()
        registry.dispatcher["global"] = self._handle_registry_global

        self.display.dispatch()
        self.display.roundtrip()

        if not self.surface or not self.inhibit_manager:
            self.disable()
            return False

        self.inhibitor = self.inhibit_manager.create_inhibitor(self.surface)
        self.display.roundtrip()
        self._is_active = True
        return True

    def disable(self) -> None:
        if self.inhibitor:
            self.inhibitor.destroy()
            self.inhibitor = None
        if self.display:
            self.display.dispatch()
            self.display.roundtrip()
            self.display.disconnect()
            self.display = None

        self.surface = None
        self.inhibit_manager = None
        self._is_active = False

    def toggle(self) -> bool:
        if self._is_active:
            self.disable()
            return False
        return self.enable()

    @property
    def is_enabled(self) -> bool:
        return self._is_active

    def shutdown(self) -> None:
        self.disable()

    def __enter__(self):
        self.enable()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disable()