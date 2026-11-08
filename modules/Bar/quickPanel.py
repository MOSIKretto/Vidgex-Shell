from fabric.bluetooth import BluetoothClient
from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label
from fabric.widgets.revealer import Revealer
from fabric.widgets.scrolledwindow import ScrolledWindow
from fabric.widgets.stack import Stack
from gi.repository import GLib, Gtk

import services.icons as icons
from services.corners import MyCorner
from services.network import NetworkClient
from services.wayland import WaylandWindow as Window

from modules.Bar.QuickPanel.wifiTab import build_trigger_button, WifiTrigger, WifiTab
from modules.Bar.QuickPanel.batteryTab import BatteryTrigger, BatteryTab
from modules.Bar.QuickPanel.toolsTab import ToolsTab
from modules.Bar.QuickPanel.powerTab import PowerTab


def _smootherstep(t: float) -> float:
    return t * t * t * (t * (6.0 * t - 15.0) + 10.0)


class TriggerRow(Box):
    """Ряд кнопок-триггеров, рисующий скользящую плашку ПОД кнопками.

    Плашка рисуется в обработчике сигнала "draw". Он срабатывает ДО
    стандартной отрисовки Gtk.Box (то есть до детей), поэтому плашка
    оказывается под кнопками. do_draw не переопределяется, отрисовку
    детей мы не трогаем.
    Состояние плашки: [x, y, w, h, alpha]. Цвет и border-radius берутся
    из CSS: `#quick-trigger-row.trigger-plate` (см. bar.css).
    """

    PLATE_CLASS = "trigger-plate"

    def __init__(self, duration_ms: int = 350, **kwargs):
        # Атрибуты задаём ДО super().__init__, чтобы обработчики сигналов,
        # сработавшие во время инициализации, не упали на отсутствующем поле.
        self._duration = duration_ms * 1000      # микросекунды (frame clock)
        self._cur = None                         # текущее [x, y, w, h, a]
        self._from = None
        self._to = None                          # цель (x, y, w, h, a)
        self._t0 = None
        self._tick_id = 0
        super().__init__(**kwargs)
        self.connect("draw", self._on_draw_plate)

    # --- публичное API -------------------------------------------------
    def move_to(self, x, y, w, h, animate: bool = True) -> None:
        """Переехать к прямоугольнику, сохранив текущую целевую прозрачность."""
        alpha = self._to[4] if self._to else 1.0
        self._go((x, y, w, h, alpha), animate)

    def appear_at(self, x, y, w, h, animate: bool = True) -> None:
        """Появиться (fade-in) сразу на месте, без скольжения."""
        self._go((x, y, w, h, 1.0), animate, snap_rect=True)

    def disappear(self, animate: bool = True) -> None:
        """Плавно погаснуть, не двигаясь."""
        if self._to is None:
            return
        x, y, w, h, _ = self._to
        self._go((x, y, w, h, 0.0), animate)

    def stop(self) -> None:
        if self._tick_id:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = 0

    # --- внутреннее ----------------------------------------------------
    @staticmethod
    def _animations_enabled() -> bool:
        return Gtk.Settings.get_default().get_property("gtk-enable-animations")

    def _go(self, target, animate: bool, snap_rect: bool = False) -> None:
        if self._cur is None:
            # первый показ: стартуем невидимыми прямо в целевом месте
            self._cur = [target[0], target[1], target[2], target[3], 0.0]
        if snap_rect:
            self._cur[:4] = list(target[:4])
            self.queue_draw()

        if target == self._to:
            return
        self._to = target

        # Ресайз во время анимации: только обновляем цель, плашка доедет сама
        if not animate and self._tick_id:
            return

        if not animate or not self._animations_enabled() or not self.get_mapped():
            self.stop()
            self._cur = list(target)
            self.queue_draw()
            return

        self._from = list(self._cur)
        self._t0 = None
        if not self._tick_id:
            self._tick_id = self.add_tick_callback(self._on_tick)

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

    def _paint_plate(self, cr, x, y, w, h) -> None:
        ctx = self.get_style_context()
        ctx.save()
        ctx.add_class(self.PLATE_CLASS)
        Gtk.render_background(ctx, cr, x, y, w, h)
        Gtk.render_frame(ctx, cr, x, y, w, h)
        ctx.restore()

    def _on_draw_plate(self, _widget, cr) -> bool:
        if self._cur is None:
            return False
        x, y, w, h, a = self._cur
        if a <= 0.001:
            return False

        cr.save()
        if a >= 0.999:
            self._paint_plate(cr, x, y, w, h)
        else:
            cr.push_group()
            self._paint_plate(cr, x, y, w, h)
            cr.pop_group_to_source()
            cr.paint_with_alpha(a)
        cr.restore()
        return False   # False: стандартная отрисовка (дети) выполнится дальше


