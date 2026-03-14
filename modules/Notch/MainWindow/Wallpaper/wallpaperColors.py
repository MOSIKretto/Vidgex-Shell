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
    "scheme-tonal-spot":  SchemeTonalSpot,
    "scheme-content":     SchemeContent,
    "scheme-expressive":  SchemeExpressive,
    "scheme-fidelity":    SchemeFidelity,
    "scheme-fruit-salad": SchemeFruitSalad,
    "scheme-monochrome":  SchemeMonochrome,
    "scheme-neutral":     SchemeNeutral,
    "scheme-rainbow":     SchemeRainbow,
}

_FALLBACK = 0xFF141318

_SCHEME_CHROMA_FACTOR = {
    "scheme-monochrome": 0.0,
    "scheme-neutral":    0.28,
    "scheme-tonal-spot": 1.0,
    "scheme-content":    1.0,
    "scheme-expressive": 1.0,
    "scheme-fidelity":   1.0,
    "scheme-fruit-salad":1.0,
    "scheme-rainbow":    1.0,
}

def _rgb2argb(r, g, b):
    return 0xFF000000 | (r << 16) | (g << 8) | b

def _argb2rgb(argb):
    return ((argb >> 16) & 0xFF, (argb >> 8) & 0xFF, argb & 0xFF)

def _hex(argb):
    r, g, b = _argb2rgb(argb)
    return f"{r:02x}{g:02x}{b:02x}"

def _int_of(obj):
    if isinstance(obj, int):
        return obj
    for a in ("argb", "_argb"):
        v = getattr(obj, a, None)
        if isinstance(v, int):
            return v
    for m in ("to_int", "toInt", "get_argb", "getArgb"):
        fn = getattr(obj, m, None)
        if fn and callable(fn):
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


def _hct(argb):
    for m in ("from_int", "fromInt"):
        fn = getattr(Hct, m, None)
        if fn:
            return fn(argb)
    return Hct(argb)


