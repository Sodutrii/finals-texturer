"""Annotate a disassembled material pixel shader with parameter and texture names.

    python -I annotate.py <out dir of ShaderExtract> <shader .asm> <master material JSON (FModel)> [cbuffer]

Reads material_layout.json (written by ShaderExtract):
* preshaders: small programs that compute each value of the material uniform buffer from
  parameters (UniformNumericParameters); decoded to expressions per buffer component;
* texture parameters: their order in the uniform buffer = the shader's t-register order
  (unused ones are skipped; pass --skip-textures to list which).
Writes <shader>.annotated.asm and prints the buffer map.

Preshader opcodes seen in this game's data (classic format; others print as opNN):
  02 Constant (type byte + floats)  03 Parameter (u16)  04 Add  05 Sub  06 Mul  07 Div
  0c Clamp(x, lo, hi)  25 Swizzle (count + 4 component bytes)  26 AppendVector
  0a Min 0b Max 16 Sqrt 17 Rcp 1a Saturate 1b Abs 1c Floor 1d Ceil 1e Round 1f Trunc 21 Frac
  (UE's EPreshaderOpcode order; 0c / 17 / 1a / 1e / 25 / 26 confirmed on this game's data)
"""
import base64
import json
import re
import struct
import sys
from pathlib import Path

UNARY_NAMES = {0x16: "sqrt", 0x17: "rcp", 0x1a: "sat", 0x1b: "abs", 0x1c: "floor", 0x1d: "ceil",
               0x1e: "round", 0x1f: "trunc", 0x21: "frac"}
VALUE_FLOATS = {1: 1, 2: 2, 3: 3, 4: 4}      # EValueType Float1..Float4


def decode_preshader(code, params):
    """Run a preshader program symbolically: returns a list of component expressions."""
    stack, i = [], 0
    while i < len(code):
        op = code[i]; i += 1
        if op == 0x02:
            t = code[i]; i += 1
            n = VALUE_FLOATS.get(t, 1)
            vals = struct.unpack_from(f"<{n}f", code, i); i += 4 * n
            stack.append([f"{v:g}" for v in vals])
        elif op == 0x03:
            idx = code[i] | code[i + 1] << 8; i += 2
            p = params[idx]
            if p["type"] == "Vector":
                stack.append([f"{p['name']}.{c}" for c in "rgba"])
            else:
                stack.append([p["name"]])
        elif op in (0x04, 0x05, 0x06, 0x07):
            b, a = stack.pop(), stack.pop()
            sym = {4: "+", 5: "-", 6: "*", 7: "/"}[op]
            n = max(len(a), len(b))
            a = a * n if len(a) == 1 else a
            b = b * n if len(b) == 1 else b
            stack.append([f"({x} {sym} {y})" for x, y in zip(a, b)])
        elif op == 0x0c:
            hi, lo, x = stack.pop(), stack.pop(), stack.pop()
            m = max(len(x), len(lo), len(hi))
            ext = lambda v: v * m if len(v) == 1 else v
            stack.append([f"clamp({a}, {b}, {c})" for a, b, c in zip(ext(x), ext(lo), ext(hi))])
        elif op == 0x25:
            n = code[i]; comps = code[i + 1:i + 5]; i += 5
            v = stack.pop()
            stack.append([v[c] if c < len(v) else "0" for c in comps[:n]])
        elif op == 0x26:
            b, a = stack.pop(), stack.pop()
            stack.append(a + b)
        else:
            v = stack.pop()
            fn = UNARY_NAMES.get(op, f"op{op:02x}")
            stack.append([f"{fn}({x})" for x in v])
    return stack[-1] if stack else []


def buffer_map(layout):
    params = [{"name": p["ParameterInfo"]["Name"], "type": p["ParameterType"]} for p in layout["UniformNumericParameters"]]
    data = base64.b64decode(layout["UniformPreshaderData"]["Data"])
    comps = {}
    for h in layout["UniformPreshaders"]:
        code = data[h["OpcodeOffset"]:h["OpcodeOffset"] + h["OpcodeSize"]]
        expr = decode_preshader(code, params)
        for f in layout["UniformPreshaderFields"][h["FieldIndex"]:h["FieldIndex"] + h["NumFields"]]:
            n = {"Float1": 1, "Float2": 2, "Float3": 3, "Float4": 4}.get(f["Type"], 1)
            for k in range(n):
                comps[f["BufferOffset"] + k] = expr[k] if k < len(expr) else "?"
    return comps


def texture_names(layout, master_json, skip):
    master = json.load(open(master_json, encoding="utf-8"))[0]
    ced = master.get("CachedExpressionData") or master["Properties"].get("CachedExpressionData") or {}
    refs = [((r or {}).get("ObjectName") or "None").split("'")[-2] if r else "None" for r in ced.get("ReferencedTextures") or []]
    out = []
    for group in layout["UniformTextureParameters"]:
        for t in group:
            name = (t.get("ParameterInfo") or {}).get("Name") or t.get("ParameterName")
            ref = refs[t["TextureIndex"]] if 0 <= t["TextureIndex"] < len(refs) else "?"
            out.append(f"{name} ({ref})" if name else f"[{ref}]")
    return [n for i, n in enumerate(out) if i not in skip]


def main():
    out_dir, asm_path, master_json = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    cb = sys.argv[4] if len(sys.argv) > 4 and not sys.argv[4].startswith("--") else "cb2"
    skip = {int(x) for a in sys.argv if a.startswith("--skip-textures=") for x in a.split("=")[1].split(",") if x}
    layout = json.load(open(out_dir / "material_layout.json", encoding="utf-8"))
    comps = buffer_map(layout)
    texs = texture_names(layout, master_json, skip)
    asm = asm_path.read_text(encoding="utf-8", errors="replace")

    def sub_cb(m):
        reg, sw = int(m.group(1)), m.group(2)
        names = [comps.get(reg * 4 + "xyzw".index(c), f"{cb}[{reg}].{c}") for c in sw]
        return f"{m.group(0)}{{{', '.join(names)}}}"
    asm = re.sub(rf"-?\|?{cb}\[(\d+)\]\.([xyzw]+)\|?", sub_cb, asm)
    regs = [int(r) for r in re.findall(r"dcl_resource_texture\w*\s*\([^)]*\)\s*t(\d+)", asm)]
    off = min(regs) if regs else 0           # structured buffers (GPU-scene data) come first
    name = lambda i: texs[i - off] if 0 <= i - off < len(texs) else "?"
    asm = re.sub(r"\bt(\d+)\.", lambda m: f"t{m.group(1)}<{name(int(m.group(1)))}>.", asm)
    head = ["// material uniform buffer (" + cb + "):"]
    for reg in range(max(comps) // 4 + 1):
        head.append(f"//   {cb}[{reg}] = " + ", ".join(comps.get(reg * 4 + k, "-") for k in range(4)))
    head.append("// textures: " + ", ".join(f"t{i}={n}" for i, n in enumerate(texs)))
    dst = asm_path.with_suffix(".annotated.asm")
    dst.write_text("\n".join(head) + "\n" + asm, encoding="utf-8")
    print("\n".join(head))
    print("wrote", dst)


if __name__ == "__main__":
    main()
