from gi.repository import Gdk, Gtk, GLib

from fabric.widgets.box import Box
from fabric.widgets.button import Button
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

_pointer_cursor = None
_default_cursor = None


def _ensure_cursors(display):
    global _pointer_cursor, _default_cursor
    if _pointer_cursor is None:
        _pointer_cursor = Gdk.Cursor.new_from_name(display, "pointer")
        _default_cursor = Gdk.Cursor.new_from_name(display, "default")


def _on_btn_enter(widget, _event):
    win = widget.get_window()
    if win:
        _ensure_cursors(win.get_display())
        win.set_cursor(_pointer_cursor)
    return False


def _on_btn_leave(widget, _event):
    win = widget.get_window()
    if win:
        _ensure_cursors(win.get_display())
        win.set_cursor(_default_cursor)
    return False


def _ease_out_cubic(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


def _val_from_x(scale: Gtk.Widget, x: float) -> float:
    w = scale.get_allocation().width
    if w <= 0:
        return 0.0
    return max(0.0, min(1.0, x / w))


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
        self.stream = stream
        self._updating = False

        self._canim_id = None
        self._canim_s = self._canim_e = 0.0
        self._canim_n = 0
        self._pressed = False

        muted = stream.muted
        self._muted_style = muted

        self.set_draw_value(False)
        self.set_size_request(100, 20)

        volume = stream.volume
        self.set_value(volume * 0.01)
        self.set_tooltip_text(f"{int(volume + 0.5)}%")

        self.connect("value-changed", self._on_value_changed)
        self.connect("button-press-event", self._on_click_press)
        self.connect("button-release-event", self._on_click_release)
        self.connect("motion-notify-event", self._on_click_motion)

        ltype = getattr(stream, "type", "").lower()
        self.add_style_class(
            "mic" if "microphone" in ltype or "input" in ltype else "vol"
        )

        if muted:
            self.add_style_class(_MUTED_CLASS)

    def _on_click_press(self, _, event):
        if event.button != 1:
            return False
        target = _val_from_x(self, event.x)
        current = self.value
        if abs(target - current) < 0.03:
            return False
        self._pressed = True
        self._cancel_canim()
        self._canim_s = current
        self._canim_e = target
        self._canim_n = 0
        self._canim_id = GLib.timeout_add(_CLICK_MS, self._canim_tick)
        return True

    def _on_click_release(self, _, event):
        if not self._pressed:
            return False
        self._pressed = False
        return True

    def _on_click_motion(self, _, event):
        if not self._pressed:
            return False
        self._cancel_canim()
        self.value = _val_from_x(self, event.x)
        return True

    def _canim_tick(self):
        self._canim_n += 1
        t = min(self._canim_n / float(_CLICK_STEPS), 1.0)
        self.value = self._canim_s + (self._canim_e - self._canim_s) * _ease_out_cubic(t)
        if self._canim_n >= _CLICK_STEPS:
            self._canim_id = None
            return False
        return True

    def _cancel_canim(self):
        if self._canim_id is not None:
            GLib.source_remove(self._canim_id)
            self._canim_id = None

    def sync(self, stream, pct_str):
        if self._canim_id is not None or self._pressed:
            return
        self._updating = True
        self.value = stream.volume * 0.01
        if pct_str is not None:
            self.set_tooltip_text(pct_str)
        muted = stream.muted
        if muted != self._muted_style:
            self._muted_style = muted
            if muted:
                self.add_style_class(_MUTED_CLASS)
            else:
                self.remove_style_class(_MUTED_CLASS)
        self._updating = False

    def _on_value_changed(self, _):
        if self._updating:
            return
        stream = self.stream
        if stream is None:
            return
        new_vol = self.value * 100.0
        if abs(stream.volume - new_vol) > 0.5:
            stream.volume = new_vol
            self.set_tooltip_text(f"{int(new_vol + 0.5)}%")

    def cleanup(self):
        self._cancel_canim()
        self.stream = None


class MixerSlot(Box):
    __slots__ = (
        "stream", "desc_lbl", "pct_lbl", "slider",
        "_last_pct", "_last_desc", "_sig_id",
    )

    def __init__(self, stream):
        super().__init__(
            name="mixer-slot",
            orientation="v",
            spacing=2,
            h_expand=True,
            v_expand=False,
        )
        self.stream = stream

        volume = stream.volume
        pct = int(volume + 0.5)
        desc = stream.description

        self._last_pct = pct
        self._last_desc = desc

        desc_lbl = Label(
            name="mixer-stream-desc",
            label=desc,
            h_expand=True,
            h_align="start",
            v_align="center",
            ellipsization="end",
        )
        self.desc_lbl = desc_lbl

        pct_lbl = Label(
            name="mixer-stream-pct",
            label=f"{pct}%",
            h_expand=False,
            h_align="end",
            v_align="center",
            width_chars=4,
        )
        self.pct_lbl = pct_lbl

        slider = MixerSlider(stream)
        self.slider = slider

        self.add(
            CenterBox(
                name="mixer-slot-header",
                start_children=desc_lbl,
                end_children=pct_lbl,
                h_expand=True,
            ),
        )
        self.add(slider)

        self._sig_id = stream.connect("changed", self._on_stream_changed)

    def _on_stream_changed(self, stream):
        vol = stream.volume
        pct = int(vol + 0.5)
        pct_str = None

        if pct != self._last_pct:
            self._last_pct = pct
            pct_str = f"{pct}%"
            self.pct_lbl.set_label(pct_str)

        desc = stream.description
        if desc != self._last_desc:
            self._last_desc = desc
            self.desc_lbl.set_label(desc)

        self.slider.sync(stream, pct_str)

    def cleanup(self):
        stream = self.stream
        if stream is not None:
            sig_id = self._sig_id
            if sig_id:
                try:
                    stream.disconnect(sig_id)
                except Exception:
                    pass
                self._sig_id = None
        self.slider.cleanup()
        self.stream = None


class MixerSection(Box):
    __slots__ = (
        "_title_btn", "_content", "_slots", "scroll",
        "_muted", "_saved_volumes", "_anim_id",
    )

    def __init__(self, title: str, **kwargs):
        title_btn = Button(
            name="mixer-section-title",
            label=title,
            h_expand=True,
            h_align="fill",
            v_expand=False,
        )
        self._title_btn = title_btn
        self._muted = False
        self._saved_volumes: dict[int, float] = {}
        self._anim_id = None

        title_btn.connect("clicked", self._on_title_clicked)
        title_btn.add_events(
            Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK,
        )
        title_btn.connect("enter-notify-event", _on_btn_enter)
        title_btn.connect("leave-notify-event", _on_btn_leave)

        content = Box(
            name="mixer-content",
            orientation="v",
            spacing=4,
            h_expand=True,
            v_expand=False,
        )
        content.set_margin_end(4)
        content.set_margin_bottom(4)
        self._content = content
        self._slots: dict[int, MixerSlot] = {}

        scroll = ScrolledWindow(
            name=f"{title.lower()}-scrolled",
            child=content,
            h_expand=True,
            v_expand=True,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_width=False,
            propagate_height=False,
        )
        scroll.set_overlay_scrolling(True)
        self.scroll = scroll

        super().__init__(
            name="mixer-section",
            orientation="v",
            spacing=4,
            h_expand=True,
            v_expand=True,
            children=(title_btn, scroll),
            **kwargs,
        )

    def _on_title_clicked(self, _btn):
        anim_id = self._anim_id
        if anim_id is not None:
            GLib.source_remove(anim_id)
            self._anim_id = None

        ctx = self._title_btn.get_style_context()

        if not self._muted:
            saved = self._saved_volumes
            saved.clear()
            for sid, slot in self._slots.items():
                s = slot.stream
                if s is not None:
                    saved[sid] = s.volume
            self._muted = True
            ctx.add_class(_SECTION_MUTED_CLASS)
            self._run_animation(True)
        else:
            self._muted = False
            ctx.remove_class(_SECTION_MUTED_CLASS)
            self._run_animation(False)

    def _run_animation(self, to_zero: bool):
        slots = self._slots
        saved = self._saved_volumes

        anim_list = []
        for sid, slot in slots.items():
            s = slot.stream
            if s is None:
                continue
            start = s.volume
            end = 0.0 if to_zero else saved.get(sid, 100.0)
            delta = end - start
            if abs(delta) < 0.5:
                s.volume = end
                continue
            anim_list.append((slot, start, delta))

        if not anim_list:
            if not to_zero:
                saved.clear()
            return

        step = 0

        def _tick():
            nonlocal step
            step += 1
            t = step * _INV_ANIM_STEPS
            if t > 1.0:
                t = 1.0
            ease = 1.0 - (1.0 - t) ** 3

            for slot, start, delta in anim_list:
                s = slot.stream
                if s is not None:
                    s.volume = start + delta * ease

            if step >= _ANIM_STEPS:
                self._anim_id = None
                if not to_zero:
                    self._saved_volumes.clear()
                return False
            return True

        self._anim_id = GLib.timeout_add(_ANIM_INTERVAL_MS, _tick)

    def update_streams(self, streams):
        slots = self._slots
        content = self._content
        seen = set()
        added = False

        for stream in streams:
            sid = id(stream)
            if sid not in seen:
                seen.add(sid)
                if sid not in slots:
                    slot = MixerSlot(stream)
                    slots[sid] = slot
                    content.add(slot)
                    added = True

        if len(seen) < len(slots):
            stale = [sid for sid in slots if sid not in seen]
            saved = self._saved_volumes
            for sid in stale:
                slot = slots.pop(sid)
                content.remove(slot)
                slot.cleanup()
                slot.destroy()
                saved.pop(sid, None)

        if added:
            content.show_all()

    def cleanup(self):
        anim_id = self._anim_id
        if anim_id is not None:
            GLib.source_remove(anim_id)
            self._anim_id = None
        for slot in self._slots.values():
            slot.cleanup()
            slot.destroy()
        self._slots.clear()
        self._saved_volumes.clear()