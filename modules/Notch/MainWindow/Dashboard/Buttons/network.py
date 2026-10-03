import os
import re
import qrcode
import cairo
import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gtk, GLib, Gst

from PIL import Image as PILImage
from pyzbar.pyzbar import decode

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow
from fabric.widgets.stack import Stack

import services.icons as icons
from modules.Notch.MainWindow.Dashboard.Buttons.Network.network import Subscriptions, strength_icon

Gst.init(None)

_QR_FRAME_W = 620
_QR_FRAME_H = 220
# Шаг строки кадра RGB24 постоянен — считаем один раз, а не на каждый кадр.
_QR_STRIDE = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_RGB24, _QR_FRAME_W)
_QR_DEVICE_WAIT_MS = 2000
# Файл с QR содержит пароль: кладём в приватный XDG_RUNTIME_DIR (0700), а не в /tmp.
_QR_SHARE_PATH = os.path.join(GLib.get_user_runtime_dir(), "wifi-share-qr.png")
# Поле формата WIFI: — всё до неэкранированной «;».
_WIFI_QR_FIELD = re.compile(r"((?:\\.|[^;\\])*);")
_WIFI_QR_UNESCAPE = re.compile(r"\\(.)")
_WIFI_QR_ESCAPE = str.maketrans({c: "\\" + c for c in '\\;,:"'})


def _parse_wifi_qr(data: str) -> tuple[str, str] | None:
    if not data.startswith("WIFI:"):
        return None
    fields = {}
    for part in _WIFI_QR_FIELD.findall(data[len("WIFI:"):]):
        key, sep, value = part.partition(":")
        if sep:
            fields[key] = _WIFI_QR_UNESCAPE.sub(r"\1", value)
    ssid = fields.get("S")
    if not ssid:
        return None
    return ssid, fields.get("P", "")


def _build_wifi_qr(ssid: str, password: str) -> str:
    ssid = ssid.translate(_WIFI_QR_ESCAPE)
    if not password:
        return f"WIFI:S:{ssid};T:nopass;;"
    return f"WIFI:S:{ssid};T:WPA;P:{password.translate(_WIFI_QR_ESCAPE)};;"


def _save_qr(data: str, path: str) -> None:
    qr = qrcode.QRCode(version=1, box_size=5, border=1)
    qr.add_data(data)
    qr.make(fit=True)
    qr.make_image(fill_color="black", back_color="white").save(path)


def _placeholder(ssid: str, strength: int = 0) -> dict:
    """Данные слота для сети, которой нет в эфире (сохранённая вне зоны / текущая без AP в списке)."""
    return {"ssid": ssid, "is_secured": True, "icon-name": strength_icon(strength)}


def _cancel(source):
    """Снимает GLib-источник, если он есть. Использовать: self._id = _cancel(self._id)."""
    if source is not None:
        GLib.source_remove(source)


def _cls(widget, name: str, on: bool = True):
    ctx = widget.get_style_context()
    (ctx.add_class if on else ctx.remove_class)(name)


def _styled(widget, *names):
    for name in names:
        _cls(widget, name)
    return widget


def _gst(factory: str, **props):
    element = Gst.ElementFactory.make(factory, None)
    for key, value in props.items():
        element.set_property(key.replace("_", "-"), value)
    return element


def _message(text: str):
    return _styled(Label(label=text), "wifi-off-label")


def _action_button(label: str, handler):
    return _styled(Button(label=label, h_align="center", on_clicked=handler), "wifi-turn-on-btn")


def _status_box(icon: str, *children):
    glyph = _styled(Label(markup=f"<span size='32768'>{icon}</span>"), "wifi-off-icon")
    return Box(
        orientation="v", v_align="center", h_align="center", spacing=12, v_expand=True,
        children=(glyph, *children),
    )


def _section(title: str, child):
    return Box(
        orientation="v", spacing=4,
        children=(Label(label=title, h_align="start", name="section-title"), child),
    )


def _scroll(child):
    window = ScrolledWindow(
        name="bluetooth-devices", min_content_size=(-1, -1), child=child,
        h_expand=True, v_expand=True, propagate_width=False, propagate_height=False,
    )
    window.set_overlay_scrolling(False)
    return window


