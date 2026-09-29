# Swept Beam: Design

**Date:** 2026-09-29
**Status:** Approved in brainstorming, not yet implemented. A throwaway spike runs
before any real implementation (see "Spike").
**Repo:** https://github.com/alesdev88/Archicad-MCP.git (library part source, build
route and MCP tool all live here).

## Purpose

Model sloped, curved beams with a custom profile in Archicad, and edit them the way
a wall, beam or morph is edited: draw a line in plan, add points, curve edges, lift
points. The section must follow the chosen profile and the floor plan must use a
`PROJECT2` projection, like the office's other GDL objects.

The result is one GDL library part, **Swept Beam**, that the whole team uses in plain
Archicad, plus one MCP tool that turns an existing curve (a morph line, a polyline,
or 3D points from a DWG or Rhino) into a Swept Beam.

Success means:

- A colleague without the MCP can place a Swept Beam in two clicks, add and curve
  nodes, set a slope, drag nodes in 3D, and pick any Profile Manager profile in the
  settings dialog.
- The 40.7 m morph line in `MCP-Test` becomes one continuous, smooth beam with a
  square section, within 2 mm of the line.
- The same `.gsm` works in Archicad 27, 28, 29 and 30.

## Why not a native element

| Route | Why not |
|---|---|
| Native Beam | `API_BeamType.beamShape` is one of Straight, HorizontallyCurved, VerticallyCurved (AC29 DevKit `APIdefs_Elements.h`). Slant applies to straight beams only. A beam that curves in plan and rises does not exist. |
| Native Railing (toprail with a profile) | Tried by the user: the node height editing is too finicky for daily work. |
| Morph solid from the API | `CreateMorphs` accepts a full body since Tapir 1.5.10, but an add-on cannot set morph edges to smooth or hidden (confirmed Archicad SDK bug, documented in the Tapir schema). Every facet edge would draw as a hard line. It is also not parametric. |
| One baked `.gsm` per beam | Library clutter, not parametric, a rebuild and reload for every edit. |

## What GDL offers (checked in the GDL reference vault, `vaults/archicad-gdl`)

| Need | GDL | Since |
|---|---|---|
| Sweep a section along a 3D path, plumb | `TUBE`: the W axis of the section points upward, U is to the right looking along the path; per-node rotation angle; joints in the bisector plane | old |
| Holes and a surface per edge | `TUBE{2}`: holes marked with status `-1`, `mati` per base edge, separate top, bottom and cut surfaces | 21 |
| Live Profile Manager geometry | `REQUEST ("Profile_components", ...)`, `REQUEST ("Profile_default_geometry", ...)`, `REQUEST ("Profile_default_boundingbox", ...)`; 2D and 3D scripts only, not the Parameter Script | 21 |
| Materials of a profile | `REQUEST{4} ("Profile_component_info", p, i, "gs_profile_bmat" / "gs_profile_surface" / "gs_profile_comp_surfaces", ...)` | 21 |
| Section cut fill | `BUILDING_MATERIAL`: every shape generated afterwards gets its surface and its cut fill in sections | 21 |
| Pick a profile in the dialog | Profile-type parameter (type 17) | 23 |
| Status code conversion | The reference gives it: `tubeStatus = additionalStatus`, plus 1 when `vertEdgeVisible = 0` (not for `-1` contour ends) | 21 |
| Free node drag | Two combined length-hotspot sets drag in a plane, three drag anywhere in 3D (Graphical Editing Using Hotspots, Example 2) | old |
| Parameter Script reactions | `GLOB_MODPAR_NAME` (name of the last modified parameter, no array index), `PARAMETERS` with arrays, `UI_BUTTON UI_FUNCTION` plus `GLOB_UI_BUTTON_ID` | old |
| Plan projection | `PROJECT2{3}` with `parts` (cut polygons, cut edges, view polygons, view edges, and optionally 3D hotspots projected as static 2D hotspots) | old |

