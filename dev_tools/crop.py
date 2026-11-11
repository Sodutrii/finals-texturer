import bpy, numpy as np, os, sys
a = sys.argv[sys.argv.index("--")+1:]
f, x0, y0, w, out = a[0], int(a[1]), int(a[2]), int(a[3]), a[4]
im = bpy.data.images.load(f); W, H = im.size; px = np.empty(W*H*4, dtype=np.float32); im.pixels.foreach_get(px); px = px.reshape(H, W, 4)
crop = px[H-y0-w:H-y0, x0:x0+w]
k = max(1, 512 // w); crop = np.repeat(np.repeat(crop, k, 0), k, 1)
img = bpy.data.images.new(out, crop.shape[1], crop.shape[0]); img.pixels.foreach_set(crop.ravel())
img.filepath_raw = os.path.join(os.environ["SP"], out + ".png"); img.file_format = 'PNG'; img.save()
