"""In-game name library.

Scans an FModel export folder once and saves, per skin and per outfit, the
in-game name ("Kangaroo Hoodie") next to its files (folder, skin file, meshes,
store image). Searching that file is instant, unlike reading thousands of
JSON files each time.

* One entry per skin, not per name: one name often covers many colour
  variants, so a search returns all of them.
* Items without an in-game name are kept and can be found by file name.
* Whether a mesh is exported is NOT stored (that changes whenever you export
  more from FModel); it's checked when you search.
* The file carries a format version; an older file is rebuilt automatically.

Nothing here touches Blender, except finding the folder to store files in.
"""

import difflib
import hashlib
import json
import os
import re
import time

from .ue_assets import export_of_type, ref_path

LIB_VERSION = 2        # 2: placeholder names
ITEM_META = "CustomizationItemMetaDataItem"
OUTFIT_META = "CharacterCustomizationOutfitMetaDataItem"


# ─────────────────────────────────────────────────────────────────────────────
# Storage
# ─────────────────────────────────────────────────────────────────────────────

def storage_dir(package=None):
    """Folder that survives add-on updates (Blender's per-extension user folder)."""
    import bpy
    try:
        if package:
            return bpy.utils.extension_path_user(package, path="library", create=True)
    except (ValueError, AttributeError, RuntimeError):
        pass
    path = os.path.join(bpy.utils.user_resource("CONFIG"), "finals_texturer_library")
    os.makedirs(path, exist_ok=True)
    return path


def _norm(root):
    return os.path.normcase(os.path.normpath(root or ""))


def library_path(root, package=None):
    """One library file per export folder."""
    key = hashlib.sha1(_norm(root).encode("utf-8")).hexdigest()[:12]
    return os.path.join(storage_dir(package), f"library_{key}.json")


_cache = {}


def load(root, package=None):
    """The library for this export folder, or None if missing / outdated."""
    path = library_path(root, package)
    hit = _cache.get(path)
    if hit is not None:
        return hit
    try:
        with open(path, encoding="utf-8") as fh:
            lib = json.load(fh)
    except (OSError, ValueError):
        return None
    if lib.get("version") != LIB_VERSION or _norm(lib.get("root")) != _norm(root):
        return None
    _index(lib)
    _cache[path] = lib
    return lib


def save(lib, package=None):
    path = library_path(lib["root"], package)
    tmp = path + ".tmp"
    clean = {k: v for k, v in lib.items() if not k.startswith("_")}
    clean["entries"] = [{k: v for k, v in e.items() if not k.startswith("_")} for e in lib["entries"]]
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(clean, fh, ensure_ascii=False, indent=0)
    os.replace(tmp, path)
    _index(lib)
    _cache[path] = lib
    return path


def _index(lib):
    """Lookup by skin file (for outfits) and pre-split search words."""
    lib["_by_da"] = {e["da"].lower(): e for e in lib["entries"] if e.get("da")}
    for e in lib["entries"]:
        e["_name"] = (e.get("name") or "").lower()
        folder = e.get("folder", "").split("Characters/", 1)[-1]
        raw = " ".join(x for x in (e.get("name"), e.get("localized"), e.get("placeholder"), folder,
                                   e.get("skin"), e.get("description")) if x)
        e["_hay"] = raw.lower()
        # fuzzy matching only looks at names and folders, not descriptions
        e["_words"] = sorted(set(_words(" ".join(x for x in (e.get("name"), e.get("localized"),
                                                            e.get("placeholder"), folder,
                                                            e.get("skin")) if x))))


# ─────────────────────────────────────────────────────────────────────────────
# Scan
# ─────────────────────────────────────────────────────────────────────────────

def _load_json(path):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _game_rel(object_path):
    """/Game/Discovery/Characters/X/Y.Y → Discovery/Content/Discovery/Characters/X/Y"""
    if not object_path:
        return ""
    path = object_path.split(".")[0]
    if path.startswith("/Game/"):
        return "Discovery/Content/" + path[len("/Game/"):]
    return path.lstrip("/")


def scan(root, progress=None):
    """Read every DA_MetaData_*.json under root and build the library."""
    root = os.path.normpath(root)
    metas = []
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.startswith("DA_MetaData_") and f.endswith(".json"):
                metas.append(os.path.join(dirpath, f))
    entries = []
    for i, meta_path in enumerate(metas):
        if progress and i % 50 == 0:
            progress(i, len(metas))
        e = _entry(root, meta_path)
        if e:
            entries.append(e)
    entries.sort(key=lambda e: ((e.get("name") or "~").lower(), e["folder"].lower()))
    return {"version": LIB_VERSION, "root": root, "scanned": time.time(), "entries": entries}