def _hue_dist(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


def _angle_diff(a, b):
    return (a - b + 180) % 360 - 180

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

def _hct2argb(hue, chroma, tone):
    hue = hue % 360
    tone = max(0, min(100, tone))

    chroma = max(chroma, 0)

    if chroma < 0.5:
        t = tone / 100.0
        if t <= 0.008856 * 903.3 / 100.0:
            lin = t * 100.0 / 903.3
        else:
            lin = ((t * 100.0 + 16.0) / 116.0) ** 3
        if lin <= 0.0031308:
            s = lin * 12.92
        else:
            s = 1.055 * (lin ** (1.0 / 2.4)) - 0.055
        v = max(0, min(255, int(s * 255 + 0.5)))
        return _rgb2argb(v, v, v)

    if _Pal:
        for m in ("from_hue_and_chroma", "fromHueAndChroma"):
            fn = getattr(_Pal, m, None)
            if not fn:
                continue
            try:
                v = _int_of(fn(hue, chroma).tone(tone))
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
    r, g, b = colorsys.hls_to_rgb(hue / 360, tone / 100, min(chroma / 100, 1))
    return _rgb2argb(
        min(255, max(0, int(r * 255))),
        min(255, max(0, int(g * 255))),
        min(255, max(0, int(b * 255))),
    )

def _apply_scheme_filter(hex_color, scheme_id):
    factor = _SCHEME_CHROMA_FACTOR.get(scheme_id, 1.0)
    if factor >= 1.0:
        return hex_color
    argb = 0xFF000000 | int(hex_color, 16)
    h = _hct(argb)
    new_chroma = h.chroma * factor
    return _hex(_hct2argb(h.hue, new_chroma, h.tone))

def _do_quantize(pixels, max_colors=128):
    try:
        result = QuantizeCelebi.quantize(pixels, max_colors)
        if isinstance(result, dict) and result:
            return result
    except Exception:
        pass

    try:
        result = QuantizeCelebi(pixels, max_colors)
        if isinstance(result, dict) and result:
            return result
    except Exception:
        pass

    return _manual_quantize(pixels, max_colors)

def _manual_quantize(pixels, max_colors=128):
    reduced = []
    for argb in pixels:
        r = ((argb >> 16) & 0xFF) >> 4
        g = ((argb >> 8) & 0xFF) >> 4
        b = (argb & 0xFF) >> 4
        reduced.append(_rgb2argb((r << 4) | r, (g << 4) | g, (b << 4) | b))
    counts = Counter(reduced)
    return dict(counts.most_common(max_colors))

def _do_score(quantized, desired=12):
    result = None

    for n in (desired, 4, 1):
        try:
            result = Score.score(quantized, desired=n)
            break
        except TypeError:
            continue
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
    if isinstance(result, (list, tuple)):
        out = []
        for item in result:
            v = _int_of(item)
            if v is not None:
                out.append(v)
        return out
    if isinstance(result, dict):
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
        return _FALLBACK, [_hct(_FALLBACK)]

    try:
        fsize = os.path.getsize(img_path)
        if fsize < 100:
            return _FALLBACK, [_hct(_FALLBACK)]
    except Exception:
        pass

    try:
        img = Image.open(img_path)
        img = img.convert("RGB")
    except Exception:
        return _FALLBACK, [_hct(_FALLBACK)]

    img.thumbnail((200, 200))
    raw_pixels = list(img.getdata())

    if not raw_pixels:
        return _FALLBACK, [_hct(_FALLBACK)]

    argb_pixels = [_rgb2argb(r, g, b) for r, g, b in raw_pixels]

    quantized = _do_quantize(argb_pixels)
    if not quantized:
        return _FALLBACK, [_hct(_FALLBACK)]

    seed_argbs = _do_score(quantized, desired)

    if not seed_argbs:
        top = sorted(quantized.items(), key=lambda x: x[1], reverse=True)
        for argb, count in top[:desired * 3]:
            h = _hct(argb)
            if h.chroma > 5:
                seed_argbs.append(argb)
            if len(seed_argbs) >= desired:
                break
        if not seed_argbs:
            seed_argbs = [k for k, v in top[:desired]]

    if not seed_argbs:
        return _FALLBACK, [_hct(_FALLBACK)]

    seed = seed_argbs[0]

    palette = []
    seen_buckets = set()

    for argb in seed_argbs:
        h = _hct(argb)
        palette.append(h)
        seen_buckets.add(int(h.hue / 10))

    top_q = sorted(quantized.items(), key=lambda x: x[1], reverse=True)
    for argb_raw, pop in top_q[:80]:
        if len(palette) >= 36:
            break
        h = _hct(argb_raw)
        if h.chroma < 8:
            continue
        bucket = int(h.hue / 10)
        if bucket not in seen_buckets or h.chroma > 35:
            palette.append(h)
            seen_buckets.add(bucket)

    return seed, palette

_GREEN_PROFILE = {
    "dark_bright":  (64, 68),
    "dark_dim":     (42, 48),
    "light_bright": (38, 70),
    "light_dim":    (26, 50),
    "floor": 55,
    "cap":  70,
    "min_match_chroma": 45,
}

_GREEN_TARGET_HUE = 145.0

def _find_closest(target_hue, palette, max_dist=55):
    best, best_d = None, 999.0
    for h in palette:
        if h.chroma < 6:
            continue
        d = _hue_dist(h.hue, target_hue)
        if d < best_d:
            best, best_d = h, d
    return (best, best_d) if best_d <= max_dist else (None, best_d)

def _blend_hue(h1, h2, w):
    diff = _angle_diff(h1, h2)
    return (h2 + diff * w) % 360

def _soft_harmonize(base_hue, source_hue, max_shift):
    diff = _angle_diff(source_hue, base_hue)
    shift = min(abs(diff), max_shift) * (1 if diff > 0 else -1) * 0.4
    return (base_hue + shift) % 360

def _palette_vibrancy(palette):
    chromas = [h.chroma for h in palette if h.chroma > 8]
    if not chromas:
        return 0.5
    avg = sum(chromas) / len(chromas)
    return min(max(avg / 50.0, 0.5), 1.0)

def _gen_green(palette, dominant, dark, vibrancy):
    tgt_hue = _GREEN_TARGET_HUE
    prof = _GREEN_PROFILE
    min_match_chr = prof["min_match_chroma"]

    match, dist = _find_closest(tgt_hue, palette)

    if match and match.chroma < min_match_chr:
        match = None
        dist = 999

    mode = "dark" if dark else "light"
    tone, base_chroma = prof[f"{mode}_bright"]
    floor_chroma = prof["floor"]
    cap_chroma = prof["cap"]

    v_boost = 0.90 + vibrancy * 0.20

    if match and dist <= 15:
        hue = _blend_hue(match.hue, tgt_hue, 0.80)
        chroma = max(match.chroma * 1.20, base_chroma)
    elif match and dist <= 35:
        t = (dist - 15) / 20.0
        blend_w = 0.65 * (1.0 - t) + 0.15 * t
        hue = _blend_hue(match.hue, tgt_hue, blend_w)
        chroma = max(match.chroma * (1.10 - 0.15 * t), base_chroma * 0.90)
    elif match and dist <= 55:
        t = (dist - 35) / 20.0
        hue = _blend_hue(match.hue, tgt_hue, 0.10 * (1.0 - t))
        chroma = base_chroma
    else:
        hue = _soft_harmonize(tgt_hue, dominant.hue, 8.0)
        chroma = base_chroma

    chroma = chroma * v_boost
    chroma = max(chroma, floor_chroma)
    chroma = min(chroma, cap_chroma)

    return _hct2argb(hue % 360, chroma, tone)


def _gen_customs(seed, palette, dark):
    dominant = _hct(seed)
    vibrancy = _palette_vibrancy(palette)

    return {
        "green": _hex(_gen_green(palette, dominant, dark, vibrancy)),
    }

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

_TONE_FB = {
    "primary":        ("primary_palette",          80,  40),
    "on_primary":     ("primary_palette",          20, 100),
    "secondary":      ("secondary_palette",        80,  40),
    "on_secondary":   ("secondary_palette",        20, 100),
    "tertiary":       ("tertiary_palette",         80,  40),
    "on_tertiary":    ("tertiary_palette",         20, 100),
    "surface":        ("neutral_palette",           6,  98),
    "surface_bright": ("neutral_palette",          24,  98),
    "error":          ("error_palette",            80,  40),
    "on_error":       ("error_palette",            20, 100),
    "outline":        ("neutral_variant_palette",  60,  50),
    "shadow":         ("neutral_palette",           0,   0),
    "background":     ("neutral_palette",           6,  98),
    "on_surface":     ("neutral_palette",          90,  10),
}

def _camel(name):
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])

