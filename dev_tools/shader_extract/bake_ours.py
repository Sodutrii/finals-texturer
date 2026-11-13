"""Blender: import a weapon skin or a clothing item with the add-on and bake our materials in UV0 space:
UV0 / UV1 / UV2 per texel, coverage, and base colour / roughness / metallic.

Weapon:   blender --factory-startup -b --python bake_ours.py -- <exports root> <weapon folder> <skin id suffix|DEFAULT> <out dir> [res]
Clothing: blender --factory-startup -b --python bake_ours.py -- <exports root> --item <mesh .psk path, relative to the
          exports root> <skin name|AUTO|NONE> <out dir> [res]
Object:   blender --factory-startup -b --python bake_ours.py -- <exports root> --object <Objects-tab path (Exports-relative,
          no extension)> - <out dir> [res]   (imports the object like the Objects tab)
Face:     blender --factory-startup -b --python bake_ours.py -- <exports root> --face <DA_Head_* file name> <L|M|H> <out dir> [res]
          (imports body + head like the Character tab, male, not muscular)
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
FACE = args[1] == "--face"
OBJECT = args[1] == "--object"
if ITEM or FACE or OBJECT:
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
from finals_texturer import character, importer, objects_tab, weapons  # noqa: E402
from finals_texturer.ue_assets import Log, find_skins  # noqa: E402

os.makedirs(OUT, exist_ok=True)
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o)
log = Log()
if OBJECT:
    objects_tab._ctx["root"] = lambda c: EXP
    obj = next(o for o in objects_tab.objects() if o["path"] == MESH)
    objs = objects_tab.import_object(bpy.context, obj, log)
elif FACE:
    character._ctx["root"] = lambda c: EXP
    face = next(f for f in character.faces(EXP) if os.path.basename(f["path"])[:-5] == MESH)
    objs = character.import_character(bpy.context, face, SKEY, "male", False, log)
elif ITEM:
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


def vcol_src(mat, obj):
    if not obj.data.color_attributes:
        return (1.0, 1.0, 1.0, 1.0)
    n = mat.node_tree.nodes.new("ShaderNodeVertexColor")
    n.layer_name = obj.data.color_attributes[0].name
    return n.outputs["Color"]


def bake_slot(src_obj, mat, slot_indices, name, uv_names):
    """Bake one material on a temporary copy of the object holding only that material's faces (parts of a
    mesh often share UV space, e.g. a head's face, lashes and teeth)."""
    import bmesh
    obj = src_obj.copy()
    obj.data = src_obj.data.copy()
    bpy.context.scene.collection.objects.link(obj)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.material_index not in slot_indices], context="FACES")
    bm.to_mesh(obj.data)
    bm.free()
    if not obj.data.polygons:
        bpy.data.objects.remove(obj)
        return
    for o in bpy.context.selected_objects:
        o.select_set(False)
    obj.hide_render = False
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    # bake target: UV0 with every face moved into the 0..1 tile (shoes, gloves use other tiles; Cycles only
    # bakes faces inside the image). The real UV0 is baked below for the game shader.
    me = obj.data
    src_uv = me.uv_layers[uv_names[0]].data
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
        s = uv_src(mat, uv_names, k)
        uvs.append(bake(obj, {mat: s}, f"uv{k}"))
        if isinstance(s, bpy.types.NodeSocket):
            mat.node_tree.nodes.remove(s.node)
    vs = vcol_src(mat, obj)
    vcol = bake(obj, {mat: vs}, "vcol")
    if isinstance(vs, bpy.types.NodeSocket):
        mat.node_tree.nodes.remove(vs.node)
    base = bake(obj, {mat: bsdf_src(mat, "Base Color")}, "base")
    rough = bake(obj, {mat: bsdf_src(mat, "Roughness")}, "rough")
    metal = bake(obj, {mat: bsdf_src(mat, "Metallic")}, "metal")
    cov = bake(obj, {mat: (1.0, 1.0, 1.0, 1.0)}, "mask")
    # the real UV0 (meshes may use other UV tiles, e.g. gloves: one hand per tile)
    np.savez_compressed(os.path.join(OUT, f"{name}.npz"),
                        uv0=np.stack([uvs[0][..., 0], 1.0 - uvs[0][..., 1]], -1),
                        uv1=np.stack([uvs[1][..., 0], 1.0 - uvs[1][..., 1]], -1),
                        uv2=np.stack([uvs[2][..., 0], 1.0 - uvs[2][..., 1]], -1),
                        mask=cov[..., 0] > 0.5, vcol=vcol[..., :4], base=base[..., :3], rough=rough[..., 0], metal=metal[..., 0],
                        mi=mat.get("ft_material", mat.name), kind=mat.get("ft_shader", ""))
    bpy.data.objects.remove(obj)


for obj in [o for o in objs if o.type == "MESH"]:
    mats = [s.material for s in obj.material_slots if s.material is not None and s.material.node_tree]
    mats = list(dict.fromkeys(mats))
    names = [l.name for l in obj.data.uv_layers]
    if not mats or not names:
        continue
    for i, mat in enumerate(mats):
        slots = {k for k, s in enumerate(obj.material_slots) if s.material is mat}
        bake_slot(obj, mat, slots, f"{obj.name}__{i}", names)
        print("BAKED", obj.name, i, mat.get("ft_material", mat.name), mat.get("ft_shader", ""))
