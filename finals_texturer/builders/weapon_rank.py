"""Ranked weapon skins (Weapons tab "Rank: …" entries), from the game's compiled shaders (2026-11-13).

Gold uses the normal layered weapon shader (weapon.py). Platinum and the gem ranks have their own masters:

* M_LegendaryWeaponFresnel (Platinum, WEAPON_FRESNEL): base = sat(lerp(BaseColorA, BaseColorB, NWO A) +
  (curvature - 0.5) x BaseCurvatureColorWeight). Inside the ID mask (see _id_mask) a view-angle rainbow:
  f = (1 - sat(N.V))^2, rainbow fresnel fr = f^Rainbow Fresnel Exponent, colour fresnel fc = f^|Color
  Fresnel Exponent| (1 - fc for a negative exponent). Hue h = (sat((OffsetMask(UV2 transposed x
  FresnelOffsetTiling) - 0.5) x FresnelOffsetWeight + fc) - cavity x CurvatureOffsetWeight + fr) x Rainbow
  Fresnel Scale + Rainbow Fresnel Offset (cavity = 1 - sat(2 curvature)); three triangle bands
  max(0, 3(|frac(h - k/3) - 0.5| - 1/6))^ColorPower x RainbowColorA/B/C. Colour = lerp(lerp(base,
  UnderlyingColor, UnderlyingColorWeight), rainbow, fc x ColorFresnelIntensity), desaturated by Desaturate,
  x (1 - cavity x CurvatureDarken). Metal lerp(BaseMetalnessA/B by A, fr x RainbowMetalness, mask); roughness
  lerp(base roughness, cavity term around Roughness, mask).

* M_Diamond_Weapon (Diamond, Ruby, Emerald, crystal skins: WEAPON_CRYSTAL), approximate: crystal facets
  T_Crystals_01_NOH on plain UV2 / UV multiplier + uvoffset (the game parallax-marches it: left out);
  d = facet normal (flattened by CrystalFlatness) . Fakelightdirection, banding
  t = sat(|d|^4 + 0.75(cos(4 pi |d|)(u - 0.5) - sin(4 pi |d|)(v - 0.5))); crystal = lerp(Color2, Color1, t) x
  |d|^8 x ShimmerIntensity + DarknessBluenessAddition x InscatteringColor + NWO A x EmissiveColor x 0.5;
  cavities -> InscatteringColor (sat(2 curvature)), edges -> EdgeWearColor (sat(2(c - 0.5))^WearAmount).
  Emission = (FakeReflectionCubemap(reflection) x RelfectionAmount + crystal) x (0.34 + 0.96 (1 - N.V)^2)
  inside the mask. Outside the mask: BaseColorA/B like the fresnel master. The shimmer cells (animated
  Voronoi) and the parallax are left out.

ID mask (both): m = dot(Masks, IDMask) + (empty ID area) x UseBlackMask; mask = |m - InvertMask|.
"""
import math

from .common import _col

CRYSTALS = "/Game/Discovery/MaterialLibrary/Weapons/Indices/Patterns/XNO/T_Crystals_01_NOH.T_Crystals_01_NOH"


def _tex(ctx, name, **kw):
    from .weapon import _tex_ok
    path = _tex_ok(ctx.p, name)
    return ctx.tex(path, **kw) if path else None


def _id_mask(ctx):
    """Where the effect shows (1) vs the plain base material (0)."""
    p, nb = ctx.p, ctx.nb
    s = p.s
    masks = _tex(ctx, "Masks", data=True, packed=True, label="Masks (ID)")
    if masks is None:
        return 1.0
    w = p.v("IDMask", (0.0, 0.0, 0.0, 0.0))
    r, g, b = nb.separate(masks.outputs["Color"], label="ID channels")
    a = masks.outputs["Alpha"]
    m = nb.vmath("DOT_PRODUCT", masks.outputs["Color"], (w[0], w[1], w[2]))
    if len(w) > 3 and w[3]:
        m = nb.math("MULTIPLY_ADD", a, w[3], m)
    if s("UseBlackMask", 0.0):
        total = nb.math("ADD", nb.add(nb.add(r, g), nb.add(b, a)), 0.0, clamp=True)
        empty = nb.math("ROUND", nb.sub(1.0, total))
        m = nb.math("MULTIPLY_ADD", empty, s("UseBlackMask", 0.0), m)
    inv = s("InvertMask", 0.0)
    if inv:
        m = nb.math("MULTIPLY_ADD", nb.math("MULTIPLY_ADD", m, -2.0, 1.0), inv, m)
    return nb.math("ADD", m, 0.0, clamp=True, label="Effect mask")


