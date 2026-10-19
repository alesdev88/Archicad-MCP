"""create_elements: every element Tapir can create, checked before it is sent.

Items keep Tapir's own field names and units (metres, radians), so the tool
follows Tapir's schema as it grows instead of keeping a parallel one. What the
tool adds is the checking an agent needs before a write: the item schema on
request, validation per item, favourites that exist, and hosts (the wall of a
door, the element a dimension measures) that Archicad would accept.
"""
from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from functools import lru_cache

import jsonschema
from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.editability import active_window, refusals
from archicad_mcp.extract import _fetch_types
from archicad_mcp.gateway.registry import (DEFINITIONS_DIR, LOCAL_DEFINITIONS,
                                           _load_js_json, build_registry)

FLOOR_PLAN = "FloorPlan"
LAYOUT = "Layout"

# The windows each placement works from. Live 19.10.2026 (AC 29, Tapir 1.5.10,
# MCP-Test): a wall created with the 3D window active landed on the floor plan;
# from a Section or a Layout window Tapir answered "Failed to create new Wall"
# for the item. 2D elements went into the active window's database (a text
# into the layout, a line into the section), so they need no check.
_WORKS_FROM = {FLOOR_PLAN: frozenset({FLOOR_PLAN, "3DModel"}),
               LAYOUT: frozenset({LAYOUT})}

# At most this many schema problems are reported per item: the first few say
# what is wrong, a long list of consequences of the same mistake does not.
PROBLEMS_PER_ITEM = 3


@dataclass(frozen=True)
class CreateType:
    command: str
    payload_key: str
    # The window the element is placed from (see _WORKS_FROM), or None for 2D
    # elements, which go into whichever drawing window is active.
    window: str | None = FLOOR_PLAN
    # Tapir's element type for GetFavoritesByType, when items can name a favourite.
    favorite_type: str | None = None
    # Item fields holding host element ids: "ownerWallId", or "witnessPoints[].elementId"
    # for a list of them.
    hosts: tuple[str, ...] = ()
    # The element types a host must be, when only some are accepted.
    host_types: frozenset[str] = frozenset()


_WALL = frozenset({"Wall"})

CREATE_TYPES: dict[str, CreateType] = {
    # Model elements
    "wall": CreateType("CreateWalls", "wallsData", favorite_type="Wall"),
    "beam": CreateType("CreateBeams", "beamsData", favorite_type="Beam"),
    "column": CreateType("CreateColumns", "columnsData", favorite_type="Column"),
    "slab": CreateType("CreateSlabs", "slabsData", favorite_type="Slab"),
    "roof": CreateType("CreateRoofs", "roofsData"),
    "morph": CreateType("CreateMorphs", "morphsData"),
    "mesh": CreateType("CreateMeshes", "meshesData", favorite_type="Mesh"),
    "stair": CreateType("CreateStairs", "stairsData", favorite_type="Stair"),
    "railing": CreateType("CreateRailings", "railingsData"),
    "zone": CreateType("CreateZones", "zonesData", favorite_type="Zone"),
    "object": CreateType("CreateObjects", "objectsData", favorite_type="Object"),
    "lamp": CreateType("CreateLamps", "lampsData", favorite_type="Lamp"),
    # Hosted in another element
    "door": CreateType("CreateDoors", "doorsData", favorite_type="Door",
                       hosts=("ownerWallId",), host_types=_WALL),
    "window": CreateType("CreateWindows", "windowsData", favorite_type="Window",
                         hosts=("ownerWallId",), host_types=_WALL),
    "opening": CreateType("CreateOpenings", "openingsData", hosts=("ownerElementId",)),
    # MEP (Archicad 28 and later)
    "mep_route": CreateType("CreateMEPRoutingElements", "routingElementsData"),
    "mep_element": CreateType("CreateMEPElements", "elementsData"),
    # Markers placed on the floor plan
    "section": CreateType("CreateSections", "sectionsData"),
    "interior_elevation": CreateType("CreateInteriorElevations", "interiorElevationsData"),
    "hotlink_instance": CreateType("CreateHotlinkInstances", "hotlinkInstances"),
    # 2D, in the active drawing window
    "line": CreateType("CreateLineElements", "linesData", window=None, favorite_type="Line"),
    "arc": CreateType("CreateArcs", "arcsData", window=None, favorite_type="Arc"),
    "circle": CreateType("CreateCircles", "circlesData", window=None, favorite_type="Circle"),
    "polyline": CreateType("CreatePolylines", "polylinesData", window=None,
                           favorite_type="PolyLine"),
    "spline": CreateType("CreateSplines", "splinesData", window=None, favorite_type="Spline"),
    "hatch": CreateType("CreateHatches", "hatchesData", window=None, favorite_type="Hatch"),
    "hotspot": CreateType("CreateHotspots", "hotspotsData", window=None,
                          favorite_type="Hotspot"),
    "text": CreateType("CreateTexts", "textsData", window=None, favorite_type="Text"),
    "label": CreateType("CreateLabels", "labelsData", window=None, favorite_type="Label",
                        hosts=("parentElementId",)),
    "keynote_label": CreateType("CreateKeynoteLabels", "labelsData", window=None),
    # Dimensions
    "dimension": CreateType("CreateAssociativeDimensions", "dimensionsData", window=None,
                            hosts=("witnessPoints[].elementId",)),
    "wall_thickness_dimension": CreateType("CreateWallThicknessDimensions",
                                           "dimensionsData", window=None,
                                           hosts=("wallId",), host_types=_WALL),
    "section_dimension": CreateType("CreateAssociativeDimensionsOnSection",
                                    "dimensionsData", window=None),
    # On a layout
    "drawing": CreateType("CreateDrawings", "drawingsData", window=LAYOUT),
}