The office closet object (`/Users/alesd/Documents/Archicad GDL/Omara_CVP_GDL_code.txt`)
already uses the Profile parameter and the three Profile requests in production,
with a rectangle fallback when the profile is missing. Swept Beam follows its
patterns (Profile requests guarded by `GLOB_SCRIPT_TYPE`, safe values during edits,
paged UI with Next and Back).

Nothing in this design needs Archicad 24 or later. The GDL features the reference
marks as new in 27, 28 or 29 are MEP route, keynote, user id and view rotation
requests, none of which are used.

## Decisions taken

| Question | Decision |
|---|---|
| Element | A GDL Object library part, "Swept Beam". Classified as a beam by the user where needed. |
| Where the path comes from | Stored in the object as node arrays. Edited with grips. The MCP tool can fill it from a curve. |
| Segment shape | Straight or a plan arc; the height changes linearly along the plan length. An arc with a rise is a helix. |
| Arcs in tilted planes (the loop in the test line) | Not exact. Approximated with more nodes, within the path tolerance. |
| Section orientation | Plumb (from `TUBE`), plus an optional roll per node, blended linearly along the path. |
| Profile source | Any project Profile attribute, read live. Plus a built-in rectangle, which is also the fallback. No steel catalogue in GDL: make those in Profile Manager. |
| Setting heights | "Apply slope" rewrites every node height from node 1 along the plan length. Afterwards the user drags nodes in 3D. The slope is an action, not a constraint. |
| Placement | Two clicks for start and end if an Archicad placement method allows it (spike), otherwise one click plus a default 3 m beam that is dragged out. |
| Plan symbol | `PROJECT2{3}` top view, with 3D hotspots not projected. Cut fill where the plan cut plane crosses the beam, if the spike confirms GDL objects are cut there. |
| Archicad versions | Compiled with the Archicad 27 LP_XMLConverter. One `.gsm` opens in 27, 28, 29 and 30. |
| Source of truth | Plain script and parameter files in this repo. The part is never edited and saved in a Library Part Editor, because saving in 29 turns it into a 29 file that 27 cannot read. |
| MCP scope | Only the convert tool plus the build route. No profile import or baking is needed, since GDL reads profiles live. |
| Splines as a source | Out of scope for version 1. Tapir returns a Spline's fit points only, not its direction handles, so the curve between points would be a guess. |

## Part 1: the Swept Beam library part

### 1.1 Parameters

All arrays have the same length `n` (the node count, at least 2). `segArc[i]` and
the other per-segment arrays describe the segment from node `i` to node `i + 1`;
item `n` is unused and kept at 0, so inserting and deleting nodes shifts every array
the same way.

**Path**

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `nodeX[]`, `nodeY[]` | length array | `[0, 3]`, `[0, 0]` | Node positions in the object's local plan coordinates. Node 1 starts at the origin but may be dragged away. |
| `nodeZ[]` | length array | `[0, 0]` | Node height above the object's own elevation. |
| `nodeRoll[]` | angle array | `[0, 0]` | Section roll at the node, 0 = plumb. |
| `segArc[]` | angle array | `[0, 0]` | Signed central angle of the segment's plan arc, 0 = straight. Positive means the path turns left (counter-clockwise) along the segment, so the arc bulges to the right of its chord. This is the same sense as a positive DXF bulge and Tapir `arcAngle` (the sign trap found on the Stanežiče rails). |
| `slopePercent` | real | 0 | Used by "Apply slope". |
| `pathTolerance` | length | 0.002 | Maximum distance between a sampled chord and the true arc. |

**Helpers** (hidden, written by grips, rebuilt by the Parameter Script)

| Parameter | Type | Meaning |
|---|---|---|
| `segSag[]` | length array | Signed bulge of the segment's midpoint off the chord, positive to the right of the chord (matching a positive `segArc`), edited by the arc grip. `segArc = 4 * ATN (2 * segSag / chord)`. |
| `insX[]`, `insY[]` | length arrays | Position of each segment's insert grip. Parked at one third of the segment's length along the path. |

