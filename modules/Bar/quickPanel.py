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


class QuickPanel(Window):
    REVEALER_DURATION = 200
    CARD_HEIGHT = 80
    # Буквальное совпадение с дефолтным transition-duration GTK (200мс,
    # как у эталонного MainWindow.stack) на практике ощущалось БЫСТРЕЕ,
    # чем в ноче: панель QuickPanel намного уже содержимого Notch, тот
    # же путь слайда покрывается за те же 200мс, но на узкой карточке
    # любое сокращение длительности субъективно заметнее (короткая
    # дистанция + короткое время = рывок). Поднято до 360мс по
    # обратной связи "слишком быстрая анимация" — ощутимо плавнее.
    TAB_TRANSITION_MS = 360

    CSS_STITCHED = "quick-panel-stitched"
    WINDOW_NAME = "quick-panel-window"
    LAYER_RULE_CMD = 'hyprctl keyword layerrule "noanim, {}"'

    # Персистентный класс "вкладка открыта" на кнопке-триггере
    # (#button-bar). В отличие от :hover, снимается/навешивается
    # только в _activate_tab/_deactivate_tab, то есть живёт ровно
    # столько, сколько соответствующая вкладка реально отображается
    # в QuickPanel — не зависит от положения курсора.
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
        GLib.spawn_command_line_async(self.LAYER_RULE_CMD.format(self.WINDOW_NAME))

    @staticmethod
    def _block_scroll(_widget, _event) -> bool:
        return True

    def _build_ui(self):
        # Переключение вкладок построено на том же механизме, что и
        # Dashboard/Player/Wallpapers в MainWindow (Notch): тот же
        # Gtk.Stack, тот же transition_type="slide-left-right",
        # set_homogeneous(False). Длительность перехода подобрана
        # отдельно под габариты этой панели (см. TAB_TRANSITION_MS) —
        # буквальное совпадение с GTK-дефолтом эталона визуально
        # оказалось слишком быстрым на узкой карточке QuickPanel.
        # homogeneous=False означает, что во время перехода Stack
        # ориентируется на размер текущей видимой вкладки, а не на
        # максимум среди всех сразу — так же, как в эталоне. На
        # статичность окна QuickPanel это не влияет: внешний размер
        # всё равно жёстко фиксирует ScrolledWindow через
        # min_content_size/_sync_width, Stack внутри него может
        # запрашивать что угодно, наружу это не проезжает.
        self.stack = Stack(
            name="quick-panel-stack",
            transition_type="slide-left-right",
            h_expand=True,
            v_expand=True,
        )
        self.stack.set_homogeneous(False)
        self.stack.set_transition_duration(self.TAB_TRANSITION_MS)

        # ScrolledWindow с фиксированным CARD_HEIGHT — единственная
        # допустимая точка задания высоты карточки. Никакая вкладка
        # (включая Battery) больше не меняет min_content_height во
        # время работы: окно QuickPanel обязано иметь статичный
        # размер (см. BatteryTab — ряд Bluetooth-кружков схлопывается
        # по ширине внутри собственной строки, высота не затрагивается).
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
        # build_trigger_button, что и у Wi-Fi/Battery (см.
        # wifiTab.py) — все четыре кнопки вызова собираются
        # абсолютно идентичным кодом, различается только
        # количество/имена передаваемых Label.
        label = Label(name="button-bar-label", markup=markup)
        return build_trigger_button(label)

    def _build_tabs(self) -> None:
        # Имена атрибутов кнопок (net_btn/util_btn/session_btn) —
        # переименованы из wifi_btn/tools_btn/power_btn по явному
        # запросу, чисто в коде, без изменения их визуального вида
        # (иконки/CSS не трогались). Единственное место использования
        # вовне — Bar.py, обновлено синхронно.
        self._wifi_trigger = WifiTrigger(self.network_client)
        self._battery_trigger = BatteryTrigger()
        self.net_btn = self._wifi_trigger.button
        self.battery_btn = self._battery_trigger.button
        self.util_btn = self._make_trigger_button(icons.photo)
        self.session_btn = self._make_trigger_button(icons.shutdown)

        # Строковые ключи вкладок ("net"/"util"/"session" вместо
        # "wifi"/"tools"/"power") — чисто внутренние идентификаторы
        # для self._tabs / Stack.add_named, нигде не отображаются
        # пользователю (не путать с CSS name="wifi-tab" и т.п. внутри
        # самих классов вкладок — те не трогались).
        self.add_tab("net", WifiTab(self.network_client), self.net_btn)
        self.add_tab("battery", BatteryTab(self.bluetooth_client), self.battery_btn)
        self.add_tab("util", ToolsTab(self.close), self.util_btn)
        self.add_tab("session", PowerTab(self.close), self.session_btn)

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

    def _switch_tab(self, name: str) -> None:
        if name == self._active_tab:
            return
        self._deactivate_tab(self._active_tab)
        self._activate_tab(name)
        self.stack.set_visible_child_name(name)

    def _activate_tab(self, name: str) -> None:
        # Персистентный индикатор "вкладка открыта": класс вешается
        # на саму кнопку-триггер (#button-bar) и держится, пока эта
        # вкладка реально показана в QuickPanel — независимо от того,
        # где сейчас курсор мыши (см. CSS-блок .trigger-active в
        # bar.css, отдельно от :hover).
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

    def is_open(self) -> bool:
        return self._open_flag

    def cleanup(self) -> None:
        self._deactivate_tab(self._active_tab)
        self._active_tab = None
        self._open_flag = False

        self._wifi_trigger.cleanup()
        self._battery_trigger.cleanup()
        self.network_client.cleanup()