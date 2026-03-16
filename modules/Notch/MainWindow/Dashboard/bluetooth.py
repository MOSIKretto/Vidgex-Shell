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

def set_pointer_cursor(widget):
    widget.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
    def _ent(w, _):
        if win := w.get_window(): win.set_cursor(Gdk.Cursor.new_from_name(w.get_display(), "pointer"))
    def _lv(w, _):
        if win := w.get_window(): win.set_cursor(None)
    widget.connect("enter-notify-event", _ent)
    widget.connect("leave-notify-event", _lv)

def _run_bt_cmd(cmd_str, callback=None):
    def _on_exit(pid, status, *_):
        GLib.spawn_close_pid(pid)
        if callback: GLib.idle_add(callback)
    try:
        pid, _, _, _ = GLib.spawn_async(
            ["/bin/sh", "-c", cmd_str],
            flags=GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.DO_NOT_REAP_CHILD
        )
        GLib.child_watch_add(GLib.PRIORITY_DEFAULT, pid, _on_exit)
    except Exception:
        if callback: GLib.idle_add(callback)

def _get_dev_name(dev):
    """
    Умное получение имени устройства.
    Возвращает None, если имени нет, оно пустое или является просто MAC-адресом.
    """
    addr = getattr(dev, "address", "").upper()
    # Сначала пытаемся получить alias, затем обычное name
    name = getattr(dev, "alias", None) or getattr(dev, "name", None)
    
    if not name or str(name).strip() in ("", "Unknown", "unknown"):
        return None
        
    # Убираем двоеточия и тире для сравнения MAC-адреса с именем
    clean_name = str(name).replace(":", "").replace("-", "").strip().upper()
    clean_addr = addr.replace(":", "").replace("-", "").strip().upper()
    
    # Если имя устройства идентично его MAC-адресу — отсеиваем его
    if clean_name == clean_addr:
        return None
        
    return str(name)


class BTSlot(Gtk.EventBox):
    __slots__ = ('dev', 'client', 'parent_bt', 'list_type', 'main_box', 'name_lbl', 'status_lbl', 'icon', 'btn_settings')

    def __init__(self, client, parent_bt):
        super().__init__()
        self.client = client
        self.parent_bt = parent_bt
        self.dev = None
        self.list_type = "avail"

        self.connect("button-press-event", self._on_click)
        set_pointer_cursor(self)

        self.main_box = CenterBox()
        self.main_box.get_style_context().add_class("pixel-slot")

        self.icon = Image(size=24)
        self.name_lbl = Label(h_expand=True, h_align="start", ellipsization="end")
        self.status_lbl = Label(h_expand=True, h_align="start", name="dim-label")

        text_box = Box(orientation="v", children=(self.name_lbl, self.status_lbl))
        start_box = Box(spacing=12, v_align="center", children=(self.icon, text_box))

        self.btn_settings = Button(
            child=Label(markup=icons.settings),
            tooltip_text="Device Settings",
            on_clicked=self._on_settings
        )
        self.btn_settings.get_style_context().add_class("pixel-icon-button")
        self.btn_settings.get_style_context().add_class("settings-btn")
        self.btn_settings.set_valign(Gtk.Align.CENTER)
        set_pointer_cursor(self.btn_settings)

        self.main_box.add_start(start_box)
        self.main_box.add_end(self.btn_settings)
        
        self.add(self.main_box)

    def update(self, dev, list_type):
        self.dev = dev
        self.list_type = list_type
        
        icon_name = f"{getattr(dev, 'icon_name', 'bluetooth')}-symbolic"
        self.icon.set_from_icon_name(icon_name, 24)
        
        dev_name = _get_dev_name(dev) or getattr(dev, 'name', 'Unknown')
        self.name_lbl.set_label(dev_name)

        self._upd()

    def _upd(self):
        if not self.dev: return
        
        c = getattr(self.dev, "connected", False)
        known = getattr(self.dev, "paired", False) or getattr(self.dev, "trusted", False)

        self.btn_settings.set_visible(known or c)

        if c:
            self.status_lbl.set_label("Connected")
            self.main_box.get_style_context().add_class("active-slot")
            self.icon.get_style_context().add_class("active-icon")
            self.btn_settings.get_style_context().add_class("active-settings-btn")
        else:
            self.status_lbl.set_label("Saved" if known else "Available")
            self.main_box.get_style_context().remove_class("active-slot")
            self.icon.get_style_context().remove_class("active-icon")
            self.btn_settings.get_style_context().remove_class("active-settings-btn")

    def _on_click(self, widget, event):
        if not self.dev: return
        addr = self.dev.address
        is_conn = getattr(self.dev, "connected", False)
        known = getattr(self.dev, "paired", False) or getattr(self.dev, "trusted", False)

        if is_conn:
            self.status_lbl.set_label("Disconnecting...")
            _run_bt_cmd(f"bluetoothctl disconnect {addr}", self.parent_bt._req_ref)
        else:
            self.status_lbl.set_label("Connecting...")
            if known:
                _run_bt_cmd(f"bluetoothctl connect {addr}", self.parent_bt._req_ref)
            else:
                _run_bt_cmd(f"bluetoothctl pair {addr} ; bluetoothctl trust {addr} ; bluetoothctl connect {addr}", self.parent_bt._req_ref)

    def _on_settings(self, _):
        if self.parent_bt: self.parent_bt.open_settings(self)


