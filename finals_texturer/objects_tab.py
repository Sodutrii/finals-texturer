"""Objects tab: game objects that aren't part of a map: Cashout objectives, death statues, arena
carryables (later: gameplay objects, breakable / liftable props).

These have no catalog of their own in the game (no data asset, name or store image like clothing and
weapons). An object is a Blueprint (BP_*: its parts, where they sit and material overrides, see
blueprints.py) or a single mesh; the list below names them. Only the default look is imported.
"""

import os
import zlib

import bpy
from bpy.props import EnumProperty

from . import blueprints, importer, search, ui
from .ue_assets import AssetFinder, Log, asset_name, resolve_material, skeletal_slots

GAME = "Discovery/Content/Discovery/"
GM = GAME + "Core/Gamemodes/"
ATK = GAME + "Environment/Props/ArenaToolKit/"
TOTEM = GAME + "Gameplay/Objects/CharacterTotem/"

# (category, name, path under the Exports folder without extension)
CATALOG = [
    ("Cashout", "Cashbox", GM + "CashOut/BP_CashoutCashBox"),
    ("Cashout", "Vault", GM + "CashOut/BP_CashOutVault"),
    ("Cashout", "Cashout Station", GM + "CashOut/BP_CashoutTransferPoint"),
    ("Death Statues", "Death Statue (Light)", TOTEM + "CharacterTotem_Light/SM_CharacterTotem_Light"),
    ("Death Statues", "Death Statue (Medium)", TOTEM + "CharacterTotem_Medium/SM_CharacterTotem_Medium"),
    ("Death Statues", "Death Statue (Heavy)", TOTEM + "CharacterTotem_Heavy/SM_CharacterTotem_Heavy_01_A"),
    ("Arena Carryables", "Explosive Canister", ATK + "ATK_CanisterGas_01/BP_ATK_GasCanister_01"),
    ("Arena Carryables", "Glitch Barrel", ATK + "ATK_BarrelGlitch_01/BP_ATK_BarrelGlitch_01"),
    ("Arena Carryables", "Goo Barrel", ATK + "ATK_BarrelGoo_01/BP_ATK_GooBarrel_01"),
    ("Arena Carryables", "Healing Barrel", ATK + "ATK_BarrelHealing_01/BP_ATK_HealingBattery_01"),
    ("Arena Carryables", "Oil Barrel", ATK + "ATK_BarrelOil_01/BP_ATK_OilBarrel_01"),
    ("Arena Carryables", "Toxic Gas Barrel", ATK + "ATK_BarrelToxic_01/BP_ATK_ToxicGasBarrel_01"),
    ("Arena Carryables", "Fire Extinguisher (Powder Barrel)", ATK + "ATK_FireExtinguisher_01/BP_ATK_FireExtinguisher_01"),
    ("Gameplay Objects", "Jump Pad", GAME + "Gameplay/BP_JumpPad"),
    ("Gameplay Objects", "Jump Pad (Omni-Directional)", ATK + "ATK_Jumppad_02/BP_ATK_OmniDirectionalJumpPad_01"),
    ("Gameplay Objects", "Zipline Anchor A", ATK + "ATK_Zipline_02/BP_ATK_Zipline_02_A"),
    ("Gameplay Objects", "Zipline Anchor B", ATK + "ATK_Zipline_02/BP_ATK_Zipline_02_B"),
    ("Gameplay Objects", "Ladder Segment A", ATK + "ATK_Ladder_01/SM_ATK_Ladder_01_z200_A"),
    ("Gameplay Objects", "Ladder Segment B", ATK + "ATK_Ladder_01/SM_ATK_Ladder_01_z200_B"),
    ("Gameplay Objects", "Ladder Segment C", ATK + "ATK_Ladder_01/SM_ATK_Ladder_01_z200_C"),
    ("Gameplay Objects", "Ladder End", ATK + "ATK_Ladder_01/SM_ATK_Ladder_01_z200_End_A"),
    ("Gameplay Objects", "Barrel Dispenser", GAME + "Gameplay/Objects/ActorDispenser/BP_PracticeRange_ActorDispenser"),
    ("Gameplay Objects", "Cannon", GAME + "Gameplay/Objects/Cannons/BP_Cannon_01"),
    ("Gameplay Objects", "Button", ATK + "ATK_Button_01/BP_ATK_Button_01"),
    ("Gameplay Objects", "Button on Pole", ATK + "ATK_Button_01/BP_ATK_Button_Pole_01"),
]
_PROPS_FILE = os.path.join(os.path.dirname(__file__), "data", "props.json")
_props = {"rows": None}