**Profile**

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `profileMode` | string | "Rectangle" | "Profile attribute" or "Rectangle". |
| `beamProfile` | Profile | none | The Profile Manager profile, read live. |
| `rectW`, `rectH` | length | 0.20, 0.20 | Built-in rectangle, also the fallback. |
| `rectBMat` | building material | project default | Building material of the rectangle. |
| `profileOffsetU`, `profileOffsetW` | length | 0, 0 | Shift of the section against the path. By default the profile's own origin sits on the path, like a native profiled beam. |
| `flipProfile` | boolean | 0 | Mirror the section left to right, like a native beam's `isFlipped`. |
| `overrideSurface`, `surfaceOverride` | boolean, surface | 0, none | One surface for the whole beam. |

**Floor plan**

| Parameter | Type | Default |
|---|---|---|
| `outlinePen` | pen | 1 |
| `showAxis`, `axisPen`, `axisLineType` | boolean, pen, line type | 0, 1, dash |
| `showNodeHeights`, `textPen`, `textSizeMm` | boolean, pen, real | 0, 1, 2.0 |

**Other**

| Parameter | Type | Meaning |
|---|---|---|
| `A` | built-in length | While the beam has exactly two nodes and a straight segment, `A` is the plan distance from node 1 to node 2. Drives two-click placement and stretching (see 1.3). Ignored otherwise. |
| `gs_ui_current_page` | integer, hidden | Dialog page, as in the closet. |
| `sb_version` | string, hidden | Build that placed the beam, for support. |

### 1.2 Editing grips

Hotspot ids are generated per node and per segment. Base and reference hotspots are
hidden (flag 128); the user sees one grip per action.

- **Move a node in plan.** Two combined length sets per node, one on `nodeX[i]`,
  one on `nodeY[i]`, laid out as in the reference's Example 2.
- **Move a node in 3D.** Three combined sets per node, on `nodeX[i]`, `nodeY[i]`
  and `nodeZ[i]`. The user can type an exact value in the tracker while dragging.
- **Curve an edge.** A grip at each segment's midpoint, a length set on `segSag[i]`
  perpendicular to the chord. The Parameter Script turns the new bulge into
  `segArc[i]`.
- **Insert a node.** A combined `insX[i]` / `insY[i]` grip at one third of each
  segment's length, so it never overlaps the arc grip (overlapping editable grips
  on different parameters would start a combined drag). When dropped elsewhere,
  the Parameter Script inserts a node at the drop point.
- **Delete a node.** Drag it within 1 cm of a neighbour and the two merge. Nodes can
  also be removed in the parameter list's array editor.
- **Numbers.** Exact coordinates, heights, roll and arc angles are typed in the
  parameter list's array editor. There is no roll grip in version 1.

### 1.3 Parameter Script

The Parameter Script runs after every edit. It does not make Profile requests.

1. **Repair.** Force `n >= 2` and give every array length `n`, padding from the
   last node or truncating to the length of `nodeX[]`.
2. **React to `GLOB_MODPAR_NAME`.** It holds only the parameter's name, not the
   array index, so each reaction finds the changed item by comparison:
   - `A`: if the beam has two nodes and a straight segment, node 2 moves to plan
     distance `A` from node 1 along their current direction (local x if the two
     nodes coincide). After any other edit of such a beam, `A` is set back to the
     plan distance between its two nodes, so the two stay in step.
   - `segSag`: `segArc[i] = 4 * ATN (2 * segSag[i] / chord[i])` for every segment.
   - `insX` or `insY`: find the item whose grip moved more than 1 mm from its
     parked position and insert a node at the drop point. Its height and roll are
     interpolated from its neighbours at the parked point's position along the
     path. A straight segment splits into two straight segments. An arc splits
     into two arcs that keep the original radius and turning direction; a half
     whose chord is longer than the original diameter becomes straight.
   - Any node array: merge nodes within 1 cm of a neighbour.
3. **React to `GLOB_UI_BUTTON_ID = 1`** ("Apply slope"): `nodeZ[1]` stays;
   every other `nodeZ[i] = nodeZ[1] + slopePercent / 100 * s(i)`, where `s(i)` is
   the plan length along the path from node 1 to node `i`, arcs included.
