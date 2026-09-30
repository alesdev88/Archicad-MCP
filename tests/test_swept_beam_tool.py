"""create_swept_beam over a faked Archicad."""

import json
import math
from pathlib import Path

import pytest

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core import swept_beam as sb
from tests.conftest import FakeCore

FIXTURE = Path(__file__).parent / "fixtures" / "swept_beam" / "morph_line_mcp_test.json"
LIB = {"libraryParts": [{"guid": "g", "index": 7, "documentName": "Swept Beam",
                         "fileName": "Swept Beam.gsm", "typeId": "Object"}]}


def _details(entry):
    return lambda params: {"detailsOfElements": [entry]}


def _conn(tapir):
    # conn.tapir() first asks the official API whether Tapir is installed
    official = {"API.IsAddOnCommandAvailable": {"available": True}}
    return ArchicadConnection(19724, core=FakeCore(official=official, tapir=tapir))


def _morph_entry():
    raw = json.loads(FIXTURE.read_text())
    return {"type": "Morph", "floorIndex": 0, "details": {
        "origin": raw["origin"], "xAxis": {"x": 1, "y": 0, "z": 0},
        "yAxis": {"x": 0, "y": 1, "z": 0}, "zAxis": {"x": 0, "y": 0, "z": 1},
        "body": {"vertices": raw["vertices"], "polygons": [], "wireEdges": raw["wireEdges"]}}}


def _params(result):
    return {p["name"]: p["value"] for p in result["gdl_parameters"]}


def test_morph_dry_run_reports_fit_and_parameters():
    conn = _conn({"GetDetailsOfElements": _details(_morph_entry()),
                  "GetAvailableLibraryParts": LIB})
    result = sb.create_swept_beam(conn, source_guid="m-1")
    assert result["dry_run"] is True and result["warnings"] == []
    assert result["max_deviation_mm"] <= 2.0
    params = _params(result)
    assert params["nodeX"][0] == 0.0 and params["nodeY"][0] == 0.0 and params["nodeZ"][0] == 0.0
    assert len(params["nodeX"]) == result["nodes"] == len(params["segArc"]) == len(params["insL"])
    assert params["profileMode"] == "Rectangle"
    # chain_wire_edges starts at the smaller free end, vertex 11, the top of the loop
    assert result["origin"]["z"] == pytest.approx(2.68387534)


def test_polyline_arc_keeps_its_sign_in_radians():
    # angle arrays go to SetGDLParametersOfElements in radians (gate A)
    entry = {"type": "PolyLine", "floorIndex": 0, "details": {
        "coordinates": [{"x": 0, "y": 0}, {"x": 2, "y": 2}, {"x": 5, "y": 2}],
        "arcs": [{"begIndex": 0, "endIndex": 1, "arcAngle": math.pi / 2}],
        "zCoordinate": 3.0}}
    conn = _conn({"GetDetailsOfElements": _details(entry), "GetAvailableLibraryParts": LIB})
    result = sb.create_swept_beam(conn, source_guid="p-1", start_height=0.5, slope_percent=10)
    params = _params(result)
    assert params["segArc"] == pytest.approx([math.pi / 2, 0.0, 0.0])
    assert result["origin"]["z"] == pytest.approx(3.5)
    assert params["nodeZ"] == pytest.approx([0.0, 0.1 * math.pi, 0.1 * (math.pi + 3)])


def test_points_keep_heights_and_warn_about_slope():
    conn = _conn({"GetAvailableLibraryParts": LIB})
    pts = [{"x": 0, "y": 0, "z": 1}, {"x": 4, "y": 0, "z": 2}]
    result = sb.create_swept_beam(conn, points=pts, slope_percent=5)
    assert _params(result)["nodeZ"] == [0.0, 1.0]
    assert _params(result)["A"] == pytest.approx(4.0)
    assert any("flat sources only" in w for w in result["warnings"])


def test_needs_exactly_one_source():
    conn = _conn({})
    assert "exactly one" in sb.create_swept_beam(conn)["error"]
    assert "exactly one" in sb.create_swept_beam(conn, source_guid="a", points=[])["error"]


