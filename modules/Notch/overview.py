import json
import random

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk, GLib

from fabric.hyprland.service import Hyprland
from fabric.utils import get_desktop_applications
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.eventbox import EventBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label

import services.icons as icons


SCALE = 0.1
COLS, ROWS = 3, 3
WS_RANGE = range(1, COLS * ROWS + 1)
SPACING = 8
ANIM_MS = 200
ICON_SCALE = 0.5
MIN_ICON = 16
FALLBACK_ICON = "application-x-executable-symbolic"
ANIM_V = "workspaces,1,6,overshot,slidevert"
ANIM_H = "workspaces,1,6,overshot,slide"
DND = [Gtk.TargetEntry.new("text/plain", Gtk.TargetFlags.SAME_APP, 0)]

_hypr = Hyprland()
_theme = Gtk.IconTheme.get_default()
_apps, _map = [], {}


def _norm(n):
    if not n:
        return ""
    return n.lower().strip().rsplit(".", 1)[-1]

def _refresh():
    global _apps
    _apps = get_desktop_applications()
    _map.clear()
    for a in _apps:
        for k in filter(None, (a.name, a.display_name)):
            _map.setdefault(k.lower(), a)
            _map.setdefault(_norm(k), a)

def _find(name):
    if not name:
        return None
    low, n = name.lower(), _norm(name)
    for key in (low, n):
        if key in _map:
            return _map[key]
    if "." in low:
        for seg in low.split("."):
            if seg in _map:
                return _map[seg]
    for a in _apps:
        an, ad = (a.name or "").lower(), (a.display_name or "").lower()
        if n in an or an in n or n in ad or ad in n:
            return a
    return None

def _icon(cls, size):
    app = _find(cls)
    if app:
        try:
            px = app.get_icon_pixbuf(size=size)
            if px:
                return px
        except Exception:
            pass
    for n in filter(None, (cls, _norm(cls), cls and cls.lower(), FALLBACK_ICON)):
        try:
            px = _theme.load_icon(n, size, Gtk.IconLookupFlags.FORCE_SIZE)
            if px:
                return px
        except Exception:
            pass
    return None

def _qj(cmd):
    try:
        return getattr(_hypr, f"get_{cmd}")()
    except Exception:
        res = _hypr.send_command(f"j/{cmd}")
        return json.loads(res.reply if hasattr(res, "reply") else res)

def _active_ws():
    try:
        ws = _qj("activeworkspace")["id"]
        return ws if ws in WS_RANGE else WS_RANGE.start
    except Exception:
        return WS_RANGE.start

def _switch(target, cb=None):
    cur = _active_ws()
    if cur == target:
        return cb and cb()

    cr, cc = divmod(cur - WS_RANGE.start, COLS)
    tr, tc = divmod(target - WS_RANGE.start, COLS)
    r, c, steps = cr, cc, []

    def go(is_row, tgt):
        nonlocal r, c
        while (r if is_row else c) != tgt:
            if is_row:
                r += 1 if tgt > r else -1
            else:
                c += 1 if tgt > c else -1
            steps.append((r * COLS + c + WS_RANGE.start, is_row))

    axes = [(True, tr), (False, tc)]
    if random.choice((True, False)):
        axes.reverse()
    for a in axes:
        go(*a)

    def tick():
        if not steps:
            return
        ws, vert = steps.pop(0)
        _hypr.send_command(
            f"[[BATCH]] keyword animation {ANIM_V} ; "
            f"dispatch workspace {ws} ; keyword animation {ANIM_H}"
            if vert
            else f"dispatch workspace {ws}",
        )
        GLib.timeout_add(ANIM_MS, tick) if steps else (cb and cb())

    tick()