def _nwo(ctx):
    """(NWO node or None, variation A, normal socket or None)."""
    nb = ctx.nb
    nwo = _tex(ctx, "NWO", data=True, packed=True, label="NWO")
    if nwo is None:
        return None, 0.5, None
    r, g, _b = nb.separate(nwo.outputs["Color"])
    return nwo, nwo.outputs["Alpha"], ctx.normal_map(nb.combine(r, g, 1.0))


def _curvature(ctx):
    t = _tex(ctx, "Curvature", data=True, label="Curvature")
    return ctx.red(t) if t is not None else 0.5


def _facing(ctx, normal):
    """sat(N . V) with the shading normal."""
    nb = ctx.nb
    geo = nb.node("ShaderNodeNewGeometry")
    n = normal if normal is not None else geo.outputs["Normal"]
    return nb.math("ADD", nb.vmath("DOT_PRODUCT", n, geo.outputs["Incoming"]), 0.0, clamp=True, label="N.V")


def _base(ctx, var, curv):
    """Plain base material outside the effect: colour, metal, roughness."""
    p, nb = ctx.p, ctx.nb
    s = p.s
    c = nb.mix(_col(p.v("BaseColorA", (0.1, 0.1, 0.1, 1))), _col(p.v("BaseColorB", (0.1, 0.1, 0.1, 1))), var,
               label="BaseColorA / B")
    k = s("BaseCurvatureColorWeight", 0.0)
    if k and not isinstance(curv, float):
        off = nb.mul(nb.sub(curv, 0.5), k)
        c = nb.mix(c, nb.combine(off, off, off), 1.0, "ADD", clamp_result=True, label="Curvature colour")
    metal = nb.lerp(s("BaseMetalnessA", 0.0), s("BaseMetalnessB", 0.0), var)
    rough = nb.lerp(s("BaseRoughnessA", 0.5), s("BaseRoughnessB", 0.5), var)
    kr = s("BaseCurvatureRoughnessWeight", 0.0)
    if kr and not isinstance(curv, float):
        rough = nb.math("MULTIPLY_ADD", nb.sub(curv, 0.5), -kr, rough, clamp=True)
    return c, metal, rough


