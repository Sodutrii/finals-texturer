"""Read-only survey of FModel exports: types, master shaders, skins."""
import json, os, re, sys, pickle, collections, time
ROOT = r"D:\BlenderStuff\Fmodel\Output\Exports"
OUT = sys.argv[1]
t0 = time.time()
TYPE_RE = re.compile(rb'"Type"\s*:\s*"([^"]+)"')
index = {}          # lower stem -> [paths]
types = {}          # path -> list of export types (first 20)
for dp, dn, fn in os.walk(ROOT):
    for f in fn:
        if not f.endswith(".json"): continue
        p = os.path.join(dp, f)
        index.setdefault(f[:-5].lower(), []).append(p)
        try:
            with open(p, "rb") as fh: head = fh.read(4096)
        except OSError: continue
        types[p] = [m.decode() for m in TYPE_RE.findall(head)[:20]]
print("indexed", len(types), "json in", round(time.time()-t0), "s", file=sys.stderr)
pickle.dump((index, types), open(OUT, "wb"))
