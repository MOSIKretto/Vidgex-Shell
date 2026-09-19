import json
import os
import random
import threading
from gi.repository import Gdk, GLib, Gtk

from fabric.utils.helpers import exec_shell_command_async
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label
from fabric.bluetooth import BluetoothClient

import services.icons as icons

from modules.Notch.MainWindow.Dashboard.network import NetworkClient
from modules.Notch.MainWindow.Dashboard.Buttons.caffeine import Caffeine
from modules.Notch.MainWindow.Dashboard.Buttons.workTime import WorkTime


_TH = (25, 50, 75)
_WI = (icons.wifi_0, icons.wifi_1, icons.wifi_2, icons.wifi_3)
_AN = (icons.wifi_0, icons.wifi_1, icons.wifi_2, icons.wifi_3, icons.wifi_2, icons.wifi_1)

_GLITCH_CLASSES = [
    "glitch-shift-right",
    "glitch-shift-left",
    "glitch-flicker",
    "glitch-aberration",
    "glitch-heavy",
    "glitch-color-swap",
]

CACHE_DIR = os.path.expanduser("~/.cache/vidgex-shell")
SETTINGS_FILE = os.path.join(CACHE_DIR, "timer_settings.json")


def _load_saved_durations() -> dict:
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    return {}


def _save_duration(key: str, value: int) -> None:
    def worker():
        os.makedirs(CACHE_DIR, exist_ok=True)
        data = _load_saved_durations()
        data[key] = value
        with open(SETTINGS_FILE, "w") as f:
            json.dump(data, f, indent=2)

    threading.Thread(target=worker, daemon=True).start()


def _ent(w, _):
    win = w.get_window()
    if win:
        if not getattr(w, "_cursor", None):
            w._cursor = Gdk.Cursor.new_from_name(w.get_display(), "pointer")
        win.set_cursor(w._cursor)


def _lv(w, _):
    win = w.get_window()
    if win:
        win.set_cursor(None)


def _hover(w):
    w.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
    w._cursor = None
    w.connect("enter-notify-event", _ent)
    w.connect("leave-notify-event", _lv)


def _fast_chk(pat: str) -> bool:
    pat_b = pat.encode()
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            cmd_path = f"/proc/{pid}/cmdline"
            if os.path.exists(cmd_path):
                with open(cmd_path, "rb") as f:
                    data = f.read()
                    if pat_b in data or pat_b in data.replace(b"\x00", b" "):
                        return True
    return False


def _async_exec(target, callback=None):
    def worker():
        res = target()
        if callback:
            GLib.idle_add(callback, res)

    threading.Thread(target=worker, daemon=True).start()


def _dis(ws, disabled: bool):
    m = "add_style_class" if disabled else "remove_style_class"
    for w in ws:
        getattr(w, m)("disabled")


def _get_all_labels(widget) -> list:
    labels = []
    if isinstance(widget, Label):
        labels.append(widget)
    elif isinstance(widget, (Box, Gtk.Container)):
        for child in widget.get_children():
            labels.extend(_get_all_labels(child))
    return labels


def _content(ic: Label, title_box: Box) -> Box:
    return Box(h_align="start", v_align="center", spacing=10, children=(ic, title_box))


