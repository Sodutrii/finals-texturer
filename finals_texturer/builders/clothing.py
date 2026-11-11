"""Clothing shaders: layered (M_Character_8Layers_Master / M_Character_Layered,
incl. decals and number decals), preview, lace, lace skirt, feather,
attachment, glass, hologram, plastic sheet, team colour."""

import math

import bpy

from .. import defaults, groups
from ..nodes import _is_socket
from ..ue_assets import asset_name
from .common import NEUTRAL, _col, _out, _packed_normal, _normal_tex, _fresnel_ramp

# ─────────────────────────────────────────────────────────────────────────────
# Layered character shader (M_Character_8Layers_Master / M_Character_Layered)
# ─────────────────────────────────────────────────────────────────────────────

def build_layered(ctx, old_gen=False):
    p, nb, log = ctx.p, ctx.nb, ctx.log
    arr_c = p.t("TextureArray_C") or p.t("TextureArray_Colors")
    arr_m = p.t("TextureArray_M") or p.t("TextureArray_Masks")
    arr_n = p.t("TextureArray_N") or p.t("TextureArray_Normals")
    arr_p = p.t("TextureArray_Patterns")
    tiling = p.s("Tiling", 1.0) or 1.0

    # Occlusion / Curvature / Material ID (+ opacity in alpha)
    ocm = ctx.tex(p.t("OcclusionCurvatureMaterialID"), data=True, packed=True, label="OCM (AO / Curvature / ID / Opacity)")
    if ocm is not None:
        ao, curv, mat_id = nb.separate(ocm.outputs["Color"], label="OCM channels")
        ocm_alpha = ocm.outputs["Alpha"]
    else:
        log.warn("  No OcclusionCurvatureMaterialID texture: edges, creases and AO will be flat.")
        ao, curv, mat_id, ocm_alpha = 1.0, 0.5, 0.0, 1.0

    cmask = ctx.tex(p.t("ColorMask"), data=True, label="Color Mask")
    cmask_out = _out(cmask, fallback=(1, 0, 0, 1))
    scheme_tex = ctx.tex(p.t("BaseColor"), data=True, label="Color Scheme Mask (BaseColor R)")

    n_layers = 1
    for k in range(2, 9):
        if p.sw(f"UseLayer{k}"):
            n_layers = k
    if n_layers > 1:
        log.info(f"  {n_layers} layers, picked per pixel by the material ID (OCM blue).")

    outs = []
    for k in range(1, n_layers + 1):
        outs.append(_build_layer(ctx, k, curv, cmask_out, scheme_tex, old_gen,
                                 arr_c, arr_m, arr_n, arr_p, tiling))

    ctx.layer_masks = _layer_masks(ctx, mat_id, n_layers)
    if n_layers == 1:
        lay = outs[0]
    else:
        lay = _combine_layers(ctx, outs, ctx.layer_masks)

    color = nb.mix(lay["Color"], ao, 1.0, "MULTIPLY", label="Ambient Occlusion")
    normal_detail = lay["Detail Normal"]

    # Decals
    if old_gen:
        # Decal slots are independent of layers: an item with 3 layers can use
        # decal slots 1-8.
        for k in range(1, 9):
            if p.sw(f"{k}_UseDecal"):
                color = _old_gen_decal(ctx, k, color)
    else:
        color = _global_decal(ctx, color)

    # Normals: mesh normal map + detail normals
    mesh_n = ctx.tex(p.t("Normal"), data=True, label="Normal")
    blend = nb.group(groups.blend_normals(), label="Mesh + Detail Normal")
    nb.feed(blend.inputs["Base"], _out(mesh_n, fallback=NEUTRAL["normal"]))
    nb.feed(blend.inputs["Detail"], normal_detail)

    bsdf = ctx.bsdf()
    nb.feed(bsdf.inputs["Base Color"], color)
    # "Shade As Cloth" (UE cloth shading: soft fuzz) is approximated with sheen below.
    nb.feed(bsdf.inputs["Roughness"], lay["Roughness"])
    nb.feed(bsdf.inputs["Metallic"], lay["Metallic"])
    nb.feed(bsdf.inputs["Specular IOR Level"], lay["Specular"])
    nb.feed(bsdf.inputs["Normal"], ctx.normal_map(blend.outputs[0]))
    if "Sheen Weight" in bsdf.inputs:
        nb.feed(bsdf.inputs["Sheen Weight"], lay["Sheen"])
        # Cloth fuzz takes the fabric colour; a white sheen washes reds out to pink.
        if "Sheen Tint" in bsdf.inputs:
            nb.feed(bsdf.inputs["Sheen Tint"], color)

    if p.sw("bUseOpacityMask") and ocm is not None:
        clip = p.opacity_clip if p.opacity_clip else 0.3333
        nb.feed(bsdf.inputs["Alpha"], nb.math("GREATER_THAN", ocm_alpha, clip, label=f"Opacity clip {clip:g}"))
    ctx.finish(two_sided=p.two_sided if p.two_sided is not None else False)


