import datetime
import calendar

from fabric.widgets.centerbox import CenterBox
from fabric.widgets.label import Label
from gi.repository import Gtk

import services.icons as icons


class Calendar(Gtk.Box):
    __slots__ = ('view_mode', 'first_weekday', '_c', 'ty', 'tm', 'td', 'sy', 'sm', 'sd', '_pb', '_nb', '_ml', '_hdr', '_wr', '_gr')

    _M = (
        "January", "February", "March", "April", 
        "May", "June", "July", "August", 
        "September", "October", "November", "December"
    )
    _D = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")

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

        now = datetime.date.today()
        self.ty, self.tm, self.td = now.year, now.month, now.day
        self.sy = self.sm = self.sd = 0
        self._rst()

        self._pb = Gtk.Button(name="prev-month-button", child=Label(name="month-button-label", markup=icons.chevron_left))
        self._nb = Gtk.Button(name="next-month-button", child=Label(name="month-button-label", markup=icons.chevron_right))
        self._ml = Gtk.Label(name="month-label")

        self._pb.connect("clicked", self._prev)
        self._nb.connect("clicked", self._next)

        self._hdr = CenterBox(
            spacing=4, 
            name="header",
            start_children=(self._pb,), 
            center_children=(self._ml,), 
            end_children=(self._nb,)
        )
        self.add(self._hdr)

        self._wr = Gtk.Box(spacing=4, name="weekday-row")
        fw = first_weekday
        
        for n in self._D[fw:] + self._D[:fw]:
            self._wr.pack_start(Gtk.Label(label=n, name="weekday-label"), True, True, 0)
        self.pack_start(self._wr, False, False, 0)

        self._gr = Gtk.Grid(column_homogeneous=True, row_homogeneous=False, name="calendar-grid" if view_mode == "month" else "calendar-grid-week-view")

        rows = 6 if view_mode == "month" else 1
        for r in range(rows):
            for c in range(7):
                lbl = Gtk.Label(name="day-label", valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER, vexpand=True, hexpand=True)
                self._gr.attach(lbl, c, r, 1, 1)
                self._c.append(lbl)

        self.pack_start(self._gr, True, True, 0)
        self._upd()
        self.show_all()

    def _rst(self):
        if self.view_mode == "month":
            self.sy, self.sm, self.sd = self.ty, self.tm, 1
        else:
            today = datetime.date(self.ty, self.tm, self.td)
            offset = (today.weekday() - self.first_weekday) % 7
            start = today - datetime.timedelta(days=offset)
            self.sy, self.sm, self.sd = start.year, start.month, start.day

    def _upd(self):
        self._ml.set_text(f"{self._M[self.sm - 1]} {self.sy}")
        self._um() if self.view_mode == "month" else self._uw()

    def _um(self):
        f_wd, mdays = calendar.monthrange(self.sy, self.sm)
        off = (f_wd - self.first_weekday) % 7
        
        ty, tm, td = self.ty, self.tm, self.td
        sy, sm = self.sy, self.sm
        
        is_curr_m_y = (sm == tm and sy == ty)

        for i, lbl in enumerate(self._c):
            ctx = lbl.get_style_context()
            if ctx.has_class("current-day"): ctx.remove_class("current-day")
            
            day = i - off + 1

            if 1 <= day <= mdays:
                lbl.set_text(str(day))
                lbl.set_name("day-label")
                if is_curr_m_y and day == td:
                    ctx.add_class("current-day")
            else:
                lbl.set_name("day-empty")
                lbl.set_markup(icons.dot)

    def _uw(self):
        ty, tm, td = self.ty, self.tm, self.td
        ref_m = self.sm
        
        start_date = datetime.date(self.sy, self.sm, self.sd)

        for i, lbl in enumerate(self._c):
            curr = start_date + datetime.timedelta(days=i)
            y, m, d = curr.year, curr.month, curr.day
            
            lbl.set_text(str(d))
            lbl.set_name("day-label")

            ctx = lbl.get_style_context()
            if ctx.has_class("current-day"): ctx.remove_class("current-day")
            if ctx.has_class("dim-label"): ctx.remove_class("dim-label")

            if d == td and m == tm and y == ty:
                ctx.add_class("current-day")
            if m != ref_m:
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
            prev_week = datetime.date(self.sy, self.sm, self.sd) - datetime.timedelta(days=7)
            self.sy, self.sm, self.sd = prev_week.year, prev_week.month, prev_week.day
        self._upd()

    def _next(self, _):
        if self.view_mode == "month":
            self.sm += 1
            if self.sm > 12:
                self.sm, self.sy = 1, self.sy + 1
        else:
            next_week = datetime.date(self.sy, self.sm, self.sd) + datetime.timedelta(days=7)
            self.sy, self.sm, self.sd = next_week.year, next_week.month, next_week.day
        self._upd()

    def cleanup(self):
        self._c.clear()
        self._pb = self._nb = self._ml = None
        self._hdr = self._wr = self._gr = None