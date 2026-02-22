from fabric.audio.service import Audio
from fabric.widgets.box import Box
from fabric.widgets.label import Label
from fabric.widgets.scale import Scale
from fabric.widgets.scrolledwindow import ScrolledWindow

from gi.repository import Gtk

_SL_H, _LBL_H, _SEC_H, _MAX_CH = 30, 20, 150, 45


class MixerSlider(Scale):
    __slots__ = ('stream', '_upd', '_sig', '_last_vol', '_muted_style')

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
        self._upd = False
        
        v = stream.volume
        self._last_vol = int(v + 0.5)
        self._muted_style = stream.muted

        self.set_value(v * 0.01)
        self.set_size_request(-1, _SL_H)
        self.set_tooltip_text(f"{self._last_vol}%")

        self.connect("value-changed", self._on_val)
        self._sig = stream.connect("changed", self._on_strm)

        t = getattr(stream, "type", "").lower()
        self.add_style_class("mic" if "microphone" in t or "input" in t else "vol")
        
        if self._muted_style:
            self.add_style_class("muted")

    def _on_val(self, _):
        if self._upd: return
        
        if s := self.stream:
            nv = self.value * 100.0
            if abs(s.volume - nv) > 0.5:
                s.volume = nv
                pct = int(nv + 0.5)
                if pct != self._last_vol:
                    self.set_tooltip_text(f"{pct}%")
                    self._last_vol = pct

    def _on_strm(self, s):
        self._upd = True
        v = s.volume
        self.value = v * 0.01
        
        pct = int(v + 0.5)
        if pct != self._last_vol:
            self.set_tooltip_text(f"{pct}%")
            self._last_vol = pct

        m = s.muted
        if m and not self._muted_style:
            self.add_style_class("muted")
            self._muted_style = True
        elif not m and self._muted_style:
            self.remove_style_class("muted")
            self._muted_style = False
            
        self._upd = False

    def cleanup(self):
        if self.stream and self._sig:
            try: self.stream.disconnect(self._sig)
            except Exception: pass
        self.stream = None


class MixerSection(Box):
    __slots__ = ('_tl', '_cb', '_sw')

    def __init__(self, title: str, **kwargs):
        self._tl = Label(name="mixer-section-title", label=title, h_expand=True, h_align="fill")
        self._cb = Box(name="mixer-content", orientation="v", spacing=8, h_expand=True, v_expand=False)
        self._sw = {}
        
        super().__init__(
            name="mixer-section",
            orientation="v",
            spacing=8,
            h_expand=True,
            v_expand=False,
            children=(self._tl, self._cb) 
        )

    def update_streams(self, streams):
        cb, ow, nw, cids = self._cb, self._sw, {}, set()

        for s in streams:
            sid = id(s)
            cids.add(sid)
            if sid in ow:
                nw[sid] = ow[sid]
            else:
                c, lbl, sl, sig = self._mk_widget(s)
                nw[sid] = (c, lbl, sl, sig)
                cb.add(c)

        for sid, (c, _, sl, sig) in ow.items():
            if sid not in cids:
                cb.remove(c)
                sl.cleanup()
                c.destroy()

        if len(ow) != len(nw) or ow.keys() != nw.keys():
            self._sw = nw
            cb.show_all()

    def _mk_widget(self, s):
        v = int(s.volume + 0.5)
        c = Box(orientation="v", spacing=4, h_expand=True, v_expand=False)
        lbl = Label(
            name="mixer-stream-label",
            label=f"[{v}%] {s.description}",
            h_expand=True,
            h_align="start",
            v_align="center",
            ellipsization="end",
            max_chars_width=_MAX_CH,
            height_request=_LBL_H,
        )
        sl = MixerSlider(s)
        
        last_v = [v]
        def _lbl_update(st, l=lbl, lv=last_v):
            new_v = int(st.volume + 0.5)
            if new_v != lv[0]:
                l.set_label(f"[{new_v}%] {st.description}")
                lv[0] = new_v

        sig = s.connect("changed", _lbl_update)
        c.add(lbl)
        c.add(sl)
        return c, lbl, sl, sig

    def cleanup(self):
        for c, _, sl, sig in self._sw.values():
            if sl.stream:
                try: sl.stream.disconnect(sig)
                except Exception: pass
            sl.cleanup()
            c.destroy()
        self._sw.clear()
        self._cb.children = ()


class Mixer(Box):
    __slots__ = ('audio', '_out', '_inp', '_sigs')

    def __init__(self, **kwargs):
        super().__init__(name="mixer", orientation="v", spacing=8, h_expand=True, v_expand=True)
        self._sigs = []
        try:
            self.audio = Audio()
        except Exception as e:
            self.add(Label(label=f"Audio service unavailable: {e}", h_align="center", v_align="center", h_expand=True, v_expand=True))
            self.audio = None
            return

        self._out = MixerSection("Outputs")
        osc = ScrolledWindow(
            name="outputs-scrolled", h_expand=True, v_expand=False,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hscrollbar_policy=Gtk.PolicyType.NEVER, child=self._out
        )
        osc.set_size_request(-1, _SEC_H)
        osc.set_max_content_height(_SEC_H)

        self._inp = MixerSection("Inputs")
        isc = ScrolledWindow(
            name="inputs-scrolled", h_expand=True, v_expand=False,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hscrollbar_policy=Gtk.PolicyType.NEVER, child=self._inp
        )
        isc.set_size_request(-1, _SEC_H)
        isc.set_max_content_height(_SEC_H)

        mc = Box(orientation="h", spacing=8, h_expand=True, v_expand=True, homogeneous=True, children=(osc, isc))
        
        self.add(mc)
        self.set_size_request(-1, _SEC_H << 1)

        a, u = self.audio, self._upd
        for sig in ("changed", "stream-added", "stream-removed"):
            self._sigs.append((a, a.connect(sig, u)))

        self._upd()
        self.show_all()

    def _upd(self, *_):
        if not (a := self.audio): return

        outs = [a.speaker] if a.speaker else []
        outs.extend(a.applications or ())

        ins = [a.microphone] if a.microphone else []
        ins.extend(a.recorders or ())

        self._out.update_streams(outs)
        self._inp.update_streams(ins)

    def cleanup(self):
        for obj, sig in self._sigs:
            try: obj.disconnect(sig)
            except Exception: pass
        self._sigs.clear()

        if out := getattr(self, '_out', None): out.cleanup()
        if inp := getattr(self, '_inp', None): inp.cleanup()

        self.audio = None