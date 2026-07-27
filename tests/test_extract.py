import pytest
from archicad_mcp import extract
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.extract import build_snapshot, fetch_property_values, resolve_property_ids
from tests.conftest import FakeCore
from tests.fixtures import api_replays


def make_conn(tapir=True):
    official = dict(api_replays.OFFICIAL)
    if not tapir:
        official["API.IsAddOnCommandAvailable"] = {"available": False}
    return ArchicadConnection(19723, core=FakeCore(
        official=official, tapir=api_replays.TAPIR if tapir else {}))


def test_resolve_property_ids_builtin_and_user():
    ids = resolve_property_ids(make_conn(), ["ModelView_LayerName", "OFFICE/Fire Rating"])
    assert ids["ModelView_LayerName"] == {"guid": "pid-ModelView_LayerName"}
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


def test_snapshot_story_from_floor_index():
    snap = build_snapshot(make_conn(),
                          needs=frozenset({"elements", "story"}))
    by_guid = {e.guid: e for e in snap.elements}
    assert by_guid["w-1"].story == 0
    assert by_guid["w-2"].story == 1
    # story must come from Tapir details, never a (non-existent) property.
    # BUILTIN_STORY was removed; General_HomeStoryNumber does not exist in AC.


def test_snapshot_story_omitted_without_story_need():
    conn = make_conn()
    build_snapshot(conn, needs=frozenset({"elements", "properties"}))
    assert not any(c == "GetDetailsOfElements" for c, _ in conn._core.calls)


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


def test_fetch_property_values_chunks_requests(monkeypatch):
    """Wide property queries crash Archicad's API bridge; guids are split into
    PROPERTY_FETCH_CHUNK-sized batches and the results merged."""
    monkeypatch.setattr(extract, "PROPERTY_FETCH_CHUNK", 2)
    conn = make_conn()
    result = fetch_property_values(conn, ["w-1", "w-2", "z-1"], ["ModelView_LayerName"])

    value_calls = [params for cmd, params in conn._core.calls
                   if cmd == "API.GetPropertyValuesOfElements"]
    # 3 elements at chunk size 2 -> two requests
    assert len(value_calls) == 2
    assert [e["elementId"]["guid"] for e in value_calls[0]["elements"]] == ["w-1", "w-2"]
    assert [e["elementId"]["guid"] for e in value_calls[1]["elements"]] == ["z-1"]
    # merged output spans both chunks
    assert result["w-1"]["ModelView_LayerName"] == "A-WALL"
    assert result["w-2"]["ModelView_LayerName"] == "Sketch"
    assert result["z-1"]["ModelView_LayerName"] == "A-ZONE"


def test_classification_and_ifc_fetches_are_chunked(monkeypatch):
    import archicad_mcp.extract as extract_mod
    monkeypatch.setattr(extract_mod, "PROPERTY_FETCH_CHUNK", 2)
    conn = make_conn()
    snap = build_snapshot(conn, needs=frozenset({"elements", "classifications", "ifc"}))
    classification_calls = [c for c, _ in conn._core.calls
                            if c == "API.GetClassificationsOfElements"]
    ifc_calls = [c for c, _ in conn._core.calls if c == "GetIFCPropertiesOfElements"]
    assert len(classification_calls) == 2  # 3 elements, chunk=2
    assert len(ifc_calls) == 2
    by_guid = {e.guid: e for e in snap.elements}
    assert by_guid["w-1"].classifications == {"ARCHICAD Classification": "c-wall"}
    assert snap.ifc_properties["w-1"] == {"Pset_WallCommon.FireRating": "EI60"}


def test_type_fetch_is_chunked(monkeypatch):
    """Enumeration now spans the whole plan (60k+ elements on a real project),
    so asking for every type in one request would be exactly the kind of wide
    official call that has crashed the API bridge."""
    monkeypatch.setattr(extract, "TYPE_FETCH_CHUNK", 2)
    conn = make_conn()
    types = extract._fetch_types(conn, ["w-1", "w-2", "z-1"])
    type_calls = [p for c, p in conn._core.calls if c == "API.GetTypesOfElements"]
    assert len(type_calls) == 2
    assert [e["elementId"]["guid"] for e in type_calls[1]["elements"]] == ["z-1"]
    assert types == {"w-1": "Wall", "w-2": "Wall", "z-1": "Zone"}


def test_property_fetch_refuses_when_too_wide(monkeypatch):
    import archicad_mcp.extract as extract_mod
    from archicad_mcp.extract import PropertyFetchTooWideError, fetch_property_values
    monkeypatch.setattr(extract_mod, "MAX_PROPERTY_FETCH_ELEMENTS", 2)
    conn = make_conn()
    with pytest.raises(PropertyFetchTooWideError, match="Refusing to read properties"):
        fetch_property_values(conn, ["a", "b", "c"], ["ModelView_LayerName"])
    # No API call must have been issued before the refusal.
    assert not any(c == "API.GetPropertyValuesOfElements" for c, _ in conn._core.calls)


def test_build_snapshot_scopes_property_fetch_to_element_types():
    conn = make_conn()
    snap = build_snapshot(conn, needs=frozenset({"elements", "properties"}),
                          element_types=frozenset({"Wall"}))
    # Only the two walls end up in the snapshot; the zone is excluded.
    assert {e.guid for e in snap.elements} == {"w-1", "w-2"}
    value_calls = [p for c, p in conn._core.calls if c == "API.GetPropertyValuesOfElements"]
    fetched = {e["elementId"]["guid"] for call in value_calls for e in call["elements"]}
    assert fetched == {"w-1", "w-2"}  # zone z-1 never fetched


def test_build_snapshot_zones_ignore_element_type_scope():
    conn = make_conn()
    snap = build_snapshot(conn, needs=frozenset({"zones"}),
                          element_types=frozenset({"Wall"}))
    # Zone discovery must not be narrowed by an element-property scope.
    assert [z.guid for z in snap.zones] == ["z-1"]


def test_ifc_skipped_when_tapir_too_old_for_ifc_commands():
    """Tapir 1.4.0 is installed but has no GetIFCPropertiesOfElements: the
    snapshot must degrade to ifc_properties=None, not raise a 4010."""
    from multiconn_archicad.errors import StandardAPIError

    official = dict(api_replays.OFFICIAL)

    def availability(params):
        cmd = params["addOnCommandId"]["commandName"]
        return {"available": cmd != "GetIFCPropertiesOfElements"}

    official["API.IsAddOnCommandAvailable"] = availability
    tapir = dict(api_replays.TAPIR)

    def unregistered(_params):
        raise StandardAPIError(
            message="Archicad does not have the registered Add-On command with "
                    "the name : TapirCommand.GetIFCPropertiesOfElements",
            code=4010)

    tapir["GetIFCPropertiesOfElements"] = unregistered
    conn = ArchicadConnection(19723, core=FakeCore(official=official, tapir=tapir))

    snap = build_snapshot(conn, needs=frozenset({"elements", "ifc"}))
    assert snap.ifc_properties is None  # skipped, not crashed
    assert not any(c == "GetIFCPropertiesOfElements" for c, _ in conn._core.calls)
