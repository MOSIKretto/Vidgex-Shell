import datetime

from fabric.widgets.box import Box
from fabric.widgets.label import Label
from gi.repository import GLib


class TimeWidget(Box):
    __slots__ = ('_time_lbl', '_date_lbl', '_tid', '_last_min')

    def __init__(self, **kwargs):
        super().__init__(
            name="time-widget",
            orientation="v",
            spacing=2,
            h_align="center",
            v_align="center",
            h_expand=False,
            v_expand=True,
            **kwargs,
        )

        self._time_lbl = Label(name="time-label", label="", h_align="center")
        self._date_lbl = Label(name="date-label", label="", h_align="center")
        self._last_min = -1

        self.add(self._time_lbl)
        self.add(self._date_lbl)

        self._update()
        self._tid = GLib.timeout_add_seconds(1, self._update)

    def _update(self):
        now = datetime.datetime.now()
        m = now.minute + now.hour * 60
        if m != self._last_min:
            self._last_min = m
            self._time_lbl.set_label(now.strftime("%H:%M"))
            self._date_lbl.set_label(now.strftime("%A, %B %d"))
        return True

    def cleanup(self):
        if self._tid:
            GLib.source_remove(self._tid)
            self._tid = None