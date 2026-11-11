import json, sys, glob, os
SKIP = ("VolumeSize","BodyDissolve","erode","Thermal","Stencil","Dissolve","StartPoint","EndPoint","SpawnEffect","CharacterDesaturation","HitLocation","LocationOffset","Squach","ZMask","Play Rate","Sample Rate","DebugCurrent","QuatOffset","PosOffset","IsBoneVAT","DEBUG","EnableSecondary","Pos Tex","Rot Tex","ImpactRender","Decal","_DetailNormalOverride","_BaseNormalOverride","_ColorOverride","_LayerMask","_SurfaceOverride","_WidthRatio")
for name in sys.argv[1:]:
    f = glob.glob(rf"D:/BlenderStuff/Fmodel/Output/Exports/Discovery/Content/Discovery/MaterialLibrary/**/{name}.json", recursive=True)
    if not f: print("==", name, "NOT FOUND"); continue
    d = json.load(open(f[0], encoding="utf-8-sig")); e = [x for x in d if x.get("Type") == "Material"][0]
    ced = e.get("CachedExpressionData") or e["Properties"].get("CachedExpressionData")
    def names(i):
        ent = ced.get("RuntimeEntries" if i == 0 else f"RuntimeEntries[{i}]") or {}
        return [p["Name"] for p in ent.get("ParameterInfoSet", [])]
    pr = e["Properties"]
    print("==", name, {k: pr.get(k) for k in ("BlendMode", "ShadingModel", "TwoSided", "OpacityMaskClipValue") if k in pr})
    out = []
    for n, v in zip(names(0), ced.get("ScalarValues", [])):
        if not any(s in n for s in SKIP): out.append(f"{n}={round(v,3)}")
    print("  S:", ", ".join(out))
    out = []
    for n, v in zip(names(1), ced.get("VectorValues", [])):
        if not any(s in n for s in SKIP): out.append(f"{n}={v.get('Hex')}/{round(v.get('A',1),2)}")
    print("  V:", ", ".join(out))
    out = []
    for n, v in zip(names(3), ced.get("TextureValues", [])):
        if not any(s in n for s in SKIP): out.append(f"{n}={((v or {}).get('ObjectPath') or '').rsplit('/',1)[-1]}")
    print("  T:", ", ".join(out))
    out = [f"{n}={v}" for n, v in zip(names(8), ced.get("StaticSwitchValues", [])) if not any(s in n for s in SKIP)]
    print("  SW:", ", ".join(out))
