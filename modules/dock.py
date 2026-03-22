import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from fabric.hyprland.widgets import get_hyprland_connection
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.eventbox import EventBox
from fabric.widgets.image import Image
from fabric.widgets.revealer import Revealer
from fabric.widgets.label import Label
from fabric.widgets.overlay import Overlay

from modules.corners import MyCorner
from services.iconResolver import IconResolver
from services.wayland import WaylandWindow as Window
from services.icons import pin, pinned

from modules.Dock.visibility import Visibility
from modules.Dock.pin import Pin
from modules.Dock.windowNavigator import WindowNavigator
from modules.Dock.restore import AppResolver
from modules.Dock.DnD import Dnd


class Dock(Window):
    __gtype_name__ = "Dock"

    _MAX_DOTS = 5
    _UPDATE_DEBOUNCE_MS = 80
    _HOVER_DEBOUNCE_MS = 40

    def __init__(
        self,
        monitor_id=0,
        integrated_mode=False,
        session_manager=None,
        **kwargs,
    ):
        self.monitor_id = monitor_id
        self.integrated_mode = integrated_mode

        self._icon_scale = 0.035
        self._drag_active = False
        self._dock_width = self._dock_height = 0
        self._mon_x = self._mon_y = self._mon_w = self._mon_h = 0
        self.icon_size = 24

        self._icon_pin = pin
        self._icon_pinned = pinned
        self._visibility = None
        self._update_timer = None
        self._last_fingerprint = None

        super().__init__(
            name="dock-window",
            layer="top",
            anchor="bottom",
            margin="0px 0px 0px 0px",
            exclusivity="none",
            monitor=monitor_id,
            visible=False,
            **kwargs,
        )

        self.conn = get_hyprland_connection()
        self._app_resolver = IconResolver.get_default()

        self._pin_mgr = Pin(session_manager, self._app_resolver)
        self._nav = WindowNavigator(self.conn, self._parse)
        self._restorer = AppResolver(self._app_resolver)
        self._dnd = Dnd(self)

        self._init_ui()
        self._bind_events()

    def _parse(self, cmd):
        try:
            s = self.conn.send_command(cmd).reply.decode()
            return eval(
                s.replace("true", "True")
                .replace("false", "False")
                .replace("null", "None")
            )
        except Exception:
            return []

    def _init_ui(self):
        self.view = Box(
            name="viewport",
            spacing=0,
            orientation=Gtk.Orientation.HORIZONTAL,
        )
        self.wrapper = Box(
            name="dock",
            children=[self.view],
            orientation=Gtk.Orientation.HORIZONTAL,
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

        self.add(
            Box(
                orientation=Gtk.Orientation.VERTICAL,
                h_align="center",
                children=[activator, self.revealer],
            )
        )

        self.wrapper.connect("size-allocate", self._on_size_allocate)

    def _on_size_allocate(self, _, alloc):
        if self._visibility:
            self._visibility.update_size(alloc.width, alloc.height)

    def _bind_events(self):
        c = self.conn
        for ev in (
            "openwindow",
            "closewindow",
            "movewindow",
            "workspace",
            "changefloatingmode",
            "fullscreen",
        ):
            c.connect(f"event::{ev}", self._schedule_update)

        c.connect("event::activewindow", self._on_active_window)
        c.connect("event::windowtitle", self._on_window_title)
        c.connect("event::monitoradded", self._update_monitor)
        c.connect("event::monitorremoved", self._update_monitor)

        if c.ready:
            self._on_ready()
        else:
            c.connect("event::ready", self._on_ready)

    def _on_window_title(self, *_):
        self._sync_tooltips()

    def _sync_tooltips(self):
        clients = self._parse("j/clients")
        if not clients:
            return

        best: dict[str, str] = {}
        for c in clients:
            original_class = c.get("initialClass") or c.get("class") or ""
            title = c.get("title", "")
            if not original_class and not title:
                continue
            raw = original_class or title
            key = raw.lower().split(" - ", 1)[0].strip()

            prev_fh = best.get(key)
            fh = c.get("focusHistoryID", 999)
            if prev_fh is None or fh < prev_fh[1]:
                best[key] = (title, fh)

        titles: dict[str, str] = {k: v[0] for k, v in best.items()}

        for container in self.view.get_children():
            cls = getattr(container, "_cls", None)
            if not cls:
                continue

            main_btn = getattr(container, "_main_btn", container)
            app = getattr(container, "_app", None)
            original = getattr(container, "_original", "")

            app_name = (app.display_name or app.name) if app else None
            fresh_title = titles.get(cls)

            tooltip = app_name or fresh_title or original
            main_btn.set_tooltip_text(tooltip)

    def _on_ready(self, *_):
        self._pin_mgr.restore()
        self._update_monitor()
        self.show_all()
        if self._visibility:
            self._visibility.start()
        GLib.timeout_add(150, self._do_full_update)

    def _schedule_update(self, *_):
        if self._update_timer is not None:
            GLib.source_remove(self._update_timer)
        self._update_timer = GLib.timeout_add(
            self._UPDATE_DEBOUNCE_MS, self._do_full_update
        )

    def _on_active_window(self, *_):
        self._sync_active()

    def _do_full_update(self):
        self._update_timer = None
        if self._drag_active:
            return False

        self._app_resolver.refresh()
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
                tuple(sorted(i.get("address", "") for i in c.get("insts", []))),
            )
            for c in candidates
        )

    def _update_monitor(self, *_):
        for m in self._parse("j/monitors"):
            if m.get("id") == self.monitor_id:
                self._mon_x, self._mon_y = m.get("x", 0), m.get("y", 0)
                self._mon_w, self._mon_h = (
                    m.get("width", 1920),
                    m.get("height", 1080),
                )
                new_size = int(self._mon_h * self._icon_scale)
                if abs(self.icon_size - new_size) > 2:
                    self.icon_size = new_size
                    self._last_fingerprint = None
                    self._schedule_update()
                return

    def _build_candidates(self, clients):
        wins: dict = {}
        for c in clients:
            original_class = c.get("initialClass") or c.get("class") or ""
            title = c.get("title", "")
            if not original_class and not title:
                continue
            raw = original_class or title
            key = raw.lower().split(" - ", 1)[0].strip()
            if key not in wins:
                wins[key] = {"original": original_class or title, "instances": []}
            wins[key]["instances"].append(c)

        seen: set = set()
        candidates = []
        app_map = self._app_resolver.app_map

        for key, data in wins.items():
            if key in seen:
                continue
            seen.add(key)
            original = data["original"]
            insts = data["instances"]

            norm = self._app_resolver.norm_name(key)
            if norm != key:
                seen.add(norm)

            app = (
                app_map.get(key)
                or app_map.get(norm)
                or app_map.get(original.lower())
                or self._app_resolver.find_app(original)
            )

            unique_id = (app.name or app.window_class or key) if app else key

            candidates.append(
                {
                    "unique_id": unique_id,
                    "app": app,
                    "insts": insts,
                    "key": key,
                    "original": original,
                }
            )

        existing_ids = {c["unique_id"] for c in candidates}
        candidates.extend(self._pin_mgr.get_ghost_candidates(existing_ids))
        candidates = self._dnd.apply_order(candidates)

        return candidates

    def _rebuild_ui(self, candidates):
        new_widgets = [
            self._make_btn(
                item["app"],
                item["insts"],
                item["key"],
                item["original"],
                item["unique_id"],
            )
            for item in candidates
        ]

        for c in self.view.get_children():
            self.view.remove(c)
            c.destroy()

        for w in new_widgets:
            self.view.add(w)

        self.view.show_all()

    def _make_btn(self, app, insts, key, original_class, unique_id):
        name = (app.display_name or app.name) if app else None

        icon_pixbuf = None
        if app:
            icon_pixbuf = self._app_resolver.get_icon(
                original_class, self.icon_size, app
            )
        if not icon_pixbuf:
            icon_pixbuf = self._app_resolver.get_icon(
                original_class, self.icon_size
            )
        if not icon_pixbuf and key != original_class.lower():
            icon_pixbuf = self._app_resolver.get_icon(key, self.icon_size)
        if not icon_pixbuf:
            try:
                icon_pixbuf = self._app_resolver.theme.load_icon(
                    "application-x-executable-symbolic",
                    self.icon_size,
                    Gtk.IconLookupFlags.FORCE_SIZE,
                )
            except Exception:
                pass

        icon_image = Image(pixbuf=icon_pixbuf, name="dock-icon-image")
        icon_box = Box(
            name="dock-icon-box",
            orientation="v",
            h_align="center",
            v_align="end",
            children=[icon_image],
        )

        dots_box = Box(
            name="dock-dots", orientation="v", spacing=2, v_align="center"
        )
        num_insts = len(insts) if insts else 0
        for _ in range(min(num_insts, self._MAX_DOTS)):
            dot = Box(name="dock-dot")
            dot.set_size_request(5, 5)
            dots_box.add(dot)

        content_box = Box(
            name="dock-icon",
            orientation="h",
            h_align="center",
            v_align="center",
            spacing=2,
        )
        icon_wrapper = Box(
            name="dock-icon-wrapper",
            orientation="v",
            h_align="center",
            v_align="end",
            spacing=4,
            children=[icon_box],
        )
        content_box.add(icon_wrapper)

        is_pinned = self._pin_mgr.is_pinned(unique_id)

        if num_insts > 0:
            content_box.add(
                Box(
                    name="dock-dots-wrapper",
                    orientation="v",
                    v_align="center",
                    children=[dots_box],
                )
            )
        elif is_pinned and not insts:
            content_box.add(
                Box(
                    name="dock-dots-wrapper",
                    orientation="v",
                    v_align="center",
                    children=[Label(label="✕", name="dock-cross")],
                )
            )

        tooltip = (
            name
            or (insts[0].get("title") if insts else None)
            or original_class
        )

        main_btn = Button(
            child=content_box,
            tooltip_text=tooltip,
            name="dock-app-button",
        )

        pin_label = Label(
            markup=self._icon_pinned if is_pinned else self._icon_pin
        )
        pin_btn = Button(
            name="dock-app-pin-btn",
            child=pin_label,
            v_align="start",
            h_align="start",
            tooltip_text="Unpin" if is_pinned else "Pin to Dock",
        )
        if is_pinned:
            pin_btn.add_style_class("active")

        container = Overlay(child=main_btn, overlays=[pin_btn])
        container._cls = key
        container._original = original_class
        container._app = app
        container._insts = insts
        container._icon_box = icon_box
        container._unique_id = unique_id
        container._main_btn = main_btn
        container._hover_timer = None

        main_btn.connect("clicked", lambda *_: self._on_btn_click(container))
        main_btn.connect(
            "enter-notify-event",
            lambda w, e: self._on_btn_hover_enter(container, e),
        )
        main_btn.connect(
            "leave-notify-event",
            lambda w, e: self._on_btn_hover_leave(container, e),
        )

        pin_btn.connect(
            "clicked",
            lambda *_: self._on_app_pin_toggle(container, pin_label, pin_btn),
        )
        pin_btn.connect(
            "enter-notify-event",
            lambda w, e: self._on_btn_hover_enter(container, e),
        )
        pin_btn.connect(
            "leave-notify-event",
            lambda w, e: self._on_btn_hover_leave(container, e),
        )

        if insts:
            main_btn.add_style_class("instance")
        elif is_pinned:
            main_btn.add_style_class("pinned-empty")

        self._dnd.setup(container)

        return container

    def _on_app_pin_toggle(self, container, pin_label, pin_btn):
        uid = container._unique_id
        now_pinned = self._pin_mgr.toggle(
            uid, container._app, container._cls, container._original
        )
        if now_pinned:
            pin_label.set_markup(self._icon_pinned)
            pin_btn.add_style_class("active")
            pin_btn.set_tooltip_text("Unpin")
        else:
            pin_label.set_markup(self._icon_pin)
            pin_btn.remove_style_class("active")
            pin_btn.set_tooltip_text("Pin to Dock")
        if not container._insts:
            self._last_fingerprint = None
            self._schedule_update()

    def _on_btn_click(self, container):
        if self._drag_active:
            return
        app, insts = container._app, container._insts
        if not insts:
            self._restorer.launch(
                app, key=container._cls, original_class=container._original
            )
            return
        self._nav.cycle_and_focus(insts)

    def _on_btn_hover_enter(self, container, event):
        if self._visibility:
            self._visibility.mouse_enter()
        if self._drag_active:
            return False

        if event.window and event.detail != Gdk.NotifyType.INFERIOR:
            display = event.window.get_display()
            cursor = Gdk.Cursor.new_from_name(display, "pointer")
            event.window.set_cursor(cursor)

        self._cancel_hover_timer(container)
        container._hover_timer = GLib.timeout_add(
            self._HOVER_DEBOUNCE_MS,
            self._apply_hover,
            container,
        )
        return False

    def _apply_hover(self, container):
        container._hover_timer = None
        container.add_style_class("hovered")
        getattr(container, "_main_btn", container).add_style_class("hovered")
        icon_box = getattr(container, "_icon_box", None)
        if icon_box:
            icon_box.add_style_class("lifted")
        return False

    def _on_btn_hover_leave(self, container, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self._cancel_hover_timer(container)
        container.remove_style_class("hovered")
        getattr(container, "_main_btn", container).remove_style_class("hovered")
        icon_box = getattr(container, "_icon_box", None)
        if icon_box:
            icon_box.remove_style_class("lifted")
        if event.window:
            event.window.set_cursor(None)
        return False

    def _cancel_hover_timer(self, container):
        timer = getattr(container, "_hover_timer", None)
        if timer:
            GLib.source_remove(timer)
            container._hover_timer = None

    def _sync_active(self):
        aw = self._parse("j/activewindow")
        active = (
            self._app_resolver.norm_name(
                aw.get("initialClass") or aw.get("class", "")
            )
            if aw
            else ""
        )
        for container in self.view.get_children():
            cls = getattr(container, "_cls", None)
            main_btn = getattr(container, "_main_btn", container)
            if cls and active and self._app_resolver.norm_name(cls) == active:
                main_btn.add_style_class("active")
            else:
                main_btn.remove_style_class("active")

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