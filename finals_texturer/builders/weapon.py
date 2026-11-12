"""Weapon shaders (Weapons tab).

M_Weapon_Base_5Layers, read from its parameters (2026-11-11, Sword_02 and
AssaultRifleLight_01 skins vs store images):
* Layer 0 is the base and covers everything. Layers 1..4 exist when their
  static switch k_UseBlend is on (MI_Weapon_Base = 1 layer, _2Layers = 2, …)
  and paint where the ID texture ("Masks") channel picked by k_Mask (an RGBA
  selector) is set; k_MaskBlack > 0.5 paints where the ID texture is empty.
* Each layer: an index texture (k_IndexMap, greyscale variation, tiled
  k_IndexTiling) picks between k_Color_A / B / C (index 0 / 0.5 / 1), or
  samples k_GradientMap when k_UseGradientTexture is on; roughness the same
  way from k_Roughness_A / B / C. k_Metalness, k_Clearcoat per layer.
  Edge wear (NWO A) brightens the index when k_AddEdgeWearToIndex(B) is on.
* NWO: normal in R/G, occlusion in B, edge wear in A.
* Graphic (UseWeaponGraphicOrGradient): a colour picture ("Graphic", when
  UseGraphic) or the greyscale pattern ("Grayscale") coloured through the
  "Gradient" atlas row 'Gradient Y Sample Index', on UV channel 2 x zw + xy
  of GraphicUVs; with UseMaterialMask it only covers the blend layers.
Not built: detail normals, dirt / grunge, numbers, emissive graphics.
"""

from .. import defaults
from .common import _col, _packed_normal

NULL_NAMES = {"defaulttexture", "baseflattengrayscalemap", "baseflattenlinearcolor", "t_50percentgray"}


def _tex_ok(p, name):
    path = p.t(name)
    if not path:
        return ""
    base = path.split("/")[-1].split(".")[0].lower()
    if base in NULL_NAMES or base in defaults.NULL_TEXTURES:
        return ""
    return path


def _lerp3(nb, a, b, c, t, label):
    """a at t=0, b at 0.5, c at 1 (colours or values)."""
    lo = nb.map_range(t, 0.0, 0.5, 0.0, 1.0, label=f"{label} 0..0.5")
    hi = nb.map_range(t, 0.5, 1.0, 0.0, 1.0, label=f"{label} 0.5..1")
    if isinstance(a, (int, float)):
        return nb.lerp(nb.lerp(a, b, lo), c, hi)
    return nb.mix(nb.mix(a, b, lo, label=f"{label} A→B"), c, hi, label=f"{label} →C")


def _layer(ctx, k, edge):
    """(colour, roughness, metalness, clearcoat) sockets/values of layer k."""
    p, nb = ctx.p, ctx.nb
    s = lambda n, d: p.s(f"{k}_{n}", d)
    idx_path = _tex_ok(p, f"{k}_IndexMap")
    tiling = s("IndexTiling", 1.0) or 1.0
    if idx_path:
        it = ctx.tex(idx_path, data=True, scale=tiling, label=f"Layer {k} index")
        index = ctx.red(it) if it is not None else 0.5
    else:
        index = 0.5
    if s("AddEdgeWearToIndex(B)", 0.0) > 0.5 and edge is not None:
        index = nb.add(index, nb.mul(edge, s("IndexEdgeWearInfluence", 1.0)), clamp=True)
    color = None
    grad_path = _tex_ok(p, f"{k}_GradientMap")
    if s("UseGradientTexture", 0.0) > 0.5 and grad_path:
        color, _ga = ctx.gradient(grad_path, p.s("Gradient Y Sample Position", 0.5), index,
                                  label=f"Layer {k} gradient")
    if color is None:
        color = _lerp3(nb, _col(p.v(f"{k}_Color_A", (0, 0, 0, 1))), _col(p.v(f"{k}_Color_B", (0.5, 0.5, 0.5, 1))),
                       _col(p.v(f"{k}_Color_C", (1, 1, 1, 1))), index, f"Layer {k} colour")
    dark = s("IndexDarkening", 0.0)
    if dark > 0.0:
        color = nb.mix(color, (0, 0, 0, 1), nb.mul(nb.math("SUBTRACT", 1.0, index), dark), label=f"Layer {k} darkening")
    rough = _lerp3(nb, s("Roughness_A", 0.5), s("Roughness_B", 0.5), s("Roughness_C", 0.5), index, f"Layer {k} roughness")
    metal = s("Metalness", 0.0)
    imetal = s("IndexMetalness", 0.0)
    if imetal > 0.0:
        step = nb.math("GREATER_THAN", index, s("IndexMetalnessThreshold", 0.75))
        metal = nb.math("MAXIMUM", metal, nb.mul(step, imetal), label=f"Layer {k} metalness")
    return color, rough, metal, s("Clearcoat", 0.0)


