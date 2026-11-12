"""Clothing tab: pick an item by category → item → skin (or search by in-game
name), see what's exported, import it or apply the skin to selected meshes."""

import glob
import os
import re
import zlib

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from . import defaults, importer, library, library_ui, search, ui
from .ue_assets import AssetFinder, Log, asset_name, find_skins, read_skin, resolve_material

CHARS_REL = os.path.join("Discovery", "Content", "Discovery", "Characters")
# texture folders covered by Check Setup (shared by many items): not listed per item
SHARED_MARKERS = ("/MaterialLibrary/", "/EmbarkScript/", "/Heads/Shared/", "/Items/Shared/", "/Engine/",
                  "/Pioneer/", "/Textures/Debug/")

_ctx = {"root": lambda context: "", "report": None}
_cache = {}          # (kind, key) → list
_status = {}         # (root, skin path, class) → (level, text, groups)
_enum_keep = []      # every enum list handed to Blender (kept alive)


def _root(context):
    return _ctx["root"](context)


def clear_cache():
    _cache.clear()
    _status.clear()


def _pretty(folder):
    """'BikerLoosePants' → 'Biker Loose Pants'."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", folder).replace("_", " ")


# ── catalog ──────────────────────────────────────────────────────────────────

def _lib_entries():
    lib = library_ui._get_lib(bpy.context)
    return lib["entries"] if lib else []


def _entry_parts(e):
    """('Casual', 'BasicTshirt', 'Cotton_X' or '') from a library entry's folder, or None."""
    parts = e["folder"].replace("\\", "/").split("Characters/", 1)[-1].split("/")
    if len(parts) < 3 or parts[1] != "Assets":
        return None
    return parts[0], parts[2], (parts[4] if len(parts) > 4 and parts[3] == "Skins" else "")


def categories(root):
    """Category folders under Characters: exported ones plus those in the name list."""
    key = ("cats", root)
    if key not in _cache:
        base = os.path.join(root, CHARS_REL)
        out = set()
        if os.path.isdir(base):
            out |= {c for c in os.listdir(base) if os.path.isdir(os.path.join(base, c, "Assets"))}
        out |= {p[0] for p in map(_entry_parts, _lib_entries()) if p and p[0] not in ("Heads", "Body")}
        _cache[key] = sorted(out)
    return _cache[key]


def items(root, cat):
    key = ("items", root, cat)
    if key not in _cache:
        base = os.path.join(root, CHARS_REL, cat, "Assets")
        found = {}
        if os.path.isdir(base):
            for it in sorted(os.listdir(base)):
                d = os.path.join(base, it)
                if not os.path.isdir(d) or it in ("Shared", "Textures", "Materials"):
                    continue
                files = os.listdir(d)
                if "Skins" in files or any(f.endswith((".psk", ".pskx")) or
                                           (f.startswith("DA_") and f.endswith(".json")) for f in files):
                    found[it] = d
        for p in map(_entry_parts, _lib_entries()):
            if p and p[0] == cat and p[1] not in found:
                found[p[1]] = os.path.join(base, p[1])
        _cache[key] = [{"id": f"{cat}/{it}", "dir": d, "name": _pretty(it)} for it, d in sorted(found.items())]
    return _cache[key]


def _lib_names(context):
    """Skin folder (relative, lower case) → library entry, if a library exists."""
    lib = library_ui._get_lib(context)
    if not lib:
        return {}
    key = ("libnames", id(lib))
    if key not in _cache:
        _cache[key] = {e["folder"].replace("\\", "/").lower(): e for e in lib["entries"] if e["kind"] == "item"}
    return _cache[key]


