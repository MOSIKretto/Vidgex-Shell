# modules/Bar/QuickPanel/wifiTab.py
from bisect import bisect_right

from gi.repository import GLib

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label

import services.icons as icons
from services.network import Subscriptions, subscribe as net_sub, unsubscribe as net_unsub, fmt_speed_short

_TH = (25, 50, 75)
_WI = (icons.wifi_0, icons.wifi_1, icons.wifi_2, icons.wifi_3)
_AN = (icons.wifi_0, icons.wifi_1, icons.wifi_2, icons.wifi_3, icons.wifi_2, icons.wifi_1)
_DISCONNECTED = "Disconnected"
_ANIM_INTERVAL_MS = 500


def _cancel(source):
    if source is not None:
        GLib.source_remove(source)
    return None


def build_trigger_button(icon_label: Label, *extra_widgets: Label) -> Button:
    """Единственная точка сборки каркаса "кнопки вызова" QuickPanel —
    используется ВСЕМИ четырьмя кнопками (Wi-Fi, Battery, Tools, Power;
    Tools/Power собираются через неё же в __init__.py). Вызывающий код
    сам создаёт Label(ы) с нужным именем/классами (у Wi-Fi/Tools/Power —
    один #button-bar-label, у Battery — пара #metrics-icon +
    #metrics-level), а эта функция собирает вокруг них идентичную
    структуру Button(#button-bar) -> Box(#button-bar-content).

    Все визуальные состояния кнопки (hover, active) описываются в CSS
    (quickpanel.css/bar.css) через #button-bar:hover / #button-bar:active
    — никакого CSS не генерируется и не подключается из Python: это
    единственный источник правды для стилей. can_focus=False — иначе
    клик мышью переводит фокус на кнопку, и системная GTK-тема может
    рисовать собственную рамку/тень для состояния :focus независимо от
    нашего CSS.

    Тултипы у кнопок вызова не предусмотрены намеренно."""
    children = [icon_label, *extra_widgets]
    wrapper = Box(
        name="button-bar-content",
        orientation="h", spacing=0,
        h_align="center", v_align="center",
        h_expand=False, v_expand=False,
        children=children,
    )
    return Button(name="button-bar", v_align="center", can_focus=False, child=wrapper)


class WifiIconState:
    def __init__(self, client, on_icon, on_subtitle=None):
        self._cl = client
        self._on_icon = on_icon
        self._on_subtitle = on_subtitle
        self._subs = Subscriptions()
        self._wifi = None
        self._uid = None
        self._aid = None
        self._ast = 0
        self._last_ico = None
        self._running = False
    def start(self) -> None:
        self._running = True
        self._last_ico = None
        self._subs.connect(self._cl, "device-ready", self._on_ready)
        self._subs.connect(self._cl, "notify::primary-device", self._sched)
        self._on_ready()
    def stop(self) -> None:
        self._running = False
        self._aid = _cancel(self._aid)
        self._uid = _cancel(self._uid)
        self._subs.clear()
        self._wifi = None
    def _on_ready(self, *_args) -> None:
        self._wifi = self._subs.rebind(self._wifi, self._cl.wifi_device, "changed", self._sched)
        self._sched()
    def _sched(self, *_args) -> None:
        if self._uid is None:
            self._uid = GLib.timeout_add(100, self._flush)
    def _flush(self) -> bool:
        self._uid = None
        self.update_state()
        return False
    def _set_icon(self, markup: str) -> None:
        if self._last_ico != markup:
            self._last_ico = markup
            self._on_icon(markup)
    def _start_anim(self) -> None:
        if self._aid is None:
            self._ast = 0
            self._aid = GLib.timeout_add(_ANIM_INTERVAL_MS, self._anim)
    def _anim(self) -> bool:
        if not self._running:
            self._aid = None
            return False
        self._set_icon(_AN[self._ast])
        self._ast = (self._ast + 1) % len(_AN)
        return True
    def _show_static(self, icon_markup: str) -> None:
        self._aid = _cancel(self._aid)
        self._set_icon(icon_markup)
        if self._on_subtitle:
            self._on_subtitle(None)
    def update_state(self) -> None:
        if not self._running:
            return
        wifi, eth = self._cl.wifi_device, self._cl.ethernet_device
        disabled = wifi is not None and not wifi.enabled
        if disabled:
            static = icons.wifi_off
        elif self._cl.primary_device == "ethernet":
            static = icons.world if eth.internet == "activated" else icons.world_off
        elif wifi is None:
            static = icons.wifi_off
        else:
            static = None
        if static is not None:
            self._show_static(static)
            return

        internet = wifi.internet
        if internet == "activating":
            # Идёт реальная попытка подключения — анимация оправдана:
            # процесс ограничен по времени, а не бесконечен.
            if self._on_subtitle:
                self._on_subtitle(None)
            self._start_anim()
            return

        if wifi.ssid not in ("", _DISCONNECTED) and internet == "activated":
            if self._on_subtitle:
                self._on_subtitle(wifi.ssid)
            self._aid = _cancel(self._aid)
            self._set_icon(_WI[bisect_right(_TH, wifi.strength)])
            return

        # Wi-Fi включён, но сеть не выбрана и подключение не идёт —
        # это штатный ПРОСТОЙ, а не процесс. Раньше сюда безусловно
        # запускалась бесконечная анимация (_start_anim): 500мс-таймер
        # крутился ВЕЧНО, пока пользователь просто не подключён к
        # сети — прямое нарушение нулевой нагрузки в простое (п.8),
        # и вдобавок вводящая в заблуждение индикация "идёт
        # подключение" там, где по факту ничего не происходит.
        # Теперь — статичная иконка без таймера.
        self._show_static(icons.wifi_0)


