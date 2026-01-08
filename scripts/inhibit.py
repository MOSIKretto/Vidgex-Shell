import sys
from signal import SIGINT, SIGTERM, signal
from threading import Event

import setproctitle
from pywayland.client import Display
from pywayland.protocol.idle_inhibit_unstable_v1 import ZwpIdleInhibitManagerV1
from pywayland.protocol.wayland import WlCompositor


class Inhibitor:
    __slots__ = ('display', 'surface', 'inhibit_manager', 'inhibitor')
    
    def __init__(self):
        self.display = None
        self.surface = None
        self.inhibit_manager = None
        self.inhibitor = None
    
    def shutdown(self) -> None:
        if self.inhibitor:
            self.inhibitor.destroy()
        if self.display:
            self.display.dispatch()
            self.display.roundtrip()
            self.display.disconnect()
    
    def handle_registry_global(self, wl_registry, id_num: int, iface_name: str, version: int) -> None:
        if iface_name == "wl_compositor":
            compositor = wl_registry.bind(id_num, WlCompositor, version)
            self.surface = compositor.create_surface()
        elif iface_name == "zwp_idle_inhibit_manager_v1":
            self.inhibit_manager = wl_registry.bind(id_num, ZwpIdleInhibitManagerV1, version)


def main() -> None:
    done = Event()
    for sig in (SIGINT, SIGTERM):
        signal(sig, lambda *_: done.set())
    
    inhibitor = Inhibitor()
    
    try:
        inhibitor.display = Display()
        inhibitor.display.connect()
        
        registry = inhibitor.display.get_registry()
        registry.dispatcher["global"] = lambda *args: inhibitor.handle_registry_global(*args)
        
        inhibitor.display.dispatch()
        inhibitor.display.roundtrip()
        
        if not inhibitor.surface or not inhibitor.inhibit_manager:
            raise RuntimeError("Не удалось получить необходимые Wayland интерфейсы")
        
        inhibitor.inhibitor = inhibitor.inhibit_manager.create_inhibitor(inhibitor.surface)
        inhibitor.display.roundtrip()
        
        done.wait()
        
    except Exception as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        inhibitor.shutdown()


if __name__ == "__main__":
    setproctitle.setproctitle("vidgex-inhibit")
    main()