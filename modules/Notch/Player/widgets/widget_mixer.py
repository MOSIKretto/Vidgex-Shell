from gi.repository import Gtk

from fabric.widgets.box import Box
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.label import Label
from fabric.widgets.scale import Scale
from fabric.widgets.scrolledwindow import ScrolledWindow


_MUTED_CLASS = "muted"

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
    __slots__ = ("_title_lbl", "_content", "_slots", "scroll")

    def __init__(self, title: str, **kwargs):
        title_lbl = Label(
            name="mixer-section-title",
            label=title,
            h_expand=True,
            h_align="start",
            v_expand=False,
        )
        self._title_lbl = title_lbl

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
            children=(title_lbl, scroll),
            **kwargs,
        )

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

        if changed:
            content.show_all()

    def cleanup(self):
        for slot in self._slots.values():
            slot.cleanup()
            slot.destroy()
        self._slots.clear()