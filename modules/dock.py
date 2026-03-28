import json

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk
from types import SimpleNamespace

from fabric.hyprland.widgets import get_hyprland_connection
from fabric.utils import get_desktop_applications
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.eventbox import EventBox
from fabric.widgets.image import Image
from fabric.widgets.revealer import Revealer
from fabric.widgets.label import Label
from fabric.widgets.overlay import Overlay

from modules.Dock.visibility import Visibility
from modules.Dock.pin import Pin
from modules.Dock.windowNavigator import WindowNavigator
from modules.Dock.restore import AppResolver as RestoreAppResolver
from modules.Dock.DnD import Dnd
from modules.corners import MyCorner
from services.wayland import WaylandWindow as Window
from services.icons import pin, pinned


ICON_SCALE_FACTOR = 0.035
DEFAULT_ICON_SIZE = 24
ICON_RESIZE_TOLERANCE = 2
ICON_SPACING = 4
WIDGET_SPACING = 2
MAX_DOTS = 5
DOT_SIZE = 5
UPDATE_DEBOUNCE_MS = 80
HOVER_DEBOUNCE_MS = 40
INIT_DELAY_MS = 150
FALLBACK_ICON = "application-x-executable-symbolic"

_apps, _app_map, _theme = [], {}, Gtk.IconTheme.get_default()


def _norm(name):
    return name.lower().strip().rsplit(".", 1)[-1] if name else ""

def _refresh():
    global _apps
    _apps = get_desktop_applications()
    _app_map.clear()
    for app in _apps:
        for k in filter(None, (app.name, app.display_name)):
            _app_map.setdefault(k.lower(), app)
            _app_map.setdefault(_norm(k), app)

def _find(name):
    if not name:
        return None
    low, n = name.lower(), _norm(name)
    for k in (low, n):
        if k in _app_map:
            return _app_map[k]
    if "." in low:
        for seg in low.split("."):
            if seg in _app_map:
                return _app_map[seg]
    for app in _apps:
        an, ad = (app.name or "").lower(), (app.display_name or "").lower()
        if n in an or an in n or n in ad or ad in n:
            return app
    return None

def _icon(cls, size, app=None):
    for src in (app, _find(cls)):
        if src and hasattr(src, "get_icon_pixbuf"):
            try:
                px = src.get_icon_pixbuf(size=size)
                if px:
                    return px
            except Exception:
                pass
    for name in filter(None, (cls, _norm(cls), cls and cls.lower(), FALLBACK_ICON)):
        try:
            px = _theme.load_icon(name, size, Gtk.IconLookupFlags.FORCE_SIZE)
            if px:
                return px
        except Exception:
            pass
    return None

def _resolver():
    return SimpleNamespace(
        app_map=_app_map, refresh=_refresh,
        norm_name=_norm, find_app=_find, get_icon=_icon,
    )


