from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow

from gi.repository import Gtk, NM, GLib

import psutil

import modules.icons as icons
from services.network import NetworkClient


class WifiNetworkSlot(CenterBox):
    __slots__ = (
        'network_data', 'network_client', 'parent_window', 'refresh_callback',
        'is_saved', 'is_connected', 'network_icon', 'network_label',
        'strength_label', 'connect_button', 'delete_button', 'connected_label',
        'unavailable_button', 'pw_block', '_error_handler_id'
    )
    
    _active_pw_block = None
    _active_slot = None

    def __init__(self, network_data, network_client, parent_window, refresh_callback=None, is_saved=False, is_connected=False, **kwargs):
        super().__init__(name="wifi-network-slot", **kwargs)
        
        self.network_data = network_data
        self.network_client = network_client
        self.parent_window = parent_window
        self.refresh_callback = refresh_callback
        self.is_saved = is_saved
        self.is_connected = is_connected
        self.pw_block = None
        
        ssid = network_data.get("ssid", "Unknown")
        strength = network_data.get("strength", 0)
        
        self.network_icon = Image(
            icon_name=network_data.get("icon-name", "network-wireless-signal-none-symbolic"), 
            size=16
        )
        self.network_label = Label(label=ssid, h_expand=True, h_align="start", ellipsization="end")
        self.strength_label = Label(label=f"{strength}%")
        
        self.connect_button = Button(name="wifi-connect", label="Подключиться")
        self.connect_button.connect("clicked", self._on_connect_clicked)
        
        self.delete_button = Button(
            name="wifi-delete", 
            child=Label(name="wifi-delete-label", markup=icons.trash),
            tooltip_text="Удалить сеть"
        )
        self.delete_button.connect("clicked", self._on_delete_clicked)
        
        self.connected_label = Label(label="Подключено", name="wifi-connected-label")
        
        self.unavailable_button = Button(
            name="wifi-unavailable", 
            label="Недоступна", 
            sensitive=False, 
            can_focus=False
        )
        self.unavailable_button.get_style_context().add_class("unavailable-network")
        
        self.start_children = Box(
            spacing=8, 
            h_expand=True, 
            h_align="fill", 
            children=[self.network_icon, self.network_label, self.strength_label]
        )
        
        self._setup_action_widgets()
        
        self._error_handler_id = self.network_client.connect(
            "connection-error", 
            self._on_global_error
        )

    def _on_global_error(self, client, err_ssid, message):
        if err_ssid == self.network_data.get("ssid"):
            self._show_error("Ошибка")

    def _setup_action_widgets(self):
        ssid = self.network_data.get("ssid")
        nc = self.network_client
        
        is_available = (
            hasattr(nc, 'is_network_available') and 
            ssid and 
            nc.is_network_available(ssid)
        )
        
        if self.is_connected:
            widgets = [self.connected_label, self.delete_button]
        elif self.is_saved and not is_available:
            widgets = [self.unavailable_button, self.delete_button]
        elif not is_available:
            widgets = [self.unavailable_button]
        elif self.is_saved:
            widgets = [self.connect_button, self.delete_button]
        else:
            widgets = [self.connect_button]
            
        self.end_children = Box(orientation="horizontal", spacing=4, children=widgets)

    def _on_connect_clicked(self, button):
        if self.is_saved:
            self._connect_to_saved_network()
        else:
            self._toggle_password_input()

    def _toggle_password_input(self):
        parent_box = self.get_parent()
        if not parent_box:
            return

        cls = WifiNetworkSlot
        
        if cls._active_slot is self:
            self._close_password_block()
            return

        if cls._active_pw_block:
            cls._active_slot._close_password_block()

        pw_block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        pw_block.set_name("password-entry-container")
        pw_block.set_margin_top(4)
        pw_block.set_margin_bottom(12)
        pw_block.set_margin_start(10)
        pw_block.set_margin_end(10)

        entry = Gtk.Entry(
            visibility=False, 
            invisible_char='•', 
            placeholder_text="Пароль..."
        )
        
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        cancel_btn = Gtk.Button(label="Отмена")
        ok_btn = Gtk.Button(label="ОК")
        ok_btn.set_sensitive(False)

        cancel_btn.connect("clicked", lambda _: self._close_password_block())
        ok_btn.connect("clicked", lambda _: self._submit_password(entry.get_text()))
        entry.connect("changed", lambda e: ok_btn.set_sensitive(len(e.get_text()) > 0))
        entry.connect("activate", lambda e: ok_btn.clicked() if ok_btn.get_sensitive() else None)

        btn_box.pack_start(cancel_btn, True, True, 0)
        btn_box.pack_start(ok_btn, True, True, 0)
        pw_block.pack_start(entry, False, False, 0)
        pw_block.pack_start(btn_box, False, False, 0)

        self.pw_block = pw_block
        cls._active_pw_block = pw_block
        cls._active_slot = self

        children = parent_box.get_children()
        idx = children.index(self)
        parent_box.pack_start(pw_block, False, False, 0)
        parent_box.reorder_child(pw_block, idx + 1)
        
        pw_block.show_all()
        GLib.idle_add(entry.grab_focus)

    def _close_password_block(self):
        cls = WifiNetworkSlot
        if cls._active_pw_block:
            cls._active_pw_block.destroy()
            cls._active_pw_block = None
            cls._active_slot = None
        self.pw_block = None

    def _submit_password(self, password):
        ssid = self.network_data.get("ssid")
        self._close_password_block()
        self._set_connecting_state(True)
        self.network_client.connect_to_new_network(
            ssid, 
            password,
            self._on_connection_success,
            self._on_connection_error
        )

    def _set_connecting_state(self, connecting):
        btn = self.connect_button
        if connecting:
            btn.set_sensitive(False)
            btn.set_label("Подключение...")
        else:
            btn.set_sensitive(True)
            btn.set_label("Подключиться")

    def _on_connection_success(self, ssid):
        self._set_connecting_state(False)
        cb = self.refresh_callback
        if cb:
            GLib.timeout_add(500, cb)

    def _on_connection_error(self, ssid, error_message):
        self._show_error("Ошибка")

    def _show_error(self, error_message):
        self._set_connecting_state(False)
        btn = self.connect_button
        btn.set_label(error_message)
        btn.set_sensitive(False)
        GLib.timeout_add(3000, self._restore_button)

    def _restore_button(self):
        btn = self.connect_button
        if btn:
            btn.set_label("Подключиться")
            btn.set_sensitive(True)
        return False

    def _on_delete_clicked(self, button):
        ssid = self.network_data.get("ssid")
        if ssid:
            self.network_client.delete_saved_network(ssid)
            cb = self.refresh_callback
            if cb:
                GLib.timeout_add(300, cb)

    def _connect_to_saved_network(self):
        self._set_connecting_state(True)
        self.network_client.connect_to_saved_network(
            self.network_data.get("ssid"),
            self._on_connection_success,
            self._on_connection_error
        )


