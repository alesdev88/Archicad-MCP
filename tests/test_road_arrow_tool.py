import json
import math
from pathlib import Path

import pytest

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core import drape as dr
from archicad_mcp.core import road_arrow as ra
from tests.conftest import FakeCore


def test_axis_height_is_linear_and_runs_on_with_the_end_rise():
    nodes, node_s = [(0, 0, 1.0), (10, 0, 2.0), (20, 0, 2.0)], [0.0, 10.0, 20.0]
    assert ra.axis_height(nodes, node_s, 5.0) == pytest.approx(1.5)
    assert ra.axis_height(nodes, node_s, 15.0) == pytest.approx(2.0)
    assert ra.axis_height(nodes, node_s, -10.0) == pytest.approx(0.0)      # the first segment rises 0.1 per metre
    assert ra.axis_height(nodes, node_s, 30.0) == pytest.approx(2.0)       # last segment is flat


def test_on_a_straight_axis_anchors_are_exact():
    path = dr.PlanPath([(0, 0, 0), (20, 0, 0)], [0.0, 0.0], 0.002)
    assert ra.place_on_axis(path, 10.0, "tail", 5.0, 0.0).tail == pytest.approx((10.0, 0.0))
    assert ra.place_on_axis(path, 10.0, "tip", 5.0, 0.0).tip == pytest.approx((10.0, 0.0))
    assert ra.place_on_axis(path, 10.0, "centre", 5.0, 0.0).tail == pytest.approx((7.5, 0.0))
    assert ra.place_on_axis(path, 10.0, "tail", 5.0, 1.5).tail == pytest.approx((10.0, -1.5))     # to the right
    pl = ra.place_on_axis(path, 10.0, "tail", 5.0, 0.0)
    assert pl.angle == pytest.approx(0.0) and not pl.past_end


def test_a_bad_anchor_is_refused():
    path = dr.PlanPath([(0, 0, 0), (20, 0, 0)], [0.0, 0.0], 0.002)
    with pytest.raises(ra.RoadArrowError, match="anchor"):
        ra.place_on_axis(path, 10.0, "middle", 5.0, 0.0)


def test_on_an_arc_both_ends_sit_on_the_axis():
    path = dr.PlanPath([(0, 0, 0), (20, 20, 0)], [90.0, 0.0], 0.002)   # radius 20
    pl = ra.place_on_axis(path, 5.0, "tail", 7.5, 0.0)
    assert pl.tail == pytest.approx(ra.axis_point(path, 5.0, 0.0))
    assert math.dist(pl.tip, ra.axis_point(path, 12.5, 0.0)) < 0.05    # the arc is about 4 cm longer than the chord here


def test_past_the_end_runs_on_straight():
    path = dr.PlanPath([(0, 0, 0), (2, 0, 0)], [0.0, 0.0], 0.002)
    pl = ra.place_on_axis(path, 0.0, "tail", 5.0, 0.0)
    assert pl.tip == pytest.approx((5.0, 0.0)) and pl.past_end
    assert ra.axis_point(path, -1.0, 0.5) == pytest.approx((-1.0, -0.5))
    assert ra.axis_point(path, 3.0, 0.0) == pytest.approx((3.0, 0.0))
    assert not ra.place_on_axis(path, 2.0005, "tip", 0.01, 0.0).past_end      # within 1 mm
    assert ra.place_on_axis(path, 2.002, "tip", 0.01, 0.0).past_end


def test_an_axis_that_folds_back_over_the_arrow_is_refused_not_a_crash():
    # a hairpin: out 5 m and back, so the tail at 0 and the tip at 10 coincide
    path = dr.PlanPath([(0, 0, 0), (5, 0, 0), (0, 0.0001, 0)], [0.0, 0.0, 0.0], 0.002)
    with pytest.raises(ra.RoadArrowError, match="station 0.*folds back"):
        ra.place_on_axis(path, 0.0, "tail", path.length, 0.0)


# ---------------------------------------------------------------- create_road_arrow over a faked Archicad

