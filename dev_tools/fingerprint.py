import pickle, sys, json, os, random, collections
mats, mi_master = pickle.load(open(sys.argv[1], "rb"))
def master_params(e):
    ced = (e.get("Properties") or {}).get("CachedExpressionData") or e.get("CachedExpressionData") or {}
    names = set()
    for i in range(10):
        ent = ced.get("RuntimeEntries" if i == 0 else f"RuntimeEntries[{i}]") or {}
        for pi in ent.get("ParameterInfoSet") or []: names.add(pi.get("Name"))
    return names
masters = {}
for s, m in mats.items():
    if m[0] == "Material":
        ps = master_params(m[4])
        if ps: masters[s] = ps
print("masters with params:", len(masters))
COMMON = set.intersection(*[v for v in masters.values() if len(v) > 20])  # global params shared by all character masters
W = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
def guess(names):
    names = set(names) - COMMON
    best = None
    for m, ps in masters.items():
        hit = len(names & ps)
        if not hit: continue
        score = (round(hit - W * len(ps - COMMON), 6), -len(ps))
        if best is None or score > best[0]: best = (score, m)
    return best[1] if best else None
def chain_names(s):
    names = set()
    while s in mats:
        t, par, pr, p, e = mats[s]
        if t == "Material": break
        for k in ("ScalarParameterValues", "VectorParameterValues", "TextureParameterValues"):
            for v in pr.get(k) or []: names.add(v["ParameterInfo"]["Name"])
        for v in ((pr.get("StaticParametersRuntime") or {}).get("StaticSwitchParameters") or []): names.add(v["ParameterInfo"]["Name"])
        s = par
    return names
mis = [s for s, m in mats.items() if m[0] == "MaterialInstanceConstant" and not mi_master.get(s, "").startswith("MISSING") and mi_master.get(s) in masters]
random.seed(1); sample = random.sample(mis, min(1500, len(mis)))
ok = 0; wrong = collections.Counter()
for s in sample:
    g = guess(chain_names(s))
    if g == mi_master[s]: ok += 1
    else: wrong[(mi_master[s], g)] += 1
print(f"correct {ok}/{len(sample)}")
for k, v in wrong.most_common(15): print(v, k)
FAM = {"m_character_8layers_master": "LAYERED", "m_character_8layers_animatedpattern_master": "LAYERED",
       "m_character_layered": "LAYERED_OLD", "m_character_preview": "PREVIEW", "m_lace": "LACE", "m_laceskirt": "LACE_SKIRT", "m_feather": "FEATHER"}
# candidates: masters whose JSON lives under MaterialLibrary/Character or that are named like character masters
cands = {m for m in masters if "\MaterialLibrary\Character" in mats[m][3] or m.startswith(("m_character", "m_charachter"))}
used = collections.Counter(mi_master[s] for s in mis if "\Characters\\" in mats[s][3])
print("candidates:", len(cands), "| character MIs whose master is outside candidates:", sum(v for k, v in used.items() if k not in cands))
print("  outside:", [k for k, v in used.most_common(30) if k not in cands])
def guess2(names):
    names = set(names) - COMMON
    best = None
    for m in cands:
        ps = masters[m]; hit = len(names & ps)
        if not hit: continue
        score = (hit, -len(ps))
        if best is None or score > best[0]: best = (score, m)
    return best[1] if best else None
test = [s for s in mis if "\Characters\\" in mats[s][3] and mi_master[s] in cands]
ok_exact = ok_fam = 0; wrong = collections.Counter()
for s in test:
    g = guess2(chain_names(s)); t = mi_master[s]
    ok_exact += g == t
    if FAM.get(g, g) == FAM.get(t, t): ok_fam += 1
    else: wrong[(t, g)] += 1
print(f"clothing MIs {len(test)}: exact {ok_exact}, right builder {ok_fam} ({ok_fam/len(test):.1%})")
for k, v in wrong.most_common(12): print("  ", v, k)
import math
df = collections.Counter(n for m in cands for n in masters[m])
def guess3(names):
    names = set(names) - COMMON
    best = None
    for m in cands:
        ps = masters[m]
        sc = sum(1.0 / df[n] for n in names & ps)
        if not sc: continue
        score = (round(sc, 6), -len(ps))
        if best is None or score > best[0]: best = (score, m)
    return best[1] if best else None
FAM2 = dict(FAM); FAM2.update({"m_characterskinnedattachment": "m_characterattachment", "m_characterattachment_teamcolor": "m_characterattachment",
    "m_character_plasticsheet_nocontactshadow": "m_character_plasticsheet", "m_ledscreen_head": "m_ledscreen", "m_characternails_halloweenghosts": "m_characternails_base"})
for fn in (guess2, guess3):
    okf = 0; wrong = collections.Counter()
    for s in test:
        g = fn(chain_names(s)); t = mi_master[s]
        if FAM2.get(g, g) == FAM2.get(t, t): okf += 1
        else: wrong[(t, g)] += 1
    print(fn.__name__, f"right family {okf}/{len(test)} ({okf/len(test):.1%})", wrong.most_common(6))
new_only = masters["m_character_8layers_master"] - masters["m_character_layered"]
old_only = masters["m_character_layered"] - masters["m_character_8layers_master"]
print("new-only names:", sorted(new_only)[:40], len(new_only))
print("old-only names:", sorted(old_only)[:40], len(old_only))
conf = [s for s in test if mi_master[s] == "m_character_8layers_master" and guess2(chain_names(s)) == "m_character_layered"]
c = collections.Counter(n for s in conf for n in chain_names(s) & new_only); print("new-only names in confused:", c.most_common(10), len(conf))
newmis = [s for s in test if mi_master[s] == "m_character_8layers_master"]; oldmis = [s for s in test if mi_master[s] == "m_character_layered"]
for n in ("DecalPositionU", "UseLayer2", "Decal", "1_HueVariationStrength", "1_UseDecal", "1_DecalColor", "DecalTint", "BaseColor"):
    print(n, "new:", sum(n in chain_names(s) for s in newmis), "/", len(newmis), "old:", sum(n in chain_names(s) for s in oldmis), "/", len(oldmis))
