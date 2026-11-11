"""Reusable shader node groups.

Each group is rebuilt only when its version tag changes, so users can open and
tweak them; materials built later reuse the same group.
"""

import bpy

from .nodes import NodeBuilder, new_group, add_socket

VERSION = 9
_TAG = "ft_group_version"


def _get(name, build):
    ng = bpy.data.node_groups.get(name)
    if ng is not None and ng.get(_TAG) == VERSION:
        return ng
    ng = new_group(name)
    build(ng)
    ng[_TAG] = VERSION
    NodeBuilder(ng).layout()
    return ng


def _io(ng):
    nb = NodeBuilder(ng)
    gi = nb.node("NodeGroupInput")
    go = nb.node("NodeGroupOutput")
    return nb, gi.outputs, go.inputs


# ─────────────────────────────────────────────────────────────────────────────
# Colour mask → colour.  ColorMask R/G/B pick ColorA/B/C; the scheme mask
# (BaseColor texture R in the 8-layer master) blends scheme 1 → scheme 2.
# ─────────────────────────────────────────────────────────────────────────────

def color_mask():
    """ColorMask R/G/B pick ColorA/B/C (scheme 1) or ColorA2/B2/C2 (scheme 2).

    The swatch switches mask channels on/off; where no enabled channel covers
    a pixel the Fallback colour (white) is used, so the layer's overlay tint
    shows as-is. E.g. CombatPants MajorsEventPass layer 3: swatch black,
    overlay #B51B34 → red.
    """
    def build(ng):
        add_socket(ng, "Color", "COLOR", "OUTPUT")
        add_socket(ng, "Color Mask", "COLOR", default=(1, 0, 0))
        add_socket(ng, "Scheme Mask", "FLOAT", default=0.0, lo=0, hi=1)
        add_socket(ng, "Swatch", "COLOR", default=(1, 1, 1))
        for n in ("ColorA", "ColorB", "ColorC", "ColorA2", "ColorB2", "ColorC2"):
            add_socket(ng, n, "COLOR", default=(1, 1, 1))
        add_socket(ng, "Fallback", "COLOR", default=(1, 1, 1))
        nb, i, o = _io(ng)
        r, g, b = nb.separate(nb.mix(i["Color Mask"], i["Swatch"], 1.0, "MULTIPLY"))
        total = nb.add(nb.add(r, g), b)
        norm = nb.math("MAXIMUM", total, 1.0)

        def scheme(a, bb, c):
            acc = nb.vmath("SCALE", i[a], scale=nb.math("DIVIDE", r, norm))
            acc = nb.vmath("ADD", acc, nb.vmath("SCALE", i[bb], scale=nb.math("DIVIDE", g, norm)))
            acc = nb.vmath("ADD", acc, nb.vmath("SCALE", i[c], scale=nb.math("DIVIDE", b, norm)))
            return acc
        s1 = scheme("ColorA", "ColorB", "ColorC")
        s2 = scheme("ColorA2", "ColorB2", "ColorC2")
        picked = nb.mix(s1, s2, i["Scheme Mask"])
        uncovered = nb.sub(1.0, total, clamp=True)
        rest = nb.vmath("SCALE", i["Fallback"], scale=uncovered)
        nb.feed(o["Color"], nb.vmath("ADD", picked, rest))
    return _get("FT Color Mask", build)


# ─────────────────────────────────────────────────────────────────────────────
# Tangent-space normal blending (UDN). Works on 0-1 encoded colours so it
# doesn't care about OpenGL/DirectX, as long as both inputs match.
# ─────────────────────────────────────────────────────────────────────────────