class BluetoothConnections(Box):
    __slots__ = ('_w', '_btns', '_cl', 'stack', '_slots', '_rid',
                 'lists_stack', 'connected_box', 'avail_box', 'saved_box',
                 'avail_section', 'saved_section', 'main_scroll', 'saved_scroll',
                 'settings_scroll', 'header_title', 'saved_btn', '_sc_btn', '_sc_lbl',
                 'current_settings_dev', '_previous_page',
                 'btn_bt_forget', 'btn_bt_disconnect', 'lbl_bt_disconnect',
                 'lbl_bt_addr', 'lbl_bt_paired', 'lbl_bt_trusted')

    def __init__(self, **kwargs):
        self._w = kwargs.pop("widgets", None)
        super().__init__(
            name="bluetooth", spacing=4, orientation="vertical", 
            h_expand=True, v_expand=True, v_align="fill", **kwargs
        )

        self._btns = getattr(self._w.buttons, "bluetooth_button", None) if self._w else None
        self._slots = {"connected": [], "avail": [], "saved": []}
        self._rid = None
        self.current_settings_dev = None
        self._previous_page = "main"

        try: self._cl = BluetoothClient()
        except Exception: return

        self._build()

        self._cl.connect("notify::enabled", self._on_en)
        self._cl.connect("notify::scanning", self._on_sc)
        self._cl.connect("device-added", self._sched)
        self._cl.connect("device-removed", self._sched)
        
        self._on_en()
        self._sched()

    def _build(self):
        back_btn = Button(name="bluetooth-back", child=Label(markup=icons.chevron_left, name="bluetooth-back-label"))
        back_btn.connect("clicked", self._on_back_click)
        set_pointer_cursor(back_btn)

        self.header_title = Label(label="Bluetooth", v_align="center", name="header-title")
        
        self.saved_btn = Button(name="bluetooth-saved", child=Label(markup=icons.save, name="bluetooth-saved-label"), tooltip_text="Saved Devices", on_clicked=self._on_saved_toggle)
        set_pointer_cursor(self.saved_btn)

        self._sc_lbl = Label(markup=icons.radar, name="bluetooth-scan-label")
        self._sc_btn = Button(name="bluetooth-scan", child=self._sc_lbl, tooltip_text="Scan", on_clicked=self._on_scan_toggle)
        set_pointer_cursor(self._sc_btn)

        header_end_box = Box(spacing=4, orientation="horizontal", children=(self.saved_btn, self._sc_btn))

        header = CenterBox(start_children=(back_btn,), center_children=(self.header_title,), end_children=(header_end_box,))
        header.set_margin_bottom(8)
        self.add(header)

        self.stack = Stack(transition_type="crossfade", h_expand=True, v_expand=True, v_align="fill")
        self.add(self.stack)

        off_box = Box(orientation="v", v_align="center", h_align="center", spacing=12, v_expand=True)
        off_icon = Label(markup=f"<span size='32768'>{icons.bluetooth_off}</span>")
        off_icon.get_style_context().add_class("bluetooth-off-icon")
        off_box.add(off_icon)
        off_label = Label(label="Bluetooth is disabled")
        off_label.get_style_context().add_class("bluetooth-off-label")
        off_box.add(off_label)
        btn_turn_on = Button(label="Turn On", h_align="center", on_clicked=self._turn_on_bt)
        btn_turn_on.get_style_context().add_class("bluetooth-turn-on-btn")
        set_pointer_cursor(btn_turn_on)
        off_box.add(btn_turn_on)
        
        self.stack.add_named(off_box, "off")

        self.lists_stack = Stack(transition_type="slide-left-right", h_expand=True, v_expand=True, v_align="fill")

        self.connected_box = Box(spacing=2, orientation="vertical")
        self.avail_box = Box(spacing=2, orientation="vertical")
        self.avail_section = Box(orientation="v", spacing=4, children=(Label(label="Available Devices", h_align="start", name="section-title"), self.avail_box))

        self.main_scroll = ScrolledWindow(
            name="bluetooth-devices", min_content_size=(-1, -1),
            child=Box(spacing=4, orientation="vertical", children=[self.connected_box, self.avail_section]),
            h_expand=True, v_expand=True, propagate_width=False, propagate_height=False
        )
        self.main_scroll.set_overlay_scrolling(False)

        self.saved_box = Box(spacing=2, orientation="vertical")
        self.saved_section = Box(orientation="v", spacing=4, children=(Label(label="Saved Devices", h_align="start", name="section-title"), self.saved_box))

        self.saved_scroll = ScrolledWindow(
            name="bluetooth-devices", min_content_size=(-1, -1),
            child=Box(spacing=4, orientation="vertical", children=[self.saved_section]),
            h_expand=True, v_expand=True, propagate_width=False, propagate_height=False
        )
        self.saved_scroll.set_overlay_scrolling(False)

        self.settings_scroll = self._build_settings_page()

        self.lists_stack.add_named(self.main_scroll, "main")
        self.lists_stack.add_named(self.saved_scroll, "saved")
        self.lists_stack.add_named(self.settings_scroll, "settings")

        self.stack.add_named(self.lists_stack, "on")

    def _build_settings_page(self):
        settings_box = Box(orientation="vertical", spacing=8, h_expand=True)
        settings_box.set_margin_start(12)
        settings_box.set_margin_end(12)

        actions_box = Box(orientation="horizontal", spacing=8, h_align="center", h_expand=True)
        
        lbl_forget = Label(markup=f"<span size='large'>{icons.trash}</span> Удалить")
        self.btn_bt_forget = Button(child=lbl_forget, on_clicked=self._do_forget)
        self.btn_bt_forget.get_style_context().add_class("net-action-btn")
        self.btn_bt_forget.get_style_context().add_class("net-forget")
        set_pointer_cursor(self.btn_bt_forget) 

        self.lbl_bt_disconnect = Label(markup=f"<span size='large'>{icons.cancel}</span> Отключить")
        self.btn_bt_disconnect = Button(child=self.lbl_bt_disconnect, on_clicked=self._do_disconnect_or_connect)
        self.btn_bt_disconnect.get_style_context().add_class("net-action-btn")
        set_pointer_cursor(self.btn_bt_disconnect) 

        actions_box.add(self.btn_bt_forget)
        actions_box.add(self.btn_bt_disconnect)
        actions_box.set_margin_bottom(12)
        settings_box.add(actions_box)

        self.lbl_bt_addr = Label(label="-", h_align="end", selectable=True)
        self.lbl_bt_paired = Label(label="-", h_align="end")
        self.lbl_bt_trusted = Label(label="-", h_align="end")

        info_group = Box(orientation="vertical", spacing=2)
        info_group.get_style_context().add_class("net-info-group")

        def add_row(title, val_widget):
            row = Box(orientation="horizontal")
            row.get_style_context().add_class("net-info-row")
            row.pack_start(Label(label=title, h_align="start", name="dim-label"), True, True, 0)
            row.pack_end(val_widget, False, False, 0)
            info_group.add(row)

        add_row("MAC-адрес", self.lbl_bt_addr)
        add_row("Сопряжено", self.lbl_bt_paired)
        add_row("Доверенное", self.lbl_bt_trusted)

        settings_box.add(info_group)

        scroll = ScrolledWindow(
            name="bluetooth-devices", min_content_size=(-1, -1),
            child=settings_box, h_expand=True, v_expand=True,
            propagate_width=False, propagate_height=False
        )
        scroll.set_overlay_scrolling(False)
        return scroll

    def open_settings(self, slot):
        self._previous_page = self.lists_stack.get_visible_child_name()
        self.current_settings_dev = slot.dev
        
        self.header_title.set_label(slot.name_lbl.get_label())
        
        is_conn = getattr(slot.dev, "connected", False)
        if is_conn:
            self.lbl_bt_disconnect.set_markup(f"<span size='large'>{icons.cancel}</span> Отключить")
        else:
            self.lbl_bt_disconnect.set_markup(f"<span size='large'>{icons.accept}</span> Подключить")

        self.lbl_bt_addr.set_label(getattr(slot.dev, "address", "Unknown"))
        self.lbl_bt_paired.set_label("Да" if getattr(slot.dev, "paired", False) else "Нет")
        self.lbl_bt_trusted.set_label("Да" if getattr(slot.dev, "trusted", False) else "Нет")

        self._sc_btn.set_visible(False)
        self.saved_btn.set_visible(False)
        self.lists_stack.set_visible_child_name("settings")

    def _do_forget(self, _):
        if self.current_settings_dev:
            addr = self.current_settings_dev.address
            _run_bt_cmd(f"bluetoothctl disconnect {addr} ; bluetoothctl untrust {addr} ; bluetoothctl remove {addr}", self._req_ref)
            self._on_back_click(None)

    def _do_disconnect_or_connect(self, _):
        if not self.current_settings_dev: return
        addr = self.current_settings_dev.address
        is_conn = getattr(self.current_settings_dev, "connected", False)
        known = getattr(self.current_settings_dev, "paired", False) or getattr(self.current_settings_dev, "trusted", False)
        
        if is_conn:
            _run_bt_cmd(f"bluetoothctl disconnect {addr}", self._req_ref)
        else:
            if known:
                _run_bt_cmd(f"bluetoothctl connect {addr}", self._req_ref)
            else:
                _run_bt_cmd(f"bluetoothctl pair {addr} ; bluetoothctl trust {addr} ; bluetoothctl connect {addr}", self._req_ref)
            
        self._on_back_click(None)

    def _on_back_click(self, btn):
        curr = self.lists_stack.get_visible_child_name()
        if curr == "settings":
            self.lists_stack.set_visible_child_name(self._previous_page)
            self.header_title.set_label("Saved Devices" if self._previous_page == "saved" else "Bluetooth")
            self._sc_btn.set_visible(True)
            self.saved_btn.set_visible(True)
            if self._previous_page == "saved":
                self.saved_btn.add_style_class("pressed")
            else:
                self.saved_btn.remove_style_class("pressed")
        elif curr == "saved":
            self.lists_stack.set_visible_child_name("main")
            self.header_title.set_label("Bluetooth")
            self.saved_btn.remove_style_class("pressed")
        else:
            if self._w: self._w.show_notif()

    def _on_saved_toggle(self, btn):
        if self.lists_stack.get_visible_child_name() == "main":
            self.lists_stack.set_visible_child_name("saved")
            self.header_title.set_label("Saved Devices")
            btn.add_style_class("pressed")
        else:
            self.lists_stack.set_visible_child_name("main")
            self.header_title.set_label("Bluetooth")
            btn.remove_style_class("pressed")

    def _attach_dev_signals(self, dev):
        if not hasattr(dev, "_signals_attached"):
            dev._signals_attached = []
            # Добавили прослушивание 'alias' для моментальной реакции на смену имени
            for sig in ("notify::connected", "notify::paired", "notify::trusted", "notify::name", "notify::alias"):
                try: dev._signals_attached.append(dev.connect(sig, self._sched))
                except TypeError: pass

    def _sched(self, *_):
        if not self._rid: self._rid = GLib.timeout_add(300, self._ref)

    def _req_ref(self):
        self._sched()
        return False

    def _ref(self):
        self._rid = None
        en = self._get_pwr()
        
        self.stack.set_visible_child_name("on" if en else "off")
        if not en: return False

        devs = getattr(self._cl, "devices", {})
        dev_list = devs.values() if isinstance(devs, dict) else devs

        cd, ad, sd = [], [], []
        
        for dev in dev_list:
            real_name = _get_dev_name(dev)
            
            # Если имя отфильтровалось (его нет или это просто MAC) — полностью пропускаем устройство
            if not real_name:
                continue
                
            self._attach_dev_signals(dev)
            
            c = getattr(dev, "connected", False)
            known = getattr(dev, "paired", False) or getattr(dev, "trusted", False)

            if c: cd.append(dev)
            if known: sd.append(dev)
            if not c and not known: ad.append(dev)

        # Сортируем списки по алфавиту для красоты
        cd.sort(key=lambda d: (_get_dev_name(d) or "").lower())
        sd.sort(key=lambda d: (not getattr(d, 'connected', False), (_get_dev_name(d) or "").lower()))
        ad.sort(key=lambda d: (_get_dev_name(d) or "").lower())

        self._ubox(self.connected_box, self._slots["connected"], cd, "connected")
        self._ubox(self.avail_box, self._slots["avail"], ad, "avail")
        self._ubox(self.saved_box, self._slots["saved"], sd, "saved")

        self._update_visibility()
        return False

    def _ubox(self, box, pool, data, list_type):
        n, e = len(data), len(pool)
        while len(pool) < n:
            slot = BTSlot(self._cl, self)
            pool.append(slot)
            box.add(slot)
        for i in range(n, e):
            pool[i].hide()
        for i, dev in enumerate(data):
            pool[i].update(dev, list_type)
            pool[i].show_all()
            pool[i]._upd() 

    def _update_visibility(self):
        def count_vis(box): return sum(1 for child in box.get_children() if child.get_visible())
        
        self.connected_box.set_visible(count_vis(self.connected_box) > 0)
        # Заголовок Available Devices теперь виден всегда, даже если устройств (в box) 0
        self.avail_section.set_visible(True)

    def _get_pwr(self):
        if self._btns and hasattr(self._btns, '_get_pwr'): return self._btns._get_pwr()
        try:
            bd = "/sys/class/rfkill/"
            for d in os.listdir(bd):
                if os.path.exists(t_p := os.path.join(bd, d, "type")):
                    with open(t_p, "r") as f:
                        if f.read().strip() == "bluetooth":
                            with open(os.path.join(bd, d, "state"), "r") as sf: return sf.read().strip() == "1"
        except Exception: pass
        return False

    def _turn_on_bt(self, *_):
        if self._btns and hasattr(self._btns, '_on_toggle_click'):
            if not self._btns._en: self._btns._on_toggle_click()
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
            self._sched()
            if not getattr(self._cl, "scanning", False):
                GLib.timeout_add(800, lambda: getattr(self._cl, "toggle_scan", lambda: None)() or False)

        if self._btns and hasattr(self._btns, 'update_state'): GLib.idle_add(self._btns.update_state)
        return False

    def _on_sc(self, *_):
        sc = getattr(self._cl, "scanning", False)
        m = "add_style_class" if sc else "remove_style_class"
        getattr(self._sc_lbl, m)("scanning")
        getattr(self._sc_btn, m)("scanning")

    def cleanup(self):
        if self._rid: 
            GLib.source_remove(self._rid)
            self._rid = None
        for pool in self._slots.values():
            for slot in pool: slot.destroy()
            pool.clear()
        self._cl = self._w = self._btns = None