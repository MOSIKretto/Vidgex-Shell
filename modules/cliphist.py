from fabric.utils import remove_handler
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.entry import Entry
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow

from gi.repository import Gdk, GdkPixbuf, GLib

import os
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, Future
from typing import List, Dict, Optional
from dataclasses import dataclass
import logging

import modules.icons as icons

# Настройка логирования
logger = logging.getLogger(__name__)

@dataclass
class ClipboardItem:
    """Класс для представления элемента буфера обмена"""
    item_id: str
    content: str
    is_image: bool = False
    display_text: str = ""
    pixbuf: Optional[GdkPixbuf.Pixbuf] = None


class ClipHistoryManager:
    """Менеджер для работы с историей буфера обмена"""
    
    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="cliphist")
        self._futures: Dict[str, Future] = {}
        self._cache_dir = tempfile.mkdtemp(prefix="cliphist-")
        self._image_cache: Dict[str, GdkPixbuf.Pixbuf] = {}
        
    def load_items(self) -> List[ClipboardItem]:
        """Загрузка элементов истории"""
        try:
            result = subprocess.run(
                ["cliphist", "list"], 
                capture_output=True, 
                check=True,
                text=True
            )
            return self._parse_items(result.stdout)
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.error(f"Ошибка загрузки истории: {e}")
            return []
    
    def decode_item(self, item_id: str) -> Optional[bytes]:
        """Декодирование элемента по ID"""
        try:
            result = subprocess.run(
                ["cliphist", "decode", item_id],
                capture_output=True,
                check=True
            )
            return result.stdout
        except subprocess.CalledProcessError as e:
            logger.error(f"Ошибка декодирования элемента {item_id}: {e}")
            return None
    
    def delete_item(self, item_id: str) -> bool:
        """Удаление элемента по ID"""
        try:
            subprocess.run(["cliphist", "delete", item_id], check=True)
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Ошибка удаления элемента {item_id}: {e}")
            return False
    
    def clear_history(self) -> bool:
        """Очистка всей истории"""
        try:
            subprocess.run(["cliphist", "wipe"], check=True)
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Ошибка очистки истории: {e}")
            return False
    
    def _parse_items(self, output: str) -> List[ClipboardItem]:
        """Парсинг вывода cliphist"""
        items = []
        for line in output.strip().split('\n'):
            if not line or "<meta http-equiv" in line:
                continue
                
            parts = line.split('\t', 1)
            if len(parts) == 2:
                item_id, content = parts
            else:
                item_id, content = "0", line
            
            display_text = self._truncate_text(content.strip())
            is_image = self._is_image_data(content)
            
            items.append(ClipboardItem(
                item_id=item_id,
                content=content,
                is_image=is_image,
                display_text=display_text
            ))
        
        return items
    
    def _truncate_text(self, text: str, max_length: int = 100) -> str:
        """Обрезка текста для отображения"""
        return text[:max_length-3] + "..." if len(text) > max_length else text
    
    def _is_image_data(self, content: str) -> bool:
        """Определение типа содержимого"""
        # Проверка data URL
        if content.startswith("data:image/"):
            return True
        
        # Проверка бинарных сигнатур
        binary_signatures = [
            b'\x89PNG',  # PNG
            b'GIF8',     # GIF
            b'\xff\xd8', # JPEG
            b'BM',       # BMP
            b'II*\x00',  # TIFF little-endian
            b'MM\x00*',  # TIFF big-endian
        ]
        
        content_bytes = content.encode('utf-8', errors='ignore')[:8]
        return any(content_bytes.startswith(sig) for sig in binary_signatures)
    
    def get_image_preview(self, item: ClipboardItem, callback) -> None:
        """Асинхронная загрузка превью изображения"""
        if item.item_id in self._image_cache:
            GLib.idle_add(callback, item.item_id, self._image_cache[item.item_id])
            return
        
        future = self._executor.submit(self._load_image_preview, item)
        self._futures[item.item_id] = future
        future.add_done_callback(lambda f: self._on_image_loaded(f, item.item_id, callback))
    
    def _load_image_preview(self, item: ClipboardItem) -> Optional[GdkPixbuf.Pixbuf]:
        """Загрузка превью изображения в потоке"""
        try:
            image_data = self.decode_item(item.item_id)
            if not image_data:
                return None
            
            loader = GdkPixbuf.PixbufLoader()
            loader.write(image_data)
            loader.close()
            pixbuf = loader.get_pixbuf()
            
            # Масштабирование до размера превью
            max_size = 72
            width, height = pixbuf.get_width(), pixbuf.get_height()
            
            if width > height:
                new_width = max_size
                new_height = int(height * (max_size / width))
            else:
                new_height = max_size
                new_width = int(width * (max_size / height))
            
            return pixbuf.scale_simple(new_width, new_height, GdkPixbuf.InterpType.BILINEAR)
        except Exception as e:
            logger.error(f"Ошибка загрузки превью для {item.item_id}: {e}")
            return None
    
    def _on_image_loaded(self, future: Future, item_id: str, callback) -> None:
        """Обработка завершения загрузки изображения"""
        if future.cancelled():
            return
            
        try:
            pixbuf = future.result()
            if pixbuf:
                self._image_cache[item_id] = pixbuf
                GLib.idle_add(callback, item_id, pixbuf)
        except Exception as e:
            logger.error(f"Ошибка обработки превью для {item_id}: {e}")
        finally:
            self._futures.pop(item_id, None)
    
    def copy_to_clipboard(self, item_id: str) -> bool:
        """Копирование элемента в буфер обмена"""
        try:
            image_data = self.decode_item(item_id)
            if not image_data:
                return False
            
            subprocess.run(
                ["wl-copy"],
                input=image_data,
                check=True
            )
            return True
        except Exception as e:
            logger.error(f"Ошибка копирования элемента {item_id}: {e}")
            return False
    
    def cleanup(self):
        """Очистка ресурсов"""
        # Отмена всех незавершенных задач
        for future in self._futures.values():
            future.cancel()
        
        self._executor.shutdown(wait=False)
        
        # Очистка временных файлов
        try:
            import shutil
            if os.path.exists(self._cache_dir):
                shutil.rmtree(self._cache_dir)
        except Exception as e:
            logger.error(f"Ошибка очистки временных файлов: {e}")
        
        self._image_cache.clear()


