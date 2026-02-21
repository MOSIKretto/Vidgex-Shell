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
    NotificationBox, NotificationGroup, load_scaled_pixbuf, get_history_ignored_apps,
    PERSISTENT_HISTORY_FILE, MAX_NOTIFICATION_HISTORY, MAX_POPUP_NOTIFICATIONS,
    HistoricalNotification, NOTIFICATION_WIDTH, save_notification_image, 
    delete_notification_image, clear_all_notification_images, submit_io_task
)
import services.icons as icons
from services.image import CustomImage


class NotificationHistory(Box):
    __slots__ = (
        'containers', 'groups', 'containers_by_id', '_save_timer_id',
        '_dnd_handler', 'header_switch', 'do_not_disturb_enabled',
        'notifications_list', 'no_notifications_box', 'scrolled_window',
        'persistent_notifications', '_is_destroyed', '_today_header',
        '_loading', '_pending_rebuild'
    )

    def __init__(self, **kwargs):
        super().__init__(name="notification-history", orientation="v", **kwargs)

        self.containers = []
        self.containers_by_id = {}
        self.groups = {}
        self.persistent_notifications = []

        self._today_header = None
        self._loading = False
        self._pending_rebuild = False
        self._save_timer_id = None
        self._dnd_handler = None
        self._is_destroyed = False
        self.do_not_disturb_enabled = False

        self._build_ui()
        GLib.idle_add(self._start_loading, priority=GLib.PRIORITY_LOW)

    def _build_ui(self):
        self.header_switch = Gtk.Switch(name="dnd-switch")
        self.header_switch.set_vexpand(False)
        self.header_switch.set_valign(Gtk.Align.CENTER)
        self._dnd_handler = self.header_switch.connect("notify::active", self._on_dnd_changed)

        header = CenterBox(
            name="notification-history-header",
            spacing=8,
            start_children=[
                self.header_switch,
                Label(name="dnd-label", markup=icons.notifications_off),
            ],
            center_children=[
                Label(name="nhh", label="Notifications", h_align="start", h_expand=True),
            ],
            end_children=[
                Button(
                    name="nhh-button",
                    child=Label(name="nhh-button-label", markup=icons.trash),
                    on_clicked=self.clear_history,
                ),
            ],
        )

        self.notifications_list = Box(
            name="notifications-list", orientation="v", spacing=4,
            h_expand=True, v_expand=True, h_align="fill", v_align="fill",
        )
        self.notifications_list.set_size_request(NOTIFICATION_WIDTH, -1)

        self.no_notifications_box = Box(
            name="no-notifications-box",
            v_align="fill", h_align="fill", v_expand=True, h_expand=True,
            children=[
                Label(
                    name="no-notif", markup=icons.notifications_clear,
                    v_align="fill", h_align="fill",
                    v_expand=True, h_expand=True, justification="center",
                ),
            ],
        )

        self.scrolled_window = ScrolledWindow(
            name="notification-history-scrolled", orientation="v",
            h_expand=True, v_expand=True, h_align="fill", v_align="fill",
            propagate_width=False, propagate_height=False,
        )
        self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scrolled_window.add_with_viewport(Box(
            orientation="v",
            children=[self.notifications_list, self.no_notifications_box],
        ))

        self.add(header)
        self.add(self.scrolled_window)

    def _on_dnd_changed(self, switch, _):
        self.do_not_disturb_enabled = switch.get_active()

    @staticmethod
    def _get_date_header_str(dt):
        today = datetime.now().date()
        date = dt.date()
        if date == today: return "Today"
        if date == today - timedelta(days=1): return "Yesterday"

        day = dt.day
        suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
        month = dt.strftime("%B")
        if dt.year == today.year: return f"{month} {day}{suffix}"
        return f"{month} {day}{suffix}, {dt.year}"

    def _create_date_separator(self, text):
        return Box(
            name="notif-date-sep",
            children=[Label(name="notif-date-sep-label", label=text, h_align="center", h_expand=True)],
        )

    def _update_empty_state(self):
        has = bool(self.containers)
        self.no_notifications_box.set_visible(not has)
        self.notifications_list.set_visible(has)

    def _cleanup_empty_date_separators(self):
        children = self.notifications_list.get_children()
        to_remove = []
        prev_sep = None
        for child in children:
            if child.get_name() == "notif-date-sep":
                if prev_sep is not None: to_remove.append(prev_sep)
                prev_sep = child
            else: prev_sep = None
        if prev_sep is not None: to_remove.append(prev_sep)
        for child in to_remove:
            if child is self._today_header: self._today_header = None
            self.notifications_list.remove(child)
            child.destroy()

    def _rebuild_with_groups(self):
        if self._is_destroyed: return
        notif_list = self.notifications_list
        self._today_header = None
        expanded = {n: g.is_expanded for n, g in self.groups.items()}
        
        for child in notif_list.get_children():
            notif_list.remove(child)
            if child.get_name() == "notif-date-sep":
                child.destroy()
                
        for g in self.groups.values():
            g.clear_containers()
            g.destroy()
        self.groups.clear()

        for c in self.containers:
            nb = getattr(c, "notification_box", None)
            if nb and nb.notification:
                app = nb.notification.app_name
                g = self.groups.get(app)
                if not g:
                    g = NotificationGroup(app, self, is_expanded=expanded.get(app, False))
                    self.groups[app] = g
                g.add_notification_id(nb.uuid, c.arrival_time)

        for g in self.groups.values():
            g.update_display(self.containers_by_id)

        sorted_groups = sorted(self.groups.values(), key=lambda grp: grp.latest_arrival_time or datetime.min, reverse=True)
        current_header = None
        for g in sorted_groups:
            lat = g.latest_arrival_time
            if lat:
                header = self._get_date_header_str(lat)
                if header != current_header:
                    sep = self._create_date_separator(header)
                    notif_list.add(sep)
                    current_header = header
                    if header == "Today": self._today_header = sep
            notif_list.add(g)

        notif_list.show_all()
        self._update_empty_state()

    def _on_hist_close_clicked(self, button, uuid, cont_ref):
        if not self._is_destroyed:
            cont = cont_ref()
            if cont: self.delete_historical_notification(uuid, cont)

    def _create_history_container(self, notification_box, arrival_time):
        container = Box(name="notification-container", orientation="v", h_align="fill", h_expand=True)
        container.set_size_request(NOTIFICATION_WIDTH, -1)
        container.arrival_time = arrival_time
        container.notification_box = notification_box

        notif = notification_box.notification
        pixbuf = load_scaled_pixbuf(notif, 48, 48)
        
        image_box = Box(name="notification-image")
        if pixbuf:
            img = CustomImage(pixbuf=pixbuf)
            img.set_valign(Gtk.Align.START)
            image_box.add(img)
            del pixbuf
        
        summary_box = Box(
            name="notification-summary-box", orientation="h",
            children=[
                Label(name="notification-summary", markup=notif.summary, h_align="start", max_chars_width=18, ellipsization="end"),
                Box(name="notif-sep"),
                Label(name="notification-timestamp", label=arrival_time.strftime("%H:%M"), h_align="start"),
            ],
        )

        text_box = Box(name="notification-text", orientation="v", v_align="center", h_expand=True, children=[summary_box])
        
        if notif.body:
            text_box.add(Label(
                name="notification-body", markup=notif.body,
                h_align="start", line_wrap="word-char", max_chars_width=34,
            ))
        
        close_btn = Button(name="notif-close-button", child=Label(name="notif-close-label", markup=icons.cancel))
        close_btn.connect("clicked", self._on_hist_close_clicked, notification_box.uuid, weakref.ref(container))

        container.add(Box(
            name="notification-box-hist", spacing=8,
            children=[image_box, text_box, Box(orientation="v", v_align="center", children=[close_btn])],
        ))
        return container

    def clear_history(self, *_):
        if self._is_destroyed: return
        
        for child in self.notifications_list.get_children():
            self.notifications_list.remove(child)
            child.destroy()
            
        self.groups.clear()
        
        for c in self.containers:
            nb = getattr(c, "notification_box", None)
            if nb: 
                nb.destroy(from_history_delete=True)
                c.notification_box = None
            c.destroy()
            
        submit_io_task(self._clear_files_sync)
        clear_all_notification_images()
        self.persistent_notifications.clear()
        self.containers.clear()
        self.containers_by_id.clear()
        self._today_header = None
        self._update_empty_state()

    def clear_history_for_app(self, app_name):
        """Эффективное O(N) массовое удаление всей стопки"""
        if self._is_destroyed or app_name not in self.groups:
            return

        group = self.groups[app_name]
        nids_to_remove = set(group.notification_ids)

        # 1. Удаляем из памяти сохранения (движемся с конца для безопасного pop)
        for i in range(len(self.persistent_notifications) - 1, -1, -1):
            if self.persistent_notifications[i].get("id") in nids_to_remove:
                self.persistent_notifications.pop(i)
        self._schedule_save()

        # 2. Уничтожаем виджеты из памяти ОЗУ
        for nid in nids_to_remove:
            delete_notification_image(nid)
            container = self.containers_by_id.pop(nid, None)
            if container:
                try:
                    self.containers.remove(container)
                except ValueError:
                    pass
                nb = getattr(container, "notification_box", None)
                if nb:
                    nb.destroy(from_history_delete=True)
                    container.notification_box = None
                container.destroy()

        # 3. Уничтожаем саму группу
        self.notifications_list.remove(group)
        group.destroy()
        del self.groups[app_name]

        self._cleanup_empty_date_separators()
        self._update_empty_state()

    def delete_historical_notification(self, note_id, container):
        if self._is_destroyed: return
        nb = getattr(container, "notification_box", None)
        app_name = nb.notification.app_name if nb and nb.notification else None
        
        if nb: 
            nb.destroy(from_history_delete=True)
            container.notification_box = None

        note_id_str = str(note_id)
        
        for i, n in enumerate(self.persistent_notifications):
            if str(n.get("id")) == note_id_str:
                self.persistent_notifications.pop(i)
                delete_notification_image(note_id)
                self._schedule_save()
                break
            
        self.containers_by_id.pop(note_id, None)
        try:
            self.containers.remove(container)
        except ValueError:
            pass

        if app_name in self.groups:
            g = self.groups[app_name]
            g.remove_notification_id(note_id)
            if g.get_notification_count() == 0:
                self.notifications_list.remove(g)
                g.destroy()
                del self.groups[app_name]
                self._cleanup_empty_date_separators()
            else: 
                g.update_display(self.containers_by_id)

        if container:
            container.destroy()
            
        self._update_empty_state()

    def _start_loading(self):
        if not self._loading:
            self._loading = True
            submit_io_task(self._load_from_file)
        return GLib.SOURCE_REMOVE

    def _load_from_file(self):
        data = []
        try:
            if os.path.exists(PERSISTENT_HISTORY_FILE):
                with open(PERSISTENT_HISTORY_FILE, "r", encoding="utf-8") as f: data = json.load(f)
        except: pass
        GLib.idle_add(self._on_loaded, data, priority=GLib.PRIORITY_LOW)

    def _on_loaded(self, data):
        if self._is_destroyed: return
        self.persistent_notifications = data if isinstance(data, list) else []
        if self.persistent_notifications: 
            notes_reversed = self.persistent_notifications.copy()
            notes_reversed.reverse()
            self._process_loaded_batch(notes_reversed, 0)
        else: 
            self._finish_loading()
        return GLib.SOURCE_REMOVE

    def _process_loaded_batch(self, notes, idx):
        if self._is_destroyed: return
        end = min(idx + 5, len(notes))
        for i in range(idx, end): self._add_historical_notification(notes[i], rebuild=False)
        if end < len(notes): GLib.idle_add(self._process_loaded_batch, notes, end, priority=GLib.PRIORITY_LOW)
        else: self._finish_loading()

    def _finish_loading(self):
        if not self._is_destroyed:
            self._rebuild_with_groups()
            self._loading = False
        return GLib.SOURCE_REMOVE

    def _schedule_save(self):
        if self._save_timer_id: GLib.source_remove(self._save_timer_id)
        self._save_timer_id = GLib.timeout_add(1000, self._do_save)

    def _do_save(self):
        self._save_timer_id = None
        if not self._is_destroyed:
            data = list(self.persistent_notifications)
            submit_io_task(lambda: self._save_to_file_sync(data))
        return GLib.SOURCE_REMOVE

    def _add_historical_notification(self, note, rebuild=True):
        if not isinstance(note, dict): return
        hist = HistoricalNotification(
            id=note.get("id"), app_icon=note.get("app_icon"),
            summary=note.get("summary", ""), body=note.get("body", ""),
            app_name=note.get("app_name", "Unknown"), timestamp=note.get("timestamp"),
        )
        box = NotificationBox(hist, timeout_ms=0)
        box.uuid = hist.id
        box.set_is_history(True)
        try: arrival = datetime.fromisoformat(hist.timestamp)
        except: arrival = datetime.now()
        container = self._create_history_container(box, arrival)
        self.containers.insert(0, container)
        self.containers_by_id[box.uuid] = container
        if rebuild: self._rebuild_with_groups()

    def add_notification(self, notification_box):
        if self._is_destroyed: return
        notification_box.set_is_history(True)
        notification_box.stop_timeout()
        notification_box.set_container(None)
        notif = notification_box.notification
        app_name = notif.app_name if notif else "Unknown"
        if app_name in get_history_ignored_apps():
            notification_box.destroy(from_history_delete=True)
            return

        while len(self.containers) >= MAX_NOTIFICATION_HISTORY:
            oldest = self.containers.pop()
            nb = getattr(oldest, "notification_box", None)
            if nb:
                if nb.notification and nb.notification.app_name in self.groups:
                    g = self.groups[nb.notification.app_name]
                    g.remove_notification_id(nb.uuid)
                    if g.get_notification_count() == 0:
                        self.notifications_list.remove(g)
                        g.destroy()
                        del self.groups[nb.notification.app_name]
                        self._cleanup_empty_date_separators()
                    else:
                        g.update_display(self.containers_by_id)
                self.containers_by_id.pop(nb.uuid, None)
                delete_notification_image(nb.uuid)
                nb.destroy(from_history_delete=True)
                oldest.notification_box = None 
            oldest.destroy()

        now = datetime.now()
        container = self._create_history_container(notification_box, now)
        self.containers.insert(0, container)
        self.containers_by_id[notification_box.uuid] = container
        self._append_persistent_notification(notification_box, now)

        if not self._today_header:
            self._today_header = self._create_date_separator("Today")
            self.notifications_list.pack_start(self._today_header, False, False, 0)
        self.notifications_list.reorder_child(self._today_header, 0)

        g = self.groups.get(app_name)
        if not g:
            g = NotificationGroup(app_name, self, is_expanded=True)
            self.groups[app_name] = g
            self.notifications_list.add(g)
        g.add_notification_id(notification_box.uuid, now)
        g.update_display(self.containers_by_id)
        self.notifications_list.reorder_child(g, 1)
        g.show_all()
        self._update_empty_state()

    def _append_persistent_notification(self, box, arrival_time):
        n = box.notification
        has_pixbuf = getattr(n, 'image_pixbuf', None) is not None
        img = save_notification_image(n, box.uuid) if has_pixbuf else getattr(n, 'app_icon', None)
        self.persistent_notifications.insert(0, {
            "id": box.uuid, "app_icon": img, "summary": getattr(n, 'summary', ''),
            "body": getattr(n, 'body', ''), "app_name": getattr(n, 'app_name', 'Unknown'),
            "timestamp": arrival_time.isoformat(), "has_saved_image": has_pixbuf,
        })
        if len(self.persistent_notifications) > MAX_NOTIFICATION_HISTORY:
            old = self.persistent_notifications.pop()
            if old.get("has_saved_image"): delete_notification_image(old.get("id"))
        self._schedule_save()

    @staticmethod
    def _clear_files_sync():
        if os.path.exists(PERSISTENT_HISTORY_FILE):
            try: os.remove(PERSISTENT_HISTORY_FILE)
            except: pass

    @staticmethod
    def _save_to_file_sync(data):
        try:
            with open(PERSISTENT_HISTORY_FILE, "w", encoding="utf-8") as f: json.dump(data, f, separators=(",", ":"))
        except: pass

    def destroy(self):
        if self._is_destroyed: return
        self._is_destroyed = True
        
        if self._dnd_handler and self.header_switch:
            self.header_switch.disconnect(self._dnd_handler)
            
        if self._save_timer_id: 
            GLib.source_remove(self._save_timer_id)
            self._save_timer_id = None
            self._save_to_file_sync(self.persistent_notifications)
            
        self.clear_history()
        super().destroy()


