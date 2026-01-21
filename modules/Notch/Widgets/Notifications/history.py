from fabric.notifications.service import Notifications
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer
from fabric.widgets.scrolledwindow import ScrolledWindow

from gi.repository import GLib, Gtk

from .notification_box import (
    NotificationBox, cache_notification_pixbuf, load_scaled_pixbuf,
    get_history_ignored_apps, PERSISTENT_HISTORY_FILE, MAX_NOTIFICATION_HISTORY,
    MAX_POPUP_NOTIFICATIONS, PERSISTENT_DIR, MAX_CACHED_IMAGES, HistoricalNotification)
import modules.icons as icons
from widgets.image import CustomImage

_ORDINALS = {1: "st", 2: "nd", 3: "rd"}
_TODAY = "Today"
_YESTERDAY = "Yesterday"


class NotificationHistory(Box):
    __slots__ = (
        'containers', '_cleanup_timer_id', 'header_label', 'header_switch',
        'header_clean', 'do_not_disturb_enabled', '_dnd_handler', 'dnd_label',
        'history_header', 'notifications_list', 'no_notifications_label',
        'no_notifications_box', 'scrolled_window', 'scrolled_window_viewport_box',
        'persistent_notifications'
    )

    def __init__(self, **kwargs):
        super().__init__(name="notification-history", orientation="v", **kwargs)

        self.containers = []
        self._cleanup_timer_id = None
        self.persistent_notifications = []
        self.do_not_disturb_enabled = False

        self.header_label = Label(name="nhh", label="Notifications", h_align="start", h_expand=True)
        
        self.header_switch = Gtk.Switch(name="dnd-switch")
        self.header_switch.set_vexpand(False)
        self.header_switch.set_valign(Gtk.Align.CENTER)
        self.header_switch.set_active(False)
        self._dnd_handler = self.header_switch.connect("notify::active", self._on_dnd_changed)

        self.header_clean = Button(
            name="nhh-button",
            child=Label(name="nhh-button-label", markup=icons.trash),
            on_clicked=self.clear_history,
        )

        self.dnd_label = Label(name="dnd-label", markup=icons.notifications_off)

        self.history_header = CenterBox(
            name="notification-history-header",
            spacing=8,
            start_children=[self.header_switch, self.dnd_label],
            center_children=[self.header_label],
            end_children=[self.header_clean],
        )

        self.notifications_list = Box(
            name="notifications-list", orientation="v", spacing=4,
            h_expand=True, v_expand=True, h_align="fill", v_align="fill",
        )

        self.no_notifications_label = Label(
            name="no-notif", markup=icons.notifications_clear,
            v_align="fill", h_align="fill", v_expand=True, h_expand=True, justification="center",
        )

        self.no_notifications_box = Box(
            name="no-notifications-box", v_align="fill", h_align="fill",
            v_expand=True, h_expand=True, children=[self.no_notifications_label],
        )

        self.scrolled_window = ScrolledWindow(
            name="notification-history-scrolled-window", orientation="v",
            h_expand=True, v_expand=True, h_align="fill", v_align="fill",
            propagate_width=False, propagate_height=False,
        )

        self.scrolled_window_viewport_box = Box(
            orientation="v", children=[self.notifications_list, self.no_notifications_box]
        )
        self.scrolled_window.add_with_viewport(self.scrolled_window_viewport_box)

        self.add(self.history_header)
        self.add(self.scrolled_window)

        GLib.idle_add(self._load_persistent_history)
        self._cleanup_timer_id = GLib.timeout_add_seconds(300, self._periodic_cleanup)

    def _on_dnd_changed(self, switch, _):
        self.do_not_disturb_enabled = switch.get_active()

    @staticmethod
    def _get_ordinal(n):
        return _ORDINALS.get(n % 10, "th") if not 11 <= n % 100 <= 13 else "th"

    def get_date_header(self, dt):
        from datetime import datetime, timedelta
        now = datetime.now()
        today = now.date()
        date = dt.date()

        if date == today:
            return _TODAY
        if date == today - timedelta(days=1):
            return _YESTERDAY

        day = dt.day
        month = dt.strftime("%B")
        ordinal = self._get_ordinal(day)
        
        if dt.year == now.year:
            return f"{month} {day}{ordinal}"
        return f"{month} {day}{ordinal}, {dt.year}"

    def _schedule_midnight_update(self):
        from datetime import datetime, timedelta
        now = datetime.now()
        next_midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time())
        GLib.timeout_add_seconds(int((next_midnight - now).total_seconds()), self._on_midnight)

    def _on_midnight(self):
        self._rebuild_with_separators()
        self._schedule_midnight_update()
        return GLib.SOURCE_REMOVE

    def _create_date_separator(self, date_header):
        return Box(
            name="notif-date-sep",
            children=[Label(name="notif-date-sep-label", label=date_header, h_align="center", h_expand=True)],
        )

    def _rebuild_with_separators(self):
        notif_list = self.notifications_list
        for child in notif_list.get_children():
            notif_list.remove(child)

        current_header = None
        for container in sorted(self.containers, key=lambda x: x.arrival_time, reverse=True):
            header = self.get_date_header(container.arrival_time)
            if header != current_header:
                notif_list.add(self._create_date_separator(header))
                current_header = header
            notif_list.add(container)

        notif_list.show_all()
        self._update_visibility()

    def _update_visibility(self):
        has_notif = bool(self.containers)
        self.no_notifications_box.set_visible(not has_notif)
        self.notifications_list.set_visible(has_notif)

    def clear_history(self, *_):
        import os
        for child in self.notifications_list.get_children()[:]:
            if hasattr(child, "notification_box") and child.notification_box:
                child.notification_box.destroy(from_history_delete=True)
            self.notifications_list.remove(child)
            child.destroy()

        if os.path.exists(PERSISTENT_HISTORY_FILE):
            os.remove(PERSISTENT_HISTORY_FILE)

        self.persistent_notifications.clear()
        self.containers.clear()
        self._rebuild_with_separators()

    def _load_persistent_history(self):
        import os
        import json

        if not os.path.exists(PERSISTENT_DIR):
            os.makedirs(PERSISTENT_DIR, exist_ok=True)

        if os.path.exists(PERSISTENT_HISTORY_FILE):
            with open(PERSISTENT_HISTORY_FILE, "r") as f:
                self.persistent_notifications = json.load(f)

            for note in reversed(self.persistent_notifications):
                self._add_historical_notification(note)

        self._update_visibility()
        self._cleanup_orphan_cached_images()
        self._schedule_midnight_update()
        return GLib.SOURCE_REMOVE

    def _save_persistent_history(self):
        import json
        with open(PERSISTENT_HISTORY_FILE, "w") as f:
            json.dump(self.persistent_notifications, f)

    def delete_historical_notification(self, note_id, container):
        if hasattr(container, "notification_box"):
            container.notification_box.destroy(from_history_delete=True)

        note_id_str = str(note_id)
        self.persistent_notifications = [
            n for n in self.persistent_notifications if str(n.get("id")) != note_id_str
        ]
        self._save_persistent_history()

        container.destroy()
        self.containers = [c for c in self.containers if c is not container]
        self._rebuild_with_separators()

    def _add_historical_notification(self, note):
        from datetime import datetime
        import weakref

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

        container = Box(name="notification-container", orientation="v", h_align="fill", h_expand=True)
        container.notification_box = hist_box

        try:
            container.arrival_time = datetime.fromisoformat(hist_notif.timestamp)
        except Exception:
            container.arrival_time = datetime.now()

        time_label = Label(
            name="notification-timestamp",
            markup=container.arrival_time.strftime("%H:%M"), h_align="start"
        )

        image_box = Box(
            name="notification-image", orientation="v",
            children=[CustomImage(pixbuf=load_scaled_pixbuf(hist_box, 48, 48)), Box(v_expand=True)],
        )

        summary_label = Label(name="notification-summary", markup=hist_notif.summary, h_align="start")
        app_label = Label(name="notification-app-name", markup=hist_notif.app_name, h_align="start")

        body_widget = (
            Label(name="notification-body", markup=hist_notif.body, h_align="start", line_wrap="word-char")
            if hist_notif.body else Box()
        )
        if hist_notif.body:
            body_widget.set_single_line_mode(True)

        sep = lambda: Box(name="notif-sep", h_expand=False, v_expand=False, h_align="center", v_align="center")

        summary_box = Box(
            name="notification-summary-box", orientation="h",
            children=[summary_label, sep(), app_label, sep(), time_label],
        )
        text_box = Box(
            name="notification-text", orientation="v", v_align="center",
            h_expand=True, children=[summary_box, body_widget],
        )

        self_ref = weakref.ref(self)
        hist_id = hist_notif.id

        close_btn = Button(
            name="notif-close-button",
            child=Label(name="notif-close-label", markup=icons.cancel),
            on_clicked=lambda *_: (s := self_ref()) and s.delete_historical_notification(hist_id, container),
        )

        close_box = Box(orientation="v", children=[close_btn, Box(v_expand=True)])

        content = Box(
            name="notification-box-hist", spacing=8,
            children=[image_box, text_box, close_box],
        )

        container.add(content)
        self.containers.insert(0, container)
        self._rebuild_with_separators()

    def add_notification(self, notification_box):
        import os
        import weakref
        from datetime import datetime

        app_name = notification_box.notification.app_name
        if app_name in get_history_ignored_apps():
            notification_box.destroy(from_history_delete=True)
            return

        if len(self.containers) >= MAX_NOTIFICATION_HISTORY:
            oldest = self.containers.pop()
            if hasattr(oldest, "notification_box"):
                nb = oldest.notification_box
                if hasattr(nb, "cached_image_path") and nb.cached_image_path and os.path.exists(nb.cached_image_path):
                    os.remove(nb.cached_image_path)
            oldest.destroy()

        self_ref = weakref.ref(self)

        def on_destroy(cont):
            if hasattr(cont, "_timestamp_timer_id") and cont._timestamp_timer_id:
                try:
                    GLib.source_remove(cont._timestamp_timer_id)
                except:
                    pass
            if hasattr(cont, "notification_box") and cont.notification_box:
                try:
                    cont.notification_box.destroy(from_history_delete=False)
                except:
                    pass
            try:
                cont.destroy()
            except:
                pass
            if (s := self_ref()):
                s.containers = [c for c in s.containers if c is not cont]
                s._rebuild_with_separators()

        container = Box(name="notification-container", orientation="v", h_align="fill", h_expand=True)
        container.arrival_time = datetime.now()
        container.notification_box = notification_box

        time_label = Label(name="notification-timestamp", markup=container.arrival_time.strftime("%H:%M"))

        image_box = Box(
            name="notification-image", orientation="v",
            children=[CustomImage(pixbuf=load_scaled_pixbuf(notification_box, 48, 48)), Box(v_expand=True, v_align="fill")],
        )

        notif = notification_box.notification
        summary_label = Label(name="notification-summary", markup=notif.summary, h_align="start")
        app_label = Label(name="notification-app-name", markup=notif.app_name, h_align="start")

        body_widget = (
            Label(name="notification-body", markup=notif.body, h_align="start", line_wrap="word-char")
            if notif.body else Box()
        )
        if notif.body:
            body_widget.set_single_line_mode(True)

        sep = lambda: Box(name="notif-sep", h_expand=False, v_expand=False, h_align="center", v_align="center")

        summary_box = Box(name="notification-summary-box", orientation="h", children=[summary_label, sep(), app_label, sep(), time_label])
        text_box = Box(name="notification-text", orientation="v", v_align="center", h_expand=True, children=[summary_box, body_widget])

        close_btn = Button(
            name="notif-close-button",
            child=Label(name="notif-close-label", markup=icons.cancel),
        )
        close_btn.connect("clicked", lambda *_: on_destroy(container))

        close_box = Box(orientation="v", children=[close_btn, Box(v_expand=True)])

        content = Box(name="notification-content", spacing=8, children=[image_box, text_box, close_box])

        hist_box = Box(name="notification-box-hist", orientation="v", h_align="fill", h_expand=True)
        hist_box.add(content)
        container.add(hist_box)

        self.containers.insert(0, container)
        self._rebuild_with_separators()
        self._append_persistent_notification(notification_box, container.arrival_time)

    def _append_persistent_notification(self, notification_box, arrival_time):
        notif = notification_box.notification
        note = {
            "id": notification_box.uuid,
            "app_icon": notif.app_icon,
            "summary": notif.summary,
            "body": notif.body,
            "app_name": notif.app_name,
            "timestamp": arrival_time.isoformat(),
            "cached_image_path": notification_box.cached_image_path,
        }

        self.persistent_notifications.insert(0, note)
        if len(self.persistent_notifications) > MAX_NOTIFICATION_HISTORY:
            self.persistent_notifications = self.persistent_notifications[:MAX_NOTIFICATION_HISTORY]

        self._save_persistent_history()
        self._cleanup_old_cached_images()

    def _cleanup_orphan_cached_images(self):
        import os
        if not os.path.exists(PERSISTENT_DIR):
            return

        history_uuids = {str(n.get("id")) for n in self.persistent_notifications if n.get("id")}
        prefix, suffix = "notification_", ".png"

        for f in os.listdir(PERSISTENT_DIR):
            if f.startswith(prefix) and f.endswith(suffix):
                uuid = f[len(prefix):-len(suffix)]
                if uuid not in history_uuids:
                    os.remove(os.path.join(PERSISTENT_DIR, f))

    def _cleanup_old_cached_images(self):
        import os
        if not os.path.exists(PERSISTENT_DIR):
            return

        cached = []
        prefix, suffix = "notification_", ".png"
        for f in os.listdir(PERSISTENT_DIR):
            if f.startswith(prefix) and f.endswith(suffix):
                path = os.path.join(PERSISTENT_DIR, f)
                cached.append((path, os.path.getmtime(path)))

        if len(cached) > MAX_CACHED_IMAGES:
            cached.sort(key=lambda x: x[1])
            for path, _ in cached[:len(cached) - MAX_CACHED_IMAGES]:
                os.remove(path)

    def _periodic_cleanup(self):
        self._cleanup_old_cached_images()
        return True

    def destroy(self):
        if self._cleanup_timer_id:
            GLib.source_remove(self._cleanup_timer_id)
            self._cleanup_timer_id = None

        if self._dnd_handler:
            try:
                self.header_switch.disconnect(self._dnd_handler)
            except:
                pass

        super().destroy()

    def clear_history_for_app(self, app_name):
        import os
        to_remove = []
        ids_to_remove = set()

        for container in self.containers:
            if hasattr(container, "notification_box"):
                nb = container.notification_box
                if nb.notification.app_name == app_name:
                    to_remove.append(container)
                    ids_to_remove.add(nb.uuid)

        for container in to_remove:
            nb = container.notification_box
            if hasattr(nb, "cached_image_path") and nb.cached_image_path and os.path.exists(nb.cached_image_path):
                os.remove(nb.cached_image_path)

            self.containers.remove(container)
            self.notifications_list.remove(container)
            nb.destroy(from_history_delete=True)
            container.destroy()

        self.persistent_notifications = [n for n in self.persistent_notifications if n.get("id") not in ids_to_remove]
        self._save_persistent_history()
        self._rebuild_with_separators()


