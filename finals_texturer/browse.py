"""Store-image browser: the image button next to every tab's search box opens a popup with the store
images (T_UI_*) of the current search results, so an item can be found from a picture of it.

Images already in the Exports folder are used as they are; the others are fetched with the bundled
exporter (`ft_exporter export --only`: just the image, no JSON, nothing else) into the add-on's own
cache (extension user folder / store_images), never into the Exports folder. Only the current search's
results are shown (at most MAX_RESULTS), a page at a time.

Each tab registers an image function with search.add_tab(image=...): fn(context, ident) ->
(local PNG or "", game path for the exporter or ""). A game path may end in "<prefix>*" (faces: the
face folder's T_UI_* file, whose exact name isn't recorded).
"""

import json
import os
import shutil
import subprocess

import bpy
import bpy.utils.previews
from bpy.props import IntProperty, StringProperty

from . import exporter, search

CACHE_LIMIT = 200 * 1024 * 1024    # bytes; the least recently shown images go first
PER_PAGE = 20
COLUMNS = 4
MAX_RESULTS = 120

_previews = {"coll": None}
_state = {"tab": "", "items": [], "missing": 0}     # items: [(ident, name, detail, png or "")]
_index = {"root": "", "map": None}                   # game path (Exports-relative, no extension) -> file name
_size = {"bytes": None}                              # cache size, measured lazily (Preferences shows it)


def cache_root():
    try:
        return bpy.utils.extension_path_user(__package__, path="store_images", create=True)
    except (ValueError, TypeError, AttributeError):
        # loaded outside the extension system (dev scripts)
        d = os.path.join(bpy.app.tempdir or os.path.expanduser("~"), "finals_texturer_store_images")
        os.makedirs(d, exist_ok=True)
        return d


def _rel(game):
    """'/Game/Discovery/X/T_UI_A.T_UI_A' or 'Discovery/Content/Discovery/X/T_UI_A' -> 'Discovery/Content/Discovery/X/T_UI_A'."""
    g = game.replace("\\", "/")
    if g.startswith("/Game/"):
        g = "Discovery/Content/" + g[len("/Game/"):]
    head, _sep, tail = g.rpartition("/")
    if "." in tail:
        tail = tail.split(".", 1)[0]
    return f"{head}/{tail}" if head else tail


def _long(path):
    """Windows long-path form: the exporter writes the game's deep folder layout (often > 260 characters,
    which Blender's Python can't open otherwise)."""
    path = os.path.abspath(path)
    return "\\\\?\\" + path if os.name == "nt" and not path.startswith("\\\\?\\") else path


def _load_index():
    root = cache_root()
    if _index["root"] != root or _index["map"] is None:
        try:
            with open(os.path.join(root, "index.json"), encoding="utf-8") as fh:
                _index["map"] = json.load(fh)
        except (OSError, ValueError):
            _index["map"] = {}
        _index["root"] = root
    return _index["map"]


def _save_index():
    try:
        with open(os.path.join(cache_root(), "index.json"), "w", encoding="utf-8") as fh:
            json.dump(_index["map"] or {}, fh)
    except OSError:
        pass


def cached_file(game):
    """The cached PNG for a game path (pattern paths "<folder>/<prefix>*": the first match), or "".
    The cache is flat (store_images/<image name>.png) with index.json mapping game paths to names."""
    rel = _rel(game)
    idx = _load_index()
    if rel.endswith("*"):
        name = next((v for k, v in sorted(idx.items()) if k.startswith(rel[:-1])), "")
    else:
        name = idx.get(rel, "")
    path = os.path.join(cache_root(), name) if name else ""
    return path if path and os.path.isfile(path) else ""


def _touch(path):
    """Mark a cached image as just used (the size limit removes the least recently used first)."""
    try:
        os.utime(path, None)
    except OSError:
        pass


def _cached_pngs():
    root = cache_root()
    out = []
    try:
        for e in os.scandir(root):
            if e.is_file() and e.name.lower().endswith(".png"):
                st = e.stat()
                out.append((st.st_mtime, st.st_size, e.path))
    except OSError:
        pass
    return out


