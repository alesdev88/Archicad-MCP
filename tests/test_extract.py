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


def test_parse_ifc_response_matches_by_position_and_keeps_errors():
    # Tapir answers GUIDs uppercase and an error entry for a missing element;
    # matching by position keeps a lowercase request lined up.
    response = {"elementIFCProperties": [
        {"elementId": {"guid": "W-1"},
         "ifcProperties": [{"propertySetName": "Pset_WallCommon", "name": "FireRating",
                            "value": "EI60"}]},
        {"error": {"code": 1, "message": "The given element was not found"}},
    ]}
    assert extract.parse_ifc_response(["w-1", "gone"], response) == {
        "w-1": {"Pset_WallCommon.FireRating": "EI60"},
        "gone": "The given element was not found"}


def _refusal_conn(rows, resolvable=("General_Locked", "X/Layer", "Other")):
    """A connection whose official read answers `rows` (one per requested element)."""
    def property_ids(params):
        out = []
        for item in params["properties"]:
            name = item.get("nonLocalizedName") or "/".join(item["localizedName"])
            out.append({"propertyId": {"guid": f"pid-{name}"}} if name in resolvable
                       else {"error": {"code": 6702, "message": "no such property"}})
        return {"properties": out}
    return ArchicadConnection(19723, core=FakeCore(official={
        "API.GetPropertyIds": property_ids,
        "API.GetPropertyValuesOfElements": {"propertyValuesForElements": rows},
    }))


_REFUSED_ELEMENT = {"error": {"code": 7203, "message": "Element not supported"}}
_REFUSED_DEFINITION = {"error": {"code": 6702, "message": "Property definition not supported"}}
_ANSWERED = {"propertyValue": {"type": "boolean", "status": "normal", "value": False}}


def test_a_refused_element_is_not_read_with_its_reason():
    conn = _refusal_conn([_REFUSED_ELEMENT])
    cells = extract.fetch_property_cells(conn, ["label-1"], ["General_Locked", "X/Layer"])
    refused = {"status": "notRead", "reason": extract.NOT_READ_ELEMENT,
               "cause": extract.NOT_READ_ELEMENT_CAUSE}
    assert cells["label-1"] == {"General_Locked": refused, "X/Layer": refused}


def test_a_refused_element_keeps_an_unresolved_name_empty():
    conn = _refusal_conn([_REFUSED_ELEMENT], resolvable=("General_Locked",))
    cells = extract.fetch_property_cells(conn, ["label-1"], ["General_Locked", "Nope"])
    assert cells["label-1"] == {
        "General_Locked": {"status": "notRead", "reason": extract.NOT_READ_ELEMENT,
                           "cause": extract.NOT_READ_ELEMENT_CAUSE},
        "Nope": {}}


def test_a_refused_definition_is_not_read_with_its_reason():
    conn = _refusal_conn([{"propertyValues": [_ANSWERED, _REFUSED_DEFINITION]}])
    cells = extract.fetch_property_cells(conn, ["w-1"], ["General_Locked", "X/Layer"])
    assert cells["w-1"]["X/Layer"] == {
        "status": "notRead", "reason": extract.NOT_READ_DEFINITION,
        "cause": extract.NOT_READ_DEFINITION_CAUSE}
    assert cells["w-1"]["General_Locked"] == {
        "type": "boolean", "status": "normal", "value": False}


def test_another_error_is_still_an_empty_cell():
    other = {"error": {"code": 4002, "message": "Something else"}}
    conn = _refusal_conn([{"propertyValues": [_ANSWERED, other]}])
    cells = extract.fetch_property_cells(conn, ["w-1"], ["General_Locked", "Other"])
    assert cells["w-1"]["Other"] == {}


def test_another_row_error_is_still_empty_cells():
    conn = _refusal_conn([{"error": {"code": 7204, "message": "Element not found"}}])
    cells = extract.fetch_property_cells(conn, ["gone"], ["General_Locked"])
    assert cells["gone"] == {"General_Locked": {}}


