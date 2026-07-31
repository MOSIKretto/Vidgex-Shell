import os
import threading
from gi.repository import Gdk, GLib, Gtk

from fabric.utils.helpers import exec_shell_command_async
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label
from fabric.bluetooth import BluetoothClient

import services.icons as icons
from modules.Notch.MainWindow.Dashboard.network import NetworkClient


_TH = (25, 50, 75)
_WI = (icons.wifi_0, icons.wifi_1, icons.wifi_2, icons.wifi_3)
_AN = (icons.wifi_0, icons.wifi_1, icons.wifi_2, icons.wifi_3, icons.wifi_2, icons.wifi_1)
_ON, _OFF = "Enabled", "Disabled"

def _ent(w, _):
    if win := w.get_window():
        if not getattr(w, '_cursor', None):
            w._cursor = Gdk.Cursor.new_from_name(w.get_display(), "pointer")
        win.set_cursor(w._cursor)

def _lv(w, _):
    if win := w.get_window():
        win.set_cursor(None)

def _hover(w):
    w.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
    w._cursor = None
    w.connect("enter-notify-event", _ent)
    w.connect("leave-notify-event", _lv)

def _fast_chk(pat):
    pat_b = pat.encode()
    try:
        for pid in os.listdir('/proc'):
            if pid.isdigit():
                try:
                    with open(f'/proc/{pid}/cmdline', 'rb') as f:
                        data = f.read()
                        if pat_b in data or pat_b in data.replace(b'\x00', b' '):
                            return True
                except Exception:
                    pass
    except Exception:
        pass
    return False

def _async_exec(target, callback=None):
    def worker():
        res = target()
        if callback:
            GLib.idle_add(callback, res)
    threading.Thread(target=worker, daemon=True).start()

def _dis(ws, disabled):
    m = "add_style_class" if disabled else "remove_style_class"
    for w in ws:
        getattr(w, m)("disabled")

def _content(ic, ti, st):
    return Box(
        h_align="start", v_align="center", spacing=10,
        children=(
            ic,
            Box(
                orientation="v", h_align="start", v_align="center",
                children=(
                    Box(children=(ti, Box(h_expand=True))),
                    Box(children=(st, Box(h_expand=True)))
                )
            )
        )
    )


class NetworkButton(Box):
    __slots__ = ('_w', '_cl', '_aid', '_uid', '_ast', '_sw',
                 'network_icon', 'network_label', 'network_ssid',
                 'network_status_button', 'network_menu_button', 'network_menu_label',
                 '_last_ico', '_en_hid', '_ssid_hid')

    def __init__(self, widgets=None):
        super().__init__()
        self._w = widgets
        self._aid = self._uid = None
        self._ast = 0
        self._last_ico = None
        self._en_hid = self._ssid_hid = None

        self._cl = NetworkClient()
        self._build()
        self._cl.connect('device-ready', self._ready)
        self._sched()

    def _build(self):
        self.network_icon = Label(name="network-icon")
        self.network_label = Label(name="network-label", label="Wi-Fi", justification="left")
        self.network_ssid = Label(name="network-ssid", justification="left")

        def _tog(*_):
            if w := getattr(self._cl, "wifi_device", None):
                if hasattr(w, "toggle_wifi"):
                    w.toggle_wifi()

        self.network_status_button = Button(
            name="network-status-button", h_expand=True,
            child=_content(self.network_icon, self.network_label, self.network_ssid),
            on_clicked=_tog
        )
        _hover(self.network_status_button)

        self.network_menu_label = Label(name="network-menu-label", markup=icons.chevron_right)
        self.network_menu_button = Button(
            name="network-menu-button", child=self.network_menu_label,
            on_clicked=self._menu_click
        )
        _hover(self.network_menu_button)

        self.add(self.network_status_button)
        self.add(self.network_menu_button)

        self._sw = (
            self, self.network_icon, self.network_label, self.network_ssid,
            self.network_status_button, self.network_menu_button, self.network_menu_label
        )

    def _menu_click(self, *_):
        if self._w and hasattr(self._w, 'show_network_applet'):
            self._w.show_network_applet()

    def _ready(self, *_):
        if wifi := self._cl.wifi_device:
            self._en_hid = wifi.connect('notify::enabled', self._sched_cb)
            self._ssid_hid = wifi.connect('notify::ssid', self._sched_cb)
            self._sched()

    def _sched_cb(self, *_):
        self._sched()

    def _sched(self):
        if self._uid: GLib.source_remove(self._uid)
        self._uid = GLib.timeout_add(100, self._do_upd)

    def _do_upd(self):
        self._uid = None
        self.update_state()
        return False

    def _set_icon(self, markup):
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

    def _anim(self):
        wifi = self._cl.wifi_device
        if not wifi or not wifi.enabled or (wifi.state == "activated" and wifi.ssid != "Отключено"):
            self._stop_anim()
            return False

        self._set_icon(_AN[self._ast])
        self._ast = (self._ast + 1) % 6
        return True

    def update_state(self):
        wifi, eth = self._cl.wifi_device, self._cl.ethernet_device

        if wifi and not wifi.enabled:
            self._stop_anim()
            self._set_icon(icons.wifi_off)
            self.network_ssid.set_label(_OFF)
            _dis(self._sw, True)
            return

        _dis(self._sw, False)

        if getattr(self._cl, 'primary_device', 'wireless') == "wired":
            self._stop_anim()
            self._set_icon(icons.world if eth and getattr(eth, "internet", "") == "activated" else icons.world_off)
            return

        if not wifi:
            self._stop_anim()
            self._set_icon(icons.wifi_off)
            return

        if wifi.state == "activated" and wifi.ssid != "Отключено":
            self._stop_anim()
            s = wifi.ssid
            self.network_ssid.set_label(s[:8].rstrip() + "..." if len(s) > 10 else s)

            st = wifi.strength
            ic = _WI[0] if st < _TH[0] else (_WI[1] if st < _TH[1] else (_WI[2] if st < _TH[2] else _WI[3]))
            self._set_icon(ic)
        else:
            self.network_ssid.set_label(_ON)
            self._start_anim()

    def cleanup(self):
        self._stop_anim()
        if self._uid:
            GLib.source_remove(self._uid)
            self._uid = None

        if self._cl and getattr(self._cl, 'wifi_device', None):
            try:
                if self._en_hid: self._cl.wifi_device.disconnect(self._en_hid)
                if self._ssid_hid: self._cl.wifi_device.disconnect(self._ssid_hid)
            except Exception: pass

        self._cl = self._w = None


