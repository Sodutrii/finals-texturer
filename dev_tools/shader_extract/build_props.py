"""Build the prop list shipped with the add-on (finals_texturer/data/props.json): every prop Blueprint
under Environment/Props that can be picked up (PickupableComponent) or broken
(EmbarkFracturedDestructibleMeshComponent / CosmeticDestructibleMeshComponent).

    1. dotnet run -- <dump dir> --dump Discovery/Content/Discovery/Environment/Props --names BP_
    2. python -I build_props.py <dump dir> [<out file>]

Left out: event props, arena-kit structures and pieces the Objects tab lists elsewhere. Names come from
the Blueprint name (no in-game names exist for props). Run again after a game update.
"""
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.normpath(os.path.join(HERE, "..", "..", "finals_texturer", "data", "props.json"))
GAME = "Discovery/Content/Discovery/"
SKIP = ("/Event/", "ATK_Walkway", "ATK_Billboard", "ATK_Screen", "ATK_Wall", "ATK_Barrel", "ATK_Canister",
        "/ParentBlueprints/")
BREAK = {"EmbarkFracturedDestructibleMeshComponent", "CosmeticDestructibleMeshComponent"}
LOCATIONS = {"HongKong": "Hong Kong", "LasVegas": "Las Vegas", "LasVegas_02": "Las Vegas", "Arena_02": "Arena",
             "Arena_04": "Arena"}
PREFIXES = ("FSM_", "Hon_", "Kyo_", "Mon_", "Ber_", "Las_", "LAS_", "Kor_", "Spa_", "Fan_", "LasVegas_02_",
            "ATK_", "Are2_", "Are4_", "Arena_")


def nice(bp_name):
    n = bp_name[3:] if bp_name.startswith("BP_") else bp_name
    changed = True
    while changed:
        changed = False
        for p in PREFIXES:
            if n.startswith(p):
                n, changed = n[len(p):], True
    words = []
    for part in n.split("_"):
        words += re.findall(r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+|[A-Z]+", part) or [part]
    return " ".join(w if not w.islower() else w.capitalize() for w in words if w)


def category(rel):
    parts = rel.split("/")              # Environment/Props/<Generic|Locations|ArenaToolKit>/<X>/...
    if parts[2] == "Generic":
        return f"Props: {parts[3]}"
    if parts[2] == "Locations":
        return f"Props: {LOCATIONS.get(parts[3], re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', parts[3]))}"
    return "Props: Arena"


def main():
    dump = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else OUT
    rows = []
    for p in sorted(glob.glob(os.path.join(dump, "**", "BP_*.json"), recursive=True)):
        rel = os.path.relpath(p, dump).replace("\\", "/")
        if not rel.startswith(GAME + "Environment/Props/") or any(s in rel for s in SKIP):
            continue
        try:
            data = json.load(open(p, encoding="utf-8"))
        except ValueError:
            continue
        types = {e.get("Type", "") for e in data}
        lift, brk = "PickupableComponent" in types, bool(types & BREAK)
        if not (lift or brk):
            continue
        short = rel[len(GAME):-5]
        rows.append({"category": category(short), "name": nice(os.path.basename(short)),
                     "path": rel[:-5], "kind": "liftable" if lift else "breakable"})
    # same name twice in a category: add the folder to tell them apart
    seen = {}
    for r in rows:
        seen.setdefault((r["category"], r["name"]), []).append(r)
    for (_c, _n), rs in seen.items():
        if len(rs) > 1:
            for r in rs:
                r["name"] += f" ({nice(r['path'].split('/')[-2])})"
    rows.sort(key=lambda r: (r["category"], r["name"]))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump({"version": 1, "props": rows}, open(out, "w", encoding="utf-8"), indent=0, ensure_ascii=False)
    cats = sorted({r["category"] for r in rows})
    print(f"{out}: {len(rows)} props in {len(cats)} categories ({sum(r['kind'] == 'liftable' for r in rows)} liftable)")
    for c in cats:
        print(f"  {c}: {sum(r['category'] == c for r in rows)}")


if __name__ == "__main__":
    main()
