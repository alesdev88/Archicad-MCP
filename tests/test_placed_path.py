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


# ---------------------------------------------------------------- Tapir 1.7.2 item writes

def test_the_tapir_version_decides_the_write_mode():
    for raw, version, items in (("1.7.1", (1, 7, 1), False), ("1.7.2", (1, 7, 2), True),
                                ("1.8.0", (1, 8, 0), True), ("2.0", (2, 0, 0), True)):
        conn = _conn({"GetAddOnVersion": {"version": raw}})
        assert pp.tapir_version(conn) == version and pp.writes_items(conn) is items


def test_an_unknown_version_keeps_whole_array_writes():
    conn = _conn({})
    assert pp.tapir_version(conn) is None and pp.writes_items(conn) is False


def test_resize_writes_reset_the_path_then_add_one_node_per_extend_write():
    assert pp.resize_writes({"extEnd": 3.0}, 5) == [
        {"name": "A", "value": 1.0}, {"name": "aDone", "value": 2.0},
        {"name": "extEnd", "value": 4.0}, {"name": "extEnd", "value": 5.0},
        {"name": "extEnd", "value": 6.0}]
    assert pp.resize_writes({}, 2) == [{"name": "A", "value": 1.0}, {"name": "aDone", "value": 2.0}]


def _grown(n):
    """A path after resize_writes, as measured live: nodes 1 m, then 1.25 m apart along x."""
    xs = [0.0] + [1.0 + 1.25 * k for k in range(n - 1)]
    return {"nodeX": xs, "nodeY": [0.0] * n}


def _closest_gap(state, lists):
    """Write the lists item by item as Tapir 1.7.2 does: the parts' merge looks at every
    consecutive pair after each node item. The closest plan gap any write leaves."""
    closest = math.inf
    for p in lists:
        assert len(p["value"]) == len(state[p["name"]])          # no resizing
        for i, value in enumerate(p["value"]):
            state[p["name"]][i] = value
            xs, ys = state["nodeX"], state["nodeY"]
            closest = min(closest, min(math.dist((xs[j], ys[j]), (xs[j + 1], ys[j + 1]))
                                       for j in range(len(xs) - 1)))
    return closest


def test_parked_writes_keep_the_merge_away_where_plain_writes_trip_it():
    xs, ys = [0.0, 2.25, 4.0, 6.0], [0.0, 0.0, 0.0, 0.0]
    plain = [{"name": "nodeX", "value": xs}, {"name": "nodeY", "value": ys}]
    assert _closest_gap(_grown(4), plain) < pp.MERGE_GAP      # node 2 lands on node 3
    assert _closest_gap(_grown(4), pp.parked_xy(xs, ys)) == pytest.approx(1.0)


@pytest.mark.parametrize("seed", range(20))
def test_parked_writes_never_bring_nodes_closer_than_the_real_path(seed):
    import random
    rnd = random.Random(seed)
    n = rnd.randint(3, 40)
    pts = [(0.0, 0.0)]
    for _ in range(n - 1):
        step = rnd.choice([0.02, 0.5, 1.0, 1.25, 3.0])
        turn = rnd.uniform(-math.pi, math.pi)
        pts.append((pts[-1][0] + step * math.cos(turn), pts[-1][1] + step * math.sin(turn)))
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    real = min(math.dist(pts[i], pts[i + 1]) for i in range(n - 1))
    gap = _closest_gap(_grown(n), pp.parked_xy(xs, ys))
    assert gap >= min(real, 1.0) - 1e-9


def test_nodes_closer_than_the_merge_gap_are_refused():
    pp.check_plan_gaps([(0, 0, 0), (0.01, 0, 0), (3, 0, 1)], "Swept Beam")
    with pytest.raises(pp.PlacedPathError, match=r"Nodes 2 and 3 are 5\.0 mm apart"):
        pp.check_plan_gaps([(0, 0, 0), (3, 0, 0), (3.005, 0, 2)], "Swept Beam")
