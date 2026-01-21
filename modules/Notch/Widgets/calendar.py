from fabric.widgets.centerbox import CenterBox
from fabric.widgets.label import Label

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

import modules.icons as icons


class Calendar(Gtk.Box):
    """Календарь без использования модулей calendar, locale, datetime."""
    
    # Названия месяцев
    MONTH_NAMES = [
        "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
        "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
    ]
    
    # Названия дней недели (начиная с понедельника)
    DAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    
    def __init__(self, view_mode="month", first_weekday=0):
        """
        Args:
            view_mode: "month" или "week"
            first_weekday: 0 = понедельник, 6 = воскресенье
        """
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, name="calendar")
        
        self.view_mode = view_mode
        self.first_weekday = first_weekday
        
        if self.view_mode == "month":
            self.set_halign(Gtk.Align.CENTER)
            self.set_hexpand(False)
        else:
            self.set_halign(Gtk.Align.FILL)
            self.set_hexpand(True)
            self.set_valign(Gtk.Align.CENTER)
            self.set_vexpand(False)

        # Получаем текущую дату через GLib
        self.current_day_date = self._get_current_date()
        self.reset_to_current()

        self.prev_button = Gtk.Button(
            name="prev-month-button",
            child=Label(name="month-button-label", markup=icons.chevron_left)
        )
        self.next_button = Gtk.Button(
            name="next-month-button",
            child=Label(name="month-button-label", markup=icons.chevron_right)
        )
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

    # ==================== Вспомогательные методы для работы с датами ====================
    
    def _get_current_date(self) -> tuple:
        """Получает текущую дату через GLib. Возвращает (year, month, day)."""
        dt = GLib.DateTime.new_now_local()
        return (dt.get_year(), dt.get_month(), dt.get_day_of_month())
    
    def _is_leap_year(self, year: int) -> bool:
        """Проверяет, является ли год високосным."""
        return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)
    
    def _days_in_month(self, year: int, month: int) -> int:
        """Возвращает количество дней в месяце."""
        if month in (1, 3, 5, 7, 8, 10, 12):
            return 31
        elif month in (4, 6, 9, 11):
            return 30
        else:  # Февраль
            return 29 if self._is_leap_year(year) else 28
    
    def _day_of_week(self, year: int, month: int, day: int) -> int:
        """
        Вычисляет день недели по алгоритму Зеллера.
        Возвращает 0 = понедельник, 6 = воскресенье.
        """
        if month < 3:
            month += 12
            year -= 1
        
        q = day
        m = month
        k = year % 100
        j = year // 100
        
        h = (q + (13 * (m + 1)) // 5 + k + k // 4 + j // 4 - 2 * j) % 7
        
        # Преобразуем: h=0 (суббота) -> 5, h=1 (воскресенье) -> 6, h=2 (понедельник) -> 0, и т.д.
        return (h + 5) % 7
    
    def _add_days(self, date: tuple, days: int) -> tuple:
        """Добавляет (или вычитает) дни к дате."""
        year, month, day = date
        
        day += days
        
        while day > self._days_in_month(year, month):
            day -= self._days_in_month(year, month)
            month += 1
            if month > 12:
                month = 1
                year += 1
        
        while day < 1:
            month -= 1
            if month < 1:
                month = 12
                year -= 1
            day += self._days_in_month(year, month)
        
        return (year, month, day)
    
    def _prev_month(self, date: tuple) -> tuple:
        """Возвращает первый день предыдущего месяца."""
        year, month, _ = date
        if month == 1:
            return (year - 1, 12, 1)
        return (year, month - 1, 1)
    
    def _next_month(self, date: tuple) -> tuple:
        """Возвращает первый день следующего месяца."""
        year, month, _ = date
        if month == 12:
            return (year + 1, 1, 1)
        return (year, month + 1, 1)
    
    def _dates_equal(self, d1: tuple, d2: tuple) -> bool:
        """Сравнивает две даты."""
        return d1[0] == d2[0] and d1[1] == d2[1] and d1[2] == d2[2]
    
    def _get_month_calendar(self, year: int, month: int) -> list:
        """
        Генерирует календарную сетку месяца.
        Возвращает список недель, каждая неделя - список дней (0 = пустой день).
        """
        first_day_weekday = self._day_of_week(year, month, 1)
        
        # Смещение относительно первого дня недели
        offset = (first_day_weekday - self.first_weekday + 7) % 7
        
        days_in_month = self._days_in_month(year, month)
        
        weeks = []
        current_week = [0] * offset  # Пустые дни до начала месяца
        
        for day in range(1, days_in_month + 1):
            current_week.append(day)
            if len(current_week) == 7:
                weeks.append(current_week)
                current_week = []
        
        # Добавляем оставшиеся дни и заполняем пустыми
        if current_week:
            current_week.extend([0] * (7 - len(current_week)))
            weeks.append(current_week)
        
        return weeks

    # ==================== Основная логика ====================

    def reset_to_current(self):
        """Сбрасывает отображение к текущей дате."""
        if self.view_mode == "month":
            year, month, _ = self.current_day_date
            self.current_shown_date = (year, month, 1)
        else:
            # Находим начало недели
            year, month, day = self.current_day_date
            weekday = self._day_of_week(year, month, day)
            diff = (weekday - self.first_weekday + 7) % 7
            self.current_shown_date = self._add_days(self.current_day_date, -diff)

    def refresh_ui(self):
        """Обновляет интерфейс календаря."""
        # Удаляем старые элементы (кроме header и weekday_row)
        for child in self.get_children():
            if child not in [self.header, self.weekday_row]:
                self.remove(child)
        
        # Обновляем заголовок
        year, month, _ = self.current_shown_date
        self.month_label.set_text(f"{self.MONTH_NAMES[month - 1]} {year}")

        # Заполняем дни недели (один раз)
        if not self.weekday_row.get_children():
            ordered_days = (
                self.DAY_NAMES[self.first_weekday:] + 
                self.DAY_NAMES[:self.first_weekday]
            )
            for name in ordered_days:
                lbl = Gtk.Label(label=name, name="weekday-label")
                self.weekday_row.pack_start(lbl, True, True, 0)
            self.weekday_row.show_all()

        # Создаём вид
        if self.view_mode == "month":
            view = self.create_month_view()
        else:
            view = self.create_week_view()
        
        self.pack_start(view, True, True, 0)
        view.show_all()

    def create_month_view(self) -> Gtk.Grid:
        """Создаёт месячный вид календаря."""
        grid = Gtk.Grid(
            column_homogeneous=True,
            row_homogeneous=False,
            name="calendar-grid"
        )
        
        year, month, _ = self.current_shown_date
        month_days = self._get_month_calendar(year, month)
        
        # Дополняем до 6 недель
        while len(month_days) < 6:
            month_days.append([0] * 7)

        for row, week in enumerate(month_days):
            for col, day in enumerate(week):
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, name="day-box")
                
                if day == 0:
                    lbl = Label(name="day-empty", markup=icons.dot)
                else:
                    lbl = Gtk.Label(label=str(day), name="day-label")
                    # Проверяем, является ли это текущим днём
                    if self._dates_equal(
                        (year, month, day),
                        self.current_day_date
                    ):
                        lbl.get_style_context().add_class("current-day")
                
                lbl.set_valign(Gtk.Align.CENTER)
                lbl.set_halign(Gtk.Align.CENTER)
                lbl.set_vexpand(True)
                lbl.set_hexpand(True)
                
                box.pack_start(lbl, True, True, 0)
                grid.attach(box, col, row, 1, 1)
        
        return grid

    def create_week_view(self) -> Gtk.Grid:
        """Создаёт недельный вид календаря."""
        grid = Gtk.Grid(
            column_homogeneous=True,
            row_homogeneous=False,
            name="calendar-grid-week-view"
        )
        
        ref_month = self.current_shown_date[1]

        for col in range(7):
            day_date = self._add_days(self.current_shown_date, col)
            year, month, day = day_date
            
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, name="day-box")
            lbl = Gtk.Label(label=str(day), name="day-label")
            
            # Текущий день
            if self._dates_equal(day_date, self.current_day_date):
                lbl.get_style_context().add_class("current-day")
            
            # День из другого месяца
            if month != ref_month:
                lbl.get_style_context().add_class("dim-label")

            lbl.set_valign(Gtk.Align.CENTER)
            lbl.set_halign(Gtk.Align.CENTER)
            lbl.set_vexpand(True)
            lbl.set_hexpand(True)

            box.pack_start(lbl, True, True, 0)
            grid.attach(box, col, 0, 1, 1)
        
        return grid

    def on_prev_clicked(self, _):
        """Переход к предыдущему периоду."""
        if self.view_mode == "month":
            self.current_shown_date = self._prev_month(self.current_shown_date)
        else:
            self.current_shown_date = self._add_days(self.current_shown_date, -7)
        self.refresh_ui()

    def on_next_clicked(self, _):
        """Переход к следующему периоду."""
        if self.view_mode == "month":
            self.current_shown_date = self._next_month(self.current_shown_date)
        else:
            self.current_shown_date = self._add_days(self.current_shown_date, 7)
        self.refresh_ui()