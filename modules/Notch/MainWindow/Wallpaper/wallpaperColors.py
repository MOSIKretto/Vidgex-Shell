import os
import colorsys
from collections import Counter

from PIL import Image
from materialyoucolor.hct import Hct
from materialyoucolor.dynamiccolor.material_dynamic_colors import MaterialDynamicColors
from materialyoucolor.quantize import QuantizeCelebi
from materialyoucolor.score.score import Score
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

SCHEME_MAP = {
    "scheme-tonal-spot": SchemeTonalSpot,
    "scheme-content": SchemeContent,
    "scheme-expressive": SchemeExpressive,
    "scheme-fidelity": SchemeFidelity,
    "scheme-fruit-salad": SchemeFruitSalad,
    "scheme-monochrome": SchemeMonochrome,
    "scheme-neutral": SchemeNeutral,
    "scheme-rainbow": SchemeRainbow,
}

_FALLBACK = 0xFF141318
_THUMB_SZ = (128, 128)

_SCHEME_CHROMA_FACTOR = {
    "scheme-monochrome": 0.0,
    "scheme-neutral": 0.28,
}

_hct_fn = getattr(Hct, "from_int", None) or getattr(Hct, "fromInt", None)


def _hct(argb):
    return _hct_fn(argb) if _hct_fn else Hct(argb)


_FALLBACK_HCT = (_hct(_FALLBACK),)


def _find_pal_class():
    try:
        h = _hct(0xFF808080)
        try:
            s = SchemeTonalSpot(h, True, 0.0)
        except TypeError:
            s = SchemeTonalSpot(h, True)
        for n in ("primary_palette", "primaryPalette"):
            p = getattr(s, n, None)
            if p and hasattr(p, "tone"):
                return type(p)
    except Exception:
        pass
    return None


_Pal = _find_pal_class()
_pal_from_hc = None
if _Pal:
    _pal_from_hc = (
        getattr(_Pal, "from_hue_and_chroma", None)
        or getattr(_Pal, "fromHueAndChroma", None)
    )


def _rgb2argb(r, g, b):
    return 0xFF000000 | (r << 16) | (g << 8) | b


def _hex(argb):
    return f"{(argb >> 16) & 0xFF:02x}{(argb >> 8) & 0xFF:02x}{argb & 0xFF:02x}"


def _int_of(obj):
    if isinstance(obj, int):
        return obj
    for a in ("argb", "_argb"):
        v = getattr(obj, a, None)
        if isinstance(v, int):
            return v
    for m in ("to_int", "toInt", "get_argb", "getArgb"):
        fn = getattr(obj, m, None)
        if fn is not None:
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


def _hue_dist(a, b):
    d = abs(a - b) % 360
    return d if d <= 180 else 360 - d


def _angle_diff(a, b):
    return (a - b + 180) % 360 - 180


def _hct2argb(hue, chroma, tone):
    hue %= 360
    if tone < 0:
        tone = 0
    elif tone > 100:
        tone = 100
    if chroma < 0:
        chroma = 0

    if chroma < 0.5:
        t = tone * 0.01
        lin = t / 9.033 if t <= 0.08 else ((t * 100.0 + 16.0) / 116.0) ** 3
        s = lin * 12.92 if lin <= 0.0031308 else 1.055 * lin ** (1.0 / 2.4) - 0.055
        v = max(0, min(255, int(s * 255 + 0.5)))
        return _rgb2argb(v, v, v)

    if _pal_from_hc:
        try:
            v = _int_of(_pal_from_hc(hue, chroma).tone(tone))
            if v is not None:
                return v
        except Exception:
            pass

    try:
        v = _int_of(Hct(hue, chroma, tone))
        if v is not None:
            return v
    except Exception:
        pass

    r, g, b = colorsys.hls_to_rgb(hue / 360, tone * 0.01, min(chroma * 0.01, 1))
    return _rgb2argb(
        min(255, max(0, int(r * 255))),
        min(255, max(0, int(g * 255))),
        min(255, max(0, int(b * 255))),
    )


def _do_quantize(pixels, max_colors=128):
    try:
        r = QuantizeCelebi.quantize(pixels, max_colors)
        if isinstance(r, dict) and r:
            return r
    except Exception:
        pass
    try:
        r = QuantizeCelebi(pixels, max_colors)
        if isinstance(r, dict) and r:
            return r
    except Exception:
        pass
    return _manual_quantize(pixels, max_colors)


def _manual_quantize(pixels, max_colors=128):
    return dict(Counter(
        0xFF000000
        | ((((a >> 16) & 0xF0) | ((a >> 20) & 0xF)) << 16)
        | ((((a >> 8) & 0xF0) | ((a >> 12) & 0xF)) << 8)
        | ((a & 0xF0) | ((a >> 4) & 0xF))
        for a in pixels
    ).most_common(max_colors))


