"""create_road_marking over a faked Archicad."""
import json
import math

import pytest

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core import road_marking as rm
from tests.conftest import FakeCore

LIB = {"libraryParts": [{"documentName": "Road Marking", "fileName": "Road Marking.gsm", "typeId": "Object"}]}
OK = {"executionResults": [{"success": True}]}
PTS = [{"x": 0, "y": 0, "z": 0}, {"x": 10, "y": 0, "z": 0}]
SLAB = {"type": "Slab", "floorIndex": 1, "details": {
    "zCoordinate": 3.25, "offsetFromTop": 0.0, "holes": [],
    "polygonOutline": [{"x": -5, "y": -10}, {"x": 20, "y": -10}, {"x": 20, "y": 10}, {"x": -5, "y": 10},
                       {"x": -5, "y": -10}]}}
MESH = {"type": "Mesh", "floorIndex": 1, "details": {"level": 0.0, "polygonCoordinates": [
    {"x": 0, "y": 0, "z": 0.0}, {"x": 20, "y": 0, "z": 0.0}, {"x": 20, "y": 20, "z": 2.0}, {"x": 0, "y": 20, "z": 2.0}]}}
STORIES = {"stories": [{"index": 0, "level": 0.0}, {"index": 1, "level": 3.0}]}


def _conn(tapir):
    return ArchicadConnection(19725, core=FakeCore(official={"API.IsAddOnCommandAvailable": {"available": True}},
                                                   tapir=tapir))


def _gdl(**over):
    """A placed marking's parameters as GetGDLParametersOfElements returns them (angles in radians)."""
    v = dict(rm.TABLE["defaults"])
    v.update(nodeX=[0.0, 10.0], nodeY=[0.0, 0.0], nodeZ=[0.0, 0.0], nodeFall=[0.0, 0.0], segArc=[0.0, 0.0])
    v.update(over)
    for k in (1, 2, 3):
        v[f"s{k}Angle"] = math.radians(v[f"s{k}Angle"])
    return {"gdlParametersOfElements": [{"parameters": [{"name": k, "value": x} for k, x in v.items()]}]}


def _sequence(*answers):
    answers = iter(answers)
    return lambda params: next(answers)


def _marking(origin=(0.0, 0.0, 0.0), angle=0.0, floor=1, name="Road Marking"):
    return {"type": "Object", "floorIndex": floor, "details": {
        "libPart": {"name": name}, "origin": dict(zip("xyz", origin)), "angle": angle}}


def _details(by_guid):
    def answer(params):
        return {"detailsOfElements": [by_guid.get(e["elementId"]["guid"], {"error": {"code": 1, "message": "not found"}})
                                      for e in params["elements"]]}
    return answer


def _params(result):
    return {p["name"]: p["value"] for p in result["gdl_parameters"]}


def _writes(conn):
    return [p["elementsWithGDLParameters"][0]["gdlParameters"]
            for c, p in conn._core.calls if c == "SetGDLParametersOfElements"]


def test_a_dry_run_reports_and_lists_the_parameters_in_write_order():
    conn = _conn({"GetAvailableLibraryParts": LIB})
    r = rm.create_road_marking(conn, points=PTS, marking="5333-2 bus stop on the carriageway",
                               lettering={"offset": 1.75})
    names = [p["name"] for p in r["gdl_parameters"]]
    assert names[:3] == ["A", "aDone", "nodeX"]
    assert names.index("nodeFall") < names.index("markingType") < names.index("letterOffset")
    params = _params(r)
    assert params["markingType"] == "5333-2 Bus stop on the carriageway" and params["letterOffset"] == 1.75
    assert "letterText" not in params and "bayLayout" not in params      # the preset writes those
    assert (params["A"], params["aDone"]) == (1.0, 1.0)
    assert r["dry_run"] is True and r["reach"] == {"u_lo": -0.15, "u_hi": 3.05} and r["warnings"] == []
    assert r["origin"] == {"x": 0.0, "y": 0.0, "z": 0.0} and r["story"] is None


def test_the_default_preset_is_not_written_again():
    r = rm.create_road_marking(_conn({"GetAvailableLibraryParts": LIB}), points=PTS)
    assert "markingType" not in _params(r) and r["marking"] == "5121 Broken divider, in settlements"


