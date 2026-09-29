"""Geometry for create_swept_beam: a source curve becomes Swept Beam nodes.

A Swept Beam segment is straight or a plan arc, and its height changes
linearly along the plan length (spec 1.1). Sources arrive as ordered 3D
points (a morph line's wire edges, points from a DWG or Rhino curve) or as a
polyline's own segments. Points are fitted greedily: a piece grows while every
point in it stays within the tolerance of one line or one plan circle and of
one straight rise, the method that fitted the Stanezice rails to 2.1 mm.

Arc angles are degrees and positive when the path turns left
(counter-clockwise), the Swept Beam's segArc convention.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Point = tuple[float, float, float]
DUPLICATE_EPS = 0.001
ARC_EPS_DEG = 0.01  # the GDL treats smaller arc angles as straight


class PathError(ValueError):
    """The source cannot become one Swept Beam path."""


def arc_geometry(p1, p2, arc_deg: float) -> tuple[float, float, float]:
    """Centre x, y and radius of a segment's plan arc."""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    chord = math.hypot(dx, dy)
    half = math.radians(arc_deg) / 2
    radius = chord / (2 * abs(math.sin(half)))
    d = chord / 2 * math.cos(half) / math.sin(half)
    return ((p1[0] + p2[0]) / 2 - dy / chord * d,
            (p1[1] + p2[1]) / 2 + dx / chord * d,
            radius)


def segment_plan_length(p1, p2, arc_deg: float) -> float:
    chord = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    if abs(arc_deg) <= ARC_EPS_DEG or chord < 1e-12:
        return chord
    return arc_geometry(p1, p2, arc_deg)[2] * abs(math.radians(arc_deg))


@dataclass
class SweptPath:
    nodes: list[Point]
    arcs: list[float]
    max_deviation: float = 0.0

    @property
    def arc_count(self) -> int:
        return sum(1 for a in self.arcs[:-1] if abs(a) > ARC_EPS_DEG)

    @property
    def straight_count(self) -> int:
        return len(self.nodes) - 1 - self.arc_count

    def segment_lengths(self) -> list[float]:
        return [segment_plan_length(self.nodes[i], self.nodes[i + 1], self.arcs[i])
                for i in range(len(self.nodes) - 1)]

    def steepest_slope_deg(self) -> float:
        worst = 0.0
        for i, length in enumerate(self.segment_lengths()):
            rise = abs(self.nodes[i + 1][2] - self.nodes[i][2])
            angle = 90.0 if length < 1e-9 else math.degrees(math.atan2(rise, length))
            worst = max(worst, angle)
        return worst


def chain_wire_edges(edges: list[tuple[int, int]]) -> list[int]:
    """Vertex order of one open, unbranched line of edges."""
    adj: dict[int, list[int]] = {}
    for a, b in edges:
        if a != b:
            adj.setdefault(a, []).append(b)
            adj.setdefault(b, []).append(a)
    if not adj:
        raise PathError("The morph has no wire edges. Draw the guide as a morph line.")
    branch = sorted(v for v, near in adj.items() if len(near) > 2)
    if branch:
        raise PathError(f"The morph line branches at vertex {branch[0]}. "
                        "A Swept Beam follows one unbranched line.")
    ends = sorted(v for v, near in adj.items() if len(near) == 1)
    if not ends:
        raise PathError("The morph line is a closed loop. A Swept Beam needs a start and an end.")
    order, prev = [ends[0]], None
    while True:
        nxt = [v for v in adj[order[-1]] if v != prev]
        if not nxt:
            break
        prev = order[-1]
        order.append(nxt[0])
    if len(order) != len(adj):
        raise PathError("The morph line has more than one separate piece. "
                        "Use a morph with one continuous line.")
    return order


def dedupe(points) -> list[Point]:
    out: list[Point] = []
    for p in points:
        q = (float(p[0]), float(p[1]), float(p[2]))
        if not out or math.dist(out[-1], q) >= DUPLICATE_EPS:
            out.append(q)
    return out


