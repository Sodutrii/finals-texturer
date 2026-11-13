"""Weapons tab: import a weapon (firearms, melee) with a skin.

How the game builds a weapon (checked on Items/Melee/Sword_02 and
Items/Firearms/AssaultRifles/AssaultRifleLight_01, 2026-11-11):
* <weapon>/DA_UIMetaData_<weapon>.json: in-game name (ItemName).
* A skin is a WeaponSkinDataAsset (Skins/**/DA_Skin_*.json, name in the
  DA_MetaData_Skin_* next to it, store image T_UI_Icon_Skin_*). It points to a
  WeaponVisualsDataAsset (WeaponVisual) and overrides materials per slot for
  the base mesh (BaseOverrides) and each part (PartOverrides).
* The default skin ("Standard Issue") has only metadata: it uses
  Skins/DA_<weapon>_VisualOverride_Default and the meshes' own materials.
* A visual: BasePartSkeletalMesh + WeaponParts, each a static or skeletal
  mesh attached by AttachmentSocket (case-insensitive) with LocalOffset /
  LocalRotation / LocalScale. A socket defined on another part's static mesh
  wins (the AR handguard's "Suppressor" = muzzle; the skeleton's socket of
  that name sits at the root), then skeleton sockets, then bones.
* Parts tagged Mesh.StickerSlot (stickers) and parts that need an optional
  attachment (RequiredTags other than the default iron sights) are skipped,
  as are charms and animations.
"""

import os
import re
import zlib

import bpy
from bpy.props import EnumProperty

from . import importer, names, search, ui
from .ue_assets import AssetFinder, Log, asset_name, export_of_type, ref_path, resolve_material, skeletal_slots

ITEMS_REL = os.path.join("Discovery", "Content", "Discovery", "Items")
CATEGORIES = ("Firearms", "Melee")
SKIP_TAGS = {"Mesh.StickerSlot"}
DEFAULT_PART_TAGS = {"Customization.Item.Attachment.Sight.IronSights_01"}
DEFAULT_SKIN = "DEFAULT"
READY_ICON = {2: "CHECKMARK", 1: "INFO", 0: "ERROR"}
PLACEHOLDER_DIRS = ("/Engine/", "/Textures/Util/", "/Textures/Debug/")

_ctx = {"root": lambda context: "", "report": None}
_weapons = {}        # root → [weapon]
_skins = {}          # (root, weapon id) → [skin]
_status = {}         # (root, weapon id, skin id) → (level, text, folders)
_items_keep = []     # every enum list handed to Blender (kept alive)
_items_cache = {}


def _root(context):
    return _ctx["root"](context)


def clear_cache():
    for c in (_weapons, _skins, _status, _items_cache):
        c.clear()


def _load(path):
    import json
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _num(key):
    return zlib.crc32(key.encode("utf-8")) % 1_000_000     # small: the UI passes enum values as floats


def _game_path_to_file(root, object_path):
    """/Game/Discovery/X/Y.Y → <root>/Discovery/Content/Discovery/X/Y.json"""
    path = object_path.split(".")[0]
    if path.startswith("/Game/"):
        path = "Discovery/Content/" + path[len("/Game/"):]
    return os.path.join(root, path.lstrip("/") + ".json")


# ─────────────────────────────────────────────────────────────────────────────
# Weapons and skins
# ─────────────────────────────────────────────────────────────────────────────

