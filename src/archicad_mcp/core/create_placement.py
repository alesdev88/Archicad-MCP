"""`placement` in create_elements: a learned part placed by what the sketch shows.

An object or lamp item gives the footprint's centre and size and the way its head
points; a door or window item gives a point near the jamb it hinges at, one on the
side it opens to, and which leaves open. The part's record (learn_library_part,
record_library_part) turns them into Tapir's own fields, which replace `placement`
before the item schema is checked, so Tapir never sees the field. An item that
cannot be placed so is skipped with the reason; the rest go ahead. modify_elements
corrects a placed part with the same placement and these checks (core.modify_placement).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import jsonschema
from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.part_placement import object_fields, opening_fields
from archicad_mcp.core.part_records import (
    PartRecord,
    PartRecords,
    RecordsFileError,
    identity_problem,
)
from archicad_mcp.extract import element_payload

if TYPE_CHECKING:
    from archicad_mcp.core.create import LoadedParts

OBJECT_KINDS = ("object", "lamp")
OPENING_KINDS = ("door", "window")

_NUMBER = {"type": "number"}
_LENGTH = {"type": "number", "exclusiveMinimum": 0}


def _point(description: str) -> dict:
    return {"type": "object", "description": description,
            "properties": {"x": _NUMBER, "y": _NUMBER},
            "required": ["x", "y"], "additionalProperties": False}


OBJECT_PLACEMENT = {
    "type": "object",
    "description": ("In place of coordinates, angle, reflected and dimensions, for a part "
                    "learned with learn_library_part: its footprint as the sketch shows "
                    "it, in metres. The item keeps libraryPartName."),
    "properties": {
        "center": {"type": "object",
                   "description": ("The middle of the footprint in plan; z is the height "
                                   "of the insertion point above the floor of its story "
                                   "(default 0): the item's floorIndex, else the story "
                                   "the floor plan shows."),
                   "properties": {"x": _NUMBER, "y": _NUMBER, "z": _NUMBER},
                   "required": ["x", "y"], "additionalProperties": False},
        "size": {"type": "object",
                 "description": ("length along the head direction, width across it, "
                                 "height (default: the height recorded for the part)."),
                 "properties": {"length": _LENGTH, "width": _LENGTH, "height": _LENGTH},
                 "required": ["length", "width"], "additionalProperties": False},
        "head": _point("The way the head (a bed's pillow end) or the front points, as a "
                       "plan direction; only its direction counts."),
        "mirrored": {"type": "boolean",
                     "description": "Mirrored, as the sketch draws it (default false)."},
    },
    "required": ["center", "size", "head"],
    "additionalProperties": False,
}

OPENING_PLACEMENT = {
    "type": "object",
    "description": ("In place of reflected and oSide, for a part learned with "
                    "learn_library_part: the hinge and swing as the sketch shows them. "
                    "The item keeps ownerWallId, centerOffset and libraryPartName."),
    "properties": {
        "hinge_near": _point("A plan point on the hinge side of the opening's centre, "
                             "such as the jamb the leaf hangs on."),
        "opens_toward": _point("A plan point on the side of the wall the leaf opens to, "
                               "such as inside the room it swings into."),
        "leaves": {"type": "string",
                   "description": ("Which leaves open: a leaf_states name from the part's "
                                   "record, set right after the create.")},
    },
    "required": ["hinge_near", "opens_toward"],
    "additionalProperties": False,
}

PLACEMENT_SCHEMAS = {"object": OBJECT_PLACEMENT, "lamp": OBJECT_PLACEMENT,
                     "door": OPENING_PLACEMENT, "window": OPENING_PLACEMENT}

# The Tapir fields a placement works out: an item gives the one or the other.
_DERIVED = {"object": ("coordinates", "angle", "reflected", "dimensions"),
            "lamp": ("coordinates", "angle", "reflected", "dimensions"),
            "door": ("reflected", "oSide"),
            "window": ("reflected", "oSide")}

NO_HEIGHT = "give size.height in metres: Tapir 1.7.2 cannot read a part's own height"

_SCHEMA_HINT = "create_elements with items=[] shows the placement_schema"


class _Skip(Exception):
    """The item cannot be placed from its placement; the message is the reason."""


def resolve_placements(conn: ArchicadConnection, name: str, items: list[dict],
                       records: PartRecords | None, parts: LoadedParts,
                       ) -> tuple[list[dict], dict[int, str], list[dict], dict[int, dict],
                                  dict[int, list[str]]]:
    """(the items as Tapir takes them, skips, the placements report, the leaf state to
    write per item index, the warnings per item index).

    Items without placement come back as they were. A skipped item comes back without
    its placement and is not sent. `records` None reads the default store.
    """
    schema = PLACEMENT_SCHEMAS.get(name)
    placing = [i for i, item in enumerate(items) if "placement" in item]
    if schema is None or not placing:
        return items, {}, [], {}, {}
    store = records if records is not None else PartRecords()
    resolved = list(items)
    skipped: dict[int, str] = {}
    learned: dict[int, PartRecord] = {}
    for index in placing:
        item = items[index]
        resolved[index] = {k: v for k, v in item.items() if k != "placement"}
        try:
            learned[index] = _learned(name, item, schema, store, parts)
        except _Skip as skip:
            skipped[index] = str(skip)
    walls = (_wall_ends(conn, name, [_wall_guid(items[i]) for i in learned])
             if name in OPENING_KINDS else {})
    stories = _stories(conn) if name in OBJECT_KINDS and learned else None
    placements: list[dict] = []
    leaf_writes: dict[int, dict] = {}
    warnings: dict[int, list[str]] = {}
    for index, record in learned.items():
        item, placement = items[index], items[index]["placement"]
        found: list[str] = []
        try:
            fields = (_object_fields(record, item, placement, stories, found)
                      if name in OBJECT_KINDS
                      else _opening_fields(record, item, placement, walls))
        except (_Skip, ValueError) as error:
            skipped[index] = str(error)
            continue
        if found:
            warnings[index] = [f"Item {index} ('{record.name}') {text}" for text in found]
        resolved[index].update(fields)
        derived = dict(fields)
        state = placement.get("leaves")
        if state is not None:
            leaf_writes[index] = derived["leaves"] = {"state": state,
                                                      "parameters": record.leaf_states[state]}
        placements.append({"index": index, "part": item["libraryPartName"],
                           "derived": derived})
    return resolved, skipped, placements, leaf_writes, warnings


def _learned(name: str, item: dict, schema: dict, store: PartRecords,
             parts: LoadedParts) -> PartRecord:
    """The record the item's placement is worked out from, once every check passes."""
    problem = _shape_problem(item["placement"], schema)
    if problem is not None:
        raise _Skip(problem)
    part = item.get("libraryPartName")
    if not isinstance(part, str):
        raise _Skip("placement needs libraryPartName, the part learned with "
                    "learn_library_part")
    problem = _derived_problem(name, item)
    if problem is not None:
        raise _Skip(problem)
    if name in OPENING_KINDS and (_wall_guid(item) is None
                                  or not _is_number(item.get("centerOffset"))):
        raise _Skip("placement needs the item's ownerWallId and centerOffset (metres along "
                    "the wall from its begin point to the opening's centre)")
    record = _record_for(store, name, part, item["placement"].get("leaves"))
    guid, reason = parts.guid_of(part)
    if guid is None:
        raise _Skip(reason)
    _check_identity(record, guid)
    return record


