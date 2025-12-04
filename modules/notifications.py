from fabric.notifications.service import Notification, NotificationAction, Notifications
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer
from fabric.widgets.scrolledwindow import ScrolledWindow

from gi.repository import GdkPixbuf, GLib, Gtk

from loguru import logger
import json
import locale
import os
import uuid
from datetime import datetime, timedelta

import modules.icons as icons
from widgets.image import CustomImage
from widgets.wayland import WaylandWindow as Window


PERSISTENT_DIR = f"/tmp/ax-shell/notifications"
PERSISTENT_HISTORY_FILE = os.path.join(PERSISTENT_DIR, "notification_history.json")

# Performance optimization constants
MAX_NOTIFICATION_HISTORY = 10  # Reduced from 25 to save memory
MAX_CACHED_IMAGES = 10  # Limit cached notification images
MAX_POPUP_NOTIFICATIONS = 3  # Reduced from 5 to save memory


def get_limited_apps_history():
    return []


def get_history_ignored_apps():
    return []


def cache_notification_pixbuf(notification_box):
    notification = notification_box.notification
    if not notification.image_pixbuf:
        logger.debug(f"Notification {notification.id} has no image_pixbuf to cache.")
        return None

    os.makedirs(PERSISTENT_DIR, exist_ok=True)
    cache_file = os.path.join(PERSISTENT_DIR, f"notification_{notification_box.uuid}.png")
    
    try:
        scaled = notification.image_pixbuf.scale_simple(48, 48, GdkPixbuf.InterpType.BILINEAR)
        scaled.savev(cache_file, "png", [], [])
        logger.info(f"Cached image for notification {notification.id}")
        return cache_file
    except Exception as e:
        logger.error(f"Error caching image: {e}")
        return None


def load_scaled_pixbuf(notification_box, width, height):
    notification = notification_box.notification
    if not hasattr(notification_box, "notification") or not notification:
        logger.error("load_scaled_pixbuf: notification_box.notification is invalid!")
        return None

    if hasattr(notification_box, "cached_image_path") and notification_box.cached_image_path:
        if os.path.exists(notification_box.cached_image_path):
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(notification_box.cached_image_path)
                return pixbuf.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
            except Exception as e:
                logger.error(f"Error loading cached image: {e}")

    if notification.image_pixbuf:
        return notification.image_pixbuf.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)

    return get_app_icon_pixbuf(notification.app_icon, width, height)


def get_app_icon_pixbuf(icon_path, width, height):
    if not icon_path:
        return None
    
    if icon_path.startswith("file://"):
        icon_path = icon_path[7:]
    
    if not os.path.exists(icon_path):
        return None
    
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(icon_path)
        return pixbuf.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
    except Exception:
        return None


class ActionButton(Button):
    def __init__(self, action: NotificationAction, index: int, total: int, notification_box):
        button_label = Label(
            name="button-label",
            h_expand=True,
            h_align="fill",
            ellipsization="end",
            max_chars_width=1,
            label=action.label,
        )
        
        super().__init__(
            name="action-button",
            h_expand=True,
            on_clicked=self.on_clicked,
            child=button_label,
        )
        
        self.action = action
        self.notification_box = notification_box
        
        if index == 0:
            style_class = "start-action"
        elif index == total - 1:
            style_class = "end-action"
        else:
            style_class = "middle-action"
        
        self.add_style_class(style_class)
        
        self.connect("enter-notify-event", lambda *_: notification_box.hover_button(self))
        self.connect("leave-notify-event", lambda *_: notification_box.unhover_button(self))

    def on_clicked(self, *_):
        self.action.invoke()
        self.action.parent.close("dismissed-by-user")