FX = Path(__file__).parent / "fixtures" / "road_marking"
OK = {"executionResults": [{"success": True}]}
LIB = {"libraryParts": [{"documentName": "Road Arrow", "fileName": "Road Arrow.gsm", "typeId": "Object"}]}
PTS = [{"x": 0, "y": 0, "z": 0}, {"x": 20, "y": 0, "z": 0}]
SLAB = {"type": "Slab", "floorIndex": 1, "details": {
    "zCoordinate": 3.25, "offsetFromTop": 0.0, "holes": [],
    "polygonOutline": [{"x": -5, "y": -10}, {"x": 20, "y": -10}, {"x": 20, "y": 10}, {"x": -5, "y": 10},
                       {"x": -5, "y": -10}]}}
HALF = {"type": "Slab", "floorIndex": 1, "details": {
    "zCoordinate": 3.25, "offsetFromTop": 0.0, "holes": [],
    "polygonOutline": [{"x": -5, "y": -10}, {"x": 2.5, "y": -10}, {"x": 2.5, "y": 10}, {"x": -5, "y": 10},
                       {"x": -5, "y": -10}]}}
MESH = {"type": "Mesh", "floorIndex": 1, "details": {"level": 0.0, "polygonCoordinates": [
    {"x": 0, "y": 0, "z": 0.0}, {"x": 20, "y": 0, "z": 0.0}, {"x": 20, "y": 20, "z": 2.0}, {"x": 0, "y": 20, "z": 2.0}]}}
_BUMPS = ([{"x": i / 10, "y": -1, "z": 0.1 * (i % 2)} for i in range(0, 201)]
          + [{"x": 20, "y": 1, "z": 0.0}] + [{"x": i / 10, "y": 1, "z": 0.1 * (i % 2)} for i in range(200, -1, -1)])
BUMPY = {"type": "Mesh", "floorIndex": 1, "details": {"level": 0.0, "polygonCoordinates": _BUMPS}}
STORIES = {"stories": [{"index": 0, "level": 0.0}, {"index": 1, "level": 3.0}]}


def _answer_by_guid(by_guid):
    def answer(params):
        return {"detailsOfElements": [by_guid.get(e["elementId"]["guid"], {"error": {"code": 1, "message": "not found"}})
                                      for e in params["elements"]]}
    return answer


def _new_guids():
    count = iter(range(1, 100))
    return lambda params: {"elements": [{"elementId": {"guid": f"new-{next(count)}"}} for _ in params["objectsData"]]}


TAPIR = {"GetAvailableLibraryParts": LIB, "GetStories": STORIES, "SetGDLParametersOfElements": OK,
         "GetDetailsOfElements": _answer_by_guid({"slab": SLAB, "mesh": MESH, "bumpy": BUMPY})}
HALF_SLAB = {**TAPIR, "GetDetailsOfElements": _answer_by_guid({"slab": HALF})}


def _conn(tapir):
    return ArchicadConnection(19725, core=FakeCore(official={"API.IsAddOnCommandAvailable": {"available": True}},
                                                   tapir={"CreateObjects": _new_guids(), **tapir}))


def _params(entry):
    return {p["name"]: p["value"] for p in entry["gdl_parameters"]}


def _created(conn):
    return [item for c, p in conn._core.calls if c == "CreateObjects" for item in p["objectsData"]]


def _writes(conn):
    return [p["elementsWithGDLParameters"][0] for c, p in conn._core.calls if c == "SetGDLParametersOfElements"]


def _placed(angle=0.0, origin=(5.0, 5.0, 3.0), name="Road Arrow", reflected=None, floor=1):
    d = {"libPart": {"name": name}, "origin": dict(zip("xyz", origin)), "angle": angle}
    if reflected is not None:
        d["reflected"] = reflected
    return {"type": "Object", "floorIndex": floor, "details": d}


def _held(**over):
    v = dict(TABLE_DEFAULTS)
    v.update(nodeCount=0, nodeX=[0.0] * 40, nodeZ=[0.0] * 40, nodeFall=[0.0] * 40, slopePercent=0.0, crossFall=0.0)
    v.update(over)
    return {"gdlParametersOfElements": [{"parameters": [{"name": k, "value": x} for k, x in v.items()]}]}


TABLE_DEFAULTS = dict(ra.TABLE["defaults"])


