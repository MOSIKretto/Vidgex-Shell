import json
import os
import weakref
from datetime import datetime

from fabric.notifications.service import Notifications
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer
from fabric.widgets.scrolledwindow import ScrolledWindow

from gi.repository import GdkPixbuf, GLib, Gtk

from .notificationBox import (
    NotificationBox,
    NotificationGroup,
    HistoricalNotification,
    get_ignored_apps,
    is_valid_image,
    _delete_image,
    _clear_all_images,
    _cleanup_orphans,
    load_pixbuf,
    HISTORY_FILE,
    MAX_HISTORY,
    MAX_POPUPS,
    NOTIF_WIDTH,
    image_path_for,
)
from .notificationBox import _io as _io_worker
import services.icons as icons
from modules.Notifications.NotificationBox.image import CustomImage


def _submit(fn):
    _io_worker.submit(fn)


class NotificationHistory(Box):
    def __init__(self, **kwargs):
        super().__init__(
            name="notification-history",
            spacing=4,
            orientation="vertical",
            **kwargs,
        )

        self.containers: list[Box] = []
        self.by_id: dict[str, Box] = {}
        self.groups: dict[str, NotificationGroup] = {}
        self.data: list[dict] = []

        self._dead = False
        self._loading = False
        self._save_tid = None
        self._dnd_hid = None
        self.do_not_disturb_enabled = False

        self._build_ui()
        GLib.idle_add(self._begin_load, priority=GLib.PRIORITY_LOW)

    def _build_ui(self):
        self.dnd_switch = Gtk.Switch(
            name="dnd-switch", vexpand=False, valign=Gtk.Align.CENTER
        )
        self._dnd_hid = self.dnd_switch.connect(
            "notify::active",
            lambda sw, _: setattr(
                self, "do_not_disturb_enabled", sw.get_active()
            ),
        )

        header = CenterBox(
            name="notification-history-header",
            start_children=[
                self.dnd_switch,
                Label(name="dnd-label", markup=icons.notifications_off),
            ],
            center_children=[
                Label(
                    name="nhh",
                    label="Notifications",
                    h_align="start",
                    h_expand=True,
                ),
            ],
            end_children=[
                Button(
                    name="nhh-button",
                    child=Label(name="nhh-button-label", markup=icons.trash),
                    on_clicked=self.clear_all,
                ),
            ],
        )

        self.notif_list = Box(
            name="notifications-list", orientation="vertical", spacing=4
        )
        self.notif_list.set_size_request(NOTIF_WIDTH, -1)

        self.empty_box = Box(
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
                ),
            ],
        )

        self.scroll = ScrolledWindow(
            name="bluetooth-devices",
            min_content_size=(-1, -1),
            child=Box(
                spacing=4,
                orientation="vertical",
                children=[self.notif_list, self.empty_box],
            ),
            v_expand=True,
            propagate_width=False,
            propagate_height=False,
        )

        self.children = [header, self.scroll]

    def _sync_empty(self):
        has = bool(self.containers)
        self.empty_box.set_visible(not has)
        self.notif_list.set_visible(has)

    # ── Добавление уведомления ──

    def add_notification(self, nb: NotificationBox):
        if self._dead:
            return

        notif = nb.notification
        app = str(getattr(notif, "app_name", "Unknown"))[:30]
        uuid = nb.uuid
        now = datetime.now()

        if app in get_ignored_apps():
            nb.destroy(from_history_delete=True)
            return

        img = getattr(nb, "thumb_path", None)
        rec = {
            "id": uuid,
            "app_icon": img,
            "summary": str(getattr(notif, "summary", ""))[:80],
            "body": str(getattr(notif, "body", ""))[:150],
            "app_name": app,
            "timestamp": now.isoformat(),
        }
        self.data.insert(0, rec)
        self._schedule_save()

        nb.destroy(from_history_delete=True)

        hist = HistoricalNotification(
            id=uuid,
            app_icon=img,
            summary=rec["summary"],
            body=rec["body"],
            app_name=app,
            timestamp=rec["timestamp"],
        )
        box = NotificationBox(hist, timeout_ms=0)
        box.set_is_history(True)

        self._evict()
        container = self._make_container(box, now)
        self.containers.insert(0, container)
        self.by_id[uuid] = container

        g = self.groups.get(app)
        if not g:
            g = NotificationGroup(app, self, is_expanded=True)
            self.groups[app] = g
            self.notif_list.add(g)

        g.add_nid(uuid, now)
        g.refresh(self.by_id)
        self.notif_list.reorder_child(g, 0)
        g.show_all()
        self._sync_empty()

    # ── Удаление ──

    def delete_one(self, uid, container):
        if self._dead:
            return

        nb = getattr(container, "notification_box", None)
        app = (
            getattr(nb.notification, "app_name", None)
            if nb and nb.notification
            else None
        )

        if nb:
            nb.destroy(from_history_delete=True)
            container.notification_box = None

        for i, n in enumerate(self.data):
            if n.get("id") == uid:
                self.data.pop(i)
                _delete_image(uid)
                self._schedule_save()
                break

        self.by_id.pop(uid, None)
        if container in self.containers:
            self.containers.remove(container)

        p = container.get_parent()
        if p:
            p.remove(container)

        if app and app in self.groups:
            g = self.groups[app]
            if g.remove_nid(uid):
                gp = g.get_parent()
                if gp:
                    gp.remove(g)
                g.destroy()
                del self.groups[app]
            else:
                g.refresh(self.by_id)

        container.destroy()
        self._sync_empty()

    def clear_app(self, app_name):
        if self._dead or app_name not in self.groups:
            return

        g = self.groups.pop(app_name)
        nids = set(g.nids)

        self.data = [n for n in self.data if n.get("id") not in nids]
        self._schedule_save()

        g.detach_all()

        for nid in nids:
            _delete_image(nid)
            c = self.by_id.pop(nid, None)
            if c is None:
                continue
            if c in self.containers:
                self.containers.remove(c)
            p = c.get_parent()
            if p:
                p.remove(c)
            nb = getattr(c, "notification_box", None)
            if nb:
                nb.destroy(from_history_delete=True)
                c.notification_box = None
            c.destroy()

        p = g.get_parent()
        if p:
            p.remove(g)
        g.destroy()
        self._sync_empty()

    def clear_all(self, *_):
        if self._dead:
            return

        for g in self.groups.values():
            g.detach_all()
        for ch in list(self.notif_list.get_children()):
            self.notif_list.remove(ch)
            ch.destroy()
        self.groups.clear()

        for c in self.containers:
            nb = getattr(c, "notification_box", None)
            if nb:
                nb.destroy(from_history_delete=True)
                c.notification_box = None
            c.destroy()
        self.containers.clear()
        self.by_id.clear()
        self.data.clear()

        _submit(self._rm_file)
        _clear_all_images()
        self._sync_empty()

    # ── Загрузка / сохранение ──

    def _begin_load(self):
        if not self._loading:
            self._loading = True
            _submit(self._read_file)
        return GLib.SOURCE_REMOVE

    def _read_file(self):
        data = []
        if os.path.isfile(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        GLib.idle_add(self._on_loaded, data, priority=GLib.PRIORITY_LOW)

    def _on_loaded(self, file_data):
        if self._dead:
            return GLib.SOURCE_REMOVE

        existing = {n.get("id") for n in self.data}
        merged = self.data.copy()
        for n in file_data if isinstance(file_data, list) else []:
            if n.get("id") not in existing:
                merged.append(n)
        self.data = merged[:MAX_HISTORY]

        if self.data:
            self._load_batch(list(reversed(self.data)), 0)
        else:
            self._finish_load()

        ids = [n.get("id") for n in self.data]
        _submit(lambda: _cleanup_orphans(ids))
        return GLib.SOURCE_REMOVE

    def _load_batch(self, notes, idx):
        if self._dead:
            return
        end = min(idx + 5, len(notes))
        for i in range(idx, end):
            self._restore_one(notes[i])
        if end < len(notes):
            GLib.idle_add(
                self._load_batch, notes, end, priority=GLib.PRIORITY_LOW
            )
        else:
            self._finish_load()

    def _finish_load(self):
        if not self._dead:
            self._rebuild_groups()
            self._loading = False

    def _restore_one(self, rec):
        if not isinstance(rec, dict):
            return
        h = HistoricalNotification(
            id=rec.get("id"),
            app_icon=rec.get("app_icon"),
            summary=rec.get("summary", ""),
            body=rec.get("body", ""),
            app_name=rec.get("app_name", "Unknown"),
            timestamp=rec.get("timestamp"),
        )
        box = NotificationBox(h, timeout_ms=0)
        box.set_is_history(True)

        try:
            arrival = datetime.fromisoformat(h.timestamp)
        except (ValueError, TypeError):
            arrival = datetime.now()

        container = self._make_container(box, arrival)
        self.containers.insert(0, container)
        self.by_id[box.uuid] = container

    def _schedule_save(self):
        if self._save_tid:
            GLib.source_remove(self._save_tid)
        self._save_tid = GLib.timeout_add(1000, self._do_save)

    def _do_save(self):
        self._save_tid = None
        if not self._dead:
            snapshot = list(self.data)
            _submit(lambda: self._write_file(snapshot))
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _write_file(data):
        tmp = HISTORY_FILE + ".tmp"
        try:
            os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, HISTORY_FILE)
        except (OSError, TypeError, ValueError):
            try:
                os.remove(tmp)
            except OSError:
                pass

    @staticmethod
    def _rm_file():
        try:
            os.remove(HISTORY_FILE)
        except OSError:
            pass

    # ── Группировка ──

    def _rebuild_groups(self):
        if self._dead:
            return

        expanded = {name: g.is_expanded for name, g in self.groups.items()}

        for ch in list(self.notif_list.get_children()):
            self.notif_list.remove(ch)
        for g in self.groups.values():
            g.detach_all()
            g.destroy()
        self.groups.clear()

        for c in self.containers:
            nb = getattr(c, "notification_box", None)
            if not (nb and nb.notification):
                continue
            app = getattr(nb.notification, "app_name", "Unknown")
            if app not in self.groups:
                self.groups[app] = NotificationGroup(
                    app, self, is_expanded=expanded.get(app, False)
                )
            self.groups[app].add_nid(nb.uuid, c.arrival_time)

        for g in self.groups.values():
            g.refresh(self.by_id)

        for g in sorted(
            self.groups.values(),
            key=lambda x: x.latest_time or datetime.min,
            reverse=True,
        ):
            self.notif_list.add(g)

        self.notif_list.show_all()
        self._sync_empty()

    # ── Контейнер истории ──

    def _make_container(self, nb, arrival):
        c = Box(
            name="notification-container",
            orientation="v",
            h_align="fill",
            h_expand=True,
        )
        c.set_size_request(NOTIF_WIDTH, -1)
        c.arrival_time = arrival
        c.notification_box = nb

        notif = nb.notification
        thumb = getattr(nb, "thumb_path", getattr(notif, "app_icon", None))

        image_box = Box(name="notification-image")
        pb = load_pixbuf(thumb) if thumb else None
        if pb:
            img = CustomImage(pixbuf=pb)
            img.set_valign(Gtk.Align.START)
            image_box.add(img)

        summary_box = Box(
            name="notification-summary-box",
            orientation="h",
            children=[
                Label(
                    name="notification-summary",
                    markup=str(getattr(notif, "summary", ""))[:40],
                    h_align="start",
                    max_chars_width=18,
                    ellipsization="end",
                ),
                Box(name="notif-sep"),
                Label(
                    name="notification-timestamp",
                    label=arrival.strftime("%H:%M"),
                    h_align="start",
                ),
            ],
        )

        text_box = Box(
            name="notification-text",
            orientation="v",
            v_align="center",
            h_expand=True,
            children=[summary_box],
        )
        body = getattr(notif, "body", None)
        if body:
            text_box.add(
                Label(
                    name="notification-body",
                    markup=str(body)[:80],
                    h_align="start",
                    line_wrap="word-char",
                    max_chars_width=34,
                )
            )

        # Захватываем uuid и weakref на контейнер в замыкание
        uuid = nb.uuid
        c_ref = weakref.ref(c)
        close_btn = Button(
            name="notif-close-button",
            child=Label(name="notif-close-label", markup=icons.cancel),
        )
        close_btn.connect(
            "clicked",
            lambda _: self.delete_one(uuid, c_ref()) if c_ref() else None,
        )

        c.add(
            Box(
                name="notification-box-hist",
                spacing=8,
                children=[
                    image_box,
                    text_box,
                    Box(
                        orientation="v",
                        v_align="center",
                        children=[close_btn],
                    ),
                ],
            )
        )
        return c

    # ── Вытеснение ──

    def _evict(self):
        while len(self.containers) >= MAX_HISTORY:
            old_c = self.containers.pop()
            nb = getattr(old_c, "notification_box", None)
            if nb:
                app = getattr(nb.notification, "app_name", None)
                uid = nb.uuid
                if app and app in self.groups:
                    g = self.groups[app]
                    if g.remove_nid(uid):
                        gp = g.get_parent()
                        if gp:
                            gp.remove(g)
                        g.destroy()
                        del self.groups[app]
                    else:
                        g.refresh(self.by_id)
                self.by_id.pop(uid, None)
                _delete_image(uid)
                nb.destroy(from_history_delete=True)
                old_c.notification_box = None

            p = old_c.get_parent()
            if p:
                p.remove(old_c)
            old_c.destroy()

            if len(self.data) > MAX_HISTORY:
                evicted = self.data.pop()
                _delete_image(evicted.get("id"))

    def destroy(self):
        if self._dead:
            return
        self._dead = True

        if (
            self._dnd_hid
            and self.dnd_switch
            and self.dnd_switch.handler_is_connected(self._dnd_hid)
        ):
            self.dnd_switch.disconnect(self._dnd_hid)

        if self._save_tid:
            GLib.source_remove(self._save_tid)
            self._save_tid = None

        snapshot = list(self.data)
        _submit(lambda: self._write_file(snapshot))

        for g in self.groups.values():
            g.detach_all()
            g.destroy()
        self.groups.clear()

        for c in self.containers:
            nb = getattr(c, "notification_box", None)
            if nb:
                nb.destroy(from_history_delete=True)
                c.notification_box = None
            c.destroy()
        self.containers.clear()
        self.by_id.clear()
        self.data.clear()

        super().destroy()


