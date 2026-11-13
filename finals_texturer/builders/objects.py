"""Game objects (Objects tab): Cashout objectives, death statues, arena carryables.

Rebuilt from the game's compiled shaders (2026-11-13, dev_tools/shader_extract). Damage (dents, scuffs,
scratches), weather (wetness, puddles), thermal vision, selection outlines, view-distance glints and the
cinematic dissolve are runtime effects at 0 by default and left out.

Shared pieces:
* Overlay tint (cashbox, cashout station): c ≥ 0.5 ? 1 − 2(1 − c)(1 − tint) : 2·c·tint (Blender's
  Overlay mix with the texture as A).
* Icon / label decal atlas (barrels, cashout station): UV channel 2 (static meshes pass it unswapped:
  the vertex shader writes (u1, v1); skinned clothing meshes store (v1, u1)), × (UTile, VTile) +
  (UOffset, VOffset), divided into Columns × Rows cells, cell Position (column = Position mod Columns, row = Position div
  Columns, rows from the top); colour × DecalMultiply desaturated by DecalColorDesaturation, laid over
  by its alpha; metal → DecalMetallic, roughness overlay-blended with RoughnessBranding inside it.
"""

from .common import _col, _packed_normal
from ..ue_assets import asset_name

# Textures some masters use directly (not as parameters)
METAL_BARE_ROUGH = "/Game/Discovery/Textures/Tileables/Metal/MetalBareRough_01_CR.MetalBareRough_01_CR"
PLASTIC_POLYMER_N = "/Game/Discovery/Textures/Detail/T_Plastic_Polymer_01_N.T_Plastic_Polymer_01_N"


TEAM_NODE = "FT Team Color"


def _t(p, name):
    path = p.t(name)
    if not path:
        return ""
    base = asset_name(path).lower()
    # T_Default_* are real (grey / flat) textures the game samples (the barricade tarp's Color is
    # T_Default_01_C x ColorMultiply 2.2); only true placeholders are skipped
    if base.startswith(("t_null", "defaulttexture")):
        return ""
    return path


# flat textures the shared loader treats as "no texture" (clothing uses them as null slots), but which
# object masters sample as real values: (linear colour, alpha)
FLAT_TEXTURES = {"t_default_01_c": ((0.2195, 0.2159, 0.2195, 1.0), 1.0)}


def _color_tex(ctx, path, **kw):
    """(colour, alpha) of a colour texture: image node outputs, a flat texture's constant, or None."""
    n = ctx.tex(path, **kw) if path else None
    if n is not None:
        return n.outputs["Color"], n.outputs["Alpha"]
    flat = FLAT_TEXTURES.get(asset_name(path).lower()) if path else None
    return flat if flat else (None, None)


def _overlay(nb, base, blend, label=None):
    return nb.mix(base, blend, 1.0, "OVERLAY", label=label)


def _overlay_value(nb, base, blend, label=None):
    """Scalar overlay (roughness): same formula on a grey colour, red channel back."""
    c = _overlay(nb, nb.combine(base, base, base), nb.combine(blend, blend, blend), label=label)
    return nb.separate(c)[0]


def _atlas_decal(ctx, path):
    """Decal atlas cell on UV channel 2 (see module docstring). Returns the image node or None."""
    p, nb = ctx.p, ctx.nb
    cols = max(p.s("Columns", 1.0), 1.0)
    rows = max(p.s("Rows", 1.0), 1.0)
    pos = int(round(p.s("Position", 0.0)))
    col, row = pos % int(cols), pos // int(cols)
    uv = ctx.uv(detail=True)
    x, y, _z = nb.separate(uv, label="Decal UV (2nd channel)")
    # UE texture space (v down): u' = (u·UT + UO + col) / C, v' = (v·VT + VO + row) / R
    in_x, in_y = x, nb.sub(1.0, y)
    u2 = nb.math("DIVIDE", nb.add(nb.math("MULTIPLY_ADD", in_x, p.s("UTile", 1.0), p.s("UOffset", 0.0)), col), cols)
    v2 = nb.math("DIVIDE", nb.add(nb.math("MULTIPLY_ADD", in_y, p.s("VTile", 1.0), p.s("VOffset", 0.0)), row), rows)
    vec = nb.combine(u2, nb.sub(1.0, v2), 0.0, label="Atlas cell")
    return ctx.tex(path, data=False, packed=True, vector=vec, label=f"Decal atlas (cell {pos})")


def _apply_decal(ctx, color, rough, metal, decal, alpha=None):
    """Lay a decal over colour / roughness / metal. Returns (colour, roughness, metal, alpha)."""
    p, nb = ctx.p, ctx.nb
    a = alpha if alpha is not None else decal.outputs["Alpha"]
    dc = nb.mix(decal.outputs["Color"], (p.s("DecalMultiply", 1.0),) * 3 + (1.0,), 1.0, "MULTIPLY",
                label="DecalMultiply")
    desat = p.s("DecalColorDesaturation", 0.0)
    if desat:
        grey = nb.vmath("DOT_PRODUCT", dc, (0.212639, 0.715169, 0.072192))
        dc = nb.mix(dc, nb.combine(grey, grey, grey), desat, label="DecalColorDesaturation")
    color = nb.mix(color, dc, a, label="Decal", clamp_result=True)
    rb = p.s("RoughnessBranding", 1.0)
    rough = nb.lerp(rough, _overlay_value(nb, rough, rb, label="RoughnessBranding"), a)
    metal = nb.lerp(metal, p.s("DecalMetallic", 0.0), a)
    return color, rough, metal, a


def _sparkle_metal(ctx, metal):
    """+ sum of UVSparkleMetalMasks (a constant the masters add to metal; 0.25 on the cashout station)."""
    s = sum(ctx.p.v("UVSparkleMetalMasks", (0.0, 0.0, 0.0, 0.0))[:4])
    return ctx.nb.math("ADD", metal, s, clamp=True) if s else metal


def _rg_normal_vec(nb, tex):
    """Tangent-space normal (x, y, z) from a packed texture's R/G (z rebuilt)."""
    r, g, _b = nb.separate(tex.outputs["Color"])
    x = nb.math("MULTIPLY_ADD", r, 2.0, -1.0)
    y = nb.math("MULTIPLY_ADD", g, 2.0, -1.0)
    z = nb.math("SQRT", nb.math("MAXIMUM", nb.sub(1.0, nb.add(nb.mul(x, x), nb.mul(y, y))), 0.0))
    return x, y, z


def _detail_normal(ctx, base_tex, detail_tex, intensity, base_strength=1.0):
    """Game formula: n = normalize(base.xy·s + detail.xy·intensity, base.z) (z from the base only)."""
    nb = ctx.nb
    x, y, z = _rg_normal_vec(nb, base_tex)
    if base_strength != 1.0:
        x, y = nb.mul(x, base_strength), nb.mul(y, base_strength)
    if detail_tex is not None and intensity:
        dr, dg, _db = nb.separate(detail_tex.outputs["Color"])
        x = nb.math("MULTIPLY_ADD", nb.math("MULTIPLY_ADD", dr, 2.0, -1.0), intensity, x)
        y = nb.math("MULTIPLY_ADD", nb.math("MULTIPLY_ADD", dg, 2.0, -1.0), intensity, y)
    n = nb.vmath("NORMALIZE", nb.combine(x, y, z))
    color = nb.vmath("MULTIPLY_ADD", n, (0.5, 0.5, 0.5), label="Normal → colour")
    nb.feed(color.node.inputs[2], (0.5, 0.5, 0.5))
    return ctx.normal_map(color)


def build_atk_canister(ctx):
    """M_ATK_Canister_01 (arena barrels and canisters). CR = colour + roughness A; NOH = normal RG,
    B = two-tone mask, A = metallic. Colour = lerp(2·CR·SecondaryColorTint, 2·CR·ColorTint, B); glows
    EmissiveMultiply × B × colour. Icon decal (atlas, see module docstring) with its own glow:
    decal × lerp(1, decal alpha, DecalEmissiveAlpha) × DecalEmissiveMultiplier × DecalEmissiveTint
    (ActivateDecalBaseColorEmissive)."""
    p, nb = ctx.p, ctx.nb
    bsdf = ctx.bsdf()
    cr = ctx.tex(_t(p, "CR"), data=False, packed=True, label="CR (colour, roughness A)")
    noh = ctx.tex(_t(p, "NOH") or _t(p, "NXM"), data=True, packed=True, label="NOH (normal RG, mask B, metal A)")
    crc = cr.outputs["Color"] if cr is not None else (1.0, 1.0, 1.0, 1.0)
    tint = _col(p.v("ColorTint", (0.5, 0.5, 0.5, 1.0)))
    sec = _col(p.v("SecondaryColorTint", (0.5, 0.5, 0.5, 1.0)))
    a_col = nb.mix(nb.mix(crc, sec, 1.0, "MULTIPLY"), (2.0, 2.0, 2.0, 1.0), 1.0, "MULTIPLY", label="2·CR·SecondaryColorTint")
    b_col = nb.mix(nb.mix(crc, tint, 1.0, "MULTIPLY"), (2.0, 2.0, 2.0, 1.0), 1.0, "MULTIPLY", label="2·CR·ColorTint")
    if noh is not None:
        normal, mask = _packed_normal(ctx, noh)
        nb.feed(bsdf.inputs["Normal"], normal)
        metal = noh.outputs["Alpha"]
    else:
        mask, metal = 1.0, 0.0
    color = nb.mix(a_col, b_col, mask, label="Two-tone (NOH B)")
    rough = cr.outputs["Alpha"] if cr is not None else 0.5
    emission = None
    em = p.s("EmissiveMultiply", 0.0)
    if em:
        emission = nb.mix(color, (em,) * 3 + (1.0,), 1.0, "MULTIPLY")
        emission = nb.mix((0, 0, 0, 1), emission, mask, label="EmissiveMultiply × B")
    dpath = _t(p, "DecalCR")
    decal = _atlas_decal(ctx, dpath) if dpath else None
    if decal is not None:
        color, rough, metal, a = _apply_decal(ctx, color, rough, metal, decal)
        if p.sw("ActivateDecalBaseColorEmissive"):
            k = nb.lerp(1.0, a, p.s("DecalEmissiveAlpha", 1.0))
            de = nb.mix(decal.outputs["Color"], nb.combine(k, k, k), 1.0, "MULTIPLY")
            mult = p.s("DecalEmissiveMultiplier", 1.0)
            dt = p.v("DecalEmissiveTint", (1, 1, 1, 1))
            de = nb.mix(de, (dt[0] * mult, dt[1] * mult, dt[2] * mult, 1.0), 1.0, "MULTIPLY", label="Decal glow")
            emission = de if emission is None else nb.mix(emission, de, 1.0, "ADD")
    nb.feed(bsdf.inputs["Base Color"], color)
    nb.feed(bsdf.inputs["Roughness"], rough)
    nb.feed(bsdf.inputs["Metallic"], _sparkle_metal(ctx, metal))
    if emission is not None:
        nb.feed(bsdf.inputs["Emission Color"], emission)
        nb.feed(bsdf.inputs["Emission Strength"], 1.0)
    ctx.finish()


