from fabric.widgets.centerbox import CenterBox
from fabric.widgets.label import Label

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

import calendar
import locale
from datetime import datetime, timedelta

import modules.icons as icons


class Calendar(Gtk.Box):
    def __init__(self, view_mode="month"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, name="calendar")
        
        try:
            locale.setlocale(locale.LC_ALL, "")
        except Exception:
            pass

        self.view_mode = view_mode
        self.first_weekday = calendar.firstweekday()
        
        if self.view_mode == "month":
            self.set_halign(Gtk.Align.CENTER)
            self.set_hexpand(False)
        else:
            self.set_halign(Gtk.Align.FILL)
            self.set_hexpand(True)
            self.set_valign(Gtk.Align.CENTER)
            self.set_vexpand(False)

        self.current_day_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.reset_to_current()

        self.prev_button = Gtk.Button(name="prev-month-button", child=Label(name="month-button-label", markup=icons.chevron_left))
        self.next_button = Gtk.Button(name="next-month-button", child=Label(name="month-button-label", markup=icons.chevron_right))
        self.month_label = Gtk.Label(name="month-label")
        
        self.prev_button.connect("clicked", self.on_prev_clicked)
        self.next_button.connect("clicked", self.on_next_clicked)

        self.header = CenterBox(
            spacing=4,
            name="header",
            start_children=[self.prev_button],
            center_children=[self.month_label],
            end_children=[self.next_button],
        )

        self.weekday_row = Gtk.Box(spacing=4, name="weekday-row")
        
        self.add(self.header)
        self.pack_start(self.weekday_row, False, False, 0)

        self.refresh_ui()

    def reset_to_current(self):
        if self.view_mode == "month":
            self.current_shown_date = self.current_day_date.replace(day=1)
        else:
            diff = (self.current_day_date.weekday() - self.first_weekday + 7) % 7
            self.current_shown_date = self.current_day_date - timedelta(days=diff)

    def refresh_ui(self):
        # Clear existing widgets except header and weekday_row
        for child in self.get_children():
            if child not in [self.header, self.weekday_row]:
                self.remove(child)
        
        self.month_label.set_text(self.current_shown_date.strftime("%B %Y").capitalize())

        if not self.weekday_row.get_children():
            day_names = list(calendar.day_name)
            ordered_days = day_names[self.first_weekday:] + day_names[:self.first_weekday]
            for name in ordered_days:
                lbl = Gtk.Label(label=name[:2].upper(), name="weekday-label")
                self.weekday_row.pack_start(lbl, True, True, 0)
            self.weekday_row.show_all()

        view = self.create_month_view() if self.view_mode == "month" else self.create_week_view()
        self.pack_start(view, True, True, 0)
        view.show_all()

    def create_month_view(self):
        grid = Gtk.Grid(column_homogeneous=True, row_homogeneous=False, name="calendar-grid")
        cal = calendar.Calendar(firstweekday=self.first_weekday)
        
        year, month = self.current_shown_date.year, self.current_shown_date.month
        today = self.current_day_date.date()
        
        month_days = cal.monthdayscalendar(year, month)
        while len(month_days) < 6:
            month_days.append([0] * 7)

        for row, week in enumerate(month_days):
            for col, day in enumerate(week):
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, name="day-box")
                if day == 0:
                    lbl = Label(name="day-empty", markup=icons.dot)
                else:
                    lbl = Gtk.Label(label=str(day), name="day-label")
                    if today.year == year and today.month == month and today.day == day:
                        lbl.get_style_context().add_class("current-day")
                
                lbl.set_valign(Gtk.Align.CENTER)
                lbl.set_halign(Gtk.Align.CENTER)
                lbl.set_vexpand(True)
                lbl.set_hexpand(True)
                
                box.pack_start(lbl, True, True, 0)
                grid.attach(box, col, row, 1, 1)
        return grid

    def create_week_view(self):
        grid = Gtk.Grid(column_homogeneous=True, row_homogeneous=False, name="calendar-grid-week-view")
        ref_month = self.current_shown_date.month
        today = self.current_day_date.date()

        for col in range(7):
            day_date = self.current_shown_date + timedelta(days=col)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, name="day-box")
            lbl = Gtk.Label(label=str(day_date.day), name="day-label")
            
            if day_date.date() == today:
                lbl.get_style_context().add_class("current-day")
            if day_date.month != ref_month:
                lbl.get_style_context().add_class("dim-label")

            lbl.set_valign(Gtk.Align.CENTER)
            lbl.set_halign(Gtk.Align.CENTER)
            lbl.set_vexpand(True)
            lbl.set_hexpand(True)

            box.pack_start(lbl, True, True, 0)
            grid.attach(box, col, 0, 1, 1)
        return grid

    def on_prev_clicked(self, _):
        if self.view_mode == "month":
            m, y = self.current_shown_date.month, self.current_shown_date.year
            new_m, new_y = (12, y - 1) if m == 1 else (m - 1, y)
            self.current_shown_date = self.current_shown_date.replace(year=new_y, month=new_m)
        else:
            self.current_shown_date -= timedelta(days=7)
        self.refresh_ui()

    def on_next_clicked(self, _):
        if self.view_mode == "month":
            m, y = self.current_shown_date.month, self.current_shown_date.year
            new_m, new_y = (1, y + 1) if m == 12 else (m + 1, y)
            self.current_shown_date = self.current_shown_date.replace(year=new_y, month=new_m)
        else:
            self.current_shown_date += timedelta(days=7)
        self.refresh_ui()