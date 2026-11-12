"""In-game name library glue: loading it for the Exports folder, the export
status of an entry, and the "rebuild" operator. The search itself is part of
the Clothing tab (clothing_tab.py, search.py)."""

import os

import bpy

from . import importer, library, names
from .ue_assets import AssetFinder

_ctx = {"root": lambda context: "", "package": None, "report": None}


def _root(context):
    return _ctx["root"](context)


def _get_lib(context):
    """The library rebuilt from the user's exports if there is one, else the shipped name list."""
    root = _root(context)
    if not root:
        return None
    return library.load(root, _ctx["package"]) or names.clothing_library(root)


def _status(root, lib, e):
    """(ready level, text) for an entry, checked against the files right now."""
    if e["kind"] == "outfit":
        pieces = library.outfit_items(lib, e)
        ok = 0
        for piece in pieces:
            if piece and piece.get("da") and piece.get("meshes"):
                da = os.path.join(root, piece["da"])
                if os.path.isfile(da) and not importer.missing_meshes(root, da):
                    ok += 1
            elif piece and not piece.get("meshes"):
                ok += 1           # body paint, voices…: nothing to export
        n = len(pieces)
        return (2 if ok == n else 1 if ok else 0), f"{ok}/{n} pieces exported"
    if e.get("da") and not os.path.isfile(os.path.join(root, e["da"])):
        return 0, "Not exported"
    if not e.get("meshes"):
        return 2, "No mesh (changes the body or another item)"
    missing = importer.missing_meshes(root, os.path.join(root, e["da"])) if e.get("da") else e["meshes"]
    n = len(e["meshes"])
    if not missing:
        return 2, "Exported" if n == 1 else f"Exported ({n} meshes)"
    if len(missing) < n:
        return 1, f"{n - len(missing)}/{n} meshes exported"
    return 0, "Mesh not exported"


class FT_OT_lib_rescan(bpy.types.Operator):
    """Rebuild the library of in-game names from the export folder (use after exporting
    more from FModel or after a game update). It reads every DA_MetaData_*.json, so search
    only knows items whose metadata is exported"""
    bl_idname = "finals_texturer.lib_rescan"
    bl_label = "Rebuild Name Library"

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
        from . import clothing_tab
        clothing_tab.clear_cache()
        named = sum(1 for e in lib["entries"] if e.get("name"))
        self.report({"INFO"}, f"Library: {len(lib['entries'])} skins and outfits ({named} with in-game names)")
        return {"FINISHED"}


CLASSES = (FT_OT_lib_rescan,)


def register(root_getter, package, report):
    _ctx.update(root=root_getter, package=package, report=report)
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
