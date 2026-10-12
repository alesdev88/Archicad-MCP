"""Constrained Delaunay triangulation, pure Python, for draping onto Meshes.

Archicad triangulates a Mesh as a constrained Delaunay triangulation of its outline,
hole and level line points, with the level lines as edges, and leaves out a level line
segment that crosses an earlier edge without adding points (trial, 12.10.2026: 0.0 mm
from Archicad's own triangles read from an IFC export). This does the same: Delaunay by
incremental insertion with Lawson flips, then each constraint, in the order given, by
Sloan's edge flipping (1993).

Triangles are counterclockwise vertex triples. `edge` maps each directed edge to the
triangle that owns it, so the neighbour across (a, b) is the owner of (b, a).
"""

from __future__ import annotations

from collections import deque

EPS = 1e-12          # orientation tolerance, in coordinates normalised to about -1 to 1


def orient(a, b, c) -> float:
    """Twice the signed area of the triangle a, b, c: positive when counterclockwise."""
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def in_circle(a, b, c, d) -> float:
    """Positive when d lies inside the circle through the counterclockwise a, b, c."""
    adx, ady = a[0] - d[0], a[1] - d[1]
    bdx, bdy = b[0] - d[0], b[1] - d[1]
    cdx, cdy = c[0] - d[0], c[1] - d[1]
    return ((adx * adx + ady * ady) * (bdx * cdy - cdx * bdy)
            - (bdx * bdx + bdy * bdy) * (adx * cdy - cdx * ady)
            + (cdx * cdx + cdy * cdy) * (adx * bdy - bdx * ady))


def _crosses(a, b, c, d) -> bool:
    """The open segments ab and cd cross."""
    return orient(a, b, c) * orient(a, b, d) < 0 and orient(c, d, a) * orient(c, d, b) < 0


