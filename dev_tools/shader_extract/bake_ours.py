"""Blender: import a weapon skin or a clothing item with the add-on and bake our materials in UV0 space:
UV0 / UV1 / UV2 per texel, coverage, and base colour / roughness / metallic.

Weapon:   blender --factory-startup -b --python bake_ours.py -- <exports root> <weapon folder> <skin id suffix|DEFAULT> <out dir> [res]
Clothing: blender --factory-startup -b --python bake_ours.py -- <exports root> --item <mesh .psk path, relative to the
          exports root> <skin name|AUTO|NONE> <out dir> [res]
Writes <out dir>/<object>__<slot>.npz per material slot with uv0, uv1, uv2 (UE convention: v = 0 at the top),
mask, base, rough, metal, mi (the material instance name). All slots of an object are baked in one go (each
material gets its own emission); the per-slot masks keep them apart.
"""
import importlib
import os
import sys

import bpy
import numpy as np

args = sys.argv[sys.argv.index("--") + 1:]
EXP = args[0]
ITEM = args[1] == "--item"
if ITEM:
    MESH, SKEY, OUT = args[2:5]
    RES = int(args[5]) if len(args) > 5 else 256
else:
    WKEY, SKEY, OUT = args[1:4]
    RES = int(args[4]) if len(args) > 4 else 256
ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)
importlib.import_module("add-on-io-scene-psk-psa-v9_1_2").register()
import finals_texturer  # noqa: E402
finals_texturer.register()
from finals_texturer import importer, weapons  # noqa: E402
from finals_texturer.ue_assets import Log, find_skins  # noqa: E402

os.makedirs(OUT, exist_ok=True)
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o)
log = Log()
if ITEM:
    mesh_path = os.path.join(EXP, MESH)
    skins = dict(find_skins(os.path.dirname(mesh_path)))
    choice = skins.get(SKEY, SKEY)
    objs = importer.import_psk(mesh_path, log)
    for m in [o for o in objs if o.type == "MESH"]:
        importer.apply_to_object(m, EXP, choice, log)
else:
    weapons._ctx["root"] = lambda c: EXP
    w = next(x for x in weapons.weapons(EXP) if x["folder"] == WKEY)
    skin = next(s for s in weapons.skins(EXP, w)
                if (SKEY == "DEFAULT" and s["id"] == "DEFAULT") or s["id"].endswith(f"_{SKEY}.json"))
    objs = weapons.import_weapon(bpy.context, w, skin, log)

sc = bpy.context.scene
sc.render.engine = "CYCLES"
sc.cycles.samples = 1
sc.cycles.device = "CPU"
sc.render.bake.margin = 0


def bake(obj, sources, name):
    """Bake, for every material of obj, an emission of sources[material] (socket or RGBA constant) into one
    float image (rows top first)."""
    img = bpy.data.images.new(name, RES, RES, alpha=True, float_buffer=True)
    img.colorspace_settings.name = "Non-Color"
    undo = []
    for mat, source in sources.items():
        nt = mat.node_tree
        out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output)
        keep = out.inputs["Surface"].links[0].from_socket if out.inputs["Surface"].is_linked else None
        em = nt.nodes.new("ShaderNodeEmission")
        if isinstance(source, bpy.types.NodeSocket):
            nt.links.new(source, em.inputs["Color"])
        else:
            em.inputs["Color"].default_value = source
        nt.links.new(em.outputs[0], out.inputs["Surface"])
        tn = nt.nodes.new("ShaderNodeTexImage")
        tn.image = img
        nt.nodes.active = tn
        undo.append((nt, out, keep, em, tn))
    bpy.ops.object.bake(type="EMIT")
    px = np.array(img.pixels[:], np.float32).reshape(RES, RES, 4)[::-1]
    for nt, out, keep, em, tn in undo:
        nt.nodes.remove(tn)
        nt.nodes.remove(em)
        if keep is not None:
            nt.links.new(keep, out.inputs["Surface"])
    bpy.data.images.remove(img)
    return px


def bsdf_src(mat, name):
    b = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if b is None:
        return (0.0, 0.0, 0.0, 1.0)
    s = b.inputs[name]
    if s.is_linked:
        return s.links[0].from_socket
    v = s.default_value
    return tuple(v) if hasattr(v, "__len__") else (v, v, v, 1.0)


def uv_src(mat, names, k):
    if len(names) <= k:
        return (0.0, 0.0, 0.0, 1.0)
    n = mat.node_tree.nodes.new("ShaderNodeUVMap")
    n.uv_map = names[k]
    return n.outputs["UV"]


for obj in [o for o in objs if o.type == "MESH"]:
    mats = [s.material for s in obj.material_slots if s.material is not None and s.material.node_tree]
    mats = list(dict.fromkeys(mats))
    if not mats:
        continue
    for o in bpy.context.selected_objects:
        o.select_set(False)
    obj.hide_render = False
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    names = [l.name for l in obj.data.uv_layers]
    if not names:
        continue
    # bake target: UV0 with every face moved into the 0..1 tile (shoes, gloves use other tiles; Cycles only
    # bakes faces inside the image). The real UV0 is baked below for the game shader.
    me = obj.data
    src_uv = me.uv_layers[names[0]].data
    tgt = me.uv_layers.new(name="ft_bake_tile0")
    for poly in me.polygons:
        idx = list(poly.loop_indices)
        cu = sum(src_uv[i].uv[0] for i in idx) / len(idx)
        cv = sum(src_uv[i].uv[1] for i in idx) / len(idx)
        fu, fv = np.floor(cu), np.floor(cv)
        for i in idx:
            tgt.data[i].uv = (src_uv[i].uv[0] - fu, src_uv[i].uv[1] - fv)
    me.uv_layers.active = tgt
    uvs = []
    for k in (0, 1, 2):
        srcs = {m: uv_src(m, names, k) for m in mats}
        uvs.append(bake(obj, srcs, f"uv{k}"))
        for m, s in srcs.items():
            if isinstance(s, bpy.types.NodeSocket):
                m.node_tree.nodes.remove(s.node)
    base = bake(obj, {m: bsdf_src(m, "Base Color") for m in mats}, "base")
    rough = bake(obj, {m: bsdf_src(m, "Roughness") for m in mats}, "rough")
    metal = bake(obj, {m: bsdf_src(m, "Metallic") for m in mats}, "metal")
    # the real UV0 (meshes may use other UV tiles, e.g. gloves: one hand per tile)
    uv0 = np.stack([uvs[0][..., 0], 1.0 - uvs[0][..., 1]], -1)
    for i, mat in enumerate(mats):
        cov = bake(obj, {m: ((1.0, 1.0, 1.0, 1.0) if m is mat else (0.0, 0.0, 0.0, 1.0)) for m in mats}, "mask")
        np.savez_compressed(os.path.join(OUT, f"{obj.name}__{i}.npz"),
                            uv0=uv0,
                            uv1=np.stack([uvs[1][..., 0], 1.0 - uvs[1][..., 1]], -1),
                            uv2=np.stack([uvs[2][..., 0], 1.0 - uvs[2][..., 1]], -1),
                            mask=cov[..., 0] > 0.5, base=base[..., :3], rough=rough[..., 0], metal=metal[..., 0],
                            mi=mat.get("ft_material", mat.name), kind=mat.get("ft_shader", ""))
        print("BAKED", obj.name, i, mat.get("ft_material", mat.name), mat.get("ft_shader", ""))
