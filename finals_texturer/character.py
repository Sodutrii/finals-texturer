"""Character tab: body + face (head mesh and skin tone) + body type.

How the game builds a character (checked on the exported DA_Head_* files):
* A face is a CharacterCustomizationItem (Heads/<Face>/…/DA_Head_*.json) with
  two VisualParts: its head mesh and the body SK_Body_M. TagOverrides swap the
  body for SK_Body_L / SK_Body_H on "Customization.Archetype.Light/Heavy".
* Its MaterialOverrides put the face's skin material on the body ("BaseBody"
  slot, MI_Body_Face_XX: Light or Dark textures x ColorMultiply) and, for
  special faces, on the head.
* "Customization.Quality.Frontend" TagOverrides swap in the menu materials:
  the same textures with finer shading settings. We always use those.
* Body type (masculine / feminine, muscular on Heavy only) is a shape key on
  the body, head and clothing; ActivatesTags "Customization.Shape.*.<key>"
  switch on more keys (head_neck_match). See shapekeys.py.
"""

import os
import re
import zlib

import bpy
from bpy.props import BoolProperty, EnumProperty

from . import importer, shapekeys
from .ue_assets import AssetFinder, Log, asset_name, export_of_type, ref_path, resolve_material, \
    skeletal_slots, tag_overrides
from . import defaults

FRONTEND = "Customization.Quality.Frontend"
ARCHETYPE = {"L": "Customization.Archetype.Light", "H": "Customization.Archetype.Heavy"}
CLASS_NAME = {"L": "Light", "M": "Medium", "H": "Heavy"}
HEADS_REL = os.path.join("Discovery", "Content", "Discovery", "Characters", "Heads")
# Texture parameters our builders use, per shader type (for the export check).
USED_TEXTURES = {
    defaults.SKIN: ("BodyColorMap", "HandsColorMap", "BodyNormalMap", "HandsNormalMap"),
    defaults.FACE: ("BaseColor", "Normal"),
    defaults.EYE: ("IrisColor", "ScleraColor"),
    defaults.EYELASH: ("Eyelash Texture",),
    defaults.TEETH: ("Color", "Normal"),
}
READY_ICON = {2: "CHECKMARK", 1: "INFO", 0: "ERROR"}

_ctx = {"root": lambda context: "", "report": None}
_faces = {}          # root → [face dict]
_status = {}         # (root, face id, class) → (level, text, [folders to export])
_items = []          # every enum list handed to Blender (kept alive)
_items_cache = {}    # (root, class) → enum items
_mat_tex = {}        # (root, material path) → texture paths our builders use


class _QuietLog(Log):
    """For the export check: collects nothing and prints nothing."""

    def info(self, msg):
        pass

    def warn(self, msg):
        pass


def _root(context):
    return _ctx["root"](context)


def clear_cache():
    _faces.clear()
    _status.clear()
    _mat_tex.clear()
    _items_cache.clear()
    shapekeys.clear_cache()


def tags_for(cls):
    return tuple(t for t in (ARCHETYPE.get(cls), FRONTEND) if t)


# ─────────────────────────────────────────────────────────────────────────────
# Face list
# ─────────────────────────────────────────────────────────────────────────────

def _load(path):
    import json
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _heads_dir(root):
    d = os.path.join(root, HEADS_REL)
    if os.path.isdir(d):
        return d
    for dirpath, dirs, _files in os.walk(root):
        if os.path.basename(dirpath) == "Heads" and os.path.basename(os.path.dirname(dirpath)) == "Characters":
            return dirpath
    return ""


_GENERIC = re.compile(r"^(heads?|face|characters?|blankface)\b", re.IGNORECASE)