def test_other_element_types_are_refused():
    entry = {"type": "Spline", "floorIndex": 0, "details": {}}
    conn = _conn({"GetDetailsOfElements": _details(entry)})
    assert "Spline" in sb.create_swept_beam(conn, source_guid="s-1")["error"]


def test_missing_library_part_warns_and_refuses():
    tapir = {"GetAvailableLibraryParts": {"libraryParts": []}}
    conn = _conn(tapir)
    pts = [{"x": 0, "y": 0, "z": 0}, {"x": 3, "y": 0, "z": 0}]
    dry = sb.create_swept_beam(conn, points=pts)
    assert any("not loaded" in w for w in dry["warnings"])
    committed = sb.create_swept_beam(conn, points=pts, dry_run=False)
    assert "not loaded" in committed["error"]
    assert all(cmd != "CreateObjects" for cmd, _ in conn._core.calls)


def test_commit_places_and_writes_parameters():
    tapir = {"GetAvailableLibraryParts": LIB,
             "CreateObjects": {"elements": [{"elementId": {"guid": "new-1"}}]},
             "SetGDLParametersOfElements": {"executionResults": [{"success": True}]}}
    conn = _conn(tapir)
    pts = [{"x": 10, "y": 5, "z": 2}, {"x": 13, "y": 5, "z": 2.3}]
    result = sb.create_swept_beam(conn, points=pts, dry_run=False)
    assert result["element"] == "new-1" and result["created"] is True
    calls = dict(conn._core.calls)
    assert calls["CreateObjects"]["objectsData"][0]["coordinates"] == {"x": 10, "y": 5, "z": 2}
    written = calls["SetGDLParametersOfElements"]["elementsWithGDLParameters"][0]
    assert written["elementId"] == {"guid": "new-1"}
    names = [p["name"] for p in written["gdlParameters"]]
    assert names[:9] == ["nodeX", "nodeY", "nodeZ", "nodeRoll", "segArc",
                         "segSag", "sagDone", "insL", "insDone"]
    values = {p["name"]: p["value"] for p in written["gdlParameters"]}
    # every grip helper pair starts equal, so no grip fires on the write (gate C)
    for name in ("segSag", "sagDone", "insL", "insDone"):
        assert values[name] == [0.0, 0.0]
    for name in ("extStart", "extStartDone", "extEnd", "extEndDone"):
        assert values[name] == 0.0
    assert values["A"] == values["aDone"] == pytest.approx(math.hypot(3, 0))


def test_update_refuses_other_elements():
    other = {"type": "Object", "floorIndex": 0, "details": {
        "libPart": {"name": "Chair"}, "origin": {"x": 0, "y": 0, "z": 0}, "angle": 0.0}}
    conn = _conn({"GetDetailsOfElements": _details(other), "GetAvailableLibraryParts": LIB})
    pts = [{"x": 0, "y": 0, "z": 0}, {"x": 3, "y": 0, "z": 0}]
    result = sb.create_swept_beam(conn, points=pts, update_guid="chair-1", dry_run=False)
    assert "not a Swept Beam" in result["error"]
    assert all(cmd != "SetGDLParametersOfElements" for cmd, _ in conn._core.calls)


def test_update_uses_the_existing_frame():
    beam = {"type": "Object", "floorIndex": 0, "details": {
        "libPart": {"name": "Swept Beam"}, "origin": {"x": 10, "y": 0, "z": 1},
        "angle": math.pi / 2}}
    tapir = {"GetDetailsOfElements": _details(beam), "GetAvailableLibraryParts": LIB,
             "SetGDLParametersOfElements": {"executionResults": [{"success": True}]}}
    conn = _conn(tapir)
    pts = [{"x": 10, "y": 0, "z": 1}, {"x": 10, "y": 3, "z": 1}]
    result = sb.create_swept_beam(conn, points=pts, update_guid="b-1", dry_run=False)
    assert result["updated"] is True and result["element"] == "b-1"
    written = dict(conn._core.calls)["SetGDLParametersOfElements"]
    values = {p["name"]: p["value"] for p in written["elementsWithGDLParameters"][0]["gdlParameters"]}
    assert values["nodeX"] == pytest.approx([0.0, 3.0]) and values["nodeY"] == pytest.approx([0.0, 0.0])
    assert all(cmd != "CreateObjects" for cmd, _ in conn._core.calls)


