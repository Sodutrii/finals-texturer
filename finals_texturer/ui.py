"""Shared panel pieces, so every tab looks and behaves the same:

  pick (search + dropdowns) → store image → status box ("what to export") →
  action buttons → options (fold-out) → footer (Show Log, Rescan)

Also: the Exports-folder check next to the folder field and the "Check Setup"
operator (which shared folders every import needs).
"""

import os

import bpy
import bpy.utils.previews
from bpy.props import BoolProperty, StringProperty

READY_ICON = {2: "CHECKMARK", 1: "INFO", 0: "ERROR"}

# Folders every kind of import reads from (relative to the Exports folder).
# (path, what it's for, which tabs need it)
SHARED_FOLDERS = (
    ("Discovery/Content/Discovery/MaterialLibrary", "Master materials, shared textures, gradients", "all"),
    ("RemappedPlugins/EmbarkScript/Content/MaterialLibrary", "Clothing templates (null / default textures)", "Clothing"),
    ("Discovery/Content/Discovery/Characters/Heads/Shared", "Face, eye and teeth masters and textures", "Character"),
    ("Discovery/Content/Discovery/Characters/Body", "Bodies (meshes, skin materials)", "Character"),
    ("Discovery/Content/Pioneer", "Eye iris / sclera textures, eyelash master", "Character"),
    ("Discovery/Content/Discovery/Items/Shared", "Weapon detail textures, shared weapon parts", "Weapons"),
    ("Discovery/Content/Discovery/Items/Firearms/Shared", "Shared firearm parts and materials", "Weapons"),
)

_previews = {"coll": None}


# ── Exports folder ───────────────────────────────────────────────────────────

def check_root(root):
    """(ok, message) for the Exports folder field."""
    if not root:
        return False, "Pick FModel's Output/Exports folder"
    if not os.path.isdir(root):
        return False, "Folder doesn't exist"
    if os.path.isdir(os.path.join(root, "Discovery", "Content")):
        return True, "Exports folder found"
    if os.path.basename(os.path.normpath(root)) == "Content" and os.path.isdir(os.path.join(root, "Discovery")):
        return False, "Pick the folder two levels up (the one that contains 'Discovery/Content')"
    if os.path.isdir(os.path.join(root, "Exports", "Discovery")):
        return False, "Pick the 'Exports' folder inside this one"
    return False, "No 'Discovery/Content' inside: pick FModel's Output/Exports folder"


def draw_root(layout, prefs, root):
    row = layout.row(align=True)
    row.prop(prefs, "export_root", text="Exports")
    ok, msg = check_root(root)
    row.label(text="", icon="CHECKMARK" if ok else "ERROR")
    if not ok:
        layout.label(text=msg, icon="BLANK1")
    return ok


# ── store image preview ──────────────────────────────────────────────────────

def preview_icon(path):
    """Icon id of a store image (cached), or 0."""
    if not path or not os.path.isfile(path):
        return 0
    coll = _previews["coll"]
    if coll is None:
        coll = _previews["coll"] = bpy.utils.previews.new()
    key = os.path.normcase(path)
    if key not in coll:
        coll.load(key, path, "IMAGE")
    return coll[key].icon_id


def draw_preview(layout, path, scale=7.0):
    icon = preview_icon(path)
    if icon:
        layout.template_icon(icon_value=icon, scale=scale)
    return bool(icon)


# ── status box ───────────────────────────────────────────────────────────────

def item_root(folder):
    """The folder FModel should export for a listed folder: the item / weapon / face it belongs to
    (FModel exports a folder with its subfolders). 'Characters/Mexico/Assets/AncientRings/Skins/Metal'
    → 'Characters/Mexico/Assets/AncientRings'."""
    parts = folder.replace("\\", "/").strip("/").split("/")
    if parts[0] == "Characters":
        if len(parts) > 3 and parts[2] == "Assets":
            return "/".join(parts[:4])
        if len(parts) > 2 and parts[1] == "Heads":
            return "/".join(parts[:3])
        return "/".join(parts[:2])
    if parts[0] == "Items" and len(parts) > 2:
        n = 4 if parts[1] == "Firearms" and parts[2] != "Shared" else 3
        return "/".join(parts[:n])
    return "/".join(parts)