def _header_button(name: str, icon: str, tooltip: str, handler):
    return Button(
        name=name, child=Label(markup=icon, name=f"{name}-label"),
        tooltip_text=tooltip, on_clicked=handler,
    )


class WifiSlot(Gtk.Box):
    _active_pw_slot = None

    def __init__(self, nc, parent_net):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._destroyed = False
        self.nc = nc
        self.parent_net = parent_net
        self.ssid = None
        self.saved = self.conn = self.available = False
        self.is_secured = True
        self._anim_id = self._restore_id = None
        self._target_height = 0
        self._cached_vadj = None
        self._cached_scroll_h = 0

        self.click_area = Gtk.EventBox()
        self.click_area.connect("button-press-event", self._on_click)

        self.main_box = _styled(CenterBox(), "pixel-slot")
        self.icon = Image(size=16)
        self.name_lbl = Label(h_expand=True, h_align="start", ellipsization="end")
        self.status_lbl = Label(label="", h_expand=True, h_align="start", name="dim-label")

        self.btn_settings = _styled(
            Button(
                child=Label(markup=icons.settings),
                tooltip_text="Network Settings",
                on_clicked=self._on_settings,
            ),
            "pixel-icon-button", "settings-btn",
        )
        self.lock_icon = _styled(Label(markup=icons.lock), "lock-icon")
        self.lock_icon.set_margin_end(8)

        self.main_box.add_start(Box(
            spacing=12, v_align="center",
            children=(self.icon, Box(orientation="v", children=(self.name_lbl, self.status_lbl))),
        ))
        self.main_box.add_end(Box(
            orientation="horizontal", v_align="center",
            children=(self.btn_settings, self.lock_icon),
        ))
        self.click_area.add(self.main_box)
        self.pack_start(self.click_area, False, False, 0)

        self.pw_rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.pw_rev.set_transition_duration(300)

        self.btn_pw_reveal = _styled(
            Button(child=Label(markup=icons.eye_closed), on_clicked=self._on_reveal_clicked),
            "pw-reveal-btn",
        )
        self.pw_entry = _styled(
            Gtk.Entry(visibility=False, invisible_char=ord("•"), placeholder_text="Password...", hexpand=True),
            "pw-entry-naked",
        )
        self.pw_entry.connect("changed", self._on_pw_change)
        self.pw_entry.connect("activate", self._on_pw_submit)

        self.btn_pw_ok = _styled(
            Button(child=Label(markup=icons.accept), on_clicked=self._on_pw_submit),
            "pw-submit-btn",
        )
        self.btn_pw_ok.set_sensitive(False)

        pw_pill = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0, name="pw-pill")
        pw_pill.pack_start(self.btn_pw_reveal, False, False, 2)
        pw_pill.pack_start(self.pw_entry, True, True, 4)
        pw_pill.pack_end(self.btn_pw_ok, False, False, 2)

        pw_wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, name="pw-wrapper")
        pw_wrapper.pack_start(pw_pill, True, True, 0)
        self.pw_rev.add(pw_wrapper)
        self.pack_start(self.pw_rev, False, False, 0)

        self.show_all()
        self.connect("destroy", lambda *_: self.cleanup())

    def cleanup(self):
        if self._destroyed:
            return
        self._destroyed = True
        self._anim_id = _cancel(self._anim_id)
        self._restore_id = _cancel(self._restore_id)
        if WifiSlot._active_pw_slot is self:
            WifiSlot._active_pw_slot = None
        self.parent_net = self.nc = None

    def _on_reveal_clicked(self, btn):
        visible = not self.pw_entry.get_visibility()
        self.pw_entry.set_visibility(visible)
        btn.get_child().set_markup(icons.eye_check if visible else icons.eye_closed)

    def _on_pw_change(self, entry):
        self.btn_pw_ok.set_sensitive(bool(entry.get_text()))

    def _on_pw_submit(self, _widget):
        pwd = self.pw_entry.get_text()
        if pwd:
            self._close_pw()
            self._connect(pwd)

    def _idle_status(self) -> str:
        if not self.available:
            return "Out of range"
        if self.saved:
            return "Saved"
        return "Secured" if self.is_secured else "Open"

    def update(self, data, saved, conn, available):
        self.ssid, self.saved, self.conn, self.available = data["ssid"], saved, conn, available
        self.is_secured = data["is_secured"]

        self.icon.set_from_icon_name(data["icon-name"], 24)
        self.name_lbl.set_label(self.ssid)

        known = conn or saved
        self.btn_settings.set_visible(known)
        self.lock_icon.set_visible(not known and self.is_secured)

        for widget, css in (
            (self.main_box, "active-slot"),
            (self.icon, "active-icon"),
            (self.btn_settings, "active-settings-btn"),
        ):
            _cls(widget, css, conn)
        self.status_lbl.set_label("Connected" if conn else self._idle_status())

    def _on_click(self, _widget, _event):
        if self.conn:
            return
        if not self.saved and self.is_secured:
            self._tog_pw()
            return
        # Сохранённая — по профилю; открытая новая — без пароля.
        self._connect(None if self.saved else "")

    def _connect(self, password):
        """password=None — подключение по сохранённому профилю."""
        self.status_lbl.set_label("Connecting...")
        if password is None:
            started = self.nc.connect_to_saved_network(self.ssid, self._ok, self._err)
        else:
            started = self.nc.connect_to_new_network(self.ssid, password, self._ok, self._err)
        if not started:
            self._err()

    def _tog_pw(self):
        active = WifiSlot._active_pw_slot
        if active is not None and active is not self:
            active._close_pw()

        if self.pw_rev.get_reveal_child():
            self._close_pw()
            return

        WifiSlot._active_pw_slot = self
        _, child_h = self.pw_rev.get_child().get_preferred_height()
        self._target_height = self.get_allocated_height() + child_h

        self.pw_rev.set_reveal_child(True)
        self.pw_entry.set_text("")
        self.pw_entry.set_visibility(False)
        self.btn_pw_reveal.get_child().set_markup(icons.eye_closed)
        self.btn_pw_ok.set_sensitive(False)
        self._start_scroll_anim()

    def _start_scroll_anim(self):
        self._anim_id = _cancel(self._anim_id)
        scroll = self.get_ancestor(Gtk.ScrolledWindow)
        if not scroll:
            return
        self._cached_vadj = scroll.get_vadjustment()
        self._cached_scroll_h = scroll.get_allocated_height()
        self._anim_id = GLib.timeout_add(16, self._scroll_tick, scroll)

    def _scroll_tick(self, scroll):
        coords = None if self._destroyed else self.translate_coordinates(scroll, 0, 0)
        if not coords:
            self._anim_id = None
            return False

        vadj = self._cached_vadj
        current = vadj.get_value()
        revealed = self.pw_rev.get_child_revealed()
        target_h = self.get_allocated_height() if revealed else self._target_height
        target = current + coords[1] - self._cached_scroll_h / 2.0 + target_h / 2.0
        lower = vadj.get_lower()
        upper = max(lower, vadj.get_upper() - vadj.get_page_size())
        target = max(lower, min(target, upper))

        if revealed and abs(target - current) < 1.0:
            vadj.set_value(target)
            self._anim_id = None
            GLib.idle_add(self.pw_entry.grab_focus)
            return False

        vadj.set_value(current + (target - current) * 0.15)
        return True

    def _close_pw(self):
        self._anim_id = _cancel(self._anim_id)
        self.pw_rev.set_reveal_child(False)
        if WifiSlot._active_pw_slot is self:
            WifiSlot._active_pw_slot = None

    def _ok(self, _ssid):
        if not self._destroyed and self.parent_net:
            self.parent_net.request_refresh(500)

    def _err(self, *_args):
        if self._destroyed:
            return
        self.status_lbl.set_label("Failed to connect")
        _cancel(self._restore_id)
        self._restore_id = GLib.timeout_add(3000, self._restore)

    def _restore(self):
        self._restore_id = None
        if not self.conn:
            self.status_lbl.set_label(self._idle_status())
        return False

    def _on_settings(self, _btn):
        if self.parent_net and self.ssid:
            self.parent_net.open_settings(self.ssid)


