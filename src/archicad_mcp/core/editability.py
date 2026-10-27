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
workspace, and which window is active. For a locked element, lock_reasons()
names the lock where it can: a hotlinked module, a locked layer, or the
element lock.
"""
from __future__ import annotations

from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.connection import ArchicadConnection, ArchicadUnavailableError
from archicad_mcp.core.attributes import layer_table
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
ELSEWHERE = "elsewhere"

_HEAD = {NOT_FOUND: "not found", HIDDEN: "not editable",
         UNRESERVED: "not editable", LOCKED: "not editable",
         ELSEWHERE: "in another view's database"}
_CAUSE = {
    NOT_FOUND: ("it does not exist (deleted already, or a wrong GUID) or belongs "
                "to another view"),
    ELSEWHERE: ("it belongs to a layout, section, elevation, detail or worksheet, "
                "not to the active window, and Archicad changes only the active "
                "window's elements; open its view and retry"),
    HIDDEN: "it is on a hidden layer; turn the layer on in the active view and retry",
    UNRESERVED: "it is not reserved in Teamwork; reserve it (reserve_elements) and retry",
    LOCKED: ("it is inside a hotlinked module, on a locked layer, or otherwise "
             "locked, so Archicad would refuse the change"),
}

# The element lock (Edit > Locking) as a built-in property. Tapir has no read
# of it (API_Elem_Head.lockId is never read in its source, 1.5.9 to 1.7.1).
# Live on MCP-Test 19.10.2026: true after LockElements, false after
# UnlockElements, and FilterElements IsEditable false while locked, so this
# names the cause of a refusal IsEditable already makes.
ELEMENT_LOCK_PROPERTY = "General_Locked"

# What lock_reasons() reads of an element: hotlinkId arrived with `fields` in
# Tapir 1.5.9 and is absent on an element outside a hotlink.
LOCK_FIELDS = ["type", "layerIndex", "hotlinkId"]

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
    """The elements that pass one Tapir FilterElements filter, uppercase.

    Tapir answers every GUID uppercase whatever case it was sent in (live
    19.10.2026), so callers compare with g.upper().
    """
    passed: set[str] = set()
    for start in range(0, len(guids), FILTER_CHUNK):
        chunk = guids[start:start + FILTER_CHUNK]
        response = conn.tapir("FilterElements", {"elements": element_payload(chunk),
                                                 "filters": [name]})
        passed.update(e["elementId"]["guid"].upper() for e in response.get("elements", []))
    return passed


def _details(conn: ArchicadConnection, chunk: list[str],
             fields: list[str] | None = None) -> dict:
    try:
        # Only the fields needed (the type by default), so Tapir skips the
        # costly floor-plan polygons.
        return conn.tapir("GetDetailsOfElements", {"elements": element_payload(chunk),
                                                   "fields": fields or ["type"]})
    except APIErrorBase:
        # `fields` arrived in Tapir 1.5.9; an older add-on may refuse it.
        return conn.tapir("GetDetailsOfElements", {"elements": element_payload(chunk)})


def present(conn: ArchicadConnection, guids: list[str]) -> set[str]:
    """The elements a Tapir GetDetailsOfElements read finds.

    It answers an error entry for an element that does not exist. It is a
    database check only from windows other than the floor plan: from a Layout
    it did not find floor-plan labels (28.09.2026), but from the floor plan it
    found layout and section Texts (19.10.2026); _elsewhere() covers that
    side. The official GetTypesOfElements cannot stand in: live on 28.09.2026
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
    refused = [g for g in guids if g.upper() not in editable]
    if not refused:
        return {}, None
    window = active_window(conn)
    found = present(conn, refused)
    existing = [g for g in refused if g in found]
    visible = _passing(conn, existing, "IsVisibleByLayer") if existing else set()
    shown = [g for g in existing if g.upper() in visible]
    if not shown:
        mine: set[str] = set()
    elif _is_teamwork(conn):
        mine = {g.upper() for g in _in_my_workspace(conn, shown)}
    else:
        # Outside Teamwork everything is "mine", so a refusal is a lock.
        mine = {g.upper() for g in shown}
    kinds = {}
    for g in refused:
        if g not in found:
            kinds[g] = NOT_FOUND
        elif g.upper() not in visible:
            kinds[g] = HIDDEN
        else:
            kinds[g] = LOCKED if g.upper() in mine else UNRESERVED
    return kinds, window


def add_elsewhere(conn: ArchicadConnection, passed: list[str], kinds: dict[str, str],
                  window: str | None) -> str | None:
    """Key the elements of `passed` the active window's database lacks as
    ELSEWHERE in `kinds`, and return the window type, read now if this is the
    first refusal.

    `passed` are elements classify() did not refuse.
    """
    found = _elsewhere(conn, passed)
    if found and not kinds:
        window = active_window(conn)
    kinds.update(dict.fromkeys(found, ELSEWHERE))
    return window


