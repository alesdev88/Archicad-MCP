"""What modify_elements knows about each Modify command's fields.

The facts behind these tables were read in the Tapir source (tag 1.5.9, the
same at 1.7.1) on 19.10.2026; see docs/superpowers/specs/2026-10-19-modify-elements-design.md.
"""
import pytest

from archicad_mcp.core.modify_fields import (
    TYPE_COMMANDS,
    absolute_z,
    allowed_fields,
    compared,
    complete,
    current_value,
    expected_read,
    home_story,
    item_schema,
    refusal,
    side_effects,
    summarize,
    values_match,
)
from tests.fixtures.modify_elements import all_details, load

D = all_details()
SLAB = D["05C7A02F-4251-6F40-8407-67893BEFF6AC"]
SLAB2 = D["8BD33B9C-5B7D-5C46-8B28-9EC1CB9310CC"]
OBJECT = D["5349C8FC-A4BF-3548-A614-77BEB2CE4261"]
MESH = D["D4DB880C-8083-F749-B27F-0886B2D6044B"]
WALL, WALL_TRAPEZOID = D["WALL-1"], D["WALL-2"]
SLAB_B = D["SLAB-B"]                      # Basic: thickness applies
DOOR, ROOF_M, ROOF_S = D["DOOR-1"], D["ROOF-M"], D["ROOF-S"]
TEXT, MORPH = D["TEXT-1"], D["MORPH-1"]
ENTRIES = {"slab": SLAB, "object": OBJECT, "mesh": MESH, "morph": MORPH,
           "roof_single": ROOF_S}
NEW = (1, 7, 1)


def test_every_type_maps_to_a_command_in_the_bundled_definitions():
    assert set(TYPE_COMMANDS) == {"Wall", "Beam", "Slab", "Column", "Window", "Door",
                                  "Morph", "Roof", "Mesh", "Object", "Lamp", "Text", "Label"}
    assert all(item_schema(c)["properties"] for c in TYPE_COMMANDS.values())


def test_allowed_fields():
    assert {"level", "sublines"} <= allowed_fields("ModifyMeshes")
    assert not {"floorIndex", "meshData", "elementId"} & allowed_fields("ModifyMeshes")
    assert {"height", "thickness"} <= allowed_fields("ModifyWalls")
    assert "elementId" not in allowed_fields("ModifyWalls")
    assert "floorIndex" not in allowed_fields("ModifyTexts")


@pytest.mark.parametrize("command,field,fields,entry_key,fragment", [
    ("ModifyObjects", "offset", {"offset": {"x": 1, "y": 0}}, "object", "discards"),
    ("ModifyObjects", "useFixedAngle", {"useFixedAngle": True}, "object", "discards"),
    ("ModifyLamps", "lightColor", {"lightColor": {}}, "object", "discards"),
    ("ModifyMorphs", "rotationDegreesZ", {"rotationDegreesZ": 90}, "morph", "xAxis"),
    ("ModifySlabs", "polygonArcs", {"polygonArcs": []}, "slab", "polygonOutline"),
    ("ModifyMeshes", "holes", {"holes": []}, "mesh", "polygonCoordinates"),
    ("ModifyMeshes", "polygonArcs", {"polygonArcs": []}, "mesh", "polygonCoordinates"),
    ("ModifyMeshes", "contourPen", {"contourPen": 0}, "mesh", "0 or less"),
    ("ModifyMeshes", "floorIndex", {"floorIndex": 1}, "mesh", "story"),
    ("ModifyTexts", "floorIndex", {"floorIndex": 1}, "slab", "story"),
    ("ModifyWalls", "elementId", {"elementId": {}}, "slab", "guid"),
    ("ModifyRoofs", "thickness", {"thickness": 0.2}, "roof_single", "single-plane"),
    ("ModifySlabs", "zCoordinate", {"zCoordinate": 1.0, "story": 1}, "slab", "story"),
    ("ModifySlabs", "thickness", {"thickness": 0.25}, "slab", "Basic"),
    ("ModifyBeams", "level", {"level": 0.5, "story": 1, "keep_elevation": True}, "slab",
     "keep_elevation"),
    ("ModifyWalls", "bottomOffset", {"bottomOffset": 0.1, "story": 1, "keep_elevation": True},
     "slab", "keep_elevation"),
])
def test_refused_fields_name_the_reason(command, field, fields, entry_key, fragment):
    assert fragment in refusal(command, field, fields, ENTRIES[entry_key], NEW)


