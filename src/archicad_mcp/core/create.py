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
import math
from dataclasses import dataclass

import jsonschema
from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.create_placement import PLACEMENT_SCHEMAS, resolve_placements
from archicad_mcp.core.editability import active_window, refusals
from archicad_mcp.core.part_placement import write_leaf_state
from archicad_mcp.core.part_records import PartRecords
from archicad_mcp.core.favorites_commit import ELEMENT_ID
from archicad_mcp.extract import (
    PropertyFetchTooWideError,
    _fetch_types,
    element_payload,
    fetch_property_values,
)
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
    # What Tapir's schema does not say about the fields, replied with the item schema.
    notes: tuple[str, ...] = ()


_WALL = frozenset({"Wall"})

# Fields Tapir reads as an absolute elevation even when the item gives floorIndex.
# Live 23.10.2026 (AC 29, Tapir 1.7.2, a basement at -2.80): a column with z 0 and
# floorIndex -1 stood from 0.00 to 2.60; a slab's level and a beam's and a stair's
# zCoordinate were absolute too. A wall's zCoordinate is the offset above floorIndex,
# as Tapir's schema says. The flag: whether the top of the story still counts as on
# it (a slab or a beam may hang from it, a column or a stair cannot stand there).
_ABSOLUTE_Z = {"column": (("coordinates", "z"), False),
               "slab": (("level",), True),
               "beam": (("zCoordinate",), True),
               "stair": (("zCoordinate",), False)}


def _absolute_note(name: str) -> str:
    field = ".".join(_ABSOLUTE_Z[name][0])
    return (f"{field} is an absolute elevation, even when floorIndex is given: a {name} "
            "on a story at -2.80 needs -2.80 plus its offset, not the offset alone.")


# Live 23.10.2026 (AC 29, Tapir 1.7.2): a stair's baseLinePoints is its left edge and
# the flight lies flightWidth to its right. A U given as its centre line made the
# flights overlap, and Archicad answered only "Failed to create new Stair"; the same U
# as its outer edge was created, but Archicad laid the risers out at its own going and
# the top flight ran 1.65 m past the baseline's end (16 risers in a 2.2 x 2.0 m U).
_STAIR_NOTES = (
    "baseLinePoints is the stair's left edge, looking up the stair: the flight lies "
    "flightWidth to its right. When a U turns towards its flights, the middle segment "
    "must be at least twice flightWidth long, or the flights overlap and Archicad "
    "answers only 'Failed to create new Stair'.",
    "Archicad lays the risers out at its own going, and CreateStairs has no turn or "
    "winder setting: a tight turn can push the top flight past the baseline's end. "
    "create_elements reports a stair that does.")

# Sides, live 23.10.2026 (AC 29, Tapir 1.7.2, Test.pln): composite walls drawn
# clockwise with referenceLineLocation Outside stood outside the outline; doors with
# oSide false swung to the right of their wall's direction, and flipping oSide also
# moved the hinge to the other jamb; windows with oSide false had their outer sill on
# the right.
_WALL_NOTES = (
    "With referenceLineLocation 'Outside' the wall's body lies to the left of "
    "begCoordinate -> endCoordinate: draw an outer wall outline counter-clockwise to "
    "keep its outside face on the line.",
    "zCoordinate is the offset above the story when floorIndex is given.")
_OPENING_SIDE = "centerOffset runs from the wall's begCoordinate to the opening's centre."
_DOOR_NOTES = (
    _OPENING_SIDE,
    "With oSide false the door swings to the right of the wall's begCoordinate -> "
    "endCoordinate. oSide true swings it to the left and also moves the hinge to the "
    "other jamb; reflected moves the hinge back.")
_WINDOW_NOTES = (
    _OPENING_SIDE,
    "With oSide false the window's outside (its outer sill) faces the right of the "
    "wall's begCoordinate -> endCoordinate.")

# How far a created stair may reach past its baseline and flights before it is
# reported: the straight stair of 23.10.2026 reached 0.14 m past its baseline's end.
STAIR_OVERRUN_TOLERANCE = 0.3

