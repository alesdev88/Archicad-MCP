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
    assert s.height(4.0005, 2) == pytest.approx(3.6, abs=1e-6)     # within 1 mm: the closest point of the edge


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


class Plane:
    """A test surface given by a function; None where it is absent."""
    box = (-1e9, -1e9, 1e9, 1e9)

    def __init__(self, fn):
        self.fn = fn

    def height(self, x, y):
        return self.fn(x, y)


def test_the_path_maps_offsets_as_the_part_does():
    path = dr.PlanPath([(0, 0, 0), (10, 0, 0), (10, 10, 0)], [0.0, 0.0, 0.0], 0.002)
    assert path.length == pytest.approx(20.0) and path.node_s == pytest.approx([0.0, 10.0, 20.0])
    assert path.xy(10, 1) == pytest.approx((11, -1))       # the mitre at the kink
    assert path.xy(5, 1) == pytest.approx((5.5, -1))       # offset lines run straight from mitre to mitre
    arc = dr.PlanPath([(10, 0, 0), (0, 10, 0)], [90.0, 0.0], 0.002)
    assert arc.length == pytest.approx(10 * math.pi / 2, rel=1e-4)


def test_no_nodes_on_a_plane_and_the_cross_fall_from_its_tilt():
    s = dr.SurfaceSet([Plane(lambda x, y: 1 + 0.03 * x - 0.02 * y)])
    d = dr.drape([(0, 0, 0), (20, 0, 0)], [0.0, 0.0], (-1, 1), s)
    assert d.added == 0 and d.max_along < 1e-9
    assert d.falls == pytest.approx([2.0, 2.0])             # looking along +x, right is -y: 2 % up to the right
    assert [n[2] for n in d.nodes] == pytest.approx([1.0, 1.6])


def test_a_crown_gives_misfit_no_nodes_and_never_buries_the_paint():
    s = dr.SurfaceSet([Plane(lambda x, y: 1 - 0.03 * abs(y))])
    d = dr.drape([(0, 0, 0), (20, 0, 0)], [0.0, 0.0], (-2, 2), s)
    assert d.added == 0
    assert d.max_across == pytest.approx(0.06)
    assert [n[2] for n in d.nodes] == pytest.approx([1.0, 1.0])     # on the ridge, not under it


def test_nodes_where_the_surface_bends_and_arcs_split_in_proportion():
    s = dr.SurfaceSet([Plane(lambda x, y: 0.01 * x * y)])            # along the arc: 0.5 sin 2t
    d = dr.drape([(10, 0, 0), (0, 10, 0)], [90.0, 0.0], (-0.1, 0.1), s, tol=0.005)
    assert d.added > 0 and d.max_along <= 0.005
    assert sum(d.arcs) == pytest.approx(90.0)
    for x, y, _ in d.nodes:
        assert math.hypot(x, y) == pytest.approx(10.0, abs=1e-6)


def test_off_and_partly_off_stretches_and_lift():
    s = dr.SurfaceSet([Plane(lambda x, y: 2.0 if 0 <= x <= 10 and y >= 0 else None)])
    # looking along +x, right is -y: the reach 0 to 2 runs from y 1 to y -1, half of it off the surface
    d = dr.drape([(-1, 1, 0), (12, 1, 0)], [0.0, 0.0], (0, 2), s, lift=0.01)
    assert d.off_surface == [{"from": 0.0, "to": 0.9}, {"from": 11.1, "to": 13.0}]
    assert d.partly_off_surface == [{"from": 1.0, "to": 11.0}]
    assert [n[2] for n in d.nodes] == pytest.approx([2.01, 2.01])


def test_a_station_with_one_sample_on_the_surface_takes_its_height():
    # a strip above y 0.95: along y = 1 only the samples at u = 0 land on it
    s = dr.SurfaceSet([Plane(lambda x, y: 2.0 + 0.1 * y if y >= 0.95 else None)])
    d = dr.drape([(0, 1, 0), (10, 1, 0)], [0.0, 0.0], (0, 2), s)
    assert [n[2] for n in d.nodes] == pytest.approx([2.1, 2.1]) and d.falls == pytest.approx([0.0, 0.0])
    assert d.partly_off_surface == [{"from": 0.0, "to": 10.0}]


def test_no_surface_anywhere_is_refused():
    s = dr.SurfaceSet([Plane(lambda x, y: None)])
    with pytest.raises(dr.DrapeError, match="No surface in drape_on lies under the marking"):
        dr.drape([(0, 0, 0), (5, 0, 0)], [0.0, 0.0], (-0.1, 0.1), s)


def test_too_many_nodes_is_refused_naming_where():
    s = dr.SurfaceSet([Plane(lambda x, y: 0.05 * math.sin(x))])
    with pytest.raises(dr.DrapeError, match="more than 10; .* between stations 0.0 and 30.0 m"):
        dr.drape([(0, 0, 0), (30, 0, 0)], [0.0, 0.0], (-0.1, 0.1), s, tol=0.0005, max_nodes=10)


def test_a_path_shorter_than_one_step_still_drapes():
    s = dr.SurfaceSet([Plane(lambda x, y: 1 + 0.1 * x)])
    d = dr.drape([(0, 0, 0), (0.05, 0, 0)], [0.0, 0.0], (-0.075, 0.075), s)
    assert [n[2] for n in d.nodes] == pytest.approx([1.0, 1.005])


def test_the_crossing_level_lines_warning_reaches_the_report():
    d = dr.drape([(591, 14, 0), (619, 14, 0)], [0.0, 0.0], (-0.075, 0.075),
                 dr.SurfaceSet([_mesh("mesh_crossing_lines")]))
    assert any("level line segments cross" in w for w in d.warnings)