class BluetoothButton(Box):
    __slots__ = ('_w', '_en', '_cl', '_sw', '_pending',
                 'bluetooth_icon', 'bluetooth_label', 'bluetooth_status_text',
                 'bluetooth_status_button', 'bluetooth_menu_button', 'bluetooth_menu_label')

    def __init__(self, widgets=None):
        super().__init__()
        self._w = widgets
        self._en = self._pending = False

        try:
            self._cl = BluetoothClient()
        except Exception:
            self._cl = None

        self._build()

        if self._cl:
            self._cl.connect("notify::enabled", self.update_state)

        GLib.idle_add(self.update_state)

    def _build(self):
        self.bluetooth_icon = Label(name="bluetooth-icon", markup=icons.bluetooth_off)
        self.bluetooth_label = Label(name="bluetooth-label", label="Bluetooth", justification="left")
        self.bluetooth_status_text = Label(name="bluetooth-status", label=_OFF, justification="left")

        self.bluetooth_status_button = Button(
            name="bluetooth-status-button", h_expand=True,
            child=_content(self.bluetooth_icon, self.bluetooth_label, self.bluetooth_status_text),
            on_clicked=self._on_toggle_click
        )
        _hover(self.bluetooth_status_button)

        self.bluetooth_menu_label = Label(name="bluetooth-menu-label", markup=icons.chevron_right)
        self.bluetooth_menu_button = Button(
            name="bluetooth-menu-button", child=self.bluetooth_menu_label,
            on_clicked=self._open_menu
        )
        _hover(self.bluetooth_menu_button)

        self.add(self.bluetooth_status_button)
        self.add(self.bluetooth_menu_button)

        self._sw = (
            self, self.bluetooth_icon, self.bluetooth_label, self.bluetooth_status_text,
            self.bluetooth_status_button, self.bluetooth_menu_button, self.bluetooth_menu_label
        )

    def _get_pwr(self):
        try:
            base_dir = "/sys/class/rfkill/"
            for d in os.listdir(base_dir):
                if os.path.exists(t_path := os.path.join(base_dir, d, "type")):
                    with open(t_path, "r") as f:
                        if f.read().strip() == "bluetooth":
                            with open(os.path.join(base_dir, d, "state"), "r") as sf:
                                return sf.read().strip() == "1"
        except Exception:
            pass
        return False

    def _on_toggle_click(self, *_):
        if self._pending: return
        self._pending = True

        en = self._get_pwr()

        if en:
            cmd = "bluetoothctl power off ; rfkill block bluetooth"
        else:
            cmd = "rfkill unblock bluetooth ; sleep 0.2 ; bluetoothctl power on"

        try:
            GLib.spawn_command_line_async(f"/bin/sh -c '{cmd}'")
        except Exception: pass

        self._upd_ui(not en)
        GLib.timeout_add(1000, self._clear_pending)

    def _clear_pending(self):
        self._pending = False
        self.update_state()
        return False

    def _upd_ui(self, en):
        if self._en == en: return
        self._en = en
        self.bluetooth_icon.set_markup(icons.bluetooth if en else icons.bluetooth_off)
        self.bluetooth_status_text.set_label(_ON if en else _OFF)
        _dis(self._sw, not en)

    def update_state(self, *_):
        if not self._pending:
            self._upd_ui(self._get_pwr())
        return False

    def _open_menu(self, *_):
        if self._w and hasattr(self._w, 'show_bt'):
            self._w.show_bt()

    def cleanup(self):
        self._cl = self._w = None