def test_a_dry_run_reports_each_arrow_and_the_write_order():
    r = ra.create_road_arrow(_conn(TAPIR), points=PTS, arrow="5412 left", speed_kmh=50, stations=[2, 10])
    assert r["dry_run"] and r["arrow"] == "5412 Left" and r["size"] == "5.00 m" and len(r["arrows"]) == 2
    names = [p["name"] for p in r["arrows"][0]["gdl_parameters"]]
    assert names == ["arrowType", "arrowSize", "nodeCount", "nodeX", "nodeZ", "nodeFall", "slopePercent", "crossFall"]
    assert all(len(p["value"]) == 40 for p in r["arrows"][0]["gdl_parameters"]
               if p["name"].startswith("node") and p["name"] != "nodeCount")


def test_a_write_places_with_the_angle_at_the_tail():
    conn = _conn(TAPIR)
    r = ra.create_road_arrow(conn, points=[{"x": 0, "y": 0, "z": 0}, {"x": 0, "y": 20, "z": 0}],
                             stations=[10], dry_run=False)
    item = _created(conn)[0]
    assert item["angle"] == pytest.approx(math.pi / 2) and item["coordinates"]["y"] == pytest.approx(10.0)
    assert r["arrows"][0]["created"] and r["arrows"][0]["element"] == "new-1"
    assert [p["name"] for p in _writes(conn)[0]["gdlParameters"]][:3] == ["arrowType", "arrowSize", "nodeCount"]


def test_draping_fills_the_height_points_in_local_coordinates():
    r = ra.create_road_arrow(_conn(TAPIR), points=PTS, drape_on=["slab"], stations=[0])
    p = _params(r["arrows"][0])
    assert p["nodeCount"] == 2 and p["nodeX"][:2] == pytest.approx([0.0, 5.0]) and p["nodeZ"][:2] == pytest.approx([0.0, 0.0])
    assert p["slopePercent"] == 0 and r["arrows"][0]["tail"]["z"] == pytest.approx(3.25)


def test_without_drape_on_the_axis_gives_height_and_slope():
    r = ra.create_road_arrow(_conn(TAPIR), points=[{"x": 0, "y": 0, "z": 1.0}, {"x": 20, "y": 0, "z": 3.0}],
                             stations=[10], height=0.5, slope_percent=1.0, cross_fall=2.0)
    p = _params(r["arrows"][0])
    assert r["arrows"][0]["tail"]["z"] == pytest.approx(2.5) and p["slopePercent"] == pytest.approx(11.0)
    assert p["crossFall"] == 2.0 and p["nodeCount"] == 0


@pytest.mark.parametrize("given", [[], "[]"])
def test_an_empty_drape_on_is_refused(given):
    assert "drape_on" in ra.create_road_arrow(_conn(TAPIR), points=PTS, drape_on=given)["error"]


def test_an_arrow_half_off_the_slab_is_placed_and_reported():
    r = ra.create_road_arrow(_conn(HALF_SLAB), points=PTS, drape_on=["slab"], stations=[0], dry_run=False)
    a = r["arrows"][0]
    assert a["created"] and (a["partly_off_surface"] or a["off_surface"]) and r["warnings"] + a["warnings"]


def test_overlapping_arrows_warn_and_are_placed():
    r = ra.create_road_arrow(_conn(TAPIR), points=PTS, stations=[2, 4, 4], dry_run=False)
    assert len(r["arrows"]) == 3 and any("overlap" in w for w in r["warnings"])


def test_a_station_at_the_axis_end_rounded_to_a_millimetre_is_accepted():
    arc = [{"x": 0, "y": 0, "z": 0}, {"x": 18, "y": 18, "z": 0}]
    assert "error" not in ra.create_road_arrow(_conn(TAPIR), points=arc, stations=[math.hypot(18, 18) + 0.0009], anchor="tip")
    assert "error" in ra.create_road_arrow(_conn(TAPIR), points=arc, stations=[math.hypot(18, 18) + 0.002], anchor="tip")


@pytest.mark.parametrize("kwargs, text", [
    ({"arrow": "no such"}, "5411 Straight"), ({"arrow": "5417 Wrong way", "size": "5.00 m"}, "10.00 m"),
    ({"size": "5.00 m", "speed_kmh": 50}, "speed_kmh"), ({"custom_length": 6.0, "speed_kmh": 50}, "speed_kmh"),
    ({"custom_length": 6.0, "size": "5.00 m"}, "Custom"), ({"custom_length": 0.1}, "custom_length"),
    ({"thickness": 1.0}, "thickness"), ({"speed_kmh": 0}, "speed_kmh"), ({"speed_kmh": True}, "speed_kmh"),
    ({"stations": [-1]}, "station"), ({"stations": []}, "stations"), ({"anchor": "middle"}, "anchor"),
    ({"stations": ["a"]}, "station"), ({"stations": [float("nan")]}, "station"),
])
def test_refusals(kwargs, text):
    assert text in ra.create_road_arrow(_conn(TAPIR), points=PTS, **kwargs)["error"]


