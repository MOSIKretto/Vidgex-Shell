import os

from gi.repository import GLib, Gtk, Gdk
from fabric.bluetooth import BluetoothClient
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow
from fabric.widgets.stack import Stack

import services.icons as icons


def set_pointer_cursor(widget):
    widget.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)

    def _on_enter(w, _event):
        win = w.get_window()
        if win:
            win.set_cursor(Gdk.Cursor.new_from_name(w.get_display(), "pointer"))

    def _on_leave(w, _event):
        win = w.get_window()
        if win:
            win.set_cursor(None)

    widget.connect("enter-notify-event", _on_enter)
    widget.connect("leave-notify-event", _on_leave)


def _run_bt_cmd(cmd_str, callback=None):
    def _on_exit(pid, _status, *_args):
        try:
            GLib.spawn_close_pid(pid)
        except Exception:
            pass
        if callback:
            GLib.idle_add(callback)

    try:
        pid, _, _, _ = GLib.spawn_async(
            ["/bin/sh", "-c", cmd_str],
            flags=GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.DO_NOT_REAP_CHILD,
        )
        GLib.child_watch_add(GLib.PRIORITY_DEFAULT, pid, _on_exit)
    except Exception:
        if callback:
            GLib.idle_add(callback)


def _get_dev_name(dev):
    addr = (getattr(dev, "address", "") or "").upper()
    name = getattr(dev, "alias", None) or getattr(dev, "name", None)

    if not name or str(name).strip() in ("", "Unknown", "unknown"):
        return None

    clean_name = str(name).replace(":", "").replace("-", "").strip().upper()
    clean_addr = addr.replace(":", "").replace("-", "").strip().upper()

    if clean_name == clean_addr:
        return None

    return str(name)


class BTSlot(Gtk.EventBox):
    def __init__(self, client, parent_bt):
        super().__init__()
        self.client = client
        self.parent_bt = parent_bt
        self.dev = None
        self.list_type = "avail"

        self.connect("button-press-event", self._on_click)
        set_pointer_cursor(self)

        self.main_box = CenterBox()
        self.main_box.get_style_context().add_class("pixel-slot")

        self.icon = Image(size=24)
        self.name_lbl = Label(h_expand=True, h_align="start", ellipsization="end")
        self.status_lbl = Label(h_expand=True, h_align="start", name="dim-label")

        text_box = Box(orientation="v", children=(self.name_lbl, self.status_lbl))
        start_box = Box(spacing=12, v_align="center", children=(self.icon, text_box))

        self.btn_settings = Button(
            child=Label(markup=icons.settings),
            tooltip_text="Device Settings",
            on_clicked=self._on_settings,
        )
        self.btn_settings.get_style_context().add_class("pixel-icon-button")
        self.btn_settings.get_style_context().add_class("settings-btn")
        self.btn_settings.set_valign(Gtk.Align.CENTER)
        set_pointer_cursor(self.btn_settings)

        self.main_box.add_start(start_box)
        self.main_box.add_end(self.btn_settings)
        self.add(self.main_box)

    def update(self, dev, list_type):
        self.dev = dev
        self.list_type = list_type

        icon_name = f"{getattr(dev, 'icon_name', 'bluetooth')}-symbolic"
        self.icon.set_from_icon_name(icon_name, 24)

        dev_name = _get_dev_name(dev) or getattr(dev, "name", None) or "Unknown"
        self.name_lbl.set_label(dev_name)

        self._upd()

    def _upd(self):
        if not self.dev:
            return

        connected = getattr(self.dev, "connected", False)
        known = getattr(self.dev, "paired", False) or getattr(self.dev, "trusted", False)

        self.btn_settings.set_visible(known or connected)

        main_ctx = self.main_box.get_style_context()
        icon_ctx = self.icon.get_style_context()
        btn_ctx = self.btn_settings.get_style_context()

        if connected:
            self.status_lbl.set_label("Connected")
            main_ctx.add_class("active-slot")
            icon_ctx.add_class("active-icon")
            btn_ctx.add_class("active-settings-btn")
        else:
            self.status_lbl.set_label("Saved" if known else "Available")
            main_ctx.remove_class("active-slot")
            icon_ctx.remove_class("active-icon")
            btn_ctx.remove_class("active-settings-btn")

    def _on_click(self, _widget, _event):
        if not self.dev:
            return

        addr = getattr(self.dev, "address", None)
        if not addr:
            return

        is_conn = getattr(self.dev, "connected", False)
        known = getattr(self.dev, "paired", False) or getattr(self.dev, "trusted", False)

        if is_conn:
            self.status_lbl.set_label("Disconnecting...")
            _run_bt_cmd(f"bluetoothctl disconnect {addr}", self._safe_refresh)
        else:
            self.status_lbl.set_label("Connecting...")
            if known:
                _run_bt_cmd(f"bluetoothctl connect {addr}", self._safe_refresh)
            else:
                _run_bt_cmd(
                    f"bluetoothctl pair {addr} && bluetoothctl trust {addr} && bluetoothctl connect {addr}",
                    self._safe_refresh,
                )

    def _safe_refresh(self):
        if self.parent_bt:
            return self.parent_bt._req_ref()
        return False

    def _on_settings(self, _btn):
        if self.parent_bt:
            self.parent_bt.open_settings(self)


