import shutil
import subprocess

from gi.repository import Gtk, NM, GLib

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow
from fabric.widgets.stack import Stack

import services.icons as icons


class WifiSlot(Gtk.Box):
    _active_pw_slot = None

    def __init__(self, nc, parent_net):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self._destroyed = False
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
            on_clicked=self._on_settings,
        )
        self.btn_settings.get_style_context().add_class("pixel-icon-button")
        self.btn_settings.get_style_context().add_class("settings-btn")

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
            on_clicked=self._on_reveal_clicked,
        )
        self.btn_pw_reveal.get_style_context().add_class("pw-reveal-btn")

        self.pw_entry = Gtk.Entry(
            visibility=False,
            invisible_char=ord('•'),
            placeholder_text="Password...",
        )
        self.pw_entry.set_hexpand(True)
        self.pw_entry.get_style_context().add_class("pw-entry-naked")

        self.btn_pw_ok = Button(
            child=Label(markup=icons.accept),
            on_clicked=self._on_pw_submit,
        )
        self.btn_pw_ok.set_sensitive(False)
        self.btn_pw_ok.get_style_context().add_class("pw-submit-btn")

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

        self.connect("destroy", self._on_destroy)

    def _on_destroy(self, _widget):
        self.cleanup()

    def cleanup(self):
        if self._destroyed:
            return
        self._destroyed = True
        if self._anim_id:
            GLib.source_remove(self._anim_id)
            self._anim_id = None
        if WifiSlot._active_pw_slot is self:
            WifiSlot._active_pw_slot = None
        self.parent_net = None
        self.nc = None

    def _on_reveal_clicked(self, btn):
        new_visibility = not self.pw_entry.get_visibility()
        self.pw_entry.set_visibility(new_visibility)
        icon_markup = icons.eye_check if new_visibility else icons.eye_closed
        child = btn.get_child()
        if child:
            child.set_markup(icon_markup)

    def _on_pw_change(self, entry):
        self.btn_pw_ok.set_sensitive(len(entry.get_text()) > 0)

    def _on_pw_submit(self, _btn):
        pwd = self.pw_entry.get_text()
        if pwd:
            self._submit(pwd)

    def _on_pw_activate(self, _entry):
        pwd = self.pw_entry.get_text()
        if pwd:
            self._submit(pwd)

    def update(self, data, saved=False, conn=False):
        self.ssid = data.get("ssid", "Unknown")
        self.saved = saved
        self.conn = conn
        is_secured = data.get("is_secured", True)

        self.icon.set_from_icon_name(
            data.get("icon-name", "network-wireless-signal-none-symbolic"), 24,
        )
        self.name_lbl.set_label(self.ssid)

        avail = False
        if self.nc and self.ssid:
            avail = self.nc.is_network_available(self.ssid)

        if conn or saved:
            self.btn_settings.set_visible(True)
            self.lock_icon.set_visible(False)
        else:
            self.btn_settings.set_visible(False)
            self.lock_icon.set_visible(is_secured)

        main_ctx = self.main_box.get_style_context()
        icon_ctx = self.icon.get_style_context()
        btn_ctx = self.btn_settings.get_style_context()

        if conn:
            self.status_lbl.set_label("Connected")
            main_ctx.add_class("active-slot")
            icon_ctx.add_class("active-icon")
            btn_ctx.add_class("active-settings-btn")
        else:
            main_ctx.remove_class("active-slot")
            icon_ctx.remove_class("active-icon")
            btn_ctx.remove_class("active-settings-btn")

            if not avail:
                self.status_lbl.set_label("Out of range")
            elif saved:
                self.status_lbl.set_label("Saved")
            else:
                self.status_lbl.set_label("Secured" if is_secured else "Open")

        return self

    def _on_click(self, _widget, _event):
        if self.conn:
            return
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
            pw_child = self.pw_rev.get_child()
            child_h = 0
            if pw_child:
                _, child_h = pw_child.get_preferred_height()
            self._target_height = base_h + child_h

            self.pw_rev.set_reveal_child(True)
            self.pw_entry.set_text("")
            self.pw_entry.set_visibility(False)

            reveal_child = self.btn_pw_reveal.get_child()
            if reveal_child:
                reveal_child.set_markup(icons.eye_closed)
            self.btn_pw_ok.set_sensitive(False)

            self._start_scroll_anim()

    def _start_scroll_anim(self):
        if self._anim_id:
            GLib.source_remove(self._anim_id)
            self._anim_id = None

        scroll = self.get_ancestor(Gtk.ScrolledWindow)
        if not scroll:
            return

        self._cached_vadj = scroll.get_vadjustment()
        self._cached_scroll_h = scroll.get_allocated_height()
        self._anim_id = GLib.timeout_add(16, self._scroll_tick, scroll)

    def _scroll_tick(self, scroll):
        if self._destroyed:
            self._anim_id = None
            return False

        coords = self.translate_coordinates(scroll, 0, 0)
        if not coords:
            self._anim_id = None
            return False

        vadj = self._cached_vadj
        if not vadj:
            self._anim_id = None
            return False

        current_scroll = vadj.get_value()
        absolute_y = current_scroll + coords[1]

        if self.pw_rev.get_child_revealed():
            target_h = self.get_allocated_height()
        else:
            target_h = self._target_height

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

    def _ok(self, _ssid):
        if self._destroyed:
            return
        if self.parent_net:
            GLib.timeout_add(500, self.parent_net._req_ref)

    def _err(self, *_args):
        if self._destroyed:
            return
        self.status_lbl.set_label("Failed to connect")
        GLib.timeout_add(3000, self._restore)

    def _restore(self):
        if self._destroyed:
            return False
        if not self.conn:
            self.status_lbl.set_label("Saved" if self.saved else "Secured")
        return False

    def _on_settings(self, _btn):
        if self.parent_net and self.ssid:
            self.parent_net.open_settings(self.ssid)