def test_a_flat_source_takes_start_height_and_slope():
    poly = {"type": "PolyLine", "floorIndex": 2, "details": {
        "coordinates": [{"x": 0, "y": 0}, {"x": 10, "y": 0}], "zCoordinate": 6.0}}
    conn = _conn({"GetDetailsOfElements": _details({"p-1": poly}), "GetAvailableLibraryParts": LIB})
    r = rm.create_road_marking(conn, source_guid="p-1", start_height=0.1, slope_percent=2)
    assert _params(r)["nodeZ"] == [0.0, 0.2] and r["origin"]["z"] == pytest.approx(6.1) and r["story"] == 2


def test_a_draped_write_places_on_the_surface_and_reads_the_name_back():
    conn = _conn({"GetDetailsOfElements": _details({"slab": SLAB}), "GetAvailableLibraryParts": LIB,
                  "CreateObjects": {"elements": [{"elementId": {"guid": "new"}}]},
                  "SetGDLParametersOfElements": OK,
                  "GetGDLParametersOfElements": _gdl(markingType="Parking 90 degrees")})
    r = rm.create_road_marking(conn, points=PTS, marking="Parking 90 degrees", drape_on=["slab"], lift=0.002,
                               dry_run=False)
    assert r["created"] is True and r["element"] == "new" and r["marking_after"] == "Parking 90 degrees"
    placed = [p for c, p in conn._core.calls if c == "CreateObjects"][0]["objectsData"][0]
    assert placed["coordinates"]["z"] == pytest.approx(3.252) and "floorIndex" not in placed
    written = {p["name"]: p["value"] for p in _writes(conn)[-1]}
    assert written["nodeZ"] == [0.0, 0.0] and written["nodeFall"] == [0.0, 0.0]
    assert r["off_surface"] == [] and r["partly_off_surface"] == []


def test_an_update_drapes_the_markings_own_path_turned_as_placed():
    conn = _conn({"GetDetailsOfElements": _details({"m-1": _marking((5.0, 5.0, 3.0), math.pi / 2), "mesh": MESH}),
                  "GetStories": STORIES, "GetAvailableLibraryParts": LIB, "SetGDLParametersOfElements": OK,
                  "GetGDLParametersOfElements": _sequence(_gdl(), _gdl(markingType="Custom", s1Width=0.12))})
    r = rm.create_road_marking(conn, update_guid="m-1", drape_on='["mesh"]', stripes='{"1": {"width": 0.12}}',
                               dry_run=False)
    assert r["updated"] is True and r["marking_after"] == "Custom"
    first, main = _writes(conn)
    assert first == [{"name": "nodeX", "value": [0.0, 1.0]}]      # same node count: nodeX as 0, 1 first
    names = [p["name"] for p in main]
    assert names.index("nodeY") < names.index("nodeX")
    main = {p["name"]: p["value"] for p in main}
    # the path runs from (5, 5) to (5, 15) on the plane z = 3 + 0.1 y, turned back into the part's frame
    assert main["nodeX"] == pytest.approx([0.0, 10.0]) and main["nodeY"] == pytest.approx([0.0, 0.0])
    assert main["nodeZ"] == pytest.approx([0.5, 1.5]) and main["nodeFall"] == pytest.approx([0.0, 0.0])
    assert main["s1Width"] == 0.12 and "markingType" not in main


def test_a_new_preset_on_an_update_replaces_what_it_sets():
    conn = _conn({"GetDetailsOfElements": _details({"m-1": _marking()}), "GetAvailableLibraryParts": LIB})
    conn._core.tapir_responses["GetGDLParametersOfElements"] = _gdl(markingType="Custom", s1Width=0.12)
    params = _params(rm.create_road_marking(conn, update_guid="m-1", points=PTS,
                                            marking="5121 Broken divider, in settlements"))
    assert params["markingType"] == "5121 Broken divider, in settlements" and "s1Width" not in params


