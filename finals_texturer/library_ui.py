"""Blender UI for the in-game name library: search box, results, import."""

import os
import time

import bpy
from bpy.props import CollectionProperty, IntProperty, StringProperty, BoolProperty

from . import importer, library
from .ue_assets import AssetFinder, Log

_ctx = {"root": lambda context: "", "package": None, "report": None}
_lib = {"entries": None}          # the library currently shown


def _root(context):
    return _ctx["root"](context)


def _get_lib(context):
    root = _root(context)
    if not root:
        return None
    lib = library.load(root, _ctx["package"])
    _lib["entries"] = lib
    return lib


def _export_hint(object_path):
    """'/Game/Discovery/Characters/X/Y/SK_Z.SK_Z' → 'Characters/X/Y'."""
    path = object_path.split(".")[0]
    folder = path.rsplit("/", 1)[0]
    if "/Discovery/" in folder:
        folder = folder.split("/Discovery/", 1)[1]
    return folder


# ── results ──────────────────────────────────────────────────────────────────

class FT_LibResult(bpy.types.PropertyGroup):
    name: StringProperty()
    folder: StringProperty()
    kind: StringProperty()
    entry: IntProperty()           # index into the library's entries
    status: StringProperty()
    ready: IntProperty()           # 2 = all meshes exported, 1 = some, 0 = none


def _status(root, lib, e):
    """(ready level, text) for an entry, checked against the files right now."""
    if e["kind"] == "outfit":
        pieces = library.outfit_items(lib, e)
        ok = 0
        for piece in pieces:
            if piece and piece.get("da") and piece.get("meshes"):
                if not importer.missing_meshes(root, os.path.join(root, piece["da"])):
                    ok += 1
            elif piece and not piece.get("meshes"):
                ok += 1           # body paint, voices…: nothing to export
        n = len(pieces)
        return (2 if ok == n else 1 if ok else 0), f"{ok}/{n} pieces exported"
    if not e.get("meshes"):
        return 2, "No mesh (changes the body or another item)"
    missing = importer.missing_meshes(root, os.path.join(root, e["da"])) if e.get("da") else e["meshes"]
    n = len(e["meshes"])
    if not missing:
        return 2, "Exported" if n == 1 else f"Exported ({n} meshes)"
    if len(missing) < n:
        return 1, f"{n - len(missing)}/{n} meshes exported"
    return 0, "Mesh not exported"


def run_search(context):
    sc = context.scene
    sc.ft_lib_results.clear()
    lib = _get_lib(context)
    if lib is None:
        return
    root = _root(context)
    for e in library.search(lib, sc.ft_lib_query):
        r = sc.ft_lib_results.add()
        r.name = library.display_name(e)
        r.folder = e["folder"].split("Characters/", 1)[-1]
        r.kind = e["kind"]
        r.entry = lib["entries"].index(e)
        r.ready, r.status = _status(root, lib, e)
    sc.ft_lib_index = 0 if len(sc.ft_lib_results) else -1


def _on_query(self, context):
    run_search(context)


def selected_entry(context):
    sc = context.scene
    lib = _lib["entries"] or _get_lib(context)
    if lib is None or not (0 <= sc.ft_lib_index < len(sc.ft_lib_results)):
        return lib, None
    i = sc.ft_lib_results[sc.ft_lib_index].entry
    return lib, (lib["entries"][i] if 0 <= i < len(lib["entries"]) else None)


