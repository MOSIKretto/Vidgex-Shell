import datetime
import random
import re

from fabric.widgets.box import Box
from fabric.widgets.label import Label
from gi.repository import GLib


_GLYPHS = {
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
}

_GL_FRAMES = 14
_GL_FRAME_MS = 35
_GL_RAND_MIN = 1
_GL_RAND_MAX = 60
_GL_REPEAT_CHANCE = 0.5

_ESCAPE_MAP = str.maketrans({'&': '&amp;', '<': '&lt;', '>': '&gt;'})
_BOLD_RE = re.compile(r'([^ ]+)')

_GLITCH_CLASSES = [
    "glitch-shift-right",
    "glitch-shift-left",
    "glitch-flicker",
    "glitch-aberration",
    "glitch-heavy",
    "glitch-color-swap",
]


def _render_time(text, colon_visible=True):
    glyphs = [
        _GLYPHS[':' if colon_visible else ':_off'] if ch == ':'
        else _GLYPHS.get(ch, _GLYPHS[':'])
        for ch in text
    ]
    return [" ".join(row) for row in zip(*glyphs)]


def _rows_to_markup(rows):
    lines = []
    for row in rows:
        escaped = row.translate(_ESCAPE_MAP)
        lines.append(_BOLD_RE.sub(r'<b>\1</b>', escaped))
    return '\n'.join(lines)


class TimeWidget(Box):
    __slots__ = (
        '_time_lbl', '_date_lbl', '_tid', '_last_min', '_colon_on',
        '_prev_time_str', '_gl_active', '_gl_rem', '_gl_total',
        '_gl_tid', '_gl_rand_tid',
    )

    def __init__(self, **kwargs):
        super().__init__(
            name="time-widget", orientation="v", spacing=1,
            h_align="center", v_align="center",
            h_expand=False, v_expand=True, **kwargs,
        )

        self._time_lbl = Label(name="time-label", label="", h_align="center")
        self._date_lbl = Label(name="date-label", label="", h_align="center")
        self._last_min = -1
        self._colon_on = True
        self._prev_time_str = ""

        self._gl_active = False
        self._gl_rem = 0
        self._gl_total = _GL_FRAMES
        self._gl_tid = None
        self._gl_rand_tid = None

        self.add(self._time_lbl)
        self.add(self._date_lbl)

        self._update()
        self._tid = GLib.timeout_add_seconds(1, self._update)
        self._schedule_random_glitch()

    def _set_art(self, rows):
        self._time_lbl.set_markup(_rows_to_markup(rows))

    def _update(self):
        now = datetime.datetime.now()
        m = now.minute + now.hour * 60
        self._colon_on = not self._colon_on
        time_str = now.strftime("%H:%M")

        if (self._prev_time_str
                and time_str != self._prev_time_str
                and not self._gl_active):
            self._start_glitch()

        self._prev_time_str = time_str

        if not self._gl_active:
            self._set_art(_render_time(time_str, self._colon_on))

        if m != self._last_min:
            self._last_min = m
            self._date_lbl.set_label(now.strftime("%A, %B %d"))

        return True

    def _schedule_random_glitch(self):
        delay = random.randint(_GL_RAND_MIN, _GL_RAND_MAX)
        self._gl_rand_tid = GLib.timeout_add_seconds(
            delay, self._fire_random_glitch,
        )

    def _fire_random_glitch(self):
        self._gl_rand_tid = None
        if not self._gl_active:
            self._start_glitch()
        self._schedule_random_glitch()
        return False

    def _start_glitch(self):
        self._gl_rem = _GL_FRAMES
        self._gl_total = _GL_FRAMES
        self._gl_active = True

        if self._gl_tid:
            GLib.source_remove(self._gl_tid)
        self._gl_tid = GLib.timeout_add(_GL_FRAME_MS, self._gl_tick)

    def _clear_glitch(self):
        ctx = self._time_lbl.get_style_context()
        for cls in _GLITCH_CLASSES:
            ctx.remove_class(cls)
        self.get_style_context().remove_class("glitching")

    def _gl_tick(self):
        time_str = datetime.datetime.now().strftime("%H:%M")
        progress = 1.0 - self._gl_rem / self._gl_total

        self._set_art(_render_time(time_str, self._colon_on))
        self._clear_glitch()

        # вероятность уменьшается → глитч затухает
        if random.random() > progress:
            self.get_style_context().add_class("glitching")
            # 1–2 эффекта одновременно для плотности
            count = 1 if random.random() > 0.4 else 2
            for cls in random.sample(_GLITCH_CLASSES, count):
                self._time_lbl.get_style_context().add_class(cls)

        self._gl_rem -= 1

        if self._gl_rem <= 0:
            self._gl_active = False
            self._gl_tid = None
            self._clear_glitch()
            self._set_art(_render_time(time_str, self._colon_on))

            if random.random() < _GL_REPEAT_CHANCE:
                self._start_glitch()
            return False

        return True

    def cleanup(self):
        for attr in ('_tid', '_gl_tid', '_gl_rand_tid'):
            tid = getattr(self, attr, None)
            if tid:
                GLib.source_remove(tid)
                setattr(self, attr, None)
        self._time_lbl = None
        self._date_lbl = None