"""
Simplified utility for running subprocess operations asynchronously.
"""

from gi.repository import GLib
import subprocess
from typing import Callable, List, Optional, Union


def run_async(
    command: Union[str, List[str]], 
    on_success: Optional[Callable] = None,
    on_error: Optional[Callable[[Exception], None]] = None,
    capture_output: bool = False
) -> None:
    """
    Run a subprocess command asynchronously.
    
    Args:
        command: Command to run (string or list)
        on_success: Callback on success (receives output if capture_output=True)
        on_error: Callback on error (receives exception)
        capture_output: Whether to capture and return output
    """
    def worker(user_data):
        try:
            if capture_output:
                if isinstance(command, str):
                    result = subprocess.run(command, shell=True, capture_output=True, check=True)
                else:
                    result = subprocess.run(command, capture_output=True, check=True)
                if on_success:
                    GLib.idle_add(lambda: on_success(result.stdout))
            else:
                if isinstance(command, str):
                    subprocess.run(command, shell=True, check=True)
                else:
                    subprocess.run(command, check=True)
                if on_success:
                    GLib.idle_add(on_success)
        except Exception as e:
            if on_error:
                GLib.idle_add(lambda: on_error(e))
    
    GLib.Thread.new("async-cmd", worker, None)


def check_process(
    process_name: str,
    on_running: Optional[Callable] = None,
    on_not_running: Optional[Callable] = None
) -> None:
    """Check if a process is running asynchronously."""
    def worker(user_data):
        try:
            subprocess.check_output(["pgrep", process_name])
            if on_running:
                GLib.idle_add(on_running)
        except subprocess.CalledProcessError:
            if on_not_running:
                GLib.idle_add(on_not_running)
    
    GLib.Thread.new("check-proc", worker, None)
