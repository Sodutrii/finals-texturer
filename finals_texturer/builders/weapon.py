"""Weapon shaders (Weapons tab).

M_Weapon_Base_5Layers, rebuilt from the game's compiled base-pass shader
(dev_tools/shader_extract, 2026-11-12; checked texel by texel with
compare_game.py). "Layer k" parameters are prefixed k_ (k = 0..4).

* NWO texture (UV 1): normal in R/G; B = wear / cavity (0.5 neutral: above
  it edge wear, edge = sat((B - 0.5) x 2); below it dirt); A = variation
  (fake colour variation, gradient row). TreatNWOasNOW swaps B and A.
  There is no ambient occlusion in it.
* Index (k_IndexMap R) on UV channel 2 x k_IndexTiling (the game's vertex
  shader passes that channel as (v, u) and the pixel shader swaps it back); x (1 - IndexDarkening); AddEdgeWearToIndex: lerp toward
  IndexEdgeWearInfluence by edge.
* Colour Color_A / B / C at index 0 / 0.5 / 1, roughness Roughness_A / B / C
  the same. Fake colour variation: x (1 - 2 x clamp(amount, 0, 0.25) x A).
  UseGradientTexture: the GradientMap at (index, A) replaces colour (RGB) and
  roughness (alpha).
* Edge-wear metal (AddEdgeWearToMetalness): m = map(edge, MaskSpreading..1),
  colour -> preset metal (EdgeWearMetalType) by sat(1.8 m - 0.4), metalness
  + m, roughness -> EdgeWearMetalnessRoughness.
* Detail (Weapon_DTN array slice DetailType, UV 2 x DetailTiling):
  normal R/G (overlay x DetailNormalStrength), A darkens colour
  (x 1.3 - 0.6 A) and scales roughness (x 1 - s + 2 s A).
* Dirt where B < 0.5: sat((DirtSpreading - sat(2B)) / DirtSpreading), plus
  tiled grunge; GrungeColor (half as bright in cavities), metal 0, roughness
  0.8 + 0.1 sat(2B).
* Metalness: Metalness -> IndexMetalness above IndexMetalnessThreshold.
  Clear coat: Clearcoat (off when IndexMetalness >= 1) x (1 - edge) x (1 - dirt).
* Curvature (when a real texture is set): colour, metal x (1 + Pop (2c - 1)),
  roughness x (1 + Pop x Cavity/Convexity), specular 0.5 sat(2c).
* Layers 1..4 (static switch k_UseBlend): mask = dot(ID "Masks", k_Mask),
  + empty-ID area when k_MaskBlack, minus edge wear (RemoveEdgeWear_s,
  EdgeWearSpreadingLow..High). The game blends the layers' parameters by the
  mask before computing; we blend the results (same on 0/1 masks).
* Graphic (UseWeaponGraphicOrGradient, see _graphic): painted inside each
  layer after the edge-wear metal (before detail / dirt) with weight
  0k_GraphicWeight x graphic alpha; with the static switch UseIDMask the
  weight comes from the ID texture instead (IDMask_GraphicWeight_R/G/B/A/Black).
"""

from .. import defaults
from .common import _col, _packed_normal

NULL_NAMES = {"defaulttexture", "baseflattengrayscalemap", "baseflattenlinearcolor", "t_50percentgray"}
DTN = "/Game/Discovery/Items/Shared/Textures/Details/Weapon_DTN.Weapon_DTN"
GRUNGE = "/Game/Discovery/Textures/Tileables/Grunges/T_GrungeDust_01_M.T_GrungeDust_01_M"
# EdgeWearMetalType 0..4 (constants in the shader); 5 = MetalColorIndex5Input
EDGE_METALS = [(0.768628, 0.780392, 0.780392), (0.960784, 0.964706, 0.964706), (0.980392, 0.815686, 0.752941),
               (0.839216, 0.725490, 0.482353), (1.0, 0.886275, 0.607843)]


def _tex_ok(p, name):
    path = p.t(name)
    if not path:
        return ""
    base = path.split("/")[-1].split(".")[0].lower()
    if base in NULL_NAMES or base in defaults.NULL_TEXTURES:
        return ""
    return path


