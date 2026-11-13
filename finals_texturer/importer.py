"""Mesh import and per-slot material assignment."""

import os
import re

import bpy

from . import builders, defaults
from .ue_assets import (AssetFinder, Log, asset_name, export_of_type, mesh_base_name, ref_path,
                        find_skins, read_skin, tag_overrides,
                        resolve_material, skeletal_slots)

DUP_SUFFIX = re.compile(r"\.\d{3}$")


def clean_name(name):
    return DUP_SUFFIX.sub("", name)


def make_finder(root, mesh_dir, log):
    extra = []
    if mesh_dir:
        extra.append(mesh_dir)
    if not root and mesh_dir:
        root = guess_root(mesh_dir)
        log.warn(f"Export folder not set in the Finals panel; searching from {root}")
    return AssetFinder(root, extra, log)


def guess_root(mesh_dir):
    """Walk up to a recognisable FModel export folder; otherwise one level up.

    Never climbs further than that, so we don't index a whole drive.
    """
    d = os.path.normpath(mesh_dir)
    for _ in range(12):
        parent = os.path.dirname(d)
        if parent == d:
            break
        if os.path.basename(d).lower() in ("exports", "output"):
            return d
        if os.path.isdir(os.path.join(d, "Discovery", "Content")):
            return d
        d = parent
    return os.path.dirname(os.path.normpath(mesh_dir))


def _belongs_to(path, mesh_dir):
    """True if a skin file is this mesh's default DA or sits in its Skins folder."""
    if not mesh_dir:
        return False
    def norm(p):
        return os.path.normcase(os.path.normpath(p))
    path = norm(path)
    return (os.path.dirname(path) == norm(mesh_dir)
            or path.startswith(norm(os.path.join(mesh_dir, "Skins")) + os.sep))


def skin_parts(finder, skin_path, tags=()):
    """Meshes a skin (DA) is made of: [(object path, kind, attachment socket)].

    `tags` (e.g. "Customization.Archetype.Light") apply the parts' mesh
    replacements, like the body swapping SK_Body_M for SK_Body_L.
    """
    data = finder.load_json(skin_path) if skin_path else None
    exp = export_of_type(data, ("CharacterCustomizationItem",))
    if not exp:
        return []
    parts = []
    for vp in (exp.get("Properties") or {}).get("VisualParts") or []:
        for kind in ("SkeletalMesh", "StaticMesh"):
            path = ref_path(vp.get(kind))
            if not path:
                continue
            for to in tag_overrides(vp, tags):
                new = ref_path(to.get(f"Replacement{kind}"))
                if to.get("bOverrideMesh") and new:
                    path = new
            parts.append((path, kind, vp.get("AttachmentSocket", "")))
    return parts


def _lists_mesh(finder, skin_path, mesh_name):
    """True if the skin's VisualParts include this mesh (any LOD)."""
    if not finder or not mesh_name or not skin_path.lower().endswith(".json"):
        return False
    want = mesh_base_name(mesh_name).lower()
    return any(mesh_base_name(asset_name(path)).lower() == want for path, _k, _s in skin_parts(finder, skin_path))


def pick_skin(mesh_dir, choice, log=None, obj_name="", finder=None, mesh_name="", outfit_skin=""):
    """choice: 'NONE', 'AUTO' or a file path.

    A skin path chosen for a different item (e.g. the dropdown still showing
    the dress's skin while the beret is also selected) is ignored, and that
    object falls back to its own first skin instead. A skin that lists this
    mesh among its parts (e.g. the BikerVest LeatherWorn skin and its pins
    mesh) counts as this object's own skin. outfit_skin: the skin the object
    was imported with as part of another item; AUTO keeps using it.
    """
    if choice == "NONE":
        return ""
    if choice and choice != "AUTO":
        if (_belongs_to(choice, mesh_dir) or not mesh_dir or choice == outfit_skin
                or _lists_mesh(finder, choice, mesh_name)):
            return choice
        if log:
            log.info(f"{obj_name}: skin '{os.path.basename(choice)}' belongs to another item; "
                     "using this item's own skin instead.")
    if outfit_skin and os.path.isfile(outfit_skin):
        return outfit_skin
    skins = find_skins(mesh_dir) if mesh_dir else []
    return skins[0][1] if skins else ""


