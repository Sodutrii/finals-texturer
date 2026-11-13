"""FT Quick Install (dev helper, temporary).

Install this file once (Edit > Preferences > Add-ons > Install from Disk… > this .py). Then the
3D view sidebar tab "FT Dev" (or F3 > "Install Latest Finals Texturer") installs the newest
finals_texturer-<version>.zip from FOLDER over the installed one and re-enables it, in one click.
"""

bl_info = {
    "name": "FT Quick Install (dev)",
    "author": "Finals Texturer contributors",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "location": "3D View > Sidebar > FT Dev",
    "description": "One-click install of the newest Finals Texturer zip from the project folder",
    "category": "Development",
}

import glob
import os
import re
import sys

import addon_utils
import bpy

FOLDER = r"D:\BlenderStuff\TheFinals\finals-texturer"


def _version(path):
    m = re.search(r"-(\d+)\.(\d+)\.(\d+)\.zip$", path)
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def newest_zip():
    zips = glob.glob(os.path.join(FOLDER, "finals_texturer-*.zip"))
    return max(zips, key=_version) if zips else ""


def installed_module():
    """Module name of the installed Finals Texturer ('bl_ext.<repo>.finals_texturer'), or ''."""
    for m in addon_utils.modules(refresh=False):
        if m.__name__ == "finals_texturer" or m.__name__.endswith(".finals_texturer"):
            return m.__name__
    return ""


def installed_version():
    for m in addon_utils.modules(refresh=False):
        if m.__name__ == "finals_texturer" or m.__name__.endswith(".finals_texturer"):
            v = addon_utils.module_bl_info(m).get("version")
            return ".".join(map(str, v)) if v else ""
    return ""


def _save_prefs(mod):
    """The add-on's preference values (Exports folder, import size…) as a dict."""
    addon = bpy.context.preferences.addons.get(mod)
    prefs = getattr(addon, "preferences", None)
    if prefs is None:
        return {}
    out = {}
    for p in prefs.bl_rna.properties:
        if p.identifier in ("rna_type", "bl_idname") or p.is_readonly or p.type in ("POINTER", "COLLECTION"):
            continue
        val = getattr(prefs, p.identifier)
        out[p.identifier] = val[:] if hasattr(val, "__len__") and not isinstance(val, str) else val
    return out


def _restore_prefs(mod, values):
    addon = bpy.context.preferences.addons.get(mod)
    prefs = getattr(addon, "preferences", None)
    if prefs is None:
        return 0
    n = 0
    for k, v in values.items():
        try:
            setattr(prefs, k, v)
            n += 1
        except (AttributeError, TypeError, ValueError):
            pass            # setting no longer exists / changed type in the new version
    return n


class FTDEV_OT_install_latest(bpy.types.Operator):
    """Install the newest finals_texturer-<version>.zip from the project folder and re-enable it"""
    bl_idname = "ftdev.install_latest"
    bl_label = "Install Latest Finals Texturer"

    def execute(self, context):
        zp = newest_zip()
        if not zp:
            self.report({"ERROR"}, f"No finals_texturer-*.zip in {FOLDER}")
            return {"CANCELLED"}
        mod = installed_module()
        repo = mod.split(".")[1] if mod.startswith("bl_ext.") else "user_default"
        saved = _save_prefs(mod) if mod else {}
        if mod:
            try:
                addon_utils.disable(mod, default_set=True)
            except Exception as e:      # noqa: BLE001 - keep going, the install replaces it anyway
                print("[FT Quick Install] disable:", e)
            # drop the old code so the new version's modules are imported fresh
            for k in [k for k in sys.modules if k == mod or k.startswith(mod + ".")]:
                del sys.modules[k]
        try:
            bpy.ops.extensions.package_install_files(filepath=zp, repo=repo, enable_on_install=True,
                                                     overwrite=True)
        except TypeError:
            bpy.ops.extensions.package_install_files(filepath=zp, repo=repo, enable_on_install=True)
        except RuntimeError as e:
            self.report({"ERROR"}, f"Install failed: {e}")
            return {"CANCELLED"}
        new_mod = installed_module() or mod
        restored = _restore_prefs(new_mod, saved) if saved else 0
        if saved:
            bpy.ops.wm.save_userpref()      # keep them across restarts, like a Preferences install
        self.report({"INFO"}, f"Installed {os.path.basename(zp)}" + (f", kept {restored} setting(s)" if saved else ""))
        return {"FINISHED"}


class FTDEV_PT_panel(bpy.types.Panel):
    bl_label = "FT Quick Install"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "FT Dev"

    def draw(self, context):
        lay = self.layout
        zp = newest_zip()
        col = lay.column(align=True)
        col.label(text=f"Newest zip: {os.path.basename(zp) if zp else 'none'}")
        col.label(text=f"Installed: {installed_version() or 'not installed'}")
        lay.operator(FTDEV_OT_install_latest.bl_idname, icon="IMPORT")


CLASSES = (FTDEV_OT_install_latest, FTDEV_PT_panel)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
