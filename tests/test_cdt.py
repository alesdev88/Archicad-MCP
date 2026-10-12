"""The constrained Delaunay triangulation behind Mesh draping (Archicad's own rule)."""
import random

import pytest

from archicad_mcp.core.cdt import CDT, in_circle


def _edges(cdt):
    return {frozenset(e) for t in cdt.triangles() for e in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0]))}


def _area(pts, t):
    a, b, c = (pts[i] for i in t)
    return ((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2


def test_a_square_with_a_point_inside_makes_four_triangles_covering_it():
    pts = [(0, 0), (2, 0), (2, 2), (0, 2), (1, 1.1)]
    tris = CDT(pts).triangles()
    assert len(tris) == 4
    assert all(_area(pts, t) > 0 for t in tris)
    assert sum(_area(pts, t) for t in tris) == pytest.approx(4.0)


def test_random_points_are_delaunay():
    rnd = random.Random(3)
    pts = [(rnd.uniform(0, 50), rnd.uniform(0, 20)) for _ in range(300)]
    cdt = CDT(pts)
    for t in cdt.triangles():
        a, b, c = (cdt.p[i] for i in t)
        for j in range(len(pts)):
            if j not in t:
                assert in_circle(a, b, c, cdt.p[j]) <= 1e-9


def test_a_constraint_becomes_an_edge_where_delaunay_would_not_have_it():
    # the trial's crowned road: (595, 0)-(595, 8) is the Delaunay diagonal, the ridge is a level line
    pts = [(590, 4), (600, 4), (595, 0), (595, 8)]
    cdt = CDT(pts)
    assert frozenset((2, 3)) in _edges(cdt)
    assert cdt.add_constraint(0, 1)
    assert frozenset((0, 1)) in _edges(cdt) and frozenset((2, 3)) not in _edges(cdt)


def test_a_constraint_through_a_point_is_split_there():
    pts = [(0, 0), (10, 0), (5, 0), (5, 3), (5, -3), (2, 2), (8, -2)]
    cdt = CDT(pts)
    assert cdt.add_constraint(0, 1)
    assert {frozenset((0, 2)), frozenset((2, 1))} <= _edges(cdt)


def test_a_constraint_crossing_an_earlier_one_is_left_out_without_new_points():
    pts = [(0, 0), (10, 0), (10, 10), (0, 10), (2, 2), (8, 8), (2, 8), (8, 2)]
    cdt = CDT(pts)
    assert cdt.add_constraint(4, 5)
    assert not cdt.add_constraint(6, 7)
    assert frozenset((4, 5)) in _edges(cdt) and frozenset((6, 7)) not in _edges(cdt)
    assert len(cdt.triangles()) == 2 * 8 - 4 - 2      # 2n - h - 2 triangles: no point was added


def test_a_repeated_point_is_one_vertex():
    pts = [(0, 0), (4, 0), (4, 4), (0, 4), (4, 0)]
    cdt = CDT(pts)
    assert len(cdt.triangles()) == 2
    assert cdt.add_constraint(4, 2)


def test_a_regular_grid_triangulates_and_tiles_the_square():
    # every cell's corners lie on one circle: the diagonal is a free choice and the flips must not loop
    pts = [(x, y) for y in range(6) for x in range(6)]
    cdt = CDT(pts)
    for row in range(6):
        for col in range(5):
            assert cdt.add_constraint(row * 6 + col, row * 6 + col + 1)
    tris = cdt.triangles()
    assert len(tris) == 50
    assert sum(_area(pts, t) for t in tris) == pytest.approx(25.0)


def test_a_long_straight_constraint_through_many_points_does_not_overflow():
    # review, 12.10.2026: one recursion per point on the line overflowed past about 1000 points
    pts = [(float(x), 0.0) for x in range(1201)] + [(600.0, 50.0), (600.0, -50.0)]
    cdt = CDT(pts)
    assert cdt.add_constraint(0, 1200)
    assert all(frozenset((i, i + 1)) in _edges(cdt) for i in range(1200))


def test_constraints_along_a_turned_row_of_points_leave_no_flat_triangles():
    # review, 12.10.2026: a fan vertex lying on the constraint, its orientation a rounding error above zero,
    # was taken as straddling it, and the constraint ran past it leaving zero-area triangles
    import math
    c, s = math.cos(math.radians(1.0)), math.sin(math.radians(1.0))

    def at(x, y):
        return (591.237 + x * c - y * s, 63.418 + x * s + y * c)

    edge = [at(x, 0) for x in range(31)]                              # an outline edge with a point every metre
    pts = edge + [at(30, 8), at(0, 8)] + [at(x, 4) for x in range(0, 31, 2)]
    cdt = CDT(pts)
    ring = list(range(31)) + [31, 32]
    for i in range(len(ring)):
        assert cdt.add_constraint(ring[i], ring[(i + 1) % len(ring)])
    for i in range(30):                                               # the level line along the same edge
        assert cdt.add_constraint(i, i + 1)
    for t in cdt.triangles():
        a, b, c3 = (pts[i] for i in t)
        longest = max(math.dist(a, b), math.dist(b, c3), math.dist(c3, a))
        assert abs(_area(pts, t)) > 1e-9 * longest ** 2
