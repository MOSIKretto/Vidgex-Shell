import os
import threading
from gi.repository import GLib, Gtk

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label
from fabric.bluetooth import BluetoothClient

import services.icons as icons

from modules.Notch.MainWindow.Dashboard.Buttons.caffeine import Caffeine
from modules.Notch.MainWindow.Dashboard.Buttons.workTime import WorkTime
from modules.Notch.MainWindow.Dashboard.Buttons.timer import _TimerSplitButton, _dis, _content


_TH = (25, 50, 75)
_WI = (icons.wifi_0, icons.wifi_1, icons.wifi_2, icons.wifi_3)
_AN = (icons.wifi_0, icons.wifi_1, icons.wifi_2, icons.wifi_3, icons.wifi_2, icons.wifi_1)


def _proc_running(name: str) -> bool:
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/comm", "r") as f:
                if f.read().strip() == name:
                    return True
        except FileNotFoundError:
            continue
    return False


def _async_exec(target, callback=None):
    def worker():
        res = target()
        if callback:
            GLib.idle_add(callback, res)

    threading.Thread(target=worker, daemon=True).start()


def _bt_device_name(dev) -> str:
    return dev.alias or dev.name or dev.address or "Unknown"


class NetworkButton(Box):
    def __init__(self, widgets=None):
        super().__init__(name="network-button")
        self._w = widgets
        self._aid = self._uid = None
        self._ast = 0
        self._last_ico = None
        self._en_hid = self._ssid_hid = self._ready_hid = None
        self._destroyed = False

        # NetworkClient общий на всё приложение (владелец — Dashboard),
        # чтобы не держать два независимых D-Bus-соединения к NetworkManager.
        self._cl = widgets.network_client
        self._build()
        self.connect("destroy", lambda *_: self.cleanup())
        self._ready_hid = self._cl.connect("device-ready", self._ready)
        self._sched()

    def _build(self):
        self.network_icon = Label(name="network-icon")
        self.network_label = Label(name="network-label", label="Wi-Fi", xalign=0, h_align="start", justification="left")
        self.network_ssid = Label(name="network-ssid", xalign=0, h_align="start", justification="left")

        self.network_ssid_revealer = Gtk.Revealer(
            halign=Gtk.Align.START,
            valign=Gtk.Align.CENTER,
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            transition_duration=400,
        )
        self.network_ssid_revealer.add(self.network_ssid)

        self._title_box = Box(
            orientation="v", h_align="start", v_align="center",
            children=(self.network_label, self.network_ssid_revealer)
        )

        def _tog(*_):
            if wifi := self._cl.wifi_device:
                wifi.toggle_wifi()

        self.network_status_button = Button(
            name="network-status-button",
            h_expand=True,
            child=_content(self.network_icon, self._title_box),
            on_clicked=_tog,
        )

        self.network_menu_label = Label(name="network-menu-label", markup=icons.chevron_right)
        self.network_menu_button = Button(
            name="network-menu-button",
            child=self.network_menu_label,
            on_clicked=self._menu_click,
        )

        self.add(self.network_status_button)
        self.add(self.network_menu_button)

        self._sw = (
            self, self.network_icon, self.network_label, self.network_ssid,
            self.network_status_button, self.network_menu_button, self.network_menu_label,
        )

    def _menu_click(self, *_):
        if self._destroyed:
            return
        self._w.show_network_applet()

    def _ready(self, *_):
        if wifi := self._cl.wifi_device:
            self._en_hid = wifi.connect("notify::enabled", self._sched_cb)
            self._ssid_hid = wifi.connect("notify::ssid", self._sched_cb)
            self._sched()

    def _sched_cb(self, *_):
        self._sched()

    def _sched(self):
        if self._uid:
            GLib.source_remove(self._uid)
        self._uid = GLib.timeout_add(100, self._do_upd)

    def _do_upd(self) -> bool:
        self._uid = None
        self.update_state()
        return False

    def _set_icon(self, markup: str):
        if self._last_ico != markup:
            self.network_icon.set_markup(markup)
            self._last_ico = markup

    def _start_anim(self):
        if self._aid is None:
            self._ast = 0
            self._aid = GLib.timeout_add(500, self._anim)

    def _stop_anim(self):
        if self._aid is not None:
            GLib.source_remove(self._aid)
            self._aid = None

    def _anim(self) -> bool:
        if self._destroyed:
            self._aid = None
            return False

        wifi = self._cl.wifi_device
        if not wifi or not wifi.enabled or (wifi.state == "activated" and wifi.ssid != "Disconnected"):
            self._stop_anim()
            return False

        self._set_icon(_AN[self._ast])
        self._ast = (self._ast + 1) % 6
        return True

    def update_state(self):
        if self._destroyed:
            return

        wifi, eth = self._cl.wifi_device, self._cl.ethernet_device

        if wifi and not wifi.enabled:
            self._stop_anim()
            self._set_icon(icons.wifi_off)
            self.network_ssid_revealer.set_reveal_child(False)
            _dis(self._sw, True)
            return

        _dis(self._sw, False)

        if self._cl.primary_device == "wired":
            self._stop_anim()
            self._set_icon(icons.world if eth and eth.internet == "activated" else icons.world_off)
            self.network_ssid_revealer.set_reveal_child(False)
            return

        if not wifi:
            self._stop_anim()
            self._set_icon(icons.wifi_off)
            self.network_ssid_revealer.set_reveal_child(False)
            return

        if wifi.state == "activated" and wifi.ssid and wifi.ssid != "Disconnected":
            self._stop_anim()
            s = wifi.ssid
            self.network_ssid.set_label(s[:6].rstrip() + "..." if len(s) > 6 else s)
            self.network_ssid_revealer.set_reveal_child(True)

            st = wifi.strength
            ic = _WI[0] if st < _TH[0] else (_WI[1] if st < _TH[1] else (_WI[2] if st < _TH[2] else _WI[3]))
            self._set_icon(ic)
        else:
            self.network_ssid_revealer.set_reveal_child(False)
            self._start_anim()

    def cleanup(self):
        if self._destroyed:
            return
        self._destroyed = True

        self._stop_anim()
        if self._uid:
            GLib.source_remove(self._uid)
            self._uid = None

        if self._ready_hid:
            self._cl.disconnect(self._ready_hid)
            self._ready_hid = None

        if wifi := self._cl.wifi_device:
            if self._en_hid:
                wifi.disconnect(self._en_hid)
            if self._ssid_hid:
                wifi.disconnect(self._ssid_hid)

        # NetworkClient общий (владелец — Dashboard) — намеренно НЕ вызываем
        # self._cl.cleanup(), это уничтожило бы клиент для NetworkConnections.
        self._cl = self._w = None


