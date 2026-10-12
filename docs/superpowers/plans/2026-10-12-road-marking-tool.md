# Road Marking tool (`create_road_marking`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new MCP tool, `create_road_marking`, that places the private `Road Marking` library part along a Line, Polyline, Arc, Morph line or points, with any preset, stripe, bay, stop and lettering setting, and drapes it onto Meshes and Slabs.

**Architecture:** `placed_path.py` takes the path reading, placement and parameter writing out of `swept_beam.py` so both tools share it. `cdt.py` is a pure Python constrained Delaunay triangulation that reproduces Archicad's Mesh surface; `drape.py` reads Meshes and Slabs into a `SurfaceSet`, maps the marking's path as the part does, fits a cross fall per station and adds nodes until the marking is within tolerance. `road_marking.py` resolves the marking's settings from a preset table exported from the part's model (`road_marking_presets.json`), works out the marking's reach, and writes the part's parameters in one call.

**Tech Stack:** Python 3.12+, pure standard library for the new modules; FastMCP server; Tapir commands over `ArchicadConnection`; pytest with `FakeCore`.

**Spec:** `docs/superpowers/specs/2026-10-12-road-marking-tool-design.md`, with the trial results in `docs/superpowers/specs/2026-10-12-road-marking-tool-findings.md`. The parent spec for the library part is `~/Documents/Archicad GDL/docs/superpowers/specs/2026-10-07-road-marking-design.md`.

## Global Constraints

- No em dashes and no en dashes anywhere: code, comments, docs, commit messages. Rewrite the sentence; do not swap the character.
- Every commit message ends with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Commit with the repo's configured identity.
- MCP work happens in the worktree `/Users/alesd/Developer/Archicad MCP/.claude/worktrees/feature+road-marking` on branch `feature/road-marking`. The main checkout's uncommitted changes (Tapir 1.7.0 definitions, `docs/api-dashboard.html`, `gdl-src/swept-beam/params.toml`) are never touched.
- GDL repo work happens in a new worktree `/Users/alesd/Documents/Archicad GDL/.worktrees/road-marking-tool` on branch `feature/road-marking-tool`; the GDL main checkout is left alone.
- Pure Python: `pyproject.toml` dependencies stay as they are. Python 3.12+.
- The library part is `Road Marking`; its preset names are exactly the 38 keys of `road_marking_presets.json`, plus `Custom`.
- Through the API: lengths in metres; `segArc` items in radians; `s1Angle` to `s3Angle` written in degrees and read back in radians; `nodeFall` in percent; `A` and `aDone` written as 1.0.
- Dry run by default; a write needs `dry_run=false`.
- Live writes go only to the project named exactly `MCP-Test`. Read `GetProjectInfo` on the chosen port before every write (ports move when Archicad restarts; MCP-Test was on 19725 on 12.10.2026; CVP and LZV are real projects).
- Test commands: MCP repo `uv run pytest -q` from the worktree; GDL repo `ARCHICAD_MCP_REPO="/Users/alesd/Developer/Archicad MCP/.claude/worktrees/feature+road-marking" python3 -m pytest road_marking/tests -q` from its worktree.

## Review Focus

