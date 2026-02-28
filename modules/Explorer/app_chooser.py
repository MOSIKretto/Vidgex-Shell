import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Gio

from pathlib import Path
from typing import List, Optional, Tuple


class AppChooserMixin:

    @staticmethod
    def _algo_app_matches(app: Gio.AppInfo, query: str) -> bool:
        if not query:
            return True
            
        query = query.lower()
        name = (app.get_display_name() or "").lower()
        if query in name:
            return True
            
        desc = (app.get_description() or "").lower()
        if query in desc:
            return True
            
        exe = (app.get_executable() or "").lower()
        return query in exe

    @staticmethod
    def _algo_categorize_apps(content_type: str) -> Tuple[List[Gio.AppInfo], List[Gio.AppInfo], List[Gio.AppInfo]]:
        try:
            recommended = Gio.AppInfo.get_recommended_for_type(content_type) or []
        except Exception:
            recommended = []
            
        rec_ids = {a.get_id() for a in recommended if a.get_id()}

        try:
            all_for = Gio.AppInfo.get_all_for_type(content_type) or []
            fallback = Gio.AppInfo.get_fallback_for_type(content_type) or []
        except Exception:
            all_for, fallback = [], []

        other = []
        other_ids = set()
        for app in all_for + fallback:
            aid = app.get_id()
            if aid and aid not in rec_ids and aid not in other_ids:
                other.append(app)
                other_ids.add(aid)

        seen = rec_ids | other_ids
        remaining = []
        try:
            for app in Gio.AppInfo.get_all():
                if app.should_show() and (app.supports_files() or app.supports_uris()):
                    aid = app.get_id()
                    if aid and aid not in seen:
                        remaining.append(app)
                        seen.add(aid)
        except Exception:
            pass
            
        remaining.sort(key=lambda a: (a.get_display_name() or "").lower())
        return recommended, other, remaining

    @staticmethod
    def _algo_truncate(text: str, limit: int = 40) -> str:
        return text[:limit - 3] + "..." if len(text) > limit else text

    def _show_app_chooser(self, path: Path):
        self._close_app_chooser()
        self._app_chooser_path = path
        self._app_chooser_active = True
        self._menu_open = True
        self._cancel_pending_hide()
        self._set_keyboard_interactive(True)

        try:
            content_type, _ = Gio.content_type_guess(str(path), None)
            content_type = content_type or "application/octet-stream"
            self._app_chooser_content_type = content_type

            rec, other, remaining = self._algo_categorize_apps(content_type)
            self._app_chooser_recommended = rec
            self._app_chooser_other = other
            self._app_chooser_remaining = remaining

            self._build_chooser_ui(path)
            self.file_view_stack.set_visible_child_name("app_chooser")
            self.status_label.set_label(f"Choose application for: {path.name}")
            self._focus_app_search()

        except Exception as e:
            print(f"App chooser error: {e}")
            self._close_app_chooser()

    def _close_app_chooser(self):
        if not self._app_chooser_active:
            return
            
        self._app_chooser_active = False
        self._app_chooser_path = None
        self._app_chooser_content_type = None
        
        self._app_chooser_recommended.clear()
        self._app_chooser_other.clear()
        self._app_chooser_remaining.clear()
        
        self._app_search_entry = None
        self._menu_open = False
        self._set_keyboard_interactive(False)
        
        self.file_view_stack.set_visible_child_name("files")
        self.status_label.set_label("Selection cancelled")

    def _build_chooser_ui(self, path: Path):
        for child in self.app_chooser_box.get_children():
            child.destroy()

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
        title.set_ellipsize(3)
        title.set_hexpand(True)
        title.set_halign(Gtk.Align.START)

        header.pack_start(back_btn, False, False, 0)
        header.pack_start(title, True, True, 0)
        self.app_chooser_box.pack_start(header, False, False, 0)

        self._app_search_entry = Gtk.SearchEntry()
        self._app_search_entry.set_name("explorer-app-search")
        self._app_search_entry.set_placeholder_text("Search applications...")
        self._app_search_entry.set_margin_start(4)
        self._app_search_entry.set_margin_end(4)
        self._app_search_entry.connect("search-changed", self._on_app_search_changed)
        self._app_search_entry.connect("key-press-event", self._on_app_search_key_press)
        
        self.app_chooser_box.pack_start(self._app_search_entry, False, False, 0)

        self._app_list_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._app_list_container.set_name("explorer-app-list")

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.add(self._app_list_container)
        
        self.app_chooser_box.pack_start(scroll, True, True, 0)

        self._populate_app_list("")
        self.app_chooser_box.show_all()

    def _focus_app_search(self):
        def do_focus():
            try:
                self.present()
                if self._app_search_entry:
                    self.set_focus(self._app_search_entry)
                    self._app_search_entry.grab_focus()
            except Exception:
                pass
            return False

        GLib.idle_add(do_focus)

    def _populate_app_list(self, search_text: str):
        if not self._app_list_container:
            return

        for child in self._app_list_container.get_children():
            child.destroy()

        query = search_text.lower().strip()
        path = self._app_chooser_path
        default_id = self._get_default_app_id()
        has_any = False

        categories = (
            ("Recommended", self._app_chooser_recommended),
            ("Other", self._app_chooser_other),
            ("All Applications", self._app_chooser_remaining),
        )

        for label_text, app_list in categories:
            filtered = [a for a in app_list if self._algo_app_matches(a, query)]
            if not filtered:
                continue
                
            has_any = True

            header = Gtk.Label(label=label_text)
            header.set_name("explorer-sidebar-header")
            header.set_halign(Gtk.Align.START)
            self._app_list_container.pack_start(header, False, False, 0)

            for app in filtered:
                row = self._create_app_row(app, path, app.get_id() == default_id)
                self._app_list_container.pack_start(row, False, False, 0)

        if not has_any:
            self._app_list_container.pack_start(self._build_no_apps_placeholder(), False, False, 0)

        self._app_list_container.show_all()

    def _get_default_app_id(self) -> Optional[str]:
        try:
            if self._app_chooser_content_type:
                da = Gio.AppInfo.get_default_for_type(self._app_chooser_content_type, False)
                if da:
                    return da.get_id()
        except Exception:
            pass
        return None

    def _build_no_apps_placeholder(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_valign(Gtk.Align.CENTER)
        box.set_margin_top(40)

        icon = Gtk.Image.new_from_icon_name("edit-find-symbolic", Gtk.IconSize.DIALOG)
        icon.get_style_context().add_class("dim-label")

        label = Gtk.Label(label="No matching applications found")
        label.set_name("explorer-empty-label")

        box.pack_start(icon, False, False, 0)
        box.pack_start(label, False, False, 0)
        return box

    def _create_app_row(self, app: Gio.AppInfo, path: Path, is_default: bool = False) -> Gtk.Button:
        icon_widget = Gtk.Image()
        icon_widget.set_pixel_size(24)
        icon_widget.set_name("explorer-file-icon")

        try:
            app_icon = app.get_icon()
            if app_icon:
                icon_widget.set_from_gicon(app_icon, Gtk.IconSize.LARGE_TOOLBAR)
            else:
                raise ValueError
        except Exception:
            icon_widget.set_from_icon_name("application-x-executable-symbolic", Gtk.IconSize.LARGE_TOOLBAR)

        name_label = Gtk.Label(label=app.get_display_name() or "Unknown")
        name_label.set_name("explorer-file-name")
        name_label.set_halign(Gtk.Align.START)
        name_label.set_hexpand(True)
        name_label.set_ellipsize(3)

        info_text = "default" if is_default else self._algo_truncate(app.get_description() or "")

        info_label = Gtk.Label(label=info_text)
        info_label.set_name("explorer-file-date")
        info_label.set_halign(Gtk.Align.END)
        info_label.set_ellipsize(3)
        
        if is_default:
            info_label.get_style_context().add_class("default-app-label")

        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        content.pack_start(icon_widget, False, False, 0)
        content.pack_start(name_label, True, True, 0)
        content.pack_start(info_label, False, False, 0)

        btn = Gtk.Button()
        btn.set_name("explorer-file-row")
        btn.add(content)
        btn._app_info = app
        btn._file_path = path
        btn.connect("clicked", self._on_app_row_clicked)

        if is_default:
            btn.get_style_context().add_class("default-app")

        return btn
    
    def _on_app_row_clicked(self, btn: Gtk.Button):
        self._open_with_app(btn._app_info, btn._file_path)
        self._close_app_chooser()

    def _on_app_search_changed(self, entry: Gtk.SearchEntry):
        self._populate_app_list(entry.get_text())

    def _on_app_search_key_press(self, entry: Gtk.SearchEntry, event) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self._close_app_chooser()
            return True
        return False