class QrScanPage(Gtk.Box):
    def __init__(self, nc, lists_stack, on_connected):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)

        self._destroyed = False
        self.nc = nc
        self._on_connected = on_connected
        self._subs = Subscriptions()
        self._pipeline = self._device_monitor = None
        self._device_wait_id = self._restore_id = None
        self._frame_surface = self._frame_polygon = self._last_attempt = None

        self.status_stack = Stack(transition_type="crossfade", h_expand=True, v_expand=True)

        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_size_request(_QR_FRAME_W, _QR_FRAME_H)
        self.drawing_area.connect("draw", self._on_draw)

        self.message_label = _message("")
        self.message_label.set_justify(Gtk.Justification.CENTER)

        self.status_stack.add_named(self.drawing_area, "camera")
        self.status_stack.add_named(_status_box(icons.wifi_off, self.message_label), "message")
        self.add(self.status_stack)
        self.show_all()

        self._subs.connect(lists_stack, "notify::visible-child-name", self._on_page_switched)
        self.connect("destroy", lambda *_: self.cleanup())

    def cleanup(self):
        if self._destroyed:
            return
        self._destroyed = True
        self._stop_all()
        self._subs.clear()
        self._on_connected = self.nc = None

    def _stop_all(self):
        self._device_wait_id = _cancel(self._device_wait_id)
        self._restore_id = _cancel(self._restore_id)
        self._stop_device_monitor()
        self._stop_pipeline()

    def _watch(self, bus, signal, callback):
        bus.add_signal_watch()
        self._subs.connect(bus, signal, callback)

    def _unwatch(self, bus):
        bus.remove_signal_watch()
        self._subs.release(bus)

    def _on_page_switched(self, stack, _pspec):
        if stack.get_visible_child_name() == "qr":
            self._start()
        else:
            self._stop_all()

    def _start(self):
        self._last_attempt = None
        self._start_monitor()
        existing = self._device_monitor.get_devices()
        if existing:
            self._on_device_found(existing[0])
            return
        self._show_message("Looking for a camera...")
        self._device_wait_id = GLib.timeout_add(_QR_DEVICE_WAIT_MS, self._on_device_wait_timeout)

    def _start_monitor(self):
        monitor = Gst.DeviceMonitor.new()
        monitor.add_filter("Video/Source", None)
        self._watch(monitor.get_bus(), "message::device-added", self._on_device_added)
        monitor.start()
        self._device_monitor = monitor

    def _stop_device_monitor(self):
        monitor, self._device_monitor = self._device_monitor, None
        if monitor is None:
            return
        self._unwatch(monitor.get_bus())
        monitor.stop()

    def _on_device_added(self, _bus, message):
        if not self._destroyed:
            self._on_device_found(message.parse_device_added())

    def _on_device_found(self, device):
        self._device_wait_id = _cancel(self._device_wait_id)
        self._stop_device_monitor()
        self._start_pipeline(device)

    def _on_device_wait_timeout(self):
        self._device_wait_id = None
        # Монитор остаётся активным: камеру можно подключить в любой момент.
        self._show_message("No camera detected.\nPlease connect a camera to your computer.")
        return False

    def _start_pipeline(self, device):
        appsink = _gst("appsink", emit_signals=True, max_buffers=1, drop=True, sync=False)
        appsink.connect("new-sample", self._on_new_sample)
        chain = (
            device.create_element(None),
            _gst("capsfilter", caps=Gst.Caps.from_string("video/x-raw,format=YUY2,width=640,height=480")),
            _gst("videoconvert"),
            _gst("videoscale"),
            _gst("capsfilter", caps=Gst.Caps.from_string(
                f"video/x-raw,format=BGRx,width={_QR_FRAME_W},height={_QR_FRAME_H}",
            )),
            appsink,
        )
        pipeline = Gst.Pipeline.new("qr-scan")
        for element in chain:
            pipeline.add(element)
        for upstream, downstream in zip(chain, chain[1:]):
            upstream.link(downstream)

        self._watch(pipeline.get_bus(), "message::error", self._on_bus_error)
        pipeline.set_state(Gst.State.PLAYING)
        self._pipeline = pipeline
        self.status_stack.set_visible_child_name("camera")

    def _stop_pipeline(self):
        pipeline, self._pipeline = self._pipeline, None
        if pipeline is None:
            return
        self._unwatch(pipeline.get_bus())
        pipeline.set_state(Gst.State.NULL)
        self._frame_surface = self._frame_polygon = None

    def _on_bus_error(self, _bus, _message):
        self._stop_pipeline()
        self._show_message("Camera disconnected.")
        # Ждём повторного подключения камеры (hot-plug).
        self._start_monitor()

    def _show_message(self, text):
        self.message_label.set_label(text)
        self.status_stack.set_visible_child_name("message")

    def _show_error_and_restore(self, text):
        self._show_message(text)
        _cancel(self._restore_id)
        self._restore_id = GLib.timeout_add(3000, self._restore_camera)

    def _restore_camera(self):
        self._restore_id = None
        if self._pipeline:
            self.status_stack.set_visible_child_name("camera")
        return False

    def _on_new_sample(self, sink):
        # Вызывается из стриминг-потока GStreamer: с виджетами здесь не работаем.
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        buf = sample.get_buffer()
        ok, mapinfo = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.OK
        # Одна копия: bytearray нужен cairo в главном потоке и годится для PIL.
        data = bytearray(mapinfo.data)
        buf.unmap(mapinfo)

        gray = PILImage.frombytes("RGB", (_QR_FRAME_W, _QR_FRAME_H), data, "raw", "BGRX").convert("L")
        decoded = decode(gray)
        qr_result = (decoded[0].data.decode("utf-8"), decoded[0].polygon) if decoded else None

        GLib.idle_add(self._on_frame_ready, data, qr_result)
        return Gst.FlowReturn.OK

    def _on_frame_ready(self, data, qr_result):
        if self._destroyed or not self._pipeline:
            return False
        self._frame_surface = cairo.ImageSurface.create_for_data(
            data, cairo.FORMAT_RGB24, _QR_FRAME_W, _QR_FRAME_H, _QR_STRIDE,
        )
        self._frame_polygon = qr_result[1] if qr_result else None
        self.drawing_area.queue_draw()
        if qr_result:
            self._handle_qr(qr_result[0])
        return False

    def _on_draw(self, _widget, cr):
        if not self._frame_surface:
            return False
        # Зеркалим только отображение (эффект зеркала для пользователя).
        # Буфер кадра, переданный в decode, остаётся неизменным.
        cr.save()
        cr.translate(_QR_FRAME_W, 0)
        cr.scale(-1, 1)
        cr.set_source_surface(self._frame_surface, 0, 0)
        cr.paint()
        if self._frame_polygon:
            cr.set_source_rgb(0.2, 0.85, 0.4)
            cr.set_line_width(3)
            points = self._frame_polygon
            cr.move_to(points[0].x, points[0].y)
            for p in points[1:]:
                cr.line_to(p.x, p.y)
            cr.close_path()
            cr.stroke()
        cr.restore()
        return False

    def _handle_qr(self, data):
        parsed = _parse_wifi_qr(data)
        if not parsed or self._last_attempt == parsed:
            return
        self._last_attempt = parsed
        ssid, password = parsed
        if not self.nc.connect_to_new_network(ssid, password, self._on_ok, self._on_err):
            self._show_error_and_restore(
                f"Can't connect to '{ssid}':\nout of range or unsupported security",
            )

    def _on_ok(self, _ssid):
        if self._destroyed:
            return
        self._stop_pipeline()
        self._on_connected()

    def _on_err(self, _ssid, _msg):
        if self._destroyed:
            return
        self._last_attempt = None
        self._show_error_and_restore("Connection failed. Try again.")


