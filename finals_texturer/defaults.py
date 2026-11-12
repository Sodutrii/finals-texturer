"""Shader-type detection and built-in parameter defaults.

The defaults are copied from M_Character_8Layers_Master's CachedExpressionData
so the addon still works when the LayeredMaterials folder was not exported.
When the master JSON is present, its own values are used instead.
"""

LAYERED_PATH = "/Game/Discovery/MaterialLibrary/Character/LayeredMaterials/Textures/"

# Per-layer defaults (layer 1). Layers 2-8 differ only as listed below.
LAYER_SCALARS = {
    'DetailTiling': 20.0, 'BaseRoughness': 0.0, 'BaseMetallicity': 0.0,
    'HueVariationStrength': 0.2, 'HueVariationTiling': 0.15, 'BaseSpecular': 0.5,
    'EdgeAmount': 0.0, 'EdgeMaskSoftness': 0.0, 'EdgeMaskTiling': 0.4,
    'EdgeNormalTiling': 1.0, 'EdgeSpecular': 0.5, 'EdgeRoughness': 0.0,
    'EdgeRougnessTiling': 1.0, 'EdgeMetallicity': 0.0, 'CreaseAmount': 0.0,
    'CreaseMaskSoftness': 0.0, 'CreaseMaskTiling': 0.4, 'CreaseNormalTiling': 1.0,
    'CreaseSpecular': 0.5, 'CreaseRoughness': 0.0, 'CreaseRougnessTiling': 1.0,
    'CreaseMetallicity': 0.0, 'EdgeMaskThickness': 0.3, 'BaseRoughnessTiling': 1.0,
    'EdgeMaskID': 0.0, 'CreaseMaskID': 0.0, 'BaseNormalID': 0.0, 'EdgeNormalID': 0.0,
    'CreaseNormalID': 0.0, 'BaseRoughnessID': 0.0, 'EdgeRoughnessID': 0.0,
    'CreaseRoughnessID': 0.0, 'ShadeAsCloth': 0.0, 'PatternID': 0.0, 'PatternTiling': 1.0,
    'CreaseTextureStrength': 0.0, 'EdgeTextureStrength': 0.0, 'BaseTextureStrength': 0.0,
    'ColorTextureTiling': 1.0, 'ColorTextureID': 0.0, 'BaseColorMaskStrength': 1.0,
    'EdgeColorMaskStrength': 1.0, 'CreaseColorMaskStrength': 1.0, 'MediumNormalID': 0.0,
    'MediumNormalTiling': 0.0, 'MediumNormalStrength': 0.0, 'MediumMaskSoftness': 0.0,
    'MediumMaskValue': 0.5,
}
LAYER_VECTORS = {
    'EdgeColorOverlay': (1.0, 1.0, 1.0, 0.0), 'CreaseColorOverlay': (0.0, 0.0, 0.0, 0.0),
    'BaseColorOverlay': (1.0, 1.0, 1.0, 1.0), 'PatternSwatch': (0.0, 1.0, 0.0, 0.0),
    'PatternSwatchMask': (1.0, 1.0, 1.0, 1.0), 'ColorMaskSwatch': (1.0, 1.0, 1.0, 1.0),
}
LAYER2PLUS_SCALARS = {'BaseColorMaskStrength': 0.0, 'EdgeColorMaskStrength': 0.0,
                      'CreaseColorMaskStrength': 0.0}
LAYER2PLUS_VECTORS = {'PatternSwatch': (1.0, 1.0, 1.0, 1.0)}

GLOBAL_SCALARS = {
    'DecalSize': 1.0, 'DecalPositionU': 0.0, 'DecalRotation': 0.0, 'DecalPositionV': 0.0,
    'Tiling': 1.0, 'DecalTint': 1.0, 'DecalDetailNormalBlend': 0.0, 'DecalSurfaceBlend': 0.0,
    'DecalNormalBlend': 0.0, 'DecalWidth': 1.0, 'UseDecalNumber': 0.0, 'DecalNumber': 32.0,
}
GLOBAL_VECTORS = {
    'ColorA': (0.57758, 0.09759, 0.04666, 1.0), 'ColorB': (0.04971, 0.57112, 0.20613, 1.0),
    'ColorC': (0.05286, 0.12964, 0.56471, 1.0), 'ColorA2': (0.57758, 0.09759, 0.04666, 1.0),
    'ColorB2': (0.04971, 0.57112, 0.20613, 1.0), 'ColorC2': (0.05286, 0.12964, 0.56471, 1.0),
    'DecalColorA': (1.0, 1.0, 1.0, 1.0), 'DecalColorB': (1.0, 1.0, 1.0, 1.0),
}
GLOBAL_TEXTURES = {
    'Normal': LAYERED_PATH + 'Normals/T_Null_N.T_Null_N',
    'BreakupMask': LAYERED_PATH + 'Masks/T_White_M.T_White_M',
    'HueVariation': LAYERED_PATH + 'Global/T_HueNoise_01_C.T_HueNoise_01_C',
    'TextureArray_N': LAYERED_PATH + 'TextureArrays/TA_Character_Normals.TA_Character_Normals',
    'TextureArray_M': LAYERED_PATH + 'TextureArrays/TA_Character_Masks.TA_Character_Masks',
    'TextureArray_Patterns': LAYERED_PATH + 'TextureArrays/TA_Character_Patterns.TA_Character_Patterns',
    'TextureArray_C': LAYERED_PATH + 'TextureArrays/TA_Character_Colors.TA_Character_Colors',
    'BaseColor': LAYERED_PATH + 'Masks/T_Black_M.T_Black_M',
    'OcclusionCurvatureMaterialID': LAYERED_PATH + 'Global/T_OcclusionCurvatureMaterialID.T_OcclusionCurvatureMaterialID',
}

