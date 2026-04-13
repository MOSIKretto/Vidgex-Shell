from fabric.utils.helpers import exec_shell_command_async
from fabric.widgets.box import Box
from fabric.widgets.label import Label
from fabric.widgets.button import Button
from fabric.widgets.revealer import Revealer

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib

from services.wayland import WaylandWindow as Window
import services.icons as icons


class PowerMenu(Window):
    _ACTIONS = (
        (icons.shutdown, "Shutdown", "systemctl poweroff"),
        (icons.reboot,   "Reboot",   "systemctl reboot"),
        (icons.logout,   "Logout",   "hyprctl dispatch exit"),
        (icons.suspend,  "Suspend",  "systemctl suspend"),
    )
    
    REVEAL_STEP_DELAY = 45
    CLOSE_HIDE_DELAY = 250
    REVEALER_DURATION = 200
    
    MARGIN_RIGHT_OFFSET = 15
    MARGIN_TOP_OFFSET = 50
    BUTTON_SPACING = 4
    
    CSS_POWER_ICON_BTN = "power-icon-btn"
    CSS_POWER_ACTIVE = "power-active"
    
    WINDOW_NAME = "power-menu-window"
    LAYER_RULE_CMD = 'hyprctl keyword layerrule "noanim, {}"'

    def __init__(self, monitor=0, **kwargs):
        super().__init__(
            exclusivity="none",
            layer="top",
            monitor=monitor,
            keyboard_mode="none",
        )
        self.set_name(self.WINDOW_NAME)
        self.anchor = "top right"
        self.margin = "0px 0px 0px 0px"

        self._setup_transparency()
        self._init_state()
        self._build_ui()
        
        self.set_visible(False)
        self._disable_animations()

    def _setup_transparency(self):
        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_app_paintable(True)
            self.set_visual(visual)

    def _init_state(self):
        self._open_flag = False
        self._close_timer = None
        self._reveal_timer = None
        
        self._trigger_btn = None
        self._trigger_sig = None
        
        self._cursors = {"hand": None, "default": None}
        
        self._btns = []
        self._revealers = []

    def _disable_animations(self):
        cmd = self.LAYER_RULE_CMD.format(self.WINDOW_NAME)
        exec_shell_command_async(cmd)

    def _ensure_cursors(self):
        if self._cursors["hand"] is None:
            display = self.get_display()
            self._cursors["hand"] = Gdk.Cursor.new_from_name(display, "pointer")
            self._cursors["default"] = Gdk.Cursor.new_from_name(display, "default")

    def _set_window_cursor(self, cursor_name):
        self._ensure_cursors()
        cursor = self._cursors.get(cursor_name)
        if cursor:
            toplevel = self.get_toplevel()
            if toplevel:
                window = toplevel.get_window()
                if window:
                    window.set_cursor(cursor)

    def _on_btn_enter(self, widget, event):
        if event.detail != Gdk.NotifyType.INFERIOR:
            self._set_window_cursor("hand")
        return False

    def _on_btn_leave(self, widget, event):
        if event.detail != Gdk.NotifyType.INFERIOR:
            self._set_window_cursor("default")
        return False

    def _create_action_button(self, icon_markup, tooltip, cmd):
        btn = Button(
            child=Label(markup=icon_markup),
            tooltip_markup=tooltip,
            can_focus=False,
        )
        btn.get_style_context().add_class(self.CSS_POWER_ICON_BTN)
        btn.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        btn.connect("enter-notify-event", self._on_btn_enter)
        btn.connect("leave-notify-event", self._on_btn_leave)
        btn.connect("clicked", self._on_action_clicked, cmd)
        
        revealer = Revealer(
            transition_type="slide-down",
            child_revealed=False,
            child=btn,
        )
        revealer.set_transition_duration(self.REVEALER_DURATION)
        
        return btn, revealer

    def _build_ui(self):
        icons_box = Box(
            name="power-menu-icons",
            orientation="v",
            spacing=self.BUTTON_SPACING
        )

        for icon_markup, tooltip, cmd in self._ACTIONS:
            btn, revealer = self._create_action_button(icon_markup, tooltip, cmd)
            self._btns.append(btn)
            self._revealers.append(revealer)
            icons_box.add(revealer)

        self._wrapper = Box(
            name="power-menu-wrapper",
            orientation="v",
            children=[icons_box],
        )
        self.add(self._wrapper)

    def set_trigger_button(self, btn):
        self._disconnect_trigger()
        self._trigger_btn = btn
        self._trigger_sig = btn.connect("size-allocate", self._on_trigger_size_allocate)

    def _disconnect_trigger(self):
        if self._trigger_btn and self._trigger_sig:
            self._trigger_btn.disconnect(self._trigger_sig)
            self._trigger_sig = None

    def _on_trigger_size_allocate(self, widget, allocation):
        width, height = allocation.width, allocation.height
        if width <= 0 or height <= 0:
            return

        self._update_button_sizes(width, height)
        self._update_wrapper_size(width, height)
        self._update_window_position(widget, width, height)

    def _update_button_sizes(self, width, height):
        for btn in self._btns:
            btn.set_size_request(width, height)

    def _update_wrapper_size(self, width, height):
        num_actions = len(self._ACTIONS)
        total_spacing = self.BUTTON_SPACING * (num_actions - 1)
        total_height = height * num_actions + total_spacing
        self._wrapper.set_size_request(width, total_height)

    def _update_window_position(self, widget, width, height):
        toplevel = widget.get_toplevel()
        if not toplevel:
            return

        coords = widget.translate_coordinates(toplevel, 0, 0)
        if coords is None:
            return
        
        x, y = coords
        toplevel_width = toplevel.get_allocated_width()

        margin_right = max(0, toplevel_width - x - width - self.MARGIN_RIGHT_OFFSET)
        margin_top = max(0, y + height + self.BUTTON_SPACING - self.MARGIN_TOP_OFFSET)

        self.margin = f"{margin_top}px {margin_right}px 0px 0px"

    def open(self):
        if self._open_flag:
            return
        
        self._cancel_timers()
        self._open_flag = True
        self._set_trigger_danger_state(True)

        self._reset_revealers()
        self.set_visible(True)
        self.show_all()
        self._start_reveal_animation()

    def _reset_revealers(self):
        for revealer in self._revealers:
            revealer.set_reveal_child(False)

    def _start_reveal_animation(self):
        self._reveal_step(0)

    def _reveal_step(self, index):
        self._reveal_timer = None
        
        if not self._open_flag or index >= len(self._revealers):
            return False
        
        self._revealers[index].set_reveal_child(True)
        
        if index + 1 < len(self._revealers):
            self._reveal_timer = GLib.timeout_add(
                self.REVEAL_STEP_DELAY,
                self._reveal_step,
                index + 1
            )
        
        return False

    def close(self):
        if not self._open_flag:
            return
        
        self._cancel_timers()
        self._open_flag = False
        self._set_trigger_danger_state(False)

        for revealer in self._revealers:
            revealer.set_reveal_child(False)

        self._close_timer = GLib.timeout_add(self.CLOSE_HIDE_DELAY, self._hide_window)

    def _hide_window(self):
        self._close_timer = None
        if not self._open_flag:
            self.set_visible(False)
        return False

    def _cancel_timers(self):
        if self._close_timer is not None:
            GLib.source_remove(self._close_timer)
            self._close_timer = None
        
        if self._reveal_timer is not None:
            GLib.source_remove(self._reveal_timer)
            self._reveal_timer = None

    def _set_trigger_danger_state(self, active):
        if not self._trigger_btn:
            return
        
        style_context = self._trigger_btn.get_style_context()
        if active:
            style_context.add_class(self.CSS_POWER_ACTIVE)
        else:
            style_context.remove_class(self.CSS_POWER_ACTIVE)

    def is_open(self):
        return self._open_flag

    def _on_action_clicked(self, button, cmd):
        self.close()
        exec_shell_command_async(cmd)

    def cleanup(self):
        self._cancel_timers()
        self._set_trigger_danger_state(False)
        
        if self._open_flag:
            self._open_flag = False
            self.set_visible(False)
        
        self._disconnect_trigger()
        self._trigger_btn = None