class WindowButton(Button):
    def __init__(self, addr, cls, title, size, ws_id):
        px = _icon(cls, max(MIN_ICON, int(min(size) * ICON_SCALE)))
        app = _find(cls)
        tip = ((app.display_name or app.name) if app else None) or title or cls

        super().__init__(
            name="overview-client-box",
            image=Image(pixbuf=px) if px else None,
            tooltip_text=tip,
            size=size,
            on_clicked=lambda *_: _switch(
                ws_id,
                lambda: _hypr.send_command(
                    f"/dispatch focuswindow address:{addr}"
                ),
            ),
            on_button_press_event=lambda _, e: (
                _hypr.send_command(f"/dispatch closewindow address:{addr}")
                if e.button == 3
                else None
            ),
            on_drag_data_get=lambda _, __, d, *___: d.set_text(addr, len(addr)),
            on_drag_begin=lambda _, ctx: (
                Gtk.drag_set_icon_pixbuf(ctx, px, 0, 0)
                if px
                else Gtk.drag_set_icon_default(ctx)
            ),
        )
        self.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, DND, Gdk.DragAction.COPY)


class WsBox(EventBox):
    def __init__(self, ws_id, fixed, sw, sh):
        super().__init__(
            name="overview-workspace-bg",
            size=(sw, sh),
            child=fixed
            or Label(
                name="overview-add-label",
                markup=icons.circle_plus,
                h_expand=True,
                v_expand=True,
            ),
            on_drag_data_received=lambda w, ctx, x, y, d, *_: _hypr.send_command(
                f"/dispatch movetoworkspacesilent "
                f"{ws_id},address:{d.get_data().decode()}",
            ),
            on_button_press_event=lambda _, e: (
                _switch(ws_id) if e.button == 1 else None
            ),
        )
        self.drag_dest_set(Gtk.DestDefaults.ALL, DND, Gdk.DragAction.COPY)
        if fixed:
            fixed.show_all()


class Overview(Box):
    def __init__(self, monitor_id=0, **kw):
        super().__init__(name="overview", orientation="v", spacing=SPACING, **kw)
        self.mid = monitor_id
        self._w, self._f = {}, {}
        _hypr.connect("event", self._rebuild)
        self._rebuild()

    def _screen(self, mons):
        for m in mons:
            if m.get("id") == self.mid:
                return m["width"], m["height"]
        if mons:
            return mons[0]["width"], mons[0]["height"]
        g = Gdk.Display.get_default().get_monitor(0).get_geometry()
        return g.width, g.height

    def _rebuild(self, *_):
        self.cleanup()
        _refresh()
        mons, clients = _qj("monitors"), _qj("clients")
        rects = {m["id"]: (m["x"], m["y"], m["width"], m["height"]) for m in mons}
        sw, sh = self._screen(mons)
        tw, th = int(sw * SCALE), int(sh * SCALE)
        rows = [Box(spacing=SPACING) for _ in range(ROWS)]
        self.children = rows

        for c in clients:
            wid = c["workspace"]["id"]
            if wid not in WS_RANGE:
                continue
            mx, my, mw, mh = rects.get(c["monitor"], (0, 0, sw, sh))
            cx, cy = c["at"]
            cw, ch = c["size"]
            if (
                not c.get("mapped", True)
                or c.get("hidden", False)
                or cx >= mx + mw
                or cx + cw <= mx
                or cy >= my + mh
                or cy + ch <= my
            ):
                continue
            addr = c["address"]
            btn = WindowButton(
                addr, c["initialClass"], c["title"],
                (int(cw * SCALE), int(ch * SCALE)), wid,
            )
            self._w[addr] = btn
            self._f.setdefault(wid, Gtk.Fixed())
            self._f[wid].put(btn, int((cx - mx) * SCALE), int((cy - my) * SCALE))

        for wid in WS_RANGE:
            rows[(wid - WS_RANGE.start) // COLS].add(
                Box(
                    name="overview-workspace-box",
                    orientation="vertical",
                    children=[
                        Label(name="overview-workspace-label", label=f"Workspace {wid}"),
                        WsBox(wid, self._f.get(wid), tw, th),
                    ],
                ),
            )

    def cleanup(self):
        for w in (*self._w.values(), *self._f.values()):
            w.destroy()
        self._w.clear()
        self._f.clear()
        self.children = []