class _ToggleBtn(Button):
    __slots__ = ('_ic', '_ti', '_st', '_sw')
    PAT = START = STOP = NAME = ICON = TEXT = ""

    def __init__(self):
        self._ic = Label(name=f"{self.NAME}-icon", markup=self.ICON)
        self._ti = Label(name=f"{self.NAME}-label", label=self.TEXT, justification="left")
        self._st = Label(name=f"{self.NAME}-status", label=_OFF, justification="left")

        super().__init__(
            name=f"{self.NAME}-button", h_expand=True,
            child=_content(self._ic, self._ti, self._st),
            on_clicked=self._click
        )

        _hover(self)
        self._sw = (self, self._ic, self._ti, self._st)
        self.update_state()

    def _click(self, *_):
        _async_exec(self._toggle, self._upd)

    def _toggle(self):
        if _fast_chk(self.PAT):
            exec_shell_command_async(self.STOP)
            return False
        exec_shell_command_async(self.START)
        return True

    def update_state(self, *_):
        _async_exec(lambda: _fast_chk(self.PAT), self._upd)
        return False

    def _upd(self, en):
        self._st.set_label(_ON if en else _OFF)
        _dis(self._sw, not en)
        return False


class NightModeButton(_ToggleBtn):
    PAT, START, STOP = "hyprsunset", "hyprsunset -t 3500", "pkill hyprsunset"
    NAME, ICON, TEXT = "night-mode", icons.night, "Night mode"


class _ScriptToggleBtn(_ToggleBtn):
    __slots__ = ('_pid',)

    def __init__(self):
        self._pid = None
        super().__init__()

    def _toggle(self):
        if self._pid is not None:
            try:
                os.kill(self._pid, 15)
                self._pid = None
                return False
            except OSError:
                self._pid = None

        if _fast_chk(self.PAT):
            exec_shell_command_async(self.STOP)
            self._pid = None
            return False

        def spawn_on_main():
            try:
                pid, _, _, _ = GLib.spawn_async(
                    argv=["/bin/sh", "-c", self.START],
                    flags=GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.DO_NOT_REAP_CHILD
                )
                self._pid = pid
                GLib.child_watch_add(GLib.PRIORITY_DEFAULT, pid, self._on_exit)
            except Exception:
                exec_shell_command_async(self.START)
                self._pid = None

        GLib.idle_add(spawn_on_main)
        return True

    def _on_exit(self, pid, _):
        if self._pid == pid:
            self._pid = None
        GLib.idle_add(self.update_state)


class CaffeineButton(_ScriptToggleBtn):
    PAT = "vidgex-inhibit"
    START = "python ~/.config/Vidgex-Shell/scripts/inhibit.py"
    STOP = "pkill -f vidgex-inhibit"
    NAME, ICON, TEXT = "caffeine", icons.coffee, "Caffeine"


class EyesHandsButton(_ScriptToggleBtn):
    PAT = "vidgex-eyes-hands"
    START = "python ~/.config/Vidgex-Shell/scripts/eyes-hands/eyesHands.py"
    STOP = "pkill -f vidgex-eyes-hands"
    NAME, ICON, TEXT = "eyes-hands", icons.spy, "Eyes-Hands"


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
            EyesHandsButton()
        )

        for i, btn in enumerate(btns):
            self.attach(btn, i, 0, 1, 1)

        (self.network_button, self.bluetooth_button, self.night_mode_button,
         self.caffeine_button, self.eyes_hands_button) = btns

        self.show_all()

    def cleanup(self):
        for child in self.get_children():
            try:
                child.cleanup()
            except AttributeError:
                pass