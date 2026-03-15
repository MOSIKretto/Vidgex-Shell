import os
import subprocess
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow
from fabric.widgets.stack import Stack

from gi.repository import Gtk, NM, GLib, Gdk

import services.icons as icons
from modules.Notch.MainWindow.Dashboard.Network.network import NetworkClient


# Глобальная функция для установки курсора-пальца при наведении на любой виджет
def set_pointer_cursor(widget):
    widget.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
    def _ent(w, _):
        if win := w.get_window(): 
            win.set_cursor(Gdk.Cursor.new_from_name(w.get_display(), "pointer"))
    def _lv(w, _):
        if win := w.get_window(): 
            win.set_cursor(None)
    widget.connect("enter-notify-event", _ent)
    widget.connect("leave-notify-event", _lv)


class WifiSlot(Gtk.Box):
    __slots__ = (
        'nc', 'parent_net', 'ssid', 'saved', 'conn',
        'click_area', 'main_box', 'icon', 'name_lbl', 'status_lbl', 
        'end_box', 'btn_settings', 'lock_icon',
        'pw_rev', 'pw_pill', 'btn_pw_reveal', 'pw_entry', 'btn_pw_ok', 
        '_anim_id', '_target_height', '_cached_vadj', '_cached_scroll_h'
    )
    
    _active_pw_slot = None

    def __init__(self, nc, parent_net):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        
        self.nc = nc
        self.parent_net = parent_net
        
        self.ssid = None
        self.saved = False
        self.conn = False
        self._anim_id = None
        self._target_height = 0
        self._cached_vadj = None
        self._cached_scroll_h = 0

        self.click_area = Gtk.EventBox()
        self.click_area.connect("button-press-event", self._on_click)
        set_pointer_cursor(self.click_area) 

        self.main_box = CenterBox()
        self.main_box.get_style_context().add_class("pixel-slot")

        self.icon = Image(size=16)
        self.name_lbl = Label(h_expand=True, h_align="start", ellipsization="end")
        self.status_lbl = Label(label="", h_expand=True, h_align="start", name="dim-label")

        text_box = Box(orientation="v", children=(self.name_lbl, self.status_lbl))
        start_box = Box(spacing=12, v_align="center", children=(self.icon, text_box))

        self.end_box = Box(orientation="horizontal", v_align="center")

        self.btn_settings = Button(
            child=Label(markup=icons.settings),
            tooltip_text="Network Settings",
            on_clicked=self._on_settings
        )
        self.btn_settings.get_style_context().add_class("pixel-icon-button")
        self.btn_settings.get_style_context().add_class("settings-btn")
        set_pointer_cursor(self.btn_settings) 

        self.lock_icon = Label(markup=icons.lock)
        self.lock_icon.get_style_context().add_class("lock-icon")
        self.lock_icon.set_margin_end(8)

        self.end_box.add(self.btn_settings)
        self.end_box.add(self.lock_icon)

        self.main_box.add_start(start_box)
        self.main_box.add_end(self.end_box)
        self.click_area.add(self.main_box)
        self.pack_start(self.click_area, False, False, 0)

        self.pw_rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.pw_rev.set_transition_duration(300)
        
        pw_wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, name="pw-wrapper")
        self.pw_pill = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0, name="pw-pill")
        
        self.btn_pw_reveal = Button(
            child=Label(markup=icons.eye_closed),
            on_clicked=self._on_reveal_clicked
        )
        self.btn_pw_reveal.get_style_context().add_class("pw-reveal-btn")
        set_pointer_cursor(self.btn_pw_reveal) 
        
        self.pw_entry = Gtk.Entry(visibility=False, invisible_char='•', placeholder_text="Password...")
        self.pw_entry.set_hexpand(True)
        self.pw_entry.get_style_context().add_class("pw-entry-naked")
        
        self.btn_pw_ok = Button(
            child=Label(markup=icons.accept), 
            on_clicked=self._on_pw_submit
        )
        self.btn_pw_ok.set_sensitive(False)
        self.btn_pw_ok.get_style_context().add_class("pw-submit-btn")
        set_pointer_cursor(self.btn_pw_ok) 

        self.pw_entry.connect("changed", self._on_pw_change)
        self.pw_entry.connect("activate", self._on_pw_activate)

        self.pw_pill.pack_start(self.btn_pw_reveal, False, False, 2)
        self.pw_pill.pack_start(self.pw_entry, True, True, 4)
        self.pw_pill.pack_end(self.btn_pw_ok, False, False, 2)
        
        pw_wrapper.pack_start(self.pw_pill, True, True, 0)
        self.pw_rev.add(pw_wrapper)
        
        self.pack_start(self.pw_rev, False, False, 0)
        self.show_all()
        self.pw_rev.set_reveal_child(False)

    def _on_reveal_clicked(self, btn):
        is_visible = self.pw_entry.get_visibility()
        new_visibility = not is_visible
        self.pw_entry.set_visibility(new_visibility)
        icon_markup = icons.eye_check if new_visibility else icons.eye_closed
        btn.get_child().set_markup(icon_markup)

    def _on_pw_change(self, e): 
        self.btn_pw_ok.set_sensitive(len(e.get_text()) > 0)

    def _on_pw_submit(self, _):
        pwd = self.pw_entry.get_text()
        if not pwd: return
        self._submit(pwd)

    def _on_pw_activate(self, _):
        pwd = self.pw_entry.get_text()
        if pwd: self._submit(pwd)

    def update(self, data, saved=False, conn=False):
        self.ssid = data.get("ssid", "Unknown")
        self.saved, self.conn = saved, conn
        is_secured = data.get("is_secured", True)

        self.icon.set_from_icon_name(data.get("icon-name", "network-wireless-signal-none-symbolic"), 24)
        self.name_lbl.set_label(self.ssid)

        try: avail = self.nc.is_network_available(self.ssid) if self.ssid and self.nc else False
        except AttributeError: avail = False

        if conn or saved:
            self.btn_settings.set_visible(True)
            self.lock_icon.set_visible(False)
        else:
            self.btn_settings.set_visible(False)
            self.lock_icon.set_visible(is_secured)

        if conn:
            self.status_lbl.set_label("Connected")
            self.main_box.get_style_context().add_class("active-slot")
            self.icon.get_style_context().add_class("active-icon")
            self.btn_settings.get_style_context().add_class("active-settings-btn")
        else:
            self.main_box.get_style_context().remove_class("active-slot")
            self.icon.get_style_context().remove_class("active-icon")
            self.btn_settings.get_style_context().remove_class("active-settings-btn")
            
            if not avail: self.status_lbl.set_label("Out of range")
            elif saved: self.status_lbl.set_label("Saved")
            else: self.status_lbl.set_label("Secured" if is_secured else "Open")

        return self

    def _on_click(self, widget, event):
        if self.conn: return 
        if self.saved and self.nc:
            self.status_lbl.set_label("Connecting...")
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
            base_h = self.get_allocated_height()
            _, child_h = self.pw_rev.get_child().get_preferred_height()
            self._target_height = base_h + child_h

            self.pw_rev.set_reveal_child(True)
            self.pw_entry.set_text("")
            self.pw_entry.set_visibility(False)
            self.btn_pw_reveal.get_child().set_markup(icons.eye_closed)
            self.btn_pw_ok.set_sensitive(False)
            
            self._start_scroll_anim()

    def _start_scroll_anim(self):
        if self._anim_id: GLib.source_remove(self._anim_id)
        scroll = self.get_ancestor(Gtk.ScrolledWindow)
        if not scroll: return
            
        self._cached_vadj = scroll.get_vadjustment()
        self._cached_scroll_h = scroll.get_allocated_height()
        self._anim_id = GLib.timeout_add(16, self._scroll_tick, scroll)

    def _scroll_tick(self, scroll):
        coords = self.translate_coordinates(scroll, 0, 0)
        if not coords:
            self._anim_id = None
            return False

        vadj = self._cached_vadj
        current_scroll = vadj.get_value()
        absolute_y = current_scroll + coords[1]

        target_h = self.get_allocated_height() if self.pw_rev.get_child_revealed() else self._target_height
        target_y = absolute_y - (self._cached_scroll_h / 2.0) + (target_h / 2.0)
        
        lower_limit = vadj.get_lower()
        upper_limit = max(lower_limit, vadj.get_upper() - vadj.get_page_size())
        target_y = max(lower_limit, min(target_y, upper_limit))
        
        new_scroll = current_scroll + (target_y - current_scroll) * 0.15
        
        if self.pw_rev.get_child_revealed() and abs(target_y - current_scroll) < 1.0:
            vadj.set_value(target_y)
            self._anim_id = None
            GLib.idle_add(self.pw_entry.grab_focus)
            return False

        vadj.set_value(new_scroll)
        return True

    def _close_pw(self):
        if self._anim_id:
            GLib.source_remove(self._anim_id)
            self._anim_id = None
            
        self.pw_rev.set_reveal_child(False)
        if WifiSlot._active_pw_slot is self:
            WifiSlot._active_pw_slot = None

    def _submit(self, pwd):
        self._close_pw()
        self.status_lbl.set_label("Connecting...")
        if self.nc:
            self.nc.connect_to_new_network(self.ssid, pwd, self._ok, self._err)

    def _ok(self, ssid):
        if self.parent_net: GLib.timeout_add(500, self.parent_net._req_ref)

    def _err(self, *_):
        self.status_lbl.set_label("Failed to connect")
        GLib.timeout_add(3000, self._restore)

    def _restore(self):
        if not self.conn: self.status_lbl.set_label("Saved" if self.saved else "Secured")
        return False

    def _on_settings(self, _):
        if self.parent_net:
            self.parent_net.open_settings(self.ssid)

    def destroy(self):
        if self._anim_id: GLib.source_remove(self._anim_id)
        self.parent_net = None
        self.nc = None
        super().destroy()