def _is_srgb(ctx, object_path):
    meta = ctx.finder.load_asset_json(object_path)
    for e in meta if isinstance(meta, list) else []:
        props = (e or {}).get("Properties") or {}
        if "SRGB" in props:
            return bool(props["SRGB"])
    return True                     # UE default


def _lerp3(nb, a, b, c, t, label):
    """a at t=0, b at 0.5, c at 1 (colours or values)."""
    if isinstance(a, (int, float)) and a == b == c:
        return a
    lo = nb.map_range(t, 0.0, 0.5, 0.0, 1.0, label=f"{label} 0..0.5")
    hi = nb.map_range(t, 0.5, 1.0, 0.0, 1.0, label=f"{label} 0.5..1")
    if isinstance(a, (int, float)):
        return nb.lerp(nb.lerp(a, b, lo), c, hi)
    return nb.mix(nb.mix(a, b, lo, label=f"{label} A→B"), c, hi, label=f"{label} →C")


def _channels(ctx, nwo):
    """(wear B, variation A, edge) sockets of the NWO texture, or Nones."""
    if nwo is None:
        return None, None, None
    nb = ctx.nb
    _r, _g, b = nb.separate(nwo.outputs["Color"], label="NWO channels")
    a = nwo.outputs["Alpha"]
    wear, var = (a, b) if ctx.p.s("TreatNWOasNOW", 0.0) > 0.0 else (b, a)
    edge = nb.map_range(wear, 0.5, 1.0, label="Edge wear (B above 0.5)")
    return wear, var, edge


def _graphic(ctx):
    """(colour, alpha) of the graphic, or None. A full-colour picture ("Graphic",
    UseGraphic) or a greyscale pattern ("Grayscale", R) coloured through the
    "Gradient" atlas row Gradient Y Sample Index (the game's vertex shader
    passes (index + 0.5) / height as the v coordinate). Placed on UV channel 2
    (UseUV2ForGraphic: channel 3) x GraphicUVs.zw + xy in UE's texture space
    (v down), optionally clamped per axis (Clamp_UV_X / Y)."""
    p, nb = ctx.p, ctx.nb
    use_pic = p.sw("UseGraphic") and _tex_ok(p, "Graphic")
    if not (p.sw("UseWeaponGraphicOrGradient") and (use_pic or _tex_ok(p, "Grayscale"))):
        return None
    uvs = p.v("GraphicUVs", (0, 0, 1, 1))
    su, sv = uvs[2], uvs[3]
    # UE: (u, v_ue) x (zw) + (xy); Blender v = 1 - v_ue on both the mesh and the image
    sep = nb.node("ShaderNodeSeparateXYZ", "UV 2")
    nb.feed(sep.inputs[0], ctx.uv(detail=True))
    if p.s("DYNAMIC_RotateUVs", 0.0) >= 0.5:
        # axes swapped: texture x = v_ue x a + g, texture v_ue = u x b + r
        x = nb.math("MULTIPLY_ADD", sep.outputs[1], -sv, sv + uvs[1])
        y = nb.math("MULTIPLY_ADD", sep.outputs[0], -su, 1.0 - uvs[0])
    else:
        x = nb.math("MULTIPLY_ADD", sep.outputs[0], su, uvs[0])
        y = nb.math("MULTIPLY_ADD", sep.outputs[1], sv, 1.0 - sv - uvs[1])
    if p.s("Clamp_UV_X", 0.0) >= 0.5:
        x = nb.math("MINIMUM", nb.math("MAXIMUM", x, 0.001), 0.999)
    if p.s("Clamp_UV_Y", 0.0) >= 0.5:
        y = nb.math("MINIMUM", nb.math("MAXIMUM", y, 0.001), 0.999)
    vec = nb.node("ShaderNodeCombineXYZ", "GraphicUVs (UV2 x zw + xy)")
    nb.feed(vec.inputs[0], x)
    nb.feed(vec.inputs[1], y)
    vec = vec.outputs[0]
    if use_pic:
        pic = ctx.tex(_tex_ok(p, "Graphic"), data=False, packed=True, vector=vec, label="Graphic (colour)")
        return (pic.outputs["Color"], pic.outputs["Alpha"]) if pic is not None else None
    gray = ctx.tex(_tex_ok(p, "Grayscale"), data=True, vector=vec, label="Graphic (greyscale)")
    if gray is None:
        return None
    g = ctx.red(gray)
    grad_path = _tex_ok(p, "Gradient")
    if grad_path:
        row = p.s("Gradient Y Sample Index", 0.0)
        gcol, ga = ctx.gradient(grad_path, 0.0, g, label=f"Graphic colours (row {row:g})", row=row)
        if gcol is not None:
            return gcol, ga
    return nb.combine(g, g, g), 1.0


