"""Rigging tab: THE FINALS rig simplifier + Auto-Rig Pro bridge.

Based on "FINALS Rig Tools" by zebby (finals_rig_tools.py), integrated as the
Rigging tab of the Finals sidebar panel.

1. Simplify Body Rig: reduce the game skeleton (dozens of *_vpj corrective and
   twist bones) to a standard humanoid one. Removed bones' weights are merged
   into the bone that carries them; bone names are kept (pelvis, thigh_l ...).
2. Attach Clothing / Gear: merge a piece's vertex groups with the same rules,
   bind it to the simplified body rig, optionally delete its own armature.
3. Generate Auto-Rig Pro Controls: Quick Rig in "Preserve" mode builds ARP
   controllers that drive the simplified skeleton (needs Auto-Rig Pro 3.77.43+
   with the Quick Rig add-on).
"""

import bpy
import os
import re
import tempfile
import importlib
import addon_utils
from collections import defaultdict
from mathutils import Vector

# ---------------------------------------------------------------------------
#  Skeleton definition
# ---------------------------------------------------------------------------

SIDES = ("l", "r")
FINGERS = ("index", "middle", "ring", "pinky")

CORE_BONES = ["root", "pelvis", "spine_01", "spine_02", "spine_03",
              "neck_01", "neck_02", "head"]
for s in SIDES:
    CORE_BONES += [f"clavicle_{s}", f"upperarm_{s}", f"lowerarm_{s}", f"hand_{s}"]
    CORE_BONES += [f"thumb_0{i}_{s}" for i in (1, 2, 3)]
    for f in FINGERS:
        CORE_BONES += [f"{f}_0{i}_{s}" for i in (1, 2, 3, 4)]
    CORE_BONES += [f"thigh_{s}", f"calf_{s}", f"foot_{s}", f"ball_{s}"]
CORE_SET = set(CORE_BONES)

# Hip-crease correctives (thigh_front/back_vpj) are parented to the pelvis in the
# game rig, but the game moves them part-way with the THIGH. Their weights are
# split: HIP_FOLLOW goes to the thigh, the rest to the pelvis.
#   0.0 = deforms exactly like the original rig does in Blender (all pelvis)
#   0.5 = halfway (default, closest to the in-game look)
HIP_FOLLOW = 0.5
WEIGHT_OVERRIDES = {}
for s in SIDES:
    WEIGHT_OVERRIDES[f"thigh_front_vpj_{s}"] = f"thigh_{s}"
    WEIGHT_OVERRIDES[f"thigh_back_vpj_{s}"] = f"thigh_{s}"

# Name-based fallback for vertex groups whose bone is not in the piece's armature
# (first matching regex wins; \\1 / \\2 refer to regex groups).
NAME_FALLBACKS = [
    (r"^upperarm_.*_(l|r)$", r"upperarm_\1"),
    (r"^lowerarm_.*_(l|r)$", r"lowerarm_\1"),
    (r"^elbow_.*_(l|r)$", r"upperarm_\1"),
    (r"^chest_.*_(l|r)$", r"clavicle_\1"),
    (r"^thigh_.*_(l|r)$", r"thigh_\1"),
    (r"^knee_.*_(l|r)$", r"thigh_\1"),
    (r"^calf_.*_(l|r)$", r"calf_\1"),
    (r"^foot_.*_(l|r)$", r"foot_\1"),
    (r"^hand_.*_(l|r)$", r"hand_\1"),
    (r"^neck_.*$", r"neck_01"),
    (r"^head_.*$", r"head"),
    (r"^spine_.*$", r"spine_03"),
    (r"^clavicle_attachment$", r"spine_03"),
]

# Bones that get a specific tail target when tidying orientations.
TAIL_TARGET = {"pelvis": "spine_01", "spine_03": "neck_01"}
for s in SIDES:
    TAIL_TARGET[f"hand_{s}"] = f"middle_02_{s}"
    TAIL_TARGET[f"foot_{s}"] = f"ball_{s}"

MASTER_PROP = "finals_master_rig"


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def _ensure_object_mode():
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