def skins(context, root, item):
    key = ("skins", root, item["id"])
    if key not in _cache:
        names = _lib_names(context)
        out = []
        for name, path in find_skins(item["dir"]):
            folder = os.path.relpath(os.path.dirname(path), root).replace("\\", "/").lower()
            e = names.get(folder)
            label = library.display_name(e) if e else name
            if e and name != "Default" and e.get("name"):
                label = f"{e['name']} ({name})"
            out.append({"id": os.path.relpath(path, root).replace("\\", "/"), "path": path, "name": label,
                        "entry": e})
        # skins in the name list that aren't exported yet
        have = {s["id"].lower() for s in out}
        cat, it = item["id"].split("/", 1)
        for e in _lib_entries():
            p = _entry_parts(e)
            if e["kind"] != "item" or not p or (p[0], p[1]) != (cat, it) or not e.get("da"):
                continue
            if e["da"].replace("\\", "/").lower() in have:
                continue
            label = f"{e['name']} ({p[2] or 'Default'})" if e.get("name") else library.display_name(e)
            out.append({"id": e["da"].replace("\\", "/"), "path": os.path.join(root, e["da"]), "name": label,
                        "entry": e})
        _cache[key] = out
    return _cache[key]


def _num(ident, used):
    n = zlib.crc32(ident.encode("utf-8")) % 1_000_000
    while n in used:
        n = (n + 1) % 1_000_000
    used.add(n)
    return n


def _cat_items(self, context):
    context = context or bpy.context
    root = _root(context)
    used = set()
    out = [(c, _pretty(c), c, "FILE_FOLDER", _num(c, used)) for c in categories(root)]
    if not out:
        out = [("NONE", "No clothing found", "Export Characters/<category> from FModel", "ERROR", 0)]
    _enum_keep.append(out)
    return out


def _item_items(self, context):
    context = context or bpy.context
    root = _root(context)
    used = set()
    out = [(it["id"], it["name"], it["id"], "MATCLOTH", _num(it["id"], used))
           for it in items(root, context.scene.ft_cloth_cat)]
    if not out:
        out = [("NONE", "No items", "", "ERROR", 0)]
    _enum_keep.append(out)
    return out


def _skin_items(self, context):
    context = context or bpy.context
    root = _root(context)
    it = selected_item(context)
    used = set()
    out = []
    cls = context.scene.ft_char_class
    for s in skins(context, root, it) if it else []:
        level = status(root, s["path"], cls)[0]
        out.append((s["id"], s["name"], s["id"], ui.READY_ICON.get(level, "DOT"), _num(s["id"], used)))
    if not out:
        out = [("NONE", "No skins", "", "ERROR", 0)]
    _enum_keep.append(out)
    return out


def _on_cat(self, context):
    its = items(_root(context), context.scene.ft_cloth_cat)
    if its:
        try:
            context.scene.ft_cloth_item = its[0]["id"]
        except TypeError:
            pass


def _on_item(self, context):
    it = selected_item(context)
    ss = skins(context, _root(context), it) if it else []
    if ss:
        try:
            context.scene.ft_cloth_skin = ss[0]["id"]
        except TypeError:
            pass


def selected_item(context):
    sc = context.scene
    root = _root(context)
    return next((it for it in items(root, sc.ft_cloth_cat) if it["id"] == sc.ft_cloth_item), None)


def selected_skin(context):
    it = selected_item(context)
    if it is None:
        return None
    return next((s for s in skins(context, _root(context), it) if s["id"] == context.scene.ft_cloth_skin), None)


# ── status: what's exported ──────────────────────────────────────────────────

def _folder(object_path):
    path = object_path.split(".")[0].rsplit("/", 1)[0]
    return path.split("/Discovery/", 1)[1] if "/Discovery/" in path else path


def _rel_folder(root, file_path):
    rel = os.path.relpath(os.path.dirname(file_path), root).replace("\\", "/")
    return rel.split("Content/Discovery/", 1)[-1]


class _QuietLog(Log):
    def info(self, msg):
        pass

    def warn(self, msg):
        pass


