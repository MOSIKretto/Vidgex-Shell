import os
from gi.repository import GLib, Gtk, Gdk
from fabric.bluetooth import BluetoothClient, BluetoothDevice
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow
from fabric.widgets.stack import Stack

import services.icons as icons

def _run_bt_cmd(cmd_str, callback=None):
    def _on_exit(pid, status, *_):
        GLib.spawn_close_pid(pid)
        if callback:
            GLib.idle_add(callback)
    try:
        pid, _, _, _ = GLib.spawn_async(
            ["/bin/sh", "-c", cmd_str],
            flags=GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.DO_NOT_REAP_CHILD
        )
        GLib.child_watch_add(GLib.PRIORITY_DEFAULT, pid, _on_exit)
    except Exception:
        if callback: GLib.idle_add(callback)

def _is_known(dev):
    return getattr(dev, "paired", False) or getattr(dev, "trusted", False)


class DeviceSlot(Gtk.EventBox):
    __slots__ = ('dev', 'client', '_cb', '_locked', '_destroyed', '_sig_ids', 
                 'main_box', 'name_lbl', 'status_lbl', 'icon', 'btn_forget')

    def __init__(self, device: BluetoothDevice, client: BluetoothClient, on_pos_change=None):
        super().__init__()
        self.dev = device
        self.client = client
        self._cb = on_pos_change
        
        self._locked = False
        self._destroyed = False

        self.connect("button-press-event", self._on_click)
        self._setup_hover()

        self.main_box = CenterBox()
        self.main_box.get_style_context().add_class("pixel-slot")

        icon_name = f"{getattr(device, 'icon_name', 'bluetooth')}-symbolic"
        dev_name = getattr(device, 'name', None) or getattr(device, 'address', 'Unknown')

        self.icon = Image(icon_name=icon_name, size=16)
        self.name_lbl = Label(label=dev_name, h_expand=True, h_align="start", ellipsization="end")
        self.status_lbl = Label(label="", h_expand=True, h_align="start", name="dim-label")

        text_box = Box(orientation="v", children=(self.name_lbl, self.status_lbl))
        start_box = Box(spacing=12, v_align="center", children=(self.icon, text_box))

        self.btn_forget = Button(
            child=Label(markup=icons.trash),
            tooltip_text="Forget",
            on_clicked=self._on_forget
        )
        self.btn_forget.get_style_context().add_class("pixel-icon-button")
        self.btn_forget.set_valign(Gtk.Align.CENTER)

        self.main_box.add_start(start_box)
        self.main_box.add_end(self.btn_forget)
        
        self.add(self.main_box)

        self._sig_ids = []
        for sig in ("notify::connected", "notify::paired", "notify::trusted", "notify::closed"):
            try:
                if sig == "notify::closed":
                    self._sig_ids.append(self.dev.connect(sig, self._on_closed))
                else:
                    self._sig_ids.append(self.dev.connect(sig, self._on_state_change))
            except TypeError: pass

        self._upd()

    def _setup_hover(self):
        self.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        def _ent(w, _):
            if win := w.get_window(): win.set_cursor(Gdk.Cursor.new_from_name(w.get_display(), "pointer"))
        def _lv(w, _):
            if win := w.get_window(): win.set_cursor(None)
        self.connect("enter-notify-event", _ent)
        self.connect("leave-notify-event", _lv)

    def _on_closed(self, dev, *_):
        if getattr(dev, "closed", False):
            GLib.idle_add(self._cleanup)

    def _on_state_change(self, *_):
        if not self._locked and not self._destroyed:
            GLib.idle_add(self._deferred_upd)

    def _deferred_upd(self):
        if not self._destroyed:
            self._upd()
            if self._cb: self._cb(self)
        return False

    def is_known(self):
        return _is_known(self.dev)

    def _upd(self):
        if self._locked or self._destroyed: return

        c = getattr(self.dev, "connected", False)
        known = self.is_known()

        self.btn_forget.set_visible(known)

        if c:
            self.status_lbl.set_label("Connected")
            self.main_box.get_style_context().add_class("active-slot")
            self.icon.get_style_context().add_class("active-icon")
        else:
            self.status_lbl.set_label("Saved" if known else "Available")
            self.main_box.get_style_context().remove_class("active-slot")
            self.icon.get_style_context().remove_class("active-icon")

    def _on_click(self, widget, event):
        if self._locked or self._destroyed: return

        self._locked = True
        addr = self.dev.address
        is_conn = getattr(self.dev, "connected", False)

        if is_conn:
            self.status_lbl.set_label("Disconnecting...")
            _run_bt_cmd(f"bluetoothctl disconnect {addr}", self._unlock)
        else:
            self.status_lbl.set_label("Connecting...")
            _run_bt_cmd(f"bluetoothctl connect {addr}", self._unlock)

    def _on_forget(self, btn):
        if self._locked or self._destroyed: return
        self._locked = True
        self.status_lbl.set_label("Forgetting...")
        addr = self.dev.address
        _run_bt_cmd(f"bluetoothctl disconnect {addr} ; bluetoothctl untrust {addr} ; bluetoothctl remove {addr}", self._unlock)

    def _unlock(self):
        if self._destroyed: return
        self._locked = False
        self._upd()
        if self._cb: self._cb(self)
        GLib.timeout_add(2000, self._force_sync)

    def _force_sync(self):
        if not self._locked and not self._destroyed: self._upd()
        return False

    def _cleanup(self):
        if self._destroyed: return
        self._destroyed = True
        for sid in self._sig_ids:
            if self.dev.handler_is_connected(sid): self.dev.disconnect(sid)
        self._sig_ids.clear()
        self.dev = self.client = self._cb = None
        self.destroy()