def build_cashout_prop(ctx):
    """M_Cashout_CashBox_01 (cashbox) and M_Cashout_TransferPoint_01 (cashout station, vault body).
    CR = colour + roughness A, overlay-tinted by ColorTint; NOM / NMH = normal RG, metal A.
    Cashbox: metal × Metallic, roughness × Roughness, normal × NormalStrenght.
    Station: tiled detail normal (U/V Tile, DetailNormalIntensity) and detail roughness overlay
    (DetailRoughness, DetailRoughnessIntensity; detail A, or B with ActivateBlueChannelRoughness_s);
    label decal atlas (ActivateDecal), only where UV channel 2's v (game space) is below 1."""
    p, nb = ctx.p, ctx.nb
    station = "transferpoint" in (p.master or "").lower()
    bsdf = ctx.bsdf()
    cr = ctx.tex(_t(p, "CR"), data=False, packed=True, label="CR (colour, roughness A)")
    nm = ctx.tex(_t(p, "NOM") or _t(p, "NMH"), data=True, packed=True, label="Normal RG, metal A")
    crc = cr.outputs["Color"] if cr is not None else (0.5, 0.5, 0.5, 1.0)
    color = crc
    if p.sw("UseColorTint", True):
        color = _overlay(nb, crc, _col(p.v("ColorTint", (0.5, 0.5, 0.5, 1.0))), label="Overlay ColorTint")
    rough = cr.outputs["Alpha"] if cr is not None else 0.5
    metal = nm.outputs["Alpha"] if nm is not None else 0.0
    if not station:
        metal = nb.mul(metal, p.s("Metallic", 1.0))
        rough = nb.math("MULTIPLY", rough, p.s("Roughness", 1.0), clamp=True)
        if nm is not None:
            nb.feed(bsdf.inputs["Normal"], _detail_normal(ctx, nm, None, 0.0, p.s("NormalStrenght", 1.0)))
    else:
        detail = None
        if p.sw("ActivateDetailMap", True) and _t(p, "DetailNormal"):
            uvsrc = ctx.uv(detail=bool(p.s("UseUV1", 0.0)))
            dvec = nb.vmath("ADD", nb.vmath("MULTIPLY", uvsrc, (p.s("U Tile", 1.0), p.s("V Tile", 1.0), 1.0)),
                            (p.s("DetailNormalOffsetU", 0.0), -p.s("DetailNormalOffsetV", 0.0), 0.0),
                            label="Detail tiling")
            detail = ctx.tex(_t(p, "DetailNormal"), data=True, packed=True, vector=dvec, label="Detail (normal RG, roughness A)")
        if nm is not None:
            inten = p.s("DetailNormalIntensity", 0.0) if p.sw("ActivateDetailNormal", True) else 0.0
            nb.feed(bsdf.inputs["Normal"], _detail_normal(ctx, nm, detail, inten))
        if detail is not None and p.sw("ActivateDetailRoughness", True):
            _r, _g, db = nb.separate(detail.outputs["Color"])
            d = db if p.s("ActivateBlueChannelRoughness_s", 0.0) else detail.outputs["Alpha"]
            if p.s("InvertRoughness_s", 0.0):
                d = nb.sub(1.0, d)
            ov = _overlay_value(nb, rough, nb.mul(d, p.s("DetailRoughness", 0.5)), label="Detail roughness")
            rough = nb.lerp(rough, ov, p.s("DetailRoughnessIntensity", 0.0))
        if p.sw("ActivateDecal", False) and _t(p, "DecalCR"):
            decal = _atlas_decal(ctx, _t(p, "DecalCR"))
            if decal is not None:
                a = decal.outputs["Alpha"]
                if p.sw("Mask out UVs below 0.0 on Y axis", False):
                    # 1 − sat(trunc(v)) of UV channel 2 in game space: shown where game v < 1
                    _x, y, _z = nb.separate(ctx.uv(detail=True))
                    a = nb.mul(a, nb.math("GREATER_THAN", y, 0.0))
                color, rough, metal, _a = _apply_decal(ctx, color, rough, metal, decal, alpha=a)
                rough = nb.math("ADD", rough, 0.0, clamp=True)
    nb.feed(bsdf.inputs["Base Color"], color)
    nb.feed(bsdf.inputs["Roughness"], rough)
    nb.feed(bsdf.inputs["Metallic"], _sparkle_metal(ctx, metal))
    ctx.finish()


def build_progress_light(ctx):
    """M_CashBoxEmissiveProgress (cashbox light strip) / M_VaultEmissiveProgress (vault strip):
    colour BackgroundColor; cashbox metal 1, roughness lerp(1, MetalBareRough A, Roughness);
    vault metal 0.5, roughness 0.125 + (A − 0.125)/4, normal from N. Glows EmissiveColor × 7 where
    UV x < Progress (cashbox UV 1, vault UV 2), dimmed while Flashing to LowFlashMultiplier (cashbox:
    lerp(LowFlashMultiplier, 1, Intensity)); defaults: cashbox Progress 1 (dim glow), vault 0 (dark)."""
    p, nb = ctx.p, ctx.nb
    vault = "vault" in (p.master or "").lower()
    bsdf = ctx.bsdf()
    bg = p.v("BackgroundColor", (0.5, 0.5, 0.5, 1.0))
    nb.feed(bsdf.inputs["Base Color"], tuple(max(0.0, min(1.0, c)) for c in bg[:3]) + (1.0,))
    mb = ctx.tex(METAL_BARE_ROUGH, data=True, packed=True, label="MetalBareRough (roughness A)")
    a = mb.outputs["Alpha"] if mb is not None else 0.5
    if vault:
        nb.feed(bsdf.inputs["Metallic"], 0.5)
        nb.feed(bsdf.inputs["Roughness"], nb.math("MULTIPLY_ADD", nb.sub(a, 0.125), 0.25, 0.125, clamp=True))
        n = ctx.tex(_t(p, "N"), data=True, packed=True, label="N (normal RG)")
        if n is not None:
            nb.feed(bsdf.inputs["Normal"], _packed_normal(ctx, n)[0])
    else:
        nb.feed(bsdf.inputs["Metallic"], 1.0)
        nb.feed(bsdf.inputs["Roughness"], nb.lerp(1.0, a, p.s("Roughness", 0.1), clamp=True))
    progress = p.s("Progress", 0.0)
    flash = 1.0
    if p.s("Flashing", 0.0):
        low = p.s("LowFlashMultiplier", 0.1)
        k = low + (1.0 - low) * (p.s("Intensity", 0.0) if not vault else 0.5)
        flash = 1.0 + p.s("Flashing", 1.0) * (k - 1.0)
    e = p.v("EmissiveColor", (1, 1, 1, 1))
    if progress > 0.0 and flash > 0.0:
        x, _y, _z = nb.separate(ctx.uv(detail=vault))
        on = nb.math("LESS_THAN", x, progress)
        nb.feed(bsdf.inputs["Emission Color"], _col(e))
        nb.feed(bsdf.inputs["Emission Strength"], nb.mul(on, 7.0 * flash))
    ctx.finish()


def build_character_totem(ctx):
    """M_CharacterTotem (death statues). NRX/NRC: normal RG, roughness B, colour mask A; colour =
    sat(2 × A × Color) (Color is per-statue primitive data: the team colour; the MI holds the
    default, blue; the Objects tab's Team colour sets the "FT Team Color" node); plastic detail normal (T_Plastic_Polymer_01_N, UV × DetailNormalScale,
    DetailNormalIntensity). Not metallic."""
    p, nb = ctx.p, ctx.nb
    bsdf = ctx.bsdf()
    t = ctx.tex(_t(p, "NRX") or _t(p, "NRC"), data=True, packed=True, label="NRC (normal RG, roughness B, mask A)")
    c = p.v("Color", (0.5, 0.5, 0.5, 1.0))
    team = nb.node("ShaderNodeRGB", "Team Color")
    team.name = TEAM_NODE
    team.outputs[0].default_value = _col(c)
    if t is not None:
        _r, _g, b = nb.separate(t.outputs["Color"])
        k = nb.mul(t.outputs["Alpha"], 2.0)
        color = nb.mix(nb.combine(k, k, k), team.outputs[0], 1.0, "MULTIPLY", clamp_result=True)
        nb.feed(bsdf.inputs["Base Color"], color)
        nb.feed(bsdf.inputs["Roughness"], b)
        det = ctx.tex(PLASTIC_POLYMER_N, data=True, scale=p.s("DetailNormalScale", 3.0), label="Plastic detail normal")
        nb.feed(bsdf.inputs["Normal"], _detail_normal(ctx, t, det, p.s("DetailNormalIntensity", 0.5)))
    else:
        nb.feed(bsdf.inputs["Base Color"], team.outputs[0])
    nb.feed(bsdf.inputs["Metallic"], 0.0)
    ctx.finish()


def build_object_glass(ctx):
    """M_Glass_01 (glass on props, e.g. the glitch barrel's window). A translucent forward-shaded
    material (no G-buffer pass to read), built from its parameters: tint ColorTint, Roughness,
    opacity from Opacity_Center (facing) to Opacity_Edge (grazing) × OpacityGlobal."""
    p, nb = ctx.p, ctx.nb
    bsdf = ctx.bsdf()
    nb.feed(bsdf.inputs["Base Color"], _col(p.v("ColorTint", (1, 1, 1, 1))))
    nb.feed(bsdf.inputs["Roughness"], max(0.0, min(1.0, p.s("Roughness", 0.05))))
    nb.feed(bsdf.inputs["Metallic"], 0.0)
    lw = nb.node("ShaderNodeLayerWeight")
    nb.feed(lw.inputs["Blend"], 0.5)
    edge = lw.outputs["Facing"]
    op = nb.lerp(p.s("Opacity_Center", 0.1), p.s("Opacity_Edge", 0.5), edge)
    nb.feed(bsdf.inputs["Alpha"], nb.math("MULTIPLY", op, p.s("OpacityGlobal", 1.0), clamp=True))
    n = ctx.tex(_t(p, "NormalMap"), data=True, packed=True, label="Normal")
    if n is not None:
        nb.feed(bsdf.inputs["Normal"], _packed_normal(ctx, n, p.s("NormalIntensity", 1.0))[0])
    ctx.finish(blended=True)


# ── Phase 2: arena kit (jump pads, ziplines, ladders, dispenser, buttons), props ──────────────────────

SPONSOR_TABLE = "/Game/Discovery/Textures/Sponsors/T_SponsorColorTable.T_SponsorColorTable"
SPONSOR_NODE = "FT Sponsor Color"
SPONSOR_COUNT = 13          # columns of T_SponsorColorTable (row 1 primary, row 2 secondary)
GRUNGE_BREAKUP = "/Game/Discovery/Textures/Tileables/Grunges/T_GrungeGeneric_02_M.T_GrungeGeneric_02_M"
LIGHT_BULB = "/Game/Discovery/Lighting/Materials/Lightfixtures/T_Lightbulb_01_C.T_Lightbulb_01_C"


def sponsor_vector(index, secondary=False):
    """Texel centre of a sponsor colour in T_SponsorColorTable (Blender UV, v up)."""
    return ((index + 0.5) / SPONSOR_COUNT, 0.25 if secondary else 0.75, 0.0)


def _desat(nb, color, amount):
    if not amount:
        return color
    g = nb.vmath("DOT_PRODUCT", color, (0.212639, 0.715169, 0.072192))
    return nb.mix(color, nb.combine(g, g, g), amount, label="Desaturation")


