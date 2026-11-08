import gi
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from fabric.widgets.box import Box
from fabric.widgets.stack import Stack

from modules.Notch.MainWindow.musicPlayer import Player
from modules.Notch.MainWindow.wallpapers import WallpaperSelector
from modules.Notch.MainWindow.dashboard import Dashboard


_NAV_ITEMS = ("dashboard", "player", "wallpapers", "close")
_DASHBOARD_APPLETS = frozenset({"dashboard", "network_applet", "bluetooth"})

_INDICATOR_MS = 350   # скольжение плашки
_STACK_MS = 480       # слайд содержимого (у Gtk.Stack easing фиксированный, меняется только время)


def _smootherstep(t: float) -> float:
    return t * t * t * (t * (6.0 * t - 15.0) + 10.0)


class SlidingIndicator(Gtk.DrawingArea):
    """Плашка, плавно переезжающая к целевому прямоугольнику."""

    def __init__(self, duration_ms: int = _INDICATOR_MS):
        super().__init__(name="switcher-indicator")
        self._duration = duration_ms * 1000      # микросекунды (frame clock)
        self._cur = None                         # [x, y, w, h] текущее
        self._from = None
        self._to = None                          # (x, y, w, h) цель
        self._t0 = None
        self._tick_id = 0
        self.set_hexpand(True)

    # --- публичное API ---
    def move_to(self, x, y, w, h, animate: bool = True) -> None:
        target = (x, y, w, h)
        if target == self._to:                   # цель не изменилась: не прерываем анимацию
            return
        self._to = target

        # Ресайз во время анимации: просто обновляем цель, плашка доедет сама
        if not animate and self._tick_id:
            return

        settings = Gtk.Settings.get_default()
        animations_on = settings.get_property("gtk-enable-animations")

        if self._cur is None or not animate or not animations_on or not self.get_mapped():
            self.stop()
            self._cur = list(target)
            self.queue_draw()
            return

        self._from = list(self._cur)
        self._t0 = None
        if not self._tick_id:
            self._tick_id = self.add_tick_callback(self._on_tick)

    def stop(self) -> None:
        if self._tick_id:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = 0

    # --- внутреннее ---
    def _on_tick(self, _widget, clock) -> bool:
        now = clock.get_frame_time()
        if self._t0 is None:
            self._t0 = now

        t = (now - self._t0) / self._duration
        if t >= 1.0:
            self._cur = list(self._to)
            self._tick_id = 0
            self.queue_draw()
            return GLib.SOURCE_REMOVE

        e = _smootherstep(t)
        self._cur = [f + (to - f) * e for f, to in zip(self._from, self._to)]
        self.queue_draw()
        return GLib.SOURCE_CONTINUE

    def do_draw(self, cr) -> bool:
        if self._cur is None:
            return False
        x, y, w, h = self._cur
        ctx = self.get_style_context()
        Gtk.render_background(ctx, cr, x, y, w, h)
        Gtk.render_frame(ctx, cr, x, y, w, h)
        return False