def _prop_rows():
    """Phase 3: props you can pick up or break (data/props.json, built from the game files by
    dev_tools/shader_extract/build_props.py)."""
    if _props["rows"] is None:
        try:
            import json
            with open(_PROPS_FILE, encoding="utf-8") as fh:
                _props["rows"] = json.load(fh).get("props", [])
        except (OSError, ValueError):
            _props["rows"] = []
    return _props["rows"]


def categories():
    return list(dict.fromkeys([c for c, _n, _p in CATALOG] + [r["category"] for r in _prop_rows()]))

# Team colours of death statues. Blue = the statue material's own default; the others are the in-match
# team colours (DataAssets/Squads/DAList_Squads MatchTeamColors, default palette, linear RGB).
TEAM_COLORS = [
    ("BLUE", "Blue", None),
    ("ORANGE", "Orange", (1.0, 0.208637, 0.0)),
    ("PINK", "Pink", (1.0, 0.03434, 0.462077)),
    ("PURPLE", "Purple", (0.309469, 0.06301, 1.0)),
    ("RED", "Red (two-team modes)", (0.930111, 0.107023, 0.124772)),
    ("INDIGO", "Indigo", (0.059511, 0.036889, 0.201556)),
    ("LIME", "Lime", (0.496933, 0.603828, 0.049707)),
]
SKIP_MESH_ROOTS = ("/Engine/",)        # engine shapes (hologram VFX cards)
READY_ICON = {2: "CHECKMARK", 1: "INFO", 0: "ERROR"}

_ctx = {"root": lambda context: "", "report": None}
_status = {}
_items_keep = []
_items_cache = {}


def _root(context):
    return _ctx["root"](context)


def clear_cache():
    _status.clear()
    _items_cache.clear()


def _num(key):
    return zlib.crc32(key.encode()) & 0xFFFFF


class _QuietLog(Log):
    def info(self, msg):
        pass

    def warn(self, msg):
        pass


def objects():
    out = [{"id": p, "name": n, "category": c, "path": p, "kind": ""} for c, n, p in CATALOG]
    out += [{"id": r["path"], "name": r["name"], "category": r["category"], "path": r["path"], "kind": r["kind"]}
            for r in _prop_rows()]
    return out


def object_by_id(oid):
    return next((o for o in objects() if o["id"] == oid), None)


def _file(root, obj):
    return os.path.join(root, *obj["path"].split("/")) + ".json"


def _mesh_file(root, obj):
    base = os.path.join(root, *obj["path"].split("/"))
    for ext in (".pskx", ".psk"):
        if os.path.isfile(base + ext):
            return base + ext
    return ""


def _folder(object_path):
    p = object_path.split(".")[0]
    return p.rsplit("/", 1)[0].replace("/Game/Discovery/", "")


def _parts(root, obj, finder):
    """[(part dict | None, mesh file)] of an object; part None for a single-mesh object."""
    if blueprints.is_blueprint(obj["path"]):
        out = []
        for p in blueprints.parts(finder, _file(root, obj)):
            if p["mesh"].startswith(SKIP_MESH_ROOTS):
                continue
            out.append((p, importer.find_part_file(finder, p["mesh"])))
        return out
    return [(None, _mesh_file(root, obj))]


def export_paths(root, obj):
    return [obj["path"] + ".uasset"]


