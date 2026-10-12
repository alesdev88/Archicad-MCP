# Road Marking tool (`create_road_marking`): Design

**Date:** 2026-10-12
**Status:** Approved in brainstorming (12.10.2026). Trial run done the same day; its findings
(`2026-10-12-road-marking-tool-findings.md`) are folded in below. Next: the implementation plan.
**Repo:** this one (the tool). The library part is private: `Road Marking` in the Archicad GDL repo
(`~/Documents/Archicad GDL`, `road_marking/`), installed in `Library/Site/Road Marking`.
**Parent spec:** `~/Documents/Archicad GDL/docs/superpowers/specs/2026-10-07-road-marking-design.md`
(the library part, its parameters, presets, bay layouts and lettering). This spec replaces the
parent's section "MCP Tool: `create_road_marking`"; everything else there stands.

## Purpose

Turn a Line, Polyline, Arc, Morph line or a list of points into a placed `Road Marking`, with any
preset, stripe, bay, stop and lettering setting the part has, and drape it onto Meshes and Slabs so
that a section shows the paint on the road surface along its whole length.

Success means:

- A polyline with arcs becomes a marking whose plan matches the polyline (as `create_swept_beam`
  does for its beam).
- A zebra across a crowned test Mesh, a lane line along it, and a disabled bay pair, a bus stop with
  end bars and BUS, and a taxi bay on a raised test Slab all sit on their surface in Archicad
  sections, within `drape_tolerance` along the path; where a surface is not flat across a wide
  marking, the report says by how much.
- Every bay, stop and lettering preset can be placed and draped through the tool.

## User decisions (12.10.2026)

- **One marking per call now, a batch form later.** The drape code reads and triangulates the
  surfaces into one `SurfaceSet` that a later batch call can build once and reuse for many markings.
- **The library part stays private.** Its source stays in the Archicad GDL repo; the tool reports the
  part missing wherever it is not loaded. The preset table the tool needs (rulebook and TSC values,
  nothing from a project) is in this public repo.
- **Settings grouped per dialog page:** `marking` (the preset), `stripes`, `bays`, `lettering`; the
  pattern and look settings stay flat arguments.
- From the parent spec: drape onto Mesh and Slab only (not Morph, Roof or Shell); dry run by default;
  live checks in MCP-Test only.

## Facts the design rests on

- Tapir reports z as an absolute elevation for morphs, polylines, lines, arcs and placed objects, and
  `CreateObjects` derives the story from an absolute z (`swept_beam.py`).
- MCP-Test ran on port 19725 on 12.10.2026 (Archicad 29, Tapir 1.5.10); 19724 had a modal dialog
  open. Ports move when Archicad restarts: `list_instances` and the project name before every write.
- **Slab.** `GetDetailsOfElements` gives `level`, `offsetFromTop`, `zCoordinate`, `thickness`,
  `referencePlaneLocation` (Top, CoreTop, CoreBottom, Bottom), `polygonOutline`, `polygonArcs` and
  `holes`. Its top is `zCoordinate + offsetFromTop` (`zCoordinate` absolute, the story's level plus
  `level`): three Slabs with Top, CoreTop and CoreBottom reference planes on stories 0 and 1 agree with
  `Get3DBoundingBoxes` (trial).
- **Mesh.** `GetDetailsOfElements` gives `level`, `polygonCoordinates` (3D), `polygonArcs`, `holes`
  (3D) and `sublines` (the level lines, 3D polylines), outlines closed (the first point repeated). It
  gives no triangles and no `zCoordinate`: a point's absolute z is the story's level (`GetStories`)
  plus `level` plus its z (trial).
- **Archicad triangulates a Mesh as a constrained Delaunay** of its outline, hole and level line
  points with the level lines as edges: 0.0 mm from Archicad's own triangles (read from an IFC export)
  on two test Meshes, where a plain Delaunay was 120 mm and 0.91 mm off. A level line segment that
  crosses an earlier one is left out (no points added); near such crossings Archicad's triangles are
  not quite Delaunay, and the tool's heights were up to 51 mm off there (trial).
- **Stanezice (August 2026)** lost points that fell exactly on Mesh outlines without an edge
  fallback (hence the 1 mm edge rule below).