def find_part_file(finder, object_path, lod_hint=""):
    """.psk/.pskx for a skin part, preferring the LOD the user imported."""
    name = asset_name(object_path)
    base = mesh_base_name(name)
    stems = []
    if lod_hint:
        stems.append(f"{base}_{lod_hint}")
    stems += [name, f"{base}_M", f"{base}_H", f"{base}_L", base]
    for stem in dict.fromkeys(stems):
        for ext in (".psk", ".pskx"):
            path = finder.find(object_path, ext, stem=stem)
            if path:
                return path
    return ""


def _mark_socket(objs, kind, socket):
    """Static meshes (hats, glasses…) are modelled in place on the Medium body
    and attached to a bone socket in game: remember it, so the Rigging tab's
    Attach can parent them to that bone."""
    if kind == "StaticMesh" and socket and socket != "None":
        for o in objs:
            if o.type == "MESH":
                o["ft_socket"] = socket


def import_skin_parts(main_obj, root, skin_choice, log, cls=None):
    """Import the other meshes the chosen skin is made of and apply the same skin.

    cls: the body class ('L' / 'M' / 'H') the parts are for; None = the main
    mesh's own class (from its _L/_M/_H suffix). Returns the newly imported
    objects. Parts that aren't exported are listed in the log with the game
    path to export.
    """
    mesh_dir = main_obj.get("ft_source_dir", "")
    mesh_name = main_obj.get("ft_source_mesh", "")
    finder = make_finder(root, mesh_dir, log)
    skin_path = pick_skin(mesh_dir, skin_choice, finder=finder, mesh_name=mesh_name)
    if cls is None:
        cls = mesh_class(mesh_name)
    tags = class_tags(cls)
    parts = skin_parts(finder, skin_path, tags)
    if len(parts) < 2:
        return []
    own = mesh_base_name(mesh_name).lower()
    new_objs = []
    for path, kind, socket in parts:
        if mesh_base_name(asset_name(path)).lower() == own:
            continue
        f = find_part_file(finder, path, cls)
        if not f:
            log.warn(f"Skin part {asset_name(path)} isn't exported (game path {path}). "
                     "Export its folder in FModel to include it.")
            continue
        objs = import_psk(f, log)
        fit_class(objs, f, cls, log)
        _mark_socket(objs, kind, socket)
        for o in objs:
            o["ft_outfit_skin"] = skin_path
            o["ft_skin_tags"] = list(tags)
            if o.type == "MESH":
                apply_to_object(o, root, skin_path, log, tags=tags)
        new_objs += objs
    return new_objs


GAME_TO_METRES = 0.01      # the game works in centimetres


def import_scale():
    """Blender units per game unit for new imports (add-on preference)."""
    addon = bpy.context.preferences.addons.get(__package__)
    size = getattr(addon.preferences, "import_size", "GAME") if addon else "GAME"
    return 1.0 if size == "GAME" else GAME_TO_METRES


def scale_objects(objs, factor):
    """Scale freshly imported objects about the world origin (the feet) into the
    mesh / bone data itself, so every object keeps a scale of 1 (rigging and
    Auto-Rig Pro need that)."""
    from mathutils import Matrix
    m = Matrix.Scale(factor, 4)
    for o in objs:
        if o.type == "ARMATURE":
            o.data.transform(m)
        elif o.type == "MESH":
            o.data.transform(m, shape_keys=True)
        o.location = o.location * factor
        # Armature.transform() doesn't tag the pose for re-evaluation: without
        # this the pose keeps the old bone positions, so meshes deform back to
        # the old size and constraints (heads following the body) copy them.
        o.data.update_tag()
        o.update_tag(refresh={"OBJECT", "DATA"})
    bpy.context.view_layer.update()