def build_weapon(ctx, fx=False):
    p, nb, log = ctx.p, ctx.nb, ctx.log
    bsdf = ctx.bsdf()
    nwo_path = _tex_ok(p, "NWO")
    nwo = ctx.tex(nwo_path, data=True, packed=True, label="NWO (normal RG, occlusion B, wear A)") if nwo_path else None
    edge = nwo.outputs["Alpha"] if nwo is not None else None
    ids_path = _tex_ok(p, "Masks")
    ids = ctx.tex(ids_path, data=True, packed=True, label="ID masks (layers 1-4)") if ids_path else None

    if fx and not _tex_ok(p, "0_IndexMap") and not p.has_any("0_Color_A", "0_Roughness_A"):
        color, rough, metal, coat = _fx_base(ctx)
    else:
        color, rough, metal, coat = _layer(ctx, 0, edge)
    blend_masks = None
    for k in range(1, 5):
        if not p.sw(f"{k}_UseBlend"):
            continue
        if p.s(f"{k}_MaskBlack", 0.0) > 0.5 and ids is not None:
            r, g, b = nb.separate(ids.outputs["Color"])
            any_id = nb.math("MAXIMUM", nb.math("MAXIMUM", r, g), nb.math("MAXIMUM", b, ids.outputs["Alpha"]))
            mask = nb.math("SUBTRACT", 1.0, any_id, label=f"Layer {k} mask (empty ID)")
        elif ids is not None:
            sel = p.v(f"{k}_Mask", (1, 0, 0, 0))
            rgb = nb.vmath("DOT_PRODUCT", ids.outputs["Color"], tuple(sel[:3]), label=f"Layer {k} mask")
            mask = nb.add(rgb, nb.mul(ids.outputs["Alpha"], sel[3]), clamp=True) if len(sel) > 3 and sel[3] else rgb
        else:
            continue
        c, r, m, cc = _layer(ctx, k, edge)
        color = nb.mix(color, c, mask, label=f"Layer {k}")
        rough = nb.lerp(rough, r, mask)
        metal = nb.lerp(metal, m, mask)
        coat = nb.lerp(coat, cc, mask)
        blend_masks = mask if blend_masks is None else nb.add(blend_masks, mask, clamp=True)

    # Graphic: a full-colour picture ("Graphic", UseGraphic) or a greyscale
    # pattern ("Grayscale") coloured through a gradient atlas row. Placed on the
    # second UV channel (uniform ~15 units/m on weapons) as UV x zw + xy of
    # GraphicUVs: checked on Ambient Aloha's halftone dots (~8 across the blade)
    # and Striped Grazer's zebra vs their store images.
    use_pic = p.sw("UseGraphic") and _tex_ok(p, "Graphic")
    if p.sw("UseWeaponGraphicOrGradient") and (use_pic or _tex_ok(p, "Grayscale")):
        uvs = p.v("GraphicUVs", (0, 0, 1, 1))
        vec = nb.vmath("ADD", nb.vmath("MULTIPLY", ctx.uv(detail=True), (uvs[2] or 1.0, uvs[3] or 1.0, 1.0)),
                       (uvs[0], uvs[1], 0.0), label="GraphicUVs (UV2 x zw + xy)")
        gcol = None
        if use_pic:
            pic = ctx.tex(_tex_ok(p, "Graphic"), data=False, vector=vec, label="Graphic (colour)")
            if pic is not None:
                gcol = pic.outputs["Color"]
        else:
            gray = ctx.tex(_tex_ok(p, "Grayscale"), data=True, vector=vec, label="Graphic (greyscale)")
            if gray is not None:
                g = ctx.red(gray)
                grad_path = _tex_ok(p, "Gradient")
                if grad_path:
                    row = p.s("Gradient Y Sample Index", 0.0)
                    gcol, _ga = ctx.gradient(grad_path, 0.0, g, label=f"Graphic colours (row {row:g})", row=row)
                if gcol is None:
                    gcol = nb.combine(g, g, g)
        if gcol is not None:
            cover = blend_masks if (p.sw("UseMaterialMask") and blend_masks is not None) else 1.0
            color = nb.mix(color, gcol, cover, label="Graphic")
    if nwo is not None:
        _r, _g, ao = nb.separate(nwo.outputs["Color"])
        color = nb.mix(color, (0, 0, 0, 1), nb.mul(nb.math("SUBTRACT", 1.0, ao), 0.6), label="Occlusion (NWO B)")
        nb.feed(bsdf.inputs["Normal"], _packed_normal(ctx, nwo)[0])
    if fx:
        color = _fx_extras(ctx, bsdf, color)
    nb.feed(bsdf.inputs["Base Color"], color)
    nb.feed(bsdf.inputs["Roughness"], rough)
    nb.feed(bsdf.inputs["Metallic"], metal)
    nb.feed(bsdf.inputs["Coat Weight"], coat)
    nb.feed(bsdf.inputs["Coat Roughness"], max(0.0, min(1.0, p.s("ClearcoatRoughness", 0.2))))
    ctx.finish(two_sided=bool(p.two_sided))


