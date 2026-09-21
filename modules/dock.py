import json
import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from fabric.hyprland.widgets import get_hyprland_connection
from fabric.utils import get_desktop_applications
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.eventbox import EventBox
from fabric.widgets.image import Image
from fabric.widgets.revealer import Revealer
from fabric.widgets.label import Label

from modules.Dock.visibility import Visibility
from modules.Dock.windowNavigator import WindowNavigator
from modules.Dock.DnD import Dnd

from modules.Dock.Desktop.infinite_desktop import (
    start_canvas_daemon,
    toggle_mode as toggle_float_mode,
    is_canvas_mode,
    register_mode_change_callback as register_float_mode_change_callback,
)

from modules.corners import MyCorner

from services.wayland import WaylandWindow as Window
from services.icons import layout_tiling, layout_float


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

_apps, _app_map, _theme = [], {}, Gtk.IconTheme.get_default()

LAYOUT_CANVAS   = 0
LAYOUT_HYPRLAND = 1


def _norm(name):
    return name.lower().strip().rsplit(".", 1)[-1]


def _refresh():
    global _apps
    _apps = get_desktop_applications()
    _app_map.clear()
    for app in _apps:
        for k in (app.name, app.display_name):
            _app_map.setdefault(k.lower(), app)
            _app_map.setdefault(_norm(k), app)


def _find(name):
    low, n = name.lower(), _norm(name)
    for k in (low, n):
        if k in _app_map:
            return _app_map[k]
    if "." in low:
        for seg in low.split("."):
            if seg in _app_map:
                return _app_map[seg]
    for app in _apps:
        an, ad = app.name.lower(), app.display_name.lower()
        if n in an or an in n or n in ad or ad in n:
            return app
    return None


def _icon(cls, size, app=None):
    for src in (app, _find(cls)):
        if src:
            px = src.get_icon_pixbuf(size=size)
            if px:
                return px
    for name in (cls, _norm(cls), cls.lower()):
        px = _theme.load_icon(name, size, Gtk.IconLookupFlags.FORCE_SIZE)
        if px:
            return px
    return None