- **The part through the API** (parent spec, "Through the API"): lengths in metres; `segArc` items in
  radians; `s1Angle` to `s3Angle` written in degrees and read back in radians; `nodeFall` in percent;
  `A` and `aDone` return to 1 m; a preset written after a stripe value replaces it, so `markingType`
  goes first. On Archicad 29 with Tapir 1.5.10 an API write of a node array does not trigger the
  Parameter Script's merge. `GetGDLParametersOfElements` returns strings as text, arrays as lists,
  the paint surface as an index and single angles in radians (trial).
- **Placement** with `CreateObjects` and only the name and absolute coordinates sets dimensions 1 by
  1 by 1 and `useFixSize: true`, and a marking placed from points lands on the story its z falls in
  (trial). A Road Marking's bounding box top is 1 m above its bottom (the nominal box), not its paint.

## Arguments

| Argument | Type | Default | Meaning |
| --- | --- | --- | --- |
| `source_guid` | string | | A Morph line, Polyline, Line or Arc |
| `points` | list of `{x, y, z}` | | Points in metres, absolute z |
| `start_height` | number | 0 | Flat sources, not draped: metres above the source |
| `slope_percent` | number | 0 | Flat sources, not draped: slope along the path |
| `marking` | string | | A preset name (any case) or "Custom" |
| `stripes` | object | | Keys "1", "2", "3"; each `{on, offset, width, dash, gap, angle, shape}` |
| `bays` | object | | `{layout, width, depth, zone_width, zone_first, bar_width, symbol}` |
| `lettering` | object | | `{text, length, width, gap, offset, align, shift, repeat}` |
| `flip_sides` | boolean | | Mirror the marking about the path |
| `pattern_align` | string | | "Start", "Centre", "End", "Both ends" |
| `pattern_shift` | number | | Moves the pattern (and a bay row) along the path |
| `whole_dashes` | boolean | | Drop dashes the path ends would cut |
| `thickness` | number | | Paint thickness, 0.0005 to 0.05 |
| `paint_surface` | string or integer | | A Surface attribute name or index |
| `drape_on` | list of strings | | Mesh and Slab GUIDs |
| `lift` | number | 0 | Metres above the surface |
| `drape_tolerance` | number | 0.005 | Metres; see Draping |
| `path_tolerance` | number | 0.002 | Curve fit of the source, and the part's `pathTolerance` |
| `update_guid` | string | | A placed Road Marking to rewrite, keeping its GUID |
| `dry_run` | boolean | true | Report only |
| `port` | integer | | Archicad instance |

- Exactly one of `source_guid` and `points`, except an update with `drape_on`, which may give neither
  (it drapes the marking's own path).
- `stripes`, `bays`, `lettering`, `points` and `drape_on` may arrive as JSON text (the Swept Beam
  rule: Claude Code sends nullable list and object fields as text).
- **Mapping to the part.** Stripe keys map to `sNOn`, `sNOffset`, `sNWidth`, `sNDash`, `sNGap`,
  `sNAngle` (degrees) and `sNShape`. `bays` maps to `bayLayout`, `bayWidth`, `bayDepth`, `zoneWidth`,
  `zoneFirst`, `barWidth`, `showSymbol`. `lettering` maps to `letterText`, `letterLength`,
  `letterWidth`, `letterGap`, `letterOffset`, `letterAlign`, `letterShift`, `letterRepeat`. The rest:
  `flipSides`, `patternAlign`, `patternShift`, `wholeDashes`, `thickness`, `paintSurface`.
- **Validation.** Unknown preset names, keys, shapes (`Bar`, `Triangle, point left`, `Triangle, point
  right`), layouts (`None`, `Disabled bay`, `Disabled bays, shared zone`, `Disabled bay, parallel`,
  `Stop end bars`, `Taxi bay`, `EV charging bay`), words (`None`, `BUS`, `TAXI`) and alignments are
  refused with the valid values. Values outside the part's ranges are refused, not clamped (the part
  would clamp silently): widths and dashes at least 0.01, gaps at least 0, angles -80 to 80, bay width
  and depth at least 0.3, zone width at least 0.1, bar width at least 0.01, letter length at least
  0.1, letter width at least 0.05, letter gap and repeat at least 0, thickness 0.0005 to 0.05.
  Lettering `align` takes Start, Centre or End.

## Resolving the values

The tool needs the marking's final values before it writes anything: to work out its reach for
draping, and to write only what differs from the preset.

