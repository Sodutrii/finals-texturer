"""Evaluate a material's real (compiled) base pass for given texels.

    from weaponsim import Evaluator
    ev = Evaluator(exports_root, extracted_dir_of_the_material)      # ShaderExtract output for that MI
    out = ev.run(mi_json, uv0, uv1)       # uv0 / uv1: (n, 2) in UE convention (v = 0 at the image top)
    out['base_color'] (n, 3) linear, out['roughness'], out['metallic'], out['specular'], out['clearcoat']

The material instance's parameter values come from the add-on's own chain resolver (ue_assets), the
uniform buffer from the material's preshaders, textures from the FModel export (curve atlases baked
with curves.py), all fed to shadersim. G-buffer outputs (UE5 deferred, SM5): o1 = normal, o2 = metallic /
specular / roughness, o3 = base colour, o4 = clear coat / clear-coat roughness.
"""
import base64
import importlib.util
import json
import math
import os
import struct
import sys
import types

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from shadersim import Shader, Texture, material_cbuffer  # noqa: E402

ADDON = os.path.normpath(os.path.join(HERE, "..", "..", "finals_texturer"))
sys.modules.setdefault("bpy", types.ModuleType("bpy"))
if "finals_texturer" not in sys.modules:
    spec = importlib.util.spec_from_file_location("finals_texturer", os.path.join(ADDON, "__init__.py"),
                                                  submodule_search_locations=[ADDON])
    sys.modules["finals_texturer"] = importlib.util.module_from_spec(spec)
ue = importlib.import_module("finals_texturer.ue_assets")
curves = importlib.import_module("finals_texturer.curves")