def _set_active(obj):
    for o in bpy.context.selected_objects:
        o.select_set(False)
    obj.hide_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def meshes_using_armature(arm_obj):
    out = []
    for o in bpy.data.objects:
        if o.type != "MESH":
            continue
        if any(m.type == "ARMATURE" and m.object == arm_obj for m in o.modifiers):
            out.append(o)
        elif o.parent == arm_obj and o.vertex_groups:
            out.append(o)
    return out


def source_armature_of(mesh_obj):
    for m in mesh_obj.modifiers:
        if m.type == "ARMATURE" and m.object:
            return m.object
    if mesh_obj.parent and mesh_obj.parent.type == "ARMATURE":
        return mesh_obj.parent
    return None


def _parent_name(name, src_bones, parents):
    if parents and name in parents:
        return parents[name] or None
    b = src_bones.get(name)
    return b.parent.name if b is not None and b.parent is not None else None


def resolve_target(name, src_bones, master_names, parents=None):
    """Return the master bone that should receive the weights of `name`
    (None = leave the vertex group untouched, not a skeleton group).
    parents: {bone: game parent} for bones whose parent was cleared (heads
    following the body, see character.fit_to_body)."""
    if name in master_names:
        return name
    ov = WEIGHT_OVERRIDES.get(name)
    if ov in master_names:
        if 0.0 < HIP_FOLLOW < 1.0 and "pelvis" in master_names:
            return [(ov, HIP_FOLLOW), ("pelvis", 1.0 - HIP_FOLLOW)]
        return ov if HIP_FOLLOW >= 1.0 or "pelvis" not in master_names else "pelvis"
    if src_bones is not None and name in src_bones:
        p = _parent_name(name, src_bones, parents)
        while p is not None:
            if p in master_names:
                return p
            if p in WEIGHT_OVERRIDES:
                return resolve_target(p, None, master_names)
            p = _parent_name(p, src_bones, parents)
        for fb in ("pelvis", "root"):
            if fb in master_names:
                return fb
    for pat, rep in NAME_FALLBACKS:
        if re.match(pat, name):
            t = re.sub(pat, rep, name)
            if t in master_names:
                return t
    return None


def merge_vertex_groups(obj, name_map):
    """name_map: {source_group: target_group | [(target_group, factor), ...]}.
    Weights of every source are ADDED into its target(s) (clamped to 1), sources
    are removed. Groups not in the map are left untouched."""
    vgs = obj.vertex_groups
    moves = {}
    for s, t in name_map.items():
        if s not in vgs:
            continue
        splits = [(t, 1.0)] if isinstance(t, str) else list(t)
        if splits == [(s, 1.0)]:
            continue
        moves[s] = splits
    if not moves:
        return 0
    targets = {t for sp in moves.values() for t, _ in sp}
    involved = set(moves) | {t for t in targets if t in vgs}
    idx_target = {}
    for vg in vgs:
        if vg.name in moves:
            idx_target[vg.index] = moves[vg.name]
        elif vg.name in targets:
            idx_target[vg.index] = [(vg.name, 1.0)]

    acc = defaultdict(dict)
    for v in obj.data.vertices:
        for g in v.groups:
            sp = idx_target.get(g.group)
            if sp is not None and g.weight > 0.0:
                for t, f in sp:
                    d = acc[t]
                    d[v.index] = d.get(v.index, 0.0) + g.weight * f

    for n in involved:
        vgs.remove(vgs[n])
    for t in sorted(targets):
        vg = vgs.new(name=t)
        buckets = defaultdict(list)
        for vi, w in acc.get(t, {}).items():
            buckets[round(min(w, 1.0), 6)].append(vi)
        for w, ids in buckets.items():
            vg.add(ids, w, "REPLACE")
    return len(moves)


# ---------------------------------------------------------------------------
#  1. Simplify
# ---------------------------------------------------------------------------

