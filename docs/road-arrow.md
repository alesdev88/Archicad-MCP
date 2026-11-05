# Road Arrow

`create_road_arrow` places the Road Arrow library part at stations along a lane axis and
drapes it onto Meshes and Slabs. The part draws the road arrows of the Slovenian
rulebook (Uradni list RS 26/24, Article 36) in 31 variants: straight, left, right and
their combinations, the roundabout arrows, sorting and split arrows, lane ending, wrong
way, the cycle arrows and the TSC garage arrow. The part is not in this repo: it lives in
the office GDL library.

## Install

1. Load `Road Arrow.gsm` (`Library/Site/Road Arrow` in the office GDL library) in Library
   Manager.
2. After every new build of the part, reload libraries (File > Libraries and Objects >
   Reload Libraries). Archicad keeps the old part until then.

Without the part the tool still dry-runs, with a warning that it is not loaded; a write
is refused.

## Arrows and sizes

`arrow` is one of the part's names, in any case. Left unset, a new arrow is `5411
Straight`.

| Arrows | Sizes |
| --- | --- |
| 5411 Straight, 5412 Left, 5413 Right, 5421 Straight or left, 5422 Straight or right, 5423 Left or right, 5424 Straight, left or right, 5431 Sorting, left, 5432 Sorting, right | 3.00 m, 5.00 m, 7.50 m, 12.00 m |
| 5414 to 5416, 5425 to 5428 (roundabout), 5452 Solid line ahead, move right | 7.50 m |
| 5417 Wrong way, 5441 Split, right, 5442 Split, left, 5443 Split, left and right | 10.00 m |
| 5451 Lane ending, move right and move left | 5.00 by 2.90 m, 5.00 by 2.00 m |
| 5461 to 5467 Cycle arrows, Garage arrow (TSC) | 1.60 m |

Every arrow also takes `Custom`.

- **Size:** `size` names a size from the table. `speed_kmh` picks it for the first nine
  arrows (Article 36): up to 30 km/h 3.00 m, up to 50 km/h 5.00 m, up to 90 km/h 7.50 m,
  faster 12.00 m. An arrow with one fixed length keeps it and the report warns. `size`
  and `speed_kmh` together are refused.
- **Custom:** `custom_length` (0.3 to 50 m) and `custom_width` (0.05 to 20 m) mean Custom
  and cannot come with `size` (other than Custom) or `speed_kmh`. The part scales the
  drawn size nearest in length.
- **Without a size,** a new arrow takes the part's default (5.00 m); an update keeps the
  arrow's own, or the nearest size the new arrow offers.
- **Look settings** (`thickness`, `paintSurface`, the plan fill and outline settings, their
  pens and `markBmat`): Archicad's Object tool keeps same-named values from other parts
  (a Road Marking with its plan fill off, say), so a new arrow is read back after it is placed
  and the tool writes the part's defaults for anything the call leaves out. An update keeps
  the arrow's own values. A dry run cannot show these writes.
- **Not dimensioned by any source:** the 3.00 m and 12.00 m arrows (derived from the 5.00
  m and 7.50 m figures by the rulebook's rules), 5427, 5467, 5452 and the mirrored 5451
  are drawn by the part without printed dimensions to check against.
- The Mirror tool turns "5412 Left" into a right arrow that is still named left: pick the
  other arrow instead.

## Stations, anchor and offset

- **Axis:** `source_guid` (a Polyline, Line, Arc or Morph line) or `points` (`[{x, y, z}]`
  in metres), drawn in the direction of travel. Exactly one of them, unless `update_guid`
  is given. Always run a dry run first.
- **Stations:** `stations` is metres along the axis, a number or a list (JSON text works
  too), default `[0]`: one arrow at each. A station off the axis is refused (1 mm is
  allowed for rounding).
- **Anchor:** what sits on the station: `tail` (default), `centre` or `tip`.
- **Offset:** `offset` metres to the right of the axis looking in the direction of
  travel; negative is left.
- **Direction on a curve:** the tool takes the axis points at the tail's and at the tip's
  station, each moved `offset` to the right, and turns the arrow along the line between
  them. Both ends of the arrow then lie on the lane, and the arrow is a chord of the curve.
- **Past an end:** where the arrow would reach beyond the axis, it runs on straight along
  the end piece, with a warning.
- **Overlap:** stations closer together than the arrow's length warn; the arrows are
  still placed.
- Every arrow of a call is fitted before anything is placed: if one is refused, none is
  placed.

## Heights and draping

- **Without `drape_on`:** the arrow's level is the axis height at its tail plus `height`.
  `slope_percent` adds to the axis's own rise from tail to tip, and `cross_fall` sets the
  fall across the arrow.
- **With `drape_on`** (Mesh and Slab GUIDs; where they overlap, the highest wins): the
  tool samples the surface along the arrow and across its width, fits a straight cross
  fall at each height point, raises it until no sample is above it, and adds points until
  the arrow stays within `drape_tolerance` (5 mm by default) of the surface along its
  length. `lift` raises the arrow. The part gets the points as `nodeX`, `nodeZ` and
  `nodeFall`; `slopePercent` and `crossFall` are 0. Giving `height`, `slope_percent` or
  `cross_fall` with `drape_on` warns that they do not apply.
- The report gives, per arrow, `nodes`, `nodes_added_for_drape`,
  `max_along_deviation_mm`, `max_across_misfit_mm` (how far the paint floats above the
  surface at most), `steepest_cross_fall_percent`, `off_surface` and `partly_off_surface`.

Limits:

- Across the arrow the height is a straight line at each height point, reaching over the
  whole width even where only the stem is painted. A ridge along the arrow leaves some
  float, worst on the wide split arrows; the tool warns when it passes the tolerance.
- Mesh heights can differ from Archicad's own surface where Archicad picks other
  triangles of a Mesh (up to 25 mm on a regular grid Mesh with twisted cells, from the
  Road Marking trial).
- At most 40 height points per arrow: more is refused, and a coarser `drape_tolerance` or
  a shorter arrow helps.
- No surface under the arrow anywhere is refused; an arrow partly off the surface is
  placed and the report says where.

## Update in place

`update_guid` rewrites one placed arrow and keeps its GUID and every setting the call
does not give.

- The arrow stays where it stands: `source_guid`, `points`, `stations`, `anchor`,
  `offset` and `height` are refused with it. Move an arrow with `move_elements`, or place
  a new one.
- With `drape_on` it drapes along the element's own position and direction, so a new
  size can be draped again in one call.
- Without `drape_on` only the values given are written.
- Changing the length without `drape_on` while the arrow has height points warns that
  they were fitted for the old length: drape again.
- A mirrored arrow is refused, and so is an element that is not a Road Arrow. If nothing
  differs, nothing is written and the report says so.

## The order of API writes

A new arrow is placed with `CreateObjects` (with its angle), then its parameters are
written in one `SetGDLParametersOfElements` call, in this order: `arrowType`,
`arrowSize`, `customLength`, `customWidth`, `nodeCount`, `nodeX`, `nodeZ`, `nodeFall`,
`slopePercent`, `crossFall`, `thickness`, `paintSurface`. The node arrays are
padded to the part's 40 items. An update sends only the values that differ, in the same order. The dry run
lists the parameters of each arrow as `gdl_parameters`. If a write fails midway, the
report lists the elements already placed.

## Not checked yet

The tool and the part are tested against a faked Archicad connection and the part's own
model. The live check in the MCP-Test project (every arrow side by side, draped on the
saved Meshes and a Slab, an IFC export, an update in place) is still to be done, and this
guide says nothing about it until it has been.
