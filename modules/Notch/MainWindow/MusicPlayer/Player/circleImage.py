from fabric.core.service import Property
from fabric.widgets.widget import Widget

import cairo
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
        value %= 360
        if value != self._angle:
            self._angle = value
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
        h_align: str | Gtk.Align | None = None,
        v_align: str | Gtk.Align | None = None,
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

        self._size = size if size is not None else 100
        self._angle = 0
        self._surface: cairo.ImageSurface | None = None
        self._surf_sz = 0

        self._current_file: str | None = None
        self._pending_file: str | None = None
        self._loading = False

        self.set_size_request(self._size, self._size)
        self.connect("draw", self._on_draw)
        self.connect("size-allocate", self._on_size_allocate)

        if image_file:
            self.set_image_from_file(image_file)
        elif pixbuf:
            self._apply_pixbuf(pixbuf)

    def _on_size_allocate(self, widget, allocation):
        new_size = min(allocation.width, allocation.height)
        if new_size <= 0 or new_size == self._size:
            return
        old_size = self._size
        self._size = new_size
        if self._current_file and abs(new_size - old_size) > 10:
            self._reload_current_file()
        elif self._surface:
            self._rescale_surface(new_size)

    def _on_draw(self, widget, ctx):
        surface = self._surface
        if not surface:
            return

        w = self.get_allocated_width()
        h = self.get_allocated_height()
        cx = w * 0.5
        cy = h * 0.5
        r = min(self._size, w, h) * 0.5

        ctx.arc(cx, cy, r, 0, self._TWO_PI)
        ctx.clip()

        if self._angle:
            ctx.translate(cx, cy)
            ctx.rotate(self._angle * self._DEG_TO_RAD)
            ctx.translate(-cx, -cy)

        sz = self._surf_sz
        ctx.set_source_surface(surface, (w - sz) * 0.5, (h - sz) * 0.5)
        ctx.paint()


    @staticmethod
    def _crop_and_scale(pixbuf: GdkPixbuf.Pixbuf, target: int) -> GdkPixbuf.Pixbuf | None:
        if not pixbuf:
            return None
        w = pixbuf.get_width()
        h = pixbuf.get_height()
        if w != h:
            s = min(w, h)
            pixbuf = pixbuf.new_subpixbuf((w - s) >> 1, (h - s) >> 1, s, s)
            w = s
        if w != target:
            pixbuf = pixbuf.scale_simple(
                target, target, GdkPixbuf.InterpType.BILINEAR
            )
        return pixbuf

    def _set_surface(self, pixbuf: GdkPixbuf.Pixbuf | None):
        if not pixbuf:
            self._surface = None
            self._surf_sz = 0
            return
        sz = pixbuf.get_width()
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, sz, sz)
        cr = cairo.Context(surface)
        Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
        cr.paint()
        self._surface = surface
        self._surf_sz = sz

    def _apply_pixbuf(self, pixbuf: GdkPixbuf.Pixbuf):
        self._set_surface(self._crop_and_scale(pixbuf, self._size))
        self.queue_draw()

    def _rescale_surface(self, new_size: int):
        surface = self._surface
        if not surface:
            return
        sz = self._surf_sz
        pb = Gdk.pixbuf_get_from_surface(surface, 0, 0, sz, sz)
        if pb:
            scaled = pb.scale_simple(
                new_size, new_size, GdkPixbuf.InterpType.BILINEAR
            )
            if scaled:
                self._set_surface(scaled)
                self.queue_draw()

    def _reload_current_file(self):
        if self._current_file:
            self._loading = False
            f = self._current_file
            self._current_file = None
            self.set_image_from_file(f)

    def set_image_from_file(self, image_file: str):
        if not image_file:
            return
        if image_file == self._current_file and self._surface:
            return
        self._current_file = image_file
        if self._loading:
            self._pending_file = image_file
            return
        self._loading = True
        self._pending_file = None

        load_file = image_file
        load_size = self._size

        def _load(_):
            try:
                if not GLib.file_test(load_file, GLib.FileTest.EXISTS):
                    return
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    load_file, load_size, load_size, True
                )
                if load_file == self._current_file:
                    GLib.idle_add(self._on_image_loaded, pixbuf, load_file)
            finally:
                GLib.idle_add(self._on_loading_complete)

        GLib.Thread.new(None, _load, None)

    def _on_image_loaded(self, pixbuf: GdkPixbuf.Pixbuf, file_path: str):
        if file_path == self._current_file:
            self._apply_pixbuf(pixbuf)
        return False

    def _on_loading_complete(self):
        self._loading = False
        if self._pending_file:
            p = self._pending_file
            self._pending_file = None
            self.set_image_from_file(p)
        return False

    def set_image_from_pixbuf(self, pixbuf: GdkPixbuf.Pixbuf | None):
        if not pixbuf:
            self.clear_image()
            return
        self._current_file = None
        self._pending_file = None
        self._apply_pixbuf(pixbuf)

    def set_image_size(self, size: int):
        if size <= 0 or size == self._size:
            return
        self._size = size
        self.set_size_request(size, size)
        if self._current_file:
            self._reload_current_file()
        elif self._surface:
            self._rescale_surface(size)

    def get_pixbuf(self) -> GdkPixbuf.Pixbuf | None:
        surface = self._surface
        if surface:
            sz = self._surf_sz
            return Gdk.pixbuf_get_from_surface(surface, 0, 0, sz, sz)
        return None

    def get_size(self) -> int:
        return self._size

    def clear_image(self):
        self._surface = None
        self._surf_sz = 0
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