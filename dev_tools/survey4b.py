import pickle, collections, sys, os, json
index, types = pickle.load(open(sys.argv[1], "rb"))
mats, mi_master = pickle.load(open(sys.argv[2], "rb"))
def load(p):
    try: return json.load(open(p, encoding="utf-8-sig"))
    except Exception: return None
def stem(ref):
    op = (ref or {}).get("AssetPathName") or (ref or {}).get("ObjectPath") or ""
    return op.rsplit("/", 1)[-1].split(".")[0].lower()
PREFIXES = ("m_character_layered", "m_character_8layers", "m_character_preview", "m_lace", "m_feather",
            "m_characterattachment", "m_characterskinnedattachment", "m_characterglass", "m_character_visorgradient", "m_visor",
            "m_character_holographicmaterial", "m_character_plasticsheet", "m_characterlayered_teamcolor")
class _S:
    def __contains__(self, m): return m.startswith(PREFIXES)
SUPPORTED = _S()
keys = collections.Counter(); slots = collections.Counter(); nparts = collections.Counter(); nover = collections.Counter()
layout = collections.Counter(); unsupported_da = collections.Counter(); da_total = 0; da_ok = 0
cat_da = collections.Counter()
part_kinds = collections.Counter()
samples = {}
for p, ts in types.items():
    if not ts or ts[0] != "CharacterCustomizationItem": continue
    d = load(p)
    if not d: continue
    e = d[0]; pr = e.get("Properties") or {}
    da_total += 1
    for k in pr: keys[k] += 1
    for t in pr.get("AssetTags", []) or []:
        if t.startswith("Customization.Slot."): slots[t[19:]] += 1
    parts = pr.get("VisualParts") or []
    nparts[len(parts)] += 1
    for vp in parts:
        kinds = tuple(k for k in ("StaticMesh", "SkeletalMesh", "Effect") if stem(vp.get(k)))
        part_kinds[kinds] += 1
    ov = pr.get("MaterialOverrides") or []
    nover[len(ov)] += 1
    parent = os.path.basename(os.path.dirname(os.path.dirname(p)))
    layout["in Skins/<x>/" if parent == "Skins" else "elsewhere"] += 1
    ms = {mi_master.get(stem(o.get("Value")), "MISSING:" + stem(o.get("Value"))) for o in ov}
    bad = {m for m in ms if m not in SUPPORTED}
    if not bad: da_ok += 1
    for m in bad:
        unsupported_da[m] += 1
        samples.setdefault(m, p)
print("DAs:", da_total, "with all override masters supported:", da_ok)
print("== DA property keys"); print(keys.most_common(40))
print("== slot tags"); print(slots.most_common(30))
print("== VisualParts per DA"); print(sorted(nparts.items()))
print("== part kinds"); print(part_kinds.most_common())
print("== MaterialOverrides per DA"); print(sorted(nover.items()))
print("== layout"); print(layout)
print("== unsupported masters by # skins")
for k, v in unsupported_da.most_common(40): print(f"{v:5d} {k}  e.g. {samples[k].split('Characters')[-1]}")
