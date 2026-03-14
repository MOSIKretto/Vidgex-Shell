import datetime
import calendar

from fabric.widgets.centerbox import CenterBox
from fabric.widgets.label import Label
from gi.repository import Gdk, Gtk

import services.icons as icons


_pointer_cursor: Gdk.Cursor | None = None
_default_cursor: Gdk.Cursor | None = None


def _get_cursors(display: Gdk.Display):
    global _pointer_cursor, _default_cursor
    if _pointer_cursor is None:
        _pointer_cursor = Gdk.Cursor.new_from_name(display, "pointer")
        _default_cursor = Gdk.Cursor.new_from_name(display, "default")
    return _pointer_cursor, _default_cursor


def _on_btn_enter(widget: Gtk.Widget, _event: Gdk.EventCrossing):
    if win := widget.get_window():
        win.set_cursor(_get_cursors(win.get_display())[0])
    return False


def _on_btn_leave(widget: Gtk.Widget, _event: Gdk.EventCrossing):
    if win := widget.get_window():
        win.set_cursor(_get_cursors(win.get_display())[1])
    return False


def _setup_pointer_cursor(widget: Gtk.Widget):
    widget.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
    widget.connect("enter-notify-event", _on_btn_enter)
    widget.connect("leave-notify-event", _on_btn_leave)


class Calendar(Gtk.Box):
    __slots__ = (
        'view_mode', 'first_weekday', 'ty', 'tm', 'td', 'sy', 'sm', 'sd',
        '_pb', '_nb', '_ml', '_hdr', '_wr', 'stack', 
        '_active_page', '_grids', '_labels'
    )

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
        
        self._active_page = 0
        self._grids = []
        self._labels = [[], []]

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

        _setup_pointer_cursor(self._pb)
        _setup_pointer_cursor(self._nb)

        self._hdr = CenterBox(
            spacing=4, name="header",
            start_children=(self._pb,), center_children=(self._ml,), end_children=(self._nb,)
        )
        self.add(self._hdr)

        self._wr = Gtk.Box(spacing=4, name="weekday-row")
        fw = first_weekday

        for n in self._D[fw:] + self._D[:fw]:
            self._wr.pack_start(Gtk.Label(label=n, name="weekday-label"), True, True, 0)
        self.pack_start(self._wr, False, False, 0)

        self.stack = Gtk.Stack(name="calendar-stack")
        self.stack.set_transition_duration(300)
        self.pack_start(self.stack, True, True, 0)

        rows = 6 if self.view_mode == "month" else 1
        grid_name = "calendar-grid" if self.view_mode == "month" else "calendar-grid-week-view"
        
        for i in range(2):
            grid = Gtk.Grid(column_homogeneous=True, row_homogeneous=False, name=grid_name)
            for r in range(rows):
                for c in range(7):
                    lbl = Gtk.Label(name="day-label", valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER, vexpand=True, hexpand=True)
                    grid.attach(lbl, c, r, 1, 1)
                    self._labels[i].append(lbl)
            self.stack.add_named(grid, f"page_{i}")
            self._grids.append(grid)

        self.show_all()
        
        self._upd(transition=Gtk.StackTransitionType.NONE)

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
            if ctx.has_class("current-day"): 
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
        self._upd(transition=Gtk.StackTransitionType.CROSSFADE)

    def _prev(self, _):
        if self.view_mode == "month":
            self.sm -= 1
            if self.sm < 1:
                self.sm, self.sy = 12, self.sy - 1
        else:
            prev_week = datetime.date(self.sy, self.sm, self.sd) - datetime.timedelta(days=7)
            self.sy, self.sm, self.sd = prev_week.year, prev_week.month, prev_week.day

        self._upd(transition=Gtk.StackTransitionType.SLIDE_RIGHT)

    def _next(self, _):
        if self.view_mode == "month":
            self.sm += 1
            if self.sm > 12:
                self.sm, self.sy = 1, self.sy + 1
        else:
            next_week = datetime.date(self.sy, self.sm, self.sd) + datetime.timedelta(days=7)
            self.sy, self.sm, self.sd = next_week.year, next_week.month, next_week.day

        self._upd(transition=Gtk.StackTransitionType.SLIDE_LEFT)

    def cleanup(self):
        if self.stack:
            self.stack.destroy()
        
        self._pb = self._nb = self._ml = None
        self._hdr = self._wr = self.stack = None
        
        self._grids.clear()
        self._labels[0].clear()
        self._labels[1].clear()