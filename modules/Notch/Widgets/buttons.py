import os
import subprocess
import threading
from gi.repository import Gdk, GLib, Gtk

from fabric.utils.helpers import exec_shell_command_async
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label

import services.icons as icons
from modules.Notch.Widgets.network import NetworkClient


_TH = (25, 50, 75)
_WI = (icons.wifi_0, icons.wifi_1, icons.wifi_2, icons.wifi_3)
_AN = (icons.wifi_0, icons.wifi_1, icons.wifi_2, icons.wifi_3, icons.wifi_2, icons.wifi_1)
_ON, _OFF = "Enabled", "Disabled"

def _hover(w):
    w.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
    w._cursor = None 

    def ent(w, _):
        win = w.get_window()
        if win:
            if not w._cursor:
                w._cursor = Gdk.Cursor.new_from_name(w.get_display(), "pointer")
            win.set_cursor(w._cursor)

    def lv(w, _):
        win = w.get_window()
        if win:
            win.set_cursor(None)

    w.connect("enter-notify-event", ent)
    w.connect("leave-notify-event", lv)


def _chk(pat):
    try:
        r = subprocess.run(
            ["pgrep", "-f", pat], 
            stdout=subprocess.DEVNULL, 
            stderr=subprocess.DEVNULL
        )
        return r.returncode == 0
    except Exception:
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
        children=[
            ic,
            Box(
                orientation="v", h_align="start", v_align="center",
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
                 'network_status_button', 'network_menu_button', 'network_menu_label', '_last_ico')

    def __init__(self, widgets=None, notch=None):
        super().__init__()
        self._w, self._n = widgets, notch
        self._aid = self._uid = None
        self._ast = 0
        self._last_ico = None

        self._cl = NetworkClient()
        self._build()
        self._cl.connect('device-ready', self._ready)
        self._sched()

    def _build(self):
        self.network_icon = Label(name="network-icon")
        self.network_label = Label(name="network-label", label="Wi-Fi", justification="left")
        self.network_ssid = Label(name="network-ssid", justification="left")

        self.network_status_button = Button(
            name="network-status-button", h_expand=True,
            child=_content(self.network_icon, self.network_label, self.network_ssid),
            on_clicked=lambda *_: getattr(self._cl.wifi_device, "toggle_wifi", lambda: None)()
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
        if self._n: self._n.open_notch("network_applet")
        elif self._w and hasattr(self._w, 'show_network_applet'): self._w.show_network_applet()

    def _ready(self, *_):
        if wifi := self._cl.wifi_device:
            wifi.connect('notify::enabled', lambda *_: self._sched())
            wifi.connect('notify::ssid', lambda *_: self._sched())
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
            self.network_ssid.set_label(s[:10] + "..." if len(s) > 10 else s)
            
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
        self._cl = self._w = self._n = None


class BluetoothButton(Box):
    __slots__ = ('_w', '_n', '_en', '_sw', '_pending',
                 'bluetooth_icon', 'bluetooth_label', 'bluetooth_status_text',
                 'bluetooth_status_button', 'bluetooth_menu_button', 'bluetooth_menu_label')

    def __init__(self, widgets=None, notch=None):
        super().__init__()
        self._w, self._n = widgets, notch
        self._en = self._pending = False

        self._build()
        self.update_state()

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
                with open(os.path.join(base_dir, d, "type"), "r") as f:
                    if f.read().strip() == "bluetooth":
                        with open(os.path.join(base_dir, d, "state"), "r") as sf:
                            return sf.read().strip() == "1"
        except Exception:
            pass
        return False

    def _on_toggle_click(self, *_):
        if self._pending: return
        self._pending = True
        
        cmd = ["rfkill", "block" if self._en else "unblock", "bluetooth"]
        def worker():
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            GLib.timeout_add(300, self.update_state)

        threading.Thread(target=worker, daemon=True).start()

    def _upd_ui(self, en):
        self._pending = False
        if self._en == en: return False
        self._en = en
            
        self.bluetooth_icon.set_markup(icons.bluetooth if en else icons.bluetooth_off)
        self.bluetooth_status_text.set_label(_ON if en else _OFF)
        _dis(self._sw, not en)
        return False

    def update_state(self, *_):
        self._upd_ui(self._get_pwr())
        return False

    def _open_menu(self, *_):
        if self._n: self._n.open_notch("bluetooth")
        elif self._w and hasattr(self._w, 'show_bt'): self._w.show_bt()
        else: exec_shell_command_async("blueman-manager")

    def cleanup(self):
        self._w = self._n = None


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
        if _chk(self.PAT):
            exec_shell_command_async(self.STOP)
            return False
        exec_shell_command_async(self.START)
        return True

    def update_state(self, *_):
        _async_exec(lambda: _chk(self.PAT), self._upd)

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
                os.kill(self._pid, 0)
                os.kill(self._pid, 15)
                self._pid = None
                return False
            except OSError:
                self._pid = None

        if _chk(self.PAT):
            exec_shell_command_async(self.STOP)
            self._pid = None
            return False

        try:
            pid, _, _, _ = GLib.spawn_async(
                argv=["/bin/sh", "-c", self.START],
                flags=GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.DO_NOT_REAP_CHILD
            )
            self._pid = pid
            GLib.child_watch_add(GLib.PRIORITY_DEFAULT, pid, self._on_exit)
            return True
        except Exception:
            exec_shell_command_async(self.START)
            self._pid = None
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
    START = "python ~/.config/Vidgex-Shell/scripts/eyes-hands/eyes-hands.py"
    STOP = "pkill -f vidgex-eyes-hands"
    NAME, ICON, TEXT = "eyes-hands", icons.spy, "Eyes-Hands"


class AutolayoutButton(_ScriptToggleBtn):
    PAT = "vidgex-autolanguage" 
    START = 'python ~/.config/Vidgex-Shell/scripts/autolanguage.py'
    STOP = "pkill -f vidgex-autolanguage"
    NAME = "autolanguage"

    def __init__(self):
        super().__init__()

        self.set_tooltip_text("Auto Language")
        self.set_hexpand(False)
        
        if self.get_child():
            self.remove(self.get_child())
            
        self._ic = Label(name=f"{self.NAME}-icon", markup=icons.keyboard)
        
        custom_content = Box(
            h_align="center", v_align="center",
            children=[self._ic] 
        )
        
        self.add(custom_content)
        self.show_all()

    def _upd(self, en):
        if en:
            self.add_style_class("active")
        else:
            self.remove_style_class("active")
        return False


class Buttons(Gtk.Grid):
    def __init__(self, widgets=None, notch=None):
        super().__init__(name="buttons-grid")
        
        self.set_row_homogeneous(True)
        self.set_column_homogeneous(True)
        self.set_row_spacing(4)
        self.set_column_spacing(4)
        self.set_vexpand(False)

        btns = [
            NetworkButton(widgets, notch),
            BluetoothButton(widgets, notch),
            NightModeButton(),
            CaffeineButton(),
            EyesHandsButton()
        ]

        for i, btn in enumerate(btns):
            self.attach(btn, i, 0, 1, 1)

        self.network_button, self.bluetooth_button, self.night_mode_button, \
        self.caffeine_button, self.eyes_hands_button = btns

        self.show_all()