def _do_score(quantized, desired=12):
    result = None
    for n in (desired, 4, 1):
        try:
            result = Score.score(quantized, desired=n)
            break
        except Exception:
            continue
    if result is None:
        try:
            result = Score.score(quantized)
        except Exception:
            pass
    if result is None:
        try:
            result = Score(quantized)
        except Exception:
            pass
    return _parse_result(result)


def _parse_result(result):
    if result is None:
        return []
    if isinstance(result, int):
        return [result]
    if isinstance(result, (list, tuple, dict)):
        out = []
        for item in result:
            v = _int_of(item)
            if v is not None:
                out.append(v)
        return out
    v = _int_of(result)
    return [v] if v is not None else []


def _extract_palette(path, desired=12):
    img_path = path or _CURRENT
    if not img_path or not os.path.isfile(img_path):
        return _FALLBACK, _FALLBACK_HCT
    try:
        if os.path.getsize(img_path) < 100:
            return _FALLBACK, _FALLBACK_HCT
    except Exception:
        pass
    try:
        img = Image.open(img_path)
        img = img.convert("RGB")
    except Exception:
        return _FALLBACK, _FALLBACK_HCT

    img.thumbnail(_THUMB_SZ)
    pixels = [0xFF000000 | (r << 16) | (g << 8) | b for r, g, b in img.getdata()]
    img.close()

    if not pixels:
        return _FALLBACK, _FALLBACK_HCT

    quantized = _do_quantize(pixels)
    del pixels

    if not quantized:
        return _FALLBACK, _FALLBACK_HCT

    seed_argbs = _do_score(quantized, desired)

    if not seed_argbs:
        top = sorted(quantized.items(), key=lambda x: x[1], reverse=True)
        seed_argbs = []
        for argb, _ in top[:desired * 3]:
            h = _hct(argb)
            if h.chroma > 5:
                seed_argbs.append(argb)
                if len(seed_argbs) >= desired:
                    break
        if not seed_argbs:
            seed_argbs = [k for k, _ in top[:desired]]

    if not seed_argbs:
        return _FALLBACK, _FALLBACK_HCT

    seed = seed_argbs[0]
    palette = []
    seen = set()

    for argb in seed_argbs:
        h = _hct(argb)
        palette.append(h)
        seen.add(int(h.hue * 0.1))

    top_q = sorted(quantized.items(), key=lambda x: x[1], reverse=True)
    for argb_raw, _ in top_q[:48]:
        if len(palette) >= 24:
            break
        h = _hct(argb_raw)
        if h.chroma < 8:
            continue
        b = int(h.hue * 0.1)
        if b not in seen or h.chroma > 35:
            palette.append(h)
            seen.add(b)

    return seed, palette


_GREEN_HUE = 145.0
_GREEN_MIN_CHR = 45
_GREEN_FLOOR = 55
_GREEN_CAP = 70
_GREEN_TONES = {True: (64, 68), False: (38, 70)}


def _find_closest(target_hue, palette):
    best, best_d = None, 999.0
    for h in palette:
        if h.chroma < 6:
            continue
        d = _hue_dist(h.hue, target_hue)
        if d < best_d:
            best, best_d = h, d
    return (best, best_d) if best_d <= 55 else (None, best_d)


def _blend_hue(h1, h2, w):
    return (h2 + _angle_diff(h1, h2) * w) % 360


def _soft_harmonize(base_hue, source_hue, max_shift):
    diff = _angle_diff(source_hue, base_hue)
    return (base_hue + min(abs(diff), max_shift) * (1 if diff > 0 else -1) * 0.4) % 360


def _palette_vibrancy(palette):
    total = count = 0
    for h in palette:
        if h.chroma > 8:
            total += h.chroma
            count += 1
    if not count:
        return 0.5
    return min(max(total / (count * 50.0), 0.5), 1.0)


def _gen_green(palette, dominant, dark, vibrancy):
    match, dist = _find_closest(_GREEN_HUE, palette)
    if match and match.chroma < _GREEN_MIN_CHR:
        match = None

    tone, base_chroma = _GREEN_TONES[dark]

    if match and dist <= 15:
        hue = _blend_hue(match.hue, _GREEN_HUE, 0.80)
        chroma = max(match.chroma * 1.20, base_chroma)
    elif match and dist <= 35:
        t = (dist - 15) * 0.05
        hue = _blend_hue(match.hue, _GREEN_HUE, 0.65 - 0.50 * t)
        chroma = max(match.chroma * (1.10 - 0.15 * t), base_chroma * 0.90)
    elif match and dist <= 55:
        hue = _blend_hue(match.hue, _GREEN_HUE, 0.10 * (1.0 - (dist - 35) * 0.05))
        chroma = base_chroma
    else:
        hue = _soft_harmonize(_GREEN_HUE, dominant.hue, 8.0)
        chroma = base_chroma

    chroma = min(max(chroma * (0.90 + vibrancy * 0.20), _GREEN_FLOOR), _GREEN_CAP)
    return _hct2argb(hue % 360, chroma, tone)


