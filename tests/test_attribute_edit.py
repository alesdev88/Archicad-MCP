"""edit_attributes: create, modify, delete and property values of attributes.

The store in tests/fixtures/attribute_replays.py answers Tapir's attribute
commands the way Tapir 1.7.1's source does, including what an overwrite loses
(a composite's useWith, a building material's texts), so these tests show the
tool puts back what Tapir would drop and refuses what it would silently get
wrong.
"""
import json

import pytest
from fastmcp import Client

import archicad_mcp.server as server_mod
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.attribute_edit import edit_attributes
from archicad_mcp.server import build_server
from tests.conftest import FakeCore
from tests.fixtures.attribute_replays import G, WritableStore

FIRE = G("prop-fire")
U_VALUE = G("prop-u")
ALL_PROPERTIES = {"properties": [
    {"propertyId": {"guid": FIRE}, "propertyType": "Custom", "propertyGroupName": "Office",
     "propertyName": "Fire Rating", "propertyCollectionType": "Single",
     "propertyValueType": "String", "propertyMeasureType": "Default"},
    {"propertyId": {"guid": U_VALUE}, "propertyType": "Custom", "propertyGroupName": "Office",
     "propertyName": "U Value", "propertyCollectionType": "Single",
     "propertyValueType": "Real", "propertyMeasureType": "Default"}],
    "propertyGroups": []}

# Elements by layer index, for the layer delete count.
ELEMENTS = {G("w-1"): 2, G("w-2"): 2}


def make(teamwork=None, missing=(), elements=None, store=None, window="FloorPlan"):
    store = store or WritableStore(properties={(G("bm-8"), FIRE): "EI 60"})
    elements = ELEMENTS if elements is None else elements
    official = {"API.IsAddOnCommandAvailable": lambda p: {
                    "available": p["addOnCommandId"]["commandName"] not in missing},
                "API.GetProductInfo": {"version": 29, "buildNumber": 5101}}
    tapir = store.tapir_table()
    tapir["GetAddOnVersion"] = {"version": "1.7.1"}
    tapir["GetAllProperties"] = ALL_PROPERTIES
    tapir["GetProjectInfo"] = {"projectName": "MCP-Test", "isTeamwork": teamwork is not None}
    tapir["GetCurrentWindowType"] = {"currentWindowType": window}
    tapir["GetAllElements"] = {"elements": [{"elementId": {"guid": g}} for g in elements]}
    tapir["GetDetailsOfElements"] = lambda p: {"detailsOfElements": [
        {"layerIndex": elements[e["elementId"]["guid"]]} for e in p["elements"]]}
    if teamwork is not None:
        tapir["GetLockableObjectSetStatus"] = lambda p: {
            "status": teamwork.get(p["objectSetName"], "Free")}
    core = FakeCore(official=official, tapir=tapir)
    return ArchicadConnection(19723, core=core), store


def sent(conn, command):
    return [p for c, p in conn._core.calls if c == command]


def writes(conn):
    return [c for c, _ in conn._core.calls
            if c.startswith("Create") or c in ("DeleteAttributes",
                                               "SetPropertyValuesOfAttributes")]


def record(store, attr_type, name):
    return store.by_name(attr_type, name)


# ---------- shape ----------

def test_changes_empty_returns_the_field_lists():
    conn, _ = make()
    out = edit_attributes(conn, [])
    composite = out["types"]["Composite"]
    assert "skins" in composite["fields"] and "useWith" in composite["fields"]
    assert composite["name_fields"] == ["buildingMaterialId", "lineTypeId"]
    assert "sourceAttributeId" in out["types"]["PenTable"]["refused_in_modify"]


@pytest.mark.parametrize("change,needle", [
    ({"create": "Layer", "delete": "Layer", "name": "X"}, "exactly one of"),
    ({"name": "X"}, "exactly one of"),
    ({"create": "Pen", "name": "X"}, "Unknown attribute type 'Pen'"),
    ({"create": "Layer"}, "needs a name"),
    ({"modify": "Layer", "isHidden": True}, "needs 'attribute'"),
    ({"create": "Layer", "name": "X", "colour": 3}, "unknown field(s) ['colour']"),
    ({"delete": "Layer", "attribute": "Interior - Partition", "isHidden": True},
     "a delete takes only"),
])
def test_shape_errors_are_skipped_with_the_reason(change, needle):
    conn, _ = make()
    out = edit_attributes(conn, [change])
    assert needle in " ".join(out["skipped"][0]["errors"])
    assert out["planned"] == []


# ---------- create ----------

def test_create_is_planned_and_nothing_is_sent_in_a_dry_run():
    conn, _ = make()
    out = edit_attributes(conn, [{"create": "Layers", "name": "MCP-Canary Layer",
                                  "isLocked": True, "intersectionGroupNr": 1}])
    [entry] = out["planned"]
    assert entry == {"action": "create", "type": "Layer", "target": "MCP-Canary Layer",
                     "fields": {"isLocked": True, "intersectionGroupNr": 1}}
    assert out["dry_run"] is True and writes(conn) == []