# Textures that mean "nothing here" — the builder skips them.
NULL_TEXTURES = {"t_decal_null_cm", "t_null_n", "t_default_01_c", "t_default_02_c", "t_black_m"}

LACE_DEFAULTS = {
    "M_Lace": ({'Transparency': 1.0, 'MinTransparency': 0.0, 'FadeStart': 0.0, 'OffsetGradient': 0.0,
                'OverallOpacity': 0.0, 'Roughness': 1.0},
               {'Color': (1.0, 1.0, 1.0, 1.0), 'ColorB': (0.026042, 0.026042, 0.026042, 1.0)}),
    "M_LaceSkirt": ({'Transparency': 1.0, 'MinTransparency': 0.0, 'FadeStart': 0.0, 'OffsetGradient': 0.0},
                    {'ColorTint': (1.0, 1.0, 1.0, 1.0)}),
}

# ─────────────────────────────────────────────────────────────────────────────
# Shader types
# ─────────────────────────────────────────────────────────────────────────────

LAYERED = "LAYERED"          # M_Character_8Layers_Master (+ AnimatedPattern)
LAYERED_OLD = "LAYERED_OLD"  # M_Character_Layered (per-layer decals, ColorSchemeBlend)
PREVIEW = "PREVIEW"          # M_Character_Preview (placeholder before a skin is applied)
LACE = "LACE"                # CustomMaterials/M_Lace
LACE_SKIRT = "LACE_SKIRT"    # CustomMaterials/M_LaceSkirt
FEATHER = "FEATHER"          # CustomMaterials/M_Feather
ATTACHMENT = "ATTACHMENT"    # M_CharacterAttachment / SkinnedAttachment (+ variants)
GLASS = "GLASS"              # M_CharacterGlass_01/02, visors
HOLOGRAM = "HOLOGRAM"        # M_Character_HolographicMaterial
PLASTIC_SHEET = "PLASTIC_SHEET"  # M_Character_PlasticSheet (+ NoContactShadow)
TEAM_COLOR = "TEAM_COLOR"    # M_CharacterLayered_TeamColor_01
SKIN = "SKIN"                # M_Skin (bare body; body / hands textures by UV tile)
FACE = "FACE"                # M_Face (head skin)
EYE = "EYE"                  # M_EyeRefractive_* (eyeballs)
EYELASH = "EYELASH"          # M_EyelashMaster
TEETH = "TEETH"              # M_Teeth (teeth, gums, tongue)
HAIR = "HAIR"                # M_Hair_Metahuman_* hair cards (eyebrows, hair styles)
GLITTER = "GLITTER"          # M_Skin_Glitter (glossy black, glitter at the edges)
WEAPON = "WEAPON"            # M_Weapon_Base_5Layers (weapon skins, layers + graphic)
WEAPON_PRESET = "WEAPON_PRESET"  # M_WeaponPreset_CR+NOM (weapon skins with their own model)
WEAPON_TEXT = "WEAPON_TEXT"  # M_WeaponText (logo / lettering decals on weapons)
WEAPON_FX = "WEAPON_FX"      # other weapon effect masters (diamond, fresnel, progression…), approximate
HIDDEN = "HIDDEN"            # eye shadow shell / wet eye edge: screen-space effects, left invisible
GENERIC = "GENERIC"          # anything else: guess from texture names

