"""Compare our baked material against the game's compiled formula, texel by texel.

python -I compare_game.py <exports root> <extract root (ShaderExtract out)> <bake dir (bake_ours.py out)> <out png>
For each baked part: finds the MI's JSON and its extracted shader (<extract root>/<MI name>/), runs the
game's base pass on the same texels, prints mean differences and writes a sheet:
rows = parts, columns = base colour ours | game, roughness ours | game, metallic ours | game.
"""
import glob
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from weaponsim import Evaluator  # noqa: E402


def to8(img):
    img = np.clip(np.nan_to_num(img), 0, 1)
    if img.ndim == 2:
        img = np.repeat(img[..., None], 3, -1)
    img = np.where(img <= 0.0031308, img * 12.92, 1.055 * np.power(img, 1 / 2.4) - 0.055)
    return Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8))


def main():
    exports, extract, bakes, out_png = sys.argv[1:5]
    rows = []
    for f in sorted(glob.glob(os.path.join(bakes, "*.npz"))):
        d = np.load(f, allow_pickle=True)
        mi = str(d["mi"])
        sm_dir = os.path.join(extract, mi)
        if not os.path.isdir(sm_dir):
            print(f"{os.path.basename(f)}: no extracted shader for {mi} (run ShaderExtract on it)")
            continue
        ev = Evaluator(exports, sm_dir)
        mi_json = ev.finder.find("", ".json", stem=mi)
        m = d["mask"]
        res = ev.run(mi_json, d["uv0"][m], d["uv1"][m])
        shape = m.shape

        def img(vals, ch=None):
            a = np.zeros(shape + ((3,) if ch else ()), np.float32)
            a[m] = vals
            return a
        game = {"base": img(res["base_color"], 3), "rough": img(res["roughness"]), "metal": img(res["metallic"])}
        ours = {"base": d["base"], "rough": d["rough"], "metal": d["metal"]}
        diff = {k: float(np.abs(np.nan_to_num(ours[k][m]) - np.nan_to_num(game[k][m])).mean()) for k in ours}
        print(f"{os.path.basename(f)} ({mi}): mean abs diff  base {diff['base']:.3f}  rough {diff['rough']:.3f}"
              f"  metal {diff['metal']:.3f}   game textures {res['textures']}")
        rows.append((os.path.basename(f)[:-4], [ours["base"], game["base"], ours["rough"], game["rough"],
                                               ours["metal"], game["metal"]]))
    if not rows:
        return
    s = rows[0][1][0].shape[0]
    sheet = Image.new("RGB", (6 * s, len(rows) * (s + 14)), (255, 255, 255))
    dr = ImageDraw.Draw(sheet)
    for r, (name, imgs) in enumerate(rows):
        for c, im in enumerate(imgs):
            sheet.paste(to8(im), (c * s, r * (s + 14) + 14))
        dr.text((2, r * (s + 14) + 1), f"{name}: base ours|game  rough ours|game  metal ours|game", fill=(0, 0, 0))
    sheet.save(out_png)
    print("wrote", out_png)


if __name__ == "__main__":
    main()