def build_weapon_preset(ctx):
    """M_WeaponPreset_CR+NOM (skins with their own model: ski pole, baseball
    bats, katanas, cane…): CR = colour RGB x ColorMultiply + roughness A;
    NOM = normal RG, blue = occlusion (UseBlueAsAO) or opacity mask
    (UseBlueAsOpacityMask), metalness A. With "Use NEM" the second texture is
    Normal / Emissive / Metal: blue glows in Emissive Color A. Tiled uTiling / vTiling."""
    p, nb = ctx.p, ctx.nb
    bsdf = ctx.bsdf()
    tiling = nb.vmath("MULTIPLY", ctx.uv(), (p.s("uTiling", 1.0) or 1.0, p.s("vTiling", 1.0) or 1.0, 1.0),
                      label="uTiling / vTiling")
    cr = ctx.tex(_tex_ok(p, "CR"), data=False, packed=True, vector=tiling, label="CR (colour, roughness A)")
    nom = ctx.tex(_tex_ok(p, "NOM"), data=True, packed=True, vector=tiling, label="NOM (normal RG, B, metal A)")
    color = cr.outputs["Color"] if cr is not None else (0.5, 0.5, 0.5, 1.0)
    color = nb.mix(color, _col(p.v("ColorMultiply", (1, 1, 1, 1))), 1.0, "MULTIPLY", label="ColorMultiply")
    if cr is not None:
        nb.feed(bsdf.inputs["Roughness"], cr.outputs["Alpha"])
    if nom is not None:
        normal, blue = _packed_normal(ctx, nom)
        nb.feed(bsdf.inputs["Normal"], normal)
        nb.feed(bsdf.inputs["Metallic"], nom.outputs["Alpha"])
        if p.sw("UseBlueAsOpacityMask"):
            nb.feed(bsdf.inputs["Alpha"], nb.mul(blue, p.s("Opacity", 1.0), clamp=True))
        elif p.sw("Use NEM"):
            # NEM: blue is an emissive mask, not occlusion (as AO it blacked
            # out the Ceremonial Saber / Plasma Katana skins)
            em = _col(p.v("Emissive Color A", (1, 1, 1, 1)))
            nb.feed(bsdf.inputs["Emission Color"], nb.mix((0, 0, 0, 1), em, blue, label="Emissive (NEM blue)"))
            nb.feed(bsdf.inputs["Emission Strength"], max(p.s("Emissive Intensity", 1.0), 0.0))
        elif p.sw("UseBlueAsAO", True):
            color = nb.mix(color, nb.combine(blue, blue, blue), 1.0, "MULTIPLY", label="Occlusion (NOM B)")
    nb.feed(bsdf.inputs["Base Color"], color)
    ctx.finish(two_sided=bool(p.two_sided))


def build_weapon_text(ctx):
    """M_WeaponText (logos and lettering decals on weapons, e.g. the sword's
    "THE FINALS" logo): opacity = AlphaTexture red (UV tiling / offset),
    colour TextColor (or ColorTexture x ColorTextureTint when
    UseColorTexture), NX normal (embossed), Metallic, Roughness."""
    p, nb = ctx.p, ctx.nb
    bsdf = ctx.bsdf()
    uv = nb.vmath("ADD", nb.vmath("MULTIPLY", ctx.uv(), (p.s("UV Tiling X", 1.0) or 1.0, p.s("UV Tiling Y", 1.0) or 1.0, 1.0)),
                  (p.s("UV Offset X", 0.0), p.s("UV Offset Y", 0.0), 0.0), label="UV tiling / offset")
    color = _col(p.v("TextColor", (1, 1, 1, 1)))
    alpha_tex = ctx.tex(_tex_ok(p, "AlphaTexture"), data=True, vector=uv, label="AlphaTexture (R = coverage)")
    if p.sw("UseColorTexture") and _tex_ok(p, "ColorTexture"):
        ct = ctx.tex(_tex_ok(p, "ColorTexture"), data=False, vector=uv, label="ColorTexture")
        if ct is not None:
            color = nb.mix(ct.outputs["Color"], _col(p.v("ColorTextureTint", (1, 1, 1, 1))), 1.0, "MULTIPLY")
            if p.sw("UseAlphaFromColorTexture"):
                nb.feed(bsdf.inputs["Alpha"], ct.outputs["Alpha"])
                alpha_tex = None
    if alpha_tex is not None:
        nb.feed(bsdf.inputs["Alpha"], ctx.red(alpha_tex))
    nb.feed(bsdf.inputs["Base Color"], color)
    nb.feed(bsdf.inputs["Metallic"], max(0.0, min(1.0, p.s("Metallic", 0.0))))
    nb.feed(bsdf.inputs["Roughness"], max(0.0, min(1.0, p.s("Roughness", 0.5))))
    if p.sw("Use NX Texture") and _tex_ok(p, "NX"):
        nx = ctx.tex(_tex_ok(p, "NX"), data=True, packed=True, vector=uv, label="NX (normal RG)")
        if nx is not None:
            nb.feed(bsdf.inputs["Normal"], _packed_normal(ctx, nx)[0])
    ctx.finish(two_sided=True)