def _build_layer(ctx, k, curv, cmask_out, scheme_tex, old_gen, arr_c, arr_m, arr_n, arr_p, tiling):
    p, nb = ctx.p, ctx.nb
    P = f"{k}_"

    def s(name, default=0.0):
        return p.s(P + name, default)

    def v(name, default=(1, 1, 1, 1)):
        return p.v(P + name, default)

    detail = s("DetailTiling", 20.0) * tiling

    # Colour mask → this layer's colour
    cm = nb.group(groups.color_mask(), label=f"Layer {k} Colours")
    nb.feed(cm.inputs["Color Mask"], cmask_out)
    nb.feed(cm.inputs["Swatch"], _col(v("ColorMaskSwatch")))
    for name in ("ColorA", "ColorB", "ColorC", "ColorA2", "ColorB2", "ColorC2"):
        nb.feed(cm.inputs[name], _col(p.v(name)))
    # Colour scheme (ColorA/B/C vs ColorA2/B2/C2) is picked per pixel by the
    # BaseColor texture's red channel; old-gen scales it by ColorSchemeBlend.
    # E.g. CombatPants Fnatic: R = 0 on the pocket bands → scheme 1 (light grey).
    blend_amt = s("ColorSchemeBlend", 1.0) if old_gen else 1.0
    if scheme_tex is not None:
        scheme_in = nb.mul(ctx.red(scheme_tex), blend_amt, clamp=True)
    else:
        scheme_in = blend_amt if old_gen else 0.0
    nb.feed(cm.inputs["Scheme Mask"], scheme_in)

    g = nb.group(groups.surface_layer(), label=f"Layer {k}")
    gi = g.inputs
    nb.feed(gi["Mask Color"], cm.outputs[0])
    nb.feed(gi["Curvature"], curv)

    regions_used = {"Base": True, "Edge": s("EdgeAmount") > 0, "Crease": s("CreaseAmount") > 0}
    for r in ("Base", "Edge", "Crease"):
        ov = v(f"{r}ColorOverlay", (1, 1, 1, 0))
        nb.feed(gi[f"{r} Color Mask Strength"], s(f"{r}ColorMaskStrength", 1.0))
        nb.feed(gi[f"{r} Overlay"], _col(ov))
        nb.feed(gi[f"{r} Overlay Alpha"], ov[3])
        nb.feed(gi[f"{r} Texture Strength"], s(f"{r}TextureStrength"))
        nb.feed(gi[f"{r} Roughness"], s(f"{r}Roughness", 0.0))
        nb.feed(gi[f"{r} Metallic"], s(f"{r}Metallicity"))
        nb.feed(gi[f"{r} Specular"], s(f"{r}Specular", 0.5))
        if not regions_used[r]:
            continue
        rough_tiling = p.scalars.get(P + f"{r}RoughnessTiling", p.scalars.get(P + f"{r}RougnessTiling", 1.0))
        rm = ctx.slice(arr_m, s(f"{r}RoughnessID"), data=True, scale=detail * rough_tiling,
                       label=f"L{k} {r} Roughness [{int(s(f'{r}RoughnessID'))}]")
        if rm is not None:
            nb.feed(gi[f"{r} Roughness Mask"], ctx.red(rm))
        n_tiling = 1.0 if r == "Base" else s(f"{r}NormalTiling", 1.0)
        dn = ctx.slice(arr_n, s(f"{r}NormalID"), data=True, scale=detail * n_tiling,
                       label=f"L{k} {r} Normal [{int(s(f'{r}NormalID'))}]")
        if dn is not None:
            nb.feed(gi[f"{r} Detail Normal"], dn.outputs["Color"])

    for r in ("Edge", "Crease"):
        nb.feed(gi[f"{r} Amount"], s(f"{r}Amount"))
        nb.feed(gi[f"{r} Softness"], s(f"{r}MaskSoftness"))
        if regions_used[r]:
            bm = ctx.slice(arr_m, s(f"{r}MaskID"), data=True, scale=detail * s(f"{r}MaskTiling", 1.0),
                           label=f"L{k} {r} Breakup [{int(s(f'{r}MaskID'))}]")
            if bm is not None:
                nb.feed(gi[f"{r} Breakup"], ctx.red(bm))
    nb.feed(gi["Edge Thickness"], min(max(s("EdgeMaskThickness", 0.3), 0.0), 0.49))

    if any(s(f"{r}TextureStrength") > 0 for r in ("Base", "Edge", "Crease")):
        ct = ctx.slice(arr_c, s("ColorTextureID"), data=False, scale=detail * s("ColorTextureTiling", 1.0),
                       label=f"L{k} Color Texture [{int(s('ColorTextureID'))}]")
        if ct is not None:
            nb.feed(gi["Color Texture"], ct.outputs["Color"])

    hue = s("HueVariationStrength")
    nb.feed(gi["Hue Variation"], hue)
    if hue > 0:
        hn = ctx.tex(p.t("HueVariation"), data=False, scale=s("HueVariationTiling", 0.15) or 0.15,
                     label=f"L{k} Hue Noise", detail=True)
        if hn is not None:
            nb.feed(gi["Hue Noise"], hn.outputs["Color"])
        else:
            nb.feed(gi["Hue Variation"], 0.0)

    # Pattern overlay. PatternSwatch is a channel selector, not a colour: its
    # RGB picks which channels of the pattern texture are used and its alpha
    # switches the pattern on. Like a second colour mask, the selected pattern
    # channels R/G/B paint ColorA/B/C (same colour scheme). PatternSwatchMask
    # limits it to the colour-mask channels it lists. E.g. Ballerina
    # MasqueradeMask Glitter rim: swatch (1,0,0,1) → gold-on-gold engraving
    # instead of red stripes.
    sw = v("PatternSwatch", (0, 0, 0, 0))
    if sw[3] > 0 and any(c > 0 for c in sw[:3]):
        pt = ctx.slice(arr_p, s("PatternID"), data=True, scale=s("PatternTiling", 1.0),
                       label=f"L{k} Pattern [{int(s('PatternID'))}]")
        if pt is not None:
            sel = (sw[0], sw[1], sw[2])
            picked = nb.vmath("MULTIPLY", pt.outputs["Color"], sel, label=f"L{k} pattern channels")
            pcm = nb.group(groups.color_mask(), label=f"Layer {k} Pattern Colours")
            nb.feed(pcm.inputs["Color Mask"], pt.outputs["Color"])
            nb.feed(pcm.inputs["Swatch"], _col(sel))
            for name in ("ColorA", "ColorB", "ColorC", "ColorA2", "ColorB2", "ColorC2"):
                nb.feed(pcm.inputs[name], _col(p.v(name)))
            nb.feed(pcm.inputs["Scheme Mask"], scheme_in)
            nb.feed(pcm.inputs["Fallback"], (0, 0, 0, 1))
            total = nb.vmath("DOT_PRODUCT", picked, (1.0, 1.0, 1.0))
            inv = nb.math("DIVIDE", 1.0, nb.math("MAXIMUM", total, 0.001))
            pcol = nb.vmath("SCALE", pcm.outputs[0], scale=inv, label=f"L{k} pattern colour")
            gate = nb.vmath("DOT_PRODUCT", cmask_out, tuple(v("PatternSwatchMask")[:3]))
            nb.feed(gi["Pattern"], picked)
            nb.feed(gi["Pattern Swatch Mask"], (1, 1, 1, 1))
            nb.feed(gi["Pattern Swatch"], pcol)
            nb.feed(gi["Pattern Strength"], nb.mul(nb.clamp01(gate) if _is_socket(gate) else gate, sw[3], clamp=True))

    mns = s("MediumNormalStrength")
    nb.feed(gi["Medium Normal Strength"], mns)
    if mns > 0:
        mt = s("MediumNormalTiling", 1.0) or 1.0
        mn = ctx.slice(arr_n, s("MediumNormalID"), data=True, scale=detail * mt,
                       label=f"L{k} Medium Normal [{int(s('MediumNormalID'))}]")
        if mn is not None:
            nb.feed(gi["Medium Normal"], mn.outputs["Color"])
    nb.feed(gi["Shade As Cloth"], s("ShadeAsCloth"))
    return {name: g.outputs[name] for name in
            ("Color", "Roughness", "Metallic", "Specular", "Detail Normal", "Sheen")}


