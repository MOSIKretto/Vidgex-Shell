from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow

from gi.repository import Gtk, NM, GLib, Gio

import services.icons as icons
from services.network import NetworkClient


class WifiSlot(CenterBox):
    __slots__ = ('nc', 'parent', 'rcb', 'ssid', 'saved', 'conn', '_btn', 'icon', 'name_lbl', 'str_lbl', 'act_box')
    
    _pw = None

    def __init__(self, nc, parent, rcb):
        super().__init__(name="wifi-network-slot")
        
        self.nc = nc
        self.parent = parent
        self.rcb = rcb
        self.ssid = None
        self.saved = False
        self.conn = False
        self._btn = None

        self.icon = Image(size=16)
        self.name_lbl = Label(h_expand=True, h_align="start", ellipsization="end")
        self.str_lbl = Label()

        self.start_children = Box(spacing=8, h_expand=True, h_align="fill",
                                  children=[self.icon, self.name_lbl, self.str_lbl])

        self.act_box = Box(orientation="horizontal", spacing=4)
        self.end_children = self.act_box

    def update(self, data, saved=False, conn=False):
        self.ssid = data.get("ssid", "Unknown")
        self.saved = saved
        self.conn = conn
        self._btn = None

        self.icon.set_from_icon_name(data.get("icon-name", "network-wireless-signal-none-symbolic"), 24)
        self.name_lbl.set_label(self.ssid)
        self.str_lbl.set_label(f"{data.get('strength', 0)}%")

        for c in self.act_box.get_children():
            c.destroy()

        if conn:
            self.act_box.add(Label(label="Подключено", name="wifi-connected-label"))
            self.act_box.add(self._mk_del())
        elif saved:
            if self._avail():
                self._btn = self._mk_conn()
                self.act_box.add(self._btn)
            else:
                self.act_box.add(self._mk_unavail())
            self.act_box.add(self._mk_del())
        elif self._avail():
            self._btn = self._mk_conn()
            self.act_box.add(self._btn)
        else:
            self.act_box.add(self._mk_unavail())

        self.act_box.show_all()
        return self

    def _avail(self):
        return hasattr(self.nc, 'is_network_available') and self.ssid and self.nc.is_network_available(self.ssid)

    def _mk_conn(self):
        btn = Button(name="wifi-connect", label="Подключиться")
        btn.connect("clicked", self._on_conn)
        return btn

    def _mk_del(self):
        btn = Button(name="wifi-delete", child=Label(name="wifi-delete-label", markup=icons.trash), tooltip_text="Удалить сеть")
        btn.connect("clicked", self._on_del)
        return btn

    def _mk_unavail(self):
        btn = Button(name="wifi-unavailable", label="Недоступна", sensitive=False, can_focus=False)
        btn.get_style_context().add_class("unavailable-network")
        return btn

    def _on_conn(self, btn):
        if self.saved:
            btn.set_sensitive(False)
            btn.set_label("Подключение...")
            self.nc.connect_to_saved_network(self.ssid, self._ok, self._err)
        else:
            self._tog_pw()

    def _tog_pw(self):
        p = self.get_parent()
        if not p:
            return

        if WifiSlot._pw and WifiSlot._pw[0] is self:
            WifiSlot._pw[1].destroy()
            WifiSlot._pw = None
            return

        if WifiSlot._pw:
            WifiSlot._pw[1].destroy()

        pw = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, name="password-entry-container")
        pw.set_margin_top(4)
        pw.set_margin_bottom(12)
        pw.set_margin_start(10)
        pw.set_margin_end(10)

        entry = Gtk.Entry(visibility=False, invisible_char='•', placeholder_text="Пароль...")

        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        cancel = Gtk.Button(label="Отмена")
        ok = Gtk.Button(label="ОК", sensitive=False)

        cancel.connect("clicked", lambda _: self._close_pw())
        ok.connect("clicked", lambda _: self._submit(entry.get_text()))
        entry.connect("changed", lambda e: ok.set_sensitive(bool(e.get_text())))
        entry.connect("activate", lambda e: ok.get_sensitive() and ok.clicked())

        btns.pack_start(cancel, True, True, 0)
        btns.pack_start(ok, True, True, 0)
        pw.pack_start(entry, False, False, 0)
        pw.pack_start(btns, False, False, 0)

        WifiSlot._pw = (self, pw)

        idx = p.get_children().index(self)
        p.pack_start(pw, False, False, 0)
        p.reorder_child(pw, idx + 1)
        pw.show_all()
        GLib.idle_add(entry.grab_focus)

    def _close_pw(self):
        if WifiSlot._pw:
            WifiSlot._pw[1].destroy()
            WifiSlot._pw = None

    def _submit(self, pwd):
        self._close_pw()
        if self._btn:
            self._btn.set_sensitive(False)
            self._btn.set_label("Подключение...")
        self.nc.connect_to_new_network(self.ssid, pwd, self._ok, self._err)

    def _ok(self, ssid):
        if self.rcb:
            GLib.timeout_add(500, self.rcb)

    def _err(self, *_):
        if self._btn:
            self._btn.set_label("Ошибка")
            self._btn.set_sensitive(False)
            GLib.timeout_add(3000, self._restore)

    def _restore(self):
        if self._btn:
            self._btn.set_label("Подключиться")
            self._btn.set_sensitive(True)
        return False

    def _on_del(self, _):
        if self.ssid:
            self.nc.delete_saved_network(self.ssid)
            if self.rcb:
                GLib.timeout_add(300, self.rcb)