def _entry(root, meta_path):
    data = _load_json(meta_path)
    meta = export_of_type(data, (ITEM_META, OUTFIT_META))
    if meta is None:
        return None
    props = meta.get("Properties") or {}
    pretty = props.get("PrettyName") or {}
    desc = props.get("Description") or {}
    name = pretty.get("SourceString") or ""
    localized = pretty.get("LocalizedString") or ""
    # Some items only carry an untranslated placeholder ("Beret Cotton") instead
    # of their shop name. Kept separately: it's not the in-game name, but it
    # reads better in results than the bare folder name.
    placeholder = pretty.get("CultureInvariantString") or "" if not name else ""
    folder_abs = os.path.dirname(meta_path)
    da_abs = os.path.join(folder_abs, os.path.basename(meta_path).replace("DA_MetaData_", "DA_", 1))
    rel = lambda p: os.path.relpath(p, root).replace("\\", "/")
    e = {
        "kind": "outfit" if meta.get("Type") == OUTFIT_META else "item",
        "name": name,
        "localized": localized if localized and localized != name else "",
        "placeholder": placeholder,
        "description": desc.get("SourceString") or "",
        "folder": rel(folder_abs),
        "skin": os.path.basename(folder_abs),
        "da": rel(da_abs) if os.path.isfile(da_abs) else "",
        "image": _game_rel(ref_path(props.get("Image"))),
        "slot": "",
        "rarity": "",
        "meshes": [],
        "items": [],
    }
    da = export_of_type(_load_json(da_abs), ("CharacterCustomizationItem", "CharacterCustomizationOutfitDataAsset"))
    if da:
        dp = da.get("Properties") or {}
        for tag in dp.get("AssetTags") or []:
            if tag.startswith("Customization.Slot."):
                e["slot"] = tag[len("Customization.Slot."):]
                break
        e["rarity"] = (dp.get("Rarity") or "").split("::")[-1]
        for vp in dp.get("VisualParts") or []:
            for kind in ("SkeletalMesh", "StaticMesh"):
                p = ref_path(vp.get(kind))
                if p:
                    e["meshes"].append(p)
        for it in dp.get("Items") or []:
            p = ref_path(it)
            if p:
                e["items"].append(_game_rel(p) + ".json")
    return e


# ─────────────────────────────────────────────────────────────────────────────
# Search
# ─────────────────────────────────────────────────────────────────────────────

_WORD = re.compile(r"[a-z0-9]+")


def _words(text):
    # split CamelCase file names too ("BikerVest" → "biker", "vest")
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text or "")
    return _WORD.findall(text.lower())


def _token_score(tok, e):
    """How well one search word matches an entry; 0 = no match."""
    if tok in _words(e["_name"]):
        return 3.0
    if tok in e["_name"]:
        return 2.5
    if tok in e["_hay"]:
        return 1.5
    # fuzzy: typos and near-misses ("kangaro", "hoody")
    best = 0.0
    for w in e["_words"]:
        if abs(len(w) - len(tok)) > 3:
            continue
        sm = difflib.SequenceMatcher(None, tok, w)
        if sm.real_quick_ratio() < 0.68 or sm.quick_ratio() < 0.68:
            continue
        r = sm.ratio()
        # close enough overall, or a near miss that starts the same way
        # ("hoody" → "hoodie"), but not "beret" → "before"
        if r >= 0.8 or (r >= 0.7 and os.path.commonprefix([tok, w]).__len__() >= 3):
            best = max(best, r)
    return best * 1.2 if best else 0.0


def search(lib, query, limit=80, kinds=("item", "outfit")):
    """Entries matching the query's words (exact, partial or fuzzy), best first.

    Entries matching every word come first. If none match them all (e.g. "red
    beret" when the colour isn't in the item's data), the entries matching the
    most words are shown instead.
    """
    toks = _words(query)
    if not lib or not toks:
        return []
    scored = []
    for e in lib["entries"]:
        if e["kind"] not in kinds:
            continue
        hits, total = 0, 0.0
        for t in toks:
            sc = _token_score(t, e)
            if sc > 0:
                hits += 1
                total += sc
        if hits:
            if e.get("name"):
                total += 0.5          # prefer items with an in-game name
            scored.append((hits, total, e))
    if not scored:
        return []
    best_hits = max(h for h, _t, _e in scored)
    scored = [x for x in scored if x[0] == best_hits]
    scored.sort(key=lambda x: (-x[1], (x[2].get("name") or "~").lower(), x[2]["folder"].lower()))
    return [e for _h, _t, e in scored[:limit]]


def display_name(e):
    """In-game name; else the placeholder or folder name in brackets."""
    return e.get("name") or f"({e.get('placeholder') or e['skin']})"


def outfit_items(lib, e):
    """Library entries for the pieces of an outfit (missing ones as None)."""
    return [lib["_by_da"].get(p.lower()) for p in e.get("items") or []]