def simplify_armature(arm_obj, tidy_orientation=True, report=print):
    _ensure_object_mode()
    from .character import store_rest
    store_rest(arm_obj)          # keep the game rest pose for fitting heads later
    data = arm_obj.data
    keep = {b.name for b in data.bones if b.name in CORE_SET}
    missing = [n for n in CORE_BONES if n not in keep]
    if "pelvis" not in keep or "head" not in keep:
        raise RuntimeError(f"'{arm_obj.name}' does not look like a FINALS/UE body skeleton "
                           f"(missing pelvis/head).")

    # --- weights (before bones are removed, we still need the hierarchy)
    meshes = meshes_using_armature(arm_obj)
    merged_total = 0
    unresolved = set()
    for me in meshes:
        nmap = {}
        for vg in me.vertex_groups:
            t = resolve_target(vg.name, data.bones, keep)
            if t is None:
                unresolved.add(vg.name)
            else:
                nmap[vg.name] = t
        merged_total += merge_vertex_groups(me, nmap)
    _retarget_followers(arm_obj, keep)

    # --- bones
    _set_active(arm_obj)
    bpy.ops.object.mode_set(mode="EDIT")
    eb = data.edit_bones

    def kept_ancestor(e):
        p = e.parent
        while p is not None and p.name not in keep:
            p = p.parent
        return p

    for e in [e for e in eb if e.name in keep]:
        e.parent = kept_ancestor(e)
    removed = [e.name for e in eb if e.name not in keep]
    for n in removed:
        eb.remove(eb[n])
    for e in eb:
        e.use_deform = e.name != "root"
        e.use_connect = False

    if tidy_orientation:
        _tidy_bones(eb)

    bpy.ops.object.mode_set(mode="OBJECT")

    # clean pose-level leftovers (constraints/drivers pointing at removed bones)
    if arm_obj.animation_data:
        for fc in list(arm_obj.animation_data.drivers):
            if any(f'"{n}"' in fc.data_path for n in removed):
                arm_obj.animation_data.drivers.remove(fc)

    for me in meshes:
        for m in me.modifiers:
            if m.type == "ARMATURE" and m.object == arm_obj:
                m.use_vertex_groups = True
                m.use_deform_preserve_volume = False

    arm_obj[MASTER_PROP] = True
    bpy.context.scene.ft_rig.master = arm_obj
    report(f"Simplified '{arm_obj.name}': {len(removed)} bones removed, {len(keep)} kept, "
           f"{merged_total} vertex groups merged on {len(meshes)} mesh(es).")
    if missing:
        report(f"Note: skeleton has no {', '.join(missing)}")
    if unresolved:
        report(f"Left untouched (not skeleton groups): {', '.join(sorted(unresolved))}")
    return removed


def _retarget_followers(arm_obj, keep):
    """Heads from the Character tab follow body bones through Armature
    constraints. A followed bone that Simplify removes hands over to the bone
    that receives its weights (the same split as the weights)."""
    bones = arm_obj.data.bones
    for o in bpy.data.objects:
        if o.type != "ARMATURE" or o == arm_obj or o.pose is None:
            continue
        for pb in o.pose.bones:
            for c in pb.constraints:
                if c.type != "ARMATURE":
                    continue
                for t in list(c.targets):
                    if t.target != arm_obj or not t.subtarget or t.subtarget in keep:
                        continue
                    new = resolve_target(t.subtarget, bones, keep)
                    if new is None:
                        continue
                    splits = [(new, 1.0)] if isinstance(new, str) else list(new)
                    w = t.weight
                    t.subtarget, t.weight = splits[0][0], w * splits[0][1]
                    for name, f in splits[1:]:
                        nt = c.targets.new()
                        nt.target, nt.subtarget, nt.weight = arm_obj, name, w * f


