import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

import shutil
import urllib.parse
from pathlib import Path
from typing import Optional


class DnDMixin:

    @staticmethod
    def _algo_clamp(value: float, min_val: float, max_val: float) -> float:
        return max(min_val, min(value, max_val))

    @staticmethod
    def _algo_unique_path(path: Path, limit: int = 99) -> Path:
        if not path.exists():
            return path
            
        stem, ext, parent = path.stem, path.suffix, path.parent
        for i in range(1, limit + 1):
            candidate = parent / f"{stem} ({i}){ext}"
            if not candidate.exists():
                return candidate
        return path

    @staticmethod
    def _algo_validate_drop(src: Path, dst_dir: Path) -> Optional[str]:
        sr, dr = src.resolve(), dst_dir.resolve()
        
        if src.parent.resolve() == dr:
            return None 
            
        if sr == dr:
            return "Cannot move into itself"
            
        try:
            dr.relative_to(sr)
            return "Cannot move into subfolder"
        except ValueError:
            return None

    @staticmethod
    def _algo_parse_uri(uri: str) -> Optional[Path]:
        try:
            return Path(urllib.parse.unquote(uri[7:])) if uri.startswith("file://") else Path(uri)
        except Exception:
            return None

    @staticmethod
    def _algo_is_copy(state: Gdk.ModifierType) -> bool:
        return bool(state & Gdk.ModifierType.CONTROL_MASK)

    @staticmethod
    def _algo_action(is_copy: bool) -> str:
        return "Copy" if is_copy else "Move"

    @staticmethod
    def _algo_actioned(is_copy: bool) -> str:
        return "Copied" if is_copy else "Moved"

    def _timer_set(self, attr_name: str, ms: int, callback):
        self._timer_kill(attr_name)
        setattr(self, attr_name, GLib.timeout_add(ms, callback))

    def _timer_kill(self, attr_name: str):
        tid = getattr(self, attr_name, None)
        if tid:
            GLib.source_remove(tid)
            setattr(self, attr_name, None)

    def _setup_activator_drop_target(self):
        self.activator.drag_dest_set(
            Gtk.DestDefaults.MOTION, self._dnd_targets,
            Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        self.activator.connect("drag-motion", self._h_act_motion)
        self.activator.connect("drag-leave", self._h_act_leave)

    def _setup_explorer_drop_tracking(self):
        self.explorer_eb.drag_dest_set(
            Gtk.DestDefaults.MOTION, self._dnd_targets,
            Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        self.explorer_eb.connect("drag-motion", self._h_exp_motion)
        self.explorer_eb.connect("drag-leave", self._h_exp_leave)

    def _setup_drag_source(self, widget: Gtk.Widget, path: Path):
        widget.drag_source_set(
            Gdk.ModifierType.BUTTON1_MASK, self._dnd_targets,
            Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        widget.connect("drag-begin", self._h_src_begin, path)
        widget.connect("drag-data-get", self._h_src_data_get, path)
        widget.connect("drag-end", self._h_src_end)
        widget.connect("drag-failed", self._h_src_failed)

    def _setup_drop_target(self, widget: Gtk.Widget, target_path: Optional[Path] = None):
        widget.drag_dest_set(
            Gtk.DestDefaults.MOTION | Gtk.DestDefaults.HIGHLIGHT | Gtk.DestDefaults.DROP,
            self._dnd_targets,
            Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        widget.connect("drag-motion", self._h_dst_motion, target_path)
        widget.connect("drag-leave", self._h_dst_leave, target_path)
        widget.connect("drag-drop", self._h_dst_drop, target_path)
        widget.connect("drag-data-received", self._h_dst_recv, target_path)

    def _setup_path_part_as_drop_target(self, widget: Gtk.Widget, path: Path):
        widget.drag_dest_set(
            Gtk.DestDefaults.MOTION | Gtk.DestDefaults.HIGHLIGHT | Gtk.DestDefaults.DROP,
            self._dnd_targets,
            Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        widget._drop_path = path
        widget.connect("drag-motion", self._h_pb_motion)
        widget.connect("drag-leave", self._h_pb_leave)
        widget.connect("drag-drop", self._h_pb_drop)
        widget.connect("drag-data-received", self._h_pb_recv)

    def _scroll_update(self, file_view_y: float):
        try:
            alloc = self.file_view.get_allocation()
            height = alloc.height
            margin = self.DRAG_SCROLL_MARGIN
            
            speed = 0.0
            if file_view_y < margin:
                speed = -self.DRAG_SCROLL_SPEED_FAST if file_view_y < (margin / 2) else -self.DRAG_SCROLL_SPEED_SLOW
            elif file_view_y > (height - margin):
                speed = self.DRAG_SCROLL_SPEED_FAST if (height - file_view_y) < (margin / 2) else self.DRAG_SCROLL_SPEED_SLOW

            if speed != self._drag_scroll_speed:
                self._drag_scroll_speed = speed
                if speed != 0 and self._drag_scroll_timer is None:
                    self._timer_set("_drag_scroll_timer", self.DRAG_SCROLL_INTERVAL, self._scroll_tick)
                elif speed == 0:
                    self._scroll_stop()
        except Exception:
            pass

    def _scroll_tick(self) -> bool:
        if not self._drag_scroll_speed:
            self._drag_scroll_timer = None
            return False
            
        try:
            adj = self.file_view.get_vadjustment()
            if adj:
                new_val = self._algo_clamp(
                    adj.get_value() + self._drag_scroll_speed,
                    adj.get_lower(),
                    adj.get_upper() - adj.get_page_size()
                )
                adj.set_value(new_val)
                return True
        except Exception:
            pass
            
        self._drag_scroll_timer = None
        return False

    def _scroll_stop(self):
        self._timer_kill("_drag_scroll_timer")
        self._drag_scroll_speed = 0.0

    def _scroll_from_widget(self, widget: Gtk.Widget, x: int, y: int):
        if widget == self.file_view:
            self._scroll_update(y)
            return
            
        try:
            ok, _, file_view_y = widget.translate_coordinates(self.file_view, x, y)
            if ok:
                self._scroll_update(file_view_y)
        except Exception:
            pass

    def _hover_start(self, path: Path, widget: Gtk.Widget = None):
        if path == self._current_path or path == self._drag_hover_path or path == self._drag_source_path:
            return
            
        self._hover_cancel()
        self._drag_hover_path = path
        self._drag_hover_widget = widget
        
        if widget:
            widget.get_style_context().add_class("drag-hover-pending")
            
        self._timer_set("_drag_hover_timer", self.DRAG_HOVER_OPEN_DELAY, self._hover_fire)

    def _hover_cancel(self):
        self._timer_kill("_drag_hover_timer")
        if self._drag_hover_widget:
            try:
                self._drag_hover_widget.get_style_context().remove_class("drag-hover-pending")
            except Exception:
                pass
        self._drag_hover_widget = None
        self._drag_hover_path = None

    def _hover_fire(self) -> bool:
        self._drag_hover_timer = None
        path, widget = self._drag_hover_path, self._drag_hover_widget
        
        self._hover_cancel()

        if not path or not path.is_dir() or path == self._current_path:
            return False

        if self._drag_in_progress and self._drag_source_path and self._drag_source_path.exists():
            self._pending_enter(self._drag_source_path)

        self._navigate_to(path)
        return False

    def _pending_enter(self, source: Path):
        self._pending_drop_source = source
        self._pending_drop_target = None
        self.file_view.get_style_context().add_class("drag-over")
        self._vis_indicator(f"Release to drop in {self._current_path.name or 'here'}", is_copy=False)

    def _pending_update(self, rx: int, ry: int, state: Gdk.ModifierType):
        if not self._pending_drop_source:
            return
            
        folder = self._find_folder_at_position(rx, ry)
        is_copy = self._algo_is_copy(state)
        word = self._algo_action(is_copy).lower()
        self._vis_indicator_action(is_copy)

        if folder and folder != self._current_path:
            self._pending_drop_target = folder
            self.dnd_indicator.set_label(f"Release to {word} to {folder.name}")
            
            if self._pending_hover_path != folder:
                self._pending_hover_start(folder)
            self._vis_highlight(folder)
        else:
            self._pending_drop_target = None
            self._pending_hover_cancel()
            self._vis_clear_highlights()
            self.dnd_indicator.set_label(f"Release to {word} to {self._current_path.name or 'here'}")

    def _pending_execute(self, is_copy: bool):
        if not self._pending_drop_source:
            return
            
        src = self._pending_drop_source
        dst = self._pending_drop_target or self._current_path
        self._pending_cleanup()
        self._fileop_single(src, dst, is_copy)

    def _pending_cleanup(self):
        self._pending_drop_source = None
        self._pending_drop_target = None
        self._pending_hover_cancel()
        self._vis_clear_highlights()
        self.file_view.get_style_context().remove_class("drag-over")
        self._vis_indicator_clear()
        self._drag_in_progress = False
        self._drag_over_explorer = False
        self._cursor_inside = True
        self._grace_start()

    def _pending_hover_start(self, path: Path):
        self._pending_hover_cancel()
        self._pending_hover_path = path
        self._timer_set("_pending_hover_timer", self.DRAG_HOVER_OPEN_DELAY, self._pending_hover_fire)

    def _pending_hover_cancel(self):
        self._timer_kill("_pending_hover_timer")
        self._pending_hover_path = None

    def _pending_hover_fire(self) -> bool:
        self._pending_hover_timer = None
        path = self._pending_hover_path
        self._pending_hover_path = None
        
        if not path or not path.is_dir() or path == self._current_path or not self._pending_drop_source:
            return False
            
        self._vis_clear_highlights()
        self._navigate_to(path)
        self.dnd_indicator.set_label(f"Release to drop in {self._current_path.name or 'here'}")
        self._pending_drop_target = None
        return False

    def _grace_start(self):
        self._grace_cancel()
        self._post_drag_grace = True
        self._timer_set("_post_drag_timer", self.POST_DRAG_GRACE_PERIOD, self._grace_end)

    def _grace_cancel(self):
        self._timer_kill("_post_drag_timer")
        self._post_drag_grace = False

    def _grace_end(self) -> bool:
        self._post_drag_timer = None
        self._post_drag_grace = False
        
        if self._should_stay_visible():
            return False
            
        if self._is_cursor_over_explorer():
            self._cursor_inside = True
            
        if not self._cursor_inside:
            self._schedule_hide()
            
        return False

    def _vis_indicator(self, text: str, is_copy: bool = False):
        self.dnd_indicator.set_label(text)
        self.dnd_indicator.get_style_context().add_class("active")
        self._vis_indicator_action(is_copy)

    def _vis_indicator_action(self, is_copy: bool):
        ctx = self.dnd_indicator.get_style_context()
        ctx.remove_class("copy" if not is_copy else "move")
        ctx.add_class("copy" if is_copy else "move")

    def _vis_indicator_clear(self):
        self.dnd_indicator.set_label("")
        ctx = self.dnd_indicator.get_style_context()
        for cls in ("active", "copy", "move"):
            ctx.remove_class(cls)

    def _vis_highlight(self, path: Path):
        for widget, widget_path in self._folder_widgets:
            try:
                if widget_path == path:
                    widget.get_style_context().add_class("drag-over")
                else:
                    widget.get_style_context().remove_class("drag-over")
            except Exception:
                pass

    def _vis_clear_highlights(self):
        for widget, _ in self._folder_widgets:
            try:
                widget.get_style_context().remove_class("drag-over")
            except Exception:
                pass

    def _vis_drag_icon(self, context: Gdk.DragContext, path: Path):
        sz = 48
        pb = None
        try:
            icon_name = self._get_icon_for_path(path).replace("-symbolic", "")
            pb = self._icon_theme.load_icon(icon_name, sz, 0)
        except Exception:
            pass
            
        if not pb:
            try:
                fallback = "folder" if path.is_dir() else "text-x-generic"
                pb = self._icon_theme.load_icon(fallback, sz, 0)
            except Exception:
                pass
                
        if pb:
            Gtk.drag_set_icon_pixbuf(context, pb, sz // 2, sz // 2)
        else:
            Gtk.drag_set_icon_default(context)

    def _fileop_single(self, src: Path, dst_dir: Path, is_copy: bool):
        if not is_copy and src.parent.resolve() == dst_dir.resolve():
            return
            
        err = self._algo_validate_drop(src, dst_dir)
        if err:
            self.status_label.set_label(err)
            return
            
        dst = self._algo_unique_path(dst_dir / src.name)
        try:
            if is_copy:
                shutil.copytree(str(src), str(dst)) if src.is_dir() else shutil.copy2(str(src), str(dst))
            else:
                shutil.move(str(src), str(dst))
            self.status_label.set_label(f"{self._algo_actioned(is_copy)}: {src.name}")
        except Exception as e:
            self.status_label.set_label(f"Error: {e}")

    def _fileop_uris(self, context: Gdk.DragContext, selection: Gtk.SelectionData, info: int, time: int, dest_dir: Path):
        if not dest_dir or not dest_dir.is_dir():
            Gtk.drag_finish(context, False, False, time)
            return

        uris = selection.get_uris()
        if not uris:
            text = selection.get_text()
            if text:
                t = text.strip()
                uris = [t if t.startswith("file://") else Path(t).as_uri()]
                
        if not uris:
            Gtk.drag_finish(context, False, False, time)
            return

        is_move = context.get_selected_action() == Gdk.DragAction.MOVE
        done = 0
        
        for uri in uris:
            src = self._algo_parse_uri(uri)
            if not src or not src.exists():
                continue
                
            if is_move and src.parent.resolve() == dest_dir.resolve():
                continue
                
            dst = self._algo_unique_path(dest_dir / src.name)
            if src == dst:
                continue
                
            try:
                if is_move:
                    shutil.move(str(src), str(dst))
                else:
                    shutil.copytree(str(src), str(dst)) if src.is_dir() else shutil.copy2(str(src), str(dst))
                done += 1
            except Exception as e:
                print(f"DnD error: {e}")

        Gtk.drag_finish(context, done > 0, is_move and done > 0, time)
        if done:
            self.status_label.set_label(f"{self._algo_actioned(not is_move)} {done} item(s)")

    def _vis_maybe_hide(self) -> bool:
        if not self._should_stay_visible() and not self._drag_over_explorer and not self._is_cursor_over_explorer():
            if not self._cursor_inside:
                self._schedule_hide()
        return False

    def _h_act_motion(self, widget, context, x, y, time) -> bool:
        self._cancel_pending_hide()
        self._cancel_activator_hover_timer()
        self._drag_over_explorer = True
        
        if not self.revealer.get_child_revealed():
            self.revealer.set_reveal_child(True)
            
        Gdk.drag_status(context, Gdk.DragAction.COPY, time)
        return True

    def _h_act_leave(self, widget, context, time):
        GLib.timeout_add(50, self._vis_maybe_hide)

    def _h_exp_motion(self, widget, context, x, y, time) -> bool:
        self._cancel_pending_hide()
        self._drag_over_explorer = True
        self._scroll_from_widget(widget, x, y)
        return False

    def _h_exp_leave(self, widget, context, time):
        self._drag_over_explorer = False
        self._scroll_stop()
        GLib.timeout_add(100, self._vis_maybe_hide)

    def _h_src_begin(self, widget, context, path: Path):
        self._drag_source_path = path
        self._drag_in_progress = True
        self._drag_over_explorer = True
        self._cancel_pending_hide()
        self._grace_cancel()
        
        self._vis_drag_icon(context, path)
        widget.get_style_context().add_class("dragging")
        self._vis_indicator(f"Dragging: {path.name}")

    def _h_src_data_get(self, widget, context, selection, info, time, path: Path):
        if info == self.TARGET_URI_LIST:
            selection.set_uris([path.as_uri()])
        else:
            selection.set_text(str(path), -1)

    def _h_src_end(self, widget, context):
        widget.get_style_context().remove_class("dragging")
        self._scroll_stop()
        
        if self._pending_drop_source:
            self._drag_in_progress = False
            return
            
        self._drag_source_path = None
        self._drag_in_progress = False
        self._drag_over_explorer = False
        self._vis_indicator_clear()
        self._hover_cancel()
        
        self._cursor_inside = True 
        self._grace_start()

    def _h_src_failed(self, widget, context, result) -> bool:
        self._h_src_end(widget, context)
        return False

    def _h_dst_motion(self, widget, context, x, y, time, target_path: Optional[Path] = None) -> bool:
        self._cancel_pending_hide()
        self._drag_over_explorer = True
        widget.get_style_context().add_class("drag-over")
        self._scroll_from_widget(widget, x, y)

        if target_path and target_path.is_dir() and self._drag_hover_path != target_path:
            self._hover_start(target_path, widget)
        elif not target_path or not target_path.is_dir():
            self._hover_cancel()

        state = Gdk.Keymap.get_default().get_modifier_state()
        is_copy = self._algo_is_copy(state)
        Gdk.drag_status(context, Gdk.DragAction.COPY if is_copy else Gdk.DragAction.MOVE, time)

        name = (target_path.name if target_path else self._current_path.name) or "Root"
        self._vis_indicator(f"{self._algo_action(is_copy)} to {name}", is_copy)
        return True

    def _h_dst_leave(self, widget, context, time, target_path: Optional[Path] = None):
        widget.get_style_context().remove_class("drag-over")
        widget.get_style_context().remove_class("drag-hover-pending")
        
        if target_path and self._drag_hover_path == target_path:
            self._hover_cancel()

    def _h_dst_drop(self, widget, context, x, y, time, target_path: Optional[Path] = None) -> bool:
        self._hover_cancel()
        self._scroll_stop()
        
        target = widget.drag_dest_find_target(context, None)
        if target:
            widget.drag_get_data(context, target, time)
            return True
        return False

    def _h_dst_recv(self, widget, context, x, y, selection, info, time, target_path: Optional[Path] = None):
        widget.get_style_context().remove_class("drag-over")
        self._hover_cancel()
        self._drag_over_explorer = False
        
        self._fileop_uris(context, selection, info, time, target_path or self._current_path)
        
        self._cursor_inside = True 
        self._grace_start()

    def _h_pb_motion(self, widget, context, x, y, time) -> bool:
        path = getattr(widget, "_drop_path", None)
        self._cancel_pending_hide()
        self._drag_over_explorer = True
        widget.get_style_context().add_class("drag-over")

        if path and path.is_dir() and self._drag_hover_path != path:
            self._hover_start(path, widget)

        state = Gdk.Keymap.get_default().get_modifier_state()
        is_copy = self._algo_is_copy(state)
        Gdk.drag_status(context, Gdk.DragAction.COPY if is_copy else Gdk.DragAction.MOVE, time)
        
        self._vis_indicator(f"{self._algo_action(is_copy)} to {path.name or 'Root'}", is_copy)
        return True

    def _h_pb_leave(self, widget, context, time):
        widget.get_style_context().remove_class("drag-over")
        widget.get_style_context().remove_class("drag-hover-pending")
        
        if getattr(widget, "_drop_path", None) == self._drag_hover_path:
            self._hover_cancel()

    def _h_pb_drop(self, widget, context, x, y, time) -> bool:
        self._hover_cancel()
        target = widget.drag_dest_find_target(context, None)
        if target:
            widget.drag_get_data(context, target, time)
            return True
        return False

    def _h_pb_recv(self, widget, context, x, y, selection, info, time):
        path = getattr(widget, "_drop_path", None)
        widget.get_style_context().remove_class("drag-over")
        self._hover_cancel()
        self._drag_over_explorer = False
        
        if path:
            self._fileop_uris(context, selection, info, time, path)
        else:
            Gtk.drag_finish(context, False, False, time)
            
        self._cursor_inside = True 
        self._grace_start()

    def _on_explorer_motion(self, widget, event) -> bool:
        self._cursor_inside = True
        self._cancel_pending_hide()

        if not self._pending_drop_source:
            return False

        state = event.state[1] if isinstance(event.state, tuple) else event.state

        if not (state & Gdk.ModifierType.BUTTON1_MASK):
            self._pending_execute(self._algo_is_copy(state))
            return True

        self._pending_update(event.x_root, event.y_root, state)
        self._scroll_from_widget(widget, int(event.x), int(event.y))
        return True

    def _on_explorer_button_release(self, widget, event) -> bool:
        if event.button == 1 and self._pending_drop_source:
            is_copy = self._algo_is_copy(event.state)
            folder = self._find_folder_at_position(event.x_root, event.y_root)
            
            if folder and folder != self._current_path:
                self._pending_drop_target = folder
                
            self._pending_execute(is_copy)
            return True
        return False