def srgb_to_linear(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


class Evaluator:
    def __init__(self, exports_root, sm_dir):
        self.root = exports_root
        self.dir = sm_dir
        self.layout = json.load(open(os.path.join(sm_dir, "material_layout.json"), encoding="utf-8"))
        asm = open(os.path.join(sm_dir, "base_pass.asm"), encoding="utf-8", errors="replace").read()
        self.shader = Shader(asm)
        self.mat_cb = material_cbuffer(asm)
        log = ue.Log()
        log.info = log.warn = lambda *a: None
        self.log = log
        self.finder = ue.AssetFinder(exports_root, log=log)
        self._tex_cache = {}

    # ── parameters → uniform buffer ──────────────────────────────────────────
    def _param_values(self, mp):
        vals = []
        for p in self.layout["UniformNumericParameters"]:
            name, typ, v = p["ParameterInfo"]["Name"], p["ParameterType"], p["Value"]
            if typ == "Vector":
                d = (v["R"], v["G"], v["B"], v["A"]) if isinstance(v, dict) else (0, 0, 0, 0)
                vals.append(list(mp.vectors.get(name, d)))
            else:
                d = float(v) if isinstance(v, (int, float)) else 0.0
                if name in mp.scalars:
                    d = float(mp.scalars[name])
                elif name in mp.switches:
                    d = 1.0 if mp.switches[name] else 0.0
                vals.append([d])
        return vals

    def uniform_buffer(self, mp):
        params = self._param_values(mp)
        data = base64.b64decode(self.layout["UniformPreshaderData"]["Data"])
        buf = np.zeros(self.layout["UniformPreshaderBufferSize"] * 4, np.float32)
        for h in self.layout["UniformPreshaders"]:
            code = data[h["OpcodeOffset"]:h["OpcodeOffset"] + h["OpcodeSize"]]
            res = run_preshader(code, params, lambda idx: self._texture_size(mp, idx))
            for f in self.layout["UniformPreshaderFields"][h["FieldIndex"]:h["FieldIndex"] + h["NumFields"]]:
                n = {"Float1": 1, "Float2": 2, "Float3": 3, "Float4": 4}.get(f["Type"], 1)
                for k in range(n):
                    buf[f["BufferOffset"] + k] = res[k] if k < len(res) else 0.0
        return buf.reshape(-1, 4)

    def _texture_size(self, mp, tex_index):
        """Size of the texture behind a referenced-texture index (the MI's override when a parameter uses it)."""
        refs = self._referenced(mp)
        path = refs[tex_index] if 0 <= tex_index < len(refs) else ""
        for group in self.layout["UniformTextureParameters"]:
            for t in group:
                name = (t.get("ParameterInfo") or {}).get("Name") or t.get("ParameterName")
                if t["TextureIndex"] == tex_index and name and mp.textures.get(name):
                    path = mp.textures[name]
        tex = self._load(path)
        return (float(tex.img.shape[-2]), float(tex.img.shape[-3])) if tex is not None else (1.0, 1.0)

    # ── textures ─────────────────────────────────────────────────────────────
    def _referenced(self, mp):
        path = self.finder.find("", ".json", stem=mp.master) if mp.master else ""
        data = self.finder.load_json(path) if path else None
        for e in data if isinstance(data, list) else []:
            ced = e.get("CachedExpressionData") or (e.get("Properties") or {}).get("CachedExpressionData")
            if ced and ced.get("ReferencedTextures"):
                return [ue.ref_path(t) if t else "" for t in ced["ReferencedTextures"]]
        return []

    def _load(self, object_path):
        if not object_path:
            return None
        if object_path in self._tex_cache:
            return self._tex_cache[object_path]
        tex = None
        png = self.finder.find_texture(object_path)
        if png:
            img = np.asarray(Image.open(png).convert("RGBA"), np.float32) / 255.0
            meta = self.finder.load_asset_json(object_path)
            props = ((meta or [{}])[0] or {}).get("Properties") or {} if isinstance(meta, list) else {}
            if props.get("SRGB", True):
                img[..., :3] = srgb_to_linear(img[..., :3])
            # gradient lookups are clamped (as our builder's EXTEND); everything else wraps
            tex = Texture(img, clamp=object_path.split(".")[0].upper().endswith("_GRAD"))
        else:
            exp = ue.export_of_type(self.finder.load_asset_json(object_path), ("CurveLinearColorAtlas",))
            if exp:
                w, h, flat = curves.atlas_pixels(self.finder, exp)
                img = np.asarray(flat, np.float32).reshape(h, w, 4)[::-1]     # bake is bottom row first
                tex = Texture(img, clamp=True)        # curve atlases: clamped (zebra stripes stay black)
            else:
                n = self.finder.array_slice_count(object_path)
                if n:
                    layers = [np.asarray(Image.open(self.finder.find_array_slice(object_path, i)).convert("RGBA"),
                                         np.float32) / 255.0 for i in range(n)]
                    tex = Texture(np.stack(layers))
        self._tex_cache[object_path] = tex
        return tex

    def textures(self, mp):
        refs = self._referenced(mp)
        entries = []
        for group in self.layout["UniformTextureParameters"]:
            for t in group:
                name = (t.get("ParameterInfo") or {}).get("Name") or t.get("ParameterName")
                default = refs[t["TextureIndex"]] if 0 <= t["TextureIndex"] < len(refs) else ""
                entries.append((name, (mp.textures.get(name) if name else None) or default))
        names = [n for n, _ in entries]
        # the compiler drops unused textures: the graphic colour atlas without a greyscale graphic
        if "Gradient" in names and "Grayscale" not in names:
            entries = [e for e in entries if e[0] != "Gradient"]
        return {i: self._load(p) for i, (_n, p) in enumerate(entries)}, [n for n, _ in entries]

    # ── run ──────────────────────────────────────────────────────────────────
    def run(self, mi_json, uv0, uv1, uv2=None, normal=None):
        mp = ue.resolve_material(self.finder, json_path=mi_json, log=self.log)
        n = len(uv0)
        cb2 = self.uniform_buffer(mp)
        cb0 = np.zeros((512, 4), np.float32)
        cb0[47] = (0, 0, 0, 1)
        cb0[160] = (0, 1, 0, 1)            # roughness / AO overrides: identity
        cb0[156] = (0, 0, 1, 0)
        cb0[319] = (0, 0, 1e9, 0)
        tex, names = self.textures(mp)
        g = tex.get(names.index("Gradient")) if "Gradient" in names else None
        row = cb2[0][0] / g.img.shape[-3] if g is not None else 0.5
        inputs = {
            "v0": [np.ones(n), np.zeros(n), np.zeros(n)],                 # tangent
            "v1": [np.zeros(n), np.zeros(n), np.ones(n), np.ones(n)],     # normal, tangent sign
            # the vertex shader passes UV1 as (v, u); v3 = UV2 (u, v), -, gradient row (custom UV:
            # (Gradient Y Sample Index + 0.5) / height of the "Gradient" texture)
            "v2": [uv0[:, 0], uv0[:, 1], uv1[:, 1], uv1[:, 0]],
            "v3": [(uv2 if uv2 is not None else uv1)[:, 0], (uv2 if uv2 is not None else uv1)[:, 1], np.zeros(n),
                   np.full(n, row)],
            "v4": [np.zeros(n)] * 3,
            "v5": [np.arange(n) % 1024 + 0.5, np.arange(n) // 1024 + 0.5, np.full(n, 0.5)],
        }
        cbs = {k: np.zeros((64, 4), np.float32) for k in range(1, self.mat_cb)}
        cbs[0] = cb0
        cbs[self.mat_cb] = cb2
        out = self.shader.run(n, inputs, cbs, tex)
        o2, o3, o4 = out.get("o2"), out.get("o3"), out.get("o4")
        if o3 is None:
            raise RuntimeError("no G-buffer output (translucent / forward-shaded material): not comparable yet")
        z = np.zeros(n, np.float32)
        return {"base_color": np.stack(o3[:3], 1), "metallic": o2[0], "specular": o2[1], "roughness": o2[2],
                "clearcoat": o4[0] if o4 else z, "clearcoat_roughness": o4[1] if o4 else z,
                "outputs": out, "textures": names, "params": mp}


UNARY = {
    0x0d: math.sin, 0x0e: math.cos, 0x0f: math.tan,
    0x10: lambda x: math.asin(min(max(x, -1.0), 1.0)), 0x11: lambda x: math.acos(min(max(x, -1.0), 1.0)),
    0x12: math.atan,
    0x16: lambda x: math.sqrt(max(x, 0.0)),
    0x17: lambda x: 1.0 / x if x else float("inf"),          # Rcp
    0x1a: lambda x: min(max(x, 0.0), 1.0),                   # Saturate
    0x1b: abs,
    0x1c: math.floor,
    0x1d: math.ceil,
    0x1e: lambda x: math.floor(x + 0.5),                     # Round
    0x1f: math.trunc,
    0x21: lambda x: x - math.floor(x),
}


def run_preshader(code, params, texture_size=None):
    """Numeric preshader (classic format, opcodes as decoded in annotate.py)."""
    stack, i = [], 0
    while i < len(code):
        op = code[i]; i += 1
        if op == 0x02:
            t = code[i]; i += 1
            k = {1: 1, 2: 2, 3: 3, 4: 4}.get(t, 1)
            stack.append(list(struct.unpack_from(f"<{k}f", code, i))); i += 4 * k
        elif op == 0x03:
            stack.append(list(params[code[i] | code[i + 1] << 8])); i += 2
        elif op in (0x04, 0x05, 0x06, 0x07):
            b, a = stack.pop(), stack.pop()
            m = max(len(a), len(b))
            a = a * m if len(a) == 1 else a
            b = b * m if len(b) == 1 else b
            f = {4: lambda x, y: x + y, 5: lambda x, y: x - y, 6: lambda x, y: x * y,
                 7: lambda x, y: x / y if y else 0.0}[op]
            stack.append([f(x, y) for x, y in zip(a, b)])
        elif op == 0x0c:      # clamp(x, lo, hi)
            hi, lo, x = stack.pop(), stack.pop(), stack.pop()
            m = max(len(x), len(lo), len(hi))
            ext = lambda v: v * m if len(v) == 1 else v
            stack.append([min(max(a, b), c) for a, b, c in zip(ext(x), ext(lo), ext(hi))])
        elif op == 0x25:
            cnt = code[i]; comps = code[i + 1:i + 5]; i += 5
            v = stack.pop()
            stack.append([v[c] if c < len(v) else 0.0 for c in comps[:cnt]])
        elif op == 0x26:
            b, a = stack.pop(), stack.pop()
            stack.append(a + b)
        elif op == 0x14:      # Dot
            b, a = stack.pop(), stack.pop()
            stack.append([sum(x * y for x, y in zip(a, b))])
        elif op == 0x18:      # Length
            v = stack.pop()
            stack.append([math.sqrt(sum(x * x for x in v))])
        elif op in (0x27, 0x28):  # TextureSize / TexelSize: parameter info (name u16, index i32, association u8),
            tex_index = struct.unpack_from("<i", code, i + 7)[0]     # then the referenced-texture index (i32)
            i += 11
            w_, h_ = texture_size(tex_index) if texture_size else (1.0, 1.0)
            stack.append([w_, h_, 1.0] if op == 0x27 else [1.0 / w_, 1.0 / h_, 1.0])
        elif op in UNARY:     # UE EPreshaderOpcode order: 0x16 Sqrt .. 0x21 Frac
            stack.append([UNARY[op](x) for x in stack.pop()])
        elif op in (0x08, 0x09):      # Fmod, Modulo
            b, a = stack.pop(), stack.pop()
            m = max(len(a), len(b))
            a = a * m if len(a) == 1 else a
            b = b * m if len(b) == 1 else b
            stack.append([math.fmod(x, y) if y else 0.0 for x, y in zip(a, b)] if op == 0x08
                         else [x % y if y else 0.0 for x, y in zip(a, b)])
        elif op in (0x0a, 0x0b):
            b, a = stack.pop(), stack.pop()
            m = max(len(a), len(b))
            a = a * m if len(a) == 1 else a
            b = b * m if len(b) == 1 else b
            stack.append([(min if op == 0x0a else max)(x, y) for x, y in zip(a, b)])
        else:
            raise NotImplementedError(f"preshader opcode {op:#x}")
    return stack[-1] if stack else [0.0]
