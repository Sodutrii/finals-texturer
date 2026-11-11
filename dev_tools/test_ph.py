import sys, types, importlib.util, os
sys.stdout.reconfigure(encoding="utf-8")
sys.modules["bpy"] = types.ModuleType("bpy")
spec = importlib.util.spec_from_file_location("finals_texturer", r"C:/Users/bisch/Desktop/New folder/finals_texturer/__init__.py", submodule_search_locations=[r"C:/Users/bisch/Desktop/New folder/finals_texturer"])
sys.modules["finals_texturer"] = importlib.util.module_from_spec(spec)
from finals_texturer import library
d = os.path.join(sys.argv[1], "libstore_ph"); os.makedirs(d, exist_ok=True)
library.storage_dir = lambda package=None: d
lib = library.scan(r"D:/BlenderStuff/Fmodel/Output/Exports"); library.save(lib)
lib = library.load(r"D:/BlenderStuff/Fmodel/Output/Exports")
print("placeholders:", sum(1 for e in lib["entries"] if e.get("placeholder")), "still unnamed:", sum(1 for e in lib["entries"] if not e["name"] and not e.get("placeholder")))
for q in ("beret", "beret cotton", "kangaroo hoodie"):
    print(q, "->", [library.display_name(e) for e in library.search(lib, q)][:9])