def test_profile_attribute_by_name():
    attrs = {"attributes": [{"index": 12, "name": "SB Hollow"}, {"index": 3, "name": "Other"}]}
    tapir = {"GetAvailableLibraryParts": LIB, "GetAttributesByType": attrs}
    conn = _conn(tapir)
    pts = [{"x": 0, "y": 0, "z": 0}, {"x": 3, "y": 0, "z": 0}]
    params = _params(sb.create_swept_beam(conn, points=pts, profile={"attribute": "sb hollow"}))
    assert params["profileMode"] == "Profile attribute" and params["beamProfile"] == 12
    missing = sb.create_swept_beam(conn, points=pts, profile={"attribute": "Hollow"})
    assert "No Profile attribute named 'Hollow'" in missing["error"] and "SB Hollow" in missing["error"]


def test_multi_node_paths_write_a_equal_to_adone():
    conn = _conn({"GetAvailableLibraryParts": LIB})
    pts = [{"x": 0, "y": 0, "z": 0}, {"x": 3, "y": 0, "z": 0}, {"x": 3, "y": 4, "z": 0}]
    params = _params(sb.create_swept_beam(conn, points=pts))
    assert len(params["nodeX"]) == 3
    assert params["A"] == params["aDone"] == pytest.approx(7.0)


SECTION_NAMES = ("profileMode", "beamProfile", "rectW", "rectH", "rectBMat",
                 "profileOffsetU", "profileOffsetW", "flipProfile")


def _written(conn):
    call = [p for cmd, p in conn._core.calls if cmd == "SetGDLParametersOfElements"][-1]
    return {p["name"]: p["value"] for p in call["elementsWithGDLParameters"][0]["gdlParameters"]}


def test_update_keeps_the_section_unless_the_call_sets_it():
    beam = {"type": "Object", "floorIndex": 0, "details": {
        "libPart": {"name": "Swept Beam"}, "origin": {"x": 0, "y": 0, "z": 0}, "angle": 0.0}}
    tapir = {"GetDetailsOfElements": _details(beam), "GetAvailableLibraryParts": LIB,
             "SetGDLParametersOfElements": {"executionResults": [{"success": True}]}}
    conn = _conn(tapir)
    pts = [{"x": 0, "y": 0, "z": 0}, {"x": 3, "y": 0, "z": 0}]
    sb.create_swept_beam(conn, points=pts, update_guid="b-1", dry_run=False)
    assert not set(SECTION_NAMES) & set(_written(conn))
    sb.create_swept_beam(conn, points=pts, update_guid="b-1", offset_u=0.05, dry_run=False)
    written = _written(conn)
    assert written["profileOffsetU"] == 0.05
    assert not {"profileMode", "profileOffsetW", "flipProfile"} & set(written)


def test_new_beams_get_the_default_section():
    conn = _conn({"GetAvailableLibraryParts": LIB})
    pts = [{"x": 0, "y": 0, "z": 0}, {"x": 3, "y": 0, "z": 0}]
    params = _params(sb.create_swept_beam(conn, points=pts))
    assert (params["profileMode"], params["rectW"], params["rectH"]) == ("Rectangle", 0.2, 0.2)
    assert (params["profileOffsetU"], params["profileOffsetW"], params["flipProfile"]) == (0.0, 0.0, False)


def test_polyline_repeated_vertex_is_dropped_and_keeps_its_arc():
    entry = {"type": "PolyLine", "floorIndex": 0, "details": {
        "coordinates": [{"x": 0, "y": 0}, {"x": 2, "y": 0}, {"x": 2, "y": 0}, {"x": 4, "y": 2}],
        "arcs": [{"begIndex": 2, "endIndex": 3, "arcAngle": math.pi / 2}], "zCoordinate": 0.0}}
    conn = _conn({"GetDetailsOfElements": _details(entry), "GetAvailableLibraryParts": LIB})
    result = sb.create_swept_beam(conn, source_guid="p-2")
    assert result["nodes"] == 3 and result["warnings"] == []
    assert _params(result)["segArc"] == pytest.approx([0.0, math.pi / 2, 0.0])


