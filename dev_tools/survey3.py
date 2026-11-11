import pickle, collections, sys, os, json, re
index, types = pickle.load(open(sys.argv[1], "rb"))
def load(p):
    try: return json.load(open(p, encoding="utf-8-sig"))
    except Exception: return None
def stem_of(ref):
    if not ref: return ""
    op = ref.get("ObjectPath") or ref.get("AssetPathName") or ""
    s = op.rsplit("/", 1)[-1].split(".")[0]
    return s
mats = {}   # stem -> (type, parent stem, props, path)
for p, ts in types.items():
    if "MaterialInstanceConstant" in ts or "Material" in ts:
        d = load(p)
        if not d: continue
        for e in d:
            if e.get("Type") in ("MaterialInstanceConstant", "Material"):
                pr = e.get("Properties") or {}
                mats[e["Name"].lower()] = (e["Type"], stem_of(pr.get("Parent")).lower(), pr, p, e)
                break
def master(stem, seen=None):
    chain = []
    while stem and stem not in chain and len(chain) < 15:
        chain.append(stem)
        m = mats.get(stem)
        if not m: return chain, "MISSING:" + stem
        if m[0] == "Material": return chain, stem
        stem = m[1]
    return chain, "?"
cnt = collections.Counter(); where = collections.defaultdict(collections.Counter); missing = collections.Counter()
mi_master = {}
for s, m in mats.items():
    if m[0] != "MaterialInstanceConstant": continue
    ch, ms = master(s)
    mi_master[s] = ms
    if "\Characters\\" not in m[3]: continue
    cnt[ms] += 1
    cat = m[3].split("\Characters\\")[1].split("\\")[0]
    where[ms][cat] += 1
print("== masters used by Characters MIs (count, master, top categories)")
for k, v in cnt.most_common(60):
    print(f"{v:5d} {k:55s} {dict(where[k].most_common(4))}")
pickle.dump((mats, mi_master), open(sys.argv[2], "wb"))