_mdc = MaterialDynamicColors()

_STD_ROLES = (
    "primary", "on_primary",
    "secondary", "on_secondary",
    "tertiary", "on_tertiary",
    "surface", "surface_bright",
    "error", "on_error",
    "outline", "shadow",
    "background", "on_surface",
)


def _camel(name):
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


_CAMEL = {n: _camel(n) for n in _STD_ROLES}

_TONE_FB = {
    name: (pa, _camel(pa), td, tl)
    for name, (pa, td, tl) in {
        "primary":       ("primary_palette",         80, 40),
        "on_primary":    ("primary_palette",         20, 100),
        "secondary":     ("secondary_palette",       80, 40),
        "on_secondary":  ("secondary_palette",       20, 100),
        "tertiary":      ("tertiary_palette",        80, 40),
        "on_tertiary":   ("tertiary_palette",        20, 100),
        "surface":       ("neutral_palette",          6, 98),
        "surface_bright":("neutral_palette",         24, 98),
        "error":         ("error_palette",           80, 40),
        "on_error":      ("error_palette",           20, 100),
        "outline":       ("neutral_variant_palette", 60, 50),
        "shadow":        ("neutral_palette",          0,  0),
        "background":    ("neutral_palette",          6, 98),
        "on_surface":    ("neutral_palette",         90, 10),
    }.items()
}


def _get_dc(name):
    for attr in (name, _CAMEL.get(name, name)):
        for obj in (_mdc, MaterialDynamicColors):
            a = getattr(obj, attr, None)
            if a is None:
                continue
            if hasattr(a, "get_argb"):
                return a
            if callable(a):
                try:
                    dc = a()
                    if hasattr(dc, "get_argb"):
                        return dc
                except Exception:
                    pass
    return None


_dc = {n: v for n in _STD_ROLES if (v := _get_dc(n)) is not None}


def _resolve(scheme, name, dark):
    dc = _dc.get(name)
    if dc:
        try:
            v = _int_of(dc.get_argb(scheme))
            if v is not None:
                return v
        except Exception:
            pass

    camel = _CAMEL.get(name, name)
    for a in (name, camel):
        val = getattr(scheme, a, None)
        if val is not None:
            try:
                v = _int_of(val() if callable(val) else val)
                if v is not None:
                    return v
            except Exception:
                pass

    fb = _TONE_FB.get(name)
    if fb:
        pa, pa_c, td, tl = fb
        t = td if dark else tl
        for pn in (pa, pa_c):
            pal = getattr(scheme, pn, None)
            if pal and hasattr(pal, "tone"):
                try:
                    v = _int_of(pal.tone(t))
                    if v is not None:
                        return v
                except Exception:
                    pass
    return _FALLBACK


def _build(path, scheme_id, dark, contrast):
    seed, palette = _extract_palette(path)
    hct_src = _hct(seed)

    cls = SCHEME_MAP.get(scheme_id, SchemeTonalSpot)
    try:
        scheme = cls(hct_src, dark, contrast)
    except TypeError:
        scheme = cls(hct_src, dark)

    p = {name: _hex(_resolve(scheme, name, dark)) for name in _STD_ROLES}

    err = _hct(0xFF000000 | int(p["error"], 16))
    p["error_dim"] = _hex(_hct2argb(
        err.hue, max(err.chroma * 0.85, 40), 52 if dark else 28
    ))

    vibrancy = _palette_vibrancy(palette)
    p["green"] = _hex(_gen_green(palette, hct_src, dark, vibrancy))

    factor = _SCHEME_CHROMA_FACTOR.get(scheme_id)
    if factor is not None:
        for key in p:
            argb = 0xFF000000 | int(p[key], 16)
            h = _hct(argb)
            p[key] = _hex(_hct2argb(h.hue, h.chroma * factor, h.tone))

    p["foreground"] = p["on_surface"]
    p["cursor"] = p["on_surface"]
    return p


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


def _write_css(p, path):
    g = p.get
    body = "\n".join(f"    --{c}: #{g(k, '000000')};" for c, k in _TPL)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(f":vars {{\n{body}\n}}\n")


def _write_hypr(p, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(f"${k} = {v}" for k, v in p.items()) + "\n")


def apply_colors(image_path=None, scheme_id="scheme-tonal-spot",
                 css_path=None, hypr_path=None, dark=True, contrast=0.0):
    try:
        p = _build(image_path, scheme_id, dark, contrast)
        if not p:
            return {}
        if css_path:
            _write_css(p, css_path)
        if hypr_path:
            _write_hypr(p, hypr_path)
        return p
    except Exception:
        return {}