class NotificationContainer(Box):
    def __init__(self, history, revealer_transition="slide-down", window=None):
        super().__init__(
            name="notification-container-main", orientation="v", spacing=4
        )

        self.history = history
        self._window = window
        self._closing = False
        self.popups: list[NotificationBox] = []
        self.idx = 0
        self._closed: set = set()

        self._server = Notifications()
        self._srv_hid = self._server.connect(
            "notification-added", self._on_new
        )
        self._build(revealer_transition)

    def _build(self, transition):
        self.stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT,
            transition_duration=120,
            visible=True,
        )

        self.btn_prev = Button(
            name="nav-button",
            child=Label(name="nav-button-label", markup=icons.chevron_left),
            on_clicked=lambda *_: self._nav(-1),
        )
        self.btn_close = Button(
            name="nav-button",
            child=Label(name="nav-button-label", markup=icons.cancel),
            on_clicked=self._close_all,
        )
        self.btn_close.get_child().add_style_class("close")
        self.btn_next = Button(
            name="nav-button",
            child=Label(name="nav-button-label", markup=icons.chevron_right),
            on_clicked=lambda *_: self._nav(1),
        )

        for b in (self.btn_prev, self.btn_close, self.btn_next):
            b.connect("enter-notify-event", lambda *_: self.pause_all())
            b.connect("leave-notify-event", lambda *_: self.resume_all())

        nav = Box(
            name="notification-navigation",
            spacing=4,
            h_align="center",
            children=[self.btn_prev, self.btn_close, self.btn_next],
        )
        self.nav_rev = Revealer(
            transition_type="slide-down", transition_duration=120, child=nav
        )

        self.main_rev = Revealer(
            name="notification-main-revealer",
            transition_type=transition,
            transition_duration=150,
            child=Box(
                name="notification-box-internal-container",
                orientation="v",
                children=[
                    Box(
                        name="notification-stack-box",
                        h_align="center",
                        children=[self.stack],
                    ),
                    self.nav_rev,
                ],
            ),
        )
        self.add(self.main_rev)

    # ── Таймауты ──

    def pause_all(self):
        for nb in self.popups:
            if not getattr(nb, "_destroyed", False):
                nb.stop_timeout()

    def resume_all(self):
        for nb in self.popups:
            if not getattr(nb, "_destroyed", False):
                nb.start_timeout()

    # ── Навигация ──

    def _nav(self, delta):
        new = self.idx + delta
        if 0 <= new < len(self.popups):
            self.idx = new
            self.stack.set_visible_child(self.popups[self.idx])
            self._update_nav()

    def _update_nav(self):
        n = len(self.popups)
        self.btn_prev.set_sensitive(self.idx > 0)
        self.btn_next.set_sensitive(self.idx < n - 1)
        self.nav_rev.set_reveal_child(n > 1)

    # ── Управление видимостью окна ──

    def _show_window(self):
        if self._window:
            self._window.set_visible(True)

    def _hide_window(self):
        if self._window:
            self._window.set_visible(False)

    # ── Новое уведомление ──

    def _on_new(self, fabric_service, notification_id):
        if self._closing:
            return

        notification = fabric_service.get_notification_from_id(notification_id)
        if notification is None:
            return

        nb = NotificationBox(notification)

        if self.history.do_not_disturb_enabled:
            self.history.add_notification(nb)
            return

        nb.set_container(self)
        if hasattr(notification, "connect"):
            notification.connect("closed", self._on_closed)

        str_id = str(notification_id)

        existing = self.stack.get_child_by_name(str_id)
        if existing:
            self.stack.remove(existing)
            if existing in self.popups:
                i = self.popups.index(existing)
                self.popups.remove(existing)
                if i <= self.idx:
                    self.idx = max(0, self.idx - 1)
            existing.destroy()

        if len(self.popups) >= MAX_POPUPS:
            old = self.popups.pop(0)
            self.stack.remove(old)
            self.history.add_notification(old)
            self.idx = max(0, self.idx - 1)

        self.stack.add_named(nb, str_id)
        self.popups.append(nb)
        self.idx = len(self.popups) - 1
        self.stack.set_visible_child(nb)

        for b in self.popups:
            b.start_timeout()

        if self._window:
            self._window.set_visible(True)
            self._window.show_all()

        self.main_rev.show_all()
        self.main_rev.set_reveal_child(True)
        self._update_nav()

        # Отложенная проверка размеров после layout
        GLib.timeout_add(500, self._debug_sizes, nb)


    def _debug_sizes(self, nb):
        print("=== DEBUG SIZES ===")
        
        # Окно
        if self._window:
            alloc = self._window.get_allocation()
            print(f"[WINDOW] size={alloc.width}x{alloc.height}, pos=({alloc.x},{alloc.y})")
            print(f"[WINDOW] get_size()={self._window.get_size()}")
            print(f"[WINDOW] visible={self._window.get_visible()}, mapped={self._window.get_mapped()}")
        
        # Контейнер (self)
        alloc = self.get_allocation()
        print(f"[CONTAINER self] size={alloc.width}x{alloc.height}")
        print(f"[CONTAINER self] visible={self.get_visible()}, mapped={self.get_mapped()}")
        
        # main_rev
        alloc = self.main_rev.get_allocation()
        print(f"[main_rev] size={alloc.width}x{alloc.height}, reveal={self.main_rev.get_reveal_child()}")
        child = self.main_rev.get_child()
        if child:
            alloc2 = child.get_allocation()
            print(f"[main_rev child] size={alloc2.width}x{alloc2.height}, visible={child.get_visible()}")
        
        # Stack
        alloc = self.stack.get_allocation()
        print(f"[stack] size={alloc.width}x{alloc.height}, visible={self.stack.get_visible()}")
        
        # NotificationBox
        alloc = nb.get_allocation()
        print(f"[NotifBox] size={alloc.width}x{alloc.height}, visible={nb.get_visible()}, mapped={nb.get_mapped()}")
        
        # Дети NotificationBox
        for i, child in enumerate(nb.get_children()):
            alloc = child.get_allocation()
            print(f"[NotifBox child {i}] {type(child).__name__} size={alloc.width}x{alloc.height}, visible={child.get_visible()}")
            if hasattr(child, 'get_children'):
                for j, grandchild in enumerate(child.get_children()):
                    alloc2 = grandchild.get_allocation()
                    print(f"  [grandchild {j}] {type(grandchild).__name__} size={alloc2.width}x{alloc2.height}, visible={grandchild.get_visible()}")

        # Проверяем всю иерархию от nb до window
        print("\n[HIERARCHY check]")
        widget = nb
        depth = 0
        while widget:
            alloc = widget.get_allocation()
            print(f"  {'  '*depth}{type(widget).__name__}: {alloc.width}x{alloc.height} visible={widget.get_visible()} mapped={widget.get_mapped()}")
            widget = widget.get_parent()
            depth += 1

        return GLib.SOURCE_REMOVE

    # ── Закрытие уведомления ──

    def _on_closed(self, notification, *_):
        nid = getattr(notification, "id", None)
        if not nid or self._closing or nid in self._closed:
            return
        self._closed.add(nid)

        idx = next(
            (
                i
                for i, nb in enumerate(self.popups)
                if nb.notification
                and getattr(nb.notification, "id", None) == nid
            ),
            -1,
        )
        if idx == -1:
            return

        nb = self.popups.pop(idx)
        self.stack.remove(nb)
        self.history.add_notification(nb)

        if not self.popups:
            self._closing = True
            self.main_rev.set_reveal_child(False)
            GLib.timeout_add(150, self._reset)
            return

        if idx <= self.idx:
            self.idx = max(0, self.idx - 1)
        self.idx = min(self.idx, len(self.popups) - 1)

        if 0 <= self.idx < len(self.popups):
            self.stack.set_visible_child(self.popups[self.idx])
        self._update_nav()

    def _reset(self):
        for c in list(self.stack.get_children()):
            self.stack.remove(c)
            c.destroy()
        self.popups.clear()
        self._closed.clear()
        self.idx = 0
        self._closing = False
        self._hide_window()
        return GLib.SOURCE_REMOVE

    def _close_all(self, *_):
        for nb in tuple(self.popups):
            if nb.notification and hasattr(nb.notification, "close"):
                try:
                    nb.notification.close("dismissed-by-user")
                except TypeError:
                    nb.notification.close()

    def destroy(self):
        self._closing = True
        if (
            self._server
            and self._srv_hid
            and self._server.handler_is_connected(self._srv_hid)
        ):
            self._server.disconnect(self._srv_hid)
            self._srv_hid = None

        for nb in self.popups:
            nb.destroy()
        self.popups.clear()
        self._closed.clear()
        self.history = None
        self._window = None
        super().destroy()