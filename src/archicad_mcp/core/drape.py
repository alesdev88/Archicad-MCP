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


# ---------------------------------------------------------------- the part's path (Road Marking Master Script)

class PlanPath:
    """The plan path as the Road Marking part samples it (arcs within the curve tolerance),
    with the station of each node and the mitre offset vectors (parent spec, Master Script
    steps 1 and 7): a point (s, u) maps to the path point plus u times the offset vector,
    both linear between samples."""

    def __init__(self, nodes, arcs_deg, tol):
        X, Y, node_k = [], [], []
        tol = max(0.0001, tol)
        for i in range(len(nodes) - 1):
            (x1, y1), (x2, y2) = nodes[i][:2], nodes[i + 1][:2]
            dxs, dys = x2 - x1, y2 - y1
            chord = math.hypot(dxs, dys)
            ang = arcs_deg[i]
            is_arc = 0.01 < abs(ang) < 359.9 and chord > 0.0001
            n_step = 1
            if is_arc:
                rad = chord / (2 * math.sin(math.radians(abs(ang) / 2)))
                step_max = 10.0
                if tol < rad:
                    step_max = 2 * math.degrees(math.acos(1 - tol / rad))
                n_step = max(1, math.ceil(abs(ang) / max(0.5, step_max)))
                d_cen = chord / 2 * math.cos(math.radians(ang / 2)) / math.sin(math.radians(ang / 2))
                cx = (x1 + x2) / 2 - dys / chord * d_cen
                cy = (y1 + y2) / 2 + dxs / chord * d_cen
                vx, vy = x1 - cx, y1 - cy
            n_step = min(n_step, 720)
            for k in range(n_step + 1):
                t = k / n_step
                if is_arc:
                    th = math.radians(ang * t)
                    px = cx + vx * math.cos(th) - vy * math.sin(th)
                    py = cy + vx * math.sin(th) + vy * math.cos(th)
                else:
                    px, py = x1 + dxs * t, y1 + dys * t
                if X and math.hypot(px - X[-1], py - Y[-1]) < 0.001:
                    if k == 0:
                        node_k.append(len(X) - 1)
                    continue
                if k == 0:
                    node_k.append(len(X))
                X.append(px)
                Y.append(py)
        node_k.append(len(X) - 1)
        self.x, self.y = X, Y
        self.s = [0.0]
        for i in range(1, len(X)):
            self.s.append(self.s[-1] + math.hypot(X[i] - X[i - 1], Y[i] - Y[i - 1]))
        self.node_s = [self.s[k] for k in node_k]
        n = len(X)
        self.mx, self.my = [], []
        for i in range(n):
            if i == 0 or i == n - 1:
                tx, ty = self._piece(0 if i == 0 else n - 2)
                self.mx.append(ty)
                self.my.append(-tx)
            else:
                d1, d2 = self._piece(i - 1), self._piece(i)
                den = max(0.1, 1 + d1[0] * d2[0] + d1[1] * d2[1])
                self.mx.append((d1[1] + d2[1]) / den)
                self.my.append(-(d1[0] + d2[0]) / den)

    @property
    def length(self) -> float:
        return self.s[-1]

    def _piece(self, i):
        dx, dy = self.x[i + 1] - self.x[i], self.y[i + 1] - self.y[i]
        ln = math.hypot(dx, dy)
        return dx / ln, dy / ln

    def xy(self, s, u):
        lo, hi = 0, len(self.s) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.s[mid] <= s:
                lo = mid
            else:
                hi = mid
        t = min(1.0, max(0.0, (s - self.s[lo]) / (self.s[lo + 1] - self.s[lo])))
        return (self.x[lo] + (self.x[lo + 1] - self.x[lo]) * t
                + u * (self.mx[lo] + (self.mx[lo + 1] - self.mx[lo]) * t),
                self.y[lo] + (self.y[lo + 1] - self.y[lo]) * t
                + u * (self.my[lo] + (self.my[lo + 1] - self.my[lo]) * t))


