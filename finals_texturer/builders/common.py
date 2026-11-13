"""Shared material-building pieces: Ctx (textures, UVs, output), small
helpers used by several shaders, and the generic fallback builder."""

import math

import bpy

from .. import defaults, groups
from ..nodes import NodeBuilder, _is_socket
from ..ue_assets import asset_name

NEUTRAL = {
    "normal": (0.5, 0.5, 1.0, 1.0),
    "mask": (0.5, 0.5, 0.5, 1.0),
    "color": (1.0, 1.0, 1.0, 1.0),
    "black": (0.0, 0.0, 0.0, 1.0),
}


class Ctx:
    """Everything one material build needs."""

    def __init__(self, mat, params, finder, log, detail_uv=None, cm_per_unit=1.0):
        self.mat = mat
        # Game centimetres per Blender unit of the mesh (100 at real size):
        # for material parameters given in game units (e.g. heights).
        self.cm_per_unit = cm_per_unit
        # Name of the mesh's second UV channel. Its texel density is uniform
        # (50 cm per UV unit on every mesh measured), and the layered shader's
        # detail textures (fabric normals, roughness/wear masks, colour
        # textures, patterns, hue noise) tile on it. Colour masks, mesh
        # normals, OCM and decals stay on the first channel.
        self.detail_uv = detail_uv
        self._duv = None
        self.p = params
        self.finder = finder
        self.log = log
        tree = mat.node_tree
        tree.nodes.clear()
        self.nb = NodeBuilder(tree)
        self._uv = None
        self._scaled = {}
        self._tex = {}
        self._missing = set()

    # ── UVs ──────────────────────────────────────────────────────────────────
    def uv(self, scale=1.0, detail=False):
        if detail and self.detail_uv:
            if self._duv is None:
                n = self.nb.node("ShaderNodeUVMap", "Detail UV (2nd channel)")
                n.uv_map = self.detail_uv
                self._duv = n.outputs["UV"]
            base = self._duv
        else:
            if self._uv is None:
                self._uv = self.nb.node("ShaderNodeTexCoord").outputs["UV"]
            base = self._uv
        scale = round(float(scale), 5)
        if scale == 1.0:
            return base
        key = (scale, base is self._duv)
        if key not in self._scaled:
            self._scaled[key] = self.nb.vmath("SCALE", base, scale=scale,
                                              label=f"{'Detail ' if base is self._duv else ''}UV x{scale:g}")
        return self._scaled[key]

    # ── images ───────────────────────────────────────────────────────────────
    def image(self, path, data=True, packed=False):
        img = bpy.data.images.load(path, check_existing=True)
        try:
            img.colorspace_settings.name = "Non-Color" if data else "sRGB"
        except TypeError:
            pass
        if packed:
            img.alpha_mode = "CHANNEL_PACKED"
        return img

    def tex(self, object_path, data=True, packed=False, scale=1.0, label=None,
            vector=None, extension="REPEAT", detail=False):
        """Image node for a texture object path, or None if it can't be found."""
        if not object_path:
            return None
        if asset_name(object_path).lower() in defaults.NULL_TEXTURES:
            return None
        key = (object_path, round(scale, 5), id(vector), extension, detail)
        if key in self._tex:
            return self._tex[key]
        path = self.finder.find_texture(object_path)
        if path and not data and not self._is_srgb(object_path):
            # stored linear (SRGB false, e.g. the Cashout vault's colour texture): the game reads it raw
            data = True
        baked = None if path else self.atlas_image(object_path)
        if not path and baked is None:
            self._warn_missing(asset_name(object_path), object_path)
            self._tex[key] = None
            return None
        if baked is not None:
            n = self.nb.node("ShaderNodeTexImage", label or asset_name(object_path))
            n.image = baked
            n.extension = extension
        else:
            n = self._image_node(path, data, packed, label or asset_name(object_path), extension)
        self.nb.feed(n.inputs["Vector"], vector if vector is not None else self.uv(scale, detail))
        self._tex[key] = n
        return n

    def _is_srgb(self, object_path):
        """The texture's SRGB flag from its JSON (UE default: true)."""
        meta = self.finder.load_asset_json(object_path)
        for e in meta if isinstance(meta, list) else []:
            props = (e or {}).get("Properties") or {}
            if "SRGB" in props:
                return bool(props["SRGB"])
        return True

    def atlas_image(self, object_path):
        """A curve atlas (CA_*, no image file) baked from its curves, or None.
        Rows top to bottom = the atlas's curves; values are linear colours."""
        from ..curves import atlas_pixels
        from ..ue_assets import export_of_type
        name = "FT_" + asset_name(object_path)
        img = bpy.data.images.get(name)
        if img is not None and img.get("ft_curve_atlas"):
            return img
        exp = export_of_type(self.finder.load_asset_json(object_path), ("CurveLinearColorAtlas",))
        baked = atlas_pixels(self.finder, exp) if exp else None
        if baked is None:
            return None
        w, h, flat = baked
        img = bpy.data.images.new(name, w, h, alpha=True, float_buffer=True)
        # colour space first: changing it later regenerates the (blank) image
        try:
            img.colorspace_settings.name = "Non-Color"     # already linear colours
        except TypeError:
            pass
        img.pixels.foreach_set(flat)
        img.update()
        img.pack()
        img["ft_curve_atlas"] = True
        img["ft_atlas_rows"] = h
        return img

    def gradient(self, object_path, v_from_top, fac, label=None, row=None):
        """(colour, alpha) sockets of one row of a gradient texture or curve
        atlas, as a Color Ramp driven by `fac` (0..1 along the row).

        A ramp instead of an image lookup: the lookup position jumps at the
        edges of whatever drives it (pattern stripes), the GPU then reads a
        blurred mip level of the whole atlas and the edges turn pale. Rows are
        blended like bilinear filtering at v (0 = top row), or `row` picks a
        row by index (curve atlases: the curve). 32 stops."""
        path = self.finder.find_texture(object_path)
        img = None
        if path:
            img = self.image(path, data=False)
        else:
            img = self.atlas_image(object_path)
        if img is None:
            self._warn_missing(asset_name(object_path), object_path)
            return None, None
        w, h = img.size
        if not w or not h:
            return None, None
        px = img.pixels[:]
        # Image.pixels are stored values: sRGB image files are still encoded,
        # Color Ramp stops are linear. Baked atlases are linear already.
        srgb = not img.get("ft_curve_atlas") and img.colorspace_settings.name == "sRGB"
        if row is not None:
            v_from_top = (row + 0.5) / h

        def texel(x, row):          # row 0 = top
            iy = h - 1 - min(max(row, 0), h - 1)
            i = (iy * w + min(max(x, 0), w - 1)) * 4
            return px[i:i + 4]

        rowf = v_from_top * h - 0.5
        r0 = int(math.floor(rowf))
        t = rowf - r0
        stops = min(32, w)
        ramp = self.nb.node("ShaderNodeValToRGB", label or f"{asset_name(object_path)} (row {v_from_top:.3f})")
        els = ramp.color_ramp.elements
        while len(els) < stops:
            els.new(0.0)
        for k in range(stops):
            pos = k / (stops - 1)
            xf = pos * (w - 1)
            x0 = int(math.floor(xf))
            tx = xf - x0
            c = [0.0] * 4
            for (xx, wx) in ((x0, 1 - tx), (x0 + 1, tx)):
                for (rr, wy) in ((r0, 1 - t), (r0 + 1, t)):
                    tv = texel(xx, rr)
                    for ch in range(4):
                        c[ch] += tv[ch] * wx * wy
            if srgb:
                c[:3] = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c[:3]]
            els[k].position = pos
            els[k].color = tuple(c)
        self.nb.feed(ramp.inputs["Fac"], fac)
        return ramp.outputs["Color"], ramp.outputs["Alpha"]

    def slice(self, array_path, index, data=True, scale=1.0, label=None, packed=False):
        """Image node for one slice of a Texture2DArray."""
        if not array_path:
            return None
        index = int(round(index))
        key = ("slice", array_path, index, round(scale, 5), packed)
        if key in self._tex:
            return self._tex[key]
        path = self.finder.find_array_slice(array_path, index)
        if not path:
            self._warn_missing(f"{asset_name(array_path)}_LAYER{index}", array_path)
            self._tex[key] = None
            return None
        n = self._image_node(path, data, packed, label or f"{asset_name(array_path)} [{index}]", "REPEAT")
        self.nb.feed(n.inputs["Vector"], self.uv(scale, detail=True))
        self._tex[key] = n
        return n

    def red(self, node):
        """R channel of an image node. Mask textures may be exported red-only,
        and feeding a colour into a float socket would average in G and B."""
        key = ("red", node.name)
        if key not in self._tex:
            self._tex[key] = self.nb.separate(node.outputs["Color"])[0]
        return self._tex[key]

    def _image_node(self, path, data, packed, label, extension):
        n = self.nb.node("ShaderNodeTexImage", label)
        n.image = self.image(path, data, packed)
        n.extension = extension
        n.hide = False
        return n

    def _warn_missing(self, name, object_path):
        if name in self._missing:
            return
        self._missing.add(name)
        self.log.warn(f"  Texture not found: {name} (game path {object_path}). Using a neutral value.")

    # ── output ───────────────────────────────────────────────────────────────
    def bsdf(self):
        n = self.nb.node("ShaderNodeBsdfPrincipled")
        out = self.nb.node("ShaderNodeOutputMaterial")
        self.nb.feed(out.inputs["Surface"], n.outputs[0])
        return n

    def normal_map(self, color):
        n = self.nb.node("ShaderNodeNormalMap")
        try:
            n.convention = "DIRECTX"      # Unreal normal maps are DirectX (green down)
        except (AttributeError, TypeError):
            # Older Blender: flip green by hand.
            r, g, b = self.nb.separate(color)
            color = self.nb.combine(r, self.nb.sub(1.0, g), b)
        self.nb.feed(n.inputs["Color"], color)
        return n.outputs["Normal"]

    def finish(self, two_sided=True, blended=False):
        """blended=True for see-through surfaces (glass, plastic sheets)."""
        m = self.mat
        try:
            m.surface_render_method = "BLENDED" if blended else "DITHERED"
        except (AttributeError, TypeError):
            m.blend_method = "BLEND" if blended else "HASHED"
        # Always off: mirrored parts (e.g. the left boot of a pair) come in with
        # reversed faces, and culling would hide their outside.
        m.use_backface_culling = False
        m["ft_two_sided"] = bool(two_sided)
        self.nb.layout()