def cache_size():
    """Bytes in the store-image cache (measured once, then kept up to date)."""
    if _size["bytes"] is None:
        _size["bytes"] = sum(sz for _t, sz, _p in _cached_pngs())
    return _size["bytes"]


def _forget(paths):
    """Drop index entries pointing at deleted files."""
    names = {os.path.basename(p) for p in paths}
    idx = _load_index()
    for k in [k for k, v in idx.items() if v in names]:
        del idx[k]
    _save_index()


def trim_cache(limit=CACHE_LIMIT):
    """Delete the least recently used images until the cache is at most `limit` bytes."""
    files = sorted(_cached_pngs())
    total = sum(sz for _t, sz, _p in files)
    gone = []
    for _t, sz, path in files:
        if total <= limit:
            break
        try:
            os.remove(path)
            total -= sz
            gone.append(path)
        except OSError:
            pass
    if gone:
        _forget(gone)
    _size["bytes"] = total
    return len(gone)


def clear_cache():
    """Delete every cached store image. Returns how many were removed."""
    gone = []
    for _t, _sz, path in _cached_pngs():
        try:
            os.remove(path)
            gone.append(path)
        except OSError:
            pass
    _index["map"] = {}
    _save_index()
    _size["bytes"] = None
    coll = _previews["coll"]
    if coll is not None:
        coll.clear()
    return len(gone)


def _fetch(games):
    """Fetch store images into the cache with the bundled exporter. Returns an error text or ""."""
    if not games:
        return ""
    if not exporter.bundled():
        return "The exporter isn't available on this system (Windows only)"
    if not exporter.ready():
        return "The exporter isn't set up (see Check Setup)"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    root = cache_root()
    tmp = os.path.join(root, "_fetch")
    try:
        subprocess.run([exporter.EXE, "export", "--out", tmp, "--only"] + [_rel(g) for g in games],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=60 + 2 * len(games), creationflags=flags)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"Fetching store images failed ({e})"
    # flatten: <tmp>/Discovery/Content/…/T_UI_x.png -> <root>/T_UI_x.png, remembered in the index
    idx = _load_index()
    ltmp = _long(tmp)
    for dirpath, _dirs, files in os.walk(ltmp):
        for f in files:
            if not f.lower().endswith(".png"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, f), ltmp).replace("\\", "/")[:-4]
            try:
                os.replace(os.path.join(dirpath, f), os.path.join(root, f))
                idx[rel] = f
            except OSError:
                pass
    shutil.rmtree(ltmp, ignore_errors=True)
    _save_index()
    _size["bytes"] = None
    return ""


def _icon(path):
    coll = _previews["coll"]
    if coll is None:
        coll = _previews["coll"] = bpy.utils.previews.new()
    if path not in coll:
        coll.load(path, path, "IMAGE")
    return coll[path].icon_id


def available(tab):
    return search.has_images(tab)


