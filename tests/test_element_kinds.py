"""Reading and sending the kinds of element data beyond property values."""
from multiconn_archicad.errors import StandardAPIError

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core import element_kinds as ek
from tests.conftest import FakeCore

ZERO = "00000000-0000-0000-0000-000000000000"


def _conn(official=None, tapir=None, available=lambda cmd: True):
    official = {"API.IsAddOnCommandAvailable":
                lambda p: {"available": available(p["addOnCommandId"]["commandName"])},
                "API.GetProductInfo": {"version": 29},
                **(official or {})}
    tapir = {"GetAddOnVersion": {"version": "1.7.1"}, **(tapir or {})}
    core = FakeCore(official=official, tapir=tapir)
    return ArchicadConnection(19723, core=core), core


def _gdl_answer(params):
    out = []
    for el in params["elements"]:
        guid = el["elementId"]["guid"]
        if guid.startswith("wall"):
            out.append({"error": {"code": -2130313113,
                                  "message": "Failed to get parameters of element"}})
        else:
            out.append({"parameters": [
                {"name": "A", "displayName": "Width", "index": 1, "type": "Length",
                 "value": 0.9, "flags": []}]})
    return {"gdlParametersOfElements": out}


def test_read_parameters_matches_by_position_and_keeps_errors():
    conn, _ = _conn(tapir={"GetGDLParametersOfElements": _gdl_answer})
    out = ek.read_parameters(conn, ["obj-1", "wall-1"], "gdl")
    assert out["obj-1"]["params"]["A"]["value"] == 0.9
    assert out["wall-1"] == {"error": "Failed to get parameters of element"}


def test_read_parameters_reads_50_elements_per_request():
    conn, core = _conn(tapir={"GetGDLParametersOfElements": _gdl_answer})
    out = ek.read_parameters(conn, [f"obj-{i}" for i in range(120)], "gdl")
    assert len(out) == 120
    sizes = [len(p["elements"]) for c, p in core.calls if c == "GetGDLParametersOfElements"]
    assert sizes == [50, 50, 20]


def test_read_parameters_reads_markers_with_their_own_command():
    conn, core = _conn(tapir={"GetMarkerParametersOfElements": {
        "markerParametersOfElements": [{"libPart": {"name": "Door Marker"}, "parameters": [
            {"name": "iNumber", "displayName": "Number", "index": 3, "type": "String",
             "value": "001", "flags": []}]}]}})
    out = ek.read_parameters(conn, ["door-1"], "marker")
    assert out["door-1"]["params"]["iNumber"]["value"] == "001"


def test_find_parameter_prefers_the_exact_name():
    params = {"A": {"name": "A"}, "a": {"name": "a"}, "gs_Leaf": {"name": "gs_Leaf"}}
    assert ek.find_parameter(params, "a")["name"] == "a"
    assert ek.find_parameter(params, "GS_LEAF")["name"] == "gs_Leaf"
    assert ek.find_parameter({"Ab": {}, "AB": {}}, "ab") is None
    assert ek.find_parameter(params, "B") is None


_SYSTEMS = {"SYS-1": "ARCHICAD Classification", "SYS-2": "Uniclass"}


def test_read_classifications_parses_the_official_nested_shape():
    conn, _ = _conn(official={"API.GetClassificationsOfElements": {"elementClassifications": [
        {"classificationIds": [
            {"classificationId": {"classificationSystemId": {"guid": "SYS-1"},
                                  "classificationItemId": {"guid": "ITEM-1"}}},
            {"classificationId": {"classificationSystemId": {"guid": "SYS-2"}}}]}]}},
        available=lambda cmd: cmd != "GetClassificationsOfElements")
    out = ek.read_classifications(conn, ["w-1"], _SYSTEMS)
    assert out == {"w-1": {"ARCHICAD Classification": "ITEM-1", "Uniclass": None}}


def test_read_classifications_prefers_tapir_and_parses_its_flat_shape():
    # Tapir 1.7.1 source: system and item directly in the list entry; an
    # unclassified element may answer an all-zero item GUID or an error entry.
    conn, core = _conn(tapir={"GetClassificationsOfElements": {"elementClassifications": [
        {"classificationIds": [
            {"classificationSystemId": {"guid": "sys-1"},
             "classificationItemId": {"guid": "ITEM-1"}},
            {"classificationSystemId": {"guid": "SYS-2"},
             "classificationItemId": {"guid": ZERO}}]},
        {"classificationIds": [
            {"error": {"code": 1, "message": "Failed to get classification item"}},
            {"error": {"code": 1, "message": "Failed to get classification item"}}]}]}})
    out = ek.read_classifications(conn, ["w-1", "w-2"], _SYSTEMS)
    assert out == {"w-1": {"ARCHICAD Classification": "ITEM-1", "Uniclass": None},
                   "w-2": {"ARCHICAD Classification": None, "Uniclass": None}}
    assert not any(c == "API.GetClassificationsOfElements" for c, _ in core.calls)


def test_read_ifc_keeps_errors_per_element():
    conn, _ = _conn(tapir={"GetIFCPropertiesOfElements": {"elementIFCProperties": [
        {"elementId": {"guid": "D-1"}, "ifcProperties": [
            {"propertySetName": "Pset_DoorCommon", "name": "FireRating", "value": "EI30"}]},
        {"error": {"code": 1, "message": "The given element was not found"}}]}})
    assert ek.read_ifc(conn, ["d-1", "gone"]) == {
        "d-1": {"Pset_DoorCommon.FireRating": "EI30"},
        "gone": "The given element was not found"}


def test_send_items_reports_each_outcome_and_where_it_stopped():
    batches = []

    def answer(params):
        batches.append(len(params["items"]))
        if len(batches) == 2:
            raise StandardAPIError(message="Invalid program status", code=4001)
        return {"executionResults": [
            {"success": False, "error": {"code": 6001, "message": "denied"}}
            if item == "bad" else {"success": True} for item in params["items"]]}

    conn, _ = _conn(tapir={"SetThings": answer})
    items = ["ok", "bad", "x", "y", "z", "w"]
    sent = ek.send_items(conn.tapir, "SetThings", "items", items, 2)
    assert sent.outcomes == ["ok", {"code": 6001, "message": "denied"},
                             "unanswered", "unanswered", "not_sent", "not_sent"]
    assert isinstance(sent.stopped, StandardAPIError) and sent.stopped.code == 4001
    assert batches == [2, 2]


def test_send_items_counts_a_missing_result_as_success():
    conn, _ = _conn(tapir={"SetThings": {"executionResults": []}})
    assert ek.send_items(conn.tapir, "SetThings", "items", ["a"], 10).outcomes == ["ok"]


def test_tapir_requirement_names_the_version_the_command_arrived_in():
    conn, _ = _conn(available=lambda cmd: cmd != "SetMarkerParametersOfElements")
    assert ek.tapir_requirement(conn, "SetGDLParametersOfElements") is None
    reason = ek.tapir_requirement(conn, "SetMarkerParametersOfElements")
    assert "1.7.1" in reason and "SetMarkerParametersOfElements" in reason


def test_tapir_version_reads_the_add_on_version():
    conn, _ = _conn(tapir={"GetAddOnVersion": {"version": "1.5.10"}})
    assert ek.tapir_version(conn) == (1, 5, 10)