def _col(v, a=None):
    return (v[0], v[1], v[2], 1.0 if a is None else a)


def _out(node, name="Color", fallback=None):
    return node.outputs[name] if node is not None else fallback


_SUFFIX_ROLES = [
    (("_basecolor", "_color", "_c", "_ca", "_d", "_diffuse", "_albedo"), "color"),
    (("_normal", "_normals", "_n"), "normal"),
    (("_nom", "_nxm", "_nem", "_nam", "_nx", "_noh"), "packed_normal"),
    (("_m", "_mask"), "mask"),
]


def _alpha_is_empty(img):
    """True if every pixel of the image is fully transparent (e.g. a blank overlay).
    Using such an alpha as opacity would make the whole object invisible."""
    if img is None or not img.size[0]:
        return False
    try:
        import numpy as np
        px = np.empty(img.size[0] * img.size[1] * img.channels, dtype=np.float32)
        img.pixels.foreach_get(px)
        return img.channels == 4 and float(px[3::4].max()) <= 0.0
    except Exception:
        return False


def _role(name):
    low = name.lower()
    for sufs, role in _SUFFIX_ROLES:
        if any(low.endswith(s) for s in sufs):
            return role
    return ""


def build_generic(ctx):
    """Unknown master: wire what we can recognise from texture names."""
    p, nb = ctx.p, ctx.nb
    bsdf = ctx.bsdf()
    roles = {}
    for pname, path in p.textures.items():
        role = _role(asset_name(path)) or _role("_" + pname)
        if role and role not in roles and asset_name(path).lower() not in defaults.NULL_TEXTURES:
            roles[role] = (pname, path)
    tint = None
    for name in ("Color", "Tint", "TintColor", "ColorTint", "BaseColor", "Base Color"):
        if name in p.vectors:
            tint = _col(p.vectors[name])
            break
    color = tint or (0.8, 0.8, 0.8, 1.0)
    if "color" in roles:
        t = ctx.tex(roles["color"][1], data=False, label=roles["color"][0])
        if t is not None:
            color = nb.mix(t.outputs["Color"], color, 1.0 if tint else 0.0, "MULTIPLY")
            if p.blend_mode in ("BLEND_Masked", "BLEND_Translucent"):
                if _alpha_is_empty(t.image):
                    ctx.log.info(f"  {asset_name(roles['color'][1])} has no opacity; ignoring its alpha.")
                else:
                    nb.feed(bsdf.inputs["Alpha"], t.outputs["Alpha"])
    nb.feed(bsdf.inputs["Base Color"], color)
    if "normal" in roles:
        t = ctx.tex(roles["normal"][1], data=True, label=roles["normal"][0])
        if t is not None:
            nb.feed(bsdf.inputs["Normal"], ctx.normal_map(t.outputs["Color"]))
    elif "packed_normal" in roles:
        t = ctx.tex(roles["packed_normal"][1], data=True, packed=True, label=roles["packed_normal"][0])
        if t is not None:
            r, g, _b = nb.separate(t.outputs["Color"])
            rg = nb.group(groups.normal_from_rg())
            nb.feed(rg.inputs["X"], r)
            nb.feed(rg.inputs["Y"], g)
            nb.feed(bsdf.inputs["Normal"], ctx.normal_map(rg.outputs[0]))
    for src, dst in (("Roughness", "Roughness"), ("Metallic", "Metallic"), ("Metallicity", "Metallic")):
        if src in p.scalars:
            nb.feed(bsdf.inputs[dst], max(0.0, min(1.0, p.scalars[src])))
    ctx.log.info(f"  Unknown master '{p.master or '?'}': used textures {sorted(r for r in roles)}.")
    ctx.finish(two_sided=bool(p.two_sided))