_KNOWN = [
    ("m_character_8layers", LAYERED, "M_Character_8Layers_Master"),
    ("mi_character_", LAYERED, "M_Character_8Layers_Master"),   # MI_Character_NLayers
    ("m_character_layered", LAYERED_OLD, "M_Character_Layered"),
    ("m_character_preview", PREVIEW, "M_Character_Preview"),
    ("m_laceskirt", LACE_SKIRT, "M_LaceSkirt"),
    ("m_lace", LACE, "M_Lace"),
    ("m_feather", FEATHER, "M_Feather"),
    ("m_characterattachment", ATTACHMENT, "M_CharacterAttachment"),
    ("m_characterskinnedattachment", ATTACHMENT, "M_CharacterSkinnedAttachment"),
    ("m_characterglass", GLASS, "M_CharacterGlass_02"),
    ("m_character_visorgradient", GLASS, "M_Character_VisorGradient"),
    ("m_visor", GLASS, "M_Visor"),
    ("m_character_holographicmaterial", HOLOGRAM, "M_Character_HolographicMaterial"),
    ("m_character_plasticsheet", PLASTIC_SHEET, "M_Character_PlasticSheet"),
    ("m_characterlayered_teamcolor", TEAM_COLOR, "M_CharacterLayered_TeamColor_01"),
    ("m_eyerefractive", EYE, "M_EyeRefractive_2"),
    ("m_eyelashmaster", EYELASH, "M_EyelashMaster"),
    ("mi_headmaster_eyelashes", EYELASH, "M_EyelashMaster"),
    ("m_teeth", TEETH, "M_Teeth"),
    ("m_weapon_base", WEAPON, "M_Weapon_Base_5Layers"),
    ("m_weaponpreset", WEAPON_PRESET, "M_WeaponPreset_CR+NOM"),
    ("m_weapontext", WEAPON_TEXT, "M_WeaponText"),
    ("mi_weapon_base", WEAPON, "M_Weapon_Base_5Layers"),
    ("m_hair_metahuman", HAIR, "M_Hair_Metahuman_01"),
    ("m_eyeocclusion", HIDDEN, "M_EyeOcclusion"),
    ("m_lacrimal_fluid", HIDDEN, "M_lacrimal_fluid"),
]

# Exact names only (M_Skin_Glitter / _Space / _Hardsurface are different shaders).
_EXACT = {"m_skin": SKIN, "m_face": FACE, "m_skin_glitter": GLITTER}


def master_from_chain(chain):
    """Best master name we can infer from the names in a chain."""
    for name in reversed(chain):
        low = name.lower()
        if low in _EXACT:
            return name
        if low.startswith("mi_character_layered"):
            return "M_Character_Layered"
        for key, _kind, master in _KNOWN:
            if low.startswith(key):
                return master
    return ""


def shader_type(params):
    names = [params.master] + list(reversed(params.chain))
    for name in names:
        if not name:
            continue
        low = name.lower()
        if low in _EXACT:
            return _EXACT[low]
        if low.startswith("mi_character_layered") or low.startswith("m_character_layered"):
            return LAYERED_OLD
        for key, kind, _master in _KNOWN:
            if low.startswith(key):
                return kind
    # Weapon-family masters without their own builder (M_Diamond_Weapon,
    # M_WeaponProgression, M_LegendaryWeaponFresnel…): approximate them;
    # checked before the parameter guess (the progression master has 1_*
    # params and was taken for the clothing layered shader).
    if any("weapon" in (n or "").lower() for n in names):
        return WEAPON_FX
    # No known parent: recognise the layered system by its parameters.
    if params.has_any("1_BaseRoughness", "1_DetailTiling", "OcclusionCurvatureMaterialID"):
        return LAYERED_OLD if params.has_any("1_ColorSchemeBlend", "TextureArray_Colors") else LAYERED
    return GENERIC


def apply_builtin_defaults(master, out):
    if master in ("M_Character_8Layers_Master", "M_Character_Layered"):
        out.scalars.update(GLOBAL_SCALARS)
        out.vectors.update(GLOBAL_VECTORS)
        out.textures.update(GLOBAL_TEXTURES)
        for n in range(1, 9):
            for k, v in LAYER_SCALARS.items():
                out.scalars[f"{n}_{k}"] = LAYER2PLUS_SCALARS.get(k, v) if n > 1 else v
            for k, v in LAYER_VECTORS.items():
                out.vectors[f"{n}_{k}"] = LAYER2PLUS_VECTORS.get(k, v) if n > 1 else v
        for n in range(2, 9):
            out.switches[f"UseLayer{n}"] = False
        out.switches["bUseOpacityMask"] = False
        out.blend_mode = out.blend_mode or "BLEND_Masked"
    elif master in LACE_DEFAULTS:
        s, v = LACE_DEFAULTS[master]
        out.scalars.update(s)
        out.vectors.update(v)
        out.blend_mode = out.blend_mode or "BLEND_Masked"
        out.two_sided = True if out.two_sided is None else out.two_sided
