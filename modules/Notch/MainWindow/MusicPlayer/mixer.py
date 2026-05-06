from gi.repository import Gdk, Gtk, GLib
from fabric.widgets.box import Box
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.label import Label
from fabric.widgets.scale import Scale
from fabric.widgets.scrolledwindow import ScrolledWindow


_MUTED_CLASS = "muted"
_SECTION_MUTED_CLASS = "section-muted"
_ANIM_STEPS = 25
_ANIM_INTERVAL_MS = 16
_INV_ANIM_STEPS = 1.0 / _ANIM_STEPS
_CLICK_STEPS = 20
_CLICK_MS = 14
_MAX_DEVICE_CHARS = 22
_MAX_LIST_CHARS = 28

_SLOT_HEIGHT = 56
_SLOTS_VISIBLE = 2
_SCROLL_HEIGHT = _SLOT_HEIGHT * _SLOTS_VISIBLE + 4

_pointer_cursor = None
_default_cursor = None


def _ensure_cursors(display):
    global _pointer_cursor, _default_cursor
    if _pointer_cursor is None:
        _pointer_cursor = Gdk.Cursor.new_from_name(display, "pointer")
        _default_cursor = Gdk.Cursor.new_from_name(display, "default")

def _on_enter(widget, _event):
    win = widget.get_window()
    if win:
        _ensure_cursors(win.get_display())
        win.set_cursor(_pointer_cursor)
    return False

def _on_leave(widget, _event):
    win = widget.get_window()
    if win:
        _ensure_cursors(win.get_display())
        win.set_cursor(_default_cursor)
    return False

