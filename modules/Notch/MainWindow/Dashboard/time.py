import datetime

from fabric.widgets.box import Box
from fabric.widgets.label import Label

from gi.repository import GLib


_DECO = "____"

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
    '_': ["  "] * 5,
}


def _markup(text: str, colon: bool) -> str:
    glyphs = [_GLYPHS[c if colon or c != ':' else '_'] for c in text]
    body = "\n".join(" ".join(row) for row in zip(*glyphs))
    return f"<b>{GLib.markup_escape_text(body)}</b>"


class TimeWidget(Box):
    def __init__(self, **kwargs):
        super().__init__(
            name="time-widget", orientation="v", spacing=1,
            h_align="center", v_align="center",
            h_expand=False, v_expand=True, **kwargs,
        )
        time_l = Label(name="time-label", h_align="center")
        day_l = Label(name="date-label")
        decos = [Label(label=_DECO, name="date-deco") for _ in range(2)]
        self.add(time_l)
        self.add(Box(
            name="date-box", orientation="h", spacing=6, h_align="center",
            children=[decos[0], day_l, decos[1]],
        ))

        self._time, self._day = time_l, day_l

        self._colon = True
        self._shown = ""
        self._wd = None
        self._tid = None

        self.connect("map", self._on_map)
        self.connect("unmap", self._stop)
        self.connect("destroy", self._stop)
        self._update()

    def _on_map(self, *_) -> None:
        if self._tid:
            return
        self._wd = None
        self._update()
        self._tid = GLib.timeout_add_seconds(
            1, self._update, priority=GLib.PRIORITY_DEFAULT_IDLE,
        )

    def _stop(self, *_) -> None:
        if self._tid:
            GLib.source_remove(self._tid)
            self._tid = None

    def _update(self) -> bool:
        now = datetime.datetime.now()
        t, d = now.strftime("%H:%M"), now.strftime("%A")

        self._colon = not self._colon
        m = _markup(t, self._colon)
        if m != self._shown:
            self._shown = m
            self._time.set_markup(m)

        if d != self._wd:
            self._day.set_label(d)
            self._wd = d
        return True