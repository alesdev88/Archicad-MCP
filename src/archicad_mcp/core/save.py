"""Save the open project to its own file (Tapir SaveProject).

Tapir's SaveProject is a plain File > Save: it brings the floor plan to the
front around the save (a save from a 3D or section window failed, Tapir #681)
and keeps every refusal File > Save makes. Any refusal comes back as one
message, "Failed to save the project.", with Archicad's code, so the causes
are named here. Live 19.10.2026: an Archicad 27 file opened in Archicad 28
was refused that way, because Archicad saves a file from an older version
only with File > Save As.

There is no Save As here. Choosing where a project lives, or converting it to
a newer format, is the user's call in Archicad.

On a Teamwork project File > Save keeps the changes in the local copy and
sends nothing (Graphisoft's documented behaviour; not tried live, the only
Teamwork project at hand is a live one). This tool calls nothing that sends.
"""
from __future__ import annotations

import os
from pathlib import Path

from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.project import _scrub

TEAMWORK_NOTE = ("On a Teamwork project this saves your local copy only. Nothing is "
                 "sent: teammates see your changes after Teamwork > Send (or Send & "
                 "Receive) in Archicad. This tool never sends.")

UNTITLED = ("The project has never been saved, so it has no file to save to. Give it "
            "a name and a folder with File > Save As in Archicad; this tool does not "
            "choose where projects live.")

_CAUSES = ("The usual causes: the project file was made in an older Archicad, which "
           "Archicad saves only with File > Save As (seen with an Archicad 27 file "
           "opened in Archicad 28; this tool does not Save As, do it in Archicad); "
           "the project was opened read-only (another user has it open, or a stale "
           "lock); or the folder cannot be written to.")


def _local_file(location: object) -> Path | None:
    """The project's file on this computer, or None (Teamwork, untitled)."""
    if not isinstance(location, str) or "://" in location:
        return None
    path = Path(location)
    return path if path.is_file() else None


def _mtime(path: Path | None) -> int | None:
    try:
        return path.stat().st_mtime_ns if path is not None else None
    except OSError:
        return None


def _failure(exc: APIErrorBase, path: Path | None) -> dict:
    code = getattr(exc, "code", None)
    said = f"{exc.message}" + (f", code {code}" if code is not None else "")
    text = f"Archicad did not save the project ({said}). "
    if path is not None and not (os.access(path, os.W_OK)
                                 and os.access(path.parent, os.W_OK)):
        text += (f"The project file or its folder is not writable for this user "
                 f"({path}). ")
    return {"error": text + _CAUSES}


def save_project(conn: ArchicadConnection, confirm: bool, show_identity: bool) -> dict:
    info = conn.tapir("GetProjectInfo")
    if info.get("isUntitled"):
        return {"error": UNTITLED}
    teamwork = bool(info.get("isTeamwork"))
    location = info.get("projectLocation") or info.get("projectPath")
    path = None if teamwork else _local_file(location)
    out: dict = {"dry_run": not confirm, "is_teamwork": teamwork}
    if show_identity:
        out["project"] = info.get("projectName")
        if isinstance(location, str):
            out["location"] = _scrub(location).split("?", 1)[0]
    if not confirm:
        note = "Nothing was saved. Pass confirm=true to save the project to its own file."
        out["note"] = f"{note} {TEAMWORK_NOTE}" if teamwork else note
        return out

    before = _mtime(path)
    try:
        conn.tapir("SaveProject")
    except APIErrorBase as exc:
        return _failure(exc, path)
    out["saved"] = True
    if teamwork:
        out["sent"] = False
        out["note"] = TEAMWORK_NOTE
    elif path is not None:
        after = _mtime(path)
        out["file_updated"] = before is not None and after is not None and after != before
        if not out["file_updated"]:
            out["note"] = ("Archicad answered success, but the file on disk was not "
                           "rewritten. Usually that means there was nothing unsaved.")
    return out
