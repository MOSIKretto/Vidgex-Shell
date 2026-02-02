from fabric.utils.helpers import exec_shell_command_async
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label

from gi.repository import Gdk, GLib, Gtk

import services.icons as icons
from modules.Notch.Widgets.network import NetworkClient


_TH = (25, 50, 75)
_WI = (icons.wifi_0, icons.wifi_1, icons.wifi_2, icons.wifi_3)
_AN = (icons.wifi_0, icons.wifi_1, icons.wifi_2, icons.wifi_3, icons.wifi_2, icons.wifi_1)
_ON, _OFF = "Enabled", "Disabled"


def _hover(w):
    w.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
    cur = [None]

    def ent(w, _):
        win = w.get_window()
        if win:
            if not cur[0]:
                cur[0] = Gdk.Cursor.new_from_name(w.get_display(), "pointer")
            win.set_cursor(cur[0])

    def lv(w, _):
        win = w.get_window()
        if win:
            win.set_cursor(None)

    w.connect("enter-notify-event", ent)
    w.connect("leave-notify-event", lv)


def _chk(pat):
    try:
        _, _, _, c = GLib.spawn_command_line_sync(f"pgrep -f '{pat}'")
        return c == 0
    except:
        return False


def _async(fn, cb=None):
    def w(_):
        r = fn()
        if cb:
            GLib.idle_add(cb, r)
    GLib.Thread.new(None, w, None)


def _dis(ws, d):
    m = "add_style_class" if d else "remove_style_class"
    for w in ws:
        if hasattr(w, m):
            getattr(w, m)("disabled")


def _content(ic, ti, st):
    return Box(
        h_align="start", v_align="center", spacing=10,
        children=[
            ic,
            Box(
                orientation="v", 
                h_align="start", 
                v_align="center", 
                children=[
                    Box(children=[ti, Box(h_expand=True)]),
                    Box(children=[st, Box(h_expand=True)])
                ]
            )
        ]
    )


class NetworkButton(Box):
    __slots__ = ('_w', '_n', '_cl', '_aid', '_uid', '_ast', '_sw',
                 'network_icon', 'network_label', 'network_ssid',
                 'network_status_button', 'network_menu_button', 'network_menu_label')

    def __init__(self, widgets=None, notch=None):
        super().__init__()
        self._w, self._n = widgets, notch
        self._aid = self._uid = None
        self._ast = 0

        self._cl = NetworkClient()
        self._build()
        self._cl.connect('device-ready', self._ready)
        self._sched()

    def _build(self):
        self.network_icon = Label(name="network-icon")
        self.network_label = Label(name="network-label", label="Wi-Fi", justification="left")
        self.network_ssid = Label(name="network-ssid", justification="left")

        self.network_status_button = Button(
            name="network-status-button", 
            h_expand=True,
            child=_content(
                self.network_icon, 
                self.network_label, 
                self.network_ssid
            ),
            on_clicked=self._stat_click
        )
        _hover(self.network_status_button)

        self.network_menu_label = Label(name="network-menu-label", markup=icons.chevron_right)
        self.network_menu_button = Button(
            name="network-menu-button", 
            child=self.network_menu_label,
            on_clicked=self._menu_click
        )
        _hover(self.network_menu_button)

        self.add(self.network_status_button)
        self.add(self.network_menu_button)

        self._sw = (
            self, self.network_icon, 
            self.network_label, 
            self.network_ssid,
            self.network_status_button, 
            self.network_menu_button, 
            self.network_menu_label
        )

    def _stat_click(self, *_):
        wifi = self._cl.wifi_device
        if wifi:
            wifi.toggle_wifi()

    def _menu_click(self, *_):
        if self._n:
            self._n.open_notch("network_applet")
        elif self._w and hasattr(self._w, 'show_network_applet'):
            self._w.show_network_applet()

    def _ready(self, *_):
        wifi = self._cl.wifi_device
        if wifi:
            wifi.connect('notify::enabled', lambda *_: self._sched())
            wifi.connect('notify::ssid', lambda *_: self._sched())
            self._sched()

    def _sched(self):
        if self._uid:
            GLib.source_remove(self._uid)
        self._uid = GLib.timeout_add(100, self._do_upd)

    def _do_upd(self):
        self._uid = None
        self.update_state()
        return False

    def _wifi_ic(self, s):
        return _WI[0] if s < _TH[0] else (_WI[1] if s < _TH[1] else (_WI[2] if s < _TH[2] else _WI[3]))

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
        GLib.idle_add(self.network_icon.set_markup, _AN[self._ast])
        self._ast = (self._ast + 1) % 6
        return True

    def update_state(self):
        wifi, eth = self._cl.wifi_device, self._cl.ethernet_device

        if wifi and not wifi.enabled:
            self._stop_anim()
            self.network_icon.set_markup(icons.wifi_off)
            self.network_ssid.set_label(_OFF)
            _dis(self._sw, True)
            return

        _dis(self._sw, False)

        if getattr(self._cl, 'primary_device', 'wireless') == "wired":
            self._stop_anim()
            self.network_icon.set_markup(icons.world if eth and eth.internet == "activated" else icons.world_off)
            return

        if not wifi:
            self._stop_anim()
            self.network_icon.set_markup(icons.wifi_off)
            return

        if wifi.state == "activated" and wifi.ssid != "Отключено":
            self._stop_anim()
            s = wifi.ssid
            self.network_ssid.set_label(s[:10] + "..." if len(s) > 10 else s)
            self.network_icon.set_markup(self._wifi_ic(wifi.strength))
        else:
            self.network_ssid.set_label(_ON)
            self._start_anim()

    def cleanup(self):
        self._stop_anim()
        if self._uid:
            GLib.source_remove(self._uid)
            self._uid = None
        self._cl = self._w = self._n = None