def _layer_masks(ctx, mat_id, n_layers):
    """{layer: 0/1 mask}. The OCM blue channel stores layer N at N/8
    (layer 1 ≈ 0.125 … layer 8 = 1.0). Measured on Racing items with 1, 5 and
    7 layers: 8-bit peaks at 37, 66, 96, 126, 158, 222 → round(B × 8)."""
    nb = ctx.nb
    if n_layers == 1 or not _is_socket(mat_id):
        return {1: 1.0}
    masks = {}
    for k in range(1, n_layers + 1):
        dist = nb.math("ABSOLUTE", nb.sub(nb.mul(mat_id, 8.0), float(k)))
        masks[k] = nb.math("LESS_THAN", dist, 0.5, label=f"Layer {k} mask")
    # Anything outside the known IDs falls back to layer 1.
    return masks


def _combine_layers(ctx, outs, masks):
    nb = ctx.nb
    res = dict(outs[0])
    for idx, lay in enumerate(outs[1:], start=2):
        m = masks[idx]
        for key in res:
            if key in ("Color", "Detail Normal"):
                res[key] = nb.mix(res[key], lay[key], m)
            else:
                res[key] = nb.lerp(res[key], lay[key], m)
    return res


# ── decals ───────────────────────────────────────────────────────────────────

def _decal_vector(ctx, cx, cy, sx, sy, rotation):
    """UV → decal space: decal UV = S ⊙ R(uv − centre) + 0.5.

    cx, cy  decal centre in Blender UV space
    sx, sy  how many decal widths/heights fit across one UV unit (1 = whole map)
    rotation in turns (UE stores 0..1); applied as rotation * -360°.
    Rotation comes before the scale so wide decals (WidthRatio ≠ 1) keep
    their shape when rotated (BikerVest chest text: WidthRatio 4, ~90°).
    UVs are wrapped to 0..1 first, because items like gloves put each side
    in a different whole-number UV tile.
    """
    nb = ctx.nb
    rot = math.radians(rotation * -360.0)
    uv = nb.vmath("FRACTION", ctx.uv(), label="Wrap UV to 0..1")
    rel = nb.vmath("SUBTRACT", uv, (cx, cy, 0.0), label="Decal centre")
    r = nb.node("ShaderNodeVectorRotate", "Decal rotation", rotation_type="Z_AXIS")
    nb.feed(r.inputs["Vector"], rel)
    r.inputs["Angle"].default_value = rot
    scaled = nb.vmath("MULTIPLY", r.outputs[0], (sx, sy, 1.0), label="Decal size")
    return nb.vmath("ADD", scaled, (0.5, 0.5, 0.0), label="Decal placement")


