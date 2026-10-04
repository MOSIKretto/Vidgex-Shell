from bisect import bisect_right
import os
from signal import SIGKILL

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from fabric.bluetooth import BluetoothClient
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label

import services.icons as icons
from services.network import NetworkClient, Subscriptions

from modules.Notch.MainWindow.Dashboard.Buttons.caffeine import Caffeine
from modules.Notch.MainWindow.Dashboard.Buttons.workTime import WorkTime
from modules.Notch.MainWindow.Dashboard.Buttons.timer import TimerSplitButton, _cancel, _dis, _content
from modules.Notch.MainWindow.Dashboard.Buttons.network import NetworkConnections
from modules.Notch.MainWindow.Dashboard.Buttons.bluetooth import BluetoothConnections


_TH = (25, 50, 75)
_WI = (icons.wifi_0, icons.wifi_1, icons.wifi_2, icons.wifi_3)
_AN = (icons.wifi_0, icons.wifi_1, icons.wifi_2, icons.wifi_3, icons.wifi_2, icons.wifi_1)
_DISCONNECTED = "Disconnected"


def _pids(name: str) -> list[int]:
    found = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/comm") as f:
                if f.read().strip() == name:
                    found.append(int(entry))
        except (FileNotFoundError, ProcessLookupError):
            pass
    return found

def _kill(pids: list[int]) -> None:
    for pid in pids:
        try:
            os.kill(pid, SIGKILL)
        except ProcessLookupError:
            pass

def _text_label(name: str, **kwargs) -> Label:
    return Label(name=name, xalign=0, h_align="start", justification="left", **kwargs)

def _title_box(*children) -> Box:
    return Box(orientation="v", h_align="start", v_align="center", children=children)


