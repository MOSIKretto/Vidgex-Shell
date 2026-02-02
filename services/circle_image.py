from typing import Literal

from fabric.core.service import Property
from fabric.widgets.widget import Widget

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk


class CircleImage(Gtk.DrawingArea, Widget):
    _TWO_PI = 6.283185307179586
    _DEG_TO_RAD = 0.017453292519943295
    
    @Property(int, "read-write", default_value=0)
    def angle(self) -> int:
        return self._angle

    @angle.setter
    def angle(self, value: int):
        self._angle = value % 360
        self.queue_draw()

    def __init__(
        self,
        image_file: str | None = None,
        pixbuf: GdkPixbuf.Pixbuf | None = None,
        name: str | None = None,
        visible: bool = True,
        all_visible: bool = False,
        style: str | None = None,
        tooltip_text: str | None = None,
        tooltip_markup: str | None = None,
        h_align: Literal["fill", "start", "end", "center", "baseline"] | Gtk.Align | None = None,
        v_align: Literal["fill", "start", "end", "center", "baseline"] | Gtk.Align | None = None,
        h_expand: bool = False,
        v_expand: bool = False,
        size: int | None = None,
        **kwargs,
    ):
        Gtk.DrawingArea.__init__(self)
        Widget.__init__(
            self,
            name=name,
            visible=visible,
            all_visible=all_visible,
            style=style,
            tooltip_text=tooltip_text,
            tooltip_markup=tooltip_markup,
            h_align=h_align,
            v_align=v_align,
            h_expand=h_expand,
            v_expand=v_expand,
            **kwargs,
        )
        
        # Состояние
        self._size = size if size is not None else 100
        self._angle = 0
        self._scaled_pixbuf: GdkPixbuf.Pixbuf | None = None
        
        # Состояние загрузки
        self._current_file: str | None = None
        self._pending_file: str | None = None
        self._loading = False
        
        # Настройка виджета
        self.set_size_request(self._size, self._size)
        self.connect("draw", self._on_draw)
        self.connect("size-allocate", self._on_size_allocate)
        
        # Начальная загрузка
        if image_file:
            self.set_image_from_file(image_file)
        elif pixbuf:
            self._process_and_set(pixbuf)
    
    def _on_size_allocate(self, widget, allocation):
        new_size = min(allocation.width, allocation.height)
        
        if new_size <= 0 or new_size == self._size:
            return
        
        old_size = self._size
        self._size = new_size
        
        # Перезагружаем если размер значительно изменился
        if self._current_file and abs(new_size - old_size) > 10:
            self._reload_current_file()
        elif self._scaled_pixbuf:
            # Перемасштабируем существующий pixbuf
            self._scaled_pixbuf = self._process_pixbuf(self._scaled_pixbuf, new_size)
            self.queue_draw()
    
    def _on_draw(self, widget, ctx):
        if not self._scaled_pixbuf:
            return
        
        width = self.get_allocated_width()
        height = self.get_allocated_height()
        center_x = width / 2
        center_y = height / 2
        radius = min(self._size, width, height) / 2
        
        # Круглая маска
        ctx.arc(center_x, center_y, radius, 0, self._TWO_PI)
        ctx.clip()
        
        # Вращение (только если нужно)
        if self._angle != 0:
            ctx.translate(center_x, center_y)
            ctx.rotate(self._angle * self._DEG_TO_RAD)
            ctx.translate(-center_x, -center_y)
        
        # Отрисовка изображения по центру
        pb_w = self._scaled_pixbuf.get_width()
        pb_h = self._scaled_pixbuf.get_height()
        pos_x = (width - pb_w) / 2
        pos_y = (height - pb_h) / 2
        
        Gdk.cairo_set_source_pixbuf(ctx, self._scaled_pixbuf, pos_x, pos_y)
        ctx.paint()
    
    def _process_pixbuf(self, pixbuf: GdkPixbuf.Pixbuf, target_size: int) -> GdkPixbuf.Pixbuf | None:
        if not pixbuf:
            return None
        
        width = pixbuf.get_width()
        height = pixbuf.get_height()
        
        # Обрезаем до квадрата (центрированно)
        if width != height:
            square = min(width, height)
            pixbuf = pixbuf.new_subpixbuf(
                (width - square) // 2,
                (height - square) // 2,
                square,
                square
            )
            width = square
        
        # Масштабируем с адаптивным качеством
        if width != target_size:
            # HYPER для уменьшения (качественнее), BILINEAR для увеличения (быстрее)
            interp = (
                GdkPixbuf.InterpType.HYPER
                if target_size < width
                else GdkPixbuf.InterpType.BILINEAR
            )
            pixbuf = pixbuf.scale_simple(target_size, target_size, interp)
        
        return pixbuf
    
    def _process_and_set(self, pixbuf: GdkPixbuf.Pixbuf):
        self._scaled_pixbuf = self._process_pixbuf(pixbuf, self._size)
        self.queue_draw()
    
    def _reload_current_file(self):
        if self._current_file:
            # Сбрасываем флаг для принудительной перезагрузки
            self._loading = False
            file_to_reload = self._current_file
            self._current_file = None  # Сбрасываем для обхода проверки
            self.set_image_from_file(file_to_reload)
    
    def set_image_from_file(self, image_file: str):
        if not image_file:
            return
        
        # Не загружаем тот же файл повторно
        if image_file == self._current_file and self._scaled_pixbuf:
            return
        
        self._current_file = image_file
        
        # Если уже идёт загрузка — ставим в очередь
        if self._loading:
            self._pending_file = image_file
            return
        
        self._loading = True
        self._pending_file = None
        
        # Запоминаем параметры для проверки в callback
        load_file = image_file
        load_size = self._size
        
        def load_thread(_):
            try:
                if not GLib.file_test(load_file, GLib.FileTest.EXISTS):
                    return
                
                # Загружаем сразу в оптимальном размере (с запасом для качества)
                target = load_size * 2
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    load_file, target, target, True
                )
                
                # Проверяем что файл не изменился пока грузили
                if load_file == self._current_file:
                    GLib.idle_add(self._on_image_loaded, pixbuf, load_file)
            finally:
                GLib.idle_add(self._on_loading_complete)
        
        GLib.Thread.new(None, load_thread, None)
    
    def _on_image_loaded(self, pixbuf: GdkPixbuf.Pixbuf, file_path: str):
        # Финальная проверка что файл всё ещё актуален
        if file_path == self._current_file:
            self._process_and_set(pixbuf)
        return False
    
    def _on_loading_complete(self):
        self._loading = False
        
        # Если есть файл в очереди — загружаем
        if self._pending_file:
            pending = self._pending_file
            self._pending_file = None
            self.set_image_from_file(pending)
        
        return False
    
    def set_image_from_pixbuf(self, pixbuf: GdkPixbuf.Pixbuf | None):
        if not pixbuf:
            self.clear_image()
            return
        
        self._current_file = None
        self._pending_file = None
        self._process_and_set(pixbuf)
    
    def set_image_size(self, size: int):
        if size <= 0 or size == self._size:
            return
        
        self._size = size
        self.set_size_request(size, size)
        
        # Перезагружаем для лучшего качества
        if self._current_file:
            self._reload_current_file()
        elif self._scaled_pixbuf:
            self._scaled_pixbuf = self._process_pixbuf(self._scaled_pixbuf, size)
            self.queue_draw()
    
    def get_pixbuf(self) -> GdkPixbuf.Pixbuf | None:
        return self._scaled_pixbuf
    
    def get_size(self) -> int:
        return self._size
    
    def clear_image(self):
        self._scaled_pixbuf = None
        self._current_file = None
        self._pending_file = None
        self.queue_draw()
    
    def is_loading(self) -> bool:
        return self._loading
    
    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.CONSTANT_SIZE
    
    def do_get_preferred_width(self) -> tuple[int, int]:
        return (self._size, self._size)
    
    def do_get_preferred_height(self) -> tuple[int, int]:
        return (self._size, self._size)