import json
import os

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk, GLib


class Dnd:
    _ORDER_FILE = GLib.get_user_cache_dir() + "/vidgex-shell/dock_order.json"
    TARGET_NAME = "DOCK_APP_ROW"

    def __init__(self, dock, order_file: str | None = None):
        self._dock = dock
        self._order_file = order_file or self._ORDER_FILE
        self._custom_order: list[str] = self._load_order()

    @property
    def custom_order(self) -> list[str]:
        return self._custom_order

    def apply_order(self, candidates: list[dict]) -> list[dict]:
        # Добавляем новые элементы, которых ещё нет в сохранённом порядке
        for c in candidates:
            uid = c["unique_id"]
            if uid not in self._custom_order:
                self._custom_order.append(uid)

        # Сортируем согласно сохраненному списку
        candidates.sort(
            key=lambda x: self._custom_order.index(x["unique_id"])
            if x["unique_id"] in self._custom_order
            else 9999
        )
        return candidates

    def setup(self, container) -> None:
        main_btn = container._main_btn
        main_btn._container = container

        # Создаем кастомный TargetEntry для точного совпадения типов внутри приложения
        te = Gtk.TargetEntry.new(self.TARGET_NAME, Gtk.TargetFlags.SAME_APP, 0)
        
        main_btn.drag_source_set(
            Gdk.ModifierType.BUTTON1_MASK, [te], Gdk.DragAction.MOVE
        )
        main_btn.drag_dest_set(
            Gtk.DestDefaults.ALL, [te], Gdk.DragAction.MOVE
        )

        main_btn.connect("drag-begin", self._on_drag_begin)
        main_btn.connect("drag-end", self._on_drag_end)
        main_btn.connect("drag-data-get", self._on_drag_data_get)
        main_btn.connect("drag-data-received", self._on_drag_data_received)
        main_btn.connect("drag-motion", self._on_drag_motion)
        main_btn.connect("drag-leave", self._on_drag_leave)

    def save_order(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._order_file), exist_ok=True)
            with open(self._order_file, "w", encoding="utf-8") as f:
                json.dump(self._custom_order, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def _load_order(self) -> list[str]:
        try:
            with open(self._order_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and all(isinstance(i, str) for i in data):
                return data
        except (OSError, json.JSONDecodeError, ValueError):
            pass
        return []

    def _on_drag_begin(self, main_btn, context):
        self._dock._drag_active = True
        visibility = getattr(self._dock, "_visibility", None)
        if visibility:
            visibility.set_drag(True)

        main_btn.add_style_class("dragging")
        container = main_btn._container
        try:
            if hasattr(container, "_icon_box"):
                img = container._icon_box.get_children()[0]
                pb = img.get_pixbuf()
                if pb:
                    Gtk.drag_set_icon_pixbuf(
                        context, pb,
                        pb.get_width() // 2,
                        pb.get_height() // 2,
                    )
                    return
        except Exception:
            pass
        Gtk.drag_set_icon_default(context)

    def _on_drag_end(self, main_btn, _context):
        self._dock._drag_active = False
        main_btn.remove_style_class("dragging")
        visibility = getattr(self._dock, "_visibility", None)
        if visibility:
            visibility.set_drag(False)

        # Вызываем обновление видимости и состояния Дока
        GLib.timeout_add(50, lambda: self._dock._schedule_update() or False)

    def _on_drag_data_get(self, main_btn, _ctx, sel, _info, _ts):
        uid = getattr(main_btn._container, "_unique_id", "")
        if uid:
            # Передаем исходный ID в виде UTF-8 байтов без изменения регистра
            sel.set(sel.get_target(), 8, str(uid).encode("utf-8"))

    def _on_drag_motion(self, main_btn, context, _x, _y, time):
        main_btn.add_style_class("drag-hover")
        Gdk.drag_status(context, Gdk.DragAction.MOVE, time)
        return True

    def _on_drag_leave(self, main_btn, _ctx, _time):
        main_btn.remove_style_class("drag-hover")

    def _on_drag_data_received(
        self, main_btn, context, x, _y, sel_data, _info, timestamp
    ):
        main_btn.remove_style_class("drag-hover")

        raw_data = sel_data.get_data()
        if not raw_data:
            context.finish(False, False, timestamp)
            return

        source_id = raw_data.decode("utf-8")
        container = main_btn._container
        target_id = getattr(container, "_unique_id", None)

        if not source_id or not target_id or source_id == target_id:
            context.finish(False, False, timestamp)
            return

        # Гарантируем наличие обеих кнопок в массиве порядка
        if source_id not in self._custom_order:
            self._custom_order.append(source_id)
        if target_id not in self._custom_order:
            self._custom_order.append(target_id)

        old_idx = self._custom_order.index(source_id)
        tgt_idx = self._custom_order.index(target_id)

        view = self._dock.view
        src_container = None
        children = view.get_children()
        
        for child in children:
            child_id = getattr(child, "_unique_id", "")
            if str(child_id) == source_id:
                src_container = child
                break

        if not src_container:
            context.finish(False, False, timestamp)
            return

        try:
            box_idx = children.index(container)
            alloc = main_btn.get_allocation()

            # Если мышь в правой половине кнопки-цели — вставляем после неё
            if x > alloc.width / 2:
                box_idx += 1
                tgt_idx += 1

            view.reorder_child(src_container, box_idx)

            self._custom_order.remove(source_id)
            if old_idx < tgt_idx:
                tgt_idx -= 1
            self._custom_order.insert(tgt_idx, source_id)

            self.save_order()
            
            # Сбрасываем кэш отпечатка дока, чтобы он принял новый порядок
            self._dock._last_fingerprint = None

        except (ValueError, IndexError):
            pass

        context.finish(True, False, timestamp)