def _display_name(da_path):
    """In-game name; "Head {0}" faces get their number; placeholders in brackets."""
    stem = os.path.basename(da_path)[len("DA_"):-len(".json")]          # Head_Face_01_Base
    meta = export_of_type(_load(os.path.join(os.path.dirname(da_path), f"DA_MetaData_{stem}.json")),
                          ("CustomizationItemMetaDataItem",))
    pretty = ((meta or {}).get("Properties") or {}).get("PrettyName") or {}
    name = pretty.get("SourceString") or ""
    m = re.search(r"Face_(\d+)", stem)
    if name and "{0}" in name:
        name = name.replace("{0}", str(int(m.group(1))) if m else "")
    if name:
        return name, True
    placeholder = pretty.get("CultureInvariantString") or ""
    if placeholder and not _GENERIC.match(placeholder):
        return f"({placeholder})", False
    short = stem[len("Head_"):] if stem.startswith("Head_") else stem
    return f"({short.replace('_Base', '').replace('_', ' ')})", False


def faces(root):
    """Every face (DA_Head_*.json) under the export folder, named, sorted."""
    if not root:
        return []
    hit = _faces.get(root)
    if hit is not None:
        return hit
    out = []
    hd = _heads_dir(root)
    for dirpath, _dirs, files in os.walk(hd) if hd else []:
        for f in files:
            if f.startswith("DA_Head_") and f.endswith(".json"):
                path = os.path.join(dirpath, f)
                name, real = _display_name(path)
                out.append({"id": os.path.relpath(path, root).replace("\\", "/"), "path": path,
                            "name": name, "real": real})
    out.sort(key=lambda e: (not e["real"], e["name"].lower()))
    _faces[root] = out
    return out


def face_by_id(root, face_id):
    return next((f for f in faces(root) if f["id"] == face_id), None)


def face_parts(finder, da_path, cls):
    """[(object path, kind, role)] for a face with the class's mesh swaps;
    role 'body', 'head' or 'extra'."""
    exp = export_of_type(finder.load_json(da_path), ("CharacterCustomizationItem",))
    tags = tags_for(cls)
    out = []
    for vp in ((exp or {}).get("Properties") or {}).get("VisualParts") or []:
        for kind in ("SkeletalMesh", "StaticMesh"):
            path = ref_path(vp.get(kind))
            if not path:
                continue
            for to in tag_overrides(vp, tags):
                new = ref_path(to.get(f"Replacement{kind}"))
                if to.get("bOverrideMesh") and new:
                    path = new
            name = asset_name(path)
            role = "head" if vp.get("bIsHeadMesh") else "body" if name.startswith("SK_Body_") else "extra"
            out.append((path, kind, role))
    return out


def mesh_file(finder, object_path):
    """The exact .psk/.pskx for a mesh (no fallback to another class's LOD)."""
    for ext in (".psk", ".pskx"):
        p = finder.find(object_path, ext, stem=asset_name(object_path))
        if p:
            return p
    return ""


def _folder(object_path):
    path = object_path.split(".")[0].rsplit("/", 1)[0]
    return path.split("/Discovery/", 1)[1] if "/Discovery/" in path else path


def _used_textures(finder, root, mat, log):
    key = (root, mat)
    hit = _mat_tex.get(key)
    if hit is None:
        params = resolve_material(finder, object_path=mat, log=log)
        hit = [params.t(n) for n in USED_TEXTURES.get(defaults.shader_type(params), ())]
        hit = [t for t in hit if t and asset_name(t).lower() not in defaults.NULL_TEXTURES]
        _mat_tex[key] = hit
    return hit


