"""Reading FModel exports: finding files on disk, loading JSON, and resolving
material instance chains into one flat set of parameters.

Nothing in this module touches bpy, so it can be tested outside Blender.
"""

import json
import os
import re

from . import defaults

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────


class Log:
    def __init__(self):
        self.lines = []
        self.warnings = 0

    def info(self, msg):
        self.lines.append(msg)
        print("[FinalsTexturer] " + msg)

    def warn(self, msg):
        self.warnings += 1
        self.lines.append("WARNING: " + msg)
        print("[FinalsTexturer] WARNING: " + msg)


# ─────────────────────────────────────────────────────────────────────────────
# Object paths
# ─────────────────────────────────────────────────────────────────────────────

def asset_name(object_path):
    """'/Game/A/B/T_Foo.T_Foo' or '/Game/A/B/T_Foo.0' -> 'T_Foo'."""
    if not object_path:
        return ""
    last = object_path.replace("\\", "/").rstrip("/").split("/")[-1]
    return last.split(".")[0]


def asset_dir_parts(object_path):
    parts = object_path.replace("\\", "/").strip("/").split("/")[:-1]
    return parts


def ref_path(ref):
    """Pull an object path out of the many shapes FModel uses for references."""
    if not ref:
        return ""
    if isinstance(ref, str):
        return ref
    if isinstance(ref, dict):
        return ref.get("ObjectPath") or ref.get("AssetPathName") or ""
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Finding files
# ─────────────────────────────────────────────────────────────────────────────


class AssetFinder:
    """Maps UE object paths to files inside an FModel export folder.

    Tries the folder layout FModel writes first (`<root>/Discovery/Content/...`),
    then falls back to a by-name index of everything under the root, so loose
    or flattened exports still work.
    """

    _index_cache = {}

    def __init__(self, root, extra_dirs=(), log=None):
        self.root = os.path.normpath(root) if root else ""
        self.extra_dirs = [os.path.normpath(d) for d in extra_dirs if d]
        self.log = log or Log()
        self._json_cache = {}

    # ── index ────────────────────────────────────────────────────────────────
    @classmethod
    def _base_index(cls, base):
        """Index of one folder tree, cached on its own so every finder that
        shares the export root reuses it (one walk per session)."""
        key = os.path.normcase(base)
        idx = cls._index_cache.get(key)
        if idx is not None:
            return idx
        idx = {}
        if os.path.isdir(base):
            for dirpath, _dirs, files in os.walk(base):
                for f in files:
                    stem, ext = os.path.splitext(f)
                    ext = ext.lower()
                    if ext not in (".json", ".png", ".tga", ".psk", ".pskx"):
                        continue
                    idx.setdefault(stem.lower(), []).append(os.path.join(dirpath, f))
        cls._index_cache[key] = idx
        return idx

    def _index(self):
        root_n = os.path.normcase(self.root) if self.root else ""
        # extra folders inside the root are already covered by the root's index
        outside = [d for d in self.extra_dirs
                   if not root_n or not os.path.normcase(d).startswith(root_n + os.sep)]
        if self.root and not outside:
            return self._base_index(self.root)
        key = ("merged", root_n, tuple(os.path.normcase(d) for d in outside))
        idx = AssetFinder._index_cache.get(key)
        if idx is not None:
            return idx
        idx = {}
        for base in ([self.root] if self.root else []) + outside:
            for k, v in self._base_index(base).items():
                idx.setdefault(k, []).extend(v)
        AssetFinder._index_cache[key] = idx
        return idx

    @classmethod
    def clear_cache(cls):
        cls._index_cache.clear()

    # ── lookup ───────────────────────────────────────────────────────────────
    def _structured_candidates(self, object_path, filename):
        parts = asset_dir_parts(object_path)
        if not parts or not self.root:
            return []
        mount, rest = parts[0], parts[1:]
        cands = []
        if mount == "Game":
            for prefix in (("Discovery", "Content"), ("Content",), ()):
                cands.append(os.path.join(self.root, *prefix, *rest, filename))
        else:
            # /Engine/..., /EmbarkScript/... and other plugin mounts
            cands.append(os.path.join(self.root, mount, "Content", *rest, filename))
            cands.append(os.path.join(self.root, "Discovery", "Plugins", mount, "Content", *rest, filename))
            cands.append(os.path.join(self.root, "RemappedPlugins", mount, "Content", *rest, filename))
            cands.append(os.path.join(self.root, mount, *rest, filename))
        return cands

    def find(self, object_path, ext, stem=None):
        """Return the file for an object path, or ''."""
        if not object_path and not stem:
            return ""
        stem = stem or asset_name(object_path)
        filename = stem + ext
        for c in self._structured_candidates(object_path or "", filename):
            if os.path.isfile(c):
                return c
        hits = [p for p in self._index().get(stem.lower(), []) if p.lower().endswith(ext.lower())]
        if not hits:
            return ""
        if len(hits) == 1 or not object_path:
            return hits[0]
        # Prefer the hit whose folders best match the object path.
        want = [p.lower() for p in asset_dir_parts(object_path)]

        def score(p):
            have = [x.lower() for x in os.path.normpath(p).split(os.sep)[:-1]]
            s = 0
            for a, b in zip(reversed(want), reversed(have)):
                if a != b:
                    break
                s += 1
            return s

        return max(hits, key=score)

    def find_texture(self, object_path):
        for ext in (".png", ".tga"):
            p = self.find(object_path, ext)
            if p:
                return p
        return ""

    def find_array_slice(self, object_path, index):
        """Texture2DArray slice: FModel writes <name>_LAYER<n>.png."""
        name = asset_name(object_path)
        for stem in (f"{name}_LAYER{index}", f"{name}_{index}"):
            for ext in (".png", ".tga"):
                p = self.find(object_path, ext, stem=stem)
                if p:
                    return p
        return ""

    def array_slice_count(self, object_path):
        name = asset_name(object_path).lower()
        pat = re.compile(re.escape(name) + r"_layer(\d+)$")
        return len([k for k in self._index() if pat.match(k)])

    # ── json ─────────────────────────────────────────────────────────────────
    def load_json(self, path):
        if not path:
            return None
        if path in self._json_cache:
            return self._json_cache[path]
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            self.log.warn(f"Could not read {path}: {e}")
            data = None
        self._json_cache[path] = data
        return data

    def load_asset_json(self, object_path, stem=None):
        return self.load_json(self.find(object_path, ".json", stem=stem))


