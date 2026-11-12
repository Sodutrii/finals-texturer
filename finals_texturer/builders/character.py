"""Character shaders (Character tab): body skin, face, eyes, eyelashes, teeth,
hair cards (eyebrows / hair styles), glitter skin, hidden eye shells."""

from .common import _col, _packed_normal, _alpha_range, _hsv_norm

def build_skin(ctx):
    """M_Skin (the bare character body).

    The body mesh's UVs use two tiles: U 0..1 = torso/legs (BodyColorMap,
    BodyNormalMap), U 1..2 = arms/hands (HandsColorMap, HandsNormalMap).
    The textures repeat, so each tile samples its own map directly.
    Colour x ColorMultiply. Tattoo/body-paint overlays (blank by default),
    hide-part masks and the spawn dissolve are not built.
    """
    p, nb = ctx.p, ctx.nb
    bsdf = ctx.bsdf()
    u = nb.separate(ctx.uv(), label="UV")[0]
    hands = nb.math("GREATER_THAN", u, 1.0, label="Arms/hands tile (U > 1)")

    def pick(body_name, hands_name, data, label):
        b = ctx.tex(p.t(body_name), data=data, label=body_name)
        h = ctx.tex(p.t(hands_name), data=data, label=hands_name)
        if b is None and h is None:
            return None
        if b is None or h is None:
            return (b or h).outputs["Color"]
        return nb.mix(b.outputs["Color"], h.outputs["Color"], hands, label=label)

    color = pick("BodyColorMap", "HandsColorMap", False, "Body / hands colour")
    tint = _col(p.v("ColorMultiply", (1, 1, 1, 1)))
    # SkinDesaturation greys the skin texture, then ColorMultiply tints it
    # (the other way round, green/blue skins like Face_21/17 came out grey).
    desat = max(0.0, min(1.0, p.s("SkinDesaturation", 0.0)))
    if color is not None and desat > 0.0:
        grey = nb.node("ShaderNodeRGBToBW", "Luminance")
        nb.feed(grey.inputs[0], color)
        g = grey.outputs[0]
        color = nb.mix(color, nb.combine(g, g, g), desat, label=f"SkinDesaturation {desat:g}")
    col = nb.mix(color, tint, 1.0, "MULTIPLY", label="ColorMultiply") if color is not None else tint
    nb.feed(bsdf.inputs["Base Color"], col)
    normal = pick("BodyNormalMap", "HandsNormalMap", True, "Body / hands normals")
    if normal is not None:
        nb.feed(bsdf.inputs["Normal"], ctx.normal_map(normal))
    # Roughness (compiled M_Skin, 2026-11-12): Roughness x lerp(normal map alpha, 0.8, Schlick
    # fresnel 0.04 + 0.96 (1 - N.V)^5); a flat Roughness came out ~25 % too rough.
    rough = max(0.0, p.s("Roughness", 0.6))
    nb_ = ctx.tex(p.t("BodyNormalMap"), data=True, label="BodyNormalMap")
    nh_ = ctx.tex(p.t("HandsNormalMap"), data=True, label="HandsNormalMap")
    if nb_ is not None or nh_ is not None:
        if nb_ is None or nh_ is None:
            na = (nb_ or nh_).outputs["Alpha"]
        else:
            na = nb.lerp(nb_.outputs["Alpha"], nh_.outputs["Alpha"], hands)
        lw = nb.node("ShaderNodeLayerWeight", "Facing (1 - N.V)")
        nb.feed(lw.inputs["Blend"], 0.5)
        fres = nb.math("MULTIPLY_ADD", nb.math("POWER", lw.outputs["Facing"], 5.0), 0.96, 0.04, label="Schlick fresnel")
        nb.feed(bsdf.inputs["Roughness"], nb.mul(nb.lerp(na, 0.8, fres), rough, clamp=True))
    else:
        nb.feed(bsdf.inputs["Roughness"], min(1.0, rough * 0.8))
    nb.feed(bsdf.inputs["Specular IOR Level"], max(0.0, min(1.0, p.s("Specular", 0.5))))
    ctx.finish(two_sided=bool(p.two_sided))


# ─────────────────────────────────────────────────────────────────────────────
# Heads (Character tab). Store images for faces weren't exported when these
# were written, so the eye and eyelash setups are approximations.
# ─────────────────────────────────────────────────────────────────────────────

