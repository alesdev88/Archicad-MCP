"""set_element_data for every kind: the plan (dry run), the send, the readback."""
import math

import pytest

from archicad_mcp.core.element_data import set_element_data
from tests.element_data_fakes import DOOR, OBJ, WALL, FakeModel

_SETS = ("API.SetPropertyValuesOfElements", "API.SetClassificationsOfElements",
         "SetClassificationsOfElements", "SetGDLParametersOfElements",
         "SetMarkerParametersOfElements", "SetIFCPropertiesOfElements")


def _dry(model, changes):
    return set_element_data(model.conn(), changes, dry_run=True)


def _planned(out):
    return {(p["guid"], p["property"]): p for p in out["planned_changes"]}


def _skips(out):
    return {(s["guid"], s["property"]): s["reason"] for s in out.get("skipped", [])}


def _sent(model):
    return [c for c, _ in model.core.calls if c in _SETS]


# ---------- the dry run ----------

def test_a_mixed_call_plans_every_kind_and_sends_nothing():
    model = FakeModel()
    out = _dry(model, [
        {"guid": OBJ, "property": "Office/Status", "value": "new"},
        {"guid": WALL, "property": "classification:Uniclass", "value": "Ss_25"},
        {"guid": OBJ, "property": "gdl:A", "value": 1.2},
        {"guid": DOOR, "property": "marker:iNumber", "value": "002"},
        {"guid": OBJ, "property": "ifc:Pset_FurnitureCommon.Style", "value": "y"},
    ])
    assert out["dry_run"] is True
    planned = _planned(out)
    assert planned[(OBJ, "Office/Status")]["current"] == "old"
    assert planned[(OBJ, "Office/Status")]["new"] == "new"
    assert planned[(WALL, "classification:Uniclass")] == {
        "guid": WALL, "property": "classification:Uniclass", "current": None, "new": "Ss_25"}
    assert (planned[(OBJ, "gdl:A")]["current"], planned[(OBJ, "gdl:A")]["new"]) == (0.9, 1.2)
    assert planned[(DOOR, "marker:iNumber")]["current"] == "001"
    assert "28" in _skips(out)[(OBJ, "ifc:Pset_FurnitureCommon.Style")]
    assert _sent(model) == []


def test_planned_changes_keep_the_input_order():
    out = _dry(FakeModel(), [
        {"guid": DOOR, "property": "marker:iNumber", "value": "002"},
        {"guid": OBJ, "property": "Office/Status", "value": "new"},
        {"guid": OBJ, "property": "gdl:B", "value": 0.7},
    ])
    assert [p["property"] for p in out["planned_changes"]] == \
        ["marker:iNumber", "Office/Status", "gdl:B"]


def test_a_single_angle_is_shown_in_radians():
    out = _dry(FakeModel(), [{"guid": OBJ, "property": "gdl:gs_angle", "value": math.pi / 2}])
    assert _planned(out)[(OBJ, "gdl:gs_angle")]["new"] == pytest.approx(math.pi / 2)


def test_an_attribute_named_shows_names():
    out = _dry(FakeModel(), [{"guid": OBJ, "property": "gdl:gs_mat", "value": "Wood - Oak"}])
    entry = _planned(out)[(OBJ, "gdl:gs_mat")]
    assert (entry["current"], entry["new"]) == ("Paint - White", "Wood - Oak")


def test_an_unknown_attribute_name_is_skipped():
    out = _dry(FakeModel(), [{"guid": OBJ, "property": "gdl:gs_mat", "value": "Gold"}])
    assert "Gold" in _skips(out)[(OBJ, "gdl:gs_mat")]


def test_a_parameter_name_matches_any_case_and_shows_as_given():
    out = _dry(FakeModel(), [{"guid": OBJ, "property": "gdl:GS_TEXT", "value": "x"}])
    assert _planned(out)[(OBJ, "gdl:GS_TEXT")]["current"] == "old"


def test_a_missing_parameter_is_skipped_with_a_hint():
    out = _dry(FakeModel(), [{"guid": OBJ, "property": "gdl:nope", "value": 1}])
    assert "gdl:*" in _skips(out)[(OBJ, "gdl:nope")]


def test_an_unlisted_value_plans_with_a_warning():
    out = _dry(FakeModel(), [{"guid": OBJ, "property": "gdl:gs_list", "value": 3}])
    assert "listed values" in _planned(out)[(OBJ, "gdl:gs_list")]["warning"]