1. A placed marking that was rotated (`angle` not 0) and is draped again on its own path: its nodes must come back to the same local positions, at the surface's heights. Test in Task 7.
2. A source drawn on another story than the surface (a DWG polyline on story 0, the road on story 1): the marking sits at the surface's absolute height and keeps the source's story. Test in Task 7.
3. A `drape_on` GUID that does not exist, is given twice, or is a Wall: a clear error naming the GUID; a repeated GUID is read once. Test in Task 3.
4. A Mesh level line that runs along an outline edge or through outline corners (common in road Meshes built from 3D edges): no segment is lost and the heights there are exact. Tests in Tasks 2 and 3.
5. A terrain Mesh on a regular grid (every cell's corners on one circle): the triangulation must finish and tile the Mesh, and whether Archicad picks the same diagonals is measured live, not assumed. Test in Task 2; live comparison in Task 9.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `src/archicad_mcp/core/placed_path.py` (new) | JSON text arguments, reading a source path, the library part check, the placed element's frame, local coordinates, placement and the parameter write; shared by both tools |
| `src/archicad_mcp/core/swept_beam.py` (modify) | Imports the shared code instead of holding it |
| `src/archicad_mcp/core/cdt.py` (new) | Constrained Delaunay triangulation |
| `src/archicad_mcp/core/drape.py` (new) | Slab and Mesh surfaces, `SurfaceSet`, the part's path mapping, the drape fit |
| `src/archicad_mcp/core/road_marking_presets.json` (new, generated) | The part's preset table, defaults, value lists and letter widths |
| `src/archicad_mcp/core/road_marking.py` (new) | Settings, validation, reach, warnings, and the tool itself |
| `src/archicad_mcp/server.py`, `manifest.json`, `tests/test_tool_annotations.py`, `scripts/build_dashboard.py`, `docs/api-dashboard.html`, `README.md`, `scripts/check_wheel_install.py` (modify) | Registration and the lists that name every tool |
| `docs/road-marking.md` (new) | The guide |
| `tests/test_placed_path.py`, `tests/test_cdt.py`, `tests/test_drape.py`, `tests/test_road_marking_presets.py`, `tests/test_road_marking_values.py`, `tests/test_road_marking_tool.py` (new) | Tests |
| GDL repo `road_marking/export_presets.py` (new), `road_marking/tests/test_road_marking_dialog.py` (modify), `docs/superpowers/specs/2026-10-07-road-marking-design.md` (modify) | The exporter, the cross repo check, the pointer to this spec |

---

### Task 1: Shared path code (`placed_path.py`)

**Files:**
- Create: `src/archicad_mcp/core/placed_path.py`
- Modify: `src/archicad_mcp/core/swept_beam.py`
- Test: `tests/test_placed_path.py`; `tests/test_swept_beam_tool.py` and `tests/test_swept_path.py` run unchanged

**Interfaces:**
- Consumes: `archicad_mcp.core.swept_path` (`SweptPath`, `PathError`, `fit_points`, `chain_wire_edges`, `DUPLICATE_EPS`).
- Produces (module `archicad_mcp.core.placed_path`, imported as `pp`):
  - `class PlacedPathError(ValueError)`
  - `from_json_text(value, name: str, kind: type)` returns the parsed list or dict, or the value unchanged when it is not a string
  - `details(conn, guid: str) -> dict` (one `GetDetailsOfElements` entry)
  - `path_from_element(conn, guid: str, tol: float, part: str) -> tuple[sp.SweptPath, int | None, bool]` (path, floorIndex, flat source)
  - `path_from_points(points: list[dict], tol: float) -> sp.SweptPath`
  - `attribute_index(conn, kind: str, name: str) -> int`
  - `library_part_loaded(conn, part: str) -> bool`
  - `existing_frame(conn, guid: str, part: str) -> tuple[tuple[float, float, float], float]` (origin, angle in radians)
  - `to_local(nodes, origin, angle: float) -> list[tuple[float, float, float]]`
  - `place(conn, part: str, origin, floor_index: int | None) -> str` (new GUID)
  - `write_parameters(conn, guid: str, params: list[dict], part: str) -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_placed_path.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_placed_path.py -v`
Expected: FAIL with `ImportError: cannot import name 'placed_path'`

- [ ] **Step 3: Create `placed_path.py`**

Create `src/archicad_mcp/core/placed_path.py`:

```python
"""Shared by the tools that place a library part along a path: create_swept_beam and
create_road_marking.

A source curve (a Morph line, Polyline, Line or Arc) or 3D points become nodes and plan
arcs (swept_path); the part is placed at a point and its node arrays are written in the
part's own coordinates. Tapir reports z as an absolute elevation for morphs, polylines,
lines, arcs and placed objects, and CreateObjects derives the story from an absolute z,
so everything here works in absolute z.
"""

from __future__ import annotations

import json
import math

from archicad_mcp.core import swept_path as sp


class PlacedPathError(ValueError):
    """The request cannot become a part placed along a path."""


def from_json_text(value, name: str, kind: type):
    """Parse a list or object that arrived as JSON text.

    Claude Code collapses a nullable list or object field to an untyped schema
    and sends the value as text; refusing it would make the tool unusable there.
    """
    if not isinstance(value, str):
        return value
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise PlacedPathError(f"{name} arrived as text that is not valid JSON ({exc.msg}). "
                              f"Pass a {kind.__name__}, or JSON text of one.") from exc
    if not isinstance(parsed, kind):
        raise PlacedPathError(f"{name} must be a JSON {kind.__name__}, "
                              f"got {type(parsed).__name__}.")
    return parsed


def details(conn, guid: str) -> dict:
    result = conn.tapir("GetDetailsOfElements", {"elements": [{"elementId": {"guid": guid}}]})
    entry = (result.get("detailsOfElements") or [{}])[0]
    if "error" in entry or "type" not in entry:
        raise PlacedPathError(f"Could not read element {guid}: {entry.get('error', entry)}")
    return entry


def path_from_element(conn, guid: str, tol: float, part: str):
    """The source's fitted path, its story, and whether it is flat (a Polyline, Line or Arc)."""
    entry = details(conn, guid)
    kind, d, floor = entry["type"], entry.get("details", {}), entry.get("floorIndex")
    if kind == "Morph":
        return _morph_path(d, tol), floor, False
    if kind == "PolyLine":
        return _polyline_path(d, part), floor, True
    if kind == "Line":
        z = float(d.get("zCoordinate", 0.0))
        b, e = d["begCoordinate"], d["endCoordinate"]
        return _distinct_path([(b["x"], b["y"], z), (e["x"], e["y"], z)], [0.0, 0.0]), floor, True
    if kind == "Arc":
        return _arc_path(d), floor, True
    raise PlacedPathError(
        f"Element {guid} is a {kind}. The source must be a Morph line, Polyline, Line "
        "or Arc (Splines are not supported: their curve shape is not readable).")


def _morph_path(d: dict, tol: float) -> sp.SweptPath:
    body = d.get("body") or {}
    if body.get("polygons"):
        raise PlacedPathError("This morph has faces. Draw the guide as a morph line (edges only).")
    order = sp.chain_wire_edges([tuple(e["vertexIds"]) for e in body.get("wireEdges", [])])
    o = d["origin"]
    ax = d.get("xAxis") or {"x": 1, "y": 0, "z": 0}
    ay = d.get("yAxis") or {"x": 0, "y": 1, "z": 0}
    az = d.get("zAxis") or {"x": 0, "y": 0, "z": 1}
    verts = body["vertices"]
    pts = []
    for i in order:
        v = verts[i]
        pts.append(tuple(o[k] + v["x"] * ax[k] + v["y"] * ay[k] + v["z"] * az[k]
                         for k in ("x", "y", "z")))
    return sp.fit_points(pts, tol)


def _polyline_path(d: dict, part: str) -> sp.SweptPath:
    coords = d.get("coordinates") or []
    if len(coords) < 2:
        raise PlacedPathError("The polyline has fewer than two points.")
    z = float(d.get("zCoordinate", 0.0))
    pts = [(float(c["x"]), float(c["y"]), z) for c in coords]
    if len(pts) > 2 and math.dist(pts[0][:2], pts[-1][:2]) < sp.DUPLICATE_EPS:
        raise PlacedPathError(f"The polyline is closed. A {part} needs a start and an end.")
    arcs = [0.0] * len(pts)
    for a in d.get("arcs") or []:
        if a["endIndex"] == a["begIndex"] + 1:
            arcs[a["begIndex"]] = math.degrees(a["arcAngle"])
    return _distinct_path(pts, arcs)


def _distinct_path(pts: list[sp.Point], arcs: list[float]) -> sp.SweptPath:
    """Drop repeated vertices, as the fit does for points (DWG polylines often
    repeat one). A zero-length piece has no direction and would make a false
    kink. The piece after a dropped vertex keeps its arc."""
    nodes, out_arcs, pending = [pts[0]], [], arcs[0]
    for p, arc in zip(pts[1:], arcs[1:]):
        if math.dist(p, nodes[-1]) >= sp.DUPLICATE_EPS:
            out_arcs.append(pending)
            nodes.append(p)
        pending = arc
    if len(nodes) < 2:
        raise sp.PathError("The source has fewer than two distinct points.")
    return sp.SweptPath(nodes, out_arcs + [0.0])


def _arc_path(d: dict) -> sp.SweptPath:
    if abs(float(d.get("ratio", 1.0)) - 1.0) > 1e-6:
        raise PlacedPathError("Elliptical arcs are not supported.")
    z = float(d.get("zCoordinate", 0.0))
    ox, oy, r = d["origin"]["x"], d["origin"]["y"], float(d["radius"])
    beg, end = float(d["begAngle"]), float(d["endAngle"])
    sweep = (end - beg) % (2 * math.pi)
    if sweep < 1e-9:
        raise PlacedPathError("The arc has no length.")
    p1 = (ox + r * math.cos(beg), oy + r * math.sin(beg), z)
    p2 = (ox + r * math.cos(end), oy + r * math.sin(end), z)
    return sp.SweptPath([p1, p2], [math.degrees(sweep), 0.0])


def path_from_points(points, tol: float) -> sp.SweptPath:
    try:
        pts = [(float(p["x"]), float(p["y"]), float(p["z"])) for p in points]
    except (KeyError, TypeError, ValueError) as exc:
        raise PlacedPathError("points must be a list of {x, y, z} numbers in metres.") from exc
    return sp.fit_points(pts, tol)


def attribute_index(conn, kind: str, name: str) -> int:
    attrs = conn.tapir("GetAttributesByType", {"attributeType": kind}).get("attributes", [])
    for a in attrs:
        if a.get("name") == name:
            return int(a["index"])
    for a in attrs:
        if str(a.get("name", "")).casefold() == name.casefold():
            return int(a["index"])
    close = sorted(str(a.get("name")) for a in attrs
                   if name.casefold() in str(a.get("name", "")).casefold()
                   or str(a.get("name", "")).casefold() in name.casefold())[:10]
    hint = f" Similar names: {', '.join(close)}." if close else ""
    raise PlacedPathError(f"No {kind} attribute named '{name}'.{hint}")


def library_part_loaded(conn, part: str) -> bool:
    parts = conn.tapir("GetAvailableLibraryParts",
                       {"filterByTypeId": "Object"}).get("libraryParts", [])
    want = part.casefold()
    return any(str(p.get("documentName", "")).casefold() == want
               or str(p.get("fileName", "")).casefold() == want + ".gsm" for p in parts)


def existing_frame(conn, guid: str, part: str):
    """A placed part's origin (absolute) and its angle in radians."""
    entry = details(conn, guid)
    d = entry.get("details", {})
    name = str((d.get("libPart") or {}).get("name", ""))
    if entry["type"] != "Object" or name.casefold() != part.casefold():
        raise PlacedPathError(f"Element {guid} is not a {part}. update_guid must "
                              f"point at a placed {part}.")
    o = d["origin"]
    return (float(o["x"]), float(o["y"]), float(o["z"])), float(d.get("angle", 0.0))


def to_local(nodes, origin, angle: float) -> list[sp.Point]:
    c, s = math.cos(-angle), math.sin(-angle)
    out = []
    for x, y, z in nodes:
        dx, dy = x - origin[0], y - origin[1]
        out.append((dx * c - dy * s, dx * s + dy * c, z - origin[2]))
    return out


def place(conn, part: str, origin, floor_index) -> str:
    item = {"libraryPartName": part,
            "coordinates": {"x": origin[0], "y": origin[1], "z": origin[2]}}
    if floor_index is not None:
        item["floorIndex"] = floor_index
    result = conn.tapir("CreateObjects", {"objectsData": [item]})
    elements = result.get("elements") or []
    if not elements or "elementId" not in elements[0]:
        raise PlacedPathError(f"CreateObjects did not place '{part}': {result}")
    return elements[0]["elementId"]["guid"]


def write_parameters(conn, guid: str, params: list[dict], part: str) -> None:
    result = conn.tapir("SetGDLParametersOfElements", {"elementsWithGDLParameters": [
        {"elementId": {"guid": guid}, "gdlParameters": params}]})
    first = (result.get("executionResults") or [{}])[0]
    if not first.get("success", False):
        raise PlacedPathError(f"Writing the {part} parameters of element {guid} failed "
                              f"(the element exists): {first.get('error', first)}")
```

- [ ] **Step 4: Run the new test**

Run: `uv run pytest tests/test_placed_path.py -v`
Expected: 6 passed

- [ ] **Step 5: Make `swept_beam.py` use the shared code**

In `src/archicad_mcp/core/swept_beam.py`:

1. Replace the imports

```python
import json
import math

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core import swept_path as sp
```

with

```python
import math

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core import placed_path as pp
from archicad_mcp.core import swept_path as sp
```

2. Replace `class SweptBeamError(ValueError):` with `class SweptBeamError(pp.PlacedPathError):`.

3. In `create_swept_beam`, replace

```python
        points = _from_json_text(points, "points", list)
        profile = _from_json_text(profile, "profile", dict)
        return _create(conn, source_guid, points, start_height, slope_percent, profile,
                       offset_u, offset_w, flip, path_tolerance, update_guid, dry_run, cuts)
    except (sp.PathError, SweptBeamError) as exc:
```

with

```python
        points = pp.from_json_text(points, "points", list)
        profile = pp.from_json_text(profile, "profile", dict)
        return _create(conn, source_guid, points, start_height, slope_percent, profile,
                       offset_u, offset_w, flip, path_tolerance, update_guid, dry_run, cuts)
    except (sp.PathError, pp.PlacedPathError) as exc:
```

4. Delete these functions from `swept_beam.py` (their code now lives in `placed_path.py`): `_from_json_text`, `_details`, `_path_from_element`, `_morph_path`, `_polyline_path`, `_distinct_path`, `_arc_path`, `_path_from_points`, `_attribute_index`, `_library_part_loaded`, `_existing_frame`, `_to_local`, `_place`, `_write_parameters`.

5. In `_create`, make these replacements:

| Before | After |
| --- | --- |
| `_path_from_element(conn, source_guid, path_tolerance)` | `pp.path_from_element(conn, source_guid, path_tolerance, LIBRARY_PART)` |
| `_path_from_points(points, path_tolerance)` | `pp.path_from_points(points, path_tolerance)` |
| `loaded = _library_part_loaded(conn)` | `loaded = pp.library_part_loaded(conn, LIBRARY_PART)` |
| `origin, angle = _existing_frame(conn, update_guid)` | `origin, angle = pp.existing_frame(conn, update_guid, LIBRARY_PART)` |
| `local = _to_local(path.nodes, origin, angle)` | `local = pp.to_local(path.nodes, origin, angle)` |
| `guid = update_guid or _place(conn, origin, floor_index)` | `guid = update_guid or pp.place(conn, LIBRARY_PART, origin, floor_index)` |
| `_write_parameters(conn, guid, params)` | `pp.write_parameters(conn, guid, params, LIBRARY_PART)` |

6. In `_profile_params`, replace both `_attribute_index(` with `pp.attribute_index(`.

- [ ] **Step 6: Run the Swept Beam tests and the whole suite**

Run: `uv run pytest tests/test_swept_beam_tool.py tests/test_swept_path.py tests/test_placed_path.py -v`
Expected: all pass, with no test file of the Swept Beam changed.
Run: `uv run pytest -q`
Expected: 916 passed (910 before, 6 new), 1 skipped, 16 deselected.

- [ ] **Step 7: Commit**

```bash
git add src/archicad_mcp/core/placed_path.py src/archicad_mcp/core/swept_beam.py tests/test_placed_path.py
git commit -m "refactor: share the Swept Beam's path, placement and write code in placed_path

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Constrained Delaunay triangulation (`cdt.py`)

**Files:**
- Create: `src/archicad_mcp/core/cdt.py`
- Modify: `docs/superpowers/specs/2026-10-12-road-marking-tool-design.md` (Files: name `cdt.py`)
- Test: `tests/test_cdt.py`

**Interfaces:**
- Produces (module `archicad_mcp.core.cdt`):
  - `orient(a, b, c) -> float`, `in_circle(a, b, c, d) -> float`
  - `class CDT(points)`: `points` are distinct `(x, y)` pairs (at least three); vertex `i` is `points[i]`
  - `CDT.add_constraint(a: int, b: int) -> bool`: makes a-b an edge (split at points on it); `False` when it crosses an earlier constraint
  - `CDT.triangles() -> list[tuple[int, int, int]]`: counterclockwise triples of point indices
  - `CDT.p`: the points normalised to about -1 to 1 (for tests)

- [ ] **Step 1: Write the failing test**

Create `tests/test_cdt.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cdt.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'archicad_mcp.core.cdt'`

- [ ] **Step 3: Write `cdt.py`**

Create `src/archicad_mcp/core/cdt.py`:

```python
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
        earlier constraint; the pieces before the crossing stay."""
        a, b = self.alias.get(a, a), self.alias.get(b, b)
        if a == b:
            return True
        if (a, b) in self.edge or (b, a) in self.edge:
            self.fixed.add(frozenset((a, b)))
            return True
        p = self.p
        pa, pb = p[a], p[b]
        right = left = None
        for _, u, v in self._around(a):
            ou, ov = orient(pa, pb, p[u]), orient(pa, pb, p[v])
            if abs(ou) < EPS and _ahead(pa, pb, p[u]):
                return self.add_constraint(a, u) and self.add_constraint(u, b)
            if ou < 0 < ov:
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
                return self.add_constraint(a, w) and self.add_constraint(w, b)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cdt.py -v`
Expected: 7 passed

- [ ] **Step 5: Name `cdt.py` in the spec**

In `docs/superpowers/specs/2026-10-12-road-marking-tool-design.md`, section "Files", replace

```
- `src/archicad_mcp/core/drape.py`, `src/archicad_mcp/core/road_marking.py`,
  `src/archicad_mcp/core/road_marking_presets.json`.
```

with

```
- `src/archicad_mcp/core/cdt.py` (the constrained Delaunay triangulation, on its own so drape.py
  stays readable), `src/archicad_mcp/core/drape.py`, `src/archicad_mcp/core/road_marking.py`,
  `src/archicad_mcp/core/road_marking_presets.json`.
```

- [ ] **Step 6: Commit**

```bash
git add src/archicad_mcp/core/cdt.py tests/test_cdt.py docs/superpowers/specs/2026-10-12-road-marking-tool-design.md
git commit -m "feat: constrained Delaunay triangulation for Mesh draping

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Surfaces (`drape.py`, part 1)

**Files:**
- Create: `src/archicad_mcp/core/drape.py`
- Test: `tests/test_drape.py`; fixtures already committed in `tests/fixtures/road_marking/`

**Interfaces:**
- Consumes: `archicad_mcp.core.cdt.CDT` (Task 2).
- Produces (module `archicad_mcp.core.drape`, imported as `dr`):
  - `class DrapeError(ValueError)`; constants `EDGE_TOL = 0.001`, `ARC_EPS_DEG = 0.01`
  - `slab_surface(guid: str, details: dict) -> SlabSurface`
  - `mesh_surface(guid: str, details: dict, story_level: float) -> MeshSurface` (attribute `dropped_lines: int`, `triangles`)
  - Both surfaces have `box: tuple[float, float, float, float]` and `height(x, y) -> float | None`
  - `class SurfaceSet(surfaces: list)`: `height(x, y) -> float | None` (the highest)
  - `read_surfaces(conn, guids: list[str]) -> SurfaceSet`

- [ ] **Step 1: Write the failing test**

Create `tests/test_drape.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_drape.py -v`
Expected: FAIL with `ImportError: cannot import name 'drape'`

- [ ] **Step 3: Write the surfaces part of `drape.py`**

Create `src/archicad_mcp/core/drape.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_drape.py -v`
Expected: 10 passed (the parametrised Mesh test counts twice)

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/core/drape.py tests/test_drape.py
git commit -m "feat: Slab and Mesh surfaces for draping, matching Archicad's own triangles

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The part's path and the drape fit (`drape.py`, part 2)

**Files:**
- Modify: `src/archicad_mcp/core/drape.py` (append)
- Test: `tests/test_drape.py` (append)

**Interfaces:**
- Consumes: `SurfaceSet` (Task 3).
- Produces:
  - `class PlanPath(nodes, arcs_deg, tol)`: attributes `s` (sample stations), `node_s` (station of each node), `length`; method `xy(s, u) -> tuple[float, float]` (the part's mapping)
  - `@dataclass Drape`: `nodes` (list of `(x, y, z)`, z with lift), `falls` (percent), `arcs` (degrees), `added: int`, `max_along: float`, `max_across: float`, `off_surface: list[dict]`, `partly_off_surface: list[dict]`, `warnings: list[str]`
  - `drape(nodes, arcs_deg, reach: tuple[float, float], surfaces, tol=0.005, lift=0.0, path_tol=0.002, step=0.1, lateral_step=0.5, max_nodes=500) -> Drape`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_drape.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_drape.py -v`
Expected: the 10 new tests FAIL with `AttributeError: module 'archicad_mcp.core.drape' has no attribute 'PlanPath'` (or `'drape'`); the 10 from Task 3 pass.

- [ ] **Step 3: Append the path and the fit to `drape.py`**

Append to `src/archicad_mcp/core/drape.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_drape.py -v`
Expected: 20 passed

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/core/drape.py tests/test_drape.py
git commit -m "feat: drape fit: the part's path, a raised cross fall line per station, nodes by Douglas and Peucker

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The preset table (both repos)

**Files:**
- GDL repo (worktree `/Users/alesd/Documents/Archicad GDL/.worktrees/road-marking-tool`):
  - Create: `road_marking/export_presets.py`
  - Modify: `road_marking/tests/test_road_marking_dialog.py` (replace `test_the_mcp_tool_knows_the_same_presets`)
  - Modify: `docs/superpowers/specs/2026-10-07-road-marking-design.md` (the MCP section's pointer and the Tests line)
- MCP repo:
  - Create (generated): `src/archicad_mcp/core/road_marking_presets.json`
  - Test: `tests/test_road_marking_presets.py`

**Interfaces:**
- Produces: `road_marking_presets.json` with keys `source` (repo, commit, part, exporter), `settings` (list of the part's setting names, without `markingType` and the node arrays), `defaults` (every setting plus `markingType`), `presets` (38 names, each a dict of the values that preset writes), `letter_widths` (A, B, I, S, T, U, X at `letterWidth` 0.50), `letter_base_width` (0.5), `line_width` (0.1), `values` (`shapes`, `aligns`, `layouts`, `letter_texts`, `letter_aligns`).
- GDL repo: `export_presets.table() -> dict` (everything but `source`).

- [ ] **Step 1: Make the GDL worktree**

```bash
git -C "/Users/alesd/Documents/Archicad GDL" worktree add .worktrees/road-marking-tool -b feature/road-marking-tool
```

Expected: `Preparing worktree (new branch 'feature/road-marking-tool')`. (`.worktrees` is already ignored in that repo.) Then check the baseline from that worktree:

```bash
python3 -m pytest road_marking/tests -q
```

Expected: all pass (443 tests at merge 91fe530 plus anything since; the MCP preset test is skipped because the MCP main checkout has no `road_marking.py`).

- [ ] **Step 2: Write the failing GDL test**

In `road_marking/tests/test_road_marking_dialog.py`, add `import json` and `import os` to the imports at the top, then replace

```python
MCP_TOOL = Path.home() / "Developer" / "Archicad MCP" / "src" / "archicad_mcp" / "core" / "road_marking.py"


@pytest.mark.skipif(not MCP_TOOL.exists(), reason="create_road_marking is not in the Archicad MCP checkout")
def test_the_mcp_tool_knows_the_same_presets():
    import road_marking_model as M
    text = MCP_TOOL.read_text(encoding="utf-8")
    block = text[text.index("PRESETS = ("):text.index(")", text.index("PRESETS = ("))]
    assert tuple(re.findall(r'"([^"]+)"', block)) == M.PRESET_NAMES
```

with

```python
MCP_REPO = Path(os.environ.get("ARCHICAD_MCP_REPO", Path.home() / "Developer" / "Archicad MCP"))
MCP_TABLE = MCP_REPO / "src" / "archicad_mcp" / "core" / "road_marking_presets.json"


@pytest.mark.skipif(not MCP_TABLE.exists(), reason="create_road_marking is not in the Archicad MCP checkout")
def test_the_mcp_preset_table_matches_the_part():
    # create_road_marking reads this table; road_marking/export_presets.py writes it after a preset change
    import export_presets
    mcp = json.loads(MCP_TABLE.read_text(encoding="utf-8"))
    mcp.pop("source")
    assert mcp == json.loads(json.dumps(export_presets.table()))
```

Run (from the GDL worktree): `ARCHICAD_MCP_REPO="/Users/alesd/Developer/Archicad MCP/.claude/worktrees/feature+road-marking" python3 -m pytest road_marking/tests/test_road_marking_dialog.py -v`
Expected: `test_the_mcp_preset_table_matches_the_part` SKIPPED (no table yet); everything else passes.

- [ ] **Step 3: Write the exporter**

Create `road_marking/export_presets.py`:

```python
"""Write the preset table create_road_marking reads (Archicad MCP repo,
src/archicad_mcp/core/road_marking_presets.json).

For each preset, the values the Parameter Script writes when the preset is picked: the
model's own apply_preset runs on parameters that all hold a marker, and whatever no
longer holds the marker was written. Run it after any preset change:

    python3 road_marking/export_presets.py           # into $ARCHICAD_MCP_REPO (default ~/Developer/Archicad MCP)
    python3 road_marking/export_presets.py <file>    # anywhere else

test_road_marking_dialog.py checks the MCP repo's copy against a fresh table.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import road_marking_model as M  # noqa: E402

# Parameters that are not marking settings: placement, the path, grip helpers and markers, the preset name
NOT_SETTINGS = {"A", "B", "ZZYZX", "AC_show2DHotspotsIn3D", "aDone", "nodeX", "nodeY", "nodeZ", "nodeFall",
                "segArc", "slopePercent", "slopeDone", "segSag", "sagDone", "insL", "insDone", "extStart",
                "extEnd", "extStartDone", "extEndDone", "markingType", "markingTypeDone", "gs_ui_current_page",
                "rm_version"}
MCP_REPO = Path(os.environ.get("ARCHICAD_MCP_REPO", Path.home() / "Developer" / "Archicad MCP"))
MCP_TABLE = MCP_REPO / "src" / "archicad_mcp" / "core" / "road_marking_presets.json"


def _written(name):
    marker = object()
    p = {k: marker for k in M.defaults()}
    p["markingType"], p["markingTypeDone"] = name, "__none__"
    q = M.apply_preset(p)
    return {k: v for k, v in q.items() if v is not marker and k not in ("markingType", "markingTypeDone")}


def table():
    defaults = M.defaults()
    settings = [k for k in defaults if k not in NOT_SETTINGS]
    return {
        "settings": settings,
        "defaults": {k: defaults[k] for k in settings + ["markingType"]},
        "presets": {name: _written(name) for name in M.PRESETS},
        "letter_widths": {ch: round(g["w"], 4) for ch, g in M.GLYPHS.items() if ch != "EV"},
        "letter_base_width": M.LETTER_W,
        "line_width": M.LINE_W,
        "values": {"shapes": list(M.SHAPES), "aligns": list(M.ALIGNS), "layouts": list(M.LAYOUTS),
                   "letter_texts": list(M.LETTER_TEXTS), "letter_aligns": list(M.ALIGNS[:3])},
    }


def main(out: Path):
    commit = subprocess.run(["git", "-C", str(HERE.parent), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    doc = {"source": {"repo": "Archicad GDL", "commit": commit, "part": "Road Marking",
                      "exporter": "road_marking/export_presets.py"}, **table()}
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(doc['presets'])} presets, {len(doc['settings'])} settings -> {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else MCP_TABLE)
```

- [ ] **Step 4: Export into the MCP worktree and run the GDL tests**

From the GDL worktree:

```bash
ARCHICAD_MCP_REPO="/Users/alesd/Developer/Archicad MCP/.claude/worktrees/feature+road-marking" python3 road_marking/export_presets.py
```

Expected: `38 presets, 60 settings -> /Users/alesd/Developer/Archicad MCP/.claude/worktrees/feature+road-marking/src/archicad_mcp/core/road_marking_presets.json`

```bash
ARCHICAD_MCP_REPO="/Users/alesd/Developer/Archicad MCP/.claude/worktrees/feature+road-marking" python3 -m pytest road_marking/tests -q
```

Expected: all pass, `test_the_mcp_preset_table_matches_the_part` among them (no longer skipped).

- [ ] **Step 5: Point the parent spec at the new spec**

In the GDL worktree's `docs/superpowers/specs/2026-10-07-road-marking-design.md`:

1. Directly under this heading line

```
## MCP Tool: `create_road_marking`
```

add a blank line and then

```
Replaced by `~/Developer/Archicad MCP/docs/superpowers/specs/2026-10-12-road-marking-tool-design.md`
(12.10.2026): grouped arguments, the preset table in road_marking_presets.json, a constrained Delaunay
Mesh surface, and the drape fit settled by a trial run. The text below is the first outline.
```

2. In the section "Tests", "Static" bullet, replace

```
  line longer than 256 characters; if
  `~/Developer/Archicad MCP` has the tool, its preset names equal the part's.
```

with

```
  line longer than 256 characters; if the MCP repo (`ARCHICAD_MCP_REPO`, default
  `~/Developer/Archicad MCP`) has road_marking_presets.json, it equals a fresh export_presets.table().
```

- [ ] **Step 6: Commit in the GDL repo**

From the GDL worktree:

```bash
git add road_marking/export_presets.py road_marking/tests/test_road_marking_dialog.py docs/superpowers/specs/2026-10-07-road-marking-design.md
git commit -m "feat: export the preset table create_road_marking reads; check the MCP copy against it

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 7: Write the MCP side test**

Create `tests/test_road_marking_presets.py`:

```python
"""road_marking_presets.json: the Road Marking part's preset table, exported from its model
(Archicad GDL repo, road_marking/export_presets.py, which also checks this copy)."""
import json
from pathlib import Path

TABLE = json.loads((Path(__file__).parents[1] / "src" / "archicad_mcp" / "core" / "road_marking_presets.json")
                   .read_text(encoding="utf-8"))


def test_the_table_has_the_parts_38_presets_and_its_values():
    assert len(TABLE["presets"]) == 38
    assert TABLE["defaults"]["markingType"] == "5121 Broken divider, in settlements"
    assert "markingType" not in TABLE["settings"] and "nodeX" not in TABLE["settings"]
    assert set(TABLE["settings"]) | {"markingType"} == set(TABLE["defaults"])
    assert TABLE["letter_widths"] == {"A": 0.6, "B": 0.5, "I": 0.15, "S": 0.5, "T": 0.5, "U": 0.5, "X": 0.6}
    assert TABLE["values"]["layouts"][0] == "None" and "Both ends" in TABLE["values"]["aligns"]
    assert TABLE["values"]["letter_aligns"] == ["Start", "Centre", "End"]


def test_every_preset_sets_what_the_part_sets_within_its_ranges():
    v = TABLE["values"]
    for name, row in TABLE["presets"].items():
        assert set(row) <= set(TABLE["settings"]), name
        assert {"patternAlign", "wholeDashes", "paintSurface", "s1On", "s2On", "s3On",
                "bayLayout", "letterText"} <= set(row), name
        assert row["patternAlign"] in v["aligns"] and row["bayLayout"] in v["layouts"], name
        assert row["letterText"] in v["letter_texts"], name
        for k in (1, 2, 3):
            if f"s{k}Width" in row:
                assert row[f"s{k}Width"] >= 0.01 and row[f"s{k}Dash"] >= 0.01 and row[f"s{k}Gap"] >= 0, name
                assert -80 <= row[f"s{k}Angle"] <= 80 and row[f"s{k}Shape"] in v["shapes"], name
        if row["bayLayout"] != "None":
            assert row["bayDepth"] >= 0.3 and row["bayWidth"] >= 0.3, name
        if row["letterText"] != "None":
            assert row["letterLength"] >= 0.1 and row["letterAlign"] in v["letter_aligns"], name
```

- [ ] **Step 8: Run it**

Run: `uv run pytest tests/test_road_marking_presets.py -v`
Expected: 2 passed

- [ ] **Step 9: Commit in the MCP repo**

```bash
git add src/archicad_mcp/core/road_marking_presets.json tests/test_road_marking_presets.py
git commit -m "feat: the Road Marking preset table, exported from the part's model

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Settings, validation, reach and warnings (`road_marking.py`, part 1)

**Files:**
- Create: `src/archicad_mcp/core/road_marking.py`
- Test: `tests/test_road_marking_values.py`

**Interfaces:**
- Consumes: `placed_path.PlacedPathError` (Task 1); `road_marking_presets.json` (Task 5).
- Produces (module `archicad_mcp.core.road_marking`, imported as `rm`):
  - `LIBRARY_PART = "Road Marking"`, `CUSTOM = "Custom"`, `TABLE`, `PRESETS`, `STRIPE_KEYS`, `BAY_KEYS`, `LETTER_KEYS`
  - `class RoadMarkingError(pp.PlacedPathError)`
  - `preset_name(marking) -> str`
  - `check_value(name: str, value)` returns the value the part takes, or raises
  - `resolve(current: dict, marking=None, stripes=None, bays=None, lettering=None, flat=None) -> tuple[dict, dict]` (`want`: the settings after the call; `state`: the part's settings right after `markingType` is written). `flat` is keyed by the part's names (`flipSides`, `patternAlign`, `patternShift`, `wholeDashes`, `thickness`, `paintSurface`).
  - `reach(p: dict) -> tuple[float, float]`
  - `overhang(p: dict, length: float) -> list[str]`
  - `no_effect(p: dict, bays: dict | None, lettering: dict | None) -> list[str]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_road_marking_values.py`:

```python
"""create_road_marking's settings: presets, the call's changes, validation, the reach and the warnings."""
import re

import pytest

from archicad_mcp.core import road_marking as rm

DEFAULTS = rm.TABLE["defaults"]


def test_preset_names_match_without_regard_to_case():
    assert rm.preset_name("5352-1 disabled BAYS, shared zone") == "5352-1 Disabled bays, shared zone"
    assert rm.preset_name("custom") == "Custom"
    with pytest.raises(rm.RoadMarkingError, match=re.escape("Unknown marking '5999'. Valid: 5111 Solid divider")):
        rm.preset_name("5999")


def test_a_new_preset_applies_and_the_calls_values_go_on_top():
    want, state = rm.resolve(DEFAULTS, marking="5333-2 Bus stop on the carriageway", lettering={"offset": 1.75})
    assert state["letterOffset"] == 1.5 and want["letterOffset"] == 1.75
    assert want["bayLayout"] == "Stop end bars" and want["markingType"] == "5333-2 Bus stop on the carriageway"


def test_the_markings_own_preset_is_not_applied_again():
    current = dict(DEFAULTS, s1Width=0.12)
    want, _ = rm.resolve(current, marking="5121 broken divider, in settlements")
    assert want["s1Width"] == 0.12


def test_flip_shift_and_thickness_survive_a_new_preset():
    current = dict(DEFAULTS, flipSides=True, patternShift=1.0, thickness=0.005)
    want, _ = rm.resolve(current, marking="Parking 90 degrees")
    assert (want["flipSides"], want["patternShift"], want["thickness"]) == (True, 1.0, 0.005)
    assert want["s1Offset"] == 2.5


def test_stripes_bays_and_lettering_map_to_the_parts_parameters():
    want, _ = rm.resolve(DEFAULTS, stripes={"2": {"on": True, "offset": 0.3, "shape": "Triangle, point left"}},
                         bays={"layout": "Taxi bay", "depth": 4.5, "symbol": False},
                         lettering={"text": "BUS", "align": "Centre", "repeat": 20})
    assert (want["s2On"], want["s2Offset"], want["s2Shape"]) == (True, 0.3, "Triangle, point left")
    assert (want["bayLayout"], want["bayDepth"], want["showSymbol"]) == ("Taxi bay", 4.5, False)
    assert (want["letterText"], want["letterAlign"], want["letterRepeat"]) == ("BUS", "Centre", 20.0)


@pytest.mark.parametrize("kwargs,message", [
    ({"stripes": {"4": {"width": 0.1}}}, "stripes keys are '1', '2' and '3'"),
    ({"stripes": {"1": "wide"}}, "stripes['1'] must be an object"),
    ({"stripes": {"1": {"colour": "red"}}}, "Unknown stripe key 'colour'. Valid: on, offset"),
    ({"stripes": {"1": {"shape": "Circle"}}}, "s1Shape must be one of: Bar, Triangle, point left"),
    ({"stripes": {"1": {"angle": 85}}}, "s1Angle must be -80 to 80, got 85"),
    ({"stripes": {"1": {"width": "wide"}}}, "s1Width must be a number"),
    ({"stripes": {"1": {"on": 1}}}, "s1On must be true or false"),
    ({"bays": {"depth": 0.2}}, "bayDepth must be at least 0.3, got 0.2"),
    ({"bays": {"colour": "blue"}}, "Unknown bays key 'colour'. Valid: layout, width"),
    ({"bays": {"layout": "Garage"}}, "bayLayout must be one of: None, Disabled bay"),
    ({"lettering": {"text": "STOP"}}, "letterText must be one of: None, BUS, TAXI"),
    ({"lettering": {"align": "Both ends"}}, "letterAlign must be one of: Start, Centre, End"),
    ({"lettering": {"font": "Arial"}}, "Unknown lettering key 'font'"),
    ({"flat": {"thickness": 0.1}}, "thickness must be 0.0005 to 0.05, got 0.1"),
    ({"flat": {"patternAlign": "Middle"}}, "patternAlign must be one of: Start, Centre, End, Both ends"),
])
def test_bad_values_are_refused_with_the_valid_ones(kwargs, message):
    with pytest.raises(rm.RoadMarkingError, match=re.escape(message)):
        rm.resolve(DEFAULTS, **kwargs)


@pytest.mark.parametrize("marking,flip,reach", [
    ("5121 Broken divider, in settlements", False, (-0.075, 0.075)),
    ("5131 Double solid", False, (-0.225, 0.225)),
    ("5231 Pedestrian crossing", False, (-2.0, 2.0)),
    ("Parking 60 degrees", False, (0.0, 5.8)),
    ("Parking 60 degrees", True, (-5.8, 0.0)),
    ("5352-1 Disabled bays, shared zone", False, (0.0, 5.45)),
    ("5352-2 Disabled bay, parallel", True, (-2.45, 0.0)),
    ("5333-2 Bus stop on the carriageway", False, (-0.15, 3.05)),
    ("5111-1 Bus lane line", False, (-0.075, 2.8)),
    ("5111-1 Bus lane line", True, (-2.8, 0.075)),
    ("5334-1 Taxi stop on the carriageway", False, (-0.075, 2.55)),
])
def test_the_reach_covers_stripes_bays_and_the_word(marking, flip, reach):
    want, _ = rm.resolve(DEFAULTS, marking=marking, flat={"flipSides": flip})
    assert rm.reach(want) == pytest.approx(reach)


def test_a_bay_row_or_word_past_the_paths_ends_is_reported():
    want, _ = rm.resolve(DEFAULTS, marking="5352-1 Disabled bays, shared zone")
    assert rm.overhang(want, 10.0) == []
    notes = rm.overhang(want, 5.0)
    assert len(notes) == 1 and "bay row is 6.30 m long" in notes[0] and "1.30 m past the end" in notes[0]
    want, _ = rm.resolve(DEFAULTS, marking="5333 Bus stop in a lay-by")
    assert "The word BUS is 4.00 m long and reaches 0.50 m before the start" in rm.overhang(want, 3.0)[0]
    want, _ = rm.resolve(DEFAULTS, marking="5333-2 Bus stop on the carriageway")
    notes = rm.overhang(want, 1.0)
    assert len(notes) == 1 and "word" in notes[0]          # the stop's end bars stay inside the path


def test_bay_and_lettering_values_with_nothing_to_act_on_are_reported():
    want, _ = rm.resolve(DEFAULTS, bays={"depth": 4.0}, lettering={"offset": 2.0})
    notes = rm.no_effect(want, {"depth": 4.0}, {"offset": 2.0})
    assert len(notes) == 2 and "bay layout is None" in notes[0] and "word is None" in notes[1]
    want, _ = rm.resolve(DEFAULTS, bays={"layout": "Taxi bay", "depth": 4.0})
    assert rm.no_effect(want, {"layout": "Taxi bay", "depth": 4.0}, None) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_road_marking_values.py -v`
Expected: FAIL with `ImportError: cannot import name 'road_marking'`

- [ ] **Step 3: Write `road_marking.py` (settings part)**

Create `src/archicad_mcp/core/road_marking.py`:

```python
"""create_road_marking: place a Road Marking along a curve and drape it onto Meshes and
Slabs (docs/superpowers/specs/2026-10-12-road-marking-tool-design.md).

The Road Marking library part (Archicad GDL repo, Library/Site/Road Marking) keeps its
path as node arrays in its own coordinates, like the Swept Beam, with a cross fall in
percent per node. Its presets live in its Parameter Script; road_marking_presets.json
holds the same table, exported from the part's model by road_marking/export_presets.py
in the GDL repo, whose tests check that the two agree. This module works out the
marking's settings (the current or default values, the preset, the call's changes), how
far the marking reaches across its path, and what the part would draw past the path's
ends.
"""

from __future__ import annotations

import json
from pathlib import Path

from archicad_mcp.core import placed_path as pp

LIBRARY_PART = "Road Marking"
CUSTOM = "Custom"
TABLE = json.loads(Path(__file__).with_name("road_marking_presets.json").read_text(encoding="utf-8"))
PRESETS: dict[str, dict] = TABLE["presets"]
VALUES = TABLE["values"]
STRIPE_KEYS = {"on": "On", "offset": "Offset", "width": "Width", "dash": "Dash", "gap": "Gap",
               "angle": "Angle", "shape": "Shape"}
BAY_KEYS = {"layout": "bayLayout", "width": "bayWidth", "depth": "bayDepth", "zone_width": "zoneWidth",
            "zone_first": "zoneFirst", "bar_width": "barWidth", "symbol": "showSymbol"}
LETTER_KEYS = {"text": "letterText", "length": "letterLength", "width": "letterWidth", "gap": "letterGap",
               "offset": "letterOffset", "align": "letterAlign", "shift": "letterShift",
               "repeat": "letterRepeat"}
# A setting's kind is its name, or a stripe setting's suffix (s1Width is a Width)
BOOLEANS = {"On", "zoneFirst", "showSymbol", "flipSides", "wholeDashes"}
NUMBERS = {"Offset", "Width", "Dash", "Gap", "Angle", "bayWidth", "bayDepth", "zoneWidth", "barWidth",
           "letterLength", "letterWidth", "letterGap", "letterOffset", "letterShift", "letterRepeat",
           "patternShift", "thickness"}
# (minimum, maximum) as the part's Parameter Script ranges them; the part clamps silently, the tool refuses
RANGES = {"Width": (0.01, None), "Dash": (0.01, None), "Gap": (0.0, None), "Angle": (-80.0, 80.0),
          "bayWidth": (0.3, None), "bayDepth": (0.3, None), "zoneWidth": (0.1, None),
          "barWidth": (0.01, None), "letterLength": (0.1, None), "letterWidth": (0.05, None),
          "letterGap": (0.0, None), "letterRepeat": (0.0, None), "thickness": (0.0005, 0.05)}
ENUMS = {"Shape": VALUES["shapes"], "patternAlign": VALUES["aligns"], "bayLayout": VALUES["layouts"],
         "letterText": VALUES["letter_texts"], "letterAlign": VALUES["letter_aligns"]}


class RoadMarkingError(pp.PlacedPathError):
    """The request cannot become a Road Marking."""


def _kind(name: str) -> str:
    return name[2:] if len(name) > 2 and name[0] == "s" and name[1] in "123" else name


def preset_name(marking) -> str:
    """The part's own spelling of a preset name, matched without regard to case."""
    want = str(marking).strip().casefold()
    for name in [*PRESETS, CUSTOM]:
        if name.casefold() == want:
            return name
    raise RoadMarkingError(f"Unknown marking {marking!r}. Valid: {', '.join([*PRESETS, CUSTOM])}.")


def check_value(name: str, value):
    """The value the part takes for this setting, or RoadMarkingError."""
    kind = _kind(name)
    if kind in BOOLEANS:
        if not isinstance(value, bool):
            raise RoadMarkingError(f"{name} must be true or false, got {value!r}.")
        return value
    if kind in NUMBERS:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RoadMarkingError(f"{name} must be a number, got {value!r}.")
        value = float(value)
        lo, hi = RANGES.get(kind, (None, None))
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            span = f"at least {lo:g}" if hi is None else f"{lo:g} to {hi:g}"
            raise RoadMarkingError(f"{name} must be {span}, got {value:g}.")
        return value
    if kind in ENUMS and value not in ENUMS[kind]:
        raise RoadMarkingError(f"{name} must be one of: {', '.join(ENUMS[kind])}; got {value!r}.")
    return value


def resolve(current: dict, marking=None, stripes=None, bays=None, lettering=None, flat=None):
    """The marking's settings after the call (want), and the part's settings right after
    markingType is written (state): the current values, then the preset when the call picks
    one the marking does not already have (it replaces what it sets, as in the dialog),
    then the call's values."""
    state = dict(current)
    if marking is not None:
        name = preset_name(marking)
        if name != current["markingType"] and name in PRESETS:
            state.update(PRESETS[name])
        state["markingType"] = name
    want = dict(state)
    for key, change in (stripes or {}).items():
        if str(key) not in ("1", "2", "3"):
            raise RoadMarkingError(f"stripes keys are '1', '2' and '3', got {key!r}.")
        if not isinstance(change, dict):
            raise RoadMarkingError(f"stripes['{key}'] must be an object such as {{\"width\": 0.12}}.")
        for k, v in change.items():
            if k not in STRIPE_KEYS:
                raise RoadMarkingError(f"Unknown stripe key {k!r}. Valid: {', '.join(STRIPE_KEYS)}.")
            want[f"s{key}{STRIPE_KEYS[k]}"] = check_value(f"s{key}{STRIPE_KEYS[k]}", v)
    for group, keys, given in (("bays", BAY_KEYS, bays), ("lettering", LETTER_KEYS, lettering)):
        for k, v in (given or {}).items():
            if k not in keys:
                raise RoadMarkingError(f"Unknown {group} key {k!r}. Valid: {', '.join(keys)}.")
            want[keys[k]] = check_value(keys[k], v)
    for name, v in (flat or {}).items():
        if v is not None:
            want[name] = check_value(name, v)
    return want, state


def reach(p: dict) -> tuple[float, float]:
    """How far the marking reaches across its path, (u_lo, u_hi), positive to the right,
    after Flip sides: the switched on stripes, the bay layout and the word, and always 0
    (the part model's layout_reach and lettering_reach)."""
    flip = -1.0 if p["flipSides"] else 1.0
    lo = hi = 0.0
    for k in (1, 2, 3):
        if p[f"s{k}On"]:
            c, w = flip * float(p[f"s{k}Offset"]), float(p[f"s{k}Width"])
            lo, hi = min(lo, c - w / 2), max(hi, c + w / 2)
    if p["bayLayout"] != "None":
        d = flip * (float(p["bayDepth"]) + TABLE["line_width"] / 2)
        lo, hi = min(lo, d), max(hi, d)
    if p["letterText"] != "None":
        scale = float(p["letterWidth"]) / TABLE["letter_base_width"]
        width = (sum(TABLE["letter_widths"][ch] * scale for ch in p["letterText"])
                 + float(p["letterGap"]) * (len(p["letterText"]) - 1))
        centre = flip * float(p["letterOffset"])
        lo, hi = min(lo, centre - width / 2), max(hi, centre + width / 2)
    return lo, hi


def _start(align: str, length: float, size: float, shift: float) -> float:
    if align == "End":
        return length - shift - size
    if align == "Centre":
        return (length - size) / 2 + shift
    return shift


def overhang(p: dict, length: float) -> list[str]:
    """Warnings for a bay row or the placed word reaching past the path's ends, where the
    part draws it straight on but the drape does not reach."""
    out = []

    def note(what, size, start):
        before, after = -start, start + size - length
        if before > 0.001 or after > 0.001:
            out.append(f"{what} is {size:.2f} m long and reaches {max(before, 0.0):.2f} m before the start and "
                       f"{max(after, 0.0):.2f} m past the end of the {length:.2f} m path: the part draws it "
                       "there, but it is not draped.")

    layout = p["bayLayout"]
    if layout not in ("None", "Stop end bars"):
        bay, zone = float(p["bayWidth"]), float(p["zoneWidth"])
        cells = {"Disabled bays, shared zone": [bay, zone, bay], "Taxi bay": [bay],
                 "EV charging bay": [bay]}.get(layout, [zone, bay])
        row = sum(cells)
        note("The bay row", row, _start(p["patternAlign"], length, row, float(p["patternShift"])))
    if p["letterText"] != "None":
        size = float(p["letterLength"])
        note(f"The word {p['letterText']}", size, _start(p["letterAlign"], length, size, float(p["letterShift"])))
    return out


def no_effect(p: dict, bays, lettering) -> list[str]:
    out = []
    if p["bayLayout"] == "None" and any(k != "layout" for k in (bays or {})):
        out.append("The bays values have no effect while the bay layout is None.")
    if p["letterText"] == "None" and any(k != "text" for k in (lettering or {})):
        out.append("The lettering values have no effect while the word is None.")
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_road_marking_values.py -v`
Expected: 33 passed

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/core/road_marking.py tests/test_road_marking_values.py
git commit -m "feat: Road Marking settings: presets, stripes, bays, lettering, validation and reach

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The tool (`road_marking.py`, part 2)

**Files:**
- Modify: `src/archicad_mcp/core/road_marking.py` (imports; append)
- Test: `tests/test_road_marking_tool.py`

**Interfaces:**
- Consumes: `placed_path` (Task 1), `drape` (Tasks 3 and 4: `read_surfaces`, `drape`, `PlanPath`, `DrapeError`), `swept_path` (`SweptPath`, `PathError`, `apply_slope`, `ARC_EPS_DEG`), Task 6 functions.
- Produces:
  - `create_road_marking(conn, source_guid=None, points=None, start_height=None, slope_percent=None, marking=None, stripes=None, bays=None, lettering=None, flip_sides=None, pattern_align=None, pattern_shift=None, whole_dashes=None, thickness=None, paint_surface=None, drape_on=None, lift=0.0, drape_tolerance=0.005, path_tolerance=0.002, update_guid=None, dry_run=True) -> dict` (the report, or `{"error": ...}`)
  - `read_parameters(conn, guid) -> dict` (single angles in degrees)
  - `node_params(local, arcs, falls, tol, y_first=False) -> list[dict]`
  - `NOT_LOADED`

- [ ] **Step 1: Write the failing test**

Create `tests/test_road_marking_tool.py`:

```python
"""create_road_marking over a faked Archicad."""
import json
import math

import pytest

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core import road_marking as rm
from tests.conftest import FakeCore

LIB = {"libraryParts": [{"documentName": "Road Marking", "fileName": "Road Marking.gsm", "typeId": "Object"}]}
OK = {"executionResults": [{"success": True}]}
PTS = [{"x": 0, "y": 0, "z": 0}, {"x": 10, "y": 0, "z": 0}]
SLAB = {"type": "Slab", "floorIndex": 1, "details": {
    "zCoordinate": 3.25, "offsetFromTop": 0.0, "holes": [],
    "polygonOutline": [{"x": -5, "y": -10}, {"x": 20, "y": -10}, {"x": 20, "y": 10}, {"x": -5, "y": 10},
                       {"x": -5, "y": -10}]}}
MESH = {"type": "Mesh", "floorIndex": 1, "details": {"level": 0.0, "polygonCoordinates": [
    {"x": 0, "y": 0, "z": 0.0}, {"x": 20, "y": 0, "z": 0.0}, {"x": 20, "y": 20, "z": 2.0}, {"x": 0, "y": 20, "z": 2.0}]}}
STORIES = {"stories": [{"index": 0, "level": 0.0}, {"index": 1, "level": 3.0}]}


def _conn(tapir):
    return ArchicadConnection(19725, core=FakeCore(official={"API.IsAddOnCommandAvailable": {"available": True}},
                                                   tapir=tapir))


def _gdl(**over):
    """A placed marking's parameters as GetGDLParametersOfElements returns them (angles in radians)."""
    v = dict(rm.TABLE["defaults"])
    v.update(nodeX=[0.0, 10.0], nodeY=[0.0, 0.0], nodeZ=[0.0, 0.0], nodeFall=[0.0, 0.0], segArc=[0.0, 0.0])
    v.update(over)
    for k in (1, 2, 3):
        v[f"s{k}Angle"] = math.radians(v[f"s{k}Angle"])
    return {"gdlParametersOfElements": [{"parameters": [{"name": k, "value": x} for k, x in v.items()]}]}


def _sequence(*answers):
    answers = iter(answers)
    return lambda params: next(answers)


def _marking(origin=(0.0, 0.0, 0.0), angle=0.0, floor=1, name="Road Marking"):
    return {"type": "Object", "floorIndex": floor, "details": {
        "libPart": {"name": name}, "origin": dict(zip("xyz", origin)), "angle": angle}}


def _details(by_guid):
    def answer(params):
        return {"detailsOfElements": [by_guid.get(e["elementId"]["guid"], {"error": {"code": 1, "message": "not found"}})
                                      for e in params["elements"]]}
    return answer


def _params(result):
    return {p["name"]: p["value"] for p in result["gdl_parameters"]}


def _writes(conn):
    return [p["elementsWithGDLParameters"][0]["gdlParameters"]
            for c, p in conn._core.calls if c == "SetGDLParametersOfElements"]


def test_a_dry_run_reports_and_lists_the_parameters_in_write_order():
    conn = _conn({"GetAvailableLibraryParts": LIB})
    r = rm.create_road_marking(conn, points=PTS, marking="5333-2 bus stop on the carriageway",
                               lettering={"offset": 1.75})
    names = [p["name"] for p in r["gdl_parameters"]]
    assert names[:3] == ["A", "aDone", "nodeX"]
    assert names.index("nodeFall") < names.index("markingType") < names.index("letterOffset")
    params = _params(r)
    assert params["markingType"] == "5333-2 Bus stop on the carriageway" and params["letterOffset"] == 1.75
    assert "letterText" not in params and "bayLayout" not in params      # the preset writes those
    assert (params["A"], params["aDone"]) == (1.0, 1.0)
    assert r["dry_run"] is True and r["reach"] == {"u_lo": -0.15, "u_hi": 3.05} and r["warnings"] == []
    assert r["origin"] == {"x": 0.0, "y": 0.0, "z": 0.0} and r["story"] is None


def test_the_default_preset_is_not_written_again():
    r = rm.create_road_marking(_conn({"GetAvailableLibraryParts": LIB}), points=PTS)
    assert "markingType" not in _params(r) and r["marking"] == "5121 Broken divider, in settlements"


def test_a_flat_source_takes_start_height_and_slope():
    poly = {"type": "PolyLine", "floorIndex": 2, "details": {
        "coordinates": [{"x": 0, "y": 0}, {"x": 10, "y": 0}], "zCoordinate": 6.0}}
    conn = _conn({"GetDetailsOfElements": _details({"p-1": poly}), "GetAvailableLibraryParts": LIB})
    r = rm.create_road_marking(conn, source_guid="p-1", start_height=0.1, slope_percent=2)
    assert _params(r)["nodeZ"] == [0.0, 0.2] and r["origin"]["z"] == pytest.approx(6.1) and r["story"] == 2


def test_a_draped_write_places_on_the_surface_and_reads_the_name_back():
    conn = _conn({"GetDetailsOfElements": _details({"slab": SLAB}), "GetAvailableLibraryParts": LIB,
                  "CreateObjects": {"elements": [{"elementId": {"guid": "new"}}]},
                  "SetGDLParametersOfElements": OK,
                  "GetGDLParametersOfElements": _gdl(markingType="Parking 90 degrees")})
    r = rm.create_road_marking(conn, points=PTS, marking="Parking 90 degrees", drape_on=["slab"], lift=0.002,
                               dry_run=False)
    assert r["created"] is True and r["element"] == "new" and r["marking_after"] == "Parking 90 degrees"
    placed = [p for c, p in conn._core.calls if c == "CreateObjects"][0]["objectsData"][0]
    assert placed["coordinates"]["z"] == pytest.approx(3.252) and "floorIndex" not in placed
    written = {p["name"]: p["value"] for p in _writes(conn)[-1]}
    assert written["nodeZ"] == [0.0, 0.0] and written["nodeFall"] == [0.0, 0.0]
    assert r["off_surface"] == [] and r["partly_off_surface"] == []


def test_an_update_drapes_the_markings_own_path_turned_as_placed():
    conn = _conn({"GetDetailsOfElements": _details({"m-1": _marking((5.0, 5.0, 3.0), math.pi / 2), "mesh": MESH}),
                  "GetStories": STORIES, "GetAvailableLibraryParts": LIB, "SetGDLParametersOfElements": OK,
                  "GetGDLParametersOfElements": _sequence(_gdl(), _gdl(markingType="Custom", s1Width=0.12))})
    r = rm.create_road_marking(conn, update_guid="m-1", drape_on='["mesh"]', stripes='{"1": {"width": 0.12}}',
                               dry_run=False)
    assert r["updated"] is True and r["marking_after"] == "Custom"
    first, main = _writes(conn)
    assert first == [{"name": "nodeX", "value": [0.0, 1.0]}]      # same node count: nodeX as 0, 1 first
    names = [p["name"] for p in main]
    assert names.index("nodeY") < names.index("nodeX")
    main = {p["name"]: p["value"] for p in main}
    # the path runs from (5, 5) to (5, 15) on the plane z = 3 + 0.1 y, turned back into the part's frame
    assert main["nodeX"] == pytest.approx([0.0, 10.0]) and main["nodeY"] == pytest.approx([0.0, 0.0])
    assert main["nodeZ"] == pytest.approx([0.5, 1.5]) and main["nodeFall"] == pytest.approx([0.0, 0.0])
    assert main["s1Width"] == 0.12 and "markingType" not in main


def test_a_new_preset_on_an_update_replaces_what_it_sets():
    conn = _conn({"GetDetailsOfElements": _details({"m-1": _marking()}), "GetAvailableLibraryParts": LIB})
    conn._core.tapir_responses["GetGDLParametersOfElements"] = _gdl(markingType="Custom", s1Width=0.12)
    params = _params(rm.create_road_marking(conn, update_guid="m-1", points=PTS,
                                            marking="5121 Broken divider, in settlements"))
    assert params["markingType"] == "5121 Broken divider, in settlements" and "s1Width" not in params


def test_a_source_on_another_story_lands_on_the_surface_and_keeps_its_story():
    poly = {"type": "PolyLine", "floorIndex": 0, "details": {
        "coordinates": [{"x": 0, "y": 0}, {"x": 10, "y": 0}], "zCoordinate": 0.0}}
    conn = _conn({"GetDetailsOfElements": _details({"p-1": poly, "slab": SLAB}), "GetAvailableLibraryParts": LIB})
    r = rm.create_road_marking(conn, source_guid="p-1", drape_on=["slab"])
    assert r["origin"]["z"] == pytest.approx(3.25) and r["story"] == 0
    assert _params(r)["nodeZ"] == [0.0, 0.0]


def test_warnings_for_replaced_heights_ignored_slope_overhang_and_no_effect():
    conn = _conn({"GetDetailsOfElements": _details({"slab": SLAB}), "GetAvailableLibraryParts": LIB})
    r = rm.create_road_marking(conn, points=[{"x": 0, "y": 0, "z": 0}, {"x": 3, "y": 0, "z": 0}],
                               marking="5352-1 Disabled bays, shared zone", lettering={"offset": 2.0},
                               drape_on=["slab"], start_height=1.0)
    text = " ".join(r["warnings"])
    assert "replaces the source's own heights" in text and "start_height and slope_percent do not apply" in text
    assert "bay row is 6.30 m long" in text and "word is None" in text


def test_without_the_part_a_dry_run_warns_and_a_write_is_refused():
    conn = _conn({"GetAvailableLibraryParts": {"libraryParts": []}})
    assert any("not loaded" in w for w in rm.create_road_marking(conn, points=PTS)["warnings"])
    assert "not loaded" in rm.create_road_marking(conn, points=PTS, dry_run=False)["error"]


def test_the_call_is_refused_without_exactly_one_source_or_with_bad_input():
    conn = _conn({"GetAvailableLibraryParts": LIB})
    assert "exactly one" in rm.create_road_marking(conn)["error"]
    assert "exactly one" in rm.create_road_marking(conn, source_guid="a", points=PTS)["error"]
    assert "exactly one" in rm.create_road_marking(conn, update_guid="m-1")["error"]
    assert "JSON" in rm.create_road_marking(conn, points=PTS, stripes="{width: 1}")["error"]
    assert "Unknown marking" in rm.create_road_marking(conn, points=PTS, marking="5999")["error"]
    assert "drape_tolerance" in rm.create_road_marking(conn, points=PTS, drape_tolerance=0)["error"]
    beam = _conn({"GetDetailsOfElements": _details({"b-1": _marking(name="Swept Beam")})})
    assert "not a Road Marking" in rm.create_road_marking(beam, update_guid="b-1", points=PTS)["error"]


def test_the_paint_surface_is_found_by_name_or_taken_as_an_index():
    attrs = {"attributes": [{"index": 78, "name": "Paint - Cadmium Yellow"}]}
    conn = _conn({"GetAvailableLibraryParts": LIB, "GetAttributesByType": attrs})
    assert _params(rm.create_road_marking(conn, points=PTS, paint_surface="paint - cadmium yellow"))["paintSurface"] == 78
    assert _params(rm.create_road_marking(conn, points=PTS, paint_surface=76))["paintSurface"] == 76
    assert _params(rm.create_road_marking(conn, points=PTS, paint_surface="32"))["paintSurface"] == 32


def test_path_errors_name_the_road_marking():
    morph = {"type": "Morph", "floorIndex": 0, "details": {"origin": {"x": 0, "y": 0, "z": 0}, "body": {
        "vertices": [{"x": 0, "y": 0, "z": 0}, {"x": 1, "y": 0, "z": 0}, {"x": 2, "y": 0, "z": 0},
                     {"x": 1, "y": 1, "z": 0}],
        "polygons": [], "wireEdges": [{"vertexIds": [0, 1]}, {"vertexIds": [1, 2]}, {"vertexIds": [1, 3]}]}}}
    r = rm.create_road_marking(_conn({"GetDetailsOfElements": _details({"m": morph})}), source_guid="m")
    assert "A Road Marking follows one unbranched line" in r["error"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_road_marking_tool.py -v`
Expected: FAIL with `AttributeError: module 'archicad_mcp.core.road_marking' has no attribute 'create_road_marking'`

- [ ] **Step 3: Add the imports**

In `src/archicad_mcp/core/road_marking.py`, replace

```python
import json
from pathlib import Path

from archicad_mcp.core import placed_path as pp
```

with

```python
import json
import math
from pathlib import Path

from archicad_mcp.core import drape as dr
from archicad_mcp.core import placed_path as pp
from archicad_mcp.core import swept_path as sp
```

- [ ] **Step 4: Append the tool**

Append to `src/archicad_mcp/core/road_marking.py`:

```python
# ---------------------------------------------------------------- the tool

NOT_LOADED = (f"'{LIBRARY_PART}' is not loaded in this project. Load the Road Marking library part "
              "(Library/Site/Road Marking in the office GDL library) and reload libraries.")


def create_road_marking(conn, source_guid=None, points=None, start_height=None, slope_percent=None,
                        marking=None, stripes=None, bays=None, lettering=None, flip_sides=None,
                        pattern_align=None, pattern_shift=None, whole_dashes=None, thickness=None,
                        paint_surface=None, drape_on=None, lift=0.0, drape_tolerance=0.005,
                        path_tolerance=0.002, update_guid=None, dry_run=True) -> dict:
    """Values left as None take the part's defaults on a new marking and keep the marking's
    own on an update; a preset replaces what it sets. Offsets, bays and the word lie to the
    right looking from the first point to the last."""
    try:
        given = {name: pp.from_json_text(value, name, kind) for name, value, kind in (
            ("points", points, list), ("stripes", stripes, dict), ("bays", bays, dict),
            ("lettering", lettering, dict), ("drape_on", drape_on, list))}
        flat = {"flipSides": flip_sides, "patternAlign": pattern_align, "patternShift": pattern_shift,
                "wholeDashes": whole_dashes, "thickness": thickness}
        return _create(conn, source_guid, given, flat, start_height, slope_percent, marking, paint_surface,
                       lift, drape_tolerance, path_tolerance, update_guid, dry_run)
    except sp.PathError as exc:
        return {"error": str(exc).replace("Swept Beam", LIBRARY_PART)}
    except (pp.PlacedPathError, dr.DrapeError) as exc:
        return {"error": str(exc)}


def _create(conn, source_guid, given, flat, start_height, slope_percent, marking, paint_surface, lift,
            drape_tolerance, path_tolerance, update_guid, dry_run) -> dict:
    points, drape_on = given["points"], given["drape_on"]
    own_path = bool(update_guid and drape_on and source_guid is None and points is None)
    if not own_path and (source_guid is None) == (points is None):
        raise RoadMarkingError("Give exactly one of source_guid or points (an update with drape_on may give "
                               "neither: it drapes the marking's own path).")
    if path_tolerance <= 0 or drape_tolerance <= 0:
        raise RoadMarkingError("path_tolerance and drape_tolerance must be positive lengths in metres.")
    warnings: list[str] = []
    frame = pp.existing_frame(conn, update_guid, LIBRARY_PART) if update_guid else None
    current = read_parameters(conn, update_guid) if update_guid else dict(TABLE["defaults"])
    if paint_surface is not None:
        flat["paintSurface"] = _surface_index(conn, paint_surface)
    want, state = resolve(current, marking, given["stripes"], given["bays"], given["lettering"], flat)
    warnings += no_effect(want, given["bays"], given["lettering"])
    u_lo, u_hi = reach(want)
    path, floor_index, flat_source = _path(conn, source_guid, points, own_path, current, frame, path_tolerance)
    nodes, arcs, falls = path.nodes, path.arcs, [0.0] * len(path.nodes)
    drape_report = {"nodes_added_for_drape": 0, "max_along_deviation_mm": 0.0, "max_across_misfit_mm": 0.0,
                    "off_surface": [], "partly_off_surface": []}
    if drape_on:
        d = dr.drape(nodes, arcs, (u_lo, u_hi), dr.read_surfaces(conn, drape_on), tol=drape_tolerance,
                     lift=float(lift), path_tol=path_tolerance)
        nodes, arcs, falls = d.nodes, d.arcs, d.falls
        warnings += d.warnings
        if not flat_source and not own_path:
            warnings.append("Draping replaces the source's own heights.")
        if start_height is not None or slope_percent is not None:
            warnings.append("start_height and slope_percent do not apply to a draped marking.")
        drape_report = {"nodes_added_for_drape": d.added, "max_along_deviation_mm": round(d.max_along * 1000, 2),
                        "max_across_misfit_mm": round(d.max_across * 1000, 2), "off_surface": d.off_surface,
                        "partly_off_surface": d.partly_off_surface}
    elif flat_source:
        nodes = sp.apply_slope(nodes, arcs, nodes[0][2] + (start_height or 0.0), slope_percent or 0.0)
    elif start_height is not None or slope_percent is not None:
        warnings.append("start_height and slope_percent apply to flat sources only; this source keeps its own "
                        "heights.")
    warnings += overhang(want, dr.PlanPath(nodes, arcs, path_tolerance).length)
    loaded = pp.library_part_loaded(conn, LIBRARY_PART)
    if not loaded:
        warnings.append(NOT_LOADED)
    origin, angle = frame if frame else (nodes[0], 0.0)
    same_count = bool(update_guid) and len(nodes) == len(current["nodeX"])
    params = node_params(pp.to_local(nodes, origin, angle), arcs, falls, path_tolerance, y_first=same_count)
    if want["markingType"] != current["markingType"]:
        params.append({"name": "markingType", "value": want["markingType"]})
    params += [{"name": k, "value": want[k]} for k in TABLE["settings"] if not _same(want[k], state[k])]
    report = {"dry_run": dry_run, "marking": want["markingType"], "nodes": len(nodes),
              "arcs": sum(1 for a in arcs[:-1] if abs(a) > sp.ARC_EPS_DEG), **drape_report,
              "max_deviation_mm": round(path.max_deviation * 1000, 2),
              "steepest_cross_fall_percent": round(max(abs(f) for f in falls), 2),
              "reach": {"u_lo": round(u_lo, 3), "u_hi": round(u_hi, 3)},
              "origin": {"x": origin[0], "y": origin[1], "z": origin[2]}, "story": floor_index,
              "warnings": warnings}
    if dry_run:
        report["gdl_parameters"] = params
        return report
    if not loaded:
        raise RoadMarkingError(NOT_LOADED)
    guid = update_guid or pp.place(conn, LIBRARY_PART, origin, floor_index)
    if same_count:
        # the same number of nodes: nodeX as 0, 1, 2 first, then nodeY before the real nodeX, so the
        # part's node merge never compares a new x with an old y (parent spec, "Through the API")
        pp.write_parameters(conn, guid, [{"name": "nodeX", "value": [float(i) for i in range(len(nodes))]}],
                            LIBRARY_PART)
    pp.write_parameters(conn, guid, params, LIBRARY_PART)
    report["element"] = guid
    report["updated" if update_guid else "created"] = True
    report["marking_after"] = read_parameters(conn, guid)["markingType"]
    return report


def _path(conn, source_guid, points, own_path, current, frame, tol):
    """The path in absolute coordinates, the source's story, and whether the source is flat."""
    if own_path:
        (ox, oy, oz), angle = frame
        c, s = math.cos(angle), math.sin(angle)
        nodes = [(ox + x * c - y * s, oy + x * s + y * c, oz + z)
                 for x, y, z in zip(current["nodeX"], current["nodeY"], current["nodeZ"])]
        return sp.SweptPath(nodes, [math.degrees(a) for a in current["segArc"]][:len(nodes)]), None, False
    if source_guid is not None:
        return pp.path_from_element(conn, source_guid, tol, LIBRARY_PART)
    return pp.path_from_points(points, tol), None, False


def read_parameters(conn, guid: str) -> dict:
    """A placed marking's parameters by name, single angles in degrees (the API reads them
    back in radians)."""
    res = conn.tapir("GetGDLParametersOfElements", {"elements": [{"elementId": {"guid": guid}}]})
    entry = (res.get("gdlParametersOfElements") or [{}])[0]
    params = entry.get("parameters")
    if not params:
        raise RoadMarkingError(f"Could not read the parameters of element {guid}: {entry.get('error', entry)}")
    values = {p["name"]: p["value"] for p in params}
    for k in (1, 2, 3):
        values[f"s{k}Angle"] = math.degrees(float(values[f"s{k}Angle"]))
    return values


def _surface_index(conn, value) -> int:
    if isinstance(value, bool):
        raise RoadMarkingError("paint_surface must be a Surface attribute name or index.")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    if isinstance(value, str):
        return pp.attribute_index(conn, "Surface", value)
    raise RoadMarkingError("paint_surface must be a Surface attribute name or index.")


def _same(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        return a is not None and b is not None and abs(float(a) - float(b)) < 1e-9
    return a == b


def node_params(local, arcs, falls, tol, y_first=False) -> list[dict]:
    """The node arrays and grip helpers in write order: A and aDone at 1 m first (the part
    returns A to 1 m), then nodeX so it sets the row count (nodeY first after a same count
    rewrite has written nodeX as 0, 1, 2), the arc angles in radians, every grip helper
    equal to its marker so the Parameter Script reads no pending drag."""
    n = len(local)

    def r6(values):
        return [round(v, 6) + 0.0 for v in values]

    xy = [{"name": "nodeX", "value": r6(p[0] for p in local)},
          {"name": "nodeY", "value": r6(p[1] for p in local)}]
    return ([{"name": "A", "value": 1.0}, {"name": "aDone", "value": 1.0}]
            + (xy[::-1] if y_first else xy)
            + [{"name": "nodeZ", "value": r6(p[2] for p in local)},
               {"name": "nodeFall", "value": r6(falls)},
               {"name": "segArc", "value": [round(math.radians(a), 9) + 0.0 for a in arcs]},
               {"name": "segSag", "value": [0.0] * n}, {"name": "sagDone", "value": [0.0] * n},
               {"name": "insL", "value": [0.0] * n}, {"name": "insDone", "value": [0.0] * n},
               {"name": "extStart", "value": 0.0}, {"name": "extStartDone", "value": 0.0},
               {"name": "extEnd", "value": 0.0}, {"name": "extEndDone", "value": 0.0},
               {"name": "pathTolerance", "value": tol}])
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_road_marking_tool.py tests/test_road_marking_values.py -v`
Expected: all pass (12 tool tests, 33 values tests)
Run: `uv run pytest -q`
Expected: every test passes.

- [ ] **Step 6: Commit**

```bash
git add src/archicad_mcp/core/road_marking.py tests/test_road_marking_tool.py
git commit -m "feat: create_road_marking: place, drape and rewrite a Road Marking in one call

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Registration, the lists that name every tool, and the guide

**Files:**
- Modify: `src/archicad_mcp/server.py` (after the `create_swept_beam` tool)
- Modify: `manifest.json`, `tests/test_tool_annotations.py`, `scripts/build_dashboard.py`, `docs/api-dashboard.html` (regenerated), `README.md`, `scripts/check_wheel_install.py`
- Create: `docs/road-marking.md`
- Test: `tests/test_road_marking_tool.py` (append); `tests/test_tool_annotations.py` and `tests/test_manifest.py` run as they are

**Interfaces:**
- Consumes: `rm.create_road_marking` (Task 7).
- Produces: the MCP tool `create_road_marking` with the arguments of the spec's table plus `port`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_road_marking_tool.py`:

```python
async def _call_tool(monkeypatch, tapir, args):
    import archicad_mcp.server as server_mod
    from fastmcp import Client

    core = FakeCore(official={"API.IsAddOnCommandAvailable": {"available": True}}, tapir=tapir)
    monkeypatch.setattr(server_mod, "get_connection", lambda port: ArchicadConnection(19725, core=core))
    async with Client(server_mod.build_server(mode="full")) as client:
        result = await client.call_tool("create_road_marking", args)
        return json.loads(result.content[0].text)


async def test_the_registered_tool_takes_its_lists_and_objects_as_json_text(monkeypatch):
    tapir = {"GetDetailsOfElements": _details({"slab": SLAB}), "GetAvailableLibraryParts": LIB}
    payload = await _call_tool(monkeypatch, tapir, {
        "points": json.dumps(PTS), "marking": "5351 Taxi bay", "bays": json.dumps({"depth": 4.5}),
        "lettering": json.dumps({}), "stripes": json.dumps({"1": {"on": True, "width": 0.12}}),
        "drape_on": json.dumps(["slab"])})
    assert payload["dry_run"] is True and payload["reach"] == {"u_lo": -0.06, "u_hi": 4.55}
    assert _params(payload)["bayDepth"] == 4.5


async def test_the_registered_tool_explains_text_that_is_not_json(monkeypatch):
    payload = await _call_tool(monkeypatch, {"GetAvailableLibraryParts": LIB}, {"points": "0,0 3,0"})
    assert "points" in payload["error"] and "JSON" in payload["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_road_marking_tool.py tests/test_manifest.py tests/test_tool_annotations.py -v`
Expected: the two new tests FAIL with an unknown tool `create_road_marking`; the manifest and annotation tests still pass.

- [ ] **Step 3: Register the tool**

In `src/archicad_mcp/server.py`, directly after the `create_swept_beam` function (the one ending in `ref_line=ref_line, ref_offset=ref_offset)`), at the same indentation, add:

```python
    from archicad_mcp.core import road_marking as _road

    @mcp.tool(description=(
        "Place a Road Marking (lane lines, zebra crossings, stop and give way lines, "
        "parking rows, disabled, taxi and EV bays, bus and taxi stops, BUS and TAXI "
        "lettering) as the 'Road Marking' library part, and drape it onto Meshes and "
        "Slabs. The path comes from source_guid (a Morph line, Polyline, Line or Arc) "
        "or points [{x, y, z}] in metres. marking is a preset name, for example "
        "'5121 Broken divider, in settlements', '5352-1 Disabled bays, shared zone' or "
        "'5333-2 Bus stop on the carriageway', or 'Custom'. stripes changes stripes: "
        "{\"1\": {on, offset, width, dash, gap, angle, shape}}; bays sets {layout, "
        "width, depth, zone_width, zone_first, bar_width, symbol}; lettering sets "
        "{text, length, width, gap, offset, align, shift, repeat}. Offsets, bays and "
        "the word lie to the right looking from the first point to the last. drape_on "
        "lists Mesh and Slab GUIDs: the cross fall follows the surface, the paint never "
        "sinks below it, and nodes are added until the marking is within "
        "drape_tolerance (metres) of it along the path; lift raises it. update_guid "
        "rewrites a placed marking, keeping its settings unless given; with drape_on and "
        "no source it drapes the marking's own path. DRY-RUN BY DEFAULT: reports nodes, "
        "the marking's reach, drape deviations, stretches off the surface and warnings. "
        "Pass dry_run=false to place or update."),
        **_tool_meta("Create Road Marking", read_only=False, destructive=True))
    @_guarded
    def create_road_marking(source_guid: str | None = None,
                            points: list[dict] | str | None = None,
                            start_height: float | None = None,
                            slope_percent: float | None = None,
                            marking: str | None = None,
                            stripes: dict | str | None = None,
                            bays: dict | str | None = None,
                            lettering: dict | str | None = None,
                            flip_sides: bool | None = None,
                            pattern_align: str | None = None,
                            pattern_shift: float | None = None,
                            whole_dashes: bool | None = None,
                            thickness: float | None = None,
                            paint_surface: str | int | None = None,
                            drape_on: list[str] | str | None = None,
                            lift: float = 0.0, drape_tolerance: float = 0.005,
                            path_tolerance: float = 0.002,
                            update_guid: str | None = None, dry_run: bool = True,
                            port: int | None = None) -> dict:
        return _road.create_road_marking(
            _conn(port), source_guid=source_guid, points=points, start_height=start_height,
            slope_percent=slope_percent, marking=marking, stripes=stripes, bays=bays,
            lettering=lettering, flip_sides=flip_sides, pattern_align=pattern_align,
            pattern_shift=pattern_shift, whole_dashes=whole_dashes, thickness=thickness,
            paint_surface=paint_surface, drape_on=drape_on, lift=lift,
            drape_tolerance=drape_tolerance, path_tolerance=path_tolerance,
            update_guid=update_guid, dry_run=dry_run)
```

- [ ] **Step 4: Name the tool in every list**

1. `manifest.json`: after the `create_swept_beam` entry, add

```json
    {
      "name": "create_road_marking",
      "description": "Place a Road Marking (lanes, crossings, stops, parking and bays, BUS and TAXI) along a line or points, draped onto Meshes and Slabs. Dry-run by default."
    },
```

2. `tests/test_tool_annotations.py`: in `WRITERS`, change `"create_swept_beam",` to `"create_swept_beam", "create_road_marking",`.

3. `scripts/build_dashboard.py`, `WRAPPED_BY`:
   - `"GetDetailsOfElements": ["find_elements", "get_element_data", "create_swept_beam", "create_road_marking"],`
   - `"CreateObjects": ["create_elements", "create_swept_beam", "create_road_marking"],`
   - `"GetAvailableLibraryParts": ["create_swept_beam", "create_road_marking"],`
   - `"GetAttributesByType": ["create_swept_beam", "create_road_marking"],`
   - `"SetGDLParametersOfElements": ["create_swept_beam", "create_road_marking"],`
   - `"GetStories": ["get_project_info", "create_road_marking"],`
   - add the line `"GetGDLParametersOfElements": ["create_road_marking"],` after the `SetGDLParametersOfElements` line.

4. `scripts/build_dashboard.py`, `TOOLS`: after the `create_swept_beam` entry, add

```python
    {"name": "create_road_marking", "cat": "Elements", "mode": "full", "mutates": True,
     "desc": "Place a Road Marking (lane lines, crossings, stop lines, parking, disabled, taxi and EV bays, bus and taxi stops, BUS and TAXI) along a line, polyline, arc, morph line or 3D points, draped onto Meshes and Slabs, or rewrite one in place. Dry-run by default."},
```

5. `README.md`, the "Core (full mode)" list: change `` `create_elements`, `create_swept_beam`, `move_elements`, `` to `` `create_elements`, `create_swept_beam`, `create_road_marking`, `move_elements`, `` (reflow the paragraph's lines if one grows past the others).

6. `scripts/check_wheel_install.py`: change

```python
        run([str(python), "-c", "import archicad_mcp.server, archicad_mcp.gdl.cli"])
```

to

```python
        # road_marking reads its preset table on import: this fails if the JSON is not in the wheel
        run([str(python), "-c", "import archicad_mcp.server, archicad_mcp.gdl.cli, archicad_mcp.core.road_marking"])
```

- [ ] **Step 5: Regenerate the dashboard**

Run: `uv run python scripts/build_dashboard.py`
Expected: it writes `docs/api-dashboard.html`; `git diff --stat docs/api-dashboard.html` shows it changed.

- [ ] **Step 6: Write the guide**

Create `docs/road-marking.md`:

````markdown
# Road Marking

`create_road_marking` places the Road Marking library part along a line or points and
drapes it onto Meshes and Slabs. The part draws lane lines (solid, broken, double,
combined), zebra crossings, stop and give way lines with shark teeth, parking rows,
disabled, taxi and EV bays, bus and taxi stops and the BUS and TAXI lettering, with
presets from the Slovenian rulebook (Uradni list RS 26/24) and TSC 02.401. The part is
not in this repo: it lives in the office GDL library.

## Install

1. Load `Road Marking.gsm` (`Library/Site/Road Marking` in the office GDL library) in
   Library Manager.
2. After every new build of the part, reload libraries (File > Libraries and Objects >
   Reload Libraries). Archicad keeps the old part until then.

Without the part the tool still dry-runs, with a warning that it is not loaded; a write
is refused.

## Draw

- **Source:** `source_guid` (a Polyline, Line, Arc or Morph line) or `points` (`[{x, y,
  z}]` in metres). Always run a dry run first: it reports the nodes, the marking's reach
  and anything that will not sit on the surface.
- **Direction:** offsets, bays and the word lie to the right looking from the first point
  to the last; `flip_sides` puts them on the left. Draw a parking kerb in the direction
  of travel in the aisle, a lane line in the direction of traffic (the letters' tops face
  the last point), a zebra's path across the road.
- **Preset:** `marking`, any case: one of the part's 38 presets or `Custom`. Changing a
  value the preset sets (a 0.12 m line on a 5121) makes the part read Custom, and the
  report's `marking_after` says so.
- **Changes**, as objects (JSON text works too):
  - `stripes`: `{"1": {"width": 0.12}, "2": {"on": true, "offset": 0.3}}`, keys `on`,
    `offset`, `width`, `dash`, `gap`, `angle` (degrees, -80 to 80), `shape` (`Bar`,
    `Triangle, point left`, `Triangle, point right`).
  - `bays`: `{"layout": "Taxi bay", "depth": 4.5}`, keys `layout`, `width`, `depth`,
    `zone_width`, `zone_first`, `bar_width`, `symbol`.
  - `lettering`: `{"text": "BUS", "align": "Centre", "repeat": 20}`, keys `text` (`None`,
    `BUS`, `TAXI`), `length`, `width`, `gap`, `offset`, `align` (`Start`, `Centre`, `End`),
    `shift`, `repeat`.
- **Look:** `pattern_align`, `pattern_shift`, `whole_dashes`, `thickness`, and
  `paint_surface` (a Surface name or index: 18 white, 78 yellow, 76 green in the office
  template).
- **Rewrite:** `update_guid` rewrites a placed marking and keeps its GUID and settings
  unless the call sets them; a new preset replaces what it sets, as in the dialog.

## Draping

`drape_on` lists Meshes and Slabs; where they overlap, the highest wins.

- The tool samples the surface every 0.1 m along the path and every 0.5 m across the
  marking's reach, exactly where the part draws the paint. At each station a straight
  cross fall follows the surface and is raised until no sample is above it, so the paint
  never sinks into the road. Nodes are added until the marking stays within
  `drape_tolerance` (5 mm by default) of those lines along the path; `lift` raises it.
- A Slab's top is exact. A Mesh is triangulated as Archicad triangulates it (its level
  lines are edges), which matched Archicad's own surface to the millimetre in the trial.
- The report: `nodes_added_for_drape`, `max_along_deviation_mm`, `max_across_misfit_mm`
  (how far the paint floats above a surface that is not flat across the marking, a crown
  under a wide zebra), `steepest_cross_fall_percent`, `reach`, `off_surface` and
  `partly_off_surface` (stretches by station).

Limits:

- Where a Mesh's level lines cross each other, Archicad leaves the later segments out;
  the tool does the same and warns, because heights near the crossing can differ from
  Archicad's surface (up to 51 mm in the trial).
- On a regular grid Mesh every cell's diagonal is a free choice, and whether Archicad
  picks the same diagonals as the tool has not been checked yet.
- A bay row or word reaching past the path's ends is drawn there by the part but not
  draped; a warning says how far.
- More than 500 nodes is refused: use a coarser `drape_tolerance` or a shorter source.

## The order of API writes

The tool writes everything in one `SetGDLParametersOfElements` call: `A` and `aDone` at
1 m, the node arrays (`nodeX` first; for an update with the same number of nodes, `nodeX`
as 0, 1, 2 in a call of its own first, then `nodeY` before the real `nodeX`), then
`markingType`, then only the values that differ from the preset. Single angles go in
degrees and read back in radians; `segArc` items go in radians; `nodeFall` is in percent.
````

- [ ] **Step 7: Run every test**

Run: `uv run pytest -q`
Expected: every test passes, including `tests/test_manifest.py`, `tests/test_tool_annotations.py` (the dashboard lists and hints) and the two new client tests.

- [ ] **Step 8: Check the wheel carries the preset table**

```bash
uv build --wheel
```

```bash
uv run --no-project python scripts/check_wheel_install.py
```

Expected: the last line reads `archicad_mcp_server-0.7.1-py3-none-any.whl: installs and starts without the lock (...)`. Then remove the build output, which git does not ignore:

```bash
rm -r dist
```

Run `git status --short`: `dist/` must not be listed.

- [ ] **Step 9: Commit**

```bash
git add src/archicad_mcp/server.py manifest.json tests/test_tool_annotations.py tests/test_road_marking_tool.py scripts/build_dashboard.py docs/api-dashboard.html README.md scripts/check_wheel_install.py docs/road-marking.md
git commit -m "feat: register create_road_marking; manifest, dashboard, README, wheel check and guide

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Live check in MCP-Test, findings and notes

**Files:**
- Modify: `docs/superpowers/specs/2026-10-12-road-marking-tool-findings.md` (section "Live check (after the build)"), `docs/road-marking.md` (the regular grid sentence)
- Scratch (not committed): `live_check.py` and `grid_ifc_check.py` in the session scratchpad
- Second Brain notes (outside the repo)

**Interfaces:**
- Consumes: the whole tool; `tests/fixtures/road_marking/surfaces_mcp_test.json`.

- [ ] **Step 1: Find MCP-Test and confirm it**

Call the `list_instances` MCP tool. If it fails because another instance is stuck, call `execute_read_api_command` with `GetProjectInfo` on each Archicad port (19723 to 19730) until one answers `"projectName": "MCP-Test"`. Note that port; every command below uses it as `<port>`. Do not continue if no instance answers MCP-Test.

- [ ] **Step 2: Save the live check script**

Save as `<scratchpad>/live_check.py`:

```python
"""create_road_marking live in MCP-Test, run from the MCP worktree:
    uv run python <scratchpad>/live_check.py <port> surfaces <scratchpad>/surfaces.json
    uv run python <scratchpad>/live_check.py <port> dry|write <scratchpad>/surfaces.json [case ...]
The test surfaces are the trial's (tests/fixtures/road_marking/surfaces_mcp_test.json) made again 80 m north, on
story 1, with a regular grid Mesh and a Polyline source; nothing already in MCP-Test is touched."""
import json
import sys
from pathlib import Path

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core import road_marking as rm

FIX = json.loads(Path("tests/fixtures/road_marking/surfaces_mcp_test.json").read_text())
SHIFT = 80.0
port, mode, store = int(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
conn = ArchicadConnection(port)
project = conn.tapir("GetProjectInfo").get("projectName")
assert project == "MCP-Test", f"refusing to write to {project!r}"


def made(res):
    return res["elements"][0]["elementId"]["guid"]


def make_surfaces():
    out = {}
    for key, e in FIX["elements"].items():
        entry, d = e["details"], e["details"]["details"]
        if entry["type"] == "Slab":
            out[key] = made(conn.tapir("CreateSlabs", {"slabsData": [{
                "level": d["zCoordinate"], "thickness": d["thickness"],
                "referencePlaneLocation": d["referencePlaneLocation"],
                "polygonCoordinates": [{"x": c["x"], "y": c["y"] + SHIFT} for c in d["polygonOutline"][:-1]]}]}))
        else:
            out[key] = made(conn.tapir("CreateMeshes", {"meshesData": [{
                "floorIndex": entry["floorIndex"], "level": d["level"], "skirtType": d["skirtType"],
                "skirtLevel": d["skirtLevel"],
                "polygonCoordinates": [{"x": c["x"], "y": c["y"] + SHIFT, "z": c["z"]}
                                       for c in d["polygonCoordinates"][:-1]],
                "sublines": [{"coordinates": [{"x": c["x"], "y": c["y"] + SHIFT, "z": c["z"]}
                                              for c in s["coordinates"]]} for s in d.get("sublines", [])]}]}))

    def z(x, y):
        return round(0.05 * (x - 632.5) * (y - 82.5), 4)

    ring = ([(630.0 + i, 80.0) for i in range(6)] + [(635.0, 80.0 + j) for j in range(1, 6)]
            + [(635.0 - i, 85.0) for i in range(1, 6)] + [(630.0, 85.0 - j) for j in range(1, 5)])
    out["mesh_grid"] = made(conn.tapir("CreateMeshes", {"meshesData": [{
        "floorIndex": 1, "level": 0.0, "skirtType": "SolidBodyWithSkirt", "skirtLevel": 0.3,
        "polygonCoordinates": [{"x": x, "y": y, "z": z(x, y)} for x, y in ring],
        "sublines": [{"coordinates": [{"x": 630.0 + i, "y": 80.0 + j, "z": z(630.0 + i, 80.0 + j)}
                                      for i in range(6)]} for j in range(1, 5)]}]}))
    out["polyline"] = made(conn.tapir("CreatePolylines", {"polylinesData": [{
        "floorInd": 1, "coordinates": [{"x": 591, "y": 101}, {"x": 605, "y": 101}, {"x": 619, "y": 103}],
        "arcs": [{"begIndex": 1, "endIndex": 2, "arcAngle": 0.3}]}]}))
    return out


def pts(*xy):
    return [{"x": x, "y": y, "z": 3.0} for x, y in xy]


CASES = {   # name: (arguments, what to expect)
    "zebra": (dict(points=pts((615, 79.8), (615, 88.2)), marking="5231 Pedestrian crossing",
                   drape_on=["mesh_crown"]), "3 nodes added (edges, crown), cross fall 2 %, both ends off"),
    "bus_lane": (dict(points=pts((591, 87.5), (611, 87.5)), marking="5111-1 Bus lane line",
                      lettering={"repeat": 12}, drape_on=["mesh_crown"]), "BUS twice on the crown's slope"),
    "taxi_lane": (dict(points=pts((611, 81.5), (591, 81.5)), marking="5111-2 Taxi lane line",
                       drape_on=["mesh_crown"]), "TAXI reading towards x 591"),
    "taxi_lane_broken": (dict(points=pts((619, 91), (591, 91)), marking="5121-2 Taxi lane line, broken",
                              drape_on=["mesh_crossing_lines"]), "the crossing level lines warning"),
    "bus_lane_broken": (dict(points=pts((619, 95), (591, 95)), marking="5121-1 Bus lane line, broken",
                             drape_on=["mesh_crossing_lines"]), "the crossing level lines warning"),
    "lane_from_polyline": (dict(source_guid="polyline", marking="5121 Broken divider, in settlements",
                                drape_on=["mesh_road_edges"]), "the arc split, nodes added, story 1"),
    "bus_stop_layby": (dict(points=pts((591, 107.5), (605, 107.5)), marking="5333 Bus stop in a lay-by",
                            drape_on=["mesh_road_edges"]), "BUS centred"),
    "taxi_stop_layby": (dict(points=pts((606, 107.5), (619, 107.5)), marking="5334 Taxi stop in a lay-by",
                             drape_on=["mesh_road_edges"]), "TAXI centred"),
    "disabled": (dict(points=pts((561, 91.5), (565, 91.5)), marking="5352 Disabled bay",
                      drape_on=["slab_top"]), "zone then bay at 3.25"),
    "disabled_pair": (dict(points=pts((566, 91.5), (572.5, 91.5)), marking="5352-1 Disabled bays, shared zone",
                           drape_on=["slab_top"]), "reach 0 to 5.45"),
    "taxi_bay": (dict(points=pts((573, 91.5), (575.7, 91.5)), marking="5351 Taxi bay",
                      drape_on=["slab_top"]), "TAXI read from the aisle"),
    "ev_bay": (dict(points=pts((576.5, 91.5), (579.2, 91.5)), marking="5354 EV charging bay",
                    drape_on=["slab_top"]), "green, the car and plug"),
    "disabled_parallel": (dict(points=pts((561, 85.8), (570.2, 85.8)), marking="5352-2 Disabled bay, parallel",
                               drape_on=["slab_top"]), "bay then zone"),
    "taxi_stop_carriageway": (dict(points=pts((561, 83.0), (575, 83.0)),
                                   marking="5334-1 Taxi stop on the carriageway", drape_on=["slab_top"]),
                              "L shaped ends, TAXI once"),
    "bus_stop_short": (dict(points=pts((576, 83.0), (579.9, 83.0)), marking="5333-2 Bus stop on the carriageway",
                            drape_on=["slab_top"]), "a warning: BUS (4.00 m) reaches past the 3.90 m path"),
    "ev_partly_off": (dict(points=pts((582.5, 83.5), (585.5, 83.5)), marking="5354 EV charging bay",
                           drape_on=["slab_corebottom"]), "partly off from 0 to 3 m, at 3.80"),
    "lane_on_grid": (dict(points=pts((630.5, 82.3), (634.5, 82.3)), marking="5121 Broken divider, in settlements",
                          drape_on=["mesh_grid"]), "nodes added on the saddle"),
}


def run(surfaces, names, dry):
    for name in names or CASES:
        args, expect = CASES[name]
        args = dict(args, drape_on=[surfaces[s] for s in args["drape_on"]])
        if "source_guid" in args:
            args["source_guid"] = surfaces[args["source_guid"]]
        result = rm.create_road_marking(conn, dry_run=dry, **args)
        result.pop("gdl_parameters", None)
        print(f"{name}: expect {expect}\n  {json.dumps(result)}")


if mode == "surfaces":
    out = make_surfaces()
    store.write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))
else:
    run(json.loads(store.read_text()), sys.argv[4:], mode == "dry")
```

- [ ] **Step 3: Make the test surfaces**

From the MCP worktree: `uv run python <scratchpad>/live_check.py <port> surfaces <scratchpad>/surfaces.json`
Expected: seven GUIDs printed (`slab_top`, `slab_corebottom`, `mesh_crown`, `mesh_crossing_lines`, `mesh_road_edges`, `mesh_grid`, `polyline`).

- [ ] **Step 4: Dry run every case**

Run: `uv run python <scratchpad>/live_check.py <port> dry <scratchpad>/surfaces.json`
Expected: each case's report agrees with its "expect" text; no `error`. Compare with the trial (findings, "The trial markings"): the zebra adds 3 nodes with a 2.00 % cross fall; the lane line from the Polyline reports story 1; `bus_stop_short` carries the word warning; `ev_partly_off` has `partly_off_surface` `[{"from": 0.0, "to": 3.0}]`. Stop and fix the code (with a test) on any disagreement before writing.

- [ ] **Step 5: Write every case and read back**

Run: `uv run python <scratchpad>/live_check.py <port> write <scratchpad>/surfaces.json`
Expected: every case `created: true` with `marking_after` equal to its preset. Then read the bounding boxes of the new elements with `execute_read_api_command` `Get3DBoundingBoxes`: each `zMin` equals the surface height at the marking's first node (3.25 on `slab_top`, 3.80 on `slab_corebottom`, the Mesh height for the Meshes).

- [ ] **Step 6: Update a marking in place**

Take the `bus_lane` element GUID from Step 5's output and run from the worktree:

```bash
uv run python -c "
import json, sys
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core import road_marking as rm
conn = ArchicadConnection(int(sys.argv[1]))
assert conn.tapir('GetProjectInfo')['projectName'] == 'MCP-Test'
surf = json.load(open(sys.argv[3]))
r = rm.create_road_marking(conn, update_guid=sys.argv[2], drape_on=[surf['mesh_crown']], stripes={'1': {'width': 0.12}}, dry_run=False)
print(json.dumps(r))
" <port> <bus_lane GUID> <scratchpad>/surfaces.json
```

Expected: `updated: true`, `marking_after: "Custom"`, the same node count as before.

- [ ] **Step 7: Compare the regular grid with Archicad's own triangles**

Ask the user before this step: it saves MCP-Test as IFC into the scratchpad with `IFCFileOperation` (in the trial MCP-Test stayed the open project). With their go-ahead, call `execute_write_api_command` `IFCFileOperation` with `{"method": "save", "ifcFilePath": "<scratchpad>/live.ifc", "fileType": "ifc"}` and `confirm: true` on `<port>` after checking the project name. Then save as `<scratchpad>/grid_ifc_check.py`:

```python
"""Does Archicad split a regular grid Mesh's cells as the tool does? From the MCP worktree:
    uv run --with ifcopenshell --with numpy python <scratchpad>/grid_ifc_check.py <port> <ifc> <mesh guid>"""
import sys

import ifcopenshell
import ifcopenshell.geom
import numpy as np

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core import drape as dr

port, ifc_path, guid = int(sys.argv[1]), sys.argv[2], sys.argv[3]
conn = ArchicadConnection(port)
assert conn.tapir("GetProjectInfo")["projectName"] == "MCP-Test"
surface = dr.read_surfaces(conn, [guid]).surfaces[0]
x0, y0, x1, y1 = surface.box
settings = ifcopenshell.geom.settings()
settings.set("use-world-coords", True)
shape = ifcopenshell.geom.create_shape(settings, ifcopenshell.open(ifc_path).by_type("IfcSite")[0])
v = np.array(shape.geometry.verts).reshape(-1, 3)
tris = []
for a, b, c in np.array(shape.geometry.faces).reshape(-1, 3):
    p, q, r = v[a], v[b], v[c]
    n = np.cross(q - p, r - p)
    xs, ys = (p[0], q[0], r[0]), (p[1], q[1], r[1])
    if n[2] / np.linalg.norm(n) > 0.3 and min(xs) >= x0 - 1e-6 and max(xs) <= x1 + 1e-6 \
            and min(ys) >= y0 - 1e-6 and max(ys) <= y1 + 1e-6:
        tris.append((p, q, r))


def archicad(x, y):
    for p, q, r in tris:
        d = (q[1] - r[1]) * (p[0] - r[0]) + (r[0] - q[0]) * (p[1] - r[1])
        l1 = ((q[1] - r[1]) * (x - r[0]) + (r[0] - q[0]) * (y - r[1])) / d
        l2 = ((r[1] - p[1]) * (x - r[0]) + (p[0] - r[0]) * (y - r[1])) / d
        if min(l1, l2, 1 - l1 - l2) > -1e-7:
            return l1 * p[2] + l2 * q[2] + (1 - l1 - l2) * r[2]
    return None


worst, n = 0.0, 0
for i in range(int((x1 - x0) / 0.25) + 1):
    for j in range(int((y1 - y0) / 0.25) + 1):
        x, y = x0 + i * 0.25, y0 + j * 0.25
        za, zt = archicad(x, y), surface.height(x, y)
        if za is not None and zt is not None:
            n += 1
            worst = max(worst, abs(za - zt))
print(f"{len(tris)} Archicad triangles, {len(surface.triangles)} the tool's, {n} points: worst {worst * 1000:.2f} mm")
```

Run: `uv run --with ifcopenshell --with numpy python <scratchpad>/grid_ifc_check.py <port> <scratchpad>/live.ifc <mesh_grid GUID>`
Expected: `50 Archicad triangles, 50 the tool's, ...` and a worst difference. Then in `docs/road-marking.md` replace the bullet that begins `On a regular grid Mesh every cell's diagonal is a free choice` (two lines, ending `has not been checked yet.`) with the result:
- worst under 0.5 mm: `On a regular grid Mesh every cell's diagonal is a free choice; Archicad and the tool picked the same ones in the live check (12.10.2026 build).`
- otherwise: `On a regular grid Mesh every cell's diagonal is a free choice, and Archicad picks other diagonals than the tool in some cells: heights there can differ by up to <worst> mm (a saddle of 5 cm a metre in the live check).`

- [ ] **Step 8: Screenshots from the user**

Ask the user for: a section along y at x = 615 from y 79 to 89 (the zebra over the crown), a section along y at x = 564 from y 79 to 93 (bays and stops on the Slab), and a 3D view of x 560 to 636, y 78 to 109 on story 1. Check in them that the paint sits on each surface along its whole length. Any marking that floats or sinks visibly is a bug: find it with superpowers:systematic-debugging before going on.

- [ ] **Step 9: Record the live check**

Append to `docs/superpowers/specs/2026-10-12-road-marking-tool-findings.md`:

```markdown
## Live check (after the build)

<date>, MCP-Test on port <port>, the build at <commit>. Test surfaces made again 80 m north (GUIDs in the table
below). Every case of the live check script: <one line per case: name, element GUID, nodes, nodes added, reach,
warnings, marking_after>. Update of the bus lane line in place: <result>. Regular grid against Archicad's IFC
triangles: <the grid_ifc_check line>. The user's sections and 3D view: <what they show>.
```

Fill every `<...>` from Steps 3 to 8 (no placeholder may remain), then replace the section "Sections" line `The user's section and 3D screenshots of the trial markings are the visual check; noted here when they arrive.` with what the trial screenshots showed, if the user sent them, or `No screenshots of the trial markings were taken; the live check's sections stand for them.`

- [ ] **Step 10: Commit**

```bash
git add docs/superpowers/specs/2026-10-12-road-marking-tool-findings.md docs/road-marking.md
git commit -m "docs: create_road_marking live check in MCP-Test

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 11: Update the Second Brain**

Read `~/Library/Mobile Documents/com~apple~CloudDocs/Second Brain/AGENTS.md` first and follow its rules. Then:
- `20_projects/_active/archicad-gdl-objects/road-marking-261007.md`: a dated Status line (the tool built on `feature/road-marking` in the MCP repo, the GDL branch `feature/road-marking-tool`, test counts, the live check's result, waiting for the merge decision), and under Documents the MCP spec, findings and this plan.
- `20_projects/_active/archicad-mcp/archicad-mcp.md`: a brief status line for `create_road_marking`.
- `90_meta/changelog.md`: one dated line.

Merging (both repos), pushing, and a version bump or release are the user's decisions: finish with superpowers:finishing-a-development-branch.
