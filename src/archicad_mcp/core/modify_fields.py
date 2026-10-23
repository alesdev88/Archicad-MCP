"""What modify_elements knows about each Tapir Modify command's fields.

Pure tables and helpers, no connection. The facts were read in the Tapir
source at tag 1.5.9 on 19.10.2026 and are unchanged at 1.7.1; the design
(docs/superpowers/specs/2026-10-19-modify-elements-design.md) cites the lines.
The ones a caller cannot see from the schemas:

* Absolute z writes (slab and column zCoordinate, roof level, object and lamp
  coordinates.z, text coordinate.z) re-home the element to the highest story
  at or below the z, even when the z is unchanged.
* GetDetailsOfElements reads some fields under other names or frames: a
  wall's thickness is begThickness, a roof's level is its absolute
  zCoordinate (details.level is relative), a morph's translation is a move by
  from its origin.
* Some partial objects are rebuilt from scratch (wall visibility, the fill
  orientations), others are refused unless complete (object coordinates and
  dimensions), so the tool completes them from the current read.
* ModifyMorphs rotationDegreesZ mixes the x and z rows of the transformation,
  which turns the morph about Y and around the project origin.
"""
from __future__ import annotations

import copy
import json
import re
from functools import lru_cache

from archicad_mcp.gateway.registry import build_registry

TYPE_COMMANDS: dict[str, str] = {
    "Wall": "ModifyWalls", "Beam": "ModifyBeams", "Slab": "ModifySlabs",
    "Column": "ModifyColumns", "Window": "ModifyWindows", "Door": "ModifyDoors",
    "Morph": "ModifyMorphs", "Roof": "ModifyRoofs", "Mesh": "ModifyMeshes",
    "Object": "ModifyObjects", "Lamp": "ModifyLamps", "Text": "ModifyTexts",
    "Label": "ModifyLabels",
}

LIST_KEYS: dict[str, str] = {
    "ModifyWalls": "wallsWithDetails", "ModifyBeams": "beamsWithDetails",
    "ModifySlabs": "slabsWithDetails", "ModifyColumns": "columnsWithDetails",
    "ModifyWindows": "windowsWithDetails", "ModifyDoors": "doorsWithDetails",
    "ModifyMorphs": "morphsWithDetails", "ModifyRoofs": "roofsWithDetails",
    "ModifyMeshes": "meshesData", "ModifyObjects": "objectsWithDetails",
    "ModifyLamps": "lampsWithDetails", "ModifyTexts": "textsWithDetails",
    "ModifyLabels": "labelsWithDetails",
}

# GetDetailsOfElements `fields` and `hotlinkId` arrived in 1.5.9.
MIN_TAPIR = (1, 5, 9)
# Tapir #719 and #720: object dimensions in metres whatever useFixSize says.
DIMENSIONS_FIXED_IN = (1, 6, 0)

COMMON_FIELDS = frozenset({"story", "layer", "draw_order", "keep_elevation"})

# Types with no elevation of their own: keep_elevation means nothing for them.
FLAT_TYPES = frozenset({
    "Text", "Label", "Dimension", "RadialDimension", "LevelDimension",
    "AngleDimension", "Hatch", "Line", "PolyLine", "Arc", "Circle", "Spline",
    "Hotspot", "Drawing", "Picture", "Detail", "Worksheet", "Elevation",
    "InteriorElevation", "CutPlane", "ChangeMarker",
})
# Elements that live in a host's story.
HOSTED_TYPES = frozenset({"Door", "Window", "Skylight", "Opening"})
# The types SetDetailsOfElements moves to the front of its batch.
OPENING_TYPES = frozenset({"Door", "Window", "Skylight"})

# Fields that hold an attribute, by the attribute type a name resolves in.
ATTRIBUTE_FIELDS: dict[str, str] = {
    "buildingMaterialId": "BuildingMaterial", "compositeId": "Composite",
    "profileId": "Profile", "surfaceId": "Surface", "lineTypeId": "Line",
    "cutLineType": "Line", "uncutLineType": "Line", "overheadLineType": "Line",
    "sectionFillId": "Fill", "coverFillType": "Fill",
}
# Fields that hold an attribute index rather than an AttributeId.
INDEX_ATTRIBUTE_FIELDS: dict[str, str] = {"lineTypeIndex": "Line"}
# {overridden, attributeId} surfaces; a name is shorthand for an override.
SURFACE_OVERRIDES = frozenset({"referenceMaterial", "oppositeMaterial", "sideMaterial",
                               "topMaterial", "bottomMaterial"})