def _id_graphic_weight(ctx, ids):
    """UseIDMask: graphic weight from the ID texture, w = 1 then lerped toward
    IDMask_GraphicWeight_R / G / B / A / Black by each channel (Black = no ID)."""
    p, nb = ctx.p, ctx.nb
    r, g, b = nb.separate(ids.outputs["Color"])
    a = ids.outputs["Alpha"]
    black = nb.math("ROUND", nb.math("SUBTRACT", 1.0, nb.add(nb.add(r, g), nb.add(b, a), clamp=True)))
    w = 1.0
    for ch, name in ((r, "R"), (g, "G"), (b, "B"), (a, "A"), (black, "Black")):
        v = p.s(f"IDMask_GraphicWeight_{name}", 1.0)
        if isinstance(w, float) and w == v:
            continue
        w = nb.lerp(w, v, ch)
    return w


def _apply_graphic(ctx, k, color, graphic):
    """Layer k's share of the graphic: k-th GraphicWeight (or the ID-mask
    weight with UseIDMask) x graphic alpha."""
    p, nb = ctx.p, ctx.nb
    if graphic is None:
        return color
    gcol, ga, idw = graphic
    wk = idw if idw is not None else p.s(f"0{k}_GraphicWeight", 1.0)
    if isinstance(wk, float) and not wk:
        return color
    if p.s("DYNAMIC_UseBaseColorOverlay", 0.0) > 0.0:
        lum = nb.vmath("DOT_PRODUCT", color, (0.3, 0.59, 0.11))
        gcol = nb.vmath("SCALE", gcol, scale=nb.mul(lum, 2.0), label="Graphic overlay")
    em = p.s("UseAlphaAsEmissiveMask", 0.0)
    cover = wk if (em >= 1.0 or isinstance(ga, float)) else nb.mul(nb.lerp(ga, 1.0, em), wk)
    label = f"Layer {k} graphic (weight {wk:g})" if isinstance(wk, float) else f"Layer {k} graphic (ID-mask weight)"
    return nb.mix(color, gcol, cover, label=label)


