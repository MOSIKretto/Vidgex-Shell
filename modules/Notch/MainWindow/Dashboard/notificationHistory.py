import json
import locale
import os
import weakref
from datetime import date, datetime, timedelta
from pathlib import Path

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow
from gi.repository import GLib, Gtk

import services.icons as icons
from services.Notifications.notificationServer import NotificationServer

from modules.Notch.MainWindow.Dashboard.NotificationHistory.historyEntry import (
    HistoricalNotification,
    HistoryEntry,
)
from modules.Notch.MainWindow.Dashboard.NotificationHistory.historyStorage import (
    MAX_NOTIFICATION_HISTORY,
    PERSISTENT_HISTORY_FILE,
    cleanup_orphan_images,
    clear_all_notification_images,
    delete_notification_image,
    submit_io_task,
)
from modules.Notch.MainWindow.Dashboard.NotificationHistory.notificationGroup import (
    NOTIFICATION_WIDTH,
    NotificationGroup,
)

locale.setlocale(locale.LC_TIME, "")


def _format_date_category(dt: datetime) -> str:
    today = date.today()
    target = dt.date()
    loc = (locale.getlocale(locale.LC_TIME)[0] or os.environ.get("LANG", "")).lower()
    is_ru = loc.startswith("ru")
    if target == today:
        return "Сегодня" if is_ru else "Today"
    if target == today - timedelta(days=1):
        return "Вчера" if is_ru else "Yesterday"
    return dt.strftime("%d %B" if target.year == today.year else "%d %B %Y").strip()


def _format_time(arrival_time: datetime) -> str:
    time_str = arrival_time.strftime("%H:%M")
    if arrival_time.date() == date.today():
        return time_str
    fmt = "%d.%m" if arrival_time.year == date.today().year else "%d.%m.%y"
    return f"{arrival_time.strftime(fmt)} {time_str}"


