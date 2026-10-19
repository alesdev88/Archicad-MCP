"""Which elements Archicad would refuse to change, and why.

Archicad changes only elements in the database of the active window, and does
not say so about the rest. Verified live 28.09.2026 (AC 29/5101, Tapir 1.5.10,
a Teamwork project): with a Layout window active, Tapir DeleteElements on 12
reserved floor-plan labels answered success and deleted none of them. Tapir
FilterElements with IsEditable had said 0 of 12 beforehand; with the floor plan
active the same call deleted all 12.

The same filter catches the other refusals: elements on a hidden layer (the
same labels were not editable until their layer was turned on), elements
inside a hotlinked module (Archicad refuses them with 6001 even outside
Teamwork, seen live 23.09.2026 on 113 doors) and, on a Teamwork project,
elements not reserved. IsEditable does not say which applies, so the reason is
built from what else can be read: whether the element is in the active
window's database, whether its layer is visible, whether it is in my
workspace, and which window is active.
"""
from __future__ import annotations

from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.connection import ArchicadConnection, ArchicadUnavailableError
from archicad_mcp.core.teamwork import FILTER_CHUNK, _in_my_workspace, _is_teamwork
from archicad_mcp.extract import (
    PROPERTY_FETCH_CHUNK,
    PropertyFetchTooWideError,
    element_payload,
    fetch_property_values,
)

NOT_FOUND = "not_found"
HIDDEN = "hidden"
UNRESERVED = "unreserved"
LOCKED = "locked"

_HEAD = {NOT_FOUND: "not found", HIDDEN: "not editable",
         UNRESERVED: "not editable", LOCKED: "not editable"}
_CAUSE = {
    NOT_FOUND: ("it does not exist (deleted already, or a wrong GUID) or belongs "
                "to another view"),
    HIDDEN: "it is on a hidden layer; turn the layer on in the active view and retry",
    UNRESERVED: "it is not reserved in Teamwork; reserve it (reserve_elements) and retry",
    LOCKED: ("it is inside a hotlinked module, on a locked layer, or otherwise "
             "locked, so Archicad would refuse the change"),
}

# The element lock (Edit > Locking) as a built-in property. Tapir has no read
# of it (API_Elem_Head.lockId is never read in its source, 1.5.9 to 1.7.1),
# and the DevKit does not say whether FilterElements IsEditable covers it. Read
# false on an unlocked slab, object and mesh on MCP-Test, 19.10.2026.
ELEMENT_LOCK_PROPERTY = "General_Locked"

HOTLINK_REASON = ("inside a hotlinked module; Archicad refuses changes to it. Change "
                  "the module's source file, or break the hotlink")
ELEMENT_LOCK_REASON = ("locked with Edit > Locking; unlock it there and retry (this "
                       "tool does not unlock)")


def layer_locked_reason(name: str) -> str:
    return f"on the locked layer '{name}'; unlock the layer (Layer Settings) and retry"


def reason_text(kind: str, window: str | None) -> str:
    """The reason text, leading with the active window when it is the likelier
    cause. From the floor plan, or when the window is unknown, the plain cause."""
    if window in (None, "FloorPlan"):
        return f"{_HEAD[kind]}: {_CAUSE[kind]}"
    return (f"{_HEAD[kind]} from the active {window} window. Archicad changes only "
            "elements in the database of the active window: if it is on the floor "
            "plan (model elements and their labels, dimensions, markers), switch "
            f"to the floor plan and retry. Otherwise {_CAUSE[kind]}")


def _passing(conn: ArchicadConnection, guids: list[str], name: str) -> set[str]:
    """The elements that pass one Tapir FilterElements filter."""
    passed: set[str] = set()
    for start in range(0, len(guids), FILTER_CHUNK):
        chunk = guids[start:start + FILTER_CHUNK]
        response = conn.tapir("FilterElements", {"elements": element_payload(chunk),
                                                 "filters": [name]})
        passed.update(e["elementId"]["guid"] for e in response.get("elements", []))
    return passed


def _details(conn: ArchicadConnection, chunk: list[str]) -> dict:
    try:
        # Only the type, so Tapir skips the costly floor-plan polygons.
        return conn.tapir("GetDetailsOfElements", {"elements": element_payload(chunk),
                                                   "fields": ["type"]})
    except APIErrorBase:
        # `fields` arrived in Tapir 1.5.9; an older add-on may refuse it.
        return conn.tapir("GetDetailsOfElements", {"elements": element_payload(chunk)})


