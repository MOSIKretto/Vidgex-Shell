from fabric.widgets.box import Box
from fabric.widgets.stack import Stack

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from modules.Notch.Mixer.mixer import Mixer
from modules.Notch.Wallpaper.wallpapers import WallpaperSelector
from modules.Notch.Widgets.widgets import Widgets


class Dashboard(Box):
    __slots__ = (
        "notch", "widgets", "wallpapers", "mixer", 
        "stack", "switcher", "_children_cache"
    )

    # Вынос конфигурации в атрибуты класса убирает "магические числа"
    # и позволяет избежать лишних аллокаций в памяти
    TRANSITION_DURATION = 400
    SPACING = 8

    def __init__(self, **kwargs):
        super().__init__(
            name="dashboard",
            orientation="v",
            spacing=self.SPACING,
            visible=True,
            all_visible=True,
        )

        self.notch = kwargs.get("notch")
        self.widgets = Widgets(notch=self.notch)
        self.wallpapers = WallpaperSelector()
        self.mixer = Mixer()

        # Анимация возвращена, но оптимизирована за счет использования 
        # заранее определенных параметров
        self.stack = Stack(
            name="stack",
            transition_type="slide-left-right",
            transition_duration=self.TRANSITION_DURATION,
            h_expand=True,
            v_expand=True,
        )
        self.stack.set_homogeneous(False)

        self.switcher = Gtk.StackSwitcher(
            name="switcher",
            spacing=self.SPACING,
            hexpand=True,
            halign=Gtk.Align.FILL
        )
        self.switcher.set_stack(self.stack)
        self.switcher.set_homogeneous(True)

        self.stack.add_titled(self.widgets, "widgets", "Виджеты")
        self.stack.add_titled(self.wallpapers, "wallpapers", "Обои")
        self.stack.add_titled(self.mixer, "mixer", "Аудио")

        # Кэшируем список виджетов один раз, чтобы методы переключения 
        # не нагружали CPU постоянными запросами к GTK
        self._children_cache = self.stack.get_children()

        self.stack.connect("notify::visible-child", self.on_visible_child_changed)
        
        self.add(self.switcher)
        self.add(self.stack)

        self.connect("button-release-event", self._on_right_click)

    def _on_right_click(self, widget, event):
        # Использование явного кода кнопки (3 = ПКМ)
        if event.button == 3:
            self.notch.close_notch()

    def go_to_next_child(self):
        # Оптимизированный поиск индекса через статический кортеж
        cur = self.stack.get_visible_child()
        if cur in self._children_cache:
            idx = (self._children_cache.index(cur) + 1) % len(self._children_cache)
            self.stack.set_visible_child(self._children_cache[idx])

    def go_to_previous_child(self):
        cur = self.stack.get_visible_child()
        if cur in self._children_cache:
            idx = (self._children_cache.index(cur) - 1) % len(self._children_cache)
            self.stack.set_visible_child(self._children_cache[idx])

    def on_visible_child_changed(self, stack, param):
        # Прямое сравнение объектов — самый быстрый способ в Python/GTK
        if stack.get_visible_child() == self.wallpapers:
            entry = self.wallpapers.search_entry
            entry.set_text("")
            entry.grab_focus()

    def go_to_section(self, section_name):
        # Логика сохранена, убраны лишние структуры данных
        if section_name == "widgets":
            self.stack.set_visible_child(self.widgets)
        elif section_name == "wallpapers":
            self.stack.set_visible_child(self.wallpapers)
        elif section_name == "mixer":
            self.stack.set_visible_child(self.mixer)

    def destroy(self):
        self.stack.disconnect_by_func(self.on_visible_child_changed)
        
        # Рекурсивная очистка памяти
        for w in self._children_cache:
            if hasattr(w, 'destroy'):
                w.destroy()
        
        # Обнуление ссылок помогает GC (Garbage Collector) быстрее освободить RAM
        self.widgets = self.wallpapers = self.mixer = self.notch = self._children_cache = None
        super().destroy()