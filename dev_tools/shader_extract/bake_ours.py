"""Blender: import a weapon skin with the add-on and bake, per mesh part, in UV0 space:
UV0 / UV1 per texel, coverage, and our material's base colour / roughness / metallic.

blender --factory-startup -b --python bake_ours.py -- <exports root> <weapon folder> <skin id suffix|DEFAULT> <out dir> [res]
Writes <out dir>/<object>.npz with uv0, uv1 (UE convention: v = 0 at the top), mask, base, rough, metal, mi.
"""
import importlib
import os
import sys

import bpy
import numpy as np

args = sys.argv[sys.argv.index("--") + 1:]
EXP, WKEY, SKEY, OUT = args[:4]
RES = int(args[4]) if len(args) > 4 else 256
ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)
importlib.import_module("add-on-io-scene-psk-psa-v9_1_2").register()
import finals_texturer  # noqa: E402
finals_texturer.register()
from finals_texturer import weapons  # noqa: E402
from finals_texturer.ue_assets import Log  # noqa: E402

os.makedirs(OUT, exist_ok=True)
weapons._ctx["root"] = lambda c: EXP
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o)
w = next(x for x in weapons.weapons(EXP) if x["folder"] == WKEY)
skin = next(s for s in weapons.skins(EXP, w)
            if (SKEY == "DEFAULT" and s["id"] == "DEFAULT") or s["id"].endswith(f"_{SKEY}.json"))
objs = weapons.import_weapon(bpy.context, w, skin, Log())

sc = bpy.context.scene
sc.render.engine = "CYCLES"
sc.cycles.samples = 1
sc.cycles.device = "CPU"
sc.render.bake.margin = 0


def bake(obj, mat, source, name):
    """Bake an emission of `source` (socket or constant) for one material into a float image."""
    nt = mat.node_tree
    out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output)
    keep = out.inputs["Surface"].links[0].from_socket if out.inputs["Surface"].is_linked else None
    em = nt.nodes.new("ShaderNodeEmission")
    if isinstance(source, bpy.types.NodeSocket):
        nt.links.new(source, em.inputs["Color"])
    else:
        em.inputs["Color"].default_value = source
    nt.links.new(em.outputs[0], out.inputs["Surface"])
    img = bpy.data.images.new(name, RES, RES, alpha=True, float_buffer=True)
    img.colorspace_settings.name = "Non-Color"
    tn = nt.nodes.new("ShaderNodeTexImage")
    tn.image = img
    nt.nodes.active = tn
    bpy.ops.object.bake(type="EMIT")
    px = np.array(img.pixels[:], np.float32).reshape(RES, RES, 4)[::-1]      # top row first
    nt.nodes.remove(tn); nt.nodes.remove(em)
    if keep is not None:
        nt.links.new(keep, out.inputs["Surface"])
    bpy.data.images.remove(img)
    return px


for obj in [o for o in objs if o.type == "MESH"]:
    if len(obj.material_slots) != 1 or len(obj.data.uv_layers) < 2 or obj.material_slots[0].material is None:
        continue
    mat = obj.material_slots[0].material
    for o in bpy.context.selected_objects:
        o.select_set(False)
    obj.hide_render = False
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        continue

    def src(name):
        s = bsdf.inputs[name]
        if s.is_linked:
            return s.links[0].from_socket
        v = s.default_value
        return tuple(v) if hasattr(v, "__len__") else (v, v, v, 1.0)
    uvn = nt.nodes.new("ShaderNodeUVMap")
    uvn.uv_map = obj.data.uv_layers[1].name
    uv1 = bake(obj, mat, uvn.outputs["UV"], "uv1")
    nt.nodes.remove(uvn)
    cov = bake(obj, mat, (1.0, 1.0, 1.0, 1.0), "mask")
    base = bake(obj, mat, src("Base Color"), "base")
    rough = bake(obj, mat, src("Roughness"), "rough")
    metal = bake(obj, mat, src("Metallic"), "metal")
    g = (np.arange(RES) + 0.5) / RES
    uu, vv = np.meshgrid(g, g)                   # vv: top row first = UE v
    np.savez_compressed(os.path.join(OUT, f"{obj.name}.npz"),
                        uv0=np.stack([uu, vv], -1), uv1=np.stack([uv1[..., 0], 1.0 - uv1[..., 1]], -1),
                        mask=cov[..., 0] > 0.5, base=base[..., :3], rough=rough[..., 0], metal=metal[..., 0],
                        mi=mat.get("ft_material", mat.name))
    print("BAKED", obj.name, mat.get("ft_material", mat.name))