def _vec_to_normal(ctx, x, y, z):
    nb = ctx.nb
    n = nb.vmath("NORMALIZE", nb.combine(x, y, z))
    colr = nb.vmath("MULTIPLY_ADD", n, (0.5, 0.5, 0.5))
    nb.feed(colr.node.inputs[2], (0.5, 0.5, 0.5))
    return ctx.normal_map(colr)


def build_atk_4materials(ctx):
    """M_4MaterialsByUVOffset (arena kit: jump pads, zipline anchors, ladders, dispenser, buttons).
    The part's UV channel 2 tile (floor of u, v in game space; DisableU / DisableV ignore an axis)
    picks one of four looks: (0,0) default, (-1,0), (1,0), (0,-1). Shared base textures
    ("BaseTexture - CR / NOM") on UV 1 x BaseTextureTiling; (0,-1) also has its own CR / NOM.
    Colours: tile Color or the arena's sponsor colour (UseATKColor; T_SponsorColorTable, sponsor index
    from primitive data, default 0 = THE FINALS red), times 2 x desaturated CR x BaseColorMultiply
    ((0,0) by BaseTextureAmount). Metal = Metalness (x NOM A with ActivateMetallicTexture) + sparkle
    masks, roughness = CR A x Roughness. Normal: base NOM RG x tile NormalIntensity (tile (1,0)
    flattens toward 0.5 by it), x NormalStrength, + trim normal (TrimTexture on UV 2) for tiles with
    UseTrimTexture. The tiles' own "UV (x,y) - CR / NOM" textures aren't used by the compiled shader."""
    p, nb = ctx.p, ctx.nb
    bsdf = ctx.bsdf()
    s = p.s
    base_uv = ctx.uv(s("BaseTextureTiling", 1.0))
    cr = ctx.tex(_t(p, "BaseTexture - CR"), data=False, packed=True, vector=base_uv, label="BaseTexture CR")
    nom = ctx.tex(_t(p, "BaseTexture - NOM"), data=True, packed=True, vector=base_uv, label="BaseTexture NOM")
    crc = cr.outputs["Color"] if cr is not None else (0.5, 0.5, 0.5, 1.0)
    cra = cr.outputs["Alpha"] if cr is not None else 0.5
    noma = nom.outputs["Alpha"] if nom is not None else 1.0
    t2 = "UV (0,-1)"
    uv01 = nb.vmath("MULTIPLY", ctx.uv(), (s(f"{t2} Tiling", 1.0) * s(f"{t2} Tiling_U", 1.0),
                                          s(f"{t2} Tiling", 1.0) * s(f"{t2} Tiling_V", 1.0), 1.0), label="UV (0,-1) tiling")
    cr2 = ctx.tex(_t(p, f"{t2} - CR"), data=False, packed=True, vector=uv01, label="UV (0,-1) CR")
    nom2 = ctx.tex(_t(p, f"{t2} NOM"), data=True, packed=True, vector=uv01, label="UV (0,-1) NOM")

    # tile weights from UV channel 2 (game space: v down)
    u, v, _z = nb.separate(ctx.uv(detail=True), label="UV 2")
    fu = nb.math("FLOOR", u)
    fv = nb.math("FLOOR", nb.sub(1.0, v))

    def axis(f, value, disable):
        eq = nb.math("COMPARE", f, float(value), 0.5)
        return nb.add(eq, nb.mul(nb.sub(1.0, eq), disable)) if disable else eq

    du, dv = s("DisableU", 0.0), s("DisableV", 0.0)
    w_m10 = nb.mul(axis(fu, -1, du), axis(fv, 0, dv))
    w_p10 = nb.mul(axis(fu, 1, du), axis(fv, 0, dv))
    w_0m1 = nb.mul(axis(fu, 0, du), axis(fv, -1, dv))
    w_00 = nb.mul(axis(fu, 0, du), axis(fv, 0, dv))

    # sponsor colour
    idx = 0 if s("GetSponsorIndexFromPrimData", 1.0) else int(round(s("SponsorIndexScalar", 0.0)))
    swap = bool(s("SwapPrimaryAndSecondarySponsorColor", 0.0))
    sp_node = ctx.tex(SPONSOR_TABLE, data=False, vector=nb.combine(*sponsor_vector(idx, swap)),
                      label="Sponsor colour", extension="EXTEND")
    if sp_node is not None:
        sp_node.interpolation = "Closest"
        sp_node.name = SPONSOR_NODE
        sponsor = sp_node.outputs["Color"]
    else:
        sponsor = (0.64448, 0.013702, 0.045186, 1.0)

    def tile_color(t):
        return nb.mix(_col(p.v(f"{t} - Color", (0.5, 0.5, 0.5, 1))), sponsor, s(f"{t} - UseATKColor", 0.0),
                      label=f"{t} colour")

    def tex_term(t, c, source):
        d = _desat(nb, source, s(f"{t} - ColorDesaturation", 0.0))
        d = nb.mix(d, (s(f"{t} BaseColorMultiply", 1.0),) * 3 + (1.0,), 1.0, "MULTIPLY")
        return nb.mix(nb.mix(c, d, 1.0, "MULTIPLY"), (2.0, 2.0, 2.0, 1.0), 1.0, "MULTIPLY", label=f"{t} 2·c·tex")

    c00 = tile_color("UV (0,0)")
    col = nb.mix(c00, tex_term("UV (0,0)", c00, crc), s("UV (0,0) - BaseTextureAmount", 0.0), label="UV (0,0)")
    for t, w in (("UV (-1,0)", w_m10), ("UV (1,0)", w_p10)):
        col = nb.mix(col, tex_term(t, tile_color(t), crc), w, label=t)
    c01 = tile_color(t2)
    src2 = nb.mix(cr2.outputs["Color"], crc, s(f"{t2} - UseBaseTexture", 1.0)) if cr2 is not None else crc
    k = s(f"{t2} - BaseTextureAmount", 1.0) * s(f"{t2} - UseCRTexture", 1.0)
    col = nb.mix(col, nb.mix(c01, tex_term(t2, c01, src2), k), w_0m1, label=t2)
    nb.feed(bsdf.inputs["Base Color"], nb.mix(col, (0, 0, 0, 1), 0.0, clamp_result=True))

    # metal / roughness
    def metal(t, a):
        m = s(f"{t} - Metalness", 0.0)
        return nb.lerp(m, nb.mul(a, m), s(f"{t} - ActivateMetallicTexture", 0.0))
    met = metal("UV (0,0)", noma)
    rough = nb.mul(cra, s("UV (0,0) - Roughness", 1.0))
    for t, w in (("UV (-1,0)", w_m10), ("UV (1,0)", w_p10)):
        met = nb.lerp(met, metal(t, noma), w)
        rough = nb.lerp(rough, nb.mul(cra, s(f"{t} - Roughness", 1.0)), w)
    a2 = nb.lerp(nom2.outputs["Alpha"], noma, s(f"{t2} - UseBaseTexture", 1.0)) if nom2 is not None else noma
    met = nb.lerp(met, metal(t2, a2), w_0m1)
    r2 = nb.lerp(cr2.outputs["Alpha"], cra, s(f"{t2} - UseBaseTexture", 1.0)) if cr2 is not None else cra
    rr = s(f"{t2} - Roughness", 1.0)
    rough = nb.lerp(rough, nb.lerp(rr, nb.mul(r2, rr), s(f"{t2} - UseCRTexture", 1.0)), w_0m1)
    sm = p.v("UVSparkleMetalMasks", (0, 0, 0, 0))
    if any(sm[:3]):
        met = nb.add(met, nb.add(nb.add(nb.mul(w_00, sm[0]), nb.mul(w_m10, sm[1])), nb.mul(w_p10, sm[2])))
    nb.feed(bsdf.inputs["Metallic"], nb.math("ADD", met, 0.0, clamp=True))
    nb.feed(bsdf.inputs["Roughness"], nb.math("ADD", rough, 0.0, clamp=True))

    # normal
    if nom is not None:
        r, g, _b = nb.separate(nom.outputs["Color"])
        ni = nb.lerp(s("UV (0,0) NormalIntensity", 1.0), s("UV (-1,0) NormalIntensity", 1.0), w_m10)
        nx = nb.math("MULTIPLY_ADD", nb.sub(r, 0.5), ni, 0.5)
        ny = nb.math("MULTIPLY_ADD", nb.sub(g, 0.5), ni, 0.5)
        n10 = s("UV (1,0) NormalIntensity", 1.0)
        nx = nb.lerp(nx, nb.lerp(r, 0.5, n10), w_p10)
        ny = nb.lerp(ny, nb.lerp(g, 0.5, n10), w_p10)
        if nom2 is not None:
            r2c, g2c, _b2 = nb.separate(nom2.outputs["Color"])
            ub = s(f"{t2} - UseBaseTexture", 1.0)
            k2 = s(f"{t2} NormalIntensity", 1.0) * s(f"{t2} - UseNOMTexture", 0.0)
            x2 = nb.math("MULTIPLY_ADD", nb.sub(nb.lerp(r2c, r, ub), 0.5), k2, 0.5)
            y2 = nb.math("MULTIPLY_ADD", nb.sub(nb.lerp(g2c, g, ub), 0.5), k2, 0.5)
            nx, ny = nb.lerp(nx, x2, w_0m1), nb.lerp(ny, y2, w_0m1)
        x = nb.math("MULTIPLY_ADD", nx, 2.0, -1.0)
        y = nb.math("MULTIPLY_ADD", ny, 2.0, -1.0)
        z = nb.math("SQRT", nb.math("MAXIMUM", nb.sub(1.0, nb.add(nb.mul(x, x), nb.mul(y, y))), 0.0))
        ns = s("NormalStrength", 1.0)
        x, y = nb.mul(x, ns), nb.mul(y, ns)
        trim = _t(p, "TrimTexture")
        tw = nb.add(nb.add(nb.mul(w_00, s("UV (0,0) UseTrimTexture", 0.0)), nb.mul(w_m10, s("UV (-1,0) UseTrimTexture", 0.0))),
                    nb.add(nb.mul(w_p10, s("UV (1,0) UseTrimTexture", 0.0)), nb.mul(w_0m1, s(f"{t2} UseTrimTexture", 0.0))))
        if trim:
            tt = ctx.tex(trim, data=True, packed=True, detail=bool(s("UseUv1TrimTexture", 1.0)), label="Trim normal")
            if tt is not None:
                tr, tg, _tb = nb.separate(tt.outputs["Color"])
                x = nb.math("MULTIPLY_ADD", nb.math("MULTIPLY_ADD", tr, 2.0, -1.0), tw, x)
                y = nb.math("MULTIPLY_ADD", nb.math("MULTIPLY_ADD", tg, 2.0, -1.0), tw, y)
        nb.feed(bsdf.inputs["Normal"], _vec_to_normal(ctx, x, y, z))
    ctx.finish()