def _layer(ctx, k, ch, graphic=None):
    """Surface of layer k: dict colour / roughness / metalness / clearcoat
    (sockets or values) and the detail texture node."""
    p, nb = ctx.p, ctx.nb
    s = lambda n, d: p.s(f"{k}_{n}", d)
    wear, var, edge = ch

    index = 0.5
    idx_path = _tex_ok(p, f"{k}_IndexMap")
    if idx_path:
        it = ctx.tex(idx_path, data=True, scale=s("IndexTiling", 1.0) or 1.0, detail=True,
                     label=f"Layer {k} index")
        if it is not None:
            index = ctx.red(it)
    dark = s("IndexDarkening", 0.0)
    if dark:
        index = nb.mul(index, 1.0 - dark)
    if s("AddEdgeWearToIndex(B)", 0.0) >= 0.5 and edge is not None:
        index = nb.lerp(index, s("IndexEdgeWearInfluence", 1.0), edge)

    color = _lerp3(nb, _col(p.v(f"{k}_Color_A", (0, 0, 0, 1))), _col(p.v(f"{k}_Color_B", (0.5, 0.5, 0.5, 1))),
                   _col(p.v(f"{k}_Color_C", (1, 1, 1, 1))), index, f"Layer {k} colour")
    fake = s("UseFakeColorVariation(B)", 0.0)
    if fake and var is not None:
        amt = min(max(s("FakeColorVariationAmount", 0.15), 0.0), 0.25)
        color = nb.vmath("SCALE", color, scale=nb.math("MULTIPLY_ADD", var, -2.0 * amt * fake, 1.0),
                         label=f"Layer {k} fake colour variation (A)")
    rough = _lerp3(nb, s("Roughness_A", 0.5), s("Roughness_B", 0.5), s("Roughness_C", 0.5), index,
                   f"Layer {k} roughness")
    use_grad = s("UseGradientTexture", 0.0)
    grad_path = _tex_ok(p, f"{k}_GradientMap")
    if use_grad and grad_path:
        vec = nb.node("ShaderNodeCombineXYZ", f"Layer {k} gradient (index, A)")
        nb.feed(vec.inputs[0], index)
        nb.feed(vec.inputs[1], nb.math("SUBTRACT", 1.0, var) if var is not None else 0.5)
        g = ctx.tex(grad_path, data=not _is_srgb(ctx, grad_path), packed=True, vector=vec.outputs[0],
                    extension="EXTEND", label=f"Layer {k} gradient (RGB colour, A roughness)")
        if g is not None:
            color = nb.mix(color, g.outputs["Color"], use_grad, label=f"Layer {k} gradient")
            rough = g.outputs["Alpha"] if use_grad >= 1.0 else nb.lerp(rough, g.outputs["Alpha"], use_grad)

    # metalness by index
    m0, m1 = s("Metalness", 0.0), s("IndexMetalness", 0.0)
    thr = s("IndexMetalnessThreshold", 0.75)
    metal = m0 if m0 == m1 or thr >= 1.0 else nb.map_range(index, thr, 1.0, m0, m1, label=f"Layer {k} metalness")

    # edge-wear metal
    add_m = s("AddEdgeWearToMetalness(B)", 0.0)
    spread = s("EdgeWearMetalnessMaskSpreading", 0.0)
    if add_m and edge is not None and spread < 1.0:
        ewm = nb.map_range(edge, spread, 1.0, 0.0, add_m, label=f"Layer {k} edge-wear metal")
        ewc = nb.map_range(edge, spread + (1 - spread) * 0.4 / 1.8, spread + (1 - spread) * 1.4 / 1.8, 0.0, add_m,
                           label=f"Layer {k} edge-wear metal colour")
        t = int(min(max(round(s("EdgeWearMetalType", 1.0)), 0), 5))
        mc = _col(p.v("MetalColorIndex5Input", (0.768628, 0.780392, 0.780392, 1))) if t == 5 else EDGE_METALS[t] + (1.0,)
        color = nb.mix(color, mc, ewc, label=f"Layer {k} edge metal (type {t})")
        metal = nb.lerp(metal, 1.0, ewm) if metal != 1.0 else 1.0
        rough = nb.lerp(rough, s("EdgeWearMetalnessRoughness", 0.0), ewc)

    color = _apply_graphic(ctx, k, color, graphic)

    # detail texture (array slice DetailType): A darkens colour, scales roughness
    dtn = ctx.slice(DTN, s("DetailType", 0.0), data=True, packed=True,
                    scale=(s("DetailTiling", 6.0) or 1.0) * (p.s("DetailUVTilingMultiplier", 1.0) or 1.0),
                    label=f"Layer {k} detail (normal RG, A)")
    if dtn is not None:
        d = dtn.outputs["Alpha"]
        color = nb.vmath("SCALE", color, scale=nb.math("MULTIPLY_ADD", d, -0.6, 1.3), label=f"Layer {k} detail colour")
        rs = s("DetailRoughnessStrength", 0.5)
        if rs:
            rough = nb.mul(rough, nb.math("MULTIPLY_ADD", d, 2.0 * rs, 1.0 - rs))

    # dirt in cavities (B below 0.5) and tiled grunge
    dirt = None
    ds = s("DirtSpreading", 0.5)
    if wear is not None and ds > 0.0:
        w2 = nb.map_range(wear, 0.0, 0.5, label="sat(2 B)")
        dirt = nb.map_range(w2, ds, 0.0, label=f"Layer {k} dirt")
        tds = s("TilingDirtSpreading", 0.8)
        if tds < 1.0 and ctx.finder.find_texture(GRUNGE):
            gr = ctx.tex(GRUNGE, data=True, packed=True, scale=p.s("TilingGrungeTiling", 5.0) or 1.0, label="Grunge")
            if gr is not None:
                gd = nb.map_range(gr.outputs["Alpha"], tds, 1.0)
                dirt = nb.math("SUBTRACT", 1.0, nb.mul(nb.math("SUBTRACT", 1.0, dirt), nb.math("SUBTRACT", 1.0, gd)))
        gc = _col(p.v("GrungeColor", (0.104167, 0.069607, 0.046042, 1)))
        gcol = nb.mix(tuple(x * 0.5 for x in gc[:3]) + (1.0,), gc, w2, label="Grunge colour")
        color = nb.mix(color, gcol, dirt, label=f"Layer {k} dirt")
        metal = nb.mul(metal, nb.math("SUBTRACT", 1.0, dirt)) if metal != 0.0 else 0.0
        rough = nb.lerp(rough, nb.math("MULTIPLY_ADD", w2, 0.1, 0.8), dirt)

    coat = s("Clearcoat", 0.0) * (1.0 - min(max((m1 - 0.95) / 0.05, 0.0), 1.0))
    if coat and edge is not None:
        coat = nb.mul(coat, nb.math("SUBTRACT", 1.0, edge))
        if dirt is not None:
            coat = nb.mul(coat, nb.math("SUBTRACT", 1.0, dirt))
    return {"color": color, "rough": rough, "metal": metal, "coat": coat, "dtn": dtn}