@pytest.mark.parametrize("command,field,fields,entry", [
    ("ModifySlabs", "thickness", {"thickness": 0.25}, SLAB_B),
    ("ModifySlabs", "holes", {"holes": []}, SLAB),          # slabs reuse the outline
    ("ModifySlabs", "polygonArcs", {"polygonArcs": [], "polygonOutline": []}, SLAB),
    ("ModifyMeshes", "contourPen", {"contourPen": 3}, MESH),
    ("ModifyRoofs", "thickness", {"thickness": 0.2}, ROOF_M),
])
def test_ordinary_fields_are_not_refused(command, field, fields, entry):
    assert refusal(command, field, fields, entry, NEW) is None


def test_dimensions_refused_only_on_old_tapir_with_fix_size_off():
    fields = {"dimensions": {"x": 1.2}}
    assert OBJECT["details"]["useFixSize"] is False
    assert "1.6.0" in refusal("ModifyObjects", "dimensions", fields, OBJECT, (1, 5, 10))
    assert refusal("ModifyObjects", "dimensions", fields, OBJECT, NEW) is None
    fixed = {**OBJECT, "details": {**OBJECT["details"], "useFixSize": True}}
    assert refusal("ModifyObjects", "dimensions", fields, fixed, (1, 5, 10)) is None


def test_completion():
    assert complete("ModifyObjects", "coordinates", {"x": 5.0}, OBJECT) == \
        {"x": 5.0, "y": 0, "z": 0}
    assert complete("ModifyObjects", "dimensions", {"x": 1.2}, OBJECT) == \
        {"x": 1.2, "y": 1, "z": 1.89}
    assert complete("ModifyMorphs", "translation", {"z": 1.0}, MORPH) == \
        {"x": 0, "y": 0, "z": 1.0}
    assert complete("ModifyTexts", "coordinate", {"x": 1.0}, TEXT) == \
        {"x": 1.0, "y": 2.5, "z": 0}
    merged = complete("ModifyWalls", "visibility", {"showAllAbove": True}, WALL)
    assert merged["showAllAbove"] is True
    assert merged["showOnHome"] == WALL["details"]["visibility"]["showOnHome"]
    assert merged["showRelBelow"] == 0
    orientation = complete("ModifySlabs", "floorFill",
                           {"orientation": {"matrix00": 2}}, SLAB)["orientation"]
    assert orientation["matrix00"] == 2 and orientation["matrix11"] == 1
    assert orientation["type"] == "Global"
    assert complete("ModifySlabs", "thickness", 0.25, SLAB) == 0.25
    assert complete("ModifyWalls", "begCoordinate", {"y": 1}, WALL) == {"x": 0, "y": 1}


def test_completion_does_not_touch_the_entry():
    complete("ModifyWalls", "visibility", {"showAllAbove": True}, WALL)
    assert WALL["details"]["visibility"]["showAllAbove"] is False


def test_current_values_are_in_the_write_frame():
    assert current_value("ModifySlabs", "zCoordinate", SLAB2) == 3.25   # absolute
    assert current_value("ModifySlabs", "thickness", SLAB) == 0.3
    assert current_value("ModifyRoofs", "level", ROOF_M) == 3.5       # not details.level
    assert current_value("ModifyRoofs", "polygonOutline", ROOF_M) == \
        ROOF_M["details"]["pivotPolygonOutline"]
    assert current_value("ModifyRoofs", "levels", ROOF_M) == \
        [{"levelHeight": 0, "levelAngle": 0.5235987755982988}]
    assert current_value("ModifyWalls", "thickness", WALL) == 0.3
    assert current_value("ModifyObjects", "coordinates", OBJECT) == \
        {"x": 3.676672178, "y": 0, "z": 0}
    assert current_value("ModifyTexts", "coordinate", TEXT) == {"x": 1.5, "y": 2.5, "z": 0}
    assert current_value("ModifyMorphs", "translation", MORPH) == {"x": 1, "y": 2, "z": 0}
    assert current_value("ModifyDoors", "reveal", DOOR) is None
    assert current_value("ModifyDoors", "width", DOOR) == 0.9


def test_expected_read_of_a_morph_translation_adds_the_origin():
    assert expected_read("ModifyMorphs", "translation", {"x": 1, "y": 0, "z": 0}, MORPH) == \
        {"x": 2, "y": 2, "z": 0}
    assert expected_read("ModifySlabs", "thickness", 0.25, SLAB) == 0.25


