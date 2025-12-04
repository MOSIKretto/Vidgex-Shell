from fabric.utils import DesktopApp, get_desktop_applications
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.entry import Entry
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow

from gi.repository import Gdk, GLib

import subprocess
import threading
import logging

import modules.icons as icons

logger = logging.getLogger(__name__)

TOOLTIP_CLOSE = "<b>Закрыть</b>"
ICON_SIZE = 24
BATCH_SIZE = 15


class AppSearchEngine:
    def __init__(self):
        self._all_apps = []
        self._app_cache = {}
        self._last_update = 0
        self._update_interval = 30
        
    def load_applications(self):
        current_time = GLib.get_monotonic_time() / 1000000
        
        if current_time - self._last_update > self._update_interval:
            try:
                self._all_apps = get_desktop_applications()
                self._app_cache.clear()
                self._last_update = current_time
                logger.info(f"Загружено {len(self._all_apps)} приложений")
            except Exception as e:
                logger.error(f"Ошибка загрузки приложений: {e}")
        
        return self._all_apps
    
    def search(self, query: str):
        if not query:
            return self._load_and_sort_apps()
        
        cache_key = query.lower()
        if cache_key in self._app_cache:
            return self._app_cache[cache_key]
        
        apps = self.load_applications()
        query_lower = query.casefold()
        
        filtered_apps = []
        for app in apps:
            search_fields = [
                app.display_name or "",
                app.name or "",
                app.generic_name or "",
                app.description or "",
                app.command_line or ""
            ]
            
            search_text = " ".join(filter(None, search_fields))
            if query_lower in search_text.casefold():
                filtered_apps.append(app)
        
        filtered_apps.sort(key=lambda app: (app.display_name or "").casefold())
        self._app_cache[cache_key] = filtered_apps
        
        return filtered_apps
    
    def _load_and_sort_apps(self):
        apps = self.load_applications()
        return sorted(apps, key=lambda app: (app.display_name or "").casefold())


class ClipboardManager:
    @staticmethod
    def copy_to_clipboard(text: str) -> bool:
        try:
            subprocess.run(
                ["wl-copy"], 
                input=text.encode('utf-8'), 
                check=True,
                capture_output=True
            )
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Ошибка копирования в буфер обмена: {e}")
            return False
        except FileNotFoundError:
            logger.error("Команда wl-copy не найдена")
            return False


