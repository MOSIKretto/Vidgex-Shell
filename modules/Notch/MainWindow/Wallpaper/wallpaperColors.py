import os
import colorsys

from PIL import Image
from materialyoucolor.hct import Hct
from materialyoucolor.dynamiccolor.material_dynamic_colors import MaterialDynamicColors
from materialyoucolor.scheme.scheme_tonal_spot import SchemeTonalSpot
from materialyoucolor.scheme.scheme_content import SchemeContent
from materialyoucolor.scheme.scheme_expressive import SchemeExpressive
from materialyoucolor.scheme.scheme_fidelity import SchemeFidelity
from materialyoucolor.scheme.scheme_fruit_salad import SchemeFruitSalad
from materialyoucolor.scheme.scheme_monochrome import SchemeMonochrome
from materialyoucolor.scheme.scheme_neutral import SchemeNeutral
from materialyoucolor.scheme.scheme_rainbow import SchemeRainbow

from modules.Notch.MainWindow.Wallpaper.wallpaperConstants import _CURRENT

__all__ = ["apply_colors"]

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

_FALLBACK = 0xFF6750A4
_THUMB = (64, 64)


# ── int / hex ──────────────────────────────────────────────────────

def _to_int(obj):
    if isinstance(obj, int):
        return obj
    for a in ("argb", "_argb"):
        v = getattr(obj, a, None)
        if isinstance(v, int):
            return v
    for m in ("to_int", "toInt"):
        fn = getattr(obj, m, None)
        if fn:
            try:
                v = fn()
                if isinstance(v, int):
                    return v
            except Exception:
                pass
    try:
        return int(obj)
    except Exception:
        return None


def _hex(argb):
    return f"{(argb >> 16) & 0xFF:02x}{(argb >> 8) & 0xFF:02x}{argb & 0xFF:02x}"


# ── HCT ────────────────────────────────────────────────────────────

_hct_from = (
    getattr(Hct, "from_int", None)
    or getattr(Hct, "fromInt", None)
    or Hct
)


def _hct(argb):
    return _hct_from(argb)


# ── TonalPalette (для green и fallback) ────────────────────────────

_tp_from_hc = None
try:
    from materialyoucolor.palettes.tonal_palette import TonalPalette as _TP
    _tp_from_hc = (
        getattr(_TP, "from_hue_and_chroma", None)
        or getattr(_TP, "fromHueAndChroma", None)
    )
except ImportError:
    pass


def _hct_to_argb(hue, chroma, tone):
    """HCT → ARGB: TonalPalette → Hct ctor → HSL (всегда сработает)."""
    if _tp_from_hc:
        try:
            v = _to_int(_tp_from_hc(hue, chroma).tone(tone))
            if v is not None:
                return v
        except Exception:
            pass
    try:
        v = _to_int(Hct(hue, chroma, tone))
        if v is not None:
            return v
    except Exception:
        pass
    s = min(chroma / 70.0, 1.0)
    r, g, b = colorsys.hls_to_rgb(hue / 360.0, tone / 100.0, s)
    return (
        0xFF000000
        | (min(255, max(0, int(r * 255 + 0.5))) << 16)
        | (min(255, max(0, int(g * 255 + 0.5))) << 8)
        | min(255, max(0, int(b * 255 + 0.5)))
    )


# ── DynamicColor (matugen way) ─────────────────────────────────────

_MDC_TARGETS = [MaterialDynamicColors]
try:
    _MDC_TARGETS.append(MaterialDynamicColors())
except Exception:
    pass


def _find_dc(*names):
    for t in _MDC_TARGETS:
        for n in names:
            a = getattr(t, n, None)
            if a is None:
                continue
            if hasattr(a, "get_argb") or hasattr(a, "getArgb"):
                return a
            if callable(a):
                try:
                    dc = a()
                    if hasattr(dc, "get_argb") or hasattr(dc, "getArgb"):
                        return dc
                except Exception:
                    pass
    return None


def _dc_argb(dc, scheme):
    if dc is None:
        return None
    for m in ("get_argb", "getArgb"):
        fn = getattr(dc, m, None)
        if fn:
            try:
                v = fn(scheme)
                if isinstance(v, int):
                    return v
            except Exception:
                pass
    return None


# Pre-resolve DynamicColor objects (import time, once)
_DC = {
    "primary":        _find_dc("primary"),
    "on_primary":     _find_dc("on_primary",     "onPrimary"),
    "secondary":      _find_dc("secondary"),
    "on_secondary":   _find_dc("on_secondary",   "onSecondary"),
    "tertiary":       _find_dc("tertiary"),
    "on_tertiary":    _find_dc("on_tertiary",    "onTertiary"),
    "surface":        _find_dc("surface"),
    "surface_bright": _find_dc("surface_bright", "surfaceBright"),
    "error":          _find_dc("error"),
    "outline":        _find_dc("outline"),
    "shadow":         _find_dc("shadow"),
    "background":     _find_dc("background"),
    "on_surface":     _find_dc("on_surface",     "onSurface"),
}


# ── Palette fallback ──────────────────────────────────────────────

_PAL = {
    "P":  ("primary_palette",         "primaryPalette"),
    "S":  ("secondary_palette",       "secondaryPalette"),
    "T":  ("tertiary_palette",        "tertiaryPalette"),
    "N":  ("neutral_palette",         "neutralPalette"),
    "NV": ("neutral_variant_palette", "neutralVariantPalette"),
    "E":  ("error_palette",           "errorPalette"),
}

