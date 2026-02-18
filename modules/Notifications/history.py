import json
import os
import weakref
from datetime import datetime, timedelta

from fabric.notifications.service import Notifications
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer
from fabric.widgets.scrolledwindow import ScrolledWindow

from gi.repository import GLib, Gtk

from .notification_box import (
    NotificationBox, load_scaled_pixbuf, get_history_ignored_apps,
    PERSISTENT_HISTORY_FILE, MAX_NOTIFICATION_HISTORY, MAX_POPUP_NOTIFICATIONS,
    PERSISTENT_DIR, MAX_CACHED_IMAGES, HistoricalNotification, NOTIFICATION_WIDTH
)
import services.icons as icons
from services.image import CustomImage


class NotificationHistory(Box):
    __slots__ = (
        'containers', '_cleanup_timer_id', '_save_timer_id', '_dnd_handler',
        'header_switch', 'do_not_disturb_enabled', 'notifications_list',
        'no_notifications_box', 'scrolled_window', 'persistent_notifications'
    )

    def __init__(self, **kwargs):
        super().__init__(name="notification-history", orientation="v", **kwargs)

        self.containers = []
        self._cleanup_timer_id = None
        self._save_timer_id = None
        self.persistent_notifications = []
        self.do_not_disturb_enabled = False

        self._build_ui()
        GLib.idle_add(self._load_persistent_history)
        self._cleanup_timer_id = GLib.timeout_add_seconds(300, self._periodic_cleanup)

    def _build_ui(self):
        self.header_switch = Gtk.Switch(name="dnd-switch")
        self.header_switch.set_vexpand(False)
        self.header_switch.set_valign(Gtk.Align.CENTER)
        self._dnd_handler = self.header_switch.connect("notify::active", self._on_dnd_changed)

        header = CenterBox(
            name="notification-history-header",
            spacing=8,
            start_children=[self.header_switch, Label(name="dnd-label", markup=icons.notifications_off)],
            center_children=[Label(name="nhh", label="Notifications", h_align="start", h_expand=True)],
            end_children=[Button(
                name="nhh-button",
                child=Label(name="nhh-button-label", markup=icons.trash),
                on_clicked=self.clear_history,
            )],
        )

        self.notifications_list = Box(
            name="notifications-list", orientation="v", spacing=4,
            h_expand=True, v_expand=True, h_align="fill", v_align="fill",
        )
        self.notifications_list.set_size_request(NOTIFICATION_WIDTH, -1)

        self.no_notifications_box = Box(
            name="no-notifications-box", v_align="fill", h_align="fill",
            v_expand=True, h_expand=True,
            children=[Label(
                name="no-notif", markup=icons.notifications_clear,
                v_align="fill", h_align="fill", v_expand=True, h_expand=True, justification="center",
            )],
        )

        self.scrolled_window = ScrolledWindow(
            name="bluetooth-devices", orientation="v",
            h_expand=True, v_expand=True, h_align="fill", v_align="fill",
            propagate_width=False, propagate_height=False,
        )
        self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scrolled_window.add_with_viewport(Box(
            orientation="v",
            children=[self.notifications_list, self.no_notifications_box]
        ))

        self.add(header)
        self.add(self.scrolled_window)

    def _on_dnd_changed(self, switch, _):
        self.do_not_disturb_enabled = switch.get_active()

    def get_date_header(self, dt):
        today = datetime.now().date()
        date = dt.date()

        if date == today:
            return "Today"
        if date == today - timedelta(days=1):
            return "Yesterday"

        day = dt.day
        if 11 <= day % 100 <= 13:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")

        month = dt.strftime("%B")
        year = dt.year

        if year == datetime.now().year:
            return f"{month} {day}{suffix}"
        return f"{month} {day}{suffix}, {year}"

    def _schedule_midnight_update(self):
        now = datetime.now()
        next_midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time())
        GLib.timeout_add_seconds(int((next_midnight - now).total_seconds()), self._on_midnight)

    def _on_midnight(self):
        self._rebuild_with_separators()
        self._schedule_midnight_update()
        return GLib.SOURCE_REMOVE

    def _rebuild_with_separators(self):
        notif_list = self.notifications_list
        
        for child in notif_list.get_children():
            notif_list.remove(child)
            if not hasattr(child, 'notification_box'):
                child.destroy()

        current_header = None
        for container in self.containers:
            header = self.get_date_header(container.arrival_time)
            if header != current_header:
                notif_list.add(Box(
                    name="notif-date-sep",
                    children=[Label(name="notif-date-sep-label", label=header, h_align="center", h_expand=True)],
                ))
                current_header = header
            notif_list.add(container)

        notif_list.show_all()
        has_notif = bool(self.containers)
        self.no_notifications_box.set_visible(not has_notif)
        self.notifications_list.set_visible(has_notif)

    def clear_history(self, *_):
        notif_list = self.notifications_list
        
        for child in notif_list.get_children()[:]:
            nb = getattr(child, "notification_box", None)
            if nb:
                nb.destroy(from_history_delete=True)
            notif_list.remove(child)
            child.destroy()

        if os.path.exists(PERSISTENT_HISTORY_FILE):
            os.remove(PERSISTENT_HISTORY_FILE)

        self.persistent_notifications.clear()
        self.containers.clear()
        self._rebuild_with_separators()

    def _load_persistent_history(self):
        os.makedirs(PERSISTENT_DIR, exist_ok=True)

        if os.path.exists(PERSISTENT_HISTORY_FILE):
            with open(PERSISTENT_HISTORY_FILE, "r") as f:
                self.persistent_notifications = json.load(f)

            for note in reversed(self.persistent_notifications):
                self._add_historical_notification(note, rebuild=False)

            self._rebuild_with_separators()

        has_notif = bool(self.containers)
        self.no_notifications_box.set_visible(not has_notif)
        self.notifications_list.set_visible(has_notif)
        self._cleanup_orphan_cached_images()
        self._schedule_midnight_update()
        return GLib.SOURCE_REMOVE

    def _save_persistent_history(self):
        if self._save_timer_id:
            GLib.source_remove(self._save_timer_id)
        self._save_timer_id = GLib.timeout_add(300, self._do_save)

    def _do_save(self):
        self._save_timer_id = None
        with open(PERSISTENT_HISTORY_FILE, "w") as f:
            json.dump(self.persistent_notifications, f, separators=(',', ':'))
        return GLib.SOURCE_REMOVE

    def delete_historical_notification(self, note_id, container):
        nb = getattr(container, "notification_box", None)
        if nb:
            nb.destroy(from_history_delete=True)

        note_id_str = str(note_id)
        for i, n in enumerate(self.persistent_notifications):
            if str(n.get("id")) == note_id_str:
                del self.persistent_notifications[i]
                break

        self._save_persistent_history()
        container.destroy()
        
        try:
            self.containers.remove(container)
        except ValueError:
            pass
            
        self._rebuild_with_separators()

    def _add_historical_notification(self, note, rebuild=True):
        hist_notif = HistoricalNotification(
            id=note.get("id"), app_icon=note.get("app_icon"),
            summary=note.get("summary"), body=note.get("body"),
            app_name=note.get("app_name"), timestamp=note.get("timestamp"),
            cached_image_path=note.get("cached_image_path"),
        )

        hist_box = NotificationBox(hist_notif, timeout_ms=0)
        hist_box.uuid = hist_notif.id
        hist_box.cached_image_path = hist_notif.cached_image_path
        hist_box.set_is_history(True)

        for child in hist_box.get_children():
            if child.get_name() == "notification-action-buttons":
                hist_box.remove(child)

        try:
            arrival = datetime.fromisoformat(hist_notif.timestamp)
        except (ValueError, TypeError):
            arrival = datetime.now()

        container = Box(name="notification-container", orientation="v", h_align="fill", h_expand=True)
        container.set_size_request(NOTIFICATION_WIDTH, -1)
        container.arrival_time = arrival
        container.notification_box = hist_box

        time_str = arrival.strftime("%H:%M")

        image_box = Box(
            name="notification-image", orientation="v",
            children=[CustomImage(pixbuf=load_scaled_pixbuf(hist_box, 48, 48)), Box(v_expand=True)],
        )

        summary_label = Label(name="notification-summary", markup=hist_notif.summary, h_align="start", max_chars_width=18, ellipsization="end")
        app_label = Label(name="notification-app-name", markup=hist_notif.app_name, h_align="start", max_chars_width=10, ellipsization="end")
        time_label = Label(name="notification-timestamp", markup=time_str, h_align="start")

        if hist_notif.body:
            body_widget = Label(name="notification-body", markup=hist_notif.body, h_align="start", line_wrap="word-char", max_chars_width=34)
        else:
            body_widget = Box()

        summary_box = Box(
            name="notification-summary-box", orientation="h",
            children=[
                summary_label,
                Box(name="notif-sep", h_expand=False, v_expand=False, h_align="center", v_align="center"),
                app_label,
                Box(name="notif-sep", h_expand=False, v_expand=False, h_align="center", v_align="center"),
                time_label
            ],
        )
        
        text_box = Box(name="notification-text", orientation="v", v_align="center", h_expand=True, children=[summary_box, body_widget])

        self_ref = weakref.ref(self)
        hist_id = hist_notif.id

        close_btn = Button(
            name="notif-close-button",
            child=Label(name="notif-close-label", markup=icons.cancel),
            on_clicked=lambda *_: (s := self_ref()) and s.delete_historical_notification(hist_id, container),
        )

        content = Box(
            name="notification-box-hist", spacing=8,
            children=[image_box, text_box, Box(orientation="v", children=[close_btn, Box(v_expand=True)])],
        )

        container.add(content)
        self.containers.insert(0, container)

        if rebuild:
            self._rebuild_with_separators()

    def add_notification(self, notification_box):
        app_name = notification_box.notification.app_name
        if app_name in get_history_ignored_apps():
            notification_box.destroy(from_history_delete=True)
            return

        if len(self.containers) >= MAX_NOTIFICATION_HISTORY:
            oldest = self.containers.pop()
            nb = getattr(oldest, "notification_box", None)
            if nb:
                cached = getattr(nb, "cached_image_path", None)
                if cached and os.path.exists(cached):
                    os.remove(cached)
            oldest.destroy()

        now = datetime.now()
        
        container = Box(name="notification-container", orientation="v", h_align="fill", h_expand=True)
        container.set_size_request(NOTIFICATION_WIDTH, -1)
        container.arrival_time = now
        container.notification_box = notification_box

        time_str = now.strftime("%H:%M")

        image_box = Box(
            name="notification-image", orientation="v",
            children=[CustomImage(pixbuf=load_scaled_pixbuf(notification_box, 48, 48)), Box(v_expand=True, v_align="fill")],
        )

        notif = notification_box.notification

        summary_label = Label(name="notification-summary", markup=notif.summary, h_align="start", max_chars_width=18, ellipsization="end")
        app_label = Label(name="notification-app-name", markup=notif.app_name, h_align="start", max_chars_width=10, ellipsization="end")
        time_label = Label(name="notification-timestamp", markup=time_str, h_align="start")

        if notif.body:
            body_widget = Label(name="notification-body", markup=notif.body, h_align="start", line_wrap="word-char", max_chars_width=34)
        else:
            body_widget = Box()

        summary_box = Box(
            name="notification-summary-box", orientation="h",
            children=[
                summary_label,
                Box(name="notif-sep", h_expand=False, v_expand=False, h_align="center", v_align="center"),
                app_label,
                Box(name="notif-sep", h_expand=False, v_expand=False, h_align="center", v_align="center"),
                time_label
            ],
        )
        
        text_box = Box(name="notification-text", orientation="v", v_align="center", h_expand=True, children=[summary_box, body_widget])

        self_ref = weakref.ref(self)

        def on_destroy(cont):
            timer = getattr(cont, "_timestamp_timer_id", None)
            if timer:
                GLib.source_remove(timer)
            nb = getattr(cont, "notification_box", None)
            if nb:
                nb.destroy(from_history_delete=False)
            cont.destroy()
            s = self_ref()
            if s:
                try:
                    s.containers.remove(cont)
                except ValueError:
                    pass
                s._rebuild_with_separators()

        close_btn = Button(name="notif-close-button", child=Label(name="notif-close-label", markup=icons.cancel))
        close_btn.connect("clicked", lambda *_: on_destroy(container))

        content = Box(name="notification-content", spacing=8, children=[image_box, text_box, Box(orientation="v", children=[close_btn, Box(v_expand=True)])])

        hist_box = Box(name="notification-box-hist", orientation="v", h_align="fill", h_expand=True)
        hist_box.add(content)
        container.add(hist_box)

        self.containers.insert(0, container)
        self._rebuild_with_separators()
        self._append_persistent_notification(notification_box, now)

    def _append_persistent_notification(self, notification_box, arrival_time):
        notif = notification_box.notification
        
        self.persistent_notifications.insert(0, {
            "id": notification_box.uuid,
            "app_icon": notif.app_icon,
            "summary": notif.summary,
            "body": notif.body,
            "app_name": notif.app_name,
            "timestamp": arrival_time.isoformat(),
            "cached_image_path": notification_box.cached_image_path,
        })

        if len(self.persistent_notifications) > MAX_NOTIFICATION_HISTORY:
            del self.persistent_notifications[MAX_NOTIFICATION_HISTORY:]

        self._save_persistent_history()
        self._cleanup_old_cached_images()

    def _cleanup_orphan_cached_images(self):
        if not os.path.exists(PERSISTENT_DIR):
            return

        history_uuids = {str(n.get("id")) for n in self.persistent_notifications if n.get("id")}

        for f in os.listdir(PERSISTENT_DIR):
            if f.startswith("notification_") and f.endswith(".png"):
                uuid = f[13:-4]
                if uuid not in history_uuids:
                    os.remove(os.path.join(PERSISTENT_DIR, f))

    def _cleanup_old_cached_images(self):
        if not os.path.exists(PERSISTENT_DIR):
            return

        cached = []
        for f in os.listdir(PERSISTENT_DIR):
            if f.startswith("notification_") and f.endswith(".png"):
                path = os.path.join(PERSISTENT_DIR, f)
                cached.append((path, os.path.getmtime(path)))

        excess = len(cached) - MAX_CACHED_IMAGES
        if excess > 0:
            cached.sort(key=lambda x: x[1])
            for path, _ in cached[:excess]:
                os.remove(path)

    def _periodic_cleanup(self):
        self._cleanup_old_cached_images()
        return True

    def destroy(self):
        if self._cleanup_timer_id:
            GLib.source_remove(self._cleanup_timer_id)
            self._cleanup_timer_id = None

        if self._save_timer_id:
            GLib.source_remove(self._save_timer_id)
            self._save_timer_id = None
            self._do_save()

        if self._dnd_handler:
            try:
                self.header_switch.disconnect(self._dnd_handler)
            except Exception:
                pass

        super().destroy()

    def clear_history_for_app(self, app_name):
        to_remove = []
        ids_to_remove = set()

        for container in self.containers:
            nb = getattr(container, "notification_box", None)
            if nb and nb.notification.app_name == app_name:
                to_remove.append(container)
                ids_to_remove.add(nb.uuid)

        if not to_remove:
            return

        for container in to_remove:
            nb = container.notification_box
            cached = getattr(nb, "cached_image_path", None)
            if cached and os.path.exists(cached):
                os.remove(cached)

            self.containers.remove(container)
            self.notifications_list.remove(container)
            nb.destroy(from_history_delete=True)
            container.destroy()

        self.persistent_notifications = [n for n in self.persistent_notifications if n.get("id") not in ids_to_remove]
        self._save_persistent_history()
        self._rebuild_with_separators()