def _packed_normal(ctx, tex, strength=1.0):
    """Normal from the R/G channels of a packed texture (NOM / NOH); also returns B."""
    nb = ctx.nb
    r, g, b = nb.separate(tex.outputs["Color"], label="Packed channels")
    rg = nb.group(groups.normal_from_rg(), label="Normal from RG")
    nb.feed(rg.inputs["X"], r)
    nb.feed(rg.inputs["Y"], g)
    nb.feed(rg.inputs["Strength"], strength)
    return ctx.normal_map(rg.outputs[0]), b


def _normal_tex(ctx, path, label="Normal", strength=1.0):
    """Normal map from either a plain normal texture or a packed NOM/NOH one.

    Returns (normal socket, ambient occlusion socket or None).
    """
    t = ctx.tex(path, data=True, packed=True, label=label)
    if t is None:
        return None, None
    name = asset_name(path).lower()
    if name.endswith(("_normal", "_n")):
        return ctx.normal_map(t.outputs["Color"]), None
    return _packed_normal(ctx, t, strength)


def _fresnel_ramp(ctx, a, b, c, label):
    """Colour ramp A → B → C across the facing ratio (UE 'rainbow fresnel')."""
    nb = ctx.nb
    lw = nb.node("ShaderNodeLayerWeight", f"{label} fresnel")
    lw.inputs["Blend"].default_value = 0.5
    ramp = nb.node("ShaderNodeValToRGB", label)
    els = ramp.color_ramp.elements
    els[0].position, els[0].color = 0.0, _col(a)
    els[1].position, els[1].color = 1.0, _col(c)
    mid = els.new(0.5)
    mid.color = _col(b)
    nb.feed(ramp.inputs["Fac"], lw.outputs["Facing"])
    return ramp.outputs["Color"], lw


def _alpha_range(img):
    """(min, max) of an image's alpha, or None if it can't be read."""
    if img is None or not img.size[0] or img.channels != 4:
        return None
    try:
        import numpy as np
        px = np.empty(img.size[0] * img.size[1] * 4, dtype=np.float32)
        img.pixels.foreach_get(px)
        a = px[3::4]
        return float(a.min()), float(a.max())
    except Exception:
        return None


def _hsv_norm(nb, color, value, sat_scale=1.0, label=None):
    """A colour's hue at a fixed brightness (and scaled saturation)."""
    sep = nb.node("ShaderNodeSeparateColor", "HSV")
    sep.mode = "HSV"
    nb.feed(sep.inputs[0], color)
    comb = nb.node("ShaderNodeCombineColor", label)
    comb.mode = "HSV"
    nb.feed(comb.inputs[0], sep.outputs[0])
    nb.feed(comb.inputs[1], nb.mul(sep.outputs[1], sat_scale) if sat_scale != 1.0 else sep.outputs[1])
    nb.feed(comb.inputs[2], value)
    return comb.outputs[0]