def _tidy_bones(eb):
    """Point every bone at its child / a sensible direction and set clean rolls.
    Safe for skinning: the mesh is bound in rest pose, so only the rest
    orientation changes, never the deformation."""
    heads = {e.name: e.head.copy() for e in eb}
    height = max((h.z for h in heads.values()), default=1.0) or 1.0
    up, fwd = Vector((0, 0, 1)), Vector((0, -1, 0))

    def single_child(e):
        ch = [c for c in e.children]
        return ch[0] if len(ch) == 1 else None

    order = sorted(eb, key=lambda e: len(e.parent_recursive))
    for e in order:
        n = e.name
        target = None
        if n in TAIL_TARGET and TAIL_TARGET[n] in heads:
            target = heads[TAIL_TARGET[n]]
        elif single_child(e) is not None:
            target = heads[single_child(e).name]
        if n == "root":
            e.tail = e.head + fwd * -(height * 0.15)
            e.roll = 0.0
            continue
        if n == "head":
            e.tail = e.head + up * (height * 0.12)
        elif n.startswith("ball_"):
            foot = e.parent
            d = (e.head - foot.head) if foot else fwd
            d.z = 0.0
            d = d.normalized() if d.length > 1e-6 else fwd
            e.tail = e.head + d * (height * 0.05)
        elif target is not None and (target - e.head).length > 1e-4:
            e.tail = target
        else:  # leaf: continue the parent's direction
            p = e.parent
            if p is not None:
                d = (p.tail - p.head)
                e.tail = e.head + d.normalized() * max(d.length * 0.75, height * 0.01)
        # rolls: Z axis forward for vertical chains, up for horizontal ones
        y = (e.tail - e.head).normalized()
        e.align_roll(fwd if abs(y.z) > 0.7 else up)


# ---------------------------------------------------------------------------
#  2. Attach clothing / gear
# ---------------------------------------------------------------------------

def rest_mismatch(src_arm, master):
    """Largest distance between same-named bone heads (world space)."""
    worst, worst_name = 0.0, ""
    for b in src_arm.data.bones:
        mb = master.data.bones.get(b.name)
        if mb is None:
            continue
        a = src_arm.matrix_world @ b.head_local
        c = master.matrix_world @ mb.head_local
        d = (a - c).length
        if d > worst:
            worst, worst_name = d, b.name
    return worst, worst_name


def attach_pieces(master, objects, delete_source_armatures=True, report=print):
    _ensure_object_mode()
    master_names = {b.name for b in master.data.bones}
    height = max(master.dimensions) or 1.0

    meshes, src_arms = [], set()
    for o in objects:
        if o == master or o.get("arp_rig") or (o.type == "ARMATURE" and o.data.bones.get("c_traj")):
            continue
        if o.type == "ARMATURE":
            src_arms.add(o)
            meshes += [m for m in meshes_using_armature(o) if m not in meshes]
        elif o.type == "MESH" and o not in meshes:
            meshes.append(o)

    done, warnings, attached = 0, [], []
    for me in meshes:
        socket = me.get("ft_socket", "")
        if socket and not me.vertex_groups:
            # static mesh on a bone socket (hat, glasses…): follows that bone
            if socket not in master_names:
                warnings.append(f"{me.name}: attaches to '{socket}' in game, which the body rig "
                                "doesn't have; parent it by hand")
                continue
            mw = me.matrix_world.copy()
            me.parent = master
            me.parent_type = "BONE"
            me.parent_bone = socket
            bpy.context.view_layer.update()
            me.matrix_world = mw
            done += 1
            attached.append(me)
            continue
        src = source_armature_of(me)
        if src == master:
            continue
        if src is not None and src != master:
            src_arms.add(src)
            d, bn = rest_mismatch(src, master)
            if d > height * 0.002:
                warnings.append(f"{me.name}: rest pose differs from body at '{bn}' "
                                f"by {d:.3f} - check fit")
        src_bones = src.data.bones if src else None
        parents = dict(src.get("ft_bone_parents", {})) if src else None
        nmap, unresolved = {}, []
        for vg in me.vertex_groups:
            t = resolve_target(vg.name, src_bones, master_names, parents)
            if t is None:
                unresolved.append(vg.name)
            else:
                nmap[vg.name] = t
        merge_vertex_groups(me, nmap)

        mw = me.matrix_world.copy()
        mods = [m for m in me.modifiers if m.type == "ARMATURE"]
        if not mods:
            mods = [me.modifiers.new("Armature", "ARMATURE")]
        for i, m in enumerate(mods):
            if i == 0:
                m.object = master
                m.use_vertex_groups = True
                m.use_deform_preserve_volume = False
            else:
                me.modifiers.remove(m)
        me.parent = master
        me.parent_type = "OBJECT"
        me.matrix_parent_inverse = master.matrix_world.inverted()
        me.matrix_world = mw
        done += 1
        attached.append(me)
        if unresolved:
            warnings.append(f"{me.name}: untouched non-bone groups {unresolved}")

    deleted = 0
    if delete_source_armatures:
        for a in src_arms:
            if a == master or a.name not in bpy.data.objects:
                continue
            if any(source_armature_of(o) == a or o.parent == a
                   for o in bpy.data.objects if o.type == "MESH"):
                continue
            bpy.data.objects.remove(a, do_unlink=True)
            deleted += 1

    report(f"Attached {done} piece(s) to '{master.name}', removed {deleted} game armature(s).")
    for w in warnings:
        report("WARNING " + w)
    return attached


