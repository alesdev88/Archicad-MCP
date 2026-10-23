"""create_elements: every element Tapir can create, checked before it is sent.

Items keep Tapir's own field names and units (metres, radians), so the tool
follows Tapir's schema as it grows instead of keeping a parallel one. What the
tool adds is the checking an agent needs before a write: the item schema on
request, validation per item, favourites and library parts that exist, and
hosts (the wall of a door, the element a dimension measures) that Archicad
would accept. An object, lamp, door or window of a learned part may give
`placement` instead of some of Tapir's fields (core/create_placement.py).
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass

import jsonschema
from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.create_placement import PLACEMENT_SCHEMAS, resolve_placements
from archicad_mcp.core.editability import active_window, refusals
from archicad_mcp.core.part_placement import write_leaf_state
from archicad_mcp.core.part_records import PartRecords
from archicad_mcp.extract import _fetch_types
from archicad_mcp.gateway.registry import build_registry
from archicad_mcp.tapir_requirements import (
    definitions_version,
    older_than_definitions,
    require_tapir_command,
)

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

# At most this many item indices are named in one warning.
INDICES_NAMED = 10

# Types placed from a library part through a tool. Tapir starts every item from
# the tool's current settings (ACAPI_Element_GetDefaults) and only swaps in the
# named part, so a size the item leaves out is the tool's (live 29.09.2026,
# AC 27, Tapir 1.5.9: the Object tool's A, B and height, not the part's own).
_TOOL_SIZED = {"object": "Object tool", "lamp": "Lamp tool"}

# Openings take their sill from the tool too (live 21.10.2026, Tapir 1.7.2: a door
# placed by libraryPartName without sillHeight came in with the Door tool's 0.1 m).
_TOOL_SILL = {"door": "Door tool", "window": "Window tool"}

# Fields Archicad drops when it creates an object or lamp.
_DISCARDED = {
    # Live 16.09.2026 (CVP shading panels): in a batch each object took the
    # previous one's offset, so the panels could not be stretched.
    "offset": ("Archicad discards it (Tapir's own definition says so), and in a "
               "batch each object took the previous one's offset, which puts the "
               "stretch hotspots off the object. Leave it out."),
    "useFixedAngle": "Archicad discards it (Tapir's own definition says so). Leave it out.",
}


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
    # The oldest Archicad that creates the type. Tapir registers some of these
    # commands on older versions too and refuses them only once they are sent.
    archicad: int | None = None


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
    "curtain_wall": CreateType("CreateCurtainWalls", "curtainWallsData",
                               favorite_type="CurtainWall"),
    "grid_element": CreateType("CreateGridElements", "gridElements"),
    "object": CreateType("CreateObjects", "objectsData", favorite_type="Object"),
    "lamp": CreateType("CreateLamps", "lampsData", favorite_type="Lamp"),
    # Hosted in another element
    "door": CreateType("CreateDoors", "doorsData", favorite_type="Door",
                       hosts=("ownerWallId",), host_types=_WALL),
    "window": CreateType("CreateWindows", "windowsData", favorite_type="Window",
                         hosts=("ownerWallId",), host_types=_WALL),
    "opening": CreateType("CreateOpenings", "openingsData", hosts=("ownerElementId",)),
    # MEP (Archicad 28 and later)
    "mep_route": CreateType("CreateMEPRoutingElements", "routingElementsData", archicad=28),
    "mep_element": CreateType("CreateMEPElements", "elementsData", archicad=28),
    # Electrical. Live 21.10.2026 (Tapir 1.7.2): registered on Archicad 29 as
    # well, where each answers "This command requires Archicad 30 or newer."
    "electrical_panel": CreateType("CreateElectricalPanels", "electricalPanelsData",
                                   archicad=30),
    # circuitId is no host: a circuit is not a placed element, GetAllElements
    # does not list it, and the other-database guard would skip every one.
    "electrical_device": CreateType("CreateElectricalDevices", "electricalDevicesData",
                                    archicad=30),
    # A circuit is not placed, so no window applies; its panel is.
    "electrical_circuit": CreateType("CreateElectricalCircuits", "electricalCircuitsData",
                                     window=None, hosts=("electricalPanelId",),
                                     archicad=30),
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
    "level_dimension": CreateType("CreateLevelDimensions", "levelDimensionsData",
                                  window=None, favorite_type="LevelDimension",
                                  hosts=("parentElementId",)),
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


def _item_schema(spec: CreateType) -> dict:
    schema = build_registry()[spec.command].input_schema or {}
    return schema.get("properties", {}).get(spec.payload_key, {}).get("items", {})


def _problems(items: list[dict], schema: dict, skipped=()) -> list[dict]:
    """Schema problems per item; `skipped` items are not sent, so not checked."""
    validator = jsonschema.validators.validator_for(schema)(schema)
    problems = []
    for index, item in enumerate(items):
        if index in skipped:
            continue
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


def _named(indices: list[int]) -> str:
    """'item 3' or 'items 0, 1, 2', and how many more past the first few."""
    shown = ", ".join(str(i) for i in indices[:INDICES_NAMED])
    more = len(indices) - INDICES_NAMED
    word = "item" if len(indices) == 1 else "items"
    return f"{word} {shown}" + (f" and {more} more" if more > 0 else "")


def closest_names(name: str, names: list[str]) -> str:
    """'; closest: a, b, c' for the loaded part names nearest to name, or ''."""
    close = difflib.get_close_matches(name, names, n=3, cutoff=0.6)
    return f"; closest: {', '.join(close)}" if close else ""


class LoadedParts:
    """GetAvailableLibraryParts, asked at most once per call and only when an item
    needs it (the answer is about 1.2 MB on MCP-Test): the name check and
    placement's identity check share it."""

    def __init__(self, conn: ArchicadConnection):
        self._conn = conn
        self._asked = False
        self._answer: dict | None = None
        self._parts: list[dict] = []

    def answer(self) -> dict | None:
        """The listing, or None when Archicad gives none."""
        if not self._asked:
            self._asked = True
            try:
                response = self._conn.tapir("GetAvailableLibraryParts", {})
            except APIErrorBase:
                response = None
            if isinstance(response, dict) and isinstance(response.get("libraryParts"), list):
                self._answer = response
                # Pictures are image files in the library, not parts an element can use.
                self._parts = [e for e in response["libraryParts"]
                               if isinstance(e, dict) and e.get("typeId") != "Picture"]
        return self._answer

    def parts(self) -> list[dict]:
        """The loaded parts, pictures left out; none when the listing failed."""
        self.answer()
        return self._parts

    def guid_of(self, name: str) -> tuple[str | None, str | None]:
        """(the main GUID of the loaded part named name, why there is none). With
        several parts of that name it is the first, as learn_library_part takes it."""
        response = self.answer()
        if response is None:
            return None, (f"the loaded library parts could not be listed, so the record of "
                          f"'{name}' could not be checked against the part")
        parts = self.parts()
        matches = [e for e in parts if e.get("documentName") == name]
        if not matches:
            names = sorted({str(e.get("documentName")) for e in parts})
            reason = f"no loaded library part named '{name}'{closest_names(name, names)}"
            unread = response.get("skippedCount") or 0
            if unread:
                reason += (f" ({unread} library entries could not be read, and without the "
                           "part's GUID its record cannot be checked)")
            return None, reason
        guid = matches[0].get("guid")
        if not isinstance(guid, str) or not guid:
            return None, (f"Archicad lists '{name}' without its GUID, so its record could "
                          "not be checked against it")
        return guid, None


