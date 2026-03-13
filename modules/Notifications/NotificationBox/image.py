import math
from fabric.widgets.image import Image
from gi.repository import Gtk


PI = math.pi
HALF_PI = PI / 2
THREE_HALF_PI = 3 * PI / 2

class CustomImage(Image):
    __slots__ = ('_cached_radius',)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._cached_radius = None

    def do_style_updated(self):
        Image.do_style_updated(self)
        self._cached_radius = None

    def do_draw(self, cr):
        width = self.get_allocated_width()
        height = self.get_allocated_height()

        if self._cached_radius is None:
            radius_prop = self.get_style_context().get_property("border-radius", Gtk.StateFlags.NORMAL)
            self._cached_radius = radius_prop if isinstance(radius_prop, (int, float)) else 0
            
        radius = self._cached_radius

        if radius <= 0:
            return Image.do_draw(self, cr)

        cr.save()

        cr.move_to(radius, 0)
        cr.line_to(width - radius, 0)
        cr.arc(width - radius, radius, radius, -HALF_PI, 0)
        cr.line_to(width, height - radius)
        cr.arc(width - radius, height - radius, radius, 0, HALF_PI)
        cr.line_to(radius, height)
        cr.arc(radius, height - radius, radius, HALF_PI, PI)
        cr.line_to(0, radius)
        cr.arc(radius, radius, radius, PI, THREE_HALF_PI)
        cr.close_path()

        cr.clip()
        Image.do_draw(self, cr)
        cr.restore()