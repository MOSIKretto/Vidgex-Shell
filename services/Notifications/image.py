import math
from fabric.widgets.image import Image
from gi.repository import Gtk


class CustomImage(Image):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._cached_radius: float | None = None

    def do_style_updated(self):
        Image.do_style_updated(self)
        self._cached_radius = None
        self.queue_draw()

    def do_draw(self, cr):
        if self._cached_radius is None:
            self._cached_radius = float(
                self.get_style_context().get_property("border-radius", Gtk.StateFlags.NORMAL)
            )

        width = self.get_allocated_width()
        height = self.get_allocated_height()
        radius = min(self._cached_radius, width / 2.0, height / 2.0)

        if radius <= 0:
            return Image.do_draw(self, cr)

        cr.save()
        cr.move_to(radius, 0)
        cr.line_to(width - radius, 0)
        cr.arc(width - radius, radius, radius, -0.5 * math.pi, 0)
        cr.line_to(width, height - radius)
        cr.arc(width - radius, height - radius, radius, 0, 0.5 * math.pi)
        cr.line_to(radius, height)
        cr.arc(radius, height - radius, radius, 0.5 * math.pi, math.pi)
        cr.line_to(0, radius)
        cr.arc(radius, radius, radius, math.pi, 1.5 * math.pi)
        cr.close_path()
        cr.clip()
        result = Image.do_draw(self, cr)
        cr.restore()
        return result