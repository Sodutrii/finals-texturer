import bpy, numpy as np, sys
for f in sys.argv[sys.argv.index("--")+1:]:
    im = bpy.data.images.load(f); im.colorspace_settings.name = "Non-Color"
    w, h = im.size; a = np.empty(w*h*4, dtype=np.float32); im.pixels.foreach_get(a); b = a.reshape(-1,4)[:,2]
    lay = np.clip(np.round(b*8), 1, 8).astype(int)
    c = np.bincount(lay, minlength=9)[1:]
    print("LAYERS", f.split("/")[-1], {i+1: f"{v/len(b)*100:.1f}%" for i, v in enumerate(c) if v})