def weapons(root):
    """Weapons (folders with a DA_Item_*.json) under Items/Firearms and Items/Melee."""
    if not root:
        return []
    hit = _weapons.get(root)
    if hit is not None:
        return hit
    out = []
    items = os.path.join(root, ITEMS_REL)
    for cat in CATEGORIES:
        base = os.path.join(items, cat)
        if not os.path.isdir(base):
            continue
        for dirpath, dirs, files in os.walk(base):
            if "Skins" in dirpath.split(os.sep) or "Anim" in dirpath.split(os.sep):
                continue
            item = next((f for f in files if f.startswith("DA_Item_") and f.endswith(".json")
                         and not f.startswith("DA_Item_Animation")), None)
            if not item:
                continue
            folder = os.path.basename(dirpath)
            data = _load(os.path.join(dirpath, f"DA_UIMetaData_{folder}.json"))
            props = next(((e.get("Properties") or {}) for e in (data if isinstance(data, list) else [])
                          if "ItemName" in (e.get("Properties") or {})), {})
            name = (props.get("ItemName") or {}).get("SourceString") or folder.replace("_", " ")
            sub = os.path.relpath(dirpath, items).replace("\\", "/")
            out.append({"id": sub, "dir": dirpath, "name": name, "folder": folder,
                        "category": sub.split("/")[0] if cat == "Melee" else sub.split("/")[1]})
            dirs[:] = []                     # a weapon's subfolders aren't weapons
    # weapons from the shipped name list that aren't exported yet (listed as "not exported")
    have = {w["id"] for w in out}
    for sw in names.weapons():
        if sw["id"] in have:
            continue
        name = sw["name"]
        if name.endswith(f" ({sw['folder']})"):
            name = name[:-len(f" ({sw['folder']})")]
        out.append({"id": sw["id"], "dir": os.path.join(items, *sw["id"].split("/")), "name": name,
                    "folder": sw["folder"], "category": sw["category"], "shipped": sw["skins"]})
    # same in-game name twice (e.g. two swords): add the folder
    counts = {}
    for w in out:
        counts[w["name"]] = counts.get(w["name"], 0) + 1
    for w in out:
        if counts[w["name"]] > 1:
            w["name"] = f"{w['name']} ({w['folder']})"
    out.sort(key=lambda w: (w["category"], w["name"].lower()))
    _weapons[root] = out
    return out


def weapon_by_id(root, wid):
    return next((w for w in weapons(root) if w["id"] == wid), None)


def _meta_name(meta_path):
    data = _load(meta_path)
    for e in data if isinstance(data, list) else []:
        if e.get("Type") == "WeaponSkinMetaDataItem":
            p = e.get("Properties") or {}
            pretty = p.get("PrettyName") or {}
            image = ref_path(p.get("Image"))
            return pretty.get("SourceString") or pretty.get("CultureInvariantString") or "", image
    return "", ""


def skins(root, weapon):
    """[skin]: the default ("Standard Issue") first, then every WeaponSkinDataAsset."""
    key = (root, weapon["id"])
    hit = _skins.get(key)
    if hit is not None:
        return hit
    shipped = {s["id"]: s for sw in names.weapons() if sw["id"] == weapon["id"] for s in sw["skins"]}
    if not os.path.isdir(weapon["dir"]):
        out = [{"id": s["id"], "path": os.path.join(root, s["path"]) if s["path"] else "", "name": s["name"],
                "image": s["image"], "variant": s["variant"]} for s in shipped.values()]
        _skins[key] = out
        return out
    sdir = os.path.join(weapon["dir"], "Skins")
    out = []
    dmeta = next((os.path.join(dp, f) for dp, _d, fs in os.walk(os.path.join(sdir, "Default"))
                  for f in fs if f.startswith("DA_MetaData_Skin_")), "")
    dname, dimage = _meta_name(dmeta) if dmeta else ("", "")
    # Some weapons (LewisGun_01) have a real skin file for the default; use it
    # as the default entry instead of listing it twice.
    ddir = os.path.normcase(os.path.join(sdir, "Default"))
    dskin = next((os.path.join(dp, f) for dp, _d, fs in os.walk(os.path.join(sdir, "Default"))
                  for f in fs if f.startswith("DA_Skin_") and f.endswith(".json")
                  and export_of_type(_load(os.path.join(dp, f)), ("WeaponSkinDataAsset",))), "")
    out.append({"id": DEFAULT_SKIN, "path": dskin, "name": dname or "Default", "image": dimage, "variant": ""})
    for dirpath, _dirs, files in os.walk(sdir):
        if os.path.normcase(dirpath).startswith(ddir):
            continue
        for f in files:
            if not (f.startswith("DA_Skin_") and f.endswith(".json")):
                continue
            path = os.path.join(dirpath, f)
            if not export_of_type(_load(path), ("WeaponSkinDataAsset",)):
                continue
            stem = f[len("DA_Skin_"):-len(".json")]
            name, image = _meta_name(os.path.join(dirpath, f"DA_MetaData_Skin_{stem}.json"))
            m = re.search(r"_([A-Z])$", stem)
            out.append({"id": os.path.relpath(path, root).replace("\\", "/"), "path": path,
                        "name": name or f"({stem})", "image": image, "variant": m.group(1) if m else ""})
    counts = {}
    for s in out:
        counts[s["name"]] = counts.get(s["name"], 0) + 1
    for s in out:
        if counts[s["name"]] > 1 and s["variant"]:
            s["name"] = f"{s['name']} ({s['variant']})"
    # skins in the shipped list whose files aren't exported yet
    have = {s["id"] for s in out}
    for sid, s in shipped.items():
        if sid not in have and sid != DEFAULT_SKIN:
            out.append({"id": sid, "path": os.path.join(root, s["path"]) if s["path"] else "", "name": s["name"],
                        "image": s["image"], "variant": s["variant"]})
    out[1:] = sorted(out[1:], key=lambda s: s["name"].lower())
    _skins[key] = out
    return out


