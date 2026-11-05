"""create_road_marking over a faked Archicad."""
import json
import math

import pytest

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core import road_marking as rm
from tests.conftest import FakeCore

RM = rm.PARTS[rm.ROAD_MARKING]
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


def _gdl(_part=RM, **over):
    """A placed marking's parameters as GetGDLParametersOfElements returns them (angles in radians)."""
    v = dict(_part.table["defaults"])
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
                  "GetGDLParametersOfElements": _gdl(markingType="5111 Solid divider")})
    r = rm.create_road_marking(conn, points=PTS, marking="5111 Solid divider", drape_on=["slab"], lift=0.002,
                               dry_run=False)
    assert r["created"] is True and r["element"] == "new" and r["marking_after"] == "5111 Solid divider"
    placed = [p for c, p in conn._core.calls if c == "CreateObjects"][0]["objectsData"][0]
    assert placed["coordinates"]["z"] == pytest.approx(3.252) and "floorIndex" not in placed
    written = {p["name"]: p["value"] for p in _writes(conn)[-1]}
    assert written["nodeZ"] == [0.0, 0.0] and written["nodeFall"] == [0.0, 0.0]
    assert r["off_surface"] == [] and r["partly_off_surface"] == []


def test_a_new_marking_is_placed_at_angle_zero():
    # The nodes assume no rotation, and the Object tool hands a new element its own angle (90 degrees in a live
    # check), so the placement must say 0 itself or the whole marking turns about its first node.
    conn = _conn({"GetAvailableLibraryParts": LIB, "CreateObjects": {"elements": [{"elementId": {"guid": "new"}}]},
                  "SetGDLParametersOfElements": OK, "GetGDLParametersOfElements": _gdl()})
    rm.create_road_marking(conn, points=PTS, dry_run=False)
    placed = [p for c, p in conn._core.calls if c == "CreateObjects"][0]["objectsData"][0]
    assert placed["angle"] == 0.0


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
                               marking="5333-2 Bus stop on the carriageway", lettering={"length": 2.0, "offset": 2.0},
                               drape_on=["slab"], start_height=1.0)
    text = " ".join(r["warnings"])
    assert "replaces the source's own heights" in text and "start_height and slope_percent do not apply" in text
    assert "word BUS is 2.00 m long" not in text
    r = rm.create_road_marking(conn, points=[{"x": 0, "y": 0, "z": 0}, {"x": 1, "y": 0, "z": 0}],
                               marking="5333-2 Bus stop on the carriageway", lettering={"length": 4.0})
    assert "word BUS is 4.00 m long" in " ".join(r["warnings"])
    r = rm.create_road_marking(conn, points=PTS, lettering={"offset": 2.0})
    assert "word is None" in " ".join(r["warnings"])


def test_without_the_part_a_dry_run_warns_and_a_write_is_refused():
    conn = _conn({"GetAvailableLibraryParts": {"libraryParts": []}})
    assert any("'Road Marking' is not loaded" in w for w in rm.create_road_marking(conn, points=PTS)["warnings"])
    assert "'Road Marking' is not loaded" in rm.create_road_marking(conn, points=PTS, dry_run=False)["error"]


def test_the_call_is_refused_without_exactly_one_source_or_with_bad_input():
    conn = _conn({"GetAvailableLibraryParts": LIB})
    assert "exactly one" in rm.create_road_marking(conn)["error"]
    assert "exactly one" in rm.create_road_marking(conn, source_guid="a", points=PTS)["error"]
    assert "exactly one" in rm.create_road_marking(conn, update_guid="m-1")["error"]
    assert "JSON" in rm.create_road_marking(conn, points=PTS, stripes="{width: 1}")["error"]
    assert "Unknown marking" in rm.create_road_marking(conn, points=PTS, marking="5999")["error"]
    assert "drape_tolerance" in rm.create_road_marking(conn, points=PTS, drape_tolerance=0)["error"]
    beam = _conn({"GetDetailsOfElements": _details({"b-1": _marking(name="Swept Beam")})})
    error = rm.create_road_marking(beam, update_guid="b-1", points=PTS)["error"]
    assert "not a Road Marking or a Road Parking" in error


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
    r = rm.create_road_marking(_conn({"GetDetailsOfElements": _details({"m": morph})}), source_guid="m",
                               marking="Parking 60 degrees")
    assert "A Road Parking follows one unbranched line" in r["error"]


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
        "points": json.dumps(PTS), "marking": "5333-2 Bus stop on the carriageway",
        "bays": json.dumps({"depth": 2.5}), "lettering": json.dumps({}),
        "stripes": json.dumps({"1": {"on": True, "width": 0.12}}), "drape_on": json.dumps(["slab"])})
    assert payload["dry_run"] is True and payload["reach"] == {"u_lo": -0.06, "u_hi": 2.55}
    assert _params(payload)["bayDepth"] == 2.5


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


