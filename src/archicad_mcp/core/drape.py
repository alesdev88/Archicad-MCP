"""Draping a Road Marking onto Meshes and Slabs (create_road_marking).

Surfaces, as settled in MCP-Test on 12.10.2026 (findings):
- a Slab's top is zCoordinate + offsetFromTop, zCoordinate absolute;
- a Mesh's point z is relative: the story's level + level + z;
- a Mesh is the constrained Delaunay triangulation of its outline, hole and level line
  points with the level lines as edges (cdt.py), as Archicad builds it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from archicad_mcp.core.cdt import CDT

EDGE_TOL = 0.001      # a point this close to an outline counts as inside
CHORD_TOL = 0.001     # an outline arc becomes chords within this sagitta
ARC_EPS_DEG = 0.01    # smaller arc angles are straight (the part's rule)


class DrapeError(ValueError):
    """The marking cannot be draped."""


def _ring(coords) -> list[tuple[float, float, float]]:
    """An outline's points, without the repeated first point Tapir closes it with."""
    pts = [(float(c["x"]), float(c["y"]), float(c.get("z", 0.0))) for c in coords]
    if len(pts) > 1 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 1e-9:
        pts.pop()
    return pts


def _with_arcs(pts, arcs):
    """The outline with every arc edge replaced by chords. arcAngle is in radians, positive
    when the edge turns left (counterclockwise), as Tapir reports polygon arcs."""
    if not arcs:
        return pts
    n = len(pts)
    turn = {}
    for a in arcs:
        b, e = int(a["begIndex"]), int(a["endIndex"])
        if e == b + 1 or (b == n - 1 and e in (0, n)):
            turn[b % n] = float(a["arcAngle"])
    out = []
    for i in range(n):
        p, q = pts[i], pts[(i + 1) % n]
        out.append(p)
        ang = turn.get(i)
        if ang is None or abs(math.degrees(ang)) <= ARC_EPS_DEG:
            continue
        chord = math.hypot(q[0] - p[0], q[1] - p[1])
        half = ang / 2
        r = chord / (2 * abs(math.sin(half)))
        d = chord / 2 * math.cos(half) / math.sin(half)
        cx = (p[0] + q[0]) / 2 - (q[1] - p[1]) / chord * d
        cy = (p[1] + q[1]) / 2 + (q[0] - p[0]) / chord * d
        step = 2 * math.acos(1 - CHORD_TOL / r) if r > CHORD_TOL else abs(ang)
        k = max(1, math.ceil(abs(ang) / step))
        a0 = math.atan2(p[1] - cy, p[0] - cx)
        for j in range(1, k):
            t = j / k
            out.append((cx + r * math.cos(a0 + ang * t), cy + r * math.sin(a0 + ang * t),
                        p[2] + (q[2] - p[2]) * t))
    return out


def _in_ring(x, y, ring) -> bool:
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _seg_dist(x, y, a, b) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    ln2 = dx * dx + dy * dy
    t = 0.0 if ln2 < 1e-18 else max(0.0, min(1.0, ((x - a[0]) * dx + (y - a[1]) * dy) / ln2))
    return math.hypot(x - a[0] - t * dx, y - a[1] - t * dy)


def _ring_dist(x, y, ring) -> float:
    return min(_seg_dist(x, y, ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring)))


def _covers(x, y, outline, holes) -> bool:
    """Inside the outline and outside the holes, with EDGE_TOL on every edge."""
    if not _in_ring(x, y, outline) and _ring_dist(x, y, outline) > EDGE_TOL:
        return False
    return not any(_in_ring(x, y, h) and _ring_dist(x, y, h) > EDGE_TOL for h in holes)


def _box(ring):
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


@dataclass
class SlabSurface:
    guid: str
    top: float
    outline: list
    holes: list
    box: tuple

    def height(self, x, y):
        return self.top if _covers(x, y, self.outline, self.holes) else None


@dataclass
class MeshSurface:
    guid: str
    triangles: list            # ((x, y, z), (x, y, z), (x, y, z)), absolute z
    box: tuple
    dropped_lines: int = 0
    _grid: dict = field(default_factory=dict)
    _cell: float = 1.0

    def __post_init__(self):
        area = (self.box[2] - self.box[0]) * (self.box[3] - self.box[1])
        self._cell = max(0.25, math.sqrt(max(area, 1e-6) / max(1, len(self.triangles))) * 1.5)
        for k, t in enumerate(self.triangles):
            i0, i1 = (int(math.floor((v - EDGE_TOL * s) / self._cell)) for v, s in
                      ((min(p[0] for p in t), 1), (max(p[0] for p in t), -1)))
            j0, j1 = (int(math.floor((v - EDGE_TOL * s) / self._cell)) for v, s in
                      ((min(p[1] for p in t), 1), (max(p[1] for p in t), -1)))
            for i in range(i0, i1 + 1):
                for j in range(j0, j1 + 1):
                    self._grid.setdefault((i, j), []).append(k)

    def height(self, x, y):
        cell = (int(math.floor(x / self._cell)), int(math.floor(y / self._cell)))
        best, best_d = None, EDGE_TOL
        for k in self._grid.get(cell, ()):
            p, q, r = self.triangles[k]
            d = (q[1] - r[1]) * (p[0] - r[0]) + (r[0] - q[0]) * (p[1] - r[1])
            if abs(d) < 1e-15:
                continue
            l1 = ((q[1] - r[1]) * (x - r[0]) + (r[0] - q[0]) * (y - r[1])) / d
            l2 = ((r[1] - p[1]) * (x - r[0]) + (p[0] - r[0]) * (y - r[1])) / d
            l3 = 1 - l1 - l2
            if min(l1, l2, l3) >= -1e-9:
                return l1 * p[2] + l2 * q[2] + l3 * r[2]
            # just outside: the nearest triangle within EDGE_TOL, its plane extended
            dist = min(_seg_dist(x, y, p, q), _seg_dist(x, y, q, r), _seg_dist(x, y, r, p))
            if dist <= best_d:
                best, best_d = l1 * p[2] + l2 * q[2] + l3 * r[2], dist
        return best


