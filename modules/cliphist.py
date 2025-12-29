from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.entry import Entry
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow

from gi.repository import Gdk, GdkPixbuf, GLib
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass
from typing import List, Dict, Optional
import subprocess
import tempfile
import os

import modules.icons as icons


@dataclass
class ClipboardItem:
    item_id: str
    content: str
    is_image: bool = False
    display_text: str = ""
    pixbuf: Optional[GdkPixbuf.Pixbuf] = None


class ClipHistoryManager:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="cliphist")
        self.futures: Dict[str, Future] = {}
        self.cache_dir = tempfile.mkdtemp(prefix="cliphist-")
        self.image_cache: Dict[str, GdkPixbuf.Pixbuf] = {}
    
    def load_items(self) -> List[ClipboardItem]:
        result = subprocess.run(["cliphist", "list"], capture_output=True, text=True)
        return self._parse_items(result.stdout)
    
    def decode_item(self, item_id: str) -> Optional[bytes]:
        result = subprocess.run(["cliphist", "decode", item_id], capture_output=True)
        return result.stdout if result.returncode == 0 else None
    
    def manage_clipboard(self, action: str, item_id: Optional[str] = None) -> bool:
        if action == "delete" and item_id:
            subprocess.run(["cliphist", "delete", item_id], check=True)
            return True
        elif action == "clear":
            subprocess.run(["cliphist", "wipe"], check=True)
            return True
        return False
    
    def _parse_items(self, output: str) -> List[ClipboardItem]:
        items = []
        for line in output.strip().split('\n'):
            if not line or "<meta http-equiv" in line:
                continue
            parts = line.split('\t', 1)
            item_id, content = parts if len(parts) == 2 else ("0", line)
            
            items.append(ClipboardItem(
                item_id=item_id,
                content=content,
                is_image=self._is_image_data(content),
                display_text=self._truncate_text(content.strip())
            ))
        return items
    
    def _truncate_text(self, text: str, max_length: int = 100) -> str:
        return text[:max_length-3] + "..." if len(text) > max_length else text
    
    def _is_image_data(self, content: str) -> bool:
        if content.startswith("data:image/"):
            return True
        
        signatures = [b'\x89PNG', b'GIF8', b'\xff\xd8', b'BM', b'II*\x00', b'MM\x00*']
        content_bytes = content.encode('utf-8', errors='ignore')[:8]
        return any(content_bytes.startswith(sig) for sig in signatures)
    
    def get_image_preview(self, item: ClipboardItem, callback) -> None:
        if item.item_id in self.image_cache:
            GLib.idle_add(callback, item.item_id, self.image_cache[item.item_id])
            return
        
        future = self.executor.submit(self._load_image_preview, item)
        self.futures[item.item_id] = future
        future.add_done_callback(lambda f: self._on_image_loaded(f, item.item_id, callback))
    
    def _load_image_preview(self, item: ClipboardItem) -> Optional[GdkPixbuf.Pixbuf]:
        image_data = self.decode_item(item.item_id)
        if not image_data:
            return None
        
        loader = GdkPixbuf.PixbufLoader()
        loader.write(image_data)
        loader.close()
        pixbuf = loader.get_pixbuf()
        
        max_size = 72
        width, height = pixbuf.get_width(), pixbuf.get_height()
        
        if width > height:
            new_width = max_size
            new_height = int(height * max_size / width)
        else:
            new_height = max_size
            new_width = int(width * max_size / height)
        
        return pixbuf.scale_simple(new_width, new_height, GdkPixbuf.InterpType.BILINEAR)
    
    def _on_image_loaded(self, future: Future, item_id: str, callback) -> None:
        if future.cancelled():
            return
        try:
            pixbuf = future.result()
            if pixbuf:
                self.image_cache[item_id] = pixbuf
                GLib.idle_add(callback, item_id, pixbuf)
        finally:
            self.futures.pop(item_id, None)
    
    def copy_to_clipboard(self, item_id: str) -> bool:
        image_data = self.decode_item(item_id)
        if not image_data:
            return False
        
        subprocess.run(["wl-copy"], input=image_data, check=True)
        return True
    
    def cleanup(self):
        for future in self.futures.values():
            future.cancel()
        self.executor.shutdown(wait=False)
        import shutil
        if os.path.exists(self.cache_dir):
            shutil.rmtree(self.cache_dir)
        self.image_cache.clear()


