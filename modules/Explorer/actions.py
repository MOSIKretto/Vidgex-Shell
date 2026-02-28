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

_COMPRESS_ONLY_EXTS = frozenset({'.gz', '.bz2', '.xz', '.zst', '.lz4', '.lzma', '.sz'})


class ActionsMixin:

    @staticmethod
    def _algo_validate_filename(name: str) -> Optional[str]:
        if not name:
            return "Name cannot be empty"
        if '/' in name or '\0' in name:
            return "Invalid characters in name"
        return None

    @staticmethod
    def _algo_is_archive_mime(content_type: str) -> bool:
        if not content_type:
            return False
        if Gio.content_type_is_a(content_type, "application/x-archive"):
            return True
        return any(k in content_type.lower() for k in ('zip', 'tar', 'compress', 'archive'))

    @staticmethod
    def _algo_compression_options(all_formats: list, is_dir: bool) -> list:
        if is_dir:
            return [(ext, name) for ext, name in all_formats if ext not in _COMPRESS_ONLY_EXTS]
        return list(all_formats)

    @staticmethod
    def _algo_select_region(name: str, is_file: bool) -> tuple:
        if is_file and '.' in name and not name.startswith('.'):
            dot = name.rfind('.')
            if dot > 0:
                return 0, dot
        return 0, len(name)

    def _exec_ouch_async(self, args: list, cwd: Path, busy_msg: str, ok_msg: str, timeout: int = 300):
        self.status_label.set_label(busy_msg)

        def run():
            try:
                result = subprocess.run(
                    args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)

                def update_ui():
                    if result.returncode == 0:
                        self.status_label.set_label(ok_msg)
                        self._load_directory()
                    else:
                        err = (result.stderr.strip() or result.stdout.strip() or "Unknown error")
                        if "error:" in err.lower():
                            err = err.split("error:")[-1].strip()
                        self.status_label.set_label(f"Failed: {err[:50]}")
                    return False

                GLib.idle_add(update_ui)
                
            except subprocess.TimeoutExpired:
                GLib.idle_add(lambda: self.status_label.set_label("Operation timed out"))
            except FileNotFoundError:
                GLib.idle_add(lambda: self.status_label.set_label("ouch not installed"))
            except Exception as e:
                GLib.idle_add(lambda: self.status_label.set_label(f"Error: {str(e)[:40]}"))

        threading.Thread(target=run, daemon=True).start()


    def _create_menu(self) -> Gtk.Menu:
        menu = Gtk.Menu()
        menu.set_name("explorer-context-menu")
        menu.connect("deactivate", lambda _: setattr(self, '_menu_open', False))
        return menu

    def _menu_item(self, label: str, callback, *args, **kwargs) -> Gtk.MenuItem:
        item = Gtk.MenuItem(label=label)
        item.connect("activate", lambda _: callback(*args, **kwargs))
        return item


    def _on_file_clicked(self, btn: Gtk.Button):
        self._lock_set()
        if self._pending_drop_source:
            return
            
        if btn._is_dir:
            self._navigate_to(btn._path)
        else:
            exec_shell_command_async(f'xdg-open "{btn._path}"')

    def _on_file_button_press(self, btn: Gtk.Button, event) -> bool:
        if event.button == 3 and not self._pending_drop_source:
            self._show_context_menu(btn, event)
            return True
        return False

    def _show_context_menu(self, btn: Gtk.Button, event):
        self._menu_open = True
        self._cancel_pending_hide()
        self._close_rename_widget()

        path = btn._path
        is_dir = btn._is_dir
        menu = self._create_menu()

        menu.append(self._menu_item("Open", self._on_file_clicked, btn))

        if not is_dir:
            submenu = self._build_open_with_submenu(path)
            if submenu:
                item = Gtk.MenuItem(label="Open with...")
                item.set_submenu(submenu)
                menu.append(item)

        menu.append(self._menu_item("Rename", self._show_rename_inline, path, btn))

        term_dir = path if is_dir else path.parent
        menu.append(self._menu_item("Open in Terminal", self._open_terminal, cwd=term_dir))

        menu.append(Gtk.SeparatorMenuItem())

        menu.append(self._menu_item("Copy", self._copy_to_clipboard, [path], is_cut=False))
        menu.append(self._menu_item("Cut", self._copy_to_clipboard, [path], is_cut=True))

        has_files, is_cut = self._get_clipboard_state()
        if is_dir and has_files and os.access(path, os.W_OK):
            label = "Move into Folder" if is_cut else "Paste into Folder"
            menu.append(self._menu_item(label, self._paste_from_clipboard, path))

        menu.append(Gtk.SeparatorMenuItem())

        menu.append(self._menu_item("Copy Path", 
            lambda: Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(str(path), -1)))

        if self._is_archive(path):
            menu.append(self._menu_item("Extract", self._extract_archive, path))

        compress_menu = Gtk.Menu()
        compress_menu.set_name("explorer-context-submenu")
        for ext, name in self._algo_compression_options(self._compression_formats, is_dir):
            item = Gtk.MenuItem(label=f"{name} ({ext})")
            item.connect("activate", lambda _, p=path, e=ext: self._compress_path(p, e))
            compress_menu.append(item)

        compress_parent = Gtk.MenuItem(label="Compress")
        compress_parent.set_submenu(compress_menu)
        menu.append(compress_parent)

        menu.append(Gtk.SeparatorMenuItem())

        if self._is_in_trash():
            menu.append(self._menu_item("Restore", self._restore_from_trash, path))
            menu.append(self._menu_item("Delete Permanently", self._delete_permanently, path))
        else:
            menu.append(self._menu_item("Move to Trash", self._move_to_trash, path))

        menu.show_all()
        menu.popup_at_pointer(event)

    def _show_background_context_menu(self, event):
        self._menu_open = True
        self._cancel_pending_hide()
        self._close_rename_widget()

        menu = self._create_menu()

        if self._clipboard_paths and self._can_paste():
            label = "Move Here" if self._clipboard_is_cut else "Paste Here"
            menu.append(self._menu_item(label, self._paste_from_clipboard, self._current_path))
            menu.append(Gtk.SeparatorMenuItem())

        menu.append(self._menu_item("New Folder", self._create_new_folder))
        menu.append(self._menu_item("New File", self._create_new_file))
        menu.append(Gtk.SeparatorMenuItem())
        menu.append(self._menu_item("Open in Terminal", self._open_terminal, cwd=self._current_path))

        menu.show_all()
        menu.popup_at_pointer(event)

    def _build_open_with_submenu(self, path: Path) -> Optional[Gtk.Menu]:
        try:
            content_type, _ = Gio.content_type_guess(str(path), None)
            if not content_type:
                return None

            apps = Gio.AppInfo.get_all_for_type(content_type)
            if not apps:
                return None

            submenu = Gtk.Menu()
            submenu.set_name("explorer-context-submenu")

            default = Gio.AppInfo.get_default_for_type(content_type, False)
            default_id = default.get_id() if default else None

            seen = set()
            for app in apps:
                aid = app.get_id()
                if aid in seen:
                    continue
                seen.add(aid)
                
                name = app.get_display_name()
                if aid == default_id:
                    name = f"● {name}"
                    
                item = Gtk.MenuItem(label=name)
                item.connect("activate", lambda _, a=app, p=path: self._open_with_app(a, p))
                submenu.append(item)

            if seen:
                submenu.append(Gtk.SeparatorMenuItem())

            other = Gtk.MenuItem(label="Other Application...")
            other.connect("activate", lambda _, p=path: self._show_app_chooser(p))
            submenu.append(other)

            return submenu
        except Exception as e:
            print(f"Open-with menu error: {e}")
            return None

    def _open_with_app(self, app: Gio.AppInfo, path: Path):
        try:
            app.launch([Gio.File.new_for_path(str(path))], None)
            self.status_label.set_label(f"Opened with {app.get_display_name()}")
        except Exception as e:
            self.status_label.set_label(f"Error: {str(e)[:30]}")

    def _is_archive(self, path: Path) -> bool:
        if path.is_dir():
            return False
        try:
            content_type, _ = Gio.content_type_guess(str(path), None)
            return self._algo_is_archive_mime(content_type)
        except Exception:
            return False

    def _extract_archive(self, path: Path):
        self._lock_set()
        if not path.exists():
            self.status_label.set_label("File not found")
            return
            
        self._exec_ouch_async(
            ["ouch", "decompress", path.name, "--yes"],
            cwd=path.parent,
            busy_msg=f"Extracting: {path.name}...",
            ok_msg=f"Extracted: {path.name}")

    def _compress_path(self, path: Path, fmt_ext: str):
        self._lock_set()
        if not path.exists():
            self.status_label.set_label("File/folder not found")
            return
            
        output = self._get_unique_path(path.parent / f"{path.name}{fmt_ext}")
        self._exec_ouch_async(
            ["ouch", "compress", path.name, output.name, "--yes"],
            cwd=path.parent,
            busy_msg=f"Compressing: {path.name}...",
            ok_msg=f"Created: {output.name}",
            timeout=600)

    def _show_rename_inline(self, path: Path, file_row: Gtk.Widget):
        self._close_rename_widget()
        self._rename_path = path
        self._menu_open = True
        self._cancel_pending_hide()
        self._set_keyboard_interactive(True)

        original = path.name

        entry = Gtk.Entry()
        entry.set_name("explorer-rename-entry")
        entry.set_text(original)
        entry.set_can_focus(True)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.set_name("explorer-rename-btn")
        cancel_btn.get_style_context().add_class("cancel")
        cancel_btn.connect("clicked", lambda _: self._close_rename_widget())

        confirm_btn = Gtk.Button(label="Rename")
        confirm_btn.set_name("explorer-rename-btn")
        confirm_btn.get_style_context().add_class("confirm")
        confirm_btn.set_sensitive(False)
        confirm_btn.connect("clicked", lambda _: self._do_rename(entry.get_text()))

        def on_changed(e):
            text = e.get_text().strip()
            confirm_btn.set_sensitive(bool(text) and text != original)

        def on_activate(e):
            if confirm_btn.get_sensitive():
                self._do_rename(e.get_text())

        def on_key(e, ev):
            if ev.keyval == Gdk.KEY_Escape:
                self._close_rename_widget()
                return True
            return False

        entry.connect("changed", on_changed)
        entry.connect("activate", on_activate)
        entry.connect("key-press-event", on_key)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_box.set_name("explorer-rename-buttons")
        btn_box.set_halign(Gtk.Align.END)
        btn_box.pack_start(cancel_btn, False, False, 0)
        btn_box.pack_start(confirm_btn, False, False, 0)

        self._rename_widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._rename_widget.set_name("explorer-rename-container")
        self._rename_widget.pack_start(entry, False, False, 0)
        self._rename_widget.pack_start(btn_box, False, False, 0)

        parent = file_row.get_parent()
        if parent:
            children = parent.get_children()
            if file_row in children:
                idx = children.index(file_row)
                parent.pack_start(self._rename_widget, False, False, 0)
                parent.reorder_child(self._rename_widget, idx + 1)

        self._rename_widget.show_all()
        self._rename_entry = entry

        sel_start, sel_end = self._algo_select_region(original, path.is_file())

        def force_focus():
            try:
                self.present()
                self.set_focus(entry)
                entry.grab_focus()
                entry.select_region(sel_start, sel_end)
            except Exception:
                pass
            return False

        GLib.idle_add(force_focus)

    def _close_rename_widget(self):
        if self._rename_widget:
            self._rename_widget.destroy()
            self._rename_widget = None
            
        self._rename_path = None
        self._menu_open = False
        self._set_keyboard_interactive(False)

    def _do_rename(self, new_name: str):
        old_path = self._rename_path
        self._close_rename_widget()
        if not old_path:
            return

        new_name = new_name.strip()
        err = self._algo_validate_filename(new_name)
        
        if err:
            self.status_label.set_label(f"Error: {err}")
            return
        if new_name == old_path.name:
            return

        new_path = old_path.parent / new_name
        if new_path.exists():
            self.status_label.set_label(f"Error: '{new_name}' already exists")
            return
            
        try:
            old_path.rename(new_path)
            self.status_label.set_label(f"Renamed to: {new_name}")
            self._load_directory()
        except PermissionError:
            self.status_label.set_label("Error: Permission denied")
        except Exception as e:
            self.status_label.set_label(f"Rename failed: {str(e)[:30]}")

    def _create_new_item(self, base_name: str, is_dir: bool):
        path = self._get_unique_path(self._current_path / base_name)
        try:
            if is_dir:
                path.mkdir(parents=False, exist_ok=False)
            else:
                path.touch(exist_ok=False)
                
            self.status_label.set_label(f"Created: {path.name}")
            self._load_directory()
            GLib.idle_add(self._find_and_rename_new_item, path)
            
        except PermissionError:
            self.status_label.set_label("Permission denied")
        except Exception as e:
            self.status_label.set_label(f"Error: {str(e)[:30]}")

    def _create_new_folder(self):
        self._create_new_item("New Folder", is_dir=True)

    def _create_new_file(self):
        self._create_new_item("New File", is_dir=False)

    def _find_and_rename_new_item(self, path: Path) -> bool:
        for child in self.files_container.get_children():
            if getattr(child, '_path', None) == path:
                self._show_rename_inline(path, child)
                return False
        return False

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

    def _move_to_trash(self, path: Path):
        try:
            Gio.File.new_for_path(str(path)).trash(None)
            self.status_label.set_label(f"Moved to trash: {path.name}")
        except Exception as e:
            self.status_label.set_label(f"Error: {e}")

    def _restore_from_trash(self, path: Path):
        try:
            info_file = self._trash_info_path / f"{path.name}.trashinfo"
            if info_file.exists():
                for line in info_file.read_text().splitlines():
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

    def _delete_permanently(self, path: Path):
        try:
            if path.is_dir():
                shutil.rmtree(str(path))
            else:
                path.unlink()
                
            info_file = self._trash_info_path / f"{path.name}.trashinfo"
            if info_file.exists():
                info_file.unlink()
                
            self.status_label.set_label(f"Deleted: {path.name}")
        except Exception as e:
            self.status_label.set_label(f"Error: {e}")

    def _on_clear_trash_clicked(self, btn: Gtk.Button):
        self._lock_set()
        count = 0
        try:
            if self._trash_path.exists():
                for item in self._trash_path.iterdir():
                    try:
                        shutil.rmtree(item) if item.is_dir() else item.unlink()
                        count += 1
                    except OSError:
                        pass
                        
            if self._trash_info_path.exists():
                for item in self._trash_info_path.iterdir():
                    try:
                        item.unlink()
                    except OSError:
                        pass
                        
            self.status_label.set_label(f"Trash emptied: {count} items")
            self._load_directory()
        except Exception as e:
            self.status_label.set_label(f"Error: {e}")