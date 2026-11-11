import bpy, numpy as np, os, sys
import glob
FILES = sys.argv[sys.argv.index("--")+1:]
SP = os.environ["SP"]
for f in FILES:
    name = os.path.splitext(os.path.basename(f))[0]
    img = bpy.data.images.load(f); img.colorspace_settings.name = "Non-Color"
    w, h = img.size
    px = np.empty(w*h*4, dtype=np.float32); img.pixels.foreach_get(px); px = px.reshape(h, w, 4)
    print("STAT", name, w, h, " ".join(f"{c}:{px[...,i].min():.2f}/{px[...,i].mean():.2f}/{px[...,i].max():.2f}" for i,c in enumerate("RGBA")))
    if name.endswith("_C") or True:
        sheet = np.concatenate([px[..., i] for i in range(4)], axis=1)  # R|G|B|A side by side
        small = sheet[::4, ::4]
        out = bpy.data.images.new(name+"_sheet", small.shape[1], small.shape[0])
        rgba = np.stack([small]*3 + [np.ones_like(small)], -1).astype(np.float32)
        out.pixels.foreach_set(rgba.ravel()); out.filepath_raw = os.path.join(SP, name+"_ch.png"); out.file_format='PNG'; out.save()