def _library_part_checks(library: LoadedParts, items: list[dict],
                         skipped: dict[int, str]) -> tuple[dict[int, str], list[str]]:
    """Items whose libraryPartName no loaded part has, and warnings for names the
    library spells another way."""
    wanted: dict[str, list[int]] = {}
    for index, item in enumerate(items):
        if index not in skipped and isinstance(item.get("libraryPartName"), str):
            wanted.setdefault(item["libraryPartName"], []).append(index)
    if not wanted:
        return {}, []
    response = library.answer()
    if response is None:
        return {}, []  # unreadable: let Archicad judge the names
    entries = response["libraryParts"]
    parts = library.parts()
    names = {str(e.get("documentName")) for e in parts}
    ordered = sorted(names)
    spellings = {}
    for e in parts:
        file_name = str(e.get("fileName"))
        for spelling in (str(e.get("documentName")), file_name,
                         file_name.removesuffix(".gsm")):
            spellings.setdefault(spelling.casefold(), str(e.get("documentName")))
    # Entries Archicad could not read past the last listed one are not parts (live
    # 21.10.2026, MCP-Test: 123 indices after 7402 parts); one among them could be.
    unread = response.get("skippedCount") or 0
    listed = sorted(e.get("index") for e in entries if isinstance(e.get("index"), int))
    complete = not unread or listed == list(range(1, len(entries) + 1))
    skips, warnings = {}, []
    for name, indices in wanted.items():
        if name in names:
            continue
        spelt = spellings.get(name.casefold())
        if spelt is not None:
            warnings.append(f"The library spells '{name}' ({_named(indices)}) as "
                            f"'{spelt}': send that name, as Archicad may not match "
                            "another spelling.")
            continue
        reason = f"no loaded library part named '{name}'{closest_names(name, ordered)}"
        if complete:
            skips.update(dict.fromkeys(indices, reason))
        else:
            warnings.append(f"There is {reason} ({_named(indices)}), but {unread} "
                            "library entries could not be read, so it is sent for "
                            "Archicad to judge.")
    return skips, warnings


