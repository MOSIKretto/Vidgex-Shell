from fabric.notifications.service import Notifications
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer
from fabric.widgets.scrolledwindow import ScrolledWindow

from gi.repository import GLib, Gtk

import json
import locale
import os
import weakref
from datetime import datetime, timedelta

from .notification_box import (
    NotificationBox, cache_notification_pixbuf, load_scaled_pixbuf, 
    get_history_ignored_apps, PERSISTENT_HISTORY_FILE, MAX_NOTIFICATION_HISTORY, 
    MAX_POPUP_NOTIFICATIONS, PERSISTENT_DIR, MAX_CACHED_IMAGES, HistoricalNotification)
import modules.icons as icons
from widgets.image import CustomImage


class NotificationHistory(Box):
    def __init__(self, **kwargs):
        super().__init__(name="notification-history", orientation="v", **kwargs)

        self.containers = []
        self._cleanup_timer_id = None
        
        self.header_label = Label(name="nhh", label="Notifications", h_align="start", h_expand=True)
        self.header_switch = Gtk.Switch(name="dnd-switch")
        self.header_switch.set_vexpand(False)
        self.header_switch.set_valign(Gtk.Align.CENTER)
        self.header_switch.set_active(False)
        
        self.header_clean = Button(
            name="nhh-button",
            child=Label(name="nhh-button-label", markup=icons.trash),
            on_clicked=self.clear_history,
        )
        
        self.do_not_disturb_enabled = False
        self._dnd_handler = self.header_switch.connect("notify::active", self.on_do_not_disturb_changed)
        self.dnd_label = Label(name="dnd-label", markup=icons.notifications_off)

        self.history_header = CenterBox(
            name="notification-history-header",
            spacing=8,
            start_children=[self.header_switch, self.dnd_label],
            center_children=[self.header_label],
            end_children=[self.header_clean],
        )
        
        self.notifications_list = Box(
            name="notifications-list",
            orientation="v",
            spacing=4,
            h_expand=True,
            v_expand=True,
            h_align="fill",
            v_align="fill",
        )
        
        self.no_notifications_label = Label(
            name="no-notif",
            markup=icons.notifications_clear,
            v_align="fill",
            h_align="fill",
            v_expand=True,
            h_expand=True,
            justification="center",
        )
        
        self.no_notifications_box = Box(
            name="no-notifications-box",
            v_align="fill",
            h_align="fill",
            v_expand=True,
            h_expand=True,
            children=[self.no_notifications_label],
        )
        
        self.scrolled_window = ScrolledWindow(
            name="notification-history-scrolled-window",
            orientation="v",
            h_expand=True,
            v_expand=True,
            h_align="fill",
            v_align="fill",
            propagate_width=False,
            propagate_height=False,
        )
        
        self.scrolled_window_viewport_box = Box(orientation="v", children=[self.notifications_list, self.no_notifications_box])
        self.scrolled_window.add_with_viewport(self.scrolled_window_viewport_box)
        self.persistent_notifications = []
        
        self.add(self.history_header)
        self.add(self.scrolled_window)
        
        GLib.idle_add(self._load_persistent_history().__next__)
        
        self._cleanup_timer_id = GLib.timeout_add_seconds(300, self._periodic_cleanup)

    def get_ordinal(self, n):
        remainder = n % 100
        if 11 <= remainder <= 13:
            return "th"
        
        last_digit = n % 10
        if last_digit == 1: return "st"
        elif last_digit == 2: return "nd"
        elif last_digit == 3: return "rd"
        else: return "th"

    def get_date_header(self, dt):
        now = datetime.now()
        today = now.date()
        date = dt.date()
        
        if date == today: return "Today"
        elif date == today - timedelta(days=1): return "Yesterday"
        else:
            original_locale = locale.getlocale(locale.LC_TIME)
            try: locale.setlocale(locale.LC_TIME, ("en_US", "UTF-8"))
            except locale.Error: locale.setlocale(locale.LC_TIME, "C")
            
            try:
                day = dt.day
                ordinal = self.get_ordinal(day)
                month = dt.strftime("%B")
                if dt.year == now.year: result = f"{month} {day}{ordinal}"
                else: result = f"{month} {day}{ordinal}, {dt.year}"
            finally:
                locale.setlocale(locale.LC_TIME, original_locale)
            
            return result

    def schedule_midnight_update(self):
        now = datetime.now()
        next_midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time())
        delta_seconds = (next_midnight - now).total_seconds()
        GLib.timeout_add_seconds(int(delta_seconds), self.on_midnight)

    def on_midnight(self):
        self.rebuild_with_separators()
        self.schedule_midnight_update()
        return GLib.SOURCE_REMOVE

    def create_date_separator(self, date_header):
        return Box(
            name="notif-date-sep",
            children=[Label(name="notif-date-sep-label", label=date_header, h_align="center", h_expand=True)],
        )

    def rebuild_with_separators(self):
        GLib.idle_add(self._do_rebuild_with_separators)

    def _do_rebuild_with_separators(self):
        children = list(self.notifications_list.get_children())
        for child in children:
            self.notifications_list.remove(child)

        current_date_header = None
        sorted_containers = sorted(self.containers, key=lambda x: x.arrival_time, reverse=True)
        
        for container in sorted_containers:
            arrival_time = container.arrival_time
            date_header = self.get_date_header(arrival_time)
            
            if date_header != current_date_header:
                sep = self.create_date_separator(date_header)
                self.notifications_list.add(sep)
                current_date_header = date_header
            
            self.notifications_list.add(container)

        if not self.containers:
            for child in list(self.notifications_list.get_children()):
                if child.get_name() == "notif-date-sep":
                    self.notifications_list.remove(child)

        self.notifications_list.show_all()
        self.update_no_notifications_label_visibility()

    def on_do_not_disturb_changed(self, switch, pspec):
        self.do_not_disturb_enabled = switch.get_active()
        status = "enabled" if self.do_not_disturb_enabled else "disabled"

    def clear_history(self, *args):
        children = self.notifications_list.get_children()[:]
        for child in children:
            container = child
            notif_box = None
            if hasattr(container, "notification_box"):
                notif_box = container.notification_box
            
            if notif_box:
                notif_box.destroy(from_history_delete=True)
            
            self.notifications_list.remove(child)
            child.destroy()

        if os.path.exists(PERSISTENT_HISTORY_FILE):
            os.remove(PERSISTENT_HISTORY_FILE)
        
        self.persistent_notifications = []
        self.containers = []
        self.rebuild_with_separators()

    def _load_persistent_history(self):
        if not os.path.exists(PERSISTENT_DIR):
            os.makedirs(PERSISTENT_DIR, exist_ok=True)
        
        if os.path.exists(PERSISTENT_HISTORY_FILE):
            with open(PERSISTENT_HISTORY_FILE, "r") as f:
                self.persistent_notifications = json.load(f)
            
            for note in reversed(self.persistent_notifications):
                self._add_historical_notification(note)
                yield True
        
        GLib.idle_add(self.update_no_notifications_label_visibility)
        self._cleanup_orphan_cached_images()
        self.schedule_midnight_update()

    def _save_persistent_history(self):
        with open(PERSISTENT_HISTORY_FILE, "w") as f:
            json.dump(self.persistent_notifications, f)

    def delete_historical_notification(self, note_id, container):
        if hasattr(container, "notification_box"):
            notif_box = container.notification_box
            notif_box.destroy(from_history_delete=True)

        target_note_id_str = str(note_id)
        new_persistent_notifications = []
        
        for note_in_list in self.persistent_notifications:
            current_note_id_str = str(note_in_list.get("id"))
            if current_note_id_str != target_note_id_str:
                new_persistent_notifications.append(note_in_list)

        self.persistent_notifications = new_persistent_notifications
        self._save_persistent_history()
        container.destroy()
        
        new_containers = [c for c in self.containers if c != container]
        self.containers = new_containers
        self.rebuild_with_separators()

    def _add_historical_notification(self, note):
        hist_notif = HistoricalNotification(
            id=note.get("id"),
            app_icon=note.get("app_icon"),
            summary=note.get("summary"),
            body=note.get("body"),
            app_name=note.get("app_name"),
            timestamp=note.get("timestamp"),
            cached_image_path=note.get("cached_image_path"),
        )

        hist_box = NotificationBox(hist_notif, timeout_ms=0)
        hist_box.uuid = hist_notif.id
        hist_box.cached_image_path = hist_notif.cached_image_path
        hist_box.set_is_history(True)
        
        for child in hist_box.get_children():
            if child.get_name() == "notification-action-buttons":
                hist_box.remove(child)
        
        container = Box(name="notification-container", orientation="v", h_align="fill", h_expand=True)
        container.notification_box = hist_box
        
        try:
            arrival = datetime.fromisoformat(hist_notif.timestamp)
        except Exception:
            arrival = datetime.now()
        
        container.arrival_time = arrival

        def compute_time_label(arrival_time):
            return arrival_time.strftime("%H:%M")

        self.hist_time_label = Label(name="notification-timestamp", markup=compute_time_label(container.arrival_time), h_align="start")
        
        self.hist_notif_image_box = Box(
            name="notification-image",
            orientation="v",
            children=[CustomImage(pixbuf=load_scaled_pixbuf(hist_box, 48, 48)), Box(v_expand=True)],
        )
        
        self.hist_notif_summary_label = Label(name="notification-summary", markup=hist_notif.summary, h_align="start")
        self.hist_notif_app_name_label = Label(name="notification-app-name", markup=f"{hist_notif.app_name}", h_align="start")

        if hist_notif.body:
            self.hist_notif_body_label = Label(name="notification-body", markup=hist_notif.body, h_align="start", line_wrap="word-char")
            self.hist_notif_body_label.set_single_line_mode(True)
        else:
            self.hist_notif_body_label = Box()

        summary_box_children = [
            self.hist_notif_summary_label,
            Box(name="notif-sep", h_expand=False, v_expand=False, h_align="center", v_align="center"),
            self.hist_notif_app_name_label,
            Box(name="notif-sep", h_expand=False, v_expand=False, h_align="center", v_align="center"),
            self.hist_time_label,
        ]
        
        self.hist_notif_summary_box = Box(name="notification-summary-box", orientation="h", children=summary_box_children)
        self.hist_notif_text_box = Box(name="notification-text", orientation="v", v_align="center", h_expand=True, children=[self.hist_notif_summary_box, self.hist_notif_body_label])
        
        self_ref = weakref.ref(self)
        hist_notif_id = hist_notif.id
        
        def on_hist_close(*_):
            self_obj = self_ref()
            if self_obj:
                self_obj.delete_historical_notification(hist_notif_id, container)
        
        self.hist_notif_close_button = Button(
            name="notif-close-button",
            child=Label(name="notif-close-label", markup=icons.cancel),
            on_clicked=on_hist_close,
        )
        
        self.hist_notif_close_button_box = Box(orientation="v", children=[self.hist_notif_close_button, Box(v_expand=True)])
        
        content_box = Box(
            name="notification-box-hist",
            spacing=8,
            children=[self.hist_notif_image_box, self.hist_notif_text_box, self.hist_notif_close_button_box],
        )
        
        container.add(content_box)
        self.containers.insert(0, container)
        self.rebuild_with_separators()
        self.update_no_notifications_label_visibility()

    def add_notification(self, notification_box):
        app_name = notification_box.notification.app_name
        
        if app_name in get_history_ignored_apps():
            notification_box.destroy(from_history_delete=True)
            return
        
        if len(self.containers) >= MAX_NOTIFICATION_HISTORY:
            oldest_container = self.containers.pop()
            if hasattr(oldest_container, "notification_box"):
                if hasattr(oldest_container.notification_box, "cached_image_path"):
                    if oldest_container.notification_box.cached_image_path and os.path.exists(oldest_container.notification_box.cached_image_path):
                        os.remove(oldest_container.notification_box.cached_image_path)

            oldest_container.destroy()

        def on_container_destroy(container):
            if hasattr(container, "_timestamp_timer_id"):
                if container._timestamp_timer_id:
                    try:
                        GLib.source_remove(container._timestamp_timer_id)
                    except:
                        pass
            
            if hasattr(container, "notification_box"):
                notif_box = container.notification_box
                if notif_box:
                    try:
                        notif_box.destroy(from_history_delete=False)
                    except:
                        pass
            
            try:
                container.destroy()
            except:
                pass
            
            self_obj = self_ref()
            if self_obj:
                new_containers = [c for c in self_obj.containers if c != container]
                self_obj.containers = new_containers
                
                self_obj.rebuild_with_separators()
                self_obj.update_no_notifications_label_visibility()

        self_ref = weakref.ref(self)

        container = Box(name="notification-container", orientation="v", h_align="fill", h_expand=True)
        container.arrival_time = datetime.now()

        def compute_time_label(arrival_time):
            return arrival_time.strftime("%H:%M")

        self.current_time_label = Label(name="notification-timestamp", markup=compute_time_label(container.arrival_time))
        
        self.current_notif_image_box = Box(
            name="notification-image",
            orientation="v",
            children=[CustomImage(pixbuf=load_scaled_pixbuf(notification_box, 48, 48)), Box(v_expand=True, v_align="fill")],
        )
        
        self.current_notif_summary_label = Label(name="notification-summary", markup=notification_box.notification.summary, h_align="start")
        self.current_notif_app_name_label = Label(name="notification-app-name", markup=f"{notification_box.notification.app_name}", h_align="start")
        
        if notification_box.notification.body:
            self.current_notif_body_label = Label(name="notification-body", markup=notification_box.notification.body, h_align="start", line_wrap="word-char")
            self.current_notif_body_label.set_single_line_mode(True)
        else:
            self.current_notif_body_label = Box()
        
        summary_box_children = [
            self.current_notif_summary_label,
            Box(name="notif-sep", h_expand=False, v_expand=False, h_align="center", v_align="center"),
            self.current_notif_app_name_label,
            Box(name="notif-sep", h_expand=False, v_expand=False, h_align="center", v_align="center"),
            self.current_time_label,
        ]
        
        self.current_notif_summary_box = Box(name="notification-summary-box", orientation="h", children=summary_box_children)
        self.current_notif_text_box = Box(name="notification-text", orientation="v", v_align="center", h_expand=True, children=[self.current_notif_summary_box, self.current_notif_body_label])
        
        self.current_notif_close_button = Button(
            name="notif-close-button",
            child=Label(name="notif-close-label", markup=icons.cancel),
        )
        self.current_notif_close_button.connect("clicked", lambda *_: on_container_destroy(container))
        
        self.current_notif_close_button_box = Box(orientation="v", children=[self.current_notif_close_button, Box(v_expand=True)])
        
        content_box = Box(
            name="notification-content",
            spacing=8,
            children=[self.current_notif_image_box, self.current_notif_text_box, self.current_notif_close_button_box],
        )
        
        container.notification_box = notification_box
        hist_box = Box(name="notification-box-hist", orientation="v", h_align="fill", h_expand=True)
        hist_box.add(content_box)
        
        container.add(hist_box)
        self.containers.insert(0, container)
        self.rebuild_with_separators()
        self._append_persistent_notification(notification_box, container.arrival_time)
        self.update_no_notifications_label_visibility()

    def _append_persistent_notification(self, notification_box, arrival_time):
        note = {
            "id": notification_box.uuid,
            "app_icon": notification_box.notification.app_icon,
            "summary": notification_box.notification.summary,
            "body": notification_box.notification.body,
            "app_name": notification_box.notification.app_name,
            "timestamp": arrival_time.isoformat(),
            "cached_image_path": notification_box.cached_image_path,
        }
        
        self.persistent_notifications.insert(0, note)
        
        # Limit persistent history to MAX_NOTIFICATION_HISTORY
        if len(self.persistent_notifications) > MAX_NOTIFICATION_HISTORY:
            self.persistent_notifications = self.persistent_notifications[:MAX_NOTIFICATION_HISTORY]
        
        self._save_persistent_history()
        self._cleanup_old_cached_images()

    def _cleanup_orphan_cached_images(self):
        if not os.path.exists(PERSISTENT_DIR):
            return

        cached_files = []
        for f in os.listdir(PERSISTENT_DIR):
            if f.startswith("notification_") and f.endswith(".png"):
                cached_files.append(f)
        
        if not cached_files:
            return

        history_uuids = set()
        for note in self.persistent_notifications:
            note_id = note.get("id")
            if note_id:
                history_uuids.add(note_id)
        
        for cached_file in cached_files:
            uuid_from_filename = cached_file[len("notification_") : -len(".png")]
            if uuid_from_filename not in history_uuids:
                cache_file_path = os.path.join(PERSISTENT_DIR, cached_file)
                os.remove(cache_file_path)
    
    def _cleanup_old_cached_images(self):
        """Limit cached images to MAX_CACHED_IMAGES"""
        if not os.path.exists(PERSISTENT_DIR):
            return
        
        cached_files = []
        for f in os.listdir(PERSISTENT_DIR):
            if f.startswith("notification_") and f.endswith(".png"):
                file_path = os.path.join(PERSISTENT_DIR, f)
                mtime = os.path.getmtime(file_path)
                cached_files.append((file_path, mtime))
        
        # Sort by modification time (oldest first)
        cached_files.sort(key=lambda x: x[1])
        
        # Remove oldest files if we exceed MAX_CACHED_IMAGES
        if len(cached_files) > MAX_CACHED_IMAGES:
            files_to_remove = cached_files[:len(cached_files) - MAX_CACHED_IMAGES]
            for file_path, _ in files_to_remove:
                os.remove(file_path)
                    
    
    def _periodic_cleanup(self):
        """Periodic cleanup of old cached images"""
        self._cleanup_old_cached_images()
        return True  # Continue timer
    
    def destroy(self):
        """Clean up resources on destroy"""
        if self._cleanup_timer_id:
            GLib.source_remove(self._cleanup_timer_id)
            self._cleanup_timer_id = None
        
        if hasattr(self, '_dnd_handler') and self._dnd_handler:
            try:
                self.header_switch.disconnect(self._dnd_handler)
            except:
                pass
        
        super().destroy()

    def update_no_notifications_label_visibility(self):
        has_notifications = bool(self.containers)
        self.no_notifications_box.set_visible(not has_notifications)
        self.notifications_list.set_visible(has_notifications)

    def clear_history_for_app(self, app_name):
        containers_to_remove = []
        persistent_notes_to_remove_ids = set()
        
        for container in list(self.containers):
            if hasattr(container, "notification_box"):
                if container.notification_box.notification.app_name == app_name:
                    containers_to_remove.append(container)
                    persistent_notes_to_remove_ids.add(container.notification_box.uuid)

        for container in containers_to_remove:
            if hasattr(container, "notification_box"):
                if hasattr(container.notification_box, "cached_image_path"):
                    if container.notification_box.cached_image_path and os.path.exists(container.notification_box.cached_image_path):
                        os.remove(container.notification_box.cached_image_path)
            
            self.containers.remove(container)
            self.notifications_list.remove(container)
            container.notification_box.destroy(from_history_delete=True)
            container.destroy()

        new_persistent_notifications = [note for note in self.persistent_notifications if note.get("id") not in persistent_notes_to_remove_ids]
        self.persistent_notifications = new_persistent_notifications
        self._save_persistent_history()
        self.rebuild_with_separators()
        self.update_no_notifications_label_visibility()


