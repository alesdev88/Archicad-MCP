"""apply_favorites: make existing elements match a favourite, dry run first.

The fake behaves as MCP-Test did live on 19.10.2026 (Archicad 29, Tapir
1.7.1): applying "Partition Wall" to a wall with ID TEST-A, layer 54, height
2.4 and an unlinked top gave it the favourite's own ID "SW - 001", layer 33,
height 3 linked to the story above, reference line Outside and a Composite
structure, and left its coordinates alone. A door given "Entrance Door" took
the library part and the ID and stayed in its wall. A favourite of another
type answers -2130313115, an unknown one -2130313113.
"""
from __future__ import annotations

import copy

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core import favorites
from tests.conftest import FakeCore
from tests.fixtures import api_replays

WALL = "2E618D06-949F-4B46-A01F-CA890F434A32"
DOOR = "6EC1629B-B123-ED48-91E5-AFD7AA799560"
OTHER_VIEW = "85310DDD-74EF-6B4D-8545-CDEEDBC11996"
ELEMENT_ID_PROPERTY = {"guid": "7E221F33-829B-4FBC-A670-E74DABCE6289"}
COMPOSITE = "93A31D5D-E3A5-4595-8B4B-A38EFC073FEB"
MATERIAL = "F15E89D6-7E60-4F19-8E37-52D16F90D333"

FAVORITES = {
    "Wall": ["Structural Wall", "Partition Wall"],
    "Door": ["Internal Door", "Entrance Door"],
    "Slab": ["Ceiling"],
}

WALL_DETAILS = {
    "type": "Wall", "id": "TEST-A", "floorIndex": 0, "layerIndex": 54, "drawIndex": 7,
    "details": {"begCoordinate": {"x": 0, "y": 0}, "endCoordinate": {"x": 4, "y": 0},
                "structureType": "Basic", "height": 2.4, "relativeTopStory": 0,
                "referenceLineLocation": "Center", "begThickness": 0.25,
                "buildingMaterialId": {"guid": MATERIAL}},
}
DOOR_DETAILS = {
    "type": "Door", "id": "D-17", "floorIndex": 0, "layerIndex": 33, "drawIndex": 7,
    "details": {"libPart": {"name": "Internal Door", "ownUnID": {"guid": "A"},
                            "parentUnID": {"guid": "B"}},
                "width": 0.9, "height": 2.1, "sillHeight": 0, "centerOffset": 1},
}

# What each favourite sets, as the details read shows it afterwards.
APPLIED = {
    "Partition Wall": {"id": "SW - 001", "layerIndex": 33, "details": {
        "structureType": "Composite", "height": 3, "relativeTopStory": 1,
        "referenceLineLocation": "Outside", "begThickness": 0.1,
        "compositeId": {"guid": COMPOSITE}, "buildingMaterialId": None}},
    "Entrance Door": {"id": "DOO - 002", "details": {
        "libPart": {"name": "Entrance Door", "ownUnID": {"guid": "C"},
                    "parentUnID": {"guid": "D"}}, "width": 1.0}},
}

LAYERS = {33: "Structural - Bearing", 54: "Shell - General"}
ATTRIBUTES = {
    "Layer": [{"index": i, "name": n, "attributeId": {"guid": f"L-{i}"}}
              for i, n in LAYERS.items()],
    "Composite": [{"index": 5, "name": "Partition 100", "attributeId": {"guid": COMPOSITE}}],
    "BuildingMaterial": [{"index": 9, "name": "Concrete", "attributeId": {"guid": MATERIAL}}],
}


