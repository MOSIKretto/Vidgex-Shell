from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow

from gi.repository import Gtk, NM, GLib

import services.icons as icons
from services.network import NetworkClient

class WifiSlot(Gtk.Box):
    __slots__ = ('nc', 'parent_list', 'rcb', 'ssid', 'saved', 'conn',
                 'icon', 'name_lbl', 'str_lbl', 'act_box',
                 'lbl_conn', 'btn_conn', 'btn_del', 'btn_unavail',
                 'pw_rev', 'pw_entry', 'btn_pw_ok')
    
    _active_pw_slot = None

    def __init__(self, nc, parent_list, rcb):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, name="wifi-network-slot")
        
        self.nc = nc
        self.parent_list = parent_list
        self.rcb = rcb
        self.ssid = None
        self.saved = self.conn = False

        self.icon = Image(size=16)
        self.name_lbl = Label(h_expand=True, h_align="start", ellipsization="end")
        self.str_lbl = Label()

        start_box = Box(spacing=8, h_expand=True, h_align="fill", children=(self.icon, self.name_lbl, self.str_lbl))
        self.act_box = Box(orientation="horizontal", spacing=4)

        self.lbl_conn = Label(label="Connected", name="wifi-connected-label")
        
        self.btn_conn = Button(name="wifi-connect", label="Connect")
        self.btn_conn.connect("clicked", self._on_conn)
        
        self.btn_del = Button(name="wifi-delete", child=Label(name="wifi-delete-label", markup=icons.trash), tooltip_text="Delete network")
        self.btn_del.connect("clicked", self._on_del)
        
        self.btn_unavail = Button(name="wifi-unavailable", label="Unavailable", sensitive=False, can_focus=False)
        self.btn_unavail.get_style_context().add_class("unavailable-network")

        self.act_box.pack_start(self.lbl_conn, False, False, 0)
        self.act_box.pack_start(self.btn_conn, False, False, 0)
        self.act_box.pack_start(self.btn_unavail, False, False, 0)
        self.act_box.pack_start(self.btn_del, False, False, 0)

        main_row = CenterBox(start_children=start_box, end_children=self.act_box)
        self.pack_start(main_row, True, True, 0)

        self.pw_rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        pw_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, name="password-entry-container")
        pw_box.set_margin_top(4); pw_box.set_margin_bottom(12)
        pw_box.set_margin_start(10); pw_box.set_margin_end(10)

        self.pw_entry = Gtk.Entry(visibility=False, invisible_char='•', placeholder_text="Password...")
        
        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_pw_cancel = Gtk.Button(label="Cancel")
        self.btn_pw_ok = Gtk.Button(label="OK", sensitive=False)

        btn_pw_cancel.connect("clicked", lambda _: self._close_pw())
        self.btn_pw_ok.connect("clicked", lambda _: self._submit(self.pw_entry.get_text()))
        self.pw_entry.connect("changed", lambda e: self.btn_pw_ok.set_sensitive(bool(e.get_text())))
        self.pw_entry.connect("activate", lambda e: self.btn_pw_ok.get_sensitive() and self.btn_pw_ok.clicked())

        btns.pack_start(btn_pw_cancel, True, True, 0)
        btns.pack_start(self.btn_pw_ok, True, True, 0)
        pw_box.pack_start(self.pw_entry, False, False, 0)
        pw_box.pack_start(btns, False, False, 0)
        
        self.pw_rev.add(pw_box)
        self.pack_start(self.pw_rev, False, False, 0)
        
        self.show_all()
        self.pw_rev.set_reveal_child(False)

    def update(self, data, saved=False, conn=False):
        self.ssid = data.get("ssid", "Unknown")
        self.saved, self.conn = saved, conn

        self.icon.set_from_icon_name(data.get("icon-name", "network-wireless-signal-none-symbolic"), 24)
        self.name_lbl.set_label(self.ssid)
        self.str_lbl.set_label(f"{data.get('strength', 0)}%")

        avail = hasattr(self.nc, 'is_network_available') and self.ssid and self.nc.is_network_available(self.ssid)

        self.lbl_conn.set_visible(conn)
        self.btn_del.set_visible(conn or saved)
        
        show_conn = not conn and avail
        self.btn_conn.set_visible(show_conn)
        self.btn_unavail.set_visible(not conn and not avail)
        
        if show_conn:
            self.btn_conn.set_label("Connect")
            self.btn_conn.set_sensitive(True)

        return self

    def _on_conn(self, btn):
        if self.saved:
            btn.set_sensitive(False)
            btn.set_label("Connecting...")
            self.nc.connect_to_saved_network(self.ssid, self._ok, self._err)
        else:
            self._tog_pw()

    def _tog_pw(self):
        if WifiSlot._active_pw_slot and WifiSlot._active_pw_slot is not self:
            WifiSlot._active_pw_slot._close_pw()

        if self.pw_rev.get_reveal_child():
            self._close_pw()
        else:
            WifiSlot._active_pw_slot = self
            self.pw_rev.set_reveal_child(True)
            self.pw_entry.set_text("")
            GLib.idle_add(self.pw_entry.grab_focus)

    def _close_pw(self):
        self.pw_rev.set_reveal_child(False)
        if WifiSlot._active_pw_slot is self:
            WifiSlot._active_pw_slot = None

    def _submit(self, pwd):
        self._close_pw()
        self.btn_conn.set_sensitive(False)
        self.btn_conn.set_label("Connecting...")
        self.nc.connect_to_new_network(self.ssid, pwd, self._ok, self._err)

    def _ok(self, ssid):
        if self.rcb: GLib.timeout_add(500, self.rcb)

    def _err(self, *_):
        self.btn_conn.set_label("Error")
        self.btn_conn.set_sensitive(False)
        GLib.timeout_add(3000, self._restore)

    def _restore(self):
        self.btn_conn.set_label("Connect")
        self.btn_conn.set_sensitive(True)
        return False

    def _on_del(self, _):
        if self.ssid:
            self.nc.delete_saved_network(self.ssid)
            if self.rcb: GLib.timeout_add(300, self.rcb)


