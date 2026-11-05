import math

import pytest

from archicad_mcp.core import drape as dr
from archicad_mcp.core import road_arrow as ra


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
    assert not ra.place_on_axis(path, 2.0005, "tail", 0.0001, 0.0).past_end      # within 1 mm
    assert ra.place_on_axis(path, 2.002, "tail", 0.0001, 0.0).past_end