def export_of_type(data, types):
    """First export in an FModel JSON whose Type is in `types`."""
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return None
    for e in data:
        if isinstance(e, dict) and e.get("Type") in types:
            return e
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Material parameters
# ─────────────────────────────────────────────────────────────────────────────

MATERIAL_TYPES = ("MaterialInstanceConstant", "Material", "MaterialInstanceDynamic")


class MaterialParams:
    """Flat result of a resolved material instance chain."""

    def __init__(self):
        self.name = ""
        self.chain = []          # leaf first: names of MI → … → master
        self.master = ""         # base Material name ('' if never reached)
        self.scalars = {}
        self.vectors = {}        # name -> (r, g, b, a)
        self.textures = {}       # name -> object path
        self.switches = {}
        self.blend_mode = ""
        self.shading_model = ""
        self.two_sided = None
        self.opacity_clip = 0.3333
        self.source_files = []

    # convenience getters with defaults
    def s(self, name, default=0.0):
        return float(self.scalars.get(name, default))

    def v(self, name, default=(1.0, 1.0, 1.0, 1.0)):
        return self.vectors.get(name, default)

    def t(self, name):
        return self.textures.get(name, "")

    def sw(self, name, default=False):
        return bool(self.switches.get(name, default))

    def has_any(self, *names):
        return any(n in self.scalars or n in self.vectors or n in self.textures for n in names)


def _vec(v):
    if isinstance(v, dict):
        return (float(v.get("R", 0)), float(v.get("G", 0)), float(v.get("B", 0)), float(v.get("A", 1)))
    if isinstance(v, (list, tuple)):
        v = list(v) + [1.0] * (4 - len(v))
        return tuple(float(x) for x in v[:4])
    return (1.0, 1.0, 1.0, 1.0)


def _enum_tail(v):
    """'EBlendMode::BLEND_Masked' -> 'BLEND_Masked'."""
    return str(v).split("::")[-1] if v else ""


def _read_master_defaults(export, out):
    """Parameter defaults from a base Material's CachedExpressionData."""
    ced = export.get("CachedExpressionData") or (export.get("Properties") or {}).get("CachedExpressionData")
    if not ced:
        return False

    def entry(i):
        key = "RuntimeEntries" if i == 0 else f"RuntimeEntries[{i}]"
        e = ced.get(key) or {}
        return [p.get("Name") for p in e.get("ParameterInfoSet", [])]

    for name, val in zip(entry(0), ced.get("ScalarValues", [])):
        out.scalars[name] = float(val)
    for name, val in zip(entry(1), ced.get("VectorValues", [])):
        out.vectors[name] = _vec(val)
    for name, val in zip(entry(3), ced.get("TextureValues", [])):
        p = ref_path(val)
        if p:
            out.textures[name] = p
    for name, val in zip(entry(8), ced.get("StaticSwitchValues", [])):
        out.switches[name] = bool(val)
    return True