class AppLauncher(Box):
    def __init__(self, **kwargs):
        super().__init__(
            name="app-launcher",
            visible=False,
            all_visible=False,
            **kwargs,
        )

        self.notch = kwargs["notch"]
        self.selected_index = -1
        self._arranger_handler = 0
        
        self.app_search = AppSearchEngine()
        
        self._widget_cache = {}
        
        self._setup_ui()
        self.show_all()

    def _setup_ui(self):
        self.viewport = Box(name="viewport", spacing=4, orientation="v")
        
        self.search_entry = Entry(
            name="search-entry",
            placeholder="Поиск приложений...",
            h_expand=True,
            h_align="fill",
            notify_text=self._on_search_changed,
            on_activate=lambda entry, *_: self._on_search_activate(entry.get_text()),
            on_key_press_event=self._on_search_key_press,
        )
        self.search_entry.props.xalign = 0.5
        
        self.scrolled_window = ScrolledWindow(
            name="scrolled-window",
            spacing=10,
            h_expand=True,
            v_expand=True,
            h_align="fill",
            v_align="fill",
            child=self.viewport,
            propagate_width=False,
            propagate_height=False,
        )

        self.header_box = Box(
            name="header_box",
            spacing=10,
            orientation="h",
            children=[
                self.search_entry,
                Button(
                    name="close-button",
                    tooltip_markup=TOOLTIP_CLOSE,
                    child=Label(name="close-label", markup=icons.cancel),
                    tooltip_text="Выход",
                    on_clicked=lambda *_: self.close_launcher()
                ),
            ],
        )

        self.launcher_box = Box(
            name="launcher-box",
            spacing=10,
            h_expand=True,
            orientation="v",
            children=[
                self.header_box,
                self.scrolled_window,
            ],
        )

        self.add(self.launcher_box)

    def close_launcher(self):
        self.viewport.children = []
        self.selected_index = -1
        self._widget_cache.clear()
        
        if self._arranger_handler:
            from fabric.utils import remove_handler
            remove_handler(self._arranger_handler)
            self._arranger_handler = 0
            
        self.notch.close_notch()

    def open_launcher(self):
        def load_apps():
            self.app_search.load_applications()
            GLib.idle_add(self._arrange_viewport, "")
        
        threading.Thread(target=load_apps, daemon=True).start()
        
        def setup_focus():
            self.search_entry.grab_focus()
            self.search_entry.set_text("")
            self._select_first_item()
            return False
        
        GLib.idle_add(setup_focus)

    def _arrange_viewport(self, query: str = ""):
        if self._arranger_handler:
            from fabric.utils import remove_handler
            remove_handler(self._arranger_handler)
            self._arranger_handler = 0
            
        self.viewport.children = []
        self.selected_index = -1

        def search_apps():
            apps = self.app_search.search(query)
            GLib.idle_add(self._display_apps_batch, apps, 0, BATCH_SIZE)
        
        threading.Thread(target=search_apps, daemon=True).start()

    def _display_apps_batch(self, apps, start: int, batch_size: int):
        end = min(start + batch_size, len(apps))
        
        for i in range(start, end):
            app = apps[i]
            widget = self._create_application_widget(app)
            self.viewport.add(widget)
            self._widget_cache[app.name] = widget

        if end < len(apps):
            GLib.timeout_add(10, self._display_apps_batch, apps, end, batch_size)
        else:
            self._select_first_item()
        
        return False

    def _create_application_widget(self, app: DesktopApp) -> Button:
        display_name = app.display_name or "Неизвестно"
        
        button = Button(
            name="slot-button",
            child=Box(
                name="slot-box",
                orientation="h",
                spacing=10,
                children=[
                    Image(
                        name="app-icon", 
                        pixbuf=app.get_icon_pixbuf(size=ICON_SIZE), 
                        h_align="start"
                    ),
                    Label(
                        name="app-label",
                        label=display_name,
                        ellipsization="end",
                        v_align="center",
                        h_align="center",
                    ),
                ],
            ),
            tooltip_text=app.description or display_name,
            on_clicked=lambda *_: self._launch_app(app),
        )
        
        return button

    def _launch_app(self, app: DesktopApp):
        try:
            app.launch()
            self.close_launcher()
        except Exception as e:
            logger.error(f"Ошибка запуска приложения {app.name}: {e}")

    def _on_search_changed(self, entry, *_):
        query = entry.get_text()
        self._arrange_viewport(query)

    def _on_search_activate(self, text: str):
        children = self.viewport.get_children()
        if not children:
            return
            
        selected_index = self.selected_index if self.selected_index != -1 else 0
        if 0 <= selected_index < len(children):
            children[selected_index].clicked()

    def _on_search_key_press(self, widget, event):
        keyval = event.keyval
        
        if keyval == Gdk.KEY_Escape:
            self.close_launcher()
            return True
        elif keyval == Gdk.KEY_Page_Down:
            self._page_scroll(True)
            return True
        elif keyval == Gdk.KEY_Page_Up:
            self._page_scroll(False)
            return True
        
        if keyval == Gdk.KEY_Down:
            self._move_selection(1)
            return True
        elif keyval == Gdk.KEY_Up:
            self._move_selection(-1)
            return True
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._on_search_activate(widget.get_text())
            return True
        
        return False

    def _update_selection(self, new_index: int):
        children = self.viewport.get_children()
        
        if self.selected_index != -1 and self.selected_index < len(children):
            current_button = children[self.selected_index]
            current_button.get_style_context().remove_class("selected")

        if new_index != -1 and new_index < len(children):
            new_button = children[new_index]
            new_button.get_style_context().add_class("selected")
            self.selected_index = new_index
            self._scroll_to_selected(new_button)
        else:
            self.selected_index = -1

    def _move_selection(self, delta: int):
        children = self.viewport.get_children()
        if not children:
            return

        if self.selected_index == -1:
            new_index = 0 if delta > 0 else len(children) - 1
        else:
            new_index = self.selected_index + delta
            
        new_index = max(0, min(new_index, len(children) - 1))
        self._update_selection(new_index)

    def _scroll_to_selected(self, button):
        def scroll():
            adj = self.scrolled_window.get_vadjustment()
            alloc = button.get_allocation()
            if alloc.height == 0:
                return False

            y = alloc.y
            height = alloc.height
            page_size = adj.get_page_size()
            current_value = adj.get_value()

            visible_top = current_value
            visible_bottom = current_value + page_size

            if y < visible_top:
                adj.set_value(y)
            elif y + height > visible_bottom:
                adj.set_value(y + height - page_size)

            return False
        
        GLib.idle_add(scroll)

    def _page_scroll(self, down: bool = True):
        adj = self.scrolled_window.get_vadjustment()
        page_size = adj.get_page_size()
        current = adj.get_value()
        
        if down:
            new_value = current + page_size
        else:
            new_value = current - page_size
            
        new_value = max(0, min(new_value, adj.get_upper() - page_size))
        adj.set_value(new_value)

    def _select_first_item(self):
        children = self.viewport.get_children()
        if children:
            self._update_selection(0)

    def __del__(self):
        self.close_launcher()