def test_zero_length_sources_are_refused():
    line = {"type": "Line", "floorIndex": 0, "details": {
        "begCoordinate": {"x": 1, "y": 1}, "endCoordinate": {"x": 1, "y": 1}, "zCoordinate": 0.0}}
    conn = _conn({"GetDetailsOfElements": _details(line), "GetAvailableLibraryParts": LIB})
    assert "two distinct points" in sb.create_swept_beam(conn, source_guid="l-1")["error"]
    poly = {"type": "PolyLine", "floorIndex": 0, "details": {
        "coordinates": [{"x": 1, "y": 1}, {"x": 1, "y": 1}], "arcs": [], "zCoordinate": 0.0}}
    conn = _conn({"GetDetailsOfElements": _details(poly), "GetAvailableLibraryParts": LIB})
    assert "two distinct points" in sb.create_swept_beam(conn, source_guid="p-3")["error"]


async def _call_tool(monkeypatch, tapir, args):
    import archicad_mcp.server as server_mod
    from fastmcp import Client

    core = FakeCore(official={"API.IsAddOnCommandAvailable": {"available": True}}, tapir=tapir)
    monkeypatch.setattr(server_mod, "get_connection",
                        lambda port: ArchicadConnection(19724, core=core))
    async with Client(server_mod.build_server(mode="full")) as client:
        result = await client.call_tool("create_swept_beam", args)
        return json.loads(result.content[0].text)


async def test_the_tool_takes_points_and_profile_sent_as_json_text(monkeypatch):
    # Claude Code collapses nullable list and object fields and sends them as text
    attrs = {"attributes": [{"index": 12, "name": "SB Hollow"}]}
    pts = [{"x": 0, "y": 0, "z": 0}, {"x": 3, "y": 0, "z": 0}]
    payload = await _call_tool(monkeypatch, {"GetAvailableLibraryParts": LIB, "GetAttributesByType": attrs},
                               {"points": json.dumps(pts), "profile": json.dumps({"attribute": "SB Hollow"})})
    assert payload["nodes"] == 2
    assert _params(payload)["beamProfile"] == 12


async def test_the_tool_explains_text_that_is_not_json(monkeypatch):
    payload = await _call_tool(monkeypatch, {"GetAvailableLibraryParts": LIB}, {"points": "0,0 3,0"})
    assert "points" in payload["error"] and "JSON" in payload["error"]


CUT_NAMES = ("cutStartPlan", "cutStartTilt", "cutEndPlan", "cutEndTilt")


def test_new_beams_get_square_ends_unless_cuts_are_given():
    conn = _conn({"GetAvailableLibraryParts": LIB})
    pts = [{"x": 0, "y": 0, "z": 0}, {"x": 3, "y": 0, "z": 0}]
    params = _params(sb.create_swept_beam(conn, points=pts))
    assert [params[n] for n in CUT_NAMES] == [0.0, 0.0, 0.0, 0.0]
    # single angle parameters go through the API in degrees (gate A)
    params = _params(sb.create_swept_beam(conn, points=pts, cut_start_plan=30, cut_end_tilt=-12.5))
    assert [params[n] for n in CUT_NAMES] == [30.0, 0.0, 0.0, -12.5]