def blend_normals():
    def build(ng):
        add_socket(ng, "Normal", "COLOR", "OUTPUT")
        add_socket(ng, "Base", "COLOR", default=(0.5, 0.5, 1.0))
        add_socket(ng, "Detail", "COLOR", default=(0.5, 0.5, 1.0))
        add_socket(ng, "Strength", "FLOAT", default=1.0, lo=0, hi=4)
        nb, i, o = _io(ng)
        br, bg, bb = nb.separate(i["Base"])
        dr, dg, _db = nb.separate(i["Detail"])
        # (d.xy - 0.5) * strength added to base xy
        x = nb.add(br, nb.mul(nb.sub(dr, 0.5), i["Strength"]))
        y = nb.add(bg, nb.mul(nb.sub(dg, 0.5), i["Strength"]))
        nb.feed(o["Normal"], nb.combine(x, y, bb))
    return _get("FT Blend Normals", build)


def normal_from_rg():
    """Rebuild a normal colour from two channels (RG-packed maps)."""
    def build(ng):
        add_socket(ng, "Normal", "COLOR", "OUTPUT")
        add_socket(ng, "X", "FLOAT", default=0.5, lo=0, hi=1)
        add_socket(ng, "Y", "FLOAT", default=0.5, lo=0, hi=1)
        add_socket(ng, "Strength", "FLOAT", default=1.0, lo=0, hi=4)
        nb, i, o = _io(ng)
        x = nb.mul(nb.sub(nb.mul(i["X"], 2.0), 1.0), i["Strength"])
        y = nb.mul(nb.sub(nb.mul(i["Y"], 2.0), 1.0), i["Strength"])
        xx = nb.add(nb.mul(x, x), nb.mul(y, y))
        z = nb.math("SQRT", nb.math("MAXIMUM", nb.sub(1.0, xx), 0.0))
        enc = lambda v: nb.add(nb.mul(v, 0.5), 0.5)
        nb.feed(o["Normal"], nb.combine(enc(x), enc(y), enc(z)))
    return _get("FT Normal From RG", build)


# ─────────────────────────────────────────────────────────────────────────────
# One layer of the layered character shader.
# Base / edge / crease regions come from the curvature channel (OCM green):
# above 0.5 is an edge, below 0.5 is a crease.
# ─────────────────────────────────────────────────────────────────────────────

REGIONS = ("Base", "Edge", "Crease")


