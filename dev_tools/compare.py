"""Usage: blender -b --python compare.py -- <mesh path rel. to Characters> <skin name|AUTO|NONE> <out name> [yaw_deg] [mesh_index]"""
import bpy, sys, importlib, os, math, mathutils, numpy as np, glob
args = sys.argv[sys.argv.index("--") + 1:]
rel, skin_name, out = args[0], args[1], args[2]
yaw = float(args[3]) if len(args) > 3 else 30.0
ROOT = r"C:/Users/bisch/Desktop/New folder"
EXP = r"D:/BlenderStuff/Fmodel/Output/Exports"
CH = EXP + "/Discovery/Content/Discovery/Characters/"
SP = os.environ["SP"]
sys.path.insert(0, ROOT)
importlib.import_module("add-on-io-scene-psk-psa-v9_1_2").register()
import finals_texturer; finals_texturer.register()
from finals_texturer import importer
from finals_texturer.ue_assets import Log, find_skins
mesh_path = CH + rel
mdir = os.path.dirname(mesh_path)
skins = dict(find_skins(mdir))
choice = skins.get(skin_name, skin_name)
for o in list(bpy.data.objects): bpy.data.objects.remove(o)
if os.environ.get('FT_NUMBER'): bpy.context.scene.ft_number = int(os.environ['FT_NUMBER'])
log = Log()
objs = importer.import_psk(mesh_path, log)
meshes = [o for o in objs if o.type == "MESH"]
for m in meshes: importer.apply_to_object(m, EXP, choice, log)
for l in log.lines:
    if "Texture not found" in l: continue
    print("LOG", l)
print("LOG missing textures:", sum("Texture not found" in l for l in log.lines))
sc = bpy.context.scene
sc.render.engine = 'BLENDER_EEVEE'; sc.render.resolution_x = sc.render.resolution_y = 512
sc.render.film_transparent = False
w = bpy.data.worlds.new("w"); sc.world = w; w.use_nodes = True
w.node_tree.nodes["Background"].inputs[0].default_value = (0.62, 0.66, 0.72, 1)
w.node_tree.nodes["Background"].inputs[1].default_value = 1.0
sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN")); sc.collection.objects.link(sun)
sun.data.energy = 2.0; sun.rotation_euler = (math.radians(45), 0, math.radians(yaw + 20))
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); sc.collection.objects.link(cam); sc.camera = cam
pts = [m.matrix_world @ mathutils.Vector(c) for m in meshes for c in m.bound_box]
half = args[4] if len(args) > 4 else ""
if half in ("left", "right"):
    vs = [m.matrix_world @ v.co for m in meshes for v in m.data.vertices]
    cx = sum(v.x for v in vs) / len(vs)
    pts = [v for v in vs if (v.x > cx) == (half == "right")]
lo = mathutils.Vector([min(p[i] for p in pts) for i in range(3)]); hi = mathutils.Vector([max(p[i] for p in pts) for i in range(3)])
ctr = (lo + hi) / 2; size = max(hi - lo)
cam.data.type = 'ORTHO'; cam.data.ortho_scale = size * 1.15
a = math.radians(yaw)
cam.location = ctr + mathutils.Vector((size*3*math.sin(a), -size*3*math.cos(a), size*0.5))
pitch = float(args[5]) if len(args) > 5 else 80.0
cam.location = ctr + mathutils.Vector((size*3*math.sin(a)*math.sin(math.radians(pitch)), -size*3*math.cos(a)*math.sin(math.radians(pitch)), size*3*math.cos(math.radians(pitch))))
cam.rotation_euler = (math.radians(pitch), 0, a)
sc.render.filepath = os.path.join(SP, out + "_render.png"); bpy.ops.render.render(write_still=True)
# side-by-side with the store image
ui = sorted(glob.glob(os.path.join(os.path.dirname(choice) if os.path.isfile(choice) else mdir, "T_UI_*.png")))
def load(p):
    im = bpy.data.images.load(p); w_, h_ = im.size
    a_ = np.empty(w_*h_*4, dtype=np.float32); im.pixels.foreach_get(a_); a_ = a_.reshape(h_, w_, 4)
    if (w_, h_) != (512, 512):
        ys = (np.arange(512) * h_ / 512).astype(int); xs = (np.arange(512) * w_ / 512).astype(int); a_ = a_[ys][:, xs]
    return a_
r = load(sc.render.filepath)
u = load(ui[0]) if ui else np.zeros_like(r)
if ui: u[..., :3] = u[..., :3] * u[..., 3:4] + 0.62 * (1 - u[..., 3:4]); u[..., 3] = 1
both = np.concatenate([r, u], axis=1)
img = bpy.data.images.new(out, 1024, 512); img.pixels.foreach_set(both.ravel())
img.filepath_raw = os.path.join(SP, out + "_cmp.png"); img.file_format = 'PNG'; img.save()
print("LOG saved", out, "ui:", os.path.basename(ui[0]) if ui else None)

bpy.ops.wm.save_as_mainfile(filepath=os.path.join(SP, out + ".blend"))