class QuickPanel(Window):
    REVEALER_DURATION = 200
    CARD_HEIGHT = 80
    # Слайд содержимого между вкладками (easing у Gtk.Stack зашит в GTK).
    # Синхронизировано с _STACK_MS в MainWindow и _APPLET_STACK_MS в Dashboard.
    TAB_TRANSITION_MS = 480
    # Скольжение плашки под кнопками-триггерами (как _INDICATOR_MS в MainWindow).
    TRIGGER_INDICATOR_MS = 350

    CSS_STITCHED = "quick-panel-stitched"
    WINDOW_NAME = "quick-panel-window"
    LAYER_RULE_CMD = 'hyprctl keyword layerrule "noanim, {}"'

    # Персистентный класс "вкладка открыта" на кнопке-триггере
    # (#button-bar). Снимается/навешивается только в
    # _activate_tab/_deactivate_tab, не зависит от положения курсора.
    # Фон активной кнопки рисует TriggerRow, класс меняет только цвет
    # иконки/текста (см. bar.css).
    TRIGGER_ACTIVE_CLASS = "trigger-active"

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

        self._open_flag = False
        self._active_tab = None
        self._tabs = {}
        self._triggers = {}
        self._locked_width = None
        self._bar_group = None

        self.network_client = NetworkClient()
        self.bluetooth_client = BluetoothClient()

        self._build_ui()
        self._build_tabs()

        self.show_all()
        self._disable_animations()

    def _setup_transparency(self):
        visual = self.get_screen().get_rgba_visual()
        self.set_app_paintable(True)
        self.set_visual(visual)

    def _disable_animations(self):
        # noanim отключает только анимации композитора (Hyprland) для
        # layer-окна. Внутренние анимации GTK (слайд Gtk.Stack, плашка)
        # он не затрагивает.
        GLib.spawn_command_line_async(self.LAYER_RULE_CMD.format(self.WINDOW_NAME))

    @staticmethod
    def _block_scroll(_widget, _event) -> bool:
        return True

    def _build_ui(self):
        # Gtk.Stack, slide-left-right. Горизонтально неоднородный (ширину
        # всё равно фиксирует ScrolledWindow), вертикально однородный.
        # ВАЖНО: на #quick-panel-stack не должно быть padding в CSS —
        # Gtk.Stack рисует слайд относительно всего своего бокса, и
        # содержимое на время перехода "уезжает" вверх. Отступы задаются
        # на самих вкладках (#wifi-tab и т.д.).
        # ВАЖНО: ни одна вкладка не должна быть выше CARD_HEIGHT, иначе
        # Viewport растянет стек и содержимое ВСЕХ вкладок съедет вниз.
        self.stack = Stack(
            name="quick-panel-stack",
            transition_type="slide-left-right",
            h_expand=True,
            v_expand=True,
        )
        self.stack.set_hhomogeneous(False)
        self.stack.set_vhomogeneous(True)
        self.stack.set_interpolate_size(False)
        self.stack.set_transition_duration(self.TAB_TRANSITION_MS)

        # ScrolledWindow с фиксированным CARD_HEIGHT — единственная
        # допустимая точка задания высоты карточки.
        self.scrolled = ScrolledWindow(
            name="quick-panel-scroll",
            min_content_size=(-1, self.CARD_HEIGHT),
            propagate_height=False,
            h_expand=True,
            v_expand=True,
            child=self.stack,
        )
        self.scrolled.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.EXTERNAL)
        self.scrolled.set_propagate_natural_width(False)
        self.scrolled.set_kinetic_scrolling(False)
        self.scrolled.connect("scroll-event", self._block_scroll)

        # Страховка: Viewport не должен сдвигать содержимое по вертикали.
        self.scrolled.get_vadjustment().connect("value-changed", self._pin_vscroll)

        self.card = Box(name="quick-panel-card", orientation="v", children=[self.scrolled])

        self._bottom_corner = Box(
            name="quick-panel-bottom-corner",
            children=[MyCorner("top-right")],
        )
        corner_row = Box(
            orientation="h",
            children=[Box(h_expand=True), self._bottom_corner],
        )

        card_col = Box(orientation="v", children=[self.card, corner_row])

        self.revealer = Revealer(
            name="quick-panel-revealer",
            transition_type="slide-left",
            transition_duration=self.REVEALER_DURATION,
            child_revealed=False,
            child=card_col,
        )

        self._keepalive = Box(name="quick-panel-keepalive", style="background: transparent;")
        self._keepalive.set_size_request(1, 1)

        self.add(Box(orientation="h", children=[self._keepalive, self.revealer]))

    @staticmethod
    def _pin_vscroll(adjustment) -> None:
        if adjustment.get_value() != 0:
            adjustment.set_value(0)

    def bind_width_to(self, widget) -> None:
        self._bar_group = widget
        widget.connect("size-allocate", self._sync_width)
        self._sync_width(widget)

    def _sync_width(self, widget, *_args) -> None:
        width = widget.get_allocated_width()
        if width <= 0 or width == self._locked_width:
            return
        self._locked_width = width
        self.scrolled.set_min_content_width(width)

    def _set_stitched(self, stitched: bool) -> None:
        card_ctx = self.card.get_style_context()
        if stitched:
            card_ctx.add_class(self.CSS_STITCHED)
        else:
            card_ctx.remove_class(self.CSS_STITCHED)

        if self._bar_group is None:
            return
        bar_ctx = self._bar_group.get_style_context()
        if stitched:
            bar_ctx.add_class(self.CSS_STITCHED)
        else:
            bar_ctx.remove_class(self.CSS_STITCHED)

    def _make_trigger_button(self, markup: str) -> Button:
        # Единая точка сборки для Tools/Power — тот же каркас
        # build_trigger_button, что и у Wi-Fi/Battery (см. wifiTab.py).
        label = Label(name="button-bar-label", markup=markup)
        return build_trigger_button(label)

    def _build_tabs(self) -> None:
        self._wifi_trigger = WifiTrigger(self.network_client)
        self._battery_trigger = BatteryTrigger()
        self.net_btn = self._wifi_trigger.button
        self.battery_btn = self._battery_trigger.button
        self.util_btn = self._make_trigger_button(icons.photo)
        self.session_btn = self._make_trigger_button(icons.shutdown)

        # Строковые ключи вкладок ("net"/"battery"/"util"/"session") —
        # внутренние идентификаторы для self._tabs / Stack.add_named.
        self.add_tab("net", WifiTab(self.network_client), self.net_btn)
        self.add_tab("battery", BatteryTab(self.bluetooth_client), self.battery_btn)
        self.add_tab("util", ToolsTab(self.close), self.util_btn)
        self.add_tab("session", PowerTab(self.close), self.session_btn)

        self._build_trigger_strip()

    def _build_trigger_strip(self) -> None:
        """Ряд кнопок-триггеров; плашку под ними рисует сам TriggerRow.

        trigger_strip вставляется в Bar (см. Bar._build) вместо четырёх
        отдельных кнопок. Кнопки нельзя добавлять в Bar ещё раз: у них
        теперь есть родитель (этот ряд).
        """
        buttons = list(self._triggers.values())

        self._trigger_row = TriggerRow(
            duration_ms=self.TRIGGER_INDICATOR_MS,
            name="quick-trigger-row",
            orientation="h",
            spacing=4,
            v_align="fill",
            children=buttons,
        )
        self.trigger_strip = self._trigger_row   # имя для Bar.py

        # Ширина кнопок меняется (процент батареи, смена иконок), поэтому
        # плашка должна следовать за активной кнопкой без анимации.
        for btn in buttons:
            btn.connect_after("size-allocate", self._on_trigger_allocate)
        self._trigger_row.connect_after("size-allocate", self._on_trigger_allocate)

        self._trigger_row.show_all()

    def _trigger_rect(self, name: str | None):
        btn = self._triggers.get(name) if name else None
        if btn is None:
            return None
        w = btn.get_allocated_width()
        h = btn.get_allocated_height()
        if w <= 1 or h <= 1:
            return None
        pos = btn.translate_coordinates(self._trigger_row, 0, 0)
        if pos is None:
            return None
        return pos[0], pos[1], w, h

    def _on_trigger_allocate(self, *_args) -> None:
        if self._active_tab is None:
            return
        rect = self._trigger_rect(self._active_tab)
        if rect is not None:
            self._trigger_row.move_to(*rect, animate=False)

    def add_tab(self, name: str, widget: Box, trigger_button) -> None:
        self._tabs[name] = widget
        self.stack.add_named(widget, name)
        trigger_button.connect("clicked", self._on_trigger_clicked, name)
        self._triggers[name] = trigger_button

    def _on_trigger_clicked(self, _button, name: str) -> None:
        if self._open_flag and self._active_tab == name:
            self.close()
        elif self._open_flag:
            self._switch_tab(name)
        else:
            self.open(name)

    def open(self, name: str) -> None:
        self._open_flag = True
        self._activate_tab(name)
        self.stack.set_visible_child_name(name)
        self.revealer.set_reveal_child(True)
        self._set_stitched(True)

        rect = self._trigger_rect(name)
        if rect is not None:
            self._trigger_row.appear_at(*rect)

    def _switch_tab(self, name: str) -> None:
        if name == self._active_tab:
            return
        self._deactivate_tab(self._active_tab)
        self._activate_tab(name)
        self.stack.set_visible_child_name(name)

        rect = self._trigger_rect(name)
        if rect is not None:
            self._trigger_row.move_to(*rect)

    def _activate_tab(self, name: str) -> None:
        self._active_tab = name
        self._tabs[name].activate()
        self._triggers[name].get_style_context().add_class(self.TRIGGER_ACTIVE_CLASS)

    def _deactivate_tab(self, name: str | None) -> None:
        if name is None:
            return
        self._tabs[name].deactivate()
        self._triggers[name].get_style_context().remove_class(self.TRIGGER_ACTIVE_CLASS)

    def close(self) -> None:
        self._open_flag = False
        self.revealer.set_reveal_child(False)
        self._set_stitched(False)
        self._deactivate_tab(self._active_tab)
        self._active_tab = None
        self._trigger_row.disappear()

    def is_open(self) -> bool:
        return self._open_flag

    def cleanup(self) -> None:
        self._deactivate_tab(self._active_tab)
        self._active_tab = None
        self._open_flag = False

        self._trigger_row.stop()
        self._wifi_trigger.cleanup()
        self._battery_trigger.cleanup()
        self.network_client.cleanup()