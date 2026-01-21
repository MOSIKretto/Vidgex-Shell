from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow

from gi.repository import Gtk, NM, GLib

import modules.icons as icons
from services.network import NetworkClient


class WifiNetworkSlot(CenterBox):
    __slots__ = ('network_data', 'network_client', 'parent_window', 'refresh_callback',
                 'is_saved', 'is_connected', 'pw_block', '_connect_btn')
    
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
        self._connect_btn = None
        
        ssid = network_data.get("ssid", "Unknown")
        
        # Создаём виджеты одним блоком
        self.start_children = Box(
            spacing=8, h_expand=True, h_align="fill",
            children=[
                Image(icon_name=network_data.get("icon-name", "network-wireless-signal-none-symbolic"), size=16),
                Label(label=ssid, h_expand=True, h_align="start", ellipsization="end"),
                Label(label=f"{network_data.get('strength', 0)}%")
            ]
        )
        
        self.end_children = self._create_actions()
        network_client.connect("connection-error", self._on_error)

    def _on_error(self, client, err_ssid, msg):
        if err_ssid == self.network_data.get("ssid") and self._connect_btn:
            self._show_error()

    def _create_actions(self):
        nc = self.network_client
        ssid = self.network_data.get("ssid")
        is_available = hasattr(nc, 'is_network_available') and ssid and nc.is_network_available(ssid)
        
        widgets = []
        
        if self.is_connected:
            widgets = [
                Label(label="Подключено", name="wifi-connected-label"),
                self._make_delete_btn()
            ]
        elif self.is_saved:
            if not is_available:
                widgets = [self._make_unavail_btn(), self._make_delete_btn()]
            else:
                self._connect_btn = self._make_connect_btn()
                widgets = [self._connect_btn, self._make_delete_btn()]
        elif is_available:
            self._connect_btn = self._make_connect_btn()
            widgets = [self._connect_btn]
        else:
            widgets = [self._make_unavail_btn()]
            
        return Box(orientation="horizontal", spacing=4, children=widgets)

    def _make_connect_btn(self):
        btn = Button(name="wifi-connect", label="Подключиться")
        btn.connect("clicked", self._on_connect)
        return btn

    def _make_delete_btn(self):
        btn = Button(
            name="wifi-delete",
            child=Label(name="wifi-delete-label", markup=icons.trash),
            tooltip_text="Удалить сеть"
        )
        btn.connect("clicked", self._on_delete)
        return btn

    def _make_unavail_btn(self):
        btn = Button(name="wifi-unavailable", label="Недоступна", sensitive=False, can_focus=False)
        btn.get_style_context().add_class("unavailable-network")
        return btn

    def _on_connect(self, btn):
        if self.is_saved:
            btn.set_sensitive(False)
            btn.set_label("Подключение...")
            self.network_client.connect_to_saved_network(
                self.network_data.get("ssid"),
                self._on_success,
                self._on_fail
            )
        else:
            self._toggle_pw_input()

    def _on_fail(self, *args):
        self._show_error()

    def _toggle_pw_input(self):
        parent = self.get_parent()
        if not parent:
            return

        cls = WifiNetworkSlot
        
        if cls._active_slot is self:
            self._close_pw()
            return

        if cls._active_pw_block:
            cls._active_slot._close_pw()

        pw_block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        pw_block.set_name("password-entry-container")
        pw_block.set_margin_top(4)
        pw_block.set_margin_bottom(12)
        pw_block.set_margin_start(10)
        pw_block.set_margin_end(10)

        entry = Gtk.Entry(visibility=False, invisible_char='•', placeholder_text="Пароль...")
        
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        cancel = Gtk.Button(label="Отмена")
        ok = Gtk.Button(label="ОК")
        ok.set_sensitive(False)

        cancel.connect("clicked", lambda _: self._close_pw())
        ok.connect("clicked", lambda _: self._submit_pw(entry.get_text()))
        entry.connect("changed", lambda e: ok.set_sensitive(bool(e.get_text())))
        entry.connect("activate", lambda e: ok.clicked() if ok.get_sensitive() else None)

        btn_box.pack_start(cancel, True, True, 0)
        btn_box.pack_start(ok, True, True, 0)
        pw_block.pack_start(entry, False, False, 0)
        pw_block.pack_start(btn_box, False, False, 0)

        self.pw_block = pw_block
        cls._active_pw_block = pw_block
        cls._active_slot = self

        children = parent.get_children()
        parent.pack_start(pw_block, False, False, 0)
        parent.reorder_child(pw_block, children.index(self) + 1)
        
        pw_block.show_all()
        GLib.idle_add(entry.grab_focus)

    def _close_pw(self):
        cls = WifiNetworkSlot
        if cls._active_pw_block:
            cls._active_pw_block.destroy()
            cls._active_pw_block = None
            cls._active_slot = None
        self.pw_block = None

    def _submit_pw(self, password):
        self._close_pw()
        if self._connect_btn:
            self._connect_btn.set_sensitive(False)
            self._connect_btn.set_label("Подключение...")
        self.network_client.connect_to_new_network(
            self.network_data.get("ssid"),
            password,
            self._on_success,
            self._show_error
        )

    def _on_success(self, ssid):
        if self.refresh_callback:
            GLib.timeout_add(500, self.refresh_callback)

    def _show_error(self):
        if self._connect_btn:
            self._connect_btn.set_label("Ошибка")
            self._connect_btn.set_sensitive(False)
            GLib.timeout_add(3000, self._restore_btn)

    def _restore_btn(self):
        if self._connect_btn:
            self._connect_btn.set_label("Подключиться")
            self._connect_btn.set_sensitive(True)
        return False

    def _on_delete(self, btn):
        ssid = self.network_data.get("ssid")
        if ssid:
            self.network_client.delete_saved_network(ssid)
            if self.refresh_callback:
                GLib.timeout_add(300, self.refresh_callback)


