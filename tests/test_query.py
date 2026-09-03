"""find_elements: what gets read, in which order, and what gets skipped.

The fixture project (tests/fixtures/api_replays.py) has two walls and one
zone; w-1 has a Fire Rating and is classified Wall, w-2 is unclassified, and
the zone is classified Zone. The user properties are available (by
classification) on Wall only, which is the live availability shape.
"""
import pytest

from archicad_mcp import extract
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.query import find_elements
from tests.conftest import FakeCore
from tests.fixtures import api_replays


def make_conn(tapir=True, **official_overrides):
    official = dict(api_replays.OFFICIAL)
    official.update(official_overrides)
    if not tapir:
        official["API.IsAddOnCommandAvailable"] = {"available": False}
    return ArchicadConnection(19723, core=FakeCore(
        official=official, tapir=api_replays.TAPIR if tapir else {}))


def calls(conn, name):
    return [p for c, p in conn._core.calls if c == name]


def prop(property, operator, value=None):
    d = {"property": property, "operator": operator}
    if value is not None:
        d["value"] = value
    return d


# ---------- shapes ----------

def test_result_shape_and_type_counts():
    payload = find_elements(make_conn(), [{"element_types": ["Wall", "Zone"]}])
    assert payload["count"] == 3
    assert payload["by_type"] == {"Wall": 2, "Zone": 1}
    assert payload["candidates"] == 3
    assert payload["property_reads"] == 0
    assert payload["coverage"] == "whole-plan"


def test_groups_union_and_element_types_is_not():
    payload = find_elements(make_conn(), [
        {"element_types": ["Zone"]},
        {"element_types": ["Zone"], "element_types_operator": "is_not",
         "comparisons": [prop("OFFICE/Fire Rating", "has_value")]},
    ])
    assert set(payload["guids"]) == {"z-1", "w-1"}


# ---------- property comparisons ----------

def test_property_equal_and_has_no_value():
    conn = make_conn()
    payload = find_elements(conn, [{"element_types": ["Wall"], "comparisons": [
        prop("OFFICE/Fire Rating", "equal", "ei60")]}])
    assert payload["guids"] == ["w-1"]
    payload = find_elements(conn, [{"element_types": ["Wall"], "comparisons": [
        prop("OFFICE/Fire Rating", "has_no_value")]}])
    assert payload["guids"] == ["w-2"]


def test_or_group():
    payload = find_elements(make_conn(), [{"element_types": ["Wall"], "logical_operator": "or",
                                           "comparisons": [
        prop("OFFICE/Fire Rating", "equal", "EI60"),
        prop("ModelView_LayerName", "equal", "Sketch")]}])
    assert set(payload["guids"]) == {"w-1", "w-2"}


def test_property_reads_are_scoped_to_the_group_candidates():
    conn = make_conn()
    find_elements(conn, [{"element_types": ["Wall"], "comparisons": [
        prop("ModelView_LayerName", "has_value")]}])
    reads = calls(conn, "API.GetPropertyValuesOfElements")
    assert len(reads) == 1
    assert {e["elementId"]["guid"] for e in reads[0]["elements"]} == {"w-1", "w-2"}


# ---------- availability pre-check ----------

def test_unavailable_pairs_are_answered_without_a_read():
    """OFFICE/Fire Rating is available on Wall-classified elements only. The
    unclassified wall and the zone must never be sent to
    GetPropertyValuesOfElements for it; only w-1 is read."""
    conn = make_conn()
    payload = find_elements(conn, [{"element_types": ["Wall", "Zone"], "comparisons": [
        prop("OFFICE/Fire Rating", "not_available")]}])
    assert set(payload["guids"]) == {"w-2", "z-1"}
    assert payload["skipped_not_available"] == 2
    reads = calls(conn, "API.GetPropertyValuesOfElements")
    assert [[e["elementId"]["guid"] for e in r["elements"]] for r in reads] == [["w-1"]]
    assert payload["property_reads"] == 1


def test_builtins_have_no_availability_pre_check_and_are_read():
    conn = make_conn()
    payload = find_elements(conn, [{"element_types": ["Wall", "Zone"], "comparisons": [
        prop("ModelView_LayerName", "has_value")]}])
    assert payload["count"] == 3
    assert "skipped_not_available" not in payload


