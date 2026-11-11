import bpy, numpy as np, sys
a = sys.argv[sys.argv.index("--")+1:]
def load(f):
    im = bpy.data.images.load(f); im.colorspace_settings.name = "Non-Color"
    w, h = im.size; x = np.empty(w*h*4, dtype=np.float32); im.pixels.foreach_get(x); return x.reshape(h, w, 4)
ocm, base = load(a[0]), load(a[1])
if base.shape != ocm.shape:
    ys = (np.arange(ocm.shape[0]) * base.shape[0] / ocm.shape[0]).astype(int); xs = (np.arange(ocm.shape[1]) * base.shape[1] / ocm.shape[1]).astype(int)
    base = base[ys][:, xs]
lay = np.clip(np.round(ocm[..., 2]*8), 1, 8).astype(int)
for k in range(1, 9):
    m = lay == k
    if m.sum() > 100:
        r = base[..., 0][m]
        print(f"SCHEME layer {k}: {m.mean()*100:.1f}%  R mean {r.mean():.2f}  p10 {np.percentile(r,10):.2f}  p90 {np.percentile(r,90):.2f}")
