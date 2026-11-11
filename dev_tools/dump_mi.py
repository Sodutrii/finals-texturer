import json, sys, os
def load(p): return json.load(open(p, encoding="utf-8-sig"))
for f in sys.argv[1:]:
    d = load(f)
    d = d if isinstance(d, list) else [d]
    for e in d:
        if e.get("Type") != "MaterialInstanceConstant": continue
        pr = e["Properties"]
        print(f"== {os.path.basename(f)}  parent={(pr.get('Parent') or {}).get('ObjectName')}  {json.dumps(pr.get('BasePropertyOverrides'))}")
        for v in pr.get("ScalarParameterValues", []): print("   S", v["ParameterInfo"]["Name"], round(v["ParameterValue"], 4))
        for v in pr.get("VectorParameterValues", []):
            c = v["ParameterValue"]; print("   V", v["ParameterInfo"]["Name"], tuple(round(c[k], 3) for k in "RGBA"), c.get("Hex"))
        for v in pr.get("TextureParameterValues", []): print("   T", v["ParameterInfo"]["Name"], ((v.get("ParameterValue") or {}).get("ObjectPath") or "").rsplit("/", 1)[-1])
        for v in ((pr.get("StaticParametersRuntime") or {}).get("StaticSwitchParameters") or []): print("   SW", v["ParameterInfo"]["Name"], v["Value"])
        pass
    for e in d:
        if isinstance(e, dict) and e.get("Type") in ("StaticMesh", "SkeletalMesh"):
            mats = e.get("StaticMaterials") or e.get("SkeletalMaterials") or (e.get("Properties") or {}).get("StaticMaterials") or (e.get("Properties") or {}).get("SkeletalMaterials") or []
            print(f"== {os.path.basename(f)} slots:", [(m.get("MaterialSlotName"), (m.get("MaterialInterface") or m.get("Material") or {}).get("ObjectName")) for m in mats])