def build_face(ctx):
    """M_Face (head skin): BaseColor, Normal, roughness Zone_Roughness x
    RoughnessMultiplier, Specular. Not built: the stubble hair-card layer
    (Hair NXA), cavity/zone masks, tattoos and face paint."""
    p, nb = ctx.p, ctx.nb
    bsdf = ctx.bsdf()
    c = ctx.tex(p.t("BaseColor"), data=False, label="BaseColor")
    nb.feed(bsdf.inputs["Base Color"], c.outputs["Color"] if c is not None else (0.6, 0.45, 0.4, 1.0))
    # Face normal maps keep the normal in R/G only; B holds something else
    # (vector length ~0.2-0.4 if read as XYZ, which gave dark blotches).
    # Rebuilding Z from R/G is also right for ordinary normal maps.
    n = ctx.tex(p.t("Normal"), data=True, packed=True, label="Normal (R/G normal, B roughness)")
    if n is not None:
        nb.feed(bsdf.inputs["Normal"], _packed_normal(ctx, n)[0])
    _face_surface(ctx, bsdf, n, c)
    # The head mesh reaches over the shoulders and fades out into the body:
    # M_Face's fixed T_Face_NeckFade (white = head, black at the UV's bottom
    # edge) x FadeAmount is the opacity (dithered in game, hashed here).
    if p.s("NeckFadeEnabled", 1.0) > 0.5:
        fade = ctx.tex("/Game/Discovery/Characters/Heads/Shared/Textures/T_Face_NeckFade.T_Face_NeckFade",
                       data=True, label="Neck fade (M_Face built-in)", extension="EXTEND")
        if fade is not None:
            # Masked material: the game discards everything under the clip
            # value (0.333). The fade only reaches ~0.2-0.3 at the mesh's
            # border, so without the clip the head's edge shows as a patch on
            # the shoulders. Soft ramp above the clip instead of UE's dither.
            clip = p.opacity_clip or 0.3333
            amount = nb.mul(ctx.red(fade), p.s("FadeAmount", 1.0))
            nb.feed(bsdf.inputs["Alpha"], nb.map_range(amount, clip, min(clip + 0.35, 1.0), 0.0, 1.0,
                                                       label=f"Opacity clip {clip:g} (soft)"))
    ctx.finish(two_sided=bool(p.two_sided))


HEAD_TEX = "/Game/Discovery/Characters/Heads/Shared/Textures/"


def _face_surface(ctx, bsdf, normal, color):
    """M_Face roughness and specular, from the compiled shader (2026-11-12):
      rough = normal B, capped by lerp(Zone_Roughness, 1, zone mask G) and on the lips
              (Mask Texture B = 0) by 1 - Lip Roughness; x RoughnessMultiplier
              + (1 - BaseColor A) x Cavity Roughness + (1 - micro) x Micro Cavity Roughness,
              micro = max(micro-normal B, 1 - Mask B); → Hair Roughness under the stubble mask
      spec  = lerp(LipSpecular, Specular, Mask B) x (1 + (BaseColor A - 1) x Specular Cavity Amount)
              x (1 + (micro - 1) x Specular MicroCavity Amount); → Hair Specular under the stubble
    The view-dependent fresnel terms (face fresnel x Mask R) are left out."""
    p, nb = ctx.p, ctx.nb
    one = lambda x: x if x is not None else 1.0
    nb_b = nb.separate(normal.outputs["Color"])[2] if normal is not None else 0.5
    mask = ctx.tex(p.t("Mask Texture"), data=True, label="Mask Texture (R fresnel, G subsurface, B not-lips)")
    mask_b = nb.separate(mask.outputs["Color"])[2] if mask is not None else 1.0
    zone = ctx.tex(HEAD_TEX + "T_ContestantHeads_ZONEMASKS.T_ContestantHeads_ZONEMASKS", data=True,
                   label="Zone masks (G)")
    zone_g = nb.separate(zone.outputs["Color"])[1] if zone is not None else 0.0
    cav = color.outputs["Alpha"] if color is not None else 1.0
    micro_t = ctx.tex(HEAD_TEX + "Head_01_MicroNormal.Head_01_MicroNormal", data=True,
                      scale=p.s("Tile_Size", 1.0) or 1.0, label="Micro normal (B micro cavity)")
    micro_b = nb.separate(micro_t.outputs["Color"])[2] if micro_t is not None else 1.0
    micro = nb.math("MAXIMUM", micro_b, nb.sub(1.0, mask_b))
    hair_t = ctx.tex(p.t("Hair NXA"), data=True, packed=True, label="Hair NXA (stubble, A = mask)")
    hair = nb.mul(hair_t.outputs["Alpha"], p.s("Hair Mask Amount", 1.0), clamp=True) if hair_t is not None else 0.0

    zr = p.s("Zone_Roughness", 0.55)
    rough = nb.math("MINIMUM", nb_b, nb.lerp(zr, 1.0, zone_g))
    rough = nb.math("MINIMUM", rough, nb.math("MAXIMUM", mask_b, 1.0 - p.s("Lip Roughness", 0.0)))
    rough = nb.math("MULTIPLY_ADD", rough, p.s("RoughnessMultiplier", 1.0),
                    nb.mul(nb.sub(1.0, cav), p.s("Cavity Roughness", 0.0)))
    rough = nb.math("MULTIPLY_ADD", nb.sub(1.0, micro), p.s("Micro Cavity Roughness", 0.0), rough)
    if hair != 0.0:
        rough = nb.lerp(rough, p.s("Hair Roughness", 0.5), hair)
    nb.feed(bsdf.inputs["Roughness"], nb.clamp01(rough))

    spec = nb.lerp(p.s("LipSpecular", 0.5), p.s("Specular", 0.5), mask_b)
    spec = nb.mul(spec, nb.math("MULTIPLY_ADD", nb.sub(cav, 1.0), p.s("Specular Cavity Amount", 0.0), 1.0))
    spec = nb.mul(spec, nb.math("MULTIPLY_ADD", nb.sub(micro, 1.0), p.s("Specular MicroCavity Amount", 0.0), 1.0))
    if hair != 0.0:
        spec = nb.lerp(spec, p.s("Hair Specular", 0.5), hair)
    nb.feed(bsdf.inputs["Specular IOR Level"], nb.clamp01(spec))


