import pickle, collections, sys
mats, mi_master = pickle.load(open(sys.argv[2], "rb"))
LAY = {"m_character_layered", "m_character_8layers_master", "m_character_8layers_animatedpattern_master"}
def chain_props(stem):
    out = []
    while stem in mats and len(out) < 15:
        out.append(mats[stem]); 
        if mats[stem][0] == "Material": break
        stem = mats[stem][1]
    return out
def switches(stem):
    sw = {}
    for m in reversed(chain_props(stem)):
        pr = m[2]
        for s in ((pr.get("StaticParametersRuntime") or pr.get("StaticParameters") or {}).get("StaticSwitchParameters") or []):
            sw[s["ParameterInfo"]["Name"]] = s.get("Value")
    return sw
def scal(stem):
    sc = {}
    for m in reversed(chain_props(stem)):
        for s in m[2].get("ScalarParameterValues") or []: sc[s["ParameterInfo"]["Name"]] = s["ParameterValue"]
    return sc
stats = collections.Counter(); layers = collections.Counter(); n = 0
for s, ms in mi_master.items():
    if ms not in LAY or "\Characters\\" not in mats[s][3]: continue
    if not s.startswith("mi_") or "skins" not in mats[s][3].lower(): continue   # skin-level MIs only
    n += 1
    sw = switches(s); sc = scal(s)
    k = max([i for i in range(2, 9) if sw.get(f"UseLayer{i}")] or [1]); layers[(ms, k)] += 1
    if any(sw.get(f"{i}_UseDecal") for i in range(1, 9)): stats[ms + " per-layer decal"] += 1
    if sw.get("UseDecal") or sw.get("bUseDecal"): stats[ms + " global decal"] += 1
    if sw.get("bUseOpacityMask"): stats[ms + " opacity mask"] += 1
    if any(sc.get(f"{i}_PatternStrength", 0) for i in range(1, 9)) : stats[ms + " pattern"] += 1
    if any(sc.get(f"{i}_EmissiveStrength", 0) or sc.get(f"{i}_Emissive", 0) for i in range(1, 9)) or sc.get("EmissiveStrength", 0): stats[ms + " emissive"] += 1
print("skin-level layered MIs:", n)
for k, v in sorted(layers.items()): print("layers", k, v)
for k, v in stats.most_common(): print(v, k)
sw_all = collections.Counter()
for s, ms in mi_master.items():
    if ms in LAY and "\Characters\\" in mats[s][3]:
        for k, v in switches(s).items():
            if v: sw_all[k] += 1
print("switches ON:", sw_all.most_common(40))
