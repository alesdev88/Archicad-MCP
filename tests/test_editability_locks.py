"""The kinds of refusal editability tells apart, and the lock reasons modify_elements adds.

Layer answers are the ones recorded on MCP-Test (tests/fixtures/modify_elements).
"""
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.attributes import layer_table, tapir_attributes
from archicad_mcp.core.editability import (
    HIDDEN,
    LOCKED,
    NOT_FOUND,
    UNRESERVED,
    classify,
    lock_reasons,
    reason_text,
)
from tests.conftest import FakeCore
from tests.fixtures import api_replays
from tests.fixtures.modify_elements import layers_answer, load
from tests.test_mutate import Project


def test_classify_names_the_kind_of_each_refusal():
    project = Project(floor_plan=["A", "B", "C", "D"], hidden={"B"}, locked={"C"},
                      teamwork=True, mine={"A", "B", "C"})
    kinds, window = classify(project.connect(), ["A", "B", "C", "D", "NOPE"])
    assert kinds == {"B": HIDDEN, "C": LOCKED, "D": UNRESERVED, "NOPE": NOT_FOUND}
    assert window == "FloorPlan"


def test_classify_reads_no_window_when_nothing_is_refused():
    project = Project(floor_plan=["A"])
    assert classify(project.connect(), ["A"]) == ({}, None)


def test_reason_text_is_what_refusals_said_before():
    assert "hotlinked module" in reason_text(LOCKED, "FloorPlan")
    layout = reason_text(LOCKED, "Layout")
    assert layout.startswith("not editable from the active Layout window")
    assert "hidden layer" in reason_text(HIDDEN, None)


def _lock_conn(locked_layers=frozenset(), general_locked=frozenset(),
               property_resolves=True):
    official = dict(api_replays.OFFICIAL)

    def property_ids(params):
        return {"properties": [{"propertyId": {"guid": "pid-General_Locked"}}
                               if property_resolves else
                               {"error": {"code": 1, "message": "no such property"}}
                               for _ in params["properties"]]}

    def values(params):
        return {"propertyValuesForElements": [
            {"propertyValues": [{"propertyValue": {
                "type": "boolean", "status": "normal",
                "value": e["elementId"]["guid"] in general_locked}}]}
            for e in params["elements"]]}

    official["API.GetPropertyIds"] = property_ids
    official["API.GetPropertyValuesOfElements"] = values
    tapir = dict(api_replays.TAPIR)
    tapir["GetAttributesByType"] = load("layers_by_type.json")
    tapir["GetLayers"] = layers_answer(locked=set(locked_layers))
    core = FakeCore(official=official, tapir=tapir)
    return ArchicadConnection(19723, core=core), core


ENTRIES = {
    "H": {"type": "Door", "layerIndex": 33, "hotlinkId": {"guid": "HL-1"}},
    "L": {"type": "Slab", "layerIndex": 56},
    "E": {"type": "Object", "layerIndex": 33},
    "OK": {"type": "Mesh", "layerIndex": 33},
}


def test_lock_reasons_hotlink_then_layer_then_element_lock():
    conn, _ = _lock_conn(locked_layers={56}, general_locked={"E", "H"})
    out = lock_reasons(conn, ENTRIES, layer_table(conn))
    assert "hotlinked module" in out["H"]
    assert "'- Hidden'" in out["L"] and "locked layer" in out["L"]
    assert "Edit > Locking" in out["E"]
    assert "OK" not in out


def test_lock_reasons_reads_general_locked_once():
    conn, core = _lock_conn()
    lock_reasons(conn, ENTRIES, layer_table(conn))
    reads = [p for name, p in core.calls if name == "API.GetPropertyValuesOfElements"]
    assert len(reads) == 1
    assert len(reads[0]["elements"]) == 4 and len(reads[0]["properties"]) == 1


def test_lock_reasons_skips_the_element_lock_when_the_property_does_not_resolve():
    conn, _ = _lock_conn(general_locked={"E"}, property_resolves=False)
    out = lock_reasons(conn, ENTRIES, layer_table(conn))
    assert set(out) == {"H"}       # the hotlink still shows; the element lock is left out


def test_layer_table_from_recorded_answers():
    conn, _ = _lock_conn()
    table = layer_table(conn)
    assert table[33]["name"] == "Structural - Bearing"
    assert table[3]["name"] == "Interior - Furniture "      # trailing space kept
    assert table[56]["isHidden"] is True and table[56]["isLocked"] is False
    assert table[33]["guid"] == "443DF8BE-A000-4B57-B783-02432B9BA268"


def test_tapir_attributes_lists_index_name_and_guid():
    conn, _ = _lock_conn()
    layers = tapir_attributes(conn, "Layer")
    assert layers[1] == {"index": 2, "name": "Interior - Partition",
                         "guid": "3C9CC72D-E9D8-4228-BA92-E83DDF8873A0"}
