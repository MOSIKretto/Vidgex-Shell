# modules/Bar/QuickPanel/powerTab.py
from gi.repository import Gio, GLib

from fabric.widgets.box import Box
from fabric.widgets.button import Button
from fabric.widgets.label import Label

import services.icons as icons

_LOGIND_NAME = "org.freedesktop.login1"
_LOGIND_PATH = "/org/freedesktop/login1"
_LOGIND_IFACE = "org.freedesktop.login1.Manager"

# Системная шина кэшируется на уровне модуля и переиспользуется ВСЕМИ
# вызовами _logind_call — и всеми PowerTab, если баров/мониторов
# несколько. Раньше Gio.bus_get() заново поднимал подключение к D-Bus
# на КАЖДЫЙ клик Shutdown/Reboot/Suspend, хотя соединение дешевле
# получить один раз и держать открытым (ровно так же, как уже
# сделано для NetworkClient/BluetoothClient) — это не idle-нагрузка
# (кэш не создаёт процессов в покое), а устранение лишней работы
# именно в момент активности (п.2 требований).
_system_bus = None


def _get_system_bus(callback) -> None:
    global _system_bus
    if _system_bus is not None:
        callback(_system_bus)
        return
    Gio.bus_get(Gio.BusType.SYSTEM, None, lambda src, res: _on_bus_got(res, callback))


def _on_bus_got(result, callback) -> None:
    global _system_bus
    try:
        _system_bus = Gio.bus_get_finish(result)
    except GLib.Error:
        # Шина недоступна (сессия уже завершается, D-Bus daemon не
        # отвечает) — действие просто не выполнится, ретраить нечем.
        return
    callback(_system_bus)


def _logind_call(method: str) -> None:
    _get_system_bus(lambda bus: _call_logind(bus, method))


def _call_logind(bus, method: str) -> None:
    bus.call(
        _LOGIND_NAME, _LOGIND_PATH, _LOGIND_IFACE, method,
        GLib.Variant("(b)", (True,)), None, Gio.DBusCallFlags.NONE, -1, None,
        _on_call_finished,
    )


def _on_call_finished(source, result) -> None:
    try:
        source.call_finish(result)
    except GLib.Error:
        # Logind мог начать завершение сессии раньше, чем пришёл ответ
        # на вызов (гонка при Shutdown/Reboot/Suspend) — результат нам
        # в этом случае уже не нужен.
        pass


class PowerTab(Box):
    _ACTIONS = (
        (icons.shutdown, "Shutdown", lambda: _logind_call("PowerOff")),
        (icons.reboot,   "Reboot",   lambda: _logind_call("Reboot")),
        (icons.logout,   "Logout",   lambda: GLib.spawn_command_line_async('hyprctl dispatch "hl.dsp.exit()"')),
        (icons.suspend,  "Suspend",  lambda: _logind_call("Suspend")),
    )
    CSS_BTN = "power-icon-btn"
    def __init__(self, close_panel, **kwargs):
        super().__init__(name="power-tab", orientation="v", spacing=0, v_expand=True, **kwargs)
        self._close_panel = close_panel
        row = Box(orientation="h", spacing=6, h_align="center", v_align="center")
        for icon_markup, tooltip, action in self._ACTIONS:
            btn = Button(child=Label(markup=icon_markup), tooltip_markup=f"<b>{tooltip}</b>", can_focus=False)
            btn.get_style_context().add_class(self.CSS_BTN)
            btn.connect("clicked", self._on_clicked, action)
            row.add(btn)
        self.add(Box(orientation="v", v_expand=True))
        self.add(row)
        self.add(Box(orientation="v", v_expand=True))
    def activate(self) -> None: pass
    def deactivate(self) -> None: pass
    def _on_clicked(self, _button, action) -> None:
        action(); self._close_panel()