import datetime
import random
import re

from fabric.widgets.box import Box
from fabric.widgets.label import Label
from gi.repository import GLib


class TimeWidget(Box):
    _CLASSES = [
        "glitch-shift-right", "glitch-shift-left", "glitch-flicker",
        "glitch-aberration", "glitch-heavy", "glitch-color-swap",
    ]

    _GLYPHS = {
        k: [
            re.sub(r'([^ ]+)', r'<b>\1</b>', r.translate(str.maketrans({'&': '&amp;', '<': '&lt;', '>': '&gt;'})))
            for r in rows
        ]
        for k, rows in {
            '0': [" /&@\\ ", "|#  @|", "|@  #|", "|!  @|", " \\@&/ "],
            '1': ["  /#  ", " /!@  ", "   @  ", "   #  ", " &#@##"],
            '2': [" /&@\\ ", "    #|", " /&@/ ", "|!    ", " \\@&/ "],
            '3': [" /&@\\ ", "    #|", "  $#/ ", "    @|", " \\@&/ "],
            '4': ["|\\  #|", "|!\\ @|", " \\@&@!", "    #|", "    @|"],
            '5': [" /&@&!", "|!    ", " \\@&\\ ", "    @|", " \\@&/ "],
            '6': [" /&@\\ ", "|!    ", "|@&$\\ ", "|#  @|", " \\@&/ "],
            '7': ["|&@#@|", "    #/", "   $/ ", "  @/  ", " #/   "],
            '8': [" /&@\\ ", "|#  @|", " \\$&/ ", "|@  #|", " \\@&/ "],
            '9': [" /&@\\ ", "|#  @|", " \\@&#|", "    @|", " \\@&/ "],
            ':': ["  ", "$#", "  ", "#$", "  "],
            ':_off': ["  ", "  ", "  ", "  ", "  "],
        }.items()
    }

    def __init__(self, **kwargs):
        super().__init__(
            name="time-widget", orientation="v", spacing=1,
            h_align="center", v_align="center",
            h_expand=False, v_expand=True, **kwargs,
        )
        self._time_lbl = Label(name="time-label", h_align="center")
        self._date_lbl = Label(name="date-label", h_align="center")
        self.add(self._time_lbl)
        self.add(self._date_lbl)

        self._lbl_ctx = self._time_lbl.get_style_context()

        self._destroyed = False
        self._last_min = -1
        self._colon_on = True
        self._prev_time_str = ""
        self._gl_active = False
        self._gl_rem = 0
        self._gl_tid = None

        self.connect("destroy", lambda _: self.cleanup())

        self._update()
        self._tid = GLib.timeout_add_seconds(1, self._update)

    @classmethod
    def _get_markup(cls, text: str, colon: bool) -> str:
        c_key = ':' if colon else ':_off'
        glyphs = [cls._GLYPHS[c_key] if c == ':' else cls._GLYPHS[c] for c in text]
        return "\n".join(" ".join(row) for row in zip(*glyphs))

    def _update(self) -> bool:
        if self._destroyed:
            return False

        now = datetime.datetime.now()
        time_str = now.strftime("%H:%M")
        self._colon_on = not self._colon_on

        if self._prev_time_str and time_str != self._prev_time_str and not self._gl_active:
            self._start_glitch()

        self._prev_time_str = time_str

        if not self._gl_active:
            self._time_lbl.set_markup(self._get_markup(time_str, self._colon_on))

        if now.minute != self._last_min:
            self._last_min = now.minute
            self._date_lbl.set_label(now.strftime("%A, %B %d"))

        return True

    def _start_glitch(self) -> None:
        self._gl_rem = 7
        self._gl_active = True
        self._gl_tid = GLib.timeout_add(40, self._gl_tick)

    def _clear_glitch(self) -> None:
        for cls in self._CLASSES:
            self._lbl_ctx.remove_class(cls)

    def _gl_tick(self) -> bool:
        if self._destroyed:
            return False

        self._clear_glitch()

        if random.random() > (1.0 - self._gl_rem / 7):
            count = 1 if random.random() > 0.4 else 2
            for cls in random.sample(self._CLASSES, count):
                self._lbl_ctx.add_class(cls)

        self._gl_rem -= 1
        if self._gl_rem <= 0:
            self._gl_active = False
            self._gl_tid = None
            self._clear_glitch()
            self._time_lbl.set_markup(self._get_markup(self._prev_time_str, self._colon_on))
            return False

        return True

    def cleanup(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        for tid in (self._tid, self._gl_tid):
            if tid:
                GLib.source_remove(tid)
        self._tid = self._gl_tid = None