def build_decal_branding(ctx):
    """M_Decals_Branding (logos / labels on arena kit: mesh decals, deferred-decal pass): coverage =
    sat(alpha x Mask Opacity with Mask Contrast) - Breakup Intensity x breakup grunge
    (T_GrungeGeneric_02_M B on UV x Breakup Tile, ^Breakup Power x Breakup Multiply, Breakup Contrast);
    colour = overlay(texture, Color), -> UVOffsetColorOverride where the game's v <= 0 (by its alpha);
    roughness Roughness, not metallic. M_Decals_Branding_Projected (ladder arrows) projects through
    per-object scale and takes runtime colours: built the same way with its U/V tile and offset (approx.)."""
    p, nb = ctx.p, ctx.nb
    s = p.s
    bsdf = ctx.bsdf()
    projected = "projected" in (p.master or "").lower()
    vec = None
    if projected:
        vec = nb.vmath("ADD", nb.vmath("MULTIPLY", ctx.uv(), (s("UTile", 1.0), s("VTile", 1.0), 1.0)),
                       (s("UOffset", 0.0), -s("VOffset", 0.0), 0.0), label="UV tile / offset")
    tex = ctx.tex(_t(p, "Color") or _t(p, "Mask"), data=False, packed=True, vector=vec, label="Decal")
    if tex is None:
        nb.feed(bsdf.inputs["Alpha"], 0.0)
        ctx.finish()
        return
    a = tex.outputs["Alpha"] if p.sw("MaskFromColorAlpha", True) else ctx.red(tex)
    if p.sw("Invert Mask", False):
        a = nb.sub(1.0, a)
    mc = s("Mask Contrast", 0.0)
    cov = nb.math("MULTIPLY_ADD", nb.mul(a, s("Mask Opacity", 1.0)), mc + 1.0, -mc, clamp=True)
    if p.sw("Use Breakup Mask", True) and s("Breakup Intensity", 0.0):
        g = ctx.tex(GRUNGE_BREAKUP, data=True, scale=s("Breakup Tile", 1.0), label="Breakup grunge")
        if g is not None:
            gb = nb.separate(g.outputs["Color"])[2]
            bc = s("Breakup Contrast", 0.0)
            pw = nb.math("POWER", nb.math("MAXIMUM", gb, 1e-6), s("Breakup Power", 1.0))
            b = nb.math("MULTIPLY_ADD", nb.mul(pw, s("Breakup Multiply", 1.0)), 2 * bc + 1.0, -bc, clamp=True)
            cov = nb.math("MULTIPLY_ADD", b, -s("Breakup Intensity", 0.0), cov, clamp=True)
    color = _overlay(nb, tex.outputs["Color"], _col(p.v("Color", (1, 1, 1, 1))), label="Overlay Color")
    ov = p.v("UVOffsetColorOverride", (0, 0, 0, 0))
    if not projected and len(ov) > 3 and ov[3]:
        _x, y, _z = nb.separate(ctx.uv())
        above = nb.math("GREATER_THAN", y, 0.99999)       # game v <= 0: the UV tile above
        color = nb.mix(color, _col(ov), nb.mul(above, ov[3]), label="UVOffsetColorOverride")
    nb.feed(bsdf.inputs["Base Color"], color)
    nb.feed(bsdf.inputs["Roughness"], max(0.0, min(1.0, s("Roughness", 0.5))))
    nb.feed(bsdf.inputs["Metallic"], 0.0)
    nb.feed(bsdf.inputs["Alpha"], cov)
    ctx.finish()


def build_atk_button(ctx):
    """M_ATK_Button (arena button face): colour lerp(grey (0.208, 0.201, 0.195), Color, button state);
    glows T_Lightbulb_01_C x Color x 0.5 x lit state. The states are per-object runtime values; shown
    lit (state 1), as a button that can be pressed. Roughness 0 (glossy), not metallic."""
    p, nb = ctx.p, ctx.nb
    bsdf = ctx.bsdf()
    c = _col(p.v("Color", (1, 1, 1, 1)))
    nb.feed(bsdf.inputs["Base Color"], c)
    bulb = ctx.tex(LIGHT_BULB, data=False, label="Light bulb")
    if bulb is not None:
        nb.feed(bsdf.inputs["Emission Color"], nb.mix(bulb.outputs["Color"], c, 1.0, "MULTIPLY"))
        nb.feed(bsdf.inputs["Emission Strength"], 0.5)
    nb.feed(bsdf.inputs["Roughness"], 0.0)          # the compiled shader writes a constant 0
    nb.feed(bsdf.inputs["Metallic"], 0.0)
    ctx.finish()


def build_props01(ctx):
    """M_Props_01 (props: cannon, cones, chairs, bins, pots...). From the compiled shader:
    * Texture set: CR / NOH on UV x Tiling; with bSecondTextureSetOnUVOffset, OffsetCR / NOHOffset on
      UV x TilingForOffsetTexture where dot(UV (game space), UVOffsetChannelWeights.rg) <= UVCutoff.
    * Colour: ((CR - 0.5) x BaseColorTextureStrength + 0.5)^ColorPower x ColorMultiply, desaturated by
      ColorDesaturation, overlay-tinted by ColorTint (UseColorTint); second set: OffsetCR x SecondSetColorTint.
    * Detail (ActivateDetailMap): one texture on lerp(UV 1, UV 2, UseUV1) x (U Tile, V Tile) + offset:
      normal RG x DetailNormalIntensity added; colour overlay (or multiply) with its A (B with
      ActivateBlueChannelColor_s) by DetailColorIntensity; roughness overlay with A (B / inverted per
      switches) x DetailRoughness, by DetailRoughnessIntensity; all three x dot(vertex colour,
      DetailVertexChannel) with ActivateDetailVertexMask.
    * Metal: ActivateMetallicTexture: lerp(NOH B, NOH A, ActivateAlphaMetallic_s) x lerp(Metallic,
      SecondarySetMetalness, second set); otherwise Metalness. Roughness: CR A x lerp(Roughness,
      SecondarySetRoughness, second set). Normal: NOH RG x NormalIntensity.
    Weather (wetness, rain, snow, soot) and damage layers are off by default."""
    p, nb = ctx.p, ctx.nb
    s = p.s
    bsdf = ctx.bsdf()
    uv = ctx.uv(s("Tiling", 1.0))
    crc, cra = _color_tex(ctx, _t(p, "CR"), data=False, packed=True, vector=uv, label="CR")
    noh = ctx.tex(_t(p, "NOH"), data=True, packed=True, vector=uv, label="NOH")
    second = cr2 = noh2 = None
    if p.sw("bSecondTextureSetOnUVOffset", False):
        w = p.v("UVOffsetChannelWeights", (0.0, 0.0, 0.0, 0.0))
        u, v, _z = nb.separate(ctx.uv())
        d = nb.add(nb.mul(u, w[0]), nb.mul(nb.sub(1.0, v), w[1]))
        second = nb.math("LESS_THAN", d, s("UVCutoff", 0.0) + 1e-6, label="Second texture set")
        uv2 = ctx.uv(s("TilingForOffsetTexture", 1.0))
        cr2 = ctx.tex(_t(p, "OffsetCR"), data=False, packed=True, vector=uv2, label="OffsetCR")
        noh2 = ctx.tex(_t(p, "NOHOffset"), data=True, packed=True, vector=uv2, label="NOHOffset")

    def pickf(a, b):
        return a if second is None or b is None else nb.lerp(a, b, second)

    crc = crc if crc is not None else (0.5, 0.5, 0.5, 1.0)
    cra = cra if cra is not None else 0.5
    if cr2 is not None:
        cra = pickf(cra, cr2.outputs["Alpha"])
    # colour
    st = s("BaseColorTextureStrength", 1.0)
    c = nb.vmath("MULTIPLY_ADD", nb.vmath("SUBTRACT", crc, (0.5, 0.5, 0.5)), (st, st, st))
    nb.feed(c.node.inputs[2], (0.5, 0.5, 0.5))
    c = nb.vmath("POWER", nb.vmath("MAXIMUM", c, (0.0, 0.0, 0.0)), (s("ColorPower", 1.0),) * 3)
    c = nb.vmath("SCALE", c, scale=s("ColorMultiply", 1.0))
    c = _desat(nb, c, s("ColorDesaturation", 0.0))
    if p.sw("UseColorTint", False):
        c = _overlay(nb, c, _col(p.v("ColorTint", (0.5, 0.5, 0.5, 1))), label="Overlay ColorTint")
    if cr2 is not None:
        c = nb.mix(c, nb.mix(cr2.outputs["Color"], _col(p.v("SecondSetColorTint", (1, 1, 1, 1))), 1.0, "MULTIPLY"),
                   second, label="Second set colour")
    rk = pickf(s("Roughness", 1.0), s("SecondarySetRoughness", 1.0))
    rough = nb.mul(cra, rk)
    # normal
    nt = None
    if noh is not None:
        nt = noh.outputs["Color"] if noh2 is None or second is None else nb.mix(noh.outputs["Color"], noh2.outputs["Color"], second)
    detail = None
    if p.sw("ActivateDetailMap", False) and _t(p, "DetailNormal"):
        duv = ctx.uv(detail=bool(s("UseUV1", 0.0)))
        dvec = nb.vmath("ADD", nb.vmath("MULTIPLY", duv, (s("U Tile", 1.0), s("V Tile", 1.0), 1.0)),
                        (s("DetailNormalOffsetU", 0.0), -s("DetailNormalOffsetV", 0.0), 0.0), label="Detail tiling")
        detail = ctx.tex(_t(p, "DetailNormal"), data=True, packed=True, vector=dvec, label="Detail")
    vmask = _vertex_mask(ctx) if detail is not None and p.sw("ActivateDetailVertexMask", False) else None
    if nt is not None:
        r, g, _b = nb.separate(nt)
        ni = s("NormalIntensity", 1.0)
        x = nb.mul(nb.math("MULTIPLY_ADD", r, 2.0, -1.0), ni)
        y = nb.mul(nb.math("MULTIPLY_ADD", g, 2.0, -1.0), ni)
        z = nb.math("SQRT", nb.math("MAXIMUM", nb.sub(1.0, nb.add(nb.mul(x, x), nb.mul(y, y))), 0.0))
        if detail is not None and p.sw("ActivateDetailNormal", True):
            dr, dg, _db = nb.separate(detail.outputs["Color"])
            di = s("DetailNormalIntensity", 1.0) if vmask is None else nb.mul(vmask, s("DetailNormalIntensity", 1.0))
            x = nb.math("MULTIPLY_ADD", nb.math("MULTIPLY_ADD", dr, 2.0, -1.0), di, x)
            y = nb.math("MULTIPLY_ADD", nb.math("MULTIPLY_ADD", dg, 2.0, -1.0), di, y)
        nb.feed(bsdf.inputs["Normal"], _vec_to_normal(ctx, x, y, z))
    if detail is not None:
        _dr, _dg, db = nb.separate(detail.outputs["Color"])
        da = detail.outputs["Alpha"]
        if p.sw("ActivateDetailBaseColor", False):
            dc = db if s("ActivateBlueChannelColor_s", 0.0) else da
            if s("Activate_DetailBaseColor_MultiplyInsteadOfOverlay_s", 0.0):
                ov = nb.mix(c, nb.combine(dc, dc, dc), 1.0, "MULTIPLY")
            else:
                ov = _overlay(nb, c, nb.combine(dc, dc, dc), label="Detail colour")
            k = s("DetailColorIntensity", 1.0)
            c = nb.mix(c, ov, k if vmask is None else nb.mul(vmask, k), label="Detail colour intensity",
                       clamp_result=True)
        if p.sw("ActivateDetailRoughness", False):
            drr = db if s("ActivateBlueChannelRoughness_s", 0.0) else da
            if s("InvertRoughness_s", 0.0):
                drr = nb.sub(1.0, drr)
            ovr = _overlay_value(nb, rough, nb.mul(drr, s("DetailRoughness", 1.0)), label="Detail roughness")
            k = s("DetailRoughnessIntensity", 1.0)
            rough = nb.lerp(rough, ovr, k if vmask is None else nb.mul(vmask, k))
    nb.feed(bsdf.inputs["Base Color"], nb.mix(c, (0, 0, 0, 1), 0.0, clamp_result=True))
    nb.feed(bsdf.inputs["Roughness"], nb.math("ADD", rough, 0.0, clamp=True))
    # metal
    if p.sw("ActivateMetallicTexture", False) and nt is not None:
        _r, _g, b = nb.separate(nt)
        a = noh.outputs["Alpha"] if noh2 is None else pickf(noh.outputs["Alpha"], noh2.outputs["Alpha"])
        m = nb.lerp(b, a, s("ActivateAlphaMetallic_s", 0.0))
        k = pickf(s("Metallic", 1.0), s("SecondarySetMetalness", 0.0))
        nb.feed(bsdf.inputs["Metallic"], nb.math("MULTIPLY", m, k, clamp=True))
    else:
        mk = pickf(s("Metalness", 0.0), s("SecondarySetMetalness", 0.0))
        nb.feed(bsdf.inputs["Metallic"], mk if not isinstance(mk, float) else max(0.0, min(1.0, mk)))
    ctx.finish()