def status(root, skin_path, cls="M"):
    """(level, text, groups) for a skin: 2 = meshes + textures exported, 1 = some textures
    missing or material files need re-saving, 0 = a mesh isn't exported."""
    key = (root, skin_path, cls)
    hit = _status.get(key)
    if hit is not None:
        return hit
    if not os.path.isfile(skin_path):
        item_dir = skin_path
        while item_dir and os.path.basename(os.path.dirname(item_dir)) != "Assets":
            item_dir = os.path.dirname(item_dir)
        res = _status[key] = (0, "Not exported", {"Item": [ui.game_folder(root, item_dir or os.path.dirname(skin_path))]})
        return res
    log = _QuietLog()
    finder = importer.make_finder(root, os.path.dirname(skin_path), log)
    parts = importer.skin_parts(finder, skin_path) if os.path.basename(skin_path).startswith("DA_") else []
    meshes = [_folder(p) for p, _k, _s in parts if not importer.find_part_file(finder, p, cls)]
    textures, flat = [], []
    try:
        overrides = read_skin(finder, skin_path)[0]
    except Exception:
        overrides = {}
    for mat in dict.fromkeys(v for v in overrides.values() if v):
        params = resolve_material(finder, object_path=mat, log=log)
        if params.flat_json:
            flat.append(_rel_folder(root, params.flat_json))
        for t in params.textures.values():
            if not t or any(m in t for m in SHARED_MARKERS) or asset_name(t).lower() in defaults.NULL_TEXTURES:
                continue
            if not finder.find_texture(t) and not finder.array_slice_count(t):
                textures.append(_folder(t))
    groups = ui.split_folders(meshes, textures, flat)
    if not parts and os.path.basename(skin_path).startswith("DA_"):
        res = (2, "No mesh (changes the body or another item)", groups)
    elif meshes:
        res = (0, f"{len(meshes)} mesh(es) not exported", groups)
    elif textures:
        res = (1, f"{len(set(textures))} texture folder(s) not exported", groups)
    elif flat:
        res = (1, "Material files need re-saving (see below)", groups)
    else:
        res = (2, "Exported", {})
    _status[key] = res
    return res


def store_image(root, skin):
    if skin is None:
        return ""
    e = skin.get("entry")
    if e and e.get("image"):
        p = os.path.join(root, e["image"] + ".png")
        if os.path.isfile(p):
            return p
    hits = sorted(glob.glob(os.path.join(os.path.dirname(skin["path"]), "T_UI_*.png")))
    return hits[0] if hits else ""


# ── search ───────────────────────────────────────────────────────────────────

def _find(context, query):
    lib = library_ui._get_lib(context)
    if not lib:
        return []
    root = _root(context)
    out = []
    for e in library.search(lib, query, limit=60):
        idx = lib["entries"].index(e)
        if e["kind"] == "outfit":
            out.append((f"outfit:{idx}", library.display_name(e), "Outfit", -1, "OUTLINER_COLLECTION"))
        else:
            parts = e["folder"].replace("\\", "/").split("Characters/", 1)[-1].split("/")
            detail = parts[2] if len(parts) > 2 else parts[-1]
            if len(parts) > 4:
                detail += f" · {parts[4]}"
            ready = library_ui._status(root, lib, e)[0]
            out.append((f"item:{idx}", library.display_name(e), detail, ready, "MATCLOTH"))
    return out


def _pick(context, ident):
    sc = context.scene
    kind, idx = ident.split(":", 1)
    lib = library_ui._get_lib(context)
    if not lib:
        return
    e = lib["entries"][int(idx)]
    sc.ft_cloth_outfit = idx if kind == "outfit" else ""
    if kind == "outfit" or not e.get("da"):
        return
    parts = e["folder"].replace("\\", "/").split("Characters/", 1)[-1].split("/")
    if len(parts) < 3:
        return
    cat, item = parts[0], f"{parts[0]}/{parts[2]}"
    try:
        sc.ft_cloth_cat = cat
        sc.ft_cloth_item = item
        sc.ft_cloth_skin = e["da"].replace("\\", "/")
    except TypeError:
        pass


# ── operators ────────────────────────────────────────────────────────────────