- **Preset table.** `core/road_marking_presets.json` holds, for each of the 38 presets, the values the
  preset writes, by parameter name, as the model's `apply_preset` leaves them (parent spec, "How
  presets apply"), plus the part's defaults, the letter widths at `letterWidth` 0.50 (A and X 0.60;
  B, S, T and U 0.50; I 0.15) and the GDL repo commit it was exported from. `road_marking/
  export_presets.py` in the GDL repo writes it.
- **New marking:** the part's defaults, then the preset's values when `marking` names a preset, then
  the call's values.
- **Update:** the marking's own values (`GetGDLParametersOfElements`; single angles converted from
  radians to degrees), then the preset's values when `marking` names a preset other than the
  marking's own, then the call's values. As in the dialog, a new preset replaces what it sets;
  `flipSides`, `patternShift`, `thickness` and the plan settings stay.
- **Reach** (metres across the path, after Flip): the switched on stripes (`offset +- width / 2`), the
  layout (`0` to `bayDepth + 0.05`, mirrored with Flip) and the word (centred `letterOffset` to the
  right, mirrored with Flip, as wide as its letters and gaps at `letterWidth / 0.50` scale). The reach always includes 0, the
  path itself. These are the model's `layout_reach` and `lettering_reach`.

## Writing

- **Placement:** `CreateObjects` at the first node, as the parent spec says (dimensions 1 by 1 by 1,
  `useFixSize: true`), with the source's story when known.
- **Order** (parent spec): `A = 1.0` and `aDone = 1.0`; the node arrays and grip helpers (`nodeX`
  first; to rewrite the same number of nodes, `nodeX` as 0, 1, 2 and so on, then `nodeY`, then the
  real `nodeX`); `markingType`; the values that differ from the preset; the rest. The node arrays
  are always written; a setting equal to the marking's current value (after the preset) is not. All
  of it goes in one `SetGDLParametersOfElements` call (trial: the 5333-2 bus stop kept a
  `letterOffset` written after `markingType` in the same call).
- **Read back.** After a write the tool reads `markingType` back and reports it as `marking_after`:
  a call that changes a value the preset sets (a 0.12 m line on a 5121) leaves the part reading
  "Custom", and the report says so instead of claiming the preset.

## Draping (`core/drape.py`)

Pure Python, no new dependency.

### Surfaces

`read_surfaces(conn, guids) -> SurfaceSet`; `SurfaceSet.height(x, y)` returns the highest surface
containing the point, or None. A batch form later builds the set once.

- **Slab:** its outline and holes (arcs as chords within 1 mm) at `zCoordinate + offsetFromTop`.
- **Mesh:** a constrained Delaunay of its outline, hole and level line points (points at the same
  plan position within 1 micrometre are one point): Delaunay by incremental insertion with Lawson
  flips, then the outline, the holes and the level lines in that order as edges by Sloan's edge
  flipping, with a vertex to triangle map so finding where an edge starts walks round the vertex. A
  segment that crosses an earlier edge is left out and counted (warning). Triangles whose centroid
  lies inside the outline and outside the holes are kept; heights inside a triangle are linear; point
  z is the story's level (`GetStories`) plus `level` plus its z. Triangles are bucketed on a grid for
  lookups. Measured: 0.35 s for 5000 points, 1.7 s for 20000, 5 to 7 microseconds a lookup.
- **Edges:** a point within 1 mm of an outline counts as inside (on a Mesh, the nearest triangle's
  plane gives its height).
- Anything in `drape_on` that is not a Mesh or a Slab is refused.

### Sampling

- **Across:** both ends of the reach, 0, and every 0.5 m between (a 5.45 m bay is not judged from
  three points).
- **Along:** every 0.1 m of plan length on every segment (on the arc for arcs), and at every node.
- **Where a sample lands:** the part's own mapping (parent spec, Master Script step 7): the path
  sampled as the part samples it (arcs within `path_tolerance`), offset vectors the mitres at the
  samples and linear between them, so the tool samples the surface where the paint is drawn.
- **At each station:** a least squares line in `u` through the lateral heights gives the cross fall
  `f (s)`; the line is then raised until no lateral sample lies above it, which gives the height
  `z0 (s)` at `u = 0`. The paint never sinks into the surface (a least squares line through a crown
  lies under the ridge: 1.5 mm under it for a 15 cm line on a 3 % crown, trial). How far the paint
  floats at most (a crown under a wide zebra) is the across misfit at that station.

### Nodes

