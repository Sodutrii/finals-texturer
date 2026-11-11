import bpy, sys, importlib, mathutils, math
sys.path.insert(0, r"C:/Users/bisch/Desktop/New folder")
importlib.import_module("add-on-io-scene-psk-psa-v9_1_2").register()
for o in list(bpy.data.objects): bpy.data.objects.remove(o)
bpy.ops.psk.import_file(filepath=r"D:/BlenderStuff/Fmodel/Output/Exports/Discovery/Content/Discovery/Characters/Racing/Assets/BikerLoosePants/SK_Racing_BikerLoosePants_M.psk")
me = [o for o in bpy.data.objects if o.type == 'MESH'][0]; mw = me.matrix_world; uvl = me.data.uv_layers[0].data
faces = []
for p in me.data.polygons:
    uv = sum((uvl[i].uv for i in p.loop_indices), mathutils.Vector((0, 0))) / p.loop_total
    faces.append((uv, mw @ p.center, (mw.to_3x3() @ p.normal).normalized()))
us=[f[0].x for f in faces]; vs=[f[0].y for f in faces]; print("PROBE uv range", min(us), max(us), min(vs), max(vs))
xs=[f[1].x for f in faces]; ys=[f[1].y for f in faces]; zs=[f[1].z for f in faces]; print("PROBE pos range x", min(xs), max(xs), "y", min(ys), max(ys), "z", min(zs), max(zs))
U, V, S = 0.1285, 0.45, 4.6073
half = 0.5 / S
cands = {"centre (0.5+U, 0.5+V)": (0.5+U, 0.5+V), "centre (0.5+U, 0.5-V)": (0.5+U, 0.5-V), "centre (U, 1-V)": (U, 1-V), "centre (U, V)": (U, V),
         "centre (0.5-U, 0.5+V)": (0.5-U, 0.5+V), "centre (0.5-U,0.5-V)": (0.5-U, 0.5-V), "corner (U,1-V)": (U+half, 1-V-half)}
for name, c in cands.items():
    c = mathutils.Vector(c)
    hit = [f for f in faces if abs((f[0].x % 1) - c.x) < half and abs((f[0].y % 1) - c.y) < half]
    if not hit: print("PROBE", name, "faces 0"); continue
    pos = sum((h[1] for h in hit), mathutils.Vector()) / len(hit); n = sum((h[2] for h in hit), mathutils.Vector()).normalized()
    print(f"PROBE {name}: c=({c.x:.2f},{c.y:.2f}) faces={len(hit)} pos=({pos.x:.0f},{pos.y:.0f},{pos.z:.0f}) normal=({n.x:.2f},{n.y:.2f},{n.z:.2f})")