def skin_by_id(root, weapon, sid):
    return next((s for s in skins(root, weapon) if s["id"] == sid), None)


def export_paths(root, weapon, skin):
    """What Export Now hands the exporter: the weapon folder's own files (names, base parts), the
    skin (with everything it references), or for the default skin the Skins folder's own files (the
    default visual data assets)."""
    from . import exporter
    out = [exporter.top_level(root, weapon["dir"])]
    if skin and skin.get("path"):
        out.append(exporter.rel_path(root, skin["path"]))
    else:
        out.append(exporter.top_level(root, os.path.join(weapon["dir"], "Skins")))
    return out


def visual_path(root, weapon, skin):
    """The WeaponVisualsDataAsset a skin uses (the default visual for the default skin)."""
    if skin and skin["path"]:
        exp = export_of_type(_load(skin["path"]), ("WeaponSkinDataAsset",))
        vis = ref_path(((exp or {}).get("Properties") or {}).get("WeaponVisual"))
        if vis:
            return _game_path_to_file(root, vis)
    # Default visual: names vary (DA_Sword_02_VisualOverride_Default,
    # DA_Melee_DualBlades_VisualOverride_Default, DA_LewisGun_01_VisualOverride_Default_01)
    sdir = os.path.join(weapon["dir"], "Skins")
    files = sorted(f for f in os.listdir(sdir) if f.startswith("DA_") and f.endswith(".json"))         if os.path.isdir(sdir) else []
    for want in ("VisualOverride_Default", "VisualOverride"):
        hits = [f for f in files if want in f and export_of_type(_load(os.path.join(sdir, f)), ("WeaponVisualsDataAsset",))]
        if hits:
            return os.path.join(sdir, min(hits, key=len))
    return ""


def visual_parts(visual_file):
    """(base skeletal mesh object path, [part dict]) of a visual, unsupported parts skipped."""
    exp = export_of_type(_load(visual_file), ("WeaponVisualsDataAsset",))
    props = (exp or {}).get("Properties") or {}
    base = ref_path(props.get("BasePartSkeletalMesh"))
    parts = []
    for p in props.get("WeaponParts") or []:
        tags = set(p.get("Tags") or [])
        req = set(p.get("RequiredTags") or [])
        if tags & SKIP_TAGS or (req - DEFAULT_PART_TAGS):
            continue
        skeletal = bool(p.get("bUseSkeletalMesh"))
        mesh = ref_path(p.get("SkeletalMesh") if skeletal else p.get("Mesh")) or ref_path(p.get("Mesh"))
        if not mesh:
            continue
        parts.append({"mesh": mesh, "socket": p.get("AttachmentSocket") or "",
                      "offset": p.get("LocalOffset") or {}, "rotation": p.get("LocalRotation") or {},
                      "scale": p.get("LocalScale") or {}})
    return base, parts


