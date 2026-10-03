import hashlib
import os
import traceback
from pathlib import Path
from queue import Queue
from threading import Thread

from gi.repository import GLib


PERSISTENT_DIR = os.path.join(GLib.get_user_cache_dir(), "vidgex-shell", "notifications")
PERSISTENT_HISTORY_FILE = os.path.join(PERSISTENT_DIR, "notification_history.json")
PERSISTENT_IMAGES_DIR = os.path.join(PERSISTENT_DIR, "images")

MAX_NOTIFICATION_HISTORY = 30
MAX_IMAGE_BYTES = 10 * 1024 * 1024

os.makedirs(PERSISTENT_IMAGES_DIR, exist_ok=True)


def get_safe_image_path(uuid) -> str:
    return os.path.join(PERSISTENT_IMAGES_DIR, f"{hashlib.md5(str(uuid).encode()).hexdigest()}.png")


def is_safe_image_file(path: str) -> bool:
    if not path.startswith("/") or not os.path.isfile(path):
        return False
    try:
        size = os.path.getsize(path)
    except OSError:
        # Файл удалён фоновым потоком между isfile() и getsize()
        return False
    return 0 < size < MAX_IMAGE_BYTES


class _IOWorker:
    __slots__ = ("_queue", "_thread")

    def __init__(self):
        self._queue: Queue = Queue()
        self._thread = Thread(target=self._run, daemon=True, name="notif-io")
        self._thread.start()

    def _run(self) -> None:
        while True:
            task = self._queue.get()
            try:
                task()
            except Exception:
                # упавшая задача не должна убивать поток: иначе запись остановится молча
                traceback.print_exc()
            finally:
                self._queue.task_done()

    def submit(self, task) -> None:
        self._queue.put_nowait(task)


_io_worker = _IOWorker()


def submit_io_task(task) -> None:
    _io_worker.submit(task)


def delete_notification_image(uuid) -> None:
    _io_worker.submit(lambda: Path(get_safe_image_path(uuid)).unlink(missing_ok=True))


def clear_all_notification_images() -> None:
    def _op():
        if os.path.isdir(PERSISTENT_IMAGES_DIR):
            for fn in os.listdir(PERSISTENT_IMAGES_DIR):
                Path(PERSISTENT_IMAGES_DIR, fn).unlink(missing_ok=True)
    _io_worker.submit(_op)


def cleanup_orphan_images(active_ids) -> None:
    def _op():
        if not os.path.isdir(PERSISTENT_IMAGES_DIR):
            return
        valid = {f"{hashlib.md5(str(uid).encode()).hexdigest()}.png" for uid in active_ids}
        for fn in os.listdir(PERSISTENT_IMAGES_DIR):
            if fn.endswith(".png") and fn not in valid:
                Path(PERSISTENT_IMAGES_DIR, fn).unlink(missing_ok=True)
    _io_worker.submit(_op)