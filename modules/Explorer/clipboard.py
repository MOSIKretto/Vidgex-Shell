import os
import shutil
import threading
from pathlib import Path
from typing import List, Optional, Tuple

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib


class ClipboardMixin:

    @staticmethod
    def _algo_validate_paste_target(src: Path, dest_resolved: Path) -> Optional[str]:
        if not src.is_dir():
            return None
            
        src_resolved = src.resolve()
        if src_resolved == dest_resolved:
            return f"Cannot paste '{src.name}' into itself"
            
        try:
            dest_resolved.relative_to(src_resolved)
            return f"Cannot paste '{src.name}' into its subfolder"
        except ValueError:
            return None

    @staticmethod
    def _algo_unique_dest(dest: Path) -> Path:
        if not dest.exists():
            return dest
            
        stem, suffix, parent = dest.stem, dest.suffix, dest.parent
        i = 1
        while True:
            candidate = parent / f"{stem} ({i}){suffix}"
            if not candidate.exists():
                return candidate
            i += 1

    @staticmethod
    def _algo_status_message(count: int, is_cut: bool, verb_past: bool = True) -> str:
        if verb_past:
            action = "Moved" if is_cut else "Pasted"
        else:
            action = "Moving" if is_cut else "Copying"
            
        return f"{action}: {count} item(s)" if count != 1 else f"{action}: 1 item"

    def _get_clipboard_state(self) -> Tuple[bool, bool]:
        has = bool(self._clipboard_paths and any(p.exists() for p in self._clipboard_paths))
        return has, self._clipboard_is_cut

    def _can_paste(self) -> bool:
        if not self._clipboard_paths:
            return False
        return any(p.exists() for p in self._clipboard_paths)

    def _copy_to_clipboard(self, paths: List[Path], is_cut: bool = False):
        self._clipboard_paths = [p for p in paths if p.exists()]
        self._clipboard_is_cut = is_cut

        if not self._clipboard_paths:
            self.status_label.set_label("Nothing to copy")
            return

        action = "Cut" if is_cut else "Copied"
        count = len(self._clipboard_paths)
        name = self._clipboard_paths[0].name if count == 1 else f"{count} items"
        self.status_label.set_label(f"{action}: {name}")

    def _paste_from_clipboard(self, dest_folder: Path):
        if not self._clipboard_paths:
            self.status_label.set_label("Clipboard is empty")
            return

        if not dest_folder.is_dir():
            dest_folder = dest_folder.parent

        if not dest_folder.exists():
            self.status_label.set_label("Destination doesn't exist")
            return

        if not os.access(dest_folder, os.W_OK):
            self.status_label.set_label("Permission denied")
            return

        is_cut = self._clipboard_is_cut
        src_paths = list(self._clipboard_paths)
        dest_resolved = dest_folder.resolve()

        self.status_label.set_label(
            f"{self._algo_status_message(len(src_paths), is_cut, verb_past=False)}...")

        def do_paste():
            pasted = 0
            errors = 0

            for src in src_paths:
                if not src.exists():
                    continue

                err = self._algo_validate_paste_target(src, dest_resolved)
                if err:
                    GLib.idle_add(self.status_label.set_label, err)
                    continue

                unique_dest = self._algo_unique_dest(dest_folder / src.name)

                try:
                    if is_cut:
                        shutil.move(str(src), str(unique_dest))
                    elif src.is_dir():
                        shutil.copytree(str(src), str(unique_dest))
                    else:
                        shutil.copy2(str(src), str(unique_dest))
                    pasted += 1
                except PermissionError:
                    errors += 1
                except Exception as e:
                    print(f"Paste error for {src}: {e}")
                    errors += 1

            def update_ui():
                if is_cut and pasted > 0:
                    self._clipboard_paths.clear()
                    self._clipboard_is_cut = False

                if pasted > 0:
                    self.status_label.set_label(self._algo_status_message(pasted, is_cut))
                elif errors > 0:
                    self.status_label.set_label(f"Failed to paste {errors} item(s)")
                else:
                    self.status_label.set_label("Nothing was pasted")

                self._load_directory()
                return False

            GLib.idle_add(update_ui)

        threading.Thread(target=do_paste, daemon=True).start()