def test_a_value_that_does_not_fit_is_skipped():
    out = _dry(FakeModel(), [{"guid": OBJ, "property": "gdl:A", "value": "wide"}])
    assert "number" in _skips(out)[(OBJ, "gdl:A")]


def test_gdl_writes_only_go_to_library_part_elements():
    out = _dry(FakeModel(), [{"guid": WALL, "property": "gdl:A", "value": 1.0}])
    assert "Wall" in _skips(out)[(WALL, "gdl:A")]


@pytest.mark.parametrize("version,skipped", [("1.5.9", True), ("1.6.0", False)])
def test_object_size_needs_a_tapir_with_the_size_fix(version, skipped):
    # Tapir before 1.6.0 writes A and B of an object whose size is not fixed
    # in the stored unit, so the object came out A x libA (#718).
    out = _dry(FakeModel(tapir_version=version), [
        {"guid": OBJ, "property": "gdl:A", "value": 1.2},
        {"guid": DOOR, "property": "gdl:A", "value": 1.0}])
    assert ((OBJ, "gdl:A") in _skips(out)) is skipped
    assert (DOOR, "gdl:A") in _planned(out)
    if skipped:
        assert "1.6.0" in _skips(out)[(OBJ, "gdl:A")]


def test_a_missing_tapir_command_skips_its_kind_only():
    out = _dry(FakeModel(unavailable={"SetMarkerParametersOfElements"}), [
        {"guid": DOOR, "property": "marker:iNumber", "value": "002"},
        {"guid": DOOR, "property": "gdl:A", "value": 1.0}])
    assert "1.7.1" in _skips(out)[(DOOR, "marker:iNumber")]
    assert (DOOR, "gdl:A") in _planned(out)


def test_a_marker_on_a_wall_is_skipped_with_tapirs_reason():
    out = _dry(FakeModel(), [{"guid": WALL, "property": "marker:iNumber", "value": "1"}])
    assert "only those have a marker" in _skips(out)[(WALL, "marker:iNumber")]


def test_marker_arrays_are_refused_and_strings_stop_at_255():
    out = _dry(FakeModel(), [{"guid": DOOR, "property": "marker:iNumber", "value": "x" * 256}])
    assert "255" in _skips(out)[(DOOR, "marker:iNumber")]


def test_a_refused_element_skips_every_change_with_one_reason():
    model = FakeModel()
    model.core.tapir_responses["FilterElements"] = lambda p: {"elements": [
        e for e in p["elements"] if e["elementId"]["guid"] != OBJ]}
    out = _dry(model, [{"guid": OBJ, "property": "Office/Status", "value": "new"},
                       {"guid": OBJ, "property": "gdl:A", "value": 1.2},
                       {"guid": DOOR, "property": "gdl:A", "value": 1.0}])
    skips = _skips(out)
    assert skips[(OBJ, "Office/Status")] == skips[(OBJ, "gdl:A")]
    assert skips[(OBJ, "gdl:A")].startswith("not editable")
    assert list(_planned(out)) == [(DOOR, "gdl:A")]


def test_lowercase_and_uppercase_guids_both_plan():
    model = FakeModel()
    out = _dry(model, [{"guid": "OBJ-1", "property": "Office/Status", "value": "new"}])
    # The fake model is keyed lowercase: an uppercase GUID is unknown to it,
    # but the editability check must not be what refuses it.
    assert "not editable" not in str(out.get("skipped"))


def test_conflicting_duplicates_are_both_skipped_and_identical_ones_collapse():
    out = _dry(FakeModel(), [
        {"guid": OBJ, "property": "gdl:A", "value": 1.2},
        {"guid": OBJ, "property": "gdl:a", "value": 1.3},
        {"guid": DOOR, "property": "gdl:B", "value": 2.0},
        {"guid": DOOR, "property": "gdl:B", "value": 2.0},
    ])
    assert [s["reason"] for s in out["skipped"]] == ["given two different values"] * 2
    assert [p["property"] for p in out["planned_changes"]] == ["gdl:B"]