def draw_status(layout, level, text, groups=None, export=None, what=""):
    """Ready level + text, then what to export grouped by kind, an Export Now button (export: the
    game paths for the bundled exporter, see exporter.py) and a Copy Path button for each folder
    FModel should export (the missing files' item folders, ready to paste into FModel's search)."""
    from . import exporter
    box = layout.box()
    col = box.column(align=True)
    col.label(text=text, icon=READY_ICON.get(level, "BLANK1"))
    if exporter.running() or (export and level < 2):
        exporter.draw(box, export if level < 2 else None, what)
    if not groups:
        return
    if isinstance(groups, (list, tuple)):
        groups = {"Folders": list(groups)}
    groups = {k: v for k, v in groups.items() if v}
    if not groups:
        return
    col.separator()
    col.label(text="Missing (or export in FModel):" if export and exporter.bundled() else "Export in FModel:",
              icon="EXPORT")
    for kind, folders in groups.items():
        col.label(text=f"{kind}:")
        for f in folders[:5]:
            col.label(text=f"   {f}")
        if len(folders) > 5:
            col.label(text=f"   … and {len(folders) - 5} more")
    roots = list(dict.fromkeys(item_root(f) for v in groups.values() for f in v))
    for r in roots[:6]:
        op = box.operator(FT_OT_copy_text.bl_idname, text="Copy Path" if len(roots) == 1 else f"Copy: {r}",
                          icon="COPYDOWN")
        op.text = r


def game_folder(root, abs_dir):
    """Exports-relative folder in FModel's search form: 'Characters/Mexico/Assets/AncientRings'."""
    rel = os.path.relpath(abs_dir, root).replace("\\", "/")
    return rel.split("Content/Discovery/", 1)[-1]


def split_folders(meshes=(), textures=(), jsons=()):
    """The usual groups for draw_status."""
    out = {}
    if meshes:
        out["Meshes (.psk, export as model)"] = list(dict.fromkeys(meshes))
    if textures:
        out["Textures (.png)"] = list(dict.fromkeys(textures))
    if jsons:
        out["Materials (re-run 'Save Properties (.json)')"] = list(dict.fromkeys(jsons))
    return out


# ── options fold-out and footer ──────────────────────────────────────────────

def draw_options(layout, context, key, draw_fn):
    """A fold-out 'Options' section; key = which tab's open/closed state."""
    wm = context.window_manager
    prop = f"ft_opts_{key}"
    box = layout.box()
    open_ = getattr(wm, prop, False)
    box.prop(wm, prop, text="Options", icon="DOWNARROW_HLT" if open_ else "RIGHTARROW", emboss=False)
    if open_:
        draw_fn(box.column())


def draw_footer(layout):
    row = layout.row(align=True)
    row.operator("finals_texturer.show_log", icon="TEXT")
    row.operator("finals_texturer.rescan", icon="FILE_REFRESH")
    row.operator(FT_OT_check_setup.bl_idname, text="Check Setup", icon="CHECKBOX_HLT")


def draw_search(layout, scene, prop, hint):
    layout.prop(scene, prop, text="", icon="VIEWZOOM", placeholder=hint) \
        if bpy.app.version >= (4, 1, 0) else layout.prop(scene, prop, text="", icon="VIEWZOOM")


# ── operators ────────────────────────────────────────────────────────────────

class FT_OT_copy_text(bpy.types.Operator):
    """Copy the item's folder to the clipboard: paste it into FModel's search and export the
    folder (FModel includes its subfolders)"""
    bl_idname = "finals_texturer.copy_text"
    bl_label = "Copy Path"
    text: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        context.window_manager.clipboard = self.text
        self.report({"INFO"}, "Copied to the clipboard")
        return {"FINISHED"}