class ClipHistory(Box):
    def __init__(self, notch, **kwargs):
        super().__init__(name="clip-history", visible=False, all_visible=False, **kwargs)
        
        self.notch = notch
        self.selected_index = -1
        self.clipboard_items: List[ClipboardItem] = []
        self.manager = ClipHistoryManager()
        self.item_widgets: Dict[str, Button] = {}
        
        self._setup_ui()
        self.show_all()

    def _setup_ui(self):
        self.viewport = Box(name="viewport", spacing=4, orientation="v")
        
        self.search_entry = Entry(
            name="search-entry",
            placeholder="Поиск в истории буфера...",
            h_expand=True,
            h_align="fill",
            notify_text=self._on_search_changed,
            on_activate=lambda *_: self._use_selected_item(),
            on_key_press_event=self._on_search_key_press,
        )
        self.search_entry.props.xalign = 0.5
        
        self.scrolled_window = ScrolledWindow(
            name="scrolled-window",
            spacing=10,
            v_expand=True,
            child=self.viewport,
            propagate_height=False,
        )

        self.add(Box(
            name="launcher-box",
            spacing=10,
            h_expand=True,
            orientation="v",
            children=[
                Box(
                    name="header_box",
                    spacing=10,
                    children=[
                        Button(
                            name="clear-button",
                            child=Label(name="clear-label", markup=icons.trash),
                            on_clicked=lambda *_: self._manage_clipboard_async("clear"),
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
                ),
                self.scrolled_window,
            ],
        ))

    def close(self):
        self.viewport.children = []
        self.selected_index = -1
        self.item_widgets.clear()
        self.notch.close_notch()

    def open(self):
        self.search_entry.set_text("")
        self.search_entry.grab_focus()
        self._load_clipboard_items()

    def _load_clipboard_items(self):
        def worker():
            items = self.manager.load_items()
            GLib.idle_add(self._display_items, items)
        
        self.manager.executor.submit(worker)

    def _display_items(self, items: List[ClipboardItem]):
        self.viewport.children = []
        self.selected_index = -1
        self.clipboard_items = items
        self.item_widgets.clear()

        if not items:
            self._show_empty_state()
            return

        self._display_items_batch(items, 0, 15)

    def _display_items_batch(self, items: List[ClipboardItem], start: int, batch_size: int):
        end = min(start + batch_size, len(items))
        
        for i in range(start, end):
            item = items[i]
            widget = self._create_item_widget(item)
            self.viewport.add(widget)
            self.item_widgets[item.item_id] = widget

        if end < len(items):
            timer_id = GLib.timeout_add(50, self._display_items_batch, items, end, batch_size)
            # Note: These batch timers are short-lived and will complete naturally
        elif self.search_entry.get_text() and self.viewport.get_children():
            self._update_selection(0)
        
        return False

    def _show_empty_state(self):
        self.viewport.add(Box(
            name="no-clip-container",
            h_align="center",
            v_align="center",
            h_expand=True,
            v_expand=True,
            children=[
                Label(name="no-clip", markup=icons.clipboard, h_align="center", v_align="center")
            ]
        ))

    def _create_item_widget(self, item: ClipboardItem) -> Button:
        icon = Image(name="clip-icon", h_align="start") if item.is_image else Label(name="clip-icon", markup=icons.clip_text, h_align="start")
        label_text = "[Изображение]" if item.is_image else item.display_text
        
        button = Button(
            name="slot-button",
            child=Box(
                name="slot-box",
                orientation="h",
                spacing=10,
                children=[
                    icon,
                    Label(
                        name="clip-label",
                        label=label_text,
                        ellipsization="end",
                        v_align="center",
                        h_align="start",
                        h_expand=True,
                    ),
                ],
            ),
            tooltip_text=item.display_text if not item.is_image else "Изображение в буфере",
        )
        
        if item.is_image:
            self.manager.get_image_preview(item, self._on_image_preview_loaded)
        
        button.connect("clicked", lambda *_: self._paste_item(item.item_id))
        button.connect("key-press-event", lambda w, e: self._on_item_key_press(w, e, item.item_id))
        button.set_can_focus(True)
        button.add_events(Gdk.EventMask.KEY_PRESS_MASK)
            
        return button

    def _on_image_preview_loaded(self, item_id: str, pixbuf: GdkPixbuf.Pixbuf):
        widget = self.item_widgets.get(item_id)
        if widget and (box := widget.get_child()) and (children := box.get_children()):
            if isinstance(children[0], Image):
                children[0].set_from_pixbuf(pixbuf)

    def _paste_item(self, item_id: str):
        def worker():
            if self.manager.copy_to_clipboard(item_id):
                GLib.idle_add(self.close)
        
        self.manager.executor.submit(worker)

    def _manage_clipboard_async(self, action: str, item_id: Optional[str] = None):
        def worker():
            if self.manager.manage_clipboard(action, item_id):
                GLib.idle_add(self._load_clipboard_items)
        
        self.manager.executor.submit(worker)

    def _on_search_changed(self, entry, *_):
        search_text = entry.get_text().lower()
        filtered_items = [
            item for item in self.clipboard_items 
            if search_text in item.content.lower() or search_text in item.display_text.lower()
        ]
        self._display_items(filtered_items)

    def _on_search_key_press(self, widget, event):
        key_map = {
            Gdk.KEY_Down: lambda: self._move_selection(1),
            Gdk.KEY_Up: lambda: self._move_selection(-1),
            Gdk.KEY_Return: self._use_selected_item,
            Gdk.KEY_KP_Enter: self._use_selected_item,
            Gdk.KEY_Delete: self._delete_selected_item,
            Gdk.KEY_Escape: self.close
        }
        
        if action := key_map.get(event.keyval):
            action()
            return True
        return False

    def _on_item_key_press(self, widget, event, item_id):
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._paste_item(item_id)
            return True
        return False

    def _update_selection(self, new_index: int):
        children = self.viewport.get_children()

        if 0 <= self.selected_index < len(children):
            children[self.selected_index].get_style_context().remove_class("selected")

        if 0 <= new_index < len(children):
            button = children[new_index]
            button.get_style_context().add_class("selected")
            self.selected_index = new_index
            self._scroll_to_selected(button)
        else:
            self.selected_index = -1

    def _move_selection(self, delta: int):
        children = self.viewport.get_children()
        if not children:
            return
        new_index = max(0, min(self.selected_index + delta, len(children) - 1))
        self._update_selection(new_index)

    def _scroll_to_selected(self, button):
        def scroll():
            adj = self.scrolled_window.get_vadjustment()
            alloc = button.get_allocation()
            if alloc.height == 0:
                return False

            y, height = alloc.y, alloc.height
            page_size, current = adj.get_page_size(), adj.get_value()

            if y < current:
                adj.set_value(y)
            elif y + height > current + page_size:
                adj.set_value(y + height - page_size)
            return False
        
        GLib.idle_add(scroll)

    def _use_selected_item(self):
        if 0 <= self.selected_index < len(self.clipboard_items):
            self._paste_item(self.clipboard_items[self.selected_index].item_id)

    def _delete_selected_item(self):
        if 0 <= self.selected_index < len(self.clipboard_items):
            item = self.clipboard_items[self.selected_index]
            self._manage_clipboard_async("delete", item.item_id)

    def destroy(self):
        """Cleanup resources"""
        if hasattr(self, 'manager'):
            self.manager.cleanup()
            self.manager = None
        self.item_widgets.clear()
        self.clipboard_items.clear()
        super().destroy()
    
    def __del__(self):
        """Fallback cleanup"""
        if hasattr(self, 'manager') and self.manager:
            try:
                self.manager.cleanup()
            except Exception:
                pass