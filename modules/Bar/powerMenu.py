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

def _hand_cursor(widget):
    def _set(w, _):
        win = w.get_window()
        if win: win.set_cursor(Gdk.Cursor.new_from_name(w.get_display(), "pointer"))
        return False

    def _reset(w, _):
        win = w.get_window()
        if win: win.set_cursor(Gdk.Cursor.new_from_name(w.get_display(), "default"))
        return False

    widget.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK | Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK)
    widget.connect("enter-notify-event", _set)
    widget.connect("leave-notify-event", _reset)
    widget.connect("button-press-event", _set)
    widget.connect("button-release-event", _set)


class PowerMenu(Window):
    _ACTIONS = [        
        (icons.shutdown, "Shutdown", "systemctl poweroff"),
        (icons.reboot, "Reboot", "systemctl reboot"),
        (icons.logout, "Logout", "hyprctl dispatch exit"),
        (icons.suspend, "Suspend", "systemctl suspend"),
    ]

    def __init__(self, monitor=0, **kw):
        super().__init__(
            exclusivity="none",
            layer="top",
            monitor=monitor,
            keyboard_mode="none",
        )
        self.set_name("power-menu-window")
        
        GLib.idle_add(lambda: exec_shell_command_async('hyprctl keyword layerrule "noanim, power-menu-window"'))
        
        self.anchor = "top right"
        self.margin = "0px 0px 0px 0px"

        self.set_app_paintable(True)
        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_visual(visual)

        self._open_flag = False
        self._close_timer = None
        self._btns = []
        self._revealers = []
        self._trigger_btn = None
        
        self._dyn_width = 44
        self._dyn_height = 44

        self._build_ui()
        self.set_visible(False)

    def _build_ui(self):
        self._icons_box = Box(
            name="power-menu-icons",
            orientation="v",
            spacing=4,
        )

        for icon_markup, tooltip, cmd in self._ACTIONS:
            btn = Button(
                child=Label(markup=icon_markup),
                tooltip_markup=tooltip,
                can_focus=False,
            )
            sc = btn.get_style_context()
            sc.add_class("power-icon-btn")

            btn.connect("clicked", lambda _, c=cmd: self._execute(c))
            _hand_cursor(btn)

            self._btns.append(btn)
            
            rev = Revealer(
                transition_type="slide-down",
                child_revealed=False,
                child=btn,
            )
            rev.set_transition_duration(200)
            self._revealers.append(rev)
            self._icons_box.add(rev)

        self._menu_wrapper = Box(
            name="power-menu-wrapper",
            orientation="v",
            children=[self._icons_box],
        )

        self.add(self._menu_wrapper)

    def set_trigger_button(self, btn):
        self._trigger_btn = btn
        self._trigger_btn.connect("size-allocate", self._on_trigger_allocate)

    def _on_trigger_allocate(self, widget, allocation):
        if allocation.width > 0 and allocation.height > 0:
            self._dyn_width = allocation.width
            self._dyn_height = allocation.height
            
            for b in self._btns:
                b.set_size_request(self._dyn_width, self._dyn_height)
                
            total_h = (self._dyn_height * len(self._btns)) + (4 * (len(self._btns) - 1))
            self._menu_wrapper.set_size_request(self._dyn_width, total_h)
                
            toplevel = widget.get_toplevel()
            if toplevel:
                ok, x, y = widget.translate_coordinates(toplevel, 0, 0)
                if ok:
                    top_w = toplevel.get_allocated_width()
                    
                    base_right = int(top_w - (x + self._dyn_width))
                    base_top = int(y + self._dyn_height) + 4
                    
                    OFFSET_RIGHT = 0  
                    OFFSET_TOP = 0    
                    
                    m_right = base_right + OFFSET_RIGHT
                    m_top = base_top + OFFSET_TOP
                    
                    self.margin = f"{m_top}px {m_right}px 0px 0px"

    def _set_trigger_danger(self, danger):
        if self._trigger_btn is None: return
        sc = self._trigger_btn.get_style_context()
        if danger: sc.add_class("power-active")
        else: sc.remove_class("power-active")

    def open(self):
        self._cancel()
        self._open_flag = True
        self._set_trigger_danger(True)

        self.set_visible(True)
        self.show_all()
        
        for rev in self._revealers:
            rev.set_reveal_child(False)
        
        GLib.timeout_add(30, self._do_reveal_step, 0)

    def _do_reveal_step(self, idx):
        if not self._open_flag or idx >= len(self._revealers):
            return False
        self._revealers[idx].set_reveal_child(True)
        GLib.timeout_add(45, self._do_reveal_step, idx + 1)
        return False

    def close(self):
        self._cancel()
        if not self._open_flag: return
        self._open_flag = False
        self._set_trigger_danger(False)
        
        for rev in self._revealers:
            rev.set_reveal_child(False)
            
        self._close_timer = GLib.timeout_add(250, self._hide)

    def _hide(self):
        self._close_timer = None
        if not self._open_flag:
            self.set_visible(False)
        return False

    def _cancel(self):
        if self._close_timer:
            GLib.source_remove(self._close_timer)
            self._close_timer = None

    def is_open(self): return self._open_flag

    def _execute(self, cmd):
        try: exec_shell_command_async(cmd)
        except Exception: pass
        self.close()

    def cleanup(self):
        self._cancel()
        self._set_trigger_danger(False)
        if self._open_flag:
            self._open_flag = False
            self.set_visible(False)
        self._trigger_btn = None