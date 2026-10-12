"""Draping onto Meshes and Slabs: the surfaces, checked against Archicad's own (trial fixtures)."""
import json
import math
from pathlib import Path

import pytest

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core import drape as dr
from tests.conftest import FakeCore

FX = Path(__file__).parent / "fixtures" / "road_marking"
SURF = json.loads((FX / "surfaces_mcp_test.json").read_text())
LEVELS = {s["index"]: s["level"] for s in SURF["stories"]["stories"]}


def _conn(tapir):
    return ArchicadConnection(19725, core=FakeCore(official={"API.IsAddOnCommandAvailable": {"available": True}},
                                                   tapir=tapir))


def _mesh(name):
    e = SURF["elements"][name]
    return dr.mesh_surface(e["guid"], e["details"]["details"], LEVELS[e["details"]["floorIndex"]])


def _slab(outline, holes=(), arcs=None, z=1.0, off=0.2):
    return dr.slab_surface("s", {
        "zCoordinate": z, "offsetFromTop": off,
        "polygonOutline": [{"x": x, "y": y} for x, y in outline], "polygonArcs": arcs or [],
        "holes": [{"polygonOutline": [{"x": x, "y": y} for x, y in h]} for h in holes]})


@pytest.mark.parametrize("name,grid", [("mesh_crown", "archicad_heights_crown.json"),
                                       ("mesh_road_edges", "archicad_heights_road_edges.json")])
def test_meshes_match_archicads_own_triangles(name, grid):
    surface = _mesh(name)
    rows = json.loads((FX / grid).read_text())["heights"]
    assert len(rows) > 3900
    for x, y, z in rows:
        assert surface.height(x, y) == pytest.approx(z, abs=1e-6)


def test_level_lines_crossing_an_earlier_one_are_left_out_as_archicad_leaves_them():
    surface = _mesh("mesh_crossing_lines")
    assert surface.dropped_lines == 3
    rows = json.loads((FX / "archicad_heights_crossing_lines.json").read_text())["heights"]
    assert max(abs(surface.height(x, y) - z) for x, y, z in rows) < 0.06     # 51 mm near the crossings (trial)


def test_slab_tops_are_z_coordinate_plus_offset_from_top():
    for name in ("slab_top", "slab_corebottom"):
        e = SURF["elements"][name]
        surface = dr.slab_surface(e["guid"], e["details"]["details"])
        assert surface.top == pytest.approx(e["bounding_box"]["zMax"], abs=1e-6)


def test_slab_heights_inside_in_a_hole_outside_and_on_the_edge():
    s = _slab([(0, 0), (10, 0), (10, 10), (0, 10)], holes=[[(4, 4), (6, 4), (6, 6), (4, 6)]])
    assert s.height(1, 1) == pytest.approx(1.2)
    assert s.height(5, 5) is None
    assert s.height(11, 5) is None
    assert s.height(10.0005, 5) == pytest.approx(1.2)      # within 1 mm of the outline
    assert s.height(4.0005, 5) == pytest.approx(1.2)       # within 1 mm of the hole's edge
    assert s.height(10.01, 5) is None


def test_a_slab_arc_edge_becomes_chords_within_a_millimetre():
    # a half disc below the x axis: the arc from (-10, 0) to (10, 0) turns left, so it bulges to the right
    s = _slab([(-10, 0), (10, 0)], arcs=[{"begIndex": 0, "endIndex": 1, "arcAngle": math.pi}])
    assert s.height(0, -5) == pytest.approx(1.2)
    assert s.height(0, -9.999) == pytest.approx(1.2)
    assert s.height(0, -10.0015) is None
    assert s.height(0, 1) is None


def test_mesh_point_heights_are_relative_to_the_story_and_the_level():
    d = {"level": 0.2, "polygonCoordinates": [{"x": 0, "y": 0, "z": 0.0}, {"x": 4, "y": 0, "z": 0.4},
                                               {"x": 4, "y": 4, "z": 0.4}, {"x": 0, "y": 4, "z": 0.0},
                                               {"x": 0, "y": 0, "z": 0.0}]}
    s = dr.mesh_surface("m", d, story_level=3.0)
    assert s.height(0, 0) == pytest.approx(3.2)
    assert s.height(4, 2) == pytest.approx(3.6)
    assert s.height(2, 2) == pytest.approx(3.4)
    assert s.height(5, 2) is None
    assert s.height(4.0005, 2) == pytest.approx(3.60005, abs=1e-6)     # within 1 mm: the nearest triangle's plane


def test_a_level_line_along_an_outline_edge_and_through_its_corners():
    d = {"level": 0.0,
         "polygonCoordinates": [{"x": 0, "y": 0, "z": 0.0}, {"x": 10, "y": 0, "z": 0.0},
                                {"x": 10, "y": 10, "z": 1.0}, {"x": 0, "y": 10, "z": 1.0}],
         "sublines": [{"coordinates": [{"x": 0, "y": 0, "z": 0.0}, {"x": 5, "y": 0, "z": 0.3},
                                       {"x": 10, "y": 0, "z": 0.0}]},
                      {"coordinates": [{"x": 0, "y": 5, "z": 0.5}, {"x": 10, "y": 5, "z": 0.5}]}]}
    s = dr.mesh_surface("m", d, story_level=0.0)
    assert s.dropped_lines == 0
    assert s.height(5, 0) == pytest.approx(0.3) and s.height(2.5, 0) == pytest.approx(0.15)
    assert s.height(5, 5) == pytest.approx(0.5) and s.height(5, 7.5) == pytest.approx(0.75)
    assert all(s.height(x / 2, y / 2) is not None for x in range(21) for y in range(21))


def test_the_highest_surface_wins_where_two_overlap():
    low = _slab([(0, 0), (10, 0), (10, 10), (0, 10)], z=1.0, off=0.0)
    high = _slab([(5, 0), (15, 0), (15, 10), (5, 10)], z=2.0, off=0.0)
    s = dr.SurfaceSet([low, high])
    assert (s.height(2, 2), s.height(7, 2), s.height(12, 2), s.height(20, 2)) == (1.0, 2.0, 2.0, None)


def test_read_surfaces_reads_each_guid_once_and_names_what_it_cannot_use():
    by_guid = {e["guid"]: e["details"] for e in SURF["elements"].values()}
    by_guid["w-1"] = {"type": "Wall", "details": {}}

    def details(params):
        return {"detailsOfElements": [by_guid.get(e["elementId"]["guid"], {"error": {"code": 1, "message": "not found"}})
                                      for e in params["elements"]]}

    conn = _conn({"GetDetailsOfElements": details, "GetStories": SURF["stories"]})
    slab, mesh = SURF["elements"]["slab_top"]["guid"], SURF["elements"]["mesh_crown"]["guid"]
    surfaces = dr.read_surfaces(conn, [slab, mesh, slab])
    assert len(surfaces.surfaces) == 2
    assert surfaces.height(565, 5) == pytest.approx(3.25)          # the fixtures are where they were exported
    with pytest.raises(dr.DrapeError, match="Could not read drape_on element nope"):
        dr.read_surfaces(conn, ["nope"])
    with pytest.raises(dr.DrapeError, match="w-1 is a Wall"):
        dr.read_surfaces(conn, ["w-1"])
    with pytest.raises(dr.DrapeError, match="lists no elements"):
        dr.read_surfaces(conn, [])
