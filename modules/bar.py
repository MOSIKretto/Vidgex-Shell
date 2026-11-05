import random
import weakref

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import GLib, GtkLayerShell

from fabric.hyprland.widgets import HyprlandLanguage, get_hyprland_connection
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.datetime import DateTime
from fabric.widgets.eventbox import EventBox
from fabric.widgets.label import Label
from fabric.widgets.box import Box

from modules.Bar.quickPanel import QuickPanel
from modules.Bar.workspaces import SideBarWindow, AxisWindow

from services.corners import MyCorner
from services.wayland import WaylandWindow as Window


_GLITCH_CLASSES = ("glitch-aberration", "glitch-color-swap")
_GLITCH_FRAMES = 6
_GLITCH_FRAME_MS = 35
_CLOCK_WIDTH_PX = 46
_LANG_WIDTH_PX = 32


class _GlitchFX:
    __slots__ = ("_widget", "_rem", "_timer_id")

    def __init__(self, widget):
        self._widget = widget
        self._rem = 0
        self._timer_id = 0

    def trigger(self) -> None:
        self._rem = _GLITCH_FRAMES
        if not self._timer_id:
            self._timer_id = GLib.timeout_add(_GLITCH_FRAME_MS, self._tick)

    def _clear_classes(self) -> None:
        ctx = self._widget.get_style_context()
        for cls in _GLITCH_CLASSES:
            ctx.remove_class(cls)
        ctx.remove_class("glitching")

    def _tick(self) -> bool:
        self._clear_classes()
        if self._rem == 0:
            self._timer_id = 0
            return False
        self._rem -= 1
        ctx = self._widget.get_style_context()
        ctx.add_class("glitching")
        for cls in random.sample(_GLITCH_CLASSES, random.randint(1, 2)):
            ctx.add_class(cls)
        return True

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        self._clear_classes()


class Bar(Window):
    def __init__(self, monitor_id=0, **kwargs):
        super().__init__(exclusivity=36, monitor_id=monitor_id)
        self.mid = monitor_id

        if GtkLayerShell.is_layer_window(self):
            GtkLayerShell.set_exclusive_zone(self, 36)

        notch = kwargs.get("notch")
        self._notch_ref = weakref.ref(notch) if notch else None

        self.anchor = "left top right"
        self.margin = "0px -8px 0px -8px"

        self.conn = get_hyprland_connection()
        self.lang = HyprlandLanguage()
        self._last_lang = ""
        self._last_time = ""
        self._lang_sig_id = 0

        self.sidebar = SideBarWindow(conn=self.conn, monitor_id=self.mid)
        self.sidebar.show_all()

        self.quick_panel = QuickPanel(monitor=self.mid)

        self._build()

        self.dt.connect("notify::label", self._dchg)
        self._lang_sig_id = self.lang.connect("notify::label", self._lchg)
        self._lchg()

        self.connect("destroy", self._on_destroy)

    def _on_destroy(self, *_):
        self.cleanup()

    @property
    def notch(self):
        return self._notch_ref() if self._notch_ref else None

    @notch.setter
    def notch(self, value):
        self._notch_ref = weakref.ref(value) if value else None

    def _build(self):
        self.top_window = AxisWindow(conn=self.conn, monitor_id=self.mid)
        self.ws = self.top_window.ws

        self.dt = DateTime(name="date-time", formatters=["%H:%M"], v_align="center")
        self.dt.set_size_request(_CLOCK_WIDTH_PX, -1)
        self._clock_glitch = _GlitchFX(self.dt)

        self.ll = Label(name="lang-label", xalign=0.5, v_align="center")
        self.ll.set_size_request(_LANG_WIDTH_PX, -1)
        self._lang_glitch = _GlitchFX(self.ll)

        self.lang_eb = EventBox(
            v_align="center",
            child=Box(
                name="HyprlandLanguage-indicator",
                v_align="center",
                spacing=0,
                children=[self.ll],
            ),
        )

        island_group = Box(
            name="bar-group-left",
            orientation="h",
            spacing=0,
            v_align="start",
            children=[
                self.top_window.island,
                self.top_window.cap,
                self.top_window.corner_static,
            ],
        )

        power_battery_container = Box(
            name="power-battery-container",
            spacing=4,
            v_align="fill",
            children=[
                self.dt, self.lang_eb,
                self.quick_panel.net_btn, self.quick_panel.battery_btn,
                self.quick_panel.util_btn, self.quick_panel.session_btn,
            ],
        )

        self.quick_panel.bind_width_to(power_battery_container)

        bar_group_right_top = Box(
            name="bar-group-right-top",
            orientation="h",
            spacing=0,
            v_align="start",
            children=[
                Box(
                    name="bar-right-corner",
                    orientation="v",
                    v_align="start",
                    h_align="end",
                    children=[MyCorner("top-right")],
                ),
                power_battery_container,
            ],
        )

        right_group = Box(
            name="bar-group-right",
            orientation="v",
            spacing=0,
            v_align="start",
            children=[
                bar_group_right_top,
                Box(
                    name="bar-right-bottom-corner",
                    orientation="v",
                    v_align="start",
                    h_align="end",
                    children=[MyCorner("top-right")],
                ),
            ],
        )

        self.nb = CenterBox(
            name="bar-inner",
            v_align="start",
            start_children=island_group,
            end_children=right_group,
        )

        self.add(self.nb)

    def _lchg(self, *_):
        raw = self.lang.get_label()
        short = raw[:2].upper()
        if short == self._last_lang:
            return
        changed = bool(self._last_lang)
        self._last_lang = short
        self.ll.set_label(short)
        if changed:
            self._lang_glitch.trigger()

    def _dchg(self, *_):
        text = self.dt.get_label()
        if text == self._last_time:
            return
        changed = bool(self._last_time)
        self._last_time = text
        if changed:
            self._clock_glitch.trigger()

    def cleanup(self):
        if self._lang_sig_id:
            self.lang.disconnect(self._lang_sig_id)
            self._lang_sig_id = 0

        self._clock_glitch.stop()
        self._lang_glitch.stop()

        if self.top_window is not None:
            self.top_window.destroy()
            self.top_window = None

        if self.sidebar is not None:
            self.sidebar.destroy()
            self.sidebar = None

        if self.quick_panel is not None:
            self.quick_panel.cleanup()
            self.quick_panel.destroy()
            self.quick_panel = None

        self._notch_ref = None
        self.conn = None
        self.lang = None