def test_a_source_is_needed_exactly_once():
    assert "exactly one" in ra.create_road_arrow(_conn(TAPIR))["error"]
    assert "exactly one" in ra.create_road_arrow(_conn(TAPIR), points=PTS, source_guid="x")["error"]


def test_one_refused_arrow_places_nothing():
    conn = _conn(TAPIR)
    r = ra.create_road_arrow(conn, points=PTS, stations=[2, 50, 4], dry_run=False)
    assert "50" in r["error"] and "station" in r["error"]
    assert not any(c == "CreateObjects" for c, _ in conn._core.calls)


def test_more_than_40_height_points_is_refused():
    conn = _conn(TAPIR)
    r = ra.create_road_arrow(conn, points=PTS, drape_on=["bumpy"], drape_tolerance=0.0001, stations=[2], dry_run=False)
    assert "more than 40" in r["error"] and "drape_tolerance" in r["error"]
    assert not any(c in ("CreateObjects", "SetGDLParametersOfElements") for c, _ in conn._core.calls)


def test_a_write_while_the_part_is_not_loaded_is_refused_and_a_dry_run_warns():
    conn = _conn({**TAPIR, "GetAvailableLibraryParts": {"libraryParts": []}})
    assert any("not loaded" in w for w in ra.create_road_arrow(conn, points=PTS)["warnings"])
    assert "not loaded" in ra.create_road_arrow(conn, points=PTS, dry_run=False)["error"]
    assert not any(c == "CreateObjects" for c, _ in conn._core.calls)


def test_a_failed_write_lists_the_elements_already_placed():
    answers = iter([OK, {"executionResults": [{"success": False, "error": "boom"}]}])
    conn = _conn({**TAPIR, "SetGDLParametersOfElements": lambda params: next(answers)})
    r = ra.create_road_arrow(conn, points=PTS, stations=[0, 10], dry_run=False)
    assert "boom" in r["error"] and r["elements_placed"] == ["new-1", "new-2"]
    assert r["arrows"][0]["created"] and "created" not in r["arrows"][1]


def test_update_keeps_settings_and_refuses_a_source():
    conn = _conn({**TAPIR, "GetDetailsOfElements": _answer_by_guid({"a-1": _placed()}),
                  "GetGDLParametersOfElements": _held()})
    for kwargs, name in (({"source_guid": "x"}, "source_guid"), ({"points": PTS}, "points"),
                         ({"stations": [1]}, "stations"), ({"anchor": "tip"}, "anchor"),
                         ({"offset": 1.0}, "offset"), ({"height": 0.1}, "height")):
        error = ra.create_road_arrow(conn, update_guid="a-1", **kwargs)["error"]
        assert name in error and "move_elements" in error
    r = ra.create_road_arrow(conn, update_guid="a-1", cross_fall=2.0, anchor="tail", offset=0, dry_run=False)
    assert r["arrows"][0]["updated"] and r["arrows"][0]["element"] == "a-1"
    assert _writes(conn)[0]["gdlParameters"] == [{"name": "crossFall", "value": 2.0}]
    assert not any(c == "CreateObjects" for c, _ in conn._core.calls)


def test_update_drapes_in_place():
    conn = _conn({**TAPIR, "GetDetailsOfElements": _answer_by_guid({"a-1": _placed(math.pi / 2, (5.0, 5.0, 3.0)), "mesh": MESH}),
                  "GetGDLParametersOfElements": _held()})
    r = ra.create_road_arrow(conn, update_guid="a-1", drape_on=["mesh"], dry_run=False)
    p = {x["name"]: x["value"] for x in _writes(conn)[0]["gdlParameters"]}
    # from (5, 5) north for 5 m on the plane z = 0.1 y: local x runs along the arrow, z against the element's level (3 m)
    assert p["nodeCount"] == 2 and p["nodeX"][:2] == pytest.approx([0.0, 5.0])
    assert p["nodeZ"][:2] == pytest.approx([0.5, 1.0]) and "slopePercent" not in p
    assert r["arrows"][0]["direction_degrees"] == pytest.approx(90.0)