def status(root, face, cls):
    """(ready level, text, [game folders to export]) for a face and class.
    2 = meshes and textures exported, 1 = meshes but some textures, 0 = a mesh is missing."""
    key = (root, face["id"], cls)
    hit = _status.get(key)
    if hit is not None:
        return hit
    log = _QuietLog()
    finder = AssetFinder(root, [os.path.dirname(face["path"])], log)
    parts = face_parts(finder, face["path"], cls)
    folders, missing_mesh = [], 0
    overrides = importer.read_skin(finder, face["path"], tags=tags_for(cls))[0]
    overrides = {k.lower(): v for k, v in overrides.items()}
    tex_missing = 0
    for path, _kind, _role in parts:
        if not mesh_file(finder, path):
            missing_mesh += 1
            folders.append(_folder(path))
        slots, _p = skeletal_slots(finder, asset_name(path))
        for slot, mat in slots:
            mat = overrides.get(slot.lower(), mat)
            if not mat:
                continue
            for tex in _used_textures(finder, root, mat, log):
                if not finder.find_texture(tex):
                    tex_missing += 1
                    folders.append(_folder(tex))
    folders = list(dict.fromkeys(folders))
    if missing_mesh:
        res = (0, f"{missing_mesh} mesh(es) not exported", folders)
    elif tex_missing:
        res = (1, f"{tex_missing} texture(s) not exported", folders)
    else:
        res = (2, "Exported", [])
    _status[key] = res
    return res


def _face_items(self, context):
    """Dropdown entries. Blender may call this without a context (e.g. while
    setting the value), and needs the same strings kept alive between calls,
    so the list is built once per export folder and class and reused."""
    context = context or bpy.context
    root = _root(context)
    cls = getattr(getattr(context, "scene", None), "ft_char_class", "M")
    key = (root, cls)
    hit = _items_cache.get(key)
    if hit is not None:
        return hit
    items, used = [], set()
    for f in faces(root):
        level, text, _folders = status(root, f, cls)
        # Stable per face but small: the UI passes enum values through a float,
        # and large numbers (e.g. a full CRC) come back changed, so a picked
        # face matched nothing and the dropdown showed empty.
        num = zlib.crc32(f["id"].encode("utf-8")) % 1_000_000
        while num in used:
            num = (num + 1) % 1_000_000
        used.add(num)
        items.append((f["id"], f["name"], f"{f['id']} · {text}", READY_ICON[level], num))
    if not items:
        items.append(("NONE", "No faces found", "Export Characters/Heads from FModel", "ERROR", 0))
    _items_cache[key] = items
    _items.append(items)          # keep every list ever handed to Blender alive
    return items


# ─────────────────────────────────────────────────────────────────────────────
# Import / update
# ─────────────────────────────────────────────────────────────────────────────

REST_PROP = "ft_rest_mats"   # body bones' game rest matrices (armature space), kept through rig edits


def store_rest(arm):
    """Remember the body skeleton's game rest pose before any rig edits."""
    if arm is not None and arm.type == "ARMATURE" and REST_PROP not in arm:
        arm[REST_PROP] = {b.name: [v for row in b.matrix_local for v in row] for b in arm.data.bones}


def _rest_matrix(arm, name):
    from mathutils import Matrix
    stored = arm.get(REST_PROP)
    if stored is not None:
        flat = stored.get(name)
        return Matrix([list(flat[i:i + 4]) for i in range(0, 16, 4)]) if flat is not None else None
    b = arm.data.bones.get(name)
    return b.matrix_local.copy() if b else None