_FAMILY_HINT = ("Attributes, layouts, views and other things that are not elements: "
                "use execute_write_api_command (describe_api_command shows the schema).")


def _lookup(element_type: str) -> tuple[str, CreateType] | None:
    """The type for a singular or plural name, or a Tapir command name."""
    key = element_type.strip().lower().replace("-", "_").replace(" ", "_")
    for name, spec in CREATE_TYPES.items():
        if key in (name, f"{name}s", f"{name}es", spec.command.lower()):
            return name, spec
    return None


@lru_cache(maxsize=1)
def _released() -> frozenset[str]:
    """Commands in the bundled release definitions."""
    groups = _load_js_json(DEFINITIONS_DIR / "command_definitions.js", "gCommands")
    return frozenset(c["name"] for g in groups for c in g.get("commands", []))


@lru_cache(maxsize=1)
def _fork_only() -> frozenset[str]:
    """Commands that exist only in the local Tapir fork, not in any release."""
    if not LOCAL_DEFINITIONS.exists():
        return frozenset()
    local = json.loads(LOCAL_DEFINITIONS.read_text(encoding="utf-8"))
    return frozenset(c["name"] for g in local.get("groups", []) for c in g.get("commands", []))


def _missing_command(name: str, spec: CreateType, version: str | None) -> dict:
    if spec.command in _fork_only() and spec.command not in _released():
        why = (f"{spec.command} exists only in the local Tapir fork build, and the "
               "running add-on is not that build.")
    else:
        why = (f"The running Tapir add-on has no {spec.command} (it arrived in Tapir "
               f"{version}). Update Tapir and restart Archicad.")
    return {"error": why, "element_type": name}


def _item_schema(spec: CreateType) -> dict:
    schema = build_registry()[spec.command].input_schema or {}
    return schema.get("properties", {}).get(spec.payload_key, {}).get("items", {})


def _problems(items: list[dict], schema: dict) -> list[dict]:
    validator = jsonschema.validators.validator_for(schema)(schema)
    problems = []
    for index, item in enumerate(items):
        errors = sorted(validator.iter_errors(item), key=jsonschema.exceptions.relevance)
        for error in errors[:PROBLEMS_PER_ITEM]:
            path = "/".join(str(p) for p in error.absolute_path)
            problems.append({"index": index, "path": path, "message": error.message})
    return problems


def _host_guids(item: dict, field: str) -> list[str]:
    """The GUIDs one host field of an item names; nothing when it is absent."""
    if "[]." in field:
        outer, inner = field.split("[].", 1)
        nested = item.get(outer)
        entries = nested if isinstance(nested, list) else []
        return [g for entry in entries if isinstance(entry, dict)
                for g in _host_guids(entry, inner)]
    value = item.get(field)
    guid = value.get("guid") if isinstance(value, dict) else None
    return [guid] if isinstance(guid, str) else []


def _favorite_skips(conn: ArchicadConnection, name: str, spec: CreateType,
                    items: list[dict]) -> dict[int, str]:
    wanted = {i: item["favoriteName"] for i, item in enumerate(items)
              if isinstance(item.get("favoriteName"), str)}
    if not wanted or spec.favorite_type is None:
        return {}
    try:
        response = conn.tapir("GetFavoritesByType", {"elementType": spec.favorite_type})
    except APIErrorBase:
        return {}  # unreadable: let Archicad judge the name
    known = response.get("favorites")
    if not isinstance(known, list):
        return {}
    skips = {}
    for index, favorite in wanted.items():
        if favorite in known:
            continue
        close = difflib.get_close_matches(favorite, known, n=3, cutoff=0.6)
        hint = f"; closest: {', '.join(close)}" if close else ""
        skips[index] = f"no {name} favourite named '{favorite}'{hint}"
    return skips


