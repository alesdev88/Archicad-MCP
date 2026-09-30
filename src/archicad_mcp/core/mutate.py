"""Move and delete elements, reporting what Archicad did rather than what was asked.

Tapir DeleteElements answers one success for the whole batch, and
ACAPI_Element_Delete passes over elements outside the active window's database
without an error. Seen live 28.09.2026: 12 floor-plan labels, a Layout window
active, success, nothing deleted, and this tool used to report {"deleted": 12}.
So both tools first leave out what Archicad would refuse (core.editability),
then delete counts the elements a read no longer finds and move counts
Archicad's per-element results.
"""
from __future__ import annotations

from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.connection import ArchicadConnection, ArchicadUnavailableError
from archicad_mcp.core.editability import present, refusals
from archicad_mcp.core.element_data import cap_list, error_fields
from archicad_mcp.extract import element_payload


def _group(reasons: dict[str, str]) -> list[dict]:
    """Elements grouped by reason, largest group first, GUIDs capped per group."""
    groups: dict[str, list[str]] = {}
    for guid, reason in reasons.items():
        groups.setdefault(reason, []).append(guid)
    out = []
    for reason, guids in sorted(groups.items(), key=lambda item: -len(item[1])):
        shown, rest = cap_list(guids)
        group = {"reason": reason, "count": len(guids), "guids": shown}
        if rest:
            group["not_shown"] = rest
        out.append(group)
    return out


def _outcome(requested: int, key: str, count: int, reasons: dict[str, str],
             window: str | None,
             stopped: APIErrorBase | ArchicadUnavailableError | None = None) -> dict:
    result = {"requested": requested, key: count}
    if reasons:
        result[f"not_{key}"] = _group(reasons)
    if window is not None:
        result["active_window"] = window
    if stopped is not None:
        result["stopped"] = error_fields(stopped)
    return result


def _survivor_reason(response: dict,
                     stopped: APIErrorBase | ArchicadUnavailableError | None) -> str:
    if stopped is not None:
        return (f"the delete command failed ({error_fields(stopped)['message']}) "
                "and the element is still there")
    if response.get("success") is False:
        error = response.get("error") or {}
        return f"Archicad refused the deletion: {error.get('message', error)}"
    return ("Archicad reported it deleted, but it is still in the active "
            "window's database")


def move_elements(conn: ArchicadConnection, guids: list[str], vector: dict,
                  confirm: bool = False) -> dict:
    guids = list(dict.fromkeys(guids))
    if not confirm:
        return {"error": f"Refusing to move {len(guids)} element(s) without "
                         "confirm=true. Review the GUIDs and vector, then retry "
                         "with confirm=true."}
    reasons, window = refusals(conn, guids)
    send = [g for g in guids if g not in reasons]
    moved = 0
    if send:
        response = conn.tapir("MoveElements", {"elementsWithMoveVectors": [
            {"elementId": {"guid": g}, "moveVector": vector} for g in send]})
        results = (response or {}).get("executionResults", [])
        for i, guid in enumerate(send):
            outcome = results[i] if i < len(results) else None
            if isinstance(outcome, dict) and outcome.get("success") is True:
                moved += 1
            elif isinstance(outcome, dict) and outcome.get("success") is False:
                error = outcome.get("error") or {}
                reasons[guid] = f"Archicad refused the move: {error.get('message', error)}"
            else:
                reasons[guid] = "Archicad returned no result for it, so the move is unconfirmed"
    return _outcome(len(guids), "moved", moved, reasons, window)


def delete_elements(conn: ArchicadConnection, guids: list[str],
                    confirm: bool = False) -> dict:
    guids = list(dict.fromkeys(guids))
    if not confirm:
        return {"error": f"Refusing to delete {len(guids)} element(s) without "
                         "confirm=true. Deletion is irreversible; retry with "
                         "confirm=true only if certain."}
    reasons, window = refusals(conn, guids)
    send = [g for g in guids if g not in reasons]
    deleted = 0
    stopped = None
    if send:
        try:
            response = conn.tapir("DeleteElements", {"elements": element_payload(send)})
        except (APIErrorBase, ArchicadUnavailableError) as exc:
            # Archicad may have acted before the error; the read says what went.
            response, stopped = {}, exc
        # Read from the database the delete acted on (see editability.present).
        remaining = present(conn, send)
        survivor = _survivor_reason(response or {}, stopped)
        for guid in send:
            if guid in remaining:
                reasons[guid] = survivor
            else:
                deleted += 1
    return _outcome(len(guids), "deleted", deleted, reasons, window, stopped)