def test_update_keeps_the_end_cuts_unless_the_call_sets_them():
    beam = {"type": "Object", "floorIndex": 0, "details": {
        "libPart": {"name": "Swept Beam"}, "origin": {"x": 0, "y": 0, "z": 0}, "angle": 0.0}}
    tapir = {"GetDetailsOfElements": _details(beam), "GetAvailableLibraryParts": LIB,
             "SetGDLParametersOfElements": {"executionResults": [{"success": True}]}}
    conn = _conn(tapir)
    pts = [{"x": 0, "y": 0, "z": 0}, {"x": 3, "y": 0, "z": 0}]
    sb.create_swept_beam(conn, points=pts, update_guid="b-1", dry_run=False)
    assert not set(CUT_NAMES) & set(_written(conn))
    sb.create_swept_beam(conn, points=pts, update_guid="b-1", cut_end_plan=-45, dry_run=False)
    written = _written(conn)
    assert written["cutEndPlan"] == -45.0
    assert not {"cutStartPlan", "cutStartTilt", "cutEndTilt"} & set(written)


def test_end_cuts_out_of_range_are_refused():
    conn = _conn({"GetAvailableLibraryParts": LIB})
    pts = [{"x": 0, "y": 0, "z": 0}, {"x": 3, "y": 0, "z": 0}]
    assert "cut_start_plan" in sb.create_swept_beam(conn, points=pts, cut_start_plan=81)["error"]
    assert "cut_end_tilt" in sb.create_swept_beam(conn, points=pts, cut_end_tilt=-86)["error"]


async def test_the_tool_passes_end_cuts(monkeypatch):
    pts = [{"x": 0, "y": 0, "z": 0}, {"x": 3, "y": 0, "z": 0}]
    payload = await _call_tool(monkeypatch, {"GetAvailableLibraryParts": LIB},
                               {"points": pts, "cut_start_plan": 20, "cut_end_tilt": 5})
    params = _params(payload)
    assert (params["cutStartPlan"], params["cutEndTilt"]) == (20.0, 5.0)


def test_new_beams_sit_centred_on_their_reference_line_unless_placed():
    conn = _conn({"GetAvailableLibraryParts": LIB})
    pts = [{"x": 0, "y": 0, "z": 0}, {"x": 3, "y": 0, "z": 0}]
    params = _params(sb.create_swept_beam(conn, points=pts))
    assert (params["refLine"], params["refOffset"]) == ("Centre", 0.0)
    params = _params(sb.create_swept_beam(conn, points=pts, ref_line="Left face", ref_offset=0.4))
    assert (params["refLine"], params["refOffset"]) == ("Left face", 0.4)
    params = _params(sb.create_swept_beam(conn, points=pts, ref_line="right face"))
    assert params["refLine"] == "Right face"


def test_update_keeps_the_reference_line_unless_the_call_sets_it():
    beam = {"type": "Object", "floorIndex": 0, "details": {
        "libPart": {"name": "Swept Beam"}, "origin": {"x": 0, "y": 0, "z": 0}, "angle": 0.0}}
    tapir = {"GetDetailsOfElements": _details(beam), "GetAvailableLibraryParts": LIB,
             "SetGDLParametersOfElements": {"executionResults": [{"success": True}]}}
    conn = _conn(tapir)
    pts = [{"x": 0, "y": 0, "z": 0}, {"x": 3, "y": 0, "z": 0}]
    sb.create_swept_beam(conn, points=pts, update_guid="b-1", dry_run=False)
    assert not {"refLine", "refOffset"} & set(_written(conn))
    sb.create_swept_beam(conn, points=pts, update_guid="b-1", ref_offset=0.25, dry_run=False)
    written = _written(conn)
    assert written["refOffset"] == 0.25 and "refLine" not in written


def test_an_unknown_reference_line_is_refused():
    conn = _conn({"GetAvailableLibraryParts": LIB})
    pts = [{"x": 0, "y": 0, "z": 0}, {"x": 3, "y": 0, "z": 0}]
    error = sb.create_swept_beam(conn, points=pts, ref_line="outside")["error"]
    assert "ref_line" in error and "left face" in error


async def test_the_tool_passes_the_reference_line(monkeypatch):
    pts = [{"x": 0, "y": 0, "z": 0}, {"x": 3, "y": 0, "z": 0}]
    payload = await _call_tool(monkeypatch, {"GetAvailableLibraryParts": LIB},
                               {"points": pts, "ref_line": "left face", "ref_offset": 0.4})
    params = _params(payload)
    assert (params["refLine"], params["refOffset"]) == ("Left face", 0.4)