- The source's nodes stay. On each segment, Douglas and Peucker adds a node at the worst station
  while the marking's heights there (`z + u * f / 100`, linear between nodes) differ by more than
  `drape_tolerance` from the station's fitted line at any of its lateral samples on a surface. It
  compares with the
  fitted line, not the raw surface, so a crown does not add a node every 0.1 m.
- A node added on an arc splits the arc in proportion to plan length.
- **No sinking** (live check, 12.10.2026): the station lines touch the surface only at the cross
  samples, and the straight pieces between nodes may stray below them by up to `drape_tolerance`;
  in MCP-Test the paint dipped up to 7.4 mm under a warped Mesh, burying 3 mm paint. So the
  marking is checked against the surface on a 5 cm grid along and across (coarser only on a marking
  that would need more than 250000 points), and both nodes of each segment are raised by its deepest
  dip. The paint is then nowhere below the surface but for the grid's resolution (under 1 mm in the
  live check).
- `nodeZ` is `z0 + lift`, `nodeFall` is `f` in percent.
- More than 500 nodes is refused, naming the stretch that needs them (a coarser `drape_tolerance` or
  a shorter source).
- Draping replaces a Morph line's or the points' own heights (a warning says so); `start_height`
  and `slope_percent` do not apply (a warning says so when given).

### Off the surface and past the ends

- A lateral sample with no surface under it is left out of its station's fit. A station with one
  sample on a surface takes the cross fall of the nearest station with two or more, and its height
  from that one sample. A station with none takes the height and cross fall of the nearest station
  that has some. `u = 0` itself need not be on a surface (a kerb line drawn a little outside a Slab).
- The report lists stretches (from and to station) with no sample on a surface (`off_surface`) and
  with some samples off (`partly_off_surface`). No surface anywhere along the path is refused.