class MainWindow(Box):
    def __init__(self, notch, **kwargs):
        self.notch = notch
        self._cur_idx = 0
        self._destroyed = False

        self.dashboard = Dashboard(notch=notch)
        self.wallpapers = WallpaperSelector()
        self.player = Player()

        self._sections = {
            "dashboard": self.dashboard,
            "player":    self.player,
            "wallpapers": self.wallpapers,
        }

        self.stack = Stack(
            name="stack",
            transition_type="slide-left-right",
            transition_duration=_STACK_MS,
            v_expand=True,
        )
        self.stack.set_homogeneous(False)
        self.stack.add_titled(self.dashboard,   "dashboard",  "Dashboard")
        self.stack.add_titled(self.player,      "player",     "Player")
        self.stack.add_titled(self.wallpapers,  "wallpapers", "Wallpapers")

        self.switcher = Gtk.StackSwitcher(name="switcher", spacing=8)
        self.switcher.set_stack(self.stack)
        self.switcher.set_hexpand(True)
        self.switcher.set_homogeneous(False)
        self.switcher.set_can_focus(False)

        self.close_button = Gtk.Button(name="close-notch-button")
        self.close_button.set_can_focus(False)
        self.close_button.get_style_context().remove_class("image-button")
        self.close_button.set_relief(Gtk.ReliefStyle.NONE)
        self.close_button.add(
            Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
        )
        self.close_button.connect("clicked", lambda _: self.notch.close_notch())
        self.switcher.pack_end(self.close_button, False, False, 0)

        self._size_group = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.HORIZONTAL)
        self._nav_buttons = []
        for child in self.switcher.get_children():
            child.set_can_focus(False)
            if child is not self.close_button:
                self.switcher.set_child_packing(
                    child, True, True, 0, Gtk.PackType.START
                )
                self._size_group.add_widget(child)
                self._nav_buttons.append(child)      # порядок = порядок страниц стека
        self._nav_buttons.append(self.close_button)  # индекс 3

        # --- Скользящий индикатор: Overlay(main=indicator, overlay=switcher) ---
        self.indicator = SlidingIndicator()
        self.switcher_overlay = Gtk.Overlay()
        self.switcher_overlay.add(self.indicator)
        self.switcher_overlay.add_overlay(self.switcher)

        self._indicator_group = Gtk.SizeGroup(mode=Gtk.SizeGroupMode.VERTICAL)
        self._indicator_group.add_widget(self.indicator)
        self._indicator_group.add_widget(self.switcher)

        # после раскладки кнопок пересчитываем цель (без анимации: это ресайз)
        self.switcher.connect_after(
            "size-allocate", lambda *_: self._sync_indicator(animate=False)
        )

        self.stack.connect("notify::visible-child", self._on_stack_child_changed)

        super().__init__(
            name="dashboard",
            orientation="v",
            spacing=8,
            visible=True,
            all_visible=True,
            children=(self.switcher_overlay, self.stack),
            **kwargs,
        )

        self.set_can_focus(True)
        self.connect("key-press-event", self._on_key_press)
        self.connect("destroy", lambda *_: self.cleanup())
        self.show_all()

    # ------------------------------------------------------------------
    def _sync_indicator(self, animate: bool = True) -> None:
        if self._destroyed:
            return
        btn = self._nav_buttons[self._cur_idx]
        w = btn.get_allocated_width()
        h = btn.get_allocated_height()
        if w <= 1 or h <= 1:
            return                                   # ещё не размещена
        pos = btn.translate_coordinates(self.indicator, 0, 0)
        if pos is None:
            return
        self.indicator.move_to(pos[0], pos[1], w, h, animate)

    def _clear_close_focus(self) -> None:
        self.switcher.get_style_context().remove_class("close-focused")
        self.close_button.get_style_context().remove_class("focused")

    def _sync_notch_cw(self, name: str) -> None:
        if name == "dashboard":
            if self.notch._cw not in _DASHBOARD_APPLETS:
                self.notch._cw = "dashboard"
        else:
            self.notch._cw = name

    def _on_stack_child_changed(self, stack, _) -> None:
        if self._destroyed:
            return
        cur_child = stack.get_visible_child()
        for idx, name in enumerate(_NAV_ITEMS[:3]):
            if self._sections.get(name) is cur_child:
                self._cur_idx = idx
                self._clear_close_focus()
                self._sync_notch_cw(name)
                self._sync_indicator()
                break

    def _set_nav_index(self, idx: int) -> None:
        if self._destroyed:
            return
        self._cur_idx = idx
        sw_ctx  = self.switcher.get_style_context()
        btn_ctx = self.close_button.get_style_context()

        if idx < 3:
            self._clear_close_focus()
            name = _NAV_ITEMS[idx]
            self.stack.set_visible_child(self._sections[name])
            self._sync_notch_cw(name)
        else:
            sw_ctx.add_class("close-focused")
            btn_ctx.add_class("focused")

        self._sync_indicator()   # нужен и для close, и для close -> уже видимая вкладка

    def _on_key_press(self, _, event) -> bool:
        if self._destroyed:
            return False

        top = self.get_toplevel()
        focus = top.get_focus()
        if isinstance(focus, Gtk.Entry) and focus.get_can_focus():
            return False

        if event.keyval in (Gdk.KEY_Up, Gdk.KEY_Down):
            return True

        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            if self._cur_idx == 3:
                self.notch.close_notch()
                return True

        if event.keyval not in (Gdk.KEY_Left, Gdk.KEY_Right):
            return False

        step = -1 if event.keyval == Gdk.KEY_Left else 1
        self._set_nav_index((self._cur_idx + step) % len(_NAV_ITEMS))
        return True

    def go_to_section(self, name: str) -> None:
        if self._destroyed:
            return
        if name in _NAV_ITEMS[:3]:
            self._set_nav_index(_NAV_ITEMS.index(name))
        else:
            self.stack.set_visible_child(self._sections[name])
        self.grab_focus()

    def cleanup(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        self.indicator.stop()
        self.dashboard.cleanup()
        self.player.cleanup()
        self.wallpapers.cleanup()
        self.notch = None
        self._sections = None
        self._size_group = None
        self._indicator_group = None
        self._nav_buttons = None