GREY_IRIS = ("/Game/Discovery/Characters/Heads/Shared/Eyes/Textures/"
             "T_EyeIrisBaseColor_Gray_01_CA.T_EyeIrisBaseColor_Gray_01_CA")


def build_eye(ctx):
    """M_EyeRefractive_2 (eyeball), approximated without refraction.

    The iris is a disc of radius "Iris UV Radius" around UV (0.5, 0.5); the
    iris texture is scaled so its full square covers that disc. The edge
    (limbus) blends over LimbusUVWidthColor. Iris = IrisColor x IrisHueTint
    x IrisBrightness; sclera = ScleraColor x ScleraTint x ScleraBrightness.
    A clear coat stands in for the wet cornea.
    """
    p, nb = ctx.p, ctx.nb
    bsdf = ctx.bsdf()
    radius = max(p.s("Iris UV Radius", 0.15), 0.01)
    width = p.s("LimbusUVWidthColor", 0.05)
    uv = ctx.uv()
    centred = nb.vmath("SUBTRACT", uv, (0.5, 0.5, 0.0), label="UV around centre")
    dist = nb.vmath("LENGTH", centred, label="Distance from centre")
    iris_mask = nb.map_range(dist, radius - width * 0.5, radius + width * 0.5, 1.0, 0.0,
                             mode="SMOOTHSTEP", label="Iris mask")
    iris_uv = nb.vmath("ADD", nb.vmath("SCALE", centred, scale=0.5 / radius), (0.5, 0.5, 0.0),
                       label="Iris UV")
    sclera = ctx.tex(p.t("ScleraColor"), data=False, label="ScleraColor")
    # Stand-in when the sclera texture isn't exported: a mid value, since
    # ScleraBrightness (often 2) multiplies it up to an off-white.
    sclera_c = sclera.outputs["Color"] if sclera is not None else (0.36, 0.35, 0.34, 1.0)
    sclera_c = nb.mix(sclera_c, _col(p.v("ScleraTint", (1, 1, 1, 1))), 1.0, "MULTIPLY", label="ScleraTint")
    sclera_c = nb.mix(sclera_c, (p.s("ScleraBrightness", 1.0),) * 3 + (1.0,), 1.0, "MULTIPLY",
                      label="ScleraBrightness", clamp_result=True)
    iris = ctx.tex(p.t("IrisColor"), data=False, label="IrisColor", vector=iris_uv, extension="EXTEND")
    if iris is None:
        # The default iris (T_EyeIrisBaseColor_Example_2) sits under an old
        # /Game/Pioneer path that FModel exports don't contain; use the
        # game's grey iris (meant for tinting) instead when it's exported.
        iris = ctx.tex(GREY_IRIS, data=False, label="Stand-in iris (grey, tinted)", vector=iris_uv,
                       extension="EXTEND")
    if iris is not None:
        iris_c = iris.outputs["Color"]
    else:
        # The default iris texture sits under an old /Game/Pioneer path that
        # isn't exported: a mid-grey iris with fine radial fibres, a pupil
        # (PupilScale) and a dark rim (LimbusPow / LimbusDarkScale) instead.
        rel = nb.math("DIVIDE", dist, radius, label="0 centre .. 1 iris edge")
        ang = nb.math("ARCTAN2", nb.separate(centred)[1], nb.separate(centred)[0], label="Angle")
        fib = nb.node("ShaderNodeTexNoise", "Iris fibres")
        fib.inputs["Scale"].default_value = 1.0
        nb.feed(fib.inputs["Vector"], nb.combine(nb.mul(ang, 12.0), nb.mul(rel, 1.5), 0.0))
        fibres = nb.map_range(fib.outputs["Fac"], 0.3, 0.7, 0.7, 1.15, label="Fibre brightness")
        iris_c = nb.mix((0.42, 0.42, 0.42, 1.0), (1.0, 1.0, 1.0, 1.0), 1.0, "MULTIPLY")
        iris_c = nb.mix(iris_c, nb.combine(fibres, fibres, fibres), 1.0, "MULTIPLY", label="Fibres")
        rim = nb.math("MULTIPLY", nb.math("POWER", rel, max(p.s("LimbusPow", 6.0), 1.0)),
                      min(p.s("LimbusDarkScale", 2.0) * 0.35, 1.0), clamp=True, label="Limbus darkening")
        iris_c = nb.mix(iris_c, (0.02, 0.015, 0.01, 1.0), rim, label="Limbus")
        pupil_r = 0.42 * p.s("PupilScale", 0.7)
        pupil = nb.map_range(rel, pupil_r - 0.04, pupil_r + 0.04, 1.0, 0.0, mode="SMOOTHSTEP", label="Pupil")
        iris_c = nb.mix(iris_c, (0.005, 0.005, 0.005, 1.0), pupil, label="Pupil (PupilScale)")
    iris_c = nb.mix(iris_c, _col(p.v("IrisHueTint", (1, 1, 1, 1))), 1.0, "MULTIPLY", label="IrisHueTint")
    iris_c = nb.mix(iris_c, (p.s("IrisBrightness", 1.0),) * 3 + (1.0,), 1.0, "MULTIPLY",
                    label="IrisBrightness", clamp_result=True)
    nb.feed(bsdf.inputs["Base Color"], nb.mix(sclera_c, iris_c, iris_mask, label="Sclera / iris"))
    nb.feed(bsdf.inputs["Roughness"], max(0.0, min(1.0, p.s("ScleraRoughness", 0.2))))
    nb.feed(bsdf.inputs["Coat Weight"], 1.0)
    nb.feed(bsdf.inputs["Coat Roughness"], 0.03)
    ctx.finish(two_sided=False)