def _apply_import_scale(objs, scale):
    scale_objects(objs, scale)
    for o in objs:
        o["ft_unit_scale"] = scale


# The Light / Heavy skeletons are the Medium one scaled about the feet: every
# bone of SK_Body_L / SK_Body_H sits at exactly x0.85 / x1.15 of SK_Body_M.
# The game scales whatever is made for Medium (hats and other static meshes on
# the head socket, single-size skeletal meshes, heads) by the same factor.
CLASS_SCALE = {"L": 0.85, "M": 1.0, "H": 1.15}
ARCHETYPE_TAG = {"L": "Customization.Archetype.Light", "H": "Customization.Archetype.Heavy"}


def mesh_class(path):
    """'L' / 'M' / 'H' from a mesh file name's suffix, '' for single-size meshes."""
    m = re.search(r"_(L|M|H)$", os.path.splitext(os.path.basename(path or ""))[0], re.IGNORECASE)
    return m.group(1).upper() if m else ""


def class_tags(cls):
    return (ARCHETYPE_TAG[cls],) if cls in ARCHETYPE_TAG else ()


def fit_class(objs, mesh_path, cls, log):
    """Scale a mesh made for another class (a Medium or single-size mesh worn
    by Light / Heavy) the way the game's skeleton does."""
    src = mesh_class(mesh_path) or "M"
    if cls not in CLASS_SCALE or src == cls:
        return 1.0
    factor = CLASS_SCALE[cls] / CLASS_SCALE[src]
    scale_objects(objs, factor)
    log.info(f"  {os.path.basename(mesh_path)} is made for {'Medium' if src == 'M' else src}: "
             f"scaled x{factor:.3f} about the feet to fit the {cls} body.")
    return factor


def cm_per_unit(obj):
    """Game centimetres per Blender unit on an object imported by the add-on."""
    return 1.0 / (obj.get("ft_unit_scale", 1.0) or 1.0)


def import_psk(filepath, log):
    missing = ("The PSK importer (io_scene_psk_psa) isn't available. Make sure it's installed "
               "and enabled. If you just removed or updated a copy of it, restart Blender.")
    # bpy.ops looks operators up lazily, so hasattr() is always True; ask for
    # the operator's type to find out whether it's really registered.
    try:
        bpy.ops.psk.import_file.get_rna_type()
    except Exception:
        raise RuntimeError(missing)
    before = set(bpy.data.objects)
    try:
        try:
            # raw vertex colours (value / 255), as Unreal hands them to shaders (blend masks of
            # the props' 2-layer material); the importer's default re-encodes them as sRGB
            res = bpy.ops.psk.import_file(filepath=filepath, vertex_color_space="LINEAR")
        except TypeError:
            res = bpy.ops.psk.import_file(filepath=filepath)
    except AttributeError:
        raise RuntimeError(missing)
    if "FINISHED" not in res:
        raise RuntimeError(f"PSK import failed for {filepath}")
    new = [o for o in bpy.data.objects if o not in before]
    scale = import_scale()
    if scale != 1.0:
        _apply_import_scale(new, scale)
    stem = os.path.splitext(os.path.basename(filepath))[0]
    for o in new:
        o["ft_source_dir"] = os.path.dirname(filepath)
        o["ft_source_mesh"] = stem
        keys = o.data.shape_keys if o.type == "MESH" else None
        if keys:
            # The game's own shape keys (body types, push_*, shrink_*…), so
            # later tools only ever change these and not ones the user adds.
            o["ft_psk_shapekeys"] = [k.name for k in keys.key_blocks[1:]]
    log.info(f"Imported {os.path.basename(filepath)}: {len(new)} object(s)")
    return new