def character_settings(master, scene):
    """(class, gender, muscular) for a rig: what the Character tab stored on the
    body, else the class from the body mesh name, else the tab's current choice."""
    meshes = meshes_using_armature(master)
    src = next((o for o in [master] + meshes if o.get("ft_char_class")), None)
    if src is not None:
        return src["ft_char_class"], src.get("ft_char_gender", "male"), bool(src.get("ft_char_muscular"))
    cls = scene.ft_char_class
    for o in meshes:
        m = re.search(r"SK_Body_(L|M|H)$", o.get("ft_source_mesh", ""))
        if m:
            cls = m.group(1)
            break
    return cls, scene.ft_char_gender.lower(), bool(scene.ft_char_muscular)


def apply_shape_keys(master, scene, report=print):
    """Body type on every mesh bound to the rig, plus the shape keys their items
    ask for. Recomputed from all bound pieces, so removing a piece and
    attaching again switches its keys back off."""
    from . import shapekeys
    cls, gender, muscular = character_settings(master, scene)
    meshes = meshes_using_armature(master)
    mism, active = shapekeys.apply_character(meshes, cls, gender, muscular)
    key = shapekeys.body_type_key(cls, gender, muscular)
    report(f"Shape keys: body type {key}" + (f"; on: {', '.join(sorted(active))}" if active else ""))
    for name in mism:
        report(f"WARNING {name} is made for another class (no {key.split('_')[0]} body-type keys); "
               "import the matching _L/_M/_H version")


def unbound_pieces(master):
    out = []
    for o in bpy.data.objects:
        if o.type == "MESH" and o.get("ft_socket") and not o.vertex_groups:
            if not (o.parent == master and o.parent_type == "BONE"):
                out.append(o)
            continue
        if o.type != "MESH" or not o.vertex_groups:
            continue
        src = source_armature_of(o)
        if src is None or src == master:
            continue
        if src.get("arp_rig") or src.data.bones.get("c_traj"):
            continue
        # another character's body or head (Character tab) isn't a piece of this one
        if o.get("ft_char_role") and not set(o.users_collection) & set(master.users_collection):
            continue
        out.append(o)
    return out


# ---------------------------------------------------------------------------
#  3. Auto-Rig Pro (Quick Rig, Preserve mode)
# ---------------------------------------------------------------------------

def _limb(name, typ, side, **kw):
    d = {"name": name, "type": typ, "side": side, "fingers": "NONE",
         "force_up_axis": True, "force_z_axis": True, "primary_axis_auto": True,
         "weights_override": False}
    for i in range(1, 8):
        d[f"bone_0{i}"] = ""
        d[f"override_bone_0{i}"] = ""
    for f in ("thumb", "index", "middle", "ring", "pinky"):
        d[f] = ""
    d.update(kw)
    return d


