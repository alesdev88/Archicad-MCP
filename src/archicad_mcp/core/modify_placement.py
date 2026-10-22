"""`placement` in modify_elements: a placed learned part corrected by what the sketch shows.

The change takes the placement create_elements takes (core.create_placement), worked out
against the element as it stands. The part and its main GUID come from the element's own
details (libPart; Tapir gives ownUnID as the main GUID, in the form
GetAvailableLibraryParts lists it, so a record checks against it as it is). A door or
window keeps the wall it stands in (its owner) and, unless a change for it gives one,
its centerOffset. An object or lamp keeps its story and its elevation: placement moves and
turns it in plan only, and modify completes coordinates.z from the current value.

The fields replace `placement` before the changes are merged, so modify plans them as any
other field. A placement that cannot be worked out makes the whole call an error naming
the change: modify's skips name Tapir's fields, which the change did not give.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.create_placement import (
    NO_HEIGHT,
    OBJECT_KINDS,
    OPENING_KINDS,
    PLACEMENT_SCHEMAS,
    _check_identity,
    _derived_problem,
    _is_number,
    _record_for,
    _shape_problem,
    _Skip,
    _wall_ends,
)
from archicad_mcp.core.part_placement import object_fields, opening_fields
from archicad_mcp.core.part_records import PartRecord, PartRecords

# Element types as GetDetailsOfElements names them -> the kinds parts are learned as.
_KINDS = {"Object": "object", "Lamp": "lamp", "Door": "door", "Window": "window"}

_SCHEMA_HINT = ("modify_elements takes the placement create_elements takes, whose "
                "items=[] shows the placement_schema")
_KEEPS_HEIGHT = ("placement.center takes x and y only: a placed object keeps its story and "
                 "its elevation. To change the elevation, give story or coordinates in "
                 "another modify_elements call")
_KEEPS_STORY = ("placement keeps the element's story and elevation, and a story change "
                "would fight its coordinates: give story in another modify_elements call")


@dataclass
class Placed:
    """What a placement names besides Tapir's fields."""
    part: str
    leaves: dict | None = None        # {"state", "parameters"}, written after the commit
    warnings: list[str] = field(default_factory=list)    # for the dry run's warnings


@dataclass
class _Pending:
    number: int
    guid: str
    kind: str
    record: PartRecord
    entry: dict
    given: dict        # the element's fields merged across all its changes


def resolve_placements(conn: ArchicadConnection, changes: list[dict],
                       given: dict[str, dict], entries: dict[str, dict],
                       records: PartRecords | None
                       ) -> tuple[list[dict], dict[str, Placed]] | str:
    """(the changes with each placement replaced by Tapir's fields, GUID -> what it
    placed), or the error naming every change whose placement cannot be worked out.

    `changes` passed modify's merge check; `given` is each GUID's merged fields across
    all its changes, `entries` its GetDetailsOfElements item. A placement on an element
    the read did not find is dropped, so planning skips it as not found. `records` None
    reads the default store.
    """
    store = records if records is not None else PartRecords()
    resolved = list(changes)
    problems: list[str] = []
    pending: list[_Pending] = []
    first: dict[str, dict] = {}
    for number, change in enumerate(changes, 1):
        if "placement" not in change:
            continue
        guid = change["guid"].strip().upper()
        resolved[number - 1] = {k: v for k, v in change.items() if k != "placement"}
        if guid not in entries:
            continue
        placement = change["placement"]
        if first.setdefault(guid, placement) != placement:
            problems.append(f"change {number} ({guid}): another change gives this element "
                            "another placement; give it one")
            continue
        try:
            kind, record = _learned(placement, given[guid], entries[guid], store)
        except _Skip as problem:
            problems.append(f"change {number} ({guid}): {problem}")
            continue
        pending.append(_Pending(number, guid, kind, record, entries[guid], given[guid]))

    openings = [p for p in pending if p.kind in OPENING_KINDS]
    names = " or ".join(sorted({p.kind for p in openings}))
    walls = _wall_ends(conn, names, [_owner(p.entry) for p in openings]) if openings else {}
    placed: dict[str, Placed] = {}
    for p in pending:
        placement = changes[p.number - 1]["placement"]
        found: list[str] = []
        try:
            fields = (_object_fields(p.record, placement, p.entry, found)
                      if p.kind in OBJECT_KINDS
                      else _opening_fields(p.record, placement, p.given, p.entry, walls))
        except (_Skip, ValueError) as problem:
            problems.append(f"change {p.number} ({p.guid}): {problem}")
            continue
        resolved[p.number - 1].update(fields)
        state = placement.get("leaves")
        leaves = (None if state is None
                  else {"state": state, "parameters": p.record.leaf_states[state]})
        placed[p.guid] = Placed(p.record.name, leaves, found)
    if problems:
        return "\n".join(problems)
    return resolved, placed