def _object_warnings(name: str, items: list[dict], ready: list[int]) -> list[str]:
    sill_tool = _TOOL_SILL.get(name)
    if sill_tool is not None:
        unsilled = [i for i in ready
                    if "sillHeight" not in items[i] and "favoriteName" not in items[i]]
        if not unsilled:
            return []
        return [f"No sillHeight on {_named(unsilled)}: a {name} without one takes the "
                f"{sill_tool}'s current sill height. Send sillHeight in metres (0 for a "
                "door on the floor)."]
    tool = _TOOL_SIZED.get(name)
    if tool is None:
        return []
    warnings = []
    unsized = [i for i in ready
               if "dimensions" not in items[i] and "favoriteName" not in items[i]]
    if unsized:
        warnings.append(f"No dimensions on {_named(unsized)}: Tapir starts each {name} "
                        f"from the {tool}'s current settings, so without dimensions it "
                        f"takes the size (A, B and height) the {tool} is set to now, "
                        "not the library part's own. Send dimensions in metres.")
    for field, why in _DISCARDED.items():
        sending = [i for i in ready if field in items[i]]
        if sending:
            warnings.append(f"{field} on {_named(sending)}: {why}")
    return warnings


def _tool_part_warnings(name: str, items: list[dict], ready: list[int],
                        dry_run: bool) -> list[str]:
    """A door or window placed by libraryPartName leaves its tool holding the part: live
    22.10.2026, Tapir 1.7.2's CreateDoors and CreateWindows set the tool's defaults to
    it and never set them back."""
    tool = _TOOL_SILL.get(name)
    named = [i for i in ready if isinstance(items[i].get("libraryPartName"), str)]
    if tool is None or not named:
        return []
    part = items[named[-1]]["libraryPartName"]
    holds = "will hold" if dry_run else "now holds"
    return [f"After this create, the {tool} {holds} '{part}' (the part of the last {name} "
            f"in it named by libraryPartName): Tapir 1.7.2 sets the tool to that part and "
            f"never sets it back, so the next {name} drawn by hand gets it. Set the {tool} "
            "back by hand if that is not wanted."]


def _part_name_parameter_warnings(conn: ArchicadConnection, name: str, spec: CreateType,
                                  items: list[dict], ready: list[int]) -> list[str]:
    """A door or window placed by libraryPartName cannot have its GDL parameters read or
    written, nor can any later one from its tool: live 23.10.2026, Tapir 1.7.2 builds the
    tool's parameter list from the bare part, Archicad will not open it, and hand-placed
    openings inherit it. The same part placed from a favourite read and wrote them all."""
    tool = _TOOL_SILL.get(name)
    named = [i for i in ready if isinstance(items[i].get("libraryPartName"), str)]
    if tool is None or not named:
        return []
    try:
        favorites = conn.tapir("GetFavoritesByType",
                               {"elementType": spec.favorite_type}).get("favorites")
    except APIErrorBase:
        favorites = None  # unreadable: suggest favoriteName without a list
    if isinstance(favorites, list) and favorites:
        shown = ", ".join(str(f) for f in favorites[:INDICES_NAMED])
        more = len(favorites) - INDICES_NAMED
        listed = f"; the {spec.favorite_type} favourites here: {shown}" + (
            f" and {more} more" if more > 0 else "")
    elif isinstance(favorites, list):
        listed = (f"; no {spec.favorite_type} favourites are saved in this project, so save "
                  f"one from a {name} whose part was picked in the {tool} settings")
    else:
        listed = ""
    return [f"{_named(named)}: a {name} placed by libraryPartName gets a GDL parameter list "
            "Archicad will not open (Tapir 1.7.2), so its parameters, the opening types of "
            "its sashes or leaves among them, can be neither read nor written, and the "
            f"{tool} keeps that list: every later {name} from the {tool}, hand-placed ones "
            f"too, has the same fault. Send favoriteName instead{listed}. One {name} placed "
            f"from a favourite, or the part picked again in the {tool} settings, puts the "
            "tool right."]


def _stale_tapir_warnings(conn: ArchicadConnection) -> list[str]:
    old = older_than_definitions(conn)
    if old is None:
        return []
    return [f"This Archicad runs Tapir {old}, older than the Tapir {definitions_version()} "
            "definitions these items are checked against: a field Tapir added since "
            "(libraryPartName on doors and windows came in 1.7.2) passes the dry run and "
            "is refused when sent. install_tapir updates it."]


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