def mesh_file(finder, object_path):
    for ext in (".psk", ".pskx"):
        f = finder.find(object_path, ext, stem=asset_name(object_path))
        if f:
            return f
    return ""


def _folder(object_path):
    path = object_path.split(".")[0].rsplit("/", 1)[0]
    return path.split("/Discovery/", 1)[1] if "/Discovery/" in path else path


class _QuietLog(Log):
    def info(self, msg):
        pass

    def warn(self, msg):
        pass


def _is_atlas(finder, object_path):
    """Curve atlases have no image file; their curves (JSON) are baked at build time."""
    return bool(export_of_type(finder.load_asset_json(object_path), ("CurveLinearColorAtlas",)))


def status(root, weapon, skin):
    """(level, text, {kind: [folders to export]}): 2 = meshes + textures, 1 = some textures missing
    or material files need re-saving, 0 = a mesh missing."""
    key = (root, weapon["id"], skin["id"])
    hit = _status.get(key)
    if hit is not None:
        return hit
    if not os.path.isdir(weapon["dir"]):
        res = _status[key] = (0, "Weapon not exported", {"Weapon": [f"Items/{weapon['id']}"]})
        return res
    if skin["path"] and not os.path.isfile(skin["path"]):
        res = _status[key] = (0, "Skin not exported",
                              {"Skin": [ui.game_folder(root, os.path.dirname(skin["path"]))]})
        return res
    log = _QuietLog()
    finder = AssetFinder(root, [weapon["dir"]], log)
    vis = visual_path(root, weapon, skin)
    base, parts = visual_parts(vis) if vis else ("", [])
    meshes = ([base] if base else []) + list(dict.fromkeys(p["mesh"] for p in parts))
    mesh_f, tex_f, flat_f, missing_mesh, tex_missing = [], [], [], 0, 0
    overrides = {}
    if skin["path"]:
        from .ue_assets import read_skin
        overrides = {k.lower(): v for k, v in read_skin(finder, skin["path"])[0].items()}
    seen = set()
    for m in meshes:
        if not mesh_file(finder, m):
            missing_mesh += 1
            mesh_f.append(_folder(m))
        for slot, mat in skeletal_slots(finder, asset_name(m))[0]:
            mat = overrides.get(slot.lower(), mat)
            if not mat or mat in seen:
                continue
            seen.add(mat)
            params = resolve_material(finder, object_path=mat, log=log)
            if params.flat_json:
                flat_f.append(os.path.relpath(os.path.dirname(params.flat_json), root).replace("\\", "/")
                              .split("Content/Discovery/", 1)[-1])
            for t in params.textures.values():
                if not t or any(d in t for d in PLACEHOLDER_DIRS) or asset_name(t).lower() in ("defaulttexture",):
                    continue
                if not finder.find_texture(t) and not _is_atlas(finder, t):
                    tex_missing += 1
                    tex_f.append(_folder(t))
    groups = ui.split_folders(mesh_f, tex_f, flat_f)
    if not vis:
        res = (0, "No visual definition found (export the weapon's Skins folder .json)", {})
    elif missing_mesh:
        res = (0, f"{missing_mesh} mesh(es) not exported", groups)
    elif tex_missing:
        res = (1, f"{tex_missing} texture(s) not exported", groups)
    elif flat_f:
        res = (1, "Material files need re-saving (see below)", groups)
    else:
        res = (2, "Exported", {})
    _status[key] = res
    return res


# ─────────────────────────────────────────────────────────────────────────────
# Import
# ─────────────────────────────────────────────────────────────────────────────