def _layer_mask(ctx, k, ids, edge):
    p, nb = ctx.p, ctx.nb
    r, g, b = nb.separate(ids.outputs["Color"])
    a = ids.outputs["Alpha"]
    sel = tuple(p.v(f"{k}_Mask", (1, 0, 0, 0))) + (0.0,) * 4
    mask = nb.vmath("DOT_PRODUCT", ids.outputs["Color"], tuple(sel[:3]), label=f"Layer {k} mask")
    if sel[3]:
        mask = nb.math("MULTIPLY_ADD", a, sel[3], mask)
    black = p.s(f"{k}_MaskBlack", 0.0)
    if black:
        anyid = nb.add(nb.add(r, g), nb.add(b, a), clamp=True)
        empty = nb.mul(nb.math("ROUND", nb.math("SUBTRACT", 1.0, anyid)), black)
        mask = nb.math("MULTIPLY_ADD", empty, nb.math("SUBTRACT", 1.0, mask), mask, label=f"Layer {k} mask (+ empty ID)")
    rem = p.s(f"{k}_RemoveEdgeWear_s", 0.0)
    lo, hi = p.s(f"{k}_EdgeWearSpreadingLow", 0.0), p.s(f"{k}_EdgeWearSpreadingHigh", 1.0)
    if rem and edge is not None and hi != lo:
        f = nb.map_range(edge, lo, hi, 0.0, rem)
        mask = nb.mul(mask, nb.math("SUBTRACT", 1.0, f))
    return nb.clamp01(mask)


def _blend(nb, a, b, mask, colour=False, label=None):
    if colour:
        return nb.mix(a, b, mask, label=label)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and a == b:
        return a
    return nb.lerp(a, b, mask)