class BluetoothConnections(Box):
    def __init__(self, **kwargs):
        self._w = kwargs.pop("widgets", None)
        super().__init__(
            name="bluetooth",
            spacing=4,
            orientation="vertical",
            h_expand=True,
            v_expand=True,
            v_align="fill",
            **kwargs,
        )

        self._btns = None
        if self._w and hasattr(self._w, "buttons"):
            self._btns = getattr(self._w.buttons, "bluetooth_button", None)

        self._slots = {"connected": [], "avail": [], "saved": []}
        self._rid = None
        self._scan = False
        self.current_settings_dev = None
        self._previous_page = "main"
        self._cl = None

        try:
            self._cl = BluetoothClient()
        except Exception:
            return

        self._build()

        self._cl.connect("notify::enabled", self._on_en)
        self._cl.connect("device-added", self._sched)
        self._cl.connect("device-removed", self._sched)

        self._on_en()
        self._sched()

    def _build(self):
        back_btn = Button(
            name="bluetooth-back",
            child=Label(markup=icons.chevron_left, name="bluetooth-back-label"),
        )
        back_btn.connect("clicked", self._on_back_click)
        set_pointer_cursor(back_btn)

        self.header_title = Label(label="Bluetooth", v_align="center", name="header-title")

        self.saved_btn = Button(
            name="bluetooth-saved",
            child=Label(markup=icons.save, name="bluetooth-saved-label"),
            tooltip_text="Saved Devices",
            on_clicked=self._on_saved_toggle,
        )
        set_pointer_cursor(self.saved_btn)

        self._sc_lbl = Label(markup=icons.radar, name="bluetooth-scan-label")
        self._sc_btn = Button(
            name="bluetooth-scan",
            child=self._sc_lbl,
            tooltip_text="Scan",
            on_clicked=self._on_scan_toggle,
        )
        set_pointer_cursor(self._sc_btn)

        header_end = Box(spacing=4, orientation="horizontal", children=(self.saved_btn, self._sc_btn))
        header = CenterBox(
            start_children=(back_btn,),
            center_children=(self.header_title,),
            end_children=(header_end,),
        )
        header.set_margin_bottom(8)
        self.add(header)

        self.stack = Stack(transition_type="crossfade", h_expand=True, v_expand=True, v_align="fill")
        self.add(self.stack)

        off_box = Box(orientation="v", v_align="center", h_align="center", spacing=12, v_expand=True)
        off_icon = Label(markup=f"<span size='32768'>{icons.bluetooth_off}</span>")
        off_icon.get_style_context().add_class("bluetooth-off-icon")
        off_box.add(off_icon)

        off_label = Label(label="Bluetooth is disabled")
        off_label.get_style_context().add_class("bluetooth-off-label")
        off_box.add(off_label)

        btn_turn_on = Button(label="Turn On", h_align="center", on_clicked=self._turn_on_bt)
        btn_turn_on.get_style_context().add_class("bluetooth-turn-on-btn")
        set_pointer_cursor(btn_turn_on)
        off_box.add(btn_turn_on)

        self.stack.add_named(off_box, "off")

        self.lists_stack = Stack(
            transition_type="slide-left-right", h_expand=True, v_expand=True, v_align="fill",
        )

        self.connected_box = Box(spacing=2, orientation="vertical")
        self.avail_box = Box(spacing=2, orientation="vertical")

        self.avail_empty = Box(
            orientation="v", v_align="center", h_align="center", spacing=12, v_expand=True,
        )
        self.avail_empty.set_margin_top(24)
        self.avail_empty.set_margin_bottom(24)

        empty_icon = Label(markup=f"<span size='32768'>{icons.radar}</span>")
        empty_icon.get_style_context().add_class("bluetooth-off-icon")
        self.avail_empty.add(empty_icon)

        empty_lbl = Label(label="No devices found")
        empty_lbl.get_style_context().add_class("bluetooth-off-label")
        self.avail_empty.add(empty_lbl)

        btn_scan = Button(label="Scan", h_align="center", on_clicked=self._on_scan_toggle)
        btn_scan.get_style_context().add_class("bluetooth-turn-on-btn")
        set_pointer_cursor(btn_scan)
        self.avail_empty.add(btn_scan)

        self.avail_stack = Stack(transition_type="crossfade", h_expand=True, v_expand=True)
        self.avail_stack.add_named(self.avail_box, "list")
        self.avail_stack.add_named(self.avail_empty, "empty")

        self.avail_section = Box(
            orientation="v",
            spacing=4,
            children=(
                Label(label="Available Devices", h_align="start", name="section-title"),
                self.avail_stack,
            ),
        )

        self.main_scroll = ScrolledWindow(
            name="bluetooth-devices",
            min_content_size=(-1, -1),
            child=Box(spacing=4, orientation="vertical", children=[self.connected_box, self.avail_section]),
            h_expand=True, v_expand=True, propagate_width=False, propagate_height=False,
        )
        self.main_scroll.set_overlay_scrolling(False)

        self.saved_box = Box(spacing=2, orientation="vertical")
        self.saved_section = Box(
            orientation="v",
            spacing=4,
            children=(
                Label(label="Saved Devices", h_align="start", name="section-title"),
                self.saved_box,
            ),
        )
        self.saved_scroll = ScrolledWindow(
            name="bluetooth-devices",
            min_content_size=(-1, -1),
            child=Box(spacing=4, orientation="vertical", children=[self.saved_section]),
            h_expand=True, v_expand=True, propagate_width=False, propagate_height=False,
        )
        self.saved_scroll.set_overlay_scrolling(False)

        self.settings_scroll = self._build_settings_page()

        self.lists_stack.add_named(self.main_scroll, "main")
        self.lists_stack.add_named(self.saved_scroll, "saved")
        self.lists_stack.add_named(self.settings_scroll, "settings")

        self.stack.add_named(self.lists_stack, "on")

    def _build_settings_page(self):
        settings_box = Box(orientation="vertical", spacing=8, h_expand=True)
        settings_box.set_margin_start(12)
        settings_box.set_margin_end(12)

        actions_box = Box(orientation="horizontal", spacing=8, h_align="center", h_expand=True)

        lbl_forget = Label(markup=f"<span size='large'>{icons.trash}</span> Remove")
        self.btn_bt_forget = Button(child=lbl_forget, on_clicked=self._do_forget)
        self.btn_bt_forget.get_style_context().add_class("net-action-btn")
        self.btn_bt_forget.get_style_context().add_class("net-forget")
        set_pointer_cursor(self.btn_bt_forget)

        self.lbl_bt_disconnect = Label(
            markup=f"<span size='large'>{icons.cancel}</span> Disconnect",
        )
        self.btn_bt_disconnect = Button(
            child=self.lbl_bt_disconnect, on_clicked=self._do_disconnect_or_connect,
        )
        self.btn_bt_disconnect.get_style_context().add_class("net-action-btn")
        set_pointer_cursor(self.btn_bt_disconnect)

        actions_box.add(self.btn_bt_forget)
        actions_box.add(self.btn_bt_disconnect)
        actions_box.set_margin_bottom(12)
        settings_box.add(actions_box)

        self.lbl_bt_addr = Label(label="-", h_align="end", selectable=True)
        self.lbl_bt_paired = Label(label="-", h_align="end")
        self.lbl_bt_trusted = Label(label="-", h_align="end")

        info_group = Box(orientation="vertical", spacing=2)
        info_group.get_style_context().add_class("net-info-group")

        def add_row(title, val_widget):
            row = Box(orientation="horizontal")
            row.get_style_context().add_class("net-info-row")
            row.pack_start(Label(label=title, h_align="start", name="dim-label"), True, True, 0)
            row.pack_end(val_widget, False, False, 0)
            info_group.add(row)

        add_row("MAC Address", self.lbl_bt_addr)
        add_row("Paired", self.lbl_bt_paired)
        add_row("Trusted", self.lbl_bt_trusted)

        settings_box.add(info_group)

        scroll = ScrolledWindow(
            name="bluetooth-devices",
            min_content_size=(-1, -1),
            child=settings_box,
            h_expand=True, v_expand=True, propagate_width=False, propagate_height=False,
        )
        scroll.set_overlay_scrolling(False)
        return scroll

    def open_settings(self, slot):
        if not slot or not slot.dev:
            return

        self._previous_page = self.lists_stack.get_visible_child_name()
        self.current_settings_dev = slot.dev

        self.header_title.set_label(slot.name_lbl.get_label())

        is_conn = getattr(slot.dev, "connected", False)
        if is_conn:
            self.lbl_bt_disconnect.set_markup(
                f"<span size='large'>{icons.cancel}</span> Disconnect",
            )
        else:
            self.lbl_bt_disconnect.set_markup(
                f"<span size='large'>{icons.accept}</span> Connect",
            )

        self.lbl_bt_addr.set_label(getattr(slot.dev, "address", "Unknown"))
        self.lbl_bt_paired.set_label("Yes" if getattr(slot.dev, "paired", False) else "No")
        self.lbl_bt_trusted.set_label("Yes" if getattr(slot.dev, "trusted", False) else "No")

        self._sc_btn.set_visible(False)
        self.saved_btn.set_visible(False)
        self.lists_stack.set_visible_child_name("settings")

    def _do_forget(self, _btn):
        if not self.current_settings_dev:
            return
        addr = getattr(self.current_settings_dev, "address", None)
        if not addr:
            return
        _run_bt_cmd(
            f"bluetoothctl disconnect {addr} ; bluetoothctl untrust {addr} ; bluetoothctl remove {addr}",
            self._req_ref,
        )
        self._on_back_click(None)

    def _do_disconnect_or_connect(self, _btn):
        if not self.current_settings_dev:
            return
        addr = getattr(self.current_settings_dev, "address", None)
        if not addr:
            return

        is_conn = getattr(self.current_settings_dev, "connected", False)
        known = (
            getattr(self.current_settings_dev, "paired", False)
            or getattr(self.current_settings_dev, "trusted", False)
        )

        if is_conn:
            _run_bt_cmd(f"bluetoothctl disconnect {addr}", self._req_ref)
        elif known:
            _run_bt_cmd(f"bluetoothctl connect {addr}", self._req_ref)
        else:
            _run_bt_cmd(
                f"bluetoothctl pair {addr} && bluetoothctl trust {addr} && bluetoothctl connect {addr}",
                self._req_ref,
            )

        self._on_back_click(None)

    def _on_back_click(self, _btn):
        curr = self.lists_stack.get_visible_child_name()
        if curr == "settings":
            self.lists_stack.set_visible_child_name(self._previous_page)
            if self._previous_page == "saved":
                self.header_title.set_label("Saved Devices")
                self.saved_btn.add_style_class("pressed")
            else:
                self.header_title.set_label("Bluetooth")
                self.saved_btn.remove_style_class("pressed")
            self._sc_btn.set_visible(True)
            self.saved_btn.set_visible(True)
        elif curr == "saved":
            self.lists_stack.set_visible_child_name("main")
            self.header_title.set_label("Bluetooth")
            self.saved_btn.remove_style_class("pressed")
        else:
            if self._w:
                self._w.show_notif()

    def _on_saved_toggle(self, btn):
        if self.lists_stack.get_visible_child_name() == "main":
            self.lists_stack.set_visible_child_name("saved")
            self.header_title.set_label("Saved Devices")
            btn.add_style_class("pressed")
        else:
            self.lists_stack.set_visible_child_name("main")
            self.header_title.set_label("Bluetooth")
            btn.remove_style_class("pressed")

    def _attach_dev_signals(self, dev):
        if getattr(dev, "_bt_signals_connected", False):
            return
        try:
            dev._bt_signals_connected = True
        except (AttributeError, TypeError):
            return
        for sig in (
            "notify::connected",
            "notify::paired",
            "notify::trusted",
            "notify::name",
            "notify::alias",
        ):
            try:
                dev.connect(sig, self._sched)
            except (TypeError, Exception):
                pass

    def _sched(self, *_args):
        if self._rid is None:
            self._rid = GLib.timeout_add(300, self._ref)

    def _req_ref(self):
        self._sched()
        return False

    def _ref(self):
        self._rid = None

        if not self._cl:
            return False

        enabled = self._get_pwr()
        self.stack.set_visible_child_name("on" if enabled else "off")
        if not enabled:
            return False

        devs = getattr(self._cl, "devices", None)
        if devs is None:
            return False

        dev_list = devs.values() if isinstance(devs, dict) else devs

        connected_devs, available_devs, saved_devs = [], [], []

        for dev in dev_list:
            if not _get_dev_name(dev):
                continue

            self._attach_dev_signals(dev)

            is_conn = getattr(dev, "connected", False)
            known = getattr(dev, "paired", False) or getattr(dev, "trusted", False)

            if is_conn:
                connected_devs.append(dev)
            if known:
                saved_devs.append(dev)
            if not is_conn and not known:
                available_devs.append(dev)

        name_key = lambda d: (_get_dev_name(d) or "").lower()
        connected_devs.sort(key=name_key)
        available_devs.sort(key=name_key)
        saved_devs.sort(key=lambda d: (not getattr(d, "connected", False), (_get_dev_name(d) or "").lower()))

        self._ubox(self.connected_box, self._slots["connected"], connected_devs, "connected")
        self._ubox(self.avail_box, self._slots["avail"], available_devs, "avail")
        self._ubox(self.saved_box, self._slots["saved"], saved_devs, "saved")

        self._update_visibility()
        return False

    def _ubox(self, box, pool, data, list_type):
        needed = len(data)

        while len(pool) < needed:
            slot = BTSlot(self._cl, self)
            pool.append(slot)
            box.add(slot)

        for i in range(needed, len(pool)):
            pool[i].hide()

        for i, dev in enumerate(data):
            pool[i].update(dev, list_type)
            pool[i].show_all()
            pool[i]._upd()

    def _update_visibility(self):
        def count_visible(box):
            return sum(1 for c in box.get_children() if c.get_visible())

        self.connected_box.set_visible(count_visible(self.connected_box) > 0)
        self.avail_section.set_visible(True)

        if count_visible(self.avail_box) > 0:
            self.avail_stack.set_visible_child_name("list")
        else:
            self.avail_stack.set_visible_child_name("empty")

    def _get_pwr(self):
        try:
            rfkill_dir = "/sys/class/rfkill/"
            if not os.path.isdir(rfkill_dir):
                return False
            for entry in os.listdir(rfkill_dir):
                type_path = os.path.join(rfkill_dir, entry, "type")
                state_path = os.path.join(rfkill_dir, entry, "state")
                if not os.path.exists(type_path):
                    continue
                with open(type_path, "r") as f:
                    if f.read().strip() != "bluetooth":
                        continue
                if not os.path.exists(state_path):
                    continue
                with open(state_path, "r") as sf:
                    return sf.read().strip() == "1"
        except Exception:
            pass
        return False

    def _turn_on_bt(self, *_args):
        if self._btns and hasattr(self._btns, "_on_toggle_click"):
            if not getattr(self._btns, "_en", True):
                self._btns._on_toggle_click()
        else:
            try:
                GLib.spawn_command_line_async("rfkill unblock bluetooth")
            except Exception:
                pass
        GLib.timeout_add(350, self._on_en)

    def _on_scan_toggle(self, *_args):
        if self._scan:
            return

        if not self._get_pwr():
            self._turn_on_bt()
            GLib.timeout_add(800, self._do_scan)
        else:
            self._do_scan()

    def _do_scan(self):
        self._scan = True
        self._sc_lbl.get_style_context().add_class("scanning")
        self._sc_btn.get_style_context().add_class("scanning")

        try:
            GLib.spawn_command_line_async("bluetoothctl --timeout 4 scan on")
        except Exception:
            pass

        GLib.timeout_add(4000, self._stop_scan)
        return False

    def _stop_scan(self):
        self._scan = False
        self._sc_lbl.get_style_context().remove_class("scanning")
        self._sc_btn.get_style_context().remove_class("scanning")

        try:
            GLib.spawn_command_line_async("bluetoothctl scan off")
        except Exception:
            pass
        return False

    def _on_en(self, *_args):
        enabled = self._get_pwr()
        self.stack.set_visible_child_name("on" if enabled else "off")

        if enabled:
            self._sched()

        if self._btns and hasattr(self._btns, "update_state"):
            GLib.idle_add(self._btns.update_state)
        return False

    def cleanup(self):
        if self._rid:
            GLib.source_remove(self._rid)
            self._rid = None

        for pool in self._slots.values():
            for slot in pool:
                try:
                    slot.destroy()
                except Exception:
                    pass
            pool.clear()

        self._cl = None
        self._w = None
        self._btns = None