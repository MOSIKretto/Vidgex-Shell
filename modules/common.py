from fabric.widgets.box import Box
from gi.repository import Gdk, GLib
import subprocess
import time
import atexit

_process_check_cache = {}
_PROCESS_CACHE_MAX_AGE = 2.0
_PROCESS_CACHE_MAX_SIZE = 20
_PROCESS_CACHE_TIMEOUT = 1.0


def _cleanup_process_cache():
    global _process_check_cache
    _process_check_cache.clear()

atexit.register(_cleanup_process_cache)


def add_hover_cursor(widget):
    widget.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
    
    def set_cursor_on_enter(widget, event):
        window = widget.get_window()
        if window:
            cursor = Gdk.Cursor.new_from_name(widget.get_display(), "pointer")
            window.set_cursor(cursor)
    
    def clear_cursor_on_leave(widget, event):
        window = widget.get_window()
        if window:
            window.set_cursor(None)
    
    widget.connect("enter-notify-event", set_cursor_on_enter)
    widget.connect("leave-notify-event", clear_cursor_on_leave)


def check_process_running(pattern, use_cache=True):
    global _process_check_cache
    
    current_time = time.time()
    
    if use_cache and pattern in _process_check_cache:
        result, timestamp = _process_check_cache[pattern]
        if current_time - timestamp < _PROCESS_CACHE_TIMEOUT:
            return result
    
    try:
        result = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True,
            timeout=2.0
        )
        is_running = result.returncode == 0
    except (subprocess.TimeoutExpired, Exception):
        is_running = False
    
    _process_check_cache[pattern] = (is_running, current_time)
    
    if len(_process_check_cache) > _PROCESS_CACHE_MAX_SIZE:
        expired_keys = [
            k for k, (_, ts) in _process_check_cache.items()
            if current_time - ts > _PROCESS_CACHE_MAX_AGE
        ]
        for key in expired_keys:
            _process_check_cache.pop(key, None)
        
        if len(_process_check_cache) > _PROCESS_CACHE_MAX_SIZE:
            keys_to_remove = sorted(
                _process_check_cache.items(),
                key=lambda x: x[1][1]
            )[:len(_process_check_cache) - _PROCESS_CACHE_MAX_SIZE]
            for key, _ in keys_to_remove:
                _process_check_cache.pop(key, None)
    
    return is_running

def run_in_thread(func, callback=None):
    def worker(user_data):
        result = func()
        if callback:
            GLib.idle_add(callback, result)
    GLib.Thread.new(None, worker, None)

def create_status_layout(icon, title, status, spacing=10):
    title_box = Box(children=[title, Box(h_expand=True)])
    status_box = Box(children=[status, Box(h_expand=True)])
    text_box = Box(
        orientation="v",
        h_align="start",
        v_align="center",
        children=[title_box, status_box],
    )
    return Box(
        h_align="start",
        v_align="center",
        spacing=spacing,
        children=[icon, text_box],
    )