class BluetoothConnections(Box):
    __slots__ = ('_w', '_btns', '_cl', '_known_addrs', 'stack', '_load_attempts',
                 '_saved_box', '_access_box', 'saved_section', 'avail_section', 
                 '_sc_lbl', '_sc_btn')

    def __init__(self, **kwargs):
        self._w = kwargs.pop("widgets", None)
        super().__init__(
            name="bluetooth", spacing=0, orientation="vertical", 
            h_expand=True, v_expand=True, v_align="fill", **kwargs
        )

        self._btns = getattr(self._w.buttons, "bluetooth_button", None) if self._w else None
        
        self._known_addrs = set()
        self._load_attempts = 0

        try: self._cl = BluetoothClient(on_device_added=self._add_dev_callback)
        except Exception: return

        back_btn = Button(
            name="bluetooth-back",
            child=Label(markup=icons.chevron_left, name="bluetooth-back-label"),
            on_clicked=lambda *_: self._w.show_notif() if self._w else None
        )
        
        self._sc_lbl = Label(markup=icons.radar, name="bluetooth-scan-label")
        self._sc_btn = Button(
            name="bluetooth-scan",
            child=self._sc_lbl,
            on_clicked=self._on_scan_toggle
        )

        header = CenterBox(
            start_children=(back_btn,),
            center_children=(Label(name="header-title", label="Bluetooth"),),
            end_children=(self._sc_btn,),
        )
        header.set_margin_bottom(8)
        self.add(header)

        self.stack = Stack(
            transition_type="crossfade",
            h_expand=True, v_expand=True, v_align="fill"
        )
        self.add(self.stack)

        off_box = Box(
            orientation="v", v_align="center", h_align="center", 
            spacing=12, v_expand=True
        )
        off_box.add(Label(markup=f"<span size='xx-large'>{icons.bluetooth_off}</span>", name="dim-label"))
        off_box.add(Label(label="Bluetooth is disabled", name="dim-label"))
        
        btn_turn_on = Button(label="Turn On", on_clicked=self._turn_on_bt)
        btn_turn_on.get_style_context().add_class("pixel-primary-btn")
        off_box.add(btn_turn_on)
        
        self.stack.add_named(off_box, "off")

        self._saved_box = Box(spacing=2, orientation="vertical")
        self._access_box = Box(spacing=2, orientation="vertical")

        self.saved_section = Box(orientation="v", spacing=4, children=(Label(label="Saved Devices", h_align="start", name="section-title"), self._saved_box))
        self.avail_section = Box(orientation="v", spacing=4, children=(Label(label="Available Devices", h_align="start", name="section-title"), self._access_box))

        scroll = ScrolledWindow(
            name="bluetooth-devices",
            min_content_size=(-1, -1),
            child=Box(
                spacing=4,
                orientation="vertical",
                children=[self.saved_section, self.avail_section],
            ),
            v_expand=True,
            propagate_width=False,
            propagate_height=False,
        )

        self.stack.add_named(scroll, "on")

        self._cl.connect("notify::enabled", self._on_en)
        self._cl.connect("notify::scanning", self._on_sc)
        
        self._on_en()
        GLib.timeout_add(500, self._load_initial)

    def _get_pwr(self):
        if self._btns and hasattr(self._btns, '_get_pwr'):
            return self._btns._get_pwr()
        try:
            base_dir = "/sys/class/rfkill/"
            for d in os.listdir(base_dir):
                if os.path.exists(t_path := os.path.join(base_dir, d, "type")):
                    with open(t_path, "r") as f:
                        if f.read().strip() == "bluetooth":
                            with open(os.path.join(base_dir, d, "state"), "r") as sf:
                                return sf.read().strip() == "1"
        except Exception: pass
        return False

    def _turn_on_bt(self, *_):
        if self._btns and hasattr(self._btns, '_on_toggle_click'):
            if not self._btns._en:  
                self._btns._on_toggle_click()
        else:
            try: GLib.spawn_command_line_async("rfkill unblock bluetooth")
            except Exception: pass
        
        GLib.timeout_add(350, self._on_en)

    def _on_scan_toggle(self, *_):
        if not self._get_pwr():
            self._turn_on_bt()
            GLib.timeout_add(800, lambda: getattr(self._cl, "toggle_scan", lambda: None)() or False)
        else:
            getattr(self._cl, "toggle_scan", lambda: None)()

    def _on_en(self, *_):
        en = self._get_pwr()
        
        self.stack.set_visible_child_name("on" if en else "off")
        
        if en:
            GLib.timeout_add(500, self._load_initial)
            if not getattr(self._cl, "scanning", False):
                GLib.timeout_add(800, lambda: getattr(self._cl, "toggle_scan", lambda: None)() or False)

        if self._btns and hasattr(self._btns, 'update_state'):
            GLib.idle_add(self._btns.update_state)
            
        return False

    def _load_initial(self):
        devs = getattr(self._cl, "devices", None)
        
        if not devs and self._load_attempts < 5:
            self._load_attempts += 1
            GLib.timeout_add(500, self._load_initial)
            return False
            
        dev_list = devs.values() if isinstance(devs, dict) else devs
        if dev_list:
            for dev in sorted(dev_list, key=lambda d: (not getattr(d, "connected", False), not _is_known(d))):
                self._add_dev(dev)
        return False

    def _add_dev_callback(self, client, address):
        if client and (dev := client.get_device(address)):
            GLib.idle_add(self._add_dev, dev)

    def _add_dev(self, dev):
        addr = getattr(dev, "address", None)
        if not addr or addr in self._known_addrs: return False
            
        self._known_addrs.add(addr)
        slot = DeviceSlot(dev, self._cl, on_pos_change=self._repos)
        slot.connect("destroy", lambda s: self._known_addrs.discard(addr))

        target = self._saved_box if _is_known(dev) else self._access_box
        
        rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, transition_duration=300)
        rev.add(slot)
        target.add(rev)
        rev.show_all()
        rev.set_reveal_child(True)
        
        if getattr(dev, "connected", False): target.reorder_child(rev, 0)
        self._update_visibility()
        return False

    def _repos(self, slot):
        if slot._destroyed: return
        rev = slot.get_parent()
        if not rev: return

        target = self._saved_box if slot.is_known() else self._access_box
        parent = rev.get_parent()

        if parent and parent != target:
            parent.remove(rev)
            target.add(rev)
            
        if getattr(slot.dev, "connected", False):
            target.reorder_child(rev, 0)
        
        self._update_visibility()

    def _update_visibility(self):
        def count_vis(box):
            return sum(1 for rev in box.get_children() if rev.get_child() and not getattr(rev.get_child(), '_destroyed', True))
        
        self.saved_section.set_visible(count_vis(self._saved_box) > 0)
        self.avail_section.set_visible(count_vis(self._access_box) > 0)

    def _on_sc(self, *_):
        sc = getattr(self._cl, "scanning", False)
        m = "add_style_class" if sc else "remove_style_class"
        getattr(self._sc_lbl, m)("scanning")
        getattr(self._sc_btn, m)("scanning")

    def cleanup(self):
        self._known_addrs.clear()
        for box in (self._saved_box, self._access_box):
            for rev in box.get_children():
                if slot := rev.get_child():
                    try: slot._cleanup()
                    except AttributeError: slot.destroy()
        self._cl = self._w = self._btns = None