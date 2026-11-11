"""The game's shape keys on imported meshes.

* Body type: every body, head and clothing mesh carries keys named
  <class>_<gender>[_muscular] for its class (light/medium/heavy). Muscular only
  exists on Heavy. Exactly one is switched on.
* Tag keys: items switch keys on through their ActivatesTags,
  "Customization.Shape.<Category>.<key>" (e.g. pants turn on push_full_pants on
  the body so it doesn't poke through, a face turns on head_neck_match).
  A key is on when any item on the character asks for it.

Only keys that came with the PSK (stored at import as ft_psk_shapekeys) are
changed; keys the user adds are left alone.
"""

import json
import re

BODY_TYPE_RE = re.compile(r"^(light|medium|heavy)_(male|female)(_muscular)?$")
CLASS_WORD = {"L": "light", "M": "medium", "H": "heavy"}
SHAPE_TAG = "Customization.Shape."

_tag_cache = {}


def body_type_key(cls, gender, muscular):
    """Key name for a class (L/M/H), gender ('male'/'female') and muscular flag."""
    key = f"{CLASS_WORD.get(cls, 'medium')}_{gender}"
    return key + "_muscular" if muscular and cls == "H" else key


def game_keys(obj):
    keys = obj.data.shape_keys if obj.type == "MESH" else None
    if not keys:
        return []
    names = obj.get("ft_psk_shapekeys")
    if names is None:
        return [k.name for k in keys.key_blocks[1:]]
    return [n for n in names if n in keys.key_blocks]


def set_body_type(obj, cls, gender, muscular):
    """Switch on the body-type key. Returns 'ok', 'none' (mesh has no body-type
    keys) or 'mismatch' (only another class's keys, e.g. a Light jacket on a
    Heavy body)."""
    kb = obj.data.shape_keys.key_blocks if obj.type == "MESH" and obj.data.shape_keys else None
    names = [n for n in game_keys(obj) if BODY_TYPE_RE.match(n)]
    if not kb or not names:
        return "none"
    want = body_type_key(cls, gender, muscular)
    if want not in names and want.endswith("_muscular"):
        want = want[:-len("_muscular")]
    for n in names:
        kb[n].value = 1.0 if n == want else 0.0
    return "ok" if want in names else "mismatch"


def shape_tags(skin_path):
    """Shape keys a skin file (DA_*.json) switches on."""
    if not skin_path or not skin_path.lower().endswith(".json"):
        return set()
    hit = _tag_cache.get(skin_path)
    if hit is not None:
        return hit
    keys = set()
    try:
        with open(skin_path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = None
    for exp in data if isinstance(data, list) else [data] if isinstance(data, dict) else []:
        for tag in (exp.get("Properties") or {}).get("ActivatesTags") or []:
            if isinstance(tag, str) and tag.startswith(SHAPE_TAG):
                parts = tag[len(SHAPE_TAG):].split(".")
                if len(parts) >= 2 and parts[-1]:
                    keys.add(parts[-1])
    _tag_cache[skin_path] = keys
    return keys


def clear_cache():
    _tag_cache.clear()


def item_skin(obj):
    """The skin file an object was imported with (its tags apply)."""
    return obj.get("ft_skin") or obj.get("ft_outfit_skin") or ""


def apply_tag_keys(objs, active):
    """On for keys in `active`, off for every other game key (body types aside)."""
    changed = 0
    for o in objs:
        kb = o.data.shape_keys.key_blocks if o.type == "MESH" and o.data.shape_keys else None
        if not kb:
            continue
        for n in game_keys(o):
            if BODY_TYPE_RE.match(n):
                continue
            v = 1.0 if n in active else 0.0
            if kb[n].value != v:
                kb[n].value = v
                changed += 1
    return changed


def apply_character(objs, cls, gender, muscular):
    """Body type + every item's tag keys across a character's meshes.

    Returns (names of meshes with another class's keys, active tag keys)."""
    meshes = [o for o in objs if o.type == "MESH"]
    active = set()
    for o in meshes:
        active |= shape_tags(item_skin(o))
    mismatched = [o.name for o in meshes if set_body_type(o, cls, gender, muscular) == "mismatch"]
    apply_tag_keys(meshes, active)
    return mismatched, active
