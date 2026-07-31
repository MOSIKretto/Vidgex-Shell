import json
import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from fabric.hyprland.widgets import get_hyprland_connection
from fabric.utils import get_desktop_applications
from fabric.widgets.box import Box
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer
from fabric.widgets.stack import Stack

from modules.Notch.Notifications.history import get_shared_history
from modules.Notch.Notifications.glyph import SideGlyph
from modules.Notch.mainWindow import MainWindow
from modules.Notch.clipHist import ClipHistory
from modules.Notch.appLauncher import AppLauncher
from modules.Notch.notificationPopup import NotificationPopup
from modules.Notch.MainWindow.Dashboard.controls import ControlSmall, get_audio
from modules.Notch.MainWindow.Dashboard.Controls.brightness import Brightness
from modules.corners import MyCorner

from services.wayland import WaylandWindow as Window


# Маппинг имён аплетов на секции/атрибуты MainWindowimport json
import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from fabric.hyprland.widgets import get_hyprland_connection
from fabric.utils import get_desktop_applications
from fabric.widgets.box import Box
from fabric.widgets.centerbox import CenterBox
from fabric.widgets.image import Image
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer
from fabric.widgets.stack import Stack

from modules.Notch.Notifications.history import get_shared_history
from modules.Notch.Notifications.glyph import SideGlyph
from modules.Notch.mainWindow import MainWindow
from modules.Notch.clipHist import ClipHistory
from modules.Notch.appLauncher import AppLauncher
from modules.Notch.notificationPopup import NotificationPopup
from modules.Notch.MainWindow.Dashboard.controls import ControlSmall, get_audio
from modules.Notch.MainWindow.Dashboard.Controls.brightness import Brightness
from modules.corners import MyCorner

from services.wayland import WaylandWindow as Window


# Маппинг имён аплетов на секции/атрибуты MainWindow
APPLET_MAP = {
    "network_applet": "network_connections",
    "bluetooth":      "bluetooth",
    "dashboard":      "notification_history",
}

_app_map: dict = {}
_theme = Gtk.IconTheme.get_default()


def _refresh_apps() -> None:
    for app in get_desktop_applications():
        for k in filter(None, (app.name, app.display_name)):
            _app_map[k.lower()] = app
            _app_map[k.lower().strip().rsplit(".", 1)[-1]] = app

_refresh_apps()


def _icon(cls: str, size: int = 20):
    key = (cls or "").lower()
    app = _app_map.get(key) or _app_map.get(key.strip().rsplit(".", 1)[-1])
    if app and hasattr(app, "get_icon_pixbuf"):
        px = app.get_icon_pixbuf(size=size)
        if px:
            return px
    for name in filter(None, (cls, key, "application-x-executable-symbolic")):
        try:
            return _theme.load_icon(name, size, Gtk.IconLookupFlags.FORCE_SIZE)
        except Exception:
            pass
    return None