def _learned(placement: object, given: dict, entry: dict,
             store: PartRecords) -> tuple[str, PartRecord]:
    """(the element's kind, the record its placement is worked out from), once every
    check that needs no other read passes."""
    kind = _KINDS.get(entry.get("type"))
    if kind is None:
        raise _Skip("placement places an object, lamp, door or window; this element is a "
                    f"{entry.get('type')}")
    center = placement.get("center") if isinstance(placement, dict) else None
    if kind in OBJECT_KINDS and isinstance(center, dict) and "z" in center:
        raise _Skip(_KEEPS_HEIGHT)
    problem = (_shape_problem(placement, PLACEMENT_SCHEMAS[kind], _SCHEMA_HINT)
               or _derived_problem(kind, given))
    if problem is not None:
        raise _Skip(problem)
    if kind in OBJECT_KINDS and "story" in given:
        raise _Skip(_KEEPS_STORY)
    details = entry.get("details") or {}
    part = details.get("libPart") if isinstance(details.get("libPart"), dict) else {}
    name = part.get("name")
    if not isinstance(name, str) or not name:
        raise _Skip("its details name no library part (libPart), so no record can be found "
                    "for it")
    record = _record_for(store, kind, name, placement.get("leaves"))
    own = part.get("ownUnID")
    guid = own.get("guid") if isinstance(own, dict) else None
    if not isinstance(guid, str) or not guid:
        raise _Skip(f"its details give no GUID for '{name}' (libPart.ownUnID), so its "
                    "record cannot be checked against the part")
    _check_identity(record, guid)
    if kind in OPENING_KINDS and _owner(entry) is None:
        raise _Skip("its details name no wall it stands in (ownerElementId)")
    return kind, record


def _object_fields(record: PartRecord, placement: dict, entry: dict,
                   warnings: list[str]) -> dict:
    """coordinates {x, y}, angle, reflected and dimensions: modify completes the z of
    coordinates from the current origin, so the object keeps its story and elevation."""
    size = placement["size"]
    height = size.get("height", record.height)
    if height is None:
        current = (entry.get("details") or {}).get("dimensions")
        height = current.get("z") if isinstance(current, dict) else None
        if not _is_number(height):
            raise _Skip(NO_HEIGHT)
    center = {"x": placement["center"]["x"], "y": placement["center"]["y"]}
    fields = object_fields(record, center, size["length"], size["width"], height,
                           placement["head"], placement.get("mirrored", False), warnings)
    fields["coordinates"] = {axis: fields["coordinates"][axis] for axis in ("x", "y")}
    return fields


def _opening_fields(record: PartRecord, placement: dict, given: dict, entry: dict,
                    walls: dict[str, tuple[dict, dict] | str]) -> dict:
    """reflected and oSide, the hinge read against the centre the element will have: a
    centerOffset from any of its changes, else the current one."""
    ends = walls[_owner(entry)]
    if isinstance(ends, str):
        raise _Skip(ends)
    offset = given.get("centerOffset", (entry.get("details") or {}).get("centerOffset"))
    if not _is_number(offset):
        raise _Skip("the hinge side needs centerOffset as a number (metres along the "
                    "wall from its begin point to the opening's centre), in a change or "
                    "in its details")
    begin, end = ends
    return opening_fields(record, begin, end, offset, placement["hinge_near"],
                          placement["opens_toward"])


def _owner(entry: dict) -> str | None:
    owner = (entry.get("details") or {}).get("ownerElementId")
    guid = owner.get("guid") if isinstance(owner, dict) else None
    return guid if isinstance(guid, str) and guid else None