def test_create_on_an_existing_name_is_skipped_with_what_would_change():
    conn, _ = make()
    out = edit_attributes(conn, [{"create": "Layer", "name": "Interior - Partition",
                                  "isLocked": True, "isHidden": False}])
    [skipped] = out["skipped"]
    assert "already exists" in skipped["errors"][0] and '"modify"' in skipped["errors"][0]
    assert skipped["would_change"] == {"isLocked": [False, True]}


def test_create_next_to_a_near_name_warns():
    conn, _ = make()
    out = edit_attributes(conn, [{"create": "Layer", "name": "interior - furniture"}])
    [entry] = out["planned"]
    assert "'Interior - Furniture '" in entry["warnings"][0]


def test_create_commits_with_overwrite_off_and_reads_back():
    conn, store = make()
    out = edit_attributes(conn, [{"create": "Layer", "name": "MCP-Canary Layer",
                                  "isLocked": True}], dry_run=False)
    [payload] = sent(conn, "CreateLayers")
    assert payload["overwriteExisting"] is False
    assert payload["layerDataArray"] == [{"name": "MCP-Canary Layer", "isLocked": True}]
    [applied] = out["applied"]
    assert applied["now"] == {"name": "MCP-Canary Layer", "isLocked": True}
    assert record(store, "Layer", "MCP-Canary Layer")["isLocked"] is True


def test_a_create_racing_a_teammate_is_reported_not_overwritten():
    conn, store = make()
    plan_only = edit_attributes(conn, [{"create": "Layer", "name": "MCP-Canary Layer"}])
    assert plan_only["planned"]
    store.records["Layer"].append({"attributeId": {"guid": G("theirs")}, "index": 9,
                                   "name": "MCP-Canary Layer", "isHidden": True})
    conn._core.tapir_responses["GetAttributesByType"] = WritableStore.get_by_type.__get__(
        _StaleNames(store))
    out = edit_attributes(conn, [{"create": "Layer", "name": "MCP-Canary Layer"}],
                          dry_run=False)
    assert out["failed"][0]["message"] == "Already exists."
    assert record(store, "Layer", "MCP-Canary Layer")["isHidden"] is True


class _StaleNames:
    """GetAttributesByType answering as before the teammate's layer existed."""

    def __init__(self, store):
        self.records = {t: [r for r in rs if r["name"] != "MCP-Canary Layer"]
                        for t, rs in store.records.items()}


def test_a_composite_create_needs_use_with_skins_and_separators():
    conn, _ = make()
    out = edit_attributes(conn, [{"create": "Composite", "name": "MCP-Canary Wall",
                                  "skins": [{"type": "Core", "framePen": 1,
                                             "thickness": 0.2,
                                             "buildingMaterialId": "Plaster"}]}])
    errors = " ".join(out["skipped"][0]["errors"])
    assert "useWith" in errors and "separators" in errors


def test_a_profile_create_needs_a_source_or_new_skins():
    conn, _ = make()
    out = edit_attributes(conn, [{"create": "Profile", "name": "MCP-Canary Profile"}])
    assert "sourceAttributeId or newSkins" in out["skipped"][0]["errors"][0]


def test_a_reference_to_an_attribute_created_in_the_same_call_resolves_on_commit():
    conn, store = make()
    out = edit_attributes(conn, [
        {"create": "Composite", "name": "MCP-Canary Wall", "useWith": ["Wall"],
         "skins": [{"type": "Core", "framePen": 1, "thickness": 0.2,
                    "buildingMaterialId": "MCP-Canary Material"}],
         "separators": [{"linePen": 1, "lineTypeId": "Solid Line"},
                        {"linePen": 1, "lineTypeId": "Solid Line"}]},
        {"create": "BuildingMaterial", "name": "MCP-Canary Material",
         "cutFillIndex": "Face Brick"}], dry_run=False)
    order = [c for c in writes(conn)]
    assert order == ["CreateBuildingMaterials", "CreateComposites"]
    material = record(store, "BuildingMaterial", "MCP-Canary Material")
    assert material["cutFillIndex"] == 2
    composite = record(store, "Composite", "MCP-Canary Wall")
    assert composite["skins"][0]["buildingMaterialId"] == material["attributeId"] or \
        composite["skins"][0]["buildingMaterialId"] == {"attributeId": material["attributeId"]}
    assert {a["target"] for a in out["applied"]} == {"MCP-Canary Wall", "MCP-Canary Material"}