def _read_instance_overrides(props, out):
    for s in props.get("ScalarParameterValues", []) or []:
        name = (s.get("ParameterInfo") or {}).get("Name")
        if name is not None:
            out.scalars[name] = float(s.get("ParameterValue", 0.0))
    for s in props.get("VectorParameterValues", []) or []:
        name = (s.get("ParameterInfo") or {}).get("Name")
        if name is not None:
            out.vectors[name] = _vec(s.get("ParameterValue"))
    for s in props.get("TextureParameterValues", []) or []:
        name = (s.get("ParameterInfo") or {}).get("Name")
        p = ref_path(s.get("ParameterValue"))
        if name is not None and p:      # null means "not set here"
            out.textures[name] = p
    static = props.get("StaticParametersRuntime") or props.get("StaticParameters") or {}
    for s in static.get("StaticSwitchParameters", []) or []:
        name = (s.get("ParameterInfo") or {}).get("Name")
        if name is not None:
            out.switches[name] = bool(s.get("Value"))


def _read_base_properties(props, out):
    bpo = props.get("BasePropertyOverrides") or {}
    bm = bpo.get("BlendMode") or props.get("BlendMode")
    if bm:
        out.blend_mode = _enum_tail(bm)
    sm = bpo.get("ShadingModel") or props.get("ShadingModel")
    if sm:
        out.shading_model = _enum_tail(sm)
    if "TwoSided" in bpo:
        out.two_sided = bool(bpo["TwoSided"])
    elif "TwoSided" in props:
        out.two_sided = bool(props["TwoSided"])
    if "OpacityMaskClipValue" in bpo:
        out.opacity_clip = float(bpo["OpacityMaskClipValue"])
    elif "OpacityMaskClipValue" in props:
        out.opacity_clip = float(props["OpacityMaskClipValue"])


def _read_flat_format(data, out):
    """FModel's simplified material export: Textures/Parameters with no Parent."""
    if not isinstance(data, dict):
        return False
    params = data.get("Parameters", data)
    tex = data.get("Textures") or params.get("Textures")
    if tex is None and "Scalars" not in params:
        return False
    for k, v in (tex or {}).items():
        p = ref_path(v)
        if p:
            out.textures[k] = p
    for k, v in (params.get("Scalars") or {}).items():
        out.scalars[k] = float(v)
    for k, v in (params.get("Colors") or {}).items():
        out.vectors[k] = _vec(v)
    for k, v in (params.get("Switches") or {}).items():
        out.switches[k] = bool(v)
    props = params.get("Properties") or {}
    _read_base_properties(props, out)
    return True


def _master_param_names(export):
    ced = export.get("CachedExpressionData") or (export.get("Properties") or {}).get("CachedExpressionData") or {}
    names = set()
    for i in range(10):
        e = ced.get("RuntimeEntries" if i == 0 else f"RuntimeEntries[{i}]") or {}
        names.update(p.get("Name") for p in e.get("ParameterInfoSet", []) if p.get("Name"))
    return names


def _flat_param_names(data):
    params = data.get("Parameters", data)
    tex = data.get("Textures") or params.get("Textures") or {}
    # FModel also lists each texture under its own asset name; skip those keys.
    names = {k for k, v in tex.items() if k.lower() != asset_name(ref_path(v) or "").lower()}
    for key in ("Scalars", "Colors", "Switches"):
        names.update((params.get(key) or {}).keys())
    return names


_MASTERS_CACHE = {}


def _character_masters(finder):
    """{name: (param names, export)} for character master materials under the root."""
    key = finder.root
    if key in _MASTERS_CACHE:
        return _MASTERS_CACHE[key]
    masters = {}
    for stem, paths in finder._index().items():
        if not stem.startswith("m_"):
            continue
        # Masters we have a builder for (heads, body…) count too: their
        # instances get rewritten by mesh exports just like clothing's.
        built = stem in defaults._EXACT or any(stem.startswith(k) for k, _t, _m in defaults._KNOWN
                                               if k.startswith("m_"))
        if not built and not stem.startswith(("m_character", "m_charachter")) and not any(
                "materiallibrary" in p.lower() and os.sep + "character" + os.sep in p.lower() for p in paths):
            continue
        for p in paths:
            if not p.lower().endswith(".json"):
                continue
            exp = export_of_type(finder.load_json(p), ("Material",))
            if exp:
                names = _master_param_names(exp)
                if names:
                    masters[exp.get("Name", stem)] = (names, exp)
                break
    _MASTERS_CACHE[key] = masters
    return masters