def _rise_deviation(pts: list[Point], s_vals: list[float]) -> list[float]:
    total = s_vals[-1]
    z0, z1 = pts[0][2], pts[-1][2]
    return [abs(p[2] - (z0 + (z1 - z0) * (s / total if total > 1e-12 else 0.0)))
            for p, s in zip(pts, s_vals)]


def _fit_line(pts: list[Point], tol: float) -> tuple[float, float] | None:
    a, b = pts[0], pts[-1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    chord = math.hypot(dx, dy)
    if chord < 1e-9:
        return None
    ux, uy = dx / chord, dy / chord
    s_vals, off = [], []
    for p in pts:
        px, py = p[0] - a[0], p[1] - a[1]
        s_vals.append(px * ux + py * uy)
        off.append(abs(px * uy - py * ux))
    if max(off) > tol or any(s2 < s1 - tol for s1, s2 in zip(s_vals, s_vals[1:])):
        return None
    dz = _rise_deviation(pts, s_vals)
    if max(dz) > tol:
        return None
    return 0.0, max(math.hypot(o, z) for o, z in zip(off, dz))


def _circle(a, m, b) -> tuple[float, float, float] | None:
    ax, ay, bx, by, cx, cy = a[0], a[1], m[0], m[1], b[0], b[1]
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay)
          + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx)
          + (cx * cx + cy * cy) * (bx - ax)) / d
    return ux, uy, math.hypot(ax - ux, ay - uy)


def _fit_arc(pts: list[Point], tol: float) -> tuple[float, float] | None:
    a, m, b = pts[0], pts[len(pts) // 2], pts[-1]
    circle = _circle(a, m, b)
    if circle is None:
        return None
    cx, cy, r = circle
    ccw = (m[0] - a[0]) * (b[1] - m[1]) - (m[1] - a[1]) * (b[0] - m[0]) > 0
    start = math.atan2(a[1] - cy, a[0] - cx)
    progress, radial = [], []
    for p in pts:
        d = (math.atan2(p[1] - cy, p[0] - cx) - start) % (2 * math.pi)
        if not ccw and d > 0:
            d -= 2 * math.pi
        progress.append(d)
        radial.append(abs(math.hypot(p[0] - cx, p[1] - cy) - r))
    if max(radial) > tol:
        return None
    steps = [q - p for p, q in zip(progress, progress[1:])]
    if (ccw and any(s < -1e-9 for s in steps)) or (not ccw and any(s > 1e-9 for s in steps)):
        return None
    # The source runs straight between its points (morph edges, polyline
    # vertices), so the arc bulges past each chord by r (1 - cos (step / 2)).
    # Three points always lie on a circle; this keeps corners straight.
    bulge = max(r * (1 - math.cos(abs(s) / 2)) for s in steps)
    if bulge > tol:
        return None
    dz = _rise_deviation(pts, [r * abs(t) for t in progress])
    if max(dz) > tol:
        return None
    return math.degrees(progress[-1]), max(bulge, max(math.hypot(o, z) for o, z in zip(radial, dz)))


def fit_points(points, tol: float) -> SweptPath:
    pts = dedupe(points)
    if len(pts) < 2:
        raise PathError("The source has fewer than two distinct points.")
    nodes, arcs, worst = [pts[0]], [], 0.0
    s, last = 0, len(pts) - 1
    while s < last:
        end, arc, dev = s + 1, 0.0, 0.0
        e = s + 2
        while e <= last:
            piece = pts[s:e + 1]
            fit = _fit_line(piece, tol) or _fit_arc(piece, tol)
            if fit is None:
                break
            end, (arc, dev) = e, fit
            e += 1
        nodes.append(pts[end])
        arcs.append(arc)
        worst = max(worst, dev)
        s = end
    arcs.append(0.0)
    return SweptPath(nodes, arcs, worst)


def apply_slope(nodes, arcs, start_z: float, slope_percent: float) -> list[Point]:
    out = [(nodes[0][0], nodes[0][1], start_z)]
    acc = 0.0
    for i in range(1, len(nodes)):
        acc += segment_plan_length(nodes[i - 1], nodes[i], arcs[i - 1])
        out.append((nodes[i][0], nodes[i][1], start_z + slope_percent / 100 * acc))
    return out
