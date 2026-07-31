import json
import os
import weakref
from datetime import datetime

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow

from gi.repository import GLib, Gtk

from .notificationBox import (
    NotificationBox,
    NotificationGroup,
    HistoricalNotification,
    get_history_ignored_apps,
    delete_notification_image,
    clear_all_notification_images,
    submit_io_task,
    cleanup_orphan_images,
    set_pointer_cursor,
    PERSISTENT_HISTORY_FILE,
    MAX_NOTIFICATION_HISTORY,
    NOTIFICATION_WIDTH,
)
import services.icons as icons


class NotificationHistory(Box):
    __slots__ = (
        "containers", "groups", "containers_by_id", "_save_timer_id",
        "_dnd_handler", "header_switch", "glyphs_switch", "glyphs_enabled",
        "trigger_glyphs_callback", "do_not_disturb_enabled",
        "notifications_list", "no_notifications_box", "scroll",
        "persistent_notifications", "_is_destroyed", "_loading",
    )

    def __init__(self, **kwargs):
        super().__init__(
            name="notification-history",
            spacing=4,
            orientation="vertical",
            **kwargs,
        )
        self.containers: list[Box] = []
        self.containers_by_id: dict[str, Box] = {}
        self.groups: dict[str, NotificationGroup] = {}
        self.persistent_notifications: list[dict] = []

        self._loading        = False
        self._save_timer_id  = None
        self._dnd_handler    = None
        self._is_destroyed   = False

        self.do_not_disturb_enabled  = False
        self.glyphs_enabled          = True

        self.trigger_glyphs_callback = None

        self._build_ui()
        GLib.idle_add(self._start_loading, priority=GLib.PRIORITY_LOW)

    # UI
    def _build_ui(self) -> None:
        self.header_switch = Gtk.Switch(
            name="dnd-switch", vexpand=False, valign=Gtk.Align.CENTER
        )
        self._dnd_handler = self.header_switch.connect(
            "notify::active", self._on_dnd_changed
        )
        set_pointer_cursor(self.header_switch)

        self.glyphs_switch = Gtk.Switch(
            name="dnd-switch", vexpand=False, valign=Gtk.Align.CENTER
        )
        self.glyphs_switch.set_active(False)
        self.glyphs_switch.connect("notify::active", self._on_glyphs_changed)
        set_pointer_cursor(self.glyphs_switch)

        clear_btn = Button(
            name="nhh-button",
            child=Label(name="nhh-button-label", markup=icons.trash),
            on_clicked=self.clear_history,
        )
        set_pointer_cursor(clear_btn)

        header = CenterBox(
            name="notification-history-header",
            start_children=[
                Box(
                    orientation="h",
                    spacing=12,
                    children=[
                        Box(
                            orientation="h",
                            children=[
                                self.header_switch,
                                Label(name="dnd-label", markup=icons.notifications_off),
                            ],
                        ),
                        Box(
                            orientation="h",
                            children=[
                                self.glyphs_switch,
                                Label(name="dnd-label", markup=icons.bulb_off),
                            ],
                        ),
                    ],
                )
            ],
            center_children=[
                Label(
                    name="nhh",
                    label="Notifications",
                    h_align="start",
                    h_expand=True,
                )
            ],
            end_children=[clear_btn],
        )

        self.notifications_list = Box(
            name="notifications-list", orientation="vertical", spacing=4
        )
        self.notifications_list.set_size_request(NOTIFICATION_WIDTH, -1)
        self.notifications_list.set_no_show_all(True)

        self.no_notifications_box = Box(
            name="no-notifications-box",
            v_align="center",
            h_align="center",
            v_expand=True,
            h_expand=True,
            children=[
                Label(
                    name="no-notif",
                    markup=icons.notifications_clear,
                    justification="center",
                )
            ],
        )
        self.no_notifications_box.set_no_show_all(True)

        self.scroll = ScrolledWindow(
            name="bluetooth-devices",
            min_content_size=(-1, -1),
            child=Box(
                spacing=4,
                orientation="vertical",
                children=[self.notifications_list, self.no_notifications_box],
            ),
            v_expand=True,
            propagate_width=False,
            propagate_height=False,
        )

        self.children = [header, self.scroll]

    # Переключатели
    def _on_dnd_changed(self, switch, _) -> None:
        self.do_not_disturb_enabled = switch.get_active()

    def _on_glyphs_changed(self, switch, _) -> None:
        self.glyphs_enabled = not switch.get_active()

    # Пустой список

    def _update_empty_state(self) -> None:
        has = bool(self.containers) or self._loading
        self.no_notifications_box.set_visible(not has)
        self.notifications_list.set_visible(has)

    # Перестройка группировки
    def _rebuild_with_groups(self) -> None:
        if self._is_destroyed:
            return
        expanded = {name: g.is_expanded for name, g in self.groups.items()}

        for child in list(self.notifications_list.get_children()):
            self.notifications_list.remove(child)
        for g in self.groups.values():
            g.clear_containers()
            g.destroy()
        self.groups.clear()

        for container in self.containers:
            nb = getattr(container, "notification_box", None)
            if not (nb and nb.notification):
                continue
            app = getattr(nb.notification, "app_name", "Unknown")
            if app not in self.groups:
                self.groups[app] = NotificationGroup(
                    app, self, is_expanded=expanded.get(app, False)
                )
            self.groups[app].add_notification_id(nb.uuid, container.arrival_time)

        for g in self.groups.values():
            g.update_display(self.containers_by_id)

        for g in sorted(
            self.groups.values(),
            key=lambda grp: grp.latest_arrival_time or datetime.min,
            reverse=True,
        ):
            self.notifications_list.add(g)

        self.notifications_list.show_all()
        self._update_empty_state()

    # Контейнер истории
    def _create_history_container(self, notification_box: NotificationBox, arrival_time: datetime) -> Box:
        container = Box(
            name="notification-container",
            orientation="v",
            h_align="fill",
            h_expand=True,
        )
        container.set_size_request(NOTIFICATION_WIDTH, -1)
        container.arrival_time     = arrival_time
        container.notification_box = notification_box

        close_btn = Button(
            name="notif-close-button",
            child=Label(name="notif-close-label", markup=icons.cancel),
        )
        close_btn.connect(
            "clicked",
            self._on_hist_close_clicked,
            notification_box.uuid,
            weakref.ref(container),
        )
        set_pointer_cursor(close_btn)

        row = Box(
            name="notification-box-hist",
            spacing=8,
            h_expand=True,
            children=[
                notification_box,
                Box(orientation="v", v_align="center", children=[close_btn]),
            ],
        )
        container.add(row)
        return container

    def _on_hist_close_clicked(self, _btn, uuid: str, cont_ref) -> None:
        if self._is_destroyed:
            return
        cont = cont_ref()
        if cont is not None:
            self.delete_historical_notification(uuid, cont)

    # Публичные операции
    def add_notification(self, notification_box: NotificationBox) -> None:
        if self._is_destroyed:
            return

        notif    = notification_box.notification
        app_name = str(getattr(notif, "app_name", "Unknown"))[:30]
        uuid     = notification_box.uuid
        now      = datetime.now()

        if app_name in get_history_ignored_apps():
            notification_box.destroy(from_history_delete=True)
            return

        hist_data = {
            "id":        uuid,
            "summary":   str(getattr(notif, "summary", ""))[:80],
            "body":      str(getattr(notif, "body", ""))[:150],
            "app_name":  app_name,
            "timestamp": now.isoformat(),
        }
        self.persistent_notifications.insert(0, hist_data)
        self._schedule_save()

        notification_box.destroy(from_history_delete=True)

        hist_notif = HistoricalNotification(
            id=uuid,
            summary=hist_data["summary"],
            body=hist_data["body"],
            app_name=app_name,
            timestamp=hist_data["timestamp"],
        )
        hist_box = NotificationBox(hist_notif, timeout_ms=0, is_history=True)

        self._evict_oldest_if_needed()

        container = self._create_history_container(hist_box, now)
        self.containers.insert(0, container)
        self.containers_by_id[uuid] = container

        g = self.groups.get(app_name)
        if not g:
            g = NotificationGroup(app_name, self, is_expanded=True)
            self.groups[app_name] = g
            self.notifications_list.add(g)

        g.add_notification_id(uuid, now)
        g.update_display(self.containers_by_id)
        self.notifications_list.reorder_child(g, 0)
        g.show_all()
        self._update_empty_state()

    def clear_history(self, *_) -> None:
        if self._is_destroyed:
            return
        for g in self.groups.values():
            g.clear_containers()
        for child in list(self.notifications_list.get_children()):
            self.notifications_list.remove(child)
            try:
                child.destroy()
            except Exception:
                pass
        self.groups.clear()
        for c in self.containers:
            nb = getattr(c, "notification_box", None)
            if nb:
                nb.destroy(from_history_delete=True)
                c.notification_box = None
            try:
                c.destroy()
            except Exception:
                pass
        self.containers.clear()
        self.containers_by_id.clear()
        self.persistent_notifications.clear()
        submit_io_task(self._clear_files_sync)
        clear_all_notification_images()
        self._update_empty_state()

    def clear_history_for_app(self, app_name: str) -> None:
        if self._is_destroyed or app_name not in self.groups:
            return
        group    = self.groups.pop(app_name)
        nids_set = set(group.notification_ids)

        self.persistent_notifications = [
            n for n in self.persistent_notifications
            if n.get("id") not in nids_set
        ]
        self._schedule_save()
        group.clear_containers()

        for nid in list(nids_set):
            delete_notification_image(nid)
            container = self.containers_by_id.pop(nid, None)
            if container is None:
                continue
            if container in self.containers:
                self.containers.remove(container)
            parent = container.get_parent()
            if parent:
                parent.remove(container)
            nb = getattr(container, "notification_box", None)
            if nb:
                nb.destroy(from_history_delete=True)
                container.notification_box = None
            try:
                container.destroy()
            except Exception:
                pass

        parent = group.get_parent()
        if parent:
            parent.remove(group)
        group.destroy()
        self._update_empty_state()

    def delete_historical_notification(self, note_id: str, container: Box) -> None:
        if self._is_destroyed:
            return
        nb = getattr(container, "notification_box", None)
        app_name = (
            getattr(nb.notification, "app_name", None)
            if nb and nb.notification
            else None
        )
        if nb:
            nb.destroy(from_history_delete=True)
            container.notification_box = None

        self.persistent_notifications = [
            n for n in self.persistent_notifications if n.get("id") != note_id
        ]
        delete_notification_image(note_id)
        self._schedule_save()

        self.containers_by_id.pop(note_id, None)
        if container in self.containers:
            self.containers.remove(container)
        parent = container.get_parent()
        if parent:
            parent.remove(container)

        if app_name and app_name in self.groups:
            g = self.groups[app_name]
            g.remove_notification_id(note_id)
            if g.get_notification_count() == 0:
                gp = g.get_parent()
                if gp:
                    gp.remove(g)
                g.destroy()
                del self.groups[app_name]
            else:
                g.update_display(self.containers_by_id)

        try:
            container.destroy()
        except Exception:
            pass
        self._update_empty_state()

    # Загрузка
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
            except Exception:
                pass
        GLib.idle_add(self._on_loaded, data, priority=GLib.PRIORITY_LOW)

    def _on_loaded(self, data) -> bool:
        if self._is_destroyed:
            return GLib.SOURCE_REMOVE
        loaded = data if isinstance(data, list) else []

        existing_ids = {n.get("id") for n in self.persistent_notifications}
        for n in loaded:
            if n.get("id") not in existing_ids:
                self.persistent_notifications.append(n)
        self.persistent_notifications = self.persistent_notifications[:MAX_NOTIFICATION_HISTORY]

        if self.persistent_notifications:
            self._process_loaded_batch(self.persistent_notifications[::-1], 0)
        else:
            self._finish_loading()

        active_ids = [n.get("id") for n in self.persistent_notifications]
        submit_io_task(lambda: cleanup_orphan_images(active_ids))
        return GLib.SOURCE_REMOVE

    def _process_loaded_batch(self, notes: list, idx: int) -> None:
        if self._is_destroyed:
            return
        end = min(idx + 5, len(notes))
        for i in range(idx, end):
            self._add_historical_notification(notes[i])
        if end < len(notes):
            GLib.idle_add(
                self._process_loaded_batch, notes, end,
                priority=GLib.PRIORITY_LOW,
            )
        else:
            self._finish_loading()

    def _finish_loading(self) -> bool:
        if not self._is_destroyed:
            self._loading = False
            self._rebuild_with_groups()
        return GLib.SOURCE_REMOVE

    def _add_historical_notification(self, note: dict) -> None:
        if not isinstance(note, dict):
            return
        hist = HistoricalNotification(
            id=note.get("id"),
            summary=note.get("summary", ""),
            body=note.get("body", ""),
            app_name=note.get("app_name", "Unknown"),
            timestamp=note.get("timestamp"),
        )
        box = NotificationBox(hist, timeout_ms=0, is_history=True)
        try:
            arrival = datetime.fromisoformat(hist.timestamp)
        except Exception:
            arrival = datetime.now()

        container = self._create_history_container(box, arrival)
        self.containers.insert(0, container)
        self.containers_by_id[box.uuid] = container

    # Вытеснение старых
    def _evict_oldest_if_needed(self) -> None:
        while len(self.containers) >= MAX_NOTIFICATION_HISTORY:
            oldest   = self.containers.pop()
            nb_old   = getattr(oldest, "notification_box", None)
            old_uuid = getattr(nb_old, "uuid", None) if nb_old else None
            old_app  = (
                getattr(nb_old.notification, "app_name", None)
                if nb_old and nb_old.notification else None
            )

            if old_uuid:
                if old_app and old_app in self.groups:
                    g = self.groups[old_app]
                    g.remove_notification_id(old_uuid)
                    if g.get_notification_count() == 0:
                        gp = g.get_parent()
                        if gp:
                            gp.remove(g)
                        g.destroy()
                        del self.groups[old_app]
                    else:
                        g.update_display(self.containers_by_id)
                self.containers_by_id.pop(old_uuid, None)
                delete_notification_image(old_uuid)
                self.persistent_notifications = [
                    n for n in self.persistent_notifications
                    if n.get("id") != old_uuid
                ]

            if nb_old:
                nb_old.destroy(from_history_delete=True)
                oldest.notification_box = None

            parent = oldest.get_parent()
            if parent:
                parent.remove(oldest)
            try:
                oldest.destroy()
            except Exception:
                pass

    # Сохранение
    def _schedule_save(self) -> None:
        if self._save_timer_id:
            GLib.source_remove(self._save_timer_id)
        self._save_timer_id = GLib.timeout_add(1000, self._do_save)

    def _do_save(self) -> bool:
        self._save_timer_id = None
        if not self._is_destroyed:
            snapshot = list(self.persistent_notifications)
            submit_io_task(lambda: self._save_to_file_sync(snapshot))
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _clear_files_sync() -> None:
        if os.path.isfile(PERSISTENT_HISTORY_FILE):
            try:
                os.remove(PERSISTENT_HISTORY_FILE)
            except OSError:
                pass

    @staticmethod
    def _save_to_file_sync(data: list) -> None:
        tmp = PERSISTENT_HISTORY_FILE + ".tmp"
        try:
            os.makedirs(os.path.dirname(PERSISTENT_HISTORY_FILE), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, PERSISTENT_HISTORY_FILE)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass

    # Уничтожение
    def destroy(self) -> None:
        if self._is_destroyed:
            return
        self._is_destroyed = True

        try:
            if self._dnd_handler and self.header_switch.handler_is_connected(self._dnd_handler):
                self.header_switch.disconnect(self._dnd_handler)
        except Exception:
            pass
        self._dnd_handler = None

        if self._save_timer_id:
            GLib.source_remove(self._save_timer_id)
            self._save_timer_id = None

        submit_io_task(
            lambda s=list(self.persistent_notifications): self._save_to_file_sync(s)
        )

        for g in self.groups.values():
            g.clear_containers()
            g.destroy()
        self.groups.clear()

        for c in self.containers:
            nb = getattr(c, "notification_box", None)
            if nb:
                nb.destroy(from_history_delete=True)
                c.notification_box = None
            try:
                c.destroy()
            except Exception:
                pass
        self.containers.clear()
        self.containers_by_id.clear()
        self.persistent_notifications.clear()
        super().destroy()


#Синглтон
_shared_history_instance: NotificationHistory | None = None

def get_shared_history() -> NotificationHistory:
    global _shared_history_instance
    if _shared_history_instance is None:
        _shared_history_instance = NotificationHistory()
    return _shared_history_instance