def guess_master(finder, names):
    """Best-matching character master for a set of parameter names, or None.

    Matches ~95% of clothing materials to the right builder in a survey of
    5,000+ materials whose parent is known. Instances often keep stale names
    from older parents, so this scores overlap instead of requiring all names.
    """
    masters = _character_masters(finder)
    if not masters or not names:
        return None
    big = [ps for ps, _e in masters.values() if len(ps) > 20]
    common = set.intersection(*big) if big else set()
    names = set(names) - common
    # Decal parameters tell the two layered masters apart reliably.
    if any(n.endswith("_UseDecal") or n.endswith("_DecalPlacement") for n in names):
        prefer = "M_Character_Layered"
    elif names & {"Decal", "DecalPositionU", "DecalTint"}:
        prefer = "M_Character_8Layers_Master"
    else:
        prefer = None
    best = None
    for name, (ps, exp) in masters.items():
        hit = len(names & ps)
        if not hit:
            continue
        score = (name == prefer, hit, -len(ps))
        if best is None or score > best[0]:
            best = (score, (name, exp))
    return best[1] if best else None


def resolve_material(finder, object_path=None, json_path=None, log=None, max_depth=12):
    """Walk MI → parent → … → base Material and merge every level's values.

    Parent values are applied first and child values on top, so the leaf wins.
    Missing files are logged; their names still count toward the chain so the
    shader type can be detected from the name alone.
    """
    log = log or finder.log
    out = MaterialParams()
    levels = []          # leaf first: (name, export or None, flat dict or None)
    path = object_path
    jpath = json_path
    for _ in range(max_depth):
        name = asset_name(path) if path else os.path.splitext(os.path.basename(jpath or ""))[0]
        data = finder.load_json(jpath) if jpath else finder.load_asset_json(path)
        if data is None:
            if path:
                log.warn(f"Missing material file '{name}' (looked for {name}.json; game path {path}).")
            levels.append((name, None, None))
            break
        if jpath:
            out.source_files.append(jpath)
        else:
            out.source_files.append(finder.find(path, ".json"))
        exp = export_of_type(data, MATERIAL_TYPES)
        if exp is None:
            levels.append((name, None, data))   # flat format, no parent info
            break
        levels.append((exp.get("Name", name), exp, None))
        if exp.get("Type") == "Material":
            break
        parent = ref_path((exp.get("Properties") or {}).get("Parent"))
        if not parent:
            break
        path, jpath = parent, None

    out.chain = [lv[0] for lv in levels]
    out.name = out.chain[0] if out.chain else ""

    # Base material defaults first (from file, or built-in for known masters).
    last_name, last_exp, last_flat = levels[-1]
    if last_flat is not None and not defaults.master_from_chain(out.chain):
        # FModel's mesh export rewrites a mesh's materials in a simplified
        # format without the Parent. Recover the master from parameter names.
        guessed = guess_master(finder, _flat_param_names(last_flat))
        if guessed:
            name, exp = guessed
            log.info(f"  {last_name} has no parent in its file (FModel mesh export); "
                     f"matched to {name} by its parameters.")
            levels.append((name, exp, None))
            out.chain.append(name)
            last_name, last_exp = name, exp
    if last_exp is not None and last_exp.get("Type") == "Material":
        out.master = last_name
        _read_master_defaults(last_exp, out)
        _read_base_properties(last_exp.get("Properties") or {}, out)
    else:
        guess = defaults.master_from_chain(out.chain)
        if guess:
            out.master = guess
            defaults.apply_builtin_defaults(guess, out)
            log.info(f"  Using built-in defaults for {guess} (its JSON was not found).")

    for name, exp, flat in reversed(levels):
        if flat is not None:
            _read_flat_format(flat, out)
        elif exp is not None and exp.get("Type") != "Material":
            props = exp.get("Properties") or {}
            _read_instance_overrides(props, out)
            _read_base_properties(props, out)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Meshes and skins
# ─────────────────────────────────────────────────────────────────────────────

LOD_SUFFIX = re.compile(r"_(L|M|H|LOD\d+)$", re.IGNORECASE)


def mesh_base_name(name):
    return LOD_SUFFIX.sub("", name)