#               role            pal  dark  light
_FB = {
    "primary":        ("P",  80,  40),
    "on_primary":     ("P",  20, 100),
    "secondary":      ("S",  80,  40),
    "on_secondary":   ("S",  20, 100),
    "tertiary":       ("T",  80,  40),
    "on_tertiary":    ("T",  20, 100),
    "surface":        ("N",   6,  98),
    "surface_bright": ("N",  24,  98),
    "error":          ("E",  80,  40),
    "outline":        ("NV", 60,  50),
    "shadow":         ("N",   0,   0),
    "background":     ("N",   6,  98),
    "on_surface":     ("N",  90,  10),
}


def _get_pal(scheme, key):
    for n in _PAL[key]:
        p = getattr(scheme, n, None)
        if p and hasattr(p, "tone"):
            return p
    return None


def _resolve(scheme, role, dark):
    """DynamicColor first (= matugen), palette.tone() fallback."""
    v = _dc_argb(_DC.get(role), scheme)
    if v is not None:
        return v
    fb = _FB.get(role)
    if fb:
        pk, td, tl = fb
        pal = _get_pal(scheme, pk)
        if pal:
            r = _to_int(pal.tone(td if dark else tl))
            if r is not None:
                return r
    return _FALLBACK


# ── Green: harmonize + per-scheme config ───────────────────────────
#
#  Android MaterialColors.harmonize():
#    diff = target − base  (signed, wrapped to ±180)
#    rotation = min(|diff| × 0.5, max_shift)
#    result = base + rotation toward target
#
#  Каждая схема: (max_hue_shift, chroma_multiplier)
#    monochrome → achromatic (chroma=0)
#    neutral    → very desaturated
#    fidelity   → faithful to source
#    expressive → vivid, big hue shift

_G_HUE = 145.0
_G_CHR = 55.0
_G_TONE = {True: 65, False: 42}

_G_CFG = {
    "scheme-monochrome":  (0.0,  0.00),   # grey
    "scheme-neutral":     (8.0,  0.25),   # barely tinted
    "scheme-fidelity":    (12.0, 0.90),   # close to source
    "scheme-tonal-spot":  (15.0, 1.00),   # default Android
    "scheme-content":     (18.0, 1.00),   # content-aware
    "scheme-rainbow":     (22.0, 1.10),   # vivid
    "scheme-fruit-salad": (28.0, 1.15),   # playful
    "scheme-expressive":  (35.0, 1.25),   # bold shift
}


def _harmonize(base, target, max_shift):
    d = ((target - base + 180.0) % 360.0) - 180.0
    r = min(abs(d) * 0.5, max_shift)
    return (base + r * (1.0 if d > 0.0 else -1.0)) % 360.0


def _make_green(seed_hue, scheme_id, dark):
    ms, cm = _G_CFG.get(scheme_id, (15.0, 1.0))
    hue = _harmonize(_G_HUE, seed_hue, ms)
    chroma = _G_CHR * cm
    tone = _G_TONE[dark]
    return _hct_to_argb(hue, chroma, tone)


# ── Seed: max vibrancy pixel ──────────────────────────────────────

def _extract_seed(path):
    if not path or not os.path.isfile(path):
        return _FALLBACK
    try:
        img = Image.open(path).convert("RGB")
    except Exception:
        return _FALLBACK
    img.thumbnail(_THUMB)
    best, best_v = _FALLBACK, -1
    for r, g, b in img.getdata():
        mx = max(r, g, b)
        if mx < 15:
            continue
        d = mx - min(r, g, b)
        if d < 8:
            continue
        v = d * mx
        if v > best_v:
            best_v = v
            best = 0xFF000000 | (r << 16) | (g << 8) | b
    img.close()
    return best


# ── Build ──────────────────────────────────────────────────────────

_ROLE_NAMES = (
    "primary", "on_primary",
    "secondary", "on_secondary",
    "tertiary", "on_tertiary",
    "surface", "surface_bright",
    "error",
    "outline", "shadow",
    "background", "on_surface",
)


def _build(path, scheme_id, dark, contrast):
    fpath = path or _CURRENT
    seed = _extract_seed(fpath)
    src = _hct(seed)

    cls = _SCHEMES.get(scheme_id, SchemeTonalSpot)
    try:
        scheme = cls(src, dark, contrast)
    except TypeError:
        try:
            scheme = cls(src, dark)
        except Exception:
            scheme = SchemeTonalSpot(src, dark)

    c = {r: _hex(_resolve(scheme, r, dark)) for r in _ROLE_NAMES}

    # error_dim: error palette, reduced tone
    ep = _get_pal(scheme, "E")
    if ep:
        v = _to_int(ep.tone(60 if dark else 30))
        c["error_dim"] = _hex(v) if v is not None else c["error"]
    else:
        c["error_dim"] = c["error"]

    c["green"]      = _hex(_make_green(src.hue, scheme_id, dark))
    c["foreground"] = c["on_surface"]
    c["cursor"]     = c["on_surface"]
    return c


# ── Output ─────────────────────────────────────────────────────────

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


def _write_css(c, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    body = "\n".join(f"    --{css}: #{c[key]};" for css, key in _TPL)
    with open(path, "w") as f:
        f.write(f":vars {{\n{body}\n}}\n")


def _write_hypr(c, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(f"${key} = {c[key]}" for _, key in _TPL))
        f.write("\n")


# ── Public API ─────────────────────────────────────────────────────

def apply_colors(image_path=None, scheme_id="scheme-tonal-spot",
                 css_path=None, hypr_path=None, dark=True, contrast=0.0):
    try:
        c = _build(image_path, scheme_id, dark, contrast)
        if not c:
            return {}
        if css_path:
            _write_css(c, css_path)
        if hypr_path:
            _write_hypr(c, hypr_path)
        return c
    except Exception:
        return {}