def test_a_source_on_another_story_lands_on_the_surface_and_keeps_its_story():
    poly = {"type": "PolyLine", "floorIndex": 0, "details": {
        "coordinates": [{"x": 0, "y": 0}, {"x": 10, "y": 0}], "zCoordinate": 0.0}}
    conn = _conn({"GetDetailsOfElements": _details({"p-1": poly, "slab": SLAB}), "GetAvailableLibraryParts": LIB})
    r = rm.create_road_marking(conn, source_guid="p-1", drape_on=["slab"])
    assert r["origin"]["z"] == pytest.approx(3.25) and r["story"] == 0
    assert _params(r)["nodeZ"] == [0.0, 0.0]


def test_warnings_for_replaced_heights_ignored_slope_overhang_and_no_effect():
    conn = _conn({"GetDetailsOfElements": _details({"slab": SLAB}), "GetAvailableLibraryParts": LIB})
    r = rm.create_road_marking(conn, points=[{"x": 0, "y": 0, "z": 0}, {"x": 3, "y": 0, "z": 0}],
                               marking="5352-1 Disabled bays, shared zone", lettering={"offset": 2.0},
                               drape_on=["slab"], start_height=1.0)
    text = " ".join(r["warnings"])
    assert "replaces the source's own heights" in text and "start_height and slope_percent do not apply" in text
    assert "bay row is 6.30 m long" in text and "word is None" in text


def test_without_the_part_a_dry_run_warns_and_a_write_is_refused():
    conn = _conn({"GetAvailableLibraryParts": {"libraryParts": []}})
    assert any("not loaded" in w for w in rm.create_road_marking(conn, points=PTS)["warnings"])
    assert "not loaded" in rm.create_road_marking(conn, points=PTS, dry_run=False)["error"]


def test_the_call_is_refused_without_exactly_one_source_or_with_bad_input():
    conn = _conn({"GetAvailableLibraryParts": LIB})
    assert "exactly one" in rm.create_road_marking(conn)["error"]
    assert "exactly one" in rm.create_road_marking(conn, source_guid="a", points=PTS)["error"]
    assert "exactly one" in rm.create_road_marking(conn, update_guid="m-1")["error"]
    assert "JSON" in rm.create_road_marking(conn, points=PTS, stripes="{width: 1}")["error"]
    assert "Unknown marking" in rm.create_road_marking(conn, points=PTS, marking="5999")["error"]
    assert "drape_tolerance" in rm.create_road_marking(conn, points=PTS, drape_tolerance=0)["error"]
    beam = _conn({"GetDetailsOfElements": _details({"b-1": _marking(name="Swept Beam")})})
    assert "not a Road Marking" in rm.create_road_marking(beam, update_guid="b-1", points=PTS)["error"]


def test_the_paint_surface_is_found_by_name_or_taken_as_an_index():
    attrs = {"attributes": [{"index": 78, "name": "Paint - Cadmium Yellow"}]}
    conn = _conn({"GetAvailableLibraryParts": LIB, "GetAttributesByType": attrs})
    assert _params(rm.create_road_marking(conn, points=PTS, paint_surface="paint - cadmium yellow"))["paintSurface"] == 78
    assert _params(rm.create_road_marking(conn, points=PTS, paint_surface=76))["paintSurface"] == 76
    assert _params(rm.create_road_marking(conn, points=PTS, paint_surface="32"))["paintSurface"] == 32


def test_path_errors_name_the_road_marking():
    morph = {"type": "Morph", "floorIndex": 0, "details": {"origin": {"x": 0, "y": 0, "z": 0}, "body": {
        "vertices": [{"x": 0, "y": 0, "z": 0}, {"x": 1, "y": 0, "z": 0}, {"x": 2, "y": 0, "z": 0},
                     {"x": 1, "y": 1, "z": 0}],
        "polygons": [], "wireEdges": [{"vertexIds": [0, 1]}, {"vertexIds": [1, 2]}, {"vertexIds": [1, 3]}]}}}
    r = rm.create_road_marking(_conn({"GetDetailsOfElements": _details({"m": morph})}), source_guid="m")
    assert "A Road Marking follows one unbranched line" in r["error"]


async def _call_tool(monkeypatch, tapir, args):
    import archicad_mcp.server as server_mod
    from fastmcp import Client

    core = FakeCore(official={"API.IsAddOnCommandAvailable": {"available": True}}, tapir=tapir)
    monkeypatch.setattr(server_mod, "get_connection", lambda port: ArchicadConnection(19725, core=core))
    async with Client(server_mod.build_server(mode="full")) as client:
        result = await client.call_tool("create_road_marking", args)
        return json.loads(result.content[0].text)


