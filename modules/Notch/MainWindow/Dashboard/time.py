import datetime
import random

from fabric.widgets.box import Box
from fabric.widgets.label import Label
from gi.repository import GLib, Pango


_DIGIT_H = 5

_GLYPHS = {
    '0': [" /&@\\ ",
          "|#  @|",
          "|@  #|",
          "|!  @|",
          " \\@&/ "],

    '1': ["  /#  ",
          " /!@  ",
          "   @  ",
          "   #  ",
          " &#@##"],

    '2': [" /&@\\ ",
          "    #|",
          " /&@/ ",
          "|!    ",
          " \\@&/ "],

    '3': [" /&@\\ ",
          "    #|",
          "  $#/ ",
          "    @|",
          " \\@&/ "],

    '4': ["|\  #|",
          "|!\ @|",
          " \\@&@!",
          "    #|",
          "    @|"],

    '5': [" /&@&!",
          "|!    ",
          " \\@&\\ ",
          "    @|",
          " \\@&/ "],

    '6': [" /&@\\ ",
          "|!    ",
          "|@&$\\ ",
          "|#  @|",
          " \\@&/ "],

    '7': ["|&@#@|",
          "    #/",
          "   $/ ",
          "  @/  ",
          " #/   "],

    '8': [" /&@\\ ",
          "|#  @|",
          " \\$&/ ",
          "|@  #|",
          " \\@&/ "],

    '9': [" /&@\\ ",
          "|#  @|",
          " \\@&#|",
          "    @|",
          " \\@&/ "],

    ':': ["  ",
          "$#",
          "  ",
          "#$",
          "  "],

    ':_off': ["  ",
              "  ",
              "  ",
              "  ",
              "  "],
}

_FONT_PT = 7

_GL_CHARS = "░▒▓█▀▄▌▐@#$%&!?*=~"
_GL_FRAMES = 14
_GL_FRAME_MS = 35
_GL_CORRUPT_MAX = 0.85
_GL_BLEED = 0.12
_GL_FLICKER = 0.10
_GL_SHIFT_CHANCE = 0.30
_GL_SHIFT_MAX = 3

_GL_RAND_MIN = 1
_GL_RAND_MAX = 60

_GL_REPEAT_CHANCE = 0.5


def _char_col_ranges(text):
    ranges = {}
    col = 0
    first = True
    for idx, ch in enumerate(text):
        g = _GLYPHS.get(':' if ch == ':' else ch)
        if g is None:
            continue
        if not first:
            col += 1
        w = len(g[0])
        ranges[idx] = (col, col + w)
        col += w
        first = False
    return ranges


def _render_time(text, colon_visible=True):
    rows = [''] * _DIGIT_H
    first = True
    for ch in text:
        if ch == ':':
            g = _GLYPHS[':' if colon_visible else ':_off']
        else:
            g = _GLYPHS.get(ch)
        if g is None:
            continue
        for i in range(_DIGIT_H):
            if not first:
                rows[i] += ' '
            rows[i] += g[i]
        first = False
    return rows


def _apply_glitch(rows, changed_ranges, progress):
    rate = _GL_CORRUPT_MAX * (1.0 - progress) ** 1.5
    result = []

    for row in rows:
        if random.random() < _GL_FLICKER * (1.0 - progress):
            result.append(' ' * len(row))
            continue

        chars = list(row)
        w = len(chars)

        for j in range(w):
            in_zone = any(s <= j < e for s, e in changed_ranges)
            r = rate if in_zone else rate * _GL_BLEED
            if random.random() < r:
                if chars[j] != ' ' or random.random() < 0.3:
                    chars[j] = random.choice(_GL_CHARS)

        line = ''.join(chars)

        if random.random() < _GL_SHIFT_CHANCE * (1.0 - progress):
            shift = random.randint(-_GL_SHIFT_MAX, _GL_SHIFT_MAX)
            if shift > 0:
                line = ' ' * shift + line[:w - shift]
            elif shift < 0:
                line = line[-shift:] + ' ' * (-shift)

        result.append(line)

    return result


