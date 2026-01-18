from fabric.widgets.image import Image
from gi.repository import Gtk
import math


class CustomImage(Image):
    def do_draw(self, cr):
        width = self.get_allocated_width()
        height = self.get_allocated_height()
        radius = self.get_style_context().get_property("border-radius", Gtk.StateFlags.NORMAL)

        cr.save()

        cr.move_to(radius, 0)
        cr.line_to(width - radius, 0)
        cr.arc(width - radius, radius, radius, -math.pi / 2, 0)
        cr.line_to(width, height - radius)
        cr.arc(width - radius, height - radius, radius, 0, math.pi / 2)
        cr.line_to(radius, height)
        cr.arc(radius, height - radius, radius, math.pi / 2, math.pi)
        cr.line_to(0, radius)
        cr.arc(radius, radius, radius, math.pi, 3 * math.pi / 2)
        cr.close_path()

        cr.clip()
        Image.do_draw(self, cr)
        cr.restore()