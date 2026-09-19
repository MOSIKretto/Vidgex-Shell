from gi.repository import GLib


class WorkTime:
    __slots__ = ("update_callback", "state", "remaining", "is_running", "_timer_id", "WORK_TIME")

    def __init__(self, update_callback=None, work_time: int = 3600):
        self.update_callback = update_callback
        self.state = "STOPPED"
        self.WORK_TIME = work_time
        self.remaining = self.WORK_TIME
        self.is_running = False
        self._timer_id = None

    def start(self) -> None:
        if not self.is_running:
            if self.state == "STOPPED":
                self.state = "WORK"
                self.remaining = self.WORK_TIME
            self.is_running = True
            self._timer_id = GLib.timeout_add_seconds(1, self._tick)
            self._notify()

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        self.is_running = False
        self.state = "STOPPED"
        self.remaining = self.WORK_TIME
        self._notify()

    def toggle(self) -> None:
        if self.is_running:
            self.stop()
        else:
            self.start()

    def _tick(self) -> bool:
        if not self.is_running:
            return False

        self.remaining -= 1

        if self.remaining <= 0:
            GLib.spawn_command_line_async(f'notify-send -a "Vidgex-Shell" "Work Time" "Take a break!"')
            self.stop()
            return False

        self._notify()
        return True

    def _notify(self) -> None:
        if self.update_callback:
            self.update_callback(self.get_status_text(), self.is_running)

    def get_status_text(self) -> str:
        if not self.is_running and self.state == "STOPPED":
            return "Disabled"

        mins, secs = divmod(self.remaining, 60)
        return f"Work {mins:02d}:{secs:02d}"