def skeletal_slots(finder, mesh_name, mesh_dir=""):
    """[(slot_name, material_object_path)] from SK_*.json, trying LOD siblings."""
    base = mesh_base_name(mesh_name)
    tried = [mesh_name] + [f"{base}_{s}" for s in ("M", "H", "L")] + [base]
    for stem in tried:
        p = ""
        if mesh_dir:
            cand = os.path.join(mesh_dir, stem + ".json")
            if os.path.isfile(cand):
                p = cand
        p = p or finder.find("", ".json", stem=stem)
        data = finder.load_json(p) if p else None
        exp = export_of_type(data, ("SkeletalMesh", "StaticMesh"))
        if not exp:
            continue
        mats = exp.get("SkeletalMaterials") or (exp.get("Properties") or {}).get("SkeletalMaterials") \
            or exp.get("StaticMaterials") or (exp.get("Properties") or {}).get("StaticMaterials") or []
        out = []
        for m in mats:
            slot = m.get("MaterialSlotName") or m.get("ImportedMaterialSlotName") or ""
            mat = ref_path(m.get("MaterialInterface") or m.get("Material"))
            out.append((slot, mat))
        if out:
            return out, p
    return [], ""


def find_skins(mesh_dir):
    """Skin definitions for a mesh.

    A DA_*.json next to the mesh itself is the item's default skin (e.g. the
    black Ballerina wings) and is listed first as "Default". Other skins live
    in <mesh dir>/Skins/<Skin>/ as DA_*.json or MI_*.json.
    """
    skins = []
    if os.path.isdir(mesh_dir):
        own = sorted(f for f in os.listdir(mesh_dir)
                     if f.startswith("DA_") and f.endswith(".json")
                     and not f.startswith(("DA_MetaData", "DA_Persistence")))
        if own:
            skins.append(("Default", os.path.join(mesh_dir, own[0])))
    sdir = os.path.join(mesh_dir, "Skins")
    if not os.path.isdir(sdir):
        return skins
    for skin in sorted(os.listdir(sdir)):
        d = os.path.join(sdir, skin)
        if not os.path.isdir(d):
            continue
        files = sorted(os.listdir(d))
        das = [f for f in files if f.startswith("DA_") and f.endswith(".json")
               and not f.startswith(("DA_MetaData", "DA_Persistence"))]
        mis = [f for f in files if f.startswith("MI_") and f.endswith(".json")]
        if das:
            skins.append((skin, os.path.join(d, das[0])))
        elif mis:
            skins.append((skin, os.path.join(d, mis[0])))
    return skins


def tag_overrides(vp, tags):
    """A VisualPart's TagOverrides whose MatchingTags are all in `tags`."""
    out = []
    for to in vp.get("TagOverrides") or []:
        match = to.get("MatchingTags") or []
        if match and all(t in tags for t in match):
            out.append(to)
    return out


def read_skin(finder, path, _depth=0, tags=()):
    """Return {slot_name: material object path} and the list of MI paths
    from a CharacterCustomizationItem (DA_*) or a lone MI_*.json.

    A skin with a ParentVariant inherits the parent's material overrides;
    its own overrides win. `tags` (e.g. "Customization.Quality.Frontend")
    also apply the material overrides of VisualParts' matching TagOverrides,
    on top of the item's own.
    """
    data = finder.load_json(path)
    exp = export_of_type(data, ("CharacterCustomizationItem",))
    if exp:
        props = exp.get("Properties") or {}
        overrides = {}
        parent = ref_path(props.get("ParentVariant"))
        if parent and _depth < 5:
            ppath = finder.find(parent, ".json")
            if ppath:
                overrides.update(read_skin(finder, ppath, _depth + 1, tags)[0])
        for o in props.get("MaterialOverrides", []) or []:
            key = o.get("Key")
            val = ref_path(o.get("Value"))
            if key and val:
                overrides[key] = val
        if tags:
            for vp in props.get("VisualParts") or []:
                for to in tag_overrides(vp, tags):
                    if not to.get("bOverrideMaterials"):
                        continue
                    for o in to.get("MaterialOverrides") or []:
                        key, val = o.get("Key"), ref_path(o.get("Value"))
                        if key and val:
                            overrides[key] = val
        return overrides, None
    wsk = export_of_type(data, ("WeaponSkinDataAsset",))
    if wsk:
        # Weapon skin: material overrides for the base mesh and per weapon
        # part; slot names are unique across a weapon's parts, so one map.
        props = wsk.get("Properties") or {}
        overrides = {}
        groups = [props.get("BaseOverrides") or {}]
        groups += [(po.get("Value") or {}) for po in props.get("PartOverrides") or []]
        for g in groups:
            for o in g.get("Materials") or []:
                key, val = o.get("Key"), ref_path(o.get("Value"))
                if key and val:
                    overrides[key] = val
        return overrides, None
    mi = export_of_type(data, MATERIAL_TYPES)
    if mi:
        return {}, path   # caller decides which slot it belongs to
    return {}, None