def _get_dc(name):
    for attr in (name, _camel(name)):
        for obj in (_mdc, MaterialDynamicColors):
            if obj is None:
                continue
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

_dc = {n: _get_dc(n) for n in _STD_ROLES}
_dc = {k: v for k, v in _dc.items() if v is not None}

def _resolve(scheme, name, dark):
    dc = _dc.get(name)
    if dc:
        try:
            v = _int_of(dc.get_argb(scheme))
            if v is not None:
                return v
        except Exception:
            pass
    for a in (name, _camel(name)):
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
        pa, td, tl = fb
        for pn in (pa, _camel(pa)):
            pal = getattr(scheme, pn, None)
            if pal and hasattr(pal, "tone"):
                try:
                    v = _int_of(pal.tone(td if dark else tl))
                    if v is not None:
                        return v
                except Exception:
                    pass
    return _FALLBACK

def _build(path, scheme_id, dark, contrast):
    seed, palette = _extract_palette(path)

    cls = SCHEME_MAP.get(scheme_id, SchemeTonalSpot)
    hct_src = _hct(seed)

    try:
        scheme = cls(hct_src, dark, contrast)
    except TypeError:
        scheme = cls(hct_src, dark)

    p = {}

    for name in _STD_ROLES:
        argb = _resolve(scheme, name, dark)
        p[name] = _hex(argb)

    err = _hct(0xFF000000 | int(p["error"], 16))
    p["error_dim"] = _hex(_hct2argb(
        err.hue, max(err.chroma * 0.85, 40), 52 if dark else 28
    ))

    p.update(_gen_customs(seed, palette, dark))

    for key in list(p.keys()):
        p[key] = _apply_scheme_filter(p[key], scheme_id)

    p["foreground"] = p["on_surface"]
    p["cursor"]     = p["on_surface"]

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
    lines = [":vars {"]
    for css, key in _TPL:
        lines.append(f"  --{css}: #{p.get(key, '000000')};")
    lines.append("}")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")

def _write_hypr(p, path):
    lines = [f"${k} = {v}" for k, v in p.items()]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")

def apply_colors(image_path=None, scheme_id="scheme-tonal-spot", css_path=None, hypr_path=None, dark=True, contrast=0.0,):
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