from fabric.utils import DesktopApp, get_desktop_applications, idle_add, remove_handler
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.entry import Entry
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.scrolledwindow import ScrolledWindow
from gi.repository import Gdk, GLib

import services.icons as icons
from services.listNavigation import ListNavigationMixin


def set_pointer_cursor(widget):
    def _on_enter(w, _event):
        win = w.get_window()
        if win:
            win.set_cursor(Gdk.Cursor.new_from_name(w.get_display(), "pointer"))
        return False

    def _on_leave(w, _event):
        win = w.get_window()
        if win:
            win.set_cursor(None)
        return False

    widget.connect("enter-notify-event", _on_enter)
    widget.connect("leave-notify-event", _on_leave)


class AppLauncher(ListNavigationMixin, Box):
    __slots__ = ('notch', 'sel', '_hnd', '_apps', 'vp', 'ent', 'sw')

    def __init__(self, **kw):
        super().__init__(name="app-launcher", visible=False, all_visible=False, **kw)
        self.notch, self.sel, self._hnd, self._apps = kw["notch"], -1, 0, None
        self.vp = Box(name="viewport", spacing=4, orientation="v")
        self.ent = Entry(
            name="search-entry",
            placeholder="Search Applications...",
            h_expand=True,
            notify_text=lambda e, *_: self._arr(e.get_text()),
            on_activate=lambda *_: self._nav_activate(),
            on_key_press_event=self._nav_key
        )
        self.ent.props.xalign = 0.5

        close_btn = Button(
            name="close-button",
            tooltip_markup="<b>Close</b>",
            child=Label(
                name="close-label",
                markup=icons.cancel
            ),
            on_clicked=lambda *_: self.close()
        )
        set_pointer_cursor(close_btn) 

        self.sw = ScrolledWindow(
            name="scrolled-window",
            spacing=10,
            h_expand=True,
            v_expand=True,
            h_align="fill",
            v_align="fill",
            child=self.vp,
            propagate_width=False,
            propagate_height=False
        )
        self.add(Box(
            name="launcher-box",
            spacing=10,
            h_expand=True,
            orientation="v",
            children=[
                Box(
                    name="header_box",
                    spacing=10,
                    orientation="h",
                    children=[
                        self.ent,
                        close_btn,
                    ]
                ),
                self.sw
            ]
        ))
        self.show_all()

    def open(self):
        self._apps = get_desktop_applications(); self._arr()
        GLib.idle_add(lambda: self.ent.grab_focus() or False)

    def close(self):
        if self._hnd: remove_handler(self._hnd); self._hnd = 0
        self.vp.children, self.sel, self._apps = [], -1, None
        self.notch.close_notch()

    def _arr(self, q=""):
        if self._apps is None:
            return
        if self._hnd: remove_handler(self._hnd)
        self.vp.children, self.sel, qf = [], -1, q.casefold()
        apps = sorted((a for a in self._apps if not qf or any(qf in (s or "").casefold()
                       for s in (a.display_name, a.name, a.generic_name, a.command_line))),
                      key=lambda a: (a.display_name or "").casefold())
        self._hnd = idle_add(lambda it: self._add(it), iter(apps), pin=True)
        if apps: GLib.idle_add(lambda: self._nav_usel(0) or False)

    def _add(self, it):
        if not (app := next(it, None)): return False
        self.vp.add(self._mk(app)); return True

    def _mk(self, a: DesktopApp) -> Button:
        btn = Button(
            name="slot-button",
            tooltip_text=a.description,
            on_clicked=lambda *_: (a.launch(), self.close()),
            child=Box(
                name="slot-box",
                orientation="h",
                spacing=10,
                children=[
                    Image(
                        name="app-icon",
                        pixbuf=a.get_icon_pixbuf(size=24),
                        h_align="start"
                    ),
                    Label(
                        name="app-label",
                        label=a.display_name or "Unknown",
                        ellipsization="end",
                        v_align="center",
                        h_align="center"
                    ),
                    Label(
                        name="app-desc",
                        label=a.description or "",
                        ellipsization="end",
                        v_align="center",
                        h_align="start",
                        h_expand=True
                    )
                ]
            )
        )
        set_pointer_cursor(btn)
        return btn