# {overridden, penIndex} pens on walls, beams, slabs and columns (a morph's are
# plain integers).
PEN_OVERRIDES = frozenset({"cutFillPen", "cutFillBackgroundPen"})

# The field whose z decides the home story, per command.
ABSOLUTE_Z_FIELDS: dict[str, str] = {
    "ModifySlabs": "zCoordinate", "ModifyColumns": "zCoordinate",
    "ModifyRoofs": "level", "ModifyObjects": "coordinates",
    "ModifyLamps": "coordinates", "ModifyTexts": "coordinate",
}

# Fields that set a height above the home story: with keep_elevation they would
# undo the move back.
RELATIVE_Z_FIELDS: dict[str, frozenset[str]] = {
    "ModifyBeams": frozenset({"level"}), "ModifyMeshes": frozenset({"level"}),
    "ModifyMorphs": frozenset({"level"}), "ModifyWalls": frozenset({"bottomOffset"}),
    "ModifyColumns": frozenset({"bottomOffset"}),
}

_NOT_SETTABLE = frozenset({"elementId", "floorIndex"})

_DISCARDED = ("Archicad discards this field (Tapir's own definition says so), so "
              "it would answer success and change nothing")
_STORY_NOT_FLOOR = "give the story as 'story', not floorIndex"
_REFUSED: dict[tuple[str, str], str] = {
    ("ModifyObjects", "offset"): _DISCARDED,
    ("ModifyObjects", "useFixedAngle"): _DISCARDED,
    ("ModifyLamps", "offset"): _DISCARDED,
    ("ModifyLamps", "useFixedAngle"): _DISCARDED,
    ("ModifyLamps", "lightColor"): _DISCARDED,
    ("ModifyMorphs", "rotationDegreesZ"): (
        "Tapir's rotationDegreesZ turns the morph about the Y axis and around the "
        "project origin (it mixes the x and z rows of the transformation); send "
        "xAxis, yAxis and zAxis instead"),
    ("ModifyMeshes", "floorIndex"): _STORY_NOT_FLOOR,
    ("ModifyTexts", "floorIndex"): _STORY_NOT_FLOOR,
}

# Fields Tapir ignores unless the outline is sent with them.
_NEEDS_OUTLINE: dict[tuple[str, str], str] = {
    ("ModifySlabs", "polygonArcs"): "polygonOutline",
    ("ModifyRoofs", "polygonArcs"): "polygonOutline",
    ("ModifyRoofs", "holes"): "polygonOutline",
    ("ModifyMeshes", "polygonArcs"): "polygonCoordinates",
    ("ModifyMeshes", "holes"): "polygonCoordinates",
}
_POSITIVE_ONLY: dict[str, frozenset[str]] = {
    "ModifyMeshes": frozenset({"contourPen", "levelPen", "lineTypeIndex"}),
}

# Where a field's current value is read when the name differs.
_READ_KEYS: dict[tuple[str, str], str] = {
    ("ModifyWalls", "thickness"): "begThickness",
    ("ModifyRoofs", "level"): "zCoordinate",
    ("ModifyRoofs", "polygonOutline"): "pivotPolygonOutline",
    ("ModifyObjects", "coordinates"): "origin",
    ("ModifyLamps", "coordinates"): "origin",
    ("ModifyMorphs", "translation"): "origin",
}
_NO_READ = frozenset({
    ("ModifyWindows", "reveal"), ("ModifyWindows", "revealDepthOffset"),
    ("ModifyDoors", "reveal"), ("ModifyDoors", "revealDepthOffset"),
    ("ModifyMorphs", "rotationDegreesZ"),
})

