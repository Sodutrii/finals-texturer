"""Search box shared by the tabs: each tab has its own query, result list and
selection (Scene.ft_<tab>_query / _results / _index). Typing fills the list
(the tab's `find` function); picking a result calls the tab's `pick`, which
sets that tab's dropdowns."""

import bpy
from bpy.props import CollectionProperty, IntProperty, StringProperty

from . import ui

_tabs = {}          # tab → {"find": fn(context, query) → [(ident, name, detail, ready, icon)], "pick": fn(context, ident),
#                 "image": fn(context, ident) → (local PNG or "", game path or "") or None (browse.py)}


class FT_SearchResult(bpy.types.PropertyGroup):
    name: StringProperty()
    detail: StringProperty()
    ident: StringProperty()
    ready: IntProperty(default=-1)
    kind_icon: StringProperty(default="DOT")


class FT_UL_search(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        row = layout.row(align=True)
        row.label(text=item.name, icon=item.kind_icon or "DOT")
        sub = row.row()
        sub.alignment = "RIGHT"
        sub.label(text=item.detail, icon=ui.READY_ICON.get(item.ready, "BLANK1"))


def run(context, tab):
    sc = context.scene
    res = getattr(sc, f"ft_{tab}_results")
    res.clear()
    query = getattr(sc, f"ft_{tab}_query").strip()
    if query and tab in _tabs:
        for ident, name, detail, ready, icon in _tabs[tab]["find"](context, query):
            r = res.add()
            r.ident, r.name, r.detail, r.ready, r.kind_icon = ident, name, detail, ready, icon
    _set_index(sc, tab, -1)


_quiet = {"on": False}


def _set_index(sc, tab, value):
    # set without triggering the pick callback (Blender 5 keeps RNA properties apart from custom
    # properties: writing sc["ft_…_index"] no longer changes the property)
    _quiet["on"] = True
    try:
        setattr(sc, f"ft_{tab}_index", value)
    finally:
        _quiet["on"] = False


def _query_update(tab):
    return lambda self, context: run(context, tab)


def _index_update(tab):
    def upd(self, context):
        if _quiet["on"]:
            return
        sc = context.scene
        i = getattr(sc, f"ft_{tab}_index")
        res = getattr(sc, f"ft_{tab}_results")
        if tab in _tabs and 0 <= i < len(res):
            _tabs[tab]["pick"](context, res[i].ident)
    return upd


def results(context, tab):
    """The current search results of a tab (the shown list)."""
    return getattr(context.scene, f"ft_{tab}_results", [])


def has_images(tab):
    return bool(_tabs.get(tab, {}).get("image"))


def image(context, tab, ident):
    fn = _tabs.get(tab, {}).get("image")
    return fn(context, ident) if fn else ("", "")


def pick(context, tab, ident):
    """Pick a result by ident (like clicking it in the list)."""
    if tab in _tabs:
        _tabs[tab]["pick"](context, ident)
        res = results(context, tab)
        _set_index(context.scene, tab, next((i for i, r in enumerate(res) if r.ident == ident), -1))


def selected(context, tab):
    sc = context.scene
    i = getattr(sc, f"ft_{tab}_index")
    res = getattr(sc, f"ft_{tab}_results")
    return res[i] if 0 <= i < len(res) else None


def draw(layout, context, tab, hint, rows=5):
    """Search field; while a query is typed, the matching results below it."""
    from . import browse
    sc = context.scene
    row = layout.row(align=True)
    ui.draw_search(row, sc, f"ft_{tab}_query", hint)
    browse.draw_button(row, tab)
    if not getattr(sc, f"ft_{tab}_query").strip():
        return
    res = getattr(sc, f"ft_{tab}_results")
    if not len(res):
        layout.label(text="No matches", icon="INFO")
        return
    layout.template_list("FT_UL_search", tab, sc, f"ft_{tab}_results", sc, f"ft_{tab}_index",
                         rows=min(rows, max(2, len(res))))


def add_tab(tab, find, pick, description, image=None):
    _tabs[tab] = {"find": find, "pick": pick, "image": image}
    setattr(bpy.types.Scene, f"ft_{tab}_query",
            StringProperty(name="Search", description=description, update=_query_update(tab),
                           options={"SKIP_SAVE"}))
    setattr(bpy.types.Scene, f"ft_{tab}_results", CollectionProperty(type=FT_SearchResult))
    setattr(bpy.types.Scene, f"ft_{tab}_index", IntProperty(default=-1, update=_index_update(tab)))


def remove_tab(tab):
    for suffix in ("query", "results", "index"):
        if hasattr(bpy.types.Scene, f"ft_{tab}_{suffix}"):
            delattr(bpy.types.Scene, f"ft_{tab}_{suffix}")
    _tabs.pop(tab, None)


CLASSES = (FT_SearchResult, FT_UL_search)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for tab in list(_tabs):
        remove_tab(tab)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