def test_update_refuses_a_part_that_is_not_a_road_arrow():
    conn = _conn({**TAPIR, "GetDetailsOfElements": _answer_by_guid({"b-1": _placed(name="Swept Beam")})})
    assert "not a Road Arrow" in ra.create_road_arrow(conn, update_guid="b-1")["error"]


def test_update_refuses_a_mirrored_arrow():
    conn = _conn({**TAPIR, "GetDetailsOfElements": _answer_by_guid({"a-1": _placed(reflected=True)}),
                  "GetGDLParametersOfElements": _held()})
    assert "mirror" in ra.create_road_arrow(conn, update_guid="a-1", cross_fall=1.0)["error"]


def test_a_new_length_on_a_draped_arrow_warns():
    conn = _conn({**TAPIR, "GetDetailsOfElements": _answer_by_guid({"a-1": _placed()}),
                  "GetGDLParametersOfElements": _held(nodeCount=2)})
    r = ra.create_road_arrow(conn, update_guid="a-1", size="7.50 m")
    assert any("height points" in w for w in r["warnings"])
    assert _params(r["arrows"][0]) == {"arrowSize": "7.50 m"}
    quiet = _conn({**TAPIR, "GetDetailsOfElements": _answer_by_guid({"a-1": _placed()}),
                   "GetGDLParametersOfElements": _held(nodeCount=0)})
    assert not any("height points" in w for w in ra.create_road_arrow(quiet, update_guid="a-1", size="7.50 m")["warnings"])


def test_json_text_arguments():
    r = ra.create_road_arrow(_conn(TAPIR), points=json.dumps(PTS), stations="[2, 6]", drape_on='["slab"]')
    assert [a["station"] for a in r["arrows"]] == [2.0, 6.0]
    assert ra.create_road_arrow(_conn(TAPIR), points=PTS, stations="4")["arrows"][0]["station"] == 4.0
    assert ra.create_road_arrow(_conn(TAPIR), points=PTS, stations=3)["arrows"][0]["station"] == 3.0
    assert "stations" in ra.create_road_arrow(_conn(TAPIR), points=PTS, stations="[2,")["error"]


def test_warnings_say_arrow_not_marking():
    conn = _conn({**TAPIR, "GetDetailsOfElements": _answer_by_guid({"wall": {"type": "Wall", "details": {}}, "slab": HALF})})
    assert "marking" not in ra.create_road_arrow(conn, points=PTS, drape_on=["wall"])["error"]
    r = ra.create_road_arrow(conn, points=PTS, drape_on=["slab"], stations=[0], height=1.0)
    text = " ".join(r["warnings"] + r["arrows"][0]["warnings"])
    assert "marking" not in text and "do not apply" in text
    assert "Swept Beam" not in ra.create_road_arrow(conn, points=[PTS[0]])["error"]


def test_past_the_end_warns():
    r = ra.create_road_arrow(_conn(TAPIR), points=PTS, stations=[18])
    assert any("station 18" in w and "past" in w for w in r["warnings"])


def test_draping_on_the_saved_surfaces():
    surf = json.loads((FX / "surfaces_mcp_test.json").read_text())
    by_guid = {e["guid"]: e["details"] for e in surf["elements"].values()}
    conn = _conn({**TAPIR, "GetDetailsOfElements": _answer_by_guid(by_guid), "GetStories": surf["stories"]})
    slab, mesh = surf["elements"]["slab_top"]["guid"], surf["elements"]["mesh_crown"]["guid"]
    r = ra.create_road_arrow(conn, points=[{"x": 562, "y": 6, "z": 0}, {"x": 578, "y": 6, "z": 0}], stations=[1],
                             drape_on=[slab, mesh])
    a = r["arrows"][0]
    assert a["tail"]["z"] == pytest.approx(3.25, abs=0.01) and not a["off_surface"] and a["nodes"] >= 2
    on_mesh = ra.create_road_arrow(conn, points=[{"x": 592, "y": 5, "z": 0}, {"x": 608, "y": 5, "z": 0}], stations=[1],
                                   drape_on=[mesh])["arrows"][0]
    assert on_mesh["nodes"] >= 2 and not on_mesh["off_surface"]


