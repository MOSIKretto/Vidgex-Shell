from fabric import Property, Service, Signal

from gi.repository import GLib, Gtk


class Animator(Service):
    """
    Simple animator with fixed 60fps for smooth animations.
    
    Features:
    - Fixed 60fps frame rate
    - Vsync-aware timing for tear-free animations
    - Low priority rendering to not block UI
    """
    
    # Fixed frame rate at 60fps
    FRAME_INTERVAL = 1000 / 60  # ~16.67ms per frame
    
    @Signal
    def finished(self) -> None: ...

    @Property(tuple[float, float, float, float], "read-write")
    def bezier_curve(self) -> tuple[float, float, float, float]:
        return self._bezier_curve

    @bezier_curve.setter
    def bezier_curve(self, value: tuple[float, float, float, float]):
        self._bezier_curve = value

    @Property(float, "read-write")
    def value(self):
        return self._value

    @value.setter
    def value(self, value: float):
        self._value = value

    @Property(float, "read-write")
    def max_value(self):
        return self._max_value

    @max_value.setter
    def max_value(self, value: float):
        self._max_value = value

    @Property(float, "read-write")
    def min_value(self):
        return self._min_value

    @min_value.setter
    def min_value(self, value: float):
        self._min_value = value

    @Property(bool, "read-write", default_value=False)
    def playing(self):
        return self._playing

    @playing.setter
    def playing(self, value: bool):
        self._playing = value

    @Property(bool, "read-write", default_value=False)
    def repeat(self):
        return self._repeat

    @repeat.setter
    def repeat(self, value: bool):
        self._repeat = value

    def __init__(
        self,
        bezier_curve: tuple[float, float, float, float],
        duration: float,
        min_value: float = 0.0,
        max_value: float = 1.0,
        repeat: bool = False,
        tick_widget: Gtk.Widget | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        
        self._bezier_curve = bezier_curve
        self._duration = duration
        self._value = min_value
        self._min_value = min_value
        self._max_value = max_value
        self._repeat = repeat
        self._playing = False
        self._start_time = None
        self._tick_handler = None
        self._timeline_pos = 0
        self._tick_widget = tick_widget

    def do_get_time_now(self):
        """Get current time in seconds with microsecond precision."""
        return GLib.get_monotonic_time() / 1000000

    def do_lerp(self, start: float, end: float, time: float) -> float:
        """Linear interpolation between start and end."""
        return start + (end - start) * time

    def do_interpolate_cubic_bezier(self, time: float) -> float:
        """Cubic bezier interpolation for smooth easing."""
        y1 = self.bezier_curve[1]
        y2 = self.bezier_curve[3]
        
        t_inv = 1 - time
        t_inv_sq = t_inv * t_inv
        t_sq = time * time
        
        return (3 * t_inv_sq * time * y1 + 
                3 * t_inv * t_sq * y2 + 
                t_sq * time)

    def do_ease(self, time: float) -> float:
        """Apply easing function to interpolate between min and max values."""
        bezier_value = self.do_interpolate_cubic_bezier(time)
        return self.do_lerp(self.min_value, self.max_value, bezier_value)

    def do_update_value(self, delta_time: float):
        """Update animation value based on elapsed time."""
        if not self.playing:
            return

        elapsed_time = delta_time - self._start_time
        self._timeline_pos = min(1, elapsed_time / self.duration)
        self.value = self.do_ease(self._timeline_pos)

        if self._timeline_pos < 1:
            return

        if self.repeat:
            self._start_time = delta_time
            self._timeline_pos = 0
        else:
            self.value = self.max_value
            self.emit("finished")
            self.pause()

    def do_handle_tick(self, *_):
        """Handle animation tick at fixed 60fps."""
        current_time = self.do_get_time_now()
        self.do_update_value(current_time)
        return True

    def do_remove_tick_handlers(self):
        """Clean up tick handlers properly."""
        if self._tick_handler is None:
            return

        if self._tick_widget is not None:
            self._tick_widget.remove_tick_callback(self._tick_handler)
        else:
            GLib.source_remove(self._tick_handler)
        
        self._tick_handler = None

    def play(self):
        """Start animation at fixed 60fps."""
        if self.playing:
            return

        self._start_time = self.do_get_time_now()

        if self._tick_handler is not None:
            return

        if self._tick_widget is not None:
            # Use widget tick callback for vsync-aware timing
            self._tick_handler = self._tick_widget.add_tick_callback(
                self.do_handle_tick
            )
        else:
            # Use GLib timeout with fixed 60fps interval
            self._tick_handler = GLib.timeout_add(
                int(self.FRAME_INTERVAL),
                self.do_handle_tick,
                priority=GLib.PRIORITY_DEFAULT_IDLE
            )

        self.playing = True

    def pause(self):
        """Pause animation and clean up handlers."""
        self.playing = False
        self.do_remove_tick_handlers()

    def stop(self):
        """Stop animation, reset state, and clean up handlers."""
        self._timeline_pos = 0
        self.playing = False
        self.do_remove_tick_handlers()