class FT_OT_show_image(bpy.types.Operator):
    """Open the store image in an Image Editor"""
    bl_idname = "finals_texturer.show_image"
    bl_label = "Store Image"
    path: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        if not self.path or not os.path.isfile(self.path):
            self.report({"WARNING"}, "Store image isn't exported (export the skin folder's textures in FModel)")
            return {"CANCELLED"}
        img = bpy.data.images.load(self.path, check_existing=True)
        for win in context.window_manager.windows:
            for area in win.screen.areas:
                if area.type == "IMAGE_EDITOR":
                    area.spaces.active.image = img
                    return {"FINISHED"}
        before = set(context.window_manager.windows)
        bpy.ops.wm.window_new()
        new = [w for w in context.window_manager.windows if w not in before]
        if new:
            area = max(new[0].screen.areas, key=lambda a: a.width * a.height)
            area.type = "IMAGE_EDITOR"
            area.spaces.active.image = img
        return {"FINISHED"}


def setup_report(root):
    """[(ok, path, what, tabs)] for the shared folders."""
    out = []
    for rel, what, tabs in SHARED_FOLDERS:
        p = os.path.join(root, *rel.split("/"))
        ok = os.path.isdir(p) and any(files for _d, _s, files in os.walk(p))
        out.append((ok, rel, what, tabs))
    return out


class FT_OT_check_setup(bpy.types.Operator):
    """Check that the shared folders every import needs are exported (master materials,
    shared textures, heads, bodies, weapon parts)"""
    bl_idname = "finals_texturer.check_setup"
    bl_label = "Check Setup"

    def execute(self, context):
        return {"FINISHED"}

    def invoke(self, context, event):
        from . import exporter
        exporter.check(refresh=True)
        return context.window_manager.invoke_popup(self, width=520)

    def draw(self, context):
        from . import _root, exporter
        root = _root(context)
        lay = self.layout
        ok, msg = check_root(root)
        lay.label(text=f"Exports folder: {msg}", icon="CHECKMARK" if ok else "ERROR")
        if exporter.bundled():
            st = exporter.check()
            if st.get("ok"):
                lay.label(text=f"Export Now: ready (FModel setup for {st.get('game_dir', 'The Finals')})",
                          icon="CHECKMARK")
            else:
                col = lay.column(align=True)
                col.label(text="Export Now: not ready", icon="ERROR")
                for p in st.get("problems", []):
                    col.label(text=f"   {p}")
        if not ok:
            return
        rep = setup_report(root)
        missing = [r for r in rep if not r[0]]
        lay.label(text="All shared folders are exported" if not missing
                  else f"{len(missing)} shared folder(s) missing: export them in FModel",
                  icon="CHECKMARK" if not missing else "INFO")
        col = lay.column(align=True)
        for ok_, rel, what, tabs in rep:
            col.label(text=f"{rel}   ({tabs}: {what})", icon="CHECKMARK" if ok_ else "ERROR")
        col.separator()
        col.label(text="In FModel, right-click a folder: Save Folder's Packages Properties (.json),")
        col.label(text="... Textures (.png) and ... Models (.psk). Export models first, then properties:")
        col.label(text="the model export overwrites material .json files with a simpler format.")
        if missing:
            op = lay.operator(FT_OT_copy_text.bl_idname, text="Copy Missing Paths", icon="COPYDOWN")
            op.text = "\n".join(r[1] for r in missing)


CLASSES = (FT_OT_copy_text, FT_OT_check_setup, FT_OT_show_image)
TABS = ("CHARACTER", "CLOTHING", "WEAPONS", "RIGGING")


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    for t in TABS:
        setattr(bpy.types.WindowManager, f"ft_opts_{t}", BoolProperty(name="Options", default=False))


def unregister():
    for t in TABS:
        delattr(bpy.types.WindowManager, f"ft_opts_{t}")
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
    if _previews["coll"] is not None:
        bpy.utils.previews.remove(_previews["coll"])
        _previews["coll"] = None
