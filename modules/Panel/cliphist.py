from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.entry import Entry
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow
from gi.repository import Gdk, GdkPixbuf, GLib
import subprocess
import modules.icons as icons

class ClipHistory(Box):
    def __init__(self, notch, **kwargs):
        # Полное сохранение имен для CSS
        super().__init__(name="clip-history", visible=False, all_visible=False, **kwargs)
        self.notch = notch
        self.selected_index = -1
        self._setup_ui()
        self.show_all()

    def _setup_ui(self):
        self.viewport = Box(name="viewport", spacing=4, orientation="v")
        self.search_entry = Entry(
            name="search-entry",
            placeholder="Поиск в истории буфера...",
            h_expand=True,
            notify_text=lambda entry, *_: self._render_items(entry.get_text().lower()),
            on_activate=lambda *_: self._use_selected(),
            on_key_press_event=self._on_search_key_press,
        )
        self.search_entry.props.xalign = 0.5
        
        self.scrolled_window = ScrolledWindow(
            name="scrolled-window",
            v_expand=True,
            child=self.viewport,
            propagate_height=False,
        )

        # Структура 1-в-1 как в оригинале
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
                            on_clicked=lambda *_: self._action("wipe"),
                        ),
                        self.search_entry,
                        Button(
                            name="close-button",
                            child=Label(name="close-label", markup=icons.cancel),
                            on_clicked=lambda *_: self.close()
                        ),
                    ],
                ),
                self.scrolled_window,
            ],
        ))

    def _render_items(self, search=""):
        """Минимальное потребление CPU: рендерим только то, что нужно."""
        self.viewport.children = []
        self.selected_index = -1
        
        # Читаем историю напрямую
        raw = subprocess.run(["cliphist", "list"], capture_output=True).stdout.decode(errors='ignore')
        if not raw:
            return self._show_empty()

        for line in raw.splitlines():
            if search and search not in line.lower():
                continue
            
            # Разрезаем строку один раз для экономии ресурсов
            parts = line.split('\t', 1)
            idx = parts[0]
            content = parts[1] if len(parts) > 1 else line
            
            self.viewport.add(self._create_item(idx, content))
        
        self.show_all()

    def _create_item(self, idx, content):
        # Быстрая проверка на изображение
        is_img = "[[ binary data" in content
        
        # Виджеты создаются максимально легковесно
        icon = Image(name="clip-icon") if is_img else Label(name="clip-icon", markup=icons.clip_text)
        
        btn = Button(
            name="slot-button",
            child=Box(
                name="slot-box",
                orientation="h",
                spacing=10,
                children=[
                    icon,
                    Label(
                        name="clip-label",
                        label="[Изображение]" if is_img else content[:100].strip(),
                        ellipsization="end",
                        h_align="start",
                        h_expand=True,
                    ),
                ],
            ),
        )
        
        if is_img: # Ленивая отрисовка превью
            GLib.idle_add(lambda: self._lazy_img(icon, idx))

        btn.connect("clicked", lambda *_: self._paste(idx))
        return btn

    def _lazy_img(self, widget, idx):
        """Декодирование картинки только в момент показа."""
        img_data = subprocess.run(["cliphist", "decode", idx], capture_output=True).stdout
        if not img_data: return
        
        loader = GdkPixbuf.PixbufLoader()
        try:
            loader.write(img_data)
            loader.close()
            # NEAREST - самый быстрый метод масштабирования для процессора
            pix = loader.get_pixbuf().scale_simple(64, 64, GdkPixbuf.InterpType.NEAREST)
            widget.set_from_pixbuf(pix)
        except: pass

    def _paste(self, idx):
        data = subprocess.run(["cliphist", "decode", idx], capture_output=True).stdout
        subprocess.run(["wl-copy"], input=data)
        self.close()

    def _action(self, op):
        subprocess.run(["cliphist", op])
        self._render_items()

    def _on_search_key_press(self, _, event):
        kv = event.keyval
        if kv == Gdk.KEY_Down: self._move_sel(1)
        elif kv == Gdk.KEY_Up: self._move_sel(-1)
        elif kv in (Gdk.KEY_Return, Gdk.KEY_KP_Enter): self._use_selected()
        elif kv == Gdk.KEY_Escape: self.close()
        return False

    def _move_sel(self, step):
        items = self.viewport.get_children()
        if not items: return
        self.selected_index = max(0, min(self.selected_index + step, len(items) - 1))
        
        for i, child in enumerate(items):
            if i == self.selected_index:
                child.get_style_context().add_class("selected")
                child.grab_focus()
                # Скролл без таймеров
                self.scrolled_window.get_vadjustment().set_value(child.get_allocation().y)
            else:
                child.get_style_context().remove_class("selected")

    def _use_selected(self):
        items = self.viewport.get_children()
        if 0 <= self.selected_index < len(items):
            items[self.selected_index].clicked()

    def open(self):
        self.search_entry.set_text("")
        self._render_items()
        self.search_entry.grab_focus()

    def close(self):
        self.viewport.children = [] # Полная очистка памяти при закрытии
        self.notch.close_notch()

    def _show_empty(self):
        self.viewport.add(Box(name="no-clip-container", children=[Label(name="no-clip", markup=icons.clipboard)]))
        self.show_all()