class Notch(Window):
    def __init__(self, **kwargs):
        super().__init__(anchor="top", margin="-40px 0px 0px 0px", monitor=0)

        self._cw: str | None = None # имя текущего открытого виджета
        self._cht: int | None = None # id таймера ctrl_rev
        self._last_win: tuple = (None, None, None)
        self._conn = get_hyprland_connection()
        self._init = False

        self._build()
        self._bind()
        self._watch()
        GLib.idle_add(self._final)

    # Построение UI
    def _build(self) -> None:
        # Компактный вид: иконка окна + воркспейс
        self.win_ic = Image(
            name="notch-window-icon",
            icon_name="application-x-executable",
            icon_size=20,
        )
        self.ws_lbl = Label(name="workspace-label", label="Workspace 1")
        self.awc = Box(
            name="active-window-container",
            spacing=8,
            children=[self.win_ic, self.ws_lbl],
        )
        self.awb = Box(
            name="active-window-box",
            h_align="center",
            children=[self.awc],
        )

        # Стек компактного вида
        self.cs = Stack(name="notch-compact-stack", transition_type="slide-up-down")
        self.cs.add_named(self.awb, "window")

        # Плашка громкости/яркости, раскрывается снизу компактного вида
        self.ctrl_rev = Revealer(
            name="control-revealer",
            transition_type="slide-down",
            transition_duration=200,
            child_revealed=False,
            child=Box(
                name="control-revealer-box",
                h_align="center",
                children=[ControlSmall()],
            ),
        )

        self.compact = Gtk.EventBox(name="notch-compact", visible=True)
        self.compact.add(
            Box(
                name="compact-content",
                orientation="v",
                children=[self.cs, self.ctrl_rev],
            )
        )
        self.compact.set_size_request(260, -1)

        # Все раскрытые виджеты — создаём сразу
        self.main_window  = MainWindow(notch=self)
        self.app_launcher = AppLauncher(notch=self)
        self.clip_history = ClipHistory(notch=self)
        self.notif_popup  = NotificationPopup(notch=self)

        self.main_window.set_size_request(1093, 472)
        self.app_launcher.set_size_request(480, 244)
        self.clip_history.set_size_request(480, 244)
        self.notif_popup.set_size_request(360, -1)

        # Главный стек
        self.stack = Stack(
            name="notch-content",
            transition_type="crossfade",
            transition_duration=200,
        )
        self.stack.add_named(self.compact, "compact")
        self.stack.add_named(self.main_window, "main_window")
        self.stack.add_named(self.app_launcher, "launcher")
        self.stack.add_named(self.clip_history, "cliphist")
        self.stack.add_named(self.notif_popup, "notification")

        for s in ("panel", "bottom", "Top"):
            self.stack.add_style_class(s)
        self.stack.set_interpolate_size(True)
        self.stack.set_homogeneous(False)

        self.nb = CenterBox(
            name="notch-box",
            start_children=Box(
                name="notch-corner-left",
                orientation="v",
                h_align="start",
                children=[MyCorner("top-right")],
            ),
            center_children=self.stack,
            end_children=Box(
                name="notch-corner-right",
                orientation="v",
                h_align="end",
                children=[MyCorner("top-left")],
            ),
        )
        self.nb.add_style_class("notch")

        self.nr = Revealer(
            name="notch-revealer",
            child_revealed=True,
            child=self.nb,
        )
        self.nr.set_size_request(-1, 1)

        self.left_glyph  = SideGlyph("left")
        self.right_glyph = SideGlyph("right")

        self.heb = Gtk.EventBox(
            name="notch-hover-eventbox",
            visible=True,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.START,
        )
        self.heb.add(Box(name="notch-complete", children=[self.nr]))
        self.heb.set_size_request(-1, 4)

        root_box = Box(
            name="notch-root-container",
            orientation="h",
            h_align="center",
            v_align="start",
            spacing=0,
        )
        root_box.add(Box(
            children=[self.left_glyph],
            style="margin-right: -10px; margin-top: -8px;",
        ))
        root_box.add(self.heb)
        root_box.add(Box(
            children=[self.right_glyph],
            style="margin-left: -10px; margin-top: -8px;",
        ))
        self.add(root_box)

        self.add(root_box)

    # Привязка сигналов
    def _bind(self) -> None:
        self.compact.connect(
            "button-press-event",
            lambda *_: self.toggle_notch("dashboard") or True,
        )
        self.compact.connect(
            "enter-notify-event",
            lambda w, _: w.get_window().set_cursor(
                Gdk.Cursor.new_from_name(w.get_display(), "pointer")
            ) or True,
        )
        self.compact.connect(
            "leave-notify-event",
            lambda w, e: w.get_window().set_cursor(None)
            if e.detail != Gdk.NotifyType.INFERIOR
            else False,
        )
        self.connect(
            "key-press-event",
            lambda _, e: self.close_notch() or True
            if e.keyval == Gdk.KEY_Escape
            else False,
        )
        self.add_keybinding("Escape", lambda *_: self.close_notch())

        if self._conn:
            self._conn.connect("event", lambda *_: self._schedule_updwin())

    # Мониторинг громкости / яркости / микро
    def _watch(self) -> None:
        self.audio = get_audio()
        self._br   = Brightness.get_initial()
        self._vals: dict = {"speaker": None, "microphone": None, "screen": None}

        for dev in ("speaker", "microphone"):
            self._bind_dev(dev)
            self.audio.connect(
                f"notify::{dev}", lambda *_, d=dev: self._bind_dev(d)
            )

        if self._br.screen_brightness != -1:
            self._vals["screen"] = self._br.screen_brightness
            self._br.connect(
                "screen",
                lambda *_: self._val_chg("screen", self._br.screen_brightness, 0),
            )

    def _bind_dev(self, dev: str) -> None:
        d = getattr(self.audio, dev, None)
        if d:
            self._vals[dev] = d.volume
            d.connect(
                "changed",
                lambda *_, name=dev: self._val_chg(
                    name, getattr(self.audio, name).volume, 0.5
                ),
            )

    def _val_chg(self, name: str, cur, threshold: float) -> None:
        if not self._init or cur is None:
            return
        prev = self._vals.get(name)
        if prev is not None and abs(cur - prev) > threshold:
            # Показываем шкалу только когда notch закрыт
            if not self._cw:
                if self._cht:
                    GLib.source_remove(self._cht)
                self.ctrl_rev.set_reveal_child(True)
                self._cht = GLib.timeout_add(
                    2000,
                    lambda: self.ctrl_rev.set_reveal_child(False) or False,
                )
        self._vals[name] = cur

    # Глифы
    def _trigger_glyphs(self) -> None:
        self.left_glyph.trigger()
        self.right_glyph.trigger()

    # Инициализация
    def _final(self) -> bool:
        self.show_all()
        self._schedule_updwin()
        self._init = True
        history = get_shared_history()
        history.trigger_glyphs_callback = self._trigger_glyphs
        return False

    # Управление уведомлением внутри notch
    def open_notification(self) -> None:
        if self._cw and self._cw != "notification":
            return

        self._stop_ctrl_rev()
        self.nr.set_reveal_child(True)
        self.nb.add_style_class("open")
        self.stack.add_style_class("open")
        self._cw = "notification"
        self.stack.set_visible_child(self.notif_popup)

    def close_notification(self) -> None:
        if self._cw != "notification":
            return
        self.nb.remove_style_class("open")
        self.stack.remove_style_class("open")
        self._cw = None
        self.stack.set_visible_child(self.compact)
        self._schedule_updwin()

    # Открытие / закрытие notch
    def toggle_notch(self, name: str) -> None:
        if self._cw == name:
            self.close_notch()
            return

        # Уведомление тихо убираем без повторной отправки в историю
        if self._cw == "notification":
            self.close_notification()

        self._stop_ctrl_rev()
        self.nr.set_reveal_child(True)
        self.nb.add_style_class("open")
        self.stack.add_style_class("open")
        self.keyboard_mode = "exclusive"
        self._cw = name

        if name in APPLET_MAP or name in {"wallpapers", "player"}:
            self.stack.set_visible_child(self.main_window)
            self.main_window.go_to_section(
                "dashboard" if name in APPLET_MAP else name
            )
            if name in APPLET_MAP:
                target = getattr(
                    self.main_window.dashboard,
                    APPLET_MAP[name],
                    None,
                )
                if target:
                    self.main_window.dashboard.applet_stack.set_visible_child(target)
        else:
            widget = self._widget_by_name(name)
            if widget:
                self.stack.set_visible_child(widget)
                if hasattr(widget, "open"):
                    widget.open()
                if name == "launcher" and hasattr(widget, "ent"):
                    widget.ent.set_text("")
                    widget.ent.grab_focus()

    def close_notch(self) -> None:
        self.keyboard_mode = "none"
        self.nb.remove_style_class("open")
        self.stack.remove_style_class("open")
        self._stop_ctrl_rev()

        # Сбрасываем аплет-стек на историю уведомлений
        dashboard = getattr(self.main_window, "dashboard", None)
        if dashboard:
            applet_stack = getattr(dashboard, "applet_stack", None)
            notif_hist   = getattr(dashboard, "notification_history", None)
            if applet_stack and notif_hist:
                applet_stack.set_visible_child(notif_hist)

        self._cw = None
        self.stack.set_visible_child(self.compact)
        self._schedule_updwin()

    # Вспомогательные методы
    def _widget_by_name(self, name: str) -> Gtk.Widget | None:
        return {
            "main_window":  self.main_window,
            "launcher":     self.app_launcher,
            "cliphist":     self.clip_history,
            "notification": self.notif_popup,
        }.get(name)

    def _stop_ctrl_rev(self) -> None:
        if self._cht:
            GLib.source_remove(self._cht)
            self._cht = None
        self.ctrl_rev.set_reveal_child(False)

    # Обновление активного окна (IPC)
    def _schedule_updwin(self) -> None:
        threading.Thread(target=self._fetch_win_info, daemon=True).start()

    def _fetch_win_info(self) -> None:
        if self._cw or not self._conn:
            return
        try:
            ws = json.loads(
                self._conn.send_command("j/activeworkspace").reply.decode()
            ).get("id", 1)
            win = json.loads(
                self._conn.send_command("j/activewindow").reply.decode()
            )
            wc = win.get("class", win.get("initialClass", ""))
            wt = win.get("title", "")
        except Exception:
            return

        # Передаём результат в главный поток GTK
        GLib.idle_add(self._apply_win_info, ws, wc, wt)

    def _apply_win_info(self, ws: int, wc: str, wt: str) -> bool:
        if self._cw:
            return False
        if (ws, wc, wt) == self._last_win:
            return False
        self._last_win = (ws, wc, wt)

        self.ws_lbl.set_label(f"Workspace {ws}")
        if wc.strip():
            px = _icon(wc)
            if px:
                self.win_ic.set_from_pixbuf(px)
            else:
                self.win_ic.set_from_icon_name(
                    "application-x-executable-symbolic", 20
                )
            self.win_ic.show()
            self.awc.set_spacing(8)
        else:
            self.win_ic.hide()
            self.awc.set_spacing(0)

        return False
