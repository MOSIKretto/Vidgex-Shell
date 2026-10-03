import json
import os
import threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.stack import Stack

import services.icons as icons
from modules.Notch.MainWindow.Dashboard.Buttons.Network.network import Subscriptions
from modules.Notch.MainWindow.Dashboard.Buttons.network import (
    _cancel, _cls, _header_button, _scroll, _section, _styled,
)


CACHE_DIR = os.path.expanduser("~/.cache/vidgex-shell")
KNOWN_DEVICES_FILE = os.path.join(CACHE_DIR, "bluetooth_known.json")

_ADDR_SEPARATORS = str.maketrans("", "", ":-")
_PLACEHOLDER_NAMES = ("", "Unknown", "unknown")
_YES_NO = {None: "Unknown", True: "Yes", False: "No"}
_PAGE_TITLES = {"main": "Bluetooth", "saved": "Saved Devices"}


class _KnownStore:
    """Сохранённые устройства {address: name} с фоновой записью на диск.

    Запись одна за раз: пока поток пишет, новые изменения лишь ставят флаг
    «нужна ещё одна запись» — поток на каждое изменение не спавнится.
    Всё состояние трогается только из главного потока.
    """

    __slots__ = ("devices", "_writing", "_dirty")

    def __init__(self):
        self.devices: dict[str, str] = {}
        if os.path.exists(KNOWN_DEVICES_FILE):
            with open(KNOWN_DEVICES_FILE, "r") as f:
                self.devices = json.load(f)
        self._writing = self._dirty = False

    def mark(self, address: str, name: str) -> bool:
        """Возвращает True, если запись реально изменилась."""
        if self.devices.get(address) == name:
            return False
        self.devices[address] = name
        self._save()
        return True

    def forget(self, address: str) -> None:
        if self.devices.pop(address, None) is not None:
            self._save()

    def _save(self) -> None:
        if self._writing:
            self._dirty = True
            return
        self._writing = True
        data = json.dumps(self.devices, indent=2)
        threading.Thread(target=self._write, args=(data,), daemon=True).start()

    def _write(self, data: str) -> None:
        # Фоновый поток. Запись через tmp + replace атомарна: обрыв не оставит обрезанный JSON.
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            tmp = KNOWN_DEVICES_FILE + ".tmp"
            with open(tmp, "w") as f:
                f.write(data)
            os.replace(tmp, KNOWN_DEVICES_FILE)
        finally:
            GLib.idle_add(self._on_written)

    def _on_written(self) -> bool:
        self._writing = False
        if self._dirty:
            self._dirty = False
            self._save()
        return False


def _run_bt_cmd(argv: list[str], callback) -> None:
    """Запускает bluetoothctl без shell; callback вызывается в главном потоке по завершении процесса."""
    def _on_exit(pid, _status, *_args):
        GLib.spawn_close_pid(pid)
        callback()

    pid, _, _, _ = GLib.spawn_async(
        argv,
        flags=(
            GLib.SpawnFlags.SEARCH_PATH
            | GLib.SpawnFlags.DO_NOT_REAP_CHILD
            | GLib.SpawnFlags.STDOUT_TO_DEV_NULL
            | GLib.SpawnFlags.STDERR_TO_DEV_NULL
        ),
    )
    GLib.child_watch_add(GLib.PRIORITY_DEFAULT, pid, _on_exit)


def _get_dev_name(dev) -> str | None:
    """Имя для показа; None — имени нет или это пустышка («Unknown», MAC вместо имени)."""
    name = dev.alias or dev.name
    if not name or name.strip() in _PLACEHOLDER_NAMES:
        return None
    # Имя-MAC может отличаться от address разделителями и регистром.
    if name.translate(_ADDR_SEPARATORS).strip().upper() == dev.address.translate(_ADDR_SEPARATORS).upper():
        return None
    return name


def _display_name(dev) -> str:
    # У живых устройств в списках имя есть всегда. У «призрака» с именем-MAC
    # _get_dev_name вернёт None — тогда показываем сохранённое имя как есть.
    return _get_dev_name(dev) or dev.name