def test_a_reference_to_an_attribute_renamed_in_the_same_call_keeps_its_guid():
    """Live 19.10.2026: the layer was renamed first (layers go before layer
    combinations), and a by-name lookup at send time no longer found it."""
    conn, store = make()
    out = edit_attributes(conn, [
        {"modify": "Layer", "attribute": "Interior - Partition", "name": "MCP-Canary Renamed"},
        {"modify": "LayerCombination", "attribute": "02 Drafting",
         "layers": [{"attributeId": "Interior - Partition", "isLocked": True}]}],
        dry_run=False)
    assert "failed" not in out, out
    assert "not_applied" not in out, out["not_applied"]
    combination = record(store, "LayerCombination", "02 Drafting")
    partition = next(e for e in combination["layers"] if e["attributeId"]["guid"] == G("lay-2"))
    assert partition["isLocked"] is True


def test_a_dashed_line_period_alone_is_refused():
    """Live 19.10.2026: period 3 on a Dashed line read back as 2, the sum of its
    dash items."""
    conn, _ = make()
    out = edit_attributes(conn, [{"modify": "Line", "attribute": "Dotted Dense", "period": 3.0}])
    assert "dashItems" in out["skipped"][0]["errors"][0]


def test_new_layers_and_materials_warn_about_zero_starts():
    conn, _ = make()
    out = edit_attributes(conn, [{"create": "Layer", "name": "MCP-Canary Layer"},
                                 {"create": "BuildingMaterial", "name": "MCP-Canary Material"}])
    layer, material = out["planned"]
    assert "intersectionGroupNr" in layer["warnings"][0]
    assert "cutSurfaceIndex" in material["warnings"][0]
    assert "cutFillIndex" in material["warnings"][0]


# ---------- references and values Tapir would silently get wrong ----------

def test_an_unknown_reference_is_skipped_before_tapir_writes_index_0():
    conn, _ = make()
    out = edit_attributes(conn, [{"modify": "Composite", "attribute": "100 Block Plastered",
                                  "skins": [{"buildingMaterialId": "Nope"}, {}]}])
    assert "no BuildingMaterial named 'Nope'" in out["skipped"][0]["errors"][0]


@pytest.mark.parametrize("change,needle", [
    ({"create": "Line", "name": "MCP-Canary Line", "lineType": "Dashd"}, "lineType"),
    ({"create": "Fill", "name": "MCP-Canary Fill", "subType": "vector"}, "subType"),
    ({"modify": "BuildingMaterial", "attribute": "Plaster",
      "cutFillOrientation": "Origin"}, "cutFillOrientation"),
    ({"modify": "Composite", "attribute": "100 Block Plastered", "useWith": ["Walls"]},
     "useWith"),
    ({"modify": "Surface", "attribute": "Stucco - White Rough", "materialType": "Shiny"},
     "materialType"),
])
def test_values_tapir_would_replace_with_a_default_are_refused(change, needle):
    conn, _ = make()
    out = edit_attributes(conn, [change])
    assert needle in " ".join(out["skipped"][0]["errors"])


def test_whole_number_pens_are_sent_as_integers():
    conn, _ = make()
    edit_attributes(conn, [{"modify": "BuildingMaterial", "attribute": "Plaster",
                            "cutFillPen": 12.0}], dry_run=False)
    [payload] = sent(conn, "CreateBuildingMaterials")
    pen = payload["buildingMaterialDataArray"][0]["cutFillPen"]
    assert pen == 12 and isinstance(pen, int)


# ---------- modify: what each type's overwrite would otherwise lose ----------

def test_a_layer_modify_sends_the_guid_the_name_and_the_change():
    conn, store = make()
    out = edit_attributes(conn, [{"modify": "Layer", "attribute": "Interior - Partition",
                                  "isLocked": True}], dry_run=False)
    [payload] = sent(conn, "CreateLayers")
    assert payload["overwriteExisting"] is True
    assert payload["layerDataArray"] == [{"attributeId": {"guid": G("lay-2")},
                                          "name": "Interior - Partition",
                                          "isLocked": True}]
    assert out["applied"][0]["now"] == {"isLocked": True}
    assert out["planned"][0]["changes"] == {"isLocked": [False, True]}


def test_a_rename_keeps_the_guid():
    conn, store = make()
    out = edit_attributes(conn, [{"modify": "Layer", "attribute": G("lay-2"),
                                  "name": "MCP-Canary Renamed"}], dry_run=False)
    assert record(store, "Layer", "MCP-Canary Renamed")["attributeId"]["guid"] == G("lay-2")
    assert out["planned"][0]["changes"] == {"name": ["Interior - Partition",
                                                     "MCP-Canary Renamed"]}


def test_a_rename_onto_an_existing_or_near_name_is_skipped():
    conn, _ = make()
    out = edit_attributes(conn, [
        {"modify": "Layer", "attribute": "Interior - Partition", "name": "\u0014"},
        {"modify": "Layer", "attribute": "Interior - Partition",
         "name": "Interior - Furniture"}])
    assert all("already" in s["errors"][0] for s in out["skipped"])


def test_the_same_attribute_changed_twice_in_one_call_is_refused():
    """Each payload is built from the state before the call, so a second modify
    of the same composite would resend the old name and undo the first."""
    conn, _ = make()
    out = edit_attributes(conn, [
        {"modify": "Composite", "attribute": "100 Block Plastered", "name": "MCP-Canary Wall"},
        {"modify": "Composite", "attribute": "100 Block Plastered", "useWith": ["Slab"]}])
    assert len(out["planned"]) == 1
    assert "one change" in out["skipped"][0]["errors"][0]