def fit_to_body(arm, body_arm, log):
    """Fit a head (or other part with its own skeleton) to the body's class.

    Heads are made for the Medium body. The Light and Heavy bodies are uniform
    scales of it about the feet (x0.85 / x1.15: head, neck and shoulders all
    agree), so the part's armature is scaled about the body's origin by the
    median ratio of matching bone heights. Its bones then copy the body bones'
    location and rotation (not scale), so posing the body carries it along.
    """
    from statistics import median
    origin = body_arm.matrix_world.translation
    ratios = []
    for b in arm.data.bones:
        bm = _rest_matrix(body_arm, b.name)
        if bm is None:
            continue
        pb = body_arm.matrix_world @ bm.translation - origin
        ph = arm.matrix_world @ b.head_local - origin
        if ph.length > 10.0:          # skip bones near the feet (cm)
            ratios.append(pb.length / ph.length)
    scale = median(ratios) if ratios else 1.0
    if abs(scale - 1.0) > 1e-4:
        arm.location = origin + (arm.location - origin) * scale
        arm.scale = arm.scale * scale
    # Moving or scaling the body moves the head with it (also keeps their rest
    # poses lined up for the Rigging tab's attach).
    bpy.context.view_layer.update()
    mw = arm.matrix_world.copy()
    arm.parent = body_arm
    arm.matrix_parent_inverse = body_arm.matrix_world.inverted()
    arm.matrix_world = mw
    for pb in arm.pose.bones:
        if body_arm.data.bones.get(pb.name) and not any(c.name.startswith("Follow body") for c in pb.constraints):
            for kind in ("COPY_LOCATION", "COPY_ROTATION"):
                c = pb.constraints.new(kind)
                c.name = "Follow body"
                c.target, c.subtarget = body_arm, pb.name
    log.info(f"Scaled {arm.name} x{scale:.3f} about the feet to fit {body_arm.name}.")
    return scale


def _fit_parts(groups, body_arm, log):
    for objs, role in groups:
        if role == "body":
            continue
        for a in [o for o in objs if o.type == "ARMATURE"]:
            fit_to_body(a, body_arm, log)


def _stamp(objs, face, cls, gender, muscular, role):
    for o in objs:
        o["ft_char_role"] = role
        o["ft_char_class"] = cls
        o["ft_char_gender"] = gender
        o["ft_char_muscular"] = bool(muscular)
        o["ft_char_face"] = face["id"]


def _import_parts(root, face, cls, roles, log):
    """Import a face's parts with the given roles; returns [(objs, role)]."""
    finder = importer.make_finder(root, os.path.dirname(face["path"]), log)
    tags = tags_for(cls)
    out = []
    for path, _kind, role in face_parts(finder, face["path"], cls):
        if role not in roles:
            continue
        f = mesh_file(finder, path)
        if not f:
            log.warn(f"{asset_name(path)} isn't exported (game folder {_folder(path)}). "
                     "Export it in FModel to include it.")
            continue
        objs = importer.import_psk(f, log)
        for o in objs:
            o["ft_outfit_skin"] = face["path"]
            o["ft_skin_tags"] = list(tags)
            if o.type == "MESH":
                importer.apply_to_object(o, root, face["path"], log, tags=tags)
        out.append((objs, role))
    return out


def _collection(name, objs, context):
    coll = bpy.data.collections.new(name)
    context.scene.collection.children.link(coll)
    for o in objs:
        for c in list(o.users_collection):
            c.objects.unlink(o)
        coll.objects.link(o)
    return coll


def import_character(context, face, cls, gender, muscular, log):
    root = _root(context)
    groups = _import_parts(root, face, cls, ("body", "head", "extra"), log)
    body_arm = next((o for g, role in groups if role == "body" for o in g if o.type == "ARMATURE"), None)
    if body_arm is not None:
        store_rest(body_arm)
        _fit_parts(groups, body_arm, log)
    objs = []
    for g, role in groups:
        _stamp(g, face, cls, gender, muscular, role)
        objs += g
    if not objs:
        return []
    mism, active = shapekeys.apply_character(objs, cls, gender, muscular)
    if active:
        log.info(f"Shape keys on: {', '.join(sorted(active))}")
    for name in mism:
        log.warn(f"{name} has no {CLASS_NAME[cls]} body-type shape keys.")
    _collection(f"{face['name'].strip('()')} ({CLASS_NAME[cls]})", objs, context)
    return objs


def character_objects(context):
    """Objects of the character(s) the selection belongs to."""
    faces_sel = {o.get("ft_char_face") for o in context.selected_objects if o.get("ft_char_class")}
    colls = {c for o in context.selected_objects if o.get("ft_char_class") for c in o.users_collection}
    out = [o for o in bpy.data.objects
           if o.get("ft_char_class") and o.get("ft_char_face") in faces_sel
           and any(c in colls for c in o.users_collection)]
    return out


