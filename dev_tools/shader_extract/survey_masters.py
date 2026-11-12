"""Which master materials (and static-switch combinations) the exported items use.

    python -I survey_masters.py <exports root> <sub folder, e.g. Discovery/Content/Discovery/Characters> [out.json]

Resolves every MaterialInstanceConstant JSON under the folder with the add-on's chain resolver and counts
them by master, then by master + static switches. Each switch combination is its own compiled shader, so this
says which MIs to extract to cover most items. Writes the full table to out.json (master -> combos -> MIs).
"""
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from weaponsim import ue  # noqa: E402  (imports the add-on's ue_assets with bpy stubbed)


def main():
    root, sub = sys.argv[1], sys.argv[2]
    out = sys.argv[3] if len(sys.argv) > 3 else None
    log = ue.Log()
    log.info = log.warn = lambda *a: None
    finder = ue.AssetFinder(root, log=log)
    by_master = collections.Counter()
    combos = collections.defaultdict(lambda: collections.defaultdict(list))
    base = os.path.join(root, sub)
    n = 0
    for dp, _dn, fn in os.walk(base):
        for f in fn:
            if not (f.startswith("MI_") and f.endswith(".json")):
                continue
            p = os.path.join(dp, f)
            try:
                head = open(p, "rb").read(2000)
                if b"MaterialInstanceConstant" not in head:
                    continue
                mp = ue.resolve_material(finder, json_path=p, log=log)
            except Exception:
                continue
            n += 1
            master = mp.master or "?"
            by_master[master] += 1
            sw = ",".join(sorted(k for k, v in (mp.switches or {}).items() if v))
            combos[master][sw].append(os.path.relpath(p, root).replace("\\", "/")[:-5])
    print(f"{n} material instances")
    for m, c in by_master.most_common():
        print(f"{c:6d}  {m}  ({len(combos[m])} switch combinations)")
        for sw, mis in sorted(combos[m].items(), key=lambda kv: -len(kv[1]))[:6]:
            print(f"          {len(mis):5d}  [{sw[:150]}]")
    if out:
        json.dump({m: dict(c) for m, c in combos.items()}, open(out, "w", encoding="utf-8"), indent=1)


if __name__ == "__main__":
    main()