def test_an_unknown_attribute_names_the_near_match():
    conn, _ = make()
    out = edit_attributes(conn, [{"modify": "Layer", "attribute": "Interior - Furniture",
                                  "isHidden": True}])
    assert "'Interior - Furniture '" in out["skipped"][0]["errors"][0]


def test_a_composite_modify_keeps_use_with_and_patches_skins_by_position():
    conn, store = make()
    out = edit_attributes(conn, [{"modify": "Composite", "attribute": "100 Block Plastered",
                                  "skins": [{}, {"thickness": 0.2}]}], dry_run=False)
    assert out["planned"][0]["changes"] == {"skins[1].thickness": [0.1, 0.2]}
    [payload] = sent(conn, "CreateComposites")
    item = payload["compositeDataArray"][0]
    assert item["useWith"] == ["Wall"] and len(item["separators"]) == 3
    composite = record(store, "Composite", "100 Block Plastered")
    assert composite["useWith"] == ["Wall"]
    assert [s["thickness"] for s in composite["skins"]] == [0.012, 0.2]
    assert composite["skins"][0]["buildingMaterialId"] == {"attributeId": {"guid": G("bm-9")}}


def test_a_composite_skin_count_change_needs_separators():
    conn, _ = make()
    out = edit_attributes(conn, [{"modify": "Composite", "attribute": "100 Block Plastered",
                                  "skins": [{}]}])
    assert "separators" in out["skipped"][0]["errors"][0]


def test_a_building_material_modify_resends_its_texts():
    conn, store = make()
    edit_attributes(conn, [{"modify": "BuildingMaterial",
                            "attribute": "Concrete Block - Structural",
                            "density": 1500}], dry_run=False)
    material = record(store, "BuildingMaterial", "Concrete Block - Structural")
    assert material["id"] == "ST-05" and material["density"] == 1500


def test_a_building_material_takes_its_fill_by_name():
    conn, store = make()
    out = edit_attributes(conn, [{"modify": "BuildingMaterial", "attribute": "Plaster",
                                  "cutFillIndex": "Concrete Block"}], dry_run=False)
    assert out["planned"][0]["changes"] == {"cutFillIndex": ["Face Brick", "Concrete Block"]}
    assert record(store, "BuildingMaterial", "Plaster")["cutFillIndex"] == 5
    assert out["applied"][0]["now"] == {"cutFillIndex": "Concrete Block"}


def test_line_pattern_fields_carry_the_line_type():
    conn, _ = make()
    edit_attributes(conn, [{"modify": "Line", "attribute": "Dotted Dense",
                            "dashItems": [{"dash": 1.0, "gap": 1.0}]}], dry_run=False)
    [payload] = sent(conn, "CreateLines")
    assert payload["lineDataArray"][0]["lineType"] == "Dashed"


def test_a_layer_combination_modify_sends_every_layer_with_merged_flags():
    conn, store = make()
    out = edit_attributes(conn, [{"modify": "LayerCombination", "attribute": "02 Drafting",
                                  "layers": [{"attributeId": "Interior - Partition",
                                              "isLocked": True}]}], dry_run=False)
    [payload] = sent(conn, "CreateLayerCombinations")
    layers = payload["layerCombinationDataArray"][0]["layers"]
    assert len(layers) == 2
    partition = next(e for e in layers if e["attributeId"] == {"guid": G("lay-2")})
    assert partition["isHidden"] is True and partition["isLocked"] is True
    assert out["planned"][0]["changes"] == {"layers[Interior - Partition].isLocked":
                                            [False, True]}


def test_pens_patch_only_the_pens_named():
    conn, store = make()
    edit_attributes(conn, [{"modify": "PenTable", "attribute": "07 Mechanical",
                            "pens": [{"index": 5, "width": 0.5}]}], dry_run=False)
    [payload] = sent(conn, "CreatePenTables")
    assert payload["penTableDataArray"][0]["pens"] == [
        {"index": 5, "color": {"red": 0, "green": 0, "blue": 0}, "width": 0.5,
         "description": "Pen 5"}]
    pens = record(store, "PenTable", "07 Mechanical")["pens"]
    assert len(pens) == 255 and pens[4]["width"] == 0.5 and pens[3]["width"] == 0.13


@pytest.mark.parametrize("change,needle", [
    ({"modify": "Fill", "attribute": "Face Brick", "subType": "Solid"}, "type cannot change"),
    ({"modify": "PenTable", "attribute": "07 Mechanical",
      "sourceAttributeId": "07 Mechanical"}, "255 pens"),
    ({"modify": "Profile", "attribute": "Brick Veneer Block Wall", "replaceSkins": True},
     "geometry"),
    ({"modify": "Surface", "attribute": "Stucco - White Rough",
      "texture": {"name": "Stucco", "mirrorX": False}}, None),
])
def test_fields_a_modify_cannot_send_safely_are_refused(change, needle):
    conn, _ = make()
    out = edit_attributes(conn, [change])
    if needle is None:
        assert out["planned"], out          # turning off a flag that is off is fine
    else:
        assert needle in out["skipped"][0]["errors"][0]