class NetworkConnections(Box):
    def __init__(self, widgets, **kwargs):
        self.widgets = widgets
        super().__init__(
            name="network-connections",
            spacing=4,
            orientation="vertical",
            h_expand=True,
            v_expand=True,
            v_align="fill",
            **kwargs,
        )

        self.nc = widgets.network_client

        self._rid = self._scan_id = None
        self._destroyed = False
        self.current_settings_ssid = None
        self._previous_page = "main"
        self._slots = {"connected": [], "avail": [], "saved": []}
        self._is_current_connected = False
        self._subs = Subscriptions()
        self._wifi = None

        self._build()
        self._subs.connect(self.nc, "device-ready", self._on_device_ready)
        self._subs.connect(self.nc, "connection-error", self._on_connection_error)
        self.connect("destroy", lambda *_: self.cleanup())
        self._on_device_ready()

    def _build(self):
        self.scan_btn = _header_button("network-scan", icons.radar, "Scan", self._on_scan)
        self.saved_btn = _header_button(
            "network-saved", icons.save, "Saved Networks", lambda *_: self._toggle_page("saved"),
        )
        self.qr_btn = _header_button(
            "network-qr", icons.scan, "Scan QR Code", lambda *_: self._toggle_page("qr"),
        )
        back = Button(
            name="network-back",
            child=Label(markup=icons.chevron_left, name="network-back-label"),
            on_clicked=self._on_back_click,
        )
        self.header_title = Label(label="Wi-Fi", v_align="center", name="header-title")

        header = CenterBox(
            start_children=(back,),
            center_children=(self.header_title,),
            end_children=(Box(
                spacing=4, orientation="horizontal",
                children=(self.saved_btn, self.qr_btn, self.scan_btn),
            ),),
        )
        header.set_margin_bottom(8)
        self.add(header)

        self.stack = Stack(transition_type="crossfade", h_expand=True, v_expand=True, v_align="fill")
        self.add(self.stack)
        self.stack.add_named(
            _status_box(
                icons.wifi_off, _message("Wi-Fi is disabled"),
                _action_button("Turn On", self._turn_on_wifi),
            ),
            "off",
        )

        self.lists_stack = Stack(
            transition_type="slide-left-right", h_expand=True, v_expand=True, v_align="fill",
        )

        self.connected_box = Box(spacing=2, orientation="vertical")
        self.avail_box = Box(spacing=2, orientation="vertical")
        self.saved_box = Box(spacing=2, orientation="vertical")

        avail_empty = _status_box(
            icons.radar, _message("No networks found"), _action_button("Scan", self._on_scan),
        )
        avail_empty.set_margin_top(24)
        avail_empty.set_margin_bottom(24)

        self.avail_stack = Stack(transition_type="crossfade", h_expand=True, v_expand=True)
        self.avail_stack.add_named(self.avail_box, "list")
        self.avail_stack.add_named(avail_empty, "empty")
        self.avail_section = _section("Available Networks", self.avail_stack)

        self.main_scroll = _scroll(Box(
            spacing=4, orientation="vertical", children=(self.connected_box, self.avail_section),
        ))
        self.saved_scroll = _scroll(Box(
            spacing=4, orientation="vertical", children=(_section("Saved Networks", self.saved_box),),
        ))
        self.settings_scroll = self._build_settings_page()
        for name, page in (
            ("main", self.main_scroll), ("saved", self.saved_scroll), ("settings", self.settings_scroll),
        ):
            self.lists_stack.add_named(page, name)

        self.qr_page = QrScanPage(self.nc, self.lists_stack, lambda: self._show_page("main"))
        self.lists_stack.add_named(self.qr_page, "qr")
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

        self.btn_net_forget = action(icons.trash, "Remove", self._do_forget, "net-forget")
        self.btn_net_disconnect = action(icons.cancel, "Disconnect", self._do_disconnect_or_connect)
        self.lbl_net_disconnect = self.btn_net_disconnect.get_child()
        self.btn_net_share = action(icons.scan, "Share", self._do_share)

        actions_box = Box(
            orientation="horizontal", spacing=8, h_align="center", h_expand=True,
            children=(self.btn_net_forget, self.btn_net_disconnect, self.btn_net_share),
        )
        actions_box.set_margin_bottom(12)

        self.qr_revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.qr_image = _styled(Image(), "qr-img")
        self.qr_password_lbl = _styled(Label(selectable=True), "qr-password-text")
        self.qr_revealer.add(_styled(
            Box(
                orientation="vertical", spacing=8, h_align="center",
                children=(self.qr_image, self.qr_password_lbl),
            ),
            "qr-container",
        ))

        # Подписи значений по ключам словаря get_network_details().
        self._info = {}
        info_group = _styled(Box(orientation="vertical", spacing=2), "net-info-group")
        for key, title in (
            ("strength", "Signal Strength"), ("frequency", "Frequency"), ("security", "Security"),
            ("type", "Type"), ("ip", "IP Address"), ("gateway", "Gateway / DNS"),
        ):
            value = Label(label="-", h_align="end", selectable=key in ("ip", "gateway"))
            self._info[key] = value
            row = _styled(Box(orientation="horizontal"), "net-info-row")
            row.pack_start(Label(label=title, h_align="start", name="dim-label"), True, True, 0)
            row.pack_end(value, False, False, 0)
            info_group.add(row)

        settings_box = Box(orientation="vertical", spacing=8, h_expand=True)
        settings_box.set_margin_start(12)
        settings_box.set_margin_end(12)
        for child in (actions_box, self.qr_revealer, info_group):
            settings_box.add(child)
        return _scroll(settings_box)

    def _show_page(self, name: str, title: str = "Wi-Fi"):
        """Единая точка смены подстраницы: стек, заголовок, кнопки шапки, состояние pressed."""
        self.lists_stack.set_visible_child_name(name)
        self.header_title.set_label(title)
        for btn in (self.scan_btn, self.saved_btn, self.qr_btn):
            btn.set_visible(name != "settings")
        _cls(self.saved_btn, "pressed", name == "saved")
        _cls(self.qr_btn, "pressed", name == "qr")

    def _toggle_page(self, target: str):
        current = self.lists_stack.get_visible_child_name()
        self._show_page("main" if current == target else target)

    def _reset_share(self):
        self.qr_revealer.set_reveal_child(False)
        _cls(self.btn_net_share, "active", False)

    def open_settings(self, ssid):
        if not ssid:
            return
        self._previous_page = self.lists_stack.get_visible_child_name()
        self.current_settings_ssid = ssid
        self._reset_share()

        details = self.nc.get_network_details(ssid)
        connected = self._is_current_connected = details["connected"]
        icon, text = (icons.cancel, "Disconnect") if connected else (icons.accept, "Connect")
        self.lbl_net_disconnect.set_markup(f"<span size='large'>{icon}</span> {text}")
        self.btn_net_disconnect.set_sensitive(connected or self.nc.is_network_available(ssid))

        gateway = details["gateway"]
        shown = {**details, "gateway": f"{gateway} / {details['dns']}" if gateway != "N/A" else "N/A"}
        for key, label in self._info.items():
            label.set_label(shown[key])

        self._show_page("settings", ssid)

    def _on_action_done(self, _ssid):
        self.request_refresh(300)

    def _do_forget(self, _btn):
        if self.current_settings_ssid:
            self.nc.delete_saved_network(self.current_settings_ssid, self._on_action_done)
            self._show_page(self._previous_page)

    def _do_disconnect_or_connect(self, _btn):
        ssid = self.current_settings_ssid
        if not ssid:
            return
        if self._is_current_connected:
            self.nc.disconnect_network(self._on_action_done)
        else:
            self.nc.connect_to_saved_network(ssid, self._on_action_done)
        self._show_page(self._previous_page)

    def _do_share(self, btn):
        if self.qr_revealer.get_reveal_child():
            self._reset_share()
            return
        ssid = self.current_settings_ssid
        if not ssid:
            return
        password = self.nc.get_network_password(ssid)
        _save_qr(_build_wifi_qr(ssid, password), _QR_SHARE_PATH)
        self.qr_image.set_from_file(_QR_SHARE_PATH)
        self.qr_password_lbl.set_label(f"Password: {password}" if password else "Open network")
        _cls(btn, "active")
        self.qr_revealer.set_reveal_child(True)

    def _on_back_click(self, _btn):
        page = self.lists_stack.get_visible_child_name()
        if page == "settings":
            self._show_page(self._previous_page)
        elif page in ("saved", "qr"):
            self._show_page("main")
        else:
            self.widgets.show_notif()

    def _turn_on_wifi(self, *_):
        wifi = self.nc.wifi_device
        if wifi is not None:
            wifi.enabled = True

    def _sync_enabled(self) -> bool:
        """Приводит UI к состоянию Wi-Fi: страница on/off, доступность кнопок шапки.

        Идемпотентна и дешёвая (читает кэш сервиса), поэтому вызывается синхронно
        на каждое изменение, а не через отложенный _ref. Нет адаптера или
        сервис ещё не готов — трактуется как «выключено».
        """
        wifi = self.nc.wifi_device
        enabled = wifi is not None and wifi.enabled
        self.stack.set_visible_child_name("on" if enabled else "off")
        for btn in (self.scan_btn, self.saved_btn, self.qr_btn):
            btn.set_sensitive(enabled)
        if not enabled:
            if WifiSlot._active_pw_slot is not None:
                WifiSlot._active_pw_slot._close_pw()
            # Возврат на main останавливает камеру: QrScanPage слушает visible-child-name.
            if self.lists_stack.get_visible_child_name() != "main":
                self._show_page("main")
                self.current_settings_ssid = None
                self._reset_share()
        return enabled

    def _on_device_ready(self, *_):
        # Wi-Fi сервис может смениться (hot-plug адаптера): переподписываемся один раз.
        self._wifi = self._subs.rebind(self._wifi, self.nc.wifi_device, "changed", self._on_wifi_changed)
        self._on_wifi_changed()

    def _on_wifi_changed(self, *_):
        self._sync_enabled()
        self._sched()

    def _on_connection_error(self, _client, ssid, _msg):
        for pool in self._slots.values():
            for slot in pool:
                # Скрытые слоты хранят устаревший ssid — их не трогаем.
                if slot.get_visible() and slot.ssid == ssid:
                    slot._err()

    def _sched(self, *_):
        if self._rid is None:
            self.request_refresh()

    def request_refresh(self, delay: int = 500):
        """Единая точка отложенного обновления: новый запрос заменяет ожидающий."""
        if self._destroyed:
            return
        _cancel(self._rid)
        self._rid = GLib.timeout_add(delay, self._ref)

    def _ref(self) -> bool:
        self._rid = None
        if self._destroyed or not self._sync_enabled() or WifiSlot._active_pw_slot is not None:
            return False

        wifi = self.nc.wifi_device
        current = None if wifi.ssid in ("", "Disconnected") else wifi.ssid
        saved = self.nc.saved_networks()
        saved_set = frozenset(saved)
        avail_d = {ap["ssid"]: ap for ap in wifi.access_points}

        connected_data = []
        if current:
            ap = avail_d.get(current) or _placeholder(current, 100)
            connected_data.append((ap, current in saved_set, True))
        available_data = [
            (ap, ssid in saved_set, False) for ssid, ap in avail_d.items() if ssid != current
        ]
        saved_data = [
            (avail_d.get(ssid) or _placeholder(ssid), True, ssid == current) for ssid in saved
        ]

        self._ubox(self.connected_box, self._slots["connected"], connected_data, avail_d)
        self._ubox(self.avail_box, self._slots["avail"], available_data, avail_d)
        self._ubox(self.saved_box, self._slots["saved"], saved_data, avail_d)

        self.connected_box.set_visible(bool(connected_data))
        self.avail_section.set_visible(True)
        self.avail_stack.set_visible_child_name("list" if available_data else "empty")
        return False

    def _ubox(self, box, pool, data, avail_d):
        while len(pool) < len(data):
            slot = WifiSlot(self.nc, self)
            pool.append(slot)
            box.add(slot)
        for slot in pool[len(data):]:
            slot.hide()
        for slot, (ap, saved, conn) in zip(pool, data):
            slot.update(ap, saved, conn, ap["ssid"] in avail_d)
            slot.show()

    def _set_scanning(self, on: bool):
        for widget in (self.scan_btn, self.scan_btn.get_child()):
            _cls(widget, "scanning", on)

    def _on_scan(self, _btn):
        wifi = self.nc.wifi_device
        if self._scan_id is not None or wifi is None or not wifi.enabled:
            return
        self._set_scanning(True)
        wifi.scan()
        self._scan_id = GLib.timeout_add(3500, self._on_scan_done)

    def _on_scan_done(self) -> bool:
        self._scan_id = None
        self._set_scanning(False)
        return False

    def cleanup(self):
        if self._destroyed:
            return
        self._destroyed = True
        self._rid = _cancel(self._rid)
        self._scan_id = _cancel(self._scan_id)
        WifiSlot._active_pw_slot = None
        self._subs.clear()
        self._wifi = None
        for pool in self._slots.values():
            for slot in pool:
                slot.destroy()
            pool.clear()
        self.nc = self.widgets = None