class NotificationContainer(Box):
    def __init__(self, notification_history_instance: NotificationHistory, revealer_transition_type: str = "slide-down"):
        super().__init__(name="notification-container-main", orientation="v", spacing=4)
        self.notification_history = notification_history_instance

        self._server = Notifications()
        self._server.connect("notification-added", self.on_new_notification)
        self._pending_removal = False
        self._is_destroying = False

        self.stack = Gtk.Stack(
            name="notification-stack",
            transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT,
            transition_duration=200,
            visible=True,
        )
        
        self.navigation = Box(name="notification-navigation", spacing=4, h_align="center")
        self.stack_box = Box(name="notification-stack-box", h_align="center", h_expand=False, children=[self.stack])
        
        self.prev_button = Button(
            name="nav-button",
            child=Label(name="nav-button-label", markup=icons.chevron_left),
            on_clicked=self.show_previous,
        )
        
        self.close_all_button = Button(
            name="nav-button",
            child=Label(name="nav-button-label", markup=icons.cancel),
            on_clicked=self.close_all_notifications,
        )
        
        self.close_all_button_label = self.close_all_button.get_child()
        self.close_all_button_label.add_style_class("close")
        
        self.next_button = Button(
            name="nav-button",
            child=Label(name="nav-button-label", markup=icons.chevron_right),
            on_clicked=self.show_next,
        )
        
        for button in [self.prev_button, self.close_all_button, self.next_button]:
            button.connect("enter-notify-event", lambda *_: self.pause_and_reset_all_timeouts())
            button.connect("leave-notify-event", lambda *_: self.resume_all_timeouts())
        
        self.navigation.add(self.prev_button)
        self.navigation.add(self.close_all_button)
        self.navigation.add(self.next_button)

        self.navigation_revealer = Revealer(
            transition_type="slide-down",
            transition_duration=200,
            child=self.navigation,
            reveal_child=False,
        )

        self.notification_box_container = Box(
            name="notification-box-internal-container",
            orientation="v",
            children=[self.stack_box, self.navigation_revealer],
        )

        self.main_revealer = Revealer(
            name="notification-main-revealer",
            transition_type=revealer_transition_type,
            transition_duration=250,
            child_revealed=False,
            child=self.notification_box_container,
        )

        self.add(self.main_revealer)

        self.notifications = []
        self.current_index = 0
        self.update_navigation_buttons()
        self._destroyed_notifications = set()

    def on_new_notification(self, fabric_notif, id):
        notification_history_instance = self.notification_history
        
        if notification_history_instance.do_not_disturb_enabled:
            notification = fabric_notif.get_notification_from_id(id)
            new_box = NotificationBox(notification)
            if notification.image_pixbuf:
                cache_notification_pixbuf(new_box)
            notification_history_instance.add_notification(new_box)
            return

        notification = fabric_notif.get_notification_from_id(id)
        new_box = NotificationBox(notification)
        new_box.set_container(self)
        notification.connect("closed", self.on_notification_closed)

        app_name = notification.app_name
        
        # Limit popup notifications to MAX_POPUP_NOTIFICATIONS (3 instead of 5)
        while len(self.notifications) >= MAX_POPUP_NOTIFICATIONS:
            oldest_notification = self.notifications[0]
            notification_history_instance.add_notification(oldest_notification)
            self.stack.remove(oldest_notification)
            self.notifications.pop(0)
            if self.current_index > 0:
                self.current_index -= 1
        
        self.stack.add_named(new_box, str(id))
        self.notifications.append(new_box)
        self.current_index = len(self.notifications) - 1
        self.stack.set_visible_child(new_box)

        for notification_box in self.notifications:
            notification_box.start_timeout()
        
        self.main_revealer.show_all()
        self.main_revealer.set_reveal_child(True)
        self.update_navigation_buttons()

    def show_previous(self, *args):
        if self.current_index > 0:
            self.current_index -= 1
            self.stack.set_visible_child(self.notifications[self.current_index])
            self.update_navigation_buttons()

    def show_next(self, *args):
        if self.current_index < len(self.notifications) - 1:
            self.current_index += 1
            self.stack.set_visible_child(self.notifications[self.current_index])
            self.update_navigation_buttons()

    def update_navigation_buttons(self):
        self.prev_button.set_sensitive(self.current_index > 0)
        self.next_button.set_sensitive(self.current_index < len(self.notifications) - 1)
        should_reveal = len(self.notifications) > 1
        self.navigation_revealer.set_reveal_child(should_reveal)

    def on_notification_closed(self, notification, reason):
        if self._is_destroying:
            return
        
        if notification.id in self._destroyed_notifications:
            return
        
        self._destroyed_notifications.add(notification.id)
        
        notif_to_remove = None
        
        for i, notif_box in enumerate(self.notifications):
            if notif_box.notification.id == notification.id:
                notif_to_remove = (i, notif_box)
                break
        
        if not notif_to_remove:
            return
        
        i, notif_box = notif_to_remove
        reason_str = str(reason)
        notification_history_instance = self.notification_history

        if reason_str == "NotificationCloseReason.DISMISSED_BY_USER":
            notif_box.destroy()
        else:
            notif_box.set_is_history(True)
            notification_history_instance.add_notification(notif_box)
            notif_box.stop_timeout()

        new_index = i
        if i == self.current_index:
            new_index = max(0, i - 1)
        elif i < self.current_index:
            new_index = self.current_index - 1

        if notif_box.get_parent() == self.stack:
            self.stack.remove(notif_box)
        
        self.notifications.pop(i)

        if new_index >= len(self.notifications):
            if len(self.notifications) > 0:
                new_index = len(self.notifications) - 1

        self.current_index = new_index
        
        if not self.notifications:
            self._is_destroying = True
            self.main_revealer.set_reveal_child(False)
            self._destroy_container()
            return
        else:
            self.stack.set_visible_child(self.notifications[self.current_index])

        self.update_navigation_buttons()

    def _destroy_container(self):
        try:
            self.notifications.clear()
            self._destroyed_notifications.clear()
            
            children = self.stack.get_children()
            for child in children:
                self.stack.remove(child)
                child.destroy()
            
            self.current_index = 0
        finally:
            self._is_destroying = False

    def pause_and_reset_all_timeouts(self):
        if self._is_destroying:
            return
        
        for notification in self.notifications[:]:
            if not notification._destroyed and notification.get_parent():
                notification.stop_timeout()            

    def resume_all_timeouts(self):
        if self._is_destroying:
            return
        
        for notification in self.notifications[:]:
            if not notification._destroyed and notification.get_parent():
                notification.start_timeout()

    def close_all_notifications(self, *args):
        notifications_to_close = self.notifications.copy()
        for notification_box in notifications_to_close:
            notification_box.notification.close("dismissed-by-user")