# ─────────────────────────────────────────────────────────────────────────────
# Weapon effect shaders (approximate)
# ─────────────────────────────────────────────────────────────────────────────

def _fx_base(ctx):
    """Base of effect shaders without the layer system: BaseColorA / B (or
    Color1 / 2) picked by the index texture."""
    p, nb = ctx.p, ctx.nb
    if p.has_any("BaseColorA", "BaseColorB"):
        a, b = p.v("BaseColorA", (0.1, 0.1, 0.1, 1)), p.v("BaseColorB", (0.3, 0.3, 0.3, 1))
    else:
        a, b = p.v("Color1", (0.1, 0.1, 0.1, 1)), p.v("Color2", (0.3, 0.3, 0.3, 1))
    idx_path = _tex_ok(p, "IndexMap") or _tex_ok(p, "OffsetMask")
    index = 0.5
    if idx_path:
        it = ctx.tex(idx_path, data=True, label="Index")
        if it is not None:
            index = ctx.red(it)
    color = nb.mix(_col(a), _col(b), index, label="BaseColorA / B")
    return color, p.s("Roughness", 0.35), p.s("Metalness", p.s("Metallic", 0.5)), p.s("Clearcoat", 0.0)


def _fx_extras(ctx, bsdf, color):
    """Rainbow fresnel colours (RainbowColorA..C), lava / glow colours as
    emission. A stand-in for the animated effect shaders."""
    from .common import _fresnel_ramp
    p, nb = ctx.p, ctx.nb
    if p.has_any("RainbowColorA") and p.has_any("RainbowColorC"):
        ramp, _lw = _fresnel_ramp(ctx, p.v("RainbowColorA", (1, 0, 0, 1)), p.v("RainbowColorB", (0, 1, 0, 1)),
                                  p.v("RainbowColorC", (0, 0, 1, 1)), "Rainbow (fresnel)")
        color = nb.mix(color, ramp, 0.6 if p.sw("Use Color Falloff") else 0.35, label="Rainbow colours")
    for name in ("Lava Color A", "EmissiveColor", "GlitchColor", "GridColor", "TintColor"):
        if name in p.vectors:
            em = _col(p.v(name, (0, 0, 0, 1)))
            if max(em[:3]) > 0.0:
                nb.feed(bsdf.inputs["Emission Color"], em)
                nb.feed(bsdf.inputs["Emission Strength"], 0.5)
                break
    return color


def build_weapon_fx(ctx):
    """Weapon effect shaders without a dedicated builder (diamond, league /
    legendary fresnel, progression, panning fresnel, lava, hologram, colour
    cycle, LED…), approximated so they aren't white: packed CR / NOM textures
    use the preset builder; materials with the weapon layer system get it
    (layers, graphic); others BaseColorA / B over their index texture. Rainbow
    fresnel colours and glow colours are added. The animated parts (panning,
    flicker, refraction, cubemap reflections) aren't built."""
    p, nb = ctx.p, ctx.nb
    if (p.blend_mode or "").startswith("BLEND_Translucent"):
        # translucent effect shells (e.g. the glitch force field around the
        # BaseballBat_02 skin): in game a faint shimmer over the weapon;
        # opaque they hid it (the blue bat rendered pink)
        bsdf = ctx.bsdf()
        tint = None
        for name in ("GlitchColor", "EmissiveColor", "Color1", "BaseColorA"):
            if name in p.vectors:
                tint = _col(p.v(name, (1, 1, 1, 1)))
                break
        nb.feed(bsdf.inputs["Base Color"], tint or (1, 1, 1, 1))
        nb.feed(bsdf.inputs["Roughness"], 0.2)
        nb.feed(bsdf.inputs["Alpha"], max(0.0, min(1.0, p.s("Opacity", 0.08))))
        ctx.finish(two_sided=True, blended=True)
        return None
    if _tex_ok(p, "CR") and _tex_ok(p, "NOM"):
        return build_weapon_preset(ctx)
    build_weapon(ctx, fx=True)