def _gaps(d, surface, reach, step=0.01):
    """The paint's lowest and highest point against the surface on a fine grid (negative: under it)."""
    path = dr.PlanPath(d.nodes, d.arcs, 0.002)
    ns, low, high = path.node_s, float("inf"), float("-inf")
    for i in range(round(path.length / step) + 1):
        s = min(i * step, path.length)
        k = min(max(j for j in range(len(ns) - 1) if ns[j] <= s + 1e-12), len(ns) - 2)
        t = (s - ns[k]) / (ns[k + 1] - ns[k])
        z = d.nodes[k][2] + (d.nodes[k + 1][2] - d.nodes[k][2]) * t
        f = d.falls[k] + (d.falls[k + 1] - d.falls[k]) * t
        for j in range(round((reach[1] - reach[0]) / step) + 1):
            u = reach[0] + j * step
            h = surface.height(*path.xy(s, u))
            if h is not None:
                low, high = min(low, z + u * f / 100 - h), max(high, z + u * f / 100 - h)
    return low, high


def test_the_paint_never_dips_under_a_crest_between_nodes():
    # a crest of 1000 m radius along the path: straight pieces between nodes would cut under it (live check, 12.10.2026)
    s = dr.SurfaceSet([Plane(lambda x, y: -0.0005 * (x - 10) ** 2)])
    d = dr.drape([(0, 0, 0), (20, 0, 0)], [0.0, 0.0], (-0.075, 0.075), s)
    assert d.added > 0
    assert _gaps(d, s, (-0.075, 0.075))[0] >= -1e-6


def test_the_paint_never_dips_under_a_ridge_between_cross_samples():
    # a ridge 0.2 m right of the path, between the cross samples at 0 and 0.46 m (looking along +x, right is -y)
    s = dr.SurfaceSet([Plane(lambda x, y: 1 - 0.03 * abs(y + 0.2))])
    d = dr.drape([(0, 0, 0), (10, 0, 0)], [0.0, 0.0], (0, 3.2), s)
    low, high = _gaps(d, s, (0, 3.2))
    assert low >= -1e-6
    assert d.max_across == pytest.approx(high, abs=0.001)       # the report says how far the paint floats


def _turned(x, y, degrees=1.0):
    r = math.radians(degrees)
    return 591.237 + x * math.cos(r) - y * math.sin(r), 63.418 + x * math.sin(r) + y * math.cos(r)


def _edge_z(x):
    return 0.02 * x + 0.1 * math.sin(x / 3)


def _road_with_a_line_along_its_edge():
    """A 30 by 8 m road Mesh turned 1 degree, with a level line along its outer edge, a point every metre
    (a road built from 3D edges, its kerb line on the outline)."""
    corners = [(0, 0, 0.0), (30, 0, _edge_z(30)), (30, 8, 0.6), (0, 8, 0.0)]
    poly = [dict(zip("xyz", _turned(x, y) + (z,))) for x, y, z in corners]
    line = [dict(zip("xyz", _turned(x, 0) + (_edge_z(x),))) for x in range(31)]
    middle = [dict(zip("xyz", _turned(x, 4) + (0.02 * x + 0.05,))) for x in range(0, 31, 2)]
    return dr.mesh_surface("m", {"level": 0.0, "polygonCoordinates": poly + [poly[0]],
                                 "sublines": [{"coordinates": line}, {"coordinates": middle}]}, 0.0)


@pytest.mark.parametrize("reach", [(-0.075, 0.075), (0.0, 0.15), (-0.15, 0.0)])
def test_a_line_just_outside_a_turned_mesh_edge_drapes_to_sane_heights(reach):
    # review, 12.10.2026: 0.5 mm outside a 1 degree turned edge the tool returned heights of 1e8 m
    surfaces = dr.SurfaceSet([_road_with_a_line_along_its_edge()])
    a, b = _turned(0.5, -0.0005), _turned(29.5, -0.0005)
    d = dr.drape([(a[0], a[1], 0), (b[0], b[1], 0)], [0.0, 0.0], reach, surfaces)
    assert all(-0.5 < n[2] < 1.0 for n in d.nodes)
    assert all(abs(f) < 50 for f in d.falls)


def test_a_point_just_outside_an_edge_takes_the_edges_height_not_a_far_plane():
    # review, 12.10.2026: a level line point 10 micrometres inside the edge made a sliver whose plane gave -10 m
    d = {"level": 0.0,
         "polygonCoordinates": [{"x": 0, "y": 0, "z": 0.0}, {"x": 10, "y": 0, "z": 0.0},
                                {"x": 10, "y": 10, "z": 1.0}, {"x": 0, "y": 10, "z": 1.0}],
         "sublines": [{"coordinates": [{"x": 5, "y": 0.00001, "z": 0.3}, {"x": 5, "y": 5, "z": 0.5}]}]}
    s = dr.mesh_surface("m", d, story_level=0.0)
    for x in (2.5, 4.999, 5.0, 5.001, 7.5):
        assert -0.01 < s.height(x, -0.0009) < 0.31


def test_the_paint_never_dips_beside_a_kink_where_nodes_were_added():
    # review, 12.10.2026: a wide row along a kinked kerb on a crest sank 28.5 mm: nodes added beside the kink
    # change the part's own offset mapping, so the check must run on the marking's output path
    s = dr.SurfaceSet([Plane(lambda x, y: -0.0005 * (x - 10) ** 2)])
    d = dr.drape([(0, 0, 0), (30, 0, 0), (45, 25.98, 0)], [0.0, 0.0, 0.0], (0, 5.45), s)
    low, high = _gaps(d, s, (0, 5.45), step=0.05)
    assert low >= -1e-4
    assert d.max_across == pytest.approx(high, abs=0.002)