def _match_slot(slots, psk_name, index):
    """SK slot entry for a mesh material, by material name, then by index."""
    for slot, mat_path in slots:
        if asset_name(mat_path).lower() == psk_name.lower():
            return slot, mat_path
    for slot, mat_path in slots:
        if slot.lower() == psk_name.lower():
            return slot, mat_path
    if index < len(slots):
        return slots[index]
    return "", ""


def apply_to_object(obj, root, skin_choice, log, tags=None, slot_overrides=None):
    """tags: TagOverrides to apply (see read_skin); None = the ones the object
    was imported with (stored by the Character tab). slot_overrides: {slot
    index: material object path} that win over everything (a Blueprint's
    OverrideMaterials); stored on the object so re-applying keeps them."""
    if obj.type != "MESH":
        return 0
    if tags is None:
        tags = tuple(obj.get("ft_skin_tags", ()))
    if slot_overrides is None:
        slot_overrides = {int(k): v for k, v in (obj.get("ft_slot_overrides") or {}).items()}
    else:
        obj["ft_slot_overrides"] = {str(k): v for k, v in slot_overrides.items()}
    mesh_dir = obj.get("ft_source_dir", "")
    mesh_name = obj.get("ft_source_mesh", "") or clean_name(obj.name)
    finder = make_finder(root, mesh_dir, log)

    # Remember the material names the PSK came with, so re-applying with
    # another skin still knows which game slot each Blender slot is.
    if "ft_psk_materials" not in obj:
        obj["ft_psk_materials"] = [clean_name(s.material.name) if s.material else ""
                                   for s in obj.material_slots]
    psk_names = list(obj["ft_psk_materials"])

    slots, sk_json = skeletal_slots(finder, mesh_name, mesh_dir)
    if sk_json:
        log.info(f"{obj.name}: slot names from {os.path.basename(sk_json)}")
    else:
        log.warn(f"{obj.name}: no {mesh_name}.json found, so slots are matched by material name. "
                 "Export the mesh's .json (SK_/SM_) next to the .psk if a skin lands on the wrong slot.")

    skin_path = pick_skin(mesh_dir, skin_choice, log, obj.name, finder=finder, mesh_name=mesh_name,
                          outfit_skin=obj.get("ft_outfit_skin", ""))
    if not skin_path and skin_choice != "NONE":
        if not mesh_dir:
            log.warn(f"{obj.name}: wasn't imported with Finals Texturer, so its Skins folder is "
                     "unknown. Re-import it with the Finals importer to get skin colours.")
        else:
            log.info(f"{obj.name}: no Skins folder next to the mesh; using base materials.")
    overrides, lone_mi = ({}, None)
    obj["ft_skin"] = skin_path or ""      # read by the Rigging tab (shape-key tags)
    if skin_path:
        overrides, lone_mi = read_skin(finder, skin_path, tags=tags)
        log.info(f"{obj.name}: skin {os.path.basename(skin_path)}")
    overrides_ci = {k.lower(): v for k, v in overrides.items()}
    lone_used = False

    done = 0
    for i, mslot in enumerate(obj.material_slots):
        psk_name = psk_names[i] if i < len(psk_names) else (clean_name(mslot.material.name) if mslot.material else "")
        slot_name, base_path = _match_slot(slots, psk_name, i)
        target_path, target_json = base_path, None
        if i in slot_overrides:
            target_path = slot_overrides[i]
        elif slot_name and slot_name.lower() in overrides_ci:
            target_path = overrides_ci[slot_name.lower()]
        elif not target_path and psk_name:
            target_json = finder.find("", ".json", stem=psk_name)
            if not target_json:
                log.warn(f"  Slot {i} '{psk_name}': no material JSON found.")
                continue

        if lone_mi and not lone_used and not (slot_name and slot_name.lower() in overrides_ci):
            base = resolve_material(finder, object_path=target_path, json_path=target_json, log=Log())
            if defaults.shader_type(base) == defaults.PREVIEW:
                target_path, target_json, lone_used = None, lone_mi, True

        log.info(f"  Slot {i} '{slot_name or psk_name}':")
        params = resolve_material(finder, object_path=target_path, json_path=target_json, log=log)
        if not params.chain:
            continue
        log.info(f"    {' > '.join(params.chain)}")
        mat = _material_for(params)
        uvs = obj.data.uv_layers
        detail_uv = uvs[1].name if len(uvs) > 1 else None
        kind = builders.build_material(mat, params, finder, log, detail_uv=detail_uv,
                                       cm_per_unit=cm_per_unit(obj))
        log.info(f"    built as {kind}")
        mslot.material = mat
        done += 1
    return done