def _status_page(icon: str, text: str, button_label: str, handler):
    glyph = _styled(Label(markup=f"<span size='32768'>{icon}</span>"), "bluetooth-off-icon")
    message = _styled(Label(label=text), "bluetooth-off-label")
    button = _styled(
        Button(label=button_label, h_align="center", on_clicked=handler), "bluetooth-turn-on-btn",
    )
    return Box(
        orientation="v", v_align="center", h_align="center", spacing=12, v_expand=True,
        children=(glyph, message, button),
    )


class _GhostDevice:
    """Сохранённое устройство, которого сейчас нет среди устройств клиента."""

    __slots__ = ("address", "name")

    alias = None
    icon_name = None
    connected = False
    connecting = False
    paired = None
    trusted = None

    def __init__(self, address: str, name: str):
        self.address = address
        self.name = name


class BTSlot(Gtk.EventBox):
    def __init__(self, parent_bt):
        super().__init__()
        self.parent_bt = parent_bt
        self.dev = None
        self.connect("button-press-event", self._on_click)

        self.main_box = _styled(CenterBox(), "pixel-slot")
        self.icon = Image(size=24)
        self.name_lbl = Label(h_expand=True, h_align="start", ellipsization="end")
        self.status_lbl = Label(h_expand=True, h_align="start", name="dim-label")

        self.btn_settings = _styled(
            Button(
                child=Label(markup=icons.settings),
                tooltip_text="Device Settings",
                on_clicked=self._on_settings,
            ),
            "pixel-icon-button", "settings-btn",
        )
        self.btn_settings.set_valign(Gtk.Align.CENTER)

        self.main_box.add_start(Box(
            spacing=12, v_align="center",
            children=(self.icon, Box(orientation="v", children=(self.name_lbl, self.status_lbl))),
        ))
        self.main_box.add_end(self.btn_settings)
        self.add(self.main_box)
        # Дети показываются один раз здесь; видимость шестерёнки дальше ведёт update().
        self.show_all()

    def update(self, dev) -> None:
        self.dev = dev
        connected = dev.connected
        known = self.parent_bt.is_known(dev.address)

        self.icon.set_from_icon_name(f"{dev.icon_name or 'bluetooth'}-symbolic", 24)
        self.name_lbl.set_label(_display_name(dev))
        self.btn_settings.set_visible(known or connected)

        for widget, css in (
            (self.main_box, "active-slot"),
            (self.icon, "active-icon"),
            (self.btn_settings, "active-settings-btn"),
        ):
            _cls(widget, css, connected)

        # connecting не различает направление: у подключённого устройства это отключение.
        if dev.connecting:
            status = "Disconnecting..." if connected else "Connecting..."
        elif connected:
            status = "Connected"
        else:
            status = "Saved" if known else "Available"
        self.status_lbl.set_label(status)

    def _on_click(self, _widget, _event) -> None:
        status = self.parent_bt.toggle_connection(self.dev)
        if status is not None:
            self.status_lbl.set_label(status)

    def _on_settings(self, _btn) -> None:
        self.parent_bt.open_settings(self.dev)