class BluetoothButton(Box):
    __slots__ = ('_w', '_n', '_en',
                 'bluetooth_icon', 'bluetooth_label', 'bluetooth_status_text',
                 'bluetooth_status_button', 'bluetooth_menu_button', 'bluetooth_menu_label')

    def __init__(self, widgets=None, notch=None):
        super().__init__()
        self._w, self._n = widgets, notch
        self._en = False

        self._build()
        self._setup_sig()
        self.update_state()

    def _build(self):
        self.bluetooth_icon = Label(name="bluetooth-icon")
        self.bluetooth_label = Label(name="bluetooth-label", label="Bluetooth", justification="left")
        self.bluetooth_status_text = Label(name="bluetooth-status", justification="left")

        self.bluetooth_status_button = Button(
            name="bluetooth-status-button",
            h_expand=True,
            child=_content(
                self.bluetooth_icon, 
                self.bluetooth_label, 
                self.bluetooth_status_text
            ),
            on_clicked=lambda *_: _async(self._toggle, self._upd_ui)
        )
        _hover(self.bluetooth_status_button)

        self.bluetooth_menu_label = Label(name="bluetooth-menu-label", markup=icons.chevron_right)
        self.bluetooth_menu_button = Button(
            name="bluetooth-menu-button", 
            child=self.bluetooth_menu_label,
            on_clicked=lambda *_: self._open_menu()
        )
        _hover(self.bluetooth_menu_button)

        self.add(self.bluetooth_status_button)
        self.add(self.bluetooth_menu_button)

    def _setup_sig(self):
        if hasattr(self._w, 'bluetooth'):
            bt = self._w.bluetooth
            if hasattr(bt, 'client') and hasattr(bt.client, 'connect'):
                try:
                    bt.client.connect('power-changed', lambda *_: self.update_state())
                    bt.client.connect('device-added', self.update_state)
                    bt.client.connect('device-removed', self.update_state)
                except:
                    pass

    def _get_pwr(self):
        try:
            if _chk("bluetoothd"):
                _, out, _, c = GLib.spawn_command_line_sync("bluetoothctl show")
                if c == 0 and out:
                    for ln in out.decode('utf-8', errors='ignore').splitlines():
                        if "Powered:" in ln:
                            return "yes" in ln
            _, out, _, c = GLib.spawn_command_line_sync("rfkill list bluetooth")
            if c == 0 and out:
                o = out.decode('utf-8', errors='ignore').lower()
                return "soft blocked: no" in o and "hard blocked: no" in o
        except:
            pass
        return False

    def _toggle(self):
        try:
            cur = self._get_pwr()
            if _chk("bluetoothd"):
                GLib.spawn_command_line_sync(f"bluetoothctl power {'off' if cur else 'on'}")
            else:
                GLib.spawn_command_line_sync(f"rfkill {'block' if cur else 'unblock'} bluetooth")
            return not cur
        except:
            return self._en

    def _upd_ui(self, en=None):
        if en is None:
            en = self._get_pwr()
        self._en = en
        self.bluetooth_icon.set_markup(icons.bluetooth if en else icons.bluetooth_off)
        self.bluetooth_status_text.set_label(_ON if en else _OFF)

    def update_state(self, *_):
        _async(self._get_pwr, self._upd_ui)

    def _open_menu(self):
        if self._n:
            self._n.open_notch("bluetooth")
        elif hasattr(self._w, 'show_bt'):
            self._w.show_bt()
        else:
            try:
                GLib.spawn_command_line_async("blueman-manager")
            except:
                pass

    def cleanup(self):
        self._w = self._n = None


class _ToggleBtn(Button):
    __slots__ = ('_ic', '_ti', '_st', '_sw')
    PAT = ""
    START = ""
    STOP = ""
    NAME = ""
    ICON = ""
    TEXT = ""

    def __init__(self):
        self._ic = Label(name=f"{self.NAME}-icon", markup=self.ICON)
        self._ti = Label(name=f"{self.NAME}-label", label=self.TEXT, justification="left")
        self._st = Label(name=f"{self.NAME}-status", label=_OFF, justification="left")

        super().__init__(name=f"{self.NAME}-button", h_expand=True, child=_content(self._ic, self._ti, self._st), on_clicked=self._click)

        _hover(self)
        self._sw = (self, self._ic, self._ti, self._st)
        self.update_state()

    def _click(self, *_):
        _async(self._toggle, self._upd)

    def _toggle(self):
        if _chk(self.PAT):
            exec_shell_command_async(self.STOP)
            return False
        exec_shell_command_async(self.START)
        return True

    def update_state(self, *_):
        _async(lambda: _chk(self.PAT), self._upd)

    def _upd(self, en):
        self._st.set_label(_ON if en else _OFF)
        _dis(self._sw, not en)
        return False