class NotificationBox(Box):
    def __init__(self, notification: Notification, timeout_ms=5000, **kwargs):
        super().__init__(
            name="notification-box",
            orientation="v",
            h_align="fill",
            h_expand=True,
            children=[],
        )
        
        self.notification = notification
        self.uuid = str(uuid.uuid4())

        if timeout_ms == 0:
            self.timeout_ms = 0
        else:
            live_timeout = getattr(self.notification, "timeout", -1)
            self.timeout_ms = live_timeout if live_timeout != -1 else timeout_ms
        
        self._timeout_id = None
        self._container = None
        self.cached_image_path = None
        self._hover_enter_handler = None
        self._hover_leave_handler = None

        if self.timeout_ms > 0:
            self.start_timeout()

        if self.notification.image_pixbuf:
            cache_path = cache_notification_pixbuf(self)
            if cache_path:
                self.cached_image_path = cache_path

        content = self.create_content()
        action_buttons = self.create_action_buttons()
        self.add(content)
        if action_buttons:
            self.add(action_buttons)

        self._hover_enter_handler = self.connect("enter-notify-event", self.on_hover_enter)
        self._hover_leave_handler = self.connect("leave-notify-event", self.on_hover_leave)

        self._destroyed = False
        self._is_history = False

    def set_is_history(self, is_history):
        self._is_history = is_history

    def set_container(self, container):
        self._container = container

    def get_container(self):
        return self._container

    def create_header(self):
        notification = self.notification
        
        if "file://" in notification.app_icon:
            self.app_icon_image = Image(
                name="notification-icon",
                image_file=notification.app_icon[7:],
                size=24,
            )
        else:
            icon_name = notification.app_icon if notification.app_icon else "dialog-information-symbolic"
            self.app_icon_image = Image(
                name="notification-icon",
                icon_name=icon_name,
                icon_size=24,
            )
        
        self.app_name_label_header = Label(notification.app_name, name="notification-app-name", h_align="start")
        self.header_close_button = self.create_close_button()

        start_box = Box(spacing=4, children=[self.app_icon_image, self.app_name_label_header])

        return CenterBox(
            name="notification-title",
            start_children=[start_box],
            end_children=[self.header_close_button],
        )

    def create_content(self):
        notification = self.notification
        pixbuf = load_scaled_pixbuf(self, 48, 48)
        
        self.notification_image_box = Box(
            name="notification-image",
            orientation="v",
            children=[CustomImage(pixbuf=pixbuf), Box(v_expand=True)],
        )
        
        self.notification_summary_label = Label(
            name="notification-summary",
            markup=notification.summary,
            h_align="start",
            max_chars_width=16,
            ellipsization="end",
        )
        
        self.notification_app_name_label_content = Label(
            name="notification-app-name",
            markup=notification.app_name,
            h_align="start",
            max_chars_width=16,
            ellipsization="end",
        )
        
        if notification.body:
            self.notification_body_label = Label(
                markup=notification.body,
                h_align="start",
                max_chars_width=34,
                ellipsization="end",
            )
            self.notification_body_label.set_single_line_mode(True)
        else:
            self.notification_body_label = Box()
        
        summary_box_children = [
            self.notification_summary_label,
            Box(name="notif-sep", h_expand=False, v_expand=False, h_align="center", v_align="center"),
            self.notification_app_name_label_content,
        ]
        
        self.notification_text_box = Box(
            name="notification-text",
            orientation="v",
            v_align="center",
            h_expand=True,
            h_align="start",
            children=[
                Box(name="notification-summary-box", orientation="h", children=summary_box_children),
                self.notification_body_label,
            ],
        )
        
        self.content_close_button = self.create_close_button()
        self.content_close_button_box = Box(orientation="v", children=[self.content_close_button])

        content_children = [self.notification_image_box, self.notification_text_box, self.content_close_button_box]
        
        return Box(name="notification-content", spacing=8, children=content_children)

    def create_action_buttons(self):
        notification = self.notification
        if not notification.actions:
            return None

        grid = Gtk.Grid()
        grid.set_column_homogeneous(True)
        grid.set_column_spacing=4
        
        for i, action in enumerate(notification.actions):
            action_button = ActionButton(action, i, len(notification.actions), self)
            grid.attach(action_button, i, 0, 1, 1)
        
        return grid

    def create_close_button(self):
        self.close_button = Button(
            name="notif-close-button",
            child=Label(name="notif-close-label", markup=icons.cancel),
            on_clicked=lambda *_: self.notification.close("dismissed-by-user"),
        )
        
        self.close_button.connect("enter-notify-event", lambda *_: self.hover_button(self.close_button))
        self.close_button.connect("leave-notify-event", lambda *_: self.unhover_button(self.close_button))
        
        return self.close_button

    def on_hover_enter(self, *args):
        if self._container:
            self._container.pause_and_reset_all_timeouts()

    def on_hover_leave(self, *args):
        if self._container:
            self._container.resume_all_timeouts()

    def start_timeout(self):
        self.stop_timeout()
        self._timeout_id = GLib.timeout_add(self.timeout_ms, self.close_notification)

    def stop_timeout(self):
        if self._timeout_id is not None:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = None

    def close_notification(self):
        if not self._destroyed:
            try:
                self.notification.close("expired")
                self.stop_timeout()
            except Exception as e:
                logger.error(f"Error in close_notification: {e}")
        return False

    def destroy(self, from_history_delete=False):
        # Disconnect event handlers to prevent memory leaks
        if hasattr(self, '_hover_enter_handler') and self._hover_enter_handler:
            try:
                self.disconnect(self._hover_enter_handler)
            except:
                pass
        if hasattr(self, '_hover_leave_handler') and self._hover_leave_handler:
            try:
                self.disconnect(self._hover_leave_handler)
            except:
                pass
        
        # Clean up cached image
        if hasattr(self, "cached_image_path") and self.cached_image_path:
            if os.path.exists(self.cached_image_path):
                if not self._is_history or from_history_delete:
                    try:
                        os.remove(self.cached_image_path)
                    except Exception as e:
                        logger.error(f"Error deleting cached image: {e}")
        
        self._destroyed = True
        self.stop_timeout()
        
        # Clear references to help garbage collection
        self.notification = None
        self._container = None
        
        super().destroy()

    def hover_button(self, button):
        if self._container:
            self._container.pause_and_reset_all_timeouts()

    def unhover_button(self, button):
        if self._container:
            self._container.resume_all_timeouts()


