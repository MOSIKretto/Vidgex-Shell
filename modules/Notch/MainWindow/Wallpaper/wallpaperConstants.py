import services.icons as icons
from gi.repository import GLib, Pango


_HOME = GLib.get_home_dir()
_WALLS = _HOME + "/.config/Vidgex-Shell/wallpapers/"
_CURRENT = _HOME + "/.current.wall"
_CACHE = GLib.get_user_cache_dir() + "/vidgex-shell"
_THUMBS = _CACHE + "/thumbnails/"
_SCHEME_F = _CACHE + "/scheme"

_SZ = 180
_HSZ = 90.0
_NHSZ = -90.0
_SPC = 100.0
_ARC_K = 1.875
_LOAD_RNG = range(-4, 5)
_CR = 16
_SUFFIX = "_r.png"
_SFXL = 6
_EXT = frozenset((".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"))
_ANG = (
    -1.5707963267948966, 0.0, 1.5707963267948966,
    3.141592653589793, 4.71238898038469,
)

_SCH = (
    ("scheme-tonal-spot", "Tonal Spot"),
    ("scheme-content", "Content"),
    ("scheme-expressive", "Expressive"),
    ("scheme-fidelity", "Fidelity"),
    ("scheme-fruit-salad", "Fruit Salad"),
    ("scheme-monochrome", "Monochrome"),
    ("scheme-neutral", "Neutral"),
    ("scheme-rainbow", "Rainbow"),
)
_SCH_K = frozenset(k for k, _ in _SCH)
_SCH_LEN = len(_SCH)

_DICE = (
    icons.dice_1, icons.dice_2, icons.dice_3,
    icons.dice_4, icons.dice_5, icons.dice_6,
)

_DRAW_ORDER = (4, -4, 3, -3, 2, -2, 1, -1, 0)
_STATIC = (
    (3, 0.55, 0.25, 16.875, False),
    (-3, 0.55, 0.25, 16.875, False),
    (2, 0.7, 0.5, 7.5, False),
    (-2, 0.7, 0.5, 7.5, False),
    (1, 0.85, 0.75, 1.875, False),
    (-1, 0.85, 0.75, 1.875, False),
    (0, 1.0, 1.0, 0.0, True),
)

_ARR_L = [
    "    _ ",
    "  /№; ",
    " /!#  ",
    "/@(&  ",
    " \\][ ",
    "  \\# ",
    "   ‾‾ ",
]
_ARR_R = [
    "_     ",
    ";$\\   ",
    " #!\\  ",
    " ?(@\\",
    " |#/  ",
    " //   ",
    "‾‾    ",
]
_ARR_FONT_PT = 8
_ARR_LINES = (_ARR_L, _ARR_R)

_AGL_CHARS = "░▒▓█▀▄▌▐@#$%&!?*=~"
_AGL_FRAMES = 12
_AGL_FRAME_MS = 35
_AGL_CORRUPT = 0.85
_AGL_FLICKER = 0.12
_AGL_SHIFT_CHANCE = 0.30
_AGL_SHIFT_MAX = 2
_AGL_RAND_MIN = 10
_AGL_RAND_MAX = 20

_ARR_FONT = Pango.FontDescription.from_string("monospace")
_ARR_FONT.set_size(_ARR_FONT_PT * Pango.SCALE)
_ARR_FONT_STR = _ARR_FONT.to_string()

# Добавить в конец файла:
_CSS_OUT = _HOME + "/.config/Vidgex-Shell/styles/colors.css"
_HYPR_OUT = _HOME + "/.config/Vidgex-Shell/vidgex-shell-conf/colors.conf"