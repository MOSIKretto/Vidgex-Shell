from typing import Dict, Final


FONT_CONFIG: Final[Dict[str, str]] = {
    'family': 'tabler-icons',
    'weight': 'normal'
}

SPAN_TEMPLATE: Final[str] = "<span font-family='{family}' font-weight='{weight}'>{{icon}}</span>"
SPAN: Final[str] = SPAN_TEMPLATE.format(**FONT_CONFIG)


class IconManager:    
    @staticmethod
    def wrap(icon_code: str) -> str:
        return SPAN.format(icon=icon_code)
    
    @staticmethod
    def get_all() -> Dict[str, str]:
        return {k: v for k, v in globals().items() 
                if not k.startswith('_') and isinstance(v, str) and 'span' not in k.lower()}


# Панели
apps: str = IconManager.wrap("&#xf1fd;")
dashboard: str = IconManager.wrap("&#xea87;")
chat: str = IconManager.wrap("&#xf59f;")
windows: str = IconManager.wrap("&#xefe6;")

# Панель управления
colorpicker: str = IconManager.wrap("&#xebe6;")
media: str = IconManager.wrap("&#xf00d;")

# Инструменты
toolbox: str = IconManager.wrap("&#xebca;")
ssfull: str = IconManager.wrap("&#xec3c;")
ssregion: str = IconManager.wrap("&#xf201;")
sswindow: str = IconManager.wrap("&#xeaea;")
screenshots: str = IconManager.wrap("&#xeb0a;")
screenrecord: str = IconManager.wrap("&#xed22;")
recordings: str = IconManager.wrap("&#xeafa;")
ocr: str = IconManager.wrap("&#xfcc3;")
gamemode: str = IconManager.wrap("&#xf026;")
gamemode_off: str = IconManager.wrap("&#xf111;")
close: str = IconManager.wrap("&#xeb55;")

# Метрики
temp: str = IconManager.wrap("&#xeb38;")
disk: str = IconManager.wrap("&#xea88;")
battery: str = IconManager.wrap("&#xea38;")
memory: str = IconManager.wrap("&#xfa97;")
cpu: str = IconManager.wrap("&#xef8e;")
gpu: str = IconManager.wrap("&#xf233;")

# Навигация
chevron_up: str = IconManager.wrap("&#xea62;")
chevron_down: str = IconManager.wrap("&#xea5f;")
chevron_left: str = IconManager.wrap("&#xea60;")
chevron_right: str = IconManager.wrap("&#xea61;")

# Питание
lock: str = IconManager.wrap("&#xeae2;")
suspend: str = IconManager.wrap("&#xece7;")
logout: str = IconManager.wrap("&#xeba8;")
reboot: str = IconManager.wrap("&#xeb13;")
shutdown: str = IconManager.wrap("&#xeb0d;")

# Управление питанием
power_saving: str = IconManager.wrap("&#xed4f;")
power_balanced: str = IconManager.wrap("&#xfa77;")
power_performance: str = IconManager.wrap("&#xec45;")
charging: str = IconManager.wrap("&#xefef;")
discharging: str = IconManager.wrap("&#xefe9;")
alert: str = IconManager.wrap("&#xea06;")
bat_charging: str = IconManager.wrap("&#xeeca;")
bat_discharging: str = IconManager.wrap("&#xf0a1;")
bat_low: str = IconManager.wrap("&#xff1d;")
bat_full: str = IconManager.wrap("&#xea38;")

# Сетевые приложения
wifi_0: str = IconManager.wrap("&#xeba3;")
wifi_1: str = IconManager.wrap("&#xeba4;")
wifi_2: str = IconManager.wrap("&#xeba5;")
wifi_3: str = IconManager.wrap("&#xeb52;")
world: str = IconManager.wrap("&#xeb54;")
world_off: str = IconManager.wrap("&#xf1ca;")
bluetooth: str = IconManager.wrap("&#xea37;")
night: str = IconManager.wrap("&#xeaf8;")
coffee: str = IconManager.wrap("&#xef0e;")
notifications: str = IconManager.wrap("&#xea35;")