def test_a_new_arrow_writes_thickness_and_a_custom_size_in_order():
    conn = _conn({**TAPIR, "GetAttributesByType": {"attributes": [{"index": 7, "name": "Paint white"}]}})
    r = ra.create_road_arrow(conn, points=PTS, custom_length=6.0, custom_width=0.9, thickness=0.004,
                             paint_surface="paint white")
    names = [p["name"] for p in r["arrows"][0]["gdl_parameters"]]
    assert names == ["arrowType", "arrowSize", "customLength", "customWidth", "nodeCount", "nodeX", "nodeZ",
                     "nodeFall", "slopePercent", "crossFall", "thickness", "paintSurface"]
    assert r["size"] == "Custom" and r["length"] == 6.0 and r["width"] == 0.9
    assert _params(r["arrows"][0])["paintSurface"] == 7


# ---------------------------------------------------------------- fix round 1

EAST_RISING = {"type": "Mesh", "floorIndex": 1, "details": {"level": 0.0, "polygonCoordinates": [
    {"x": -5, "y": -5, "z": 0.0}, {"x": 25, "y": -5, "z": 3.0}, {"x": 25, "y": 25, "z": 3.0}, {"x": -5, "y": 25, "z": 0.0}]}}
NORTH = [{"x": 5, "y": 0, "z": 0}, {"x": 5, "y": 20, "z": 0}]       # the plane rises 0.1 m per metre to the east


def test_a_mesh_rising_to_the_right_gives_a_positive_node_fall_new_and_update():
    tapir = {**TAPIR, "GetDetailsOfElements": _answer_by_guid({"east": EAST_RISING, "a-1": _placed(math.pi / 2, (5.0, 5.0, 3.0))}),
             "GetGDLParametersOfElements": _held()}
    new = ra.create_road_arrow(_conn(tapir), points=NORTH, drape_on=["east"], stations=[2])
    assert _params(new["arrows"][0])["nodeFall"][:2] == pytest.approx([10.0, 10.0], abs=0.01)
    conn = _conn(tapir)
    ra.create_road_arrow(conn, update_guid="a-1", drape_on=["east"], dry_run=False)
    p = {x["name"]: x["value"] for x in _writes(conn)[0]["gdlParameters"]}
    assert p["nodeFall"][:2] == pytest.approx([10.0, 10.0], abs=0.01) and p["nodeFall"][2:] == [0.0] * 38


def test_a_draped_update_does_not_write_a_given_slope_or_cross_fall():
    conn = _conn({**TAPIR, "GetDetailsOfElements": _answer_by_guid({"a-1": _placed(), "mesh": MESH}),
                  "GetGDLParametersOfElements": _held(slopePercent=0.0, crossFall=0.0)})
    r = ra.create_road_arrow(conn, update_guid="a-1", drape_on=["mesh"], slope_percent=5.0, cross_fall=3.0, dry_run=False)
    names = [x["name"] for x in _writes(conn)[0]["gdlParameters"]]
    assert "slopePercent" not in names and "crossFall" not in names
    assert any("do not apply" in w for w in r["warnings"])


def test_an_update_with_nothing_to_change_says_so():
    conn = _conn({**TAPIR, "GetDetailsOfElements": _answer_by_guid({"a-1": _placed()}),
                  "GetGDLParametersOfElements": _held()})
    r = ra.create_road_arrow(conn, update_guid="a-1", dry_run=False)
    assert r["arrows"][0]["updated"] is False and any("Nothing differed" in w for w in r["warnings"])
    assert not _writes(conn)


async def test_the_registered_tool_takes_its_lists_as_json_text(monkeypatch):
    import archicad_mcp.server as server_mod
    from fastmcp import Client

    core = FakeCore(official={"API.IsAddOnCommandAvailable": {"available": True}}, tapir=TAPIR)
    monkeypatch.setattr(server_mod, "get_connection", lambda port: ArchicadConnection(19725, core=core))
    async with Client(server_mod.build_server(mode="full")) as client:
        result = await client.call_tool("create_road_arrow", {
            "points": json.dumps(PTS), "stations": json.dumps([2, 10]), "arrow": "5412 Left"})
    payload = json.loads(result.content[0].text)
    assert payload["dry_run"] is True and len(payload["arrows"]) == 2