def _ue_matrix(loc, rot, scale, unit):
    """UE location (cm) / rotator (deg) / scale → Blender matrix (PSK axes: Y flipped)."""
    import math
    from mathutils import Euler, Matrix, Vector
    t = Vector((loc.get("X", 0.0), -loc.get("Y", 0.0), loc.get("Z", 0.0))) * unit
    e = Euler((math.radians(rot.get("Roll", 0.0)), math.radians(-rot.get("Pitch", 0.0)),
               math.radians(-rot.get("Yaw", 0.0))), "XYZ")
    s = Matrix.Diagonal((scale.get("X", 1.0) or 1.0, scale.get("Y", 1.0) or 1.0, scale.get("Z", 1.0) or 1.0, 1.0))
    return Matrix.Translation(t) @ e.to_matrix().to_4x4() @ s


def _sockets(finder, base_object_path):
    """{socket name (lower): (bone, relative location, rotation, scale)} from the skeleton JSON."""
    name = asset_name(base_object_path)
    p = finder.find(base_object_path, ".json", stem=f"{name}_Skeleton")
    out = {}
    for e in _load(p) if p else []:
        if e.get("Type") == "SkeletalMeshSocket":
            pr = e.get("Properties") or {}
            out[(pr.get("SocketName") or "").lower()] = (pr.get("BoneName") or "", pr.get("RelativeLocation") or {},
                                                        pr.get("RelativeRotation") or {}, pr.get("RelativeScale") or {})
    return out


def _mesh_sockets(finder, object_path):
    """{socket name (lower): (location, rotation, scale)} defined on a static mesh."""
    p = finder.find(object_path, ".json", stem=asset_name(object_path))
    out = {}
    for e in _load(p) if p else []:
        if e.get("Type") == "StaticMeshSocket":
            pr = e.get("Properties") or {}
            out[(pr.get("SocketName") or "").lower()] = (pr.get("RelativeLocation") or {}, pr.get("RelativeRotation") or {},
                                                        pr.get("RelativeScale") or {})
    return out


def _attach_to_part(obj, provider, rel, part, unit):
    """Parent a part to another part's socket (e.g. the suppressor on the handguard's muzzle socket)."""
    world = provider.matrix_world @ _ue_matrix(*rel, unit) @ _ue_matrix(part["offset"], part["rotation"], part["scale"], unit)
    obj.parent = provider
    obj.parent_type = "OBJECT"
    bpy.context.view_layer.update()
    obj.matrix_world = world


def _attach(obj, arm, socket, sockets, part, unit, log):
    """Parent a part to the base armature's socket / bone at the game's transform."""
    bones = {b.name.lower(): b for b in arm.data.bones}
    s = sockets.get(socket.lower())
    bone_name, rel = None, None
    if s is not None and s[0].lower() in bones:
        bone_name = bones[s[0].lower()].name
        rel = _ue_matrix(s[1], s[2], s[3], unit)
    elif socket.lower() in bones:
        bone_name = bones[socket.lower()].name
    if bone_name is None:
        log.warn(f"  {obj.name}: socket '{socket}' not found on {arm.name}; left at the origin.")
        return
    from mathutils import Matrix
    world = arm.matrix_world @ arm.data.bones[bone_name].matrix_local @ (rel or Matrix.Identity(4)) \
        @ _ue_matrix(part["offset"], part["rotation"], part["scale"], unit)
    obj.parent = arm
    obj.parent_type = "BONE"
    obj.parent_bone = bone_name
    bpy.context.view_layer.update()
    obj.matrix_world = world


