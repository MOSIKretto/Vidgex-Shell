from fabric.core.service import Property
from fabric.widgets.widget import Widget

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gdk, GdkPixbuf, Gtk


class CircleImage(Gtk.DrawingArea, Widget):
    @Property(int, "read-write")
    def angle(self) -> int:
        return self._angle

    @angle.setter
    def angle(self, value: int):
        self._angle = value % 360
        self.queue_draw()

    def __init__(
        self,
        image_file: str = None,
        pixbuf: GdkPixbuf.Pixbuf = None,
        name: str = None,
        visible: bool = True,
        all_visible: bool = False,
        style: str = None,
        tooltip_markup: str = None,
        size: int = None,
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
        
        self._size = size or 100
        self._angle = 0
        self._orig_pixbuf = None
        self._scaled_pixbuf = None
        
        self.set_size_request(self._size, self._size)
        self.connect("draw", self._on_draw)
        self.connect("size-allocate", self._on_size_allocate)
        
        if image_file:
            self.set_image_from_file(image_file)
        elif pixbuf:
            self._set_pixbuf(pixbuf)
    
    def _on_size_allocate(self, widget, allocation):
        new_size = min(allocation.width, allocation.height)
        if new_size > 0 and new_size != self._size:
            self._size = new_size
            self._update_scaled_pixbuf()
    
    def _process_pixbuf(self, pixbuf: GdkPixbuf.Pixbuf, target_size: int) -> GdkPixbuf.Pixbuf:
        width, height = pixbuf.get_width(), pixbuf.get_height()
        
        # Обрезаем до квадрата если нужно
        if width != height:
            square = min(width, height)
            pixbuf = pixbuf.new_subpixbuf(
                (width - square) // 2,
                (height - square) // 2,
                square, square
            )
            width = square
        
        # Масштабируем если нужно
        if width != target_size:
            interp = GdkPixbuf.InterpType.HYPER if target_size < width else GdkPixbuf.InterpType.BILINEAR
            pixbuf = pixbuf.scale_simple(target_size, target_size, interp)
        
        return pixbuf
    
    def _on_draw(self, widget, ctx):
        if not self._scaled_pixbuf:
            return
        
        # Замена math.pi
        PI = 3.141592653589793
        
        width = self.get_allocated_width()
        height = self.get_allocated_height()
        
        # Вычисляем центр
        center_x = width / 2
        center_y = height / 2
        
        # Рисуем круглый путь и обрезаем (Clip)
        # ctx.arc(xc, yc, radius, angle1, angle2)
        ctx.arc(center_x, center_y, self._size / 2, 0, 2 * PI)
        ctx.clip()
        
        # Применяем вращение
        # Переносим координаты в центр, вращаем, возвращаем обратно
        ctx.translate(center_x, center_y)
        ctx.rotate(self._angle * PI / 180.0)
        ctx.translate(-center_x, -center_y)
        
        # Рисуем изображение
        # Вычисляем позицию, чтобы изображение было по центру виджета
        pb_w = self._scaled_pixbuf.get_width()
        pb_h = self._scaled_pixbuf.get_height()
        pos_x = (width - pb_w) // 2
        pos_y = (height - pb_h) // 2
        
        Gdk.cairo_set_source_pixbuf(ctx, self._scaled_pixbuf, pos_x, pos_y)
        ctx.paint()
    
    def _set_pixbuf(self, pixbuf: GdkPixbuf.Pixbuf):
        self._orig_pixbuf = pixbuf
        self._update_scaled_pixbuf()
    
    def _update_scaled_pixbuf(self):
        if self._orig_pixbuf:
            self._scaled_pixbuf = self._process_pixbuf(self._orig_pixbuf, self._size)
        self.queue_draw()
    
    def set_image_from_file(self, image_file: str):
        def load():
            try:
                # Замена os.path.isfile на GLib.file_test
                if GLib.file_test(image_file, GLib.FileTest.EXISTS):
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file(image_file)
                    GLib.idle_add(self._set_pixbuf, pixbuf)
            except Exception as e:
                print(f"Error loading image: {e}")
        
        GLib.Thread.new("load-image", lambda _: load(), None)
    
    def set_image_from_pixbuf(self, pixbuf: GdkPixbuf.Pixbuf):
        self._set_pixbuf(pixbuf)
    
    def set_image_size(self, size: int):
        if size > 0 and size != self._size:
            self._size = size
            self.set_size_request(size, size)
            self._update_scaled_pixbuf()
    
    def get_pixbuf(self) -> GdkPixbuf.Pixbuf:
        return self._scaled_pixbuf
    
    def clear_image(self):
        self._orig_pixbuf = None
        self._scaled_pixbuf = None
        self.queue_draw()
    
    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.CONSTANT_SIZE
    
    def do_get_preferred_width(self):
        return (self._size, self._size)
    
    def do_get_preferred_height(self):
        return (self._size, self._size)