def _host_skips(conn: ArchicadConnection, name: str, spec: CreateType,
                items: list[dict], skipped: dict[int, str]) -> dict[int, str]:
    hosts = {i: [g for field in spec.hosts for g in _host_guids(item, field)]
             for i, item in enumerate(items) if i not in skipped}
    guids = sorted({g for gs in hosts.values() for g in gs})
    if not guids:
        return {}
    refused, _ = refusals(conn, guids)
    types = _fetch_types(conn, [g for g in guids if g not in refused]) \
        if spec.host_types else {}
    allowed = " or ".join(sorted(spec.host_types))
    skips = {}
    for index, item_hosts in hosts.items():
        for guid in item_hosts:
            if guid in refused:
                skips[index] = f"host {guid} is {refused[guid]}"
                break
            host_type = types.get(guid)
            if spec.host_types and host_type and host_type not in spec.host_types:
                skips[index] = (f"host {guid} is a {host_type}; a {name} goes in a "
                                f"{allowed}")
                break
    return skips


def _window_warnings(name: str, spec: CreateType, items: list[dict],
                     window: str | None) -> list[str]:
    if spec.window is None or window is None or window in _WORKS_FROM[spec.window]:
        return []
    if spec.window == LAYOUT:
        if all(isinstance(item.get("layoutDatabaseId"), dict) for item in items):
            return []
        return [f"A drawing goes on a layout, and the active window is {window}. Open "
                "the layout, or give layoutDatabaseId on every item, before committing."]
    return [f"A {name} is placed from the floor plan or the 3D window, and the active "
            f"window is {window}: Archicad would refuse it there. Switch windows before "
            "committing."]


def _commit(conn: ArchicadConnection, spec: CreateType, items: list[dict],
            ready: list[int], skipped: dict[int, str], warnings: list[str]) -> dict:
    response = conn.tapir(spec.command, {spec.payload_key: [items[i] for i in ready]})
    entries = response.get("elements", [])
    by_index: dict[str, str] = {}
    failed: dict[tuple, list[int]] = {}
    for position, index in enumerate(ready):
        entry = entries[position] if position < len(entries) else None
        guid = (entry or {}).get("elementId", {}).get("guid")
        if guid:
            by_index[str(index)] = guid
            continue
        error = (entry or {}).get("error") or {"code": None,
                                               "message": "no answer for this item"}
        failed.setdefault((error.get("code"), error.get("message")), []).append(index)
    result = {"dry_run": False, "created": len(by_index), "elements": list(by_index.values())}
    if failed or skipped:
        result["failed"] = [{"code": code, "message": message, "count": len(indices),
                             "indices": indices}
                            for (code, message), indices in failed.items()]
        result["skipped"] = _skipped_list(skipped)
        result["by_index"] = by_index
    if warnings:
        result["warnings"] = warnings
    return result


def _skipped_list(skipped: dict[int, str]) -> list[dict]:
    return [{"index": i, "reason": skipped[i]} for i in sorted(skipped)]


def create_elements(conn: ArchicadConnection, element_type: str,
                    items: list[dict], dry_run: bool = True) -> dict:
    found = _lookup(element_type)
    if found is None:
        return {"error": f"Unknown element_type '{element_type}'. {_FAMILY_HINT}",
                "valid_types": sorted(CREATE_TYPES)}
    name, spec = found
    info = build_registry()[spec.command]
    schema = _item_schema(spec)
    if not items:
        return {"element_type": name, "command": spec.command,
                "since_tapir": info.version, "item_schema": schema}
    if not conn.tapir_command_available(spec.command):
        if not conn.tapir_available():
            conn.tapir(spec.command)  # raises the install message
        return _missing_command(name, spec, info.version)
    problems = _problems(items, schema)
    if problems:
        return {"dry_run": dry_run, "element_type": name, "command": spec.command,
                "items": len(items), "created": 0, "problems": problems,
                "hint": "Nothing was sent. Call create_elements with items=[] for "
                        "this type's item schema."}
    skipped = _favorite_skips(conn, name, spec, items)
    skipped.update(_host_skips(conn, name, spec, items, skipped))
    window = active_window(conn)
    warnings = _window_warnings(name, spec, items, window)
    ready = [i for i in range(len(items)) if i not in skipped]
    if not dry_run:
        if not ready:
            return {"dry_run": False, "created": 0, "elements": [],
                    "skipped": _skipped_list(skipped), "warnings": warnings}
        return _commit(conn, spec, items, ready, skipped, warnings)
    return {"dry_run": True, "element_type": name, "command": spec.command,
            "items": len(items), "ready": len(ready), "skipped": _skipped_list(skipped),
            "warnings": warnings, "active_window": window,
            "payload": {spec.payload_key: [items[i] for i in ready]}}
