"""The name list shipped with the add-on (data/names.json.gz, built from the game files by
dev_tools/shader_extract/build_names.py): every clothing item and outfit, weapon (with its skins)
and face, with in-game names and the folders they live in. It lets search and the pickers list
everything right after install, also what isn't exported yet (those show "not exported" and the
folder to export). A library the user rebuilds from their own exports takes precedence."""

import gzip
import json
import os

_PATH = os.path.join(os.path.dirname(__file__), "data", "names.json.gz")
_data = {"loaded": False, "value": None}


def data():
    if not _data["loaded"]:
        _data["loaded"] = True
        try:
            with gzip.open(_PATH, "rt", encoding="utf-8") as fh:
                _data["value"] = json.load(fh)
        except (OSError, ValueError):
            _data["value"] = None
    return _data["value"]


def clothing_library(root):
    """The shipped clothing library in library.load's format (for this Exports folder), or None."""
    from . import library
    d = data()
    if not d or d.get("library_version") != library.LIB_VERSION:
        return None
    key = ("lib", os.path.normcase(os.path.normpath(root or "")))
    hit = _data.get(key)
    if hit is None:
        hit = {"version": library.LIB_VERSION, "root": root, "scanned": d.get("generated", 0), "shipped": True,
               "entries": [dict(e) for e in d.get("clothing", [])]}
        library._index(hit)
        _data[key] = hit
    return hit


def weapons():
    d = data()
    return d.get("weapons", []) if d else []


def faces():
    d = data()
    return d.get("faces", []) if d else []