class TimerWidget(Box):
    def __init__(self, name_prefix: str):
        super().__init__(orientation="v", v_align="center", h_align="center")
        self.hh_label = Label(name=f"{name_prefix}-timer-hh", label="00", xalign=0.5)
        self.mm_label = Label(name=f"{name_prefix}-timer-mm", label="05", xalign=0.5)
        self.add(self.hh_label)
        self.add(self.mm_label)

        self._last_str = ""
        self._gl_rem = 0
        self._gl_tid = None

    def update_time(self, total_seconds: int):
        total_minutes = total_seconds // 60
        hh = min(24, total_minutes // 60)
        mm = total_minutes % 60 if hh < 24 else 0
        time_str = f"{hh:02d}:{mm:02d}"

        if self._last_str and time_str != self._last_str:
            self._trigger_glitch()

        self._last_str = time_str
        self.hh_label.set_label(f"{hh:02d}")
        self.mm_label.set_label(f"{mm:02d}")

    def _trigger_glitch(self):
        self._gl_rem = 6
        if self._gl_tid is None:
            self._gl_tid = GLib.timeout_add(35, self._glitch_tick)

    def _clear_glitch(self):
        for lbl in (self.hh_label, self.mm_label):
            ctx = lbl.get_style_context()
            for cls in _GLITCH_CLASSES:
                ctx.remove_class(cls)
            ctx.remove_class("glitching")

    def _glitch_tick(self) -> bool:
        self._clear_glitch()
        if self._gl_rem > 0:
            for lbl in (self.hh_label, self.mm_label):
                lbl.get_style_context().add_class("glitching")
                for cls in random.sample(_GLITCH_CLASSES, random.randint(1, 2)):
                    lbl.get_style_context().add_class(cls)
            self._gl_rem -= 1
            return True
        self._gl_tid = None
        return False


class _TimerSplitButton(Box):
    def __init__(self, name_prefix: str, icon_markup: str, title_widget: Box, default_seconds: int = 1500):
        super().__init__(name=f"{name_prefix}-button")
        self._name_prefix = name_prefix

        saved_durations = _load_saved_durations()
        if name_prefix in saved_durations:
            default_seconds = saved_durations[name_prefix]

        self._duration = default_seconds
        self._remaining = default_seconds
        self._is_running = False
        self._timer_id = None
        self._scroll_acc = 0.0

        self.icon = Label(name=f"{name_prefix}-icon", markup=icon_markup)
        self.status_button = Button(
            name=f"{name_prefix}-status-button",
            h_expand=True,
            child=_content(self.icon, title_widget),
            on_clicked=self._on_status_click,
        )
        _hover(self.status_button)

        self.timer_widget = TimerWidget(name_prefix)
        self.timer_button = Button(
            name=f"{name_prefix}-timer-button",
            child=self.timer_widget,
            on_clicked=self._on_timer_click,
        )

        self.timer_button.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        self.timer_button.connect("scroll-event", self._on_timer_scroll)
        _hover(self.timer_button)

        self.add(self.status_button)
        self.add(self.timer_button)

        title_labels = _get_all_labels(title_widget)
        self._sw = (
            self,
            self.icon,
            self.status_button,
            self.timer_button,
            self.timer_widget.hh_label,
            self.timer_widget.mm_label,
            *title_labels,
        )
        self._dis_ui(True)
        self.update_timer_display()

    def _dis_ui(self, disabled: bool):
        _dis(self._sw, disabled)

    def update_timer_display(self):
        sec = self._remaining if self._is_running else self._duration
        self.timer_widget.update_time(sec)

    def _on_timer_click(self, *_):
        self._on_status_click()

    def _on_timer_scroll(self, widget, event: Gdk.EventScroll) -> bool:
        if self._is_running:
            return True

        delta_minutes = 0
        if event.direction == Gdk.ScrollDirection.UP:
            delta_minutes = 1
        elif event.direction == Gdk.ScrollDirection.DOWN:
            delta_minutes = -1
        elif event.direction == Gdk.ScrollDirection.SMOOTH:
            _, _, dy = event.get_scroll_deltas()
            if dy != 0.0:
                self._scroll_acc += dy
                threshold = 0.25
                if abs(self._scroll_acc) >= threshold:
                    steps = int(self._scroll_acc / threshold)
                    delta_minutes = -steps
                    self._scroll_acc -= steps * threshold

        if delta_minutes != 0:
            self._duration = max(60, min(86400, self._duration + delta_minutes * 60))
            self._remaining = self._duration
            self.update_timer_display()
            _save_duration(self._name_prefix, self._duration)

        return True

    def _trigger_tick_pulse(self):
        ctx = self.timer_button.get_style_context()
        ctx.add_class("tick-pulse")

        def _clear():
            ctx.remove_class("tick-pulse")
            return False

        GLib.timeout_add(120, _clear)

    def start_timer(self):
        if not self._is_running:
            self._is_running = True
            self._remaining = self._duration
            if self._timer_id is None:
                self._timer_id = GLib.timeout_add(1000, self._tick)
            self._dis_ui(False)
            self.update_timer_display()

    def stop_timer(self):
        if self._is_running:
            self._is_running = False
            if self._timer_id:
                GLib.source_remove(self._timer_id)
                self._timer_id = None
            self._remaining = self._duration
            self._dis_ui(True)
            self.update_timer_display()

    def _tick(self) -> bool:
        if not self._is_running:
            self._timer_id = None
            return False
        self._remaining -= 1
        self._trigger_tick_pulse()
        if self._remaining <= 0:
            self._remaining = 0
            self.update_timer_display()
            self._on_timer_finished()
            self.stop_timer()
            return False
        self.update_timer_display()
        return True

    def _on_status_click(self, *_):
        pass

    def _on_timer_finished(self):
        pass

    def cleanup(self):
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = None


class NetworkButton(Box):
    __slots__ = (
        "_w", "_cl", "_aid", "_uid", "_ast", "_sw",
        "network_icon", "network_label", "network_ssid", "network_ssid_revealer",
        "network_status_button", "network_menu_button", "network_menu_label",
        "_last_ico", "_en_hid", "_ssid_hid", "_title_box",
    )

    def __init__(self, widgets=None):
        super().__init__(name="network-button")
        self._w = widgets
        self._aid = self._uid = None
        self._ast = 0
        self._last_ico = None
        self._en_hid = self._ssid_hid = None

        self._cl = NetworkClient()
        self._build()
        self._cl.connect("device-ready", self._ready)
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
            if wifi := getattr(self._cl, "wifi_device", None):
                wifi.toggle_wifi()

        self.network_status_button = Button(
            name="network-status-button",
            h_expand=True,
            child=_content(self.network_icon, self._title_box),
            on_clicked=_tog,
        )
        _hover(self.network_status_button)

        self.network_menu_label = Label(name="network-menu-label", markup=icons.chevron_right)
        self.network_menu_button = Button(
            name="network-menu-button",
            child=self.network_menu_label,
            on_clicked=self._menu_click,
        )
        _hover(self.network_menu_button)

        self.add(self.network_status_button)
        self.add(self.network_menu_button)

        self._sw = (
            self, self.network_icon, self.network_label, self.network_ssid,
            self.network_status_button, self.network_menu_button, self.network_menu_label,
        )

    def _menu_click(self, *_):
        if self._w and hasattr(self._w, "show_network_applet"):
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
            self.network_ssid_revealer.set_reveal_child(False)
            _dis(self._sw, True)
            return

        _dis(self._sw, False)

        if getattr(self._cl, "primary_device", "wireless") == "wired":
            self._stop_anim()
            self._set_icon(icons.world if eth and getattr(eth, "internet", "") == "activated" else icons.world_off)
            self.network_ssid_revealer.set_reveal_child(False)
            return

        if not wifi:
            self._stop_anim()
            self._set_icon(icons.wifi_off)
            self.network_ssid_revealer.set_reveal_child(False)
            return

        if wifi.state == "activated" and wifi.ssid and wifi.ssid != "Отключено":
            self._stop_anim()
            s = wifi.ssid
            self.network_ssid.set_label(s[:6].rstrip() + "..." if len(s) > 10 else s)
            self.network_ssid_revealer.set_reveal_child(True)

            st = wifi.strength
            ic = _WI[0] if st < _TH[0] else (_WI[1] if st < _TH[1] else (_WI[2] if st < _TH[2] else _WI[3]))
            self._set_icon(ic)
        else:
            self.network_ssid_revealer.set_reveal_child(False)
            self._start_anim()

    def cleanup(self):
        self._stop_anim()
        if self._uid:
            GLib.source_remove(self._uid)
            self._uid = None

        if wifi := getattr(self._cl, "wifi_device", None):
            if self._en_hid:
                wifi.disconnect(self._en_hid)
            if self._ssid_hid:
                wifi.disconnect(self._ssid_hid)

        self._cl = self._w = None


class BluetoothButton(Box):
    __slots__ = (
        "_w", "_en", "_cl", "_sw", "_pending",
        "bluetooth_icon", "bluetooth_label",
        "bluetooth_status_button", "bluetooth_menu_button", "bluetooth_menu_label",
    )

    def __init__(self, widgets=None):
        super().__init__(name="bluetooth-button")
        self._w = widgets
        self._en = self._pending = False
        self._cl = BluetoothClient()

        self._build()
        self._cl.connect("notify::enabled", self.update_state)
        GLib.idle_add(self.update_state)

    def _build(self):
        self.bluetooth_icon = Label(name="bluetooth-icon", markup=icons.bluetooth_off)
        self.bluetooth_label = Label(name="bluetooth-label", label="Bluetooth", xalign=0, h_align="start", justification="left")

        title_box = Box(orientation="v", h_align="start", v_align="center", children=(self.bluetooth_label,))

        self.bluetooth_status_button = Button(
            name="bluetooth-status-button",
            h_expand=True,
            child=_content(self.bluetooth_icon, title_box),
            on_clicked=self._on_toggle_click,
        )
        _hover(self.bluetooth_status_button)

        self.bluetooth_menu_label = Label(name="bluetooth-menu-label", markup=icons.chevron_right)
        self.bluetooth_menu_button = Button(
            name="bluetooth-menu-button",
            child=self.bluetooth_menu_label,
            on_clicked=self._open_menu,
        )
        _hover(self.bluetooth_menu_button)

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
        self._upd_ui(not en)
        GLib.timeout_add(1000, self._clear_pending)

    def _clear_pending(self) -> bool:
        self._pending = False
        self.update_state()
        return False

    def _upd_ui(self, en: bool):
        if self._en == en:
            return
        self._en = en
        self.bluetooth_icon.set_markup(icons.bluetooth if en else icons.bluetooth_off)
        _dis(self._sw, not en)

    def update_state(self, *_):
        if not self._pending:
            self._upd_ui(self._get_pwr())
        return False

    def _open_menu(self, *_):
        if self._w and hasattr(self._w, "show_bt"):
            self._w.show_bt()

    def cleanup(self):
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
        if _fast_chk(self.PAT):
            exec_shell_command_async(self.STOP)
            self.stop_timer()
        else:
            exec_shell_command_async(self.START)
            self.start_timer()

    def _on_timer_finished(self):
        exec_shell_command_async(self.STOP)

    def update_state(self, *_):
        _async_exec(lambda: _fast_chk(self.PAT), self._upd_proc)

    def _upd_proc(self, active: bool) -> bool:
        if active and not self._is_running:
            self.start_timer()
        elif not active and self._is_running:
            self.stop_timer()
        else:
            self._dis_ui(not active)
        return False


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
            if hasattr(child, "cleanup"):
                child.cleanup()