class Dock(Window):
    __gtype_name__ = "Dock"

    def __init__(
        self,
        monitor_id=0,
        integrated_mode=False,
        session_manager=None,
        icon_scale=ICON_SCALE_FACTOR,
        default_icon_size=DEFAULT_ICON_SIZE,
        **kwargs,
    ):
        self.monitor_id = monitor_id
        self.integrated_mode = integrated_mode
        self._icon_scale = icon_scale
        self.icon_size = default_icon_size

        self._drag_active = False
        self._dock_width = self._dock_height = 0
        self._mon_x = self._mon_y = self._mon_w = self._mon_h = 0
        self._visibility = None
        self._update_timer = None
        self._last_fingerprint = None

        super().__init__(
            name="dock-window", layer="top", anchor="bottom",
            margin="0px 0px 0px 0px", exclusivity="none",
            monitor=monitor_id, visible=False, **kwargs,
        )

        self.conn = get_hyprland_connection()
        resolver = _resolver()
        self._pin_mgr = Pin(session_manager, resolver)
        self._nav = WindowNavigator(self.conn, self._parse)
        self._restorer = RestoreAppResolver(resolver)
        self._dnd = Dnd(self)

        self._init_ui()
        self._bind_events()

    def _parse(self, cmd):
        try:
            raw = self.conn.send_command(cmd).reply
            return json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        except Exception:
            return []

    def _init_ui(self):
        self.view = Box(name="viewport", spacing=0, orientation=Gtk.Orientation.HORIZONTAL)
        self.wrapper = Box(name="dock", children=[self.view], orientation=Gtk.Orientation.HORIZONTAL)

        if self.integrated_mode:
            self.add(self.wrapper)
            return

        self._visibility = Visibility(self)

        self.dock_eb = EventBox()
        self.dock_eb.add(self.wrapper)
        self.dock_eb.connect("enter-notify-event", self._on_dock_enter)
        self.dock_eb.connect("leave-notify-event", self._on_dock_leave)

        dock_full = Box(
            name="dock-full", orientation=Gtk.Orientation.HORIZONTAL,
            h_expand=True, h_align="fill",
            children=[
                Box(name="dock-corner-left", orientation=Gtk.Orientation.VERTICAL,
                    h_align="start", children=[Box(v_expand=True, v_align="fill"), MyCorner("bottom-right")]),
                self.dock_eb,
                Box(name="dock-corner-right", orientation=Gtk.Orientation.VERTICAL,
                    h_align="end", children=[Box(v_expand=True, v_align="fill"), MyCorner("bottom-left")]),
            ],
        )

        self.revealer = Revealer(
            name="dock-revealer", transition_type="slide-up",
            child_revealed=True, child=dock_full,
        )

        activator = EventBox()
        activator.set_size_request(-1, Visibility.ACTIVATOR_HEIGHT)
        activator.connect("enter-notify-event", self._on_hover_enter)
        activator.connect("leave-notify-event", self._on_hover_leave)

        self.add(Box(
            orientation=Gtk.Orientation.VERTICAL, h_align="center",
            children=[activator, self.revealer],
        ))
        self.wrapper.connect("size-allocate", self._on_size_allocate)

    def _on_size_allocate(self, _, alloc):
        if self._visibility:
            self._visibility.update_size(alloc.width, alloc.height)

    def _bind_events(self):
        c = self.conn
        for ev, handler in {
            "activewindow": self._on_active_window,
            "windowtitle": self._on_window_title,
            "monitoradded": self._update_monitor,
            "monitorremoved": self._update_monitor,
        }.items():
            c.connect(f"event::{ev}", handler)

        c.connect("event", self._schedule_update)

        if c.ready:
            self._on_ready()
        else:
            c.connect("event::ready", self._on_ready)

    def _on_ready(self, *_):
        self._pin_mgr.restore()
        self._update_monitor()
        self.show_all()
        if self._visibility:
            self._visibility.start()
        GLib.timeout_add(INIT_DELAY_MS, self._do_full_update)

    def _schedule_update(self, *_):
        if self._update_timer is not None:
            GLib.source_remove(self._update_timer)
        self._update_timer = GLib.timeout_add(UPDATE_DEBOUNCE_MS, self._do_full_update)

    def _on_active_window(self, *_):
        self._sync_active()

    def _on_window_title(self, *_):
        self._sync_tooltips()

    def _do_full_update(self):
        self._update_timer = None
        if self._drag_active:
            return False

        _refresh()
        clients = self._parse("j/clients")
        candidates = self._build_candidates(clients)

        fp = self._fingerprint(candidates)
        if fp != self._last_fingerprint:
            self._last_fingerprint = fp
            self._rebuild_ui(candidates)

        self._sync_active()
        self._sync_tooltips()

        if self._visibility:
            self._visibility.check_now(clients)
        return False

    @staticmethod
    def _fingerprint(candidates):
        return tuple(
            (c["unique_id"], tuple(sorted(i.get("address", "") for i in c.get("insts", []))))
            for c in candidates
        )

    def _build_candidates(self, clients):
        wins = {}
        for c in clients:
            cls = c.get("initialClass") or c.get("class") or ""
            title = c.get("title", "")
            if not cls and not title:
                continue
            raw = cls or title
            key = raw.lower().split(" - ", 1)[0].strip()
            if key not in wins:
                wins[key] = {"original": cls or title, "instances": []}
            wins[key]["instances"].append(c)

        seen, candidates = set(), []
        for key, data in wins.items():
            if key in seen:
                continue
            seen.add(key)
            original = data["original"]
            n = _norm(key)
            if n != key:
                seen.add(n)

            app = (
                _app_map.get(key) or _app_map.get(n)
                or _app_map.get(original.lower()) or _find(original)
            )
            uid = (app.name or getattr(app, "window_class", "") or key) if app else key
            candidates.append({
                "unique_id": uid, "app": app, "insts": data["instances"],
                "key": key, "original": original,
            })

        existing = {c["unique_id"] for c in candidates}
        candidates.extend(self._pin_mgr.get_ghost_candidates(existing))
        return self._dnd.apply_order(candidates)

    def _gdk_geometry(self):
        display = Gdk.Display.get_default()
        if display and 0 <= self.monitor_id < display.get_n_monitors():
            mon = display.get_monitor(self.monitor_id)
            if mon:
                return mon.get_geometry()
        return None

    def _update_monitor(self, *_):
        for m in self._parse("j/monitors"):
            if m.get("id") != self.monitor_id:
                continue

            w, h = m.get("width", self._mon_w), m.get("height", self._mon_h)
            x, y = m.get("x", self._mon_x), m.get("y", self._mon_y)

            if not all((w, h)):
                g = self._gdk_geometry()
                if g:
                    w, h = w or g.width, h or g.height
                    x, y = x or g.x, y or g.y

            self._mon_w, self._mon_h = w, h
            self._mon_x, self._mon_y = x, y

            if h > 0:
                new_size = int(h * self._icon_scale)
                if abs(self.icon_size - new_size) > ICON_RESIZE_TOLERANCE:
                    self.icon_size = new_size
                    self._last_fingerprint = None
                    self._schedule_update()
            return

    def _sync_active(self):
        aw = self._parse("j/activewindow")
        active = _norm(aw.get("initialClass") or aw.get("class", "")) if aw else ""
        for container in self.view.get_children():
            cls = getattr(container, "_cls", None)
            btn = getattr(container, "_main_btn", container)
            n = _norm(cls) if cls else ""
            match = n and active and (n == active or n in active or active in n)
            if match:
                btn.add_style_class("active")
            else:
                btn.remove_style_class("active")

    def _sync_tooltips(self):
        clients = self._parse("j/clients")
        if not clients:
            return

        best = {}
        for c in clients:
            cls = c.get("initialClass") or c.get("class") or ""
            title = c.get("title", "")
            if not cls and not title:
                continue
            key = (cls or title).lower().split(" - ", 1)[0].strip()
            fh = c.get("focusHistoryID", float("inf"))
            prev = best.get(key)
            if prev is None or fh < prev[1]:
                best[key] = (title, fh)

        titles = {k: v[0] for k, v in best.items()}
        for container in self.view.get_children():
            cls = getattr(container, "_cls", None)
            if not cls:
                continue
            btn = getattr(container, "_main_btn", container)
            app = getattr(container, "_app", None)
            app_name = (app.display_name or app.name) if app else None
            btn.set_tooltip_text(
                app_name or titles.get(cls) or getattr(container, "_original", "")
            )

    def _rebuild_ui(self, candidates):
        for c in self.view.get_children():
            self.view.remove(c)
            c.destroy()
        for item in candidates:
            self.view.add(self._make_btn(
                item["app"], item["insts"], item["key"],
                item["original"], item["unique_id"],
            ))
        self.view.show_all()

    def _make_btn(self, app, insts, key, original, uid):
        name = (app.display_name or app.name) if app else None
        px = _icon(original or key, self.icon_size, app)
        num = len(insts) if insts else 0
        is_pin = self._pin_mgr.is_pinned(uid)

        icon_box = Box(
            name="dock-icon-box", orientation="v",
            h_align="center", v_align="end",
            children=[Image(pixbuf=px, name="dock-icon-image")],
        )
        icon_wrapper = Box(
            name="dock-icon-wrapper", orientation="v",
            h_align="center", v_align="end", spacing=ICON_SPACING,
            children=[icon_box],
        )

        dots_box = Box(name="dock-dots", orientation="v", spacing=WIDGET_SPACING, v_align="center")
        for _ in range(min(num, MAX_DOTS)):
            dot = Box(name="dock-dot")
            dot.set_size_request(DOT_SIZE, DOT_SIZE)
            dots_box.add(dot)

        content = Box(
            name="dock-icon", orientation="h",
            h_align="center", v_align="center", spacing=WIDGET_SPACING,
        )
        content.add(icon_wrapper)

        if num > 0:
            content.add(Box(
                name="dock-dots-wrapper", orientation="v",
                v_align="center", children=[dots_box],
            ))
        elif is_pin:
            content.add(Box(
                name="dock-dots-wrapper", orientation="v",
                v_align="center", children=[Label(label="✕", name="dock-cross")],
            ))

        main_btn = Button(
            child=content,
            tooltip_text=name or (insts[0].get("title") if insts else None) or original,
            name="dock-app-button",
        )

        pin_lbl = Label(markup=pinned if is_pin else pin)
        pin_btn = Button(
            name="dock-app-pin-btn", child=pin_lbl,
            v_align="start", h_align="start",
            tooltip_text="Unpin" if is_pin else "Pin to Dock",
        )
        if is_pin:
            pin_btn.add_style_class("active")

        container = Overlay(child=main_btn, overlays=[pin_btn])
        container._cls = key
        container._original = original
        container._app = app
        container._insts = insts
        container._icon_box = icon_box
        container._unique_id = uid
        container._main_btn = main_btn
        container._hover_timer = None

        main_btn.connect("clicked", lambda *_: self._on_btn_click(container))
        main_btn.connect("enter-notify-event", lambda w, e: self._on_btn_hover_enter(container, e))
        main_btn.connect("leave-notify-event", lambda w, e: self._on_btn_hover_leave(container, e))
        pin_btn.connect("clicked", lambda *_: self._on_pin_toggle(container, pin_lbl, pin_btn))
        pin_btn.connect("enter-notify-event", lambda w, e: self._on_btn_hover_enter(container, e))
        pin_btn.connect("leave-notify-event", lambda w, e: self._on_btn_hover_leave(container, e))

        main_btn.add_style_class("instance" if insts else ("pinned-empty" if is_pin else ""))
        self._dnd.setup(container)
        return container

    def _on_btn_click(self, container):
        if self._drag_active:
            return
        if not container._insts:
            self._restorer.launch(
                container._app, key=container._cls,
                original_class=container._original,
            )
        else:
            self._nav.cycle_and_focus(container._insts)

    def _on_pin_toggle(self, container, pin_lbl, pin_btn):
        now = self._pin_mgr.toggle(
            container._unique_id, container._app,
            container._cls, container._original,
        )
        pin_lbl.set_markup(pinned if now else pin)
        pin_btn.set_tooltip_text("Unpin" if now else "Pin to Dock")
        if now:
            pin_btn.add_style_class("active")
        else:
            pin_btn.remove_style_class("active")
        if not container._insts:
            self._last_fingerprint = None
            self._schedule_update()

    def _on_btn_hover_enter(self, container, event):
        if self._visibility:
            self._visibility.mouse_enter()
        if self._drag_active:
            return False
        if event.window and event.detail != Gdk.NotifyType.INFERIOR:
            event.window.set_cursor(
                Gdk.Cursor.new_from_name(event.window.get_display(), "pointer")
            )
        self._cancel_hover(container)
        container._hover_timer = GLib.timeout_add(
            HOVER_DEBOUNCE_MS, self._apply_hover, container,
        )
        return False

    def _on_btn_hover_leave(self, container, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self._cancel_hover(container)
        for target in (container, getattr(container, "_main_btn", container)):
            target.remove_style_class("hovered")
        ib = getattr(container, "_icon_box", None)
        if ib:
            ib.remove_style_class("lifted")
        if event.window:
            event.window.set_cursor(None)
        return False

    def _apply_hover(self, container):
        container._hover_timer = None
        for target in (container, getattr(container, "_main_btn", container)):
            target.add_style_class("hovered")
        ib = getattr(container, "_icon_box", None)
        if ib:
            ib.add_style_class("lifted")
        return False

    def _cancel_hover(self, container):
        t = getattr(container, "_hover_timer", None)
        if t:
            GLib.source_remove(t)
            container._hover_timer = None

    def _on_hover_enter(self, *_):
        if self._visibility:
            self._visibility.mouse_enter()

    def _on_hover_leave(self, *_):
        if self._visibility:
            self._visibility.mouse_leave()

    def _on_dock_enter(self, *_):
        if self._visibility:
            self._visibility.mouse_enter()
        return True

    def _on_dock_leave(self, _, e):
        if self.integrated_mode or e.detail == Gdk.NotifyType.INFERIOR:
            return e.detail != Gdk.NotifyType.INFERIOR
        if self._visibility:
            self._visibility.mouse_leave()
        return True