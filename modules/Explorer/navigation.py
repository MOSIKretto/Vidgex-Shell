import os

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gio

from pathlib import Path

_ATTR_CHANGED = Gio.FileMonitorEvent.ATTRIBUTE_CHANGED
_WATCH_MOVES  = Gio.FileMonitorFlags.WATCH_MOVES
_source_remove = GLib.source_remove
_timeout_add   = GLib.timeout_add


class NavigationMixin:

    def _set_navigation_lock(self):
        self._navigation_lock = True
        self._cancel_pending_hide()

        timer = self._navigation_lock_timer
        if timer:
            _source_remove(timer)

        self._navigation_lock_timer = _timeout_add(
            500, self._clear_navigation_lock
        )

    def _clear_navigation_lock(self):
        self._navigation_lock = False
        self._navigation_lock_timer = None
        return False

    def _navigate_to(self, path, *, _update_history=True):
        if self._is_loading:
            return

        if not isinstance(path, Path):
            path = Path(path)

        if not path.is_dir():
            return

        try:
            with os.scandir(path):
                pass
        except OSError:
            return

        self._close_rename_widget()
        self._close_app_chooser()
        self._set_navigation_lock()
        self._current_path = path

        if _update_history:
            hist = self._history
            idx  = self._history_index
            end  = len(hist) - 1

            if idx < end:
                del hist[idx + 1:]

            if not hist or hist[-1] != path:
                hist.append(path)
                self._history_index = len(hist) - 1

        idx      = self._history_index
        hist_len = len(self._history)
        self.btn_back.set_sensitive(idx > 0)
        self.btn_forward.set_sensitive(idx < hist_len - 1)
        self.btn_up.set_sensitive(path.parent != path)

        self._update_path_bar()
        self._update_trash_button()
        self._update_eject_button()
        self._load_directory()
        self._setup_file_monitor()

    def _setup_file_monitor(self):
        self._cleanup_file_monitor()
        try:
            gfile   = Gio.File.new_for_path(str(self._current_path))
            monitor = gfile.monitor_directory(_WATCH_MOVES, None)
            monitor.connect("changed", self._on_directory_changed)
            self._file_monitor = monitor
        except Exception:
            pass

    def _cleanup_file_monitor(self):
        monitor = self._file_monitor
        if monitor:
            try:
                monitor.cancel()
            except Exception:
                pass
            self._file_monitor = None

        refresh = self._pending_refresh
        if refresh:
            _source_remove(refresh)
            self._pending_refresh = None

    def _on_directory_changed(self, monitor, file, other_file, event_type):
        if event_type == _ATTR_CHANGED:
            return

        refresh = self._pending_refresh
        if refresh:
            _source_remove(refresh)

        self._pending_refresh = _timeout_add(
            self._refresh_debounce_ms, self._do_refresh
        )

    def _do_refresh(self):
        self._pending_refresh = None

        if not (self._is_loading or self._rename_widget
                or self._app_chooser_active):
            self._load_directory()

        return False

    def _on_back_clicked(self, _):
        if self._pending_drop_source:
            return

        idx = self._history_index
        if idx > 0:
            idx -= 1
            self._history_index = idx
            self._navigate_to(self._history[idx], _update_history=False)

    def _on_forward_clicked(self, _):
        if self._pending_drop_source:
            return

        idx  = self._history_index
        hist = self._history

        if idx < len(hist) - 1:
            idx += 1
            self._history_index = idx
            self._navigate_to(hist[idx], _update_history=False)

    def _on_up_clicked(self, _):
        if self._pending_drop_source:
            return

        path   = self._current_path
        parent = path.parent

        if parent != path:
            self._navigate_to(parent)

    def _on_home_clicked(self, _):
        if self._pending_drop_source:
            return
        self._navigate_to(Path.home())

    def _on_toggle_hidden(self, btn):
        if self._pending_drop_source:
            return

        self._set_navigation_lock()

        show = not self._show_hidden
        self._show_hidden = show

        ctx = btn.get_style_context()
        if show:
            ctx.add_class("active")
        else:
            ctx.remove_class("active")

        self._load_directory()

    def _on_pin_clicked(self, btn):
        self._set_navigation_lock()

        pinned = not self._is_pinned
        self._is_pinned = pinned

        ctx = btn.get_style_context()
        if pinned:
            ctx.add_class("active")
        else:
            ctx.remove_class("active")

        if pinned:
            self._cancel_pending_hide()

    def _on_bookmark_clicked(self, btn):
        if self._pending_drop_source:
            return

        path = getattr(btn, '_path', None)
        if path:
            self._navigate_to(path)

    def _on_path_part_clicked(self, btn):
        if self._pending_drop_source:
            return
        self._navigate_to(btn._path)