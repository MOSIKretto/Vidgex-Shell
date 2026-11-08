from datetime import date, timedelta

import gi

gi.require_version("Gtk", "3.0")

from fabric.widgets.centerbox import CenterBox
from fabric.widgets.label import Label
from gi.repository import Gtk

import services.icons as icons


class Calendar(Gtk.Box):
    _M = (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
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
        month = self._month = view_mode == "month"

        self.set_halign(Gtk.Align.CENTER if month else Gtk.Align.FILL)
        self.set_hexpand(not month)
        if not month:
            self.set_valign(Gtk.Align.CENTER)

        self._page = 0
        self._labels = ([], [])
        self._reset()

        pb = Gtk.Button(name="prev-month-button", child=Label(name="month-button-label", markup=icons.chevron_left))
        nb = Gtk.Button(name="next-month-button", child=Label(name="month-button-label", markup=icons.chevron_right))
        pb.connect("clicked", lambda _: self._shift(-1))
        nb.connect("clicked", lambda _: self._shift(1))
        self._ml = Gtk.Label(name="month-label")

        self.add(CenterBox(
            spacing=4, name="header",
            start_children=(pb,), center_children=(self._ml,), end_children=(nb,),
        ))

        row = Gtk.Box(spacing=4, name="weekday-row")
        fw = first_weekday
        for n in self._D[fw:] + self._D[:fw]:
            row.pack_start(Gtk.Label(label=n, name="weekday-label"), True, True, 0)
        self.pack_start(row, False, False, 0)

        self.stack = Gtk.Stack(name="calendar-stack")
        self.stack.set_transition_duration(300)
        self.pack_start(self.stack, True, True, 0)

        for i in range(2):
            grid = Gtk.Grid(
                column_homogeneous=True,
                name="calendar-grid" if month else "calendar-grid-week-view",
            )
            for n in range(42 if month else 7):
                lbl = Gtk.Label(
                    name="day-label", halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                    hexpand=True, vexpand=True,
                )
                grid.attach(lbl, n % 7, n // 7, 1, 1)
                self._labels[i].append(lbl)
            self.stack.add_named(grid, f"page_{i}")

        self.connect("map", self._on_map)
        self.show_all()
        self._show()

    def _align(self, d):
        return d - timedelta((d.weekday() - self.first_weekday) % 7)

    def _reset(self):
        self._today = t = date.today()
        self._cur = t.replace(day=1) if self._month else self._align(t)

    def _on_map(self, *_):
        today = date.today()
        if today != self._today:
            self._today = today
            self._fill(self._labels[self._page])

    def _show(self, transition=Gtk.StackTransitionType.NONE):
        cur = self._cur
        self._ml.set_text(f"{self._M[cur.month - 1]} {cur.year}")
        self._page ^= 1
        self._fill(self._labels[self._page])
        self.stack.set_visible_child_full(f"page_{self._page}", transition)

    def _fill(self, labels):
        cur, today, month = self._cur, self._today, self._month
        start = self._align(cur)
        for i, lbl in enumerate(labels):
            d = start + timedelta(i)
            out = d.month != cur.month
            dot = out and month
            if dot:
                lbl.set_name("day-empty")
                lbl.set_markup(icons.dot)
            else:
                lbl.set_name("day-label")
                lbl.set_text(str(d.day))
            ctx = lbl.get_style_context()
            for cls, on in (("current-day", d == today and not dot), ("dim-label", out and not month)):
                (ctx.add_class if on else ctx.remove_class)(cls)

    def reset_to_current(self):
        self._reset()
        self._show(Gtk.StackTransitionType.CROSSFADE)

    def _shift(self, step):
        cur = self._cur
        if self._month:
            n = cur.year * 12 + cur.month - 1 + step
            self._cur = date(n // 12, n % 12 + 1, 1)
        else:
            self._cur = cur + timedelta(7 * step)
        self._show(self._SLIDE[step])