def test_a_texture_flag_cannot_be_switched_off():
    conn, store = make()
    store.records["Surface"][0]["texture"]["mirrorX"] = True
    out = edit_attributes(conn, [{"modify": "Surface", "attribute": "Stucco - White Rough",
                                  "texture": {"mirrorX": False}}])
    assert "switched off" in out["skipped"][0]["errors"][0]


def test_a_texture_change_sends_the_whole_texture():
    conn, _ = make()
    edit_attributes(conn, [{"modify": "Surface", "attribute": "Stucco - White Rough",
                            "texture": {"xSize": 1.2}}], dry_run=False)
    [payload] = sent(conn, "CreateSurfaces")
    texture = payload["surfaceDataArray"][0]["texture"]
    assert texture["name"] == "Stucco" and texture["xSize"] == 1.2


def test_profile_skin_overrides_take_names():
    conn, store = make()
    edit_attributes(conn, [{"modify": "Profile", "attribute": "Brick Veneer Block Wall",
                            "skinOverrides": [{"skinId": "F3B62A00",
                                               "buildingMaterialId": "Plaster"}]}],
                    dry_run=False)
    skin = record(store, "Profile", "Brick Veneer Block Wall")["skins"][0]
    assert skin["buildingMaterialId"] == {"attributeId": {"guid": G("bm-9")}}


def test_nothing_to_change_is_not_sent():
    conn, _ = make()
    out = edit_attributes(conn, [{"modify": "Layer", "attribute": "Interior - Partition",
                                  "isLocked": False}], dry_run=False)
    assert "nothing to change" in out["planned"][0]["warnings"]
    assert writes(conn) == []


def test_a_field_archicad_did_not_change_is_reported():
    conn, store = make()
    real = conn._core.tapir_responses["CreateLayers"]

    def ignores_lock(params):
        for item in params["layerDataArray"]:
            item.pop("isLocked", None)
        return real(params)
    conn._core.tapir_responses["CreateLayers"] = ignores_lock
    out = edit_attributes(conn, [{"modify": "Layer", "attribute": "Interior - Partition",
                                  "isLocked": True}], dry_run=False)
    assert out["not_applied"] == [{"type": "Layer", "target": "Interior - Partition",
                                   "field": "isLocked", "sent": True, "read": False}]


# ---------- built-ins and deletes ----------

@pytest.mark.parametrize("change", [
    {"modify": "Layer", "attribute": "\u0014", "isLocked": True},
    {"delete": "Line", "attribute": "Solid Line"},
    {"delete": "Fill", "attribute": "Empty Fill"},
    {"delete": "Composite", "attribute": "100 Block Plastered"},
])
def test_attributes_archicad_refuses_to_change_are_skipped(change):
    conn, _ = make()
    out = edit_attributes(conn, [change])
    assert out["skipped"] and out["planned"] == []


def test_a_layer_with_elements_on_it_is_not_deleted():
    conn, _ = make()
    out = edit_attributes(conn, [{"delete": "Layer", "attribute": "Interior - Partition"}],
                          dry_run=False)
    error = out["skipped"][0]["errors"][0]
    assert "2 elements" in error and "modify_elements" in error
    assert writes(conn) == []


def test_an_empty_layer_is_deleted_with_a_warning():
    conn, store = make()
    out = edit_attributes(conn, [{"delete": "Layer", "attribute": "Interior - Furniture "}],
                          dry_run=False)
    assert "sections" in out["planned"][0]["warnings"][0]
    [payload] = sent(conn, "DeleteAttributes")
    assert payload == {"attributesToDelete": [
        {"attributeType": "Layer", "attributeId": {"attributeId": {"guid": G("lay-3")}}}]}
    assert out["applied"] == [{"action": "delete", "type": "Layer",
                               "target": "Interior - Furniture "}]
    assert ("Layer", "Interior - Furniture ") in store.deleted


def test_a_layer_delete_needs_the_floor_plan_active():
    conn, _ = make(window="Layout")
    out = edit_attributes(conn, [{"delete": "Layer", "attribute": "Interior - Furniture "}])
    assert "floor plan" in out["skipped"][0]["errors"][0]


def test_deleting_another_type_warns_about_missing_references():
    conn, _ = make()
    out = edit_attributes(conn, [{"delete": "Surface", "attribute": "Stucco - White Rough"}])
    assert "missing" in out["planned"][0]["warnings"][0]


def test_deletes_go_last():
    conn, _ = make()
    edit_attributes(conn, [{"delete": "Fill", "attribute": "Concrete Block"},
                           {"modify": "Layer", "attribute": "Interior - Partition",
                            "isLocked": True}], dry_run=False)
    assert writes(conn) == ["CreateLayers", "DeleteAttributes"]