def import_weapon(context, weapon, skin, log):
    root = _root(context)
    finder = importer.make_finder(root, weapon["dir"], log)
    vis = visual_path(root, weapon, skin)
    if not vis:
        log.warn(f"{weapon['name']}: no visual definition (DA_*_VisualOverride*) found.")
        return []
    base, parts = visual_parts(vis)
    bf = mesh_file(finder, base) if base else ""
    if not bf:
        log.warn(f"Base mesh {asset_name(base) if base else '?'} isn't exported (game folder {_folder(base) if base else '?'}).")
        return []
    skin_path = skin["path"] if skin and skin["path"] else ""
    choice = skin_path or "NONE"
    objs = importer.import_psk(bf, log)
    arm = next((o for o in objs if o.type == "ARMATURE"), None)
    unit = importer.import_scale()
    sockets = _sockets(finder, base)
    # Sockets can sit on other parts' meshes (the handguard's "Suppressor"
    # socket is the muzzle; the skeleton's own socket of that name is just at
    # the root), and those win. Parts on the skeleton are placed first.
    part_sockets = {i: _mesh_sockets(finder, p["mesh"]) for i, p in enumerate(parts)}
    placed, pending = {}, []
    for i, part in enumerate(parts):
        f = mesh_file(finder, part["mesh"])
        if not f:
            log.warn(f"Part {asset_name(part['mesh'])} isn't exported (game folder {_folder(part['mesh'])}).")
            continue
        new = importer.import_psk(f, log)
        top = [o for o in new if o.parent is None or o.parent not in new]
        objs += new
        provider = next((j for j, sk in part_sockets.items() if j != i and part["socket"].lower() in sk), None)
        if provider is not None:
            pending.append((i, part, top, provider))
        elif arm is not None:
            for o in top:
                _attach(o, arm, part["socket"], sockets, part, unit, log)
        placed[i] = top[0] if top else None
    for i, part, top, j in pending:
        prov = placed.get(j)
        rel = part_sockets[j][part["socket"].lower()]
        for o in top:
            if prov is not None:
                _attach_to_part(o, prov, rel, part, unit)
            elif arm is not None:
                _attach(o, arm, part["socket"], sockets, part, unit, log)
    for o in objs:
        o["ft_weapon"] = weapon["id"]
        o["ft_weapon_skin"] = skin["id"] if skin else DEFAULT_SKIN
        o["ft_outfit_skin"] = skin_path
        if o.type == "MESH":
            importer.apply_to_object(o, root, choice, log)
    coll = bpy.data.collections.new(f"{weapon['name']} - {skin['name'] if skin else 'Default'}")
    context.scene.collection.children.link(coll)
    for o in objs:
        for c in list(o.users_collection):
            c.objects.unlink(o)
        coll.objects.link(o)
    return objs


def apply_skin(context, objs, weapon, skin, log):
    """Give an imported weapon another skin. Skins that change the weapon's
    shape (another visual) are imported fresh instead; returns (objs, replaced)."""
    root = _root(context)
    old_vis = visual_path(root, weapon, skin_by_id(root, weapon, objs[0].get("ft_weapon_skin", DEFAULT_SKIN)))
    new_vis = visual_path(root, weapon, skin)
    if os.path.normcase(old_vis) != os.path.normcase(new_vis):
        mw = next((o.matrix_world.copy() for o in objs if o.parent is None), None)
        for o in objs:
            if o.name in bpy.data.objects:
                bpy.data.objects.remove(o, do_unlink=True)
        new = import_weapon(context, weapon, skin, log)
        root_obj = next((o for o in new if o.parent is None), None)
        if root_obj is not None and mw is not None:
            root_obj.matrix_world = mw
        return new, True
    skin_path = skin["path"] if skin["path"] else ""
    for o in objs:
        o["ft_weapon_skin"] = skin["id"]
        o["ft_outfit_skin"] = skin_path
        if o.type == "MESH":
            importer.apply_to_object(o, root, skin_path or "NONE", log)
    return objs, False


def weapon_objects(context):
    ids = {(o.get("ft_weapon"), c) for o in context.selected_objects if o.get("ft_weapon")
           for c in o.users_collection}
    return [o for o in bpy.data.objects if o.get("ft_weapon")
            and any((o.get("ft_weapon"), c) in ids for c in o.users_collection)]


# ─────────────────────────────────────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────────────────────────────────────

def _weapon_items(self, context):
    context = context or bpy.context
    root = _root(context)
    key = ("w", root)
    hit = _items_cache.get(key)
    if hit is not None:
        return hit
    items = []
    for w in weapons(root):
        base = os.path.isdir(w["dir"])
        items.append((w["id"], w["name"], f"{w['category']} · {w['id']}", "CHECKMARK" if base else "ERROR", _num(w["id"])))
    if not items:
        items.append(("NONE", "No weapons found", "Export Items/Firearms or Items/Melee from FModel", "ERROR", 0))
    _items_cache[key] = items
    _items_keep.append(items)
    return items


