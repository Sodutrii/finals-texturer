"""Export now: runs the bundled exporter (bin/exporter/ft_exporter.exe, built from /exporter in the
repository) in the background. It reads the game files with FModel's own setup (game folder, AES key,
mappings, Oodle; nothing is stored by the add-on) and writes what an item needs into the Exports
folder, in FModel's layout: the skin, the files next to it, and everything it references (meshes,
materials up to the master, textures, curve atlases). Output matches FModel's file for file.

Runs on Windows with the .NET 10 runtime, which current FModel versions need anyway.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time

import bpy
from bpy.props import StringProperty

EXE = os.path.join(os.path.dirname(__file__), "bin", "exporter", "ft_exporter.exe")

_check = {"time": 0.0, "value": None}
_job = {"proc": None, "queue": None, "lines": [], "done": 0, "total": 0, "text": "", "cancel": False,
        "result": None}


def bundled():
    return sys.platform == "win32" and os.path.isfile(EXE)


def _run_quiet(args, timeout=30):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run([EXE] + args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=flags)


def check(refresh=False):
    """FModel setup as the exporter sees it: {"ok": bool, "problems": [...], "game_dir": ..., ...}.
    Cached until Rescan / Check Setup (the panel draws often)."""
    if not bundled():
        return {"ok": False, "problems": ["Export now isn't available on this system "
                                          "(Windows only; the exporter is missing from the add-on)."]}
    if not refresh and _check["value"] is not None:
        return _check["value"]
    try:
        res = _run_quiet(["check"])
        line = next((l for l in res.stdout.splitlines() if l.startswith("{")), "")
        val = json.loads(line) if line else {"ok": False, "problems": [res.stderr.strip() or "No answer"]}
    except FileNotFoundError:
        val = {"ok": False, "problems": ["The exporter couldn't start."]}
    except OSError as e:
        val = {"ok": False, "problems": [f"The exporter couldn't start ({e})."]}
    except subprocess.TimeoutExpired:
        val = {"ok": False, "problems": ["The exporter didn't answer."]}
    except ValueError:
        val = {"ok": False, "problems": ["The exporter gave no usable answer."]}
    if not val.get("ok") and not val.get("problems"):
        val["problems"] = ["The exporter isn't ready."]
    if _dotnet_missing(val):
        val["problems"] = ["The .NET 10 runtime is missing (FModel needs it too): "
                           "install '.NET Desktop Runtime 10' from Microsoft."]
    _check.update(time=time.time(), value=val)
    return val


def clear_cache():
    _check["value"] = None


def _dotnet_missing(val):
    text = " ".join(val.get("problems", [])).lower()
    return "framework" in text and ("not found" in text or "install" in text) or "hostfxr" in text


def ready():
    return check().get("ok", False)


def running():
    return _job["proc"] is not None


def progress():
    """(fraction 0..1, text) of the running export."""
    total = _job["total"]
    return (_job["done"] / total if total else 0.0), _job["text"]


def rel_path(root, path):
    """Exports-relative game path for the exporter ('Discovery/Content/…')."""
    return os.path.relpath(path, root).replace("\\", "/")


def top_level(root, folder):
    """The exporter's 'only this folder's own files' form."""
    return rel_path(root, folder).rstrip("/") + "/*"


def _reader(proc, q):
    for line in proc.stdout:
        q.put(line)
    q.put(None)


class FT_OT_export_game(bpy.types.Operator):
    """Export what this needs straight from the game files (uses FModel's setup; FModel doesn't
    have to be open). Writes into the Exports folder in FModel's layout"""
    bl_idname = "finals_texturer.export_game"
    bl_label = "Export Now"

    paths: StringProperty(options={"SKIP_SAVE"})       # one game path per line
    what: StringProperty(options={"SKIP_SAVE"})

    _timer = None

    @classmethod
    def poll(cls, context):
        return not running()

    def execute(self, context):
        from . import _root
        root = _root(context)
        paths = [p for p in self.paths.splitlines() if p.strip()]
        if not paths:
            self.report({"ERROR"}, "Nothing to export")
            return {"CANCELLED"}
        st = check()
        if not st.get("ok"):
            self.report({"ERROR"}, " ".join(st.get("problems", [])) or "The exporter isn't ready")
            return {"CANCELLED"}
        if not root or not os.path.isdir(root):
            self.report({"ERROR"}, "Set the Exports folder first")
            return {"CANCELLED"}
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.Popen([EXE, "export", "--out", root] + paths, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                    creationflags=flags)
        except OSError as e:
            self.report({"ERROR"}, f"The exporter couldn't start ({e})")
            return {"CANCELLED"}
        q = queue.Queue()
        threading.Thread(target=_reader, args=(proc, q), daemon=True).start()
        _job.update(proc=proc, queue=q, lines=[f"Export now: {self.what or ', '.join(paths)}"], done=0, total=0,
                    text="Opening the game files…", cancel=False, result=None)
        self._timer = context.window_manager.event_timer_add(0.2, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        q = _job["queue"]
        finished = False
        while True:
            try:
                line = q.get_nowait()
            except queue.Empty:
                break
            if line is None:
                finished = True
                break
            self._handle(line.strip())
        if _job["cancel"] and _job["proc"].poll() is None:
            _job["proc"].terminate()
        _redraw(context)
        if not finished:
            return {"PASS_THROUGH"}
        _job["proc"].wait()
        context.window_manager.event_timer_remove(self._timer)
        return self._finish(context)

    def _handle(self, line):
        if not line.startswith("{"):
            if line:
                _job["lines"].append(line)
            return
        try:
            ev = json.loads(line)
        except ValueError:
            return
        kind = ev.get("event")
        if kind == "scan":
            _job["text"] = f"Finding files… {ev.get('done', 0)}"
        elif kind == "progress":
            _job["done"], _job["total"] = ev.get("done", 0), ev.get("total", 0)
            _job["text"] = (f"Exporting {_job['done']}/{_job['total']}: "
                            f"{os.path.splitext(os.path.basename(ev.get('path', '')))[0]}")
        elif kind == "status":
            _job["text"] = ev.get("message", "")
        elif kind in ("warning", "error"):
            _job["lines"].append(f"{'WARNING' if kind == 'warning' else 'ERROR'}: {ev.get('message', '')}")
        elif kind == "done":
            _job["result"] = ev

    def _finish(self, context):
        res, cancelled = _job["result"], _job["cancel"]
        lines = _job["lines"]
        _job.update(proc=None, queue=None, cancel=False)
        if res:
            lines.append(f"Done: {res.get('written', 0)} file(s) written, {res.get('skipped', 0)} already there, "
                         f"{res.get('failed', 0)} failed.")
        _write_log(lines)
        try:
            bpy.ops.finals_texturer.rescan()
        except RuntimeError:
            pass
        _redraw(context)
        if cancelled:
            self.report({"WARNING"}, "Export cancelled (files written so far are kept)")
            return {"CANCELLED"}
        if not res:
            err = next((l for l in reversed(lines) if l.startswith("ERROR")), "the exporter stopped")
            self.report({"ERROR"}, f"Export failed: {err.split(': ', 1)[-1]} (see Show Log)")
            return {"CANCELLED"}
        msg = f"Exported {res.get('written', 0)} file(s)"
        if res.get("failed"):
            self.report({"WARNING"}, msg + f", {res['failed']} failed (see Show Log)")
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}