# Partial values completed from the current read, and the key they come from.
_COMPLETE_FROM: dict[tuple[str, str], str] = {
    ("ModifyObjects", "coordinates"): "origin",
    ("ModifyLamps", "coordinates"): "origin",
    ("ModifyObjects", "dimensions"): "dimensions",
    ("ModifyLamps", "dimensions"): "dimensions",
    ("ModifyWalls", "begCoordinate"): "begCoordinate",
    ("ModifyWalls", "endCoordinate"): "endCoordinate",
    ("ModifyBeams", "begCoordinate"): "begCoordinate",
    ("ModifyBeams", "endCoordinate"): "endCoordinate",
    ("ModifyColumns", "origin"): "origin",
    ("ModifyWalls", "visibility"): "visibility",
    ("ModifyObjects", "visibility"): "visibility",
    ("ModifyLamps", "visibility"): "visibility",
    ("ModifyBeams", "coverFill"): "coverFill",
    ("ModifyColumns", "coverFill"): "coverFill",
    ("ModifySlabs", "floorFill"): "floorFill",
}

# Geometry lists the readback does not compare (closing points and arcs make
# an equal polygon read back different).
_LIST_FIELDS = frozenset({"polygonOutline", "polygonArcs", "polygonCoordinates",
                          "holes", "sublines", "body", "levels", "runs",
                          "textureProjectionCoords"})

# Style fields that make ModifyTexts / ModifyLabels rebuild the content.
_RUN_STYLE = frozenset({"penIndex", "fontIndex", "bold", "italic", "underline",
                        "height", "effectStrikeout", "effectSuperscript",
                        "effectSubscript", "effectProtected"})
_CROSS_SECTION = {
    "ModifyBeams": frozenset({"width", "height", "isWidthAndHeightLinked",
                              "buildingMaterialId", "profileId", "circleBased"}),
    "ModifyColumns": frozenset({"width", "depth", "isWidthAndHeightLinked",
                                "buildingMaterialId", "profileId", "circleBased"}),
}

_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                   r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


@lru_cache(maxsize=None)
def _item_schema(command: str) -> dict:
    schema = build_registry()[command].input_schema
    item = schema["properties"][LIST_KEYS[command]]["items"]
    if command == "ModifyMeshes":
        item = item["properties"]["meshData"]
    item = copy.deepcopy(item)
    item.get("properties", {}).pop("elementId", None)
    if "required" in item:
        item["required"] = [r for r in item["required"] if r != "elementId"]
    return item


def item_schema(command: str) -> dict:
    """The schema of one element's fields, without elementId; for ModifyMeshes
    the meshData schema, so flat fields validate the same way for every command."""
    return copy.deepcopy(_item_schema(command))


def allowed_fields(command: str) -> frozenset[str]:
    return frozenset(_item_schema(command).get("properties", {})) - _NOT_SETTABLE


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# Elements whose thickness counts only for a Basic structure: a Composite or
# Profile one takes its thickness from the attribute (Tapir documents it from
# 1.7.0; ModifySlabs answered success on a Composite slab and changed nothing,
# live 19.10.2026).
_STRUCTURED = {"ModifyWalls": "wall", "ModifySlabs": "slab", "ModifyRoofs": "roof"}


def _structure(fields: dict, details: dict) -> str | None:
    """The structure the element will have once the change lands. A lone
    building material, composite or profile implies its structure, as Tapir's
    ParseStructureSelection does."""
    if "structureType" in fields:
        return fields["structureType"]
    for key, structure in (("buildingMaterialId", "Basic"), ("compositeId", "Composite"),
                           ("profileId", "Profile")):
        if key in fields:
            return structure
    return details.get("structureType")


