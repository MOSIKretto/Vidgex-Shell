from fabric.widgets.centerbox import CenterBox
from fabric.widgets.label import Label

from gi.repository import Gtk, GLib

import services.icons as icons


class Calendar(Gtk.Box):
    __slots__ = ('view_mode', 'first_weekday', '_c', 'ty', 'tm', 'td',
                 'sy', 'sm', 'sd', '_pb', '_nb', '_ml', '_hdr', '_wr', '_gr')

    _M = ("Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
          "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь")
    _D = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
    _ML = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)

    def __init__(self, view_mode="month", first_weekday=0):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, name="calendar")

        self.view_mode = view_mode
        self.first_weekday = first_weekday
        self._c = []

        if view_mode == "month":
            self.set_halign(Gtk.Align.CENTER)
            self.set_hexpand(False)
        else:
            self.set_halign(Gtk.Align.FILL)
            self.set_hexpand(True)
            self.set_valign(Gtk.Align.CENTER)
            self.set_vexpand(False)

        dt = GLib.DateTime.new_now_local()
        self.ty, self.tm, self.td = dt.get_year(), dt.get_month(), dt.get_day_of_month()
        self.sy = self.sm = self.sd = 0
        self._rst()

        self._pb = Gtk.Button(name="prev-month-button",
                              child=Label(name="month-button-label", markup=icons.chevron_left))
        self._nb = Gtk.Button(name="next-month-button",
                              child=Label(name="month-button-label", markup=icons.chevron_right))
        self._ml = Gtk.Label(name="month-label")

        self._pb.connect("clicked", self._prev)
        self._nb.connect("clicked", self._next)

        self._hdr = CenterBox(spacing=4, name="header",
                              start_children=[self._pb], center_children=[self._ml], end_children=[self._nb])
        self.add(self._hdr)

        self._wr = Gtk.Box(spacing=4, name="weekday-row")
        fw = first_weekday
        for n in self._D[fw:] + self._D[:fw]:
            self._wr.pack_start(Gtk.Label(label=n, name="weekday-label"), True, True, 0)
        self.pack_start(self._wr, False, False, 0)

        self._gr = Gtk.Grid(column_homogeneous=True, row_homogeneous=False,
                            name="calendar-grid" if view_mode == "month" else "calendar-grid-week-view")

        rows = 6 if view_mode == "month" else 1
        for r in range(rows):
            for c in range(7):
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, name="day-box")
                lbl = Gtk.Label(name="day-label")
                lbl.set_valign(Gtk.Align.CENTER)
                lbl.set_halign(Gtk.Align.CENTER)
                lbl.set_vexpand(True)
                lbl.set_hexpand(True)
                box.pack_start(lbl, True, True, 0)
                self._gr.attach(box, c, r, 1, 1)
                self._c.append(lbl)

        self.pack_start(self._gr, True, True, 0)
        self._upd()
        self.show_all()

    def _lp(self, y):
        return (y % 4 == 0 and y % 100 != 0) or y % 400 == 0

    def _dim(self, y, m):
        return 29 if m == 2 and self._lp(y) else self._ML[m - 1]

    def _dow(self, y, m, d):
        if m < 3:
            m, y = m + 12, y - 1
        k, j = y % 100, y // 100
        return ((d + (13 * (m + 1)) // 5 + k + k // 4 + j // 4 - 2 * j) % 7 + 5) % 7

    def _add(self, y, m, d, delta):
        d += delta
        while d > self._dim(y, m):
            d -= self._dim(y, m)
            m += 1
            if m > 12:
                m, y = 1, y + 1
        while d < 1:
            m -= 1
            if m < 1:
                m, y = 12, y - 1
            d += self._dim(y, m)
        return y, m, d

    def _rst(self):
        if self.view_mode == "month":
            self.sy, self.sm, self.sd = self.ty, self.tm, 1
        else:
            diff = (self._dow(self.ty, self.tm, self.td) - self.first_weekday + 7) % 7
            self.sy, self.sm, self.sd = self._add(self.ty, self.tm, self.td, -diff)

    def _upd(self):
        self._ml.set_text(f"{self._M[self.sm - 1]} {self.sy}")
        (self._um if self.view_mode == "month" else self._uw)()

    def _um(self):
        off = (self._dow(self.sy, self.sm, 1) - self.first_weekday + 7) % 7
        mdays = self._dim(self.sy, self.sm)
        ty, tm, td = self.ty, self.tm, self.td
        sy, sm = self.sy, self.sm

        for i, lbl in enumerate(self._c):
            ctx = lbl.get_style_context()
            ctx.remove_class("current-day")
            day = i - off + 1

            if 1 <= day <= mdays:
                lbl.set_text(str(day))
                lbl.set_name("day-label")
                if day == td and sm == tm and sy == ty:
                    ctx.add_class("current-day")
            else:
                lbl.set_name("day-empty")
                lbl.set_markup(icons.dot)

    def _uw(self):
        ref = self.sm
        ty, tm, td = self.ty, self.tm, self.td

        for i, lbl in enumerate(self._c):
            y, m, d = self._add(self.sy, self.sm, self.sd, i)
            lbl.set_text(str(d))
            lbl.set_name("day-label")

            ctx = lbl.get_style_context()
            ctx.remove_class("current-day")
            ctx.remove_class("dim-label")

            if d == td and m == tm and y == ty:
                ctx.add_class("current-day")
            if m != ref:
                ctx.add_class("dim-label")

    def reset_to_current(self):
        self._rst()
        self._upd()

    def _prev(self, _):
        if self.view_mode == "month":
            self.sm -= 1
            if self.sm < 1:
                self.sm, self.sy = 12, self.sy - 1
        else:
            self.sy, self.sm, self.sd = self._add(self.sy, self.sm, self.sd, -7)
        self._upd()

    def _next(self, _):
        if self.view_mode == "month":
            self.sm += 1
            if self.sm > 12:
                self.sm, self.sy = 1, self.sy + 1
        else:
            self.sy, self.sm, self.sd = self._add(self.sy, self.sm, self.sd, 7)
        self._upd()

    def cleanup(self):
        self._c.clear()
        self._pb = self._nb = self._ml = None
        self._hdr = self._wr = self._gr = None