def update_character(context, objs, face, gender, muscular, log, report):
    """Give an imported character another face and/or body type (same class)."""
    from . import rigging
    root = _root(context)
    cls = objs[0]["ft_char_class"]
    tags = tags_for(cls)
    body = [o for o in objs if o.get("ft_char_role") == "body"]
    old = [o for o in objs if o.get("ft_char_role") in ("head", "extra")]
    colls = body[0].users_collection if body else (objs[0].users_collection if objs else [])
    # body: new skin tone
    for o in body:
        o["ft_outfit_skin"] = face["path"]
        o["ft_skin_tags"] = list(tags)
        if o.type == "MESH":
            importer.apply_to_object(o, root, face["path"], log, tags=tags)
    _stamp(body, face, cls, gender, muscular, "body")
    # head: replace when the face changed (or it's missing)
    new = []
    same_face = all(o.get("ft_char_face") == face["id"] for o in old) and old
    if not same_face:
        for o in old:
            if o.name in bpy.data.objects:
                bpy.data.objects.remove(o, do_unlink=True)
        groups = _import_parts(root, face, cls, ("head", "extra"), log)
        body_arm = next((m.object for o in body if o.type == "MESH" for m in o.modifiers
                         if m.type == "ARMATURE" and m.object), None)
        if body_arm is not None:
            _fit_parts(groups, body_arm, log)
        for g, role in groups:
            _stamp(g, face, cls, gender, muscular, role)
            new += g
        for o in new:
            for c in list(o.users_collection):
                c.objects.unlink(o)
            for c in colls:
                c.objects.link(o)
        # a body already bound to a simplified rig: bind the new head too
        master = next((m.object for o in body if o.type == "MESH" for m in o.modifiers
                       if m.type == "ARMATURE" and m.object and m.object.get(rigging.MASTER_PROP)), None)
        if master is not None and new:
            rigging.attach_pieces(master, new, True, report)
    else:
        for o in old:
            _stamp([o], face, cls, gender, muscular, o.get("ft_char_role") or "head")
    everything = [o for o in body + new + (old if same_face else []) if o.name in bpy.data.objects]
    # include clothing bound to the same rig, so their body-type keys follow too
    arms = {m.object for o in body if o.type == "MESH" for m in o.modifiers if m.type == "ARMATURE" and m.object}
    for a in arms:
        a["ft_char_gender"], a["ft_char_muscular"], a["ft_char_face"] = gender, bool(muscular), face["id"]
        everything += [m for m in rigging.meshes_using_armature(a) if m not in everything]
    mism, _active = shapekeys.apply_character(everything, cls, gender, muscular)
    for name in mism:
        log.warn(f"{name} has no {CLASS_NAME[cls]} body-type shape keys.")
    return everything


# ─────────────────────────────────────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────────────────────────────────────

def _report_fn(op):
    return lambda msg: op.report({"WARNING"} if msg.startswith(("WARNING", "Note")) else {"INFO"}, msg)


class FT_OT_char_import(bpy.types.Operator):
    """Import the body for the chosen class with the chosen face (head mesh and skin tone) and body type"""
    bl_idname = "finals_texturer.char_import"
    bl_label = "Import Character"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        sc = context.scene
        root = _root(context)
        face = face_by_id(root, sc.ft_char_face)
        if face is None:
            self.report({"ERROR"}, "Pick a face first (set the Exports folder if the list is empty)")
            return {"CANCELLED"}
        log = Log()
        try:
            objs = import_character(context, face, sc.ft_char_class, sc.ft_char_gender.lower(),
                                    sc.ft_char_muscular, log)
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        _status.clear()
        _items_cache.clear()
        _ctx["report"](self, log, sum(1 for o in objs if o.type == "MESH"))
        return {"FINISHED"} if objs else {"CANCELLED"}


