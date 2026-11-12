"""Run a disassembled SM5 pixel shader (fxc /dumpbin output) on many pixels at once with numpy.

Only what the game's material base passes use: arithmetic, comparisons, bit ops, if/else (as masks),
texture2d / texture2darray sampling (bilinear, wrap, mip 0). Registers hold float32 bit patterns, so
comparisons (0xFFFFFFFF masks), `and` / `or` and integer literals behave like on the GPU.

    sim = Shader(asm_text)
    out = sim.run(n, inputs={'v2': [u0, v0, u1, v1], ...}, cbuffers={2: array (regs, 4)},
                  textures={0: Texture(...), ...})
    out['o3'][0]  -> red channel of output 3, shape (n,)
"""
import re

import numpy as np

F32 = np.float32
U32 = np.uint32


def as_u(x):
    return np.ascontiguousarray(x, dtype=F32).view(U32)


def as_f(x):
    return np.ascontiguousarray(x, dtype=U32).view(F32)


def material_texture_registers(asm):
    """Texture registers holding the material's textures, in order: the declared texture registers after
    the last structured buffer (GPU-scene vertex factories declare per-object buffers, and some passes a
    view texture before them)."""
    regs = sorted(int(m) for m in re.findall(r"dcl_resource_texture\w*\s*\([^)]*\)\s*t(\d+)", asm))
    st = [int(m) for m in re.findall(r"dcl_resource_structured\s+t(\d+)", asm)]
    return [r for r in regs if not st or r > max(st)]


def material_cbuffer(asm):
    """Index of the material's uniform buffer: the last declared constant buffer (cb0 = view, then
    primitive / scene buffers; cb2 on the weapon base pass, cb3 on clothing)."""
    idx = [int(m) for m in re.findall(r"dcl_constantbuffer\s+[Cc][Bb](\d+)\[", asm)]
    return max(idx) if idx else 2


class Texture:
    """RGBA float image, row 0 = top (UE texture space: v = 0 at the top)."""

    def __init__(self, rgba, clamp=False):
        self.img = np.asarray(rgba, dtype=F32)          # (h, w, 4) or (layers, h, w, 4)
        self.is_array = self.img.ndim == 4
        self.clamp = clamp                              # address mode: clamp instead of wrap

    def sample(self, u, v, layer=None):
        img = self.img
        if self.is_array:
            li = np.clip(np.rint(layer).astype(int), 0, img.shape[0] - 1)
        h, w = img.shape[-3], img.shape[-2]
        x = u * w - 0.5
        y = v * h - 0.5
        x0 = np.floor(x); y0 = np.floor(y)
        fx = (x - x0)[:, None]; fy = (y - y0)[:, None]
        x0 = x0.astype(np.int64); y0 = y0.astype(np.int64)
        if self.clamp:
            x1 = np.clip(x0 + 1, 0, w - 1); y1 = np.clip(y0 + 1, 0, h - 1)
            x0 = np.clip(x0, 0, w - 1); y0 = np.clip(y0, 0, h - 1)
        else:
            x0 %= w; y0 %= h
            x1 = (x0 + 1) % w; y1 = (y0 + 1) % h

        def at(yy, xx):
            return img[li, yy, xx] if self.is_array else img[yy, xx]
        top = at(y0, x0) * (1 - fx) + at(y0, x1) * fx
        bot = at(y1, x0) * (1 - fx) + at(y1, x1) * fx
        return (top * (1 - fy) + bot * fy).astype(F32)   # (n, 4)


SWZ = {"x": 0, "y": 1, "z": 2, "w": 3, "r": 0, "g": 1, "b": 2, "a": 3}
OPERAND = re.compile(r"(-)?(\|)?(l\([^)]*\)|[a-zA-Z]+\d*(?:\[\d+\])?)(?:\.([xyzw]+))?\|?")