def _apply_decal(ctx, color, tex_path, vec, color_a, color_b, recolor=1.0, label="Decal",
                 number=None, coverage=1.0):
    """Composite a decal over colour.

    Recoloured decals map texture brightness to the two decal colours: white
    texels → ColorA, black texels → ColorB (by brightness for colour decals,
    by the red channel for *_M / *_CM masks). With A = white and B = black a
    colour decal keeps its own colours. Checked on the CombatPants Envy skin
    ("ENVY" text: white mask texels, ColorA light grey → light text) and the
    Trentila gloves. recolor (old-gen ColorOverride, new-gen DecalTint) blends
    from the texture's own colours (0) to the recoloured result (1).
    number: show one number from a 0-9 digit atlas (UseNumber).
    coverage: extra 0..1 mask (e.g. the layers a decal is limited to).
    """
    nb = ctx.nb
    name = asset_name(tex_path).lower()
    is_mask = name.endswith("_m") or name.endswith("_cm")
    if number is not None:
        vec = _number_atlas_vector(ctx, vec, number)
    t = ctx.tex(tex_path, data=is_mask, packed=True, vector=vec, extension="CLIP", label=label)
    if t is None:
        return color
    raw = t.outputs["Color"]
    a = tuple(_col(color_a)[:3])
    b = tuple(_col(color_b)[:3])
    if is_mask:
        r, _g, _b = nb.separate(raw)
        tinted = nb.mix(_col(color_b), _col(color_a), r, label=f"{label} colours")
        own = nb.combine(r, r, r)
    else:
        # Recolour by brightness: coloured artwork still maps white → ColorA,
        # black → ColorB (BikerVest Leather_USA "Chop Chop" badge: red art,
        # A light grey, B black → grey badge like the store image).
        lum = nb.node("ShaderNodeRGBToBW", f"{label} brightness")
        nb.feed(lum.inputs[0], raw)
        tinted = nb.mix(_col(color_b), _col(color_a), lum.outputs[0], label=f"{label} colours")
        own = raw
    recolor = max(0.0, min(1.0, recolor))
    dcol = tinted if recolor >= 1.0 else nb.mix(own, tinted, recolor, label=f"{label} recolour")
    alpha = t.outputs["Alpha"]
    if coverage != 1.0:
        alpha = nb.mul(alpha, coverage, clamp=True)
    return nb.mix(color, dcol, alpha, label=label)


NUMBER_NODE = "FT_Number"


def _number_value(ctx, default):
    """One editable 'Number' value per material, shared by its number decals.

    The game fills the number in at runtime (no skin file sets it; the master
    default is 8), so it's exposed here and in the Finals panel instead.
    """
    tree = ctx.mat.node_tree
    n = tree.nodes.get(NUMBER_NODE)
    if n is None:
        n = ctx.nb.node("ShaderNodeValue", "Number decal (set in Finals panel)")
        n.name = NUMBER_NODE
        scene_num = getattr(bpy.context.scene, "ft_number", None) if bpy.context.scene else None
        n.outputs[0].default_value = float(scene_num if scene_num is not None else default)
    return n.outputs[0]


def _number_atlas_vector(ctx, vec, number):
    """Remap decal UVs into a horizontal 0-9 digit atlas. One- and two-digit
    numbers are laid out side by side across the decal."""
    nb = ctx.nb
    num = nb.math("ROUND", _number_value(ctx, number))
    num = nb.math("MINIMUM", nb.math("MAXIMUM", num, 0.0), 99.0)
    ones = nb.math("FLOORED_MODULO", num, 10.0)
    tens = nb.math("FLOOR", nb.math("DIVIDE", num, 10.0))
    two = nb.math("GREATER_THAN", num, 9.5)          # 1 when the number has two digits
    ndig = nb.add(two, 1.0)
    x, y, _z = nb.separate(vec, label="Decal UV")
    xs = nb.mul(x, ndig)
    cell = nb.math("FLOOR", xs)
    local = nb.sub(xs, cell)
    # first cell of a two-digit number shows the tens, otherwise the ones
    first = nb.mul(two, nb.math("LESS_THAN", cell, 0.5))
    digit = nb.add(ones, nb.mul(first, nb.sub(tens, ones)))
    ax = nb.mul(nb.add(local, digit), 0.1)
    # outside the decal square → push off the atlas so CLIP hides it
    inside = nb.mul(nb.mul(nb.math("GREATER_THAN", x, 0.0), nb.math("LESS_THAN", x, 1.0)),
                    nb.mul(nb.math("GREATER_THAN", y, 0.0), nb.math("LESS_THAN", y, 1.0)))
    ax = nb.add(nb.mul(ax, inside), nb.mul(nb.sub(1.0, inside), -1.0))
    return nb.combine(ax, y, 0.0, label="Number atlas lookup")


def _global_decal(ctx, color):
    """New-gen (8Layers) decal: one decal placed by DecalSize/PositionU/V.

    DecalSize is how many decals fit across the UV map (1 = covers it all,
    e.g. full-UV livery textures). Centre = UV middle + (PositionU, PositionV),
    the same convention as old-gen placement; checked on the BikerLoosePants
    AlfaActa skin (slogan upright along the red side stripe). DecalTint blends
    from the decal's own colours (0) to DecalColorA/B (1).
    """
    p = ctx.p
    path = p.t("Decal")
    if not path or asset_name(path).lower() in defaults.NULL_TEXTURES:
        return color
    size = p.s("DecalSize", 1.0) or 1.0
    width = p.s("DecalWidth", 1.0) or 1.0
    vec = _decal_vector(ctx, 0.5 + p.s("DecalPositionU"), 0.5 + p.s("DecalPositionV"),
                        size, size * width, p.s("DecalRotation"))
    number = p.s("DecalNumber", 32.0) if p.s("UseDecalNumber") else None
    ctx.log.info(f"  Decal {asset_name(path)}")
    return _apply_decal(ctx, color, path, vec, p.v("DecalColorA"), p.v("DecalColorB", (0, 0, 0, 1)),
                        recolor=p.s("DecalTint", 1.0), number=number)