class NetworkConnections(Box):
    __slots__ = ('widgets', '_btns', '_rid', '_scan', '_slots', 'nc',
                 'stack', 'lists_stack', 'scan_lbl', 'scan_btn', 'saved_lbl', 'saved_btn',
                 'connected_box', 'avail_box', 'avail_section', 'saved_box', 'saved_section', 
                 'main_scroll', 'saved_scroll', 'header_title',
                 'settings_scroll', 'current_settings_ssid', '_previous_page',
                 'btn_net_forget', 'btn_net_disconnect', 'btn_net_share',
                 'lbl_net_forget', 'lbl_net_disconnect', '_is_current_connected',
                 'qr_revealer', 'qr_image', 'qr_password_lbl',
                 'lbl_sig', 'lbl_freq', 'lbl_sec', 'lbl_type', 'lbl_ip', 'lbl_gw')

    def __init__(self, **kwargs):
        self.widgets = kwargs.pop("widgets", None)
        super().__init__(
            name="network-connections", spacing=4, orientation="vertical",
            h_expand=True, v_expand=True, v_align="fill", **kwargs
        )

        self._btns = getattr(self.widgets.buttons, "network_button", None) if self.widgets else None
        self._rid = None
        self._scan = False
        self.current_settings_ssid = None
        self._previous_page = "main"
        self._slots = {"connected": [], "avail": [], "saved": []}
        self._is_current_connected = False

        try: self.nc = NetworkClient()
        except Exception: self.nc = None

        self._build()

        if self.nc:
            try:
                self.nc.connect("device-ready", self._rdy)
                self.nc.connect("connection-error", self._cerr)
            except Exception: pass
            if getattr(self.nc, 'wifi_device', None):
                GLib.idle_add(self._rdy)

    def _build(self):
        self.scan_lbl = Label(markup=icons.radar, name="network-scan-label")
        self.scan_btn = Button(name="network-scan", child=self.scan_lbl, tooltip_text="Scan Wi-Fi", on_clicked=self._on_scan)
        set_pointer_cursor(self.scan_btn) 

        self.saved_lbl = Label(markup=icons.save, name="network-saved-label")
        self.saved_btn = Button(
            name="network-saved", 
            child=self.saved_lbl, 
            tooltip_text="Saved Networks",
            on_clicked=self._on_saved_toggle
        )
        set_pointer_cursor(self.saved_btn) 

        back = Button(name="network-back", child=Label(markup=icons.chevron_left, name="network-back-label"))
        back.connect("clicked", self._on_back_click)
        set_pointer_cursor(back) 

        self.header_title = Label(label="Wi-Fi", v_align="center", name="header-title")

        header_end_box = Box(spacing=4, orientation="horizontal", children=(self.saved_btn, self.scan_btn))

        header = CenterBox(
            start_children=(back,), 
            center_children=(self.header_title,), 
            end_children=(header_end_box,)
        )
        header.set_margin_bottom(8)
        self.add(header)

        self.stack = Stack(transition_type="crossfade", h_expand=True, v_expand=True, v_align="fill")
        self.add(self.stack)

        off_box = Box(orientation="v", v_align="center", h_align="center", spacing=12, v_expand=True)
        
        off_icon = Label(markup=f"<span size='32768'>{icons.wifi_off}</span>")
        off_icon.get_style_context().add_class("wifi-off-icon")
        off_box.add(off_icon)
        
        off_label = Label(label="Wi-Fi is disabled")
        off_label.get_style_context().add_class("wifi-off-label")
        off_box.add(off_label)
        
        btn_turn_on = Button(label="Turn On", on_clicked=self._turn_on_wifi)
        btn_turn_on.get_style_context().add_class("wifi-turn-on-btn")
        set_pointer_cursor(btn_turn_on) 
        off_box.add(btn_turn_on)
        
        self.stack.add_named(off_box, "off")
        self.lists_stack = Stack(transition_type="slide-left-right", h_expand=True, v_expand=True, v_align="fill")

        self.connected_box = Box(spacing=2, orientation="vertical")
        self.avail_box = Box(spacing=2, orientation="vertical")
        self.avail_section = Box(orientation="v", spacing=4, children=(Label(label="Available Networks", h_align="start", name="section-title"), self.avail_box))

        self.main_scroll = ScrolledWindow(
            name="bluetooth-devices",
            min_content_size=(-1, -1),
            child=Box(spacing=4, orientation="vertical", children=[self.connected_box, self.avail_section]),
            h_expand=True, v_expand=True, propagate_width=False, propagate_height=False,
        )
        self.main_scroll.set_overlay_scrolling(False)

        self.saved_box = Box(spacing=2, orientation="vertical")
        self.saved_section = Box(orientation="v", spacing=4, children=(Label(label="Saved Networks", h_align="start", name="section-title"), self.saved_box))

        self.saved_scroll = ScrolledWindow(
            name="bluetooth-devices",
            min_content_size=(-1, -1),
            child=Box(spacing=4, orientation="vertical", children=[self.saved_section]),
            h_expand=True, v_expand=True, propagate_width=False, propagate_height=False,
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
        
        self.lbl_net_forget = Label(markup=f"<span size='large'>{icons.trash}</span> Удалить")
        self.btn_net_forget = Button(child=self.lbl_net_forget, on_clicked=self._do_forget)
        self.btn_net_forget.get_style_context().add_class("net-action-btn")
        self.btn_net_forget.get_style_context().add_class("net-forget")
        set_pointer_cursor(self.btn_net_forget) 

        self.lbl_net_disconnect = Label(markup=f"<span size='large'>{icons.cancel}</span> Отключить")
        self.btn_net_disconnect = Button(child=self.lbl_net_disconnect, on_clicked=self._do_disconnect_or_connect)
        self.btn_net_disconnect.get_style_context().add_class("net-action-btn")
        set_pointer_cursor(self.btn_net_disconnect) 

        self.btn_net_share = Button(child=Label(markup=f"<span size='large'>{icons.scan}</span> Поделиться"), on_clicked=self._do_share)
        self.btn_net_share.get_style_context().add_class("net-action-btn")
        set_pointer_cursor(self.btn_net_share) 

        actions_box.add(self.btn_net_forget)
        actions_box.add(self.btn_net_disconnect)
        actions_box.add(self.btn_net_share)
        actions_box.set_margin_bottom(12)
        settings_box.add(actions_box)

        self.qr_revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        qr_container = Box(orientation="vertical", spacing=8, h_align="center")
        qr_container.get_style_context().add_class("qr-container")
        
        self.qr_image = Image()
        self.qr_image.get_style_context().add_class("qr-img")
        
        self.qr_password_lbl = Label(selectable=True)
        self.qr_password_lbl.get_style_context().add_class("qr-password-text")

        qr_container.add(self.qr_image)
        qr_container.add(self.qr_password_lbl)
        self.qr_revealer.add(qr_container)
        settings_box.add(self.qr_revealer)

        self.lbl_sig = Label(label="-", h_align="end")
        self.lbl_freq = Label(label="-", h_align="end")
        self.lbl_sec = Label(label="-", h_align="end")
        self.lbl_type = Label(label="-", h_align="end")
        self.lbl_ip = Label(label="-", h_align="end", selectable=True)
        self.lbl_gw = Label(label="-", h_align="end", selectable=True)

        info_group = Box(orientation="vertical", spacing=2)
        info_group.get_style_context().add_class("net-info-group")

        def add_row(title, val_widget):
            row = Box(orientation="horizontal")
            row.get_style_context().add_class("net-info-row")
            row.pack_start(Label(label=title, h_align="start", name="dim-label"), True, True, 0)
            row.pack_end(val_widget, False, False, 0)
            info_group.add(row)

        add_row("Оценка уровня сигнала", self.lbl_sig)
        add_row("Частота", self.lbl_freq)
        add_row("Защита", self.lbl_sec)
        add_row("Тип", self.lbl_type)
        add_row("IP-адрес", self.lbl_ip)
        add_row("Шлюз, DNS", self.lbl_gw)

        settings_box.add(info_group)

        scroll = ScrolledWindow(
            name="bluetooth-devices", 
            min_content_size=(-1, -1),
            child=settings_box,
            h_expand=True, v_expand=True,
            propagate_width=False, propagate_height=False,
        )
        scroll.set_overlay_scrolling(False)
        return scroll

    def open_settings(self, ssid):
        self._previous_page = self.lists_stack.get_visible_child_name()
        
        self.current_settings_ssid = ssid
        self.header_title.set_label(ssid)
        self.qr_revealer.set_reveal_child(False)
        self.btn_net_share.get_style_context().remove_class("active")
        
        details = self.nc.get_network_details(ssid) if self.nc else {}

        self._is_current_connected = details.get("connected", False)
        
        is_available = False
        try:
            if self.nc:
                if hasattr(self.nc, 'is_network_available'):
                    is_available = self.nc.is_network_available(ssid)
                elif hasattr(self.nc, 'wifi_device') and self.nc.wifi_device:
                    for ap in getattr(self.nc.wifi_device, 'access_points', []):
                        if ap.get("ssid") == ssid:
                            is_available = True
                            break
        except Exception: pass

        if self._is_current_connected:
            self.lbl_net_disconnect.set_markup(f"<span size='large'>{icons.cancel}</span> Отключить")
            self.btn_net_disconnect.set_sensitive(True)
        else:
            self.lbl_net_disconnect.set_markup(f"<span size='large'>{icons.accept}</span> Подключить")
            self.btn_net_disconnect.set_sensitive(is_available)

        self.lbl_sig.set_label(details.get("strength", "Unknown"))
        self.lbl_freq.set_label(details.get("frequency", "Unknown"))
        self.lbl_sec.set_label(details.get("security", "Unknown"))
        self.lbl_type.set_label(details.get("type", "Unknown"))
        self.lbl_ip.set_label(details.get("ip", "N/A"))
        
        gw = details.get("gateway", "N/A")
        dns = details.get("dns", "N/A")
        self.lbl_gw.set_label(f"{gw} / {dns}" if gw != "N/A" else "N/A")

        self.scan_btn.set_visible(False)
        self.saved_btn.set_visible(False)

        self.lists_stack.set_visible_child_name("settings")

    def _do_forget(self, _):
        if self.current_settings_ssid and self.nc:
            self.nc.delete_saved_network(self.current_settings_ssid)
            self._on_back_click(None)
            GLib.timeout_add(300, self._req_ref)

    def _do_disconnect_or_connect(self, _):
        if not self.nc or not self.current_settings_ssid: return

        if self._is_current_connected:
            self.nc.disconnect_network()
        else:
            def on_success(*args):
                GLib.timeout_add(500, self._req_ref)
            def on_error(*args): pass
            
            self.nc.connect_to_saved_network(self.current_settings_ssid, on_success, on_error)

        self._on_back_click(None)
        GLib.timeout_add(300, self._req_ref)

    def _do_share(self, btn):
        if self.qr_revealer.get_reveal_child():
            self.qr_revealer.set_reveal_child(False)
            btn.get_style_context().remove_class("active")
            return

        ssid = self.current_settings_ssid
        if not ssid or not self.nc: return

        password = self.nc.get_network_password(ssid)
        sec_raw = self.lbl_sec.get_label().upper()
        
        sec_type = "WPA" if "WPA" in sec_raw else "WEP" if "WEP" in sec_raw else "nopass"

        qr_string = f"WIFI:S:{ssid};"
        if password: qr_string += f"T:{sec_type};P:{password};;"
        else: qr_string += "T:nopass;;"

        qr_path = f"/tmp/wifi_qr_{ssid}.png"
        generated = False

        try:
            import qrcode
            qr = qrcode.QRCode(version=1, box_size=5, border=1)
            qr.add_data(qr_string)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            img.save(qr_path)
            generated = True
        except ImportError:
            try:
                subprocess.run(["qrencode", "-o", qr_path, qr_string], check=True)
                generated = True
            except Exception:
                pass

        if generated and os.path.exists(qr_path):
            self.qr_image.set_from_file(qr_path)
            self.qr_password_lbl.set_label(f"Пароль: {password}" if password else "Открытая сеть")
            self.qr_revealer.set_reveal_child(True)
            btn.get_style_context().add_class("active")
        else:
            self.qr_password_lbl.set_label("Установите 'python3-qrcode' для QR кода")
            self.qr_revealer.set_reveal_child(True)

    def _on_back_click(self, btn):
        curr = self.lists_stack.get_visible_child_name()
        
        if curr == "settings":
            self.lists_stack.set_visible_child_name(self._previous_page)
            self.header_title.set_label("Wi-Fi")
            self.scan_btn.set_visible(True)
            self.saved_btn.set_visible(True)
            
            if self._previous_page == "saved":
                self.saved_btn.add_style_class("pressed")
            else:
                self.saved_btn.remove_style_class("pressed")
                
        elif curr == "saved":
            self.lists_stack.set_visible_child_name("main")
            self.saved_btn.remove_style_class("pressed")
            
        else:
            if self.widgets: self.widgets.show_notif()

    def _on_saved_toggle(self, btn):
        if self.lists_stack.get_visible_child_name() == "main":
            self.lists_stack.set_visible_child_name("saved")
            btn.add_style_class("pressed")
        else:
            self.lists_stack.set_visible_child_name("main")
            btn.remove_style_class("pressed")

    def _turn_on_wifi(self, *_):
        if self._btns and hasattr(self._btns, 'network_status_button'):
            self._btns.network_status_button.clicked()
        else:
            if dev := getattr(self.nc, 'wifi_device', None):
                if hasattr(dev, "toggle_wifi"): dev.toggle_wifi()
        GLib.timeout_add(400, self._req_ref)

    def _rdy(self, client=None):
        if getattr(self.nc, 'wifi_device', None):
            try: self.nc.wifi_device.connect("changed", self._sched)
            except Exception: pass
            self._sched()

    def _cerr(self, client, ssid, msg):
        for pool in self._slots.values():
            for slot in pool:
                if slot.ssid == ssid:
                    slot._err()
                    return

    def _sched(self, *_):
        if not self._rid: self._rid = GLib.timeout_add(500, self._ref)

    def _req_ref(self):
        self._ref()
        return False

    def _ref(self):
        self._rid = None
        if getattr(WifiSlot, "_active_pw_slot", None): return False

        nc = self.nc
        dev = getattr(nc, 'wifi_device', None)
        en = bool(dev and getattr(dev, 'enabled', False))

        self.stack.set_visible_child_name("on" if en else "off")
        if not en: return False

        cur = self._gcur()
        saved = self._gsaved() 
        avail = dev.access_points if dev else []
        
        avail_d = {ap.get("ssid"): ap for ap in avail if ap.get("ssid")}
        saved_s = frozenset(saved)

        dap = {"strength": 0, "is_secured": True, "icon-name": "network-wireless-signal-none-symbolic"}
        cd, ad, sd = [], [], []

        if cur:
            is_cur_saved = cur in saved_s
            cd.append((avail_d.get(cur, {"ssid": cur, **dap, "icon-name": "network-wireless-signal-excellent-symbolic"}), is_cur_saved, True))

        for ap in avail:
            s = ap.get("ssid")
            if s and s != cur:
                ad.append((ap, s in saved_s, False))

        for s in saved:
            ap_data = avail_d.get(s, {"ssid": s, **dap})
            sd.append((ap_data, True, s == cur))

        self._ubox(self.connected_box, self._slots["connected"], cd)
        self._ubox(self.avail_box, self._slots["avail"], ad)
        self._ubox(self.saved_box, self._slots["saved"], sd)

        self.connected_box.set_visible(len(cd) > 0)
        self.avail_section.set_visible(True)

        st = getattr(dev, 'strength', 0) if dev else 0
        txt = "Off" if not en else (cur or "Disconnected")

        if self.widgets and hasattr(self.widgets, 'update_network_display'):
            self.widgets.update_network_display(txt, st, en)
        if self._btns and hasattr(self._btns, 'update_state'):
            GLib.idle_add(self._btns.update_state)

        return False

    def _ubox(self, box, pool, data):
        n, e = len(data), len(pool)
        while len(pool) < n:
            slot = WifiSlot(self.nc, self)
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
                        s = conn.get_setting_wireless()
                        c_set = conn.get_setting_connection()
                        if s and (sd := s.get_ssid()):
                            ssid = NM.utils_ssid_to_utf8(sd.get_data())
                            ts = c_set.get_timestamp() if c_set else 0
                            if not any(x[0] == ssid for x in saved):
                                saved.append((ssid, ts))
            except Exception: pass
        saved.sort(key=lambda x: x[1], reverse=True)
        return [x[0] for x in saved]

    def _on_scan(self, btn):
        if self._scan: return
        self._scan = True
        self.scan_lbl.get_style_context().add_class("scanning")
        self.scan_btn.get_style_context().add_class("scanning")
        if dev := getattr(self.nc, 'wifi_device', None):
            if getattr(dev, 'enabled', False):
                try: dev.scan() 
                except Exception: 
                    if hasattr(dev, "request_scan"):
                        try: dev.request_scan()
                        except Exception: pass
        GLib.timeout_add(3500, self._rscan)

    def _rscan(self):
        self.scan_lbl.get_style_context().remove_class("scanning")
        self.scan_btn.get_style_context().remove_class("scanning")
        self._scan = False
        return False

    def cleanup(self):
        if self._rid: GLib.source_remove(self._rid); self._rid = None
        WifiSlot._active_pw_slot = None
        for pool in self._slots.values():
            for slot in pool: slot.destroy()
            pool.clear()
        self.nc = self.widgets = self._btns = None