class BluetoothConnections(Box):
    def __init__(self, widgets, **kwargs):
        self._w = widgets
        super().__init__(
            name="bluetooth",
            spacing=4,
            orientation="vertical",
            h_expand=True,
            v_expand=True,
            v_align="fill",
            **kwargs,
        )

        self._cl = widgets.bluetooth_client

        self._rid = self._scan_id = None
        self._destroyed = False
        self.current_settings_dev = None
        self._previous_page = "main"
        self._slots: dict[str, list[BTSlot]] = {"connected": [], "avail": [], "saved": []}
        # address -> устройство, на чей "changed" мы подписаны.
        self._watched: dict[str, object] = {}
        self._known = _KnownStore()
        self._subs = Subscriptions()

        self._build()
        self._subs.connect(self._cl, "notify::enabled", self._on_enabled)
        # enabled считается из state, а notify::enabled эмитится только по смене powered:
        # порядок powered/state не гарантирован, поэтому слушаем и state.
        self._subs.connect(self._cl, "notify::state", self._on_enabled)
        self._subs.connect(self._cl, "device-added", self._sched)
        self._subs.connect(self._cl, "device-removed", self._on_device_removed)
        self.connect("destroy", lambda *_: self.cleanup())
        self._on_enabled()

    def _build(self) -> None:
        self.scan_btn = _header_button("bluetooth-scan", icons.radar, "Scan", self._on_scan)
        self.saved_btn = _header_button(
            "bluetooth-saved", icons.save, "Saved Devices", lambda *_: self._toggle_page("saved"),
        )
        back = Button(
            name="bluetooth-back",
            child=Label(markup=icons.chevron_left, name="bluetooth-back-label"),
            on_clicked=self._on_back_click,
        )
        self.header_title = Label(label="Bluetooth", v_align="center", name="header-title")

        header = CenterBox(
            start_children=(back,),
            center_children=(self.header_title,),
            end_children=(Box(
                spacing=4, orientation="horizontal",
                children=(self.saved_btn, self.scan_btn),
            ),),
        )
        header.set_margin_bottom(8)
        self.add(header)

        self.stack = Stack(
            transition_type="crossfade", h_expand=True, v_expand=True, v_align="fill",
        )
        self.add(self.stack)
        self.stack.add_named(
            _status_page(icons.bluetooth_off, "Bluetooth is disabled", "Turn On", self._turn_on_bt),
            "off",
        )

        self.lists_stack = Stack(
            transition_type="slide-left-right", h_expand=True, v_expand=True, v_align="fill",
        )

        self.connected_box = Box(spacing=2, orientation="vertical")
        self.avail_box = Box(spacing=2, orientation="vertical")
        self.saved_box = Box(spacing=2, orientation="vertical")

        avail_empty = _status_page(icons.radar, "No devices found", "Scan", self._on_scan)
        avail_empty.set_margin_top(24)
        avail_empty.set_margin_bottom(24)

        self.avail_stack = Stack(transition_type="crossfade", h_expand=True, v_expand=True)
        self.avail_stack.add_named(self.avail_box, "list")
        self.avail_stack.add_named(avail_empty, "empty")
        self.avail_section = _section("Available Devices", self.avail_stack)

        self.main_scroll = _scroll(Box(
            spacing=4, orientation="vertical", children=(self.connected_box, self.avail_section),
        ))
        self.saved_scroll = _scroll(Box(
            spacing=4, orientation="vertical", children=(_section("Saved Devices", self.saved_box),),
        ))
        self.settings_scroll = self._build_settings_page()
        for name, page in (
            ("main", self.main_scroll), ("saved", self.saved_scroll), ("settings", self.settings_scroll),
        ):
            self.lists_stack.add_named(page, name)

        self.stack.add_named(self.lists_stack, "on")

    def _build_settings_page(self):
        def action(icon, text, handler, *extra):
            return _styled(
                Button(
                    child=Label(markup=f"<span size='large'>{icon}</span> {text}"),
                    on_clicked=handler,
                ),
                "net-action-btn", *extra,
            )

        self.btn_bt_forget = action(icons.trash, "Remove", self._do_forget, "net-forget")
        self.btn_bt_disconnect = action(icons.cancel, "Disconnect", self._do_disconnect_or_connect)
        self.lbl_bt_disconnect = self.btn_bt_disconnect.get_child()

        actions_box = Box(
            orientation="horizontal", spacing=8, h_align="center", h_expand=True,
            children=(self.btn_bt_forget, self.btn_bt_disconnect),
        )
        actions_box.set_margin_bottom(12)

        # Подписи значений по ключам словаря в open_settings().
        self._info = {}
        info_group = _styled(Box(orientation="vertical", spacing=2), "net-info-group")
        for key, title in (("address", "MAC Address"), ("paired", "Paired"), ("trusted", "Trusted")):
            value = Label(label="-", h_align="end", selectable=key == "address")
            self._info[key] = value
            row = _styled(Box(orientation="horizontal"), "net-info-row")
            row.pack_start(Label(label=title, h_align="start", name="dim-label"), True, True, 0)
            row.pack_end(value, False, False, 0)
            info_group.add(row)

        settings_box = Box(orientation="vertical", spacing=8, h_expand=True)
        settings_box.set_margin_start(12)
        settings_box.set_margin_end(12)
        for child in (actions_box, info_group):
            settings_box.add(child)
        return _scroll(settings_box)

    # ---------- Сохранённые устройства и подключение ----------

    def is_known(self, address: str) -> bool:
        return address in self._known.devices

    def mark_known(self, address: str, name: str) -> None:
        if self._known.mark(address, name):
            self._sched()

    def toggle_connection(self, dev) -> str | None:
        """Единая точка connect/disconnect для слота и страницы настроек.

        Возвращает подпись статуса для немедленной обратной связи
        или None, если действие не начато (устройство уже подключается).
        fabric не эмитит changed при старте connecting, только по завершению,
        поэтому подпись выставляется сразу здесь.
        """
        if isinstance(dev, _GhostDevice):
            _run_bt_cmd(["bluetoothctl", "connect", dev.address], self.request_refresh)
            return "Connecting..."
        if dev.connecting:
            return None
        if dev.connected:
            dev.connected = False
            return "Disconnecting..."
        self.mark_known(dev.address, _display_name(dev))
        dev.connected = True
        return "Connecting..."

    # ---------- Страницы ----------

    def open_settings(self, dev) -> None:
        self._previous_page = self.lists_stack.get_visible_child_name()
        self.current_settings_dev = dev

        icon, text = (icons.cancel, "Disconnect") if dev.connected else (icons.accept, "Connect")
        self.lbl_bt_disconnect.set_markup(f"<span size='large'>{icon}</span> {text}")

        shown = {
            "address": dev.address,
            "paired": _YES_NO[dev.paired],
            "trusted": _YES_NO[dev.trusted],
        }
        for key, label in self._info.items():
            label.set_label(shown[key])

        self._show_page("settings", _display_name(dev))

    def _show_page(self, name: str, title: str | None = None) -> None:
        """Единая точка смены подстраницы: стек, заголовок, кнопки шапки, состояние pressed."""
        self.lists_stack.set_visible_child_name(name)
        self.header_title.set_label(title or _PAGE_TITLES[name])
        for btn in (self.scan_btn, self.saved_btn):
            btn.set_visible(name != "settings")
        _cls(self.saved_btn, "pressed", name == "saved")

    def _toggle_page(self, target: str) -> None:
        current = self.lists_stack.get_visible_child_name()
        self._show_page("main" if current == target else target)

    def _on_back_click(self, _btn) -> None:
        page = self.lists_stack.get_visible_child_name()
        if page == "settings":
            self._show_page(self._previous_page)
        elif page == "saved":
            self._show_page("main")
        else:
            self._w.show_notif()

    def _do_forget(self, _btn) -> None:
        address = self.current_settings_dev.address
        # remove у BlueZ сам разрывает соединение и стирает pairing/trust.
        _run_bt_cmd(["bluetoothctl", "remove", address], lambda: self._on_forgotten(address))
        self._show_page(self._previous_page)

    def _on_forgotten(self, address: str) -> None:
        # Забываем только после remove: иначе _ref успеет заново занести ещё спаренное устройство.
        self._known.forget(address)
        self.request_refresh()

    def _do_disconnect_or_connect(self, _btn) -> None:
        if self.toggle_connection(self.current_settings_dev) is not None:
            self._show_page(self._previous_page)

    # ---------- Питание и сканирование ----------

    def _turn_on_bt(self, *_args) -> None:
        self._cl.powered = True

    def _sync_enabled(self) -> bool:
        """Приводит UI к состоянию адаптера: страница on/off, доступность кнопок шапки.

        Идемпотентна и дёшева (читает кэш клиента), поэтому вызывается синхронно
        на каждое изменение, а не через отложенный _ref.
        """
        enabled = self._cl.enabled
        self.stack.set_visible_child_name("on" if enabled else "off")
        for btn in (self.scan_btn, self.saved_btn):
            btn.set_sensitive(enabled)
        if not enabled and self.lists_stack.get_visible_child_name() != "main":
            self._show_page("main")
            self.current_settings_dev = None
        return enabled

    def _on_enabled(self, *_args) -> None:
        self._sync_enabled()
        self._sched()

    def _set_scanning(self, on: bool) -> None:
        for widget in (self.scan_btn, self.scan_btn.get_child()):
            _cls(widget, "scanning", on)

    def _on_scan(self, _btn) -> None:
        if self._scan_id is not None or not self._cl.enabled:
            return
        self._set_scanning(True)
        self._cl.scanning = True
        self._scan_id = GLib.timeout_add(4000, self._on_scan_done)

    def _on_scan_done(self) -> bool:
        self._scan_id = None
        self._set_scanning(False)
        self._cl.scanning = False
        self.request_refresh()
        return False

    # ---------- Устройства и обновление списков ----------

    def _sync_devices(self, devices) -> None:
        """Идемпотентно приводит подписки на "changed" к текущему списку устройств."""
        live = {dev.address: dev for dev in devices}
        for address, old in list(self._watched.items()):
            if live.get(address) is not old:
                self._subs.release(self._watched.pop(address))
        for address, dev in live.items():
            if address not in self._watched:
                self._subs.connect(dev, "changed", self._sched)
                self._watched[address] = dev

    def _on_device_removed(self, _client, address: str) -> None:
        # Отписываемся сразу, не дожидаясь _ref.
        dev = self._watched.pop(address, None)
        if dev is not None:
            self._subs.release(dev)
        self._sched()

    def _sched(self, *_args) -> None:
        if self._rid is None:
            self.request_refresh()

    def request_refresh(self, delay: int = 300) -> None:
        """Единая точка отложенного обновления: новый запрос заменяет ожидающий."""
        if self._destroyed:
            return
        _cancel(self._rid)
        self._rid = GLib.timeout_add(delay, self._ref)

    def _ref(self) -> bool:
        self._rid = None
        if self._destroyed or not self._sync_enabled():
            return False

        devices = self._cl.devices
        self._sync_devices(devices)

        connected: list = []
        available: list = []
        saved: list = []
        live_addrs: set = set()

        for dev in devices:
            if _get_dev_name(dev) is None:
                continue
            addr = dev.address
            live_addrs.add(addr)

            if (dev.paired or dev.trusted) and not self.is_known(addr):
                self._known.mark(addr, _display_name(dev))

            (connected if dev.connected else available).append(dev)
            if self.is_known(addr):
                saved.append(dev)

        for addr, name in self._known.devices.items():
            if addr not in live_addrs:
                saved.append(_GhostDevice(addr, name))

        name_key = lambda d: _display_name(d).lower()
        connected.sort(key=name_key)
        available.sort(key=name_key)
        saved.sort(key=lambda d: (not d.connected, name_key(d)))

        self._ubox(self.connected_box, self._slots["connected"], connected)
        self._ubox(self.avail_box, self._slots["avail"], available)
        self._ubox(self.saved_box, self._slots["saved"], saved)

        self.connected_box.set_visible(bool(connected))
        self.avail_stack.set_visible_child_name("list" if available else "empty")
        return False

    def _ubox(self, box, pool: list, devices: list) -> None:
        while len(pool) < len(devices):
            slot = BTSlot(self)
            pool.append(slot)
            box.add(slot)
        for slot in pool[len(devices):]:
            slot.hide()
        for slot, dev in zip(pool, devices):
            slot.update(dev)
            slot.show()

    def cleanup(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True

        self._rid = _cancel(self._rid)
        if self._scan_id is not None:
            # Не оставляем discovery включённым, если закрылись посреди сканирования.
            self._cl.scanning = False
            self._scan_id = _cancel(self._scan_id)

        self._subs.clear()
        self._watched.clear()
        for pool in self._slots.values():
            for slot in pool:
                slot.destroy()
            pool.clear()
        self._cl = self._w = None