import os
import re
import signal
import subprocess

import gi

gi.require_version("Gray", "0.1")
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gray, Gtk
from fabric.widgets.box import Box


class SystemTray(Box):
    def __init__(self, pixel_size: int = 20, **kwargs) -> None:
        super().__init__(
            name="systray",
            spacing=0,
            visible=False,
            **kwargs,
        )
        self.set_no_show_all(True)
        self.set_visible(False)

        self._pixel_size = pixel_size
        self._destroyed = False
        self._items: dict[str, tuple] = {}

        self._expanded: str | None = None
        self._anim_timer: int | None = None
        self._anim_step = 0
        self._anim_dir = 1
        self._outside_handler: int | None = None

        self._build_ui()
        self.connect("destroy", lambda _: self.cleanup())

        self._watcher = Gray.Watcher()
        self._watcher.connect("item-added", lambda _w, ident: GLib.idle_add(self._add_item, ident))


    def _build_ui(self) -> None:
        overlay = Gtk.Overlay(visible=True)

        self._inner = Gtk.Box(
            spacing=8,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
            visible=True,
        )
        self._inner.set_name("systray-inner")

        self._scroller = Gtk.ScrolledWindow(visible=True)
        self._scroller.set_name("systray-scroller")
        self._scroller.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        self._scroller.set_shadow_type(Gtk.ShadowType.NONE)
        self._scroller.set_overlay_scrolling(True)
        self._scroller.set_kinetic_scrolling(True)
        self._scroller.add(self._inner)
        self._scroller.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        self._scroller.connect("scroll-event", self._on_scroll)

        self._action_bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            halign=Gtk.Align.FILL,
            valign=Gtk.Align.CENTER,
            no_show_all=True,
        )
        self._action_bar.set_name("systray-action-bar")

        self._action_icon = Gtk.Image(visible=True)
        self._action_bar.pack_start(self._action_icon, False, False, 8)

        self._action_bar.pack_start(Gtk.Box(hexpand=True, visible=True), True, True, 0)

        close_btn = Gtk.Button(label="Close", can_focus=False)
        close_btn.set_name("systray-close-btn")
        close_btn.set_relief(Gtk.ReliefStyle.NONE)
        close_btn.connect("clicked", self._on_close_clicked)
        close_btn.show()
        self._action_bar.pack_end(close_btn, False, False, 8)

        overlay.add(self._scroller)
        overlay.add_overlay(self._action_bar)
        overlay.set_overlay_pass_through(self._action_bar, False)

        self.pack_start(overlay, True, True, 0)

    def _get_pixbuf(self, item: Gray.Item) -> GdkPixbuf.Pixbuf:
        pixmaps = item.get_icon_pixmaps()
        if pixmaps:
            pm = Gray.get_pixmap_for_pixmaps(pixmaps, self._pixel_size)
            return pm.as_pixbuf(self._pixel_size, GdkPixbuf.InterpType.HYPER)

        name = item.get_icon_name()
        if name and os.path.exists(name):
            return GdkPixbuf.Pixbuf.new_from_file_at_scale(
                name, self._pixel_size, self._pixel_size, True
            )

        theme = Gtk.IconTheme.get_default()
        icon_path = item.get_icon_theme_path()
        if icon_path and os.path.isdir(icon_path):
            theme = Gtk.IconTheme.new()
            theme.set_search_path(Gtk.IconTheme.get_default().get_search_path())
            theme.prepend_search_path(icon_path)

        return theme.load_icon(
            name or "image-missing", self._pixel_size, Gtk.IconLookupFlags.FORCE_SIZE
        )

    def _refresh_icon(self, item: Gray.Item, button: Gtk.Button) -> None:
        pixbuf = self._get_pixbuf(item)
        img = button.get_image()
        img.set_from_pixbuf(pixbuf)

        title = item.get_title()
        if title:
            button.set_tooltip_text(str(title))
        else:
            button.set_has_tooltip(False)

        self._update_visibility()

    def _on_scroll(self, _widget, event: Gdk.EventScroll) -> bool:
        hadj = self._scroller.get_hadjustment()

        has_deltas, dx, dy = event.get_scroll_deltas()
        if has_deltas:
            delta = dx if abs(dx) > 0.01 else dy
        else:
            delta = -1.0 if event.direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.LEFT) else 1.0

        step = (28 + 8) * delta
        lower = hadj.get_lower()
        upper = hadj.get_upper() - hadj.get_page_size()
        hadj.set_value(max(lower, min(upper, hadj.get_value() + step)))
        return True

    def _update_carousel_size(self, count: int) -> None:
        if count > 5:
            width = 5 * 28 + (5 - 1) * 8
            self._scroller.set_size_request(width, -1)
        else:
            self._scroller.set_size_request(-1, -1)

    def _update_visibility(self) -> None:
        if self._destroyed:
            return
        count = len(self._items)
        self._update_carousel_size(count)
        self.set_visible(count > 0)

    def _add_item(self, ident: str) -> bool:
        if self._destroyed:
            return False

        item = self._watcher.get_item_for_identifier(ident)
        self._remove_item(ident)

        btn = Gtk.Button(can_focus=False, no_show_all=True)
        btn.set_name("systray-item")
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_image(Gtk.Image(visible=True))
        btn.connect("button-press-event", lambda b, e, i=ident, it=item: self._on_item_click(it, i, e))
        btn.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        btn.connect("scroll-event", lambda _b, e: self._on_scroll(self._scroller, e))

        handlers = [
            item.connect("notify::icon-pixmaps", lambda it, _: self._refresh_icon(it, btn)),
            item.connect("notify::icon-name", lambda it, _: self._refresh_icon(it, btn)),
            item.connect("removed", lambda *_: GLib.idle_add(self._remove_item, ident)),
        ]

        bus_name = ident.split("/")[0]
        watch_id = Gio.bus_watch_name(
            Gio.BusType.SESSION,
            bus_name,
            Gio.BusNameWatcherFlags.NONE,
            None,
            lambda *_a, i=ident: GLib.idle_add(self._remove_item, i),
        )

        self._items[ident] = (item, btn, handlers, watch_id)
        self._inner.add(btn)
        self._refresh_icon(item, btn)
        btn.show()
        self._update_visibility()
        return False

    def _remove_item(self, ident: str) -> bool:
        if self._expanded == ident:
            self._collapse(animate=False)

        entry = self._items.pop(ident, None)
        if entry is None:
            return False

        item, btn, handlers, watch_id = entry
        for hid in handlers:
            item.disconnect(hid)
        Gio.bus_unwatch_name(watch_id)
        btn.destroy()
        self._update_visibility()
        return False

    def _on_item_click(self, item: Gray.Item, ident: str, event: Gdk.EventButton) -> bool:
        if event.button == Gdk.BUTTON_SECONDARY:
            menu = item.get_menu()
            menu.popup_at_pointer(event)
            return True

        if event.button == Gdk.BUTTON_PRIMARY:
            if self._expanded == ident:
                self._collapse()
            else:
                if self._expanded is not None:
                    self._collapse(animate=False)
                self._expand(ident)
            return True

        return False

    def _expand(self, ident: str) -> None:
        self._expanded = ident
        item, _btn, _handlers, _watch_id = self._items[ident]
        self._action_icon.set_from_pixbuf(self._get_pixbuf(item))
        self._animate(direction=1)
        self._attach_outside_handler()

    def _collapse(self, animate: bool = True) -> None:
        self._expanded = None
        self._detach_outside_handler()
        if animate:
            self._animate(direction=-1)
        else:
            self._stop_animation()
            self._action_bar.hide()
            self._scroller.set_opacity(1.0)

    def _animate(self, direction: int) -> None:
        self._stop_animation()
        self._anim_dir = direction
        self._anim_step = 0 if direction == 1 else 12
        self._anim_timer = GLib.timeout_add(16, self._animation_tick)

    def _stop_animation(self) -> None:
        if self._anim_timer is not None:
            GLib.source_remove(self._anim_timer)
            self._anim_timer = None

    def _animation_tick(self) -> bool:
        self._anim_step += self._anim_dir
        progress = max(0.0, min(1.0, self._anim_step / 12))
        t = progress * progress * (3 - 2 * progress)

        self._scroller.set_opacity(1.0 - t * 0.85)
        if t > 0.05:
            self._action_bar.show()
            self._action_bar.set_opacity(t)
        else:
            self._action_bar.set_opacity(0.0)
            self._action_bar.hide()

        if (self._anim_dir == 1 and self._anim_step >= 12) or (self._anim_dir == -1 and self._anim_step <= 0):
            self._anim_timer = None
            if self._anim_dir == -1:
                self._action_bar.hide()
                self._scroller.set_opacity(1.0)
            return False
        return True

    def _attach_outside_handler(self) -> None:
        self._detach_outside_handler()
        top = self.get_toplevel()
        self._outside_handler = top.connect("button-press-event", self._on_window_click)

    def _detach_outside_handler(self) -> None:
        if self._outside_handler is not None:
            self.get_toplevel().disconnect(self._outside_handler)
            self._outside_handler = None

    def _on_window_click(self, _window: Gtk.Window, event: Gdk.EventButton) -> bool:
        if self._expanded is None:
            return False

        ab_win = self._action_bar.get_window()
        ok, ax, ay = ab_win.get_origin()
        alloc = self._action_bar.get_allocation()
        rel_x = int(event.x_root) - ax
        rel_y = int(event.y_root) - ay

        if not (ok and 0 <= rel_x <= alloc.width and 0 <= rel_y <= alloc.height):
            self._collapse()
        return False

    def _on_close_clicked(self, _btn: Gtk.Button) -> None:
        if self._destroyed or self._expanded is None:
            return
        ident = self._expanded
        item, _btn2, _handlers, _watch_id = self._items[ident]
        self._collapse(animate=False)
        self._kill_item(item, ident)
        GLib.idle_add(self._remove_item, ident)

    def _kill_item(self, item: Gray.Item, ident: str) -> None:
        bus_name = ident.split("/")[0]
        pid = self._dbus_pid(bus_name)
        if pid is not None:
            self._kill_tree(pid)

    def _dbus_pid(self, bus_name: str) -> int | None:
        result = subprocess.run(
            [
                "gdbus", "call", "--session",
                "--dest", "org.freedesktop.DBus",
                "--object-path", "/org/freedesktop/DBus",
                "--method", "org.freedesktop.DBus.GetConnectionUnixProcessID",
                bus_name,
            ],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode != 0:
            return None
        match = re.search(r"uint32\s+(\d+)", result.stdout)
        return int(match.group(1)) if match else None

    def _kill_tree(self, pid: int) -> None:
        children = subprocess.run(
            ["pgrep", "-P", str(pid)], capture_output=True, text=True, timeout=2
        ).stdout.split()
        for child in children:
            self._kill_tree(int(child))
        if os.path.exists(f"/proc/{pid}"):
            os.kill(pid, signal.SIGKILL)

    def cleanup(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        self._stop_animation()
        self._detach_outside_handler()
        for ident in list(self._items):
            self._remove_item(ident)