def _vertex_color(ctx):
    """The mesh's vertex colour (raw, as imported) as a colour socket."""
    n = ctx.nb.node("ShaderNodeVertexColor", "Vertex colour")
    n.layer_name = "VERTEXCOLOR"
    return n


def _vertex_mask(ctx, channel_param="DetailVertexChannel"):
    """dot(vertex colour, <channel weights>) (r, g, b, a)."""
    nb = ctx.nb
    w = ctx.p.v(channel_param, (1.0, 0.0, 0.0, 0.0))
    vc = _vertex_color(ctx)
    m = nb.vmath("DOT_PRODUCT", vc.outputs["Color"], (w[0], w[1], w[2]))
    if len(w) > 3 and w[3]:
        m = nb.math("MULTIPLY_ADD", vc.outputs["Alpha"], w[3], m)
    return m


def _sponsor_color(ctx):
    """The arena sponsor colour (T_SponsorColorTable, index from primitive data: default 0)."""
    s = ctx.p.s
    idx = 0 if s("GetSponsorIndexFromPrimData", 1.0) else int(round(s("SponsorIndexScalar", 0.0)))
    n = ctx.tex(SPONSOR_TABLE, data=False, label="Sponsor colour", extension="EXTEND",
                vector=ctx.nb.combine(*sponsor_vector(idx, bool(s("SwapPrimaryAndSecondarySponsorColor", 0.0)))))
    if n is None:
        return (0.64448, 0.013702, 0.045186, 1.0)
    n.interpolation = "Closest"
    n.name = SPONSOR_NODE
    return n.outputs["Color"]


def _detail_layer(ctx, color, rough):
    """Shared detail map (DetailNormal texture): returns (detail node or None, colour, roughness).
    Colour overlay / multiply with A (B: ActivateBlueChannelColor_s) by DetailColorIntensity; roughness
    overlay with A (B / inverted) x DetailRoughness by DetailRoughnessIntensity."""
    p, nb = ctx.p, ctx.nb
    s = p.s
    if not _t(p, "DetailNormal"):
        return None, color, rough
    duv = ctx.uv(detail=bool(s("UseUV1", 0.0)))
    dvec = nb.vmath("ADD", nb.vmath("MULTIPLY", duv, (s("U Tile", 1.0), s("V Tile", 1.0), 1.0)),
                    (s("DetailNormalOffsetU", 0.0), -s("DetailNormalOffsetV", 0.0), 0.0), label="Detail tiling")
    det = ctx.tex(_t(p, "DetailNormal"), data=True, packed=True, vector=dvec, label="Detail")
    if det is None:
        return None, color, rough
    _dr, _dg, db = nb.separate(det.outputs["Color"])
    da = det.outputs["Alpha"]
    dc = db if s("ActivateBlueChannelColor_s", 0.0) else da
    if s("Activate_DetailBaseColor_MultiplyInsteadOfOverlay_s", 0.0):
        ov = nb.mix(color, nb.combine(dc, dc, dc), 1.0, "MULTIPLY")
    else:
        ov = _overlay(nb, color, nb.combine(dc, dc, dc), label="Detail colour")
    color = nb.mix(color, ov, s("DetailColorIntensity", 0.0), label="Detail colour intensity", clamp_result=True)
    drr = db if s("ActivateBlueChannelRoughness_s", 0.0) else da
    if s("InvertRoughness_s", 0.0):
        drr = nb.sub(1.0, drr)
    ovr = _overlay_value(nb, rough, nb.mul(drr, s("DetailRoughness", 1.0)), label="Detail roughness")
    rough = nb.lerp(rough, ovr, s("DetailRoughnessIntensity", 0.0))
    return det, color, rough


def _normal_with_detail(ctx, base, det, intensity=1.0):
    """Normal from a packed texture's RG x intensity, plus the detail's RG x DetailNormalIntensity."""
    nb = ctx.nb
    r, g, _b = nb.separate(base)
    x = nb.mul(nb.math("MULTIPLY_ADD", r, 2.0, -1.0), intensity)
    y = nb.mul(nb.math("MULTIPLY_ADD", g, 2.0, -1.0), intensity)
    z = nb.math("SQRT", nb.math("MAXIMUM", nb.sub(1.0, nb.add(nb.mul(x, x), nb.mul(y, y))), 0.0))
    if det is not None:
        dr, dg, _db = nb.separate(det.outputs["Color"])
        di = ctx.p.s("DetailNormalIntensity", 0.0)
        x = nb.math("MULTIPLY_ADD", nb.math("MULTIPLY_ADD", dr, 2.0, -1.0), di, x)
        y = nb.math("MULTIPLY_ADD", nb.math("MULTIPLY_ADD", dg, 2.0, -1.0), di, y)
    return _vec_to_normal(ctx, x, y, z)


def build_props_cloth(ctx):
    """M_Props_Cloth_01 (tarps, awnings, flags on props): Color^ColorPower x ColorMultiply (on UV x Tiling);
    outside the ColorMask (UV 2 with ColorMaskUVSet2, else UV 1; e.g. THE FINALS icon) overlay-blended with the arena sponsor colour (after
    ColorDesaturation), inside it overlay-blended with SecondaryColorTint; then the detail map (colour,
    roughness, normal). Roughness Color A x Roughness, not metallic. The wind-animated fold normal and
    subsurface are left out."""
    p, nb = ctx.p, ctx.nb
    s = p.s
    bsdf = ctx.bsdf()
    uv = ctx.uv(s("Tiling", 1.0))
    cc, ca = _color_tex(ctx, _t(p, "Color"), data=False, packed=True, vector=uv, label="Color")
    noh = ctx.tex(_t(p, "NOH"), data=True, packed=True, vector=uv, label="NOH")
    c = cc if cc is not None else (1.0, 1.0, 1.0, 1.0)
    c = nb.vmath("POWER", nb.vmath("MAXIMUM", c, (0.0, 0.0, 0.0)), (s("ColorPower", 1.0),) * 3)
    c6 = nb.vmath("SCALE", c, scale=s("ColorMultiply", 1.0))
    sec = _overlay(nb, c6, _col(p.v("SecondaryColorTint", (0.5, 0.5, 0.5, 1))), label="SecondaryColorTint")
    spo = _overlay(nb, _desat(nb, c6, s("ColorDesaturation", 0.0)), _sponsor_color(ctx), label="Sponsor colour")
    mt = ctx.tex(_t(p, "ColorMask"), data=True, detail=p.sw("ColorMaskUVSet2", False), label="ColorMask")
    if mt is not None:
        m = nb.mix((1, 1, 1, 1), mt.outputs["Color"], 1.0, "SUBTRACT")
        inner = nb.mix(c6, spo, m, label="Mask")       # per-channel lerp in the shader: grey masks
        color = nb.mix(sec, inner, m, label="Mask")
    else:
        color = spo
    rough = nb.mul(ca if ca is not None else 0.5, s("Roughness", 0.5))
    det, color, rough = _detail_layer(ctx, color, rough)
    nb.feed(bsdf.inputs["Base Color"], nb.mix(color, (0, 0, 0, 1), 0.0, clamp_result=True))
    nb.feed(bsdf.inputs["Roughness"], nb.math("ADD", rough, 0.0, clamp=True))
    nb.feed(bsdf.inputs["Metallic"], 0.0)
    if noh is not None:
        nb.feed(bsdf.inputs["Normal"], _normal_with_detail(ctx, noh.outputs["Color"], det, s("NormalIntensity", 1.0)))
    ctx.finish()