def refusal(command: str, field: str, fields: dict, entry: dict,
            tapir: tuple[int, int, int]) -> str | None:
    """Why `field` cannot be sent for this element, or None.

    `fields` is the element's whole change (for the fields that need a partner),
    `entry` its GetDetailsOfElements item, `tapir` the installed version.
    """
    details = entry.get("details") or {}
    if field == "elementId":
        return "the element is addressed by 'guid'; leave elementId out"
    if (command, field) in _REFUSED:
        return _REFUSED[(command, field)]
    if command == "ModifyRoofs" and details.get("roofClass") == "SinglePlane":
        return "ModifyRoofs changes multi-plane roofs only; this is a single-plane roof"
    partner = _NEEDS_OUTLINE.get((command, field))
    if partner is not None and partner not in fields:
        return f"Tapir ignores {field} unless {partner} is sent too"
    if field in _POSITIVE_ONLY.get(command, ()) and _number(fields.get(field)) \
            and fields[field] <= 0:
        return f"Tapir ignores a {field} of 0 or less"
    if field == "dimensions" and command in ("ModifyObjects", "ModifyLamps") \
            and tapir < DIMENSIONS_FIXED_IN and details.get("useFixSize") is False:
        return ("Tapir before 1.6.0 scales 'dimensions' wrongly on an object whose "
                "useFixSize is off (Tapir #719); update Tapir to 1.6.0 or later")
    if ABSOLUTE_Z_FIELDS.get(command) == field and "story" in fields:
        return (f"give either 'story' or '{field}': Tapir takes the story from the "
                f"absolute z in {field}, so the two would fight")
    if field == "thickness" and command in _STRUCTURED:
        structure = _structure(fields, details)
        if structure not in (None, "Basic"):
            return (f"thickness applies only to a Basic {_STRUCTURED[command]}; this one "
                    f"is {structure}, so Archicad keeps the thickness the {structure.lower()} "
                    "gives and still answers success (seen live 19.10.2026). Change the "
                    "composite, or make it Basic with structureType or buildingMaterialId")
    if fields.get("keep_elevation") is True and field in RELATIVE_Z_FIELDS.get(command, ()):
        return (f"give either keep_elevation or '{field}': {field} sets the height above "
                "the new story, so the element would not stay at its height")
    return None


def _merge(base, over):
    if not (isinstance(base, dict) and isinstance(over, dict)):
        return copy.deepcopy(over)
    out = copy.deepcopy(base)
    for key, value in over.items():
        out[key] = _merge(out[key], value) if key in out else copy.deepcopy(value)
    return out


def complete(command: str, field: str, value, entry: dict):
    """`value` with what it leaves out taken from the current read, for the
    fields Tapir would otherwise zero or refuse. Other fields come back as given."""
    if not isinstance(value, dict):
        return value
    if command == "ModifyMorphs" and field == "translation":
        return {"x": 0, "y": 0, "z": 0, **value}
    if command == "ModifyTexts" and field == "coordinate":
        base = current_value(command, field, entry)
    else:
        key = _COMPLETE_FROM.get((command, field))
        base = None if key is None else (entry.get("details") or {}).get(key)
    if not isinstance(base, dict):
        return value
    return _merge(base, value)


def current_value(command: str, field: str, entry: dict):
    """The field's current value in the frame the write takes, or None when no
    read holds it."""
    details = entry.get("details") or {}
    if (command, field) in _NO_READ:
        return None
    if command == "ModifyTexts" and field == "coordinate":
        position = details.get("position")
        if not isinstance(position, dict):
            return None
        return {"x": position.get("x"), "y": position.get("y"),
                "z": details.get("zCoordinate")}
    if command == "ModifyRoofs" and field == "levels":
        levels = details.get("levels")
        if not isinstance(levels, list):
            return None
        return [{"levelHeight": level.get("height"), "levelAngle": level.get("angle")}
                for level in levels]
    return copy.deepcopy(details.get(_READ_KEYS.get((command, field), field)))


def expected_read(command: str, field: str, sent, entry: dict):
    """What current_value should show once the write has landed."""
    if command == "ModifyMorphs" and field == "translation":
        origin = current_value(command, field, entry) or {}
        return {axis: (origin.get(axis) or 0) + (sent.get(axis) or 0) for axis in "xyz"}
    if command == "ModifyTexts" and field == "coordinate" and isinstance(sent, dict):
        # The z only picks the story; the read shows that story's level.
        return {axis: sent[axis] for axis in ("x", "y") if axis in sent}
    return sent


def compared(command: str, field: str) -> bool:
    """Whether a readback compares this field (False: geometry lists, no read)."""
    return field not in _LIST_FIELDS and (command, field) not in _NO_READ