class FT_OT_cloth_import(bpy.types.Operator):
    """Import the chosen item with the chosen skin (and, if set in Options, the other meshes the skin uses)"""
    bl_idname = "finals_texturer.cloth_import"
    bl_label = "Import"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        root = _root(context)
        s = selected_skin(context)
        if s is None:
            self.report({"ERROR"}, "Pick an item and a skin first")
            return {"CANCELLED"}
        if not os.path.isfile(s["path"]):
            folder = status(root, s["path"])[2].get("Item", [""])[0]
            self.report({"ERROR"}, f"Not exported yet: export {folder} in FModel (Copy Path), then Rescan")
            return {"CANCELLED"}
        log = Log()
        sc = context.scene
        try:
            if os.path.basename(s["path"]).startswith("DA_"):
                objs = importer.import_item(s["path"], root, log, whole_skin=sc.ft_cloth_whole,
                                            lod_hint=sc.ft_char_class)
            else:
                # lone material skin (MI_*.json): import the item's mesh and apply it
                it = selected_item(context)
                mesh = _item_mesh(it["dir"], sc.ft_char_class)
                if not mesh:
                    self.report({"ERROR"}, "The item's mesh isn't exported (see the status box)")
                    return {"CANCELLED"}
                objs = importer.import_psk(mesh, log)
                for o in objs:
                    if o.type == "MESH":
                        importer.apply_to_object(o, root, s["path"], log)
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        _status.clear()
        _ctx["report"](self, log, sum(1 for o in objs if o.type == "MESH"))
        return {"FINISHED"} if objs else {"CANCELLED"}


def _item_mesh(item_dir, cls):
    files = sorted(f for f in os.listdir(item_dir) if f.endswith((".psk", ".pskx")))
    pref = [f for f in files if re.search(rf"_{cls}\.pskx?$", f)]
    return os.path.join(item_dir, (pref or files)[0]) if files else ""


class FT_OT_cloth_apply(bpy.types.Operator):
    """Rebuild the selected meshes' materials with the chosen skin"""
    bl_idname = "finals_texturer.cloth_apply"
    bl_label = "Apply to Selected"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(o.type == "MESH" for o in context.selected_objects)

    def execute(self, context):
        s = selected_skin(context)
        if s is None:
            self.report({"ERROR"}, "Pick an item and a skin first")
            return {"CANCELLED"}
        log = Log()
        done = 0
        for o in context.selected_objects:
            if o.type == "MESH":
                done += importer.apply_to_object(o, _root(context), s["path"], log)
        _ctx["report"](self, log, done)
        return {"FINISHED"}


class FT_OT_cloth_outfit(bpy.types.Operator):
    """Import every piece of the outfit picked in the search results"""
    bl_idname = "finals_texturer.cloth_outfit"
    bl_label = "Import Outfit"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        sc = context.scene
        lib = library_ui._get_lib(context)
        if not lib or not sc.ft_cloth_outfit:
            self.report({"ERROR"}, "Pick an outfit in the search results first")
            return {"CANCELLED"}
        root = _root(context)
        e = lib["entries"][int(sc.ft_cloth_outfit)]
        pieces = [p for p in library.outfit_items(lib, e) if p and p.get("da") and p.get("meshes")]
        log = Log()
        log.info(f"Outfit {library.display_name(e)}: {len(pieces)} piece(s) with meshes")
        objs = []
        try:
            for p in pieces:
                objs += importer.import_item(os.path.join(root, p["da"]), root, log,
                                             whole_skin=sc.ft_cloth_whole, lod_hint=sc.ft_char_class)
        except RuntimeError as err:
            self.report({"ERROR"}, str(err))
            return {"CANCELLED"}
        _ctx["report"](self, log, sum(1 for o in objs if o.type == "MESH"))
        return {"FINISHED"}


# ── panel ────────────────────────────────────────────────────────────────────