class FT_OT_char_update(bpy.types.Operator):
    """Give the selected character the chosen face and body type (the class stays: re-import to change it)"""
    bl_idname = "finals_texturer.char_update"
    bl_label = "Update Selected"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(o.get("ft_char_class") for o in context.selected_objects)

    def execute(self, context):
        sc = context.scene
        root = _root(context)
        face = face_by_id(root, sc.ft_char_face)
        objs = character_objects(context)
        if face is None or not objs:
            self.report({"ERROR"}, "Select a character imported with the Character tab and pick a face")
            return {"CANCELLED"}
        cls = objs[0]["ft_char_class"]
        if cls != sc.ft_char_class:
            self.report({"WARNING"}, f"This character is {CLASS_NAME[cls]}; kept that class "
                                     "(import a new character to change class)")
        log = Log()
        try:
            out = update_character(context, objs, face, sc.ft_char_gender.lower(), sc.ft_char_muscular,
                                   log, _report_fn(self))
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        _ctx["report"](self, log, sum(1 for o in out if o.type == "MESH"))
        return {"FINISHED"}


class FT_OT_char_refresh(bpy.types.Operator):
    """Re-read the face list and which faces are exported (use after exporting more from FModel)"""
    bl_idname = "finals_texturer.char_refresh"
    bl_label = "Refresh Faces"

    def execute(self, context):
        clear_cache()
        AssetFinder.clear_cache()
        return {"FINISHED"}


def draw(layout, context):
    sc = context.scene
    root = _root(context)
    if not root:
        layout.label(text="Set the Exports folder first", icon="ERROR")
        return
    layout.row().prop(sc, "ft_char_class", expand=True)
    row = layout.row(align=True)
    row.prop(sc, "ft_char_face", text="Face")
    row.operator(FT_OT_char_refresh.bl_idname, text="", icon="FILE_REFRESH")
    face = face_by_id(root, sc.ft_char_face)
    if face is not None:
        level, text, folders = status(root, face, sc.ft_char_class)
        col = layout.column(align=True)
        col.label(text=text, icon=READY_ICON[level])
        for f in folders[:4]:
            col.label(text=f"Export: {f}", icon="EXPORT")
    row = layout.row(align=True)
    row.prop(sc, "ft_char_gender", expand=True)
    sub = row.row()
    sub.enabled = sc.ft_char_class == "H"
    sub.prop(sc, "ft_char_muscular")
    row = layout.row(align=True)
    row.operator(FT_OT_char_import.bl_idname, icon="IMPORT")
    row.operator(FT_OT_char_update.bl_idname, icon="FILE_REFRESH")


CLASSES = (FT_OT_char_import, FT_OT_char_update, FT_OT_char_refresh)


def register(root_getter, report):
    _ctx.update(root=root_getter, report=report)
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.ft_char_class = EnumProperty(
        name="Class", default="M",
        items=(("L", "Light", "Light class body"), ("M", "Medium", "Medium class body"),
               ("H", "Heavy", "Heavy class body")))
    bpy.types.Scene.ft_char_face = EnumProperty(
        name="Face", items=_face_items,
        description="Face (head and skin tone). Check = exported, i = some textures missing, "
                    "warning = a mesh isn't exported")
    bpy.types.Scene.ft_char_gender = EnumProperty(
        name="Body Type", default="MALE",
        items=(("MALE", "Male", "Masculine body shape"), ("FEMALE", "Female", "Feminine body shape")))
    bpy.types.Scene.ft_char_muscular = BoolProperty(
        name="Muscular", description="Muscular body shape (Heavy class only)")


def unregister():
    for p in ("ft_char_class", "ft_char_face", "ft_char_gender", "ft_char_muscular"):
        delattr(bpy.types.Scene, p)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