class NotificationContainer(Box):
    __slots__ = (
        'notification_history', '_server', '_is_destroying',
        'stack', 'prev_button', 'close_all_button', 'next_button',
        'navigation_revealer', 'main_revealer', 'notifications',
        'current_index', '_destroyed_notifications'
    )

    def __init__(self, notification_history_instance, revealer_transition_type="slide-down"):
        super().__init__(name="notification-container-main", orientation="v", spacing=4)
        
        self.notification_history = notification_history_instance
        self._is_destroying = False
        self.notifications = []
        self.current_index = 0
        self._destroyed_notifications = set()

        self._server = Notifications()
        self._server.connect("notification-added", self._on_new_notification)

        self._build_ui(revealer_transition_type)

    def _build_ui(self, revealer_transition_type):
        self.stack = Gtk.Stack(
            name="notification-stack",
            transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT,
            transition_duration=200,
            visible=True,
        )

        self.prev_button = Button(name="nav-button", child=Label(name="nav-button-label", markup=icons.chevron_left), on_clicked=self._show_previous)
        self.close_all_button = Button(name="nav-button", child=Label(name="nav-button-label", markup=icons.cancel), on_clicked=self._close_all)
        self.close_all_button.get_child().add_style_class("close")
        self.next_button = Button(name="nav-button", child=Label(name="nav-button-label", markup=icons.chevron_right), on_clicked=self._show_next)

        for btn in (self.prev_button, self.close_all_button, self.next_button):
            btn.connect("enter-notify-event", lambda *_: self._pause_timeouts())
            btn.connect("leave-notify-event", lambda *_: self._resume_timeouts())

        navigation = Box(name="notification-navigation", spacing=4, h_align="center")
        navigation.add(self.prev_button)
        navigation.add(self.close_all_button)
        navigation.add(self.next_button)

        self.navigation_revealer = Revealer(transition_type="slide-down", transition_duration=200, child=navigation, reveal_child=False)

        self.main_revealer = Revealer(
            name="notification-main-revealer",
            transition_type=revealer_transition_type,
            transition_duration=250,
            child_revealed=False,
            child=Box(
                name="notification-box-internal-container",
                orientation="v",
                children=[Box(name="notification-stack-box", h_align="center", h_expand=False, children=[self.stack]), self.navigation_revealer],
            ),
        )

        self.add(self.main_revealer)
        self._update_nav()

    def _on_new_notification(self, fabric_notif, notif_id):
        hist = self.notification_history
        notification = fabric_notif.get_notification_from_id(notif_id)
        new_box = NotificationBox(notification)

        if hist.do_not_disturb_enabled:
            hist.add_notification(new_box)
            return

        new_box.set_container(self)
        notification.connect("closed", self._on_closed)

        while len(self.notifications) >= MAX_POPUP_NOTIFICATIONS:
            oldest = self.notifications.pop(0)
            hist.add_notification(oldest)
            self.stack.remove(oldest)
            if self.current_index > 0:
                self.current_index -= 1

        self.stack.add_named(new_box, str(notif_id))
        self.notifications.append(new_box)
        self.current_index = len(self.notifications) - 1
        self.stack.set_visible_child(new_box)

        for nb in self.notifications:
            nb.start_timeout()

        self.main_revealer.show_all()
        self.main_revealer.set_reveal_child(True)
        self._update_nav()

    def _show_previous(self, *_):
        if self.current_index > 0:
            self.current_index -= 1
            self.stack.set_visible_child(self.notifications[self.current_index])
            self._update_nav()

    def _show_next(self, *_):
        if self.current_index < len(self.notifications) - 1:
            self.current_index += 1
            self.stack.set_visible_child(self.notifications[self.current_index])
            self._update_nav()

    def _update_nav(self):
        count = len(self.notifications)
        self.prev_button.set_sensitive(self.current_index > 0)
        self.next_button.set_sensitive(self.current_index < count - 1)
        self.navigation_revealer.set_reveal_child(count > 1)

    def _on_closed(self, notification, reason):
        notif_id = notification.id
        
        if self._is_destroying or notif_id in self._destroyed_notifications:
            return

        self._destroyed_notifications.add(notif_id)

        idx = None
        for i, nb in enumerate(self.notifications):
            if nb.notification.id == notif_id:
                idx = i
                break

        if idx is None:
            return

        notif_box = self.notifications[idx]

        if str(reason) == "NotificationCloseReason.DISMISSED_BY_USER":
            notif_box.destroy()
        else:
            notif_box.set_is_history(True)
            self.notification_history.add_notification(notif_box)
            notif_box.stop_timeout()

        current = self.current_index
        new_idx = max(0, idx - 1) if idx == current else (current - 1 if idx < current else current)

        if notif_box.get_parent() == self.stack:
            self.stack.remove(notif_box)

        del self.notifications[idx]

        if not self.notifications:
            self._is_destroying = True
            self.main_revealer.set_reveal_child(False)
            self._destroy_container()
            return

        self.current_index = min(new_idx, len(self.notifications) - 1)
        self.stack.set_visible_child(self.notifications[self.current_index])
        self._update_nav()

    def _destroy_container(self):
        self.notifications.clear()
        self._destroyed_notifications.clear()
        
        for child in self.stack.get_children():
            self.stack.remove(child)
            child.destroy()
            
        self.current_index = 0
        self._is_destroying = False

    def _pause_timeouts(self):
        if self._is_destroying:
            return
        for nb in self.notifications:
            if not nb._destroyed and nb.get_parent():
                nb.stop_timeout()

    def _resume_timeouts(self):
        if self._is_destroying:
            return
        for nb in self.notifications:
            if not nb._destroyed and nb.get_parent():
                nb.start_timeout()

    def _close_all(self, *_):
        for nb in self.notifications[:]:
            nb.notification.close("dismissed-by-user")

    def pause_and_reset_all_timeouts(self):
        self._pause_timeouts()

    def resume_all_timeouts(self):
        self._resume_timeouts()