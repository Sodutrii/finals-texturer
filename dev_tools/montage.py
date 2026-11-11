import bpy, numpy as np, os, sys
args = sys.argv[sys.argv.index("--") + 1:]
out, cols, files = args[0], int(args[1]), args[2:]
SP = os.environ["SP"]; H = int(os.environ.get("TILE_H", "300"))
tiles = []
for f in files:
    im = bpy.data.images.load(os.path.join(SP, f)); w, h = im.size
    a = np.empty(w*h*4, dtype=np.float32); im.pixels.foreach_get(a); a = a.reshape(h, w, 4)
    W = int(H * w / h)
    ys = (np.arange(H) * h / H).astype(int); xs = (np.arange(W) * w / W).astype(int); tiles.append(a[ys][:, xs])
W = max(t.shape[1] for t in tiles)
tiles = [np.pad(t, ((0, 0), (0, W - t.shape[1]), (0, 0)), constant_values=1.0) for t in tiles]
while len(tiles) % cols: tiles.append(np.ones_like(tiles[0]))
rows = [np.concatenate(tiles[i:i+cols], axis=1) for i in range(0, len(tiles), cols)]
grid = np.concatenate(rows[::-1], axis=0)
img = bpy.data.images.new(out, grid.shape[1], grid.shape[0]); img.pixels.foreach_set(grid.ravel())
img.filepath_raw = os.path.join(SP, out + ".png"); img.file_format = 'PNG'; img.save()