def status(root, obj):
    key = (root, obj["id"])
    hit = _status.get(key)
    if hit is not None:
        return hit
    folder = obj["path"].replace(GAME, "").rsplit("/", 1)[0]
    if blueprints.is_blueprint(obj["path"]) and not os.path.isfile(_file(root, obj)):
        res = _status[key] = (0, "Not exported", {"Object": [folder]})
        return res
    log = _QuietLog()
    finder = importer.make_finder(root, os.path.dirname(_file(root, obj)), log)
    parts = _parts(root, obj, finder)
    meshes = [(_folder(p["mesh"]) if p else folder) for p, f in parts if not f]
    textures = []
    for p, f in parts:
        if not f:
            continue
        name = os.path.splitext(os.path.basename(f))[0]
        slots, _sj = skeletal_slots(finder, name, os.path.dirname(f))
        overrides = (p or {}).get("materials") or {}
        for i, (_slot, mat) in enumerate(slots):
            mat = overrides.get(i, mat)
            if not mat:
                continue
            params = resolve_material(finder, object_path=mat, log=log)
            for t in params.textures.values():
                if t and not t.startswith("/Engine/") and "/Textures/Debug/" not in t                         and not asset_name(t).startswith(("RT_", "RTC_")) \
                        and not finder.has_texture(t) and not finder.array_slice_count(t):
                    textures.append(_folder(t))
    groups = ui.split_folders(meshes, textures)
    if not parts:
        res = (0, "No parts found", groups)
    elif meshes:
        res = (0, f"{len(meshes)} mesh(es) not exported", groups)
    elif textures:
        res = (1, f"{len(set(textures))} texture folder(s) not exported", groups)
    else:
        res = (2, "Exported", {})
    _status[key] = res
    return res


def set_team_color(objs, team):
    """Give death statues a team colour: a material copy per colour ("<material> (Orange)"), so
    statues of different teams can share a scene. Returns how many slots changed."""
    from .builders.objects import TEAM_NODE
    entry = next((t for t in TEAM_COLORS if t[0] == team), TEAM_COLORS[0])
    done = 0
    for o in objs:
        if o.type != "MESH":
            continue
        for slot in o.material_slots:
            m = slot.material
            if m is None or not m.node_tree or TEAM_NODE not in m.node_tree.nodes:
                continue
            base = m.get("ft_team_base") or m
            if isinstance(base, str):
                base = bpy.data.materials.get(base) or m
            if entry[2] is None:
                target = base
            else:
                name = f"{base.name} ({entry[1].split(' ')[0]})"
                target = bpy.data.materials.get(name)
                if target is None:
                    target = base.copy()
                    target.name = name
                    target["ft_team_base"] = base.name
                    target.node_tree.nodes[TEAM_NODE].outputs[0].default_value = entry[2] + (1.0,)
            slot.material = target
            done += 1
        o["ft_team"] = entry[0]
    return done


def import_object(context, obj, log):
    """Import an object: its parts placed as in the game, under an empty named after it."""
    root = _root(context)
    finder = importer.make_finder(root, os.path.dirname(_file(root, obj)), log)
    unit = importer.import_scale()
    from .weapons import _ue_matrix
    from mathutils import Matrix
    new_objs = []
    holder = bpy.data.objects.new(obj["name"], None)
    holder.empty_display_type = "PLAIN_AXES"
    holder.empty_display_size = 50.0 * unit
    for part, f in _parts(root, obj, finder):
        if not f:
            log.warn(f"{asset_name(part['mesh']) if part else obj['name']} isn't exported "
                     f"(game folder {_folder(part['mesh']) if part else obj['path']}).")
            continue
        objs = importer.import_psk(f, log)
        m = Matrix.Identity(4)
        for loc, rot, scale in (part or {}).get("chain", []):
            m = m @ _ue_matrix(loc, rot, scale, unit)
        for o in objs:
            if o.parent is None or o.parent not in objs:
                o.matrix_world = m @ o.matrix_world
                o.parent = holder
            o["ft_object"] = obj["id"]
            if o.type == "MESH":
                importer.apply_to_object(o, root, "NONE", log, slot_overrides=(part or {}).get("materials") or {})
        new_objs += objs
    if not new_objs:
        bpy.data.objects.remove(holder)
        return []
    holder["ft_object"] = obj["id"]
    team = getattr(context.scene, "ft_obj_team", "BLUE")
    if team != "BLUE":
        set_team_color(new_objs, team)
    coll = bpy.data.collections.new(obj["name"])
    context.scene.collection.children.link(coll)
    for o in [holder] + new_objs:
        for c in list(o.users_collection):
            c.objects.unlink(o)
        coll.objects.link(o)
    return [holder] + new_objs


# ─────────────────────────────────────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────────────────────────────────────

def _cat_items(self, context):
    items = _items_cache.get("cats")
    if items is None:
        items = _items_cache["cats"] = [(c, c, "", i) for i, c in enumerate(categories())]
        _items_keep.append(items)
    return items


