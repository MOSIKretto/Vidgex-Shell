import os
import signal
import subprocess

import gi

gi.require_version("Gray", "0.1")
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, GdkPixbuf, GLib, GObject, Gray, Gtk
from fabric.widgets.box import Box


ICON_CACHE_LIMIT = 128
ANIMATION_STEPS = 12
ANIMATION_INTERVAL = 16


class SystemTray(Box):
    def __init__(self, pixel_size: int = 16, **kwargs) -> None:
        super().__init__(
            name="systray",
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=0,
            visible=False,
            **kwargs,
        )

        self._pixel_size = pixel_size
        self._destroyed = False
        self._items: dict[str, tuple] = {}
        self._icon_cache: dict[tuple, GdkPixbuf.Pixbuf] = {}
        self._theme_cache: dict[str, Gtk.IconTheme] = {}

        self._expanded_ident: str | None = None
        self._anim_timer: int | None = None
        self._anim_step: int = 0
        self._anim_direction: int = 1
        self._click_outside_handler: int | None = None

        self._build_ui()
        self.connect("destroy", lambda _: self.cleanup())
        self._start_watcher()

    def _build_ui(self) -> None:
        overlay = Gtk.Overlay(visible=True)

        self._inner = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            halign=Gtk.Align.CENTER,
            visible=True,
        )
        self._inner.set_name("systray-inner")

        self._action_bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=0,
            halign=Gtk.Align.FILL,
            valign=Gtk.Align.CENTER,
            no_show_all=True,
        )
        self._action_bar.set_name("systray-action-bar")

        self._action_icon = Gtk.Image(visible=True)
        self._action_bar.pack_start(self._action_icon, False, False, 8)

        spacer = Gtk.Box(hexpand=True, visible=True)
        self._action_bar.pack_start(spacer, True, True, 0)

        close_btn = Gtk.Button(label="Close", can_focus=False, has_tooltip=False)
        close_btn.set_name("systray-close-btn")
        close_btn.set_relief(Gtk.ReliefStyle.NONE)
        close_btn.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        close_btn.connect("enter-notify-event", self._on_enter)
        close_btn.connect("leave-notify-event", self._on_leave)
        close_btn.connect("clicked", self._on_close_clicked)
        close_btn.show()
        self._action_bar.pack_end(close_btn, False, False, 8)

        overlay.add(self._inner)
        overlay.add_overlay(self._action_bar)
        overlay.set_overlay_pass_through(self._action_bar, False)
        self.pack_start(overlay, True, True, 0)

    def _start_watcher(self) -> None:
        self._watcher = Gray.Watcher()
        self._watcher.connect(
            "item-added",
            lambda _w, ident: GLib.idle_add(self._add_item, ident),
        )
        for method in ("run", "start", "own_name", "watch"):
            if callable(getattr(self._watcher, method, None)):
                getattr(self._watcher, method)()
                break

    def _set_cursor(self, widget: Gtk.Widget, name: str) -> None:
        if win := widget.get_window():
            win.set_cursor(Gdk.Cursor.new_from_name(widget.get_display(), name))

    def _on_enter(self, widget: Gtk.Widget, _event) -> bool:
        self._set_cursor(widget, "pointer")
        return False

    def _on_leave(self, widget: Gtk.Widget, _event) -> bool:
        self._set_cursor(widget, "default")
        return False

    def _get_theme(self, path: str) -> Gtk.IconTheme:
        if not path:
            return Gtk.IconTheme.get_default()
        if path not in self._theme_cache:
            theme = Gtk.IconTheme.new()
            theme.prepend_search_path(path)
            self._theme_cache[path] = theme
        return self._theme_cache[path]

    def _trim_icon_cache(self) -> None:
        if len(self._icon_cache) > ICON_CACHE_LIMIT:
            trim = list(self._icon_cache)[: ICON_CACHE_LIMIT // 3]
            for k in trim:
                del self._icon_cache[k]

    def _load_icon(self, item, ident: str) -> GdkPixbuf.Pixbuf | None:
        sz = self._pixel_size

        key = ("px", ident)
        if key not in self._icon_cache:
            try:
                pixmaps = item.get_icon_pixmaps()
                if pixmaps:
                    pm = Gray.get_pixmap_for_pixmaps(pixmaps, sz)
                    pb = pm.as_pixbuf(sz, GdkPixbuf.InterpType.BILINEAR) if pm else None
                    if pb:
                        self._icon_cache[key] = pb
                        self._trim_icon_cache()
            except Exception:
                pass
        if key in self._icon_cache:
            return self._icon_cache[key]

        try:
            name = item.get_icon_name() or ""
        except Exception:
            return None
        if not name:
            return None

        if "/" in name:
            key = ("file", name)
            if key not in self._icon_cache:
                try:
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(name, sz, sz, True)
                    if pb:
                        self._icon_cache[key] = pb
                        self._trim_icon_cache()
                except Exception:
                    pass
            return self._icon_cache.get(key)

        try:
            theme_path = item.get_icon_theme_path() or ""
        except Exception:
            theme_path = ""

        key = ("theme", name, theme_path)
        if key not in self._icon_cache:
            try:
                pb = self._get_theme(theme_path).load_icon(name, sz, Gtk.IconLookupFlags.FORCE_SIZE)
                if pb:
                    self._icon_cache[key] = pb
                    self._trim_icon_cache()
            except Exception:
                pass
        return self._icon_cache.get(key)

    def _invalidate_icon(self, ident: str) -> None:
        self._icon_cache = {
            k: v for k, v in self._icon_cache.items()
            if not (k[0] == "px" and k[1] == ident)
        }

    def _update_visibility(self) -> None:
        visible = any(btn.get_visible() for _, btn, _ in self._items.values())
        self.set_visible(visible)

    def _update_btn(self, item, btn: Gtk.Button, ident: str) -> None:
        if self._destroyed:
            return

        try:
            status = item.get_status() or ""
        except Exception:
            status = ""

        pb = None if status == "Passive" else self._load_icon(item, ident)

        if pb:
            btn.get_image().set_from_pixbuf(pb)
            tooltip = None
            for attr in ("get_tooltip_text", "get_title"):
                try:
                    tooltip = getattr(item, attr, lambda: None)() or None
                    if tooltip:
                        break
                except Exception:
                    pass
            if tooltip:
                btn.set_tooltip_text(str(tooltip))
            else:
                btn.set_has_tooltip(False)
            btn.show()
        else:
            btn.hide()

        self._update_visibility()

    def _add_item(self, ident: str) -> bool:
        if self._destroyed:
            return False

        item = self._watcher.get_item_for_identifier(ident)
        if item is None:
            return False

        self._remove_item(ident)

        btn = Gtk.Button(can_focus=False, no_show_all=True)
        btn.set_name("systray-item")
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_image(Gtk.Image(visible=True))
        btn.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        btn.connect("enter-notify-event", self._on_enter)
        btn.connect("leave-notify-event", self._on_leave)
        btn.connect("button-press-event", lambda _b, _e, i=ident: self._on_tray_click(i))

        def on_icon(*_, i=ident, it=item, b=btn):
            self._invalidate_icon(i)
            GLib.idle_add(self._update_btn, it, b, i)

        def on_status(*_, it=item, b=btn, i=ident):
            GLib.idle_add(self._update_btn, it, b, i)

        def on_removed(*_, i=ident):
            GLib.idle_add(self._remove_item, i)

        handlers = [
            item.connect("notify::icon-pixmaps", on_icon),
            item.connect("notify::icon-name", on_icon),
            item.connect("notify::status", on_status),
            item.connect("removed", on_removed),
        ]
        if "icon-changed" in GObject.signal_list_names(type(item)):
            handlers.append(item.connect("icon-changed", on_icon))

        self._items[ident] = (item, btn, handlers)
        self._inner.add(btn)
        self._update_btn(item, btn, ident)
        return False

    def _remove_item(self, ident: str) -> bool:
        if self._expanded_ident == ident:
            self._collapse(animate=False)

        entry = self._items.pop(ident, None)
        if entry is None:
            return False

        item, btn, handlers = entry
        for hid in handlers:
            try:
                item.disconnect(hid)
            except Exception:
                pass

        btn.destroy()
        self._invalidate_icon(ident)
        self._update_visibility()
        return False

    def _on_tray_click(self, ident: str) -> bool:
        if self._expanded_ident == ident:
            self._collapse()
        else:
            if self._expanded_ident is not None:
                self._collapse(animate=False)
            self._expand(ident)
        return True

    def _expand(self, ident: str) -> None:
        if ident not in self._items:
            return
        self._expanded_ident = ident
        item, _btn, _ = self._items[ident]
        pb = self._load_icon(item, ident)
        if pb:
            self._action_icon.set_from_pixbuf(pb)
        else:
            self._action_icon.clear()
        self._start_animation(direction=1)
        self._attach_click_outside()

    def _collapse(self, animate: bool = True) -> None:
        self._expanded_ident = None
        self._detach_click_outside()
        if animate:
            self._start_animation(direction=-1)
        else:
            self._stop_animation()
            self._apply_animation_state(0.0)
            self._action_bar.hide()
            self._inner.set_opacity(1.0)

    def _start_animation(self, direction: int) -> None:
        self._stop_animation()
        self._anim_direction = direction
        self._anim_step = 0 if direction == 1 else ANIMATION_STEPS
        self._anim_timer = GLib.timeout_add(ANIMATION_INTERVAL, self._animation_tick)

    def _stop_animation(self) -> None:
        if self._anim_timer is not None:
            GLib.source_remove(self._anim_timer)
            self._anim_timer = None

    def _animation_tick(self) -> bool:
        self._anim_step += self._anim_direction
        progress = max(0.0, min(1.0, self._anim_step / ANIMATION_STEPS))
        t = progress * progress * (3 - 2 * progress)
        self._apply_animation_state(t)

        done = (self._anim_direction == 1 and self._anim_step >= ANIMATION_STEPS) or \
               (self._anim_direction == -1 and self._anim_step <= 0)

        if done:
            self._anim_timer = None
            if self._anim_direction == -1:
                self._action_bar.hide()
                self._inner.set_opacity(1.0)
            return False
        return True

    def _apply_animation_state(self, t: float) -> None:
        self._inner.set_opacity(1.0 - t * 0.85)
        if t > 0.05:
            self._action_bar.show()
            self._action_bar.set_opacity(t)
        else:
            self._action_bar.set_opacity(0.0)
            self._action_bar.hide()

    def _attach_click_outside(self) -> None:
        self._detach_click_outside()
        top = self.get_toplevel()
        if isinstance(top, Gtk.Window):
            self._click_outside_handler = top.connect(
                "button-press-event", self._on_window_click
            )

    def _detach_click_outside(self) -> None:
        if self._click_outside_handler is not None:
            top = self.get_toplevel()
            if isinstance(top, Gtk.Window):
                try:
                    top.disconnect(self._click_outside_handler)
                except Exception:
                    pass
            self._click_outside_handler = None

    def _on_window_click(self, _window: Gtk.Window, event: Gdk.EventButton) -> bool:
        if self._expanded_ident is None or not self._action_bar.get_visible():
            self._collapse()
            return False

        ab_win = self._action_bar.get_window()
        if ab_win is None:
            self._collapse()
            return False

        ok, ax, ay = ab_win.get_origin()
        if not ok:
            self._collapse()
            return False

        alloc = self._action_bar.get_allocation()
        rel_x = int(event.x_root) - ax
        rel_y = int(event.y_root) - ay

        if not (0 <= rel_x <= alloc.width and 0 <= rel_y <= alloc.height):
            self._collapse()
        return False

    def _on_close_clicked(self, _btn: Gtk.Button) -> None:
        if self._destroyed or self._expanded_ident is None:
            return
        ident = self._expanded_ident
        self._collapse(animate=False)
        if ident not in self._items:
            return
        item, _, _ = self._items[ident]
        self._kill_item(item, ident)
        GLib.idle_add(self._remove_item, ident)

    def _kill_item(self, item, ident: str) -> None:
        dbus_name = self._get_dbus_name(item, ident)
        if dbus_name:
            pid = self._pid_from_dbus(dbus_name)
            if pid:
                self._kill_pid(pid)
                return
        app_name = self._guess_app_name(item, ident)
        if app_name:
            self._kill_by_name(app_name)

    def _get_dbus_name(self, item, ident: str) -> str:
        for attr in ("get_service_name", "get_bus_name", "get_dbus_name"):
            try:
                name = getattr(item, attr, lambda: None)()
                if name:
                    return str(name)
            except Exception:
                pass
        if "." in ident:
            return ident.split("/")[0]
        return ""

    def _pid_from_dbus(self, bus_name: str) -> int | None:
        try:
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
            parts = result.stdout.strip().strip("()").split()
            return int(parts[1].rstrip(","))
        except Exception:
            return None

    def _kill_pid(self, pid: int) -> None:
        try:
            result = subprocess.run(
                ["pgrep", "-P", str(pid)],
                capture_output=True, text=True, timeout=2,
            )
            for child in result.stdout.split():
                if child.isdigit():
                    self._kill_pid(int(child))
        except Exception:
            pass
        try:
            os.kill(pid, signal.SIGTERM)
            GLib.timeout_add(2000, lambda p=pid: self._force_kill(p))
        except ProcessLookupError:
            pass

    def _force_kill(self, pid: int) -> bool:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return False

    def _guess_app_name(self, item, ident: str) -> str:
        for attr in ("get_title", "get_app_id"):
            try:
                name = getattr(item, attr, lambda: None)() or ""
                if name:
                    return name.lower().split()[0]
            except Exception:
                pass
        if ident:
            parts = [p for p in ident.replace("-", ".").split(".") if not p.isdigit()]
            if parts:
                return parts[-1].lower()
        return ""

    def _kill_by_name(self, app_name: str) -> None:
        try:
            subprocess.run(["pkill", "-TERM", "-i", "-f", app_name], timeout=2, check=False)
            GLib.timeout_add(2000, lambda n=app_name: self._force_kill_by_name(n))
        except Exception:
            pass

    def _force_kill_by_name(self, app_name: str) -> bool:
        try:
            subprocess.run(["pkill", "-KILL", "-i", "-f", app_name], timeout=2, check=False)
        except Exception:
            pass
        return False

    def cleanup(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        self._stop_animation()
        self._detach_click_outside()
        try:
            self._watcher.disconnect_by_func(GLib.idle_add)
        except Exception:
            pass
        for ident in list(self._items):
            self._remove_item(ident)
        self._icon_cache.clear()
        self._theme_cache.clear()