class Project:
    def __init__(self, elements=None, elsewhere=(), fail_apply=None):
        self.elements = copy.deepcopy(elements if elements is not None else
                                      {WALL: WALL_DETAILS, DOOR: DOOR_DETAILS})
        self.elsewhere = set(elsewhere)      # passes IsEditable, not in this database
        self.fail_apply = fail_apply or {}   # guid -> (code, message)
        self.core = FakeCore(official=self._official(), tapir=self._tapir())
        self.conn = ArchicadConnection(19723, core=self.core)

    def sent(self, command):
        return [p for name, p in self.core.calls if name == command]

    def _official(self):
        official = dict(api_replays.OFFICIAL)
        official["API.GetPropertyIds"] = lambda p: {"properties": [
            {"propertyId": ELEMENT_ID_PROPERTY} for _ in p["properties"]]}
        official["API.SetPropertyValuesOfElements"] = self._set_ids
        return official

    def _set_ids(self, params):
        for item in params["elementPropertyValues"]:
            self.elements[item["elementId"]["guid"].upper()]["id"] = \
                item["propertyValue"]["value"]
        return {"executionResults": [{"success": True}
                                     for _ in params["elementPropertyValues"]]}

    def _details(self, params):
        out = []
        for e in params["elements"]:
            entry = self.elements.get(e["elementId"]["guid"].upper())
            if entry is None:
                out.append({"error": {"code": -2130313112, "message": "Not found"}})
                continue
            fields = params.get("fields")
            out.append(copy.deepcopy({k: v for k, v in entry.items()
                                      if fields is None or k in fields}))
        return {"detailsOfElements": out}

    def _apply(self, params):
        results = []
        for item in params["favoritesToApply"]:
            guid = item["elementId"]["guid"].upper()
            name = item["favorite"]
            if guid in self.fail_apply:
                code, message = self.fail_apply[guid]
                results.append({"success": False, "error": {"code": code, "message": message}})
                continue
            kind = next((t for t, names in FAVORITES.items() if name in names), None)
            if kind is None:
                results.append({"success": False, "error": {
                    "code": -2130313113, "message": "Failed to get favorite"}})
                continue
            if kind != self.elements[guid]["type"]:
                results.append({"success": False, "error": {
                    "code": -2130313115, "message": "The Favorite's element type does "
                                                    "not match the target element's type."}})
                continue
            change = APPLIED.get(name, {})
            entry = self.elements[guid]
            for key, value in change.items():
                if key == "details":
                    for k, v in value.items():
                        if v is None:
                            entry["details"].pop(k, None)
                        else:
                            entry["details"][k] = copy.deepcopy(v)
                else:
                    entry[key] = value
            results.append({"success": True})
        return {"executionResults": results}

    def _set_details(self, params):
        for item in params["elementsWithDetails"]:
            self.elements[item["elementId"]["guid"].upper()].update(item["details"])
        return {"executionResults": [{"success": True}
                                     for _ in params["elementsWithDetails"]]}

    def _filter(self, params):
        return {"elements": [e for e in params["elements"]
                             if e["elementId"]["guid"].upper() in self.elements
                             or e["elementId"]["guid"].upper() in self.elsewhere]}

    def _tapir(self):
        return {
            "GetFavoritesByType": lambda p: {"favorites": FAVORITES.get(p["elementType"], [])},
            "GetDetailsOfElements": self._details,
            "FilterElements": self._filter,
            "GetAllElements": lambda p=None: {"elements": [
                {"elementId": {"guid": g}} for g in self.elements]},
            "GetCurrentWindowType": {"currentWindowType": "FloorPlan"},
            "ApplyFavoritesToElements": self._apply,
            "ApplyFavoritesToElementDefaults": lambda p: {"executionResults": [
                {"success": True} for _ in p["favorites"]]},
            "SetDetailsOfElements": self._set_details,
            "GetAttributesByType": lambda p: {"attributes": ATTRIBUTES.get(p["attributeType"], [])},
            "GetLayers": lambda p: {"layers": [{"index": i} for i in LAYERS]},
        }


def run(project, guids=None, favorite=None, dry_run=True, **kwargs):
    return favorites.apply_favorites(project.conn, guids, favorite, dry_run=dry_run, **kwargs)


def warnings_of(result, guid):
    return [w["warning"] for w in result.get("warnings", []) if guid in w["guids"]]


# Listing and the dry run

def test_listing_for_guids():
    p = Project()
    result = run(p, [WALL, DOOR])
    assert result == {"favorites": {"Wall": FAVORITES["Wall"], "Door": FAVORITES["Door"]}}
    assert not p.sent("ApplyFavoritesToElements")


def test_listing_all_types():
    result = run(Project())
    assert result == {"favorites": FAVORITES}


def test_dry_run_plans_and_changes_nothing():
    p = Project()
    result = run(p, [WALL.lower()], "Partition Wall")
    assert result["dry_run"] is True
    assert result["favorite_type"] == "Wall"
    assert result["planned_count"] == 1
    assert result["planned"] == [{"guid": WALL, "type": "Wall", "id": "TEST-A",
                                  "layer": "Shell - General"}]
    assert result["keep"] == ["id"]
    assert result["applies"] == {"settings": True, "classifications": True,
                                 "categories": True, "properties": True}
    assert not p.sent("ApplyFavoritesToElements")


def test_type_mismatch_skipped():
    result = run(Project(), [WALL, DOOR], "Partition Wall")
    assert result["planned_count"] == 1
    assert result["skipped"] == [{
        "reason": "'Partition Wall' is not a Door favourite (it is a Wall favourite)",
        "count": 1, "guids": [DOOR]}]


def test_unknown_favourite_error():
    p = Project()
    result = run(p, [WALL], "Partition wall")
    assert "Partition Wall" in result["error"]
    assert not p.sent("ApplyFavoritesToElements")


def test_refused_elements_skipped():
    p = Project(elsewhere={OTHER_VIEW})
    result = run(p, [WALL, OTHER_VIEW], "Partition Wall")
    assert result["planned_count"] == 1
    assert result["skipped"][0]["guids"] == [OTHER_VIEW]
    assert "another view's database" in result["skipped"][0]["reason"]


