from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.extract import build_snapshot, resolve_property_ids
from tests.conftest import FakeCore
from tests.fixtures import api_replays


def make_conn(tapir=True):
    official = dict(api_replays.OFFICIAL)
    if not tapir:
        official["API.IsAddOnCommandAvailable"] = {"available": False}
    return ArchicadConnection(19723, core=FakeCore(
        official=official, tapir=api_replays.TAPIR if tapir else {}))


def test_resolve_property_ids_builtin_and_user():
    ids = resolve_property_ids(make_conn(), ["General_LayerName", "OFFICE/Fire Rating"])
    assert ids["General_LayerName"] == {"guid": "pid-General_LayerName"}
    assert ids["OFFICE/Fire Rating"] == {"guid": "pid-OFFICE/Fire Rating"}


def test_snapshot_elements_types_layers_properties():
    snap = build_snapshot(make_conn(),
                          needs=frozenset({"elements", "properties", "layers"}),
                          property_names=frozenset({"OFFICE/Fire Rating"}))
    by_guid = {e.guid: e for e in snap.elements}
    assert by_guid["w-1"].element_type == "Wall"
    assert by_guid["w-1"].layer == "A-WALL"
    assert by_guid["w-2"].layer == "Sketch"
    assert by_guid["w-1"].properties["OFFICE/Fire Rating"] == "EI60"
    assert by_guid["w-2"].properties["OFFICE/Fire Rating"] is None  # not available -> None
    assert set(snap.layers) == {"A-WALL", "A-ZONE"}


def test_snapshot_classifications():
    snap = build_snapshot(make_conn(), needs=frozenset({"elements", "classifications"}))
    by_guid = {e.guid: e for e in snap.elements}
    assert by_guid["w-1"].classifications == {"ARCHICAD Classification": "c-wall"}
    assert by_guid["w-2"].classifications == {"ARCHICAD Classification": None}


def test_snapshot_zones():
    snap = build_snapshot(make_conn(), needs=frozenset({"zones"}))
    assert len(snap.zones) == 1
    zone = snap.zones[0]
    assert (zone.guid, zone.number, zone.name) == ("z-1", "101", "Office")


def test_snapshot_ifc_with_tapir():
    snap = build_snapshot(make_conn(), needs=frozenset({"elements", "ifc"}))
    assert snap.ifc_properties == {
        "w-1": {"Pset_WallCommon.FireRating": "EI60"}, "w-2": {}, "z-1": {}}


def test_snapshot_ifc_without_tapir_is_none():
    snap = build_snapshot(make_conn(tapir=False), needs=frozenset({"elements", "ifc"}))
    assert snap.ifc_properties is None


def test_minimal_needs_makes_no_extra_calls():
    conn = make_conn()
    build_snapshot(conn, needs=frozenset({"elements"}))
    called = {c for c, _ in conn._core.calls}
    assert "API.GetPropertyValuesOfElements" not in called
    assert "API.GetClassificationsOfElements" not in called