class FT_OT_export_cancel(bpy.types.Operator):
    """Stop the running export (files written so far are kept)"""
    bl_idname = "finals_texturer.export_cancel"
    bl_label = "Cancel Export"

    @classmethod
    def poll(cls, context):
        return running()

    def execute(self, context):
        _job["cancel"] = True
        return {"FINISHED"}


def _write_log(lines):
    txt = bpy.data.texts.get("Finals Texturer Log") or bpy.data.texts.new("Finals Texturer Log")
    txt.clear()
    txt.write("\n".join(lines) + "\n")
    for l in lines:
        print("[FinalsTexturer]", l)


def _redraw(context):
    for win in context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def draw(layout, paths, what=""):
    """Export Now button (or the running export's progress) for these game paths. Returns True if
    something was drawn."""
    if running():
        frac, text = progress()
        row = layout.row(align=True)
        if hasattr(row, "progress"):
            row.progress(factor=frac, type="BAR", text=text)
        else:
            row.label(text=text, icon="TIME")
        row.operator(FT_OT_export_cancel.bl_idname, text="", icon="CANCEL")
        return True
    if not paths or not bundled():
        return False
    st = check()
    if not st.get("ok"):
        col = layout.column(align=True)
        col.label(text="Export Now needs FModel set up for The Finals:", icon="INFO")
        for p in st.get("problems", [])[:2]:
            col.label(text=f"   {p}")
        return True
    op = layout.operator(FT_OT_export_game.bl_idname, text="Export Now", icon="IMPORT")
    op.paths = "\n".join(paths)
    op.what = what
    return True


CLASSES = (FT_OT_export_game, FT_OT_export_cancel)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    p = _job.get("proc")
    if p is not None and p.poll() is None:
        p.terminate()
    _job.update(proc=None, queue=None)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