def test_absolute_z_and_home_story():
    stories = load("stories.json")["stories"]
    assert absolute_z("ModifyObjects", "coordinates", {"x": 0, "y": 0, "z": 2.5}) == 2.5
    assert absolute_z("ModifySlabs", "zCoordinate", 3.25) == 3.25
    assert absolute_z("ModifyRoofs", "level", 3.5) == 3.5
    assert absolute_z("ModifyTexts", "coordinate", {"x": 0, "y": 0, "z": 3}) == 3
    assert absolute_z("ModifyBeams", "level", 0.5) is None
    assert absolute_z("ModifySlabs", "thickness", 0.5) is None
    assert [home_story(z, stories) for z in (2.5, 3.0, 3.25, 7.0, -1.0)] == [0, 1, 1, 2, 0]


def test_side_effects():
    outline = [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}]
    assert any("removes the holes" in w
               for w in side_effects("ModifySlabs", {"polygonOutline": outline}, SLAB))
    assert any("edge" in w for w in side_effects("ModifySlabs", {"holes": []}, SLAB))
    assert not any("removes the holes" in w for w in side_effects(
        "ModifySlabs", {"polygonOutline": outline, "holes": []}, SLAB))
    assert any("relativeTopStory" in w
               for w in side_effects("ModifyWalls", {"height": 3.0}, WALL))
    assert any("start thickness" in w
               for w in side_effects("ModifyWalls", {"thickness": 0.2}, WALL_TRAPEZOID))
    assert not side_effects("ModifyWalls", {"thickness": 0.2}, WALL)
    assert any("word wrap" in w for w in side_effects("ModifyTexts", {"text": "A"}, TEXT))
    assert any("word wrap" in w
               for w in side_effects("ModifyTexts", {"style": {"height": 3}}, TEXT))
    assert not side_effects("ModifyTexts", {"style": {"angle": 0.1}}, TEXT)
    assert any("geometry" in w for w in side_effects("ModifyMorphs", {"body": {}}, MORPH))
    assert any("size" in w for w in side_effects("ModifyObjects", {"useFixSize": True}, OBJECT))


def test_summarize_and_compared():
    assert summarize(MESH["details"]["polygonCoordinates"]) == "25 points"
    assert summarize(MESH["details"]["sublines"]) == "3 items"
    assert summarize(0.3) == 0.3
    assert summarize({"x": 1, "y": 2}) == {"x": 1, "y": 2}
    assert summarize([1, 2]) == [1, 2]
    body = {"vertices": [{"x": i, "y": 0, "z": 0} for i in range(20)], "edges": []}
    assert summarize(body) == "an object of 2 fields"
    assert compared("ModifySlabs", "thickness")
    assert compared("ModifyObjects", "coordinates")
    assert not compared("ModifySlabs", "polygonOutline")
    assert not compared("ModifyMeshes", "sublines")
    assert not compared("ModifyDoors", "reveal")


def test_values_match():
    assert values_match(0.25, 0.2500000001)
    assert not values_match(0.25, 0.26)
    assert values_match({"guid": "a8836ee9-5c72-492b-a7f6-30efed26b3ee"},
                        {"guid": "A8836EE9-5C72-492B-A7F6-30EFED26B3EE"})
    assert values_match({"overridden": True},
                        {"overridden": True, "attributeId": {"guid": "X"}})
    assert not values_match({"overridden": True}, {"overridden": False})
    assert values_match(True, True) and not values_match(True, 1)
    assert not values_match("Note", "note")


def test_thickness_follows_the_structure():
    # Live 19.10.2026: ModifySlabs thickness on a Composite slab answered success
    # and the slab kept its composite's 0.3 m.
    assert SLAB["details"]["structureType"] == "Composite"
    assert refusal("ModifySlabs", "thickness", {"thickness": 0.25, "structureType": "Basic"},
                   SLAB, NEW) is None
    assert refusal("ModifySlabs", "thickness", {"thickness": 0.25, "buildingMaterialId": "C"},
                   SLAB, NEW) is None
    assert refusal("ModifyWalls", "thickness", {"thickness": 0.25}, WALL, NEW) is None
    assert "Basic" in refusal("ModifyWalls", "thickness",
                              {"thickness": 0.25, "compositeId": "X"}, WALL, NEW)
