from fabric.core.service import Property
from fabric.widgets.widget import Widget

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gdk, GdkPixbuf, Gtk

import math
import cairo
import os
from typing import Optional, Tuple


class CircleImage(Gtk.DrawingArea, Widget):
    @Property(int, "read-write")
    def angle(self) -> int:
        return self._angle

    @angle.setter
    def angle(self, value: int):
        self._angle = value % 360
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
        
        self._size = size or 100
        self._angle = 0
        self._orig_pixbuf: Optional[GdkPixbuf.Pixbuf] = None
        self._scaled_pixbuf: Optional[GdkPixbuf.Pixbuf] = None
        self._cached_surface: Optional[cairo.ImageSurface] = None
        
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
    
    def _create_surface(self) -> Optional[cairo.ImageSurface]:
        if not self._scaled_pixbuf:
            return None
        
        scale = self.get_scale_factor()
        surface_size = self._size * scale
        
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, surface_size, surface_size)
        ctx = cairo.Context(surface)
        ctx.scale(scale, scale)
        
        # Круглый клип
        ctx.arc(self._size / 2, self._size / 2, self._size / 2, 0, 2 * math.pi)
        ctx.clip()
        
        # Вращение
        ctx.translate(self._size / 2, self._size / 2)
        ctx.rotate(self._angle * math.pi / 180.0)
        ctx.translate(-self._size / 2, -self._size / 2)
        
        Gdk.cairo_set_source_pixbuf(ctx, self._scaled_pixbuf, 0, 0)
        ctx.paint()
        
        return surface
    
    def _on_draw(self, widget, ctx):
        if not self._scaled_pixbuf:
            return
        
        if not self._cached_surface:
            self._cached_surface = self._create_surface()
        
        if self._cached_surface:
            scale = self.get_scale_factor()
            ctx.scale(1/scale, 1/scale)
            ctx.set_source_surface(self._cached_surface, 0, 0)
            ctx.paint()
    
    def _set_pixbuf(self, pixbuf: GdkPixbuf.Pixbuf):
        self._orig_pixbuf = pixbuf
        self._update_scaled_pixbuf()
    
    def _update_scaled_pixbuf(self):
        if self._orig_pixbuf:
            self._scaled_pixbuf = self._process_pixbuf(self._orig_pixbuf, self._size)
        self._cached_surface = None
        self.queue_draw()
    
    def set_image_from_file(self, image_file: str):
        def load():
            try:
                if os.path.isfile(image_file):
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
    
    def get_pixbuf(self) -> Optional[GdkPixbuf.Pixbuf]:
        return self._scaled_pixbuf
    
    def clear_image(self):
        self._orig_pixbuf = None
        self._scaled_pixbuf = None
        self._cached_surface = None
        self.queue_draw()
    
    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.CONSTANT_SIZE
    
    def do_get_preferred_width(self) -> Tuple[int, int]:
        return (self._size, self._size)
    
    def do_get_preferred_height(self) -> Tuple[int, int]:
        return (self._size, self._size)