class NotificationHistory(Box):
    def __init__(self, server: NotificationServer | None = None, **kwargs):
        super().__init__(name="notification-history", spacing=4, orientation="vertical", **kwargs)
        self._server = server if server is not None else NotificationServer.get_default()

        self.containers: list[Box] = []
        self.containers_by_id: dict[str, Box] = {}
        self.groups: dict[str, NotificationGroup] = {}
        self.persistent_notifications: list[dict] = []

        self._date_boxes: dict[str, Box] = {}
        self._group_category: dict[str, str] = {}

        self._loading = False
        self._save_timer_id = None
        self._is_destroyed = False

        self._build_ui()
        self.connect("destroy", self._on_destroy)
        self._server_handlers = (
            self._server.connect("notification-added", self._on_notification_added),
            self._server.connect("notification-dnd-added", self._on_notification_added),
        )
        GLib.idle_add(self._start_loading, priority=GLib.PRIORITY_LOW)

    def _build_ui(self) -> None:
        self.header_switch = Gtk.Switch(name="dnd-switch", vexpand=False, valign=Gtk.Align.CENTER)
        self.header_switch.set_active(self._server.dnd)
        self.header_switch.connect("notify::active", self._on_dnd_toggled)
        header = CenterBox(
            name="notification-history-header",
            start_children=[Box(orientation="h", children=[
                self.header_switch,
                Label(name="dnd-label", markup=icons.notifications_off),
            ])],
            center_children=[Label(name="nhh", label="Notifications", h_align="start", h_expand=True)],
            end_children=[Button(
                name="nhh-button",
                child=Label(name="nhh-button-label", markup=icons.trash),
                on_clicked=self.clear_history,
            )],
        )
        self.notifications_list = Box(name="notifications-list", orientation="vertical", spacing=4)
        self.notifications_list.set_size_request(NOTIFICATION_WIDTH, -1)
        self.notifications_list.set_no_show_all(True)

        self.no_notifications_box = Box(
            name="no-notifications-box",
            v_align="center", h_align="center", v_expand=True, h_expand=True,
            children=[Label(name="no-notif", markup=icons.notifications_clear, justification="center")],
        )
        self.no_notifications_box.set_no_show_all(True)

        self.scroll = ScrolledWindow(
            name="bluetooth-devices",
            min_content_size=(-1, -1),
            child=Box(spacing=4, orientation="vertical",
                      children=[self.notifications_list, self.no_notifications_box]),
            v_expand=True,
            propagate_width=False,
            propagate_height=False,
        )
        self.add(header)
        self.add(self.scroll)

    def _on_dnd_toggled(self, switch, _pspec) -> None:
        self._server.dnd = switch.get_active()

    def _on_notification_added(self, server, notif_id: int) -> None:
        self.add_notification(server.get_data(notif_id))

    def _make_date_box(self, category: str) -> Box:
        return Box(
            name="notification-date-box",
            orientation="horizontal",
            children=[Label(name="notification-date-header", label=category, h_align="start")],
        )

    def _list_children(self) -> list[Gtk.Widget]:
        return self.notifications_list.get_children()

    def _sorted_groups_for_category(self, category: str) -> list[str]:
        return sorted(
            [g for g, c in self._group_category.items()
             if c == category and g in self.groups],
            key=lambda app: self.groups[app].latest_arrival_time or datetime.min,
            reverse=True,
        )

    def _insert_group(self, group: NotificationGroup) -> None:
        app = group.app_name
        category = _format_date_category(group.latest_arrival_time)
        old_category = self._group_category.get(app)

        parent = group.get_parent()
        if parent is not None:
            parent.remove(group)

        self._group_category[app] = category

        if old_category and old_category != category:
            if not self._sorted_groups_for_category(old_category):
                date_box = self._date_boxes.pop(old_category, None)
                if date_box is not None:
                    p = date_box.get_parent()
                    if p is not None:
                        p.remove(date_box)
                    date_box.destroy()

        if category not in self._date_boxes:
            self._date_boxes[category] = self._make_date_box(category)

        date_box = self._date_boxes[category]
        children = self._list_children()
        category_order = self._category_order()
        target_idx = self._compute_insert_index(category, category_order, children, group)

        block_start = self._find_block_start(category, children)
        if block_start is None:
            self.notifications_list.pack_start(date_box, False, False, 0)
            self.notifications_list.reorder_child(date_box, target_idx)
            self.notifications_list.pack_start(group, False, False, 0)
            self.notifications_list.reorder_child(group, target_idx + 1)
        else:
            self.notifications_list.pack_start(group, False, False, 0)
            self.notifications_list.reorder_child(group, target_idx)

        group.show_all()

    def _category_order(self) -> list[str]:
        best: dict[str, datetime] = {}
        for app, cat in self._group_category.items():
            if app not in self.groups:
                continue
            t = self.groups[app].latest_arrival_time
            if t and (cat not in best or t > best[cat]):
                best[cat] = t
        return sorted(best, key=lambda c: best[c], reverse=True)

    def _find_block_start(self, category: str, children: list) -> int | None:
        date_box = self._date_boxes.get(category)
        if date_box is None:
            return None
        try:
            return children.index(date_box)
        except ValueError:
            return None

    def _compute_insert_index(self, category: str, category_order: list[str], children: list, group: NotificationGroup) -> int:
        cat_idx = category_order.index(category) if category in category_order else len(category_order)

        idx = 0
        for child in children:
            child_cat = self._widget_category(child)
            if child_cat is None:
                continue
            if category_order.index(child_cat) < cat_idx if child_cat in category_order else True:
                idx += 1
            else:
                break

        if self._find_block_start(category, children) is not None:
            idx += 1

        for sibling_app in self._sorted_groups_for_category(category):
            if sibling_app == group.app_name:
                break
            sibling = self.groups.get(sibling_app)
            if sibling is not None and sibling.get_parent() is not None:
                idx += 1

        return idx

    def _widget_category(self, widget: Gtk.Widget) -> str | None:
        for app, g in self.groups.items():
            if g is widget:
                return self._group_category.get(app)
        return None

    def _remove_group_from_list(self, app_name: str) -> None:
        category = self._group_category.pop(app_name, None)
        group = self.groups.get(app_name)
        if group is not None:
            parent = group.get_parent()
            if parent is not None:
                parent.remove(group)

        if category and not self._sorted_groups_for_category(category):
            date_box = self._date_boxes.pop(category, None)
            if date_box is not None:
                p = date_box.get_parent()
                if p is not None:
                    p.remove(date_box)
                date_box.destroy()

    def _destroy_container(self, container: Box) -> None:
        parent = container.get_parent()
        if parent is not None:
            parent.remove(container)
        container.notification_box.destroy()
        container.notification_box = None
        container.destroy()

    def _destroy_group(self, group: NotificationGroup) -> None:
        group.clear_containers()
        parent = group.get_parent()
        if parent is not None:
            parent.remove(group)
        group.destroy()

    def _update_empty_state(self) -> None:
        has = bool(self.containers) or self._loading
        self.no_notifications_box.set_visible(not has)
        self.notifications_list.set_visible(has)

    def _sync_group(self, app_name: str) -> None:
        if self._is_destroyed:
            return
        group = self.groups.get(app_name)
        if group is None:
            return
        group.update_display(self.containers_by_id)
        if group.latest_arrival_time:
            self._insert_group(group)
        self.notifications_list.show_all()
        self._update_empty_state()

    def _detach_from_group(self, note_id: str) -> None:
        for app_name, group in list(self.groups.items()):
            if note_id in group.notification_ids:
                empty = group.remove_notification_id(note_id)
                if empty:
                    self._remove_group_from_list(app_name)
                    self._destroy_group(self.groups.pop(app_name))
                else:
                    self._sync_group(app_name)
                break

    def _create_history_container(self, notification_box: HistoryEntry, arrival_time: datetime) -> Box:
        container = Box(name="notification-container", orientation="v", h_align="fill", h_expand=True)
        container.set_size_request(NOTIFICATION_WIDTH, -1)
        container.arrival_time = arrival_time
        container.notification_box = notification_box
        notification_box.set_hexpand(True)

        close_btn = Button(
            name="notif-close-button",
            child=Label(name="notif-close-label", markup=icons.cancel),
            on_clicked=lambda *_: self._on_hist_close(notification_box.uuid, weakref.ref(container)),
        )
        actions_box = Box(
            orientation="h", spacing=6, v_align="center", h_align="end",
            children=[
                Label(name="notif-time-label", label=_format_time(arrival_time), v_align="center"),
                close_btn,
            ],
        )
        container.add(Box(name="notification-box-hist", spacing=8, h_expand=True,
                          children=[notification_box, actions_box]))
        return container

    def _on_hist_close(self, uuid: str, cont_ref) -> None:
        if self._is_destroyed:
            return
        cont = cont_ref()
        if cont is not None:
            self.delete_historical_notification(uuid, cont)

    def add_notification(self, data) -> None:
        if self._is_destroyed:
            return

        hist_data = {
            "id": data.uuid,
            "summary": data.summary,
            "body": data.body,
            "app_name": data.app_name,
            "timestamp": data.timestamp.isoformat(),
        }
        self.persistent_notifications.insert(0, hist_data)
        self._schedule_save()

        hist_notif = HistoricalNotification(
            id=data.uuid,
            summary=data.summary,
            body=data.body,
            app_name=data.app_name,
            timestamp=hist_data["timestamp"],
        )
        hist_box = HistoryEntry(hist_notif, self._server, pixbuf=data.thumbnail)
        self._evict_oldest_if_needed()

        container = self._create_history_container(hist_box, data.timestamp)
        self.containers.insert(0, container)
        self.containers_by_id[data.uuid] = container

        if data.app_name not in self.groups:
            self.groups[data.app_name] = NotificationGroup(data.app_name, self, is_expanded=False)
        self.groups[data.app_name].add_notification_id(data.uuid, data.timestamp)
        self._sync_group(data.app_name)

    def clear_history(self, *_) -> None:
        if self._is_destroyed:
            return

        for child in list(self.notifications_list.get_children()):
            self.notifications_list.remove(child)
            child.destroy()

        for date_box in self._date_boxes.values():
            p = date_box.get_parent()
            if p is not None:
                p.remove(date_box)
            date_box.destroy()
        self._date_boxes.clear()
        self._group_category.clear()

        for g in list(self.groups.values()):
            self._destroy_group(g)
        self.groups.clear()

        for c in self.containers:
            self._destroy_container(c)
        self.containers.clear()
        self.containers_by_id.clear()
        self.persistent_notifications.clear()

        submit_io_task(lambda: Path(PERSISTENT_HISTORY_FILE).unlink(missing_ok=True))
        clear_all_notification_images()
        self._update_empty_state()

    def clear_history_for_app(self, app_name: str) -> None:
        if self._is_destroyed:
            return

        group = self.groups.pop(app_name, None)
        nids = set(group.notification_ids) if group else set()

        if nids:
            self.persistent_notifications = [
                n for n in self.persistent_notifications if n.get("id") not in nids
            ]
            self._schedule_save()
            for nid in nids:
                delete_notification_image(nid)
                container = self.containers_by_id.pop(nid, None)
                if container is not None:
                    if container in self.containers:
                        self.containers.remove(container)
                    self._destroy_container(container)

        if group is not None:
            self._remove_group_from_list(app_name)
            self._destroy_group(group)

        self._update_empty_state()

    def delete_historical_notification(self, note_id: str, container: Box) -> None:
        if self._is_destroyed:
            return

        self.persistent_notifications = [
            n for n in self.persistent_notifications if n.get("id") != note_id
        ]
        delete_notification_image(note_id)
        self._schedule_save()

        self.containers_by_id.pop(note_id, None)
        if container in self.containers:
            self.containers.remove(container)
        self._destroy_container(container)

        self._detach_from_group(note_id)

        self._update_empty_state()

    def _start_loading(self) -> bool:
        if not self._loading:
            self._loading = True
            self._update_empty_state()
            submit_io_task(self._load_from_file)
        return GLib.SOURCE_REMOVE

    def _load_from_file(self) -> None:
        data = []
        if os.path.isfile(PERSISTENT_HISTORY_FILE):
            try:
                with open(PERSISTENT_HISTORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                data = []
        GLib.idle_add(self._on_loaded, data, priority=GLib.PRIORITY_LOW)

    def _on_loaded(self, loaded: list) -> bool:
        if self._is_destroyed:
            return GLib.SOURCE_REMOVE

        existing_ids = {n.get("id") for n in self.persistent_notifications}
        for n in loaded:
            if n.get("id") not in existing_ids:
                self.persistent_notifications.append(n)
        self.persistent_notifications = self.persistent_notifications[:MAX_NOTIFICATION_HISTORY]

        if self.persistent_notifications:
            self._process_loaded_batch(self.persistent_notifications[::-1], 0)
        else:
            self._finish_loading()

        cleanup_orphan_images([n.get("id") for n in self.persistent_notifications])
        return GLib.SOURCE_REMOVE

    def _process_loaded_batch(self, notes: list, idx: int) -> bool:
        if self._is_destroyed:
            return GLib.SOURCE_REMOVE
        for i in range(idx, min(idx + 5, len(notes))):
            self._add_historical_notification(notes[i])
        end = idx + 5
        if end < len(notes):
            GLib.idle_add(self._process_loaded_batch, notes, end, priority=GLib.PRIORITY_LOW)
        else:
            self._finish_loading()
        return GLib.SOURCE_REMOVE

    def _finish_loading(self) -> None:
        if self._is_destroyed:
            return
        self._loading = False
        self._full_rebuild()

    def _full_rebuild(self) -> None:
        if self._is_destroyed:
            return

        for child in list(self.notifications_list.get_children()):
            self.notifications_list.remove(child)
            if child.get_name() == "notification-date-box":
                child.destroy()

        for date_box in self._date_boxes.values():
            date_box.destroy()
        self._date_boxes.clear()
        self._group_category.clear()

        app_notifs: dict[str, list[tuple[str, datetime]]] = {}
        for container in self.containers:
            nb = container.notification_box
            app = (nb.notification.app_name or "Unknown")[:30]
            app_notifs.setdefault(app, []).append((nb.uuid, container.arrival_time))

        dead_apps = [a for a in self.groups if a not in app_notifs]
        for app in dead_apps:
            self._destroy_group(self.groups.pop(app))

        for app, items in app_notifs.items():
            if app not in self.groups:
                self.groups[app] = NotificationGroup(app, self, is_expanded=False)
            g = self.groups[app]
            g.clear_containers()
            for nid, arr in items:
                g.add_notification_id(nid, arr)
            g.update_display(self.containers_by_id)

        sorted_groups = sorted(
            self.groups.values(),
            key=lambda g: g.latest_arrival_time or datetime.min,
            reverse=True,
        )

        current_cat = None
        for g in sorted_groups:
            if g.latest_arrival_time:
                cat = _format_date_category(g.latest_arrival_time)
                if cat != current_cat:
                    current_cat = cat
                    db = self._make_date_box(cat)
                    self._date_boxes[cat] = db
                    self.notifications_list.add(db)
                self._group_category[g.app_name] = cat
                self.notifications_list.add(g)

        self.notifications_list.show_all()
        self._update_empty_state()

    def _add_historical_notification(self, note: dict) -> None:
        hist = HistoricalNotification(
            id=note.get("id"),
            summary=note.get("summary", ""),
            body=note.get("body", ""),
            app_name=note.get("app_name", "Unknown"),
            timestamp=note.get("timestamp"),
        )
        box = HistoryEntry(hist, self._server)
        arrival = datetime.fromisoformat(hist.timestamp) if hist.timestamp else datetime.now()
        container = self._create_history_container(box, arrival)
        self.containers.insert(0, container)
        self.containers_by_id[box.uuid] = container

    def _evict_oldest_if_needed(self) -> None:
        while len(self.containers) >= MAX_NOTIFICATION_HISTORY:
            oldest = self.containers.pop()
            old_uuid = oldest.notification_box.uuid if oldest.notification_box else None
            if old_uuid:
                self.containers_by_id.pop(old_uuid, None)
                delete_notification_image(old_uuid)
                self.persistent_notifications = [
                    n for n in self.persistent_notifications if n.get("id") != old_uuid
                ]
                self._detach_from_group(old_uuid)
            self._destroy_container(oldest)

    def _schedule_save(self) -> None:
        if self._save_timer_id is not None:
            GLib.source_remove(self._save_timer_id)
        self._save_timer_id = GLib.timeout_add(1000, self._do_save)

    def _do_save(self) -> bool:
        self._save_timer_id = None
        if not self._is_destroyed:
            submit_io_task(lambda s=list(self.persistent_notifications): self._save_sync(s))
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _save_sync(data: list) -> None:
        tmp = f"{PERSISTENT_HISTORY_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, PERSISTENT_HISTORY_FILE)

    def _on_destroy(self, _widget) -> None:
        if self._is_destroyed:
            return
        self._is_destroyed = True

        for handler in self._server_handlers:
            self._server.disconnect(handler)

        if self._save_timer_id is not None:
            GLib.source_remove(self._save_timer_id)
            self._save_timer_id = None
            submit_io_task(lambda s=list(self.persistent_notifications): self._save_sync(s))

        for date_box in self._date_boxes.values():
            date_box.destroy()
        self._date_boxes.clear()
        self._group_category.clear()

        for g in list(self.groups.values()):
            self._destroy_group(g)
        self.groups.clear()

        for c in self.containers:
            self._destroy_container(c)
        self.containers.clear()
        self.containers_by_id.clear()
        self.persistent_notifications.clear()