- A bay row or word reaching before station 0 or past the path's end is drawn there by the part
  (straight on, with the end node's height and cross fall) but not draped; a warning gives how far it
  reaches.

## Report

`dry_run`, `marking` (the preset asked for), `nodes`, `arcs`, `nodes_added_for_drape`,
`max_deviation_mm` (the source fit), `max_along_deviation_mm` (what the nodes left before the
no-sink raise, within the tolerance), `max_across_misfit_mm` (how far the paint floats above the
surface at most, on the check grid after the raise),
`steepest_cross_fall_percent`, `reach` (`u_lo`, `u_hi`),
`off_surface`, `partly_off_surface`, `origin`, `story`, `warnings`. A dry run adds `gdl_parameters`
(in write order); a write adds `element`, `created` or `updated`, and `marking_after`.

Warnings, besides those above: the part is not loaded (a dry run warns, a write is refused); the
across misfit exceeds `drape_tolerance` (with the stations); a Mesh's level line segments were left
out where they cross (with the count); a `bays` value with layout None or a `lettering` value with
word None has no effect.

## Files

- `src/archicad_mcp/core/placed_path.py`: from `swept_beam.py`, unchanged in behaviour: JSON text
  parsing, the path from a Morph line, Polyline, Line, Arc or points, `_details`, the library part
  check, the placed element's frame, the transform to local coordinates, placement and the parameter
  write, each taking the library part name where it needs one. `swept_beam.py` imports them.
- `src/archicad_mcp/core/cdt.py` (the constrained Delaunay triangulation, on its own so drape.py
  stays readable), `src/archicad_mcp/core/drape.py`, `src/archicad_mcp/core/road_marking.py`,
  `src/archicad_mcp/core/road_marking_presets.json`.
- `server.py` registration; `manifest.json`; `tests/test_tool_annotations.py`;
  `scripts/build_dashboard.py` (command map: `GetDetailsOfElements`, `CreateObjects`,
  `GetAvailableLibraryParts`, `GetAttributesByType`, `SetGDLParametersOfElements`,
  `GetGDLParametersOfElements`) and the regenerated `docs/api-dashboard.html`; the README tool list;
  the guide `docs/road-marking.md` (Swept Beam guide style: install, draw, presets, Flip, bays, stops
  and lettering, draping and its limits, the order of API writes).
- The "not loaded" message: load the `Road Marking` library part (`Library/Site/Road Marking`) and
  reload libraries.
- **Archicad GDL repo** (a small branch): `road_marking/export_presets.py`, and the static test that
  checked preset names extended to the whole JSON, reading the MCP repo from `ARCHICAD_MCP_REPO`
  (default `~/Developer/Archicad MCP`) so a worktree can be checked before the merge. The parent
  spec's MCP section gets a one line pointer to this spec.
- The main checkout holds uncommitted changes (Tapir 1.7.0 definitions, `docs/api-dashboard.html`, a
  Swept Beam `params.toml` edit); the work happens in the worktree and leaves them alone. At the merge
  the dashboard may need the user's change set aside first.

## Tests

- `tests/test_drape.py`: Slab heights inside, in a hole, outside and on the edge; Mesh triangulation
  exact at points and linear inside triangles; the tool's heights against Archicad's grid heights
  (`tests/fixtures/road_marking/`: `surfaces_mcp_test.json` with the test Slabs' and Meshes' details
  and the stories, `archicad_heights_crown.json` and `archicad_heights_road_edges.json` within 1
  micrometre, `archicad_heights_crossing_lines.json` with the three dropped segments); both Slab tops
  against their bounding boxes; a level line through an outline point; the highest surface wins; no nodes added on a plane, nodes where the
  surface bends; a crown gives across misfit without extra nodes and keeps the paint on the ridge;
  arcs split in proportion; one and
  no lateral samples on the surface; off and partly off stretches; lift added; the 500 node cap.
- `tests/test_road_marking_tool.py` with the fake connection (as `test_swept_beam_tool.py`): the dry
  run report and write order; preset, stripe, bay and lettering validation and ranges; the reach of
  every layout and word, with Flip; an update keeps settings and a new preset replaces what it sets;
  draping an existing marking without a source; the past the end and no effect warnings; the Custom
  read back; JSON text arguments; the registered tool through the client.
- `tests/test_road_marking_presets.py`: the JSON's shape, every preset value inside the part's ranges.
- `tests/test_swept_beam_tool.py` and `tests/test_swept_path.py` pass unchanged after the move.

## Trial run (done 12.10.2026)

Results, test element GUIDs and the trial markings: `2026-10-12-road-marking-tool-findings.md`. The
steps as approved (the test elements ended up at x 560 to 620, y 40 to 68 on story 1: the Road
Markings on story 0 span x 520 to 732):

1. **Slab top.** A test Slab on a raised story with non-zero `level` and offset and reference plane
   Top, and one with CoreBottom: `GetDetailsOfElements` against `Get3DBoundingBoxes` gives the top's
   formula.
2. **Mesh heights.** A crowned test Mesh on that story (a level line along the ridge, a cross slope,
   a non-zero `level`): read back, absolute or relative z.
3. **Archicad's triangles.** With the user's go-ahead (if Archicad then takes the IFC as the open
   document, MCP-Test is reopened), save MCP-Test as IFC into the scratchpad, read the test Mesh's
   triangles with ifcopenshell (`uv run --with ifcopenshell`), and compare heights on a 0.25 m grid:
   Archicad, plain Delaunay, constrained Delaunay.
4. **End to end.** Draft `placed_path.py`, `drape.py` and `road_marking.py` with fake connection
   tests. Settle the write calls (one `SetGDLParametersOfElements` call in the order above, or
   `markingType` in its own call first) and what `GetGDLParametersOfElements` returns for strings,
   arrays and angles. Then dry run and write: a zebra across the crown and a broken lane line along it
   on the Mesh; a disabled bay pair, a 5333-2 bus stop with end bars and BUS, and a taxi bay on the
   Slab. Read back; the user takes sections across each.
5. **Speed.** Triangulate synthetic Meshes of 1000 and 5000 points in pure Python and record the
   times (the number that decides when the batch form is worth building).
6. **Findings.** `docs/superpowers/specs/2026-10-12-road-marking-tool-findings.md`; the test Mesh and
   Slab details and Archicad's grid heights become fixtures under `tests/fixtures/road_marking/`;
   this spec is amended where the trial disagrees; then the plan.

## Live check (after the build)

In MCP-Test only: the parent spec's live check steps 3 and 4 (a polyline with arcs, dry run then
write; draped zebra, lane line and parking row with sections), and every bay, stop and lettering
preset placed and draped through the tool, read back. Sections and 3D screenshots come from the user.
Notes in the findings file.

## Out of scope

The batch form (many sources in one call); bundling the library part in this repo; draping onto
Morph, Roof or Shell; a version bump or release (the user's call after the merge); everything the
parent spec puts out of scope.