async def test_the_registered_tool_takes_its_lists_and_objects_as_json_text(monkeypatch):
    tapir = {"GetDetailsOfElements": _details({"slab": SLAB}), "GetAvailableLibraryParts": LIB}
    payload = await _call_tool(monkeypatch, tapir, {
        "points": json.dumps(PTS), "marking": "5351 Taxi bay", "bays": json.dumps({"depth": 4.5}),
        "lettering": json.dumps({}), "stripes": json.dumps({"1": {"on": True, "width": 0.12}}),
        "drape_on": json.dumps(["slab"])})
    assert payload["dry_run"] is True and payload["reach"] == {"u_lo": -0.06, "u_hi": 4.55}
    assert _params(payload)["bayDepth"] == 4.5


async def test_the_registered_tool_explains_text_that_is_not_json(monkeypatch):
    payload = await _call_tool(monkeypatch, {"GetAvailableLibraryParts": LIB}, {"points": "0,0 3,0"})
    assert "points" in payload["error"] and "JSON" in payload["error"]


# ---------------------------------------------------------------- Tapir 1.7.2 item writes

V172 = {"version": "1.7.2"}


def test_item_writes_reset_and_grow_the_marking_in_one_write():
    conn = _conn({"GetDetailsOfElements": _details({"m-1": _marking((5.0, 5.0, 3.0), math.pi / 2), "mesh": MESH}),
                  "GetStories": STORIES, "GetAvailableLibraryParts": LIB, "SetGDLParametersOfElements": OK,
                  "GetAddOnVersion": V172,
                  "GetGDLParametersOfElements": _sequence(_gdl(extEnd=4.0), _gdl(markingType="Custom"))})
    r = rm.create_road_marking(conn, update_guid="m-1", drape_on=["mesh"], stripes={"1": {"width": 0.12}},
                               dry_run=False)
    assert r["updated"] is True and r["array_writes"] == "item by item"
    (main,) = _writes(conn)                       # no nodeX 0, 1, 2 write first
    names = [p["name"] for p in main]
    assert main[:2] == [{"name": "A", "value": 1.0}, {"name": "aDone", "value": 2.0}]
    assert r["nodes"] == 2 and "extEnd" not in names and "extEndDone" not in names
    lists = [n for n in names if n.startswith("node")]
    assert lists[:3] == ["nodeY", "nodeX", "nodeY"]


def test_item_writes_send_arcs_in_degrees():
    arc = {"type": "Arc", "floorIndex": 0, "details": {"origin": {"x": 0, "y": 0}, "radius": 10.0,
                                                       "begAngle": 0.0, "endAngle": math.pi / 3,
                                                       "zCoordinate": 0.0}}
    conn = _conn({"GetDetailsOfElements": _details({"a-1": arc}), "GetAvailableLibraryParts": LIB,
                  "GetAddOnVersion": V172})
    r = rm.create_road_marking(conn, source_guid="a-1")
    assert _params(r)["segArc"][0] == pytest.approx(60.0)
    assert "A" not in _params(r) and "extStart" not in _params(r)


def test_a_new_marking_is_read_after_placing_for_its_grip_state():
    conn = _conn({"GetAvailableLibraryParts": LIB, "GetAddOnVersion": V172,
                  "CreateObjects": {"elements": [{"elementId": {"guid": "new"}}]},
                  "SetGDLParametersOfElements": OK, "GetGDLParametersOfElements": _gdl()})
    pts = [{"x": 0, "y": 0, "z": 0}, {"x": 5, "y": 0, "z": 0}, {"x": 10, "y": 2, "z": 0}]
    r = rm.create_road_marking(conn, points=pts, dry_run=False)
    assert r["created"] is True
    commands = [c for c, _ in conn._core.calls]
    assert commands.index("CreateObjects") < commands.index("GetGDLParametersOfElements")
    (main,) = _writes(conn)
    assert main[:3] == [{"name": "A", "value": 1.0}, {"name": "aDone", "value": 2.0},
                        {"name": "extEnd", "value": 1.0}]
