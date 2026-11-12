"""Bake Unreal curve atlases (CurveLinearColorAtlas, the "CA_*" gradients) into pixels.

FModel can't export curve atlases as images; the game generates them from
CurveLinearColor assets, whose keys are in the exported JSON. An atlas is
TextureSize pixels wide with one row per curve (GradientCurves, in order);
pixel x samples the curve at time x / (width - 1), like UE's
UCurveLinearColorAtlas. Curves are FRichCurves (constant / linear / cubic
keys, constant extrapolation) with UCurveLinearColor's adjustments (hue,
saturation, brightness, brightness curve, vibrance, alpha range).

No Blender imports: returns plain lists, the builders make the image.
"""

import colorsys
import math

from .ue_assets import export_of_type, ref_path


def _eval_channel(keys, t):
    if not keys:
        return 0.0
    if t <= keys[0]["Time"]:
        return keys[0]["Value"]
    if t >= keys[-1]["Time"]:
        return keys[-1]["Value"]
    for k0, k1 in zip(keys, keys[1:]):
        t0, t1 = k0["Time"], k1["Time"]
        if t0 <= t <= t1:
            dt = t1 - t0
            if dt <= 1e-9:
                return k1["Value"]
            a = (t - t0) / dt
            mode = k0.get("InterpMode", "RCIM_Linear")
            if mode == "RCIM_Constant":
                return k0["Value"]
            if mode == "RCIM_Cubic":
                p0, p1 = k0["Value"], k1["Value"]
                m0 = k0.get("LeaveTangent", 0.0) * dt
                m1 = k1.get("ArriveTangent", 0.0) * dt
                a2, a3 = a * a, a * a * a
                return ((2 * a3 - 3 * a2 + 1) * p0 + (a3 - 2 * a2 + a) * m0
                        + (-2 * a3 + 3 * a2) * p1 + (a3 - a2) * m1)
            return k0["Value"] + (k1["Value"] - k0["Value"]) * a
    return keys[-1]["Value"]


def _channels(props):
    out = []
    for name in ("FloatCurves", "FloatCurves[1]", "FloatCurves[2]", "FloatCurves[3]"):
        keys = sorted((props.get(name) or {}).get("Keys") or [], key=lambda k: k["Time"])
        out.append(keys)
    return out


def eval_color(props, t):
    """Linear RGBA of a CurveLinearColor at time t, with its adjustments."""
    chans = _channels(props)
    r, g, b = (_eval_channel(chans[i], t) for i in range(3))
    a = _eval_channel(chans[3], t) if chans[3] else 1.0
    hue = props.get("AdjustHue", 0.0)
    sat = props.get("AdjustSaturation", 1.0)
    bri = props.get("AdjustBrightness", 1.0)
    bcurve = props.get("AdjustBrightnessCurve", 1.0)
    vib = props.get("AdjustVibrance", 0.0)
    if hue or sat != 1.0 or bri != 1.0 or bcurve != 1.0 or vib:
        mx = max(r, g, b)
        scale = mx if mx > 1.0 else 1.0          # colorsys works on 0..1; keep HDR values
        h, s, v = colorsys.rgb_to_hsv(max(r, 0) / scale, max(g, 0) / scale, max(b, 0) / scale)
        v *= scale
        h = (h + hue / 360.0) % 1.0
        s = s * sat
        if vib:
            s = s + (1.0 - s) * max(0.0, min(1.0, vib)) * (1.0 - s)
        s = max(0.0, min(1.0, s))
        v *= bri
        if bcurve not in (0.0, 1.0) and v > 0:
            v = math.pow(v, bcurve)
        r, g, b = colorsys.hsv_to_rgb(h, s, 1.0)
        r, g, b = r * v, g * v, b * v
    lo, hi = props.get("AdjustMinAlpha", 0.0), props.get("AdjustMaxAlpha", 1.0)
    a = lo + (hi - lo) * a
    return r, g, b, a


def atlas_pixels(finder, atlas_export):
    """(width, height, flat RGBA list bottom row first) for a CurveLinearColorAtlas,
    or None. Row i (top = first curve) is curve i; missing curves stay black."""
    props = atlas_export.get("Properties") or {}
    width = int(props.get("TextureSize") or 256)
    curves = props.get("GradientCurves") or []
    if not curves:
        return None
    height = len(curves)
    rows = []
    for c in curves:
        path = ref_path(c)
        exp = export_of_type(finder.load_asset_json(path), ("CurveLinearColor",)) if path else None
        cp = (exp or {}).get("Properties") or {}
        if not cp:
            rows.append([(0.0, 0.0, 0.0, 1.0)] * width)
            continue
        rows.append([eval_color(cp, x / max(width - 1, 1)) for x in range(width)])
    # Blender images start at the bottom; atlas row 0 is the top (UV v = (i + 0.5) / height from the top)
    flat = []
    for row in reversed(rows):
        for px in row:
            flat.extend(px)
    return width, height, flat
