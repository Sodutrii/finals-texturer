import bpy, os, math, mathutils, sys
args = sys.argv[sys.argv.index("--") + 1:]
x, y, z, yaw, scale, out = (float(a) for a in args[:5]) + (args[5],) if False else (float(args[0]), float(args[1]), float(args[2]), float(args[3]), float(args[4]), args[5])
sc = bpy.context.scene; cam = sc.camera
a = math.radians(yaw)
cam.data.ortho_scale = scale
cam.location = mathutils.Vector((x, y, z)) + mathutils.Vector((200*math.sin(a), -200*math.cos(a), 0))
cam.rotation_euler = (math.radians(90), 0, a)
sc.render.filepath = os.path.join(os.environ["SP"], out + ".png"); bpy.ops.render.render(write_still=True)