class NetworkConnections(Box):
    __slots__ = ('widgets', '_rid', '_scan', '_slots', '_lio', 'nc',
                 'dl_lbl', 'ul_lbl', 'scan_lbl', 'scan_btn', 'saved_box', 'avail_box', 'scroll')

    _U = ((1048576.0, "MB/s"), (1024.0, "KB/s"), (1.0, "B/s"))

    def __init__(self, **kwargs):
        self.widgets = kwargs.pop("widgets", None)
        super().__init__(name="network-connections", spacing=4, orientation="vertical", **kwargs)

        self._rid = None
        self._scan = False
        self._slots = {"saved": [], "avail": []}
        self._lio = self._rio()

        try:
            self.nc = NetworkClient()
        except Exception:
            self.nc = None

        self._build()
        GLib.timeout_add(1000, self._uspd)

        if self.nc:
            try:
                self.nc.connect("device-ready", self._rdy)
                self.nc.connect("connection-error", self._cerr)
            except Exception: pass

            if getattr(self.nc, 'wifi_device', None):
                GLib.idle_add(self._rdy)

    def _rio(self):
        recv = sent = 0
        try:
            with open("/proc/net/dev", "rb") as f:
                next(f); next(f)
                for line in f:
                    parts = line.split()
                    if not parts or parts[0].startswith(b'lo:'): continue
                    if len(parts) >= 10:
                        recv += int(parts[1])
                        sent += int(parts[9])
        except Exception: pass
        return recv, sent

    def _build(self):
        self.dl_lbl = Label(name="download-label", markup="0 B/s")
        self.ul_lbl = Label(name="upload-label", markup="0 B/s")

        self.scan_lbl = Label(name="network-scan-label", markup=icons.radar)
        self.scan_btn = Button(name="network-scan", child=self.scan_lbl, tooltip_text="Scan Wi-Fi Networks")
        self.scan_btn.connect("clicked", self._on_scan)

        back = Button(name="network-back", child=Label(name="network-back-label", markup=icons.chevron_left))
        back.connect("clicked", lambda *_: self.widgets.show_notif())

        self.saved_box = Box(name="saved-box", spacing=2, orientation="vertical")
        self.avail_box = Box(name="available-box", spacing=2, orientation="vertical")

        content = Box(
            spacing=4, orientation="vertical", 
            children=(
                Label(name="network-section", label="Saved Networks"),
                self.saved_box,
                Label(name="network-section", label="Available Networks"),
                self.avail_box
            )
        )

        center = Box(
            orientation="horizontal", spacing=12, h_align="center",
            children=(
                Box(orientation="horizontal", spacing=4, v_align="center", children=(self.ul_lbl, Label(name="upload-icon-label", markup=icons.upload))),
                Label(name="network-text", label="Wi-Fi", v_align="center"),
                Box(orientation="horizontal", spacing=4, v_align="center", children=(Label(name="download-icon-label", markup=icons.download), self.dl_lbl)),
            )
        )

        self.scroll = ScrolledWindow(
            name="bluetooth-devices", min_content_size=(-1, -1),
            child=content, v_expand=True, propagate_width=False, propagate_height=False
        )

        self.add(CenterBox(name="network-header", start_children=(back,), center_children=(center,), end_children=(self.scan_btn,)))
        self.add(self.scroll)

    def _fmt(self, s):
        for th, u in self._U:
            if s >= th:
                return f"{s / th:.1f} {u}" if th > 1.0 else f"{int(s)} {u}"
        return "0 B/s"

    def _uspd(self):
        c = self._rio()
        self.dl_lbl.set_markup(self._fmt(max(0, c[0] - self._lio[0])))
        self.ul_lbl.set_markup(self._fmt(max(0, c[1] - self._lio[1])))
        self._lio = c
        return True

    def _rdy(self, client=None):
        if dev := getattr(self.nc, 'wifi_device', None):
            try: dev.connect("changed", self._sched)
            except Exception: pass
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
        if getattr(WifiSlot, "_active_pw_slot", None):
            return False

        nc = self.nc
        dev = getattr(nc, 'wifi_device', None)
        en = bool(dev and getattr(dev, 'enabled', False))

        cur = self._gcur()
        saved = self._gsaved()
        avail = dev.access_points if en and dev else []
        avail_d = {ap.get("ssid"): ap for ap in avail if ap.get("ssid")}

        saved_s = set(saved)
        tl = self.get_toplevel()
        rcb = lambda: (self._ref(), False)[1]

        dap = {"strength": 0, "is_secured": True, "icon-name": "network-wireless-signal-none-symbolic"}
        sd, ad = [], []

        if cur and cur in saved:
            sd.append((avail_d.get(cur, {"ssid": cur, **dap, "icon-name": "network-wireless-signal-excellent-symbolic"}), True, True))

        for ssid in saved:
            if ssid != cur:
                sd.append((avail_d.get(ssid, {"ssid": ssid, **dap}), True, False))

        for ap in avail:
            if (s := ap.get("ssid")) and s not in saved_s and s != cur:
                ad.append((ap, False, False))

        self._ubox(self.saved_box, self._slots["saved"], sd, tl, rcb)
        self._ubox(self.avail_box, self._slots["avail"], ad, tl, rcb)

        st = getattr(dev, 'strength', 0) if dev else 0
        txt = "Off" if not en else (cur or "Disconnected")

        if hasattr(self.widgets, 'update_network_display'):
            self.widgets.update_network_display(txt, st, en)

        return False

    def _ubox(self, box, pool, data, tl, rcb):
        n, e = len(data), len(pool)

        while len(pool) < n:
            slot = WifiSlot(self.nc, tl, rcb)
            pool.append(slot)
            box.add(slot)

        for i in range(n, e):
            pool[i].hide()

        for i, (ap, saved, conn) in enumerate(data):
            pool[i].update(ap, saved, conn)
            pool[i].show()

    def _gcur(self):
        if dev := getattr(self.nc, 'wifi_device', None):
            ssid = getattr(dev, 'ssid', None)
            return None if ssid in ("Disconnected", "Off", "Не подключено", "Выключено", None) else ssid
        return None

    def _gsaved(self):
        saved = []
        if client := getattr(self.nc, '_client', None):
            try:
                for conn in client.get_connections():
                    if conn.get_connection_type() == '802-11-wireless':
                        if (s := conn.get_setting_wireless()) and (sd := s.get_ssid()) and (ssid := NM.utils_ssid_to_utf8(sd.get_data())) not in saved:
                            saved.append(ssid)
            except Exception: pass
        return saved

    def _on_scan(self, btn):
        if self._scan: return
        self._scan = True

        self.scan_lbl.get_style_context().add_class("scanning")
        btn.get_style_context().add_class("scanning")
        btn.set_tooltip_text("Scanning...")
        btn.set_sensitive(False)

        if dev := getattr(self.nc, 'wifi_device', None):
            if getattr(dev, 'enabled', False):
                try: dev.scan()
                except Exception: pass

        GLib.timeout_add(3000, self._rscan)

    def _rscan(self):
        self.scan_lbl.get_style_context().remove_class("scanning")
        self.scan_btn.get_style_context().remove_class("scanning")
        self.scan_btn.set_tooltip_text("Scan Wi-Fi Networks")
        self.scan_btn.set_sensitive(True)
        self._scan = False
        return False

    def cleanup(self):
        if self._rid:
            GLib.source_remove(self._rid)
            self._rid = None

        WifiSlot._active_pw_slot = None

        for pool in self._slots.values():
            for slot in pool: slot.destroy()
            pool.clear()

        self.nc = self.widgets = None