class HistoricalNotification(object):
    def __init__(self, id, app_icon, summary, body, app_name, timestamp, cached_image_path=None):
        self.id = id
        self.app_icon = app_icon
        self.summary = summary
        self.body = body
        self.app_name = app_name
        self.timestamp = timestamp
        self.cached_image_path = cached_image_path
        self.image_pixbuf = None
        self.actions = []
        self.cached_scaled_pixbuf = None


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
        
        # Start periodic cleanup timer (every 5 minutes)
        self._cleanup_timer_id = GLib.timeout_add_seconds(300, self._periodic_cleanup)

    def get_ordinal(self, n):
        remainder = n % 100
        if 11 <= remainder <= 13:
            return "th"
        
        last_digit = n % 10
        if last_digit == 1:
            return "st"
        elif last_digit == 2:
            return "nd"
        elif last_digit == 3:
            return "rd"
        else:
            return "th"

    def get_date_header(self, dt):
        now = datetime.now()
        today = now.date()
        date = dt.date()
        
        if date == today:
            return "Today"
        elif date == today - timedelta(days=1):
            return "Yesterday"
        else:
            original_locale = locale.getlocale(locale.LC_TIME)
            try:
                locale.setlocale(locale.LC_TIME, ("en_US", "UTF-8"))
            except locale.Error:
                locale.setlocale(locale.LC_TIME, "C")
            
            try:
                day = dt.day
                ordinal = self.get_ordinal(day)
                month = dt.strftime("%B")
                if dt.year == now.year:
                    result = f"{month} {day}{ordinal}"
                else:
                    result = f"{month} {day}{ordinal}, {dt.year}"
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
        logger.info(f"Do Not Disturb mode {status}")

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
            try:
                os.remove(PERSISTENT_HISTORY_FILE)
            except Exception as e:
                logger.error(f"Error deleting persistent history file: {e}")
        
        self.persistent_notifications = []
        self.containers = []
        self.rebuild_with_separators()

    def _load_persistent_history(self):
        if not os.path.exists(PERSISTENT_DIR):
            os.makedirs(PERSISTENT_DIR, exist_ok=True)
        
        if os.path.exists(PERSISTENT_HISTORY_FILE):
            try:
                with open(PERSISTENT_HISTORY_FILE, "r") as f:
                    self.persistent_notifications = json.load(f)
                
                for note in reversed(self.persistent_notifications):
                    self._add_historical_notification(note)
                    yield True
            except Exception as e:
                logger.error(f"Error loading persistent history: {e}")
        
        GLib.idle_add(self.update_no_notifications_label_visibility)
        self._cleanup_orphan_cached_images()
        self.schedule_midnight_update()

    def _save_persistent_history(self):
        try:
            with open(PERSISTENT_HISTORY_FILE, "w") as f:
                json.dump(self.persistent_notifications, f)
        except Exception as e:
            logger.error(f"Error saving persistent history: {e}")

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
        
        self.hist_notif_close_button = Button(
            name="notif-close-button",
            child=Label(name="notif-close-label", markup=icons.cancel),
            on_clicked=lambda *_: self.delete_historical_notification(hist_notif.id, container),
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
        
        # Limit history to MAX_NOTIFICATION_HISTORY (10 instead of 25)
        if len(self.containers) >= MAX_NOTIFICATION_HISTORY:
            oldest_container = self.containers.pop()
            if hasattr(oldest_container, "notification_box"):
                if hasattr(oldest_container.notification_box, "cached_image_path"):
                    if oldest_container.notification_box.cached_image_path and os.path.exists(oldest_container.notification_box.cached_image_path):
                        try:
                            os.remove(oldest_container.notification_box.cached_image_path)
                        except Exception as e:
                            logger.error(f"Error deleting cached image: {e}")
            oldest_container.destroy()

        def on_container_destroy(container):
            if hasattr(container, "_timestamp_timer_id"):
                if container._timestamp_timer_id:
                    GLib.source_remove(container._timestamp_timer_id)
            
            if hasattr(container, "notification_box"):
                notif_box = container.notification_box
            
            container.destroy()
            
            new_containers = [c for c in self.containers if c != container]
            self.containers = new_containers
            
            self.rebuild_with_separators()
            self.update_no_notifications_label_visibility()

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
            on_clicked=lambda *_: on_container_destroy(container),
        )
        
        self.current_notif_close_button_box = Box(orientation="v", children=[self.current_notif_close_button, Box(v_expand=True)])
        
        content_box = Box(
            name="notification-content",
            spacing=8,
            children=[self.current_notif_image_box, self.current_notif_text_box, self.current_notif_close_button_box],
        )
        
        container.notification_box = notification_box
        hist_box = Box(name="notification-box-hist", orientation="v", h_align="fill", h_expand=True)
        hist_box.add(content_box)
        
        close_button = content_box.get_children()[2].get_children()[0]
        close_button.connect("clicked", lambda *_: on_container_destroy(container))
        
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
            try:
                uuid_from_filename = cached_file[len("notification_") : -len(".png")]
                if uuid_from_filename not in history_uuids:
                    cache_file_path = os.path.join(PERSISTENT_DIR, cached_file)
                    os.remove(cache_file_path)
            except Exception as e:
                logger.error(f"Error processing cached file {cached_file}: {e}")
    
    def _cleanup_old_cached_images(self):
        """Limit cached images to MAX_CACHED_IMAGES"""
        if not os.path.exists(PERSISTENT_DIR):
            return
        
        try:
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
                    try:
                        os.remove(file_path)
                    except Exception as e:
                        logger.error(f"Error removing old cached image: {e}")
        except Exception as e:
            logger.error(f"Error in _cleanup_old_cached_images: {e}")
    
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
                        try:
                            os.remove(container.notification_box.cached_image_path)
                        except Exception as e:
                            logger.error(f"Error deleting cached image: {e}")
            
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
        
        try:
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
        except Exception as e:
            logger.error(f"Error closing notification: {e}")

    def _destroy_container(self):
        try:
            self.notifications.clear()
            self._destroyed_notifications.clear()
            
            children = self.stack.get_children()
            for child in children:
                self.stack.remove(child)
                child.destroy()
            
            self.current_index = 0
        except Exception as e:
            logger.error(f"Error cleaning up the container: {e}")
        finally:
            self._is_destroying = False
            return False

    def pause_and_reset_all_timeouts(self):
        if self._is_destroying:
            return
        
        for notification in self.notifications[:]:
            try:
                if not notification._destroyed and notification.get_parent():
                    notification.stop_timeout()
            except Exception as e:
                logger.error(f"Error pausing timeout: {e}")

    def resume_all_timeouts(self):
        if self._is_destroying:
            return
        
        for notification in self.notifications[:]:
            try:
                if not notification._destroyed and notification.get_parent():
                    notification.start_timeout()
            except Exception as e:
                logger.error(f"Error resuming timeout: {e}")

    def close_all_notifications(self, *args):
        notifications_to_close = self.notifications.copy()
        for notification_box in notifications_to_close:
            notification_box.notification.close("dismissed-by-user")


class NotificationPopup(Window):
    def __init__(self, **kwargs):
        super().__init__(
            name="notification-popup",
            anchor="right top",
            layer="top",
            keyboard_mode="none",
            exclusivity="none",
            visible=True,
            all_visible=True,
        )

        self.widgets = kwargs.get("widgets", None)

        if self.widgets:
            self.notification_history = self.widgets.notification_history
        else:
            self.notification_history = NotificationHistory()
        
        self.notification_container = NotificationContainer(
            notification_history_instance=self.notification_history,
            revealer_transition_type="slide-down" if True else "slide-up",
        )

        self.show_box = Box()
        self.show_box.set_size_request(1, 1)

        main_box = Box(
            name="notification-popup-box",
            orientation="v",
            children=[self.notification_container, self.show_box],
        )
        
        self.add(main_box)