def build_eyelash(ctx):
    """M_EyelashMaster: Eyelash Color, cut out by the Eyelash Texture (its
    alpha if it has one, else its red channel) x Lash Opacity."""
    p, nb = ctx.p, ctx.nb
    bsdf = ctx.bsdf()
    nb.feed(bsdf.inputs["Base Color"], _col(p.v("Eyelash Color", (0.02, 0.012, 0.006, 1))))
    # compiled M_EyelashMaster: roughness 0.5, no specular
    nb.feed(bsdf.inputs["Roughness"], 0.5)
    nb.feed(bsdf.inputs["Specular IOR Level"], 0.0)
    t = ctx.tex(p.t("Eyelash Texture"), data=True, label="Eyelash Texture")
    if t is not None:
        rng = _alpha_range(t.image)
        cover = t.outputs["Alpha"] if rng and rng[0] < 0.99 else ctx.red(t)
        nb.feed(bsdf.inputs["Alpha"], nb.mul(cover, p.s("Lash Opacity", 1.0), clamp=True))
    # Lashes are hair-thin strands: dithered transparency breaks them up into
    # noise at normal render sizes, blended keeps them as soft dark lines.
    ctx.finish(two_sided=True, blended=True)


def build_teeth(ctx):
    """M_Teeth: Color x Tint: Teeth, Normal, Roughness, Specular. Gum and
    tongue tints (white by default) and the grunge layers aren't built."""
    p, nb = ctx.p, ctx.nb
    bsdf = ctx.bsdf()
    tint = _col(p.v("Tint: Teeth", (1, 1, 1, 1)))
    c = ctx.tex(p.t("Color"), data=False, label="Color")
    nb.feed(bsdf.inputs["Base Color"],
            nb.mix(c.outputs["Color"], tint, 1.0, "MULTIPLY", label="Tint: Teeth") if c is not None else tint)
    n = ctx.tex(p.t("Normal"), data=True, label="Normal")
    if n is not None:
        nb.feed(bsdf.inputs["Normal"], ctx.normal_map(n.outputs["Color"]))
    nb.feed(bsdf.inputs["Roughness"], max(0.0, min(1.0, p.s("Roughness", 0.3))))
    nb.feed(bsdf.inputs["Specular IOR Level"], max(0.0, min(1.0, p.s("Specular", 0.4))))
    ctx.finish(two_sided=bool(p.two_sided))


