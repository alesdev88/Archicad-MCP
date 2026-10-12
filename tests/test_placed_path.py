"""placed_path: the code create_swept_beam and create_road_marking share."""
import math

import pytest

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core import placed_path as pp
from tests.conftest import FakeCore


def _conn(tapir):
    official = {"API.IsAddOnCommandAvailable": {"available": True}}
    return ArchicadConnection(19724, core=FakeCore(official=official, tapir=tapir))


def _details(entry):
    return lambda params: {"detailsOfElements": [entry]}


def test_the_frame_check_names_the_part_it_wants():
    beam = {"type": "Object", "details": {"libPart": {"name": "Swept Beam"},
                                          "origin": {"x": 1, "y": 2, "z": 3}, "angle": 0.5}}
    conn = _conn({"GetDetailsOfElements": _details(beam)})
    assert pp.existing_frame(conn, "b-1", "Swept Beam") == ((1.0, 2.0, 3.0), 0.5)
    with pytest.raises(pp.PlacedPathError, match="is not a Road Marking"):
        pp.existing_frame(conn, "b-1", "Road Marking")


def test_the_library_part_check_takes_the_part_name():
    lib = {"libraryParts": [{"documentName": "Road Marking", "fileName": "Road Marking.gsm"}]}
    conn = _conn({"GetAvailableLibraryParts": lib})
    assert pp.library_part_loaded(conn, "Road Marking")
    assert not pp.library_part_loaded(conn, "Swept Beam")


def test_a_closed_polyline_is_refused_in_the_parts_words():
    entry = {"type": "PolyLine", "floorIndex": 0, "details": {
        "coordinates": [{"x": 0, "y": 0}, {"x": 2, "y": 0}, {"x": 2, "y": 2}, {"x": 0, "y": 0}],
        "zCoordinate": 0.0}}
    conn = _conn({"GetDetailsOfElements": _details(entry)})
    with pytest.raises(pp.PlacedPathError, match="A Road Marking needs a start and an end"):
        pp.path_from_element(conn, "p-1", 0.002, "Road Marking")


def test_placing_and_writing_name_the_part():
    conn = _conn({"CreateObjects": {"elements": [{}]}})
    with pytest.raises(pp.PlacedPathError, match="did not place 'Road Marking'"):
        pp.place(conn, "Road Marking", (0.0, 0.0, 0.0), None)
    conn = _conn({"SetGDLParametersOfElements": {"executionResults": [{"success": False, "error": "x"}]}})
    with pytest.raises(pp.PlacedPathError, match="Writing the Road Marking parameters"):
        pp.write_parameters(conn, "g", [], "Road Marking")


def test_json_text_is_parsed_and_bad_text_explained():
    assert pp.from_json_text("[1, 2]", "points", list) == [1, 2]
    assert pp.from_json_text([1], "points", list) == [1]
    assert pp.from_json_text(None, "points", list) is None
    with pytest.raises(pp.PlacedPathError, match="not valid JSON"):
        pp.from_json_text("0,0 3,0", "points", list)
    with pytest.raises(pp.PlacedPathError, match="must be a JSON list"):
        pp.from_json_text('{"a": 1}', "points", list)


def test_to_local_moves_to_the_origin_and_turns_back_by_the_angle():
    local = pp.to_local([(1.0, 1.0, 5.0), (1.0, 3.0, 6.0)], (1.0, 1.0, 5.0), math.pi / 2)
    assert local[0] == pytest.approx((0, 0, 0)) and local[1] == pytest.approx((2, 0, 1))