4. **Park the helpers.** Recompute `segSag[]` from `segArc[]` and the current
   chords (an arc keeps its angle when its nodes move), and put `insX[]` /
   `insY[]` back at their one-third points.
5. **Write back** with `PARAMETERS`, then `VALUES` and `HIDEPARAMETER` for the
   dialog (as in the closet).

### 1.4 Geometry (Master Script and 3D Script)

**Sampling** (Master Script, used by both the 2D and 3D scripts):

- A straight segment is two points, unless its roll changes by more than 5 degrees,
  then it is split so the twist stays smooth.
- An arc with central angle `t` and chord `c` has radius `R = c / (2 SIN (|t| / 2))`.
  It is split into `k = MAX (1, CEIL (|t| / (2 * ACS (1 - pathTolerance / R))))`
  equal steps.
- Height and roll change linearly with the plan length along each segment.
- Points closer than 1 mm to the previous point are skipped.
- The result is `pathX[]`, `pathY[]`, `pathZ[]`, `pathRoll[]`.

On the test line (smallest radius 2.4 m, 2 mm tolerance) that is about one point
every 4.7 degrees, roughly 150 path points for 40.7 m.

**Sweep** (3D Script, `MODEL SOLID`): one `TUBE{2}` per profile component.

- **Section mapping.** Profile Manager `x` becomes `u` and `y` becomes `w`, minus
  `profileOffsetU` and `profileOffsetW`. With `flipProfile` set, `u = -x` and each
  contour's node order is reversed to keep its winding.
- **Status codes** use the reference's formula. Contour ends (`-1`) stay `-1`, which
  is exactly `TUBE{2}`'s hole syntax.
- **Path.** The sampled points, each with its roll as the `TUBE` angle, plus one
  phantom point before the first and after the last. The phantom points lie along
  the true end tangent (the arc's tangent and the segment's slope, not the last
  chord), so the end faces are square to the curve.
- **Kinks** between segments are cut as mitres on the bisector plane by `TUBE`
  itself.
- **Mask:** start and end faces present, their edges visible (`1 + 2 + 16 + 32`).
  No rings are drawn between path points, so the curve renders smooth.
- **Materials.** Per component: `BUILDING_MATERIAL` from `"gs_profile_bmat"`, the
  edge surfaces from `"gs_profile_comp_surfaces"` into `mati`, and
  `"gs_profile_surface"` for the end faces and the cut surface. In Rectangle mode,
  `rectBMat` and its surface. `overrideSurface` replaces every surface.
- **3D grips** as in 1.2.

### 1.5 Floor plan (2D Script)

- `PROJECT2{3} 3, 270, method, parts` with `parts = 1 + 2 + 4 + 8`, so the 3D grips
  are not projected as static hotspots on top of the plan grips. `method` is 3
  (shading, which is what produces cut polygons) if the spike shows the plan cut
  plane cuts GDL objects; otherwise 2, the office's usual hidden-line projection.
- `showAxis`: the path as `LINE2` and `ARC2` pieces, with `axisPen` and
  `axisLineType`, like a native beam's reference axis.
- `showNodeHeights`: each node's height relative to the home story (object
  elevation plus `nodeZ[i]`), written beside the node with `TEXT2`.
- The plan grips from 1.2.

### 1.6 Settings dialog (User Interface Script)

Three pages with Next and Back, following the closet's layout:

1. **Path:** `slopePercent`, the "Apply slope" button (`UI_BUTTON UI_FUNCTION`, id 1),
   `pathTolerance`, the node count as read-only text, and a note that exact values
   and roll are edited in the parameter list's array editor.
2. **Profile:** `profileMode`, `beamProfile`, `rectW`, `rectH`, `rectBMat`,
   `profileOffsetU`, `profileOffsetW`, `flipProfile`, `overrideSurface`,
   `surfaceOverride`.
