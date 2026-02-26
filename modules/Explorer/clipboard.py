import os
import shutil
from pathlib import Path
from typing import List


class ClipboardMixin:
    def _copy_to_clipboard(self, paths: List[Path], is_cut: bool = False):
        self._clipboard_paths = [p for p in paths if p.exists()]
        self._clipboard_is_cut = is_cut

        if not self._clipboard_paths:
            self.status_label.set_label("Nothing to copy")
            return

        action = "Cut" if is_cut else "Copied"
        count = len(self._clipboard_paths)
        if count == 1:
            self.status_label.set_label(f"{action}: {self._clipboard_paths[0].name}")
        else:
            self.status_label.set_label(f"{action}: {count} items")

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

        pasted = 0
        errors = 0
        is_cut = self._clipboard_is_cut
        dest_resolved = dest_folder.resolve()

        for src in self._clipboard_paths[:]:
            if not src.exists():
                continue

            if src.is_dir():
                src_resolved = src.resolve()
                if src_resolved == dest_resolved:
                    continue
                try:
                    dest_resolved.relative_to(src_resolved)
                    self.status_label.set_label(f"Cannot paste '{src.name}' into itself")
                    continue
                except ValueError:
                    pass

            dest = self._get_unique_path(dest_folder / src.name)

            try:
                if is_cut:
                    shutil.move(str(src), str(dest))
                else:
                    if src.is_dir():
                        shutil.copytree(str(src), str(dest))
                    else:
                        shutil.copy2(str(src), str(dest))
                pasted += 1
            except PermissionError:
                self.status_label.set_label(f"Permission denied: {src.name}")
                errors += 1
            except Exception as e:
                print(f"Paste error for {src}: {e}")
                errors += 1

        if is_cut and pasted > 0:
            self._clipboard_paths.clear()
            self._clipboard_is_cut = False

        if pasted > 0:
            action = "Moved" if is_cut else "Pasted"
            self.status_label.set_label(f"{action}: {pasted} item(s)")
        elif errors > 0:
            self.status_label.set_label(f"Failed to paste {errors} item(s)")

        self._load_directory()

    def _can_paste(self) -> bool:
        if not self._clipboard_paths:
            return False
        for p in self._clipboard_paths:
            if p.exists():
                return True
        return False