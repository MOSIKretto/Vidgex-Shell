from pathlib import Path
import os
from typing import List, Optional, Tuple

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gio


class NavigationMixin:

    @staticmethod
    def _algo_history_push(history: List[Path], index: int, path: Path) -> Tuple[List[Path], int]:
        if index < len(history) - 1:
            del history[index + 1:]
            
        if not history or history[-1] != path:
            history.append(path)
            
        return history, len(history) - 1

    @staticmethod
    def _algo_history_go(index: int, hist_len: int, delta: int) -> Optional[int]:
        new = index + delta
        if 0 <= new < hist_len:
            return new
        return None

    @staticmethod
    def _algo_nav_sensitivity(index: int, hist_len: int, path: Path) -> Tuple[bool, bool, bool]:
        return (
            index > 0,
            index < hist_len - 1,
            path.parent != path,
        )

    @staticmethod
    def _algo_is_navigable(path: Path) -> bool:
        if not isinstance(path, Path):
            try:
                path = Path(path)
            except Exception:
                return False
                
        if not path.is_dir():
            return False
            
        try:
            with os.scandir(path):
                pass
            return True
        except OSError:
            return False

    @staticmethod
    def _algo_should_refresh(is_loading: bool, rename_active: bool, chooser_active: bool) -> bool:
        return not (is_loading or rename_active or chooser_active)

    def _lock_set(self):
        self._navigation_lock = True
        self._cancel_pending_hide()
        
        if self._navigation_lock_timer:
            GLib.source_remove(self._navigation_lock_timer)
            
        self._navigation_lock_timer = GLib.timeout_add(500, self._lock_clear)

    def _lock_clear(self) -> bool:
        self._navigation_lock = False
        self._navigation_lock_timer = None
        return False

    def _monitor_setup(self):
        self._monitor_cleanup()
        try:
            gfile = Gio.File.new_for_path(str(self._current_path))
            mon = gfile.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
            mon.connect("changed", self._h_dir_changed)
            self._file_monitor = mon
        except Exception:
            pass

    def _monitor_cleanup(self):
        if self._file_monitor:
            try:
                self._file_monitor.cancel()
            except Exception:
                pass
            self._file_monitor = None
            
        if self._pending_refresh:
            GLib.source_remove(self._pending_refresh)
            self._pending_refresh = None

    def _monitor_schedule_refresh(self):
        if self._pending_refresh:
            GLib.source_remove(self._pending_refresh)
            
        self._pending_refresh = GLib.timeout_add(
            self._refresh_debounce_ms, self._monitor_do_refresh)

    def _monitor_do_refresh(self) -> bool:
        self._pending_refresh = None
        if self._algo_should_refresh(self._is_loading, bool(self._rename_widget), self._app_chooser_active):
            self._load_directory()
        return False

    def _history_push(self, path: Path):
        self._history, self._history_index = self._algo_history_push(
            self._history, self._history_index, path)

    def _history_go(self, delta: int):
        new_idx = self._algo_history_go(self._history_index, len(self._history), delta)
        if new_idx is not None:
            self._history_index = new_idx
            self._navigate_to(self._history[new_idx], _update_history=False)

    def _sync_nav_buttons(self):
        back, fwd, up = self._algo_nav_sensitivity(
            self._history_index, len(self._history), self._current_path)
            
        self.btn_back.set_sensitive(back)
        self.btn_forward.set_sensitive(fwd)
        self.btn_up.set_sensitive(up)

    def _navigate_to(self, path: Path, *, _update_history: bool = True):
        if self._is_loading:
            return

        if not isinstance(path, Path):
            path = Path(path)

        if not self._algo_is_navigable(path):
            return

        self._close_rename_widget()
        self._close_app_chooser()
        self._lock_set()
        
        self._current_path = path

        if _update_history:
            self._history_push(path)

        self._sync_nav_buttons()
        self._update_path_bar()
        self._update_trash_button()
        self._update_eject_button()
        self._load_directory()
        self._monitor_setup()

    def _h_dir_changed(self, monitor: Gio.FileMonitor, file: Gio.File, other_file: Gio.File, event_type: Gio.FileMonitorEvent):
        if event_type != Gio.FileMonitorEvent.ATTRIBUTE_CHANGED:
            self._monitor_schedule_refresh()

    def _on_back_clicked(self, _):
        if not self._pending_drop_source:
            self._history_go(-1)

    def _on_forward_clicked(self, _):
        if not self._pending_drop_source:
            self._history_go(+1)

    def _on_up_clicked(self, _):
        if self._pending_drop_source:
            return
            
        parent = self._current_path.parent
        if parent != self._current_path:
            self._navigate_to(parent)

    def _on_home_clicked(self, _):
        if not self._pending_drop_source:
            self._navigate_to(Path.home())

    def _on_toggle_hidden(self, btn):
        if self._pending_drop_source:
            return
            
        self._lock_set()
        self._show_hidden = not self._show_hidden
        
        ctx = btn.get_style_context()
        if self._show_hidden:
            ctx.add_class("active")
        else:
            ctx.remove_class("active")
            
        self._load_directory()

    def _on_pin_clicked(self, btn):
        self._lock_set()
        self._is_pinned = not self._is_pinned
        
        ctx = btn.get_style_context()
        if self._is_pinned:
            ctx.add_class("active")
            self._cancel_pending_hide()
        else:
            ctx.remove_class("active")

    def _on_bookmark_clicked(self, btn):
        if self._pending_drop_source:
            return
            
        path = getattr(btn, '_path', None)
        if path:
            self._navigate_to(path)

    def _on_path_part_clicked(self, btn):
        if not self._pending_drop_source:
            self._navigate_to(btn._path)