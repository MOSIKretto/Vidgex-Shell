import threading
from pathlib import Path

from fabric.utils import DesktopApp, get_desktop_applications, idle_add, remove_handler
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label
from fabric.widgets.entry import Entry
from fabric.widgets.scrolledwindow import ScrolledWindow
from fabric.widgets.stack import Stack
from fabric.widgets.image import Image

from gi.repository import GLib

import services.icons as icons
from services.listNavigation import ListNavigationMixin
from modules.Notch.SessionManager.sessionUtils import normalize_str
from modules.Notch.SessionManager.sessionCore import SessionManager


class SessionManagerUI(ListNavigationMixin, Box):
    __slots__ = (
        'notch', 'manager', 'vp_ignored', 'vp_picker', 'sw_ignored',
        'sw_picker', 'stack', 'ent', 'sel', '_hnd', '_apps', 'vp', 'sw',
        'page_ignored', 'page_picker',
    )

    def __init__(self, notch=None, **kw):
        super().__init__(
            name="app-launcher", visible=False, all_visible=False, **kw
        )
        self.notch = notch
        self.manager = SessionManager()
        self.sel, self._hnd = -1, 0
        self._apps = get_desktop_applications()
        self.vp_picker = None
        self.vp_ignored = None

        btn_save = Button(
            name="clear-button",
            tooltip_markup="<b>Save Session</b>",
            child=Label(name="clear-label", markup=icons.download),
            on_clicked=self._on_save,
        )
        btn_add = Button(
            name="session-add-btn", label="Add Application",
            h_expand=True,
            on_clicked=lambda *_: self.stack.set_visible_child_name("picker"),
        )
        btn_load = Button(
            name="close-button",
            tooltip_markup="<b>Restore Session</b>",
            child=Label(name="close-label", markup=icons.upload),
            on_clicked=self._on_load,
        )
        header_ignored = Box(
            name="header_box", spacing=10, orientation="h",
            h_expand=True, children=[btn_save, btn_add, btn_load],
        )
        self.vp_ignored = Box(name="viewport", spacing=4, orientation="v")
        self.sw_ignored = ScrolledWindow(
            name="scrolled-window", spacing=10,
            h_expand=True, v_expand=True,
            h_align="fill", v_align="fill",
            child=self.vp_ignored,
            propagate_width=False, propagate_height=False,
            can_focus=True, on_key_press_event=self._nav_key,
        )
        self.page_ignored = Box(
            spacing=10, h_expand=True, v_expand=True,
            h_align="fill", v_align="fill", orientation="v",
            children=[header_ignored, self.sw_ignored],
        )

        self.ent = Entry(
            name="search-entry", placeholder="Search Applications...",
            h_expand=True,
            notify_text=lambda e, *_: self._arr(e.get_text()),
            on_activate=lambda *_: self._handle_enter(),
            on_key_press_event=self._nav_key,
        )
        self.ent.props.xalign = 0.5
        btn_cancel = Button(
            name="close-button",
            tooltip_markup="<b>Cancel</b>",
            child=Label(name="close-label", markup=icons.cancel),
            on_clicked=lambda *_: self.stack.set_visible_child_name("ignored"),
        )
        header_picker = Box(
            name="header_box", spacing=10, orientation="h",
            h_expand=True, children=[self.ent, btn_cancel],
        )
        self.vp_picker = Box(name="viewport", spacing=4, orientation="v")
        self.sw_picker = ScrolledWindow(
            name="scrolled-window", spacing=10,
            h_expand=True, v_expand=True,
            h_align="fill", v_align="fill",
            child=self.vp_picker,
            propagate_width=False, propagate_height=False,
        )
        self.page_picker = Box(
            spacing=10, h_expand=True, v_expand=True,
            h_align="fill", v_align="fill", orientation="v",
            children=[header_picker, self.sw_picker],
        )

        self.stack = Stack(
            name="stack", transition_type="slide-left-right",
            v_expand=True, h_expand=True,
        )
        self.stack.set_homogeneous(False)
        self.stack.add_named(self.page_ignored, "ignored")
        self.stack.add_named(self.page_picker, "picker")
        self.stack.connect("notify::visible-child", self._on_vis)

        self.add(Box(
            name="launcher-box", spacing=10,
            h_expand=True, v_expand=True,
            h_align="fill", v_align="fill",
            orientation="v", children=[self.stack],
        ))

        self.vp = self.vp_ignored
        self.sw = self.sw_ignored
        self.show_all()
        self.refresh_ignored(target_sel=0)

    def _on_vis(self, stack, _):
        if self._hnd:
            remove_handler(self._hnd)
            self._hnd = 0
        self._nav_clear()

        if stack.get_visible_child() is self.page_picker:
            self.vp = self.vp_picker
            self.sw = self.sw_picker
            self.ent.set_text("")
            self._arr()
            GLib.idle_add(lambda: self.ent.grab_focus() or False)
        else:
            self.vp = self.vp_ignored
            self.sw = self.sw_ignored
            self.refresh_ignored(target_sel=0)
            GLib.idle_add(lambda: self.sw_ignored.grab_focus() or False)

    def _get_app_class(self, a: DesktopApp) -> str:
        if hasattr(a, 'app_info'):
            try:
                wm = a.app_info.get_startup_wm_class()
                if wm:
                    return wm.lower()
            except:
                pass
            try:
                app_id = a.app_info.get_id()
                if app_id:
                    return app_id.replace('.desktop', '').lower()
            except:
                pass

        for attr in ['get_startup_wm_class', 'get_id', 'id', 'app_id']:
            val = getattr(a, attr, None)
            if callable(val):
                try:
                    val = val()
                except:
                    continue
            if isinstance(val, str) and val:
                return val.replace('.desktop', '').lower()

        wrappers = {
            'env', 'flatpak', 'sh', 'bash',
            'dbus-run-session', 'prime-run',
        }
        for attr in [
            'get_commandline', 'command_line',
            'get_executable', 'executable',
        ]:
            val = getattr(a, attr, None)
            if callable(val):
                try:
                    val = val()
                except:
                    continue
            if isinstance(val, str) and val.strip():
                parts = val.split()
                for part in parts:
                    clean_part = Path(part).name.lower()
                    if (
                        clean_part not in wrappers
                        and '=' not in part
                        and not part.startswith('-')
                    ):
                        return clean_part

        name = (
            getattr(a, 'name', '')
            or getattr(a, 'display_name', '')
            or 'unknown'
        )
        return str(name).lower()

    def _arr(self, q=""):
        if self.vp_picker is None:
            return
        if self._hnd:
            remove_handler(self._hnd)
        self.vp_picker.children, self.sel, qf = [], -1, q.casefold()

        if self._apps:
            apps = sorted(
                (
                    a for a in self._apps
                    if not qf or any(
                        qf in (s or "").casefold()
                        for s in (
                            a.display_name, a.name,
                            a.generic_name, a.command_line,
                        )
                    )
                ),
                key=lambda a: (a.display_name or "").casefold(),
            )
            apps = [
                a for a in apps
                if not self.manager.is_excluded(self._get_app_class(a))
            ]
            self._hnd = idle_add(
                lambda it: self._add_picker(it), iter(apps), pin=True
            )
            if apps:
                GLib.idle_add(lambda: self._nav_usel(0) or False)

    def _add_picker(self, it):
        if not (app := next(it, None)):
            return False
        self.vp_picker.add(self._mk_picker(app))
        self.vp_picker.show_all()
        return True

    def _mk_picker(self, a: DesktopApp) -> Button:
        return Button(
            name="slot-button", tooltip_text=a.description,
            on_clicked=lambda *_: self._on_add(a),
            child=Box(
                name="slot-box", orientation="h", spacing=10,
                children=[
                    Image(
                        name="app-icon",
                        pixbuf=a.get_icon_pixbuf(size=24),
                        h_align="start",
                    ),
                    Label(
                        name="app-label",
                        label=a.display_name or "Unknown",
                        ellipsization="end",
                        v_align="center", h_align="center",
                    ),
                    Label(
                        name="app-desc",
                        label=a.description or "",
                        ellipsization="end",
                        v_align="center", h_align="start",
                        h_expand=True,
                    ),
                ],
            ),
        )

    def refresh_ignored(self, target_sel: int = 0):
        if self._hnd:
            remove_handler(self._hnd)
            self._hnd = 0
        self._nav_clear()
        self.vp_ignored.children, self.sel = [], -1

        self.manager.sync_exclusions()

        if not self.manager.exclusions:
            self._empty(self.vp_ignored, icons.close)
        else:
            max_idx = len(self.manager.exclusions) - 1
            if target_sel > max_idx:
                target_sel = max_idx
            if target_sel < 0:
                target_sel = 0
            self._hnd = idle_add(
                lambda it: self._add_ignored(it, target_sel),
                iter(self.manager.exclusions), pin=True,
            )

    def _add_ignored(self, it, target_sel: int):
        if not (app_name := next(it, None)):
            GLib.idle_add(lambda: self._nav_usel(target_sel) or False)
            return False
        self.vp_ignored.add(self._mk_ignored(app_name))
        self.vp_ignored.show_all()
        return True

    def _mk_ignored(self, app_name: str) -> Button:
        found_app = None
        c_clean = normalize_str(app_name)
        if self._apps:
            for a in self._apps:
                app_cls = normalize_str(self._get_app_class(a))
                if c_clean in app_cls or app_cls in c_clean:
                    found_app = a
                    break

        if found_app:
            icon_w = Image(
                name="app-icon",
                pixbuf=found_app.get_icon_pixbuf(size=24),
                h_align="start",
            )
            title = found_app.display_name or app_name
            desc = (
                found_app.description or "Click to remove from exclusions"
            )
        else:
            icon_w = Image(
                name="app-icon",
                icon_name="application-x-executable",
                pixel_size=24, h_align="start",
            )
            title = app_name
            desc = "Manual exclusion (Click to remove)"

        return Button(
            name="slot-button",
            tooltip_text="Remove from exclusions",
            on_clicked=lambda *_: self._on_remove(app_name),
            child=Box(
                name="slot-box", orientation="h", spacing=10,
                children=[
                    icon_w,
                    Label(
                        name="app-label", label=title,
                        ellipsization="end",
                        v_align="center", h_align="center",
                    ),
                    Label(
                        name="app-desc", label=desc,
                        ellipsization="end",
                        v_align="center", h_align="start",
                        h_expand=True,
                    ),
                    Label(
                        name="clip-icon", markup=icons.trash,
                        v_align="center", h_align="end",
                    ),
                ],
            ),
        )

    def _empty(self, target_box, icon):
        target_box.children = []
        target_box.add(Box(
            name="no-clip-container",
            v_expand=True, h_expand=True, orientation="v",
            children=[Label(
                name="no-clip", markup=icon,
                v_align="center", h_align="center",
                v_expand=True, h_expand=True,
            )],
        ))
        target_box.show_all()

    def _handle_enter(self, *args):
        if self.sel >= 0 and self.vp.children:
            self._nav_activate()
        elif self.vp.children:
            self._nav_usel(0)
            self._nav_activate()
        elif text := self.ent.get_text().strip():
            self._on_add(text.lower())

    def _on_add(self, a):
        app_name = a if isinstance(a, str) else self._get_app_class(a)
        if app_name:
            self.manager.add_exclusion(app_name)
        self.stack.set_visible_child_name("ignored")

    def _on_remove(self, app_name: str):
        idx = -1
        if app_name in self.manager.exclusions:
            idx = self.manager.exclusions.index(app_name)
        self.manager.remove_exclusion(app_name)
        target_sel = idx - 1 if idx > 0 else 0
        self.refresh_ignored(target_sel=target_sel)

    def _on_save(self, *args):
        self.manager.save()
        if self.notch:
            self.notch.close_notch()

    def _on_load(self, *args):
        if self.notch:
            self.notch.close_notch()
        threading.Thread(target=self.manager.restore, daemon=True).start()