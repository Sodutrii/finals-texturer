import bpy, sys
for m in bpy.data.materials:
    if not m.node_tree: continue
    b = [n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED']
    print("MAT", m.name, m.get("ft_shader"), getattr(m, "surface_render_method", None), "backface", m.use_backface_culling)
    for n in b:
        print("   linked:", [i.name for i in n.inputs if i.is_linked], "alpha", n.inputs["Alpha"].default_value, "transmission", n.inputs["Transmission Weight"].default_value)