def build_weapon_fresnel(ctx):
    """M_LegendaryWeaponFresnel (Platinum rank and other legendary fresnel skins); see the module docstring."""
    p, nb = ctx.p, ctx.nb
    s = p.s
    bsdf = ctx.bsdf()
    _nwo_node, var, normal = _nwo(ctx)
    curv = _curvature(ctx)
    mask = _id_mask(ctx)
    base, bmetal, brough = _base(ctx, var, curv)
    nv = _facing(ctx, normal)
    f = nb.math("POWER", nb.math("MAXIMUM", nb.sub(1.0, nv), 1e-4), 2.0, label="(1 - N.V)^2")
    fr = nb.math("POWER", f, s("Rainbow Fresnel Exponent", 1.0), label="Rainbow fresnel")
    ce = s("Color Fresnel Exponent", 1.0)
    fc = nb.math("POWER", f, abs(ce), label="Colour fresnel")
    if ce <= 0.0:
        fc = nb.sub(1.0, fc)
    cavity = nb.sub(1.0, nb.math("MULTIPLY", curv, 2.0, clamp=True)) if not isinstance(curv, float) else 0.0
    off = fc
    if p.sw("EnableOffsetTexture", False):
        u, v, _z = nb.separate(ctx.uv(detail=True))
        tl = s("FresnelOffsetTiling", 1.0)
        # the shader samples UV channel 2 as stored (v, u): transposed
        vec = nb.combine(nb.mul(nb.sub(1.0, v), tl), nb.sub(1.0, nb.mul(u, tl)), 0.0, label="Offset UV (transposed)")
        om = _tex(ctx, "OffsetMask", data=True, vector=vec, label="OffsetMask")
        if om is not None:
            off = nb.math("MULTIPLY_ADD", nb.sub(ctx.red(om), 0.5), s("FresnelOffsetWeight", 0.0), fc, clamp=True)
    h = nb.add(nb.math("MULTIPLY_ADD", cavity, -s("CurvatureOffsetWeight", 0.0), off), fr)
    h = nb.math("MULTIPLY_ADD", h, s("Rainbow Fresnel Scale", 1.0), s("Rainbow Fresnel Offset", 0.0), label="Hue")
    rainbow = None
    cp = s("ColorPower", 1.0)
    for k, name in enumerate(("RainbowColorA", "RainbowColorB", "RainbowColorC")):
        band = nb.math("FRACT", nb.sub(h, k / 3.0))
        band = nb.mul(nb.sub(nb.math("ABSOLUTE", nb.sub(band, 0.5)), 1.0 / 6.0), 3.0)
        band = nb.math("POWER", nb.math("MAXIMUM", band, 0.0), cp, label=f"Band {'ABC'[k]}")
        col = _col(p.v(name, (1, 1, 1, 1)))
        term = nb.mix((0, 0, 0, 1), col, band, label=name)
        rainbow = term if rainbow is None else nb.mix(rainbow, term, 1.0, "ADD", label="Rainbow")
    c = nb.mix(base, _col(p.v("UnderlyingColor", (0.5, 0.5, 0.5, 1))), s("UnderlyingColorWeight", 0.0),
               label="UnderlyingColor")
    c = nb.mix(c, rainbow, nb.mul(fc, s("ColorFresnelIntensity", 1.0)), label="Rainbow by colour fresnel")
    d = s("Desaturate", 0.0)
    if d:
        lum = nb.vmath("DOT_PRODUCT", c, (0.212639, 0.715169, 0.072192))
        c = nb.mix(c, nb.combine(lum, lum, lum), d, label="Desaturate")
    if not isinstance(cavity, float) and s("CurvatureDarken", 0.0):
        dk = nb.math("MULTIPLY_ADD", cavity, -s("CurvatureDarken", 0.0), 1.0)
        c = nb.mix(c, nb.combine(dk, dk, dk), 1.0, "MULTIPLY", label="Cavity darken")
    nb.feed(bsdf.inputs["Base Color"], nb.mix(base, c, mask, clamp_result=True, label="Effect mask"))
    metal = nb.lerp(bmetal, nb.mul(fr, s("RainbowMetalness", 0.0)), mask)
    nb.feed(bsdf.inputs["Metallic"], nb.math("ADD", metal, 0.0, clamp=True))
    rr = s("Roughness", 0.3)
    if not isinstance(cavity, float):
        fxr = nb.math("MULTIPLY_ADD", nb.mul(cavity, s("CurvatureRoughnessWeight", 0.0)),
                      nb.math("MULTIPLY_ADD", cavity, s("CavityRoughnessValue", 0.0), -rr), rr)
    else:
        fxr = rr
    nb.feed(bsdf.inputs["Roughness"], nb.math("ADD", nb.lerp(brough, fxr, mask), 0.0, clamp=True))
    if normal is not None:
        nb.feed(bsdf.inputs["Normal"], normal)
    ctx.finish(two_sided=True)