class NetworkConnections(Box):
    __slots__ = ('widgets', '_rid', '_scan', '_slots', '_lio', 'nc',
                 'dl_lbl', 'ul_lbl', 'scan_lbl', 'scan_btn', 'saved_box', 'avail_box', 'scroll')

    _U = ((1048576, "MB/s"), (1024, "KB/s"), (1, "B/s"))

    def __init__(self, **kwargs):
        super().__init__(name="network-connections", spacing=4, orientation="vertical", **kwargs)

        self.widgets = kwargs["widgets"]
        self._rid = None
        self._scan = False
        self._slots = {"saved": [], "avail": []}
        self._lio = self._rio()

        try:
            self.nc = NetworkClient()
        except:
            self.nc = None

        self._build()

        GLib.timeout_add(1000, self._uspd)

        if self.nc:
            try:
                self.nc.connect("device-ready", self._rdy)
                self.nc.connect("connection-error", self._cerr)
            except:
                pass

            if hasattr(self.nc, 'wifi_device') and self.nc.wifi_device:
                GLib.idle_add(self._rdy)

    def _rio(self):
        recv = sent = 0
        try:
            f = Gio.File.new_for_path("/proc/net/dev")
            ok, data, _ = f.load_contents(None)
            if ok:
                for line in data.decode().split('\n')[2:]:
                    p = line.split()
                    if not p or p[0].rstrip(':') == 'lo':
                        continue
                    if len(p) >= 10:
                        recv += int(p[1])
                        sent += int(p[9])
        except:
            pass
        return recv, sent

    def _build(self):
        self.dl_lbl = Label(name="download-label", markup="0 B/s")
        self.ul_lbl = Label(name="upload-label", markup="0 B/s")

        self.scan_lbl = Label(name="network-scan-label", markup=icons.radar)
        self.scan_btn = Button(name="network-scan", child=self.scan_lbl, tooltip_text="Сканировать Wi-Fi сети")
        self.scan_btn.connect("clicked", self._on_scan)

        back = Button(name="network-back", child=Label(name="network-back-label", markup=icons.chevron_left))
        back.connect("clicked", lambda *_: self.widgets.show_notif())

        self.saved_box = Box(name="saved-box", spacing=2, orientation="vertical")
        self.avail_box = Box(name="available-box", spacing=2, orientation="vertical")

        content = Box(
            spacing=4, 
            orientation="vertical", 
            children=[
                Label(name="network-section", label="Сохраненные сети"),
                self.saved_box,
                Label(name="network-section", label="Доступные сети"),
                self.avail_box
            ]
        )

        center = Box(
            orientation="horizontal", spacing=12, h_align="center",
            children=[
                Box(
                    orientation="horizontal", 
                    spacing=4, 
                    v_align="center", 
                    children=[
                        self.ul_lbl, 
                        Label(
                            name="upload-icon-label", 
                            markup=icons.upload
                        )
                    ]
                ),
                Label(name="network-text", label="Wi-Fi", v_align="center"),
                Box(
                    orientation="horizontal", 
                    spacing=4, 
                    v_align="center",
                    children=[
                        Label(
                            name="download-icon-label", 
                            markup=icons.download
                        ), 
                    self.dl_lbl
                    ]
                ),
            ]
        )

        self.scroll = ScrolledWindow(
            name="bluetooth-devices", 
            min_content_size=(-1, -1),
            child=content, 
            v_expand=True, 
            propagate_width=False, 
            propagate_height=False
        )

        self.children = [
            CenterBox(
                name="network-header", 
                start_children=back, 
                center_children=center, 
                end_children=self.scan_btn
            ),
            self.scroll
        ]

    def _fmt(self, s):
        for th, u in self._U:
            if s >= th:
                return f"{s / th:.1f} {u}" if th > 1 else f"{int(s)} {u}"
        return "0 B/s"

    def _uspd(self):
        c = self._rio()
        self.dl_lbl.set_markup(self._fmt(max(0, c[0] - self._lio[0])))
        self.ul_lbl.set_markup(self._fmt(max(0, c[1] - self._lio[1])))
        self._lio = c
        return True

    def _rdy(self, client=None):
        if self.nc and hasattr(self.nc, 'wifi_device') and self.nc.wifi_device:
            try:
                self.nc.wifi_device.connect("changed", self._sched)
            except:
                pass
            self._sched()

    def _cerr(self, client, ssid, msg):
        for pool in self._slots.values():
            for slot in pool:
                if slot.ssid == ssid:
                    slot._err()
                    return

    def _sched(self, *_):
        if not self._rid:
            self._rid = GLib.timeout_add(500, self._ref)

    def _ref(self):
        self._rid = None

        if WifiSlot._pw:
            return False

        nc = self.nc
        dev = nc.wifi_device if nc and hasattr(nc, 'wifi_device') else None
        en = bool(dev and getattr(dev, 'enabled', False))

        cur = self._gcur()
        saved = self._gsaved()
        avail = dev.access_points if en and dev else []
        avail_d = {ap.get("ssid"): ap for ap in avail if ap.get("ssid")}

        saved_s = set(saved)
        tl = self.get_toplevel()
        rcb = lambda: (self._ref(), False)[1]

        dap = {"strength": 0, "is_secured": True, "icon-name": "network-wireless-signal-none-symbolic"}

        sd = []
        if cur and cur in saved:
            ap = avail_d.get(cur, {"ssid": cur, **dap, "icon-name": "network-wireless-signal-excellent-symbolic"})
            sd.append((ap, True, True))

        for ssid in saved:
            if ssid != cur:
                sd.append((avail_d.get(ssid, {"ssid": ssid, **dap}), True, False))

        ad = [(ap, False, False) for ap in avail if (s := ap.get("ssid")) and s not in saved_s and s != cur]

        self._ubox(self.saved_box, self._slots["saved"], sd, tl, rcb)
        self._ubox(self.avail_box, self._slots["avail"], ad, tl, rcb)

        st = getattr(dev, 'strength', 0) if dev else 0
        txt = "Выключено" if not en else (cur or "Не подключено")

        if hasattr(self.widgets, 'update_network_display'):
            self.widgets.update_network_display(txt, st, en)

        return False

    def _ubox(self, box, pool, data, tl, rcb):
        n = len(data)
        e = len(pool)

        while len(pool) < n:
            slot = WifiSlot(self.nc, tl, rcb)
            pool.append(slot)
            box.add(slot)

        for i in range(n, e):
            pool[i].hide()

        for i, (ap, saved, conn) in enumerate(data):
            pool[i].update(ap, saved, conn)
            pool[i].show()

        box.show_all()

    def _gcur(self):
        if not self.nc or not hasattr(self.nc, 'wifi_device'):
            return None
        dev = self.nc.wifi_device
        if not dev or not hasattr(dev, 'ssid'):
            return None
        ssid = dev.ssid
        return None if ssid in ("Disconnected", "Выключено", "Не подключено", None) else ssid

    def _gsaved(self):
        saved = []
        if not self.nc or not hasattr(self.nc, '_client') or not self.nc._client:
            return saved
        try:
            for conn in self.nc._client.get_connections():
                if conn.get_connection_type() == '802-11-wireless':
                    s = conn.get_setting_wireless()
                    if s and (sd := s.get_ssid()) and (ssid := NM.utils_ssid_to_utf8(sd.get_data())) and ssid not in saved:
                        saved.append(ssid)
        except:
            pass
        return saved

    def _on_scan(self, btn):
        if self._scan:
            return

        self._scan = True

        self.scan_lbl.get_style_context().add_class("scanning")
        btn.get_style_context().add_class("scanning")
        btn.set_tooltip_text("Сканирование...")
        btn.set_sensitive(False)

        if self.nc and hasattr(self.nc, 'wifi_device') and self.nc.wifi_device:
            dev = self.nc.wifi_device
            if getattr(dev, 'enabled', False):
                try:
                    dev.scan()
                except:
                    pass

        GLib.timeout_add(3000, self._rscan)

    def _rscan(self):
        self.scan_lbl.get_style_context().remove_class("scanning")
        self.scan_btn.get_style_context().remove_class("scanning")
        self.scan_btn.set_tooltip_text("Сканировать Wi-Fi сети")
        self.scan_btn.set_sensitive(True)
        self._scan = False
        return False

    def cleanup(self):
        if self._rid:
            GLib.source_remove(self._rid)
            self._rid = None

        for pool in self._slots.values():
            for slot in pool:
                slot.destroy()
            pool.clear()

        if WifiSlot._pw:
            WifiSlot._pw[1].destroy()
            WifiSlot._pw = None

        self.nc = None
        self.widgets = None