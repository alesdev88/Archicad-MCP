"""Geometry behind create_swept_beam."""

import json
import math
from pathlib import Path

import pytest

from archicad_mcp.core import swept_path as sp

FIXTURE = Path(__file__).parent / "fixtures" / "swept_beam" / "morph_line_mcp_test.json"


def _helix(radius, sweep_deg, rise, n, ccw=True):
    sign = 1 if ccw else -1
    return [(radius * math.cos(math.radians(sweep_deg * k / (n - 1)) * sign),
             radius * math.sin(math.radians(sweep_deg * k / (n - 1)) * sign),
             rise * k / (n - 1)) for k in range(n)]


def test_arc_geometry_quarter_turn_left():
    cx, cy, r = sp.arc_geometry((0, 0, 0), (2, 2, 0), 90.0)
    assert (round(cx, 9), round(cy, 9), round(r, 9)) == (0, 2, 2)


def test_major_arc_centre_is_on_the_right():
    cx, cy, r = sp.arc_geometry((0, 0, 0), (2, 0, 0), 270.0)
    assert round(cx, 9) == 1 and cy < 0 and round(r, 6) == round(1 / math.sin(math.radians(135)), 6)


def test_helix_fits_one_arc_left():
    path = sp.fit_points(_helix(5.0, 90.0, 1.0, 46), 0.002)
    assert len(path.nodes) == 2
    assert path.arcs[0] == pytest.approx(90.0, abs=1e-6) and path.arcs[1] == 0.0
    assert path.nodes[-1] == pytest.approx((0.0, 5.0, 1.0))
    # the arc bulges 0.76 mm past each 2 degree chord of the sampled helix
    assert path.max_deviation == pytest.approx(5 * (1 - math.cos(math.radians(1))), abs=1e-6)


def test_helix_fits_one_arc_right():
    path = sp.fit_points(_helix(5.0, 90.0, 1.0, 46, ccw=False), 0.002)
    assert path.arcs[0] == pytest.approx(-90.0, abs=1e-6)


def test_straight_rise_is_one_segment():
    pts = [(k * 0.5, 0.0, k * 0.05) for k in range(21)]
    path = sp.fit_points(pts, 0.002)
    assert len(path.nodes) == 2 and path.arcs == [0.0, 0.0]


def test_bent_rise_splits():
    pts = [(k * 0.5, 0.0, min(k, 20 - k) * 0.1) for k in range(21)]
    assert len(sp.fit_points(pts, 0.002).nodes) >= 3


def test_corner_points_stay_straight():
    # any three points lie on a circle; an arc through corners would miss the
    # straight edges between them by metres while every point fits
    path = sp.fit_points([(0, 0, 0), (3, 0, 0), (3, 4, 0)], 0.002)
    assert len(path.nodes) == 3 and path.arcs == [0.0, 0.0, 0.0]
    rectangle = sp.fit_points([(0, 0, 0), (3, 0, 0), (3, 4, 0), (0, 4, 0)], 0.002)
    assert len(rectangle.nodes) == 4 and rectangle.arc_count == 0


def test_duplicates_are_dropped():
    pts = [(0, 0, 0), (0, 0, 0), (1, 0, 0), (1, 0.0000001, 0), (2, 0, 0)]
    assert sp.dedupe(pts) == [(0, 0, 0), (1, 0, 0), (2, 0, 0)]


def test_fewer_than_two_distinct_points():
    with pytest.raises(sp.PathError, match="two distinct points"):
        sp.fit_points([(1, 1, 1), (1, 1, 1)], 0.002)


def test_chain_orders_from_an_end():
    assert sp.chain_wire_edges([(2, 1), (0, 1), (3, 2)]) in ([0, 1, 2, 3], [3, 2, 1, 0])


def test_chain_refuses_branch_loop_and_pieces():
    with pytest.raises(sp.PathError, match="branches"):
        sp.chain_wire_edges([(0, 1), (1, 2), (1, 3)])
    with pytest.raises(sp.PathError, match="closed loop"):
        sp.chain_wire_edges([(0, 1), (1, 2), (2, 0)])
    with pytest.raises(sp.PathError, match="separate piece"):
        sp.chain_wire_edges([(0, 1), (2, 3)])


def test_mcp_test_morph_line_fits_within_2mm():
    raw = json.loads(FIXTURE.read_text())
    edges = [tuple(e["vertexIds"]) for e in raw["wireEdges"]]
    order = sp.chain_wire_edges(edges)
    o = raw["origin"]
    pts = [(raw["vertices"][i]["x"] + o["x"], raw["vertices"][i]["y"] + o["y"],
            raw["vertices"][i]["z"] + o["z"]) for i in order]
    path = sp.fit_points(pts, 0.002)
    assert path.max_deviation <= 0.002
    assert path.nodes[0] == pytest.approx(pts[0]) and path.nodes[-1] == pytest.approx(pts[-1])
    assert len(path.arcs) == len(path.nodes) and path.arcs[-1] == 0.0


def test_apply_slope_along_plan_length():
    nodes = [(0, 0, 9), (2, 2, 9), (2, 12, 9)]
    out = sp.apply_slope(nodes, [90.0, 0.0, 0.0], 1.0, 10.0)
    assert out[0][2] == 1.0
    assert out[1][2] == pytest.approx(1.0 + 0.1 * math.pi)
    assert out[2][2] == pytest.approx(1.0 + 0.1 * (math.pi + 10))


def test_steepest_slope():
    path = sp.SweptPath([(0, 0, 0), (1, 0, 1)], [0.0, 0.0])
    assert path.steepest_slope_deg() == pytest.approx(45.0)