class BluetoothButton(Box):
    def __init__(self, widgets=None):
        super().__init__(name="bluetooth-button")
        self._w = widgets
        self._en = self._pending = False
        self._pending_tid = None
        self._uid = None
        self._en_hid = self._changed_hid = self._added_hid = self._removed_hid = None
        self._destroyed = False
        self._cl = BluetoothClient()

        self._build()
        self.connect("destroy", lambda *_: self.cleanup())

        self._en_hid = self._cl.connect("notify::enabled", self._sched_cb)
        self._changed_hid = self._cl.connect("changed", self._sched_cb)
        self._added_hid = self._cl.connect("device-added", self._sched_cb)
        self._removed_hid = self._cl.connect("device-removed", self._sched_cb)

        GLib.idle_add(self.update_state)

    def _build(self):
        self.bluetooth_icon = Label(name="bluetooth-icon", markup=icons.bluetooth_off)
        self.bluetooth_label = Label(name="bluetooth-label", label="Bluetooth", xalign=0, h_align="start", justification="left")
        self.bluetooth_ssid = Label(name="bluetooth-ssid", xalign=0, h_align="start", justification="left")

        self.bluetooth_ssid_revealer = Gtk.Revealer(
            halign=Gtk.Align.START,
            valign=Gtk.Align.CENTER,
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            transition_duration=400,
        )
        self.bluetooth_ssid_revealer.add(self.bluetooth_ssid)

        title_box = Box(
            orientation="v", h_align="start", v_align="center",
            children=(self.bluetooth_label, self.bluetooth_ssid_revealer),
        )

        self.bluetooth_status_button = Button(
            name="bluetooth-status-button",
            h_expand=True,
            child=_content(self.bluetooth_icon, title_box),
            on_clicked=self._on_toggle_click,
        )

        self.bluetooth_menu_label = Label(name="bluetooth-menu-label", markup=icons.chevron_right)
        self.bluetooth_menu_button = Button(
            name="bluetooth-menu-button",
            child=self.bluetooth_menu_label,
            on_clicked=self._open_menu,
        )

        self.add(self.bluetooth_status_button)
        self.add(self.bluetooth_menu_button)

        self._sw = (
            self, self.bluetooth_icon, self.bluetooth_label,
            self.bluetooth_status_button, self.bluetooth_menu_button, self.bluetooth_menu_label,
        )

    def _get_pwr(self) -> bool:
        base_dir = "/sys/class/rfkill/"
        if os.path.exists(base_dir):
            for d in os.listdir(base_dir):
                t_path = os.path.join(base_dir, d, "type")
                if os.path.exists(t_path):
                    with open(t_path, "r") as f:
                        if f.read().strip() == "bluetooth":
                            with open(os.path.join(base_dir, d, "state"), "r") as sf:
                                return sf.read().strip() == "1"
        return False

    def _on_toggle_click(self, *_):
        if self._pending:
            return
        self._pending = True

        en = self._get_pwr()
        cmd = "bluetoothctl power off ; rfkill block bluetooth" if en else "rfkill unblock bluetooth ; bluetoothctl power on"

        GLib.spawn_command_line_async(f"/bin/sh -c '{cmd}'")
        self._upd_ui(not en, [])
        self._pending_tid = GLib.timeout_add(1000, self._clear_pending)

    def _clear_pending(self) -> bool:
        self._pending_tid = None
        if self._destroyed:
            return False
        self._pending = False
        self.update_state()
        return False

    def _sched_cb(self, *_):
        if self._uid:
            GLib.source_remove(self._uid)
        self._uid = GLib.timeout_add(100, self._do_upd)

    def _do_upd(self) -> bool:
        self._uid = None
        self.update_state()
        return False

    def _upd_ui(self, en: bool, connected):
        if self._en != en:
            self._en = en
            self.bluetooth_icon.set_markup(icons.bluetooth if en else icons.bluetooth_off)
            _dis(self._sw, not en)

        if not en or not connected:
            self.bluetooth_ssid_revealer.set_reveal_child(False)
            return

        dev = min(connected, key=lambda d: (d.alias or d.name or "").lower())
        name = _bt_device_name(dev)
        self.bluetooth_ssid.set_label(name[:6].rstrip() + "..." if len(name) > 6 else name)
        self.bluetooth_ssid_revealer.set_reveal_child(True)

    def update_state(self, *_):
        if self._destroyed:
            return False
        if not self._pending:
            self._upd_ui(self._get_pwr(), self._cl.connected_devices)
        return False

    def _open_menu(self, *_):
        if self._destroyed:
            return
        self._w.show_bt()

    def cleanup(self):
        if self._destroyed:
            return
        self._destroyed = True

        if self._pending_tid:
            GLib.source_remove(self._pending_tid)
            self._pending_tid = None
        if self._uid:
            GLib.source_remove(self._uid)
            self._uid = None

        if self._en_hid:
            self._cl.disconnect(self._en_hid)
            self._en_hid = None
        if self._changed_hid:
            self._cl.disconnect(self._changed_hid)
            self._changed_hid = None
        if self._added_hid:
            self._cl.disconnect(self._added_hid)
            self._added_hid = None
        if self._removed_hid:
            self._cl.disconnect(self._removed_hid)
            self._removed_hid = None

        self._cl = self._w = None