def _env_reflection(ctx, normal):
    """Fake reflection cubemap (exported as a lat-long .hdr) sampled by the reflection vector."""
    p, nb = ctx.p, ctx.nb
    path = p.t("FakeReflectionCubemap")
    file = ctx.finder.find(path, ".hdr") if path else ""
    if not file:
        return None
    import bpy
    img = bpy.data.images.load(file, check_existing=True)
    try:
        img.colorspace_settings.name = "Linear Rec.709"
    except TypeError:
        pass
    env = nb.node("ShaderNodeTexEnvironment", "FakeReflectionCubemap")
    env.image = img
    geo = nb.node("ShaderNodeNewGeometry")
    n = normal if normal is not None else geo.outputs["Normal"]
    # reflect(-I, N): -I + 2 (N.I) N
    ni = nb.vmath("DOT_PRODUCT", n, geo.outputs["Incoming"])
    refl = nb.vmath("SUBTRACT", nb.vmath("SCALE", n, scale=nb.mul(ni, 2.0)), geo.outputs["Incoming"],
                    label="Reflection")
    nb.feed(env.inputs["Vector"], refl)
    return env.outputs["Color"]


def build_weapon_crystal(ctx):
    """M_Diamond_Weapon (Diamond / Ruby / Emerald ranks, crystal skins); see the module docstring."""
    p, nb = ctx.p, ctx.nb
    s = p.s
    bsdf = ctx.bsdf()
    _nwo_node, var, normal = _nwo(ctx)
    curv = _curvature(ctx)
    mask = _id_mask(ctx)                      # 1 = plain base material (crystal outside it)
    base, bmetal, brough = _base(ctx, var, curv)
    # crystal facets on plain UV2 / UV multiplier + uvoffset (UE space)
    um = s("UV multiplier", 1.0) or 1.0
    uvo = p.v("uvoffset", (0.0, 0.0, 0.0, 0.0))
    u, v, _z = nb.separate(ctx.uv(detail=True))
    vg = nb.sub(1.0, v)
    cu = nb.math("MULTIPLY_ADD", u, 1.0 / um, uvo[0])
    cv = nb.math("MULTIPLY_ADD", vg, 1.0 / um, uvo[1])
    facets = ctx.tex(CRYSTALS, data=True, vector=nb.combine(cu, nb.sub(1.0, cv), 0.0, label="Crystal UV"),
                     label="T_Crystals_01_NOH")
    crystal = _col(p.v("Color2", (0.5, 0.5, 0.5, 1)))
    crystal_normal = None
    if facets is not None:
        fr_, fg_, _fb = nb.separate(facets.outputs["Color"])
        x = nb.math("MULTIPLY_ADD", fr_, 2.0, -1.0)
        y = nb.math("MULTIPLY_ADD", fg_, 2.0, -1.0)
        z = nb.math("SQRT", nb.math("MAXIMUM", nb.sub(1.0, nb.add(nb.mul(x, x), nb.mul(y, y))), 0.0))
        fl = s("CrystalFlatness", 0.0)
        # shading normal of the crystal: the facets flattened by 0.9 (the game's G-buffer normal)
        crystal_normal = ctx.normal_map(nb.combine(nb.math("MULTIPLY_ADD", x, 0.05, 0.5),
                                                   nb.math("MULTIPLY_ADD", y, 0.05, 0.5),
                                                   nb.math("MULTIPLY_ADD", z, 0.05, 0.95)))
        x, y = nb.mul(x, -(1.0 - fl)), nb.mul(y, -(1.0 - fl))
        z = nb.math("MULTIPLY_ADD", nb.sub(1.0, z), fl, z)
        n = nb.vmath("NORMALIZE", nb.combine(x, y, z))
        ld = p.v("Fakelightdirection", (0.0, 0.0, 1.0, 1.0))
        ll = math.sqrt(sum(c * c for c in ld[:3])) or 1.0
        d = nb.math("ABSOLUTE", nb.vmath("DOT_PRODUCT", n, (ld[0] / ll, ld[1] / ll, ld[2] / ll)), label="|d|")
        ang = nb.mul(d, 4.0 * math.pi)
        wave = nb.sub(nb.mul(nb.math("COSINE", ang), nb.sub(u, 0.5)), nb.mul(nb.math("SINE", ang), nb.sub(vg, 0.5)))
        d4 = nb.math("POWER", d, 4.0)
        t = nb.math("MULTIPLY_ADD", wave, 0.75, d4, clamp=True, label="Crystal bands")
        crystal = nb.mix(_col(p.v("Color2", (0.5, 0.5, 0.5, 1))), _col(p.v("Color1", (0.2, 0.2, 0.2, 1))), t,
                         label="Color2 → Color1")
        k = nb.mul(nb.mul(d4, d4), s("ShimmerIntensity", 1.0))
        crystal = nb.mix(crystal, nb.combine(k, k, k), 1.0, "MULTIPLY", label="x |d|^8 x ShimmerIntensity")
    ins = p.v("InscatteringColor", (0.0, 0.0, 0.0, 1.0))
    db = s("DarknessBluenessAddition", 0.0)
    crystal = nb.mix(crystal, (ins[0] * db, ins[1] * db, ins[2] * db, 1.0), 1.0, "ADD", label="Inscattering")
    em = p.v("EmissiveColor", (0.0, 0.0, 0.0, 1.0))
    if max(em[:3]) > 0.0 and not isinstance(var, float):
        glow = nb.mix((0, 0, 0, 1), (em[0] * 0.5, em[1] * 0.5, em[2] * 0.5, 1.0), var)
        crystal = nb.mix(crystal, glow, 1.0, "ADD", label="Emissive tint")
    if not isinstance(curv, float):
        crystal = nb.mix(_col(ins), crystal, nb.math("MULTIPLY", curv, 2.0, clamp=True), label="Cavities")
        edge = nb.math("POWER", nb.math("MULTIPLY", nb.sub(curv, 0.5), 2.0, clamp=True), s("WearAmount", 1.0))
        crystal = nb.mix(crystal, _col(p.v("EdgeWearColor", (1, 1, 1, 1))), edge, label="Edge wear")
    crystal = nb.mix(crystal, (0, 0, 0, 1), 0.0, clamp_result=True)
    nb.feed(bsdf.inputs["Base Color"], nb.mix(crystal, base, mask, label="Base outside the crystal"))
    nb.feed(bsdf.inputs["Metallic"], nb.lerp(0.0, bmetal, mask))
    crough = s("RoughnessMultiply", 0.25)
    nb.feed(bsdf.inputs["Roughness"], nb.math("ADD", nb.lerp(crough, brough, mask), 0.0, clamp=True))
    if crystal_normal is not None:
        if normal is not None:
            normal = nb.vmath("NORMALIZE", nb.mix(crystal_normal, normal, mask, label="Crystal / base normal"))
        else:
            normal = crystal_normal
    if normal is not None:
        nb.feed(bsdf.inputs["Normal"], normal)
    # emission: reflections + crystal colour, brighter at grazing angles, inside the crystal only
    nv = _facing(ctx, normal)
    g = nb.math("MULTIPLY_ADD", nb.math("POWER", nb.math("MAXIMUM", nb.sub(1.0, nv), 1e-4), 2.0), 0.96, 0.34)
    # x ~1.5: the shader's exposure term (min(1 / pre-exposure, 1) + 0.5)
    g = nb.mul(nb.math("MINIMUM", g, 1.0), nb.mul(nb.sub(1.0, mask), 1.5) if not isinstance(mask, float) else 1.5 * (1.0 - mask))
    refl = _env_reflection(ctx, normal)
    glow = crystal
    if refl is not None:
        ra = s("RelfectionAmount", 0.5)
        glow = nb.mix(crystal, nb.mix(refl, (ra, ra, ra, 1.0), 1.0, "MULTIPLY"), 1.0, "ADD", label="Reflections")
    nb.feed(bsdf.inputs["Emission Color"], glow)
    nb.feed(bsdf.inputs["Emission Strength"], g)
    ctx.finish(two_sided=True)