def _write_leaves(conn: ArchicadConnection, leaf_writes: dict[int, dict],
                  by_index: dict[str, str]) -> list[dict]:
    """Each created door's or window's leaf state, written after the create. A refusal
    keeps the element as placed and names it, so the agent can set it another way."""
    unset = []
    for index, leaves in sorted(leaf_writes.items()):
        guid = by_index.get(str(index))
        if guid is None:
            continue  # not created
        try:
            message = write_leaf_state(conn, guid, leaves["parameters"])
        except Exception as exc:  # whatever stops a write, the created GUIDs are replied
            message = f"the write stopped on {type(exc).__name__}: {exc}"
        if message is not None:
            unset.append({"index": index, "guid": guid, "leaves": leaves["state"],
                          "parameters": leaves["parameters"], "message": message})
    return unset


def _commit(conn: ArchicadConnection, spec: CreateType, items: list[dict],
            ready: list[int], skipped: dict[int, str], warnings: list[str],
            placements: list[dict], leaf_writes: dict[int, dict]) -> dict:
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
    if placements:
        result["placements"] = placements
    unset = _write_leaves(conn, leaf_writes, by_index)
    if unset:
        result["placed_not_set"] = unset
    if warnings:
        result["warnings"] = warnings
    return result


def _skipped_list(skipped: dict[int, str]) -> list[dict]:
    return [{"index": i, "reason": skipped[i]} for i in sorted(skipped)]


def create_elements(conn: ArchicadConnection, element_type: str,
                    items: list[dict], dry_run: bool = True,
                    records: PartRecords | None = None) -> dict:
    """`records` is the store of learned parts that `placement` reads; None reads the
    default one, and only when an item gives a placement."""
    found = _lookup(element_type)
    if found is None:
        return {"error": f"Unknown element_type '{element_type}'. {_FAMILY_HINT}",
                "valid_types": sorted(CREATE_TYPES)}
    name, spec = found
    info = build_registry()[spec.command]
    schema = _item_schema(spec)
    if not items:
        result = {"element_type": name, "command": spec.command,
                  "since_tapir": info.version, "item_schema": schema}
        if name in PLACEMENT_SCHEMAS:
            result["placement_schema"] = PLACEMENT_SCHEMAS[name]
        return result
    archicad = conn.archicad_version()
    if spec.archicad and archicad and archicad < spec.archicad:
        return {"error": f"'{name}' elements need Archicad {spec.archicad} or later, and "
                         f"this is Archicad {archicad}. Nothing was sent.",
                "element_type": name}
    refusal = require_tapir_command(conn, spec.command, "create_elements")
    if refusal is not None:
        return dict(refusal, element_type=name)
    library = LoadedParts(conn)
    # Before the schema check: Tapir's item schema has no placement field.
    items, skipped, placements, leaf_writes, placed_warnings = resolve_placements(
        conn, name, items, records, library)
    problems = _problems(items, schema, skipped)
    if problems:
        reply = {"dry_run": dry_run, "element_type": name, "command": spec.command,
                 "items": len(items), "created": 0, "problems": problems,
                 "hint": "Nothing was sent. Call create_elements with items=[] for "
                         "this type's item schema."}
        if skipped:  # placements that cannot be resolved, so all can be fixed at once
            reply["skipped"] = _skipped_list(skipped)
        return reply
    for index, reason in _favorite_skips(conn, name, spec, items).items():
        skipped.setdefault(index, reason)
    skipped.update(_host_skips(conn, name, spec, items, skipped))
    library_skips, library_warnings = _library_part_checks(library, items, skipped)
    skipped.update(library_skips)
    window = active_window(conn)
    ready = [i for i in range(len(items)) if i not in skipped]
    placements = [p for p in placements if p["index"] not in skipped]
    leaf_writes = {i: leaves for i, leaves in leaf_writes.items() if i not in skipped}
    warnings = (_window_warnings(name, spec, items, window) + library_warnings
                + [text for i, texts in sorted(placed_warnings.items()) if i not in skipped
                   for text in texts]
                + _object_warnings(name, items, ready)
                + _tool_part_warnings(name, items, ready, dry_run)
                + _part_name_parameter_warnings(conn, name, spec, items, ready)
                + _stale_tapir_warnings(conn))
    if not dry_run:
        if not ready:
            return {"dry_run": False, "created": 0, "elements": [],
                    "skipped": _skipped_list(skipped), "warnings": warnings}
        return _commit(conn, spec, items, ready, skipped, warnings, placements, leaf_writes)
    result = {"dry_run": True, "element_type": name, "command": spec.command,
              "items": len(items), "ready": len(ready), "skipped": _skipped_list(skipped),
              "warnings": warnings, "active_window": window,
              "payload": {spec.payload_key: [items[i] for i in ready]}}
    if placements:
        result["placements"] = placements
    return result