def build_hair_cards(ctx):
    """M_Hair_Metahuman_01 hair cards (eyebrows on some faces, hair styles).

    Opacity = Coverage R (strands peak around 0.7) scaled up so single
    strands read, blended like eyelashes. Colour varies per strand between
    ColorA and ColorB by the attribute map's B (per-strand random value).
    Roughness = 'Roughness min'. Approximation: no root/tip gradient,
    scatter or the MetaHuman hair shading model.
    """
    p, nb = ctx.p, ctx.nb
    bsdf = ctx.bsdf()
    a = _col(p.v("ColorA", (0.05, 0.03, 0.02, 1)))
    b = _col(p.v("ColorB", (0.02, 0.015, 0.01, 1)))
    attr = ctx.tex(p.t("AttributeMap"), data=True, packed=True, label="AttributeMap (B = strand seed)")
    if attr is not None:
        seed = nb.separate(attr.outputs["Color"])[2]
        color = nb.mix(a, b, seed, label="ColorA / ColorB per strand")
    else:
        color = nb.mix(a, b, 0.5)
    nb.feed(bsdf.inputs["Base Color"], color)
    nb.feed(bsdf.inputs["Roughness"], max(0.0, min(1.0, p.s("Roughness min", 0.4))))
    cov = ctx.tex(p.t("Coverage"), data=True, label="Coverage (R)")
    if cov is not None:
        nb.feed(bsdf.inputs["Alpha"], nb.mul(ctx.red(cov), 2.0, clamp=True))
    ctx.finish(two_sided=True, blended=True)


GLITTER_TEX = ("/Game/Discovery/MaterialLibrary/Character/CustomMaterials/Textures/"
               "T_Glitter_01_X.T_Glitter_01_X")


