import hashlib
import random

from gi.repository import Gdk, Gtk

from modules.Notch.MainWindow.Wallpaper.wallpaperConstants import (
    _AGL_CHARS, _AGL_CORRUPT, _AGL_FLICKER,
    _AGL_SHIFT_CHANCE, _AGL_SHIFT_MAX, _ANG,
    _ARR_FONT_STR,
)

_md5 = hashlib.md5
_rand = random.random
_choice = random.choice
_randint = random.randint

_HOVER_MASK = Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK

_CMAP = {}


def _get_primary_hex(widget):
    ctx = widget.get_style_context()
    found, rgba = ctx.lookup_color("primary")
    if not found:
        rgba = ctx.get_color(Gtk.StateFlags.NORMAL)
    return (
        f"#{int(rgba.red * 255):02x}"
        f"{int(rgba.green * 255):02x}"
        f"{int(rgba.blue * 255):02x}"
    )

def _arr_set_art(lbl, lines):
    fg = _get_primary_hex(lbl)
    cmap = _CMAP
    ml = []
    ml_ap = ml.append
    for line in lines:
        parts = []
        ap = parts.append
        for ch in line:
            try:
                ap(cmap[ch])
            except KeyError:
                esc = (
                    ch.replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                )
                v = esc if ch == " " else f"<b>{esc}</b>"
                cmap[ch] = v
                ap(v)
        ml_ap("".join(parts))
    inner = "\n".join(ml)
    lbl.set_markup(
        f'<span font_desc="{_ARR_FONT_STR}" foreground="{fg}">{inner}</span>'
    )

def _arr_glitch_lines(lines, progress):
    inv = 1.0 - progress
    rate = _AGL_CORRUPT * inv * inv * inv ** 0.5
    flk = _AGL_FLICKER * inv
    sht = _AGL_SHIFT_CHANCE * inv
    rnd = _rand
    ch = _choice
    ri = _randint
    chars = _AGL_CHARS
    smax = _AGL_SHIFT_MAX
    result = []
    ap = result.append

    for line in lines:
        if rnd() < flk:
            ap(" " * len(line))
            continue

        cs = list(line)
        w = len(cs)
        for j in range(w):
            if rnd() < rate and (cs[j] != " " or rnd() < 0.4):
                cs[j] = ch(chars)
        out = "".join(cs)

        if rnd() < sht:
            shift = ri(-smax, smax)
            if shift > 0:
                out = " " * shift + out[:w - shift]
            elif shift < 0:
                out = out[-shift:] + " " * (-shift)
        ap(out)
    return result

_pointer_cursor = None
_default_cursor = None

def _get_cursors(display):
    global _pointer_cursor, _default_cursor
    if _pointer_cursor is None:
        _pointer_cursor = Gdk.Cursor.new_from_name(display, "pointer")
        _default_cursor = Gdk.Cursor.new_from_name(display, "default")
    return _pointer_cursor, _default_cursor

def _on_btn_enter(widget, _event):
    win = widget.get_window()
    if win:
        win.set_cursor(_get_cursors(win.get_display())[0])
    return False

def _on_btn_leave(widget, _event):
    win = widget.get_window()
    if win:
        win.set_cursor(_get_cursors(win.get_display())[1])
    return False

def _setup_pointer_cursor(widget):
    widget.add_events(_HOVER_MASK)
    widget.connect("enter-notify-event", _on_btn_enter)
    widget.connect("leave-notify-event", _on_btn_leave)

def _md5hex(s):
    return _md5(s.encode()).hexdigest()

def _rpath(c, x, y, w, h, r):
    xr = x + w - r
    yr = y + r
    yhr = y + h - r
    xrr = x + r
    a0, a1, a2, a3, a4 = _ANG
    c.new_path()
    arc = c.arc
    arc(xr, yr, r, a0, a1)
    arc(xr, yhr, r, a1, a2)
    arc(xrr, yhr, r, a2, a3)
    arc(xrr, yr, r, a3, a4)
    c.close_path()