def _derived_problem(name: str, fields: dict) -> str | None:
    """Why fields that also give what placement works out cannot go, or None."""
    given = [field for field in _DERIVED[name] if field in fields]
    return f"placement sets {_listed(given)}; leave one out" if given else None


def _record_for(store: PartRecords, name: str, part: str, state: str | None) -> PartRecord:
    """The record of `part` for a `name` placement, once it can place one: learned as
    that kind, nothing missing, and the leaf state named, if any, recorded."""
    try:
        record = store.get(part)
    except RecordsFileError as error:
        raise _Skip(str(error)) from error
    if record is None:
        raise _Skip(f"'{part}' has not been learned: call learn_library_part for it, then "
                    "record_library_part with what its image shows")
    if record.kind != name:
        raise _Skip(f"'{part}' was learned as a {record.kind}, not a {name}: learn it as a "
                    f"{name} with learn_library_part")
    missing = record.missing()
    if "footprint" in missing:
        raise _Skip(f"'{part}' has no footprint: learning found that its plan size does not "
                    "follow its own A and B, so placement cannot place it; place it by "
                    "coordinates, angle, reflected and dimensions")
    if missing:
        raise _Skip(f"the record of '{part}' lacks {_listed(missing)}: give them with "
                    "record_library_part (learn_library_part shows the part again)")
    states = record.leaf_states or {}
    if state is not None and state not in states:
        known = (f"recorded: {', '.join(sorted(states))}" if states
                 else "none is recorded; give leaf_states with record_library_part")
        raise _Skip(f"'{part}' has no leaf state '{state}' ({known})")
    return record


def _check_identity(record: PartRecord, guid: str) -> None:
    """Refuses a record learned for another part than the one with this main GUID."""
    problem = identity_problem(record, guid)
    if problem is not None:
        raise _Skip(f"{problem.removesuffix('.')} (learn_library_part).")