# ---------------------------------------------------------------- the drape fit

@dataclass
class Drape:
    nodes: list            # (x, y, z): z the marking's underside on the path, with lift
    falls: list            # percent, positive when the right side is higher
    arcs: list             # degrees
    added: int
    max_along: float
    max_across: float
    off_surface: list
    partly_off_surface: list
    warnings: list


def _lateral(reach, step):
    """Sample offsets across the marking: both ends of the reach, 0, and every step between."""
    lo, hi = min(reach[0], 0.0), max(reach[1], 0.0)
    n = max(1, math.ceil((hi - lo) / step - 1e-9))
    return sorted({round(lo + (hi - lo) * k / n, 6) for k in range(n + 1)} | {0.0})


def _stretches(stations, flags):
    out, start, prev = [], None, None
    for s, f in zip(stations, flags):
        if f and start is None:
            start = s
        if not f and start is not None:
            out.append({"from": round(start, 2), "to": round(prev, 2)})
            start = None
        prev = s
    if start is not None:
        out.append({"from": round(start, 2), "to": round(stations[-1], 2)})
    return out


def _on_segment(p1, p2, ang, t):
    """The plan point a fraction t along a segment (along the arc for an arc)."""
    if abs(ang) <= ARC_EPS_DEG:
        return (p1[0] + (p2[0] - p1[0]) * t, p1[1] + (p2[1] - p1[1]) * t)
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    chord = math.hypot(dx, dy)
    d_cen = chord / 2 * math.cos(math.radians(ang / 2)) / math.sin(math.radians(ang / 2))
    cx = (p1[0] + p2[0]) / 2 - dy / chord * d_cen
    cy = (p1[1] + p2[1]) / 2 + dx / chord * d_cen
    th = math.radians(ang * t)
    vx, vy = p1[0] - cx, p1[1] - cy
    return (cx + vx * math.cos(th) - vy * math.sin(th), cy + vx * math.sin(th) + vy * math.cos(th))


