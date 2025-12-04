"""
Optimized utility for running subprocess operations asynchronously with thread pool.
"""

from gi.repository import GLib
import subprocess
import atexit
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, List, Optional, Union

# Global thread pool executor with automatic cleanup
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="async_subprocess")
_shutting_down = False


def _cleanup_executor():
    """Cleanup executor on program exit"""
    global _shutting_down
    _shutting_down = True
    _executor.shutdown(wait=False)


# Register cleanup on exit
atexit.register(_cleanup_executor)


def run_async(
    command: Union[str, List[str]],
    on_success: Optional[Callable] = None,
    on_error: Optional[Callable[[Exception], None]] = None,
    capture_output: bool = False
) -> None:
    """
    Run a subprocess command asynchronously using a thread pool.

    Args:
        command: Command to run (string or list)
        on_success: Callback on success (receives output if capture_output=True)
        on_error: Callback on error (receives exception)
        capture_output: Whether to capture and return output
    """
    if _shutting_down:
        return

    def worker():
        if _shutting_down:
            return

        try:
            if capture_output:
                if isinstance(command, str):
                    result = subprocess.run(command, shell=True, capture_output=True, check=True)
                else:
                    result = subprocess.run(command, capture_output=True, check=True)
                if on_success and not _shutting_down:
                    GLib.idle_add(lambda: on_success(result.stdout) if not _shutting_down else False)
            else:
                if isinstance(command, str):
                    subprocess.run(command, shell=True, check=True)
                else:
                    subprocess.run(command, check=True)
                if on_success and not _shutting_down:
                    GLib.idle_add(lambda: on_success() if not _shutting_down else False)
        except Exception as e:
            if on_error and not _shutting_down:
                GLib.idle_add(lambda: on_error(e) if not _shutting_down else False)

    _executor.submit(worker)


def check_process(
    process_name: str,
    on_running: Optional[Callable] = None,
    on_not_running: Optional[Callable] = None
) -> None:
    """Check if a process is running asynchronously using thread pool."""
    if _shutting_down:
        return

    def worker():
        if _shutting_down:
            return

        try:
            subprocess.check_output(["pgrep", process_name])
            if on_running and not _shutting_down:
                GLib.idle_add(lambda: on_running() if not _shutting_down else False)
        except subprocess.CalledProcessError:
            if on_not_running and not _shutting_down:
                GLib.idle_add(lambda: on_not_running() if not _shutting_down else False)

    _executor.submit(worker)


def shutdown():
    """Manually shutdown the thread pool (called automatically on exit)"""
    _cleanup_executor()