class Dock(Window):
    __gtype_name__ = "Dock"

    def __init__(
        self,
        monitor_id=0,
        integrated_mode=False,
        icon_scale=ICON_SCALE_FACTOR,
        default_icon_size=DEFAULT_ICON_SIZE,
        **kwargs,
    ):
        self.monitor_id = monitor_id
        self.integrated_mode = integrated_mode
        self._icon_scale = icon_scale
        self.icon_size = default_icon_size

        self._drag_active = False
        self._mon_x = self._mon_y = self._mon_w = self._mon_h = 0
        self._visibility = None
        self._update_timer = None
        self._last_fingerprint = None

        self._layouts = ["Canvas", "Hyprland"]
        self._layout_icons = [layout_float, layout_tiling]
        self._current_layout_idx = LAYOUT_CANVAS

        self._daemons_started = False

        super().__init__(
            name="dock-window", layer="top", anchor="bottom",
            margin="0px 0px 0px 0px", exclusivity="none",
            monitor=monitor_id, visible=False, **kwargs,
        )

        self.conn = get_hyprland_connection()
        self._nav = WindowNavigator(self.conn, self._parse)
        self._dnd = Dnd(self)

        self._init_ui()
        self._bind_events()

    def _parse(self, cmd):
        raw = self.conn.send_command(cmd).reply
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)

    def _init_ui(self):
        self.view = Box(
            name="viewport",
            spacing=0,
            orientation=Gtk.Orientation.HORIZONTAL,
        )

        self._layout_btn = self._make_layout_btn()

        self.wrapper = Box(
            name="dock",
            orientation=Gtk.Orientation.HORIZONTAL,
            children=[
                self.view,
                Box(
                    name="dock-separator",
                    orientation=Gtk.Orientation.VERTICAL,
                ),
                self._layout_btn,
            ],
        )

        if self.integrated_mode:
            self.add(self.wrapper)
            return

        self._visibility = Visibility(self)

        self.dock_eb = EventBox()
        self.dock_eb.add(self.wrapper)
        self.dock_eb.connect("enter-notify-event", self._on_dock_enter)
        self.dock_eb.connect("leave-notify-event", self._on_dock_leave)

        dock_full = Box(
            name="dock-full",
            orientation=Gtk.Orientation.HORIZONTAL,
            h_expand=True,
            h_align="fill",
            children=[
                Box(
                    name="dock-corner-left",
                    orientation=Gtk.Orientation.VERTICAL,
                    h_align="start",
                    children=[
                        Box(v_expand=True, v_align="fill"),
                        MyCorner("bottom-right"),
                    ],
                ),
                self.dock_eb,
                Box(
                    name="dock-corner-right",
                    orientation=Gtk.Orientation.VERTICAL,
                    h_align="end",
                    children=[
                        Box(v_expand=True, v_align="fill"),
                        MyCorner("bottom-left"),
                    ],
                ),
            ],
        )

        self.revealer = Revealer(
            name="dock-revealer",
            transition_type="slide-up",
            child_revealed=True,
            child=dock_full,
        )

        activator = EventBox()
        activator.set_size_request(-1, Visibility.ACTIVATOR_HEIGHT)
        activator.connect("enter-notify-event", self._on_hover_enter)
        activator.connect("leave-notify-event", self._on_hover_leave)

        self.add(Box(
            orientation=Gtk.Orientation.VERTICAL,
            h_align="center",
            children=[activator, self.revealer],
        ))
        self.wrapper.connect("size-allocate", self._on_size_allocate)

    def _on_size_allocate(self, _, alloc):
        if self._visibility:
            self._visibility.update_size(alloc.width, alloc.height)

    def _make_layout_btn(self):
        self._layout_icon_lbl = Label(
            markup=self._layout_icons[self._current_layout_idx],
            name="dock-layout-icon",
        )

        content = Box(
            name="dock-layout-content",
            orientation="h",
            h_align="center",
            v_align="center",
            children=[self._layout_icon_lbl],
        )

        btn = Button(
            child=content,
            name="dock-app-button",
            tooltip_text=f"Layout: {self._layouts[self._current_layout_idx]}",
        )
        btn.add_style_class("layout-btn")
        btn._hover_timer = None

        btn.connect("clicked", self._on_layout_click)
        btn.connect(
            "enter-notify-event",
            lambda w, e: self._on_layout_hover_enter(btn, e),
        )
        btn.connect(
            "leave-notify-event",
            lambda w, e: self._on_layout_hover_leave(btn, e),
        )

        return btn

    def _determine_layout_state(self) -> int:
        if is_canvas_mode():
            return LAYOUT_CANVAS
        return LAYOUT_HYPRLAND

    def _cycle_layout(self):
        self._ensure_daemons()
        threading.Thread(
            target=self._do_cycle_layout,
            name="layout-toggle",
            daemon=True,
        ).start()

    def _do_cycle_layout(self):
        toggle_float_mode()
        GLib.idle_add(self._sync_layout_btn)

    def _ensure_daemons(self):
        if self._daemons_started:
            return
        self._daemons_started = True
        threading.Thread(
            target=start_canvas_daemon, name="canvas-daemon-init", daemon=True,
        ).start()

    def _on_layout_click(self, *_):
        self._cycle_layout()

    def _on_layout_hover_enter(self, btn, event):
        if self._visibility:
            self._visibility.mouse_enter()
        t = btn._hover_timer
        if t:
            GLib.source_remove(t)
        btn._hover_timer = GLib.timeout_add(
            HOVER_DEBOUNCE_MS, self._apply_layout_hover, btn
        )
        return False

    def _on_layout_hover_leave(self, btn, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        t = btn._hover_timer
        if t:
            GLib.source_remove(t)
            btn._hover_timer = None
        btn.remove_style_class("hovered")
        return False

    def _apply_layout_hover(self, btn):
        btn._hover_timer = None
        btn.add_style_class("hovered")
        return False

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
        self._update_monitor()

        register_float_mode_change_callback(self._on_canvas_mode_changed)

        self.show_all()
        if self._visibility:
            self._visibility.start()

        GLib.timeout_add(INIT_DELAY_MS, self._do_full_update)
        GLib.timeout_add(INIT_DELAY_MS + 100, self._sync_layout_btn)

        self._ensure_daemons()

    def _sync_layout_btn(self):
        idx = self._determine_layout_state()
        if idx != self._current_layout_idx:
            self._current_layout_idx = idx
            self._layout_icon_lbl.set_markup(self._layout_icons[idx])
            self._layout_btn.set_tooltip_text(f"Layout: {self._layouts[idx]}")

        if idx == LAYOUT_HYPRLAND:
            self._layout_btn.add_style_class("active")
            self._layout_btn.add_style_class("active-hyprland")
        else:
            self._layout_btn.add_style_class("active")
            self._layout_btn.remove_style_class("active-hyprland")
        return False

    def _on_canvas_mode_changed(self, ws_id, is_canvas):
        GLib.idle_add(self._sync_layout_btn)

    def _schedule_update(self, *_):
        if self._update_timer is not None:
            GLib.source_remove(self._update_timer)
        self._update_timer = GLib.timeout_add(
            UPDATE_DEBOUNCE_MS, self._do_full_update
        )

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
            (
                c["unique_id"],
                tuple(sorted(i["address"] for i in c["insts"])),
            )
            for c in candidates
        )

    def _build_candidates(self, clients):
        wins = {}
        for c in clients:
            cls = c["initialClass"]
            key = cls.lower().split(" - ", 1)[0].strip()
            if key not in wins:
                wins[key] = {"original": cls, "instances": []}
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
                _app_map.get(key)
                or _app_map.get(n)
                or _app_map.get(original.lower())
                or _find(original)
            )
            uid = app.name if app else key

            candidates.append({
                "unique_id": uid,
                "app": app,
                "insts": data["instances"],
                "key": key,
                "original": original,
            })

        return self._dnd.apply_order(candidates)

    def _gdk_geometry(self):
        display = Gdk.Display.get_default()
        mon = display.get_monitor(self.monitor_id)
        return mon.get_geometry()

    def _update_monitor(self, *_):
        for m in self._parse("j/monitors"):
            if m["id"] != self.monitor_id:
                continue

            w = m["width"]
            h = m["height"]
            x = m["x"]
            y = m["y"]

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
        active = _norm(aw["initialClass"])
        for btn in self.view.get_children():
            cls = btn._cls
            n = _norm(cls)
            match = n == active or n in active or active in n
            if match:
                btn.add_style_class("active")
            else:
                btn.remove_style_class("active")

    def _sync_tooltips(self):
        for btn in self.view.get_children():
            app = btn._app
            btn.set_tooltip_text(app.display_name if app else btn._original)

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
        name = app.display_name if app else original
        px = _icon(original, self.icon_size, app)
        num = len(insts)

        icon_box = Box(
            name="dock-icon-box",
            orientation="v",
            h_align="center",
            v_align="end",
            children=[Image(pixbuf=px, name="dock-icon-image")],
        )
        icon_wrapper = Box(
            name="dock-icon-wrapper",
            orientation="v",
            h_align="center",
            v_align="end",
            spacing=ICON_SPACING,
            children=[icon_box],
        )

        dots_box = Box(
            name="dock-dots",
            orientation="v",
            spacing=WIDGET_SPACING,
            v_align="center",
        )
        for _ in range(min(num, MAX_DOTS)):
            dot = Box(name="dock-dot")
            dot.set_size_request(DOT_SIZE, DOT_SIZE)
            dots_box.add(dot)

        content = Box(
            name="dock-icon",
            orientation="h",
            h_align="center",
            v_align="center",
            spacing=WIDGET_SPACING,
        )
        content.add(icon_wrapper)

        if num > 0:
            content.add(Box(
                name="dock-dots-wrapper",
                orientation="v",
                v_align="center",
                children=[dots_box],
            ))

        main_btn = Button(
            child=content,
            tooltip_text=name,
            name="dock-app-button",
        )

        main_btn._cls = key
        main_btn._original = original
        main_btn._app = app
        main_btn._insts = insts
        main_btn._icon_box = icon_box
        main_btn._unique_id = uid
        main_btn._main_btn = main_btn
        main_btn._hover_timer = None

        main_btn.connect("clicked", lambda *_: self._on_btn_click(main_btn))
        main_btn.connect(
            "enter-notify-event",
            lambda w, e: self._on_btn_hover_enter(main_btn, e),
        )
        main_btn.connect(
            "leave-notify-event",
            lambda w, e: self._on_btn_hover_leave(main_btn, e),
        )

        if insts:
            main_btn.add_style_class("instance")

        self._dnd.setup(main_btn)
        return main_btn

    def _on_btn_click(self, btn):
        if self._drag_active or not btn._insts:
            return
        self._nav.cycle_and_focus(btn._insts)

    def _on_btn_hover_enter(self, btn, event):
        if self._visibility:
            self._visibility.mouse_enter()
        if self._drag_active:
            return False
        self._cancel_hover(btn)
        btn._hover_timer = GLib.timeout_add(
            HOVER_DEBOUNCE_MS, self._apply_hover, btn,
        )
        return False

    def _on_btn_hover_leave(self, btn, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self._cancel_hover(btn)
        btn.remove_style_class("hovered")
        return False

    def _apply_hover(self, btn):
        btn._hover_timer = None
        btn.add_style_class("hovered")
        return False

    def _cancel_hover(self, btn):
        t = btn._hover_timer
        if t:
            GLib.source_remove(t)
            btn._hover_timer = None

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