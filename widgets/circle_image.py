from fabric.core.service import Property
from fabric.widgets.widget import Widget

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gdk, GdkPixbuf, Gtk

import math
import cairo
import threading
from functools import lru_cache
from typing import Optional, Tuple


class CircleImage(Gtk.DrawingArea, Widget):
    @Property(int, "read-write")
    def angle(self) -> int:
        return self._angle

    @angle.setter
    def angle(self, value: int):
        self._angle = value % 360
        if self._cached_surface:
            self._cached_surface = None
        self.queue_draw()

    def __init__(
        self,
        image_file: Optional[str] = None,
        pixbuf: Optional[GdkPixbuf.Pixbuf] = None,
        name: Optional[str] = None,
        visible: bool = True,
        all_visible: bool = False,
        style: Optional[str] = None,
        tooltip_markup: Optional[str] = None,
        size: Optional[int] = None,
        **kwargs,
    ):
        Gtk.DrawingArea.__init__(self)
        Widget.__init__(
            self,
            name=name,
            visible=visible,
            all_visible=all_visible,
            style=style,
            tooltip_markup=tooltip_markup,
            size=size,
            **kwargs,
        )
        
        self._default_size = size if size is not None else 100
        self._current_size = self._default_size
        self._angle = 0
        
        # Основные ресурсы
        self._orig_image: Optional[GdkPixbuf.Pixbuf] = None
        self._processed_image: Optional[GdkPixbuf.Pixbuf] = None
        self._cached_surface: Optional[cairo.ImageSurface] = None
        
        # Блокировки для thread-safe операций
        self._image_lock = threading.RLock()
        self._draw_lock = threading.RLock()
        
        # Флаги состояния
        self._loading = False
        self._size_requested = False
        
        # Устанавливаем минимальный размер
        self.set_size_request(self._default_size, self._default_size)
        
        # Обработчики событий
        self.connect("draw", self.on_draw)
        self.connect("size-allocate", self._on_size_allocate)
        
        # Загружаем изображение если предоставлено
        if image_file:
            self.load_image_from_file_async(image_file)
        elif pixbuf:
            self.set_image_from_pixbuf(pixbuf)
    
    def _on_size_allocate(self, widget: Gtk.Widget, allocation: Gdk.Rectangle):
        """Обработчик изменения размера виджета"""
        new_size = min(allocation.width, allocation.height)
        if new_size > 0 and new_size != self._current_size:
            self._current_size = new_size
            # Пересчитываем изображение только если размер изменился
            self._reprocess_image_async()
    
    def _process_image(self, pixbuf: GdkPixbuf.Pixbuf, target_size: int) -> GdkPixbuf.Pixbuf:
        """Обработка изображения с кэшированием промежуточных результатов"""
        width, height = pixbuf.get_width(), pixbuf.get_height()
        
        # Если изображение уже нужного размера и квадратное
        if width == height and width == target_size:
            return pixbuf
        
        # Если изображение не квадратное, обрезаем до квадрата
        if width != height:
            square_size = min(width, height)
            x_offset = (width - square_size) // 2
            y_offset = (height - square_size) // 2
            pixbuf = pixbuf.new_subpixbuf(x_offset, y_offset, square_size, square_size)
            width = height = square_size
        
        # Масштабируем до целевого размера если нужно
        if width != target_size:
            # Используем лучшее качество при уменьшении, быстрее при увеличении
            interp_type = (GdkPixbuf.InterpType.HYPER if target_size < width 
                          else GdkPixbuf.InterpType.BILINEAR)
            pixbuf = pixbuf.scale_simple(target_size, target_size, interp_type)
        
        return pixbuf
    
    @lru_cache(maxsize=4)
    def _create_clip_path(self, size: int, dpi: float = 1.0) -> cairo.Path:
        """Создание пути клипа с кэшированием"""
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        ctx = cairo.Context(surface)
        ctx.scale(dpi, dpi)
        ctx.arc(size / 2, size / 2, size / 2, 0, 2 * math.pi)
        return ctx.copy_path()
    
    def _create_cached_surface(self) -> Optional[cairo.ImageSurface]:
        """Создание кэшированной поверхности для отрисовки"""
        with self._image_lock:
            if not self._processed_image:
                return None
            
            try:
                # Создаем поверхность с учетом DPI экрана
                scale = self.get_scale_factor()
                surface_size = self._current_size * scale
                
                surface = cairo.ImageSurface(
                    cairo.FORMAT_ARGB32, 
                    surface_size, 
                    surface_size
                )
                ctx = cairo.Context(surface)
                ctx.scale(scale, scale)
                
                # Применяем клип
                clip_path = self._create_clip_path(self._current_size)
                ctx.append_path(clip_path)
                ctx.clip()
                
                # Применяем вращение
                ctx.translate(self._current_size / 2, self._current_size / 2)
                ctx.rotate(self._angle * math.pi / 180.0)
                ctx.translate(-self._current_size / 2, -self._current_size / 2)
                
                # Рисуем изображение
                Gdk.cairo_set_source_pixbuf(ctx, self._processed_image, 0, 0)
                ctx.paint()
                
                return surface
            except Exception:
                return None
    
    def on_draw(self, widget: "CircleImage", ctx: cairo.Context):
        """Отрисовка виджета"""
        with self._draw_lock:
            if not self._processed_image:
                return
            
            # Используем кэшированную поверхность если есть
            if self._cached_surface is None:
                self._cached_surface = self._create_cached_surface()
            
            if self._cached_surface:
                # Масштабируем с учетом DPI
                scale = self.get_scale_factor()
                ctx.scale(1/scale, 1/scale)
                ctx.set_source_surface(self._cached_surface, 0, 0)
                ctx.paint()
    
    def load_image_from_file_async(self, image_file: str):
        """Асинхронная загрузка изображения из файла"""
        if self._loading:
            return
        
        self._loading = True
        
        def load_task():
            try:
                # Проверяем существование файла
                import os
                if not os.path.isfile(image_file):
                    GLib.idle_add(self._on_image_load_error, image_file)
                    return
                
                # Загружаем в фоновом потоке
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(image_file)
                GLib.idle_add(self._on_image_loaded, pixbuf)
                
            except Exception as e:
                GLib.idle_add(self._on_image_load_error, str(e))
            finally:
                self._loading = False
        
        thread = threading.Thread(target=load_task, daemon=True)
        thread.start()
    
    def _on_image_loaded(self, pixbuf: GdkPixbuf.Pixbuf):
        """Обработка загруженного изображения в главном потоке"""
        with self._image_lock:
            # Освобождаем старые ресурсы
            if self._orig_image:
                self._orig_image = None
            
            self._orig_image = pixbuf
            self._processed_image = self._process_image(pixbuf, self._current_size)
            self._cached_surface = None  # Инвалидируем кэш
        
        self.queue_draw()
    
    def _on_image_load_error(self, error_info):
        """Обработка ошибки загрузки"""
        print(f"Error loading image: {error_info}")
    
    def set_image_from_file(self, image_file: str):
        """Синхронная установка изображения из файла"""
        self.load_image_from_file_async(image_file)
    
    def set_image_from_pixbuf(self, pixbuf: GdkPixbuf.Pixbuf):
        """Установка изображения из пиксбуфера"""
        with self._image_lock:
            # Освобождаем старые ресурсы
            if self._orig_image:
                self._orig_image = None
            
            self._orig_image = pixbuf
            self._processed_image = self._process_image(pixbuf, self._current_size)
            self._cached_surface = None  # Инвалидируем кэш
        
        self.queue_draw()
    
    def set_image_size(self, size: int):
        """Установка размера изображения"""
        if size <= 0 or size == self._current_size:
            return
        
        self._current_size = size
        self.set_size_request(size, size)
        self._reprocess_image_async()
    
    def _reprocess_image_async(self):
        """Асинхронный пересчет изображения"""
        def reprocess_task():
            with self._image_lock:
                if self._orig_image:
                    self._processed_image = self._process_image(
                        self._orig_image, 
                        self._current_size
                    )
                    self._cached_surface = None
                    GLib.idle_add(self.queue_draw)
        
        thread = threading.Thread(target=reprocess_task, daemon=True)
        thread.start()
    
    def get_pixbuf(self) -> Optional[GdkPixbuf.Pixbuf]:
        """Получить текущий пиксбуфер"""
        with self._image_lock:
            return self._processed_image
    
    def clear_image(self):
        """Очистить изображение"""
        with self._image_lock:
            self._orig_image = None
            self._processed_image = None
            self._cached_surface = None
        
        self.queue_draw()
    
    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        """Запрос режима измерения размера"""
        return Gtk.SizeRequestMode.CONSTANT_SIZE
    
    def do_get_preferred_width(self) -> Tuple[int, int]:
        """Предпочтительная ширина"""
        size = self._current_size
        return (size, size)
    
    def do_get_preferred_height(self) -> Tuple[int, int]:
        """Предпочтительная высота"""
        size = self._current_size
        return (size, size)
    
    def cleanup(self):
        """Очистка ресурсов"""
        with self._image_lock:
            self._orig_image = None
            self._processed_image = None
            self._cached_surface = None
            self._create_clip_path.cache_clear()