APPLET_MAP = {
    "network_applet": "network_connections",
    "bluetooth":      "bluetooth",
    "dashboard":      "notification_history",
}

_app_map: dict = {}
_theme = Gtk.IconTheme.get_default()


def _refresh_apps() -> None:
    for app in get_desktop_applications():
        for k in filter(None, (app.name, app.display_name)):
            _app_map[k.lower()] = app
            _app_map[k.lower().strip().rsplit(".", 1)[-1]] = app

_refresh_apps()


def _icon(cls: str, size: int = 20):
    key = (cls or "").lower()
    app = _app_map.get(key) or _app_map.get(key.strip().rsplit(".", 1)[-1])
    if app and hasattr(app, "get_icon_pixbuf"):
        px = app.get_icon_pixbuf(size=size)
        if px:
            return px
    for name in filter(None, (cls, key, "application-x-executable-symbolic")):
        try:
            return _theme.load_icon(name, size, Gtk.IconLookupFlags.FORCE_SIZE)
        except Exception:
            pass
    return None


class Notch(Window):
    def __init__(self, **kwargs):
        super().__init__(anchor="top", margin="-40px 0px 0px 0px", monitor=0)

        self._cw: str | None = None # имя текущего открытого виджета
        self._cht: int | None = None # id таймера ctrl_rev
        self._last_win: tuple = (None, None, None)
        self._conn = get_hyprland_connection()
        self._init = False

        self._build()
        self._bind()
        self._watch()
        GLib.idle_add(self._final)

    # Построение UI
    def _build(self) -> None:
        # Компактный вид: иконка окна + воркспейс
        self.win_ic = Image(
            name="notch-window-icon",
            icon_name="application-x-executable",
            icon_size=20,
        )
        self.ws_lbl = Label(name="workspace-label", label="Workspace 1")
        self.awc = Box(
            name="active-window-container",
            spacing=8,
            children=[self.win_ic, self.ws_lbl],
        )
        self.awb = Box(
            name="active-window-box",
            h_align="center",
            children=[self.awc],
        )

        # Стек компактного вида
        self.cs = Stack(name="notch-compact-stack", transition_type="slide-up-down")
        self.cs.add_named(self.awb, "window")

        # Плашка громкости/яркости, раскрывается снизу компактного вида
        self.ctrl_rev = Revealer(
            name="control-revealer",
            transition_type="slide-down",
            transition_duration=200,
            child_revealed=False,
            child=Box(
                name="control-revealer-box",
                h_align="center",
                children=[ControlSmall()],
            ),
        )

        self.compact = Gtk.EventBox(name="notch-compact", visible=True)
        self.compact.add(
            Box(
                name="compact-content",
                orientation="v",
                children=[self.cs, self.ctrl_rev],
            )
        )
        self.compact.set_size_request(260, -1)

        # Все раскрытые виджеты — создаём сразу
        self.main_window  = MainWindow(notch=self)
        self.app_launcher = AppLauncher(notch=self)
        self.clip_history = ClipHistory(notch=self)
        self.notif_popup  = NotificationPopup(notch=self)

        self.main_window.set_size_request(1093, 472)
        self.app_launcher.set_size_request(480, 244)
        self.clip_history.set_size_request(480, 244)
        self.notif_popup.set_size_request(360, -1)

        # Главный стек
        self.stack = Stack(
            name="notch-content",
            transition_type="crossfade",
            transition_duration=200,
        )
        self.stack.add_named(self.compact, "compact")
        self.stack.add_named(self.main_window, "main_window")
        self.stack.add_named(self.app_launcher, "launcher")
        self.stack.add_named(self.clip_history, "cliphist")
        self.stack.add_named(self.notif_popup, "notification")

        for s in ("panel", "bottom", "Top"):
            self.stack.add_style_class(s)
        self.stack.set_interpolate_size(True)
        self.stack.set_homogeneous(False)

        self.nb = CenterBox(
            name="notch-box",
            start_children=Box(
                name="notch-corner-left",
                orientation="v",
                h_align="start",
                children=[MyCorner("top-right")],
            ),
            center_children=self.stack,
            end_children=Box(
                name="notch-corner-right",
                orientation="v",
                h_align="end",
                children=[MyCorner("top-left")],
            ),
        )
        self.nb.add_style_class("notch")

        self.nr = Revealer(
            name="notch-revealer",
            child_revealed=True,
            child=self.nb,
        )
        self.nr.set_size_request(-1, 1)

        self.left_glyph  = SideGlyph("left")
        self.right_glyph = SideGlyph("right")

        self.heb = Gtk.EventBox(
            name="notch-hover-eventbox",
            visible=True,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.START,
        )
        self.heb.add(Box(name="notch-complete", children=[self.nr]))
        self.heb.set_size_request(-1, 4)

        root_box = Box(
            name="notch-root-container",
            orientation="h",
            h_align="center",
            v_align="start",
            spacing=0,
        )
        root_box.add(Box(
            children=[self.left_glyph],
            style="margin-right: -10px; margin-top: -8px;",
        ))
        root_box.add(self.heb)
        root_box.add(Box(
            children=[self.right_glyph],
            style="margin-left: -10px; margin-top: -8px;",
        ))
        self.add(root_box)

        self.add(root_box)

    # Привязка сигналов
    def _bind(self) -> None:
        self.compact.connect(
            "button-press-event",
            lambda *_: self.toggle_notch("dashboard") or True,
        )
        self.compact.connect(
            "enter-notify-event",
            lambda w, _: w.get_window().set_cursor(
                Gdk.Cursor.new_from_name(w.get_display(), "pointer")
            ) or True,
        )
        self.compact.connect(
            "leave-notify-event",
            lambda w, e: w.get_window().set_cursor(None)
            if e.detail != Gdk.NotifyType.INFERIOR
            else False,
        )
        self.connect(
            "key-press-event",
            lambda _, e: self.close_notch() or True
            if e.keyval == Gdk.KEY_Escape
            else False,
        )
        self.add_keybinding("Escape", lambda *_: self.close_notch())

        if self._conn:
            self._conn.connect("event", lambda *_: self._schedule_updwin())

    # Мониторинг громкости / яркости / микро
    def _watch(self) -> None:
        self.audio = get_audio()
        self._br   = Brightness.get_initial()
        self._vals: dict = {"speaker": None, "microphone": None, "screen": None}

        for dev in ("speaker", "microphone"):
            self._bind_dev(dev)
            self.audio.connect(
                f"notify::{dev}", lambda *_, d=dev: self._bind_dev(d)
            )

        if self._br.screen_brightness != -1:
            self._vals["screen"] = self._br.screen_brightness
            self._br.connect(
                "screen",
                lambda *_: self._val_chg("screen", self._br.screen_brightness, 0),
            )

    def _bind_dev(self, dev: str) -> None:
        d = getattr(self.audio, dev, None)
        if d:
            self._vals[dev] = d.volume
            d.connect(
                "changed",
                lambda *_, name=dev: self._val_chg(
                    name, getattr(self.audio, name).volume, 0.5
                ),
            )

    def _val_chg(self, name: str, cur, threshold: float) -> None:
        if not self._init or cur is None:
            return
        prev = self._vals.get(name)
        if prev is not None and abs(cur - prev) > threshold:
            # Показываем шкалу только когда notch закрыт
            if not self._cw:
                if self._cht:
                    GLib.source_remove(self._cht)
                self.ctrl_rev.set_reveal_child(True)
                self._cht = GLib.timeout_add(
                    2000,
                    lambda: self.ctrl_rev.set_reveal_child(False) or False,
                )
        self._vals[name] = cur

    # Глифы
    def _trigger_glyphs(self) -> None:
        self.left_glyph.trigger()
        self.right_glyph.trigger()

    # Инициализация
    def _final(self) -> bool:
        self.show_all()
        self._schedule_updwin()
        self._init = True
        history = get_shared_history()
        history.trigger_glyphs_callback = self._trigger_glyphs
        return False

    # Управление уведомлением внутри notch
    def open_notification(self) -> None:
        if self._cw and self._cw != "notification":
            return

        self._stop_ctrl_rev()
        self.nr.set_reveal_child(True)
        self.nb.add_style_class("open")
        self.stack.add_style_class("open")
        self._cw = "notification"
        self.stack.set_visible_child(self.notif_popup)

    def close_notification(self) -> None:
        if self._cw != "notification":
            return
        self.nb.remove_style_class("open")
        self.stack.remove_style_class("open")
        self._cw = None
        self.stack.set_visible_child(self.compact)
        self._schedule_updwin()

    # Открытие / закрытие notch
    def toggle_notch(self, name: str) -> None:
        if self._cw == name:
            self.close_notch()
            return

        # Уведомление тихо убираем без повторной отправки в историю
        if self._cw == "notification":
            self.close_notification()

        self._stop_ctrl_rev()
        self.nr.set_reveal_child(True)
        self.nb.add_style_class("open")
        self.stack.add_style_class("open")
        self.keyboard_mode = "exclusive"
        self._cw = name

        if name in APPLET_MAP or name in {"wallpapers", "player"}:
            self.stack.set_visible_child(self.main_window)
            self.main_window.go_to_section(
                "dashboard" if name in APPLET_MAP else name
            )
            if name in APPLET_MAP:
                target = getattr(
                    self.main_window.dashboard,
                    APPLET_MAP[name],
                    None,
                )
                if target:
                    self.main_window.dashboard.applet_stack.set_visible_child(target)
        else:
            widget = self._widget_by_name(name)
            if widget:
                self.stack.set_visible_child(widget)
                if hasattr(widget, "open"):
                    widget.open()
                if name == "launcher" and hasattr(widget, "ent"):
                    widget.ent.set_text("")
                    widget.ent.grab_focus()

    def close_notch(self) -> None:
        self.keyboard_mode = "none"
        self.nb.remove_style_class("open")
        self.stack.remove_style_class("open")
        self._stop_ctrl_rev()

        # Сбрасываем аплет-стек на историю уведомлений
        dashboard = getattr(self.main_window, "dashboard", None)
        if dashboard:
            applet_stack = getattr(dashboard, "applet_stack", None)
            notif_hist   = getattr(dashboard, "notification_history", None)
            if applet_stack and notif_hist:
                applet_stack.set_visible_child(notif_hist)

        self._cw = None
        self.stack.set_visible_child(self.compact)
        self._schedule_updwin()

    # Вспомогательные методы
    def _widget_by_name(self, name: str) -> Gtk.Widget | None:
        return {
            "main_window":  self.main_window,
            "launcher":     self.app_launcher,
            "cliphist":     self.clip_history,
            "notification": self.notif_popup,
        }.get(name)

    def _stop_ctrl_rev(self) -> None:
        if self._cht:
            GLib.source_remove(self._cht)
            self._cht = None
        self.ctrl_rev.set_reveal_child(False)

    # Обновление активного окна (IPC)
    def _schedule_updwin(self) -> None:
        threading.Thread(target=self._fetch_win_info, daemon=True).start()

    def _fetch_win_info(self) -> None:
        if self._cw or not self._conn:
            return
        try:
            ws = json.loads(
                self._conn.send_command("j/activeworkspace").reply.decode()
            ).get("id", 1)
            win = json.loads(
                self._conn.send_command("j/activewindow").reply.decode()
            )
            wc = win.get("class", win.get("initialClass", ""))
            wt = win.get("title", "")
        except Exception:
            return

        # Передаём результат в главный поток GTK
        GLib.idle_add(self._apply_win_info, ws, wc, wt)

    def _apply_win_info(self, ws: int, wc: str, wt: str) -> bool:
        if self._cw:
            return False
        if (ws, wc, wt) == self._last_win:
            return False
        self._last_win = (ws, wc, wt)

        self.ws_lbl.set_label(f"Workspace {ws}")
        if wc.strip():
            px = _icon(wc)
            if px:
                self.win_ic.set_from_pixbuf(px)
            else:
                self.win_ic.set_from_icon_name(
                    "application-x-executable-symbolic", 20
                )
            self.win_ic.show()
            self.awc.set_spacing(8)
        else:
            self.win_ic.hide()
            self.awc.set_spacing(0)

        return False