import bpy, numpy as np, sys
f = sys.argv[sys.argv.index("--")+1]; pts = [tuple(map(int, s.split(","))) for s in sys.argv[sys.argv.index("--")+2:]]
im = bpy.data.images.load(f); w, h = im.size; a = np.empty(w*h*4, dtype=np.float32); im.pixels.foreach_get(a); a = a.reshape(h, w, 4)
for x, y in pts:
    patch = a[h-1-y-3:h-1-y+3, x-3:x+3, :3].reshape(-1, 3).mean(0)
    print("PX", (x, y), tuple(int(c*255) for c in patch))