3. **Floor plan:** `outlinePen`, `showAxis`, `axisPen`, `axisLineType`,
   `showNodeHeights`, `textPen`, `textSizeMm`.

### 1.7 Guards

- A missing or invalid profile falls back to the rectangle, as in the closet.
- A path with fewer than two distinct points draws a small marker in plan and
  nothing in 3D, instead of failing.
- At an exactly vertical segment the plumb frame is undefined; `TUBE` reuses the
  previous node's up direction. The object's description says so. It is not
  blocked.

## Part 2: source and build route

**Source**, in `gdl-src/swept-beam/`:

- `scripts/master.gdl`, `param.gdl`, `2d.gdl`, `3d.gdl`, `ui.gdl`.
- `params.toml`: one entry per parameter (name, type, default, flags,
  description), including the Profile, building material, surface, pen, line type
  and array types.
- `libpart.toml`: name "Swept Beam", a fixed `MainGUID`, the ancestry used by the
  existing pipeline for placeable objects, and the version string written into
  `sb_version`.

Because the `MainGUID` never changes, a rebuilt `.gsm` replaces the old one and
placed beams update, instead of becoming a second library part.

**Build**, a new route beside the mesh route in `archicad_mcp.gdl`:

```bash
archicad-gdl build-source gdl-src/swept-beam --archicad 27 --out build
```

1. Write the HSF folder: `libpartdata.xml`, `ancestry.xml`, `paramlist.xml`
   (generated from `params.toml`), `libpartdocs.xml`, `scripts/*.gdl`.
2. Compile with the chosen Archicad's LP_XMLConverter. `find_lp_xmlconverter` gains
   a version argument; without it, it keeps today's behaviour (newest installed
   release, which currently resolves to 29).
3. Check the scripts offline with `convertlibrary -interpret`, using both the 27
   and the 29 converters when installed.

Verified on 2026-09-29: the Archicad 27 converter compiles an HSF written with
29's `Version="46"` and produces a 27-format part (`Version="45"` on the way back).

**Deploy.** For testing, the existing `deploy_gdl_object` into the linked GDL
workspace library. For the team, the user copies the one 27-format `.gsm` into the
office library, and the BIMcloud library for Teamwork projects. That step is
documented in `docs/swept-beam.md`, not automated.

## Part 3: MCP tool `create_swept_beam`

Registered in full mode, like `create_elements`. Not gated on the GDL workspace
folder.

```
create_swept_beam(source_guid=None, points=None, start_height=None,
                  slope_percent=None, profile=None, offset_u=0, offset_w=0,
                  flip=False, path_tolerance=0.002, update_guid=None,
                  dry_run=True, port=None)
```

- **Source**, exactly one of:
  - `source_guid`: a Morph line (read as its wire edges through
    `GetDetailsOfElements`), a Polyline (coordinates and arcs), a Line or an Arc.
  - `points`: a list of `{x, y, z}` in model coordinates. The client reads DWG or
    Rhino curves itself.
- **Heights.** Morph lines and 3D points keep their own heights. For flat sources,
  `start_height` and `slope_percent` apply the same rule as "Apply slope".
- **Profile:** `{"attribute": "<Profile name>"}` or
  `{"rectangle": {"width": w, "height": h, "building_material": "<name>"}}`.
- **`update_guid`:** rewrite the path of an existing Swept Beam in place, keeping its
  GUID.

**Turning a source into nodes.**

1. Morph lines: chain the wire edges into one ordered line. Refuse a branch, a
   closed loop or a morph with faces, with a clear message.
2. Polylines, lines and arcs: their own straight and arc segments become the nodes
   directly, no fitting.
3. Morph lines and points: a **greedy fit** at `path_tolerance`. From the current
   start point, extend the piece while every point in it fits a straight line or a
   plan circle (through its first, middle and last points) within tolerance **and**
   its height is linear in plan length within tolerance. When the next point fails,
   close the piece and start a new one there. A helix becomes one arc; a
   tilted-plane arc becomes a run of short pieces.