def surface_layer():
    def build(ng):
        for name, kind in (("Color", "COLOR"), ("Roughness", "FLOAT"), ("Metallic", "FLOAT"),
                           ("Specular", "FLOAT"), ("Detail Normal", "COLOR"),
                           ("Edge Mask", "FLOAT"), ("Crease Mask", "FLOAT"), ("Sheen", "FLOAT")):
            add_socket(ng, name, kind, "OUTPUT")
        add_socket(ng, "Mask Color", "COLOR", default=(1, 1, 1))
        add_socket(ng, "Curvature", "FLOAT", default=0.5, lo=0, hi=1)
        for r in REGIONS:
            add_socket(ng, f"{r} Color Mask Strength", "FLOAT", default=1.0, lo=0, hi=1)
            add_socket(ng, f"{r} Overlay", "COLOR", default=(1, 1, 1))
            add_socket(ng, f"{r} Overlay Alpha", "FLOAT", default=0.0, lo=0, hi=1)
            add_socket(ng, f"{r} Texture Strength", "FLOAT", default=0.0, lo=0, hi=1)
            add_socket(ng, f"{r} Roughness", "FLOAT", default=0.0, lo=-1, hi=1)   # offset, can be negative
            add_socket(ng, f"{r} Roughness Mask", "FLOAT", default=0.5, lo=0, hi=1)
            add_socket(ng, f"{r} Metallic", "FLOAT", default=0.0, lo=0, hi=1)
            add_socket(ng, f"{r} Specular", "FLOAT", default=0.5, lo=0, hi=1)
            add_socket(ng, f"{r} Detail Normal", "COLOR", default=(0.5, 0.5, 1.0))
        for r in ("Edge", "Crease"):
            add_socket(ng, f"{r} Amount", "FLOAT", default=0.0, lo=0, hi=1)
            add_socket(ng, f"{r} Softness", "FLOAT", default=0.5, lo=0, hi=4)
            add_socket(ng, f"{r} Breakup", "FLOAT", default=0.5, lo=0, hi=1)
        add_socket(ng, "Edge Thickness", "FLOAT", default=0.3, lo=0.0, hi=0.49)
        add_socket(ng, "Color Texture", "COLOR", default=(1, 1, 1))
        add_socket(ng, "Hue Noise", "COLOR", default=(0.5, 0.33, 0.3))
        add_socket(ng, "Hue Variation", "FLOAT", default=0.0, lo=0, hi=1)
        add_socket(ng, "Pattern", "COLOR", default=(0, 0, 0))
        add_socket(ng, "Pattern Swatch", "COLOR", default=(1, 1, 1))
        add_socket(ng, "Pattern Swatch Mask", "COLOR", default=(1, 1, 1))
        add_socket(ng, "Pattern Strength", "FLOAT", default=0.0, lo=0, hi=1)
        add_socket(ng, "Medium Normal", "COLOR", default=(0.5, 0.5, 1.0))
        add_socket(ng, "Medium Normal Strength", "FLOAT", default=0.0, lo=0, hi=2)
        add_socket(ng, "Shade As Cloth", "FLOAT", default=0.0, lo=0, hi=1)

        nb, i, o = _io(ng)
        curv = i["Curvature"]

        # Edge / crease masks: curvature distance from 0.5, broken up by a
        # tiling mask (0.5 = neutral), then thresholded by Amount.
        def region_mask(raw, r):
            broken = nb.clamp01(nb.mul(raw, nb.mul(i[f"{r} Breakup"], 2.0)))
            lo = nb.sub(1.0, i[f"{r} Amount"])
            soft = nb.math("MAXIMUM", nb.mul(i[f"{r} Softness"], 0.5), 0.01)
            m = nb.map_range(broken, lo, nb.add(lo, soft), mode="SMOOTHSTEP")
            on = nb.math("GREATER_THAN", i[f"{r} Amount"], 0.0)
            return nb.mul(m, on)

        # Thickness widens the edge band: 0 follows the curvature map as-is
        # (0.5 → 1.0), larger values saturate sooner (0.3 → full at 0.7).
        # A thickness of 0 is common (BikerVest Leather, CombatPants Nylon) and
        # must not turn every convex area into a hard edge.
        edge_raw = nb.map_range(curv, 0.5, nb.sub(1.0, i["Edge Thickness"]))
        crease_raw = nb.map_range(curv, 0.5, 0.0)
        edge = region_mask(edge_raw, "Edge")
        crease = region_mask(crease_raw, "Crease")

        def region_color(r):
            if r == "Base":
                # Base = mix(overlay, mask colour × tint, ColorMaskStrength), where
                # tint = white → overlay by the overlay's alpha. Checked against
                # store images: CombatPants SSG pockets (strength 0, #4F4F4F) dark;
                # SSG body (1, a 0) mask yellow; Nylon fabric (1, #3F3E41, a 1)
                # black; Envy pockets (0.38, #495571) muted blue (66,75,102);
                # BikerVest (1, #96989A, a 1 over #6C6E70) dark grey (~60).
                tint = nb.mix((1, 1, 1, 1), i["Base Overlay"], i["Base Overlay Alpha"])
                masked = nb.mix(i["Mask Color"], tint, 1.0, "MULTIPLY")
                tinted = nb.mix(i["Base Overlay"], masked, i["Base Color Mask Strength"])
            else:
                # Edge / crease: the overlay colour is painted over the base by
                # its alpha (below); e.g. grey worn edges, darkened creases.
                tinted = i[f"{r} Overlay"]
            return nb.mix(tinted, i["Color Texture"], i[f"{r} Texture Strength"], "MULTIPLY")

        def region_rough(r):
            # Roughness is an offset on the mask (flat masks are 0.5; skins use
            # negative values, e.g. -0.48 for polished leather edges).
            return nb.add(i[f"{r} Roughness Mask"], i[f"{r} Roughness"], clamp=True)

        def by_region(f):
            base = f("Base")
            return base, f("Edge"), f("Crease")

        def blend(vals, color=False):
            b, e, c = vals
            if color:
                return nb.mix(nb.mix(b, e, edge), c, crease)
            return nb.lerp(nb.lerp(b, e, edge), c, crease)

        # Edge / crease colour is painted over the base by the overlay's alpha
        # (alpha 0 = the region keeps the base colour; only its surface changes).
        b_col, e_col, c_col = by_region(region_color)
        color = nb.mix(nb.mix(b_col, e_col, nb.mul(edge, i["Edge Overlay Alpha"])),
                       c_col, nb.mul(crease, i["Crease Overlay Alpha"]))

        # Hue variation: low-frequency noise gently varies brightness. In game
        # the effect is barely visible (e.g. the red beret at 0.1 shows no
        # orange), so the hue itself is left alone; shifting it turned
        # saturated reds into orange/magenta patches.
        luma = nb.node("ShaderNodeRGBToBW")
        nb.feed(luma.inputs[0], i["Hue Noise"])
        hsv = nb.node("ShaderNodeHueSaturation")
        nb.feed(hsv.inputs["Hue"], 0.5)
        nb.feed(hsv.inputs["Saturation"], 1.0)
        nb.feed(hsv.inputs["Value"], nb.add(1.0, nb.mul(nb.sub(luma.outputs[0], 0.5), i["Hue Variation"])))
        nb.feed(hsv.inputs["Fac"], 1.0)
        nb.feed(hsv.inputs["Color"], color)
        color = hsv.outputs[0]

        # Pattern overlay: pattern channels weighted by the swatch mask.
        pat = nb.vmath("DOT_PRODUCT", i["Pattern"], i["Pattern Swatch Mask"])
        pat = nb.mul(nb.clamp01(pat), i["Pattern Strength"])
        color = nb.mix(color, i["Pattern Swatch"], pat)

        nb.feed(o["Color"], color)
        nb.feed(o["Roughness"], blend(by_region(region_rough)))
        nb.feed(o["Metallic"], blend(by_region(lambda r: i[f"{r} Metallic"])))
        nb.feed(o["Specular"], blend(by_region(lambda r: i[f"{r} Specular"])))

        dn = blend(by_region(lambda r: i[f"{r} Detail Normal"]), color=True)
        bl = nb.group(blend_normals())
        nb.feed(bl.inputs["Base"], dn)
        nb.feed(bl.inputs["Detail"], i["Medium Normal"])
        nb.feed(bl.inputs["Strength"], i["Medium Normal Strength"])
        nb.feed(o["Detail Normal"], bl.outputs[0])
        nb.feed(o["Edge Mask"], edge)
        nb.feed(o["Crease Mask"], crease)
        nb.feed(o["Sheen"], i["Shade As Cloth"])
    return _get("FT Surface Layer", build)


