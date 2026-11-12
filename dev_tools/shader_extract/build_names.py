"""Build the name list shipped with the add-on (finals_texturer/data/names.json.gz) from the game files.

    1. dotnet run -- <dump dir> --dump Discovery/Content/Discovery/Characters Discovery/Content/Discovery/Items
       (every DA_ data asset as FModel-format JSON, read straight from the paks; env vars as for ShaderExtract)
    2. python -I build_names.py <dump dir> [<out file>]

Contents (all paths relative to an FModel Exports folder, so they line up with a user's exports):
  clothing: the in-game-name library (library.scan format: items + outfits, names, folders, meshes, images)
  weapons:  [{id, name, folder, category, skins: [{id, name, image, variant}]}]
  faces:    [{id, name, real}]
Run it again after a game update and ship the new file with the next release.
"""
import gzip
import importlib
import importlib.util
import json
import os
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.normpath(os.path.join(HERE, "..", "..", "finals_texturer"))


def _stub_bpy():
    bpy = types.ModuleType("bpy")
    props = types.ModuleType("bpy.props")
    for n in ("BoolProperty", "EnumProperty", "IntProperty", "StringProperty", "FloatProperty",
              "PointerProperty", "CollectionProperty"):
        setattr(props, n, lambda *a, **k: None)

    class _Base:
        pass

    class _Types:
        def __getattr__(self, name):
            return _Base
    bpy.props, bpy.types = props, _Types()
    utils = types.ModuleType("bpy.utils")
    utils.previews = types.ModuleType("bpy.utils.previews")
    bpy.utils = utils
    sys.modules.update({"bpy": bpy, "bpy.props": props, "bpy.utils": utils, "bpy.utils.previews": utils.previews})
    spec = importlib.util.spec_from_file_location("finals_texturer", os.path.join(ADDON, "__init__.py"),
                                                  submodule_search_locations=[ADDON])
    sys.modules["finals_texturer"] = importlib.util.module_from_spec(spec)


def main():
    dump = os.path.normpath(sys.argv[1])
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ADDON, "data", "names.json.gz")
    _stub_bpy()
    library = importlib.import_module("finals_texturer.library")
    weapons = importlib.import_module("finals_texturer.weapons")
    character = importlib.import_module("finals_texturer.character")
    rel = lambda p: os.path.relpath(p, dump).replace("\\", "/") if p else ""

    lib = library.scan(dump)
    clothing = [{k: v for k, v in e.items() if not k.startswith("_")} for e in lib["entries"]]
    weap = []
    for w in weapons.weapons(dump):
        skins = weapons.skins(dump, w)
        if len(skins) <= 1 and not os.path.isdir(os.path.join(w["dir"], "Skins")):
            continue                           # QuickMelee, Unarmed: no visuals to import
        weap.append({"id": w["id"], "name": w["name"], "folder": w["folder"], "category": w["category"],
                     "skins": [{"id": s["id"], "path": rel(s["path"]), "name": s["name"], "image": s["image"],
                                "variant": s["variant"]} for s in skins]})
    faces = [{"id": f["id"], "name": f["name"], "real": f["real"]} for f in character.faces(dump)]
    data = {"version": 1, "library_version": library.LIB_VERSION, "generated": time.time(),
            "clothing": clothing, "weapons": weap, "faces": faces}
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"{out}: {len(clothing)} clothing entries, {len(weap)} weapons "
          f"({sum(len(w['skins']) for w in weap)} skins), {len(faces)} faces, {os.path.getsize(out) // 1024} KB")


if __name__ == "__main__":
    main()