class NetworkConnections(Box):
    __slots__ = ('network_client', 'widgets', '_refresh_id', '_scan_active',
                 'dl_label', 'ul_label', 'scan_label', 'scan_btn', 'saved_box',
                 'avail_box', 'scroll', '_last_recv', '_last_sent')
    
    _UNITS = ((1048576, "MB/s"), (1024, "KB/s"), (1, "B/s"))

    def __init__(self, **kwargs):
        super().__init__(name="network-connections", spacing=4, orientation="vertical", **kwargs)
        
        self.widgets = kwargs["widgets"]
        self._refresh_id = None
        self._scan_active = False
        
        self._last_recv, self._last_sent = self._get_net_io_counters()
        
        try:
            self.network_client = NetworkClient()
        except Exception:
            self.network_client = type('Fallback', (), {
                'wifi_device': None,
                'is_network_available': lambda s, x: False,
                '_client': None,
                'connect': lambda s, *a: None,
            })()

        self._build_ui()
        
        GLib.timeout_add(1000, self._update_speeds)

        nc = self.network_client
        if hasattr(nc, 'connect'):
            try:
                nc.connect("device-ready", self._on_ready)
            except:
                pass
        
        if hasattr(nc, 'wifi_device') and nc.wifi_device:
            GLib.idle_add(self._on_ready)

    def _get_net_io_counters(self):
        """Получить счетчики сетевого трафика без psutil"""
        recv = 0
        sent = 0
        try:
            with open('/proc/net/dev', 'r') as f:
                # Пропускаем первые две строки заголовков
                lines = f.readlines()[2:]
                for line in lines:
                    parts = line.split()
                    # parts[0] - интерфейс (lo:, eth0:, wlan0:)
                    if parts[0].strip().rstrip(':') == 'lo':
                        continue
                    
                    # parts[1] - bytes recv
                    # parts[9] - bytes sent
                    if len(parts) >= 10:
                        recv += int(parts[1])
                        sent += int(parts[9])
        except Exception:
            pass
        return recv, sent

    def _build_ui(self):
        self.dl_label = Label(name="download-label", markup="0 B/s")
        self.ul_label = Label(name="upload-label", markup="0 B/s")
        
        self.scan_label = Label(name="network-scan-label", markup=icons.radar)
        self.scan_btn = Button(
            name="network-scan",
            child=self.scan_label,
            tooltip_text="Сканировать Wi-Fi сети"
        )
        self.scan_btn.connect("clicked", self._on_scan)
        
        back_btn = Button(
            name="network-back",
            child=Label(name="network-back-label", markup=icons.chevron_left)
        )
        back_btn.connect("clicked", lambda *_: self.widgets.show_notif())

        self.saved_box = Box(name="saved-box", spacing=2, orientation="vertical")
        self.avail_box = Box(name="available-box", spacing=2, orientation="vertical")

        content = Box(spacing=4, orientation="vertical")
        content.add(Label(name="network-section", label="Сохраненные сети"))
        content.add(self.saved_box)
        content.add(Label(name="network-section", label="Доступные сети"))
        content.add(self.avail_box)

        center = Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
            h_align="center",
            children=[
                Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, v_align="center",
                    children=[self.ul_label, Label(name="upload-icon-label", markup=icons.upload)]),
                Label(name="network-text", label="Wi-Fi", v_align="center"),
                Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, v_align="center",
                    children=[Label(name="download-icon-label", markup=icons.download), self.dl_label]),
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
                start_children=back_btn,
                center_children=center,
                end_children=self.scan_btn
            ),
            self.scroll,
        ]

    def _format_speed(self, speed):
        for threshold, unit in self._UNITS:
            if speed >= threshold:
                return f"{speed / threshold:.1f} {unit}" if threshold > 1 else f"{int(speed)} {unit}"
        return "0 B/s"

    def _update_speeds(self):
        try:
            curr_recv, curr_sent = self._get_net_io_counters()
            
            dl = curr_recv - self._last_recv
            ul = curr_sent - self._last_sent
            
            self._last_recv = curr_recv
            self._last_sent = curr_sent
            
            # Если значения отрицательные (перезагрузка/сброс счетчиков), считаем 0
            if dl < 0: dl = 0
            if ul < 0: ul = 0
            
            self.dl_label.set_markup(self._format_speed(dl))
            self.ul_label.set_markup(self._format_speed(ul))
        except:
            pass
        return True

    def _on_ready(self, client=None):
        nc = self.network_client
        if hasattr(nc, 'wifi_device') and nc.wifi_device:
            dev = nc.wifi_device
            if hasattr(dev, 'connect'):
                try:
                    dev.connect("changed", self._schedule_refresh)
                except:
                    pass
            self._schedule_refresh()

    def _schedule_refresh(self, device=None):
        if not self._refresh_id:
            self._refresh_id = GLib.timeout_add(500, self._do_refresh)

    def _do_refresh(self):
        self._refresh_id = None
        self._refresh_nets()
        self._update_widgets()
        return False

    def _force_refresh(self):
        self._refresh_nets()
        self._update_widgets()
        return False

    def _update_widgets(self):
        nc = self.network_client
        current = self._get_current()
        
        dev = nc.wifi_device if hasattr(nc, 'wifi_device') else None
        strength = getattr(dev, 'strength', 0) if dev else 0
        enabled = bool(dev and getattr(dev, 'enabled', False))
        
        if not enabled:
            text = "Выключено"
        elif current:
            text = current
        else:
            text = "Не подключено"
        
        if hasattr(self.widgets, 'update_network_display'):
            self.widgets.update_network_display(text, strength, enabled)

    def _on_scan(self, btn):
        if self._scan_active:
            return
            
        self._scan_active = True
        
        ctx = self.scan_label.get_style_context()
        btn_ctx = btn.get_style_context()
        
        ctx.add_class("scanning")
        btn_ctx.add_class("scanning")
        btn.set_tooltip_text("Сканирование...")
        btn.set_sensitive(False)
        
        nc = self.network_client
        if hasattr(nc, 'wifi_device') and nc.wifi_device:
            dev = nc.wifi_device
            if getattr(dev, 'enabled', False):
                try:
                    dev.scan()
                except:
                    pass

        GLib.timeout_add(3000, self._reset_scan)

    def _reset_scan(self):
        ctx = self.scan_label.get_style_context()
        btn_ctx = self.scan_btn.get_style_context()
        
        ctx.remove_class("scanning")
        btn_ctx.remove_class("scanning")
        self.scan_btn.set_tooltip_text("Сканировать Wi-Fi сети")
        self.scan_btn.set_sensitive(True)
        self._scan_active = False
        return False

    def _get_saved(self):
        saved = []
        nc = self.network_client
        
        if not hasattr(nc, '_client') or not nc._client:
            return saved
            
        try:
            for conn in nc._client.get_connections():
                if conn.get_connection_type() == '802-11-wireless':
                    setting = conn.get_setting_wireless()
                    if setting:
                        ssid_data = setting.get_ssid()
                        if ssid_data:
                            ssid = NM.utils_ssid_to_utf8(ssid_data.get_data())
                            if ssid and ssid not in saved:
                                saved.append(ssid)
        except:
            pass
            
        return saved

    def _get_current(self):
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

    def _get_scroll(self):
        try:
            vadj = self.scroll.get_vadjustment()
            return vadj.get_value() if vadj else 0
        except:
            return 0

    def _set_scroll(self, pos):
        def restore():
            try:
                vadj = self.scroll.get_vadjustment()
                if vadj:
                    vadj.set_value(pos)
            except:
                pass
            return False
        GLib.idle_add(restore)

    def _refresh_nets(self):
        # Не обновляем при открытом вводе пароля
        if WifiNetworkSlot._active_pw_block is not None:
            return
    
        scroll_pos = self._get_scroll()
        
        current = self._get_current()
        saved = self._get_saved()
        
        nc = self.network_client
        dev = nc.wifi_device if hasattr(nc, 'wifi_device') else None
        enabled = bool(dev and getattr(dev, 'enabled', False))
        
        # Получаем доступные сети
        available = []
        if enabled and dev:
            try:
                available = dev.access_points
            except:
                pass
        
        # Freeze для оптимизации
        saved_box = self.saved_box
        avail_box = self.avail_box
        
        saved_box.freeze_child_notify()
        avail_box.freeze_child_notify()
        
        # Очищаем
        for child in saved_box.get_children():
            child.destroy()
        for child in avail_box.get_children():
            child.destroy()
        
        # Индексируем доступные
        avail_dict = {ap.get("ssid"): ap for ap in available if ap.get("ssid")}
        
        refresh_cb = self._force_refresh
        toplevel = self.get_toplevel()
        
        default_ap = {
            "strength": 0,
            "is_secured": True,
            "icon-name": "network-wireless-signal-none-symbolic"
        }
        
        # Текущая подключенная
        if current and current in saved:
            ap = avail_dict.get(current)
            if not ap:
                ap = {"ssid": current, **default_ap}
                ap["icon-name"] = "network-wireless-signal-excellent-symbolic"
            
            saved_box.add(WifiNetworkSlot(
                ap, nc, toplevel,
                refresh_callback=refresh_cb,
                is_saved=True,
                is_connected=True
            ))

        # Остальные сохранённые
        for ssid in saved:
            if ssid == current:
                continue
                
            ap = avail_dict.get(ssid, {"ssid": ssid, **default_ap})
            
            saved_box.add(WifiNetworkSlot(
                ap, nc, toplevel,
                refresh_callback=refresh_cb,
                is_saved=True,
                is_connected=False
            ))

        # Доступные несохранённые
        saved_set = set(saved)
        for ap in available:
            ssid = ap.get("ssid")
            if ssid and ssid not in saved_set and ssid != current:
                avail_box.add(WifiNetworkSlot(
                    ap, nc, toplevel,
                    refresh_callback=refresh_cb,
                    is_saved=False,
                    is_connected=False
                ))

        saved_box.thaw_child_notify()
        avail_box.thaw_child_notify()
        
        saved_box.show_all()
        avail_box.show_all()
        
        self._set_scroll(scroll_pos)