def build_flag(ctx):
    """M_Flag (hanging flags, cloth banners): colour = arena sponsor colour, painted with
    FlagColorForeground by the FlagPattern texture's red (UV x UVTiling + UVOffset, rotated with
    RotateTextures, clamped to the texture unless TileUVs); BackSide Color on back faces
    (UseUniqueBackside); detail colour (multiply / overlay) and roughness from the detail map; the
    pattern is metallic with UseMetallic. Normal: fabric (Normal x NormalTiling, x NormalStrength) +
    detail normal. The wind ripples (animated normals) are left out."""
    p, nb = ctx.p, ctx.nb
    s = p.s
    bsdf = ctx.bsdf()
    u, v, _z = nb.separate(ctx.uv())
    vg = nb.sub(1.0, v)                                    # game space
    rot = s("RotateTextures", 0.0)
    pu = nb.lerp(u, vg, rot) if rot else u
    pv = nb.lerp(vg, nb.mul(u, -1.0), rot) if rot else vg
    pu = nb.math("MULTIPLY_ADD", pu, s("UVTilingX", 1.0), s("UVOffsetX", 0.0))
    pv = nb.math("MULTIPLY_ADD", pv, s("UVTilingY", 1.0), s("UVOffsetY", 0.0))
    tile = s("TileUVs", 0.0)
    cu = nb.math("MINIMUM", nb.math("MAXIMUM", pu, 0.0), 0.9999)
    cv = nb.math("MINIMUM", nb.math("MAXIMUM", pv, 0.0), 0.9999)
    if tile:
        cu = nb.math("MULTIPLY_ADD", nb.sub(pu, cu), tile, cu)
        cv = nb.math("MULTIPLY_ADD", nb.sub(pv, cv), tile, cv)
    pattern = ctx.tex(_t(p, "FlagPattern"), data=True, vector=nb.combine(cu, nb.sub(1.0, cv), 0.0), label="FlagPattern")
    pm = ctx.red(pattern) if pattern is not None else 0.0
    c = nb.mix(_sponsor_color(ctx), _col(p.v("FlagColorForeground", (1, 1, 1, 1))), pm, label="Flag pattern")
    back = None
    if s("UseUniqueBackside", 0.0):
        geo = nb.node("ShaderNodeNewGeometry")
        back = nb.mul(geo.outputs["Backfacing"], s("UseUniqueBackside", 0.0))
        c = nb.mix(c, _col(p.v("BackSide Color", (1, 1, 1, 1))), back, label="Back side")
    det, c, rough = _detail_layer(ctx, c, s("Roughness", 0.5))
    nb.feed(bsdf.inputs["Base Color"], nb.mix(c, (0, 0, 0, 1), 0.0, clamp_result=True))
    nb.feed(bsdf.inputs["Roughness"], nb.math("ADD", rough, 0.0, clamp=True) if not isinstance(rough, float) else rough)
    um = s("UseMetallic", 0.0)
    if um and not isinstance(pm, float):
        m = nb.mul(pm, um)
        if back is not None:
            m = nb.mul(m, nb.sub(1.0, back))
        nb.feed(bsdf.inputs["Metallic"], m)
    else:
        nb.feed(bsdf.inputs["Metallic"], 0.0)
    fab = ctx.tex(_t(p, "Normal"), data=True, packed=True,
                  vector=nb.vmath("MULTIPLY", ctx.uv(), (s("NormalTilingX", 1.0), s("NormalTilingY", 1.0), 1.0)),
                  label="Fabric normal")
    if fab is not None:
        r, g, _b = nb.separate(fab.outputs["Color"])
        ns = s("NormalStrength", 1.0)
        x = nb.mul(nb.math("MULTIPLY_ADD", r, 2.0, -1.0), ns)
        y = nb.mul(nb.math("MULTIPLY_ADD", g, 2.0, -1.0), ns)
        if det is not None:
            dr, dg, _db = nb.separate(det.outputs["Color"])
            di = s("DetailNormalIntensity", 0.0)
            x = nb.math("MULTIPLY_ADD", nb.math("MULTIPLY_ADD", dr, 2.0, -1.0), di, x)
            y = nb.math("MULTIPLY_ADD", nb.math("MULTIPLY_ADD", dg, 2.0, -1.0), di, y)
        nb.feed(bsdf.inputs["Normal"], _vec_to_normal(ctx, x, y, 1.0))
    ctx.finish()


# ── phase 3: long-tail prop masters ─────────────────────────────────────────

def _ue_uv(ctx, tu=1.0, tv=1.0, ou=0.0, ov=0.0, detail=False):
    """UV x tiling + offset in UE texture space (v down), back as a Blender UV vector."""
    nb = ctx.nb
    if (tu, tv, ou, ov) == (1.0, 1.0, 0.0, 0.0):
        return ctx.uv(detail=detail)
    u, v, _z = nb.separate(ctx.uv(detail=detail))
    u2 = nb.math("MULTIPLY_ADD", u, tu, ou)
    v2 = nb.math("MULTIPLY_ADD", nb.sub(1.0, v), tv, ov)
    return nb.combine(u2, nb.sub(1.0, v2), 0.0, label="UV tiling")


def _rg_normal(ctx, color, strength=1.0):
    """Normal from a packed texture's RG (2x - 1) x strength; z from the unscaled RG (UE's order)."""
    nb = ctx.nb
    r, g, _b = nb.separate(color)
    x0 = nb.math("MULTIPLY_ADD", r, 2.0, -1.0)
    y0 = nb.math("MULTIPLY_ADD", g, 2.0, -1.0)
    z = nb.math("SQRT", nb.math("MAXIMUM", nb.sub(1.0, nb.add(nb.mul(x0, x0), nb.mul(y0, y0))), 0.0))
    if strength == 1.0:
        return x0, y0, z
    return nb.mul(x0, strength), nb.mul(y0, strength), z


def _desat_to(nb, c, amount):
    """lerp(c, luminance(c), amount) (UE's Desaturation node)."""
    if isinstance(amount, float) and amount == 0.0:
        return c
    lum = nb.vmath("DOT_PRODUCT", c, (0.212639, 0.715169, 0.072192))
    return nb.mix(c, nb.combine(lum, lum, lum), amount, label="Desaturate")


def _contrast(nb, c, k):
    """(c - 0.5) x k + 0.5"""
    if k == 1.0:
        return c
    out = nb.vmath("MULTIPLY_ADD", nb.vmath("SUBTRACT", c, (0.5, 0.5, 0.5)), (k, k, k))
    nb.feed(out.node.inputs[2], (0.5, 0.5, 0.5))
    return out


def build_vegetation(ctx):
    """M_Vegetation_01 (plants in pots, palm leaves, flower beds), compiled shader:
    c = BaseColor x BaseColorMultiply; leaves (NormalMap B) = 2 x desat(c, BaseColorDesaturation) x
    BaseColourTint, branches keep c: colour = lerp(c, 2 tint desat, B). Roughness = lerp(R_Branches,
    R-Leaves (x BaseColor R with BaseColorRoughness), B). Normal = NormalMap RG. Alpha = BaseColor A, clipped at 0.3333 (masked).
    Wind, wetness, back-face specular / roughness and subsurface are left out."""
    p, nb = ctx.p, ctx.nb
    s = p.s
    bsdf = ctx.bsdf()
    cc, ca = _color_tex(ctx, _t(p, "BaseColor"), data=False, packed=True, label="BaseColor")
    nm = ctx.tex(_t(p, "NormalMap"), data=True, packed=True, label="NormalMap")
    cc = cc if cc is not None else (0.5, 0.5, 0.5, 1.0)
    c = nb.vmath("SCALE", cc, scale=s("BaseColorMultiply", 1.0))
    tint = p.v("BaseColourTint", (0.5, 0.5, 0.5, 1))
    leaf = nb.vmath("MULTIPLY", _desat_to(nb, c, s("BaseColorDesaturation", 0.0)),
                    (2 * tint[0], 2 * tint[1], 2 * tint[2]), label="Leaf tint x2")
    lb = nb.separate(nm.outputs["Color"])[2] if nm is not None else 1.0
    color = nb.mix(c, leaf, lb, label="Leaves (NormalMap B)")
    nb.feed(bsdf.inputs["Base Color"], nb.mix(color, (0, 0, 0, 1), 0.0, clamp_result=True))
    leaves_r = s("R-Leaves", 0.5)
    if p.sw("BaseColorRoughness", False):
        leaves_r = nb.mul(nb.separate(cc)[0] if not isinstance(cc, tuple) else cc[0], leaves_r)
    rough = nb.lerp(s("R_Branches", 0.5), leaves_r, lb)
    nb.feed(bsdf.inputs["Roughness"], nb.math("ADD", rough, 0.0, clamp=True))
    nb.feed(bsdf.inputs["Metallic"], 0.0)
    if nm is not None:
        x, y, z = _rg_normal(ctx, nm.outputs["Color"])
        nb.feed(bsdf.inputs["Normal"], _vec_to_normal(ctx, x, y, z))
    if ca is not None and not isinstance(ca, float):
        clip = p.opacity_clip if p.opacity_clip else 0.3333
        nb.feed(bsdf.inputs["Alpha"], nb.math("GREATER_THAN", ca, clip, label=f"Opacity clip {clip:g}"))
    ctx.finish()


def build_tree_trunk(ctx):
    """M_Nature_01_TreeTrunk (palm trunks), compiled shader: colour = 2 x Tint x Brightness x
    desat(BaseColorTexture, Desaturate) on UV x (TileU, TileV); roughness from the colour's red:
    x = 2 R Brightness Tint.r + Bias, r = lerp(1 - Contrast, Contrast, min((1 - x)^2, 1)) + (1 -
    NOH.B x AmbientOcclusion) x AO Contribution, at least 0.1. Normal = NOH RG x Strength. The mesh's
    break faces (UV x in 1..2) use DestructionBaseColor / DestructionNormal (colour, roughness A)."""
    p, nb = ctx.p, ctx.nb
    s = p.s
    bsdf = ctx.bsdf()
    uvt = _ue_uv(ctx, s("TileU", 1.0), s("TileV", 1.0))
    cc, _ca = _color_tex(ctx, _t(p, "BaseColorTexture"), data=False, vector=uvt, label="BaseColorTexture")
    noh = ctx.tex(_t(p, "BaseNOHTexture"), data=True, packed=True, vector=uvt, label="BaseNOHTexture")
    cc = cc if cc is not None else (0.5, 0.5, 0.5, 1.0)
    tint = p.v("Tint", (0.5, 0.5, 0.5, 1))
    br = s("Brightness", 1.0)
    c1 = nb.vmath("MULTIPLY", _desat_to(nb, cc, s("Desaturate", 0.0)), (br * tint[0], br * tint[1], br * tint[2]))
    color = nb.vmath("SCALE", c1, scale=2.0)
    x = nb.math("MULTIPLY_ADD", nb.separate(c1)[0], 2.0, s("1. Bias", 0.0))
    q = nb.math("MINIMUM", nb.math("POWER", nb.math("ABSOLUTE", nb.sub(1.0, x)), 2.0), 1.0)
    con = s("2. Contrast", 1.0)
    rough = nb.math("MULTIPLY_ADD", q, con - (1.0 - con), 1.0 - con)
    if noh is not None:
        ao = nb.math("MULTIPLY_ADD", nb.separate(noh.outputs["Color"])[2], -s("AmbientOcclusion", 1.0), 1.0)
        rough = nb.math("MULTIPLY_ADD", ao, s("3. AO Contribution", 0.0), rough, clamp=True)
    rough = nb.math("MAXIMUM", nb.math("ADD", rough, 0.0, clamp=True), 0.1)
    # break faces of the trunk (UV x in 1..2)
    u = nb.separate(ctx.uv())[0]
    brk = nb.mul(nb.math("GREATER_THAN", u, 1.0), nb.math("LESS_THAN", u, 2.0 + 1e-6))
    dct = ctx.tex(_t(p, "DestructionBaseColor"), data=False, packed=True, label="DestructionBaseColor")
    if dct is not None:
        color = nb.mix(color, dct.outputs["Color"], brk, label="Break faces (UV 1..2)")
        rough = nb.lerp(rough, dct.outputs["Alpha"], brk)
    nb.feed(bsdf.inputs["Base Color"], nb.mix(color, (0, 0, 0, 1), 0.0, clamp_result=True))
    nb.feed(bsdf.inputs["Roughness"], rough)
    nb.feed(bsdf.inputs["Metallic"], 0.0)
    if noh is not None:
        ncol = noh.outputs["Color"]
        dn = ctx.tex(_t(p, "DestructionNormal"), data=True, packed=True, label="DestructionNormal")
        if dn is not None:
            ncol = nb.mix(ncol, dn.outputs["Color"], brk)
        x, y, z = _rg_normal(ctx, ncol, s("Strength", 1.0))
        nb.feed(bsdf.inputs["Normal"], _vec_to_normal(ctx, x, y, z))
    ctx.finish()


