from fabric.widgets.centerbox import CenterBox
from fabric.widgets.label import Label

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Gio

import calendar
import subprocess
from datetime import datetime, timedelta

import modules.icons as icons


class Calendar(Gtk.Box):
    def __init__(self, view_mode="month"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, name="calendar")
        self.view_mode = view_mode
        self.first_weekday = 0

        self.set_halign(Gtk.Align.CENTER)
        self.set_hexpand(False)

        self.current_day_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        if self.view_mode == "month":
            self.current_shown_date = self.current_day_date.replace(day=1)
            self.current_year = self.current_shown_date.year
            self.current_month = self.current_shown_date.month
            self.current_day = self.current_day_date.day
            self.previous_key = (self.current_year, self.current_month)
        elif self.view_mode == "week":
            days_to_subtract = (self.current_day_date.weekday() - self.first_weekday + 7) % 7
            self.current_shown_date = self.current_day_date - timedelta(days=days_to_subtract)
            self.current_year = self.current_shown_date.year
            self.current_month = self.current_shown_date.month
            iso_year, iso_week, _ = self.current_shown_date.isocalendar()
            self.previous_key = (iso_year, iso_week)
            
            self.set_halign(Gtk.Align.FILL)
            self.set_hexpand(True)
            self.set_valign(Gtk.Align.CENTER)
            self.set_vexpand(False)
        
        self.cache_threshold = 3
        self.month_views = {}

        self.prev_button = Gtk.Button(
            name="prev-month-button", 
            child=Label(name="month-button-label", markup=icons.chevron_left)
        )
        self.prev_button.connect("clicked", self.on_prev_clicked)

        self.month_label = Gtk.Label(name="month-label")

        self.next_button = Gtk.Button(
            name="next-month-button",
            child=Label(name="month-button-label", markup=icons.chevron_right)
        )
        self.next_button.connect("clicked", self.on_next_clicked)

        self.header = CenterBox(
            spacing=4,
            name="header",
            start_children=[self.prev_button],
            center_children=[self.month_label],
            end_children=[self.next_button],
        )

        self.add(self.header)

        self.weekday_row = Gtk.Box(spacing=4, name="weekday-row")
        self.pack_start(self.weekday_row, False, False, 0)

        self.stack = Gtk.Stack(name="calendar-stack")
        self.stack.set_transition_duration(250)
        self.pack_start(self.stack, True, True, 0)

        self.update_header()
        self.update_calendar()
        self._date_check_timer = GLib.timeout_add_seconds(1, self.check_date_change)
        self._dbus_subscription_id = None
        self.setup_dbus_listeners()
        self._init_locale_settings()

    def _init_locale_settings(self):
        def worker(user_data):
            origin_date_str = subprocess.check_output(["locale", "week-1stday"], text=True).strip()
            first_weekday_val = int(subprocess.check_output(["locale", "first_weekday"], text=True).strip())
            
            origin_date = datetime.fromisoformat(origin_date_str)
            date_of_first_day_of_week_config = origin_date + timedelta(days=first_weekday_val - 1)
            new_first_weekday = date_of_first_day_of_week_config.weekday()
            
            GLib.idle_add(self._update_first_weekday, new_first_weekday)
            
        GLib.Thread.new("locale-worker", worker, None)
    
    def _update_first_weekday(self, new_first_weekday):
        if self.first_weekday != new_first_weekday:
            self.first_weekday = new_first_weekday
            
            self.clear_all_views()
            self.update_header()
            self.update_calendar()
        return False

    def clear_all_views(self):
        for widget in self.month_views.items():
            self.stack.remove(widget)
            widget.destroy()
        self.month_views.clear()

    def setup_dbus_listeners(self):
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            self._dbus_subscription_id = bus.signal_subscribe(
                None,
                'org.freedesktop.login1.Manager',
                'PrepareForSleep',
                '/org/freedesktop/login1',
                None,
                Gio.DBusSignalFlags.NONE,
                self.check_date_change,
                None
            )
            self._dbus_bus = bus
        except Exception:
            self._dbus_subscription_id = None
            self._dbus_bus = None

    def check_date_change(self, *args):
        now = datetime.now()
        if now.date() != self.current_day_date.date():
            self.on_midnight()
        return True

    def on_midnight(self):
        now = datetime.now()
        self.current_day_date = now.replace(hour=0, minute=0, second=0, microsecond=0)

        key_to_remove = None
        if self.view_mode == "month":
            self.current_shown_date = self.current_day_date.replace(day=1)
            self.current_year = self.current_shown_date.year
            self.current_month = self.current_shown_date.month
            self.current_day = self.current_day_date.day
            key_to_remove = (self.current_year, self.current_month)
        elif self.view_mode == "week":
            days_to_subtract = (self.current_day_date.weekday() - self.first_weekday + 7) % 7
            self.current_shown_date = self.current_day_date - timedelta(days=days_to_subtract)
            self.current_year = self.current_shown_date.year
            self.current_month = self.current_shown_date.month
            iso_year, iso_week, _ = self.current_shown_date.isocalendar()
            key_to_remove = (iso_year, iso_week)

        if key_to_remove and key_to_remove in self.month_views:
            widget = self.month_views.pop(key_to_remove)
            self.stack.remove(widget)
            widget.destroy()

        self.update_calendar()
        return False

    def update_header(self):
        month_names = {
            1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель", 
            5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
            9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
        }
        
        month_name = month_names.get(self.current_shown_date.month, "Неизвестный месяц")
        header_text = f"{month_name} {self.current_shown_date.year}"
        self.month_label.set_text(header_text)

        if len(self.weekday_row.get_children()) == 0:
            day_initials = self.get_weekday_initials()
            for day_initial in day_initials:
                label = Gtk.Label(label=day_initial.upper(), name="weekday-label")
                self.weekday_row.pack_start(label, True, True, 0)
            self.weekday_row.show_all()
        elif len(self.weekday_row.get_children()) > 0 and self.view_mode == "month": 
            pass 

    def update_calendar(self):
        new_key = None
        child_name = ""
        view_widget = None

        if self.view_mode == "month":
            new_key = (self.current_year, self.current_month)
            child_name = f"{self.current_year}_{self.current_month}"
            if new_key not in self.month_views:
                view_widget = self.create_month_view(self.current_year, self.current_month)
        elif self.view_mode == "week":
            iso_year, iso_week, _ = self.current_shown_date.isocalendar()
            new_key = (iso_year, iso_week)
            child_name = f"{iso_year}_w{iso_week}"
            if new_key not in self.month_views:
                view_widget = self.create_week_view(self.current_shown_date)
        
        if new_key is None: 
            return

        if new_key > self.previous_key:
            self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
        elif new_key < self.previous_key:
            self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_RIGHT)

        self.previous_key = new_key

        if view_widget:
            self.month_views[new_key] = view_widget
            self.stack.add_titled(view_widget, child_name, child_name)
            view_widget.show_all()
        
        self.stack.set_visible_child_name(child_name)
        self.update_header()
        self.prune_cache()

    def prune_cache(self):
        def get_key_index(key_tuple):
            year, num = key_tuple
            if self.view_mode == "month":
                return year * 12 + (num - 1)
            else:
                return year * 53 + num

        current_index = get_key_index(self.previous_key)
        keys_to_remove = []
        
        for key_iter in list(self.month_views.keys()):
            if abs(get_key_index(key_iter) - current_index) > self.cache_threshold:
                keys_to_remove.append(key_iter)
                
        for key_to_remove in keys_to_remove:
            widget = self.month_views.pop(key_to_remove)
            self.stack.remove(widget)
            widget.destroy()

    def create_month_view(self, year, month):
        grid = Gtk.Grid(column_homogeneous=True, row_homogeneous=False, name="calendar-grid")
        cal = calendar.Calendar(firstweekday=self.first_weekday)
        month_days = cal.monthdayscalendar(year, month)

        while len(month_days) < 6:
            month_days.append([0] * 7)

        for row, week in enumerate(month_days):
            for col, day_num in enumerate(week):
                day_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, name="day-box")
                
                label = None
                if day_num == 0:
                    label = Label(name="day-empty", markup=icons.dot)
                else:
                    label = Gtk.Label(label=str(day_num), name="day-label")
                    day_date_obj = datetime(year, month, day_num)
                    if day_date_obj.date() == self.current_day_date.date():
                        label.get_style_context().add_class("current-day")
                
                label.set_valign(Gtk.Align.CENTER)
                label.set_halign(Gtk.Align.CENTER)
                label.set_vexpand(True)
                label.set_hexpand(True)

                day_box.pack_start(label, True, True, 0)
                grid.attach(day_box, col, row, 1, 1)
        
        return grid

    def create_week_view(self, first_day_of_week_to_display):
        grid = Gtk.Grid(column_homogeneous=True, row_homogeneous=False, name="calendar-grid-week-view")
        reference_month = first_day_of_week_to_display.month

        for col in range(7):
            current_day = first_day_of_week_to_display + timedelta(days=col)
            day_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, name="day-box")
            
            label = Gtk.Label(label=str(current_day.day), name="day-label")
            if current_day.date() == self.current_day_date.date():
                label.get_style_context().add_class("current-day")
            
            if current_day.month != reference_month:
                label.get_style_context().add_class("dim-label")

            label.set_valign(Gtk.Align.CENTER)
            label.set_halign(Gtk.Align.CENTER)
            label.set_vexpand(True)
            label.set_hexpand(True)

            day_box.pack_start(label, True, True, 0)
            grid.attach(day_box, col, 0, 1, 1)

        return grid

    def get_weekday_initials(self):
        russian_day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        return [russian_day_names[(self.first_weekday + i) % 7] for i in range(7)]

    def on_prev_clicked(self, widget):
        if self.view_mode == "month":
            # Используем математику дат для перехода
            prev_month = self.current_shown_date.replace(day=1) - timedelta(days=1)
            self.current_shown_date = prev_month.replace(day=1)
            self.current_year = self.current_shown_date.year
            self.current_month = self.current_shown_date.month
        elif self.view_mode == "week":
            self.current_shown_date -= timedelta(days=7)
            self.current_year = self.current_shown_date.year
            self.current_month = self.current_shown_date.month
        
        self.update_calendar()

    def on_next_clicked(self, widget):
        if self.view_mode == "month":
            year = self.current_shown_date.year
            month = self.current_shown_date.month
            if month == 12:
                self.current_shown_date = self.current_shown_date.replace(year=year + 1, month=1)
            else:
                self.current_shown_date = self.current_shown_date.replace(month=month + 1)
            
            self.current_year = self.current_shown_date.year
            self.current_month = self.current_shown_date.month
        elif self.view_mode == "week":
            self.current_shown_date += timedelta(days=7)
            self.current_year = self.current_shown_date.year
            self.current_month = self.current_shown_date.month

        self.update_calendar()
    
    def destroy(self):
        if hasattr(self, '_date_check_timer') and self._date_check_timer:
            GLib.source_remove(self._date_check_timer)
            self._date_check_timer = None
        
        if hasattr(self, '_dbus_subscription_id') and self._dbus_subscription_id and hasattr(self, '_dbus_bus') and self._dbus_bus:
            try:
                self._dbus_bus.signal_unsubscribe(self._dbus_subscription_id)
            except Exception:
                pass
            self._dbus_subscription_id = None
            self._dbus_bus = None
        
        self.clear_all_views()
        super().destroy()