def build_glitter(ctx):
    """M_Skin_Glitter (BlankFace AbsorbeGlitter head and body), recreated from
    the compiled shader (2026-11-12):
    * base colour 0.02 grey; T_Glitter_01_X on UV1 x Tiling (R/G flake normals);
    * flake seed = frac(A - 0.2 B) (the game also scrolls it with time, making
      the flakes twinkle); sparkle m = min(seed^2 x (1 - N.V)^4, 1);
    * metallic 0.8 → 1 and roughness 0.2 → 0.01 by m;
    * emission = palette(3 seed: ColorA → B → C → D) x m x EmissiveIntensity
      + a thin rim highlight near the silhouette on upward-facing surfaces,
      colour (5 (0.733 - 0.727 x), 5 (0.184 - 0.018 x), 4.77) with x = the
      view-space normal's X (pink on one side, blue on the other).
    The previous hand-tuned look (pitch black, clear coat, specks x3 smaller)
    is in git history (0.10.0) if this one looks worse."""
    p, nb = ctx.p, ctx.nb
    bsdf = ctx.bsdf()
    tiling = p.s("Tiling", 4.0) or 4.0
    nb.feed(bsdf.inputs["Base Color"], (0.02, 0.02, 0.02, 1.0))
    nb.feed(bsdf.inputs["Specular IOR Level"], 0.5)
    tex = ctx.tex(GLITTER_TEX, data=True, packed=True, scale=tiling, label=f"T_Glitter_01_X (UV x {tiling:g})")
    if tex is None:
        nb.feed(bsdf.inputs["Metallic"], 0.8)
        nb.feed(bsdf.inputs["Roughness"], 0.2)
        ctx.finish(two_sided=bool(p.two_sided))
        return
    # flake normal x/y = 0.4 (R/G - 0.5) in game = strength 0.2 (0.4 was twice as bumpy: noisy reflections)
    nb.feed(bsdf.inputs["Normal"], _packed_normal(ctx, tex, strength=0.2)[0])
    # seed from an unfiltered copy: mipmapped averages blur the per-flake seeds away with distance
    raw = ctx._image_node(ctx.finder.find_texture(GLITTER_TEX), True, True, "T_Glitter_01_X (unfiltered, seeds)",
                          "REPEAT")
    raw.interpolation = "Closest"
    nb.feed(raw.inputs["Vector"], ctx.uv(tiling))
    _r, _g, blue = nb.separate(raw.outputs["Color"])
    seed = nb.math("FRACT", nb.math("MULTIPLY_ADD", blue, -0.2, raw.outputs["Alpha"]), label="Flake seed")
    lw = nb.node("ShaderNodeLayerWeight", "Facing (1 - N.V)")
    nb.feed(lw.inputs["Blend"], 0.5)
    x = lw.outputs["Facing"]
    m = nb.math("MINIMUM", nb.mul(nb.mul(seed, seed), nb.math("POWER", x, 4.0)), 1.0, label="Sparkle")
    nb.feed(bsdf.inputs["Metallic"], nb.math("MULTIPLY_ADD", m, 0.2, 0.8))
    # game: 0.2 → 0.01; 0.12 here because Blender keeps the flake bumps sharp where the game blurs them
    # with distance (user: "a bit more glossy" between the flakes)
    nb.feed(bsdf.inputs["Roughness"], nb.math("MULTIPLY_ADD", m, -0.11, 0.12))
    t = nb.mul(seed, 3.0)
    pal = nb.mix(_col(p.v("ColorA", (0.001, 0.001, 0.01, 1))), _col(p.v("ColorB", (0.074, 0.017, 0.15, 1))),
                 nb.clamp01(t), label="ColorA → B")
    pal = nb.mix(pal, _col(p.v("ColorC", (0.037, 0.127, 0.426, 1))), nb.clamp01(nb.sub(t, 1.0)), label="→ C")
    pal = nb.mix(pal, _col(p.v("ColorD", (1.778, 1.271, 2.0, 1))), nb.clamp01(nb.sub(t, 2.0)), label="→ D")
    em = nb.vmath("SCALE", pal, scale=nb.mul(m, max(p.s("EmissiveIntensity", 5.0), 0.0)), label="Flake emission")
    # rim: min((2.5 x (1 - N.V)^0.53 x f)^64 x 100, 1) x upward-facing, f = 1 - N.V
    ndv = nb.sub(1.0, x)
    f = nb.mul(nb.mul(x, nb.math("POWER", ndv, 0.53)), 2.5)
    rim = nb.math("MINIMUM", nb.mul(nb.math("POWER", f, 64.0), 100.0), 1.0)
    geo = nb.node("ShaderNodeNewGeometry")
    _nx, _ny, nz = nb.separate(geo.outputs["Normal"])
    rim = nb.math("MULTIPLY", rim, nb.clamp01(nz), label="Rim (upward-facing)")
    vt = nb.node("ShaderNodeVectorTransform", "View-space normal", vector_type="NORMAL",
                 convert_from="WORLD", convert_to="CAMERA")
    nb.feed(vt.inputs[0], geo.outputs["Normal"])
    vx = nb.separate(vt.outputs[0])[0]
    rim_col = nb.combine(nb.math("MULTIPLY_ADD", vx, -3.635, 3.665), nb.math("MULTIPLY_ADD", vx, -0.09, 0.92), 4.77,
                         label="Rim colour (pink ↔ blue)")
    em = nb.vmath("ADD", em, nb.vmath("SCALE", rim_col, scale=rim), label="Flakes + rim")
    nb.feed(bsdf.inputs["Emission Color"], em)
    nb.feed(bsdf.inputs["Emission Strength"], 1.0)
    ctx.finish(two_sided=bool(p.two_sided))


def build_hidden(ctx):
    """Eye shadow shell (M_EyeOcclusion) and wet eye edge (M_lacrimal_fluid):
    thin shells over the eyes that only add soft shading in game. Rendered as
    plain shells they'd cover the eyes, so they're left fully transparent."""
    nb = ctx.nb
    t = nb.node("ShaderNodeBsdfTransparent")
    out = nb.node("ShaderNodeOutputMaterial")
    nb.feed(out.inputs["Surface"], t.outputs[0])
    ctx.finish(two_sided=True, blended=True)
