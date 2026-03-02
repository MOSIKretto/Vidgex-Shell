import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Gio, Pango

from pathlib import Path
from typing import List, Optional, Tuple


class AppChooserMixin:
    _system_apps_cache = None

    @classmethod
    def _get_system_apps(cls) -> List[Gio.AppInfo]:
        if cls._system_apps_cache is None:
            cls._system_apps_cache = [
                app for app in Gio.AppInfo.get_all()
                if app.should_show() and (app.supports_files() or app.supports_uris())
            ]
        return cls._system_apps_cache

    def _algo_categorize_apps(self, content_type: str) -> Tuple[List[Gio.AppInfo], List[Gio.AppInfo], List[Gio.AppInfo]]:
        recommended = Gio.AppInfo.get_recommended_for_type(content_type) or []
        rec_ids = {a.get_id() for a in recommended if a.get_id()}

        other_dict = {}
        all_other = (Gio.AppInfo.get_all_for_type(content_type) or []) + \
                    (Gio.AppInfo.get_fallback_for_type(content_type) or [])
        
        for app in all_other:
            if (aid := app.get_id()) and aid not in rec_ids:
                other_dict[aid] = app
                
        other = list(other_dict.values())
        seen = rec_ids | other_dict.keys()

        remaining = [
            app for app in self._get_system_apps() 
            if (aid := app.get_id()) and aid not in seen
        ]
            
        remaining.sort(key=lambda a: (a.get_display_name() or "").lower())
        return recommended, other, remaining

    def _show_app_chooser(self, path: Path):
        self._close_app_chooser()
        self._app_chooser_path = path
        self._app_chooser_active = True
        self._menu_open = True
        self._cancel_pending_hide()
        self._set_keyboard_interactive(True)
        self._current_app_query = ""

        # АЛГОРИТМ: Динамически расширяем родительское окно, чтобы меню физически стало шире
        if hasattr(self, 'explorer_box'):
            self.explorer_box.set_size_request(850, -1)

        content_type, _ = Gio.content_type_guess(str(path), None)
        self._app_chooser_content_type = content_type or "application/octet-stream"

        rec, other, rem = self._algo_categorize_apps(self._app_chooser_content_type)
        
        self._build_chooser_ui(path, rec, other, rem)
        
        self.file_view_stack.set_visible_child_name("app_chooser")
        self.status_label.set_label(f"Choose application for: {path.name}")
        self._focus_app_search()

    def _close_app_chooser(self):
        if not getattr(self, '_app_chooser_active', False):
            return
            
        self._app_chooser_active = False
        self._app_chooser_path = None
        self._app_chooser_content_type = None
        self._menu_open = False
        self._current_app_query = ""
        self._set_keyboard_interactive(False)

        # АЛГОРИТМ: Возвращаем главному окну его изначальную ширину
        if hasattr(self, 'explorer_box') and hasattr(self, '_explorer_width'):
            self.explorer_box.set_size_request(self._explorer_width, -1)
        
        for child in self.app_chooser_box.get_children():
            child.destroy()
            
        self._app_search_entry = None
        self._app_listbox = None
            
        self.file_view_stack.set_visible_child_name("files")
        self.status_label.set_label("Selection cancelled")

    def _build_chooser_ui(self, path: Path, recommended: list, other: list, remaining: list):
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_name("explorer-app-chooser-header")
        header.set_margin_start(4)
        header.set_margin_end(4)
        header.set_margin_top(4)

        back_btn = Gtk.Button()
        back_btn.set_name("explorer-app-back")
        back_btn.add(Gtk.Image.new_from_icon_name("go-previous-symbolic", Gtk.IconSize.BUTTON))
        back_btn.connect("clicked", lambda _: self._close_app_chooser())

        title = Gtk.Label(label=f'Open "{path.name}" with...')
        title.set_name("explorer-app-chooser-title")
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_hexpand(True)
        title.set_halign(Gtk.Align.START)

        header.pack_start(back_btn, False, False, 0)
        header.pack_start(title, True, True, 0)
        self.app_chooser_box.pack_start(header, False, False, 0)

        self._app_search_entry = Gtk.SearchEntry()
        self._app_search_entry.set_name("explorer-app-search")
        self._app_search_entry.set_hexpand(True)
        self._app_search_entry.set_margin_start(4)
        self._app_search_entry.set_margin_end(4)
        self._app_search_entry.set_placeholder_text("Search applications...")

        self._app_search_entry.connect("button-press-event", self._on_app_search_clicked)
        self._app_search_entry.connect("search-changed", self._on_app_search_changed)
        self._app_search_entry.connect("key-press-event", self._on_app_search_key_press)
        
        self.app_chooser_box.pack_start(self._app_search_entry, False, False, 0)

        self._app_listbox = Gtk.ListBox()
        self._app_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self._app_listbox.set_name("explorer-app-list")
        self._app_listbox.set_filter_func(self._listbox_filter_func)
        self._app_listbox.set_header_func(self._listbox_header_func)
        self._app_listbox.connect("row-activated", self._on_app_row_activated)
        self._app_listbox.set_placeholder(self._build_no_apps_placeholder())

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)
        scroll.set_overlay_scrolling(False) 
        
        scroll.add(self._app_listbox)
        
        self.app_chooser_box.pack_start(scroll, True, True, 0)
        
        self._populate_listbox(recommended, other, remaining)
        self.app_chooser_box.show_all()

    def _on_app_search_clicked(self, widget, event):
        if event.button == 1:
            self._set_keyboard_interactive(True)
            self._cancel_pending_hide()
            GLib.idle_add(lambda: (self.present(), self.set_focus(self._app_search_entry),
                                   self._app_search_entry.grab_focus()) or False)
        return False

    def _build_no_apps_placeholder(self) -> Gtk.Box:
        icon = Gtk.Image(name="explorer-empty-icon")
        icon.set_from_icon_name("edit-find-symbolic", Gtk.IconSize.DIALOG)
        icon.set_pixel_size(96)
        
        label = Gtk.Label(label="No matching applications found")
        label.set_name("explorer-empty-label")
        
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        inner.set_halign(Gtk.Align.CENTER)
        inner.set_valign(Gtk.Align.CENTER)
        inner.pack_start(icon, False, False, 0)
        inner.pack_start(label, False, False, 0)

        wrapper = Gtk.Box(name="explorer-empty-state", orientation=Gtk.Orientation.VERTICAL)
        wrapper.set_hexpand(True)
        wrapper.set_vexpand(True)
        wrapper.pack_start(Gtk.Box(vexpand=True), True, True, 0)
        wrapper.pack_start(inner, False, False, 0)
        wrapper.pack_start(Gtk.Box(vexpand=True), True, True, 0)
        wrapper.show_all()
        return wrapper

    def _on_app_search_changed(self, entry: Gtk.SearchEntry):
        self._current_app_query = entry.get_text().lower()
        self._app_listbox.invalidate_filter()

    def _populate_listbox(self, recommended, other, remaining):
        default_id = self._get_default_app_id()
        categories = (
            ("Recommended", recommended),
            ("Other", other),
            ("All Applications", remaining),
        )

        for cat_name, app_list in categories:
            for app in app_list:
                row = self._create_app_row(app, app.get_id() == default_id)
                row.app_data = app
                row.category_name = cat_name
                self._app_listbox.add(row)

    def _listbox_header_func(self, row: Gtk.ListBoxRow, before: Optional[Gtk.ListBoxRow]):
        if before is None or row.category_name != before.category_name:
            header = Gtk.Label(label=row.category_name)
            header.set_name("explorer-sidebar-header")
            header.set_halign(Gtk.Align.START)
            header.set_margin_top(6)
            header.set_margin_bottom(2)
            header.show()
            row.set_header(header)
        else:
            row.set_header(None)

    def _listbox_filter_func(self, row: Gtk.ListBoxRow) -> bool:
        return not self._current_app_query or self._current_app_query in row.search_key

    def _focus_app_search(self):
        def do_focus():
            if self._app_chooser_active and getattr(self, '_app_search_entry', None):
                self._app_search_entry.grab_focus()
            return False
        GLib.idle_add(do_focus)

    def _get_default_app_id(self) -> Optional[str]:
        if getattr(self, '_app_chooser_content_type', None):
            if da := Gio.AppInfo.get_default_for_type(self._app_chooser_content_type, False):
                return da.get_id()
        return None

    def _create_app_row(self, app: Gio.AppInfo, is_default: bool = False) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        
        search_terms = filter(None, [
            app.get_display_name(),
            app.get_description(),
            app.get_executable()
        ])
        row.search_key = " ".join(search_terms).lower()
        
        icon_widget = Gtk.Image()
        icon_widget.set_name("explorer-file-icon")
        icon_widget.set_pixel_size(24)
        
        if app_icon := app.get_icon():
            icon_widget.set_from_gicon(app_icon, Gtk.IconSize.LARGE_TOOLBAR)
        else:
            icon_widget.set_from_icon_name("application-x-executable-symbolic", Gtk.IconSize.LARGE_TOOLBAR)

        name_label = Gtk.Label(label=app.get_display_name() or "Unknown")
        name_label.set_name("explorer-file-name")
        name_label.set_halign(Gtk.Align.START)
        name_label.set_hexpand(True)
        name_label.set_ellipsize(Pango.EllipsizeMode.END)

        info_text = "default" if is_default else (app.get_description() or "")
        info_label = Gtk.Label(label=info_text)
        info_label.set_name("explorer-file-date")
        info_label.set_halign(Gtk.Align.END)
        info_label.set_ellipsize(Pango.EllipsizeMode.END)
        info_label.set_max_width_chars(100) 

        if is_default:
            info_label.get_style_context().add_class("default-app-label")

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        content.pack_start(icon_widget, False, False, 0)
        content.pack_start(name_label, True, True, 0)
        content.pack_start(info_label, False, False, 0)
        
        row.add(content)
        if is_default:
            row.get_style_context().add_class("default-app")

        return row

    def _on_app_row_activated(self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow):
        self._open_with_app(row.app_data, self._app_chooser_path)
        self._close_app_chooser()

    def _on_app_search_key_press(self, entry: Gtk.SearchEntry, event) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self._close_app_chooser()
            return True
        return False