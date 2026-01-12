from fabric.utils import DesktopApp, get_desktop_applications
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.entry import Entry
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow
from gi.repository import Gdk
import modules.icons as icons

class AppLauncher(Box):
    def __init__(self, notch, **kwargs):
        super().__init__(name="app-launcher", visible=False, all_visible=False, **kwargs)
        
        self.notch = notch
        self.selected_index = -1
        self._apps_cache = [] # Загружается один раз при запуске системы/окна
        
        self._setup_ui()

    def _setup_ui(self):
        # Компактная инициализация без лишних переменных в self
        self.viewport = Box(name="viewport", spacing=4, orientation="v")
        self.search_entry = Entry(
            name="search-entry",
            placeholder="Поиск...",
            h_expand=True,
            notify_text=self._on_search_changed,
            on_activate=self._on_search_activate,
            on_key_press_event=self._on_key_press
        )
        self.search_entry.props.xalign = 0.5
        
        self.scrolled_window = ScrolledWindow(
            name="scrolled-window",
            v_expand=True,
            child=self.viewport,
            propagate_width=False,
            propagate_height=False
        )

        # Компонуем интерфейс напрямую в один проход
        self.add(Box(
            name="launcher-box",
            spacing=10,
            h_expand=True,
            orientation="v",
            children=[
                Box(spacing=10, children=[
                    self.search_entry, 
                    Button(
                        name="close-button",
                        child=Label(name="close-label", markup=icons.cancel),
                        on_clicked=lambda *_: self.close_launcher()
                    )
                ]),
                self.scrolled_window
            ]
        ))

    def open_launcher(self):
        # Ленивая загрузка приложений только если кэш пуст
        if not self._apps_cache:
            self._apps_cache = sorted(
                get_desktop_applications(), 
                key=lambda a: (a.display_name or "").casefold()
            )
        
        self.search_entry.set_text("")
        self.search_entry.grab_focus()
        self._render_list("")

    def _on_search_changed(self, entry, *_):
        # Мгновенный поиск без таймеров
        self._render_list(entry.get_text())

    def _render_list(self, query: str):
        query = query.casefold()
        existing = self.viewport.get_children()
        idx = 0

        # Линейный проход по приложениям с фильтрацией "на лету"
        for app in self._apps_cache:
            # Логика фильтрации (функциональность сохранена)
            match = not query or any(
                query in (attr or "").casefold() 
                for attr in [app.display_name, app.name, app.command_line, app.description]
            )
            
            if match:
                if idx < len(existing):
                    widget = existing[idx]
                    self._update_widget(widget, app)
                else:
                    widget = self._create_widget(app)
                    self.viewport.add(widget)
                
                widget.show()
                idx += 1

        # Скрываем неиспользуемые виджеты вместо их удаления (экономия CPU)
        for i in range(idx, len(existing)):
            existing[i].hide()
            
        self._update_selection(0 if idx > 0 else -1)

    def _update_widget(self, btn: Button, app: DesktopApp):
        # Прямое обновление без создания новых объектов Label/Image
        box_children = btn.get_child().get_children()
        name = app.display_name or "App"
        
        box_children[0].set_from_pixbuf(app.get_icon_pixbuf(size=btn.get_allocated_height() or 24))
        box_children[1].set_label(name)
        btn.set_tooltip_text(app.description or name)
        btn._app = app
        btn.get_style_context().remove_class("selected")

    def _create_widget(self, app: DesktopApp) -> Button:
        name = app.display_name or "App"
        btn = Button(
            name="slot-button",
            child=Box(spacing=10, children=[
                Image(name="app-icon", pixbuf=app.get_icon_pixbuf(size=24)),
                Label(name="app-label", label=name, ellipsization="end")
            ]),
            on_clicked=self._on_app_clicked
        )
        btn._app = app
        return btn

    def _on_app_clicked(self, btn):
        btn._app.launch()
        self.close_launcher()

    def _on_search_activate(self, *_):
        visible = [c for c in self.viewport.get_children() if c.get_visible()]
        if visible and 0 <= self.selected_index < len(visible):
            self._on_app_clicked(visible[self.selected_index])

    def _on_key_press(self, _, event) -> bool:
        kv = event.keyval
        # Прямое сравнение без словарей (KEY_MAPPINGS удален для экономии ОЗУ)
        if kv == Gdk.KEY_Escape: self.close_launcher()
        elif kv in (Gdk.KEY_Up, Gdk.KEY_Down):
            step = 1 if kv == Gdk.KEY_Down else -1
            visible = [c for c in self.viewport.get_children() if c.get_visible()]
            if visible:
                self._update_selection((self.selected_index + step) % len(visible))
        elif kv in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._on_search_activate()
        else: return False
        return True

    def _update_selection(self, index: int):
        visible = [c for c in self.viewport.get_children() if c.get_visible()]
        if not visible:
            self.selected_index = -1
            return

        if 0 <= self.selected_index < len(visible):
            visible[self.selected_index].get_style_context().remove_class("selected")
        
        self.selected_index = index
        target = visible[index]
        target.get_style_context().add_class("selected")
        
        # Скроллинг без лишних вычислений
        adj = self.scrolled_window.get_vadjustment()
        alloc = target.get_allocation()
        if alloc.y < adj.get_value(): adj.set_value(alloc.y)
        elif alloc.y + alloc.height > adj.get_value() + adj.get_page_size():
            adj.set_value(alloc.y + alloc.height - adj.get_page_size())

    def close_launcher(self):
        self.notch.close_notch()
        self.selected_index = -1

    def destroy(self):
        self._apps_cache.clear()
        super().destroy()