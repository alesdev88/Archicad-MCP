"""Send a stored changeset, exactly as it was previewed, and check the result."""
from __future__ import annotations

import math

from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.connection import ArchicadUnavailableError
from archicad_mcp.core.element_data import (
    REPORT_CAP,
    cap_list,
    group_failures,
    error_fields,
    send_property_writes,
)
from archicad_mcp.core.project import project_identity
from archicad_mcp.extract import MAX_PROPERTY_FETCH_ELEMENTS, fetch_property_cells
from archicad_mcp.gateway.execute import _dispatch
from archicad_mcp.scripting.changesets import ChangesetError, ChangesetStore, summarize

# Failed elements shown per recorded command; the count covers the rest.
COMMAND_SAMPLE = 5


def _same(read, sent) -> bool:
    if isinstance(sent, float) or isinstance(read, float):
        try:
            return math.isclose(float(read), float(sent), rel_tol=1e-9, abs_tol=1e-9)
        except (TypeError, ValueError):
            return False
    return read == sent


def _read_back(conn, writes: list[dict], failed: list[dict]) -> list[dict]:
    """Writes Archicad accepted but that do not read back as sent.

    Slices large batches by MAX_PROPERTY_FETCH_ELEMENTS to avoid hitting
    the ceiling on wide property queries.
    """
    refused = {(f["guid"], f["property"]) for f in failed}
    # Only the last write to a cell can read back: an earlier value for the
    # same element and property was overwritten on purpose, not lost.
    last = {(w["guid"], w["property"]): w for w in writes}
    done = [w for key, w in last.items() if key not in refused]
    if not done:
        return []
    out = []
    guids_list = list(dict.fromkeys(w["guid"] for w in done))
    properties = sorted({w["property"] for w in done})
    # Slice by MAX_PROPERTY_FETCH_ELEMENTS to avoid ceiling.
    for i in range(0, len(guids_list), MAX_PROPERTY_FETCH_ELEMENTS):
        slice_guids = guids_list[i:i + MAX_PROPERTY_FETCH_ELEMENTS]
        in_slice = set(slice_guids)
        cells = fetch_property_cells(conn, slice_guids, properties)
        for w in done:
            if w["guid"] not in in_slice:
                continue
            read = cells.get(w["guid"], {}).get(w["property"], {}).get("value")
            if not _same(read, w["new"]):
                out.append({"guid": w["guid"], "property": w["property"],
                            "sent": w["new"], "read": read})
    return out


def _command_outcome(name: str, response) -> dict:
    """A recorded command's entry, counting the elements it refused.

    Many write commands answer with one executionResults entry per element and
    still succeed as a call, so a refusal there would otherwise read as ok.
    Like a property batch, per-element refusals do not stop the run.
    """
    results = response.get("executionResults") if isinstance(response, dict) else None
    if not isinstance(results, list):
        return {"name": name, "ok": True}
    bad = [r for r in results if isinstance(r, dict) and r.get("success") is False]
    entry = {"name": name, "ok": not bad, "failed": len(bad)}
    if bad:
        entry["sample"] = bad[:COMMAND_SAMPLE]
    return entry


def _report(applied: int, failed: list[dict], mismatched: list[dict],
            commands: list[dict], stopped: dict | None = None) -> dict:
    shown_mismatched, mismatched_rest = cap_list(mismatched, REPORT_CAP)
    result = {"applied": applied, "failed": group_failures(failed),
              "mismatched": shown_mismatched, "commands": commands}
    if mismatched_rest:
        result["mismatched_not_shown"] = mismatched_rest
    if stopped is not None:
        result["stopped"] = stopped
    return result


def _identity_change(recorded: dict, current: dict | None) -> str | None:
    """Why the open project is not the recorded one, or None when it is."""
    if current is None:
        return "its identity could not be read"
    for key in ("name", "is_teamwork", "location"):
        if current.get(key) != recorded.get(key):
            return (f"{key} was {recorded.get(key)!r} when planned and is "
                    f"{current.get(key)!r} now")
    return None


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
    if cs.identity is not None:
        # A scratch copy and the live Teamwork project can share a name, so
        # when Tapir gave an identity at plan time the whole of it must match.
        try:
            current = project_identity(conn)
        except (APIErrorBase, ArchicadUnavailableError):
            current = None
        change = _identity_change(cs.identity, current)
        if change is not None:
            return {"error": f"The project open on port {cs.port} is not the one "
                             f"this changeset was planned against: {change}. "
                             "Nothing was written."}
    try:
        # Single use from here on, whatever happens: a half-applied changeset
        # must not be re-sent blindly. Taken under the store's lock, so two
        # concurrent applies cannot both get past this point.
        store.take(changeset_id)
    except ChangesetError as exc:
        return {"error": str(exc)}
    applied = 0
    failed: list[dict] = []
    mismatched: list[dict] = []
    commands: list[dict] = []
    for op in cs.operations:
        if op["kind"] == "props":
            count, refused, error = send_property_writes(conn, op["writes"])
            applied += count
            failed.extend(refused)
            # Every write in a batch that was sent is either applied or
            # refused, so this is exactly the writes that reached Archicad.
            sent = op["writes"][:count + len(refused)]
            stopped_at = "property writes"
            try:
                mismatched.extend(_read_back(conn, sent, refused))
            except (APIErrorBase, ArchicadUnavailableError) as exc:
                if error is None:
                    error, stopped_at = exc, "readback"
            if error is not None:
                return _report(applied, failed, mismatched, commands,
                               stopped={"at": stopped_at, **error_fields(error)})
            continue
        # Check registry before attempting dispatch.
        name = op["name"]
        if name not in registry:
            message = "command is not in this server's registry"
            commands.append({"name": name, "ok": False, "code": None,
                             "message": message})
            return _report(applied, failed, mismatched, commands,
                           stopped={"at": name, "code": None, "message": message})
        try:
            response = _dispatch(conn, registry[name], op["params"])
        except (APIErrorBase, ArchicadUnavailableError) as exc:
            # Later operations may depend on this one, so nothing after it runs.
            fields = error_fields(exc)
            commands.append({"name": name, "ok": False, **fields})
            return _report(applied, failed, mismatched, commands,
                           stopped={"at": name, **fields})
        commands.append(_command_outcome(name, response))
    return _report(applied, failed, mismatched, commands)
