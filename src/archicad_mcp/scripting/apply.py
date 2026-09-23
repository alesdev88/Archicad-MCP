"""Send a stored changeset, exactly as it was previewed, and check the result."""
from __future__ import annotations

import math

from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.core.element_data import send_property_writes
from archicad_mcp.extract import fetch_property_cells
from archicad_mcp.gateway.execute import _dispatch
from archicad_mcp.scripting.changesets import ChangesetError, ChangesetStore, summarize

REPORT_CAP = 50


def _same(read, sent) -> bool:
    if isinstance(sent, float) or isinstance(read, float):
        try:
            return math.isclose(float(read), float(sent), rel_tol=1e-9, abs_tol=1e-9)
        except (TypeError, ValueError):
            return False
    return read == sent


def _read_back(conn, writes: list[dict], failed: list[dict]) -> list[dict]:
    """Writes Archicad accepted but that do not read back as sent."""
    refused = {(f["guid"], f["property"]) for f in failed}
    done = [w for w in writes if (w["guid"], w["property"]) not in refused]
    if not done:
        return []
    cells = fetch_property_cells(conn, list(dict.fromkeys(w["guid"] for w in done)),
                                 sorted({w["property"] for w in done}))
    out = []
    for w in done:
        read = cells.get(w["guid"], {}).get(w["property"], {}).get("value")
        if not _same(read, w["new"]):
            out.append({"guid": w["guid"], "property": w["property"],
                        "sent": w["new"], "read": read})
    return out


def _report(applied: int, failed: list[dict], mismatched: list[dict],
            commands: list[dict], stopped: dict | None = None) -> dict:
    result = {"applied": applied, "failed": failed[:REPORT_CAP],
              "mismatched": mismatched[:REPORT_CAP], "commands": commands}
    if len(failed) > REPORT_CAP:
        result["failed_not_shown"] = len(failed) - REPORT_CAP
    if len(mismatched) > REPORT_CAP:
        result["mismatched_not_shown"] = len(mismatched) - REPORT_CAP
    if stopped is not None:
        result["stopped"] = stopped
    return result


def apply_changeset(store: ChangesetStore, changeset_id: str, confirm: bool,
                    registry: dict, probe, connect) -> dict:
    try:
        cs = store.lookup(changeset_id)
    except ChangesetError as exc:
        return {"error": str(exc)}
    if not confirm:
        return {"error": "This changeset changes the project and was not confirmed. "
                         "Re-send with confirm=true to apply it.",
                "changeset": summarize(cs)}
    info = probe(cs.port)
    if info is None or not info.project_open:
        return {"error": f"No Archicad with an open project answers on port "
                         f"{cs.port}. Nothing was written."}
    # Checked by name because that is what the API offers. Without Tapir both
    # names are None and the check cannot tell projects apart.
    if info.project_name != cs.project:
        return {"error": f"Port {cs.port} now has project {info.project_name!r} "
                         f"open, but this changeset was planned against "
                         f"{cs.project!r}. Nothing was written."}
    conn = connect(cs.port)
    # Single use from here on, whatever happens: a half-applied changeset must
    # not be re-sent blindly.
    cs.applied = True
    applied = 0
    failed: list[dict] = []
    mismatched: list[dict] = []
    commands: list[dict] = []
    for op in cs.operations:
        if op["kind"] == "props":
            try:
                count, refused = send_property_writes(conn, op["writes"])
            except APIErrorBase as exc:
                return _report(applied, failed, mismatched, commands,
                               stopped={"at": "property writes",
                                        "code": getattr(exc, "code", None),
                                        "message": exc.message})
            applied += count
            failed.extend(refused)
            mismatched.extend(_read_back(conn, op["writes"], refused))
            continue
        try:
            _dispatch(conn, registry[op["name"]], op["params"])
        except APIErrorBase as exc:
            # Later operations may depend on this one, so nothing after it runs.
            commands.append({"name": op["name"], "ok": False,
                             "code": getattr(exc, "code", None), "message": exc.message})
            return _report(applied, failed, mismatched, commands,
                           stopped={"at": op["name"]})
        commands.append({"name": op["name"], "ok": True})
    return _report(applied, failed, mismatched, commands)