def _old_gen_decal(ctx, k, color):
    """Old-gen decal slot k (also used by attachment and team-colour shaders).

    k_DecalPlacement: R, G = offset of the centre from the UV middle (U flipped),
    B = size as a fraction of the UV map, A = rotation (turns).
    k_ColorOverride: 1 = recolour with k_ColorA/B, 0 = the decal's own colours.
    k_LayerMask: bit N-1 limits the decal to layer N (0 or 255 = all layers).
    k_UseNumber / k_Number: show one number from a digit atlas.
    """
    p, nb = ctx.p, ctx.nb
    path = p.t(f"{k}_DecalColor")
    if not path or asset_name(path).lower() in defaults.NULL_TEXTURES:
        return color
    u, v, size, rot = p.v(f"{k}_DecalPlacement", (0, 0, 1, 0))
    size = size if abs(size) > 1e-6 else 1.0
    # Centre = (0.5 - R, 0.5 + G): U is flipped, V is not. Checked on the
    # CombatPants Envy "N" badge and MajorsEventPass "TGM2025" decal, which
    # both land on the front left hip pocket like in the store images (the
    # mirrored glove UVs could not tell the two directions apart).
    vec = _decal_vector(ctx, 0.5 - u, 0.5 + v, 1.0 / size, p.s(f"{k}_WidthRatio", 1.0) / size, rot)
    coverage = 1.0
    bits = int(round(p.s(f"{k}_LayerMask", 0.0)))
    masks = getattr(ctx, "layer_masks", None) or {}
    if 0 < bits < 255 and len(masks) > 1:
        coverage = 0.0
        for layer, m in masks.items():
            if bits & (1 << (layer - 1)):
                coverage = nb.add(coverage, m) if _is_socket(coverage) or _is_socket(m) else coverage + m
    number = p.s(f"{k}_Number", 8.0) if p.s(f"{k}_UseNumber") else None
    ctx.log.info(f"  Decal slot {k}: {asset_name(path)}")
    return _apply_decal(ctx, color, path, vec, p.v(f"{k}_ColorA", (1, 1, 1, 1)),
                        p.v(f"{k}_ColorB", (0, 0, 0, 1)), recolor=p.s(f"{k}_ColorOverride", 0.0),
                        label=f"Decal {k}", number=number, coverage=coverage)


# ─────────────────────────────────────────────────────────────────────────────
# Other masters
# ─────────────────────────────────────────────────────────────────────────────

def build_preview(ctx):
    """M_Character_Preview: placeholder look used before a skin is chosen."""
    p, nb = ctx.p, ctx.nb
    ocm = ctx.tex(p.t("OcclusionCurvatureMaterialID"), data=True, packed=True, label="OCM")
    bsdf = ctx.bsdf()
    color = (0.6, 0.6, 0.6, 1.0)
    if ocm is not None:
        ao, _c, _i = nb.separate(ocm.outputs["Color"])
        color = nb.mix(color, ao, 1.0, "MULTIPLY")
        if p.blend_mode == "BLEND_Masked":
            nb.feed(bsdf.inputs["Alpha"], nb.math("GREATER_THAN", ocm.outputs["Alpha"], p.opacity_clip or 0.3333))
    nb.feed(bsdf.inputs["Base Color"], color)
    nb.feed(bsdf.inputs["Roughness"], 0.7)
    n = ctx.tex(p.t("Normal"), data=True, label="Normal")
    if n is not None:
        nb.feed(bsdf.inputs["Normal"], ctx.normal_map(n.outputs["Color"]))
    ctx.finish(two_sided=p.two_sided if p.two_sided is not None else False)


def _lace_fade(ctx):
    p, nb = ctx.p, ctx.nb
    fade = nb.group(groups.lace_fade(), label="Lace fade (along UV V)")
    _u, v, _w = nb.separate(ctx.uv())
    nb.feed(fade.inputs["Gradient"], v)
    nb.feed(fade.inputs["Fade Start"], p.s("FadeStart"))
    nb.feed(fade.inputs["Min Transparency"], p.s("MinTransparency"))
    nb.feed(fade.inputs["Offset Gradient"], p.s("OffsetGradient"))
    nb.feed(fade.inputs["Transparency"], p.s("Transparency", 1.0))
    return fade.outputs[0]


def build_lace(ctx):
    """M_Lace: sheer fabric. NOM texture: B = embroidery, A = trim colour mask."""
    p, nb = ctx.p, ctx.nb
    bsdf = ctx.bsdf()
    nom = ctx.tex(p.t("NOM"), data=True, packed=True, label="NOM (R/G relief, B embroidery, A trim)")
    color = _col(p.v("Color"))
    opacity = p.s("OverallOpacity")
    if nom is not None:
        r, g, b = nb.separate(nom.outputs["Color"], label="NOM channels")
        if p.sw("bUseAlphaAsColorMask"):
            color = nb.mix(color, _col(p.v("ColorB", (0.026, 0.026, 0.026, 1))), nom.outputs["Alpha"],
                           label="Trim colour (NOM alpha)")
        opacity = nb.lerp(opacity, 1.0, b, clamp=True)
        rg = nb.group(groups.normal_from_rg(), label="R/G relief (off by default)")
        nb.feed(rg.inputs["X"], r)
        nb.feed(rg.inputs["Y"], g)
        nb.feed(rg.inputs["Strength"], 0.0)
        bump = nb.node("ShaderNodeBump", "Embroidery bump")
        nb.feed(bump.inputs["Strength"], 0.3)
        nb.feed(bump.inputs["Height"], b)
        nb.feed(bump.inputs["Normal"], ctx.normal_map(rg.outputs[0]))
        nb.feed(bsdf.inputs["Normal"], bump.outputs[0])
    nb.feed(bsdf.inputs["Base Color"], color)
    nb.feed(bsdf.inputs["Roughness"], p.s("Roughness", 1.0))
    nb.feed(bsdf.inputs["Alpha"], nb.mul(opacity, _lace_fade(ctx), clamp=True))
    ctx.finish(two_sided=p.two_sided if p.two_sided is not None else True)