def drape(nodes, arcs_deg, reach, surfaces, tol=0.005, lift=0.0, path_tol=0.002,
          step=0.1, lateral_step=0.5, max_nodes=500) -> Drape:
    """Heights and cross falls for the marking's nodes, and the nodes the surface needs.

    Every `step` along the path the surface is sampled at `lateral_step` across the
    marking's reach; a least squares line gives the cross fall and is raised until no
    sample lies above it, so the paint never sinks. Douglas and Peucker on each segment
    adds a node where the marking's linear heights would leave those lines by more than
    `tol`."""
    path = PlanPath(nodes, arcs_deg, path_tol)
    us = _lateral(reach, lateral_step)
    st, node_idx = [], []
    for i in range(len(nodes) - 1):
        s0, s1 = path.node_s[i], path.node_s[i + 1]
        m = max(1, math.ceil((s1 - s0) / step - 1e-9))
        if not st:
            st.append(s0)
            node_idx.append(0)
        st += [s0 + (s1 - s0) * k / m for k in range(1, m + 1)]
        node_idx.append(len(st) - 1)
    z0, f, res, valid = [], [], [], []
    for s in st:
        ok = [(u, h) for u in us if (h := surfaces.height(*path.xy(s, u))) is not None]
        valid.append(len(ok))
        if len(ok) >= 2:
            mu = sum(u for u, _ in ok) / len(ok)
            mh = sum(h for _, h in ok) / len(ok)
            suu = sum((u - mu) ** 2 for u, _ in ok)
            b = sum((u - mu) * (h - mh) for u, h in ok) / suu if suu > 1e-12 else 0.0
            a = mh - b * mu
            a += max(h - (a + b * u) for u, h in ok)      # never under the surface
            z0.append(a)
            f.append(b * 100)
            res.append(max(a + b * u - h for u, h in ok))
        else:
            z0.append(ok[0] if ok else None)              # one sample: resolved below
            f.append(None)
            res.append(0.0)
    if not any(valid):
        raise DrapeError("No surface in drape_on lies under the marking anywhere along its path.")
    two = [j for j, n in enumerate(valid) if n >= 2]
    for j, n in enumerate(valid):
        if n == 1:
            u1, h1 = z0[j]
            fall = f[min(two, key=lambda k: abs(st[k] - st[j]))] if two else 0.0
            z0[j], f[j] = h1 - u1 * fall / 100, fall
    some = [j for j, n in enumerate(valid) if n >= 1]
    for j, n in enumerate(valid):
        if n == 0:
            k = min(some, key=lambda k: abs(st[k] - st[j]))
            z0[j], f[j] = z0[k], f[k]
    u_lo, u_hi = us[0], us[-1]

    def err(j, i0, i1):
        t = (st[j] - st[i0]) / (st[i1] - st[i0])
        dz = z0[i0] + (z0[i1] - z0[i0]) * t - z0[j]
        df = (f[i0] + (f[i1] - f[i0]) * t - f[j]) / 100
        return max(abs(dz + u_lo * df), abs(dz + u_hi * df))

    keep = set(node_idx)
    todo = list(zip(node_idx, node_idx[1:]))
    while todo:
        i0, i1 = todo.pop()
        worst, wj = 0.0, None
        for j in range(i0 + 1, i1):
            e = err(j, i0, i1)
            if e > worst:
                worst, wj = e, j
        if wj is not None and worst > tol:
            keep.add(wj)
            todo += [(i0, wj), (wj, i1)]
    kept = sorted(keep)
    if len(kept) > max_nodes:
        per = [(sum(1 for j in kept if a < j < b), st[a], st[b]) for a, b in zip(node_idx, node_idx[1:])]
        most, s_a, s_b = max(per)
        raise DrapeError(f"Draping needs {len(kept)} nodes, more than {max_nodes}; {most} of them between "
                         f"stations {s_a:.1f} and {s_b:.1f} m. Use a coarser drape_tolerance or a shorter source.")
    max_along = max((err(j, a, b) for a, b in zip(kept, kept[1:]) for j in range(a + 1, b)), default=0.0)
    out_nodes, out_arcs, out_falls = [], [], []
    for seg in range(len(nodes) - 1):
        a, b = node_idx[seg], node_idx[seg + 1]
        inner = [j for j in kept if a <= j < b]
        ang = arcs_deg[seg]
        fracs = [(st[j] - st[a]) / (st[b] - st[a]) for j in inner] + [1.0]
        for j, t0, t1 in zip(inner, fracs, fracs[1:]):
            out_nodes.append(_on_segment(nodes[seg], nodes[seg + 1], ang, t0) + (z0[j] + lift,))
            out_falls.append(f[j])
            out_arcs.append(ang * (t1 - t0) if abs(ang) > ARC_EPS_DEG else 0.0)
    out_nodes.append(tuple(nodes[-1][:2]) + (z0[node_idx[-1]] + lift,))
    out_falls.append(f[node_idx[-1]])
    out_arcs.append(0.0)
    warnings = []
    max_across = max(res)
    if max_across > tol:
        bad = [s for s, r in zip(st, res) if r > tol]
        warnings.append(f"The surface is not flat across the marking: the paint floats up to "
                        f"{max_across * 1000:.0f} mm above it between stations {bad[0]:.1f} and {bad[-1]:.1f} m.")
    dropped = sum(getattr(s, "dropped_lines", 0) for s in surfaces.surfaces)
    if dropped:
        warnings.append(f"{dropped} Mesh level line segments cross earlier ones and were left out, as Archicad "
                        "leaves them out; heights near them can differ from Archicad's surface.")
    return Drape(out_nodes, out_falls, out_arcs, len(kept) - len(node_idx), max_along, max_across,
                 _stretches(st, [n == 0 for n in valid]),
                 _stretches(st, [0 < n < len(us) for n in valid]), warnings)