def build_trims(ctx):
    """M_Trims_01 (pallets, planks), compiled shader: Color (UV x Tiling) x ColorMultiply, desaturated
    by ColorDesaturation, contrast (c - 0.5) x ColorTintUnderlyingDetail + 0.5, grey from R with
    UsePackedMask_s x ActivateColorMask_s; overlay with the tint = ColorTint, lerped toward
    GreenVCColorTint by vertex G (ActivateGreenVCTintColor_s) and BlueVCColorTint by vertex B.
    Roughness Color A x Roughness; normal NOH RG x NormalIntensity. Rain drops / wetness left out."""
    p, nb = ctx.p, ctx.nb
    s = p.s
    bsdf = ctx.bsdf()
    uv = ctx.uv(s("Tiling", 1.0))
    cc, ca = _color_tex(ctx, _t(p, "Color"), data=False, packed=True, vector=uv, label="Color")
    noh = ctx.tex(_t(p, "NOH"), data=True, packed=True, vector=uv, label="NOH")
    cc = cc if cc is not None else (0.5, 0.5, 0.5, 1.0)
    c = _desat_to(nb, nb.vmath("SCALE", cc, scale=s("ColorMultiply", 1.0)), s("ColorDesaturation", 0.0))
    c = _contrast(nb, c, s("ColorTintUnderlyingDetail", 1.0))
    grey = s("UsePackedMask_s", 0.0) * s("ActivateColorMask_s", 0.0)
    if grey:
        r = nb.separate(c)[0]
        c = nb.mix(c, nb.combine(r, r, r), grey)
    t = _col(p.v("ColorTint", (0.5, 0.5, 0.5, 1)))
    ag, ab = s("ActivateGreenVCTintColor_s", 0.0), s("ActivateBlueVCTintColor_s", 0.0)
    if ag or ab:
        _r, vg, vb = nb.separate(_vertex_color(ctx).outputs["Color"])
        if ag:
            t = nb.mix(t, _col(p.v("GreenVCColorTint", (0.5, 0.5, 0.5, 1))), nb.mul(vg, ag), label="Green VC tint")
        if ab:
            t = nb.mix(t, _col(p.v("BlueVCColorTint", (0.5, 0.5, 0.5, 1))), nb.mul(vb, ab), label="Blue VC tint")
    color = _overlay(nb, c, t, label="Overlay tint")
    nb.feed(bsdf.inputs["Base Color"], nb.mix(color, (0, 0, 0, 1), 0.0, clamp_result=True))
    nb.feed(bsdf.inputs["Roughness"], nb.math("MULTIPLY", ca if ca is not None else 0.5, s("Roughness", 1.0), clamp=True))
    nb.feed(bsdf.inputs["Metallic"], 0.0)
    if noh is not None:
        x, y, z = _rg_normal(ctx, noh.outputs["Color"], s("NormalIntensity", 1.0))
        nb.feed(bsdf.inputs["Normal"], _vec_to_normal(ctx, x, y, z))
    ctx.finish()


# cable index in NXM alpha: (centre, half width) of cables 2..8 (cable 1 = the rest), compiled shader
_CABLE_BANDS = [(0.18, 0.08), (0.30, 0.10), (0.42, 0.08), (0.55, 0.07), (0.66, 0.05), (0.80, 0.05), (0.95, 0.05)]


def build_spline_cable(ctx):
    """M_SplineThicken (cable bundles on utility poles), compiled shader: atlas row trunc(Position) of
    the cable trim on UV channel 2 (u x Tiling_U x 0.3, v / Number Of Rows); NXM A picks the cable
    (bands at 0.18/0.3/0.42/0.55/0.66/0.8/0.95 -> Color_Cable2..8, else Color_Cable1) and its
    Metal_Cable<n> (0.5 outside cable 1's band); colour = sat(cable colour x 2 x
    desat(CR^BaseColorPower x ColorMultply, ColorDesaturation)); roughness (CR A, 1 on cable 2)^
    RoughnessPower x RoughnessMultiply; normal NXM RG x NormalStrength."""
    p, nb = ctx.p, ctx.nb
    s = p.s
    bsdf = ctx.bsdf()
    rows = max(s("Number Of Rows", 1.0), 1.0)
    pos = float(int(s("Position", 0.0)))
    # spline meshes store UV channel 2 like skinned meshes: the shader's (x, y) = (game v, u)
    u, v, _z = nb.separate(ctx.uv(detail=True))
    uu = nb.math("MULTIPLY_ADD", nb.sub(1.0, v), 0.3 * s("Tiling_U", 1.0), -0.3 / rows)
    vv = nb.math("MULTIPLY_ADD", u, 1.0 / rows, (pos - 1.0) / rows)
    vec = nb.combine(uu, nb.sub(1.0, vv), 0.0, label="Cable atlas UV")
    nxm = ctx.tex(_t(p, "NXM"), data=True, packed=True, vector=vec, label="NXM")
    cc, ca = _color_tex(ctx, _t(p, "CR"), data=False, packed=True, vector=vec, label="CR")
    cc = cc if cc is not None else (0.5, 0.5, 0.5, 1.0)
    cols = [p.v(f"Color_Cable{i}", (0.5, 0.5, 0.5, 1)) for i in range(1, 9)]
    metals = [s(f"Metal_Cable{i}", 0.0) for i in range(1, 9)]
    color, metal, m2 = _col(cols[0]), metals[0], None
    if nxm is not None:
        idx = nxm.outputs["Alpha"]
        metal = nb.lerp(metals[0], 0.5, nb.math("GREATER_THAN", idx, 0.1))
        for i, (centre, half) in enumerate(_CABLE_BANDS, start=1):
            m = nb.math("LESS_THAN", nb.math("ABSOLUTE", nb.sub(idx, centre)), half + 1e-6, label=f"Cable {i + 1}")
            if i == 1:
                m2 = m
            color = nb.mix(color, _col(cols[i]), m)
            metal = nb.lerp(metal, metals[i], m)
    c = nb.vmath("POWER", nb.vmath("MAXIMUM", cc, (0.0, 0.0, 0.0)), (s("BaseColorPower", 1.0),) * 3)
    c = _desat_to(nb, nb.vmath("SCALE", c, scale=s("ColorMultply", 1.0)), s("ColorDesaturation", 0.0))
    c = nb.vmath("SCALE", c, scale=2.0)
    nb.feed(bsdf.inputs["Base Color"], nb.mix(color, c, 1.0, "MULTIPLY", clamp_result=True))
    ra = ca if ca is not None else 0.5
    if m2 is not None and not isinstance(ra, float):
        ra = nb.lerp(ra, 1.0, m2)
    rough = nb.math("MULTIPLY", nb.math("POWER", ra, s("RoughnessPower", 1.0)), s("RoughnessMultiply", 1.0), clamp=True)
    nb.feed(bsdf.inputs["Roughness"], rough)
    nb.feed(bsdf.inputs["Metallic"], metal if isinstance(metal, float) else nb.math("ADD", metal, 0.0, clamp=True))
    if nxm is not None:
        x, y, z = _rg_normal(ctx, nxm.outputs["Color"], s("NormalStrength", 1.0))
        nb.feed(bsdf.inputs["Normal"], _vec_to_normal(ctx, x, y, z))
    ctx.finish()


def build_decal_pom_masked(ctx):
    """M_Decals_POM_Masked (mesh decals: roof trims, sign plates), compiled shader without the parallax:
    CR on UV x (UTile, VTile) + (UOffset, VOffset): ((c - 0.5) x BaseColorTextureStrength + 0.5) x
    BaseColorMultiply, desaturated, ^BaseColorPower; roughness (CR A x Roughness Multiply)^Roughness Power;
    metal Metallic (x NOH B / A with ActivateMaskMetallicFromNormal*); normal NOH RG x Normal Strength
    + detail normal. Opacity = NOH B x Global Opacity, contrast OpacityContrast (game: dithered, opaque
    from 0.83)."""
    p, nb = ctx.p, ctx.nb
    s = p.s
    bsdf = ctx.bsdf()
    tu, tv = s("UTile", 1.0), s("VTile", 1.0)
    if p.sw("Use Atlas", False):
        # one cell of a Columns x Rows sheet: ((uv·T + O)·T − 0.5)/TileFromMiddle + 0.5, / (C, R) + cell
        cols, rows = max(s("Columns", 1.0), 1.0), max(s("Rows", 1.0), 1.0)
        pos = int(s("Position", 0.0))
        col, row = pos % int(cols), pos // int(cols)
        tf = s("TileFromMiddle", 1.0) or 1.0
        u, v, _z = nb.separate(ctx.uv())
        uu = nb.math("MULTIPLY_ADD", nb.math("MULTIPLY_ADD", u, tu, s("UOffset", 0.0)), tu / tf, 0.5 - 0.5 / tf)
        vv = nb.math("MULTIPLY_ADD", nb.math("MULTIPLY_ADD", nb.sub(1.0, v), tv, s("VOffset", 0.0)), tv / tf, 0.5 - 0.5 / tf)
        uu = nb.math("MULTIPLY_ADD", uu, 1.0 / cols, col / cols)
        vv = nb.math("MULTIPLY_ADD", vv, 1.0 / rows, row / rows)
        uvt = nb.combine(uu, nb.sub(1.0, vv), 0.0, label=f"Atlas cell {pos}")
    else:
        uvt = _ue_uv(ctx, tu, tv, s("UOffset", 0.0), s("VOffset", 0.0))
    cc, ca = _color_tex(ctx, _t(p, "CR"), data=False, packed=True, vector=uvt, label="CR")
    noh = ctx.tex(_t(p, "NOH"), data=True, packed=True, vector=uvt, label="NOH")
    cc = cc if cc is not None else (0.5, 0.5, 0.5, 1.0)
    c = _contrast(nb, cc, s("BaseColorTextureStrength", 1.0))
    c = _desat_to(nb, nb.vmath("SCALE", c, scale=s("BaseColorMultiply", 1.0)), s("BaseColorDesaturation", 0.0))
    c = nb.vmath("POWER", nb.vmath("MAXIMUM", c, (0.0, 0.0, 0.0)), (s("BaseColorPower", 1.0),) * 3)
    nb.feed(bsdf.inputs["Base Color"], nb.mix(c, (0, 0, 0, 1), 0.0, clamp_result=True))
    rough = nb.math("POWER", nb.mul(ca if ca is not None else 0.5, s("Roughness Multiply", 1.0)), s("Roughness Power", 1.0))
    nb.feed(bsdf.inputs["Roughness"], nb.math("ADD", rough, 0.0, clamp=True))
    met = s("Metallic", 0.0)
    if noh is None:
        nb.feed(bsdf.inputs["Metallic"], max(0.0, min(1.0, met)))
        ctx.finish()
        return
    _r, _g, nbch = nb.separate(noh.outputs["Color"])
    m = nb.lerp(met, nb.mul(nbch, met), s("ActivateMaskMetallicFromNormalBlueChannel", 0.0))
    m = nb.lerp(m, noh.outputs["Alpha"], s("ActivateMaskMetallicFromNormalAlphaChannel", 0.0))
    nb.feed(bsdf.inputs["Metallic"], nb.math("ADD", m, 0.0, clamp=True))
    x, y, z = _rg_normal(ctx, noh.outputs["Color"], s("Normal Strength", 1.0))
    if p.sw("ActivateDetailNormal", False) and _t(p, "DetailNormal"):
        duv = ctx.uv(detail=bool(s("UseUV1", 0.0)))
        dvec = nb.vmath("ADD", nb.vmath("MULTIPLY", duv, (s("U Tile", 1.0), s("V Tile", 1.0), 1.0)),
                        (s("DetailNormalOffsetU", 0.0), -s("DetailNormalOffsetV", 0.0), 0.0), label="Detail tiling")
        det = ctx.tex(_t(p, "DetailNormal"), data=True, packed=True, vector=dvec, label="DetailNormal")
        if det is not None:
            dx, dy, _dz = _rg_normal(ctx, det.outputs["Color"])
            di = s("DetailNormalIntensity", 1.0)
            x = nb.math("MULTIPLY_ADD", dx, di, x)
            y = nb.math("MULTIPLY_ADD", dy, di, y)
    nb.feed(bsdf.inputs["Normal"], _vec_to_normal(ctx, x, y, z))
    if s("ActivateOpacityMasking", 1.0):
        k = s("OpacityContrast", 0.0)
        o = nb.math("MULTIPLY_ADD", nb.mul(nbch, s("Global Opacity", 1.0)), 2 * k + 1, -k)
        nb.feed(bsdf.inputs["Alpha"], nb.math("DIVIDE", o, 0.83, clamp=True))
    ctx.finish()