def _material_for(params):
    """The Blender material for a game material: by name, but kept apart when two different game
    files share a name (MI_ATK_BarrelOil_01 exists for the oil and the powder barrel): the second one
    gets "<name> (<folder>)", so building one doesn't overwrite the other."""
    src = os.path.normcase(os.path.normpath(params.source_files[0])) if params.source_files and params.source_files[0] else ""
    mat = bpy.data.materials.get(params.name)
    if mat is not None and src and mat.get("ft_source", src) != src:
        mat = next((m for m in bpy.data.materials if m.get("ft_source") == src), None)
        if mat is None:
            folder = os.path.basename(os.path.dirname(src))
            mat = bpy.data.materials.new(f"{params.name} ({folder})")
    if mat is None:
        mat = bpy.data.materials.new(params.name)
    if src:
        mat["ft_source"] = src
    return mat


def write_log(log):
    txt = bpy.data.texts.get("Finals Texturer Log") or bpy.data.texts.new("Finals Texturer Log")
    txt.clear()
    txt.write("\n".join(log.lines) + "\n")


def import_item(skin_path, root, log, whole_skin=True, lod_hint=""):
    """Import an item straight from its skin file (DA), e.g. a library search hit.

    The first exported mesh of the skin becomes the main object; with
    whole_skin the skin's other meshes come along. lod_hint: the body class
    ('L' / 'M' / 'H'): picks the class's meshes (incl. the skin's class mesh
    swaps) and scales meshes made for Medium to it. Returns the new objects;
    meshes that aren't exported are reported in the log.
    """
    finder = make_finder(root, os.path.dirname(skin_path), log)
    tags = class_tags(lod_hint)
    parts = skin_parts(finder, skin_path, tags)
    if not parts:
        log.warn(f"{os.path.basename(skin_path)} lists no meshes (it may change the body or "
                 "another item instead, e.g. face paint or a voice).")
        return []
    main_file = ""
    main_kind = main_socket = ""
    for path, main_kind, main_socket in parts:
        main_file = find_part_file(finder, path, lod_hint)
        if main_file:
            break
    if not main_file:
        for path, _k, _s in parts:
            log.warn(f"Mesh {asset_name(path)} isn't exported (game path {path}). "
                     "Export its folder in FModel, then try again.")
        return []
    objs = import_psk(main_file, log)
    if lod_hint:
        fit_class(objs, main_file, lod_hint, log)
    _mark_socket(objs, main_kind, main_socket)
    main =next((o for o in objs if o.type == "MESH"), None)
    for o in objs:
        o["ft_outfit_skin"] = skin_path
        o["ft_skin_tags"] = list(tags)
        if o.type == "MESH":
            apply_to_object(o, root, skin_path, log, tags=tags)
    if whole_skin and main is not None:
        objs += import_skin_parts(main, root, skin_path, log, cls=lod_hint or None)
    return objs


def missing_meshes(root, skin_path, log=None):
    """Mesh object paths of a skin that aren't exported (checked live)."""
    finder = make_finder(root, os.path.dirname(skin_path), log or Log())
    return [p for p, _k, _s in skin_parts(finder, skin_path) if not find_part_file(finder, p)]