def present(conn: ArchicadConnection, guids: list[str]) -> set[str]:
    """The elements a read of the active window's database finds.

    Tapir GetDetailsOfElements reads the current database, the one element
    commands act on, and answers an error entry for an element that is not
    there. The official GetTypesOfElements cannot stand in: live on 28.09.2026
    it answered 7203 "Element not supported" for every label, existing ones
    included. An element with no entry at all counts as present: unconfirmed
    is not gone.
    """
    found: set[str] = set()
    for start in range(0, len(guids), PROPERTY_FETCH_CHUNK):
        chunk = guids[start:start + PROPERTY_FETCH_CHUNK]
        items = _details(conn, chunk).get("detailsOfElements", [])
        for i, guid in enumerate(chunk):
            item = items[i] if i < len(items) else None
            if not isinstance(item, dict) or "error" not in item:
                found.add(guid)
    return found


def active_window(conn: ArchicadConnection) -> str | None:
    """The active window's type ("FloorPlan", "Layout", ...), or None if unreadable."""
    try:
        return conn.tapir("GetCurrentWindowType").get("currentWindowType")
    except (APIErrorBase, ArchicadUnavailableError):
        return None


def classify(conn: ArchicadConnection,
             guids: list[str]) -> tuple[dict[str, str], str | None]:
    """(guid -> the kind of refusal, the active window type).

    Kinds are NOT_FOUND, HIDDEN, UNRESERVED and LOCKED. Only refused elements
    are keyed. The window is read only when something is refused, so it is
    None both when nothing was and when it cannot be read. Without Tapir's
    FilterElements nothing is checked, and Archicad's own refusal is what the
    caller gets.
    """
    if not guids or not conn.tapir_command_available("FilterElements"):
        return {}, None
    editable = _passing(conn, guids, "IsEditable")
    refused = [g for g in guids if g not in editable]
    if not refused:
        return {}, None
    window = active_window(conn)
    found = present(conn, refused)
    existing = [g for g in refused if g in found]
    visible = _passing(conn, existing, "IsVisibleByLayer") if existing else set()
    shown = [g for g in existing if g in visible]
    if not shown:
        mine: set[str] = set()
    elif _is_teamwork(conn):
        mine = _in_my_workspace(conn, shown)
    else:
        # Outside Teamwork everything is "mine", so a refusal is a lock.
        mine = set(shown)
    kinds = {}
    for g in refused:
        if g not in found:
            kinds[g] = NOT_FOUND
        elif g not in visible:
            kinds[g] = HIDDEN
        else:
            kinds[g] = LOCKED if g in mine else UNRESERVED
    return kinds, window


def refusals(conn: ArchicadConnection,
             guids: list[str]) -> tuple[dict[str, str], str | None]:
    """(guid -> why Archicad would refuse to change it, the active window type).

    The text of classify(): only refused elements are keyed.
    """
    kinds, window = classify(conn, guids)
    return {g: reason_text(kind, window) for g, kind in kinds.items()}, window


def lock_reasons(conn: ArchicadConnection, entries: dict[str, dict],
                 layers: dict[int, dict]) -> dict[str, str]:
    """guid -> why a lock stops the change, for the locks IsEditable cannot name.

    `entries` are GetDetailsOfElements items (hotlinkId, layerIndex), `layers`
    is attributes.layer_table(). Checked in order, first match wins: inside a
    hotlinked module, on a locked layer, locked with Edit > Locking. The
    element lock is one property read for all of `entries`; when the property
    does not resolve, or the read would span too many elements, that check is
    left out rather than failing the caller.
    """
    guids = list(entries)
    try:
        values = fetch_property_values(conn, guids, [ELEMENT_LOCK_PROPERTY])
    except PropertyFetchTooWideError:
        values = {}
    reasons = {}
    for g, entry in entries.items():
        layer = layers.get(entry.get("layerIndex"), {})
        if entry.get("hotlinkId"):
            reasons[g] = HOTLINK_REASON
        elif layer.get("isLocked"):
            reasons[g] = layer_locked_reason(layer.get("name", ""))
        elif values.get(g, {}).get(ELEMENT_LOCK_PROPERTY) is True:
            reasons[g] = ELEMENT_LOCK_REASON
    return reasons