def quick_rig_mapping(arm_obj):
    b = arm_obj.data.bones
    has = lambda n: n if b.get(n) else ""
    m = {
        "Spine.x": _limb("Spine.x", "SPINE", "CENTER", spine_amount=4, up_axis="X",
                         bone_01="pelvis", bone_02=has("spine_01"),
                         bone_03=has("spine_02"), bone_04=has("spine_03")),
        "Head.x": _limb("Head.x", "HEAD", "CENTER", up_axis="X",
                        neck_bones_amount=2 if b.get("neck_02") else 1,
                        bone_01=has("neck_01"), bone_02="head", bone_04=has("neck_02")),
    }
    for s, side, ax in (("l", "LEFT", "-X"), ("r", "RIGHT", "X")):
        four = all(b.get(f"{f}_04_{s}") for f in FINGERS)
        m[f"Arm.{s}"] = _limb(f"Arm.{s}", "ARM", side, up_axis=ax,
                              bone_01=has(f"clavicle_{s}"), bone_02=f"upperarm_{s}",
                              bone_04=f"lowerarm_{s}", bone_06=f"hand_{s}",
                              fingers="4" if four else "3",
                              thumb=has(f"thumb_01_{s}"), index=has(f"index_01_{s}"),
                              middle=has(f"middle_01_{s}"), ring=has(f"ring_01_{s}"),
                              pinky=has(f"pinky_01_{s}"))
        m[f"Leg.{s}"] = _limb(f"Leg.{s}", "LEG", side, up_axis="X" if s == "l" else "-X",
                              bone_01=f"thigh_{s}", bone_03=f"calf_{s}",
                              bone_05=f"foot_{s}", bone_06=has(f"ball_{s}"))
    return m


def _restore_names(before):
    for o in bpy.data.objects:
        old = before.get(o.as_pointer())
        if old and o.name != old:
            o.name = old


def _find_addon_module(suffix):
    for mod in addon_utils.modules():
        if mod.__name__.split(".")[-1] == suffix:
            enabled = addon_utils.check(mod.__name__)[1]
            return mod, enabled
    return None, False


def _view3d_override():
    """Auto-Rig Pro needs a 3D Viewport context (space_data.overlay ...)."""
    ctx = bpy.context
    if ctx.space_data is not None and ctx.space_data.type == "VIEW_3D":
        return None
    wins = [ctx.window] if ctx.window else []
    wins += [w for w in ctx.window_manager.windows if w not in wins]
    for win in wins:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                region = next(r for r in area.regions if r.type == "WINDOW")
                return dict(window=win, screen=win.screen, area=area, region=region,
                            space_data=area.spaces.active)
    raise RuntimeError("Open a 3D Viewport first (Auto-Rig Pro needs one).")


def generate_arp_rig(arm_obj, arm_mode="FK", leg_mode="IK", report=print):
    ov = _view3d_override()
    if ov is None:
        return _generate_arp_rig(arm_obj, arm_mode, leg_mode, report)
    with bpy.context.temp_override(**ov):
        return _generate_arp_rig(arm_obj, arm_mode, leg_mode, report)