def build_2layer_blend(ctx):
    """M_2LayerBlend_01 (props sharing building materials: painted metal, plaster), base layer from the
    compiled shader: CR on UV x (BaseTextureTiliing_U/V x BaseTextureTiling) + offset, or a box
    projection (WorldTiling cm) with ActivateTriplanar; ((c - 0.5) x BaseColorTextureStrength + 0.5),
    desat, x BaseColorMultiply, ^BaseColorPower, x BaseColorTint x 2 (x 1 with
    ActivateMultiplyColorTint_s). Roughness min((lerp(CR R, CR A, ActivateRoughnessMap_s) x
    BaseRoughness)^BaseRoughnessPower, 1); metal lerp(BaseMetallic, NOH B, ActivateBaseMetallic_s) x
    BaseMetallicMultiply; normal NOH RG x BaseNormalIntensity; + the shared detail map. Approximate:
    the blend layer (vertex paint / texture / slope blends), parallax, ground dirt and wetness are
    left out."""
    p, nb = ctx.p, ctx.nb
    s = p.s
    bsdf = ctx.bsdf()
    tri = s("ActivateTriplanar", 0.0) >= 0.5
    if tri:
        k = ctx.cm_per_unit / max(s("WorldTiling", 1024.0), 1e-3)
        vec = nb.vmath("SCALE", nb.node("ShaderNodeTexCoord").outputs["Object"], scale=k, label="World tiling")
    else:
        bt = s("BaseTextureTiling", 1.0)
        vec = _ue_uv(ctx, s("BaseTextureTiliing_U", 1.0) * bt, s("BaseTextureTiliing_V", 1.0) * bt,
                     s("BaseTextureUVOffset_U", 0.0), s("BaseTextureUVOffset_V", 0.0))
    crn = ctx.tex(_t(p, "BaseColorTexture"), data=False, packed=True, vector=vec, label="BaseColorTexture")
    noh = ctx.tex(_t(p, "BaseNOHTexture"), data=True, packed=True, vector=vec, label="BaseNOHTexture")
    if tri:
        for n in (crn, noh):
            if n is not None:
                n.projection = "BOX"
                n.projection_blend = 0.2
    if crn is not None:
        cc, ca = crn.outputs["Color"], crn.outputs["Alpha"]
        cr = nb.separate(cc)[0]
    else:
        cc, ca = _color_tex(ctx, _t(p, "BaseColorTexture"))
        cc = cc if cc is not None else (0.5, 0.5, 0.5, 1.0)
        ca = ca if ca is not None else 0.5
        cr = cc[0]
    c = _contrast(nb, cc, s("BaseColorTextureStrength", 1.0))
    c = nb.vmath("SCALE", _desat_to(nb, c, s("BaseColorDesaturation", 0.0)), scale=s("BaseColorMultiply", 1.0))
    c = nb.vmath("POWER", nb.vmath("MAXIMUM", c, (0.0, 0.0, 0.0)), (s("BaseColorPower", 1.0),) * 3)
    t = p.v("BaseColorTint", (0.5, 0.5, 0.5, 1))
    f = 2.0 - s("ActivateMultiplyColorTint_s", 0.0)
    c = nb.vmath("MULTIPLY", c, (f * t[0], f * t[1], f * t[2]), label="BaseColorTint")
    r0 = nb.lerp(cr, ca, s("ActivateRoughnessMap_s", 1.0))
    rough = nb.math("MINIMUM", nb.math("POWER", nb.mul(r0, s("BaseRoughness", 1.0)), s("BaseRoughnessPower", 1.0)), 1.0)
    det = None
    if p.sw("ActivateDetailMap", False):
        det, c, rough = _detail_layer(ctx, c, rough)
    nb.feed(bsdf.inputs["Base Color"], nb.mix(c, (0, 0, 0, 1), 0.0, clamp_result=True))
    nb.feed(bsdf.inputs["Roughness"], nb.math("ADD", rough, 0.0, clamp=True))
    bm, act, mul = s("BaseMetallic", 0.0), s("ActivateBaseMetallic_s", 0.0), s("BaseMetallicMultiply", 1.0)
    if noh is not None and act:
        m = nb.lerp(bm, nb.separate(noh.outputs["Color"])[2], act)
        nb.feed(bsdf.inputs["Metallic"], nb.math("MULTIPLY", m, mul, clamp=True))
    else:
        nb.feed(bsdf.inputs["Metallic"], max(0.0, min(1.0, bm * mul)))
    if noh is not None:
        nb.feed(bsdf.inputs["Normal"], _normal_with_detail(ctx, noh.outputs["Color"], det, s("BaseNormalIntensity", 1.0)))
    ctx.finish()


def build_decal_grunge(ctx):
    """M_Decals_Grunge_Atlas_01 (dirt / leak mesh decals on props and walls), compiled shader:
    atlas cell (Atlas Active: (uv x (UTile, VTile))/TileFromMiddle + 0.5 - 0.5/TileFromMiddle, / (Columns,
    Rows) + cell of Position); colour = overlay(desat(Colormap x ColorMultiply, ColorDesaturation), Color);
    mask = dot(Opacity Mask RGB, OpacityChannelWeights); normal = Opacity Mask RG x NormalIntensity.
    Roughness: m = mask x (1 - game v x Variation Mask Gradient Power), x the Variation Masks channels
    (VariationMaskChannel by Variation Mask Intensity, MaskDetailChannel by Variation Mask Detail Overlay),
    r = lerp(1 - Rough Max, Rough Max, min((1 - m - Rough Min)^2, 1)), final lerp(Rough Value,
    Rough Value x r, Detail R Overlay). Opacity sat((mask x Opacity Multiply)(2C + 1) - C) x Global Opacity
    (C = Opacity Contrast), dithered against 0.83 in the game. Colour variation left out."""
    p, nb = ctx.p, ctx.nb
    s = p.s
    bsdf = ctx.bsdf()
    tu, tv = s("UTile", 1.0), s("VTile", 1.0)
    u, v, _z = nb.separate(ctx.uv())
    vg = nb.sub(1.0, v)
    tf = s("TileFromMiddle", 1.0) or 1.0
    uu = nb.math("MULTIPLY_ADD", u, tu / tf, 0.5 - 0.5 / tf)
    vv = nb.math("MULTIPLY_ADD", vg, tv / tf, 0.5 - 0.5 / tf)
    if p.sw("Atlas Active", False):
        cols, rows = max(s("Columns", 1.0), 1.0), max(s("Rows", 1.0), 1.0)
        pos = int(s("Position", 0.0))
        uu = nb.math("MULTIPLY_ADD", uu, 1.0 / cols, (pos % int(cols)) / cols)
        vv = nb.math("MULTIPLY_ADD", vv, 1.0 / rows, (pos // int(cols)) / rows)
    vec = nb.combine(uu, nb.sub(1.0, vv), 0.0, label="Decal atlas cell")
    om = ctx.tex(_t(p, "Opacity Mask"), data=True, vector=vec, label="Opacity Mask")
    cc, _ca = _color_tex(ctx, _t(p, "Colormap"), data=False, vector=vec, label="Colormap")
    if cc is not None and p.sw("Use Color Map", True):
        c = _desat_to(nb, nb.vmath("SCALE", cc, scale=s("ColorMultiply", 1.0)), s("ColorDesaturation", 0.0))
        color = _overlay(nb, c, _col(p.v("Color", (0.5, 0.5, 0.5, 1))), label="Overlay Color")
    else:
        color = _col(p.v("Color", (0.5, 0.5, 0.5, 1)))
    nb.feed(bsdf.inputs["Base Color"], nb.mix(color, (0, 0, 0, 1), 0.0, clamp_result=True))
    nb.feed(bsdf.inputs["Metallic"], 0.0)
    if om is None:
        nb.feed(bsdf.inputs["Roughness"], s("Rough Value", 0.5))
        ctx.finish()
        return
    w = p.v("OpacityChannelWeights", (0.0, 0.0, 1.0, 0.0))
    mask = nb.vmath("DOT_PRODUCT", om.outputs["Color"], (w[0], w[1], w[2]), label="Mask")
    # roughness
    m = nb.mul(mask, nb.math("MULTIPLY_ADD", vg, -s("Variation Mask Gradient Power", 0.0), 1.0))
    vt = s("Mask Detail Tile", 1.0)
    var = ctx.tex(_t(p, "Variation Masks"), data=True, vector=nb.vmath("SCALE", ctx.uv(), scale=vt), label="Variation Masks")
    if var is not None:
        for chan, amount in (("VariationMaskChannel", "Variation Mask Intensity"),
                             ("MaskDetailChannel", "Variation Mask Detail Overlay")):
            cw = p.v(chan, (0.0, 0.0, 0.0, 0.0))
            k = s(amount, 0.0)
            if k and any(cw[:3]):
                d = nb.vmath("DOT_PRODUCT", var.outputs["Color"], (cw[0], cw[1], cw[2]))
                m = nb.lerp(m, nb.mul(m, d), k)
    x = nb.sub(1.0, nb.add(m, s("Rough Min", 0.0)))
    q = nb.math("MINIMUM", nb.mul(x, x), 1.0)
    rmax = s("Rough Max", 1.0)
    r = nb.math("MULTIPLY_ADD", q, rmax - (1.0 - rmax), 1.0 - rmax, clamp=True)
    rv = s("Rough Value", 0.5)
    r = nb.math("MULTIPLY_ADD", nb.math("MULTIPLY_ADD", r, rv, -rv), s("Detail R Overlay", 0.0), rv, clamp=True)
    nb.feed(bsdf.inputs["Roughness"], r)
    x, y, z = _rg_normal(ctx, om.outputs["Color"], s("NormalIntensity", 1.0))
    nb.feed(bsdf.inputs["Normal"], _vec_to_normal(ctx, x, y, z))
    if s("ActivateOpacityMasking", 1.0):
        k = s("Opacity Contrast", 0.0)
        o = nb.math("MULTIPLY_ADD", nb.mul(mask, s("Opacity Multiply", 1.0)), 2 * k + 1, -k, clamp=True)
        o = nb.math("MULTIPLY", o, s("Global Opacity", 1.0), clamp=True)
        nb.feed(bsdf.inputs["Alpha"], nb.math("DIVIDE", o, 0.83, clamp=True))
    ctx.finish()
