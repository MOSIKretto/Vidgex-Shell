from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow

import gi
from gi.repository import Gtk, NM, GLib
gi.require_version('Gtk', '3.0')
gi.require_version('NM', '1.0')

import time
import psutil

import modules.icons as icons
from services.network import NetworkClient

class WifiNetworkSlot(CenterBox):
    active_pw_block = None
    active_slot = None

    def __init__(self, network_data, network_client, parent_window, is_saved=False, is_connected=False, **kwargs):
        super().__init__(name="wifi-network-slot", **kwargs)
        self.network_data, self.network_client, self.parent_window = network_data, network_client, parent_window
        self.is_saved, self.is_connected = is_saved, is_connected
        
        ssid = network_data.get("ssid", "Unknown")
        strength = network_data.get("strength", 0)
        
        self.network_icon = Image(icon_name=network_data.get("icon-name", "network-wireless-signal-none-symbolic"), size=16)
        self.network_label = Label(label=ssid, h_expand=True, h_align="start", ellipsization="end")
        self.strength_label = Label(label=f"{strength}%")
        
        self.connect_button = Button(name="wifi-connect", label="Подключиться", on_clicked=self.on_connect_clicked)
        self.delete_button = Button(name="wifi-delete", child=Label(name="wifi-delete-label", markup=icons.trash),
                                  on_clicked=self.on_delete_clicked, tooltip_text="Удалить сеть")
        self.connected_label = Label(label="Подключено", name="wifi-connected-label")
        self.unavailable_button = Button(name="wifi-unavailable", label="Недоступна", sensitive=False, can_focus=False)
        self.unavailable_button.add_style_class("unavailable-network")
        
        self.start_children = Box(spacing=8, h_expand=True, h_align="fill", children=[
            self.network_icon, self.network_label, self.strength_label])
        
        self._setup_action_widgets()
        
        # Анимации нажатия
        self.connect_button.connect('button-press-event', self._on_button_press)
        self.delete_button.connect('button-press-event', self._on_delete_button_press)
        
        # Слушаем глобальные ошибки, чтобы сбросить кнопку, если ошибка пришла асинхронно
        self.network_client.connect("connection-error", self._on_global_error)

    def _on_global_error(self, client, err_ssid, message):
        """Обработка сигнала ошибки от сервиса"""
        if err_ssid == self.network_data.get("ssid"):
            self._show_error("Ошибка")

    def _setup_action_widgets(self):
        ssid = self.network_data.get("ssid")
        is_available = self.network_client.is_network_available(ssid) if hasattr(self.network_client, 'is_network_available') and ssid else False
        
        if self.is_connected:
            action_widgets = [self.connected_label, self.delete_button]
        elif not is_available and self.is_saved:
            action_widgets = [self.unavailable_button, self.delete_button]
        elif not is_available:
            action_widgets = [self.unavailable_button]
        elif self.is_saved:
            action_widgets = [self.connect_button, self.delete_button]
        else:
            action_widgets = [self.connect_button]
            
        self.end_children = Box(orientation="horizontal", spacing=4, children=action_widgets)

    def _on_button_press(self, widget, event):
        widget.add_style_class('pressed')
        GLib.timeout_add(150, lambda: widget.remove_style_class('pressed') if widget else False)
        return False

    def _on_delete_button_press(self, widget, event):
        widget.add_style_class('pressed')
        GLib.timeout_add(150, lambda: widget.remove_style_class('pressed') if widget else False)
        return False

    def on_connect_clicked(self, button):
        if self.is_saved: 
            self._connect_to_saved_network(self.network_data.get("ssid"))
        else: 
            self._toggle_password_input()

    def _toggle_password_input(self):
        parent_box = self.get_parent()
        if not parent_box: return

        if WifiNetworkSlot.active_slot == self:
            self._close_active_password_block()
            return

        if WifiNetworkSlot.active_pw_block:
            WifiNetworkSlot.active_slot._close_active_password_block()

        self.pw_block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.pw_block.set_name("password-entry-container")
        self.pw_block.set_margin_top(4)
        self.pw_block.set_margin_bottom(12)
        self.pw_block.set_margin_start(10)
        self.pw_block.set_margin_end(10)

        entry = Gtk.Entry(visibility=False, invisible_char='•', placeholder_text="Пароль...")
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        cancel_btn = Gtk.Button(label="Отмена")
        ok_btn = Gtk.Button(label="ОК")
        ok_btn.set_sensitive(False)

        cancel_btn.connect("clicked", lambda _: self._close_active_password_block())
        ok_btn.connect("clicked", lambda _: self._on_password_entered(self.network_data.get("ssid"), entry.get_text()))
        entry.connect("changed", lambda e: ok_btn.set_sensitive(len(e.get_text()) > 0))
        entry.connect("activate", lambda e: ok_btn.clicked() if ok_btn.get_sensitive() else None)

        btn_box.pack_start(cancel_btn, True, True, 0)
        btn_box.pack_start(ok_btn, True, True, 0)
        self.pw_block.pack_start(entry, False, False, 0)
        self.pw_block.pack_start(btn_box, False, False, 0)

        WifiNetworkSlot.active_pw_block = self.pw_block
        WifiNetworkSlot.active_slot = self

        children = parent_box.get_children()
        idx = children.index(self)
        parent_box.pack_start(self.pw_block, False, False, 0)
        parent_box.reorder_child(self.pw_block, idx + 1)
        
        self.pw_block.show_all()
        GLib.idle_add(lambda: entry.grab_focus())

    def _close_active_password_block(self):
        if WifiNetworkSlot.active_pw_block:
            WifiNetworkSlot.active_pw_block.destroy()
            WifiNetworkSlot.active_pw_block = None
            WifiNetworkSlot.active_slot = None

    def _on_password_entered(self, ssid, password):
        self._close_active_password_block()
        self._set_connecting_state(True)
        # Логика теперь в NetworkClient: если пароль неверный, сеть удалится из сохраненных и придет ошибка
        self.network_client.connect_to_new_network(ssid, password, 
                                                 self._on_connection_success, 
                                                 self._on_connection_error_callback)

    def _set_connecting_state(self, connecting):
        if connecting: 
            self.connect_button.set_sensitive(False)
            self.connect_button.set_label("Подключение...")
        else: 
            self.connect_button.set_sensitive(True)
            self.connect_button.set_label("Подключиться")

    def _on_connection_success(self, ssid):
        self._set_connecting_state(False)

    def _on_connection_error_callback(self, ssid, error_message): 
        """Коллбэк при прямой ошибке вызова"""
        self._show_error("Ошибка")

    def _show_error(self, error_message):
        self._set_connecting_state(False)
        self.connect_button.set_label(error_message)
        self.connect_button.set_sensitive(False)
        # Возвращаем кнопку в исходное состояние через 3 секунды
        GLib.timeout_add(3000, self._restore_connect_button)

    def _restore_connect_button(self):
        if self.connect_button:
            self.connect_button.set_label("Подключиться")
            self.connect_button.set_sensitive(True)
        return False

    def on_delete_clicked(self, button):
        ssid = self.network_data.get("ssid")
        if ssid: self.network_client.delete_saved_network(ssid)

    def _connect_to_saved_network(self, ssid):
        self._set_connecting_state(True)
        self.network_client.connect_to_saved_network(ssid, 
                                                   self._on_connection_success, 
                                                   self._on_connection_error_callback)