def _shape_problem(placement: object, schema: dict, hint: str = _SCHEMA_HINT) -> str | None:
    """Where the placement departs from its schema, or None."""
    validator = jsonschema.validators.validator_for(schema)(schema)
    error = jsonschema.exceptions.best_match(validator.iter_errors(placement))
    if error is None:
        return None
    where = "/".join(["placement", *(str(p) for p in error.absolute_path)])
    return f"{where}: {error.message}; {hint}"


def _object_fields(record: PartRecord, item: dict, placement: dict,
                   stories: tuple[int | None, dict[int, float]] | str,
                   warnings: list[str]) -> dict:
    """Tapir's fields, on the floor of the item's story: Tapir reads coordinates.z as
    absolute, and without floorIndex takes the story from it, so z 0 would put the part
    on the ground floor whatever story the plan shows."""
    size = placement["size"]
    height = size.get("height", record.height)
    if height is None:
        raise _Skip(NO_HEIGHT)
    if isinstance(stories, str):
        raise _Skip(stories)
    active, levels = stories
    story = item.get("floorIndex", active)
    if story is None:
        raise _Skip("Archicad did not say which story the floor plan shows, so the floor "
                    "the part stands on is not known: give floorIndex on the item")
    level = levels.get(story) if _is_number(story) else None
    if level is None:
        raise _Skip(f"Archicad gives no level for story {story}, so the floor the part "
                    "stands on is not known")
    center = placement["center"]
    on_floor = {"x": center["x"], "y": center["y"], "z": level + center.get("z", 0.0)}
    fields = object_fields(record, on_floor, size["length"], size["width"], height,
                           placement["head"], placement.get("mirrored", False), warnings)
    fields["floorIndex"] = story
    return fields


def _stories(conn: ArchicadConnection) -> tuple[int | None, dict[int, float]] | str:
    """(the story the floor plan shows, each story's level by index), or why they are
    not known. Read as learn_library_part reads them (part_learning._current_story)."""
    try:
        answer = conn.tapir("GetStories")
    except APIErrorBase as exc:
        return (f"the stories could not be read ({exc.message}), so the floor the part "
                "stands on is not known")
    levels = {s["index"]: float(s["level"]) for s in answer.get("stories") or []
              if isinstance(s, dict) and _is_number(s.get("index"))
              and _is_number(s.get("level"))}
    active = answer.get("actStory")
    return (active if _is_number(active) else None), levels


def _opening_fields(record: PartRecord, item: dict, placement: dict,
                    walls: dict[str, tuple[dict, dict] | str]) -> dict:
    ends = walls[_wall_guid(item)]
    if isinstance(ends, str):
        raise _Skip(ends)
    begin, end = ends
    return opening_fields(record, begin, end, item["centerOffset"], placement["hinge_near"],
                          placement["opens_toward"])


def _wall_ends(conn: ArchicadConnection, name: str,
               walls: list[str]) -> dict[str, tuple[dict, dict] | str]:
    """Wall GUID -> its (begin, end) points in plan, or why they are not known. `name`
    is what stands in them, for the reason a host that is no wall gives."""
    guids = list(dict.fromkeys(walls))
    if not guids:
        return {}
    try:
        answer = conn.tapir("GetDetailsOfElements", {"elements": element_payload(guids)})
    except APIErrorBase as exc:
        return dict.fromkeys(guids, f"the walls could not be read ({exc.message})")
    # Matched by position: Tapir answers GUIDs in upper case, errors with none.
    entries = answer.get("detailsOfElements") or []
    return {guid: _ends(name, guid, entries[i] if i < len(entries) else None)
            for i, guid in enumerate(guids)}


def _ends(name: str, guid: str, entry: object) -> tuple[dict, dict] | str:
    if not isinstance(entry, dict) or "error" in entry:
        error = entry.get("error") if isinstance(entry, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        return f"wall {guid} could not be read ({message or 'no answer for it'})"
    if entry.get("type") != "Wall":
        return f"host {guid} is a {entry.get('type')}; a {name} goes in a Wall"
    details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
    begin, end = details.get("begCoordinate"), details.get("endCoordinate")
    if not (_is_point(begin) and _is_point(end)):
        return f"wall {guid} has no begin and end point in its details"
    return begin, end


def _wall_guid(item: dict) -> str | None:
    owner = item.get("ownerWallId")
    guid = owner.get("guid") if isinstance(owner, dict) else None
    return guid if isinstance(guid, str) else None


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_point(value: object) -> bool:
    return isinstance(value, dict) and _is_number(value.get("x")) and _is_number(value.get("y"))


def _listed(names: list[str]) -> str:
    """'a', 'a and b', 'a, b and c'."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
