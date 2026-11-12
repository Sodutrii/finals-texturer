"""Reusable shader node groups.

Each group is rebuilt only when its version tag changes, so users can open and
tweak them; materials built later reuse the same group.
"""

import bpy

from .nodes import NodeBuilder, new_group, add_socket

VERSION = 13
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
    """Colour mask → colour, as the game's compiled shader does it: starting
    from Start (white), lerp toward ColorA by the mask's R, then toward ColorB
    by G, then ColorC by B (a later channel wins where several are set), each
    weight x Strength. Each colour is lerped to its scheme-2 version (A2/B2/C2)
    by the scheme mask (BaseColor texture R). The Color Mask input is the
    ColorMask texture x the layer's ColorMaskSwatch (channels it disables stay
    at Start)."""
    def build(ng):
        add_socket(ng, "Color", "COLOR", "OUTPUT")
        add_socket(ng, "Color Mask", "COLOR", default=(1, 0, 0))
        add_socket(ng, "Scheme Mask", "FLOAT", default=0.0, lo=0, hi=1)
        add_socket(ng, "Strength", "FLOAT", default=1.0, lo=0, hi=1)
        for n in ("ColorA", "ColorB", "ColorC", "ColorA2", "ColorB2", "ColorC2"):
            add_socket(ng, n, "COLOR", default=(1, 1, 1))
        add_socket(ng, "Start", "COLOR", default=(1, 1, 1))
        nb, i, o = _io(ng)
        chans = nb.separate(i["Color Mask"])
        col = i["Start"]
        for ch, x in zip(chans, ("ColorA", "ColorB", "ColorC")):
            target = nb.mix(i[x], i[x + "2"], i["Scheme Mask"])
            col = nb.mix(col, target, nb.mul(ch, i["Strength"]))
        nb.feed(o["Color"], col)
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


def region_masks():
    """Edge and crease masks of a layer, as the game's compiled shaders do it
    (M_Character_8Layers_Master / M_Character_Layered, 2026-11-12):
      edge   x = 1 - sat(OCM.R x (2 curvature - 1)),  x' = x (x + 1 - EdgeMask)
      crease x = sat(2 curvature),                    x' = x (x + CreaseMask)
      mask = sat(((1 + s) x Amount - x') / s),  s = MaskSoftness (0 = hard step)
    EdgeMask / CreaseMask = the layer's TextureArray_M slices. New gen: x is
    multiplied by the BreakupMask texture. Old gen: x' gets a wear term from the
    BaseColor texture, clamp((channel - 0.5) x 2 x k, -1, 1) with blue and
    k = 1 + breakup for edges, green and k = 2 - breakup for creases.
    (EdgeMaskThickness only bevels normals; it doesn't change these masks.)"""
    def build(ng):
        add_socket(ng, "Edge", "FLOAT", "OUTPUT")
        add_socket(ng, "Crease", "FLOAT", "OUTPUT")
        add_socket(ng, "Curvature", "FLOAT", default=0.5, lo=0, hi=1)
        add_socket(ng, "Edge Scale", "FLOAT", default=1.0, lo=0, hi=1)          # OCM red
        add_socket(ng, "Breakup Mask", "FLOAT", default=1.0, lo=0, hi=1)
        add_socket(ng, "Old Gen", "FLOAT", default=0.0, lo=0, hi=1)
        add_socket(ng, "Edge Wear", "FLOAT", default=0.5, lo=0, hi=1)           # BaseColor B (old gen)
        add_socket(ng, "Crease Wear", "FLOAT", default=0.5, lo=0, hi=1)         # BaseColor G (old gen)
        for r in ("Edge", "Crease"):
            add_socket(ng, f"{r} Amount", "FLOAT", default=0.0, lo=0, hi=1)
            add_socket(ng, f"{r} Softness", "FLOAT", default=0.5, lo=0, hi=10)
            add_socket(ng, f"{r} Mask", "FLOAT", default=0.5, lo=0, hi=1)
        nb, i, o = _io(ng)
        old = i["Old Gen"]
        brk = nb.clamp01(i["Breakup Mask"])
        new_brk = nb.lerp(brk, 1.0, old)                      # new gen multiplies x by the breakup mask

        def finish(xp, r):
            s = nb.math("MAXIMUM", i[f"{r} Softness"], 0.0001)
            k = nb.mul(nb.add(s, 1.0), i[f"{r} Amount"])
            return nb.math("DIVIDE", nb.sub(k, xp), s, clamp=True)

        def wear(chan, kfac):
            w = nb.mul(nb.mul(nb.sub(chan, 0.5), 2.0), kfac)
            w = nb.math("MINIMUM", nb.math("MAXIMUM", w, -1.0), 1.0)
            return nb.mul(w, old)

        c = i["Curvature"]
        ex = nb.sub(1.0, nb.mul(i["Edge Scale"], nb.sub(nb.mul(c, 2.0), 1.0), clamp=True))
        ex = nb.mul(ex, new_brk)
        exp_ = nb.mul(ex, nb.sub(nb.add(ex, 1.0), i["Edge Mask"]))
        exp_ = nb.add(exp_, wear(i["Edge Wear"], nb.add(1.0, brk)))
        cx = nb.mul(nb.mul(c, 2.0, clamp=True), new_brk)
        cxp = nb.mul(cx, nb.add(cx, i["Crease Mask"]))
        cxp = nb.add(cxp, wear(i["Crease Wear"], nb.sub(2.0, brk)))
        nb.feed(o["Edge"], finish(exp_, "Edge"))
        nb.feed(o["Crease"], finish(cxp, "Crease"))
    return _get("FT Region Masks", build)