class Shader:
    def __init__(self, asm):
        body, started = [], False
        for line in asm.splitlines():
            s = line.strip()
            if not s or s.startswith("//"):
                continue
            if s.startswith("dcl_") or s.startswith("ps_"):
                started = True
                continue
            if started:
                s = re.sub(r"\s*\[precise[^\]]*\]", "", s)
                body.append(s)
        self.lines = body
        # the k-th declared texture register = the k-th used material texture (structured buffers of
        # GPU-scene vertex factories sit in between, e.g. t0 or t1 / t2)
        regs = material_texture_registers(asm)
        self.tex_index = {r: k for k, r in enumerate(regs)}

    # ── operands ─────────────────────────────────────────────────────────────
    INT_OPS = {"iadd", "imad", "imul", "umul", "umad", "ineg", "ishl", "ishr", "ushr", "imin", "imax", "ige",
               "ilt", "ieq", "ine", "uge", "ult", "umin", "umax", "and", "or", "xor", "not", "itof", "utof", "udiv"}

    def _src(self, tok, n, integer=False):
        m = OPERAND.fullmatch(tok.strip())
        if not m:
            raise ValueError(f"operand {tok!r}")
        neg, absv, reg, swz = m.group(1), m.group(2), m.group(3), m.group(4) or "xyzw"
        if reg.startswith("l("):
            vals = [v.strip() for v in reg[2:-1].split(",")]
            comps = []
            for v in vals:
                if v.startswith("0x"):
                    comps.append(np.full(n, int(v, 16), U32).view(F32))
                elif re.fullmatch(r"-?\d+", v):
                    comps.append(np.full(n, int(v) & 0xFFFFFFFF, U32).view(F32))
                else:
                    comps.append(np.full(n, float(v), F32))
            while len(comps) < 4:
                comps.append(comps[-1])
            src = comps
        else:
            src = self._reg(reg, n)
        out = [src[SWZ[c]] for c in swz]
        while len(out) < 4:
            out.append(out[-1])
        if integer:                              # source modifiers act on integers for integer instructions
            if absv:
                out = [as_f(np.abs(as_u(c).view(np.int32)).view(U32)) for c in out]
            if neg:
                out = [as_f((-as_u(c).view(np.int32).astype(np.int64) & 0xFFFFFFFF).astype(U32)) for c in out]
            return out
        if absv:
            out = [np.abs(c) for c in out]
        if neg:
            out = [-c for c in out]
        return out

    def _reg(self, reg, n):
        m = re.fullmatch(r"([a-z]+)(\d+)(?:\[(\d+)\])?", reg)
        kind, idx, sub = m.group(1), int(m.group(2)), m.group(3)
        if kind == "cb":
            buf = self.cb.get(idx)
            row = buf[int(sub)] if buf is not None and int(sub) < len(buf) else np.zeros(4, F32)
            return [np.full(n, row[k], F32) for k in range(4)]
        key = f"{kind}{idx}"
        if key not in self.regs:
            self.regs[key] = [np.zeros(n, F32) for _ in range(4)]
        return self.regs[key]

    def _dst(self, tok):
        tok = tok.strip()
        m = re.fullmatch(r"([a-z]+\d+)(?:\.([xyzw]+))?", tok)
        return m.group(1), m.group(2) or "xyzw"

    def _write(self, dst, vals, sat=False):
        reg, mask = dst
        cur = self.regs.setdefault(reg, [np.zeros(self.n, F32) for _ in range(4)])
        for k, c in enumerate(mask):
            i = SWZ[c]
            v = vals[i]
            if sat:
                v = np.clip(np.nan_to_num(v, nan=0.0), 0.0, 1.0)
            cur[i] = np.where(self.mask, v, cur[i]).astype(F32)

    # ── run ──────────────────────────────────────────────────────────────────
    def run(self, n, inputs, cbuffers, textures):
        self.n = n
        self.regs = {k: [np.asarray(c, F32) * np.ones(n, F32) for c in v] + [np.zeros(n, F32)] * (4 - len(v))
                     for k, v in inputs.items()}
        self.cb = {k: np.asarray(v, F32) for k, v in cbuffers.items()}
        self.tex = textures
        self.mask = np.ones(n, bool)
        stack = []
        with np.errstate(all="ignore"):
            for line in self.lines:
                op, _, rest = line.partition(" ")
                args = self._split(rest)
                sat = op.endswith("_sat")
                op = op[:-4] if sat else op
                if op == "ret":
                    break
                if op in ("if_nz", "if_z"):
                    c = as_u(self._src(args[0], n)[0]) != 0
                    if op == "if_z":
                        c = ~c
                    stack.append((self.mask, c))
                    self.mask = self.mask & c
                    continue
                if op == "else":
                    outer, c = stack[-1]
                    self.mask = outer & ~c
                    continue
                if op == "endif":
                    self.mask = stack.pop()[0]
                    continue
                if op in ("discard_nz", "discard_z"):
                    continue
                try:
                    self._exec(op, args, sat, n)
                except Exception as e:
                    raise RuntimeError(f"{line!r}: {e}") from e
        return {k: v for k, v in self.regs.items() if k.startswith("o")}

    @staticmethod
    def _split(rest):
        out, depth, cur = [], 0, ""
        for ch in rest:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == "," and depth == 0:
                out.append(cur.strip()); cur = ""
            else:
                cur += ch
        if cur.strip():
            out.append(cur.strip())
        return out

    def _exec(self, op, a, sat, n):
        if op.startswith("sample"):
            return self._sample(a, sat, n)
        if op.startswith("resinfo"):
            # resinfo dst, mip, tN: width, height, depth / layers, mip count (as floats; _uint variants as ints)
            full = op + " " + ", ".join(a)
            m = re.search(r"\)\s*([a-z]+\d+(?:\.[xyzw]+)?)\s*,\s*[^,]+,\s*t(\d+)", full)
            d, ti = self._dst(m.group(1)), int(m.group(2))
            tex = self.tex.get(self.tex_index.get(ti, -1))
            if tex is not None:
                h, w = tex.img.shape[-3], tex.img.shape[-2]
                layers = tex.img.shape[0] if tex.is_array else 1
            else:
                h = w = 1; layers = 1
            vals = [float(w), float(h), float(layers), 1.0]
            if "_uint" in full:
                return self._write(d, [as_f(np.full(n, int(v), U32)) for v in vals])
            return self._write(d, [np.full(n, v, F32) for v in vals])
        if op.startswith("ld_indexable") or op.startswith("ld("):
            # texel fetch: integer coordinates
            full = op + " " + ", ".join(a)
            m = re.search(r"\)\s*([a-z]+\d+(?:\.[xyzw]+)?)\s*,\s*([^,]+),\s*t(\d+)\.([xyzw]+)", full)
            d, coord, ti, swz = self._dst(m.group(1)), self._src(m.group(2), n), int(m.group(3)), m.group(4)
            tex = self.tex.get(self.tex_index.get(ti, -1))
            if tex is None:
                res = np.full((n, 4), 0.5, F32)
            else:
                h, w = tex.img.shape[-3], tex.img.shape[-2]
                x = as_u(coord[0]).view(np.int32) % w
                y = as_u(coord[1]).view(np.int32) % h
                res = tex.img[np.clip(as_u(coord[2]).view(np.int32), 0, tex.img.shape[0] - 1), y, x]                     if tex.is_array else tex.img[y, x]
            return self._write(d, [res[:, SWZ[c]] for c in swz])
        if op.startswith("ld_structured"):
            # per-object GPU-scene data: not modelled, reads as zeros
            d = self._dst(re.search(r"\)\s*([a-z]+\d+(?:\.[xyzw]+)?)\s*,", op + " " + ", ".join(a)).group(1))
            return self._write(d, [np.zeros(n, F32)] * 4)
        if op == "sincos":                      # sincos dst_sin, dst_cos, src (either may be null)
            src = self._src(a[2], n)
            for dtok, f in ((a[0], np.sin), (a[1], np.cos)):
                if dtok.strip() != "null":
                    self._write(self._dst(dtok), [f(c) for c in src], sat)
            return
        if op in ("imul", "umul", "udiv"):      # two destinations (high / low, quotient / remainder)
            if op == "udiv":
                d1 = None if a[0].strip() == "null" else self._dst(a[0])
                d2 = None if a[1].strip() == "null" else self._dst(a[1])
                s = [self._src(x, n, True) for x in a[2:]]
                q = [as_f((as_u(x) // np.maximum(as_u(y), 1)).astype(U32)) for x, y in zip(s[0], s[1])]
                r = [as_f((as_u(x) % np.maximum(as_u(y), 1)).astype(U32)) for x, y in zip(s[0], s[1])]
                if d1:
                    self._write(d1, q)
                if d2:
                    self._write(d2, r)
                return
            hi_null = a[0].strip() == "null"
            if hi_null:
                a = a[1:]
            if not hi_null:
                a = a[1:]                       # high half not modelled
            d = self._dst(a[0])
            s = [self._src(x, n, True) for x in a[1:]]
            sgn = np.int32 if op == "imul" else U32
            r = [as_f((as_u(x).view(sgn).astype(np.int64) * as_u(y).view(sgn).astype(np.int64)).astype(np.int64)
                      .astype(np.uint64).astype(U32)) for x, y in zip(s[0], s[1])]
            return self._write(d, r)
        d = self._dst(a[0])
        s = [self._src(x, n, op in self.INT_OPS) for x in a[1:]]
        i32 = lambda x: as_u(x).view(np.int32).astype(np.int64)
        fi = lambda v: as_f((np.asarray(v, np.int64) & 0xFFFFFFFF).astype(U32))
        ew = lambda f: [f(*[x[k] for x in s]) for k in range(4)]
        t = np.full(n, 0xFFFFFFFF, U32).view(F32)
        z = np.zeros(n, F32)
        cmp = lambda f: ew(lambda *x: np.where(f(*x), t, z))
        if op == "mov":
            r = s[0]
        elif op == "add":
            r = ew(lambda x, y: x + y)
        elif op == "mul":
            r = ew(lambda x, y: x * y)
        elif op == "mad":
            r = ew(lambda x, y, w: x * y + w)
        elif op == "div":
            r = ew(lambda x, y: x / y)
        elif op == "max":
            r = ew(np.fmax)
        elif op == "min":
            r = ew(np.fmin)
        elif op == "lt":
            r = cmp(lambda x, y: x < y)
        elif op == "ge":
            r = cmp(lambda x, y: x >= y)
        elif op == "eq":
            r = cmp(lambda x, y: x == y)
        elif op == "ne":
            r = cmp(lambda x, y: x != y)
        elif op == "movc":
            r = ew(lambda c, x, y: np.where(as_u(c) != 0, x, y))
        elif op in ("dp2", "dp3", "dp4"):
            k = int(op[2])
            v = sum(s[0][i] * s[1][i] for i in range(k))
            r = [v] * 4
        elif op == "rsq":
            r = ew(lambda x: 1.0 / np.sqrt(x))
        elif op == "sqrt":
            r = ew(np.sqrt)
        elif op == "rcp":
            r = ew(lambda x: 1.0 / x)
        elif op == "frc":
            r = ew(lambda x: x - np.floor(x))
        elif op == "round_ne":
            r = ew(np.rint)
        elif op == "round_ni":
            r = ew(np.floor)
        elif op == "round_pi":
            r = ew(np.ceil)
        elif op == "round_z":
            r = ew(np.trunc)
        elif op == "exp":
            r = ew(np.exp2)
        elif op == "log":
            r = ew(np.log2)
        elif op == "and":
            r = ew(lambda x, y: as_f(as_u(x) & as_u(y)))
        elif op == "or":
            r = ew(lambda x, y: as_f(as_u(x) | as_u(y)))
        elif op == "iadd":
            r = ew(lambda x, y: fi(i32(x) + i32(y)))
        elif op == "imad":
            r = ew(lambda x, y, w: fi(i32(x) * i32(y) + i32(w)))
        elif op == "umad":
            r = ew(lambda x, y, w: fi(as_u(x).astype(np.int64) * as_u(y) + as_u(w)))
        elif op == "ineg":
            r = ew(lambda x: fi(-i32(x)))
        elif op == "ishl":
            r = ew(lambda x, y: fi(as_u(x).astype(np.int64) << (as_u(y) & 31)))
        elif op == "ishr":
            r = ew(lambda x, y: fi(i32(x) >> (as_u(y) & 31)))
        elif op == "ushr":
            r = ew(lambda x, y: as_f(as_u(x) >> (as_u(y) & 31)))
        elif op == "xor":
            r = ew(lambda x, y: as_f(as_u(x) ^ as_u(y)))
        elif op == "not":
            r = ew(lambda x: as_f(~as_u(x)))
        elif op in ("ige", "ilt", "ieq", "ine", "uge", "ult"):
            f = {"ige": lambda x, y: i32(x) >= i32(y), "ilt": lambda x, y: i32(x) < i32(y),
                 "ieq": lambda x, y: as_u(x) == as_u(y), "ine": lambda x, y: as_u(x) != as_u(y),
                 "uge": lambda x, y: as_u(x) >= as_u(y), "ult": lambda x, y: as_u(x) < as_u(y)}[op]
            r = cmp(f)
        elif op in ("imin", "imax"):
            r = ew(lambda x, y: fi((np.minimum if op == "imin" else np.maximum)(i32(x), i32(y))))
        elif op == "umax":
            r = ew(lambda x, y: as_f(np.maximum(as_u(x), as_u(y))))
        elif op == "ftou":
            r = ew(lambda x: as_f(np.clip(np.nan_to_num(x), 0, 4294967295).astype(np.uint64).astype(U32)))
        elif op in ("deriv_rtx", "deriv_rty", "deriv_rtx_coarse", "deriv_rty_coarse", "deriv_rtx_fine",
                    "deriv_rty_fine"):
            r = [np.zeros(n, F32)] * 4          # no neighbouring pixels
        elif op == "umin":
            r = ew(lambda x, y: as_f(np.minimum(as_u(x), as_u(y))))
        elif op == "utof":
            r = ew(lambda x: as_u(x).astype(F32))
        elif op == "ftoi":
            r = ew(lambda x: as_f(np.trunc(x).astype(np.int32).view(U32)))
        elif op == "itof":
            r = ew(lambda x: as_u(x).view(np.int32).astype(F32))
        else:
            raise NotImplementedError(op)
        self._write(d, r, sat)

    def _sample(self, a, sat, n):
        # sample_indexable(texture2d)(float,...) dst, coord, tN.swz, sN
        d = self._dst(a[0].split(")")[-1].strip())
        coord = self._src(a[1], n)
        tm = re.fullmatch(r"t(\d+)\.([xyzw]+)", a[2].strip())
        ti, swz = int(tm.group(1)), tm.group(2)
        tex = self.tex.get(self.tex_index.get(ti, -1))
        if tex is None:
            res = np.full((n, 4), 0.5, F32)
        else:
            res = tex.sample(coord[0], coord[1], coord[2] if tex.is_array else None)
        vals = [res[:, SWZ[c]] for c in swz]
        self._write(d, vals, sat)