class NetworkConnections(Box):
    def __init__(self, **kwargs):
        self.widgets = kwargs.pop("widgets", None)
        super().__init__(
            name="network-connections",
            spacing=4,
            orientation="vertical",
            h_expand=True,
            v_expand=True,
            v_align="fill",
            **kwargs,
        )

        # Контракт: NetworkConnections требует объект widgets (Dashboard) с уже
        # созданными `.network_client` (единый общий NetworkClient) и
        # `.buttons.network_button`. Порядок создания в Dashboard.__init__
        # гарантирует, что оба атрибута существуют к этому моменту.
        self.nc = self.widgets.network_client
        self._btns = self.widgets.buttons.network_button

        self._rid = None
        self._scan = False
        self._destroyed = False
        self.current_settings_ssid = None
        self._previous_page = "main"
        self._slots = {"connected": [], "avail": [], "saved": []}
        self._is_current_connected = False
        self._wifi_changed_hid = None

        self._build()

        self._device_ready_hid = self.nc.connect("device-ready", self._rdy)
        self._connection_error_hid = self.nc.connect("connection-error", self._cerr)

        self.connect("destroy", self._on_destroy)

    def _on_destroy(self, _widget):
        self.cleanup()

    def _build(self):
        self.scan_lbl = Label(markup=icons.radar, name="network-scan-label")
        self.scan_btn = Button(
            name="network-scan",
            child=self.scan_lbl,
            tooltip_text="Scan",
            on_clicked=self._on_scan,
        )

        self.saved_lbl = Label(markup=icons.save, name="network-saved-label")
        self.saved_btn = Button(
            name="network-saved",
            child=self.saved_lbl,
            tooltip_text="Saved Networks",
            on_clicked=self._on_saved_toggle,
        )

        back = Button(
            name="network-back",
            child=Label(markup=icons.chevron_left, name="network-back-label"),
        )
        back.connect("clicked", self._on_back_click)

        self.header_title = Label(label="Wi-Fi", v_align="center", name="header-title")

        header_end_box = Box(
            spacing=4, orientation="horizontal", children=(self.saved_btn, self.scan_btn),
        )

        header = CenterBox(
            start_children=(back,),
            center_children=(self.header_title,),
            end_children=(header_end_box,),
        )
        header.set_margin_bottom(8)
        self.add(header)

        self.stack = Stack(
            transition_type="crossfade", h_expand=True, v_expand=True, v_align="fill",
        )
        self.add(self.stack)

        off_box = Box(
            orientation="v", v_align="center", h_align="center", spacing=12, v_expand=True,
        )

        off_icon = Label(markup=f"<span size='32768'>{icons.wifi_off}</span>")
        off_icon.get_style_context().add_class("wifi-off-icon")
        off_box.add(off_icon)

        off_label = Label(label="Wi-Fi is disabled")
        off_label.get_style_context().add_class("wifi-off-label")
        off_box.add(off_label)

        btn_turn_on = Button(label="Turn On", h_align="center", on_clicked=self._turn_on_wifi)
        btn_turn_on.get_style_context().add_class("wifi-turn-on-btn")
        off_box.add(btn_turn_on)

        self.stack.add_named(off_box, "off")

        self.lists_stack = Stack(
            transition_type="slide-left-right", h_expand=True, v_expand=True, v_align="fill",
        )

        self.connected_box = Box(spacing=2, orientation="vertical")
        self.avail_box = Box(spacing=2, orientation="vertical")

        self.avail_empty = Box(
            orientation="v", v_align="center", h_align="center", spacing=12, v_expand=True,
        )
        self.avail_empty.set_margin_top(24)
        self.avail_empty.set_margin_bottom(24)

        empty_icon = Label(markup=f"<span size='32768'>{icons.radar}</span>")
        empty_icon.get_style_context().add_class("wifi-off-icon")
        self.avail_empty.add(empty_icon)

        empty_lbl = Label(label="No networks found")
        empty_lbl.get_style_context().add_class("wifi-off-label")
        self.avail_empty.add(empty_lbl)

        btn_scan = Button(label="Scan", h_align="center", on_clicked=self._on_scan)
        btn_scan.get_style_context().add_class("wifi-turn-on-btn")
        self.avail_empty.add(btn_scan)

        self.avail_stack = Stack(transition_type="crossfade", h_expand=True, v_expand=True)
        self.avail_stack.add_named(self.avail_box, "list")
        self.avail_stack.add_named(self.avail_empty, "empty")

        self.avail_section = Box(
            orientation="v",
            spacing=4,
            children=(
                Label(label="Available Networks", h_align="start", name="section-title"),
                self.avail_stack,
            ),
        )

        self.main_scroll = ScrolledWindow(
            name="bluetooth-devices",
            min_content_size=(-1, -1),
            child=Box(
                spacing=4,
                orientation="vertical",
                children=[self.connected_box, self.avail_section],
            ),
            h_expand=True, v_expand=True, propagate_width=False, propagate_height=False,
        )
        self.main_scroll.set_overlay_scrolling(False)

        self.saved_box = Box(spacing=2, orientation="vertical")
        self.saved_section = Box(
            orientation="v",
            spacing=4,
            children=(
                Label(label="Saved Networks", h_align="start", name="section-title"),
                self.saved_box,
            ),
        )

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

        actions_box = Box(
            orientation="horizontal", spacing=8, h_align="center", h_expand=True,
        )

        self.lbl_net_forget = Label(
            markup=f"<span size='large'>{icons.trash}</span> Remove",
        )
        self.btn_net_forget = Button(child=self.lbl_net_forget, on_clicked=self._do_forget)
        self.btn_net_forget.get_style_context().add_class("net-action-btn")
        self.btn_net_forget.get_style_context().add_class("net-forget")

        self.lbl_net_disconnect = Label(
            markup=f"<span size='large'>{icons.cancel}</span> Disconnect",
        )
        self.btn_net_disconnect = Button(
            child=self.lbl_net_disconnect, on_clicked=self._do_disconnect_or_connect,
        )
        self.btn_net_disconnect.get_style_context().add_class("net-action-btn")

        self.btn_net_share = Button(
            child=Label(markup=f"<span size='large'>{icons.scan}</span> Share"),
            on_clicked=self._do_share,
        )
        self.btn_net_share.get_style_context().add_class("net-action-btn")

        actions_box.add(self.btn_net_forget)
        actions_box.add(self.btn_net_disconnect)
        actions_box.add(self.btn_net_share)
        actions_box.set_margin_bottom(12)
        settings_box.add(actions_box)

        self.qr_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
        )
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
            row.pack_start(
                Label(label=title, h_align="start", name="dim-label"), True, True, 0,
            )
            row.pack_end(val_widget, False, False, 0)
            info_group.add(row)

        add_row("Signal Strength", self.lbl_sig)
        add_row("Frequency", self.lbl_freq)
        add_row("Security", self.lbl_sec)
        add_row("Type", self.lbl_type)
        add_row("IP Address", self.lbl_ip)
        add_row("Gateway / DNS", self.lbl_gw)

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
        if not ssid:
            return

        self._previous_page = self.lists_stack.get_visible_child_name()
        self.current_settings_ssid = ssid
        self.header_title.set_label(ssid)
        self.qr_revealer.set_reveal_child(False)
        self.btn_net_share.get_style_context().remove_class("active")

        details = self.nc.get_network_details(ssid)
        self._is_current_connected = details.get("connected", False)
        is_available = self.nc.is_network_available(ssid)

        if self._is_current_connected:
            self.lbl_net_disconnect.set_markup(
                f"<span size='large'>{icons.cancel}</span> Disconnect",
            )
            self.btn_net_disconnect.set_sensitive(True)
        else:
            self.lbl_net_disconnect.set_markup(
                f"<span size='large'>{icons.accept}</span> Connect",
            )
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

    def _do_forget(self, _btn):
        if not self.current_settings_ssid:
            return
        self.nc.delete_saved_network(self.current_settings_ssid)
        self._on_back_click(None)
        GLib.timeout_add(300, self._req_ref)

    def _do_disconnect_or_connect(self, _btn):
        if not self.current_settings_ssid:
            return

        if self._is_current_connected:
            self.nc.disconnect_network()
        else:
            self.nc.connect_to_saved_network(
                self.current_settings_ssid,
                success_cb=lambda _ssid: GLib.timeout_add(500, self._req_ref),
            )

        self._on_back_click(None)
        GLib.timeout_add(300, self._req_ref)

    def _do_share(self, btn):
        if self.qr_revealer.get_reveal_child():
            self.qr_revealer.set_reveal_child(False)
            btn.get_style_context().remove_class("active")
            return

        ssid = self.current_settings_ssid
        if not ssid:
            return

        password = self.nc.get_network_password(ssid)
        sec_raw = (self.lbl_sec.get_label() or "").upper()

        if "WPA" in sec_raw:
            sec_type = "WPA"
        elif "WEP" in sec_raw:
            sec_type = "WEP"
        else:
            sec_type = "nopass"

        qr_string = f"WIFI:S:{ssid};"
        qr_string += f"T:{sec_type};P:{password};;" if password else "T:nopass;;"

        qr_path = f"/tmp/wifi_qr_{ssid}.png"
        generated = self._generate_qr(qr_string, qr_path)

        if generated:
            self.qr_image.set_from_file(qr_path)
            self.qr_password_lbl.set_label(
                f"Password: {password}" if password else "Open network",
            )
            btn.get_style_context().add_class("active")
        else:
            self.qr_password_lbl.set_label(
                "Install 'python3-qrcode' or 'qrencode' for QR code",
            )

        self.qr_revealer.set_reveal_child(True)

    def _generate_qr(self, data: str, path: str) -> bool:
        try:
            import qrcode
        except ImportError:
            # python3-qrcode не установлен — падаем на внешний бинарь qrencode
            return self._generate_qr_via_qrencode(data, path)

        qr = qrcode.QRCode(version=1, box_size=5, border=1)
        qr.add_data(data)
        qr.make(fit=True)
        qr.make_image(fill_color="black", back_color="white").save(path)
        return True

    def _generate_qr_via_qrencode(self, data: str, path: str) -> bool:
        if shutil.which("qrencode") is None:
            return False
        try:
            subprocess.run(["qrencode", "-o", path, data], check=True, timeout=5)
        except subprocess.CalledProcessError:
            # переданная строка (SSID/пароль) не влезает в выбранную версию QR-кода
            return False
        return True

    def _on_back_click(self, _btn):
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
            self.widgets.show_notif()

    def _on_saved_toggle(self, btn):
        if self.lists_stack.get_visible_child_name() == "main":
            self.lists_stack.set_visible_child_name("saved")
            btn.add_style_class("pressed")
        else:
            self.lists_stack.set_visible_child_name("main")
            btn.remove_style_class("pressed")

    def _turn_on_wifi(self, *_args):
        if self._btns:
            self._btns.network_status_button.clicked()
        elif self.nc.wifi_device:
            self.nc.wifi_device.toggle_wifi()
        GLib.timeout_add(400, self._req_ref)

    def _rdy(self, _client=None):
        dev = self.nc.wifi_device
        if dev:
            self._wifi_changed_hid = dev.connect("changed", self._sched)
            self._sched()

    def _cerr(self, _client, ssid, _msg):
        for pool in self._slots.values():
            for slot in pool:
                if slot.ssid == ssid:
                    slot._err()
                    return

    def _sched(self, *_args):
        if self._destroyed:
            return
        if self._rid is None:
            self._rid = GLib.timeout_add(500, self._ref)

    def _req_ref(self):
        if self._destroyed:
            return False
        self._ref()
        return False

    def _ref(self):
        self._rid = None

        if self._destroyed:
            return False

        if WifiSlot._active_pw_slot:
            return False

        dev = self.nc.wifi_device
        enabled = bool(dev and dev.enabled)

        self.stack.set_visible_child_name("on" if enabled else "off")
        if not enabled:
            return False

        cur = self._get_current_ssid()
        saved = self._get_saved_networks()
        avail = dev.access_points

        avail_d = {}
        for ap in avail:
            ssid = ap.get("ssid")
            if ssid:
                avail_d[ssid] = ap

        saved_s = frozenset(saved)
        default_ap = {
            "strength": 0,
            "is_secured": True,
            "icon-name": "network-wireless-signal-none-symbolic",
        }

        connected_data = []
        available_data = []
        saved_data = []

        if cur:
            is_cur_saved = cur in saved_s
            ap_data = avail_d.get(cur)
            if not ap_data:
                ap_data = {
                    "ssid": cur,
                    **default_ap,
                    "icon-name": "network-wireless-signal-excellent-symbolic",
                }
            connected_data.append((ap_data, is_cur_saved, True))

        for ap in avail:
            ssid = ap.get("ssid")
            if ssid and ssid != cur:
                available_data.append((ap, ssid in saved_s, False))

        for ssid in saved:
            ap_data = avail_d.get(ssid, {"ssid": ssid, **default_ap})
            saved_data.append((ap_data, True, ssid == cur))

        self._ubox(self.connected_box, self._slots["connected"], connected_data)
        self._ubox(self.avail_box, self._slots["avail"], available_data)
        self._ubox(self.saved_box, self._slots["saved"], saved_data)

        self.connected_box.set_visible(len(connected_data) > 0)
        self.avail_section.set_visible(True)

        if len(available_data) > 0:
            self.avail_stack.set_visible_child_name("list")
        else:
            self.avail_stack.set_visible_child_name("empty")

        self.widgets.update_network_display(cur or "Disconnected", dev.strength, enabled)

        if self._btns:
            GLib.idle_add(self._btns.update_state)

        return False

    def _ubox(self, box, pool, data):
        needed = len(data)

        while len(pool) < needed:
            slot = WifiSlot(self.nc, self)
            pool.append(slot)
            box.add(slot)

        for i in range(needed, len(pool)):
            pool[i].hide()

        for i, (ap, saved, conn) in enumerate(data):
            pool[i].update(ap, saved, conn)
            pool[i].show()

    def _get_current_ssid(self):
        dev = self.nc.wifi_device
        if not dev:
            return None
        ssid = dev.ssid
        if ssid in ("Disconnected", "Off", None, ""):
            return None
        return ssid

    def _get_saved_networks(self):
        saved = []
        if not self.nc._client:
            return saved

        for conn in self.nc._client.get_connections():
            if conn.get_connection_type() != "802-11-wireless":
                continue
            s = conn.get_setting_wireless()
            if not s:
                continue
            sd = s.get_ssid()
            if not sd:
                continue
            ssid = NM.utils_ssid_to_utf8(sd.get_data())
            if not ssid:
                continue
            c_set = conn.get_setting_connection()
            ts = c_set.get_timestamp() if c_set else 0
            if not any(x[0] == ssid for x in saved):
                saved.append((ssid, ts))

        saved.sort(key=lambda x: x[1], reverse=True)
        return [x[0] for x in saved]

    def _on_scan(self, _btn):
        if self._scan:
            return

        self._scan = True
        self.scan_lbl.get_style_context().add_class("scanning")
        self.scan_btn.get_style_context().add_class("scanning")

        dev = self.nc.wifi_device
        if dev and dev.enabled:
            dev.scan()

        GLib.timeout_add(3500, self._rscan)

    def _rscan(self):
        if self._destroyed:
            return False
        self._scan = False
        self.scan_lbl.get_style_context().remove_class("scanning")
        self.scan_btn.get_style_context().remove_class("scanning")
        return False

    def cleanup(self):
        if self._destroyed:
            return
        self._destroyed = True

        if self._rid:
            GLib.source_remove(self._rid)
            self._rid = None

        WifiSlot._active_pw_slot = None

        self.nc.disconnect(self._device_ready_hid)
        self.nc.disconnect(self._connection_error_hid)
        dev = self.nc.wifi_device
        if dev and self._wifi_changed_hid:
            dev.disconnect(self._wifi_changed_hid)

        for pool in self._slots.values():
            for slot in pool:
                slot.destroy()
            pool.clear()

        # NetworkClient общий (владелец — Dashboard) — намеренно НЕ вызываем
        # self.nc.cleanup(), это уничтожило бы клиент для NetworkButton.
        self.nc = None
        self.widgets = None
        self._btns = None