# ---------- found by review, 19.10.2026 ----------

def test_a_layer_delete_with_unreadable_elements_is_refused():
    conn, _ = make(elements={G("w-1"): 2, G("w-3"): 3})
    real = conn._core.tapir_responses["GetDetailsOfElements"]

    def one_unreadable(params):
        answer = real(params)
        for i, e in enumerate(params["elements"]):
            if e["elementId"]["guid"] == G("w-3"):
                answer["detailsOfElements"][i] = {"error": {"code": -1, "message": "x"}}
        return answer
    conn._core.tapir_responses["GetDetailsOfElements"] = one_unreadable
    out = edit_attributes(conn, [{"delete": "Layer", "attribute": "Interior - Furniture "}])
    assert "could not be read" in out["skipped"][0]["errors"][0]


def test_a_layer_delete_on_a_tapir_that_refuses_the_read_is_refused():
    from multiconn_archicad.errors import TapirCommandError
    conn, _ = make()

    def refuse(params):
        raise TapirCommandError(message="unknown field", code=-1)
    conn._core.tapir_responses["GetDetailsOfElements"] = refuse
    out = edit_attributes(conn, [{"delete": "Layer", "attribute": "Interior - Furniture "}])
    assert "could not be read" in out["skipped"][0]["errors"][0]


def test_a_combination_layer_given_by_guid_patches_that_layer():
    conn, store = make()
    out = edit_attributes(conn, [{"modify": "LayerCombination", "attribute": "02 Drafting",
                                  "layers": [{"attributeId": G("lay-2"), "isLocked": True}]}],
                          dry_run=False)
    assert out["planned"][0]["changes"] == {"layers[Interior - Partition].isLocked":
                                            [False, True]}
    [payload] = sent(conn, "CreateLayerCombinations")
    assert len(payload["layerCombinationDataArray"][0]["layers"]) == 2
    assert "not_applied" not in out


def test_a_reference_given_as_a_guid_string_is_shown_by_name():
    conn, _ = make()
    out = edit_attributes(conn, [
        {"modify": "Surface", "attribute": "Stucco - White Rough", "fillId": G("fil-2")}])
    assert "nothing to change" in out["planned"][0]["warnings"]
    out = edit_attributes(conn, [
        {"modify": "Surface", "attribute": "Stucco - White Rough", "fillId": G("fil-5")}],
        dry_run=False)
    assert out["planned"][0]["changes"] == {"fillId": ["Face Brick", "Concrete Block"]}
    assert "not_applied" not in out


def test_stale_layer_entries_are_left_out_of_a_combination_modify():
    conn, store = make()
    store.records["LayerCombination"][0]["layers"].append(
        {"attributeId": {"guid": "00000000-0000-0000-0000-000000000000"}, "isHidden": False,
         "isLocked": False, "isWireframe": False, "intersectionGroupNr": 1})
    out = edit_attributes(conn, [{"modify": "LayerCombination", "attribute": "02 Drafting",
                                  "name": "MCP-Canary Drafting"}], dry_run=False)
    assert "no longer exist" in out["planned"][0]["warnings"][0]
    [payload] = sent(conn, "CreateLayerCombinations")
    assert len(payload["layerCombinationDataArray"][0]["layers"]) == 2


def test_write_only_inputs_are_not_reported_as_not_applied():
    conn, _ = make()
    out = edit_attributes(conn, [
        {"create": "Profile", "name": "MCP-Canary Profile",
         "sourceAttributeId": "Brick Veneer Block Wall"},
        {"create": "Fill", "name": "MCP-Canary Fill", "subType": "Vector",
         "texture": {"name": "x", "FillRectangle": True}},
        {"modify": "Profile", "attribute": "Brick Veneer Block Wall",
         "skinOverrides": [{"skinId": "F3B62A00", "contourPen": 5,
                            "edgeOverrides": [{"edgeIndex": 1, "pen": 3}]}]}],
        dry_run=False)
    assert "failed" not in out and "skipped" not in out, out
    assert len(out["applied"]) == 3
    assert "not_applied" not in out, out["not_applied"]


def test_two_renames_to_one_name_are_refused():
    conn, _ = make()
    out = edit_attributes(conn, [
        {"modify": "Layer", "attribute": "Interior - Partition", "name": "MCP-Canary Same"},
        {"modify": "Layer", "attribute": "Interior - Furniture ", "name": "mcp-canary same"},
        {"create": "Layer", "name": "MCP-Canary Same"}])
    assert len(out["planned"]) == 1
    assert all("this call" in s["errors"][0] for s in out["skipped"])


def test_an_empty_rename_is_refused():
    conn, _ = make()
    out = edit_attributes(conn, [{"modify": "Layer", "attribute": "Interior - Partition",
                                  "name": "  "}])
    assert "name" in out["skipped"][0]["errors"][0]