def test_warnings_per_type():
    result = run(Project(), [WALL, DOOR], "Partition Wall", keep=[])
    wall = warnings_of(result, WALL)
    assert any("height and top link" in w and "reference line" in w for w in wall)
    assert any("layer" in w for w in wall)
    assert any("Element ID" in w for w in wall)
    door = warnings_of(run(Project(), [DOOR], "Entrance Door"), DOOR)
    assert any("library part" in w and "sill" in w for w in door)
    assert not any("Element ID" in w for w in door)


def test_bad_keep():
    result = run(Project(), [WALL], "Partition Wall", keep=["height"])
    assert "modify_elements" in result["error"]


def test_to_defaults_dry_run():
    p = Project()
    result = run(p, None, "Partition Wall", to_defaults=True)
    assert result["dry_run"] is True and result["to_defaults"] is True
    assert result["tool"] == "Wall"
    assert not p.sent("ApplyFavoritesToElementDefaults")
    assert "error" in run(Project(), [WALL], "Partition Wall", to_defaults=True)


# The commit

def test_commit_reports_changes():
    p = Project()
    result = run(p, [WALL], "Partition Wall", dry_run=False, keep=[])
    assert result["dry_run"] is False
    assert result["applied"] == 1
    changed = result["changed"][WALL]
    assert changed["id"] == {"current": "TEST-A", "new": "SW - 001"}
    assert changed["layer"] == {"current": "Shell - General", "new": "Structural - Bearing"}
    assert changed["details.height"] == {"current": 2.4, "new": 3}
    assert changed["details.relativeTopStory"] == {"current": 0, "new": 1}
    assert changed["details.compositeId"] == {"current": None, "new": "Partition 100"}
    assert changed["details.buildingMaterialId"] == {"current": "Concrete", "new": None}
    assert "details.begCoordinate.x" not in changed
    sent = p.sent("ApplyFavoritesToElements")[0]
    assert sent == {"favoritesToApply": [{"elementId": {"guid": WALL},
                                          "favorite": "Partition Wall"}],
                    "applySettings": True, "applyClassifications": True,
                    "applyCategories": True, "applyProperties": True}


def test_keep_id_restores():
    p = Project()
    result = run(p, [WALL], "Partition Wall", dry_run=False)
    written = p.sent("API.SetPropertyValuesOfElements")[0]["elementPropertyValues"]
    assert written == [{"elementId": {"guid": WALL}, "propertyId": ELEMENT_ID_PROPERTY,
                        "propertyValue": {"type": "string", "status": "normal",
                                          "value": "TEST-A"}}]
    assert result["restored"] == {"id": 1}
    assert "id" not in result["changed"][WALL]
    assert p.elements[WALL]["id"] == "TEST-A"


def test_keep_empty_id():
    elements = {WALL: dict(copy.deepcopy(WALL_DETAILS), id="")}
    p = Project(elements=elements)
    run(p, [WALL], "Partition Wall", dry_run=False)
    written = p.sent("API.SetPropertyValuesOfElements")[0]["elementPropertyValues"]
    assert written[0]["propertyValue"]["value"] == ""
    assert p.elements[WALL]["id"] == ""


def test_keep_layer_restores():
    p = Project()
    result = run(p, [WALL], "Partition Wall", dry_run=False, keep=["id", "layer"])
    assert p.sent("SetDetailsOfElements")[0] == {"elementsWithDetails": [
        {"elementId": {"guid": WALL}, "details": {"layerIndex": 54}}]}
    assert result["restored"] == {"id": 1, "layer": 1}
    assert "layer" not in result["changed"][WALL]


def test_nothing_to_restore_sends_nothing():
    p = Project()
    run(p, [DOOR], "Internal Door", dry_run=False, keep=["id", "layer"])
    assert not p.sent("API.SetPropertyValuesOfElements")
    assert not p.sent("SetDetailsOfElements")


def test_unchanged_reported():
    result = run(Project(), [DOOR], "Internal Door", dry_run=False)
    assert result["unchanged"] == [DOOR]
    assert result["applied"] == 1
    assert DOOR not in result.get("changed", {})


def test_failures_grouped():
    p = Project(fail_apply={
        WALL: (-2130312311, "Failed to apply favorite to element")})
    result = run(p, [WALL], "Partition Wall", dry_run=False)
    assert result["applied"] == 0
    failure = result["failed"][0]
    assert failure["code"] == -2130312311 and failure["count"] == 1
    assert "#812" in failure["message"]
    assert not p.sent("API.SetPropertyValuesOfElements")


def test_not_read_back_note():
    result = run(Project(), [DOOR], "Entrance Door", dry_run=False)
    assert any("not read back" in n for n in result["notes"])
    quiet = run(Project(), [DOOR], "Entrance Door", dry_run=False,
                classifications=False, categories=False, properties=False)
    assert "notes" not in quiet


def test_to_defaults_commit():
    p = Project()
    result = run(p, None, "Partition Wall", dry_run=False, to_defaults=True)
    assert p.sent("ApplyFavoritesToElementDefaults") == [{"favorites": ["Partition Wall"]}]
    assert result["applied"] is True and result["tool"] == "Wall"
