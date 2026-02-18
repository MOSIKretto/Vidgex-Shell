import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Gio

from pathlib import Path


class AppChooserMixin:

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

            default_app = None
            try:
                default_app = Gio.AppInfo.get_default_for_type(content_type, False)
            except:
                pass

            recommended = []
            try:
                recommended = Gio.AppInfo.get_recommended_for_type(content_type) or []
            except:
                pass
            recommended_ids = {a.get_id() for a in recommended}

            all_for_type = []
            try:
                all_for_type = Gio.AppInfo.get_all_for_type(content_type) or []
            except:
                pass

            fallback = []
            try:
                fallback = Gio.AppInfo.get_fallback_for_type(content_type) or []
            except:
                pass

            other = []
            other_ids = set()
            for app in list(all_for_type) + list(fallback):
                aid = app.get_id()
                if aid and aid not in recommended_ids and aid not in other_ids:
                    other.append(app)
                    other_ids.add(aid)

            seen = recommended_ids | other_ids
            remaining = []
            try:
                for app in Gio.AppInfo.get_all():
                    if not app.should_show():
                        continue
                    aid = app.get_id()
                    if aid and aid not in seen:
                        remaining.append(app)
                        seen.add(aid)
            except:
                pass
            remaining.sort(key=lambda a: (a.get_display_name() or "").lower())

            self._app_chooser_recommended = recommended
            self._app_chooser_other = other
            self._app_chooser_remaining = remaining

            for child in self.files_container.get_children():
                child.destroy()

            header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            header_box.set_name("explorer-app-chooser-header")

            back_btn = Gtk.Button()
            back_btn.set_name("explorer-app-back")
            back_icon = Gtk.Image.new_from_icon_name("go-previous-symbolic", Gtk.IconSize.BUTTON)
            back_btn.add(back_icon)
            back_btn.connect("clicked", lambda _: self._close_app_chooser())

            title_label = Gtk.Label(label=f"Open \"{path.name}\" with...")
            title_label.set_name("explorer-app-chooser-title")
            title_label.set_ellipsize(3)
            title_label.set_hexpand(True)
            title_label.set_halign(Gtk.Align.START)

            header_box.pack_start(back_btn, False, False, 0)
            header_box.pack_start(title_label, True, True, 0)

            self.files_container.pack_start(header_box, False, False, 0)

            self._app_search_entry = Gtk.SearchEntry()
            self._app_search_entry.set_name("explorer-app-search")
            self._app_search_entry.set_placeholder_text("Search applications...")
            self._app_search_entry.connect("search-changed", self._on_app_search_changed)
            self._app_search_entry.connect("key-press-event", self._on_app_search_key_press)

            self.files_container.pack_start(self._app_search_entry, False, False, 0)

            self._app_list_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            self._app_list_container.set_name("explorer-app-list")
            self.files_container.pack_start(self._app_list_container, False, False, 0)

            self._populate_app_list("")

            self.files_container.show_all()

            self.status_label.set_label(f"Choose application for: {path.name}")

            def focus_search():
                try:
                    self.present()
                    if self._app_search_entry:
                        self.set_focus(self._app_search_entry)
                        self._app_search_entry.grab_focus()
                except:
                    pass
                return False

            GLib.idle_add(focus_search)
            GLib.timeout_add(100, focus_search)

        except Exception as e:
            print(f"App chooser error: {e}")
            self._close_app_chooser()

    def _populate_app_list(self, search_text: str):
        if not self._app_list_container:
            return

        for child in self._app_list_container.get_children():
            child.destroy()

        search = search_text.lower().strip()
        path = self._app_chooser_path

        default_app = None
        try:
            if self._app_chooser_content_type:
                default_app = Gio.AppInfo.get_default_for_type(self._app_chooser_content_type, False)
        except:
            pass
        default_id = default_app.get_id() if default_app else None

        def matches(app):
            if not search:
                return True
            name = (app.get_display_name() or "").lower()
            desc = (app.get_description() or "").lower()
            exe = (app.get_executable() or "").lower()
            return search in name or search in desc or search in exe

        has_any = False

        filtered_rec = [a for a in self._app_chooser_recommended if matches(a)]
        if filtered_rec:
            has_any = True
            header = Gtk.Label(label="Recommended")
            header.set_name("explorer-sidebar-header")
            header.set_halign(Gtk.Align.START)
            self._app_list_container.pack_start(header, False, False, 0)
            for app in filtered_rec:
                is_def = (app.get_id() == default_id)
                row = self._create_app_row(app, path, is_def)
                self._app_list_container.pack_start(row, False, False, 0)

        filtered_other = [a for a in self._app_chooser_other if matches(a)]
        if filtered_other:
            has_any = True
            header = Gtk.Label(label="Other")
            header.set_name("explorer-sidebar-header")
            header.set_halign(Gtk.Align.START)
            self._app_list_container.pack_start(header, False, False, 0)
            for app in filtered_other:
                row = self._create_app_row(app, path, False)
                self._app_list_container.pack_start(row, False, False, 0)

        filtered_remaining = [a for a in self._app_chooser_remaining if matches(a)]
        if filtered_remaining:
            has_any = True
            header = Gtk.Label(label="All Applications")
            header.set_name("explorer-sidebar-header")
            header.set_halign(Gtk.Align.START)
            self._app_list_container.pack_start(header, False, False, 0)
            for app in filtered_remaining:
                row = self._create_app_row(app, path, False)
                self._app_list_container.pack_start(row, False, False, 0)

        if not has_any:
            empty_label = Gtk.Label(label="No matching applications found")
            empty_label.set_name("explorer-empty-label")
            empty_label.set_margin_top(20)
            empty_label.set_margin_bottom(20)
            self._app_list_container.pack_start(empty_label, False, False, 0)

        self._app_list_container.show_all()

    def _create_app_row(self, app: Gio.AppInfo, path: Path, is_default: bool = False) -> Gtk.Button:
        icon_widget = Gtk.Image()
        icon_widget.set_pixel_size(24)
        icon_widget.set_name("explorer-file-icon")

        try:
            app_icon = app.get_icon()
            if app_icon:
                icon_widget.set_from_gicon(app_icon, Gtk.IconSize.LARGE_TOOLBAR)
            else:
                icon_widget.set_from_icon_name("application-x-executable-symbolic", Gtk.IconSize.LARGE_TOOLBAR)
        except:
            icon_widget.set_from_icon_name("application-x-executable-symbolic", Gtk.IconSize.LARGE_TOOLBAR)

        display_name = app.get_display_name() or "Unknown"
        name_label = Gtk.Label(label=display_name)
        name_label.set_name("explorer-file-name")
        name_label.set_halign(Gtk.Align.START)
        name_label.set_hexpand(True)
        name_label.set_ellipsize(3)

        if is_default:
            info_text = "default"
        else:
            info_text = app.get_description() or ""
            if len(info_text) > 40:
                info_text = info_text[:37] + "..."

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

    def _on_app_row_clicked(self, btn):
        app = btn._app_info
        path = btn._file_path
        self._open_with_app(app, path)
        self._close_app_chooser()

    def _on_app_search_changed(self, entry):
        search_text = entry.get_text()
        self._populate_app_list(search_text)

    def _on_app_search_key_press(self, entry, event) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self._close_app_chooser()
            return True
        return False

    def _close_app_chooser(self):
        if self._app_chooser_active:
            self._app_chooser_active = False
            self._app_chooser_path = None
            self._app_chooser_content_type = None
            self._app_chooser_recommended = []
            self._app_chooser_other = []
            self._app_chooser_remaining = []
            self._app_list_container = None
            self._app_search_entry = None
            self._menu_open = False
            self._set_keyboard_interactive(False)
            self._load_directory()