def test_stripe_angles_go_in_degrees_up_to_1_7_2_and_in_radians_from_1_7_3():
    # Tapir #1405: from 1.7.3 SetGDLParametersOfElements takes Angle values in radians
    for version, sent in (("1.7.1", 45.0), ("1.7.2", 45.0), ("1.7.3", math.radians(45))):
        conn = _conn({"GetAvailableLibraryParts": LIB, "GetAddOnVersion": {"version": version}})
        r = rm.create_road_marking(conn, points=PTS, stripes={"1": {"angle": 45}})
        assert _params(r)["s1Angle"] == pytest.approx(sent), version


def test_from_1_7_3_item_writes_send_arcs_in_radians():
    arc = {"type": "Arc", "floorIndex": 0, "details": {"origin": {"x": 0, "y": 0}, "radius": 10.0,
                                                       "begAngle": 0.0, "endAngle": math.pi / 3,
                                                       "zCoordinate": 0.0}}
    conn = _conn({"GetDetailsOfElements": _details({"a-1": arc}), "GetAvailableLibraryParts": LIB,
                  "GetAddOnVersion": {"version": "1.7.3"}})
    r = rm.create_road_marking(conn, source_guid="a-1")
    assert _params(r)["segArc"][0] == pytest.approx(math.pi / 3)


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


# ---------------------------------------------------------------- the Object tool's stale settings

NEW = {"GetAvailableLibraryParts": LIB, "CreateObjects": {"elements": [{"elementId": {"guid": "new"}}]},
       "SetGDLParametersOfElements": OK}
LOOK = dict(thickness=0.01, paintSurface=78, markBmat=5, showPlanFill=False, useProjectFill=True, planFill=9,
            fillFgPen=3, fillBgPen=4, showOutline=False, outlinePen=6)
# every setting holding something other than the part's default, as a part left in the Object tool could
EVERYTHING_STALE = {
    **LOOK, "gripNodes": False, "gripCurves": False, "gripAdd": False, "flipSides": True, "patternAlign": "End",
    "patternShift": 1.5, "wholeDashes": True, "s1On": False, "s1Offset": 1.0, "s1Width": 0.5, "s1Dash": 2.0, "s1Gap": 3.0,
    "s1Angle": 20.0, "s1Shape": "Triangle, point left", "s2On": True, "s2Offset": 1.0, "s2Width": 0.5, "s2Dash": 2.0,
    "s2Gap": 3.0, "s2Angle": 20.0, "s2Shape": "Triangle, point left", "s3On": True, "s3Offset": 1.0, "s3Width": 0.5,
    "s3Dash": 2.0, "s3Gap": 3.0, "s3Angle": 20.0, "s3Shape": "Triangle, point left", "bayLayout": "Stop end bars",
    "bayDepth": 6.0, "barWidth": 0.2, "letterText": "TAXI", "letterLength": 2.0, "letterWidth": 0.4, "letterGap": 0.4, "letterOffset": 3.0,
    "letterAlign": "End", "letterShift": 3.0, "letterRepeat": 9.0, "showAxis": True, "axisPen": 5, "axisLineType": 2,
    "showNodeHeights": True, "textPen": 5, "textSizeMm": 3.5, "markingType": "Custom"}


def _named(writes):
    return [(p["name"], p["value"]) for p in writes]


def _reads(conn):
    return [p for c, p in conn._core.calls if c == "GetGDLParametersOfElements"]


def _after(held, writes, part=RM):
    """The settings once the writes are done, as the part takes them: a new preset name replaces what it sets."""
    state = dict(held)
    for name, value in _named(writes):
        if name == "markingType" and value != state["markingType"] and value in part.presets:
            state.update(part.presets[value])
        state[name] = value
    return state


