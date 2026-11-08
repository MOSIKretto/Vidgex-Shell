import math

from fabric.widgets.image import Image
from gi.repository import Gtk


class CustomImage(Image):
    _radius = None

    def do_style_updated(self):
        Image.do_style_updated(self)
        self._radius = None
        self.queue_draw()

    def do_draw(self, cr):
        if self._radius is None:
            self._radius = self.get_style_context().get_property(
                "border-radius", Gtk.StateFlags.NORMAL
            )

        w, h = self.get_allocated_width(), self.get_allocated_height()
        r = min(self._radius, w / 2, h / 2)
        if r <= 0:
            return Image.do_draw(self, cr)

        cr.save()
        cr.new_sub_path()
        for cx, cy, a in ((w - r, r, -0.5), (w - r, h - r, 0), (r, h - r, 0.5), (r, r, 1)):
            cr.arc(cx, cy, r, a * math.pi, (a + 0.5) * math.pi)
        cr.close_path()
        cr.clip()
        result = Image.do_draw(self, cr)
        cr.restore()
        return result