class NightModeButton(_TimerSplitButton):
    PAT, START, STOP = "hyprsunset", "hyprsunset -t 3500", "pkill hyprsunset"

    def __init__(self):
        title_box = Box(
            orientation="v", h_align="start", v_align="center",
            children=(
                Label(name="night-mode-label", label="Night", xalign=0, h_align="start", justification="left"),
                Label(name="night-mode-label", label="Mode", xalign=0, h_align="start", justification="left"),
            ),
        )
        super().__init__(
            name_prefix="night-mode",
            icon_markup=icons.night,
            title_widget=title_box,
            default_seconds=3600,
        )
        self.update_state()

    def _on_status_click(self, *_):
        if _proc_running(self.PAT):
            GLib.spawn_command_line_async(self.STOP)
            self.stop_timer()
        else:
            GLib.spawn_command_line_async(self.START)
            self.start_timer()

    def _on_timer_finished(self):
        GLib.spawn_command_line_async(self.STOP)

    def update_state(self, *_):
        _async_exec(lambda: _proc_running(self.PAT), self._upd_proc)

    def _upd_proc(self, active: bool) -> bool:
        if active and not self._is_running:
            self.start_timer()
        elif not active and self._is_running:
            self.stop_timer()
        else:
            self._dis_ui(not active)
        return False

    def cleanup(self):
        if self._destroyed:
            return
        super().cleanup()
        if _proc_running(self.PAT):
            GLib.spawn_command_line_async(self.STOP)