def test_a_source_created_in_the_same_call_is_refused():
    conn, _ = make()
    out = edit_attributes(conn, [
        {"create": "PenTable", "name": "MCP-Canary Pens"},
        {"create": "PenTable", "name": "MCP-Canary Pens 2", "sourceAttributeId": "MCP-Canary Pens"}])
    assert "same call" in out["skipped"][0]["errors"][0]


def test_a_read_back_failure_keeps_the_report():
    from multiconn_archicad.errors import TapirCommandError
    conn, _ = make()
    real = conn._core.tapir_responses["GetLayers"]
    calls = []

    def fails_on_read_back(params):
        calls.append(1)
        if len(calls) > 1:
            raise TapirCommandError(message="Invalid program status", code=4001)
        return real(params)
    conn._core.tapir_responses["GetLayers"] = fails_on_read_back
    out = edit_attributes(conn, [{"modify": "Layer", "attribute": "Interior - Partition",
                                  "isLocked": True}], dry_run=False)
    assert out["applied"] == [{"action": "modify", "type": "Layer",
                               "target": "Interior - Partition"}]
    assert "read-back" in out["error"]["message"]


def test_a_failure_resolving_a_later_batch_keeps_the_report():
    from multiconn_archicad.errors import TapirCommandError
    conn, store = make()
    real = conn._core.tapir_responses["GetAttributesByType"]
    calls = {"n": 0}

    def flaky(params):
        calls["n"] += 1
        if params["attributeType"] == "BuildingMaterial" and calls["n"] > 3:
            raise TapirCommandError(message="Invalid program status", code=4001)
        return real(params)
    conn._core.tapir_responses["GetAttributesByType"] = flaky
    out = edit_attributes(conn, [
        {"create": "BuildingMaterial", "name": "MCP-Canary Material", "cutFillIndex": 2,
         "cutSurfaceIndex": 132},
        {"create": "Composite", "name": "MCP-Canary Wall", "useWith": ["Wall"],
         "skins": [{"type": "Core", "framePen": 1, "thickness": 0.2,
                    "buildingMaterialId": "MCP-Canary Material"}],
         "separators": [{"linePen": 1, "lineTypeId": "Solid Line"},
                        {"linePen": 1, "lineTypeId": "Solid Line"}]}], dry_run=False)
    assert "error" in out
    assert record(store, "BuildingMaterial", "MCP-Canary Material") is not None
    failed_targets = [t for g in out.get("failed", []) for t in g["sample"]]
    assert "Composite/MCP-Canary Wall" in failed_targets


def test_an_empty_pen_list_changes_nothing_and_float_pen_indices_match():
    conn, _ = make()
    out = edit_attributes(conn, [{"modify": "PenTable", "attribute": "07 Mechanical",
                                  "pens": []}])
    assert "nothing to change" in out["planned"][0]["warnings"]
    out = edit_attributes(conn, [{"modify": "PenTable", "attribute": "07 Mechanical",
                                  "pens": [{"index": 5.0, "width": 0.5}]}], dry_run=False)
    assert out["planned"][0]["changes"] == {"pens[5].width": [0.13, 0.5]}
    assert "not_applied" not in out


def test_a_composite_skin_without_a_material_says_so():
    conn, store = make()
    del store.records["Composite"][0]["skins"][0]["buildingMaterialId"]
    out = edit_attributes(conn, [{"modify": "Composite", "attribute": "100 Block Plastered",
                                  "useWith": ["Wall", "Slab"]}])
    assert "building material" in out["skipped"][0]["errors"][0]


def test_read_records_answers_none_for_a_missing_answer():
    from archicad_mcp.core.attribute_types import TYPES
    from archicad_mcp.core.attributes import read_records
    conn, _ = make()
    conn._core.tapir_responses["GetLayers"] = {"layers": []}
    assert read_records(conn, TYPES["Layer"], [G("lay-2")]) == [None]


# ---------- property values ----------

def test_property_values_show_current_and_new():
    conn, _ = make()
    out = edit_attributes(conn, [{"modify": "BuildingMaterial",
                                  "attribute": "Concrete Block - Structural",
                                  "properties": {"Office/Fire Rating": "EI 90"}}])
    assert out["planned"][0]["changes"] == {"properties": {"Office/Fire Rating":
                                                           ["EI 60", "EI 90"]}}


def test_a_property_not_available_for_the_attribute_is_skipped():
    conn, _ = make()
    out = edit_attributes(conn, [{"modify": "BuildingMaterial",
                                  "attribute": "Concrete Block - Structural",
                                  "properties": {"Office/U Value": 0.3}}])
    assert "not available" in out["skipped"][0]["errors"][0]