class NetworkConnections(Box):
    def __init__(self, **kwargs):
        super().__init__(name="network-connections", spacing=4, orientation="vertical", **kwargs)
        
        try:
            self.network_client = NetworkClient()
        except Exception:
            self.network_client = type('FallbackNetworkClient', (), {
                'wifi_device': None,
                'is_network_available': lambda x: False,
            })()
            
        self.widgets = kwargs["widgets"]
        self._refresh_timeout_id, self._periodic_refresh_id = None, None
        self._refresh_delay, self._scan_click_in_progress, self._cached_networks = 500, False, None

        # Создаем метки для скорости с цветовым выделением
        self.download_label = Label(name="download-label", markup="0 Б/с")
        self.upload_label = Label(name="upload-label", markup="0 Б/с")
        
        # Иконки для скачивания и отправки
        self.download_icon = Label(name="download-icon-label", markup=icons.download)
        self.upload_icon = Label(name="upload-icon-label", markup=icons.upload)
        
        # Контейнеры для скоростей (только иконки + метки, без текста)
        self.download_box = Box(
            orientation=Gtk.Orientation.HORIZONTAL, 
            spacing=4,
            children=[self.download_icon, self.download_label]
        )
        self.upload_box = Box(
            orientation=Gtk.Orientation.HORIZONTAL, 
            spacing=4,
            children=[self.upload_label, self.upload_icon]  # upload слева, download справа
        )

        self.scan_label = Label(name="network-scan-label", markup=icons.radar)
        self.scan_button = Button(name="network-scan", child=self.scan_label, tooltip_text="Сканировать Wi-Fi сети", on_clicked=self.on_scan_clicked)
        self.back_button = Button(name="network-back", child=Label(name="network-back-label", markup=icons.chevron_left), on_clicked=lambda *_: self.widgets.show_notif())

        self.saved_box = Box(name="saved-box", spacing=2, orientation="vertical")
        self.available_box = Box(name="available-box", spacing=2, orientation="vertical")

        content_box = Box(spacing=4, orientation="vertical")
        content_box.add(Label(name="network-section", label="Сохраненные сети"))
        content_box.add(self.saved_box)
        content_box.add(Label(name="network-section", label="Доступные сети"))
        content_box.add(self.available_box)

        # Создаем центральный контейнер - компактный вариант
        center_content = Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12, h_align="center")
        
        # Левая часть: отправка (только иконка + значение)
        left_box = Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, v_align="center")
        left_box.add(self.upload_box)
        
        # Центральная часть: WiFi сети
        center_box = Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, v_align="center")
        center_box.add(Label(name="network-text", label="Wi-Fi"))
        
        # Правая часть: скачивание (только иконка + значение)
        right_box = Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, v_align="center")
        right_box.add(self.download_box)

        # Подключаем обработчики нажатия к кнопкам
        self.scan_button.connect('button-press-event', self._on_button_press)
        self.back_button.connect('button-press-event', self._on_button_press)
            
        # Добавляем все в центральный контейнер
        center_content.add(left_box)
        center_content.add(center_box)
        center_content.add(right_box)

        self.children = [
            CenterBox(
                name="network-header", 
                start_children=self.back_button, 
                center_children=center_content,
                end_children=self.scan_button
            ),
            ScrolledWindow(
                name="bluetooth-devices", 
                min_content_size=(-1, -1), 
                child=content_box,
                v_expand=True, 
                propagate_width=False, 
                propagate_height=False
            ),
        ]

        # Инициализация мониторинга сети
        self.last_counters = None
        self.last_time = time.time()
        self._init_network_counters()
        
        # Запуск обновления скоростей
        self._speed_timer_id = GLib.timeout_add(1000, self.update_network_speeds)

        if hasattr(self.network_client, 'connect'):
            try:
                self.network_client.connect("device-ready", self.on_device_ready)
            except Exception:
                pass
        
        if hasattr(self.network_client, 'wifi_device') and self.network_client.wifi_device: 
            GLib.idle_add(self.on_device_ready)

    def _on_button_press(self, widget, event):
        """Обработчик нажатия кнопки с анимацией"""
        # Добавляем класс для анимации нажатия
        widget.add_style_class('pressed')
        
        # Удаляем класс через 150мс для повторной анимации
        GLib.timeout_add(150, lambda: widget.remove_style_class('pressed') if widget else False)
        
        return False 

    def _init_network_counters(self):
        """Инициализация счетчиков сети"""
        try:
            self.last_counters = psutil.net_io_counters()
            self.last_time = time.time()
        except ImportError:
            self.last_counters = None
            print("Ошибка: psutil не установлен. Установите: pip install psutil")

    def update_network_speeds(self):
        if self.last_counters is None:
            self._init_network_counters()
            return True

        try:
            current_time = time.time()
            elapsed = current_time - self.last_time
            current_counters = psutil.net_io_counters()

            if elapsed > 0:
                download_speed = (current_counters.bytes_recv - self.last_counters.bytes_recv) / elapsed
                upload_speed = (current_counters.bytes_sent - self.last_counters.bytes_sent) / elapsed

                download_str = self.format_speed(download_speed)
                upload_str = self.format_speed(upload_speed)

                self.download_label.set_markup(download_str)
                self.upload_label.set_markup(upload_str)

                tooltip_text = f"Скачивание: {download_str}\nОтправка: {upload_str}"
                self.download_box.set_tooltip_text(tooltip_text)
                self.upload_box.set_tooltip_text(tooltip_text)

            self.last_counters = current_counters
            self.last_time = current_time

        except Exception:
            pass

        return True

    def format_speed(self, speed):
        """Форматирование скорости"""
        if speed < 1024: return f"{speed:.0f} B/s"
        elif speed < 1024 * 1024: return f"{speed / 1024:.1f} KB/s"
        else: return f"{speed / (1024 * 1024):.1f} MB/s"

    def on_device_ready(self, client=None):
        """Обработчик готовности устройства"""
        if hasattr(self.network_client, 'wifi_device') and self.network_client.wifi_device:
            if hasattr(self.network_client.wifi_device, 'connect'):
                try:
                    self.network_client.wifi_device.connect("changed", self._schedule_refresh)
                except Exception:
                    pass
            self._start_periodic_refresh()
            self._schedule_refresh()

    def _start_periodic_refresh(self):
        """Запуск периодического обновления"""
        if self._periodic_refresh_id is None:
            self._periodic_refresh_id = GLib.timeout_add(self._refresh_delay, self._do_complete_refresh)

    def _schedule_refresh(self, device=None):
        """Планирование обновления"""
        if not self._refresh_timeout_id:
            self._refresh_timeout_id = GLib.timeout_add(self._refresh_delay, self._do_complete_refresh)

    def _do_complete_refresh(self):
        """Полное обновление"""
        self._refresh_timeout_id = None
        self.refresh_networks()
        self._update_external_widgets()
        return True

    def _update_external_widgets(self):
        """Обновление внешних виджетов"""
        current_ssid = self.get_current_network_ssid()
        strength = self._get_connection_strength()
        enabled = self._is_wifi_enabled()
        
        display_text = current_ssid if current_ssid and current_ssid != "Disconnected" and enabled else "Не подключено"
        if not enabled:
            display_text = "Выключено"
        
        if hasattr(self.widgets, 'update_network_display'):
            self.widgets.update_network_display(display_text, strength, enabled)

    def _is_wifi_enabled(self):
        """Проверка включен ли WiFi"""
        try:
            return bool(self.network_client.wifi_device and getattr(self.network_client.wifi_device, 'enabled', False))
        except Exception:
            return False

    def _get_connection_strength(self):
        """Получение силы сигнала"""
        try:
            return getattr(self.network_client.wifi_device, 'strength', 0) if self.network_client.wifi_device else 0
        except Exception:
            return 0

    def on_scan_clicked(self, button):
        """Обработчик клика по сканированию"""
        if self._scan_click_in_progress: 
            return
        self._scan_click_in_progress = True
        self.scan_label.add_style_class("scanning")
        self.scan_button.add_style_class("scanning")
        self.scan_button.set_tooltip_text("Сканирование...")
        self.scan_button.set_sensitive(False)
        
        if (hasattr(self.network_client, 'wifi_device') and 
            self.network_client.wifi_device and 
            getattr(self.network_client.wifi_device, 'enabled', False)): 
            try:
                self.network_client.wifi_device.scan()
            except Exception:
                pass
                
        GLib.timeout_add(3000, self._reset_scan_button)

    def _reset_scan_button(self):
        """Сброс состояния кнопки сканирования"""
        self.scan_label.remove_style_class("scanning")
        self.scan_button.remove_style_class("scanning")
        self.scan_button.set_tooltip_text("Сканировать Wi-Fi сети")
        self.scan_button.set_sensitive(True)
        self._scan_click_in_progress = False
        return False

    def get_saved_networks(self):
        """Получение сохраненных сетей"""
        saved_networks = []
        try:
            if (hasattr(self.network_client, '_client') and 
                self.network_client._client):
                connections = self.network_client._client.get_connections()
                for connection in connections:
                    if connection.get_connection_type() == '802-11-wireless':
                        setting = connection.get_setting_wireless()
                        if setting and setting.get_ssid():
                            ssid = NM.utils_ssid_to_utf8(setting.get_ssid().get_data())
                            if ssid and ssid not in saved_networks: 
                                saved_networks.append(ssid)
        except Exception:
            pass
        return saved_networks

    def get_current_network_ssid(self):
        """Получение текущего SSID"""
        try:
            if (hasattr(self.network_client, 'wifi_device') and 
                self.network_client.wifi_device and 
                hasattr(self.network_client.wifi_device, 'ssid') and
                self.network_client.wifi_device.ssid and 
                self.network_client.wifi_device.ssid not in ["Disconnected", "Выключено", "Не подключено"]):
                return self.network_client.wifi_device.ssid
        except Exception:
            pass
        return None

    def refresh_networks(self):
        """Обновление списка сетей"""
        if WifiNetworkSlot.active_pw_block is not None:
            return
    
        current_ssid, saved_networks = self.get_current_network_ssid(), self.get_saved_networks()
        enabled = self._is_wifi_enabled()
        
        available_networks = []
        if enabled and hasattr(self.network_client, 'wifi_device') and self.network_client.wifi_device:
            try:
                available_networks = self.network_client.wifi_device.access_points
            except Exception:
                pass
        
        current_state = {
            'current_ssid': current_ssid, 
            'saved_networks': tuple(saved_networks), 
            'available_count': len(available_networks),
            'enabled': enabled
        }
        
        if self._cached_networks == current_state: 
            return
            
        self._cached_networks = current_state
        
        for child in self.saved_box.get_children(): child.destroy()
        for child in self.available_box.get_children(): child.destroy()
        
        available_networks_dict = {ap.get("ssid"): ap for ap in available_networks if ap.get("ssid")}
        self._create_network_slots(current_ssid, saved_networks, available_networks_dict, available_networks)
        
        self.saved_box.show_all()
        self.available_box.show_all()

    def _create_network_slots(self, current_ssid, saved_networks, available_networks_dict, available_networks):
        """Создание слотов сетей"""
        if current_ssid and current_ssid in saved_networks:
            ap_data = available_networks_dict.get(current_ssid, {
                "ssid": current_ssid, 
                "strength": 0, 
                "is_secured": True, 
                "icon-name": "network-wireless-signal-excellent-symbolic"
            })
            self.saved_box.add(WifiNetworkSlot(ap_data, self.network_client, self.get_toplevel(), is_saved=True, is_connected=True))

        for ssid in saved_networks:
            if ssid == current_ssid:
                continue
            ap_data = available_networks_dict.get(ssid, {
                "ssid": ssid, 
                "strength": 0, 
                "is_secured": True, 
                "icon-name": "network-wireless-signal-none-symbolic"
            })
            self.saved_box.add(WifiNetworkSlot(ap_data, self.network_client, self.get_toplevel(), 
                                             is_saved=True, is_connected=False))

        for ap_data in available_networks:
            ssid = ap_data.get("ssid")
            if ssid and ssid not in saved_networks and ssid != current_ssid:
                self.available_box.add(WifiNetworkSlot(ap_data, self.network_client, self.get_toplevel(), 
                                                     is_saved=False, is_connected=False))

    def destroy(self):
        """Очистка ресурсов"""
        if hasattr(self, '_refresh_timeout_id') and self._refresh_timeout_id:
            GLib.source_remove(self._refresh_timeout_id)
            self._refresh_timeout_id = None
        if hasattr(self, '_periodic_refresh_id') and self._periodic_refresh_id:
            GLib.source_remove(self._periodic_refresh_id)
            self._periodic_refresh_id = None
        if hasattr(self, '_speed_timer_id') and self._speed_timer_id:
            GLib.source_remove(self._speed_timer_id)
            self._speed_timer_id = None
        
        # Disconnect wifi device signals
        if hasattr(self, 'network_client') and hasattr(self.network_client, 'wifi_device'):
            if hasattr(self.network_client.wifi_device, 'destroy'):
                try:
                    self.network_client.wifi_device.destroy()
                except:
                    pass
        
        # Clear references
        self.network_client = None
        self.last_counters = None
        self._cached_networks = None
        
        super().destroy()
    
    def __del__(self):
        """Деструктор"""
        try: self.destroy()
        except: pass