def _ahead(a, b, u) -> bool:
    """u, on the line ab, lies between a and b."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = (u[0] - a[0]) * dx + (u[1] - a[1]) * dy
    return 0 < t < dx * dx + dy * dy


class CDT:
    """A constrained Delaunay triangulation.

    CDT(points) triangulates distinct (x, y) points; add_constraint(a, b) then makes a-b
    an edge; triangles() lists the counterclockwise triples of point indices."""

    def __init__(self, points):
        if len(points) < 3:
            raise ValueError("A triangulation needs at least three points.")
        xs, ys = [float(p[0]) for p in points], [float(p[1]) for p in points]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        sc = max(max(xs) - min(xs), max(ys) - min(ys), 1e-9) / 2
        self.p = [((x - cx) / sc, (y - cy) / sc) for x, y in zip(xs, ys)]
        self.n = len(self.p)
        self.p += [(-1e3, -1e3), (1e3, -1e3), (0.0, 1e3)]      # the enclosing triangle
        self.tri: dict[int, tuple[int, int, int]] = {}
        self.edge: dict[tuple[int, int], int] = {}
        self.vtri: dict[int, int] = {}
        self.fixed: set[frozenset] = set()
        self.alias: dict[int, int] = {}
        self._next = 0
        self.last = self._add(self.n, self.n + 1, self.n + 2)
        # rows of 1/8 of the extent, every other row reversed: each point lands near the last one
        rows = {i: round(self.p[i][1] * 8) for i in range(self.n)}
        order = sorted(range(self.n),
                       key=lambda i: (rows[i], self.p[i][0] if rows[i] % 2 == 0 else -self.p[i][0]))
        for i in order:
            self._insert(i)

    def triangles(self) -> list[tuple[int, int, int]]:
        return [t for t in self.tri.values() if max(t) < self.n]

    # ---------------------------------------------------------------- bookkeeping

    def _add(self, a, b, c) -> int:
        t = self._next
        self._next += 1
        self.tri[t] = (a, b, c)
        self.edge[(a, b)] = self.edge[(b, c)] = self.edge[(c, a)] = t
        self.vtri[a] = self.vtri[b] = self.vtri[c] = t
        return t

    def _remove(self, t):
        a, b, c = self.tri.pop(t)
        for e in ((a, b), (b, c), (c, a)):
            if self.edge.get(e) == t:
                del self.edge[e]

    @staticmethod
    def _rot(t, v):
        """The triangle's vertices starting at v."""
        a, b, c = t
        return (a, b, c) if a == v else (b, c, a) if b == v else (c, a, b)

    def _apex(self, u, v):
        """The third vertex of the triangle that owns the directed edge u-v."""
        return self._rot(self.tri[self.edge[(u, v)]], u)[2]

    def _around(self, a):
        """The triangles round vertex a, each as (a, u, v), counterclockwise."""
        t0 = self.vtri.get(a)
        if t0 not in self.tri or a not in self.tri[t0]:
            t0 = next(t for t, tr in self.tri.items() if a in tr)
        t, out = t0, []
        while True:
            tri = self._rot(self.tri[t], a)
            out.append(tri)
            t = self.edge.get((a, tri[2]))
            if t is None or t == t0:
                return out

    # ---------------------------------------------------------------- Delaunay

    def _locate(self, q) -> int:
        """A triangle containing q: a walk from the last triangle made."""
        t = self.last if self.last in self.tri else next(iter(self.tri))
        for _ in range(len(self.tri) + 10):
            a, b, c = self.tri[t]
            for u, v in ((a, b), (b, c), (c, a)):
                if orient(self.p[u], self.p[v], q) < 0:
                    t = self.edge[(v, u)]
                    break
            else:
                return t
        for t, (a, b, c) in self.tri.items():
            if min(orient(self.p[a], self.p[b], q), orient(self.p[b], self.p[c], q),
                   orient(self.p[c], self.p[a], q)) >= 0:
                return t
        raise ValueError("A point fell outside the triangulation.")

    def _insert(self, i):
        q = self.p[i]
        t = self._locate(q)
        a, b, c = self.tri[t]
        for v in (a, b, c):
            if (self.p[v][0] - q[0]) ** 2 + (self.p[v][1] - q[1]) ** 2 < EPS * EPS:
                self.alias[i] = v
                return
        stack = []
        on_edge = [(u, v) for u, v in ((a, b), (b, c), (c, a)) if abs(orient(self.p[u], self.p[v], q)) < EPS]
        if on_edge:
            u, v = on_edge[0]
            w = self._apex(u, v)
            x = self._apex(v, u) if (v, u) in self.edge else None
            self._remove(t)
            self._add(u, i, w)
            self.last = self._add(i, v, w)
            stack += [(i, w, u), (i, v, w)]
            if x is not None:
                self._remove(self.edge[(v, u)])
                self._add(v, i, x)
                self._add(i, u, x)
                stack += [(i, x, v), (i, u, x)]
        else:
            self._remove(t)
            self.last = self._add(a, b, i)
            self._add(b, c, i)
            self._add(c, a, i)
            stack += [(i, a, b), (i, b, c), (i, c, a)]
        while stack:
            self._legalize(*stack.pop(), stack)

    def _legalize(self, pi, u, v, stack):
        """Flip the edge u-v of the triangle (pi, u, v) if the point beyond it lies in its circle."""
        t1 = self.edge.get((u, v))
        if t1 is None or pi not in self.tri[t1] or (v, u) not in self.edge or frozenset((u, v)) in self.fixed:
            return
        w = self._apex(v, u)
        if in_circle(self.p[pi], self.p[u], self.p[v], self.p[w]) > 0:
            self._remove(t1)
            self._remove(self.edge[(v, u)])
            self.last = self._add(pi, u, w)
            self._add(pi, w, v)
            stack += [(pi, u, w), (pi, w, v)]

    # ---------------------------------------------------------------- constraints

    def _flip(self, u, v):
        """Replace the edge u-v by the other diagonal of its quadrilateral; returns that diagonal."""
        x, y = self._apex(u, v), self._apex(v, u)
        self._remove(self.edge[(u, v)])
        self._remove(self.edge[(v, u)])
        self._add(x, u, y)
        self._add(y, v, x)
        return x, y

    def _convex(self, u, v) -> bool:
        """The quadrilateral round the edge u-v is convex (so the edge can be flipped)."""
        x, y = self._apex(u, v), self._apex(v, u)
        return _crosses(self.p[u], self.p[v], self.p[x], self.p[y])

    def add_constraint(self, a, b) -> bool:
        """Make a-b an edge, splitting it at any point lying on it. False when it crosses an
        earlier constraint; the pieces before the crossing stay. The pieces are worked off a
        stack, not by recursion: a long outline edge can carry thousands of points (review,
        12.10.2026: recursion overflowed past about 1000)."""
        pieces = [(self.alias.get(a, a), self.alias.get(b, b))]
        while pieces:
            a, b = pieces.pop()
            done = self._piece(a, b)
            if done is False:
                return False
            if done is not True:              # a point on the piece: a to it first, then it to b
                pieces += [(done, b), (a, done)]
        return True

    def _piece(self, a, b):
        """Make a-b an edge. True when done, False when it crosses an earlier constraint, or the
        vertex lying on it where it must be split first."""
        if a == b:
            return True
        if (a, b) in self.edge or (b, a) in self.edge:
            self.fixed.add(frozenset((a, b)))
            return True
        p = self.p
        pa, pb = p[a], p[b]
        fan = self._around(a)
        # a fan vertex on the line ahead splits it; test both of each triangle's vertices before
        # looking for the triangle the line leaves through, or one whose orientation is a rounding
        # error above zero passes as straddling it (review, 12.10.2026: zero-area triangles)
        for _, u, v in fan:
            for w in (u, v):
                if abs(orient(pa, pb, p[w])) < EPS and _ahead(pa, pb, p[w]):
                    return w
        right = left = None
        for _, u, v in fan:
            if orient(pa, pb, p[u]) < 0 < orient(pa, pb, p[v]):
                right, left = u, v
                break
        if right is None:
            return False
        crossed = []
        while True:
            if frozenset((right, left)) in self.fixed:
                return False
            crossed.append((right, left))
            w = self._apex(left, right)
            if w == b:
                break
            ow = orient(pa, pb, p[w])
            if abs(ow) < EPS:
                return w
            if ow > 0:
                left = w
            else:
                right = w
        queue, made = deque(crossed), []
        for _ in range(100000):
            if not queue:
                break
            u, v = queue.popleft()
            if not self._convex(u, v):
                queue.append((u, v))
                continue
            x, y = self._flip(u, v)
            if {x, y} != {a, b} and _crosses(pa, pb, p[x], p[y]):
                queue.append((x, y))
            else:
                made.append((x, y))
        else:
            raise ValueError("A level line could not be made an edge.")
        self.fixed.add(frozenset((a, b)))
        for _ in range(1000):          # restore Delaunay round the new edges
            changed = False
            for k, (u, v) in enumerate(made):
                if ({u, v} == {a, b} or frozenset((u, v)) in self.fixed
                        or (u, v) not in self.edge or (v, u) not in self.edge):
                    continue
                if in_circle(p[u], p[v], p[self._apex(u, v)], p[self._apex(v, u)]) > EPS and self._convex(u, v):
                    made[k] = self._flip(u, v)
                    changed = True
            if not changed:
                break
        return True
