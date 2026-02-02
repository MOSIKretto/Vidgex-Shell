from fabric.bluetooth import BluetoothClient, BluetoothDevice
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow

import services.icons as icons


class BluetoothDeviceSlot(CenterBox):
    __slots__ = ('dev', '_lbl', '_btn', '_cb', '_lc', '_lg')

    def __init__(self, device: BluetoothDevice, on_pos_change=None):
        super().__init__(name="bluetooth-device")
        self.dev = device
        self._cb = on_pos_change
        self._lc = self._lg = None

        self._lbl = Label(name="bluetooth-connection")
        self._btn = Button(name="bluetooth-connect", on_clicked=self._toggle)

        self.start_children = Box(
            spacing=8,
            children=[
                Image(icon_name=device.icon_name + "-symbolic", size=16),
                Label(label=device.name, h_expand=True, h_align="start", ellipsization="end"),
                self._lbl,
            ]
        )
        self.end_children = self._btn

        device.connect("changed", self._on_chg)
        device.connect("notify::closed", lambda d, _: self.destroy() if d.closed else None)
        self._upd()

    def _toggle(self, *_):
        self.dev.set_connecting(not self.dev.connected)

    def _on_chg(self, *_):
        if self._lc != self.dev.connected and self._cb:
            self._cb(self)
        self._upd()

    def _upd(self):
        c, g = self.dev.connected, self.dev.connecting
        if self._lc == c and self._lg == g:
            return

        self._lc, self._lg = c, g
        self._lbl.set_markup(icons.bluetooth_connected if c else icons.bluetooth_disconnected)
        self._btn.set_label("Connecting..." if g else ("Disconnect" if c else "Connect"))

        ctx = self._btn.get_style_context()
        has = "connected" in (ctx.list_classes() or [])
        if c and not has:
            self._btn.add_style_class("connected")
        elif not c and has:
            self._btn.remove_style_class("connected")


class BluetoothConnections(Box):
    __slots__ = ('_w', '_btns', '_cl', '_cb', '_ob', '_sl', '_sb', '_le', '_ls')

    def __init__(self, **kwargs):
        w = kwargs.pop("widgets", None)
        if not w:
            raise ValueError("Widgets parameter is required")

        super().__init__(name="bluetooth", spacing=4, orientation="vertical", **kwargs)

        self._w = w
        self._btns = w.buttons.bluetooth_button
        self._le = self._ls = None

        self._sl = Label(name="bluetooth-scan-label", markup=icons.radar)
        self._sb = Button(name="bluetooth-scan", child=self._sl, on_clicked=lambda *_: self._cl.toggle_scan())

        self._cb = Box(spacing=2, orientation="vertical")
        self._ob = Box(spacing=2, orientation="vertical")

        self.children = [
            CenterBox(
                name="bluetooth-header",
                start_children=Button(
                    name="bluetooth-back",
                    child=Label(name="bluetooth-back-label", markup=icons.chevron_left),
                    on_clicked=lambda *_: self._w.show_notif()
                ),
                center_children=Label(name="bluetooth-text", label="Bluetooth"),
                end_children=self._sb
            ),
            ScrolledWindow(
                name="bluetooth-devices",
                child=Box(
                    spacing=4,
                    orientation="vertical",
                    children=[
                        Label(name="bluetooth-section", label="Connected"),
                        self._cb,
                        Label(name="bluetooth-section", label="Accessible"),
                        self._ob
                    ]
                ),
                v_expand=True,
                propagate_width=False,
                propagate_height=False,
            ),
        ]

        self._cl = BluetoothClient(on_device_added=self._add_dev)
        self._cl.connect("notify::enabled", self._on_en)
        self._cl.connect("notify::scanning", self._on_sc)

        self._on_en()
        self._on_sc()

    def _add_dev(self, cl, addr):
        dev = cl.get_device(addr)
        if dev:
            slot = BluetoothDeviceSlot(dev, on_pos_change=self._repos)
            (self._cb if dev.connected else self._ob).add(slot)

    def _repos(self, slot):
        np = self._cb if slot.dev.connected else self._ob
        op = slot.get_parent()
        if op is not np:
            if op:
                op.remove(slot)
            np.add(slot)

    def _on_en(self, *_):
        en = self._cl.enabled
        if self._le == en:
            return
        self._le = en

        b = self._btns
        m = "remove_style_class" if en else "add_style_class"

        for w in (b.bluetooth_status_text, b.bluetooth_status_button, b.bluetooth_icon,
                  b.bluetooth_label, b.bluetooth_menu_button, b.bluetooth_menu_label):
            getattr(w, m)("disabled")

        b.bluetooth_status_text.set_label("Enabled" if en else "Disabled")
        b.bluetooth_icon.set_markup(icons.bluetooth if en else icons.bluetooth_off)

    def _on_sc(self, *_):
        sc = self._cl.scanning
        if self._ls == sc:
            return
        self._ls = sc

        m = "add_style_class" if sc else "remove_style_class"
        getattr(self._sl, m)("scanning")
        getattr(self._sb, m)("scanning")
        self._sb.set_tooltip_text("Stop scanning" if sc else "Scan Bluetooth")

    def cleanup(self):
        for box in (self._cb, self._ob):
            for c in box.get_children():
                c.destroy()
            box.children = []
        self._cl = None
        self._w = self._btns = None