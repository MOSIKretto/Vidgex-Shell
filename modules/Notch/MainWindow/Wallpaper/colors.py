import os

from PIL import Image
from gi.repository import GLib

from materialyoucolor.hct import Hct
from materialyoucolor.palettes.tonal_palette import TonalPalette
from materialyoucolor.quantize.celebi import QuantizeCelebi
from materialyoucolor.score.score import Score
from materialyoucolor.dynamiccolor.material_dynamic_colors import MaterialDynamicColors
from materialyoucolor.scheme.scheme_tonal_spot import SchemeTonalSpot
from materialyoucolor.scheme.scheme_content import SchemeContent
from materialyoucolor.scheme.scheme_expressive import SchemeExpressive
from materialyoucolor.scheme.scheme_fidelity import SchemeFidelity
from materialyoucolor.scheme.scheme_fruit_salad import SchemeFruitSalad
from materialyoucolor.scheme.scheme_monochrome import SchemeMonochrome
from materialyoucolor.scheme.scheme_neutral import SchemeNeutral
from materialyoucolor.scheme.scheme_rainbow import SchemeRainbow

__all__ = ["apply_colors", "_CURRENT", "_CSS_OUT", "_HYPR_OUT", "_SCH", "_SCH_K"]

_HOME = GLib.get_home_dir()
_cfg = _HOME + "/.config/hypr/Vidgex-Shell/"
_CSS_OUT = _cfg + "styles/colors.css"
_HYPR_OUT = _cfg + "vidgex-shell-conf/colors.lua"
del _cfg
_CURRENT = _HOME + "/.current.wall"

_SCH = (
    ("scheme-tonal-spot",  "Tonal Spot"),
    ("scheme-content",     "Content"),
    ("scheme-expressive",  "Expressive"),
    ("scheme-fidelity",    "Fidelity"),
    ("scheme-fruit-salad", "Fruit Salad"),
    ("scheme-monochrome",  "Monochrome"),
    ("scheme-neutral",     "Neutral"),
    ("scheme-rainbow",     "Rainbow"),
)
_SCH_K = frozenset(k for k, _ in _SCH)

_SCHEMES = {
    "scheme-tonal-spot":  SchemeTonalSpot,
    "scheme-content":     SchemeContent,
    "scheme-expressive":  SchemeExpressive,
    "scheme-fidelity":    SchemeFidelity,
    "scheme-fruit-salad": SchemeFruitSalad,
    "scheme-monochrome":  SchemeMonochrome,
    "scheme-neutral":     SchemeNeutral,
    "scheme-rainbow":     SchemeRainbow,
}

_THUMB = (128, 128)
_MAX_QUANTIZE_COLORS = 128

_ROLE_COLOR = {
    "primary":        MaterialDynamicColors.primary,
    "on_primary":     MaterialDynamicColors.onPrimary,
    "secondary":      MaterialDynamicColors.secondary,
    "on_secondary":   MaterialDynamicColors.onSecondary,
    "tertiary":       MaterialDynamicColors.tertiary,
    "on_tertiary":    MaterialDynamicColors.onTertiary,
    "surface":        MaterialDynamicColors.surface,
    "surface_bright": MaterialDynamicColors.surfaceBright,
    "error":          MaterialDynamicColors.error,
    "outline":        MaterialDynamicColors.outline,
    "shadow":         MaterialDynamicColors.shadow,
    "background":     MaterialDynamicColors.background,
    "on_surface":     MaterialDynamicColors.onSurface,
}


def _hex(argb):
    return f"{(argb >> 16) & 0xFF:02x}{(argb >> 8) & 0xFF:02x}{argb & 0xFF:02x}"


# ── Green accent: harmonized toward the wallpaper's dominant hue ───

_G_HUE = 145.0
_G_CHROMA = 55.0
_G_TONE = {True: 65, False: 42}
_G_CFG = {
    "scheme-monochrome":  (0.0,  0.00),
    "scheme-neutral":     (8.0,  0.25),
    "scheme-fidelity":    (12.0, 0.90),
    "scheme-tonal-spot":  (15.0, 1.00),
    "scheme-content":     (18.0, 1.00),
    "scheme-rainbow":     (22.0, 1.10),
    "scheme-fruit-salad": (28.0, 1.15),
    "scheme-expressive":  (35.0, 1.25),
}


def _make_green(seed_hue, scheme_id, dark):
    max_shift, chroma_mult = _G_CFG[scheme_id]
    d = ((seed_hue - _G_HUE + 180.0) % 360.0) - 180.0
    shift = min(abs(d) * 0.5, max_shift) * (1.0 if d > 0.0 else -1.0)
    hue = (_G_HUE + shift) % 360.0
    return TonalPalette.from_hue_and_chroma(hue, _G_CHROMA * chroma_mult).tone(_G_TONE[dark])


# ── Seed color: dominant color via quantization + scoring ──────────

def _extract_seed(path):
    img = Image.open(path).convert("RGB")
    img.thumbnail(_THUMB)
    pixels = list(img.getdata())
    img.close()
    color_to_pop = QuantizeCelebi(pixels, _MAX_QUANTIZE_COLORS)
    return Score.score(color_to_pop)[0]


# ── Build ────────────────────────────────────────────────────────

def _build(path, scheme_id, dark, contrast):
    seed = _extract_seed(path or _CURRENT)
    hct = Hct.from_int(seed)
    scheme = _SCHEMES[scheme_id](hct, dark, contrast)

    c = {
        role: _hex(color.get_hct(scheme).to_int())
        for role, color in _ROLE_COLOR.items()
    }
    c["error_dim"] = _hex(scheme.error_palette.tone(60 if dark else 30))
    c["green"] = _hex(_make_green(hct.hue, scheme_id, dark))
    c["foreground"] = c["on_surface"]
    c["cursor"] = c["on_surface"]
    return c


_TPL = (
    ("foreground",     "foreground"),
    ("background",     "background"),
    ("cursor",         "cursor"),
    ("primary",        "primary"),
    ("on-primary",     "on_primary"),
    ("secondary",      "secondary"),
    ("on-secondary",   "on_secondary"),
    ("tertiary",       "tertiary"),
    ("on-tertiary",    "on_tertiary"),
    ("surface",        "surface"),
    ("surface-bright", "surface_bright"),
    ("error",          "error"),
    ("error-dim",      "error_dim"),
    ("outline",        "outline"),
    ("shadow",         "shadow"),
    ("green",          "green"),
)

_dirs_made = set()


def _ensure_dir(path):
    d = os.path.dirname(path)
    if d and d not in _dirs_made:
        os.makedirs(d, exist_ok=True)
        _dirs_made.add(d)


def _write_css(c, path):
    _ensure_dir(path)
    body = "\n".join(f"    --{css}: #{c[key]};" for css, key in _TPL)
    with open(path, "w") as f:
        f.write(f":vars {{\n{body}\n}}\n")


def _write_hypr(c, path):
    _ensure_dir(path)
    max_len = max(len(key) for _, key in _TPL)
    last = len(_TPL) - 1
    lines = ["return {"]
    for i, (_, key) in enumerate(_TPL):
        pad = " " * (max_len - len(key) + 3)
        comma = "," if i < last else ""
        lines.append(f'    {key}{pad}= "#{c[key]}"{comma}')
    lines.append("}")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def apply_colors(image_path=None, scheme_id="scheme-tonal-spot",
                  css_path=None, hypr_path=None, dark=True, contrast=0.0):
    c = _build(image_path, scheme_id, dark, contrast)
    if css_path:
        _write_css(c, css_path)
    if hypr_path:
        _write_hypr(c, hypr_path)
    return c