class ClipHistory(Box):
    def __init__(self, **kwargs):
        super().__init__(
            name="clip-history",
            visible=False,
            all_visible=False,
            **kwargs,
        )

        self.notch = kwargs["notch"]
        self.selected_index = -1
        self._arranger_handler = 0
        self.clipboard_items: List[ClipboardItem] = []
        
        # Менеджер для работы с историей
        self.manager = ClipHistoryManager()
        
        # Кэш для виджетов элементов
        self.item_widgets: Dict[str, Button] = {}
        
        self._setup_ui()
        self.show_all()

    def _setup_ui(self):
        """Настройка пользовательского интерфейса"""
        self.viewport = Box(name="viewport", spacing=4, orientation="v")
        
        self.search_entry = Entry(
            name="search-entry",
            placeholder="Поиск в истории буфера...",
            h_expand=True,
            h_align="fill",
            notify_text=self._on_search_changed,
            on_activate=lambda entry, *_: self._use_selected_item(),
            on_key_press_event=self._on_search_key_press,
        )
        self.search_entry.props.xalign = 0.5
        
        self.scrolled_window = ScrolledWindow(
            name="scrolled-window",
            spacing=10,
            h_expand=True,
            v_expand=True,
            h_align="fill",
            v_align="fill",
            child=self.viewport,
            propagate_width=False,
            propagate_height=False,
        )

        self.header_box = Box(
            name="header_box",
            spacing=10,
            orientation="h",
            children=[
                Button(
                    name="clear-button",
                    child=Label(name="clear-label", markup=icons.trash),
                    on_clicked=lambda *_: self._clear_history(),
                    tooltip_text="Очистить историю",
                ),
                self.search_entry,
                Button(
                    name="close-button",
                    child=Label(name="close-label", markup=icons.cancel),
                    tooltip_text="Закрыть",
                    on_clicked=lambda *_: self.close()
                ),
            ],
        )

        self.history_box = Box(
            name="launcher-box",
            spacing=10,
            h_expand=True,
            orientation="v",
            children=[
                self.header_box,
                self.scrolled_window,
            ],
        )

        self.add(self.history_box)

    def close(self):
        """Закрыть панель истории буфера обмена"""
        self.viewport.children = []
        self.selected_index = -1
        self.item_widgets.clear()
        self.notch.close_notch()

    def open(self):
        """Открыть панель истории буфера обмена и загрузить элементы"""
        self.search_entry.set_text("")
        self.search_entry.grab_focus()
        self._load_clipboard_items()

    def _load_clipboard_items(self):
        """Загрузка элементов буфера обмена"""
        def worker():
            try:
                items = self.manager.load_items()
                GLib.idle_add(self._display_items, items)
            except Exception as e:
                logger.error(f"Ошибка загрузки истории: {e}")
        
        threading.Thread(target=worker, daemon=True).start()

    def _display_items(self, items: List[ClipboardItem]):
        """Отображение загруженных элементов"""
        if self._arranger_handler:
            remove_handler(self._arranger_handler)
        
        self.viewport.children = []
        self.selected_index = -1
        self.clipboard_items = items
        self.item_widgets.clear()

        if not items:
            self._show_empty_state()
            return

        # Отображение элементов партиями для лучшей производительности
        self._display_items_batch(items, 0, 15)

    def _display_items_batch(self, items: List[ClipboardItem], start: int, batch_size: int):
        """Отображение элементов партиями"""
        end = min(start + batch_size, len(items))
        
        for i in range(start, end):
            item = items[i]
            widget = self._create_item_widget(item)
            self.viewport.add(widget)
            self.item_widgets[item.item_id] = widget

        # Продолжаем загрузку если есть еще элементы
        if end < len(items):
            GLib.timeout_add(50, self._display_items_batch, items, end, batch_size)
        elif self.search_entry.get_text() and self.viewport.get_children():
            self._update_selection(0)
        
        return False

    def _show_empty_state(self):
        """Показать состояние пустого списка"""
        container = Box(
            name="no-clip-container",
            orientation="v",
            h_align="center",
            v_align="center",
            h_expand=True,
            v_expand=True
        )
        
        label = Label(
            name="no-clip",
            markup=icons.clipboard,
            h_align="center",
            v_align="center",
        )
        
        container.add(label)
        self.viewport.add(container)

    def _create_item_widget(self, item: ClipboardItem) -> Button:
        """Создание виджета для элемента"""
        if item.is_image:
            button = self._create_image_item(item)
        else:
            button = self._create_text_item(item)
        
        # Настройка обработчиков событий
        button.connect("clicked", lambda *_: self._paste_item(item.item_id))
        button.connect("key-press-event", lambda w, e: self._on_item_key_press(w, e, item.item_id))
        button.set_can_focus(True)
        button.add_events(Gdk.EventMask.KEY_PRESS_MASK)
            
        return button

    def _create_image_item(self, item: ClipboardItem) -> Button:
        """Создание виджета для изображения"""
        button = Button(
            name="slot-button",
            child=Box(
                name="slot-box",
                orientation="h",
                spacing=10,
                children=[
                    Image(name="clip-icon", h_align="start"),
                    Label(
                        name="clip-label",
                        label="[Изображение]",
                        ellipsization="end",
                        v_align="center",
                        h_align="start",
                        h_expand=True,
                    ),
                ],
            ),
            tooltip_text="Изображение в буфере",
        )
        
        # Асинхронная загрузка превью
        self.manager.get_image_preview(item, self._on_image_preview_loaded)
        
        return button

    def _create_text_item(self, item: ClipboardItem) -> Button:
        """Создание виджета для текстового элемента"""
        return Button(
            name="slot-button",
            child=Box(
                name="slot-box",
                orientation="h",
                spacing=10,
                children=[
                    Label(
                        name="clip-icon",
                        markup=icons.clip_text,
                        h_align="start",
                    ),
                    Label(
                        name="clip-label",
                        label=item.display_text,
                        ellipsization="end",
                        v_align="center",
                        h_align="start",
                        h_expand=True,
                    ),
                ],
            ),
            tooltip_text=item.display_text,
        )

    def _on_image_preview_loaded(self, item_id: str, pixbuf: GdkPixbuf.Pixbuf):
        """Обработчик загрузки превью изображения"""
        widget = self.item_widgets.get(item_id)
        if not widget:
            return
            
        box = widget.get_child()
        if box and box.get_children():
            image_widget = box.get_children()[0]
            if isinstance(image_widget, Image):
                image_widget.set_from_pixbuf(pixbuf)

    def _paste_item(self, item_id: str):
        """Копирование элемента в буфер обмена"""
        def worker():
            success = self.manager.copy_to_clipboard(item_id)
            if success:
                GLib.idle_add(self.close)
        
        threading.Thread(target=worker, daemon=True).start()

    def _delete_item(self, item_id: str):
        """Удаление элемента"""
        def worker():
            success = self.manager.delete_item(item_id)
            if success:
                GLib.idle_add(self._load_clipboard_items)
        
        threading.Thread(target=worker, daemon=True).start()

    def _clear_history(self):
        """Очистка всей истории"""
        def worker():
            success = self.manager.clear_history()
            if success:
                GLib.idle_add(self._load_clipboard_items)
        
        threading.Thread(target=worker, daemon=True).start()

    def _on_search_changed(self, entry, *_):
        """Обработчик изменения текста поиска"""
        search_text = entry.get_text().lower()
        filtered_items = [
            item for item in self.clipboard_items 
            if search_text in item.content.lower() or search_text in item.display_text.lower()
        ]
        self._display_items(filtered_items)

    def _on_search_key_press(self, widget, event):
        """Обработчик нажатий клавиш в поле поиска"""
        keyval = event.keyval
        
        if keyval == Gdk.KEY_Down:
            self._move_selection(1)
            return True
        elif keyval == Gdk.KEY_Up:
            self._move_selection(-1)
            return True
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._use_selected_item()
            return True
        elif keyval == Gdk.KEY_Delete:
            self._delete_selected_item()
            return True
        elif keyval == Gdk.KEY_Escape:
            self.close()
            return True
        
        return False

    def _on_item_key_press(self, widget, event, item_id):
        """Обработчик нажатий клавиш на элементах"""
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._paste_item(item_id)
            return True
        return False

    def _update_selection(self, new_index: int):
        """Обновление выбранного элемента"""
        children = self.viewport.get_children()

        if self.selected_index != -1 and self.selected_index < len(children):
            current_button = children[self.selected_index]
            current_button.get_style_context().remove_class("selected")

        if new_index != -1 and new_index < len(children):
            new_button = children[new_index]
            new_button.get_style_context().add_class("selected")
            self.selected_index = new_index
            self._scroll_to_selected(new_button)
        else:
            self.selected_index = -1

    def _move_selection(self, delta: int):
        """Перемещение выбора вверх/вниз"""
        children = self.viewport.get_children()
        if not children:
            return

        if self.selected_index == -1 and delta == 1:
            new_index = 0
        else:
            new_index = self.selected_index + delta
            
        new_index = max(0, min(new_index, len(children) - 1))
        self._update_selection(new_index)

    def _scroll_to_selected(self, button):
        """Прокрутка к выбранному элементу"""
        def scroll():
            adj = self.scrolled_window.get_vadjustment()
            alloc = button.get_allocation()
            if alloc.height == 0:
                return False

            y = alloc.y
            height = alloc.height
            page_size = adj.get_page_size()
            current_value = adj.get_value()

            visible_top = current_value
            visible_bottom = current_value + page_size

            if y < visible_top:
                adj.set_value(y)
            elif y + height > visible_bottom:
                new_value = y + height - page_size
                adj.set_value(new_value)
            return False
        
        GLib.idle_add(scroll)

    def _use_selected_item(self):
        """Использование выбранного элемента"""
        children = self.viewport.get_children()
        if not children or self.selected_index == -1 or self.selected_index >= len(self.clipboard_items):
            return

        item = self.clipboard_items[self.selected_index]
        self._paste_item(item.item_id)

    def _delete_selected_item(self):
        """Удаление выбранного элемента"""
        children = self.viewport.get_children()
        if not children or self.selected_index == -1:
            return

        item = self.clipboard_items[self.selected_index]
        self._delete_item(item.item_id)

    def __del__(self):
        """Очистка ресурсов при удалении"""
        if hasattr(self, 'manager'):
            self.manager.cleanup()