def _object_items(self, context):
    context = context or bpy.context
    root = _root(context)
    cat = getattr(getattr(context, "scene", None), "ft_obj_cat", categories()[0])
    key = (root, cat)
    hit = _items_cache.get(key)
    if hit is not None:
        return hit
    items = []
    for o in objects():
        if o["category"] != cat:
            continue
        level, text, _g = status(root, o) if root else (0, "Set the Exports folder", {})
        desc = f"{o['kind'].capitalize()} · {text}" if o.get("kind") else text
        items.append((o["id"], o["name"], desc, READY_ICON[level], _num(o["id"])))
    if not items:
        items.append(("NONE", "Nothing here", "", "ERROR", 0))
    _items_cache[key] = items
    _items_keep.append(items)
    return items


def _selected(context):
    return object_by_id(context.scene.ft_obj)


class FT_OT_object_import(bpy.types.Operator):
    """Import the chosen object with all its parts, placed as in the game (default look)"""
    bl_idname = "finals_texturer.object_import"
    bl_label = "Import"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = _selected(context)
        if obj is None:
            self.report({"ERROR"}, "Pick an object first")
            return {"CANCELLED"}
        root = _root(context)
        level, text, _g = status(root, obj)
        if text == "Not exported":
            self.report({"ERROR"}, "Not exported yet: use Export Now (or export its folder in FModel)")
            return {"CANCELLED"}
        log = Log()
        try:
            objs = import_object(context, obj, log)
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        _ctx["report"](self, log, sum(1 for o in objs if o.type == "MESH"))
        return {"FINISHED"} if objs else {"CANCELLED"}


def _find(context, query):
    q = query.lower().split()
    return [(o["id"], o["name"], o["category"], -1, "OBJECT_DATA") for o in objects()
            if all(t in f"{o['name']} {o['category']} {o['kind']} {o['path']}".lower() for t in q)]


def _pick(context, ident):
    o = object_by_id(ident)
    if o is None:
        return
    try:
        context.scene.ft_obj_cat = o["category"]
        context.scene.ft_obj = o["id"]
    except TypeError:
        pass


def _on_cat(self, context):
    items = _object_items(self, context)
    try:
        context.scene.ft_obj = items[0][0]
    except (TypeError, IndexError):
        pass


def _on_team(self, context):
    """Recolour the selected death statues."""
    sel = [o for o in context.selected_objects if o.get("ft_object")]
    if sel:
        set_team_color(sel, context.scene.ft_obj_team)


def _draw_options(col, context):
    from . import _prefs
    p = _prefs(context)
    if p:
        col.prop(p, "import_size", text="Size")


def draw(layout, context):
    sc = context.scene
    root = _root(context)
    search.draw(layout, context, "obj", "Search objects")
    col = layout.column(align=True)
    col.prop(sc, "ft_obj_cat", text="Category")
    col.prop(sc, "ft_obj", text="Object")
    obj = _selected(context)
    if obj is not None and obj["category"] == "Death Statues":
        col.prop(sc, "ft_obj_team", text="Team colour")
    if obj is not None and root:
        level, text, groups = status(root, obj)
        ui.draw_status(layout, level, text, groups, export=export_paths(root, obj), what=obj["name"])
    layout.operator(FT_OT_object_import.bl_idname, icon="IMPORT")
    ui.draw_options(layout, context, "OBJECTS", lambda c: _draw_options(c, context))


CLASSES = (FT_OT_object_import,)


def register(root_getter, report):
    _ctx.update(root=root_getter, report=report)
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.ft_obj_cat = EnumProperty(name="Category", items=_cat_items, update=_on_cat)
    bpy.types.Scene.ft_obj = EnumProperty(
        name="Object", items=_object_items,
        description="Object to import. Check = exported, i = some textures missing, warning = not exported")
    bpy.types.Scene.ft_obj_team = EnumProperty(
        name="Team colour", items=[(k, n, "", i) for i, (k, n, _c) in enumerate(TEAM_COLORS)], update=_on_team,
        description="Death statue colour: blue is the statue's own default; orange, pink and purple are the "
                    "other teams' colours (indigo and lime too), red the second team in two-team modes. Also recolours the selected statues")
    search.add_tab("obj", _find, _pick, "Object name")


def unregister():
    search.remove_tab("obj")
    del bpy.types.Scene.ft_obj_team
    del bpy.types.Scene.ft_obj
    del bpy.types.Scene.ft_obj_cat
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
