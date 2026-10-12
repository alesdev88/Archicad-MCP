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