class FT_OT_browse_images(bpy.types.Operator):
    """Show the store images of the search results (fetches missing images from the game files into the
    add-on's cache, not into the Exports folder). Click an image to pick that item"""
    bl_idname = "finals_texturer.browse_images"
    bl_label = "Store Images"

    tab: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return True

    @classmethod
    def description(cls, context, props):
        if props and not search.has_images(props.tab):
            return "Objects have no store images"
        return cls.__doc__

    def invoke(self, context, event):
        tab = self.tab
        if not search.has_images(tab):
            self.report({"INFO"}, "This tab has no store images")
            return {"CANCELLED"}
        results = list(search.results(context, tab))[:MAX_RESULTS]
        if not results:
            self.report({"INFO"}, "Search for something first: the images of its results are shown")
            return {"CANCELLED"}
        rows, want = [], []
        context.window.cursor_set("WAIT")
        try:
            for r in results:
                local, game = search.image(context, tab, r.ident)
                png = local if local and os.path.isfile(local) else (cached_file(game) if game else "")
                if not png and game:
                    want.append(game)
                rows.append([r.ident, r.name, r.detail, png, game])
            err = _fetch(list(dict.fromkeys(want)))
            for row in rows:
                if not row[3] and row[4]:
                    row[3] = cached_file(row[4])
            cache = os.path.normcase(cache_root())
            for row in rows:
                if row[3] and os.path.normcase(row[3]).startswith(cache):
                    _touch(row[3])
            if want:
                trim_cache()
        finally:
            context.window.cursor_set("DEFAULT")
        missing = sum(1 for row in rows if not row[3])
        if err:
            self.report({"WARNING"}, err)
        _state.update(tab=tab, items=[tuple(row[:4]) for row in rows], missing=missing)
        context.window_manager.ft_browse_page = 1
        return context.window_manager.invoke_popup(self, width=COLUMNS * 150)

    def execute(self, context):
        return {"FINISHED"}

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        items = _state["items"]
        pages = max(1, (len(items) + PER_PAGE - 1) // PER_PAGE)
        page = min(max(wm.ft_browse_page - 1, 0), pages - 1)
        head = layout.row()
        head.label(text=f"{len(items)} result(s)" + (f", {_state['missing']} without image" if _state["missing"] else ""),
                   icon="IMAGE_DATA")
        if pages > 1:
            # a property, not operator buttons: those would close the popup
            nav = head.row(align=True)
            nav.prop(wm, "ft_browse_page", text="Page")
            nav.label(text=f"of {pages}")
        grid = layout.grid_flow(row_major=True, columns=COLUMNS, even_columns=True, even_rows=True, align=False)
        for ident, name, detail, png in items[page * PER_PAGE:(page + 1) * PER_PAGE]:
            box = grid.box()
            col = box.column(align=True)
            if png:
                col.template_icon(icon_value=_icon(png), scale=6.0)
            else:
                col.label(text="(no image)", icon="QUESTION")
            op = col.operator(FT_OT_browse_pick.bl_idname, text=name[:22])
            op.tab, op.ident = _state["tab"], ident
            if detail:
                col.label(text=detail[:24])


class FT_OT_browse_pick(bpy.types.Operator):
    """Pick this item (sets the tab's dropdowns, like choosing it in the search results)"""
    bl_idname = "finals_texturer.browse_pick"
    bl_label = "Pick"
    bl_options = {"INTERNAL"}

    tab: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    ident: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        search.pick(context, self.tab, self.ident)
        for area in context.screen.areas:
            area.tag_redraw()
        return {"FINISHED"}


def draw_button(layout, tab):
    """The image button next to a search box (all tabs, disabled where there are no store images)."""
    sub = layout.row(align=True)
    sub.enabled = search.has_images(tab)
    op = sub.operator(FT_OT_browse_images.bl_idname, text="", icon="IMAGE_DATA")
    op.tab = tab


class FT_OT_clear_image_cache(bpy.types.Operator):
    """Delete all store images the image browser has downloaded (they are fetched again when needed)"""
    bl_idname = "finals_texturer.clear_image_cache"
    bl_label = "Clear Store Image Cache"

    def execute(self, context):
        n = clear_cache()
        self.report({"INFO"}, f"Removed {n} cached store image(s)")
        return {"FINISHED"}


def draw_prefs(layout):
    """Preferences: cache size + clear button."""
    row = layout.row()
    mb = cache_size() / (1024 * 1024)
    row.label(text=f"Store image cache: {mb:.1f} MB of {CACHE_LIMIT // (1024 * 1024)} MB "
                   "(oldest images are removed automatically)", icon="IMAGE_DATA")
    row.operator(FT_OT_clear_image_cache.bl_idname, text="Clear Cache", icon="TRASH")


CLASSES = (FT_OT_browse_images, FT_OT_browse_pick, FT_OT_clear_image_cache)


def register():
    bpy.types.WindowManager.ft_browse_page = IntProperty(name="Page", default=1, min=1, max=MAX_RESULTS // PER_PAGE + 1,
                                                         description="Page of store images")
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
    if hasattr(bpy.types.WindowManager, "ft_browse_page"):
        del bpy.types.WindowManager.ft_browse_page
    if _previews["coll"] is not None:
        bpy.utils.previews.remove(_previews["coll"])
        _previews["coll"] = None
    _state.update(tab="", items=[], missing=0)