class NightModeButton(_ToggleBtn):
    PAT = "hyprsunset"
    START = "hyprsunset -t 3500"
    STOP = "pkill hyprsunset"
    NAME = "night-mode"
    ICON = icons.night
    TEXT = "Night mode"


class CaffeineButton(_ToggleBtn):
    __slots__ = ('_pid',)
    PAT = "vidgex-inhibit"
    START = "python ~/.config/Vidgex-Shell/scripts/inhibit.py"
    STOP = "pkill -f vidgex-inhibit"
    NAME = "caffeine"
    ICON = icons.coffee
    TEXT = "Caffeine"

    def __init__(self):
        self._pid = None
        super().__init__()

    def _toggle(self):
        try:
            if self._pid is not None:
                try:
                    _, _, _, c = GLib.spawn_command_line_sync(f"kill -0 {self._pid}")
                    if c == 0:
                        GLib.spawn_command_line_sync(f"kill {self._pid}")
                        self._pid = None
                        return False
                except:
                    pass
                self._pid = None

            if _chk(self.PAT):
                exec_shell_command_async(self.STOP)
                self._pid = None
                return False

            try:
                pid, _, _, _ = GLib.spawn_async(
                    argv=["/bin/sh", "-c", self.START],
                    flags=GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.DO_NOT_REAP_CHILD)
                self._pid = pid
                GLib.child_watch_add(GLib.PRIORITY_DEFAULT, pid, self._on_exit)
            except:
                GLib.spawn_command_line_async(self.START)
                self._pid = None
            return True
        except:
            return False

    def _on_exit(self, pid, _):
        if self._pid == pid:
            self._pid = None
        GLib.idle_add(self.update_state)


class EyesHandsButton(_ToggleBtn):
    __slots__ = ('_pid',)
    PAT = "vidgex-eyes-hands"
    START = "python ~/.config/Vidgex-Shell/scripts/eyes-hands/eyes-hands.py"
    STOP = "pkill -f vidgex-eyes-hands"
    NAME = "eyes-hands"
    ICON = icons.spy
    TEXT = "Eyes-Hands"

    def __init__(self):
        self._pid = None
        super().__init__()

    def _toggle(self):
        try:
            if self._pid is not None:
                try:
                    _, _, _, c = GLib.spawn_command_line_sync(f"kill -0 {self._pid}")
                    if c == 0:
                        GLib.spawn_command_line_sync(f"kill {self._pid}")
                        self._pid = None
                        return False
                except:
                    pass
                self._pid = None

            if _chk(self.PAT):
                exec_shell_command_async(self.STOP)
                self._pid = None
                return False

            try:
                pid, _, _, _ = GLib.spawn_async(
                    argv=["/bin/sh", "-c", self.START],
                    flags=GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.DO_NOT_REAP_CHILD)
                self._pid = pid
                GLib.child_watch_add(GLib.PRIORITY_DEFAULT, pid, self._on_exit)
            except:
                GLib.spawn_command_line_async(self.START)
                self._pid = None
            return True
        except:
            return False

    def _on_exit(self, pid, _):
        if self._pid == pid:
            self._pid = None
        GLib.idle_add(self.update_state)


class Buttons(Gtk.Grid):
    __slots__ = ('_w', '_n', 'network_button', 'bluetooth_button',
                 'night_mode_button', 'caffeine_button', 'eyes_hands_button')

    def __init__(self, widgets=None, notch=None):
        super().__init__(name="buttons-grid")
        self._w, self._n = widgets, notch

        self.set_row_homogeneous(True)
        self.set_column_homogeneous(True)
        self.set_row_spacing(4)
        self.set_column_spacing(4)
        self.set_vexpand(False)

        self.network_button = NetworkButton(widgets=self._w, notch=self._n)
        self.bluetooth_button = BluetoothButton(widgets=self._w, notch=self._n)
        self.night_mode_button = NightModeButton()
        self.caffeine_button = CaffeineButton()
        self.eyes_hands_button = EyesHandsButton()

        self.attach(self.network_button, 0, 0, 1, 1)
        self.attach(self.bluetooth_button, 1, 0, 1, 1)
        self.attach(self.night_mode_button, 2, 0, 1, 1)
        self.attach(self.caffeine_button, 3, 0, 1, 1)
        self.attach(self.eyes_hands_button, 4, 0, 1, 1)

        self.show_all()

    def refresh_all_states(self):
        self.network_button.update_state()
        self.bluetooth_button.update_state()
        self.night_mode_button.update_state()
        self.caffeine_button.update_state()
        self.eyes_hands_button.update_state()

    def cleanup(self):
        self.network_button.cleanup()
        self.bluetooth_button.cleanup()
        self._w = self._n = None