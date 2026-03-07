import json
import random

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk, GLib

from fabric.hyprland.service import Hyprland
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.eventbox import EventBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label

import services.icons as icons
from services.icon_resolver import IconResolver


def get_screen_dims():
    scr = Gdk.Screen.get_default()
    if scr is not None:
        return scr.get_width(), scr.get_height()
    return 1920, 1080

_CW, _CH = get_screen_dims()
_SW, _SH = int(_CW * 0.1), int(_CH * 0.1)

_BS = 0.1
_TG = [Gtk.TargetEntry.new("text/plain", Gtk.TargetFlags.SAME_APP, 0)]

_conn = Hyprland()
_app_resolver = IconResolver.get_default()


def _get_monitors():
    try:
        return _conn.get_monitors()
    except Exception:
        return json.loads(_conn.send_command("j/monitors").reply)


def _get_clients():
    try:
        return _conn.get_clients()
    except Exception:
        return json.loads(_conn.send_command("j/clients").reply)


def _switch_workspace(target_ws, on_complete=None):
    try:
        res = _conn.send_command("j/activeworkspace")
        active_ws = json.loads(res.reply)["id"] if hasattr(res, "reply") else json.loads(res)["id"]
    except Exception:
        active_ws = 1

    if not (1 <= active_ws <= 9):
        active_ws = 5

    def finish_action():
        if on_complete:
            on_complete()

    if active_ws == target_ws:
        finish_action()
        return

    cur_row = (active_ws - 1) // 3
    cur_col = (active_ws - 1) % 3

    target_row = (target_ws - 1) // 3
    target_col = (target_ws - 1) % 3

    ANIM_TIME_MS = 200

    def move_vert(ws):
        cmd = f"[[BATCH]] keyword animation workspaces,1,6,overshot,slidevert ; dispatch workspace {ws} ; keyword animation workspaces,1,6,overshot,slide"
        _conn.send_command(cmd)

    steps = []
    r, c = cur_row, cur_col
    
    horiz_first = random.choice([True, False])

    if horiz_first:
        step_c = 1 if target_col > c else -1
        while c != target_col:
            c += step_c
            steps.append((r * 3 + c + 1, False))
            
        step_r = 1 if target_row > r else -1
        while r != target_row:
            r += step_r
            steps.append((r * 3 + c + 1, True))
    else:
        step_r = 1 if target_row > r else -1
        while r != target_row:
            r += step_r
            steps.append((r * 3 + c + 1, True))
            
        step_c = 1 if target_col > c else -1
        while c != target_col:
            c += step_c
            steps.append((r * 3 + c + 1, False))

    def run_next_step():
        if not steps:
            return False
            
        next_ws, is_vert = steps.pop(0)
        
        if is_vert:
            move_vert(next_ws)
        else:
            _conn.send_command(f"dispatch workspace {next_ws}")
            
        if steps:
            GLib.timeout_add(ANIM_TIME_MS, run_next_step)
        else:
            finish_action()
            
        return False

    run_next_step()


class HyprlandWindowButton(Button):
    __slots__ = ("addr", "_px", "wid")

    def __init__(self, addr, aid, title, sz, wid):
        app = _app_resolver.find_app(aid)
        self._px = _app_resolver.get_icon(aid, int(min(sz) * 0.5), app)
        self.addr = addr
        self.wid = wid

        super().__init__(
            name="overview-client-box",
            image=Image(pixbuf=self._px),
            tooltip_text=(app.display_name or app.name if app else None) or title or aid,
            size=sz,
            on_clicked=self._foc,
            on_button_press_event=self._press,
            on_drag_data_get=self._dget,
            on_drag_begin=self._dbegin,
        )
        self.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, _TG, Gdk.DragAction.COPY)

    def _foc(self, *_):
        def focus_window():
            _conn.send_command(f"/dispatch focuswindow address:{self.addr}")
        
        _switch_workspace(self.wid, on_complete=focus_window)

    def _press(self, _, e):
        if e.button == 3:
            _conn.send_command(f"/dispatch closewindow address:{self.addr}")

    def _dget(self, _, __, d, *___):
        d.set_text(self.addr, len(self.addr))

    def _dbegin(self, _, ctx):
        Gtk.drag_set_icon_pixbuf(ctx, self._px, 0, 0) if self._px else Gtk.drag_set_icon_default(ctx)