def _generate_arp_rig(arm_obj, arm_mode, leg_mode, report):
    _ensure_object_mode()
    arp_mod, arp_on = _find_addon_module("auto_rig_pro")
    qr_mod, qr_on = _find_addon_module("auto_rig_pro_quick_rig")
    if not (arp_mod and arp_on):
        raise RuntimeError("Auto-Rig Pro is not installed/enabled.")
    if not (qr_mod and qr_on):
        raise RuntimeError("Auto-Rig Pro: Quick Rig is not installed/enabled.")

    _set_active(arm_obj)
    # make sure the base skeleton is in rest pose & has clean transforms
    for pb in arm_obj.pose.bones:
        pb.location = (0, 0, 0)
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.rotation_euler = (0, 0, 0)
        pb.scale = (1, 1, 1)

    # 1) limbs mapping -> Quick Rig
    path = os.path.join(tempfile.gettempdir(), "finals_quick_rig_mapping.py")
    with open(path, "w", encoding="utf8", newline="\n") as f:
        f.write(str(quick_rig_mapping(arm_obj)))
    bpy.ops.arp.quick_import_mapping("EXEC_DEFAULT", filepath=path)

    # 2) Make Rig (Preserve). The operator does its setup in invoke(); replicate it.
    qr = importlib.import_module(qr_mod.__name__ + ".auto_rig_quick")
    cls = qr.ARP_OT_quick_make_rig
    v = arp_mod.bl_info["version"]
    cls.base_armature_name = arm_obj.name
    cls.base_armature_pos = arm_obj.location.copy()
    cls.arp_ver_int = int(f"{v[0]}{v[1]}{v[2]}")
    cls.arp_light = "Light" in arp_mod.bl_info.get("name", "")
    cls.error_mess = ""
    for attr in ("fingers_ctrl", "toes_ctrl", "tail_ctrl", "spline_ctrl",
                 "meshes_parented_to_bones", "skin_dict", "orphan_bones_dict"):
        setattr(cls, attr, {})

    # ARP appends a rig called "rig" and renames any existing one, so track
    # objects by identity and restore names afterwards.
    before = {o.as_pointer(): o.name for o in bpy.data.objects}
    before_cols = {c.as_pointer() for c in bpy.data.collections}
    try:
        bpy.ops.arp.quick_make_rig("EXEC_DEFAULT", mode="PRESERVE", match_to_rig=True,
                                   arm_ik_fk=arm_mode, leg_ik_fk=leg_mode,
                                   remove_root=True, show_in_front=True,
                                   preserve_volume=False, match_origin=True,
                                   constrain_overrides=False)
    except Exception:
        # remove the half-built rig so the scene stays clean, then re-raise
        _ensure_object_mode()
        for o in [o for o in bpy.data.objects if o.as_pointer() not in before]:
            bpy.data.objects.remove(o, do_unlink=True)
        for c in [c for c in bpy.data.collections if c.as_pointer() not in before_cols]:
            bpy.data.collections.remove(c)
        for pbn in arm_obj.pose.bones:
            for c in [c for c in pbn.constraints if getattr(c, "target", 1) is None]:
                pbn.constraints.remove(c)
        _restore_names(before)
        raise
    new = [o for o in bpy.data.objects if o.as_pointer() not in before]
    rig = next((o for o in new if o.type == "ARMATURE" and o.data.bones.get("c_traj")), None)
    if rig is None:
        _restore_names(before)
        raise RuntimeError("Quick Rig did not produce a rig - see the system console.")
    rig.name = arm_obj.name + "_ARP"
    _restore_names(before)
    rig["arp_rig"] = True
    bpy.context.scene.ft_rig.arp_rig = rig
    report(f"Auto-Rig Pro control rig '{rig.name}' now drives '{arm_obj.name}'.")
    return rig


# ---------------------------------------------------------------------------
#  UI (drawn by the Finals panel when the Rigging tab is active)
# ---------------------------------------------------------------------------

class FT_RigProps(bpy.types.PropertyGroup):
    master: bpy.props.PointerProperty(
        type=bpy.types.Object, name="Body Rig",
        poll=lambda self, o: o.type == "ARMATURE",
        description="Simplified body skeleton that every piece is bound to")
    arp_rig: bpy.props.PointerProperty(
        type=bpy.types.Object, name="ARP Rig",
        poll=lambda self, o: o.type == "ARMATURE")
    hip_follow: bpy.props.FloatProperty(
        name="Hip Follows Thigh", default=0.5, min=0.0, max=1.0, subtype="FACTOR",
        description="How much of the hip-crease corrective weights move with the thigh "
                    "(0 = exactly like the original rig in Blender)")
    tidy: bpy.props.BoolProperty(
        name="Tidy Bone Orientations", default=True,
        description="Point bones at their children with clean rolls (skinning is unaffected)")
    auto_shapekeys: bpy.props.BoolProperty(
        name="Auto Apply Shapekeys", default=True,
        description="When attaching, set the body type (male / female / muscular) on the pieces and "
                    "switch on the shape keys the clothing asks for (e.g. pulling the body in under "
                    "pants) on every mesh bound to the rig")
    delete_src: bpy.props.BoolProperty(
        name="Delete Piece Armatures", default=True,
        description="Remove a piece's own game armature once it is bound to the body rig")
    arm_mode: bpy.props.EnumProperty(name="Arms", items=(("FK", "FK", ""), ("IK", "IK", "")), default="FK")
    leg_mode: bpy.props.EnumProperty(name="Legs", items=(("IK", "IK", ""), ("FK", "FK", "")), default="IK")


def _reporter(op):
    return lambda msg: (print("[Finals Rigging]", msg),
                        op.report({"WARNING"} if msg.startswith(("WARNING", "Note")) else {"INFO"}, msg))