class NotificationContainer(Box):
    __slots__ = (
        'notification_history', '_server', '_pending_removal', '_is_destroying',
        'stack', 'navigation', 'stack_box', 'prev_button', 'close_all_button',
        'next_button', 'navigation_revealer', 'notification_box_container',
        'main_revealer', 'notifications', 'current_index', '_destroyed_notifications'
    )

    def __init__(self, notification_history_instance, revealer_transition_type="slide-down"):
        super().__init__(name="notification-container-main", orientation="v", spacing=4)
        self.notification_history = notification_history_instance
        self._pending_removal = False
        self._is_destroying = False
        self.notifications = []
        self.current_index = 0
        self._destroyed_notifications = set()

        self._server = Notifications()
        self._server.connect("notification-added", self._on_new_notification)

        self.stack = Gtk.Stack(
            name="notification-stack",
            transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT,
            transition_duration=200, visible=True,
        )

        self.prev_button = Button(
            name="nav-button",
            child=Label(name="nav-button-label", markup=icons.chevron_left),
            on_clicked=self._show_previous,
        )

        self.close_all_button = Button(
            name="nav-button",
            child=Label(name="nav-button-label", markup=icons.cancel),
            on_clicked=self._close_all,
        )
        self.close_all_button.get_child().add_style_class("close")

        self.next_button = Button(
            name="nav-button",
            child=Label(name="nav-button-label", markup=icons.chevron_right),
            on_clicked=self._show_next,
        )

        for btn in (self.prev_button, self.close_all_button, self.next_button):
            btn.connect("enter-notify-event", lambda *_: self._pause_timeouts())
            btn.connect("leave-notify-event", lambda *_: self._resume_timeouts())

        self.navigation = Box(name="notification-navigation", spacing=4, h_align="center")
        self.navigation.add(self.prev_button)
        self.navigation.add(self.close_all_button)
        self.navigation.add(self.next_button)

        self.navigation_revealer = Revealer(
            transition_type="slide-down", transition_duration=200,
            child=self.navigation, reveal_child=False,
        )

        self.stack_box = Box(name="notification-stack-box", h_align="center", h_expand=False, children=[self.stack])

        self.notification_box_container = Box(
            name="notification-box-internal-container", orientation="v",
            children=[self.stack_box, self.navigation_revealer],
        )

        self.main_revealer = Revealer(
            name="notification-main-revealer",
            transition_type=revealer_transition_type, transition_duration=250,
            child_revealed=False, child=self.notification_box_container,
        )

        self.add(self.main_revealer)
        self._update_nav()

    def _on_new_notification(self, fabric_notif, id):
        hist = self.notification_history

        if hist.do_not_disturb_enabled:
            notification = fabric_notif.get_notification_from_id(id)
            new_box = NotificationBox(notification)
            if notification.image_pixbuf:
                cache_notification_pixbuf(new_box)
            hist.add_notification(new_box)
            return

        notification = fabric_notif.get_notification_from_id(id)
        new_box = NotificationBox(notification)
        new_box.set_container(self)
        notification.connect("closed", self._on_closed)

        while len(self.notifications) >= MAX_POPUP_NOTIFICATIONS:
            oldest = self.notifications.pop(0)
            hist.add_notification(oldest)
            self.stack.remove(oldest)
            if self.current_index > 0:
                self.current_index -= 1

        self.stack.add_named(new_box, str(id))
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
        self.prev_button.set_sensitive(self.current_index > 0)
        self.next_button.set_sensitive(self.current_index < len(self.notifications) - 1)
        self.navigation_revealer.set_reveal_child(len(self.notifications) > 1)

    def _on_closed(self, notification, reason):
        if self._is_destroying or notification.id in self._destroyed_notifications:
            return

        self._destroyed_notifications.add(notification.id)

        idx = next((i for i, nb in enumerate(self.notifications) if nb.notification.id == notification.id), None)
        if idx is None:
            return

        notif_box = self.notifications[idx]

        if str(reason) == "NotificationCloseReason.DISMISSED_BY_USER":
            notif_box.destroy()
        else:
            notif_box.set_is_history(True)
            self.notification_history.add_notification(notif_box)
            notif_box.stop_timeout()

        new_idx = max(0, idx - 1) if idx == self.current_index else (self.current_index - 1 if idx < self.current_index else self.current_index)

        if notif_box.get_parent() == self.stack:
            self.stack.remove(notif_box)

        self.notifications.pop(idx)

        if not self.notifications:
            self._is_destroying = True
            self.main_revealer.set_reveal_child(False)
            self._destroy_container()
            return

        self.current_index = min(new_idx, len(self.notifications) - 1)
        self.stack.set_visible_child(self.notifications[self.current_index])
        self._update_nav()

    def _destroy_container(self):
        try:
            self.notifications.clear()
            self._destroyed_notifications.clear()
            for child in self.stack.get_children():
                self.stack.remove(child)
                child.destroy()
            self.current_index = 0
        finally:
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