class _StatusButton(Box):
    def __init__(self, widgets, client, prefix: str, title: str, icon_markup: str):
        super().__init__(name=f"{prefix}-button")
        self._w = widgets
        self._cl = client
        self._uid = None
        self._destroyed = False
        self._subs = Subscriptions()

        self._icon = Label(name=f"{prefix}-icon", markup=icon_markup)
        self._subtitle = _text_label(f"{prefix}-ssid")
        self._revealer = Gtk.Revealer(
            halign=Gtk.Align.START,
            valign=Gtk.Align.CENTER,
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            transition_duration=400,
        )
        self._revealer.add(self._subtitle)

        title_label = _text_label(f"{prefix}-label", label=title)
        status_button = Button(
            name=f"{prefix}-status-button",
            h_expand=True,
            child=_content(self._icon, _title_box(title_label, self._revealer)),
            on_clicked=self._on_status_click,
        )
        menu_label = Label(name=f"{prefix}-menu-label", markup=icons.chevron_right)
        menu_button = Button(
            name=f"{prefix}-menu-button",
            child=menu_label,
            on_clicked=self._on_menu_click,
        )
        self.add(status_button)
        self.add(menu_button)

        self._sw = (
            self, self._icon, title_label, self._subtitle, status_button, menu_button, menu_label,
        )
        self.connect("destroy", lambda *_: self.cleanup())

    def _toggle(self) -> None:
        raise NotImplementedError

    def _open_menu(self) -> None:
        raise NotImplementedError

    def update_state(self) -> None:
        raise NotImplementedError

    def _on_status_click(self, *_) -> None:
        if not self._destroyed:
            self._toggle()

    def _on_menu_click(self, *_) -> None:
        if not self._destroyed:
            self._open_menu()

    def _sched(self, *_) -> None:
        if self._uid is None:
            self._uid = GLib.timeout_add(100, self._flush)

    def _flush(self) -> bool:
        self._uid = None
        self.update_state()
        return False

    def _show_subtitle(self, text: str) -> None:
        self._subtitle.set_label(text[:6].rstrip() + "..." if len(text) > 6 else text)
        self._revealer.set_reveal_child(True)

    def _hide_subtitle(self) -> None:
        self._revealer.set_reveal_child(False)

    def cleanup(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        self._uid = _cancel(self._uid)
        self._subs.clear()
        self._cl = self._w = None


class NetworkButton(_StatusButton):
    def __init__(self, widgets=None):
        super().__init__(widgets, widgets.network_client, "network", "Wi-Fi", icons.wifi_off)
        self._aid = None
        self._ast = 0
        self._last_ico = icons.wifi_off
        self._wifi = None
        self._subs.connect(self._cl, "device-ready", self._on_ready)
        self._subs.connect(self._cl, "notify::primary-device", self._sched)
        self._on_ready()

    def _toggle(self) -> None:
        wifi = self._cl.wifi_device
        if wifi is not None:
            wifi.toggle_wifi()

    def _open_menu(self) -> None:
        self._w.show_network_applet()

    def _on_ready(self, *_) -> None:
        self._wifi = self._subs.rebind(self._wifi, self._cl.wifi_device, "changed", self._sched)
        self._sched()

    def _set_icon(self, markup: str) -> None:
        if self._last_ico != markup:
            self._icon.set_markup(markup)
            self._last_ico = markup

    def _start_anim(self) -> None:
        if self._aid is None:
            self._ast = 0
            self._aid = GLib.timeout_add(500, self._anim)

    def _stop_anim(self) -> None:
        self._aid = _cancel(self._aid)

    def _anim(self) -> bool:
        if self._destroyed:
            self._aid = None
            return False
        self._set_icon(_AN[self._ast])
        self._ast = (self._ast + 1) % len(_AN)
        return True

    def _show_static(self, icon: str) -> None:
        self._stop_anim()
        self._set_icon(icon)
        self._hide_subtitle()

    def update_state(self) -> None:
        if self._destroyed:
            return

        wifi, eth = self._cl.wifi_device, self._cl.ethernet_device
        disabled = wifi is not None and not wifi.enabled
        _dis(self._sw, disabled)

        if disabled:
            static = icons.wifi_off
        elif self._cl.primary_device == "ethernet":
            static = icons.world if eth.internet == "activated" else icons.world_off
        elif wifi is None:
            static = icons.wifi_off
        else:
            static = None
        if static is not None:
            self._show_static(static)
            return

        internet = wifi.internet
        if wifi.ssid in ("", _DISCONNECTED) or internet not in ("activating", "activated"):
            self._hide_subtitle()
            self._start_anim()
            return

        self._show_subtitle(wifi.ssid)
        if internet == "activated":
            self._stop_anim()
            self._set_icon(_WI[bisect_right(_TH, wifi.strength)])
        else:
            self._start_anim()

    def cleanup(self) -> None:
        self._stop_anim()
        self._wifi = None
        super().cleanup()


class BluetoothButton(_StatusButton):
    def __init__(self, widgets=None):
        super().__init__(widgets, widgets.bluetooth_client, "bluetooth", "Bluetooth", icons.bluetooth_off)
        self._en = None
        self._pending_id = None
        self._subs.connect(self._cl, "changed", self._sched)
        self._sched()

    def _toggle(self) -> None:
        if self._pending_id is not None:
            return
        target = not self._cl.powered
        self._cl.toggle_power()
        self._upd_ui(target, ())
        self._pending_id = GLib.timeout_add(1000, self._clear_pending)

    def _open_menu(self) -> None:
        self._w.show_bt()

    def _clear_pending(self) -> bool:
        self._pending_id = None
        self.update_state()
        return False

    def _upd_ui(self, en: bool, connected) -> None:
        if self._en != en:
            self._en = en
            self._icon.set_markup(icons.bluetooth if en else icons.bluetooth_off)
            _dis(self._sw, not en)

        if not (en and connected):
            self._hide_subtitle()
            return

        dev = min(connected, key=lambda d: (d.alias or d.name or "").lower())
        self._show_subtitle(dev.alias or dev.name or dev.address)

    def update_state(self, *_) -> bool:
        if self._destroyed:
            return False
        if self._pending_id is None:
            self._upd_ui(self._cl.enabled, self._cl.connected_devices)
        return False

    def cleanup(self) -> None:
        self._pending_id = _cancel(self._pending_id)
        super().cleanup()


class _StateTimerButton(TimerSplitButton):
    def _sync_active(self, active: bool) -> None:
        if active and not self._is_running:
            self.start_timer()
        elif not active and self._is_running:
            self.stop_timer()
        else:
            self._dis_ui(not active)


class NightModeButton(_StateTimerButton):
    PAT, START = "hyprsunset", "hyprsunset -t 3500"

    def __init__(self):
        super().__init__(
            name_prefix="night-mode",
            icon_markup=icons.night,
            title_widget=_title_box(
                _text_label("night-mode-label", label="Night"),
                _text_label("night-mode-label", label="Mode"),
            ),
            default_seconds=3600,
        )
        self.update_state()

    def _on_status_click(self, *_):
        pids = _pids(self.PAT)
        if pids:
            _kill(pids)
            self.stop_timer()
        else:
            GLib.spawn_command_line_async(self.START)
            self.start_timer()

    def _on_timer_finished(self):
        _kill(_pids(self.PAT))

    def update_state(self, *_):
        self._sync_active(bool(_pids(self.PAT)))

    def cleanup(self):
        if self._destroyed:
            return
        super().cleanup()
        _kill(_pids(self.PAT))


class CaffeineButton(_StateTimerButton):
    def __init__(self):
        self._caffeine = Caffeine()
        super().__init__(
            name_prefix="caffeine",
            icon_markup=icons.coffee,
            title_widget=_title_box(_text_label("caffeine-label", label="Caffeine")),
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
        self._sync_active(self._caffeine.is_enabled)
        return False

    def cleanup(self):
        if self._destroyed:
            return
        super().cleanup()
        self._caffeine.disable()


class WorkTimeButton(TimerSplitButton):
    def __init__(self):
        self._work_time = WorkTime(update_callback=self._on_work_time_update)
        super().__init__(
            name_prefix="work-time",
            icon_markup=icons.clock,
            title_widget=_title_box(
                _text_label("work-time-label", label="Work"),
                _text_label("work-time-label", label="Time"),
            ),
            default_seconds=self._work_time.WORK_TIME,
        )
        self._push_duration()
        self.update_state()

    def _push_duration(self) -> None:
        self._work_time.WORK_TIME = self._duration
        self._work_time.remaining = self._duration

    def _on_status_click(self, *_):
        self._work_time.toggle()
        self.update_state()

    def _on_work_time_update(self, status_text: str, is_running: bool):
        if self._destroyed:
            return
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
        self._push_duration()
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
    def __init__(self, dashboard=None):
        super().__init__(name="buttons-grid")

        self.set_row_homogeneous(True)
        self.set_column_homogeneous(True)
        self.set_row_spacing(4)
        self.set_column_spacing(4)
        self.set_vexpand(False)

        self._dashboard = dashboard
        self._destroyed = False
        self.network_client = NetworkClient()
        self.bluetooth_client = BluetoothClient()
        self.network_connections = NetworkConnections(widgets=self)
        self.bluetooth = BluetoothConnections(widgets=self)

        self.network_button = NetworkButton(widgets=self)
        self.bluetooth_button = BluetoothButton(widgets=self)
        self.night_mode_button = NightModeButton()
        self.caffeine_button = CaffeineButton()
        self.work_time_button = WorkTimeButton()

        for i, btn in enumerate((
            self.network_button, self.bluetooth_button, self.night_mode_button,
            self.caffeine_button, self.work_time_button,
        )):
            self.attach(btn, i, 0, 1, 1)

        self.connect("destroy", lambda *_: self.cleanup())
        self.show_all()

    def show_network_applet(self) -> None:
        self._dashboard.show_network_applet()

    def show_bt(self) -> None:
        self._dashboard.show_bt()

    def show_notif(self) -> None:
        self._dashboard.show_notif()

    def cleanup(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        for child in self.get_children():
            child.cleanup()
        self.bluetooth.cleanup()
        self.network_connections.cleanup()
        self.network_client.cleanup()