"""Finals Texturer: build Blender materials for The Finals clothing exported with FModel."""

bl_info = {
    "name": "Finals Texturer",
    "author": "Finals Texturer contributors",
    "version": (0, 14, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Finals",
    "description": "Imports The Finals clothing (.psk) and rebuilds its layered materials from FModel exports",
    "category": "Import-Export",
}

import os

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from . import character, clothing_tab, exporter, importer, library_ui, rigging, search, ui, weapons
from .ue_assets import AssetFinder, Log

ADDON_ID = __package__ or __name__


def _prefs(context):
    context = context or bpy.context
    addon = context.preferences.addons.get(ADDON_ID)
    return addon.preferences if addon else None


def _root(context):
    p = _prefs(context)
    root = p.export_root if p else ""
    return bpy.path.abspath(root) if root else ""


class FT_Preferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    export_root: StringProperty(
        name="FModel Export Folder",
        description="Folder that contains your FModel exports (usually Output/Exports). "
                    "Parent materials and shared textures are looked up here",
        subtype="DIR_PATH",
    )
    import_size: EnumProperty(
        name="Import Size",
        items=(("REAL", "Real size (metres)", "Scale imports to real-world size: 1 Blender unit = 1 m, "
                "a Medium body is 1.68 m. The scale is applied to the mesh and bones (object scale stays 1)"),
               ("GAME", "Game units (centimetres)", "Keep the game's units: 1 Blender unit = 1 cm, "
                "a Medium body is 168 units tall")),
        default="GAME",
    )

    def draw(self, context):
        self.layout.prop(self, "export_root")
        self.layout.prop(self, "import_size")


def _set_numbers(self, context):
    """Push the panel's number into every material's number-decal value."""
    for mat in bpy.data.materials:
        tree = mat.node_tree
        n = tree.nodes.get("FT_Number") if tree else None
        if n is not None:
            n.outputs[0].default_value = float(self.ft_number)


def _flat_warning(log):
    """Material files that FModel's mesh export flattened: name the folders to re-save."""
    flat = getattr(log, "flat_jsons", None)
    if not flat:
        return
    root = _root(bpy.context)
    folders = sorted({os.path.relpath(os.path.dirname(p), root).replace("\\", "/").split("Content/Discovery/", 1)[-1]
                      for p in flat})
    log.warn("Some material files were overwritten by FModel's model export (no parent material in them). "
             "In FModel, run 'Save Folder's Packages Properties (.json)' again on: " + ", ".join(folders))


def _report(op, log, done):
    _flat_warning(log)
    importer.write_log(log)
    msg = f"Built {done} material(s)"
    if log.warnings:
        first = next((l[len("WARNING: "):].strip() for l in log.lines if l.startswith("WARNING: ")), "")
        msg += f" with {log.warnings} warning(s). First: {first}  (Finals panel > Show Log for all)"
        op.report({"WARNING"}, msg)
    else:
        op.report({"INFO"}, msg)


class FT_OT_import(bpy.types.Operator, ImportHelper):
    """Import a The Finals .psk and build its materials"""
    bl_idname = "finals_texturer.import_psk"
    bl_label = "Import Finals Item"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".psk"
    filter_glob: StringProperty(default="*.psk;*.pskx", options={"HIDDEN"})
    skin: StringProperty(
        name="Skin",
        description="'AUTO' picks the first skin next to the mesh, 'NONE' keeps base materials, "
                    "or give the path of a DA_*.json / MI_*.json skin",
        default="AUTO",
    )
    import_parts: BoolProperty(
        name="Import whole skin",
        description="Also import the other meshes the skin is made of (pins, spikes, a hoodie, …) "
                    "and give them the same skin",
        default=True,
    )

    def invoke(self, context, event):
        # Always start the file browser in the Exports folder from the panel.
        root = _root(context)
        if root and os.path.isdir(root):
            self.filepath = os.path.join(root, "")
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        log = Log()
        try:
            objs = importer.import_psk(self.filepath, log)
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        done = 0
        for o in objs:
            done += importer.apply_to_object(o, _root(context), self.skin, log)
        if self.import_parts:
            main = next((o for o in objs if o.type == "MESH"), None)
            if main is not None:
                try:
                    extra = importer.import_skin_parts(main, _root(context), self.skin, log)
                except RuntimeError as e:
                    log.warn(str(e))
                    extra = []
                done += sum(1 for o in extra if o.type == "MESH")
        _report(self, log, done)
        return {"FINISHED"}


class FT_OT_rescan(bpy.types.Operator):
    """Re-read the export folder: item lists and what's exported (use after exporting more from FModel)"""
    bl_idname = "finals_texturer.rescan"
    bl_label = "Rescan"

    def execute(self, context):
        AssetFinder.clear_cache()
        character.clear_cache()
        weapons.clear_cache()
        clothing_tab.clear_cache()
        exporter.clear_cache()
        self.report({"INFO"}, "Re-read the export folder")
        return {"FINISHED"}


class FT_OT_show_log(bpy.types.Operator):
    """Open the Finals Texturer Log in its own window"""
    bl_idname = "finals_texturer.show_log"
    bl_label = "Show Log"

    def execute(self, context):
        txt = bpy.data.texts.get("Finals Texturer Log")
        if txt is None:
            self.report({"INFO"}, "No log yet: import an item or apply materials first")
            return {"CANCELLED"}
        # Reuse an open Text Editor if there is one, otherwise open a new window.
        for win in context.window_manager.windows:
            for area in win.screen.areas:
                if area.type == "TEXT_EDITOR":
                    area.spaces.active.text = txt
                    return {"FINISHED"}
        before = set(context.window_manager.windows)
        bpy.ops.wm.window_new()
        new = [w for w in context.window_manager.windows if w not in before]
        if not new:
            self.report({"WARNING"}, "Couldn't open a window; open a Text Editor and pick 'Finals Texturer Log'")
            return {"CANCELLED"}
        area = max(new[0].screen.areas, key=lambda a: a.width * a.height)
        area.type = "TEXT_EDITOR"
        area.spaces.active.text = txt
        return {"FINISHED"}


class FT_PT_panel(bpy.types.Panel):
    bl_label = "Finals Texturer"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Finals"

    def draw(self, context):
        lay = self.layout
        # Shared by all tabs: every import reads from the Exports folder.
        p = _prefs(context)
        root = _root(context)
        ok = ui.draw_root(lay, p, root) if p else False
        lay.row().prop(context.window_manager, "ft_tab", expand=True)
        tab = context.window_manager.ft_tab
        if tab == "RIGGING":
            rigging.draw(lay, context)
        elif not ok:
            lay.label(text="Set the Exports folder above first", icon="INFO")
        elif tab == "CHARACTER":
            character.draw(lay, context)
        elif tab == "WEAPONS":
            weapons.draw(lay, context)
        else:
            clothing_tab.draw(lay, context)
        ui.draw_footer(lay)


def _menu_import(self, context):
    self.layout.operator(FT_OT_import.bl_idname, text="The Finals item (.psk)")


CLASSES = (FT_Preferences, FT_OT_import, FT_OT_rescan, FT_OT_show_log, FT_PT_panel)


def register():
    ui.register()
    exporter.register()
    search.register()
    for c in CLASSES:
        bpy.utils.register_class(c)
    library_ui.register(_root, ADDON_ID, _report)
    clothing_tab.register(_root, _report)
    character.register(_root, _report)
    weapons.register(_root, _report)
    rigging.register()
    bpy.types.WindowManager.ft_tab = EnumProperty(
        name="Tab",
        items=(("CHARACTER", "Character", "Body, face and body type", "USER", 0),
               ("CLOTHING", "Clothing", "Import clothing and build its materials", "MATCLOTH", 1),
               ("WEAPONS", "Weapons", "Import weapons with their skins", "TOOL_SETTINGS", 3),
               ("RIGGING", "Rigging", "Simplify the game rig, attach clothing, Auto-Rig Pro controls",
                "ARMATURE_DATA", 2)))
    bpy.types.Scene.ft_number = IntProperty(
        name="Number decals", min=0, max=99, default=8, update=_set_numbers,
        description="Number shown by number decals (jersey numbers etc.). The game sets it per "
                    "player; skins don't store it. Applies to all imported Finals materials")
    bpy.types.TOPBAR_MT_file_import.append(_menu_import)


def unregister():
    clothing_tab.unregister()
    library_ui.unregister()
    rigging.unregister()
    character.unregister()
    weapons.unregister()
    del bpy.types.WindowManager.ft_tab
    bpy.types.TOPBAR_MT_file_import.remove(_menu_import)
    del bpy.types.Scene.ft_number
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
    search.unregister()
    exporter.unregister()
    ui.unregister()