def test_partial_availability_splits_the_read_by_property_set():
    """An element that can take some of the requested properties but not all
    is read for the ones it can take, in its own request."""
    conn = make_conn()
    find_elements(conn, [{"element_types": ["Wall", "Zone"], "logical_operator": "or",
                          "comparisons": [prop("OFFICE/Fire Rating", "has_value"),
                                          prop("ModelView_LayerName", "equal", "A-ZONE")]}])
    reads = calls(conn, "API.GetPropertyValuesOfElements")
    requested = {(tuple(e["elementId"]["guid"] for e in r["elements"]),
                  len(r["properties"])) for r in reads}
    assert (("w-1",), 2) in requested          # classified wall: both
    assert (("w-2", "z-1"), 1) in requested    # the rest: layer only


# ---------- cheap comparisons narrow before the read ----------

def test_story_narrows_an_and_group_before_the_property_read():
    conn = make_conn()
    payload = find_elements(conn, [{"element_types": ["Wall"], "comparisons": [
        prop("story", "equal", 1), prop("ModelView_LayerName", "has_value")]}])
    assert payload["guids"] == ["w-2"]
    reads = calls(conn, "API.GetPropertyValuesOfElements")
    assert [e["elementId"]["guid"] for e in reads[0]["elements"]] == ["w-2"]


def test_story_ordering():
    payload = find_elements(make_conn(), [{"element_types": ["Wall", "Zone"], "comparisons": [
        prop("story", "greater_or_equal", 1)]}])
    assert payload["guids"] == ["w-2"]


def test_story_without_tapir_matches_nothing_and_says_why():
    payload = find_elements(make_conn(tapir=False), [{"element_types": ["Wall"], "comparisons": [
        prop("story", "equal", 0)]}])
    assert payload["count"] == 0
    assert any("Tapir" in n for n in payload["notes"])


def test_classification_branch_tests_use_the_tree():
    conn = make_conn()
    system = "classification:ARCHICAD Classification"
    in_building = find_elements(conn, [{"element_types": ["Wall", "Zone"], "comparisons": [
        prop(system, "is_in_branch_of", "Building")]}])
    assert in_building["guids"] == ["w-1"]
    direct = find_elements(conn, [{"element_types": ["Wall", "Zone"], "comparisons": [
        prop(system, "is_direct_child_of", "c-building")]}])
    assert direct["guids"] == ["w-1"]
    unclassified = find_elements(conn, [{"element_types": ["Wall", "Zone"], "comparisons": [
        prop(system, "has_no_value")]}])
    assert unclassified["guids"] == ["w-2"]
    equal = find_elements(conn, [{"element_types": ["Wall", "Zone"], "comparisons": [
        prop(system, "equal", "zone")]}])
    assert equal["guids"] == ["z-1"]
    assert calls(conn, "API.GetPropertyValuesOfElements") == []


def test_unknown_classification_system_is_reported():
    payload = find_elements(make_conn(), [{"element_types": ["Wall"], "comparisons": [
        prop("classification:Uniclass", "has_value")]}])
    assert payload["count"] == 0
    assert any("Uniclass" in n for n in payload["notes"])


# ---------- addresses ----------

def test_unresolved_property_is_reported_and_treated_as_not_available():
    conn = make_conn(**{"API.GetPropertyIds": lambda p: {"properties": [
        {"error": {"code": 1, "message": "not found"}} for _ in p["properties"]]}})
    payload = find_elements(conn, [{"element_types": ["Wall"], "comparisons": [
        prop("NoSuch/Property", "not_available")]}])
    assert payload["count"] == 2
    assert any("did not resolve" in n for n in payload["notes"])
    assert calls(conn, "API.GetPropertyValuesOfElements") == []


def test_a_property_guid_is_accepted_as_an_address():
    conn = make_conn()
    find_elements(conn, [{"element_types": ["Wall"], "comparisons": [
        prop("11111111-1111-1111-1111-111111111111", "has_value")]}])
    assert calls(conn, "API.GetPropertyIds") == []   # no lookup needed
    read = calls(conn, "API.GetPropertyValuesOfElements")[0]
    assert read["properties"] == [{"propertyId": {"guid": "11111111-1111-1111-1111-111111111111"}}]


# ---------- blast radius ----------

def test_the_element_ceiling_still_refuses_an_oversized_read(monkeypatch):
    monkeypatch.setattr(extract, "MAX_PROPERTY_FETCH_ELEMENTS", 0)
    with pytest.raises(extract.PropertyFetchTooWideError, match="find_elements"):
        find_elements(make_conn(), [{"element_types": ["Wall"], "comparisons": [
            prop("OFFICE/Fire Rating", "has_value")]}])


def test_malformed_groups_return_an_error_before_any_call():
    conn = make_conn()
    assert "error" in find_elements(conn, [])
    assert "error" in find_elements(conn, [{"comparisons": [prop("x", "equal")]}])
    assert conn._core.calls == []