class WifiTrigger:
    """Контроллер кнопки вызова Wi-Fi-вкладки: владеет иконкой и её
    анимацией состояния (WifiIconState). Сама Button собрана через
    build_trigger_button — общий каркас для всех четырёх кнопок
    вызова, единый для всех по коду и по CSS. У кнопки ровно ОДНО
    визуальное состояние — hover. Тултип отсутствует намеренно (убран
    со всех кнопок вызова)."""

    def __init__(self, client):
        self._client = client
        self._label = Label(name="button-bar-label", markup=icons.wifi_off)
        self.button = build_trigger_button(self._label)
        self._state = WifiIconState(client, on_icon=self._label.set_markup)
        self._state.start()

    def cleanup(self) -> None:
        self._state.stop()


class WifiTab(Box):
    def __init__(self, client, **kwargs):
        super().__init__(name="wifi-tab", orientation="v", spacing=2, h_align="center", **kwargs)
        self._client = client

        self._icon = Label(name="wifi-tab-icon", markup=icons.wifi_off)
        label = Label(label="Wi-Fi", name="wifi-tab-label")

        self._subtitle = Label(
            name="wifi-tab-ssid", label=f"○ {_DISCONNECTED}", h_align="start",
            width_chars=14, max_width_chars=14, ellipsization="end",
        )
        self._signal_lbl = Label(
            label="—", name="wifi-tab-signal", h_align="start",
            width_chars=13, max_width_chars=13, ellipsization="end",
        )

        row1 = Box(name="wifi-tab-row", orientation="h", spacing=8, h_align="center", children=[
            self._icon,
            label,
            Box(name="wifi-tab-vdivider"),
            self._subtitle,
            Box(name="wifi-tab-vdivider"),
            self._signal_lbl,
        ])

        self._dl_icon = Label(markup=icons.download, name="wifi-speed-icon")
        self._ul_icon = Label(markup=icons.upload, name="wifi-speed-icon")
        self._dl_lbl = Label(
            label="—", name="wifi-speed-val", h_align="start",
            width_chars=8, max_width_chars=8, ellipsization="end",
        )
        self._ul_lbl = Label(
            label="—", name="wifi-speed-val", h_align="start",
            width_chars=8, max_width_chars=8, ellipsization="end",
        )
        row2 = Box(name="wifi-tab-speed", orientation="h", spacing=12, h_align="center", children=[
            Box(orientation="h", spacing=4, children=[self._dl_icon, self._dl_lbl]),
            Box(orientation="h", spacing=4, children=[self._ul_icon, self._ul_lbl]),
        ])

        self.add(row1)
        self.add(row2)
        # Распорка — см. докстринг класса. Должна быть добавлена ПОСЛЕДНЕЙ,
        # иначе "съест" место сверху, а не снизу.
        self.add(Box(v_expand=True))

        self._state = WifiIconState(client, on_icon=self._icon.set_markup, on_subtitle=self._on_subtitle)

    def _on_subtitle(self, text: str | None) -> None:
        # Точка-индикатор в духе macOS Control Center (●/○) перед SSID.
        dot = "●" if text else "○"
        self._subtitle.set_label(f"{dot} {text if text else _DISCONNECTED}")
        self._update_signal()

    def _update_signal(self):
        wifi = self._client.wifi_device
        if wifi and wifi.ssid not in ("", _DISCONNECTED) and wifi.internet == "activated":
            self._signal_lbl.set_label(f"{wifi.strength}% · {wifi.frequency/1000:.1f}GHz")
        else:
            self._signal_lbl.set_label("—")

    def _on_net_speed(self, dl: float, ul: float) -> None:
        # Направление показывает иконка (download/upload) слева от
        # значения — юникод-стрелка в тексте убрана как дублирующий
        # индикатор направления.
        self._dl_lbl.set_label(fmt_speed_short(dl))
        self._ul_lbl.set_label(fmt_speed_short(ul))

    def activate(self) -> None:
        self._state.start()
        net_sub(self)
        self._update_signal()

    def deactivate(self) -> None:
        self._state.stop()
        net_unsub(self)