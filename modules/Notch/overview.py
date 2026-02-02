import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk

from fabric.hyprland.service import Hyprland
from fabric.utils.helpers import get_desktop_applications
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.eventbox import EventBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label

import services.icons as icons
from services.icon_resolver import IconResolver

_scr = Gdk.Screen.get_default()
_CW, _CH = _scr.get_width(), _scr.get_height()
_SW, _SH = int(_CW * 0.1), int(_CH * 0.1)
del _scr

_BS = 0.1
_TG = [Gtk.TargetEntry.new("text/plain", Gtk.TargetFlags.SAME_APP, 0)]

_conn = Hyprland()
_icr = IconResolver()


class HyprlandWindowButton(Button):
    __slots__ = ("addr", "_px")

    def __init__(self, addr, aid, title, sz, da):
        self.addr = addr
        self._px = _icr.resolve_icon(aid, int(min(sz) * 0.5), da)

        super().__init__(
            name="overview-client-box",
            image=Image(pixbuf=self._px),
            tooltip_text=(da.display_name or da.name if da else None) or title or aid,
            size=sz,
            on_clicked=self._foc,
            on_button_press_event=self._press,
            on_drag_data_get=self._dget,
            on_drag_begin=self._dbegin,
        )
        self.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, _TG, Gdk.DragAction.COPY)

    def _foc(self, *_):
        _conn.send_command(f"/dispatch focuswindow address:{self.addr}")

    def _press(self, _, e):
        if e.button == 3:
            _conn.send_command(f"/dispatch closewindow address:{self.addr}")

    def _dget(self, _, __, d, *___):
        d.set_text(self.addr, len(self.addr))

    def _dbegin(self, _, ctx):
        Gtk.drag_set_icon_pixbuf(ctx, self._px, 0, 0) if self._px else Gtk.drag_set_icon_default(ctx)


class WorkspaceEventBox(EventBox):
    __slots__ = ()

    def __init__(self, wid, fixed):
        super().__init__(
            name="overview-workspace-bg",
            size=(_SW, _SH),
            child=fixed or Label(name="overview-add-label", markup=icons.circle_plus, h_expand=True, v_expand=True),
            on_drag_data_received=lambda *a: _conn.send_command(
                f"/dispatch movetoworkspacesilent {wid},address:{a[4].get_data().decode()}"
            ),
        )
        self.drag_dest_set(Gtk.DestDefaults.ALL, _TG, Gdk.DragAction.COPY)
        if fixed:
            fixed.show_all()


class Overview(Box):
    __slots__ = ("mid", "ws_s", "ws_e", "cli", "wsb", "_apps")

    def __init__(self, monitor_id=0, **kwargs):
        super().__init__(name="overview", orientation="v", spacing=8, **kwargs)

        self.mid = monitor_id
        self.ws_s = 1
        self.ws_e = 9
        self.cli = {}
        self.wsb = {}
        self._apps = None

        for ev in ("openwindow", "closewindow", "movewindow"):
            _conn.connect(f"event::{ev}", self.update)

        self.update()

    def _find(self, aid):
        if not aid:
            return None
        al = aid.lower()
        for a in self._apps:
            if (al == (a.window_class or "").lower() or 
                al == (a.name or "").lower() or 
                al == (a.display_name or "").lower() or 
                al == (a.executable or "").rsplit("/", 1)[-1].lower()):
                return a
        return None

    def update(self, *_):
        for b in self.cli.values():
            b.destroy()
        self.cli.clear()

        for b in self.wsb.values():
            b.destroy()
        self.wsb.clear()

        self.children = []
        self._apps = get_desktop_applications()

        try:
            md = _conn.get_monitors()
        except:
            import json
            md = json.loads(_conn.send_command("j/monitors").reply)

        try:
            cd = _conn.get_clients()
        except:
            import json
            cd = json.loads(_conn.send_command("j/clients").reply)

        mons = {m["id"]: (m["x"], m["y"]) for m in md}
        rows = [Box(spacing=8) for _ in range(3)]
        self.children = rows

        ws_s, ws_e = self.ws_s, self.ws_e

        for c in cd:
            wid = c["workspace"]["id"]
            if not ws_s <= wid <= ws_e:
                continue

            mx, my = mons[c["monitor"]]
            addr = c["address"]
            aid = c["initialClass"]
            sz = (int(c["size"][0] * _BS), int(c["size"][1] * _BS))

            btn = HyprlandWindowButton(addr, aid, c["title"], sz, self._find(aid))
            self.cli[addr] = btn

            if wid not in self.wsb:
                self.wsb[wid] = Gtk.Fixed()

            self.wsb[wid].put(btn, int(abs(c["at"][0] - mx) * _BS), int(abs(c["at"][1] - my) * _BS))

        for wid in range(ws_s, ws_e + 1):
            rows[(wid - ws_s) // 3].add(Box(
                name="overview-workspace-box",
                orientation="vertical",
                children=[
                    Label(name="overview-workspace-label", label=f"Workspace {wid}"),
                    WorkspaceEventBox(wid, self.wsb.get(wid)),
                ],
            ))

    def cleanup(self):
        for b in self.cli.values():
            b.destroy()
        self.cli.clear()

        for b in self.wsb.values():
            b.destroy()
        self.wsb.clear()

        self._apps = None
        self.children = []