def surface_layer():
    """One layer of the layered clothing masters, after the game's compiled
    shader (M_Character_Layered / M_Character_8Layers_Master, 2026-11-12):
    colour = Mask Color (from FT Color Mask) x BaseColorOverlay.rgb (its alpha
    is not used) x the colour texture blended in by Base / Edge / Crease
    texture strength; old-gen pattern (PatternMask / PatternColorA-C); edge and
    crease overlays painted by mask x overlay alpha (old gen: crease first);
    hue variation = lerp toward the hue-noise colour by strength x 0.1.
    Roughness (mask + offset), metallic, specular and detail normals blend
    base → edge → crease (old gen: base → crease → edge)."""
    def build(ng):
        for name, kind in (("Color", "COLOR"), ("Roughness", "FLOAT"), ("Metallic", "FLOAT"),
                           ("Specular", "FLOAT"), ("Detail Normal", "COLOR"), ("Shade As Cloth", "FLOAT")):
            add_socket(ng, name, kind, "OUTPUT")
        add_socket(ng, "Mask Color", "COLOR", default=(1, 1, 1))
        add_socket(ng, "Edge Mask", "FLOAT", default=0.0, lo=0, hi=1)
        add_socket(ng, "Crease Mask", "FLOAT", default=0.0, lo=0, hi=1)
        for r in REGIONS:
            add_socket(ng, f"{r} Overlay", "COLOR", default=(1, 1, 1))
            if r != "Base":
                add_socket(ng, f"{r} Overlay Alpha", "FLOAT", default=0.0, lo=0, hi=1)
            add_socket(ng, f"{r} Texture Strength", "FLOAT", default=0.0, lo=0, hi=1)
            add_socket(ng, f"{r} Roughness", "FLOAT", default=0.0, lo=-1, hi=1)   # offset, can be negative
            add_socket(ng, f"{r} Roughness Mask", "FLOAT", default=0.5, lo=0, hi=1)
            add_socket(ng, f"{r} Metallic", "FLOAT", default=0.0, lo=0, hi=1)
            add_socket(ng, f"{r} Specular", "FLOAT", default=0.5, lo=0, hi=1)
            add_socket(ng, f"{r} Detail Normal", "COLOR", default=(0.5, 0.5, 1.0))
        add_socket(ng, "Color Texture", "COLOR", default=(1, 1, 1))
        add_socket(ng, "Pattern", "COLOR", default=(0, 0, 0))
        add_socket(ng, "Pattern Mask Alpha", "FLOAT", default=0.0, lo=0, hi=1)
        for x in "ABC":
            add_socket(ng, f"Pattern Color {x}", "COLOR", default=(1, 1, 1))
            add_socket(ng, f"Pattern Color {x} Alpha", "FLOAT", default=0.0, lo=0, hi=1)
        add_socket(ng, "Pattern Weight", "FLOAT", default=0.0, lo=0, hi=1)
        add_socket(ng, "Crease First", "FLOAT", default=0.0, lo=0, hi=1)
        add_socket(ng, "Hue Noise", "COLOR", default=(0.5, 0.33, 0.3))
        add_socket(ng, "Hue Variation", "FLOAT", default=0.0, lo=-10, hi=10)
        add_socket(ng, "Medium Normal", "COLOR", default=(0.5, 0.5, 1.0))
        add_socket(ng, "Medium Normal Strength", "FLOAT", default=0.0, lo=0, hi=2)
        add_socket(ng, "Shade As Cloth", "FLOAT", default=0.0, lo=0, hi=1)

        nb, i, o = _io(ng)
        edge, crease = i["Edge Mask"], i["Crease Mask"]
        base_w = nb.clamp01(nb.mul(nb.sub(nb.sub(1.0, edge), crease), 2.0))

        # colour texture: lerp(1, tex) by base / edge / crease texture strength
        tex = i["Color Texture"]
        t = nb.mix((1, 1, 1, 1), tex, nb.mul(i["Base Texture Strength"], base_w, clamp=True))
        t = nb.mix(t, tex, nb.mul(i["Edge Texture Strength"], edge, clamp=True))
        t = nb.mix(t, tex, nb.mul(i["Crease Texture Strength"], crease, clamp=True))
        color = nb.mix(nb.mix(i["Mask Color"], i["Base Overlay"], 1.0, "MULTIPLY"), t, 1.0, "MULTIPLY",
                       label="Mask colour x overlay x texture")

        # old-gen pattern: pattern RGB by PatternMask alpha, then PatternColorA/B/C by the
        # pattern's R/G/B x their alphas; painted by Pattern Weight (from the colour mask)
        pr, pg, pb = nb.separate(i["Pattern"])
        pc = nb.mix(color, i["Pattern"], i["Pattern Mask Alpha"])
        for ch, x in zip((pr, pg, pb), "ABC"):
            pc = nb.mix(pc, i[f"Pattern Color {x}"], nb.mul(ch, i[f"Pattern Color {x} Alpha"]))
        color = nb.mix(color, pc, i["Pattern Weight"], label="Pattern (old gen)")

        # edge / crease overlays (new gen: edge then crease; old gen: crease then edge)
        def paint(c, r, m):
            return nb.mix(c, i[f"{r} Overlay"], nb.mul(m, i[f"{r} Overlay Alpha"]), label=f"{r} overlay")
        ec = paint(paint(color, "Edge", edge), "Crease", crease)
        ce = paint(paint(color, "Crease", crease), "Edge", edge)
        color = nb.mix(ec, ce, i["Crease First"])

        # hue variation: lerp toward the noise colour by strength x 0.1
        color = nb.mix(color, i["Hue Noise"], nb.mul(i["Hue Variation"], 0.1), label="Hue variation")

        def blend(r_vals, as_color=False):
            # new gen: base → edge → crease; old gen ("Crease First"): base → crease → edge
            b, e, c = r_vals
            if as_color:
                ec = nb.mix(nb.mix(b, e, edge), c, crease)
                ce = nb.mix(nb.mix(b, c, crease), e, edge)
                return nb.mix(ec, ce, i["Crease First"])
            ec = nb.lerp(nb.lerp(b, e, edge), c, crease)
            ce = nb.lerp(nb.lerp(b, c, crease), e, edge)
            return nb.lerp(ec, ce, i["Crease First"])

        nb.feed(o["Color"], color)
        nb.feed(o["Roughness"], blend([nb.add(i[f"{r} Roughness Mask"], i[f"{r} Roughness"], clamp=True)
                                       for r in REGIONS]))
        nb.feed(o["Metallic"], blend([i[f"{r} Metallic"] for r in REGIONS]))
        nb.feed(o["Specular"], blend([i[f"{r} Specular"] for r in REGIONS]))
        dn = blend([i[f"{r} Detail Normal"] for r in REGIONS], as_color=True)
        bl = nb.group(blend_normals())
        nb.feed(bl.inputs["Base"], dn)
        nb.feed(bl.inputs["Detail"], i["Medium Normal"])
        nb.feed(bl.inputs["Strength"], i["Medium Normal Strength"])
        nb.feed(o["Detail Normal"], bl.outputs[0])
        nb.feed(o["Shade As Cloth"], i["Shade As Cloth"])
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
