"""Build Blender materials from resolved UE material parameters.

One builder per master shader type (see defaults.shader_type):
* common.py    Ctx and shared helpers, generic fallback
* clothing.py  clothing shaders
* character.py body, face, eyes, hair, glitter…
* weapon.py    weapon skins (M_Weapon_Base_5Layers)
"""

from .. import defaults
from .common import Ctx, build_generic
from .clothing import (build_attachment, build_feather, build_glass, build_hologram, build_lace,
                       build_lace_skirt, build_layered, build_plastic_sheet, build_preview, build_team_color)
from .character import (build_eye, build_eyelash, build_face, build_glitter, build_hair_cards, build_hidden,
                        build_skin, build_teeth)
from .weapon import build_weapon, build_weapon_fx, build_weapon_preset, build_weapon_text

BUILDERS = {
    defaults.LAYERED: lambda ctx: build_layered(ctx, old_gen=False),
    defaults.LAYERED_OLD: lambda ctx: build_layered(ctx, old_gen=True),
    defaults.PREVIEW: build_preview,
    defaults.LACE: build_lace,
    defaults.LACE_SKIRT: build_lace_skirt,
    defaults.FEATHER: build_feather,
    defaults.ATTACHMENT: build_attachment,
    defaults.GLASS: build_glass,
    defaults.HOLOGRAM: build_hologram,
    defaults.PLASTIC_SHEET: build_plastic_sheet,
    defaults.TEAM_COLOR: build_team_color,
    defaults.SKIN: build_skin,
    defaults.FACE: build_face,
    defaults.EYE: build_eye,
    defaults.EYELASH: build_eyelash,
    defaults.TEETH: build_teeth,
    defaults.HIDDEN: build_hidden,
    defaults.WEAPON: build_weapon,
    defaults.WEAPON_PRESET: build_weapon_preset,
    defaults.WEAPON_TEXT: build_weapon_text,
    defaults.WEAPON_FX: build_weapon_fx,
    defaults.HAIR: build_hair_cards,
    defaults.GLITTER: build_glitter,
    defaults.GENERIC: build_generic,
}


def build_material(mat, params, finder, log, detail_uv=None, cm_per_unit=1.0):
    kind = defaults.shader_type(params)
    if mat.node_tree is None:
        mat.use_nodes = True
    ctx = Ctx(mat, params, finder, log, detail_uv=detail_uv, cm_per_unit=cm_per_unit)
    BUILDERS[kind](ctx)
    mat["ft_material"] = params.name
    mat["ft_chain"] = " > ".join(params.chain)
    mat["ft_shader"] = kind
    return kind