class FT_UL_library(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        icon_ready = {2: "CHECKMARK", 1: "INFO", 0: "ERROR"}.get(item.ready, "BLANK1")
        row = layout.row(align=True)
        row.label(text=item.name, icon="OUTLINER_COLLECTION" if item.kind == "outfit" else "MATCLOTH")
        sub = row.row()
        sub.alignment = "RIGHT"
        sub.label(text=item.folder.split("/")[-1], icon=icon_ready)


# ── operators ────────────────────────────────────────────────────────────────

class FT_OT_lib_rescan(bpy.types.Operator):
    """Rebuild the library of in-game names from the export folder (use after exporting
    more from FModel or after a game update). Search isn't 100% reliable: some items'
    files don't contain their in-game name, or contain a different one"""
    bl_idname = "finals_texturer.lib_rescan"
    bl_label = "Rescan Library"

    def execute(self, context):
        root = _root(context)
        if not root or not os.path.isdir(root):
            self.report({"ERROR"}, "Set the Exports folder in the Finals panel first")
            return {"CANCELLED"}
        wm = context.window_manager
        wm.progress_begin(0, 100)
        try:
            lib = library.scan(root, progress=lambda i, n: wm.progress_update(int(100 * i / max(n, 1))))
            library.save(lib, _ctx["package"])
        finally:
            wm.progress_end()
        AssetFinder.clear_cache()
        run_search(context)
        named = sum(1 for e in lib["entries"] if e.get("name"))
        self.report({"INFO"}, f"Library: {len(lib['entries'])} skins and outfits ({named} with in-game names)")
        return {"FINISHED"}


class FT_OT_lib_import(bpy.types.Operator):
    """Import the selected search result with its skin (outfits: every piece)"""
    bl_idname = "finals_texturer.lib_import"
    bl_label = "Import"
    bl_options = {"REGISTER", "UNDO"}

    whole_skin: BoolProperty(name="Import whole skin", default=True,
                             description="Also import the other meshes the skin is made of")

    def execute(self, context):
        lib, e = selected_entry(context)
        if e is None:
            self.report({"WARNING"}, "Select a search result first")
            return {"CANCELLED"}
        root = _root(context)
        log = Log()
        if e["kind"] == "outfit":
            pieces = [p for p in library.outfit_items(lib, e) if p and p.get("da") and p.get("meshes")]
            skins = [os.path.join(root, p["da"]) for p in pieces]
            log.info(f"Outfit {library.display_name(e)}: {len(skins)} piece(s) with meshes")
        else:
            skins = [os.path.join(root, e["da"])] if e.get("da") else []
        objs = []
        try:
            for skin in skins:
                objs += importer.import_item(skin, root, log, whole_skin=self.whole_skin)
        except RuntimeError as err:
            self.report({"ERROR"}, str(err))
            return {"CANCELLED"}
        done = sum(1 for o in objs if o.type == "MESH")
        _ctx["report"](self, log, done)
        run_search(context)            # export status may have changed nothing, but keep it fresh
        return {"FINISHED"}


class FT_OT_lib_image(bpy.types.Operator):
    """Show the selected result's store image"""
    bl_idname = "finals_texturer.lib_image"
    bl_label = "Store Image"

    def execute(self, context):
        _lib_, e = selected_entry(context)
        if e is None:
            self.report({"WARNING"}, "Select a search result first")
            return {"CANCELLED"}
        root = _root(context)
        path = os.path.join(root, e["image"] + ".png") if e.get("image") else ""
        if not os.path.isfile(path):
            # store images are sometimes exported elsewhere: look it up by name
            name = os.path.basename(e.get("image", ""))
            finder = AssetFinder(root, [os.path.join(root, e["folder"])], Log())
            path = finder.find("", ".png", stem=name) if name else ""
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
        new = [w for w in context.window_manager.windows if w not in before]
        if new:
            area = max(new[0].screen.areas, key=lambda a: a.width * a.height)
            area.type = "IMAGE_EDITOR"
            area.spaces.active.image = img
            return {"FINISHED"}
        try:
            os.startfile(path)            # Windows: open in the default image viewer
        except (AttributeError, OSError):
            self.report({"INFO"}, f"Store image: {path}")
        return {"FINISHED"}


# ── panel ────────────────────────────────────────────────────────────────────

class FT_PT_library(bpy.types.Panel):
    bl_label = "Find Item"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Finals"
    bl_parent_id = "FT_PT_panel"

    @classmethod
    def poll(cls, context):
        return getattr(context.window_manager, "ft_tab", "CLOTHING") == "CLOTHING"

    def draw(self, context):
        lay = self.layout
        sc = context.scene
        root = _root(context)
        lib = _get_lib(context) if root else None
        row = lay.row(align=True)
        if not root:
            row.label(text="Set the Exports folder first", icon="ERROR")
            return
        if lib is None:
            row.label(text="No library for this Exports folder yet", icon="INFO")
        else:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(lib.get("scanned", 0)))
            row.label(text=f"{len(lib['entries'])} skins · scanned {when}", icon="ASSET_MANAGER")
        lay.operator(FT_OT_lib_rescan.bl_idname, icon="FILE_REFRESH")
        if lib is None:
            return
        lay.prop(sc, "ft_lib_query", text="", icon="VIEWZOOM")
        if not sc.ft_lib_query.strip():
            lay.label(text="Type an in-game or file name (typos are OK)")
            return
        if not len(sc.ft_lib_results):
            lay.label(text="No matches")
            return
        lay.template_list("FT_UL_library", "", sc, "ft_lib_results", sc, "ft_lib_index", rows=6)
        _l, e = selected_entry(context)
        if e is None:
            return
        res = sc.ft_lib_results[sc.ft_lib_index]
        box = lay.box()
        col = box.column(align=True)
        col.label(text=library.display_name(e), icon="OUTLINER_COLLECTION" if e["kind"] == "outfit" else "MATCLOTH")
        bits = [b for b in (e["kind"].title(), e.get("slot"), e.get("rarity")) if b]
        col.label(text=" · ".join(bits))
        col.label(text=res.folder)
        if e.get("description"):
            col.label(text=e["description"][:90] + ("…" if len(e["description"]) > 90 else ""))
        col.label(text=res.status, icon={2: "CHECKMARK", 1: "INFO", 0: "ERROR"}.get(res.ready, "BLANK1"))
        if res.ready < 2:
            for hint in _missing_folders(context, e)[:4]:
                col.label(text=f"Export: {hint}", icon="EXPORT")
        row = box.row(align=True)
        row.operator(FT_OT_lib_import.bl_idname, icon="IMPORT")
        row.operator(FT_OT_lib_image.bl_idname, icon="IMAGE_DATA")


def _missing_folders(context, e):
    root = _root(context)
    lib = _lib["entries"]
    skins = []
    if e["kind"] == "outfit" and lib:
        skins = [os.path.join(root, p["da"]) for p in library.outfit_items(lib, e) if p and p.get("da")]
    elif e.get("da"):
        skins = [os.path.join(root, e["da"])]
    out = []
    for s in skins:
        for path in importer.missing_meshes(root, s):
            hint = _export_hint(path)
            if hint not in out:
                out.append(hint)
    return out


CLASSES = (FT_LibResult, FT_UL_library, FT_OT_lib_rescan, FT_OT_lib_import, FT_OT_lib_image, FT_PT_library)


def register(root_getter, package, report):
    _ctx.update(root=root_getter, package=package, report=report)
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.ft_lib_query = StringProperty(
        name="Find item", description="In-game name, skin or file name; typos are OK",
        update=_on_query)
    bpy.types.Scene.ft_lib_results = CollectionProperty(type=FT_LibResult)
    bpy.types.Scene.ft_lib_index = IntProperty(default=-1)


def unregister():
    del bpy.types.Scene.ft_lib_index
    del bpy.types.Scene.ft_lib_results
    del bpy.types.Scene.ft_lib_query
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