def _outfit_status(context, lib, e):
    root = _root(context)
    meshes = []
    for p in library.outfit_items(lib, e):
        if p and p.get("da"):
            for path in importer.missing_meshes(root, os.path.join(root, p["da"])):
                meshes.append(_folder(path))
    level, text = library_ui._status(root, lib, e)
    return level, text, ui.split_folders(meshes)


def _draw_options(col, context):
    sc = context.scene
    col.prop(sc, "ft_cloth_whole")
    col.row().prop(sc, "ft_char_class", expand=True)
    col.prop(sc, "ft_number", text="Number decals")
    from . import _prefs
    p = _prefs(context)
    if p:
        col.prop(p, "import_size", text="Size")
    col.operator("finals_texturer.import_psk", text="Import from File…", icon="FILEBROWSER")
    col.operator(library_ui.FT_OT_lib_rescan.bl_idname, text="Rebuild Name Library", icon="FILE_REFRESH")


def draw(layout, context):
    sc = context.scene
    root = _root(context)
    lib = library_ui._get_lib(context)
    if lib:
        search.draw(layout, context, "cloth", "Search by in-game or file name")
    else:
        row = layout.row(align=True)
        row.label(text="Search needs the name library", icon="INFO")
        row.operator(library_ui.FT_OT_lib_rescan.bl_idname, text="Build", icon="FILE_REFRESH")
    if sc.ft_cloth_outfit and lib:
        e = lib["entries"][int(sc.ft_cloth_outfit)]
        box = layout.box()
        box.label(text=f"Outfit: {library.display_name(e)}", icon="OUTLINER_COLLECTION")
        level, text, groups = _outfit_status(context, lib, e)
        ui.draw_status(box, level, text, groups)
        box.operator(FT_OT_cloth_outfit.bl_idname, icon="IMPORT")
    col = layout.column(align=True)
    col.prop(sc, "ft_cloth_cat", text="Category")
    col.prop(sc, "ft_cloth_item", text="Item")
    col.prop(sc, "ft_cloth_skin", text="Skin")
    s = selected_skin(context)
    if s is not None:
        ui.draw_preview(layout, store_image(root, s))
        level, text, groups = status(root, s["path"], sc.ft_char_class)
        ui.draw_status(layout, level, text, groups)
    row = layout.row(align=True)
    row.operator(FT_OT_cloth_import.bl_idname, icon="IMPORT")
    row.operator(FT_OT_cloth_apply.bl_idname, icon="MATERIAL")
    img = store_image(root, s)
    op = row.operator(ui.FT_OT_show_image.bl_idname, text="", icon="IMAGE_DATA")
    op.path = img
    ui.draw_options(layout, context, "CLOTHING", lambda c: _draw_options(c, context))


CLASSES = (FT_OT_cloth_import, FT_OT_cloth_apply, FT_OT_cloth_outfit)


def register(root_getter, report):
    _ctx.update(root=root_getter, report=report)
    for c in CLASSES:
        bpy.utils.register_class(c)
    S = bpy.types.Scene
    S.ft_cloth_cat = EnumProperty(name="Category", items=_cat_items, update=_on_cat,
                                  description="Clothing category (folder under Characters)")
    S.ft_cloth_item = EnumProperty(name="Item", items=_item_items, update=_on_item,
                                   description="Item (folder under <category>/Assets)")
    S.ft_cloth_skin = EnumProperty(name="Skin", items=_skin_items,
                                   description="Skin. Check = exported, i = some textures missing or "
                                               "material files need re-saving, warning = a mesh isn't exported")
    S.ft_cloth_whole = BoolProperty(name="Import whole skin", default=True,
                                    description="Also import the other meshes the skin uses (pins, hoods, …)")
    S.ft_cloth_outfit = StringProperty(options={"SKIP_SAVE"})
    search.add_tab("cloth", _find, _pick, "In-game, skin or file name; typos are OK")


def unregister():
    search.remove_tab("cloth")
    for p in ("ft_cloth_cat", "ft_cloth_item", "ft_cloth_skin", "ft_cloth_whole", "ft_cloth_outfit"):
        delattr(bpy.types.Scene, p)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
