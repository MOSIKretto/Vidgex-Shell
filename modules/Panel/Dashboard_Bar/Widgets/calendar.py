from fabric.widgets.centerbox import CenterBox
from fabric.widgets.label import Label

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Gio

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
        
        self.stack = Gtk.Stack(name="calendar-stack")
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)

        self.add(self.header)
        self.pack_start(self.weekday_row, False, False, 0)
        self.pack_start(self.stack, True, True, 0)

        self.setup_dbus_listeners()
        self.refresh_ui()

    def reset_to_current(self):
        if self.view_mode == "month":
            self.current_shown_date = self.current_day_date.replace(day=1)
        else:
            diff = (self.current_day_date.weekday() - self.first_weekday + 7) % 7
            self.current_shown_date = self.current_day_date - timedelta(days=diff)

    def setup_dbus_listeners(self):
        try:
            self._dbus_bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            self._dbus_id = self._dbus_bus.signal_subscribe(
                'org.freedesktop.login1',
                'org.freedesktop.login1.Manager',
                'PrepareForSleep',
                '/org/freedesktop/login1',
                None,
                Gio.DBusSignalFlags.NONE,
                self.on_system_wakeup,
                None
            )
        except:
            self._dbus_id = None

    def on_system_wakeup(self, *args):
        new_now = datetime.now().date()
        if new_now != self.current_day_date.date():
            self.current_day_date = datetime.combine(new_now, datetime.min.time())
            self.reset_to_current()
            GLib.idle_add(self.refresh_ui)

    def refresh_ui(self):
        for child in self.stack.get_children():
            self.stack.remove(child)
            child.destroy()

        self.month_label.set_text(self.current_shown_date.strftime("%B %Y").capitalize())

        if not self.weekday_row.get_children():
            day_names = list(calendar.day_name)
            ordered_days = day_names[self.first_weekday:] + day_names[:self.first_weekday]
            for name in ordered_days:
                lbl = Gtk.Label(label=name[:2].upper(), name="weekday-label")
                self.weekday_row.pack_start(lbl, True, True, 0)
            self.weekday_row.show_all()

        view = self.create_month_view() if self.view_mode == "month" else self.create_week_view()
        
        child_id = str(self.current_shown_date.timestamp())
        self.stack.add_named(view, child_id)
        view.show_all()
        self.stack.set_visible_child_name(child_id)

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
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_RIGHT)
        if self.view_mode == "month":
            m, y = self.current_shown_date.month, self.current_shown_date.year
            new_m, new_y = (12, y - 1) if m == 1 else (m - 1, y)
            self.current_shown_date = self.current_shown_date.replace(year=new_y, month=new_m)
        else:
            self.current_shown_date -= timedelta(days=7)
        self.refresh_ui()

    def on_next_clicked(self, _):
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
        if self.view_mode == "month":
            m, y = self.current_shown_date.month, self.current_shown_date.year
            new_m, new_y = (1, y + 1) if m == 12 else (m + 1, y)
            self.current_shown_date = self.current_shown_date.replace(year=new_y, month=new_m)
        else:
            self.current_shown_date += timedelta(days=7)
        self.refresh_ui()

    def destroy(self):
        if hasattr(self, '_dbus_id') and self._dbus_id:
            try: self._dbus_bus.signal_unsubscribe(self._dbus_id)
            except: pass
        super().destroy()