class FT_OT_rig_simplify(bpy.types.Operator):
    """Reduce the active game armature to a standard humanoid skeleton and merge the weights"""
    bl_idname = "finals_texturer.rig_simplify"
    bl_label = "Simplify Body Rig"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, ctx):
        return ctx.active_object is not None and ctx.active_object.type == "ARMATURE"

    def execute(self, ctx):
        global HIP_FOLLOW
        HIP_FOLLOW = ctx.scene.ft_rig.hip_follow
        try:
            simplify_armature(ctx.active_object, ctx.scene.ft_rig.tidy, _reporter(self))
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        return {"FINISHED"}


class FT_OT_rig_attach(bpy.types.Operator):
    """Bind the selected pieces (armatures and/or meshes) to the simplified body rig"""
    bl_idname = "finals_texturer.rig_attach"
    bl_label = "Attach Selected Pieces"
    bl_options = {"REGISTER", "UNDO"}
    all_unbound: bpy.props.BoolProperty(default=False, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, ctx):
        return ctx.scene.ft_rig.master is not None

    def execute(self, ctx):
        global HIP_FOLLOW
        p = ctx.scene.ft_rig
        HIP_FOLLOW = p.hip_follow
        objs = unbound_pieces(p.master) if self.all_unbound else list(ctx.selected_objects)
        if not objs:
            self.report({"WARNING"}, "Nothing to attach")
            return {"CANCELLED"}
        report = _reporter(self)
        attach_pieces(p.master, objs, p.delete_src, report)
        if p.auto_shapekeys:
            apply_shape_keys(p.master, ctx.scene, report)
        return {"FINISHED"}


class FT_OT_rig_arp(bpy.types.Operator):
    """Generate an Auto-Rig Pro control rig (Quick Rig, Preserve mode) that drives the body rig"""
    bl_idname = "finals_texturer.rig_generate_arp"
    bl_label = "Generate ARP Control Rig"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, ctx):
        return ctx.scene.ft_rig.master is not None

    def execute(self, ctx):
        p = ctx.scene.ft_rig
        try:
            generate_arp_rig(p.master, p.arm_mode, p.leg_mode, _reporter(self))
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        return {"FINISHED"}


def _draw_options(col, ctx):
    p = ctx.scene.ft_rig
    col.label(text="Body")
    col.prop(p, "hip_follow")
    col.prop(p, "tidy")
    col.label(text="Clothing / gear")
    col.prop(p, "auto_shapekeys")
    col.prop(p, "delete_src")
    col.label(text="Auto-Rig Pro")
    row = col.row()
    row.prop(p, "arm_mode")
    row.prop(p, "leg_mode")


def draw(layout, ctx):
    from . import ui
    p = ctx.scene.ft_rig
    b = layout.box()
    b.label(text="1. Body", icon="ARMATURE_DATA")
    b.operator(FT_OT_rig_simplify.bl_idname, icon="MOD_DECIM")
    b.prop(p, "master")

    b = layout.box()
    b.label(text="2. Clothing / Gear", icon="MOD_CLOTH")
    row = b.row(align=True)
    row.operator(FT_OT_rig_attach.bl_idname, text="Attach Selected", icon="LINKED").all_unbound = False
    row.operator(FT_OT_rig_attach.bl_idname, text="Attach All Unbound", icon="SELECT_EXTEND").all_unbound = True
    if p.master:
        n = len(unbound_pieces(p.master))
        b.label(text=f"{n} unbound piece(s) in scene", icon="INFO" if n else "CHECKMARK")

    b = layout.box()
    b.label(text="3. Auto-Rig Pro", icon="POSE_HLT")
    b.operator(FT_OT_rig_arp.bl_idname, icon="OUTLINER_OB_ARMATURE")
    b.prop(p, "arp_rig")
    ui.draw_options(layout, ctx, "RIGGING", lambda c: _draw_options(c, ctx))


CLASSES = (FT_RigProps, FT_OT_rig_simplify, FT_OT_rig_attach, FT_OT_rig_arp)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.ft_rig = bpy.props.PointerProperty(type=FT_RigProps)


def unregister():
    del bpy.types.Scene.ft_rig
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