def test_property_values_commit_and_read_back():
    conn, store = make()
    out = edit_attributes(conn, [{"modify": "BuildingMaterial",
                                  "attribute": "Concrete Block - Structural",
                                  "properties": {"Office/Fire Rating": "EI 90"}}],
                          dry_run=False)
    [payload] = sent(conn, "SetPropertyValuesOfAttributes")
    assert payload == {"attributePropertyValues": [
        {"attributeId": {"guid": G("bm-8")}, "propertyId": {"guid": FIRE},
         "propertyValue": {"value": "EI 90"}}]}
    assert store.values[(G("bm-8"), FIRE)] == "EI 90"
    assert out["applied"][0]["now"] == {"properties": {"Office/Fire Rating": "EI 90"}}


def test_an_empty_property_answer_counts_as_not_written():
    conn, _ = make()
    conn._core.tapir_responses["SetPropertyValuesOfAttributes"] = \
        lambda p: {"executionResults": [{}]}
    out = edit_attributes(conn, [{"modify": "BuildingMaterial",
                                  "attribute": "Concrete Block - Structural",
                                  "properties": {"Office/Fire Rating": "EI 90"}}],
                          dry_run=False)
    assert "not written" in out["failed"][0]["message"]


def test_an_unknown_property_is_skipped():
    conn, _ = make()
    out = edit_attributes(conn, [{"modify": "BuildingMaterial", "attribute": "Plaster",
                                  "properties": {"Office/Nope": "x"}}])
    assert "Office/Nope" in out["skipped"][0]["errors"][0]


# ---------- Tapir and Teamwork ----------

def test_a_type_whose_command_is_missing_is_skipped_with_the_version():
    conn, _ = make(missing={"CreateFills"})
    out = edit_attributes(conn, [{"create": "Fill", "name": "MCP-Canary Fill"},
                                 {"create": "Layer", "name": "MCP-Canary Layer"}])
    assert "1.5.4" in out["skipped"][0]["errors"][0]
    assert [p["type"] for p in out["planned"]] == ["Layer"]


def test_teamwork_skips_writes_whose_set_is_not_reserved():
    conn, _ = make(teamwork={"LayerSettingsDialog": "Editable", "Composites": "Free"})
    out = edit_attributes(conn, [
        {"modify": "Layer", "attribute": "Interior - Partition", "isLocked": True},
        {"modify": "Composite", "attribute": "100 Block Plastered", "useWith": ["Slab"]}],
        dry_run=False)
    assert [p["type"] for p in out["planned"]] == ["Layer"]
    assert 'reserve_elements(object_sets=["Composites"], confirm=true)' in \
        out["skipped"][0]["errors"][0]
    assert {e["set"]: e["status"] for e in out["teamwork"]} == {
        "LayerSettingsDialog": "Editable", "Composites": "Free"}
    assert writes(conn) == ["CreateLayers"]


def test_teamwork_property_values_only_warn():
    conn, _ = make(teamwork={"BuildingMaterials": "Free"})
    out = edit_attributes(conn, [{"modify": "BuildingMaterial",
                                  "attribute": "Concrete Block - Structural",
                                  "properties": {"Office/Fire Rating": "EI 90"}}])
    assert "reserve_elements" in out["planned"][0]["warnings"][0]


def test_teamwork_layer_flags_only_warn():
    conn, _ = make(teamwork={"LayerSettingsDialog": "Free"})
    out = edit_attributes(conn, [
        {"modify": "Layer", "attribute": "Interior - Partition", "isHidden": True},
        {"modify": "Layer", "attribute": "Interior - Furniture ", "name": "MCP-Canary X"}])
    assert [p["target"] for p in out["planned"]] == ["Interior - Partition"]
    assert "reserve_elements" in out["planned"][0]["warnings"][0]
    assert "reserve_elements" in out["skipped"][0]["errors"][0]


def test_a_whole_command_refused_stops_the_rest():
    from multiconn_archicad.errors import TapirCommandError
    conn, _ = make()

    def refuse(params):
        raise TapirCommandError(message="Invalid parameters", code=-2130313112)
    conn._core.tapir_responses["CreateLayers"] = refuse
    out = edit_attributes(conn, [
        {"modify": "Layer", "attribute": "Interior - Partition", "isLocked": True},
        {"delete": "Fill", "attribute": "Concrete Block"}], dry_run=False)
    assert out["error"]["message"] == "Invalid parameters"
    assert out["stopped"] == [{"action": "delete", "type": "Fill",
                               "target": "Concrete Block"}]
    assert "DeleteAttributes" not in writes(conn)


# ---------- tool ----------

async def test_the_tool_is_registered_and_dry_runs_by_default(monkeypatch):
    conn, _ = make()
    monkeypatch.setattr(server_mod, "get_connection", lambda port: conn)
    async with Client(build_server(mode="full")) as client:
        tools = {t.name: t for t in await client.list_tools()}
        assert tools["edit_attributes"].annotations.destructiveHint is True
        result = await client.call_tool("edit_attributes", {"changes": [
            {"modify": "Layer", "attribute": "Interior - Partition", "isLocked": True}]})
        payload = json.loads(result.content[0].text)
        assert payload["dry_run"] is True and writes(conn) == []