@pytest.mark.parametrize("change,words", [
    ({"guid": OBJ, "property": "gdl:*", "value": 1}, "wildcard"),
    ({"guid": OBJ, "property": "gdl:A", "value": None}, "null"),
    ({"guid": OBJ, "property": "Office/Status", "value": "x", "ifc_type": "IfcLabel"},
     "ifc_type"),
    ({"guid": OBJ, "property": "ifc:Bad", "value": "x"}, "ifc:<Pset>.<Name>"),
    ({"guid": OBJ, "value": 1}, "property"),
    ({"property": "gdl:A", "value": 1}, "guid"),
    ({"guid": OBJ, "property": "gdl:A"}, "value"),
])
def test_malformed_changes_are_skipped_not_fatal(change, words):
    out = _dry(FakeModel(), [change, {"guid": DOOR, "property": "gdl:A", "value": 1.0}])
    assert words in out["skipped"][0]["reason"]
    assert [p["guid"] for p in out["planned_changes"]] == [DOOR]


def test_a_malformed_change_that_is_not_an_object_is_skipped():
    out = _dry(FakeModel(), ["gdl:A=1"])
    assert out["skipped"][0]["guid"] is None


# ---------- classifications ----------

def test_classification_by_code_by_guid_and_unclassify():
    out = _dry(FakeModel(), [
        {"guid": WALL, "property": "classification:Uniclass", "value": "Ss_25"},
        {"guid": DOOR, "property": "classification:ARCHICAD Classification",
         "value": "ITEM-WALL"},
        {"guid": OBJ, "property": "classification:ARCHICAD Classification", "value": None},
    ])
    planned = _planned(out)
    assert planned[(DOOR, "classification:ARCHICAD Classification")]["new"] == "Wall"
    assert planned[(DOOR, "classification:ARCHICAD Classification")]["current"] == "Door"
    assert planned[(OBJ, "classification:ARCHICAD Classification")]["new"] is None


@pytest.mark.parametrize("value,words", [
    ("DUP", "by GUID"),
    ("Nope", "no item 'Nope'"),
    ("ITEM-SS25", "Uniclass"),
    (3, "item ID"),
])
def test_classification_values_that_do_not_resolve(value, words):
    out = _dry(FakeModel(), [{"guid": WALL, "property": "classification:ARCHICAD Classification",
                              "value": value}])
    assert words in _skips(out)[(WALL, "classification:ARCHICAD Classification")]


def test_an_unknown_classification_system_is_skipped_with_the_systems():
    out = _dry(FakeModel(), [{"guid": WALL, "property": "classification:Nope", "value": "x"}])
    assert "Uniclass" in _skips(out)[(WALL, "classification:Nope")]


def test_a_property_only_available_after_the_classification_says_so():
    model = FakeModel()
    out = _dry(model, [
        {"guid": WALL, "property": "classification:ARCHICAD Classification", "value": "Door"},
        {"guid": WALL, "property": "Office/DoorOnly", "value": "x"}])
    assert "second call" in _skips(out)[(WALL, "Office/DoorOnly")]


def test_a_property_unavailable_without_a_classification_change_has_no_hint():
    out = _dry(FakeModel(), [{"guid": WALL, "property": "Office/DoorOnly", "value": "x"}])
    assert "second call" not in _skips(out)[(WALL, "Office/DoorOnly")]


# ---------- IFC ----------

def test_ifc_writes_plan_on_archicad_27_with_the_type_used():
    out = _dry(FakeModel(archicad=27), [
        {"guid": OBJ, "property": "ifc:Pset_FurnitureCommon.Style", "value": "y"},
        {"guid": OBJ, "property": "ifc:Pset_FurnitureCommon.IsExternal", "value": True},
        {"guid": OBJ, "property": "ifc:Pset_X.U", "value": 0.3,
         "ifc_type": "IfcThermalTransmittanceMeasure"}])
    planned = _planned(out)
    assert planned[(OBJ, "ifc:Pset_FurnitureCommon.Style")]["current"] == "x"
    assert planned[(OBJ, "ifc:Pset_FurnitureCommon.Style")]["ifc_type"] == "IfcLabel"
    assert planned[(OBJ, "ifc:Pset_FurnitureCommon.IsExternal")]["ifc_type"] == "IfcBoolean"
    assert planned[(OBJ, "ifc:Pset_X.U")]["ifc_type"] == "IfcThermalTransmittanceMeasure"


def test_an_ifc_value_that_does_not_fit_its_type_is_skipped():
    out = _dry(FakeModel(archicad=27), [
        {"guid": OBJ, "property": "ifc:Pset_X.N", "value": "3", "ifc_type": "IfcInteger"}])
    assert "whole number" in _skips(out)[(OBJ, "ifc:Pset_X.N")]
