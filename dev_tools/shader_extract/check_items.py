"""Check clothing items against the game's compiled shaders: bake ours, extract what's missing, compare.

    python -I check_items.py <list file> <work dir> [--res 256] [--only <out name>]

<list file>: one item per line in dev_tools/regress.txt format:
    <mesh path relative to Characters> <skin name|AUTO|NONE> <out name> [...ignored]
  or a face:  DA_Head_<...> <L|M|H> <out name>
For each item: bake_ours.py (Blender) -> <work>/bake/<out name>/; every material instance it baked without an
extracted shader is extracted (ShaderExtract, needs FT_GAME_PAKS / FT_AES / FT_USMAP / FT_FMODEL_DATA) into
<work>/sm/; then compare_game.py -> <work>/cmp_<out name>.png. Prints one summary line per material slot.
"""
import glob
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXPORTS = os.environ.get("FT_EXPORTS", r"D:\BlenderStuff\Fmodel\Output\Exports")
BLENDER = os.environ.get("FT_BLENDER", r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe")
CHARS = "Discovery/Content/Discovery/Characters"


def mi_package(mi):
    """Package path of a material instance JSON in the exports (for ShaderExtract)."""
    hits = glob.glob(os.path.join(EXPORTS, "**", mi + ".json"), recursive=True)
    if not hits:
        return None
    return os.path.relpath(hits[0], EXPORTS).replace("\\", "/")[:-5]


def main():
    lst, work = sys.argv[1], sys.argv[2]
    res = sys.argv[sys.argv.index("--res") + 1] if "--res" in sys.argv else "256"
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    sm = os.path.join(work, "sm")
    os.makedirs(sm, exist_ok=True)
    for line in open(lst, encoding="utf-8"):
        parts = line.split()
        if len(parts) < 3 or line.startswith("#"):
            continue
        mesh, skin, name = parts[:3]
        if only and name != only:
            continue
        bake = os.path.join(work, "bake", name)
        print(f"== {name}: {mesh} [{skin}]", flush=True)
        mode = ["--face", mesh] if mesh.startswith("DA_Head_") else ["--item", f"{CHARS}/{mesh}"]
        r = subprocess.run([BLENDER, "--factory-startup", "-b", "--python", os.path.join(HERE, "bake_ours.py"), "--",
                            EXPORTS] + mode + [skin, bake, res],
                           capture_output=True, text=True, errors="replace")
        baked = re.findall(r"^BAKED (\S+) (\d+) (\S+) (\S*)", r.stdout, re.M)
        if not baked:
            print("   bake failed:", (r.stdout + r.stderr)[-600:])
            continue
        missing = sorted({mi for _o, _i, mi, _k in baked if not os.path.isdir(os.path.join(sm, mi))})
        pkgs = [p for p in (mi_package(mi) for mi in missing) if p]
        if pkgs:
            e = subprocess.run(["dotnet", "run", "--no-build", "--project", HERE, "--", sm] + pkgs,
                               capture_output=True, text=True, errors="replace", cwd=HERE)
            for l in e.stdout.splitlines():
                if "base pass" not in l:
                    print("   extract:", l)
        c = subprocess.run([sys.executable, "-I", os.path.join(HERE, "compare_game.py"), EXPORTS, sm, bake,
                            os.path.join(work, f"cmp_{name}.png")], capture_output=True, text=True, errors="replace")
        kinds = {f"{o}__{i}": k for o, i, _mi, k in baked}
        for l in c.stdout.splitlines():
            m = re.match(r"(\S+)\.npz \((\S+)\): mean abs diff\s+base ([\d.]+)\s+rough ([\d.]+)\s+metal ([\d.]+)", l)
            if m:
                print(f"   {m.group(2)[:60]:60s} {kinds.get(m.group(1), ''):14s} base {m.group(3)}  rough {m.group(4)}"
                      f"  metal {m.group(5)}")
            elif "no extracted" in l or "rror" in l or "skipped" in l:
                print("  ", l[:200])
        if c.returncode:
            print("   compare failed:", c.stderr[-800:])


if __name__ == "__main__":
    main()
