import bpy, sys, importlib, bmesh, math
sys.path.insert(0, r"C:/Users/bisch/Desktop/New folder")
importlib.import_module("add-on-io-scene-psk-psa-v9_1_2").register()
for f in sys.argv[sys.argv.index("--") + 1:]:
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    bpy.ops.psk.import_file(filepath=f)
    me = [o for o in bpy.data.objects if o.type == 'MESH'][0]
    bm = bmesh.new()
    bm.from_mesh(me.data)
    bm.transform(me.matrix_world)
    for li, uv in enumerate(bm.loops.layers.uv.values()):
        A3 = AU = 0.0
        for fc in bm.faces:
            A3 += fc.calc_area()
            p = [x[uv].uv for x in fc.loops]
            for i in range(1, len(p) - 1):
                AU += abs((p[i].x - p[0].x) * (p[i + 1].y - p[0].y) - (p[i + 1].x - p[0].x) * (p[i].y - p[0].y)) / 2
        print(f"TEXEL {f.split('/')[-1]} uv{li}: {math.sqrt(A3 / max(AU, 1e-9)):.1f} units per UV unit")
