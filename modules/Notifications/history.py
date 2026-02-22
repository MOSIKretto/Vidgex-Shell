import json
import os
import weakref
import gc
from datetime import datetime

from fabric.notifications.service import Notifications
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer
from fabric.widgets.scrolledwindow import ScrolledWindow

from gi.repository import GLib, GdkPixbuf, Gtk

from .notification_box import (
    NotificationBox, NotificationGroup, get_history_ignored_apps,
    PERSISTENT_HISTORY_FILE, MAX_NOTIFICATION_HISTORY, MAX_POPUP_NOTIFICATIONS,
    HistoricalNotification, NOTIFICATION_WIDTH, delete_notification_image, 
    clear_all_notification_images, submit_io_task
)
import services.icons as icons
from services.image import CustomImage


class NotificationHistory(Box):
    __slots__ = (
        'containers', 'groups', 'containers_by_id', '_save_timer_id',
        '_dnd_handler', 'header_switch', 'do_not_disturb_enabled',
        'notifications_list', 'no_notifications_box', 'scroll',
        'persistent_notifications', '_is_destroyed',
        '_loading', '_pending_rebuild'
    )

    def __init__(self, **kwargs):
        super().__init__(name="notification-history", spacing=4, orientation="vertical", **kwargs)

        self.containers = []
        self.containers_by_id = {}
        self.groups = {}
        self.persistent_notifications = []

        self._loading = False
        self._pending_rebuild = False
        self._save_timer_id = None
        self._dnd_handler = None
        self._is_destroyed = False
        self.do_not_disturb_enabled = False

        self._build_ui()
        GLib.idle_add(self._start_loading, priority=GLib.PRIORITY_LOW)

    def _build_ui(self):
        self.header_switch = Gtk.Switch(name="dnd-switch", vexpand=False, valign=Gtk.Align.CENTER)
        self._dnd_handler = self.header_switch.connect("notify::active", self._on_dnd_changed)

        header = CenterBox(
            name="notification-history-header",
            start_children=[self.header_switch, Label(name="dnd-label", markup=icons.notifications_off)],
            center_children=[Label(name="nhh", label="Notifications", h_align="start", h_expand=True)],
            end_children=[Button(name="nhh-button", child=Label(name="nhh-button-label", markup=icons.trash), on_clicked=self.clear_history)],
        )

        self.notifications_list = Box(name="notifications-list", orientation="vertical", spacing=4)
        self.notifications_list.set_size_request(NOTIFICATION_WIDTH, -1)

        self.no_notifications_box = Box(
            name="no-notifications-box", v_align="center", h_align="center", v_expand=True, h_expand=True,
            children=[Label(name="no-notif", markup=icons.notifications_clear, justification="center")],
        )

        self.scroll = ScrolledWindow(
            name="bluetooth-devices", min_content_size=(-1, -1),
            child=Box(spacing=4, orientation="vertical", children=[self.notifications_list, self.no_notifications_box]),
            v_expand=True, propagate_width=False, propagate_height=False
        )
        self.children = [header, self.scroll]

    def _on_dnd_changed(self, switch, _):
        self.do_not_disturb_enabled = switch.get_active()

    def _update_empty_state(self):
        has = bool(self.containers)
        self.no_notifications_box.set_visible(not has)
        self.notifications_list.set_visible(has)

    def _rebuild_with_groups(self):
        if self._is_destroyed: return
        expanded = {n: g.is_expanded for n, g in self.groups.items()}
        
        for child in self.notifications_list.get_children():
            self.notifications_list.remove(child)
            
        for g in self.groups.values():
            g.clear_containers()
            g.destroy()
        self.groups.clear()

        for c in self.containers:
            nb = getattr(c, "notification_box", None)
            if nb and nb.notification:
                app = getattr(nb.notification, 'app_name', 'Unknown')
                if app not in self.groups:
                    self.groups[app] = NotificationGroup(app, self, is_expanded=expanded.get(app, False))
                self.groups[app].add_notification_id(nb.uuid, c.arrival_time)

        for g in self.groups.values(): g.update_display(self.containers_by_id)

        sorted_groups = sorted(self.groups.values(), key=lambda grp: grp.latest_arrival_time or datetime.min, reverse=True)
        
        for g in sorted_groups:
            self.notifications_list.add(g)

        self.notifications_list.show_all()
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
        image_box = Box(name="notification-image")
        
        thumb_path = getattr(notification_box, '_thumb_path', getattr(notif, 'app_icon', None))
        if thumb_path and os.path.exists(thumb_path):
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file(thumb_path)
                img = CustomImage(pixbuf=pb)
                img.set_valign(Gtk.Align.START)
                image_box.add(img)
                del pb # Мгновенная сборка мусора
            except Exception: pass

        summary_box = Box(name="notification-summary-box", orientation="h", children=[
            Label(name="notification-summary", markup=str(getattr(notif, 'summary', ''))[:40], h_align="start", max_chars_width=18, ellipsization="end"),
            Box(name="notif-sep"),
            Label(name="notification-timestamp", label=arrival_time.strftime("%H:%M"), h_align="start"),
        ])

        text_box = Box(name="notification-text", orientation="v", v_align="center", h_expand=True, children=[summary_box])
        body = getattr(notif, 'body', None)
        if body:
            text_box.add(Label(name="notification-body", markup=str(body)[:80], h_align="start", line_wrap="word-char", max_chars_width=34))
        
        close_btn = Button(name="notif-close-button", child=Label(name="notif-close-label", markup=icons.cancel))
        close_btn.connect("clicked", self._on_hist_close_clicked, notification_box.uuid, weakref.ref(container))

        container.add(Box(name="notification-box-hist", spacing=8, children=[
            image_box, text_box, Box(orientation="v", v_align="center", children=[close_btn])
        ]))
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
        self._update_empty_state()
        gc.collect()

    def clear_history_for_app(self, app_name):
        if self._is_destroyed or app_name not in self.groups: return
        group = self.groups.pop(app_name)
        nids = set(group.notification_ids)

        self.persistent_notifications = [n for n in self.persistent_notifications if n.get("id") not in nids]
        self._schedule_save()

        for nid in nids:
            delete_notification_image(nid)
            container = self.containers_by_id.pop(nid, None)
            if container:
                try: self.containers.remove(container)
                except ValueError: pass
                nb = getattr(container, "notification_box", None)
                if nb:
                    nb.destroy(from_history_delete=True)
                    container.notification_box = None
                container.destroy()

        self.notifications_list.remove(group)
        group.destroy()
        self._update_empty_state()
        gc.collect()

    def delete_historical_notification(self, note_id, container):
        if self._is_destroyed: return
        nb = getattr(container, "notification_box", None)
        app_name = getattr(nb.notification, 'app_name', None) if nb and nb.notification else None
        
        if nb: 
            nb.destroy(from_history_delete=True)
            container.notification_box = None

        nid_str = str(note_id)
        for i, n in enumerate(self.persistent_notifications):
            if str(n.get("id")) == nid_str:
                self.persistent_notifications.pop(i)
                delete_notification_image(note_id)
                self._schedule_save()
                break
            
        self.containers_by_id.pop(note_id, None)
        if container in self.containers: self.containers.remove(container)

        if app_name in self.groups:
            g = self.groups[app_name]
            g.remove_notification_id(note_id)
            if g.get_notification_count() == 0:
                self.notifications_list.remove(g)
                g.destroy()
                del self.groups[app_name]
            else: 
                g.update_display(self.containers_by_id)

        if container: container.destroy()
        self._update_empty_state()

    def _start_loading(self):
        if not self._loading:
            self._loading = True
            submit_io_task(self._load_from_file)
        return GLib.SOURCE_REMOVE

    def _load_from_file(self):
        data = []
        if os.path.exists(PERSISTENT_HISTORY_FILE):
            try:
                with open(PERSISTENT_HISTORY_FILE, "r", encoding="utf-8") as f: data = json.load(f)
            except: pass
        GLib.idle_add(self._on_loaded, data, priority=GLib.PRIORITY_LOW)

    def _on_loaded(self, data):
        if self._is_destroyed: return
        self.persistent_notifications = data if isinstance(data, list) else []
        if self.persistent_notifications: 
            self._process_loaded_batch(self.persistent_notifications[::-1], 0)
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
            submit_io_task(lambda: self._save_to_file_sync(list(self.persistent_notifications)))
        return GLib.SOURCE_REMOVE

    def _add_historical_notification(self, note, rebuild=True):
        if not isinstance(note, dict): return
        hist = HistoricalNotification(
            id=note.get("id"), app_icon=note.get("app_icon"),
            summary=note.get("summary", ""), body=note.get("body", ""),
            app_name=note.get("app_name", "Unknown"), timestamp=note.get("timestamp"),
        )
        box = NotificationBox(hist, timeout_ms=0)
        box.set_is_history(True)
        try: arrival = datetime.fromisoformat(hist.timestamp)
        except: arrival = datetime.now()
        
        container = self._create_history_container(box, arrival)
        self.containers.insert(0, container)
        self.containers_by_id[box.uuid] = container
        if rebuild: self._rebuild_with_groups()

    def add_notification(self, notification_box):
        if self._is_destroyed: return
        
        notif = notification_box.notification
        app_name = str(getattr(notif, 'app_name', 'Unknown'))[:30]
        uuid = notification_box.uuid
        now = datetime.now()

        if app_name in get_history_ignored_apps():
            notification_box.destroy(from_history_delete=True)
            return

        img_path = getattr(notification_box, '_thumb_path', None)

        hist_data = {
            "id": uuid, "app_icon": img_path, "summary": str(getattr(notif, 'summary', ''))[:80],
            "body": str(getattr(notif, 'body', ''))[:150], "app_name": app_name,
            "timestamp": now.isoformat()
        }
        self.persistent_notifications.insert(0, hist_data)
        self._schedule_save()

        hist_notif = HistoricalNotification(
            id=uuid, app_icon=img_path, summary=hist_data["summary"],
            body=hist_data["body"], app_name=app_name, timestamp=hist_data["timestamp"]
        )

        notification_box.destroy(from_history_delete=True)

        light_box = NotificationBox(hist_notif, timeout_ms=0)
        light_box.set_is_history(True)

        while len(self.containers) >= MAX_NOTIFICATION_HISTORY:
            oldest = self.containers.pop()
            nb = getattr(oldest, "notification_box", None)
            if nb:
                app_n = getattr(nb.notification, 'app_name', None)
                if app_n and app_n in self.groups:
                    g = self.groups[app_n]
                    g.remove_notification_id(nb.uuid)
                    if g.get_notification_count() == 0:
                        self.notifications_list.remove(g)
                        g.destroy()
                        del self.groups[app_n]
                    else:
                        g.update_display(self.containers_by_id)
                self.containers_by_id.pop(nb.uuid, None)
                delete_notification_image(nb.uuid)
                nb.destroy(from_history_delete=True)
                oldest.notification_box = None 
            oldest.destroy()
            
            old_data = self.persistent_notifications.pop() if len(self.persistent_notifications) > MAX_NOTIFICATION_HISTORY else None
            if old_data: delete_notification_image(old_data.get("id"))

        container = self._create_history_container(light_box, now)
        self.containers.insert(0, container)
        self.containers_by_id[uuid] = container

        g = self.groups.get(app_name)
        if not g:
            g = NotificationGroup(app_name, self, is_expanded=True)
            self.groups[app_name] = g
            self.notifications_list.add(g)
            
        g.add_notification_id(uuid, now)
        g.update_display(self.containers_by_id)
        # Поднимаем новую группу в самый верх истории
        self.notifications_list.reorder_child(g, 0)
        g.show_all()
        self._update_empty_state()

    @staticmethod
    def _clear_files_sync():
        if os.path.exists(PERSISTENT_HISTORY_FILE):
            try: os.remove(PERSISTENT_HISTORY_FILE)
            except: pass

    @staticmethod
    def _save_to_file_sync(data):
        try:
            tmp_file = PERSISTENT_HISTORY_FILE + ".tmp"
            os.makedirs(os.path.dirname(PERSISTENT_HISTORY_FILE), exist_ok=True)
            with open(tmp_file, "w", encoding="utf-8") as f: json.dump(data, f, separators=(",", ":"))
            os.replace(tmp_file, PERSISTENT_HISTORY_FILE)
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
            name="notification-main-revealer", transition_type=transition, transition_duration=150,
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
        if hasattr(n, 'connect'): n.connect("closed", self._on_closed)
        
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
        nid = getattr(notification, 'id', None)
        if not nid or self._is_destroying or nid in self._destroyed_ids: return
        self._destroyed_ids.add(nid)
        
        idx = next((i for i, nb in enumerate(self.notifications) if nb.notification and getattr(nb.notification, 'id', None) == nid), -1)
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
        gc.collect()
        return GLib.SOURCE_REMOVE

    def _close_all(self, *_):
        for nb in tuple(self.notifications):
            if nb.notification and hasattr(nb.notification, 'close'): nb.notification.close("dismissed-by-user")

    def destroy(self):
        self._is_destroying = True
        if self._server and self._server_handler: 
            self._server.disconnect(self._server_handler)
            self._server_handler = None
        for nb in self.notifications: nb.destroy()
        self.notifications.clear()
        super().destroy()