def build_lace_skirt(ctx):
    """M_LaceSkirt: tinted see-through layer with a gradient fade, no textures."""
    p, nb = ctx.p, ctx.nb
    bsdf = ctx.bsdf()
    nb.feed(bsdf.inputs["Base Color"], _col(p.v("ColorTint")))
    nb.feed(bsdf.inputs["Roughness"], 0.5)
    nb.feed(bsdf.inputs["Alpha"], _lace_fade(ctx))
    ctx.finish(two_sided=p.two_sided if p.two_sided is not None else True)


def build_feather(ctx):
    """M_Feather (e.g. Ballerina wings).

    Colour texture is channel-packed: R (= B) is a greyscale feather albedo,
    G is an extra mask whose use is unknown without M_Feather.json, A is the
    feather cut-out. Colour = greyscale x TintColor (#3D3D3D = the black
    default). Roughness is a guess until the master is exported.
    """
    p, nb, log = ctx.p, ctx.nb, ctx.log
    bsdf = ctx.bsdf()
    tint = _col(p.v("TintColor", (1, 1, 1, 1)))
    tex = ctx.tex(p.t("Color"), data=False, packed=True, label="Color (R grey, G mask?, A cut-out)")
    if tex is not None:
        grey, _g, _b = nb.separate(tex.outputs["Color"], label="Feather channels")
        color = nb.mix(nb.combine(grey, grey, grey), tint, 1.0, "MULTIPLY", label="TintColor")
        clip = p.opacity_clip if p.opacity_clip else 0.3333
        if p.blend_mode in ("", "BLEND_Masked", "BLEND_Translucent"):
            nb.feed(bsdf.inputs["Alpha"], nb.math("GREATER_THAN", tex.outputs["Alpha"], clip,
                                                  label=f"Opacity clip {clip:g}"))
    else:
        color = tint
    nb.feed(bsdf.inputs["Base Color"], color)
    n = ctx.tex(p.t("Normals") or p.t("Normal"), data=True, label="Normals")
    if n is not None:
        nb.feed(bsdf.inputs["Normal"], ctx.normal_map(n.outputs["Color"]))
    nb.feed(bsdf.inputs["Roughness"], max(0.0, min(1.0, p.s("Roughness", 0.55))))
    if p.master != "M_Feather":
        log.info("  M_Feather.json not found: roughness and the colour texture's green channel are guesses.")
    ctx.finish(two_sided=p.two_sided if p.two_sided is not None else True)


def build_attachment(ctx):
    """M_CharacterAttachment family (charms, glasses frames, masks, badges).

    CR  = colour (RGB) + roughness (A), roughness scaled by "Roughness".
    NOH = normal (RG) + ambient occlusion (B) + metal mask (A, used when
          ActivateAlphaMetallic is on), metallic scaled by "Metallic".
    """
    p, nb, log = ctx.p, ctx.nb, ctx.log
    bsdf = ctx.bsdf()
    cr = ctx.tex(p.t("CR"), data=False, packed=True, label="CR (colour, A roughness)")
    color = cr.outputs["Color"] if cr is not None else _col(p.v("ColorTint", (0.5, 0.5, 0.5, 1)))
    if p.sw("UseColorTint"):
        color = nb.mix(color, _col(p.v("ColorTint")), 1.0, "MULTIPLY", label="ColorTint")
    if p.sw("ActivateColorMask") and p.t("ColorMask"):
        cm = ctx.tex(p.t("ColorMask"), data=True, label="ColorMask")
        if cm is not None:
            r, g, _b = nb.separate(cm.outputs["Color"], label="ColorMask channels")
            color = nb.mix(color, _col(p.v("ColorTint")), r, "MULTIPLY", label="ColorTint (mask R)")
            color = nb.mix(color, _col(p.v("SecondaryColorTint")), g, "MULTIPLY",
                           label="SecondaryColorTint (mask G)")
            log.info("  Colour-mask tinting is approximate for this shader.")
    noh = ctx.tex(p.t("NOH") or p.t("NOM"), data=True, packed=True, label="NOH (normal RG, AO B, metal A)")
    metallic = 0.0
    if noh is not None:
        normal, ao = _packed_normal(ctx, noh, p.s("NormalIntensity", 1.0))
        nb.feed(bsdf.inputs["Normal"], normal)
        if p.s("UseAmbientOcclusion", 1.0):
            ao = nb.math("POWER", ao, p.s("AOPower", 1.0))
            color = nb.mix(color, ao, 1.0, "MULTIPLY", label="Ambient Occlusion")
        if p.sw("ActivateAlphaMetallic") or p.s("ActivateAlphaMetallic_s"):
            metallic = nb.mul(noh.outputs["Alpha"], p.s("Metallic", 1.0), clamp=True)
    for k in range(1, 9):
        if p.sw(f"{k}_UseDecal"):
            color = _old_gen_decal(ctx, k, color)
    nb.feed(bsdf.inputs["Base Color"], color)
    nb.feed(bsdf.inputs["Metallic"], metallic)
    rough = p.s("Roughness", 1.0)
    nb.feed(bsdf.inputs["Roughness"],
            nb.mul(cr.outputs["Alpha"], rough, clamp=True) if cr is not None else rough)
    nb.feed(bsdf.inputs["Specular IOR Level"], p.s("Specular", 0.5))
    if p.sw("ActivateEmissive") and p.t("Emissive"):
        em = ctx.tex(p.t("Emissive"), data=False, label="Emissive")
        if em is not None:
            nb.feed(bsdf.inputs["Emission Color"],
                    nb.mix(em.outputs["Color"], _col(p.v("EmissiveColor", (1, 1, 1, 1))), 1.0, "MULTIPLY"))
            nb.feed(bsdf.inputs["Emission Strength"], p.s("EmissiveMultiply", 1.0))
    if p.sw("ActivateOpacity"):
        log.info("  This attachment uses an opacity mask that isn't rebuilt yet.")
    ctx.finish(two_sided=bool(p.two_sided))


