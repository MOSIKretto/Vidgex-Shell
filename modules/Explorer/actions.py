import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Gio

from fabric.utils import exec_shell_command_async

import os
import shutil
import subprocess
import threading
import urllib.parse
from pathlib import Path
from typing import Optional



class ActionsMixin:
    def _on_file_clicked(self, btn):
        self._set_navigation_lock()
        if self._pending_drop_source:
            return
        if btn._is_dir:
            self._navigate_to(btn._path)
        else:
            exec_shell_command_async(f'xdg-open "{btn._path}"')

    def _on_file_button_press(self, btn, event) -> bool:
        if event.button == 3 and not self._pending_drop_source:
            self._show_context_menu(btn, event)
            return True
        return False

    def _build_open_with_submenu(self, path: Path) -> Optional[Gtk.Menu]:
        try:
            content_type, uncertain = Gio.content_type_guess(str(path), None)
            if not content_type:
                return None

            apps = Gio.AppInfo.get_all_for_type(content_type)
            if not apps:
                return None

            submenu = Gtk.Menu()
            submenu.set_name("explorer-context-submenu")

            default_app = Gio.AppInfo.get_default_for_type(content_type, False)
            default_id = default_app.get_id() if default_app else None

            added = set()
            append = submenu.append
            for app in apps:
                app_id = app.get_id()
                if app_id in added:
                    continue
                added.add(app_id)

                app_name = app.get_display_name()
                if app_id == default_id:
                    app_name = f"● {app_name}"

                item = Gtk.MenuItem(label=app_name)
                item.connect("activate", lambda _, a=app, p=path: self._open_with_app(a, p))
                append(item)

            if added:
                append(Gtk.SeparatorMenuItem())

            other_item = Gtk.MenuItem(label="Other Application...")
            other_item.connect("activate", lambda _, p=path: self._show_app_chooser(p))
            append(other_item)

            return submenu

        except Exception as e:
            print(f"Error building open with menu: {e}")
            return None

    def _show_context_menu(self, btn, event):
        self._menu_open = True
        self._cancel_pending_hide()
        self._close_rename_widget()

        menu = Gtk.Menu()
        menu.set_name("explorer-context-menu")
        menu.connect("deactivate", lambda m: setattr(self, '_menu_open', False))
        append = menu.append

        path = btn._path
        is_dir = btn._is_dir

        open_item = Gtk.MenuItem(label="Open")
        open_item.connect("activate", lambda _: self._on_file_clicked(btn))
        append(open_item)

        if not is_dir:
            open_with_submenu = self._build_open_with_submenu(path)
            if open_with_submenu:
                open_with_item = Gtk.MenuItem(label="Open with...")
                open_with_item.set_submenu(open_with_submenu)
                append(open_with_item)

        rename_item = Gtk.MenuItem(label="Rename")
        rename_item.connect("activate", lambda _, b=btn, p=path: self._show_rename_inline(p, b))
        append(rename_item)

        term_dir = path if is_dir else path.parent
        term_item = Gtk.MenuItem(label="Open in Terminal")
        term_item.connect("activate", lambda _, d=term_dir: exec_shell_command_async(
            f'{os.environ.get("TERMINAL", "kitty")} --working-directory "{d}"'))
        append(term_item)

        append(Gtk.SeparatorMenuItem())

        copy_item = Gtk.MenuItem(label="Copy")
        copy_item.connect("activate", lambda _, p=path: self._copy_to_clipboard([p], is_cut=False))
        append(copy_item)

        cut_item = Gtk.MenuItem(label="Cut")
        cut_item.connect("activate", lambda _, p=path: self._copy_to_clipboard([p], is_cut=True))
        append(cut_item)

        if is_dir and self._can_paste_to(path):
            paste_item = Gtk.MenuItem(label="Paste")
            paste_item.connect("activate", lambda _, p=path: self._paste_from_clipboard(p))
            append(paste_item)

        append(Gtk.SeparatorMenuItem())

        copy_path_item = Gtk.MenuItem(label="Copy Path")
        copy_path_item.connect("activate", lambda _: Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(str(path), -1))
        append(copy_path_item)

        if self._is_archive(path):
            extract_item = Gtk.MenuItem(label="Extract")
            extract_item.connect("activate", lambda _, p=path: self._extract_archive(p))
            append(extract_item)

        compress_submenu = Gtk.Menu()
        compress_submenu.set_name("explorer-context-submenu")

        single_file_only = frozenset({'.gz', '.bz2', '.xz', '.zst', '.lz4', '.lzma', '.sz'})
        for fmt_ext, fmt_name in self._compression_formats:
            if is_dir and fmt_ext in single_file_only:
                continue

            compress_item = Gtk.MenuItem(label=f"{fmt_name} ({fmt_ext})")
            compress_item.connect(
                "activate",
                lambda _, p=path, e=fmt_ext: self._compress_path(p, e)
            )
            compress_submenu.append(compress_item)

        compress_parent = Gtk.MenuItem(label="Compress")
        compress_parent.set_submenu(compress_submenu)
        append(compress_parent)

        append(Gtk.SeparatorMenuItem())

        if self._is_in_trash():
            restore = Gtk.MenuItem(label="Restore")
            restore.connect("activate", lambda _, p=path: self._restore_from_trash(p))
            append(restore)
            delete = Gtk.MenuItem(label="Delete Permanently")
            delete.connect("activate", lambda _, p=path: self._delete_permanently(p))
            append(delete)
        else:
            trash = Gtk.MenuItem(label="Move to Trash")
            trash.connect("activate", lambda _, p=path: self._move_to_trash(p))
            append(trash)

        menu.show_all()
        menu.popup_at_pointer(event)

    def _can_paste_to(self, dest_folder: Path) -> bool:
        if not self._clipboard_paths:
            return False

        dest_resolved = dest_folder.resolve()

        for p in self._clipboard_paths:
            if not p.exists():
                continue

            if p.is_dir():
                pr = p.resolve()
                if pr == dest_resolved:
                    continue
                try:
                    dest_resolved.relative_to(pr)
                    continue
                except ValueError:
                    pass

            return True

        return False

    def _show_background_context_menu(self, event):
        self._menu_open = True
        self._cancel_pending_hide()
        self._close_rename_widget()

        menu = Gtk.Menu()
        menu.set_name("explorer-context-menu")
        menu.connect("deactivate", lambda m: setattr(self, '_menu_open', False))
        append = menu.append

        if self._can_paste():
            paste_label = "Move" if self._clipboard_is_cut else "Paste"
            paste_item = Gtk.MenuItem(label=paste_label)
            paste_item.connect("activate", lambda _: self._paste_from_clipboard(self._current_path))
            append(paste_item)
            append(Gtk.SeparatorMenuItem())

        new_folder_item = Gtk.MenuItem(label="New Folder")
        new_folder_item.connect("activate", lambda _: self._create_new_folder())
        append(new_folder_item)

        new_file_item = Gtk.MenuItem(label="New File")
        new_file_item.connect("activate", lambda _: self._create_new_file())
        append(new_file_item)

        append(Gtk.SeparatorMenuItem())

        term_item = Gtk.MenuItem(label="Open in Terminal")
        term_item.connect("activate", lambda _: exec_shell_command_async(
            f'{os.environ.get("TERMINAL", "kitty")} --working-directory "{self._current_path}"'))
        append(term_item)

        menu.show_all()
        menu.popup_at_pointer(event)

    def _is_archive(self, path: Path) -> bool:
        if path.is_dir():
            return False
        name_lower = path.name.lower()
        if name_lower.endswith(self._archive_extensions_compound):
            return True
        return path.suffix.lower() in self._archive_extensions_simple

    def _extract_archive(self, path: Path):
        self._set_navigation_lock()
        if not path.exists():
            self.status_label.set_label("File not found")
            return
        archive_dir = path.parent
        archive_name = path.name
        self.status_label.set_label(f"Extracting: {archive_name}...")

        def do_extract():
            try:
                result = subprocess.run(
                    ["ouch", "decompress", archive_name, "--yes"],
                    cwd=str(archive_dir),
                    capture_output=True,
                    text=True,
                    timeout=300
                )

                def update_ui():
                    if result.returncode == 0:
                        self.status_label.set_label(f"Extracted: {archive_name}")
                        self._load_directory()
                    else:
                        error = result.stderr.strip() or result.stdout.strip() or "Unknown error"
                        self.status_label.set_label(f"Failed: {error[:40]}")
                    return False

                GLib.idle_add(update_ui)
            except subprocess.TimeoutExpired:
                GLib.idle_add(lambda: self.status_label.set_label("Extract timed out"))
            except FileNotFoundError:
                GLib.idle_add(lambda: self.status_label.set_label("ouch not installed"))
            except Exception as e:
                GLib.idle_add(lambda: self.status_label.set_label(f"Error: {str(e)[:40]}"))

        thread = threading.Thread(target=do_extract, daemon=True)
        thread.start()

    def _compress_path(self, path: Path, format_ext: str):
        self._set_navigation_lock()

        if not path.exists():
            self.status_label.set_label("File/folder not found")
            return

        single_file_formats = frozenset({'.gz', '.xz', '.zst', '.bz2', '.lz4', '.lzma', '.sz'})
        if path.is_dir() and format_ext in single_file_formats:
            self.status_label.set_label(f"Cannot compress folder to {format_ext}")
            return

        output_name = f"{path.name}{format_ext}"
        output_path = path.parent / output_name
        output_path = self._get_unique_path(output_path)

        self.status_label.set_label(f"Compressing: {path.name}...")

        def do_compress():
            try:
                result = subprocess.run(
                    ["ouch", "compress", path.name, output_path.name, "--yes"],
                    cwd=str(path.parent),
                    capture_output=True,
                    text=True,
                    timeout=600
                )

                def update_ui():
                    if result.returncode == 0:
                        self.status_label.set_label(f"Created: {output_path.name}")
                        self._load_directory()
                    else:
                        error = result.stderr.strip() or result.stdout.strip() or "Unknown error"
                        if "error:" in error.lower():
                            error = error.split("error:")[-1].strip()
                        self.status_label.set_label(f"Failed: {error[:50]}")
                    return False

                GLib.idle_add(update_ui)

            except subprocess.TimeoutExpired:
                GLib.idle_add(lambda: self.status_label.set_label("Compression timed out"))
            except FileNotFoundError:
                GLib.idle_add(lambda: self.status_label.set_label("ouch not installed (cargo install ouch)"))
            except Exception as e:
                GLib.idle_add(lambda: self.status_label.set_label(f"Error: {str(e)[:40]}"))

        thread = threading.Thread(target=do_compress, daemon=True)
        thread.start()

    def _show_rename_inline(self, path: Path, file_row: Gtk.Widget):
        self._close_rename_widget()
        self._rename_path = path
        self._menu_open = True
        self._cancel_pending_hide()

        self._set_keyboard_interactive(True)

        self._rename_widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._rename_widget.set_name("explorer-rename-container")

        entry = Gtk.Entry()
        entry.set_name("explorer-rename-entry")
        entry.set_text(path.name)
        entry.set_can_focus(True)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_box.set_name("explorer-rename-buttons")
        btn_box.set_halign(Gtk.Align.END)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.set_name("explorer-rename-btn")
        cancel_btn.get_style_context().add_class("cancel")
        cancel_btn.connect("clicked", lambda _: self._close_rename_widget())

        confirm_btn = Gtk.Button(label="Rename")
        confirm_btn.set_name("explorer-rename-btn")
        confirm_btn.get_style_context().add_class("confirm")
        confirm_btn.set_sensitive(False)
        confirm_btn.connect("clicked", lambda _: self._do_rename_inline(entry.get_text()))

        original_name = path.name

        def on_text_changed(entry_widget):
            new_text = entry_widget.get_text().strip()
            has_change = new_text != original_name and len(new_text) > 0
            confirm_btn.set_sensitive(has_change)

        entry.connect("changed", on_text_changed)

        def on_entry_activate(entry_widget):
            if confirm_btn.get_sensitive():
                self._do_rename_inline(entry_widget.get_text())

        entry.connect("activate", on_entry_activate)
        entry.connect("key-press-event", self._on_rename_key_press)

        btn_box.pack_start(cancel_btn, False, False, 0)
        btn_box.pack_start(confirm_btn, False, False, 0)

        self._rename_widget.pack_start(entry, False, False, 0)
        self._rename_widget.pack_start(btn_box, False, False, 0)

        parent = file_row.get_parent()
        if parent:
            children = parent.get_children()
            idx = children.index(file_row) if file_row in children else -1
            if idx >= 0:
                parent.pack_start(self._rename_widget, False, False, 0)
                parent.reorder_child(self._rename_widget, idx + 1)

        self._rename_widget.show_all()

        self._rename_entry = entry

        is_file = path.is_file()
        has_dot = '.' in path.name and not path.name.startswith('.')
        last_dot = path.name.rfind('.') if has_dot else -1

        def force_focus():
            try:
                self.present()
                self.set_focus(self._rename_entry)
                self._rename_entry.grab_focus()

                if is_file and last_dot > 0:
                    self._rename_entry.select_region(0, last_dot)
                else:
                    self._rename_entry.select_region(0, len(path.name))
            except Exception as e:
                pass
            return False

        GLib.idle_add(force_focus)
        GLib.timeout_add(50, force_focus)
        GLib.timeout_add(100, force_focus)
        GLib.timeout_add(200, force_focus)

    def _on_rename_key_press(self, entry, event) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self._close_rename_widget()
            return True
        return False

    def _close_rename_widget(self):
        if self._rename_widget:
            self._rename_widget.destroy()
            self._rename_widget = None
        self._rename_path = None
        self._menu_open = False
        self._set_keyboard_interactive(False)

    def _do_rename_inline(self, new_name: str):
        if not self._rename_path:
            self._close_rename_widget()
            return
        old_path = self._rename_path
        new_name = new_name.strip()
        self._close_rename_widget()
        try:
            if not new_name:
                self.status_label.set_label("Error: Name cannot be empty")
                return
            if '/' in new_name or '\0' in new_name:
                self.status_label.set_label("Error: Invalid characters in name")
                return
            if new_name == old_path.name:
                return
            new_path = old_path.parent / new_name
            if new_path.exists():
                self.status_label.set_label(f"Error: '{new_name}' already exists")
                return
            old_path.rename(new_path)
            self.status_label.set_label(f"Renamed to: {new_name}")
            self._load_directory()
        except PermissionError:
            self.status_label.set_label("Error: Permission denied")
        except Exception as e:
            self.status_label.set_label(f"Rename failed: {str(e)[:30]}")

    def _create_new_folder(self):
        base_name = "New Folder"
        new_path = self._get_unique_path(self._current_path / base_name)

        try:
            new_path.mkdir(parents=False, exist_ok=False)
            self.status_label.set_label(f"Created: {new_path.name}")
            self._load_directory()

            GLib.timeout_add(100, lambda: self._find_and_rename_new_item(new_path))
        except PermissionError:
            self.status_label.set_label("Permission denied")
        except Exception as e:
            self.status_label.set_label(f"Error: {str(e)[:30]}")

    def _create_new_file(self):
        base_name = "New File"
        new_path = self._get_unique_path(self._current_path / base_name)

        try:
            new_path.touch(exist_ok=False)
            self.status_label.set_label(f"Created: {new_path.name}")
            self._load_directory()

            GLib.timeout_add(100, lambda: self._find_and_rename_new_item(new_path))
        except PermissionError:
            self.status_label.set_label("Permission denied")
        except Exception as e:
            self.status_label.set_label(f"Error: {str(e)[:30]}")

    def _find_and_rename_new_item(self, path: Path) -> bool:
        try:
            for child in self.files_container.get_children():
                if hasattr(child, '_path') and child._path == path:
                    self._show_rename_inline(path, child)
                    return False
        except:
            pass
        return False

    def _move_to_trash(self, path):
        try:
            Gio.File.new_for_path(str(path)).trash(None)
            self.status_label.set_label(f"Moved to trash: {path.name}")
        except Exception as e:
            self.status_label.set_label(f"Error: {e}")

    def _restore_from_trash(self, path):
        try:
            info_file = self._trash_info_path / f"{path.name}.trashinfo"
            if info_file.exists():
                with open(info_file) as f:
                    for line in f:
                        if line.startswith("Path="):
                            orig = Path(urllib.parse.unquote(line[5:].strip()))
                            orig.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(path), str(self._get_unique_path(orig)))
                            info_file.unlink()
                            self.status_label.set_label(f"Restored: {path.name}")
                            return
            shutil.move(str(path), str(self._get_unique_path(Path.home() / path.name)))
            self.status_label.set_label(f"Restored to Home: {path.name}")
        except Exception as e:
            self.status_label.set_label(f"Error: {e}")

    def _delete_permanently(self, path):
        try:
            shutil.rmtree(str(path)) if path.is_dir() else path.unlink()
            info_file = self._trash_info_path / f"{path.name}.trashinfo"
            if info_file.exists():
                info_file.unlink()
            self.status_label.set_label(f"Deleted: {path.name}")
        except Exception as e:
            self.status_label.set_label(f"Error: {e}")

    def _on_clear_trash_clicked(self, btn):
        self._set_navigation_lock()
        try:
            count = 0
            if self._trash_path.exists():
                for item in self._trash_path.iterdir():
                    try:
                        shutil.rmtree(str(item)) if item.is_dir() else item.unlink()
                        count += 1
                    except:
                        pass
            if self._trash_info_path.exists():
                for item in self._trash_info_path.iterdir():
                    try:
                        item.unlink()
                    except:
                        pass
            self.status_label.set_label(f"Trash emptied: {count} items")
            self._load_directory()
        except Exception as e:
            self.status_label.set_label(f"Error: {e}")

    def _open_with_app(self, app: Gio.AppInfo, path: Path):
        try:
            gfile = Gio.File.new_for_path(str(path))
            app.launch([gfile], None)
            self.status_label.set_label(f"Opened with {app.get_display_name()}")
        except Exception as e:
            self.status_label.set_label(f"Error: {str(e)[:30]}")