def _plain(_part=RM, **over):
    """What a placed marking holds, in the tool's own degrees."""
    return {**_part.table["defaults"], **over}


def test_the_table_covers_the_look_settings():
    assert set(LOOK) <= set(RM.table["settings"]) and set(LOOK) <= set(RM.table["defaults"])
    assert set(EVERYTHING_STALE) - {"markingType"} == set(RM.table["settings"]) - {"pathTolerance"}
    assert all(EVERYTHING_STALE[k] != RM.table["defaults"][k] for k in EVERYTHING_STALE)


def test_a_new_marking_does_not_keep_settings_left_in_the_object_tool():
    conn = _conn({**NEW, "GetGDLParametersOfElements": _gdl(showPlanFill=False, paintSurface=78, thickness=0.01)})
    r = rm.create_road_marking(conn, points=PTS, dry_run=False)
    written = _named(_writes(conn)[0])
    assert written[-3:] == [("thickness", 0.003), ("paintSurface", 18), ("showPlanFill", True)]
    assert r["created"] is True and r["element"] == "new" and "error" not in r
    assert _reads(conn) == [{"elements": [{"elementId": {"guid": "new"}}]}] * 2      # after placing and at the end


def test_every_look_setting_is_written_back_to_the_parts_default():
    conn = _conn({**NEW, "GetGDLParametersOfElements": _gdl(**LOOK)})
    rm.create_road_marking(conn, points=PTS, dry_run=False)
    written = dict(_named(_writes(conn)[0]))
    assert {k: written[k] for k in LOOK} == {k: RM.table["defaults"][k] for k in LOOK}


def test_a_placed_marking_holding_the_defaults_gets_the_writes_of_its_dry_run():
    # 1 for True and 1.0 for 1 are the same value
    conn = _conn({**NEW, "GetGDLParametersOfElements": _gdl(showPlanFill=1, planFill=1.0, paintSurface=18.0)})
    dry = rm.create_road_marking(conn, points=PTS)
    rm.create_road_marking(conn, points=PTS, dry_run=False)
    assert _writes(conn)[0] == dry["gdl_parameters"]


def test_settings_the_call_gives_are_not_overwritten_by_the_defaults():
    conn = _conn({**NEW, "GetGDLParametersOfElements": _gdl(showPlanFill=False, paintSurface=76, thickness=0.01,
                                                           pathTolerance=0.05)})
    rm.create_road_marking(conn, points=PTS, paint_surface=78, thickness=0.005, path_tolerance=0.01, dry_run=False)
    written = _named(_writes(conn)[0])
    assert [v for n, v in written if n == "thickness"] == [0.005]
    assert [v for n, v in written if n == "paintSurface"] == [78]
    assert [v for n, v in written if n == "pathTolerance"] == [0.01]
    assert ("showPlanFill", True) in written


def test_a_stale_setting_the_call_also_holds_needs_no_write():
    conn = _conn({**NEW, "GetGDLParametersOfElements": _gdl(thickness=0.005)})
    rm.create_road_marking(conn, points=PTS, thickness=0.005, dry_run=False)
    assert "thickness" not in dict(_named(_writes(conn)[0]))


def test_every_stale_setting_comes_back_with_no_preset_given():
    held = _plain(**EVERYTHING_STALE)
    conn = _conn({**NEW, "GetGDLParametersOfElements": _gdl(**EVERYTHING_STALE)})
    r = rm.create_road_marking(conn, points=PTS, dry_run=False)
    state = _after(held, _writes(conn)[0])
    assert all(state[k] == RM.table["defaults"][k] for k in RM.table["settings"] if k != "pathTolerance")
    assert state["markingType"] == r["marking"] == "5121 Broken divider, in settlements"


def test_a_stale_preset_name_gives_way_to_the_default_preset_which_sets_its_own_values():
    held = _plain(markingType="5231 Pedestrian crossing", **{"s1Width": 4.0, "s1Dash": 0.5, "s1Gap": 0.5, "patternAlign": "Centre",
                                                             "wholeDashes": True})
    conn = _conn({**NEW, "GetGDLParametersOfElements": _gdl(markingType="5231 Pedestrian crossing", s1Width=4.0, s1Dash=0.5,
                                                            s1Gap=0.5, patternAlign="Centre", wholeDashes=True)})
    rm.create_road_marking(conn, points=PTS, dry_run=False)
    written = _named(_writes(conn)[0])
    assert ("markingType", "5121 Broken divider, in settlements") in written
    names = [n for n, _ in written]
    assert not {"s1Width", "s1Dash", "s1Gap", "patternAlign", "wholeDashes"} & set(names)  # the preset writes those
    assert names.index("nodeX") < names.index("markingType")
    assert _after(held, _writes(conn)[0])["s1Width"] == 0.15