def test_error_codes_and_reasons_are_the_agreed_ones():
    assert extract.OFFICIAL_UNSUPPORTED_DEFINITION == 6702
    assert extract.OFFICIAL_UNSUPPORTED_ELEMENT == 7203
    assert extract.NOT_READ_DEFINITION_CAUSE == (
        "the official API does not support this property definition")
    assert extract.NOT_READ_ELEMENT_CAUSE == (
        "the official API reads no property of this element (2D and annotation "
        "elements, and sub-elements such as stair treads and curtain wall panels, "
        "are outside it)")
    assert extract.NOT_READ_DEFINITION == (
        extract.NOT_READ_DEFINITION_CAUSE + "; Tapir 2.0.0 or newer reads it")
    assert extract.NOT_READ_ELEMENT == (
        extract.NOT_READ_ELEMENT_CAUSE + "; Tapir 2.0.0 or newer reads them")
    assert extract.NOT_READ_TAPIR_SILENT == "; Tapir did not answer it either"
    assert extract.NOT_READ_WRITE == (
        "; property writes go through the official API, so it cannot be written")


def test_values_of_cells_not_read_are_none():
    conn = _refusal_conn([_REFUSED_ELEMENT])
    assert fetch_property_values(conn, ["label-1"], ["General_Locked"]) == {
        "label-1": {"General_Locked": None}}


# ---------- the snapshot reads through Tapir and records what stays unread ----------

_LOCKED_AND_LAYER = {"properties": [
    {"propertyId": {"guid": "PID-GENERAL_LOCKED"}, "propertyValueType": "Boolean",
     "propertyCollectionType": "Single", "propertyMeasureType": "Default"},
    {"propertyId": {"guid": "PID-MODELVIEW_LAYERNAME"}, "propertyValueType": "String",
     "propertyCollectionType": "Single", "propertyMeasureType": "Default"},
]}
_TAPIR_LOCKED_AND_LAYER = {"pid-General_Locked": "True", "pid-ModelView_LayerName": "A-ANNO"}
_ANSWERED_CELLS = {"propertyValues": [
    {"propertyValue": {"type": "boolean", "status": "normal", "value": False}},
    {"propertyValue": {"type": "string", "status": "normal", "value": "A-WALL"}}]}


def _label_conn(version="2.0.0"):
    """A project with a wall the official read answers and a label it refuses."""
    from tests.test_property_fallback import _REFUSED_ELEMENT, _fake

    def tapir_values(params):
        row = {"propertyValues": [
            {"propertyValue": {"value": _TAPIR_LOCKED_AND_LAYER[p["propertyId"]["guid"]]},
             "isAvailable": True} for p in params["properties"]]}
        return {"propertyValuesForElements": [row for _ in params["elements"]]}

    core = _fake(official_rows={"label-1": _REFUSED_ELEMENT, "w-1": _ANSWERED_CELLS},
                 version=version, values=tapir_values, definitions=_LOCKED_AND_LAYER)
    core.official_responses["API.GetTypesOfElements"] = lambda p: {"typesOfElements": [
        {"typeOfElement": {"elementId": e["elementId"], "elementType": "Wall"}}
        if e["elementId"]["guid"] == "w-1" else
        {"error": {"code": 7203, "message": "Element not supported"}}
        for e in p["elements"]]}
    core.official_responses["API.GetAllElements"] = {
        "elements": [{"elementId": {"guid": "w-1"}}, {"elementId": {"guid": "label-1"}}]}
    core.tapir_responses["GetAllElements"] = core.official_responses["API.GetAllElements"]
    core.tapir_responses["GetDetailsOfElements"] = lambda p: {"detailsOfElements": [
        {"type": "Label"} for _ in p["elements"]]}
    core.official_responses["API.GetAttributesByType"] = {"attributeIds": []}
    return ArchicadConnection(19723, core=core)


_LABEL_NEEDS = frozenset({"elements", "properties", "layers"})


def test_the_snapshot_reads_gaps_through_tapir_and_records_what_stays_unread():
    snap = build_snapshot(_label_conn(), needs=_LABEL_NEEDS,
                          property_names=frozenset({"General_Locked"}))
    by_guid = {e.guid: e for e in snap.elements}
    label = by_guid["label-1"]
    assert label.element_type == "Label"
    assert label.properties["General_Locked"] is True and label.not_read == {}
    assert label.layer == "A-ANNO"
    assert "ModelView_LayerName" not in label.properties
    assert by_guid["w-1"].layer == "A-WALL" and by_guid["w-1"].not_read == {}


def test_without_tapir_2_the_snapshot_records_the_reasons():
    snap = build_snapshot(_label_conn(version="1.8.0"), needs=_LABEL_NEEDS,
                          property_names=frozenset({"General_Locked"}))
    label = {e.guid: e for e in snap.elements}["label-1"]
    assert label.not_read == {"General_Locked": extract.NOT_READ_ELEMENT,
                              "layer": extract.NOT_READ_ELEMENT}
    assert label.layer is None
    assert label.properties["General_Locked"] is None
