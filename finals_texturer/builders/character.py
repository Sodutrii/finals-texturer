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
    nb.feed(bsdf.inputs["Roughness"], max(0.0, min(1.0, p.s("Roughness", 0.6))))
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
    n = ctx.tex(p.t("Normal"), data=True, packed=True, label="Normal (R/G; B unused)")
    if n is not None:
        nb.feed(bsdf.inputs["Normal"], _packed_normal(ctx, n)[0])
    rough = p.s("Zone_Roughness", 0.55) * p.s("RoughnessMultiplier", 1.0)
    nb.feed(bsdf.inputs["Roughness"], max(0.0, min(1.0, rough)))
    nb.feed(bsdf.inputs["Specular IOR Level"], max(0.0, min(1.0, p.s("Specular", 0.5))))
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
    nb.feed(bsdf.inputs["Roughness"], 0.6)
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
    """M_Skin_Glitter (BlankFace AbsorbeGlitter head and body), approximated.

    From in-game screenshots and the user's notes (2026-11-11): the skin is
    pitch black everywhere, very glossy; the only colour comes from
    reflections and from tiny specks near the silhouette (mostly whitish
    ColorD, some ColorB / ColorC), scaled by EmissiveIntensity.
    The built-in T_Glitter_01_X (R/G flake normals, B smudge, A per-flake
    seed) tiles over the **UVs** x Tiling: the head (4) and body (8) values
    are set so one tile is ~11 cm on both (46 vs 88 cm per UV unit).
    x3 on top makes the specks as small as in game (~1 mm).
    """
    p, nb = ctx.p, ctx.nb
    bsdf = ctx.bsdf()
    tiling = (p.s("Tiling", 4.0) or 4.0) * 3.0
    tex = ctx.tex(GLITTER_TEX, data=True, packed=True, scale=tiling, label=f"T_Glitter_01_X (UV x {tiling:g})")
    nb.feed(bsdf.inputs["Base Color"], (0.0, 0.0, 0.0, 1.0))      # pitch black in game
    nb.feed(bsdf.inputs["Roughness"], 0.06)
    nb.feed(bsdf.inputs["Specular IOR Level"], 1.0)
    nb.feed(bsdf.inputs["Coat Weight"], 1.0)
    nb.feed(bsdf.inputs["Coat Roughness"], 0.0)
    nb.feed(bsdf.inputs["Coat IOR"], 1.6)
    if tex is None:
        ctx.finish(two_sided=bool(p.two_sided))
        return
    flake_n, smudge = _packed_normal(ctx, tex, strength=0.25)
    nb.feed(bsdf.inputs["Normal"], flake_n)
    # Specks = the brightest per-flake seeds. With smooth filtering the GPU
    # blends far-away texels (mipmaps), the seed never reaches the threshold
    # and the specks vanish with distance, at a different distance on the
    # arms (another UV scale) with a visible line. Unfiltered sampling keeps
    # every speck at any distance, like glitter sparkling in game.
    raw = ctx._image_node(ctx.finder.find_texture(GLITTER_TEX), True, True, "T_Glitter_01_X (unfiltered, specks)",
                          "REPEAT")
    raw.interpolation = "Closest"
    nb.feed(raw.inputs["Vector"], ctx.uv(tiling))
    seed = raw.outputs["Alpha"]
    smudge = nb.separate(raw.outputs["Color"])[2]
    view = nb.node("ShaderNodeLayerWeight", "Facing")
    view.inputs["Blend"].default_value = 0.5
    edge = nb.map_range(view.outputs["Facing"], 0.55, 0.88, 0.0, 1.0, mode="SMOOTHSTEP", label="Toward the edges")
    speck = nb.map_range(seed, 0.86, 0.93, 0.0, 1.0, label="Sparse specks")
    # even a few % shows against pitch black: the front stays almost clean
    speck = nb.math("MULTIPLY", speck, edge, label="Toward the edges only")
    tint = nb.mix(_hsv_norm(nb, _col(p.v("ColorB", (0.07, 0.02, 0.15, 1))), 1.0, label="ColorB hue"),
                  _hsv_norm(nb, _col(p.v("ColorC", (0.04, 0.13, 0.43, 1))), 1.0, label="ColorC hue"),
                  smudge, label="ColorB / ColorC per speck")
    white = _hsv_norm(nb, _col(p.v("ColorD", (1.78, 1.27, 2.0, 1))), 1.0, sat_scale=0.3, label="ColorD (whitish)")
    speck_col = nb.mix(tint, white, nb.map_range(smudge, 0.2, 0.5, 0.0, 1.0), label="Most specks whitish, some tinted")
    nb.feed(bsdf.inputs["Emission Color"], nb.mix((0, 0, 0, 1), speck_col, speck, label="Specks"))
    nb.feed(bsdf.inputs["Emission Strength"], max(p.s("EmissiveIntensity", 5.0), 0.0) * 1.6)
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