def _escape(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _rows_to_bold_markup(rows):
    """Каждый непробельный символ оборачивается в <b>."""
    markup_lines = []
    for row in rows:
        parts = []
        for ch in row:
            esc = _escape(ch)
            if ch != ' ':
                parts.append(f'<b>{esc}</b>')
            else:
                parts.append(esc)
        markup_lines.append(''.join(parts))
    return '\n'.join(markup_lines)


class TimeWidget(Box):
    __slots__ = (
        '_time_lbl', '_date_lbl', '_tid', '_last_min', '_colon_on',
        '_prev_time_str',
        '_gl_active', '_gl_rem', '_gl_total', '_gl_tid', '_gl_ranges',
        '_gl_rand_tid',
        '_gl_is_random',
        '_font_str',
    )

    def __init__(self, **kwargs):
        super().__init__(
            name="time-widget",
            orientation="v",
            spacing=1,
            h_align="center",
            v_align="center",
            h_expand=False,
            v_expand=True,
            **kwargs,
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
        self._gl_ranges = []
        self._gl_rand_tid = None
        self._gl_is_random = False

        font = Pango.FontDescription.from_string("monospace")
        font.set_size(_FONT_PT * Pango.SCALE)
        self._font_str = font.to_string()

        self.add(self._time_lbl)
        self.add(self._date_lbl)

        self._update()
        self._tid = GLib.timeout_add_seconds(1, self._update)
        self._schedule_random_glitch()

    def _set_art(self, rows):
        inner = _rows_to_bold_markup(rows)
        self._time_lbl.set_markup(
            f'<span font_desc="{self._font_str}">{inner}</span>'
        )

    def _update(self):
        now = datetime.datetime.now()
        m = now.minute + now.hour * 60
        self._colon_on = not self._colon_on

        time_str = now.strftime("%H:%M")

        if self._prev_time_str and time_str != self._prev_time_str:
            changed = {
                i for i, (o, n)
                in enumerate(zip(self._prev_time_str, time_str))
                if o != n and o != ':'
            }
            if changed:
                self._gl_is_random = False
                self._start_glitch(time_str, changed)

        self._prev_time_str = time_str

        if not self._gl_active:
            self._set_art(_render_time(time_str, self._colon_on))

        if m != self._last_min:
            self._last_min = m
            self._date_lbl.set_label(now.strftime("%A, %B %d"))

        return True

    def _schedule_random_glitch(self):
        delay = random.randint(_GL_RAND_MIN, _GL_RAND_MAX)
        self._gl_rand_tid = GLib.timeout_add_seconds(delay, self._fire_random_glitch)

    def _fire_random_glitch(self):
        self._gl_rand_tid = None

        if not self._gl_active:
            time_str = self._prev_time_str or datetime.datetime.now().strftime("%H:%M")
            digit_indices = [i for i, ch in enumerate(time_str) if ch.isdigit()]

            count = random.randint(1, max(1, len(digit_indices)))
            chosen = set(random.sample(digit_indices, count))
            self._gl_is_random = True
            self._start_glitch(time_str, chosen)

        self._schedule_random_glitch()
        return False

    def _start_glitch(self, time_str, changed_indices):
        col_map = _char_col_ranges(time_str)
        self._gl_ranges = [
            col_map[i] for i in changed_indices if i in col_map
        ]
        if not self._gl_ranges:
            return

        self._gl_total = _GL_FRAMES
        self._gl_rem = _GL_FRAMES
        self._gl_active = True

        if self._gl_tid:
            GLib.source_remove(self._gl_tid)
        self._gl_tid = GLib.timeout_add(_GL_FRAME_MS, self._gl_tick)

    def _gl_tick(self):
        now = datetime.datetime.now()
        time_str = now.strftime("%H:%M")

        progress = 1.0 - self._gl_rem / self._gl_total
        rows = _render_time(time_str, self._colon_on)
        self._set_art(_apply_glitch(rows, self._gl_ranges, progress))

        self._gl_rem -= 1
        if self._gl_rem <= 0:
            self._gl_active = False
            self._gl_tid = None
            self._set_art(_render_time(time_str, self._colon_on))

            if self._gl_is_random and random.random() < _GL_REPEAT_CHANCE:
                digit_indices = [i for i, ch in enumerate(time_str) if ch.isdigit()]
                count = random.randint(1, max(1, len(digit_indices)))
                chosen = set(random.sample(digit_indices, count))
                self._start_glitch(time_str, chosen)

            return False

        return True

    def cleanup(self):
        for attr in ('_tid', '_gl_tid', '_gl_rand_tid'):
            tid = getattr(self, attr, None)
            if tid:
                GLib.source_remove(tid)
                setattr(self, attr, None)