@pytest.mark.parametrize("marking", ["5334-1 Taxi stop on the carriageway", "5111-1 Bus lane line"])
def test_every_stale_setting_gives_way_to_the_preset_the_call_names(marking):
    held = _plain(**EVERYTHING_STALE)
    conn = _conn({**NEW, "GetGDLParametersOfElements": _gdl(**EVERYTHING_STALE)})
    rm.create_road_marking(conn, points=PTS, marking=marking, dry_run=False)
    state = _after(held, _writes(conn)[0])
    want = {**RM.table["defaults"], **RM.presets[marking], "markingType": marking}
    assert {k: state[k] for k in want if k != "pathTolerance"} == {k: want[k] for k in want if k != "pathTolerance"}


def test_a_stale_preset_equal_to_the_calls_is_not_written_and_its_other_settings_are_corrected():
    conn = _conn({**NEW, "GetGDLParametersOfElements": _gdl(markingType="5111 Solid divider", s1Width=0.5,
                                                            s2On=True, showPlanFill=False)})
    r = rm.create_road_marking(conn, points=PTS, marking="5111 Solid divider", dry_run=False)
    written = dict(_named(_writes(conn)[0]))
    assert "markingType" not in written
    assert written["s1Width"] == 0.15
    assert written["s2On"] is False and written["showPlanFill"] is True
    assert r["marking"] == "5111 Solid divider"


def test_a_call_for_custom_writes_the_name_and_every_stale_value():
    held = _plain(**{**EVERYTHING_STALE, "markingType": "5231 Pedestrian crossing"})
    stale = {**EVERYTHING_STALE, "markingType": "5231 Pedestrian crossing"}
    conn = _conn({**NEW, "GetGDLParametersOfElements": _gdl(**stale)})
    rm.create_road_marking(conn, points=PTS, marking="Custom", dry_run=False)
    state = _after(held, _writes(conn)[0])
    assert state["markingType"] == "Custom"
    assert all(state[k] == RM.table["defaults"][k] for k in RM.table["settings"] if k != "pathTolerance")


def test_a_stale_angle_goes_back_in_the_radians_the_tapir_takes():
    conn = _conn({**NEW, "GetAddOnVersion": {"version": "1.7.3"},
                  "GetGDLParametersOfElements": _gdl(markingType="5111-1 Bus lane line", s1Angle=20.0)})
    rm.create_road_marking(conn, points=PTS, marking="5111-1 Bus lane line", dry_run=False)
    assert dict(_named(_writes(conn)[0]))["s1Angle"] == pytest.approx(0.0)


@pytest.mark.parametrize("version", ["1.7.1", "1.8.0"])
def test_the_helper_grips_are_not_touched_by_the_correction(version):
    # a lone write of one of these is a drag; whatever they hold, the writes stay those of a clean placement
    helpers = dict(extStart=2.0, extStartDone=1.0, extEnd=2.0, extEndDone=1.0, insL=[1.0, 0.0], insDone=[0.0, 0.0],
                   segSag=[0.5, 0.0], sagDone=[0.0, 0.0], slopePercent=2.0, slopeDone=1.0)
    clean = _conn({**NEW, "GetAddOnVersion": {"version": version}, "GetGDLParametersOfElements": _gdl()})
    rm.create_road_marking(clean, points=PTS, dry_run=False)
    stale = _conn({**NEW, "GetAddOnVersion": {"version": version}, "GetGDLParametersOfElements": _gdl(**helpers)})
    rm.create_road_marking(stale, points=PTS, dry_run=False)
    assert _writes(stale) == _writes(clean)


