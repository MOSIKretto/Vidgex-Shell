FONT_CONFIG = {
    'family': 'tabler-icons',
    'weight': 'normal'
}

SPAN_TEMPLATE = "<span font-family='{family}' font-weight='{weight}'>{{icon}}</span>"
SPAN = SPAN_TEMPLATE.format(**FONT_CONFIG)


class IconManager:    
    @staticmethod
    def wrap(icon_code: str) -> str:
        return SPAN.format(icon=icon_code)
    
    @staticmethod
    def get_all():
        return {k: v for k, v in globals().items() if not k.startswith('_') and isinstance(v, str) and 'span' not in k.lower()}


# Панели
apps = IconManager.wrap("&#xf1fd;")
dashboard = IconManager.wrap("&#xea87;")
chat = IconManager.wrap("&#xf59f;")
windows = IconManager.wrap("&#xefe6;")

# Панель управления
colorpicker = IconManager.wrap("&#xebe6;")
media = IconManager.wrap("&#xf00d;")

# Инструменты
toolbox = IconManager.wrap("&#xebca;")
ssfull = IconManager.wrap("&#xec3c;")
ssregion = IconManager.wrap("&#xf201;")
sswindow = IconManager.wrap("&#xeaea;")
screenshots = IconManager.wrap("&#xeb0a;")
screenrecord = IconManager.wrap("&#xed22;")
recordings = IconManager.wrap("&#xeafa;")
ocr = IconManager.wrap("&#xfcc3;")
gamemode = IconManager.wrap("&#xf026;")
gamemode_off = IconManager.wrap("&#xf111;")
close = IconManager.wrap("&#xeb55;")

# Метрики
temp = IconManager.wrap("&#xeb38;")
disk = IconManager.wrap("&#xea88;")
battery = IconManager.wrap("&#xea38;")
memory = IconManager.wrap("&#xfa97;")
cpu = IconManager.wrap("&#xef8e;")
gpu = IconManager.wrap("&#xf233;")

# Навигация
chevron_up = IconManager.wrap("&#xea62;")
chevron_down = IconManager.wrap("&#xea5f;")
chevron_left = IconManager.wrap("&#xea60;")
chevron_right = IconManager.wrap("&#xea61;")

# Питание
lock = IconManager.wrap("&#xeae2;")
suspend = IconManager.wrap("&#xece7;")
logout = IconManager.wrap("&#xeba8;")
reboot = IconManager.wrap("&#xeb13;")
shutdown = IconManager.wrap("&#xeb0d;")

# Управление питанием
power_saving = IconManager.wrap("&#xed4f;")
power_balanced = IconManager.wrap("&#xfa77;")
power_performance = IconManager.wrap("&#xec45;")
charging = IconManager.wrap("&#xefef;")
discharging = IconManager.wrap("&#xefe9;")
alert = IconManager.wrap("&#xea06;")
bat_charging = IconManager.wrap("&#xeeca;")
bat_discharging = IconManager.wrap("&#xf0a1;")
bat_low = IconManager.wrap("&#xff1d;")
bat_full = IconManager.wrap("&#xea38;")

# Сетевые приложения
wifi_0 = IconManager.wrap("&#xeba3;")
wifi_1 = IconManager.wrap("&#xeba4;")
wifi_2 = IconManager.wrap("&#xeba5;")
wifi_3 = IconManager.wrap("&#xeb52;")
world = IconManager.wrap("&#xeb54;")
world_off = IconManager.wrap("&#xf1ca;")
bluetooth = IconManager.wrap("&#xea37;")
night = IconManager.wrap("&#xeaf8;")
coffee = IconManager.wrap("&#xef0e;")
notifications = IconManager.wrap("&#xea35;")

wifi_off = IconManager.wrap("&#xecfa;")
bluetooth_off = IconManager.wrap("&#xeceb;")
night_off = IconManager.wrap("&#xf162;")
notifications_off = IconManager.wrap("&#xece9;")
notifications_clear = IconManager.wrap("&#xf814;")

download = IconManager.wrap("&#xea96;")
upload = IconManager.wrap("&#xeb47;")

# Bluetooth
bluetooth_connected = IconManager.wrap("&#xecea;")
bluetooth_disconnected = IconManager.wrap("&#xf081;")

# Медиаплеер
pause = IconManager.wrap("&#xf690;")
play = IconManager.wrap("&#xf691;")
stop = IconManager.wrap("&#xf695;")
skip_back = IconManager.wrap("&#xf693;")
skip_forward = IconManager.wrap("&#xf694;")
prev = IconManager.wrap("&#xf697;")
next = IconManager.wrap("&#xf696;")
shuffle = IconManager.wrap("&#xf000;")
repeat = IconManager.wrap("&#xeb72;")
music = IconManager.wrap("&#xeafc;")
rewind_backward_5 = IconManager.wrap("&#xfabf;")
rewind_forward_5 = IconManager.wrap("&#xfac7;")

# Громкость
vol_off = IconManager.wrap("&#xf1c3;")
vol_mute = IconManager.wrap("&#xeb50;")
vol_medium = IconManager.wrap("&#xeb4f;")
vol_high = IconManager.wrap("&#xeb51;")

mic = IconManager.wrap("&#xeaf0;")
mic_mute = IconManager.wrap("&#xed16;")

speaker = IconManager.wrap("&#x10045;")
headphones = IconManager.wrap("&#xfa3c;")
mic_filled = IconManager.wrap("&#xfe0f;")

# Разное
circle_plus = IconManager.wrap("&#xea69;")
clipboard = IconManager.wrap("&#xea6f;")
clip_text = IconManager.wrap("&#xf089;")
accept = IconManager.wrap("&#xea5e;")
cancel = IconManager.wrap("&#xeb55;")
trash = IconManager.wrap("&#xeb41;")
config = IconManager.wrap("&#xeb20;")
disc = IconManager.wrap("&#x1003e;")
disc_off = IconManager.wrap("&#xf118;")

# Яркость
brightness_low = IconManager.wrap("&#xeb7d;")
brightness_medium = IconManager.wrap("&#xeb7e;")
brightness_high = IconManager.wrap("&#xeb30;")
brightness = IconManager.wrap("&#xee1a;")

# Дашборд
widgets = IconManager.wrap("&#xf02c;")
pins = IconManager.wrap("&#xec9c;")
kanban = IconManager.wrap("&#xec3f;")
wallpapers = IconManager.wrap("&#xeb61;")
sparkles = IconManager.wrap("&#xf6d7;")

# Прочее
dot = IconManager.wrap("&#xf698;")
palette = IconManager.wrap("&#xeb01;")
cloud_off = IconManager.wrap("&#xed3e;")
loader = IconManager.wrap("&#xeca3;")
radar = IconManager.wrap("&#xf017;")
emoji = IconManager.wrap("&#xeaf7;")
keyboard = IconManager.wrap("&#xebd6;")
terminal = IconManager.wrap("&#xebef;")
timer_off = IconManager.wrap("&#xf146;")
timer_on = IconManager.wrap("&#xf756;")
spy = IconManager.wrap("&#xf227;")

# Кубики (для случайного выбора)
dice_1 = IconManager.wrap("&#xf08b;")
dice_2 = IconManager.wrap("&#xf08c;")
dice_3 = IconManager.wrap("&#xf08d;")
dice_4 = IconManager.wrap("&#xf08e;")
dice_5 = IconManager.wrap("&#xf08f;")
dice_6 = IconManager.wrap("&#xf090;")