class NotificationContainer(Box):
    __slots__ = (
        'notification_history', '_server', '_server_handler',
        '_is_destroying', 'stack', 'prev_button', 'close_all_button',
        'next_button', 'navigation_revealer', 'main_revealer',
        'notifications', 'current_index', '_destroyed_ids'
    )

    def __init__(self, notification_history_instance, revealer_transition_type="slide-down"):
        super().__init__(name="notification-container-main", orientation="v", spacing=4)
        self.notification_history = notification_history_instance
        self._is_destroying = False
        self.notifications = []
        self.current_index = 0
        self._destroyed_ids = set()
        self._server = Notifications()
        self._server_handler = self._server.connect("notification-added", self._on_new_notification)
        self._build_ui(revealer_transition_type)

    def _build_ui(self, transition):
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, transition_duration=120, visible=True)
        self.prev_button = Button(name="nav-button", child=Label(name="nav-button-label", markup=icons.chevron_left), on_clicked=self._show_previous)
        self.close_all_button = Button(name="nav-button", child=Label(name="nav-button-label", markup=icons.cancel), on_clicked=self._close_all)
        self.close_all_button.get_child().add_style_class("close")
        self.next_button = Button(name="nav-button", child=Label(name="nav-button-label", markup=icons.chevron_right), on_clicked=self._show_next)

        for b in (self.prev_button, self.close_all_button, self.next_button):
            b.connect("enter-notify-event", self._on_nav_enter)
            b.connect("leave-notify-event", self._on_nav_leave)

        nav = Box(name="notification-navigation", spacing=4, h_align="center", children=[self.prev_button, self.close_all_button, self.next_button])
        self.navigation_revealer = Revealer(transition_type="slide-down", transition_duration=120, child=nav)
        
        self.main_revealer = Revealer(
            name="notification-main-revealer",
            transition_type=transition, transition_duration=150,
            child=Box(name="notification-box-internal-container", orientation="v", children=[
                Box(name="notification-stack-box", h_align="center", children=[self.stack]),
                self.navigation_revealer
            ])
        )
        self.add(self.main_revealer)

    def _on_nav_enter(self, widget, event):
        for nb in self.notifications:
            if not getattr(nb, '_destroyed', False): nb.stop_timeout()
        return False

    def _on_nav_leave(self, widget, event):
        for nb in self.notifications:
            if not getattr(nb, '_destroyed', False): nb.start_timeout()
        return False

    def _on_new_notification(self, fabric_notif, notif_id):
        if self._is_destroying: return
        n = fabric_notif.get_notification_from_id(notif_id)
        nb = NotificationBox(n)
        if self.notification_history.do_not_disturb_enabled:
            self.notification_history.add_notification(nb)
            return
        nb.set_container(self)
        n.connect("closed", self._on_closed)
        if len(self.notifications) >= MAX_POPUP_NOTIFICATIONS:
            old = self.notifications.pop(0)
            self.stack.remove(old)
            self.notification_history.add_notification(old)
            self.current_index = max(0, self.current_index - 1)
        self.stack.add_named(nb, str(notif_id))
        self.notifications.append(nb)
        self.current_index = len(self.notifications) - 1
        self.stack.set_visible_child(nb)
        for b in self.notifications: b.start_timeout()
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
        c = len(self.notifications)
        self.prev_button.set_sensitive(self.current_index > 0)
        self.next_button.set_sensitive(self.current_index < c - 1)
        self.navigation_revealer.set_reveal_child(c > 1)

    def _on_closed(self, notification, reason):
        nid = notification.id
        if self._is_destroying or nid in self._destroyed_ids: return
        self._destroyed_ids.add(nid)
        idx = next((i for i, nb in enumerate(self.notifications) if nb.notification and nb.notification.id == nid), -1)
        if idx == -1: return
        nb = self.notifications.pop(idx)
        if "dismissed" in str(reason).lower(): nb.destroy()
        else: self.notification_history.add_notification(nb)
        self.stack.remove(nb)
        if not self.notifications:
            self._is_destroying = True
            self.main_revealer.set_reveal_child(False)
            GLib.timeout_add(150, self._reset_container)
            return
        self.current_index = min(max(0, idx - 1) if idx == self.current_index else self.current_index, len(self.notifications)-1)
        self.stack.set_visible_child(self.notifications[self.current_index])
        self._update_nav()

    def _reset_container(self):
        for c in self.stack.get_children(): self.stack.remove(c)
        self.notifications.clear()
        self._destroyed_ids.clear()
        self.current_index = 0
        self._is_destroying = False
        return GLib.SOURCE_REMOVE

    def _close_all(self, *_):
        for nb in list(self.notifications):
            if nb.notification: nb.notification.close("dismissed-by-user")

    def destroy(self):
        self._is_destroying = True
        if self._server and self._server_handler: 
            self._server.disconnect(self._server_handler)
            self._server_handler = None
        for nb in self.notifications: nb.destroy()
        super().destroy()
