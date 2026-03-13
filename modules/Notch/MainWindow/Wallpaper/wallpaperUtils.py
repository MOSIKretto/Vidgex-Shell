import hashlib
import random

import cairo
from gi.repository import Gdk, Gtk

from modules.Notch.MainWindow.Wallpaper.wallpaperConstants import (
    _AGL_CHARS,
    _AGL_CORRUPT,
    _AGL_FLICKER,
    _AGL_SHIFT_CHANCE,
    _AGL_SHIFT_MAX,
    _ANG,
    _ARR_FONT_STR,
)


# ── Цвет primary из темы ─────────────────────────────────────
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


# ── Рендер ASCII-арта в Label ─────────────────────────────────
def _arr_set_art(lbl, lines):
    art = "\n".join(lines)
    safe = (
        art.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    fg = _get_primary_hex(lbl)
    lbl.set_markup(
        f'<span font_desc="{_ARR_FONT_STR}" foreground="{fg}">{safe}</span>'
    )


# ── Глитч-искажение строк ────────────────────────────────────
def _arr_glitch_lines(lines, progress):
    rate = _AGL_CORRUPT * (1.0 - progress) ** 1.5
    result = []
    for line in lines:
        if random.random() < _AGL_FLICKER * (1.0 - progress):
            result.append(" " * len(line))
            continue

        chars = list(line)
        w = len(chars)
        for j in range(w):
            if random.random() < rate:
                if chars[j] != " " or random.random() < 0.4:
                    chars[j] = random.choice(_AGL_CHARS)
        out = "".join(chars)

        if random.random() < _AGL_SHIFT_CHANCE * (1.0 - progress):
            shift = random.randint(-_AGL_SHIFT_MAX, _AGL_SHIFT_MAX)
            if shift > 0:
                out = " " * shift + out[: w - shift]
            elif shift < 0:
                out = out[-shift:] + " " * (-shift)
        result.append(out)
    return result


# ── Курсоры pointer / default ─────────────────────────────────
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
        pointer, _ = _get_cursors(win.get_display())
        win.set_cursor(pointer)
    return False


def _on_btn_leave(widget, _event):
    win = widget.get_window()
    if win:
        _, default = _get_cursors(win.get_display())
        win.set_cursor(default)
    return False


def _setup_pointer_cursor(widget):
    widget.add_events(
        Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
    )
    widget.connect("enter-notify-event", _on_btn_enter)
    widget.connect("leave-notify-event", _on_btn_leave)


# ── MD5-хеш строки ───────────────────────────────────────────
def _md5hex(s):
    return hashlib.md5(s.encode()).hexdigest()


# ── Скруглённый прямоугольник (cairo path) ────────────────────
def _rpath(c, x, y, w, h, r):
    xr = x + w - r
    yr = y + r
    yhr = y + h - r
    xrr = x + r
    a = _ANG
    c.new_path()
    c.arc(xr, yr, r, a[0], a[1])
    c.arc(xr, yhr, r, a[1], a[2])
    c.arc(xrr, yhr, r, a[2], a[3])
    c.arc(xrr, yr, r, a[3], a[4])
    c.close_path()