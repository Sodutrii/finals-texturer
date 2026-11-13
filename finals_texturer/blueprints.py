"""Game objects from their Blueprints (no bpy).

A cooked Blueprint (BP_*.json, FModel's "Save Properties" format) lists its parts as components:
StaticMeshComponent / SkeletalMeshComponent exports with the mesh, RelativeLocation / RelativeRotation /
RelativeScale3D, AttachParent (the component it hangs from) and OverrideMaterials (by slot index).
Components come in two forms:
  * subobjects of the class default object (Outer "Default__BP_X_C"), parent via AttachParent;
  * construction-script templates ("<Name>_GEN_VARIABLE"), parent via their SCS_Node's
    ParentComponentOrVariableName.
A parent Blueprint (SuperStruct under /Game) contributes its parts too; the child's component of the
same name overrides the parent's properties.

Parts are left out when hidden (bVisible false / bHiddenInGame) or when game script places them: the
same mesh on several components that all have no transform (the cashout station's four barrier doors
come up only while it's locked).
"""

import json
import os

from .ue_assets import asset_name, ref_path

MESH_KEYS = (("StaticMesh", "StaticMesh"), ("SkeletalMesh", "SkeletalMesh"), ("SkinnedAsset", "SkeletalMesh"),
             ("SkeletalMeshAsset", "SkeletalMesh"))
XFORM_KEYS = ("RelativeLocation", "RelativeRotation", "RelativeScale3D")


def _load(path):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _ref_name(ref):
    """'StaticMeshComponent'Default__BP_X_C:Vault'' → 'Vault'."""
    if not isinstance(ref, dict):
        return ""
    on = ref.get("ObjectName") or ""
    inner = on.split("'", 1)[1].rstrip("'") if "'" in on else on
    return inner.rsplit(":", 1)[-1].rsplit(".", 1)[-1]


def _component_name(export):
    n = export.get("Name") or ""
    return n[:-len("_GEN_VARIABLE")] if n.endswith("_GEN_VARIABLE") else n


def _components(data):
    """{name: {"type", "props", "parent"}} of one Blueprint file (scene components only)."""
    scs_parent = {}
    for e in data:
        if e.get("Type") == "SCS_Node":
            p = e.get("Properties") or {}
            tmpl = _ref_name(p.get("ComponentTemplate"))
            if tmpl:
                if tmpl.endswith("_GEN_VARIABLE"):
                    tmpl = tmpl[:-len("_GEN_VARIABLE")]
                scs_parent[tmpl] = p.get("ParentComponentOrVariableName") or ""
    # SCS children are listed under their parent node; map child template → parent template
    nodes = {e.get("Name"): e for e in data if e.get("Type") == "SCS_Node"}
    for e in nodes.values():
        p = e.get("Properties") or {}
        parent_tmpl = _ref_name(p.get("ComponentTemplate")).replace("_GEN_VARIABLE", "")
        for ch in p.get("ChildNodes") or []:
            node = nodes.get(_ref_name(ch))
            if node is None:
                continue
            ct = _ref_name((node.get("Properties") or {}).get("ComponentTemplate")).replace("_GEN_VARIABLE", "")
            if ct and not scs_parent.get(ct):
                scs_parent[ct] = parent_tmpl
    out = {}
    for e in data:
        t = e.get("Type") or ""
        if not t.endswith("Component"):
            continue
        p = e.get("Properties") or {}
        name = _component_name(e)
        parent = _ref_name(p.get("AttachParent")) or scs_parent.get(name, "")
        out[name] = {"type": t, "props": dict(p), "parent": parent}
    return out


def _super_path(data):
    for e in data:
        if e.get("Type") == "BlueprintGeneratedClass":
            sup = (e.get("SuperStruct") or {}).get("ObjectPath") or ""
            return sup if sup.startswith("/") and not sup.startswith("/Script/") else ""
    return ""


def read_components(finder, bp_file, depth=0):
    """Components of a Blueprint incl. its parent Blueprints' (child overrides parent)."""
    data = _load(bp_file)
    comps = {}
    sup = _super_path(data)
    if sup and depth < 8:
        parent_file = finder.find(sup.rsplit(".", 1)[0], ".json")
        if parent_file:
            comps = read_components(finder, parent_file, depth + 1)
    for name, c in _components(data).items():
        if name in comps:
            merged = dict(comps[name]["props"])
            merged.update(c["props"])
            comps[name] = {"type": c["type"], "props": merged, "parent": c["parent"] or comps[name]["parent"]}
        else:
            comps[name] = c
    return comps


def parts(finder, bp_file):
    """[{"name", "mesh" (object path), "kind" ("StaticMesh" / "SkeletalMesh"), "chain" ([(loc, rot, scale)],
    root first), "materials" ({slot index: object path})}] of a Blueprint, ready to place."""
    comps = read_components(finder, bp_file)
    found = []
    for name, c in comps.items():
        p = c["props"]
        mesh, kind = "", ""
        for key, k in MESH_KEYS:
            mesh = ref_path(p.get(key))
            if mesh:
                kind = k
                break
        if not mesh:
            continue
        if p.get("bVisible") is False or p.get("bHiddenInGame") is True:
            continue
        found.append((name, mesh, kind))
    # script-placed: same mesh on several components, none of them with a transform
    by_mesh = {}
    for name, mesh, _k in found:
        by_mesh.setdefault(mesh.lower(), []).append(name)
    scripted = set()
    for names in by_mesh.values():
        if len(names) > 1 and not any(any(k in comps[n]["props"] for k in XFORM_KEYS) for n in names):
            scripted.update(names)
    out = []
    for name, mesh, kind in found:
        if name in scripted:
            continue
        chain, cur, seen = [], name, set()
        while cur and cur in comps and cur not in seen:
            seen.add(cur)
            p = comps[cur]["props"]
            chain.append((p.get("RelativeLocation") or {}, p.get("RelativeRotation") or {}, p.get("RelativeScale3D") or {}))
            cur = comps[cur]["parent"]
        chain.reverse()
        mats = {}
        for i, m in enumerate(comps[name]["props"].get("OverrideMaterials") or []):
            mp = ref_path(m) if m else ""
            if mp:
                mats[i] = mp
        out.append({"name": name, "mesh": mesh, "kind": kind, "chain": chain, "materials": mats})
    return out


def mesh_names(finder, bp_file):
    return [asset_name(p["mesh"]) for p in parts(finder, bp_file)]


def is_blueprint(path):
    return os.path.basename(path or "").startswith("BP_")