wifi_off: str = IconManager.wrap("&#xecfa;")
bluetooth_off: str = IconManager.wrap("&#xeceb;")
night_off: str = IconManager.wrap("&#xf162;")
notifications_off: str = IconManager.wrap("&#xece9;")
notifications_clear: str = IconManager.wrap("&#xf814;")

download: str = IconManager.wrap("&#xea96;")
upload: str = IconManager.wrap("&#xeb47;")

# Bluetooth
bluetooth_connected: str = IconManager.wrap("&#xecea;")
bluetooth_disconnected: str = IconManager.wrap("&#xf081;")

# Медиаплеер
pause: str = IconManager.wrap("&#xf690;")
play: str = IconManager.wrap("&#xf691;")
stop: str = IconManager.wrap("&#xf695;")
skip_back: str = IconManager.wrap("&#xf693;")
skip_forward: str = IconManager.wrap("&#xf694;")
prev: str = IconManager.wrap("&#xf697;")
next: str = IconManager.wrap("&#xf696;")
shuffle: str = IconManager.wrap("&#xf000;")
repeat: str = IconManager.wrap("&#xeb72;")
music: str = IconManager.wrap("&#xeafc;")
rewind_backward_5: str = IconManager.wrap("&#xfabf;")
rewind_forward_5: str = IconManager.wrap("&#xfac7;")

# Громкость
vol_off: str = IconManager.wrap("&#xf1c3;")
vol_mute: str = IconManager.wrap("&#xeb50;")
vol_medium: str = IconManager.wrap("&#xeb4f;")
vol_high: str = IconManager.wrap("&#xeb51;")

mic: str = IconManager.wrap("&#xeaf0;")
mic_mute: str = IconManager.wrap("&#xed16;")

speaker: str = IconManager.wrap("&#x10045;")
headphones: str = IconManager.wrap("&#xfa3c;")
mic_filled: str = IconManager.wrap("&#xfe0f;")

# Разное
circle_plus: str = IconManager.wrap("&#xea69;")
clipboard: str = IconManager.wrap("&#xea6f;")
clip_text: str = IconManager.wrap("&#xf089;")
accept: str = IconManager.wrap("&#xea5e;")
cancel: str = IconManager.wrap("&#xeb55;")
trash: str = IconManager.wrap("&#xeb41;")
config: str = IconManager.wrap("&#xeb20;")
disc: str = IconManager.wrap("&#x1003e;")
disc_off: str = IconManager.wrap("&#xf118;")

# Яркость
brightness_low: str = IconManager.wrap("&#xeb7d;")
brightness_medium: str = IconManager.wrap("&#xeb7e;")
brightness_high: str = IconManager.wrap("&#xeb30;")
brightness: str = IconManager.wrap("&#xee1a;")

# Дашборд
widgets: str = IconManager.wrap("&#xf02c;")
pins: str = IconManager.wrap("&#xec9c;")
kanban: str = IconManager.wrap("&#xec3f;")
wallpapers: str = IconManager.wrap("&#xeb61;")
sparkles: str = IconManager.wrap("&#xf6d7;")

# Прочее
dot: str = IconManager.wrap("&#xf698;")
palette: str = IconManager.wrap("&#xeb01;")
cloud_off: str = IconManager.wrap("&#xed3e;")
loader: str = IconManager.wrap("&#xeca3;")
radar: str = IconManager.wrap("&#xf017;")
emoji: str = IconManager.wrap("&#xeaf7;")
keyboard: str = IconManager.wrap("&#xebd6;")
terminal: str = IconManager.wrap("&#xebef;")
timer_off: str = IconManager.wrap("&#xf146;")
timer_on: str = IconManager.wrap("&#xf756;")
spy: str = IconManager.wrap("&#xf227;")

# Кубики (для случайного выбора)
dice_1: str = IconManager.wrap("&#xf08b;")
dice_2: str = IconManager.wrap("&#xf08c;")
dice_3: str = IconManager.wrap("&#xf08d;")
dice_4: str = IconManager.wrap("&#xf08e;")
dice_5: str = IconManager.wrap("&#xf08f;")
dice_6: str = IconManager.wrap("&#xf090;")