def build_glass(ctx):
    """M_CharacterGlass_01/02 and visors: tinted see-through glossy surface.

    Colour is ColorTint, or a gradient texture sampled across the UVs when
    bUseGradientMap is on (e.g. the red/blue 3D paper glasses). Opacity is
    "Opacity", raised toward the edges by FresnelOpacityStrength.
    """
    p, nb = ctx.p, ctx.nb
    bsdf = ctx.bsdf()
    color = _col(p.v("ColorTint", (0.39, 0.42, 0.4, 1)))
    if p.sw("bUseGradientMap") and p.t("ColorGradient"):
        u, v, _w = nb.separate(ctx.uv(), label="UV")
        along = u if p.sw("bUseHorizontalGradient") else v
        vec = nb.combine(along, 0.5, 0.0, label="Gradient lookup")
        g = ctx.tex(p.t("ColorGradient"), data=False, vector=vec, extension="EXTEND", label="ColorGradient")
        if g is not None:
            color = nb.mix(g.outputs["Color"], color, 1.0, "MULTIPLY", label="ColorTint")
    nb.feed(bsdf.inputs["Base Color"], color)
    nb.feed(bsdf.inputs["Roughness"], max(0.0, min(1.0, p.s("Roughness", 0.0))))
    nb.feed(bsdf.inputs["Specular IOR Level"], max(0.0, min(1.0, p.s("Specular", 1.0))))
    lw = nb.node("ShaderNodeLayerWeight", "Edge (fresnel)")
    lw.inputs["Blend"].default_value = 0.5
    edge = nb.mul(lw.outputs["Facing"], p.s("FresnelOpacityStrength", 0.5))
    # UE "grey transmittance" lets the background through by (1 - opacity)
    # without tinting it, which is ordinary alpha blending.
    nb.feed(bsdf.inputs["Alpha"], nb.add(p.s("Opacity", 0.2), edge, clamp=True))
    ctx.finish(two_sided=True, blended=True)


def build_hologram(ctx):
    """M_Character_HolographicMaterial: base colour (texture or BaseColor),
    metallic, with a rainbow tint that shifts toward the edges."""
    p, nb = ctx.p, ctx.nb
    bsdf = ctx.bsdf()
    color = _col(p.v("BaseColor", (0.84, 0.95, 0.92, 1)))
    if p.sw("UseBaseColorTexture") and p.t("BaseColorTexture"):
        t = ctx.tex(p.t("BaseColorTexture"), data=False, packed=True, label="BaseColorTexture")
        if t is not None:
            color = t.outputs["Color"]
    lw = nb.node("ShaderNodeLayerWeight", "Rainbow fresnel")
    lw.inputs["Blend"].default_value = 0.5
    ramp = nb.node("ShaderNodeValToRGB", "Rainbow colours")
    els = ramp.color_ramp.elements
    els[0].position, els[0].color = 0.0, _col(p.v("RainbowColorA", (1, 0, 0, 1)))
    els[1].position, els[1].color = 1.0, _col(p.v("RainbowColorC", (0, 0, 1, 1)))
    mid = els.new(0.5)
    mid.color = _col(p.v("RainbowColorB", (0, 1, 0, 1)))
    nb.feed(ramp.inputs["Fac"], nb.add(lw.outputs["Facing"], p.s("Rainbow Fresnel Offset", 0.5)))
    rainbow = ramp.outputs["Color"]
    desat = max(0.0, min(1.0, p.s("Desaturate", 0.0)))
    if desat:
        hsv = nb.node("ShaderNodeHueSaturation")
        nb.feed(hsv.inputs["Color"], rainbow)
        nb.feed(hsv.inputs["Saturation"], 1.0 - desat)
        rainbow = hsv.outputs[0]
    amount = nb.mul(lw.outputs["Fresnel"], 0.5 * p.s("Rainbow Fresnel Scale", 1.0), clamp=True)
    color = nb.mix(color, rainbow, amount, "OVERLAY", label="Rainbow tint")
    nb.feed(bsdf.inputs["Base Color"], color)
    nb.feed(bsdf.inputs["Metallic"], max(0.0, min(1.0, p.s("Metallic", 0.5))))
    nb.feed(bsdf.inputs["Roughness"], max(0.0, min(1.0, p.s("Roughness", 0.3))))
    nom = ctx.tex(p.t("NOM"), data=True, packed=True, label="NOM (normal RG, AO B)")
    if nom is not None:
        normal, _ao = _packed_normal(ctx, nom)
        nb.feed(bsdf.inputs["Normal"], normal)
    ctx.finish(two_sided=bool(p.two_sided))