def slab_surface(guid: str, d: dict) -> SlabSurface:
    outline = _with_arcs(_ring(d["polygonOutline"]), d.get("polygonArcs"))
    holes = [_with_arcs(_ring(h.get("polygonOutline") or h.get("polygonCoordinates")), h.get("polygonArcs"))
             for h in d.get("holes") or []]
    top = float(d["zCoordinate"]) + float(d.get("offsetFromTop", 0.0))
    return SlabSurface(guid, top, outline, holes, _box(outline))


def mesh_surface(guid: str, d: dict, story_level: float) -> MeshSurface:
    base = story_level + float(d["level"])

    def absolute(pts):
        return [(x, y, z + base) for x, y, z in pts]

    outline = absolute(_with_arcs(_ring(d["polygonCoordinates"]), d.get("polygonArcs")))
    holes = [absolute(_with_arcs(_ring(h["polygonCoordinates"]), h.get("polygonArcs")))
             for h in d.get("holes") or []]
    lines = [[(float(c["x"]), float(c["y"]), float(c["z"]) + base) for c in s["coordinates"]]
             for s in d.get("sublines") or []]
    index, pts = {}, []

    def vertex(p):
        key = (round(p[0], 6), round(p[1], 6))       # one point per plan position, within a micrometre
        if key not in index:
            index[key] = len(pts)
            pts.append(p)
        return index[key]

    rings = [[vertex(p) for p in r] for r in [outline] + holes]
    paths = [[vertex(p) for p in s] for s in lines]
    if len(pts) < 3:
        raise DrapeError(f"Mesh {guid} has fewer than three distinct points.")
    cdt = CDT([(p[0], p[1]) for p in pts])
    for r in rings:
        for i in range(len(r)):
            cdt.add_constraint(r[i], r[(i + 1) % len(r)])
    dropped = sum(1 for s in paths for i in range(len(s) - 1) if not cdt.add_constraint(s[i], s[i + 1]))
    tris = []
    for a, b, c in cdt.triangles():
        cx = (pts[a][0] + pts[b][0] + pts[c][0]) / 3
        cy = (pts[a][1] + pts[b][1] + pts[c][1]) / 3
        if _in_ring(cx, cy, outline) and not any(_in_ring(cx, cy, h) for h in holes):
            tris.append((pts[a], pts[b], pts[c]))
    return MeshSurface(guid, tris, _box(outline), dropped)


@dataclass
class SurfaceSet:
    surfaces: list

    def height(self, x, y):
        """The highest surface containing the point, or None."""
        best = None
        for s in self.surfaces:
            b = s.box
            if b[0] - EDGE_TOL <= x <= b[2] + EDGE_TOL and b[1] - EDGE_TOL <= y <= b[3] + EDGE_TOL:
                h = s.height(x, y)
                if h is not None and (best is None or h > best):
                    best = h
        return best


def read_surfaces(conn, guids) -> SurfaceSet:
    """The drape_on elements as one SurfaceSet; a GUID given twice is read once."""
    unique = list(dict.fromkeys(str(g) for g in guids))
    if not unique:
        raise DrapeError("drape_on lists no elements.")
    res = conn.tapir("GetDetailsOfElements", {"elements": [{"elementId": {"guid": g}} for g in unique]})
    levels, out = None, []
    for g, e in zip(unique, res.get("detailsOfElements") or []):
        if "error" in e or "type" not in e:
            raise DrapeError(f"Could not read drape_on element {g}: {e.get('error', e)}")
        if e["type"] == "Slab":
            out.append(slab_surface(g, e["details"]))
        elif e["type"] == "Mesh":
            if levels is None:
                levels = {int(s["index"]): float(s["level"]) for s in conn.tapir("GetStories").get("stories", [])}
            out.append(mesh_surface(g, e["details"], levels.get(int(e.get("floorIndex", 0)), 0.0)))
        else:
            raise DrapeError(f"drape_on element {g} is a {e['type']}; a marking drapes onto Meshes and Slabs only.")
    return SurfaceSet(out)