def _elsewhere(conn: ArchicadConnection, passed: list[str]) -> set[str]:
    """The elements IsEditable passed that the active window's database lacks.

    From the floor plan every read reaches the other databases. Live on
    MCP-Test, 19.10.2026: IsEditable passed a layout Text and a section Text,
    GetDetailsOfElements found them, MoveElements answered success and moved
    neither, DeleteElements left both. With the floor plan active, Tapir 1.7.3
    GetContainingDatabases names each element's database. Otherwise Tapir
    GetAllElements lists only the active window's database, so membership in it
    is the check. An empty or failed listing checks nothing: the active
    database holds the editable elements, so it cannot be empty.
    """
    if not passed or not conn.tapir_command_available("FilterElements"):
        return set()
    if conn.tapir_command_available("GetContainingDatabases") \
            and active_window(conn) == "FloorPlan":
        found = _off_the_floor_plan(conn, passed)
        if found is not None:
            return found
    try:
        # Tapir 1.7.3 leaves zone stamps and stair parts out unless asked (#1471)
        response = conn.tapir("GetAllElements", {"filters": ["IncludeSubElemObjects"]})
        listed = {e["elementId"]["guid"].upper() for e in response.get("elements", [])}
    except APIErrorBase:
        return set()
    if not listed:
        return set()
    return {g for g in passed if g.upper() not in listed}


def _off_the_floor_plan(conn: ArchicadConnection, guids: list[str]) -> set[str] | None:
    """The elements GetContainingDatabases places outside the floor plan database,
    or None when it cannot answer. One not found is left to classify()."""
    found = set()
    try:
        for start in range(0, len(guids), FILTER_CHUNK):
            chunk = guids[start:start + FILTER_CHUNK]
            databases = conn.tapir("GetContainingDatabases",
                                   {"elements": element_payload(chunk)}).get("databases", [])
            found.update(g for g, db in zip(chunk, databases)
                         if db.get("windowType") not in (None, "FloorPlan"))
    except (APIErrorBase, ArchicadUnavailableError):
        return None
    return found


def refusals(conn: ArchicadConnection,
             guids: list[str]) -> tuple[dict[str, str], str | None]:
    """(guid -> why Archicad would refuse to change it, the active window type).

    The text of classify(), plus the elements of another database that
    IsEditable passed, and the lock a locked element is held by where it can be
    read. Only refused elements are keyed.
    """
    kinds, window = classify(conn, guids)
    window = add_elsewhere(conn, [g for g in guids if g not in kinds], kinds, window)
    texts = {g: reason_text(kinds[g], window) for g in guids if g in kinds}
    locked = [g for g in texts if kinds[g] == LOCKED]
    if locked:
        texts.update(_named_locks(conn, locked))
    return texts, window


def _named_locks(conn: ArchicadConnection, guids: list[str]) -> dict[str, str]:
    """lock_reasons() for elements IsEditable refused as locked.

    Best effort: a Tapir that cannot read the details leaves every element
    with the general reason, one without GetLayers leaves out the layer check.
    """
    entries: dict[str, dict] = {}
    try:
        for start in range(0, len(guids), PROPERTY_FETCH_CHUNK):
            chunk = guids[start:start + PROPERTY_FETCH_CHUNK]
            items = _details(conn, chunk, LOCK_FIELDS).get("detailsOfElements", [])
            entries.update((g, item) for g, item in zip(chunk, items)
                           if isinstance(item, dict) and "error" not in item)
    except APIErrorBase:
        return {}
    try:
        layers = layer_table(conn)
    except APIErrorBase:
        layers = {}
    return lock_reasons(conn, entries, layers)


def lock_reasons(conn: ArchicadConnection, entries: dict[str, dict],
                 layers: dict[int, dict]) -> dict[str, str]:
    """guid -> why a lock stops the change, for the locks IsEditable cannot name.

    `entries` are GetDetailsOfElements items (hotlinkId, layerIndex), `layers`
    is attributes.layer_table(). Checked in order, first match wins: inside a
    hotlinked module, on a locked layer, locked with Edit > Locking. The
    element lock is one property read, for the entries neither of the others
    explains (property reads are the class that has crashed Archicad); when
    the property does not resolve, the read would span too many elements, or
    Archicad refuses it, that check is left out rather than failing the caller.
    """
    reasons = {}
    for g, entry in entries.items():
        layer = layers.get(entry.get("layerIndex"), {})
        if entry.get("hotlinkId"):
            reasons[g] = HOTLINK_REASON
        elif layer.get("isLocked"):
            reasons[g] = layer_locked_reason(layer.get("name", ""))
    rest = [g for g in entries if g not in reasons]
    if not rest:
        return reasons
    try:
        values = fetch_property_values(conn, rest, [ELEMENT_LOCK_PROPERTY])
    except (PropertyFetchTooWideError, APIErrorBase):
        values = {}
    for g in rest:
        if values.get(g, {}).get(ELEMENT_LOCK_PROPERTY) is True:
            reasons[g] = ELEMENT_LOCK_REASON
    return reasons