def _skin_items(self, context):
    context = context or bpy.context
    root = _root(context)
    wid = getattr(getattr(context, "scene", None), "ft_weapon", "")
    key = ("s", root, wid)
    hit = _items_cache.get(key)
    if hit is not None:
        return hit
    items = []
    w = weapon_by_id(root, wid)
    for s in skins(root, w) if w else []:
        level, text, _f = status(root, w, s)
        items.append((s["id"], s["name"], text, READY_ICON[level], _num(s["id"])))
    if not items:
        items.append((DEFAULT_SKIN, "Default", "", "BLANK1", 0))
    _items_cache[key] = items
    _items_keep.append(items)
    return items


def _on_weapon(self, context):
    try:
        context.scene.ft_weapon_skin = DEFAULT_SKIN
    except TypeError:
        pass


def _selected(context):
    root = _root(context)
    w = weapon_by_id(root, context.scene.ft_weapon)
    s = skin_by_id(root, w, context.scene.ft_weapon_skin) if w else None
    return root, w, s


class FT_OT_weapon_import(bpy.types.Operator):
    """Import the chosen weapon with the chosen skin (base mesh and its parts; stickers and charms are left out)"""
    bl_idname = "finals_texturer.weapon_import"
    bl_label = "Import Weapon"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        root, w, s = _selected(context)
        if w is None:
            self.report({"ERROR"}, "Pick a weapon first (set the Exports folder if the list is empty)")
            return {"CANCELLED"}
        level, text, groups = status(root, w, s)
        if text in ("Weapon not exported", "Skin not exported"):
            folder = next(iter(groups.values()))[0]
            self.report({"ERROR"}, f"{text}: export {folder} in FModel (Copy Path), then Rescan")
            return {"CANCELLED"}
        log = Log()
        try:
            objs = import_weapon(context, w, s, log)
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        _ctx["report"](self, log, sum(1 for o in objs if o.type == "MESH"))
        return {"FINISHED"} if objs else {"CANCELLED"}


class FT_OT_weapon_apply(bpy.types.Operator):
    """Give the selected weapon the chosen skin (skins that change the weapon's shape re-import it)"""
    bl_idname = "finals_texturer.weapon_apply"
    bl_label = "Apply Skin"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(o.get("ft_weapon") for o in context.selected_objects)

    def execute(self, context):
        root, _w, _s = _selected(context)
        objs = weapon_objects(context)
        w = weapon_by_id(root, objs[0]["ft_weapon"]) if objs else None
        s = skin_by_id(root, w, context.scene.ft_weapon_skin) if w else None
        if w is None or s is None:
            self.report({"ERROR"}, "Select a weapon imported with the Weapons tab, and pick one of its skins")
            return {"CANCELLED"}
        if w["id"] != context.scene.ft_weapon:
            self.report({"WARNING"}, f"The selected weapon is {w['name']}; pick a skin of that weapon")
            return {"CANCELLED"}
        log = Log()
        out, replaced = apply_skin(context, objs, w, s, log)
        if replaced:
            log.info("This skin changes the weapon's shape, so the weapon was imported again.")
        _ctx["report"](self, log, sum(1 for o in out if o.type == "MESH"))
        return {"FINISHED"}


class FT_OT_weapon_image(bpy.types.Operator):
    """Show the chosen skin's store image"""
    bl_idname = "finals_texturer.weapon_image"
    bl_label = "Store Image"

    def execute(self, context):
        root, w, s = _selected(context)
        path = _game_path_to_file(root, s["image"])[:-5] + ".png" if s and s.get("image") else ""
        if not path or not os.path.isfile(path):
            self.report({"WARNING"}, "Store image isn't exported (export the skin folder's textures in FModel)")
            return {"CANCELLED"}
        img = bpy.data.images.load(path, check_existing=True)
        for win in context.window_manager.windows:
            for area in win.screen.areas:
                if area.type == "IMAGE_EDITOR":
                    area.spaces.active.image = img
                    return {"FINISHED"}
        before = set(context.window_manager.windows)
        bpy.ops.wm.window_new()
        new = [x for x in context.window_manager.windows if x not in before]
        if new:
            area = max(new[0].screen.areas, key=lambda a: a.width * a.height)
            area.type = "IMAGE_EDITOR"
            area.spaces.active.image = img
        return {"FINISHED"}