class WorkspaceEventBox(EventBox):
    __slots__ = ("wid",)

    def __init__(self, wid, fixed):
        self.wid = wid
        super().__init__(
            name="overview-workspace-bg",
            size=(_SW, _SH),
            child=fixed or Label(
                name="overview-add-label", 
                markup=icons.circle_plus, 
                h_expand=True, 
                v_expand=True
            ),
            on_drag_data_received=self._on_drop,
            on_button_press_event=self._on_click,
        )
        self.drag_dest_set(Gtk.DestDefaults.ALL, _TG, Gdk.DragAction.COPY)
        if fixed:
            fixed.show_all()

    def _on_drop(self, _, __, ___, ____, data, _____):
        _conn.send_command(f"/dispatch movetoworkspacesilent {self.wid},address:{data.get_data().decode()}")

    def _on_click(self, _, event):
        if event.button == 1:
            _switch_workspace(self.wid)


class Overview(Box):
    __slots__ = ("mid", "ws_s", "ws_e", "cli", "wsb")

    def __init__(self, monitor_id=0, **kwargs):
        super().__init__(name="overview", orientation="v", spacing=8, **kwargs)

        self.mid = monitor_id
        self.ws_s = 1
        self.ws_e = 9
        self.cli = {}
        self.wsb = {}

        events = (
            "openwindow", 
            "closewindow", 
            "movewindow", 
            "activewindow", 
            "workspace",
            "fullscreen",
            "changefloatingmode"
        )
        for ev in events:
            _conn.connect(f"event::{ev}", self.update)

        self.update()

    def update(self, *_):
        for b in self.cli.values():
            b.destroy()
        self.cli.clear()

        for b in self.wsb.values():
            b.destroy()
        self.wsb.clear()

        self.children = []
        
        _app_resolver.refresh()

        md = _get_monitors()
        cd = _get_clients()

        mons = {m["id"]: (m["x"], m["y"], m["width"], m["height"]) for m in md}
        rows = [Box(spacing=8) for _ in range(3)]
        self.children = rows

        ws_s, ws_e = self.ws_s, self.ws_e

        for c in cd:
            wid = c["workspace"]["id"]
            if not ws_s <= wid <= ws_e:
                continue

            mx, my, mw, mh = mons.get(c["monitor"], (0, 0, 1920, 1080))
            
            cx, cy = c["at"]
            cw, ch = c["size"]

            is_mapped = c.get("mapped", True)
            is_hidden = c.get("hidden", False)
            
            is_on_screen = (
                cx < (mx + mw) and
                (cx + cw) > mx and
                cy < (my + mh) and
                (cy + ch) > my
            )

            if not is_mapped or is_hidden or not is_on_screen:
                continue

            addr = c["address"]
            aid = c["initialClass"]
            sz = (int(cw * _BS), int(ch * _BS))

            btn = HyprlandWindowButton(addr, aid, c["title"], sz, wid)
            self.cli[addr] = btn

            if wid not in self.wsb:
                self.wsb[wid] = Gtk.Fixed()

            self.wsb[wid].put(btn, int((cx - mx) * _BS), int((cy - my) * _BS))

        for wid in range(ws_s, ws_e + 1):
            ws_box = Box(
                name="overview-workspace-box",
                orientation="vertical",
                children=[
                    Label(name="overview-workspace-label", label=f"Workspace {wid}"),
                    WorkspaceEventBox(wid, self.wsb.get(wid)),
                ],
            )
            rows[(wid - ws_s) // 3].add(ws_box)

    def cleanup(self):
        for b in self.cli.values():
            b.destroy()
        self.cli.clear()

        for b in self.wsb.values():
            b.destroy()
        self.wsb.clear()

        self.children = []