# ─────────────────────────────────────────────────────────────────────────────
# Lace opacity fade (M_Lace / M_LaceSkirt).  The game fades lace along a
# gradient; we run it along the UV V axis, which follows the lace strips.
# ─────────────────────────────────────────────────────────────────────────────

def lace_fade():
    def build(ng):
        add_socket(ng, "Opacity", "FLOAT", "OUTPUT")
        add_socket(ng, "Gradient", "FLOAT", default=0.0)
        add_socket(ng, "Fade Start", "FLOAT", default=0.0)
        add_socket(ng, "Min Transparency", "FLOAT", default=0.0)
        add_socket(ng, "Offset Gradient", "FLOAT", default=0.0)
        add_socket(ng, "Transparency", "FLOAT", default=1.0, lo=0, hi=1)
        nb, i, o = _io(ng)
        g = nb.clamp01(nb.add(i["Gradient"], i["Offset Gradient"]))
        span = nb.math("MAXIMUM", nb.sub(1.0, i["Fade Start"]), 0.01)
        f = nb.clamp01(nb.math("DIVIDE", nb.sub(g, i["Fade Start"]), span))
        op = nb.lerp(1.0, nb.clamp01(i["Min Transparency"]), f)
        nb.feed(o["Opacity"], nb.mul(op, i["Transparency"], clamp=True))
    return _get("FT Lace Fade", build)