class NetworkConnections(Box):
    __slots__ = (
        'network_client', 'widgets', '_refresh_timeout_id', '_scan_in_progress',
        'download_label', 'upload_label', 'download_icon', 'upload_icon',
        'download_box', 'upload_box', 'scan_label', 'scan_button', 'back_button',
        'saved_box', 'available_box', 'scrolled_window',
        '_last_bytes_recv', '_last_bytes_sent', '_speed_units'
    )
    
    _SPEED_UNITS = ((1048576, "MB/s"), (1024, "KB/s"), (1, "B/s"))
    _REFRESH_DELAY = 500
    _SCAN_DURATION = 3000
    _SPEED_UPDATE_INTERVAL = 1000

    def __init__(self, **kwargs):
        super().__init__(
            name="network-connections", 
            spacing=4, 
            orientation="vertical", 
            **kwargs
        )
        
        self.widgets = kwargs["widgets"]
        self._refresh_timeout_id = None
        self._scan_in_progress = False
        
        # Инициализация счётчиков скорости
        counters = psutil.net_io_counters()
        self._last_bytes_recv = counters.bytes_recv
        self._last_bytes_sent = counters.bytes_sent
        
        try:
            self.network_client = NetworkClient()
        except Exception:
            self.network_client = type('FallbackClient', (), {
                'wifi_device': None,
                'is_network_available': lambda s, x: False,
                '_client': None,
                'connect': lambda s, *a: None,
            })()

        self._create_ui()
        
        GLib.timeout_add(self._SPEED_UPDATE_INTERVAL, self._update_speeds)

        nc = self.network_client
        if hasattr(nc, 'connect'):
            try:
                nc.connect("device-ready", self._on_device_ready)
            except Exception:
                pass
        
        if hasattr(nc, 'wifi_device') and nc.wifi_device:
            GLib.idle_add(self._on_device_ready)

    def _create_ui(self):
        self.download_label = Label(name="download-label", markup="0 B/s")
        self.upload_label = Label(name="upload-label", markup="0 B/s")
        self.download_icon = Label(name="download-icon-label", markup=icons.download)
        self.upload_icon = Label(name="upload-icon-label", markup=icons.upload)
        
        self.download_box = Box(
            orientation=Gtk.Orientation.HORIZONTAL, 
            spacing=4,
            children=[self.download_icon, self.download_label]
        )
        self.upload_box = Box(
            orientation=Gtk.Orientation.HORIZONTAL, 
            spacing=4,
            children=[self.upload_label, self.upload_icon]
        )

        self.scan_label = Label(name="network-scan-label", markup=icons.radar)
        self.scan_button = Button(
            name="network-scan", 
            child=self.scan_label,
            tooltip_text="Сканировать Wi-Fi сети"
        )
        self.scan_button.connect("clicked", self._on_scan_clicked)
        
        self.back_button = Button(
            name="network-back",
            child=Label(name="network-back-label", markup=icons.chevron_left)
        )
        self.back_button.connect("clicked", lambda *_: self.widgets.show_notif())

        self.saved_box = Box(name="saved-box", spacing=2, orientation="vertical")
        self.available_box = Box(name="available-box", spacing=2, orientation="vertical")

        content_box = Box(spacing=4, orientation="vertical")
        content_box.add(Label(name="network-section", label="Сохраненные сети"))
        content_box.add(self.saved_box)
        content_box.add(Label(name="network-section", label="Доступные сети"))
        content_box.add(self.available_box)

        center_content = Box(
            orientation=Gtk.Orientation.HORIZONTAL, 
            spacing=12, 
            h_align="center",
            children=[
                Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, v_align="center", 
                    children=[self.upload_box]),
                Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, v_align="center",
                    children=[Label(name="network-text", label="Wi-Fi")]),
                Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, v_align="center",
                    children=[self.download_box]),
            ]
        )

        self.scrolled_window = ScrolledWindow(
            name="bluetooth-devices",
            min_content_size=(-1, -1),
            child=content_box,
            v_expand=True,
            propagate_width=False,
            propagate_height=False
        )

        self.children = [
            CenterBox(
                name="network-header",
                start_children=self.back_button,
                center_children=center_content,
                end_children=self.scan_button
            ),
            self.scrolled_window,
        ]

    def _format_speed(self, speed):
        for threshold, unit in self._SPEED_UNITS:
            if speed >= threshold:
                if threshold == 1:
                    return f"{int(speed)} {unit}"
                return f"{speed / threshold:.1f} {unit}"
        return "0 B/s"

    def _update_speeds(self):
        try:
            counters = psutil.net_io_counters()
            
            download = counters.bytes_recv - self._last_bytes_recv
            upload = counters.bytes_sent - self._last_bytes_sent
            
            self._last_bytes_recv = counters.bytes_recv
            self._last_bytes_sent = counters.bytes_sent
            
            self.download_label.set_markup(self._format_speed(download))
            self.upload_label.set_markup(self._format_speed(upload))
        except Exception:
            pass
        return True

    def _on_device_ready(self, client=None):
        nc = self.network_client
        if hasattr(nc, 'wifi_device') and nc.wifi_device:
            dev = nc.wifi_device
            if hasattr(dev, 'connect'):
                try:
                    dev.connect("changed", self._schedule_refresh)
                except Exception:
                    pass
            self._schedule_refresh()

    def _schedule_refresh(self, device=None):
        if not self._refresh_timeout_id:
            self._refresh_timeout_id = GLib.timeout_add(
                self._REFRESH_DELAY, 
                self._do_refresh
            )

    def _do_refresh(self):
        self._refresh_timeout_id = None
        self._refresh_networks()
        self._update_external_widgets()
        return False

    def _force_refresh(self):
        self._refresh_networks()
        self._update_external_widgets()
        return False

    def _update_external_widgets(self):
        nc = self.network_client
        current_ssid = self._get_current_ssid()
        
        dev = nc.wifi_device if hasattr(nc, 'wifi_device') else None
        strength = getattr(dev, 'strength', 0) if dev else 0
        enabled = bool(dev and getattr(dev, 'enabled', False))
        
        if not enabled:
            display_text = "Выключено"
        elif current_ssid:
            display_text = current_ssid
        else:
            display_text = "Не подключено"
        
        if hasattr(self.widgets, 'update_network_display'):
            self.widgets.update_network_display(display_text, strength, enabled)

    def _on_scan_clicked(self, button):
        if self._scan_in_progress:
            return
            
        self._scan_in_progress = True
        
        ctx = self.scan_label.get_style_context()
        btn_ctx = self.scan_button.get_style_context()
        
        ctx.add_class("scanning")
        btn_ctx.add_class("scanning")
        self.scan_button.set_tooltip_text("Сканирование...")
        self.scan_button.set_sensitive(False)
        
        nc = self.network_client
        if hasattr(nc, 'wifi_device') and nc.wifi_device:
            dev = nc.wifi_device
            if getattr(dev, 'enabled', False):
                try:
                    dev.scan()
                except Exception:
                    pass
                
        GLib.timeout_add(self._SCAN_DURATION, self._reset_scan_button)

    def _reset_scan_button(self):
        ctx = self.scan_label.get_style_context()
        btn_ctx = self.scan_button.get_style_context()
        
        ctx.remove_class("scanning")
        btn_ctx.remove_class("scanning")
        self.scan_button.set_tooltip_text("Сканировать Wi-Fi сети")
        self.scan_button.set_sensitive(True)
        self._scan_in_progress = False
        return False

    def _get_saved_networks(self):
        saved = []
        nc = self.network_client
        
        if not hasattr(nc, '_client') or not nc._client:
            return saved
            
        try:
            for conn in nc._client.get_connections():
                if conn.get_connection_type() != '802-11-wireless':
                    continue
                    
                setting = conn.get_setting_wireless()
                if not setting:
                    continue
                    
                ssid_data = setting.get_ssid()
                if not ssid_data:
                    continue
                    
                ssid = NM.utils_ssid_to_utf8(ssid_data.get_data())
                if ssid and ssid not in saved:
                    saved.append(ssid)
        except Exception:
            pass
            
        return saved

    def _get_current_ssid(self):
        nc = self.network_client
        
        if not hasattr(nc, 'wifi_device') or not nc.wifi_device:
            return None
            
        dev = nc.wifi_device
        if not hasattr(dev, 'ssid'):
            return None
            
        ssid = dev.ssid
        if ssid in ("Disconnected", "Выключено", "Не подключено", None):
            return None
            
        return ssid

    def _get_scroll_position(self):
        try:
            vadj = self.scrolled_window.get_vadjustment()
            return vadj.get_value() if vadj else 0
        except Exception:
            return 0

    def _restore_scroll_position(self, position):
        def restore():
            try:
                vadj = self.scrolled_window.get_vadjustment()
                if vadj:
                    vadj.set_value(position)
            except Exception:
                pass
            return False
        GLib.idle_add(restore)

    def _refresh_networks(self):
        # Не обновляем при открытом вводе пароля
        if WifiNetworkSlot._active_pw_block is not None:
            return
    
        scroll_pos = self._get_scroll_position()
        
        current_ssid = self._get_current_ssid()
        saved_networks = self._get_saved_networks()
        
        nc = self.network_client
        dev = nc.wifi_device if hasattr(nc, 'wifi_device') else None
        enabled = bool(dev and getattr(dev, 'enabled', False))
        
        # Получаем доступные сети
        available = []
        if enabled and dev:
            try:
                available = dev.access_points
            except Exception:
                pass
        
        # Очищаем контейнеры
        saved_box = self.saved_box
        avail_box = self.available_box
        
        for child in saved_box.get_children():
            child.destroy()
        for child in avail_box.get_children():
            child.destroy()
        
        # Индексируем доступные сети
        available_dict = {ap.get("ssid"): ap for ap in available if ap.get("ssid")}
        
        refresh_cb = self._force_refresh
        toplevel = self.get_toplevel()
        
        default_ap = {
            "strength": 0, 
            "is_secured": True,
            "icon-name": "network-wireless-signal-none-symbolic"
        }
        
        # Текущая подключенная сеть
        if current_ssid and current_ssid in saved_networks:
            ap_data = available_dict.get(current_ssid)
            if not ap_data:
                ap_data = {"ssid": current_ssid, **default_ap}
                ap_data["icon-name"] = "network-wireless-signal-excellent-symbolic"
            
            saved_box.add(WifiNetworkSlot(
                ap_data, nc, toplevel,
                refresh_callback=refresh_cb,
                is_saved=True, 
                is_connected=True
            ))

        # Остальные сохранённые сети
        for ssid in saved_networks:
            if ssid == current_ssid:
                continue
                
            ap_data = available_dict.get(ssid)
            if not ap_data:
                ap_data = {"ssid": ssid, **default_ap}
            
            saved_box.add(WifiNetworkSlot(
                ap_data, nc, toplevel,
                refresh_callback=refresh_cb,
                is_saved=True, 
                is_connected=False
            ))

        # Доступные несохранённые сети
        saved_set = set(saved_networks)
        for ap_data in available:
            ssid = ap_data.get("ssid")
            if ssid and ssid not in saved_set and ssid != current_ssid:
                avail_box.add(WifiNetworkSlot(
                    ap_data, nc, toplevel,
                    refresh_callback=refresh_cb,
                    is_saved=False, 
                    is_connected=False
                ))

        saved_box.show_all()
        avail_box.show_all()
        
        self._restore_scroll_position(scroll_pos)