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

_pointer_cursor: Gdk.Cursor | None = None
_default_cursor: Gdk.Cursor | None = None


def _get_cursors(display: Gdk.Display):
    global _pointer_cursor, _default_cursor
    if _pointer_cursor is None:
        _pointer_cursor = Gdk.Cursor.new_from_name(display, "pointer")
        _default_cursor = Gdk.Cursor.new_from_name(display, "default")
    return _pointer_cursor, _default_cursor


def _on_btn_enter(widget: Gtk.Widget, _event: Gdk.EventCrossing):
    win = widget.get_window()
    if win:
        pointer, _ = _get_cursors(win.get_display())
        win.set_cursor(pointer)
    return False


def _on_btn_leave(widget: Gtk.Widget, _event: Gdk.EventCrossing):
    win = widget.get_window()
    if win:
        _, default = _get_cursors(win.get_display())
        win.set_cursor(default)
    return False


class MixerSlider(Scale):
    __slots__ = ("stream", "_updating", "_muted_style")

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

        muted = stream.muted
        self._muted_style = muted

        self.set_draw_value(False)
        self.set_size_request(100, 20)

        volume = stream.volume
        self.set_value(volume * 0.01)
        self.set_tooltip_text(f"{int(volume + 0.5)}%")

        self.connect("value-changed", self._on_value_changed)

        stream_type = getattr(stream, "type", "").lower()
        self.add_style_class(
            "mic"
            if "microphone" in stream_type or "input" in stream_type
            else "vol",
        )

        if muted:
            self.add_style_class(_MUTED_CLASS)

    def sync(self, stream, pct_str):
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
        pct = int(stream.volume + 0.5)
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
            if sig_id := self._sig_id:
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

        # ── курсор-указатель ТОЛЬКО на кнопке ──
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

    # ── toggle mute / unmute ──────────────────────────────────────────────
    def _on_title_clicked(self, _btn):
        if self._anim_id is not None:
            GLib.source_remove(self._anim_id)
            self._anim_id = None

        ctx = self._title_btn.get_style_context()

        if not self._muted:
            self._saved_volumes.clear()
            for sid, slot in self._slots.items():
                s = slot.stream
                if s is not None:
                    self._saved_volumes[sid] = s.volume
            self._muted = True
            ctx.add_class(_SECTION_MUTED_CLASS)
            self._run_animation(to_zero=True)
        else:
            self._muted = False
            ctx.remove_class(_SECTION_MUTED_CLASS)
            self._run_animation(to_zero=False)

    # ── smooth volume ramp ────────────────────────────────────────────────
    def _run_animation(self, to_zero: bool):
        targets: dict[int, tuple[float, float]] = {}
        for sid, slot in self._slots.items():
            s = slot.stream
            if s is None:
                continue
            start = s.volume
            end = 0.0 if to_zero else self._saved_volumes.get(sid, 100.0)
            if abs(start - end) < 0.5:
                s.volume = end
                continue
            targets[sid] = (start, end)

        if not targets:
            if not to_zero:
                self._saved_volumes.clear()
            return

        step = [0]

        def _tick():
            step[0] += 1
            t = min(step[0] / _ANIM_STEPS, 1.0)
            ease = 1.0 - (1.0 - t) ** 3

            for sid, (s, e) in targets.items():
                slot = self._slots.get(sid)
                if slot is None or slot.stream is None:
                    continue
                slot.stream.volume = s + (e - s) * ease

            if step[0] >= _ANIM_STEPS:
                self._anim_id = None
                if not to_zero:
                    self._saved_volumes.clear()
                return False
            return True

        self._anim_id = GLib.timeout_add(_ANIM_INTERVAL_MS, _tick)

    # ── public API ────────────────────────────────────────────────────────
    def update_streams(self, streams):
        slots = self._slots
        content = self._content
        seen = set()
        changed = False

        for stream in streams:
            sid = id(stream)
            if sid in seen:
                continue
            seen.add(sid)
            if sid not in slots:
                slot = MixerSlot(stream)
                slots[sid] = slot
                content.add(slot)
                changed = True

        stale = slots.keys() - seen
        if stale:
            changed = True
            for sid in stale:
                slot = slots.pop(sid)
                content.remove(slot)
                slot.cleanup()
                slot.destroy()
                self._saved_volumes.pop(sid, None)

        if changed:
            content.show_all()

    def cleanup(self):
        if self._anim_id is not None:
            GLib.source_remove(self._anim_id)
            self._anim_id = None
        for slot in self._slots.values():
            slot.cleanup()
            slot.destroy()
        self._slots.clear()
        self._saved_volumes.clear()