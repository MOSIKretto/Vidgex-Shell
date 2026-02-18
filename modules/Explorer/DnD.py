import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

import shutil
import urllib.parse
from pathlib import Path


class DnDMixin:

    def _setup_activator_drop_target(self):
        self.activator.drag_dest_set(
            Gtk.DestDefaults.MOTION,
            self._dnd_targets,
            Gdk.DragAction.COPY | Gdk.DragAction.MOVE
        )
        self.activator.connect("drag-motion", self._on_activator_drag_motion)
        self.activator.connect("drag-leave", self._on_activator_drag_leave)

    def _setup_explorer_drop_tracking(self):
        self.explorer_eb.drag_dest_set(
            Gtk.DestDefaults.MOTION,
            self._dnd_targets,
            Gdk.DragAction.COPY | Gdk.DragAction.MOVE
        )
        self.explorer_eb.connect("drag-motion", self._on_explorer_drag_motion)
        self.explorer_eb.connect("drag-leave", self._on_explorer_drag_leave)

    def _on_activator_drag_motion(self, widget, context, x, y, time) -> bool:
        self._cancel_pending_hide()
        self._cancel_activator_hover_timer()
        self._drag_over_explorer = True
        if not self.revealer.get_child_revealed():
            self.revealer.set_reveal_child(True)
        Gdk.drag_status(context, Gdk.DragAction.COPY, time)
        return True

    def _on_activator_drag_leave(self, widget, context, time):
        GLib.timeout_add(50, self._check_drag_still_over)

    def _on_explorer_drag_motion(self, widget, context, x, y, time) -> bool:
        self._cancel_pending_hide()
        self._drag_over_explorer = True
        try:
            success, file_view_x, file_view_y = widget.translate_coordinates(self.file_view, x, y)
            if success:
                self._update_drag_scroll(file_view_y)
        except:
            pass
        return False

    def _on_explorer_drag_leave(self, widget, context, time):
        self._drag_over_explorer = False
        self._stop_drag_scroll()
        GLib.timeout_add(100, self._check_drag_hide)

    def _check_drag_still_over(self) -> bool:
        if not self._drag_over_explorer and not self._is_pinned:
            if not self._is_cursor_over_explorer() and not self._pending_drop_source:
                self._schedule_hide()
        return False

    def _check_drag_hide(self) -> bool:
        if self._is_pinned or self._pending_drop_source or self._post_drag_grace or self._navigation_lock:
            return False
        if not self._is_cursor_over_explorer():
            self._schedule_hide()
        return False

    def _update_drag_scroll(self, y: int):
        try:
            alloc = self.file_view.get_allocation()
            height = alloc.height
            if y < 0 or y > height:
                self._stop_drag_scroll()
                return
            if y < self.DRAG_SCROLL_MARGIN:
                distance_from_edge = y
                speed = -self.DRAG_SCROLL_SPEED_FAST if distance_from_edge < self.DRAG_SCROLL_MARGIN // 2 else -self.DRAG_SCROLL_SPEED_SLOW
            elif y > height - self.DRAG_SCROLL_MARGIN:
                distance_from_edge = height - y
                speed = self.DRAG_SCROLL_SPEED_FAST if distance_from_edge < self.DRAG_SCROLL_MARGIN // 2 else self.DRAG_SCROLL_SPEED_SLOW
            else:
                speed = 0
            self._drag_scroll_speed = speed
            if speed != 0:
                if self._drag_scroll_timer is None:
                    self._do_drag_scroll()
                    self._drag_scroll_timer = GLib.timeout_add(self.DRAG_SCROLL_INTERVAL, self._do_drag_scroll)
            else:
                self._stop_drag_scroll()
        except:
            pass

    def _stop_drag_scroll(self):
        if self._drag_scroll_timer:
            GLib.source_remove(self._drag_scroll_timer)
            self._drag_scroll_timer = None
        self._drag_scroll_speed = 0

    def _do_drag_scroll(self) -> bool:
        if self._drag_scroll_speed == 0:
            self._drag_scroll_timer = None
            return False
        try:
            adj = self.file_view.get_vadjustment()
            if adj:
                current = adj.get_value()
                new_value = current + self._drag_scroll_speed
                lower = adj.get_lower()
                upper = adj.get_upper() - adj.get_page_size()
                new_value = max(lower, min(new_value, upper))
                adj.set_value(new_value)
        except:
            pass
        return True

    def _start_post_drag_grace(self):
        self._cancel_post_drag_grace()
        self._post_drag_grace = True
        self._post_drag_timer = GLib.timeout_add(self.POST_DRAG_GRACE_PERIOD, self._end_post_drag_grace)

    def _cancel_post_drag_grace(self):
        if self._post_drag_timer:
            GLib.source_remove(self._post_drag_timer)
            self._post_drag_timer = None

    def _end_post_drag_grace(self) -> bool:
        self._post_drag_timer = None
        self._post_drag_grace = False
        if self._is_pinned or self._menu_open or self._drag_in_progress or self._pending_drop_source or self._navigation_lock:
            return False
        if self._is_cursor_over_explorer():
            self._cursor_inside = True
        else:
            self._cursor_inside = False
            self._schedule_hide()
        return False

    def _start_drag_hover_timer(self, path: Path, widget: Gtk.Widget = None):
        self._cancel_drag_hover_timer()

        if path == self._current_path:
            return

        if self._drag_source_path and path == self._drag_source_path:
            return

        self._drag_hover_path = path
        self._drag_hover_widget = widget
        if widget:
            widget.get_style_context().add_class("drag-hover-pending")
        self._drag_hover_timer = GLib.timeout_add(self.DRAG_HOVER_OPEN_DELAY, self._on_drag_hover_timeout)

    def _cancel_drag_hover_timer(self):
        if self._drag_hover_timer:
            GLib.source_remove(self._drag_hover_timer)
            self._drag_hover_timer = None
        if self._drag_hover_widget:
            try:
                self._drag_hover_widget.get_style_context().remove_class("drag-hover-pending")
            except:
                pass
            self._drag_hover_widget = None
        self._drag_hover_path = None

    def _on_drag_hover_timeout(self) -> bool:
        self._drag_hover_timer = None
        path = self._drag_hover_path
        widget = self._drag_hover_widget
        if widget:
            try:
                widget.get_style_context().remove_class("drag-hover-pending")
            except:
                pass
        self._drag_hover_widget = None
        self._drag_hover_path = None
        if not path or not path.is_dir() or path == self._current_path:
            return False
        source_path = self._drag_source_path
        was_dragging = self._drag_in_progress
        if was_dragging and source_path and source_path.exists():
            self._pending_drop_source = source_path
            self._pending_drop_target = None
            self._start_pending_drop_mode()
        self._navigate_to(path)
        return False

    def _start_pending_drop_mode(self):
        self.file_view.get_style_context().add_class("drag-over")
        dest_name = self._current_path.name or "here"
        self.dnd_indicator.set_label(f"Release to drop in {dest_name}")
        self.dnd_indicator.get_style_context().add_class("active")
        self.dnd_indicator.get_style_context().add_class("move")

    def _update_pending_drop_from_motion(self, root_x: float, root_y: float, state):
        if not self._pending_drop_source:
            return
        folder = self._find_folder_at_position(root_x, root_y)
        is_copy = bool(state & Gdk.ModifierType.CONTROL_MASK)
        action_word = "copy" if is_copy else "move"
        self.dnd_indicator.get_style_context().remove_class("copy" if not is_copy else "move")
        self.dnd_indicator.get_style_context().add_class("copy" if is_copy else "move")
        if folder and folder != self._current_path:
            dest_name = folder.name
            self.dnd_indicator.set_label(f"Release to {action_word} to {dest_name}")
            self._pending_drop_target = folder
            if self._pending_hover_path != folder:
                self._start_pending_hover_timer(folder)
            self._highlight_folder(folder)
        else:
            dest_name = self._current_path.name or "here"
            self.dnd_indicator.set_label(f"Release to {action_word} to {dest_name}")
            self._pending_drop_target = None
            self._cancel_pending_hover_timer()
            self._clear_folder_highlights()

    def _start_pending_hover_timer(self, path: Path):
        self._cancel_pending_hover_timer()
        self._pending_hover_path = path
        self._pending_hover_timer = GLib.timeout_add(self.DRAG_HOVER_OPEN_DELAY, self._on_pending_hover_timeout)

    def _cancel_pending_hover_timer(self):
        if self._pending_hover_timer:
            GLib.source_remove(self._pending_hover_timer)
            self._pending_hover_timer = None
        self._pending_hover_path = None

    def _on_pending_hover_timeout(self) -> bool:
        self._pending_hover_timer = None
        path = self._pending_hover_path
        self._pending_hover_path = None
        if not path or not path.is_dir() or path == self._current_path:
            return False
        if not self._pending_drop_source:
            return False
        self._clear_folder_highlights()
        self._navigate_to(path)
        dest_name = self._current_path.name or "here"
        self.dnd_indicator.set_label(f"Release to drop in {dest_name}")
        self._pending_drop_target = None
        return False

    def _execute_pending_drop(self, is_copy: bool):
        if not self._pending_drop_source:
            return
        source = self._pending_drop_source
        target = self._pending_drop_target or self._current_path
        self._cleanup_pending_drop()
        try:
            if source.parent.resolve() == target.resolve():
                self.status_label.set_label("Already in this folder")
                return
            if source.resolve() == target.resolve():
                self.status_label.set_label("Cannot move into itself")
                return
            try:
                target.resolve().relative_to(source.resolve())
                self.status_label.set_label("Cannot move into subfolder")
                return
            except ValueError:
                pass
            dest_path = self._get_unique_path(target / source.name)
            if is_copy:
                if source.is_dir():
                    shutil.copytree(str(source), str(dest_path))
                else:
                    shutil.copy2(str(source), str(dest_path))
                self.status_label.set_label(f"Copied: {source.name}")
            else:
                shutil.move(str(source), str(dest_path))
                self.status_label.set_label(f"Moved: {source.name}")
        except Exception as e:
            self.status_label.set_label(f"Error: {e}")

    def _highlight_folder(self, path: Path):
        for widget, widget_path in self._folder_widgets:
            try:
                if widget_path == path:
                    widget.get_style_context().add_class("drag-over")
                else:
                    widget.get_style_context().remove_class("drag-over")
            except:
                pass

    def _clear_folder_highlights(self):
        for widget, _ in self._folder_widgets:
            try:
                widget.get_style_context().remove_class("drag-over")
            except:
                pass

    def _cleanup_pending_drop(self):
        self._pending_drop_source = None
        self._pending_drop_target = None
        self._cancel_pending_hover_timer()
        self._clear_folder_highlights()
        self.file_view.get_style_context().remove_class("drag-over")
        self.dnd_indicator.set_label("")
        self.dnd_indicator.get_style_context().remove_class("active")
        self.dnd_indicator.get_style_context().remove_class("copy")
        self.dnd_indicator.get_style_context().remove_class("move")
        self._drag_in_progress = False
        self._drag_over_explorer = False
        self._start_post_drag_grace()

    def _on_explorer_motion(self, widget, event) -> bool:
        self._cursor_inside = True
        self._cancel_pending_hide()
        if self._pending_drop_source:
            state = event.state
            if isinstance(state, tuple):
                state = state[1]
            if not (state & Gdk.ModifierType.BUTTON1_MASK):
                is_copy = bool(state & Gdk.ModifierType.CONTROL_MASK)
                self._execute_pending_drop(is_copy)
                return True
            self._update_pending_drop_from_motion(event.x_root, event.y_root, state)
            try:
                success, file_view_x, file_view_y = widget.translate_coordinates(self.file_view, int(event.x), int(event.y))
                if success:
                    self._update_drag_scroll(file_view_y)
            except:
                pass
            return True
        return False

    def _on_explorer_button_release(self, widget, event) -> bool:
        if event.button == 1 and self._pending_drop_source:
            is_copy = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
            folder = self._find_folder_at_position(event.x_root, event.y_root)
            if folder and folder != self._current_path:
                self._pending_drop_target = folder
            self._execute_pending_drop(is_copy)
            return True
        return False

    def _setup_drag_source(self, widget: Gtk.Widget, path: Path):
        widget.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, self._dnd_targets,
                               Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        widget.connect("drag-begin", self._on_drag_begin, path)
        widget.connect("drag-data-get", self._on_drag_data_get, path)
        widget.connect("drag-end", self._on_drag_end)
        widget.connect("drag-failed", self._on_drag_failed)

    def _setup_drop_target(self, widget: Gtk.Widget, target_path: Path = None):
        widget.drag_dest_set(Gtk.DestDefaults.MOTION | Gtk.DestDefaults.HIGHLIGHT | Gtk.DestDefaults.DROP,
                             self._dnd_targets, Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        widget.connect("drag-motion", self._on_drag_motion, target_path)
        widget.connect("drag-leave", self._on_drag_leave, target_path)
        widget.connect("drag-drop", self._on_drag_drop, target_path)
        widget.connect("drag-data-received", self._on_drag_data_received, target_path)

    def _on_drag_begin(self, widget, context, path):
        self._drag_source_path = path
        self._drag_in_progress = True
        self._drag_over_explorer = True
        self._cancel_pending_hide()
        self._cancel_post_drag_grace()

        try:
            icon_size = 48
            icon_name = self._get_icon_for_path(path)
            full_color_icon_name = icon_name.replace("-symbolic", "")

            pixbuf = None
            try:
                pixbuf = self._icon_theme.load_icon(full_color_icon_name, icon_size, 0)
            except:
                pass

            if not pixbuf:
                fallback_name = "folder" if path.is_dir() else "text-x-generic"
                try:
                    pixbuf = self._icon_theme.load_icon(fallback_name, icon_size, 0)
                except:
                    pass

            if pixbuf:
                hot_x = icon_size // 2
                hot_y = icon_size // 2
                Gtk.drag_set_icon_pixbuf(context, pixbuf, hot_x, hot_y)
            else:
                Gtk.drag_set_icon_default(context)

        except Exception:
            Gtk.drag_set_icon_default(context)

        widget.get_style_context().add_class("dragging")
        self.dnd_indicator.set_label(f"Dragging: {path.name}")
        self.dnd_indicator.get_style_context().add_class("active")

    def _on_drag_data_get(self, widget, context, selection, info, time, path):
        uri = path.as_uri()
        selection.set_uris([uri]) if info == self.TARGET_URI_LIST else selection.set_text(str(path), -1)

    def _on_drag_end(self, widget, context):
        widget.get_style_context().remove_class("dragging")
        self._stop_drag_scroll()
        if self._pending_drop_source:
            self._drag_in_progress = False
            return
        self._drag_source_path = None
        self._drag_in_progress = False
        self.dnd_indicator.set_label("")
        self.dnd_indicator.get_style_context().remove_class("active")
        self.dnd_indicator.get_style_context().remove_class("copy")
        self.dnd_indicator.get_style_context().remove_class("move")
        self._cancel_drag_hover_timer()
        self._start_post_drag_grace()

    def _on_drag_failed(self, widget, context, result) -> bool:
        widget.get_style_context().remove_class("dragging")
        self._stop_drag_scroll()
        if self._pending_drop_source:
            self._drag_in_progress = False
            return False
        self._drag_source_path = None
        self._drag_in_progress = False
        self.dnd_indicator.set_label("")
        self.dnd_indicator.get_style_context().remove_class("active")
        self._cancel_drag_hover_timer()
        self._start_post_drag_grace()
        return False

    def _on_drag_motion(self, widget, context, x, y, time, target_path=None) -> bool:
        self._cancel_pending_hide()
        self._drag_over_explorer = True
        widget.get_style_context().add_class("drag-over")
        try:
            success, file_view_x, file_view_y = widget.translate_coordinates(self.file_view, x, y)
            if success:
                self._update_drag_scroll(file_view_y)
        except:
            pass
        if target_path and target_path.is_dir() and self._drag_hover_path != target_path:
            self._start_drag_hover_timer(target_path, widget)
        elif not target_path or not target_path.is_dir():
            self._cancel_drag_hover_timer()
        state = Gdk.Keymap.get_default().get_modifier_state()
        action = Gdk.DragAction.COPY if state & Gdk.ModifierType.CONTROL_MASK else Gdk.DragAction.MOVE
        Gdk.drag_status(context, action, time)
        dest_name = (target_path.name if target_path else self._current_path.name) or "Root"
        self._update_dnd_indicator(action, dest_name)
        return True

    def _on_drag_leave(self, widget, context, time, target_path=None):
        widget.get_style_context().remove_class("drag-over")
        widget.get_style_context().remove_class("drag-hover-pending")
        if target_path and self._drag_hover_path == target_path:
            self._cancel_drag_hover_timer()

    def _on_drag_drop(self, widget, context, x, y, time, target_path=None) -> bool:
        self._cancel_drag_hover_timer()
        self._stop_drag_scroll()
        target = widget.drag_dest_find_target(context, None)
        if target:
            widget.drag_get_data(context, target, time)
            return True
        return False

    def _on_drag_data_received(self, widget, context, x, y, selection, info, time, target_path=None):
        widget.get_style_context().remove_class("drag-over")
        self._cancel_drag_hover_timer()
        self._handle_drop_data(context, selection, info, time, target_path or self._current_path)

    def _update_dnd_indicator(self, action, dest_name: str):
        action_text = "Copy" if action == Gdk.DragAction.COPY else "Move"
        self.dnd_indicator.set_label(f"{action_text} to {dest_name}")
        self.dnd_indicator.get_style_context().add_class("active")
        if action == Gdk.DragAction.COPY:
            self.dnd_indicator.get_style_context().remove_class("move")
            self.dnd_indicator.get_style_context().add_class("copy")
        else:
            self.dnd_indicator.get_style_context().remove_class("copy")
            self.dnd_indicator.get_style_context().add_class("move")

    def _handle_drop_data(self, context, selection, info, time, dest_folder):
        if not dest_folder or not dest_folder.is_dir():
            Gtk.drag_finish(context, False, False, time)
            return
        uris = selection.get_uris()
        if not uris:
            text = selection.get_text()
            if text:
                uris = [text.strip() if text.startswith("file://") else Path(text.strip()).as_uri()]
        if not uris:
            Gtk.drag_finish(context, False, False, time)
            return
        action = context.get_selected_action()
        is_move = action == Gdk.DragAction.MOVE
        processed = 0
        for uri in uris:
            try:
                src_path = self._uri_to_path(uri)
                if not src_path or not src_path.exists():
                    continue
                dest_path = dest_folder / src_path.name
                if src_path == dest_path:
                    continue
                dest_path = self._get_unique_path(dest_path)
                if is_move:
                    shutil.move(str(src_path), str(dest_path))
                else:
                    if src_path.is_dir():
                        shutil.copytree(str(src_path), str(dest_path))
                    else:
                        shutil.copy2(str(src_path), str(dest_path))
                processed += 1
            except Exception as e:
                print(f"DnD error: {e}")
        Gtk.drag_finish(context, processed > 0, is_move and processed > 0, time)
        if processed:
            self.status_label.set_label(f"{'Moved' if is_move else 'Copied'} {processed} item(s)")

    def _uri_to_path(self, uri: str):
        try:
            return Path(urllib.parse.unquote(uri[7:])) if uri.startswith("file://") else Path(uri)
        except:
            return None

    def _get_unique_path(self, path: Path) -> Path:
        if not path.exists():
            return path
        base, ext, parent = path.stem, path.suffix, path.parent
        for i in range(1, 100):
            new_path = parent / f"{base} ({i}){ext}"
            if not new_path.exists():
                return new_path
        return path

    def _setup_path_part_as_drop_target(self, widget: Gtk.Widget, path: Path):
        widget.drag_dest_set(
            Gtk.DestDefaults.MOTION | Gtk.DestDefaults.HIGHLIGHT | Gtk.DestDefaults.DROP,
            self._dnd_targets,
            Gdk.DragAction.COPY | Gdk.DragAction.MOVE
        )
        widget._drop_path = path
        widget.connect("drag-motion", self._on_path_part_drag_motion)
        widget.connect("drag-leave", self._on_path_part_drag_leave)
        widget.connect("drag-drop", self._on_path_part_drag_drop)
        widget.connect("drag-data-received", self._on_path_part_drag_data_received)

    def _on_path_part_drag_motion(self, widget, context, x, y, time) -> bool:
        target_path = widget._drop_path
        self._cancel_pending_hide()
        self._drag_over_explorer = True
        widget.get_style_context().add_class("drag-over")
        if target_path and target_path.is_dir() and self._drag_hover_path != target_path:
            self._start_drag_hover_timer(target_path, widget)
        state = Gdk.Keymap.get_default().get_modifier_state()
        action = Gdk.DragAction.COPY if state & Gdk.ModifierType.CONTROL_MASK else Gdk.DragAction.MOVE
        Gdk.drag_status(context, action, time)
        self._update_dnd_indicator(action, target_path.name or "Root")
        return True

    def _on_path_part_drag_leave(self, widget, context, time):
        widget.get_style_context().remove_class("drag-over")
        widget.get_style_context().remove_class("drag-hover-pending")
        if hasattr(widget, '_drop_path') and self._drag_hover_path == widget._drop_path:
            self._cancel_drag_hover_timer()

    def _on_path_part_drag_drop(self, widget, context, x, y, time) -> bool:
        self._cancel_drag_hover_timer()
        target = widget.drag_dest_find_target(context, None)
        if target:
            widget.drag_get_data(context, target, time)
            return True
        return False

    def _on_path_part_drag_data_received(self, widget, context, x, y, selection, info, time):
        target_path = getattr(widget, '_drop_path', None)
        widget.get_style_context().remove_class("drag-over")
        self._cancel_drag_hover_timer()
        if target_path:
            self._handle_drop_data(context, selection, info, time, target_path)
        else:
            Gtk.drag_finish(context, False, False, time)