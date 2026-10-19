"""Save the project as IFC with an export translator chosen by name.

Tapir's IFCFileOperation saves through the Automate API when it is given a
translatorName (Tapir 1.6.0, ProjectCommands.cpp
SaveProjectAsIfcWithTranslator): plain IFC or IFC ZIP, one of five element
scopes, and the floor plan or the 3D window in front. Without a translatorName
it saves with whatever translator Archicad remembers from its own Save dialog,
which a caller can neither see nor set, so this tool always names one. Open and
merge are left out: they replace or change the project, a different job.

Tapir answers success without checking the file, so the file is checked here.
"""
from __future__ import annotations

import time
from pathlib import Path

from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.editability import active_window
from archicad_mcp.extract import get_selected_element_ids

SCOPES = ("EntireProject", "VisibleElementsOnAllStories", "AllElementsOnCurrentStory",
          "VisibleElementsOnCurrentStory", "SelectedElementsOnly")
DEFAULT_SCOPE = "VisibleElementsOnAllStories"
FILE_TYPES = {".ifc": "ifc", ".ifczip": "ifczip"}
_CURRENT_STORY = ("AllElementsOnCurrentStory", "VisibleElementsOnCurrentStory")
_EXPORT_WINDOWS = ("FloorPlan", "3DModel")


def _target(path: str) -> tuple[Path | None, str | None, str | None]:
    """(the file, its fileType, None) or (None, None, why it is refused)."""
    target = Path(path).expanduser()
    if not target.is_absolute():
        return None, None, (f"'{path}' is not an absolute path. Give the full path of "
                            "the IFC file to write.")
    file_type = FILE_TYPES.get(target.suffix.lower())
    if file_type is None:
        return None, None, (f"'{target.name}' must end in .ifc or .ifczip; the extension "
                            "sets the file type.")
    if not target.parent.is_dir():
        return None, None, f"The folder {target.parent} does not exist. Create it first."
    if target.is_dir():
        return None, None, f"{target} is a folder, not a file."
    return target, file_type, None


def _translator(names: list[str], preview: str | None,
                wanted: str | None) -> tuple[str | None, str | None]:
    """(the translator's name, None) or (None, why it is refused)."""
    if not names:
        return None, "The project has no IFC export translators."
    if wanted is None:
        return preview or names[0], None
    if wanted in names:
        return wanted, None
    folded = [n for n in names if n.casefold() == wanted.strip().casefold()]
    if folded:
        return folded[0], None
    listed = ", ".join(f"'{n}'" for n in names)
    return None, (f"The project has no IFC export translator named '{wanted}'. "
                  f"Its translators: {listed}.")


def _scope(wanted: str) -> str | None:
    return next((s for s in SCOPES if s.casefold() == wanted.strip().casefold()), None)


def _mtime(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def export_ifc(conn: ArchicadConnection, path: str, translator: str | None = None,
               scope: str = DEFAULT_SCOPE, overwrite: bool = False,
               dry_run: bool = True) -> dict:
    target, file_type, refusal = _target(path)
    if refusal:
        return {"error": refusal}
    exists = target.exists()
    if exists and not overwrite:
        return {"error": f"{target} already exists. Pass overwrite=true to replace it, "
                         "or choose another path."}
    chosen_scope = _scope(scope)
    if chosen_scope is None:
        return {"error": f"Unknown scope '{scope}'. Use one of: {', '.join(SCOPES)}."}

    listed = conn.tapir("GetIFCExportTranslators").get("translators", [])
    names = [t.get("name") for t in listed if t.get("name")]
    preview = next((t["name"] for t in listed if t.get("preview") and t.get("name")), None)
    name, refusal = _translator(names, preview, translator)
    if refusal:
        return {"error": refusal}

    window = active_window(conn)
    switch = window not in _EXPORT_WINDOWS
    plan: dict = {"path": str(target), "file_type": file_type, "translator": name,
                  "translator_is_default": translator is None, "translators": names,
                  "scope": chosen_scope, "active_window": window, "exists": exists}
    if chosen_scope == "SelectedElementsOnly":
        if switch:
            return {"error": ("SelectedElementsOnly exports the selection of the floor "
                              "plan or the 3D window, and neither is in front (the active "
                              f"window is {window or 'unknown'}). Bring the floor plan or "
                              "the 3D window to the front, select there, and retry.")}
        plan["selected"] = len(get_selected_element_ids(conn))
        if not plan["selected"]:
            return {"error": "SelectedElementsOnly, but nothing is selected."}
    if chosen_scope in _CURRENT_STORY:
        stories = conn.tapir("GetStories")
        act = stories.get("actStory")
        named = {s.get("index"): s.get("name") for s in stories.get("stories", [])}
        plan["story"] = {"index": act, "name": named.get(act) or f"Story {act}"}

    if dry_run:
        note = "Nothing was written. Pass dry_run=false to export."
        if switch:
            note += (f" The {window or 'active'} window cannot export IFC, so the export "
                     "brings the floor plan to the front first and leaves it there.")
        return {"dry_run": True, **plan, "note": note}

    out: dict = {"dry_run": False}
    if switch:
        conn.tapir("ChangeWindow", {"windowType": "FloorPlan"})
        out["switched_window"] = {"from": window, "to": "FloorPlan"}
    before = _mtime(target) if exists else None
    started = time.monotonic()
    try:
        conn.tapir("IFCFileOperation", {"method": "save", "ifcFilePath": str(target),
                                        "fileType": file_type, "translatorName": name,
                                        "elementsToExport": chosen_scope})
    except APIErrorBase as exc:
        return {"error": f"Archicad did not export the IFC: {exc.message}"}
    seconds = round(time.monotonic() - started, 1)
    if not target.is_file():
        return {"error": f"Archicad answered success, but there is no file at {target}."}
    if exists and _mtime(target) == before:
        return {"error": f"Archicad answered success, but {target} was not rewritten: "
                         "it is the file that was there before."}
    out.update({"exported": str(target), "bytes": target.stat().st_size,
                "seconds": seconds, "translator": name, "scope": chosen_scope,
                "file_type": file_type})
    if "story" in plan:
        out["story"] = plan["story"]
    return out