def build_weapon(ctx, fx=False):
    p, nb = ctx.p, ctx.nb
    bsdf = ctx.bsdf()
    nwo_path = _tex_ok(p, "NWO")
    nwo = ctx.tex(nwo_path, data=True, packed=True, label="NWO (normal RG, wear B, variation A)") if nwo_path else None
    ch = _channels(ctx, nwo)
    edge = ch[2]
    ids_path = _tex_ok(p, "Masks")
    ids = ctx.tex(ids_path, data=True, packed=True, label="ID masks (layers 1-4)") if ids_path else None

    graphic = _graphic(ctx)
    if graphic is not None:
        idw = _id_graphic_weight(ctx, ids) if (p.sw("UseIDMask") and ids is not None) else None
        graphic = (graphic[0], graphic[1], idw)
    if fx and not _tex_ok(p, "0_IndexMap") and not p.has_any("0_Color_A", "0_Roughness_A"):
        color, rough, metal, coat = _fx_base(ctx)
        color = _apply_graphic(ctx, 0, color, graphic)
        dtn0 = None
    else:
        L = _layer(ctx, 0, ch, graphic)
        color, rough, metal, coat, dtn0 = L["color"], L["rough"], L["metal"], L["coat"], L["dtn"]
    for k in range(1, 5):
        if not p.sw(f"{k}_UseBlend") or ids is None:
            continue
        mask = _layer_mask(ctx, k, ids, edge)
        L = _layer(ctx, k, ch, graphic)
        color = _blend(nb, color, L["color"], mask, colour=True, label=f"Layer {k}")
        rough = _blend(nb, rough, L["rough"], mask)
        metal = _blend(nb, metal, L["metal"], mask)
        coat = _blend(nb, coat, L["coat"], mask)

    # curvature "pop" (only when a real curvature texture is set; 50 % grey = none)
    curv_path = _tex_ok(p, "Curvature")
    curv = ctx.tex(curv_path, data=True, label="Curvature") if curv_path else None
    if curv is not None:
        pop = p.s("PopMaskContribution", 0.75)
        c = ctx.red(curv)
        c2 = nb.map_range(c, 0.0, 0.5, label="sat(2 curvature)")
        color = nb.vmath("SCALE", color, scale=nb.math("MULTIPLY_ADD", c, 2.0 * pop, 1.0 - pop), label="Curvature pop")
        if metal != 0.0:
            metal = nb.mul(metal, nb.math("MULTIPLY_ADD", c2, pop, 1.0 - pop), clamp=True)
        cav, conv = (p.s("CavityRoughness", 1.0) - 0.5) * 2, (p.s("ConvexityRoughness", 1.1) - 0.5) * 2
        x = nb.math("MULTIPLY_ADD", nb.map_range(c, 0.5, 1.0), conv,
                    nb.mul(nb.math("SUBTRACT", 1.0, c2), cav))
        rough = nb.mul(rough, nb.math("MULTIPLY_ADD", x, pop, 1.0), clamp=True)
        nb.feed(bsdf.inputs["Specular IOR Level"], nb.mul(c2, 0.5))
    if nwo is not None:
        if dtn0 is not None and p.s("0_DetailNormalStrength", 1.0):
            # detail normal: n = N x (1 + s (2 D - 1)) on the 0..1 encoded R/G
            st = p.s("0_DetailNormalStrength", 1.0)
            nr, ng, _nb = nb.separate(nwo.outputs["Color"])
            dr, dg, _db = nb.separate(dtn0.outputs["Color"])
            from .. import groups
            rg = nb.group(groups.normal_from_rg(), label="Normal from RG (+ detail)")
            nb.feed(rg.inputs["X"], nb.mul(nr, nb.math("MULTIPLY_ADD", dr, 2.0 * st, 1.0 - st)))
            nb.feed(rg.inputs["Y"], nb.mul(ng, nb.math("MULTIPLY_ADD", dg, 2.0 * st, 1.0 - st)))
            nb.feed(rg.inputs["Strength"], 1.0)
            nb.feed(bsdf.inputs["Normal"], ctx.normal_map(rg.outputs[0]))
        else:
            nb.feed(bsdf.inputs["Normal"], _packed_normal(ctx, nwo)[0])
    if graphic is not None and p.s("UseGraphicAsEmissive", 0.0) > 0.0:
        # glowing graphic (layer 0's weight; the game blends the weights by the layer masks)
        gcol, ga, _idw = graphic
        nb.feed(bsdf.inputs["Emission Color"], nb.mix((0, 0, 0, 1), gcol, nb.mul(ga, p.s("00_GraphicWeight", 1.0))
                                                      if not isinstance(ga, float) else p.s("00_GraphicWeight", 1.0)))
        nb.feed(bsdf.inputs["Emission Strength"], max(p.s("GraphicEmissiveIntensity", 1.0), 0.0))
    if fx:
        color = _fx_extras(ctx, bsdf, color)
    nb.feed(bsdf.inputs["Base Color"], color)
    nb.feed(bsdf.inputs["Roughness"], rough)
    nb.feed(bsdf.inputs["Metallic"], metal)
    nb.feed(bsdf.inputs["Coat Weight"], coat)
    nb.feed(bsdf.inputs["Coat Roughness"], max(0.0, min(1.0, p.s("ClearCoatRoughness", 0.2))))
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