def build_plastic_sheet(ctx):
    """M_Character_PlasticSheet (+ NoContactShadow): coloured plastic whose
    colour shifts with viewing angle (RainbowColorA/B/C) and blends to the
    *Top colours along the mesh height (LowestPoint..HighestPoint).
    Its blend mode is Masked, so it renders opaque."""
    p, nb = ctx.p, ctx.nb
    bsdf = ctx.bsdf()
    bottom, _ = _fresnel_ramp(ctx, p.v("RainbowColorA", (1, 0, 0, 1)), p.v("RainbowColorB", (0, 1, 0, 1)),
                              p.v("RainbowColorC", (0, 0, 1, 1)), "Colours (bottom)")
    top, _ = _fresnel_ramp(ctx, p.v("RainbowColorTopA", (1, 0, 0, 1)), p.v("RainbowColorTopB", (0, 1, 0, 1)),
                           p.v("RainbowColorTopC", (0, 0, 1, 1)), "Colours (top)")
    tc = nb.node("ShaderNodeTexCoord")
    _x, _y, z = nb.separate(tc.outputs["Object"], label="Object position")
    lo, hi = p.s("LowestPoint", 0.0), p.s("HighestPoint", 1.0)
    height = nb.map_range(z, lo, hi if abs(hi - lo) > 1e-4 else lo + 1.0)
    color = nb.mix(bottom, top, height, label="Bottom → top")
    nb.feed(bsdf.inputs["Base Color"], color)
    nb.feed(bsdf.inputs["Roughness"], max(0.0, min(1.0, p.s("Roughness", 0.2))))
    nb.feed(bsdf.inputs["Metallic"], max(0.0, min(1.0, p.s("Metallic", 0.0))))
    if p.t("NOM"):
        normal, _ao = _normal_tex(ctx, p.t("NOM"), "NOM")
        if normal is not None:
            nb.feed(bsdf.inputs["Normal"], normal)
    ctx.finish(two_sided=p.two_sided if p.two_sided is not None else True)


def build_team_color(ctx):
    """M_CharacterLayered_TeamColor_01: ColorMask R/G/B pick ColorA/B/C, each
    with its own roughness/metallic/specular. Channels with UseTeamColorX on
    take the team colour instead. Store images show the enemy colour, so that
    is the default; set the "Team" value node to 1 for the friendly colour."""
    p, nb, log = ctx.p, ctx.nb, ctx.log
    bsdf = ctx.bsdf()
    cm = ctx.tex(p.t("ColorMask"), data=True, label="ColorMask")
    if cm is not None:
        mr, mg, mb = nb.separate(cm.outputs["Color"], label="ColorMask channels")
    else:
        mr, mg, mb = 1.0, 0.0, 0.0
    if p.s("UseAutomaticTeamColor", 1.0):
        team = _col(p.v("TeamColor", (0.28, 0.61, 1, 1)))
    else:
        team_pick = nb.value(0.0, label="Team (0 enemy = store look, 1 friendly)")
        team = nb.mix(_col(p.v("EnemyTeamColorOverride", (1, 0.14, 0, 1))),
                      _col(p.v("FriendlyTeamColorOverride", (0, 0.65, 1, 1))), team_pick, label="Team colour")
    cols, rough, metal, spec = [], [], [], []
    for ch in "ABC":
        c = team if p.s(f"UseTeamColor{ch}", 1.0) else _col(p.v(f"Color{ch}", (0.4, 0.4, 0.4, 1)))
        cols.append(c)
        rough.append(p.s(f"Roughness{ch}", 0.5))
        metal.append(p.s(f"Metallic{ch}", 0.0))
        spec.append(p.s(f"Specular{ch}", 0.5))
    color = nb.mix(nb.mix(cols[0], cols[1], mg), cols[2], mb, label="ColorMask → colours")

    def by_mask(vals):
        return nb.lerp(nb.lerp(vals[0], vals[1], mg), vals[2], mb)

    noh = ctx.tex(p.t("NOH"), data=True, packed=True, label="NOH (normal RG, AO B)")
    if noh is not None:
        normal, ao = _packed_normal(ctx, noh)
        if not asset_name(p.t("NOH")).lower().endswith(("_normal", "_n")):
            color = nb.mix(color, ao, 1.0, "MULTIPLY", label="Ambient Occlusion")
        else:
            normal = ctx.normal_map(noh.outputs["Color"])
        nb.feed(bsdf.inputs["Normal"], normal)
    for k in range(1, 9):
        if p.sw(f"{k}_UseDecal"):
            color = _old_gen_decal(ctx, k, color)
    nb.feed(bsdf.inputs["Base Color"], color)
    nb.feed(bsdf.inputs["Roughness"], by_mask(rough))
    nb.feed(bsdf.inputs["Metallic"], by_mask(metal))
    nb.feed(bsdf.inputs["Specular IOR Level"], by_mask(spec))
    ctx.finish(two_sided=bool(p.two_sided))
