from fabric.widgets.image import Image

from gi.repository import Gtk

import math


class CustomImage(Image):
    def do_render_rectangle(self, cr, width, height, radius=0):
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

    def do_draw(self, cr):
        context = self.get_style_context()
        width = self.get_allocated_width()
        height = self.get_allocated_height()
        
        cr.save()

        border_radius = context.get_property("border-radius", Gtk.StateFlags.NORMAL)
        self.do_render_rectangle(cr, width, height, border_radius)
        cr.clip()
        
        Image.do_draw(self, cr)

        cr.restore()