**Placing.** `CreateObjects` places "Swept Beam" at node 1, on the source's story,
with the object's elevation at node 1's height, so `nodeZ[1] = 0`. Then one
`SetGDLParametersOfElements` call writes all arrays and the profile parameters
(Tapir 1.5.7 and later resize arrays to fit). Angle arrays (`segArc`,
`nodeRoll`) go through the API in radians, while a single angle parameter goes in
degrees; both read back in radians (found at gate A). If the Parameter Script does not run
after an API write (spike), the tool writes the helper arrays itself with the same
parking rule. The source element is left untouched.

**Dry run** returns the node count, straight versus arc pieces, the maximum 3D
deviation from the source, the steepest slope, the target story and placement point,
and warnings (a near-vertical piece, a missing library part). If "Swept Beam" is
not loaded in the project, the tool says where the `.gsm` is and how to link it,
instead of failing inside Archicad.

## Spike (throwaway, before implementation)

In `MCP-Test` (port checked by `project_name` before any write), on Archicad 29 and
27. The findings are written up and may change this design before real code.

1. `TUBE{2}` with the live Profile requests: a square, a Profile Manager profile
   with arcs, a hollow profile. Orientation compared with a native beam using the
   same profile (decides whether `flipProfile` defaults to 0 or 1).
2. A helix with roll, square ends, mitres at kinks.
3. The section cut fill from `BUILDING_MATERIAL`. Whether `PROJECT2{3}` cuts GDL
   objects at the floor plan cut plane (decides `method` in 1.5).
4. Grips: node drag in plan, combined 3D drag with about 60 nodes (about 900
   hotspots), the arc grip, insert detection through the helper arrays, merge
   delete, "Apply slope".
5. Which placement method gives start and end in two clicks.
6. `CreateObjects` with elevation and story on Tapir 1.5.10, array writes with
   `SetGDLParametersOfElements`, and whether the Parameter Script runs after an API
   write.

## Testing

1. **Python unit tests** (no Archicad):
   - Chaining morph wire edges, including refusing branches and loops. Fixture: the
     59-vertex morph line read from `MCP-Test` on 2026-09-29.
   - Greedy fit: a sampled helix gives one arc with the right radius and rise; a
     straight line gives one segment; the fixture fits within 2 mm. The fixture's
     arcs rise non-linearly, so they come back as several pieces each; the test
     asserts the deviation, not a piece count.
   - The slope rule and plan length along arcs.
   - `params.toml` to `paramlist.xml`, including Profile, building material and
     array types.
   - Converter selection by version.
   - `create_swept_beam` dry-run payloads.
2. **Offline GDL check:** `-interpret` with the 27 and 29 converters, skipped when a
   converter is not installed.
3. **Live acceptance** in `MCP-Test`:
   - The 40.7 m morph line with a square profile becomes one continuous beam,
     within 2 mm, clean through the -55 degree loop and the three kinks.
   - Place with two clicks, insert two nodes, curve an edge, apply a 5 % slope,
     drag one node in 3D.
   - Switch to a hollow Profile Manager profile: the section shows the hole and the
     correct building materials.
   - The plan shows the outline and the axis.
   - The same `.gsm` opens, places, edits and cuts in Archicad 27.

## Rollout

1. Spike, findings note.
2. Build route (`build-source`, converter choice).
3. Library part: path, sampling, sweep, profile, plan, dialog, "Apply slope".
4. Grips: move, 3D move, arc, insert, merge.
5. `create_swept_beam`.
6. Archicad 27 check, `docs/swept-beam.md` for the team, office library copy.
7. MCP release with the tool and the build route.

## Out of scope

- Exact arcs in tilted planes (approximated with nodes).
- A section that changes along the path.
- A native Beam element, and automatic IFC type mapping (the user classifies).
- Splines as a source.
- A live link that rebuilds the beam when a guide element changes.
- A steel section catalogue inside the GDL (use Profile Manager).
- Roll grips.
- Connections between two Swept Beams (intersections, mitres between parts).
- Quantities for schedules (length, volume) from a Properties Script. Cheap to add
  later.