class FT_OT_weapon_refresh(bpy.types.Operator):
    """Re-read weapons, skins and which are exported (use after exporting more from FModel)"""
    bl_idname = "finals_texturer.weapon_refresh"
    bl_label = "Refresh Weapons"

    def execute(self, context):
        clear_cache()
        AssetFinder.clear_cache()
        return {"FINISHED"}


def store_image(root, skin):
    p = _game_path_to_file(root, skin["image"])[:-5] + ".png" if skin and skin.get("image") else ""
    return p if p and os.path.isfile(p) else ""


def _find(context, query):
    """Weapons and skins whose name matches every word (skins of all weapons: read once, then cached)."""
    root = _root(context)
    q = query.lower().split()
    out = []
    for w in weapons(root):
        if all(t in f"{w['name']} {w['folder']}".lower() for t in q):
            out.append((f"{w['id']}|", w["name"], w["category"], -1, "TOOL_SETTINGS"))
    for w in weapons(root):
        for s in skins(root, w):
            hay = f"{s['name']} {w['name']} {w['folder']}".lower()
            if all(t in hay for t in q) and not all(t in f"{w['name']} {w['folder']}".lower() for t in q):
                out.append((f"{w['id']}|{s['id']}", s["name"], w["name"], -1, "BRUSH_DATA"))
        if len(out) > 120:
            break
    return out


def _pick(context, ident):
    sc = context.scene
    wid, sid = ident.split("|", 1)
    try:
        sc.ft_weapon = wid
        if sid:
            sc.ft_weapon_skin = sid
    except TypeError:
        pass


def _draw_options(col, context):
    from . import _prefs
    p = _prefs(context)
    if p:
        col.prop(p, "import_size", text="Size")


def draw(layout, context):
    sc = context.scene
    root = _root(context)
    search.draw(layout, context, "weap", "Search weapons and skins")
    col = layout.column(align=True)
    col.prop(sc, "ft_weapon", text="Weapon")
    col.prop(sc, "ft_weapon_skin", text="Skin")
    root, w, s = _selected(context)
    if w is not None and s is not None:
        ui.draw_preview(layout, store_image(root, s))
        level, text, groups = status(root, w, s)
        ui.draw_status(layout, level, text, groups, export=export_paths(root, w, s),
                       what=f"{w['name']} {s.get('name', '')}".strip())
    row = layout.row(align=True)
    row.operator(FT_OT_weapon_import.bl_idname, text="Import", icon="IMPORT")
    row.operator(FT_OT_weapon_apply.bl_idname, text="Apply to Selected", icon="MATERIAL")
    op = row.operator(ui.FT_OT_show_image.bl_idname, text="", icon="IMAGE_DATA")
    op.path = store_image(root, s)
    ui.draw_options(layout, context, "WEAPONS", lambda c: _draw_options(c, context))


CLASSES = (FT_OT_weapon_import, FT_OT_weapon_apply, FT_OT_weapon_image, FT_OT_weapon_refresh)


def register(root_getter, report):
    _ctx.update(root=root_getter, report=report)
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.ft_weapon = EnumProperty(name="Weapon", items=_weapon_items, update=_on_weapon,
                                             description="Weapon to import (firearms and melee)")
    bpy.types.Scene.ft_weapon_skin = EnumProperty(
        name="Skin", items=_skin_items,
        description="Weapon skin. Check = exported, i = some textures missing, warning = a mesh isn't exported")
    search.add_tab("weap", _find, _pick, "Weapon or skin name")


def unregister():
    search.remove_tab("weap")
    del bpy.types.Scene.ft_weapon_skin
    del bpy.types.Scene.ft_weapon
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