def _ease_out_cubic(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3

def _val_from_x(scale: Gtk.Widget, x: float) -> float:
    w = scale.get_allocation().width
    return max(0.0, min(1.0, x / w)) if w > 0 else 0.0

def _truncate(name: str, n: int = _MAX_DEVICE_CHARS) -> str:
    if not name:
        return "Unknown"
    return name if len(name) <= n else name[: n - 1] + "…"

def _make_ctrl_btn(label_text: str) -> Gtk.Button:
    btn = Gtk.Button()
    btn.set_name("mixer-ctrl-btn")
    btn.set_relief(Gtk.ReliefStyle.NONE)
    btn.set_hexpand(True)
    btn.set_vexpand(False)

    lbl = Gtk.Label(label=label_text)
    lbl.set_ellipsize(3)
    lbl.set_xalign(0.5)
    btn.add(lbl)

    btn.add_events(
        Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
    )
    btn.connect("enter-notify-event", _on_enter)
    btn.connect("leave-notify-event", _on_leave)
    return btn


class DeviceDropdown(Gtk.Box):
    def __init__(self, audio, is_input: bool):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_name("device-dropdown-wrap")
        self.set_hexpand(True)
        self.set_vexpand(False)

        self._audio = audio
        self._is_input = is_input
        self._open = False
        self._header_ref = None

        self._btn = Gtk.Button()
        self._btn.set_name("mixer-ctrl-btn")
        self._btn.set_relief(Gtk.ReliefStyle.NONE)
        self._btn.set_hexpand(True)
        self._btn.set_vexpand(False)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        row.set_halign(Gtk.Align.FILL)

        self._dev_lbl = Gtk.Label(label="…")
        self._dev_lbl.set_name("device-dropdown-label")
        self._dev_lbl.set_ellipsize(3)
        self._dev_lbl.set_xalign(0.0)
        self._dev_lbl.set_hexpand(True)

        self._arrow = Gtk.Label(label="▾")
        self._arrow.set_name("device-dropdown-arrow")
        self._arrow.set_xalign(1.0)

        row.pack_start(self._dev_lbl, True,  True,  0)
        row.pack_start(self._arrow,   False, False, 0)
        self._btn.add(row)

        self._btn.add_events(
            Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self._btn.connect("enter-notify-event", _on_enter)
        self._btn.connect("leave-notify-event", _on_leave)
        self._btn.connect("clicked", self._on_clicked)

        self.pack_start(self._btn, True, True, 0)

        self._rev = Gtk.Revealer()
        self._rev.set_name("device-revealer")
        self._rev.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._rev.set_transition_duration(180)
        self._rev.set_halign(Gtk.Align.START)
        self._rev.set_valign(Gtk.Align.START)
        self._rev.set_hexpand(False)
        self._rev.set_reveal_child(False)

        GLib.timeout_add(300, self._deferred_refresh)
        self.show_all()

    def register_revealer(self, overlay: Gtk.Overlay, header_box: Gtk.Box):
        self._header_ref = header_box
        overlay.add_overlay(self._rev)
        overlay.set_overlay_pass_through(self._rev, False)

    def _deferred_refresh(self):
        self.refresh_label()
        return False

    def _devices(self):
        try:
            devs = (
                self._audio.microphones if self._is_input
                else self._audio.speakers
            )
            return [d for d in (devs or []) if d is not None]
        except Exception:
            return []

    def _default_device(self):
        try:
            return (
                self._audio.microphone if self._is_input
                else self._audio.speaker
            )
        except Exception:
            return None

    def refresh_label(self):
        dev = self._default_device()
        if dev is not None:
            name = (
                getattr(dev, "description", None)
                or getattr(dev, "name", None)
                or "Unknown"
            )
            self._dev_lbl.set_label(_truncate(name, _MAX_DEVICE_CHARS))
            self._btn.set_tooltip_text(name)
        else:
            devs = self._devices()
            if devs:
                name = (
                    getattr(devs[0], "description", None)
                    or getattr(devs[0], "name", None)
                    or "Unknown"
                )
                self._dev_lbl.set_label(_truncate(name, _MAX_DEVICE_CHARS))
            else:
                self._dev_lbl.set_label("No device")
        return False

    def _on_clicked(self, _w):
        if self._open:
            self._collapse()
        else:
            self._expand()

    def _expand(self):
        self._build_list()
        self._open = True
        self._rev.set_reveal_child(True)
        self._arrow.set_label("▴")
        self._btn.get_style_context().add_class("open")

    def _collapse(self):
        self._open = False
        self._rev.set_reveal_child(False)
        self._arrow.set_label("▾")
        self._btn.get_style_context().remove_class("open")

    def _build_list(self):
        old = self._rev.get_child()
        if old:
            self._rev.remove(old)
            old.destroy()

        btn_w = self._btn.get_allocated_width()
        header_h = (
            self._header_ref.get_allocated_height()
            if self._header_ref else 0
        )
        self._rev.set_margin_top(header_h)

        devices  = self._devices()
        cur      = self._default_device()
        cur_name = getattr(cur, "name", None) if cur else None

        container = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=0
        )
        container.set_name("device-list-container")
        if btn_w > 10:
            container.set_size_request(btn_w, -1)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_name("device-list-separator")
        container.pack_start(sep, False, False, 0)

        if not devices:
            lbl = Gtk.Label(label="No devices found")
            lbl.set_name("device-list-empty")
            lbl.set_xalign(0.5)
            container.pack_start(lbl, False, False, 8)
        else:
            for dev in devices:
                desc = (
                    getattr(dev, "description", None)
                    or getattr(dev, "name", None)
                    or "?"
                )
                dev_name = getattr(dev, "name", None)
                active   = bool(
                    cur_name and dev_name and dev_name == cur_name
                )

                item_btn = Gtk.Button()
                item_btn.set_name(
                    "device-list-item-active" if active
                    else "device-list-item"
                )
                item_btn.set_relief(Gtk.ReliefStyle.NONE)
                item_btn.set_hexpand(False)

                inner = Gtk.Box(
                    orientation=Gtk.Orientation.HORIZONTAL, spacing=6
                )

                dot = Gtk.Label(label="▶" if active else "")
                dot.set_name("device-list-dot")
                dot.set_size_request(14, -1)
                dot.set_xalign(0.5)

                name_lbl = Gtk.Label(
                    label=_truncate(desc, _MAX_LIST_CHARS)
                )
                name_lbl.set_name("device-list-name")
                name_lbl.set_xalign(0.0)
                name_lbl.set_ellipsize(3)
                name_lbl.set_hexpand(True)
                name_lbl.set_tooltip_text(desc)

                inner.pack_start(dot,      False, False, 0)
                inner.pack_start(name_lbl, True,  True,  0)
                item_btn.add(inner)

                item_btn.add_events(
                    Gdk.EventMask.ENTER_NOTIFY_MASK
                    | Gdk.EventMask.LEAVE_NOTIFY_MASK
                )
                item_btn.connect("enter-notify-event", _on_enter)
                item_btn.connect("leave-notify-event", _on_leave)
                item_btn.connect(
                    "clicked", lambda _b, d=dev: self._select(d)
                )
                container.pack_start(item_btn, False, False, 0)

        container.show_all()

        scroll = Gtk.ScrolledWindow()
        scroll.set_name("device-list-scroll")
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
        scroll.set_hexpand(False)
        scroll.set_vexpand(False)
        if btn_w > 10:
            scroll.set_size_request(btn_w, -1)
        scroll.add(container)
        scroll.show_all()
        self._rev.add(scroll)

    def _select(self, device):
        self._collapse()
        self._switch(device)

    def _switch(self, device):
        try:
            from gi.repository import Cvc
            ctrl   = device._control
            stream = device._stream
            if isinstance(stream, Cvc.MixerSource):
                ctrl.set_default_source(stream)
            elif isinstance(stream, Cvc.MixerSink):
                ctrl.set_default_sink(stream)
        except Exception as e:
            print(f"[DeviceDropdown] switch error: {e}")
        GLib.timeout_add(200, self.refresh_label)

    def cleanup(self):
        self._collapse()
        old = self._rev.get_child()
        if old:
            self._rev.remove(old)
            old.destroy()
        self._audio = None


class MixerSlider(Scale):
    __slots__ = (
        "stream", "_updating", "_muted_style",
        "_canim_id", "_canim_s", "_canim_e", "_canim_n", "_pressed",
    )

    def __init__(self, stream, **kwargs):
        super().__init__(
            name="control-slider",
            orientation="h",
            h_expand=True,
            h_align="fill",
            has_origin=True,
            increments=(0.01, 0.1),
            style_classes=("no-icon",),
            **kwargs,
        )
        self.stream       = stream
        self._updating    = False
        self._canim_id    = None
        self._pressed     = False
        self._muted_style = stream.muted

        self.set_draw_value(False)
        self.set_size_request(100, 20)
        self.set_value(stream.volume * 0.01)
        self.set_tooltip_text(f"{int(stream.volume + 0.5)}%")

        self.connect("value-changed",        self._on_value_changed)
        self.connect("button-press-event",   self._on_press)
        self.connect("button-release-event", self._on_release)
        self.connect("motion-notify-event",  self._on_motion)

        ltype = getattr(stream, "type", "").lower()
        self.add_style_class(
            "mic" if ("microphone" in ltype or "input" in ltype) else "vol"
        )
        if self._muted_style:
            self.add_style_class(_MUTED_CLASS)

    def _on_press(self, _, ev):
        if ev.button != 1:
            return False
        self._pressed = True
        self._cancel_canim()
        target = _val_from_x(self, ev.x)
        self._canim_s, self._canim_e, self._canim_n = self.value, target, 0
        self._canim_id = GLib.timeout_add(_CLICK_MS, self._canim_tick)
        return True

    def _on_release(self, _, _ev):
        self._pressed = False
        return True

    def _on_motion(self, _, ev):
        if self._pressed:
            self._cancel_canim()
            self.value = _val_from_x(self, ev.x)
        return True

    def _canim_tick(self):
        self._canim_n += 1
        t = min(self._canim_n / float(_CLICK_STEPS), 1.0)
        self.value = (
            self._canim_s
            + (self._canim_e - self._canim_s) * _ease_out_cubic(t)
        )
        if self._canim_n >= _CLICK_STEPS:
            self._canim_id = None
            return False
        return True

    def _cancel_canim(self):
        if self._canim_id:
            GLib.source_remove(self._canim_id)
            self._canim_id = None

    def sync(self, stream, pct_str):
        if self._canim_id or self._pressed:
            return
        self._updating = True
        self.value = stream.volume * 0.01
        if pct_str:
            self.set_tooltip_text(pct_str)
        if stream.muted != self._muted_style:
            self._muted_style = stream.muted
            if self._muted_style:
                self.add_style_class(_MUTED_CLASS)
            else:
                self.remove_style_class(_MUTED_CLASS)
        self._updating = False

    def _on_value_changed(self, _):
        if self._updating or not self.stream:
            return
        vol = self.value * 100.0
        if abs(self.stream.volume - vol) > 0.5:
            self.stream.volume = vol
            self.set_tooltip_text(f"{int(vol + 0.5)}%")

    def cleanup(self):
        self._cancel_canim()
        self.stream = None


class MixerSlot(Box):
    def __init__(self, stream):
        super().__init__(
            name="mixer-slot", orientation="v", spacing=2, h_expand=True
        )
        self.stream = stream
        self._last_pct = int(stream.volume + 0.5)
        self._last_desc = stream.description

        self.desc_lbl = Label(
            name="mixer-stream-desc",
            label=self._last_desc,
            h_align="start",
            ellipsization="end",
        )
        self.pct_lbl = Label(
            name="mixer-stream-pct",
            label=f"{self._last_pct}%",
            h_align="end",
            width_chars=4,
        )
        self.slider = MixerSlider(stream)

        self.add(
            CenterBox(
                name="mixer-slot-header",
                start_children=self.desc_lbl,
                end_children=self.pct_lbl,
                h_expand=True,
            )
        )
        self.add(self.slider)
        self._sig_id = stream.connect("changed", self._on_changed)

    def _on_changed(self, stream):
        pct     = int(stream.volume + 0.5)
        pct_str = f"{pct}%" if pct != self._last_pct else None
        if pct_str:
            self._last_pct = pct
            self.pct_lbl.set_label(pct_str)
        if stream.description != self._last_desc:
            self._last_desc = stream.description
            self.desc_lbl.set_label(self._last_desc)
        self.slider.sync(stream, pct_str)

    def cleanup(self):
        if self.stream and self._sig_id:
            try:
                self.stream.disconnect(self._sig_id)
            except Exception:
                pass
        self.slider.cleanup()
        self.stream = None


class MixerSection(Box):
    _size_group: Gtk.SizeGroup = None

    def __init__(
        self, title: str, audio=None, is_input: bool = False, **kwargs
    ):
        super().__init__(
            name="mixer-section",
            orientation="v",
            spacing=0,
            h_expand=True,
            v_expand=False,
            **kwargs,
        )
        self._audio = audio
        self._is_input      = is_input
        self._muted         = False
        self._saved_volumes = {}
        self._anim_id       = None
        self._slots: dict   = {}

        if MixerSection._size_group is None:
            MixerSection._size_group = Gtk.SizeGroup(
                mode=Gtk.SizeGroupMode.HORIZONTAL
            )
        MixerSection._size_group.add_widget(self)

        if audio:
            self._device_btn = DeviceDropdown(
                audio=audio, is_input=is_input
            )
        else:
            self._device_btn = None

        self._mute_btn = _make_ctrl_btn(title)
        self._mute_btn.connect("clicked", self._on_mute_clicked)

        self._header = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=4
        )
        self._header.set_name("mixer-header")
        self._header.set_homogeneous(True)
        self._header.set_hexpand(True)
        self._header.set_vexpand(False)
        self._header.set_margin_bottom(4)

        if self._device_btn:
            self._header.pack_start(self._device_btn, True, True, 0)
        else:
            fb = Gtk.Button(label="No device")
            fb.set_name("mixer-ctrl-btn")
            fb.set_relief(Gtk.ReliefStyle.NONE)
            fb.set_hexpand(True)
            fb.set_sensitive(False)
            self._header.pack_start(fb, True, True, 0)

        self._header.pack_start(self._mute_btn, True, True, 0)

        self._content = Box(
            name="mixer-content",
            orientation="v",
            spacing=4,
            h_expand=True,
        )

        scroll_name = (
            "inputs-scrolled" if is_input else "outputs-scrolled"
        )
        self._scroll = ScrolledWindow(
            name=scroll_name,
            child=self._content,
            h_expand=True,
            v_expand=False,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        self._scroll.set_propagate_natural_height(False)
        self._scroll.set_size_request(-1, _SCROLL_HEIGHT)

        base = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        base.set_hexpand(True)
        base.set_vexpand(False)
        base.pack_start(self._header, False, False, 0)
        base.pack_start(self._scroll, False, False, 0)

        self._overlay = Gtk.Overlay()
        self._overlay.set_hexpand(True)
        self._overlay.set_vexpand(False)
        self._overlay.add(base)

        if self._device_btn:
            self._device_btn.register_revealer(
                self._overlay, self._header
            )

        self.pack_start(self._overlay, False, False, 0)
        self.show_all()

    def do_get_preferred_width(self):
        min_w, _ = super().do_get_preferred_width()
        return (min_w, min_w)

    def do_get_preferred_width_for_height(self, height):
        min_w, _ = super().do_get_preferred_width_for_height(height)
        return (min_w, min_w)

    def do_get_preferred_height(self):
        min_h, _ = super().do_get_preferred_height()
        return (min_h, min_h)

    def do_get_preferred_height_for_width(self, width):
        min_h, _ = super().do_get_preferred_height_for_width(width)
        return (min_h, min_h)

    def _on_mute_clicked(self, _btn):
        if self._anim_id:
            GLib.source_remove(self._anim_id)
            self._anim_id = None

        if not self._muted:
            self._saved_volumes = {
                sid: slot.stream.volume
                for sid, slot in self._slots.items()
                if slot.stream
            }
            self._muted = True
            self._mute_btn.get_style_context().add_class(
                _SECTION_MUTED_CLASS
            )
        else:
            self._muted = False
            self._mute_btn.get_style_context().remove_class(
                _SECTION_MUTED_CLASS
            )

        self._run_anim(to_zero=self._muted)

    def _run_anim(self, to_zero: bool):
        anim_list = []
        for sid, slot in self._slots.items():
            if not slot.stream:
                continue
            start = slot.stream.volume
            end   = (
                0.0 if to_zero
                else self._saved_volumes.get(sid, 100.0)
            )
            anim_list.append((slot.stream, start, end - start))

        step = 0

        def _tick():
            nonlocal step
            step += 1
            t    = min(step * _INV_ANIM_STEPS, 1.0)
            ease = _ease_out_cubic(t)
            for stream, start, delta in anim_list:
                stream.volume = start + delta * ease
            if step >= _ANIM_STEPS:
                self._anim_id = None
                return False
            return True

        self._anim_id = GLib.timeout_add(_ANIM_INTERVAL_MS, _tick)

    def update_streams(self, streams):
        seen  = set()
        added = False
        for stream in streams:
            sid = id(stream)
            seen.add(sid)
            if sid not in self._slots:
                slot = MixerSlot(stream)
                self._slots[sid] = slot
                self._content.add(slot)
                added = True

        for sid in list(self._slots.keys()):
            if sid not in seen:
                slot = self._slots.pop(sid)
                self._content.remove(slot)
                slot.cleanup()
                slot.destroy()

        if added:
            self._content.show_all()

    def cleanup(self):
        if self._anim_id:
            GLib.source_remove(self._anim_id)
        if self._device_btn:
            self._device_btn.cleanup()
        if MixerSection._size_group:
            MixerSection._size_group.remove_widget(self)
        for slot in self._slots.values():
            slot.cleanup()
            slot.destroy()
        self._slots.clear()