def test_a_new_marking_grows_from_the_one_read_it_makes():
    three = [{"x": 0, "y": 0, "z": 0}, {"x": 10, "y": 0, "z": 0}, {"x": 20, "y": 5, "z": 0}]
    conn = _conn({**NEW, "GetAddOnVersion": {"version": "1.8.0"}, "GetGDLParametersOfElements": _gdl(extEnd=4.0, showPlanFill=False)})
    rm.create_road_marking(conn, points=three, dry_run=False)
    written = _named(_writes(conn)[0])
    assert written[:3] == [("A", 1.0), ("aDone", 2.0), ("extEnd", 5.0)]
    assert written[-1] == ("showPlanFill", True) and len(_reads(conn)) == 2


def test_an_update_keeps_the_markings_own_values():
    stale = dict(showPlanFill=False, paintSurface=78, thickness=0.01, markingType="Custom", s1Width=0.12, outlinePen=6)
    conn = _conn({**NEW, "GetDetailsOfElements": _details({"m-1": _marking()}),
                  "GetGDLParametersOfElements": _gdl(**stale)})
    r = rm.create_road_marking(conn, update_guid="m-1", points=PTS, flip_sides=True, dry_run=False)
    names = [n for w in _writes(conn) for n, _ in _named(w)]
    assert r["updated"] is True and "flipSides" in names
    assert not {*stale, "markBmat"} & set(names) and len(_reads(conn)) == 2


def test_a_dry_run_does_not_read_a_placement_it_has_not_made():
    conn = _conn({"GetAvailableLibraryParts": LIB})
    r = rm.create_road_marking(conn, points=PTS)
    assert "showPlanFill" not in _params(r) and "CreateObjects" not in [c for c, _ in conn._core.calls]
    assert not _reads(conn)


def test_a_failed_read_after_placing_still_reports_what_was_placed():
    answer = {"gdlParametersOfElements": [{"error": {"message": "gone"}}]}
    conn = _conn({**NEW, "GetGDLParametersOfElements": answer})
    r = rm.create_road_marking(conn, points=PTS, dry_run=False)
    assert "gone" in r["error"] and r["element"] == "new" and r["elements_placed"] == ["new"]
    assert "created" not in r and r["marking"] == "5121 Broken divider, in settlements" and not _writes(conn)


def test_a_read_after_placing_refused_by_the_api_still_reports_what_was_placed():
    conn = _conn(NEW)                 # FakeCore raises the transport's error for the read it has no answer to
    r = rm.create_road_marking(conn, points=PTS, dry_run=False)
    assert "GetGDLParametersOfElements" in r["error"] and r["elements_placed"] == ["new"] and "created" not in r


def test_a_failed_write_after_placing_still_reports_what_was_placed():
    conn = _conn({**NEW, "GetGDLParametersOfElements": _gdl(),
                  "SetGDLParametersOfElements": {"executionResults": [{"success": False, "error": "boom"}]}})
    r = rm.create_road_marking(conn, points=PTS, dry_run=False)
    assert "boom" in r["error"] and r["element"] == "new" and r["elements_placed"] == ["new"] and "created" not in r


def test_a_failed_last_read_warns_and_keeps_the_written_marking():
    bad = {"gdlParametersOfElements": [{"error": {"message": "gone"}}]}
    conn = _conn({**NEW, "GetGDLParametersOfElements": _sequence(_gdl(showPlanFill=False), bad)})
    r = rm.create_road_marking(conn, points=PTS, dry_run=False)
    assert r["created"] is True and r["element"] == "new" and "error" not in r and "marking_after" not in r
    assert any("gone" in w for w in r["warnings"]) and dict(_named(_writes(conn)[0]))["showPlanFill"] is True


def test_a_failed_placement_is_still_a_plain_error():
    conn = _conn({**NEW, "CreateObjects": {"elements": []}})
    r = rm.create_road_marking(conn, points=PTS, dry_run=False)
    assert "did not place" in r["error"] and "elements_placed" not in r


def test_a_failed_write_on_an_update_is_still_a_plain_error():
    conn = _conn({**NEW, "GetDetailsOfElements": _details({"m-1": _marking()}), "GetGDLParametersOfElements": _gdl(),
                  "SetGDLParametersOfElements": {"executionResults": [{"success": False, "error": "boom"}]}})
    r = rm.create_road_marking(conn, update_guid="m-1", points=PTS, dry_run=False)
    assert "boom" in r["error"] and "elements_placed" not in r