class CaffeineButton(_TimerSplitButton):
    def __init__(self):
        self._caffeine = Caffeine()
        title_box = Box(
            orientation="v", h_align="start", v_align="center",
            children=(
                Label(name="caffeine-label", label="Caffeine", xalign=0, h_align="start", justification="left"),
            ),
        )
        super().__init__(
            name_prefix="caffeine",
            icon_markup=icons.coffee,
            title_widget=title_box,
            default_seconds=3600,
        )
        self.update_state()

    def _on_status_click(self, *_):
        if self._caffeine.toggle():
            self.start_timer()
        else:
            self.stop_timer()

    def _on_timer_finished(self):
        self._caffeine.disable()

    def update_state(self, *_):
        en = self._caffeine.is_enabled
        if en and not self._is_running:
            self.start_timer()
        elif not en and self._is_running:
            self.stop_timer()
        else:
            self._dis_ui(not en)
        return False

    def cleanup(self):
        if self._destroyed:
            return
        super().cleanup()
        self._caffeine.disable()


class WorkTimeButton(_TimerSplitButton):
    def __init__(self):
        title_box = Box(
            orientation="v", h_align="start", v_align="center",
            children=(
                Label(name="work-time-label", label="Work", xalign=0, h_align="start", justification="left"),
                Label(name="work-time-label", label="Time", xalign=0, h_align="start", justification="left"),
            ),
        )
        self._work_time = WorkTime(update_callback=self._on_work_time_update)
        super().__init__(
            name_prefix="work-time",
            icon_markup=icons.clock,
            title_widget=title_box,
            default_seconds=self._work_time.WORK_TIME,
        )
        self._work_time.WORK_TIME = self._duration
        self._work_time.remaining = self._duration
        self.update_state()

    def _on_status_click(self, *_):
        self._work_time.toggle()
        self.update_state()

    def _on_work_time_update(self, status_text: str, is_running: bool):
        self._is_running = is_running
        self._remaining = self._work_time.remaining
        self._dis_ui(not is_running)
        self.update_timer_display()
        if is_running:
            self._trigger_tick_pulse()

    def _on_timer_scroll(self, widget, event) -> bool:
        if self._is_running:
            return True
        res = super()._on_timer_scroll(widget, event)
        self._work_time.WORK_TIME = self._duration
        self._work_time.remaining = self._duration
        return res

    def update_state(self, *_):
        is_run = self._work_time.is_running
        self._is_running = is_run
        self._dis_ui(not is_run)
        self._remaining = self._work_time.remaining if is_run else self._duration
        self.update_timer_display()
        return False

    def cleanup(self):
        if self._destroyed:
            return
        super().cleanup()
        self._work_time.stop()


class Buttons(Gtk.Grid):
    def __init__(self, widgets=None):
        super().__init__(name="buttons-grid")

        self.set_row_homogeneous(True)
        self.set_column_homogeneous(True)
        self.set_row_spacing(4)
        self.set_column_spacing(4)
        self.set_vexpand(False)

        btns = (
            NetworkButton(widgets),
            BluetoothButton(widgets),
            NightModeButton(),
            CaffeineButton(),
            WorkTimeButton(),
        )

        for i, btn in enumerate(btns):
            self.attach(btn, i, 0, 1, 1)

        (
            self.network_button,
            self.bluetooth_button,
            self.night_mode_button,
            self.caffeine_button,
            self.work_time_button,
        ) = btns

        self.show_all()

    def cleanup(self):
        for child in self.get_children():
            child.cleanup()