def absolute_z(command: str, field: str, value) -> float | None:
    """The absolute z an absolute-z field sets, else None."""
    if ABSOLUTE_Z_FIELDS.get(command) != field:
        return None
    z = value.get("z") if isinstance(value, dict) else value
    return float(z) if _number(z) else None


def home_story(z: float, stories: list[dict]) -> int:
    """The story Tapir homes an element on for an absolute z: the highest story
    whose level is at or below z, story 0 when none is (CommandBase.cpp)."""
    home = 0
    for story in sorted(stories, key=lambda s: s["index"]):
        if story["level"] > z:
            break
        home = story["index"]
    return home


def side_effects(command: str, fields: dict, entry: dict) -> list[str]:
    """Warnings for what a field changes besides itself."""
    details = entry.get("details") or {}
    out = []
    if command == "ModifySlabs":
        if "polygonOutline" in fields and "holes" not in fields:
            out.append("a new polygonOutline without holes removes the holes")
        if "polygonOutline" in fields or "holes" in fields:
            out.append("a new outline or holes resets the edge angles to vertical and "
                       "the per-edge surfaces to the side surface")
    if command == "ModifyMeshes" and "polygonCoordinates" in fields \
            and "holes" not in fields:
        out.append("new polygonCoordinates without holes removes the holes")
    # Only a linked top is unlinked: a top already at relativeTopStory 0 keeps nothing
    # to lose, and saying so for it is noise (live 23.10.2026, twelve walls).
    if command in ("ModifyWalls", "ModifyColumns") and "height" in fields \
            and details.get("relativeTopStory") != 0:
        out.append("height unlinks the top from its story (relativeTopStory becomes 0)")
    if command == "ModifyWalls" and "thickness" in fields \
            and details.get("geometryType") == "Trapezoid":
        out.append("thickness on a Trapezoid wall changes only the start thickness")
    if command == "ModifyBeams" and "holes" in fields:
        out.append("holes replace all of the beam's holes")
    if command in _CROSS_SECTION:
        if "slantAngle" in fields and "isSlanted" not in fields:
            out.append("slantAngle also sets isSlanted")
        if _CROSS_SECTION[command] & set(fields):
            out.append("cross-section fields apply to every segment")
    if command == "ModifyMorphs" and "body" in fields:
        out.append("body replaces the morph's whole geometry")
    if command == "ModifyRoofs":
        if "polygonOutline" in fields:
            out.append("polygonOutline replaces the pivot polygon and makes the "
                       "overhang an offset")
        elif "eavesOverhang" in fields:
            out.append("eavesOverhang makes the overhang an offset")
    if command in ("ModifyObjects", "ModifyLamps") and "useFixSize" in fields:
        out.append("changing useFixSize changes how the stored size is read: the "
                   "object may change size")
    if command in ("ModifyTexts", "ModifyLabels"):
        style = fields.get("style")
        if "text" in fields or "runs" in fields or (
                isinstance(style, dict) and _RUN_STYLE & set(style)):
            out.append("rebuilds the content as one paragraph: word wrap turns off "
                       "and the style applies to every run")
    return out


def summarize(value):
    """A long value (polygon, holes, sublines, body) as its size, for reports."""
    if isinstance(value, list) and (len(value) >= 4 or len(json.dumps(value)) > 200):
        points = all(isinstance(v, dict) and v and set(v) <= {"x", "y", "z"}
                     for v in value)
        return f"{len(value)} {'points' if points else 'items'}"
    if isinstance(value, dict) and len(json.dumps(value)) > 200:
        return f"an object of {len(value)} fields"
    return value


def values_match(expected, actual, tol: float = 1e-6) -> bool:
    """Whether a read shows what was sent: numbers within tol, GUIDs in any
    case, objects on the keys that were sent."""
    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(expected) is type(actual) and expected == actual
    if _number(expected) and _number(actual):
        return abs(expected - actual) <= tol
    if isinstance(expected, str) and isinstance(actual, str):
        if _GUID.match(expected) and _GUID.match(actual):
            return expected.lower() == actual.lower()
        return expected == actual
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and values_match(value, actual[key], tol)
            for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(
            values_match(e, a, tol) for e, a in zip(expected, actual))
    return expected == actual
