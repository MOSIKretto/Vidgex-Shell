import calendar
import datetime

import gi

gi.require_version("Gtk", "3.0")

from fabric.widgets.centerbox import CenterBox
from fabric.widgets.label import Label
from gi.repository import Gtk

import services.icons as icons


class Calendar(Gtk.Box):
    _M = (
        "January", "February", "March", "April",
        "May", "June", "July", "August",
        "September", "October", "November", "December"
    )
    _D = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")

    _SLIDE = {
        -1: Gtk.StackTransitionType.SLIDE_RIGHT,
        1: Gtk.StackTransitionType.SLIDE_LEFT,
    }

    def __init__(self, view_mode="month", first_weekday=0):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, name="calendar")

        self.view_mode = view_mode
        self.first_weekday = first_weekday

        self._active_page = 0
        self._labels = [[], []]

        if view_mode == "month":
            self.set_halign(Gtk.Align.CENTER)
            self.set_hexpand(False)
        else:
            self.set_halign(Gtk.Align.FILL)
            self.set_hexpand(True)
            self.set_valign(Gtk.Align.CENTER)
            self.set_vexpand(False)

        self._sync_today()
        self._rst()

        self._pb = Gtk.Button(name="prev-month-button", child=Label(name="month-button-label", markup=icons.chevron_left))
        self._nb = Gtk.Button(name="next-month-button", child=Label(name="month-button-label", markup=icons.chevron_right))
        self._ml = Gtk.Label(name="month-label")

        self._pb.connect("clicked", self._prev)
        self._nb.connect("clicked", self._next)

        self.add(CenterBox(
            spacing=4, name="header",
            start_children=(self._pb,), center_children=(self._ml,), end_children=(self._nb,)
        ))

        weekday_row = Gtk.Box(spacing=4, name="weekday-row")
        fw = first_weekday
        for n in self._D[fw:] + self._D[:fw]:
            weekday_row.pack_start(Gtk.Label(label=n, name="weekday-label"), True, True, 0)
        self.pack_start(weekday_row, False, False, 0)

        self.stack = Gtk.Stack(name="calendar-stack")
        self.stack.set_transition_duration(300)
        self.pack_start(self.stack, True, True, 0)

        rows = 6 if view_mode == "month" else 1
        grid_name = "calendar-grid" if view_mode == "month" else "calendar-grid-week-view"

        for i in range(2):
            grid = Gtk.Grid(column_homogeneous=True, row_homogeneous=False, name=grid_name)
            for r in range(rows):
                for c in range(7):
                    lbl = Gtk.Label(name="day-label", valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER, vexpand=True, hexpand=True)
                    grid.attach(lbl, c, r, 1, 1)
                    self._labels[i].append(lbl)
            self.stack.add_named(grid, f"page_{i}")

        self.show_all()

        self._upd(transition=Gtk.StackTransitionType.NONE)

    def _sync_today(self):
        now = datetime.date.today()
        self.ty, self.tm, self.td = now.year, now.month, now.day

    def _rst(self):
        if self.view_mode == "month":
            self.sy, self.sm, self.sd = self.ty, self.tm, 1
        else:
            today = datetime.date(self.ty, self.tm, self.td)
            offset = (today.weekday() - self.first_weekday) % 7
            start = today - datetime.timedelta(days=offset)
            self.sy, self.sm, self.sd = start.year, start.month, start.day

    def _upd(self, transition=Gtk.StackTransitionType.NONE):
        self._ml.set_text(f"{self._M[self.sm - 1]} {self.sy}")

        target_page = 0 if transition == Gtk.StackTransitionType.NONE else 1 - self._active_page
        target_labels = self._labels[target_page]

        if self.view_mode == "month":
            self._um(target_labels)
        else:
            self._uw(target_labels)

        page_name = f"page_{target_page}"
        if transition != Gtk.StackTransitionType.NONE:
            self.stack.set_visible_child_full(page_name, transition)
        else:
            self.stack.set_visible_child_name(page_name)

        self._active_page = target_page

    def _um(self, labels):
        f_wd, mdays = calendar.monthrange(self.sy, self.sm)
        off = (f_wd - self.first_weekday) % 7

        ty, tm, td = self.ty, self.tm, self.td
        sy, sm = self.sy, self.sm

        is_curr_m_y = (sm == tm and sy == ty)

        for i, lbl in enumerate(labels):
            ctx = lbl.get_style_context()
            ctx.remove_class("current-day")

            day = i - off + 1

            if 1 <= day <= mdays:
                lbl.set_text(str(day))
                lbl.set_name("day-label")
                if is_curr_m_y and day == td:
                    ctx.add_class("current-day")
            else:
                lbl.set_name("day-empty")
                lbl.set_markup(icons.dot)

    def _uw(self, labels):
        ty, tm, td = self.ty, self.tm, self.td
        ref_m = self.sm

        start_date = datetime.date(self.sy, self.sm, self.sd)

        for i, lbl in enumerate(labels):
            curr = start_date + datetime.timedelta(days=i)
            y, m, d = curr.year, curr.month, curr.day

            lbl.set_text(str(d))

            ctx = lbl.get_style_context()
            ctx.remove_class("current-day")
            ctx.remove_class("dim-label")

            if d == td and m == tm and y == ty:
                ctx.add_class("current-day")
            if m != ref_m:
                ctx.add_class("dim-label")

    def reset_to_current(self):
        self._sync_today()
        self._rst()
        self._upd(transition=Gtk.StackTransitionType.CROSSFADE)

    def _shift(self, step):
        if self.view_mode == "month":
            self.sy, month0 = divmod(self.sy * 12 + self.sm - 1 + step, 12)
            self.sm = month0 + 1
        else:
            moved = datetime.date(self.sy, self.sm, self.sd) + datetime.timedelta(days=7 * step)
            self.sy, self.sm, self.sd = moved.year, moved.month, moved.day

        self._upd(transition=self._SLIDE[step])

    def _prev(self, _):
        self._shift(-1)

    def _next(self, _):
        self._shift(1)