CREATE_TYPES: dict[str, CreateType] = {
    # Model elements
    "wall": CreateType("CreateWalls", "wallsData", favorite_type="Wall", notes=_WALL_NOTES),
    "beam": CreateType("CreateBeams", "beamsData", favorite_type="Beam",
                       notes=(_absolute_note("beam"),)),
    "column": CreateType("CreateColumns", "columnsData", favorite_type="Column",
                         notes=(_absolute_note("column"),)),
    "slab": CreateType("CreateSlabs", "slabsData", favorite_type="Slab",
                       notes=(_absolute_note("slab"),)),
    "roof": CreateType("CreateRoofs", "roofsData"),
    "morph": CreateType("CreateMorphs", "morphsData"),
    "mesh": CreateType("CreateMeshes", "meshesData", favorite_type="Mesh"),
    "stair": CreateType("CreateStairs", "stairsData", favorite_type="Stair",
                        notes=(*_STAIR_NOTES, _absolute_note("stair"))),
    "railing": CreateType("CreateRailings", "railingsData"),
    "zone": CreateType("CreateZones", "zonesData", favorite_type="Zone"),
    "curtain_wall": CreateType("CreateCurtainWalls", "curtainWallsData",
                               favorite_type="CurtainWall"),
    "grid_element": CreateType("CreateGridElements", "gridElements"),
    "object": CreateType("CreateObjects", "objectsData", favorite_type="Object"),
    "lamp": CreateType("CreateLamps", "lampsData", favorite_type="Lamp"),
    # Hosted in another element
    "door": CreateType("CreateDoors", "doorsData", favorite_type="Door",
                       hosts=("ownerWallId",), host_types=_WALL, notes=_DOOR_NOTES),
    "window": CreateType("CreateWindows", "windowsData", favorite_type="Window",
                         hosts=("ownerWallId",), host_types=_WALL, notes=_WINDOW_NOTES),
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


def _story_ranges(conn: ArchicadConnection) -> dict[int, tuple[float, float]]:
    """Story index -> (level, top); none when Archicad does not say. A story without a
    height ends where the next one starts; the topmost one without a height is left out."""
    try:
        answer = conn.tapir("GetStories")
    except APIErrorBase:
        return {}
    stories = sorted((s for s in answer.get("stories") or []
                      if isinstance(s, dict) and isinstance(s.get("index"), int)
                      and isinstance(s.get("level"), (int, float))),
                     key=lambda s: s["index"])
    ranges = {}
    for position, story in enumerate(stories):
        level, height = float(story["level"]), story.get("height")
        if isinstance(height, (int, float)):
            ranges[story["index"]] = (level, level + height)
        elif position + 1 < len(stories):
            ranges[story["index"]] = (level, float(stories[position + 1]["level"]))
    return ranges


def _absolute_height_warnings(conn: ArchicadConnection, name: str, items: list[dict],
                              ready: list[int]) -> list[str]:
    """Items whose absolute height lies off the story their floorIndex names: most
    likely an offset above the story, which Tapir would place that far above 0.00."""
    if name not in _ABSOLUTE_Z:
        return []
    path, top_is_on = _ABSOLUTE_Z[name]
    placed = []
    for index in ready:
        value: object = items[index]
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        floor = items[index].get("floorIndex")
        if isinstance(floor, int) and isinstance(value, (int, float)):
            placed.append((index, floor, float(value)))
    ranges = _story_ranges(conn) if placed else {}
    off = []
    for index, floor, z in placed:
        if floor not in ranges:
            continue
        level, top = ranges[floor]
        above = z > top + 1e-6 if top_is_on else z >= top - 1e-6
        if z < level - 1e-6 or above:
            off.append(f"item {index}: {z:.2f} on story {floor}, which runs from "
                       f"{level:.2f} to {top:.2f}")
    if not off:
        return []
    field = ".".join(path)
    shown = "; ".join(off[:INDICES_NAMED])
    more = len(off) - INDICES_NAMED
    return [f"{field} is an absolute elevation for a {name}, even with floorIndex, and it "
            f"lies off the story it names ({shown}" + (f"; and {more} more" if more > 0 else "")
            + f"). If it was meant as an offset above the story, send the story's level "
            f"plus the offset."]


def _flights(item: dict) -> list[list[tuple[float, float]]] | None:
    """Each baseline segment's flight: the rectangle flightWidth to the right of it.
    None when the item gives no width (a favourite's or the tool's applies) or bad points."""
    width = item.get("flightWidth")
    points = item.get("baseLinePoints")
    if not isinstance(width, (int, float)) or not isinstance(points, list):
        return None
    try:
        xy = [(float(p["x"]), float(p["y"])) for p in points]
    except (KeyError, TypeError, ValueError):
        return None
    flights = []
    for (x0, y0), (x1, y1) in zip(xy, xy[1:]):
        length = math.hypot(x1 - x0, y1 - y0)
        if length < 1e-9:
            return None
        nx, ny = (y1 - y0) / length * width, -(x1 - x0) / length * width
        flights.append([(x0, y0), (x1, y1), (x1 + nx, y1 + ny), (x0 + nx, y0 + ny)])
    return flights


def _overlap(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> bool:
    """Whether two convex outlines share area; touching edges do not count."""
    for outline in (a, b):
        for (x0, y0), (x1, y1) in zip(outline, outline[1:] + outline[:1]):
            length = math.hypot(x1 - x0, y1 - y0)
            ax, ay = (y1 - y0) / length, (x0 - x1) / length
            on_a = [ax * x + ay * y for x, y in a]
            on_b = [ax * x + ay * y for x, y in b]
            if max(on_a) <= min(on_b) + 1e-3 or max(on_b) <= min(on_a) + 1e-3:
                return False
    return True


def _stair_overlap_warnings(name: str, items: list[dict], ready: list[int]) -> list[str]:
    if name != "stair":
        return []
    crossing = []
    for index in ready:
        flights = _flights(items[index]) or []
        if any(_overlap(flights[i], flights[j])
               for i in range(len(flights)) for j in range(i + 2, len(flights))):
            crossing.append(index)
    if not crossing:
        return []
    return [f"The flights of {_named(crossing)} overlap: baseLinePoints is the stair's "
            "left edge, looking up the stair, and each flight lies flightWidth to its "
            "right. Archicad refuses such a stair with only 'Failed to create new Stair'. "
            "Give the edge the flights turn away from, or a longer middle segment."]


def _stair_overrun_warnings(conn: ArchicadConnection, items: list[dict],
                            by_index: dict[str, str]) -> list[str]:
    """Created stairs reaching past their baseline and flights by more than
    STAIR_OVERRUN_TOLERANCE, measured on their 3D bounding boxes."""
    measured = [(int(i), guid) for i, guid in by_index.items() if _flights(items[int(i)])]
    if not measured or not conn.tapir_command_available("Get3DBoundingBoxes"):
        return []
    try:
        answer = conn.tapir("Get3DBoundingBoxes",
                            {"elements": element_payload([g for _, g in measured])})
    except APIErrorBase:
        return []
    out = []
    for (index, _), entry in zip(measured, answer.get("boundingBoxes3D") or []):
        box = entry.get("boundingBox3D") if isinstance(entry, dict) else None
        if not isinstance(box, dict):
            continue
        corners = [c for flight in _flights(items[index]) for c in flight]
        xs, ys = [x for x, _ in corners], [y for _, y in corners]
        reach = max(min(xs) - box.get("xMin", min(xs)), box.get("xMax", max(xs)) - max(xs),
                    min(ys) - box.get("yMin", min(ys)), box.get("yMax", max(ys)) - max(ys))
        if reach > STAIR_OVERRUN_TOLERANCE:
            out.append(f"item {index}: {reach:.2f} m")
    if not out:
        return []
    return [f"A created stair reaches past its baseline and flights ({'; '.join(out)}): "
            "Archicad lays the risers out at its own going, and CreateStairs has no turn "
            "or winder setting, so a tight turn pushes the top flight out. Check it in "
            "plan before building on it: it can run through walls or the slab above."]


def _shared_id_warnings(conn: ArchicadConnection, name: str,
                        by_index: dict[str, str]) -> list[str]:
    """Created elements that came in with one element ID. Live 23.10.2026 (AC 29, Tapir
    1.7.2, Test.pln): 14 doors placed from a favourite all came in as 'DOO - 004' (a
    favourite carries its ID), 8 windows as 'WD - 002' and 12 zones as 'ZON - 003',
    while walls, columns and slabs were numbered one by one."""
    if len(by_index) < 2:
        return []
    try:
        values = fetch_property_values(conn, list(by_index.values()), [ELEMENT_ID])
    except (APIErrorBase, PropertyFetchTooWideError):
        return []
    groups: dict[str, list[int]] = {}
    for index, guid in by_index.items():
        value = values.get(guid, {}).get(ELEMENT_ID)
        if isinstance(value, str) and value:
            groups.setdefault(value, []).append(int(index))
    shared = [(value, sorted(indices)) for value, indices in groups.items()
              if len(indices) > 1]
    if not shared:
        return []
    shown = "; ".join(f"'{value}' on {_named(indices)}" for value, indices in shared)
    return [f"Created {name}s share an element ID ({shown}): a favourite carries its own "
            "ID, and Archicad does not number every type it creates through the API. A "
            "schedule keyed on the ID merges them; give each its own with set_element_data "
            f"({ELEMENT_ID})."]


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


def _commit(conn: ArchicadConnection, name: str, spec: CreateType, items: list[dict],
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
    if spec.command == "CreateStairs":
        warnings = warnings + _stair_overrun_warnings(conn, items, by_index)
    warnings = warnings + _shared_id_warnings(conn, name, by_index)
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
        if spec.notes:
            result["notes"] = list(spec.notes)
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
                + _absolute_height_warnings(conn, name, items, ready)
                + _stair_overlap_warnings(name, items, ready)
                + _stale_tapir_warnings(conn))
    if not dry_run:
        if not ready:
            return {"dry_run": False, "created": 0, "elements": [],
                    "skipped": _skipped_list(skipped), "warnings": warnings}
        return _commit(conn, name, spec, items, ready, skipped, warnings, placements,
                       leaf_writes)
    result = {"dry_run": True, "element_type": name, "command": spec.command,
              "items": len(items), "ready": len(ready), "skipped": _skipped_list(skipped),
              "warnings": warnings, "active_window": window,
              "payload": {spec.payload_key: [items[i] for i in ready]}}
    if placements:
        result["placements"] = placements
    return result
