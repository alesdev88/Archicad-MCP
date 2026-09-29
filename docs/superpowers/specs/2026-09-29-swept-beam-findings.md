# Swept Beam: gate findings

## Gate A

Run on 2026-09-29. Archicad 29 build 5101 (`MCP-Test`, port 19724) and, at the
user's request, mainly Archicad 27 build 6080 (`MCP-Test_27`, port 19725, Tapir
1.5.9). The GDL workspace folder was not loaded in either project; it was added
with Tapir `AddLibraries` (undo: remove it in Library Manager).

1. Build into the workspace: clean validation with the 27 and 29 converters.
2. Place and render (29): a straight 3 m beam, 0.2 x 0.2, building material 1.
3. Helix through the API: arrays read back exactly as written. The helpers first
   looked parked as if segment 2 were straight, which read as "the Parameter
   Script does not run after the last parameter". That was the units problem
   below: after resending in radians the helpers were right. The Parameter
   Script runs after each parameter an API write sets (`GLOB_MODPAR_NAME` is
   empty then). The MCP tool still writes the helper arrays itself, last.
   Units: `SetGDLParametersOfElements` passes numbers through unchanged, and
   Archicad takes a single Angle parameter in degrees but Angle ARRAY items in
   radians (both read back in radians). The first helix was sent in degrees, so
   `segArc[2]` became 5156.62 degrees: the script treated it as straight and the
   roll of "30" was a 1719 degree twist, which looked like scalloped wedges.
   Resent in radians (arc pi/2, roll pi/6): a clean quarter helix rising 1.0 m, a
   45 degree mitre at node 2, a smooth twist to 30 degrees, and the Parameter
   Script parks the helpers exactly as offline (`segSag[2] = 0.6213`, insert grip
   at (3.549, 0.951)). A roll-per-beam change made on the wrong reading was
   reverted; per-node roll stays as specified. Decision: the MCP tool sends
   `segArc` and `nodeRoll` in radians.
   `A` is not the part's default (3) after an API placement but the Object tool's
   current default size (0.7 in 29, 0.6 in 27), and the Parameter Script does not
   run on placement. On a two-node beam the next edit would snap node 2 to that
   `A`. To be settled with the placement method at gate C.
5. Absolute z: `CreateObjects` at z 5.0 on story 1 (level 4.0) reads back
   `origin.z = 5.0`, `floorIndex = 1`: z is absolute. Probe deleted.
7. Archicad 27 opens, places and renders the 27-format part.

6. Plan and section (27). Plan: outline, axis (solid: line type index 1 is solid
   in the office template) and node heights all draw. With roll 0 the plan is two
   parallel lines; the user's "rotating" plan was the 30 degree test roll.
   Section: the building material's cut fill shows. A cut in the middle of the
   straight part was bent: TUBE places the joint's averaged frame at node 2
   (direction and slope both change there), so the whole 3 m straight piece was
   a warped, triangulated ruled surface. Fix: two extra samples 2 cm from each
   segment end keep the segment's own plumb frame between them; the compromise
   at a real kink now fits in the last 2 cm before the mitre. Plan re-rendered:
   parallel to the corner.
   The user's close-up of the corner then showed the 2 cm piece as a crumpled
   joint. Fix: the Master Script marks kinks (direction change over 1 degree, with
   the mitre plane bisecting the two directions); the 3D script sweeps each run
   between kinks on its own, carries it past the kink along its own direction
   and trims it with CUTPLANE on the mitre plane. Plan: parallel lines into one
   straight mitre line. 3D: a flat mitre with a small step where the two slopes
   differ, as two real cut members would show.
   A 3D-bisector mitre plane leans when the path rises, so the two sides' cut
   edges drifted apart in plan (user's close-up). The mitre plane is now vertical
   and bisects the two directions in plan (a plumb mitre cut); a slope-only kink
   gets a plumb cut square to the plan direction. Plan at pixel level: one mitre
   line, both sides end on it. Section DP4 in the middle of the straight part: a
   clean rectangle (user).
   Kinks: two sloped members meeting at a plan angle cannot meet flush on any
   mitre (their sloped top faces cross along the average direction, the stair
   handrail "wreath" problem). With the plumb cut the plan is clean and the
   section shows a small X-step at the joint. A tangent curve (arc starting in
   the direction the straight part ends) has no joint: section at the joint and
   mid-straight both clean (user). Guide rule: sloped beams that turn should
   curve tangentially.
4. Apply slope (27, user pressed the button): nodeZ became [0, 0.3, 0.6332]
   exactly; the arc kept its helper values. The dialog button and whole-array
   PARAMETERS both work.
6. Plan cut: with the hidden-line projection the beam stays an outline where it
   crosses the floor plan cut height (user). Decision: keep it, it matches the
   office's other objects (`project2 3, 270, 2`), which is what the user asked
   for. A real plan cut with fill is possible later with PROJECT2{4} and the
   cut height from GLOB_CUTPLANES_INFO (Archicad 20+).

Gate A result: pass, with the fixes above (units, end samples, plumb mitres).

## Gate B

Run on 29.09.2026 in Archicad 27 (`MCP-Test_27`), on the tangent test beam,
with the office template's profiles. The profile branch sits inside the kink
run loop; offsets move only position points (plain, 600, 900, 1000+, 3000+),
not codes whose x/y hold a vector, length or angle.

1. IPE300 (solid I): clean I-section along the curve, web plumb.
2. RHS100x100x5 (one skin, outer and hole outlines): the hole shows (user's 3D).
3. CHS48.3X5.0 (round, hollow): smooth sides, no facet lines (user: smooth).
4. Orientation: a native Beam with UPE100 drawn left to right opens to the
   opposite side from the Swept Beam (the two channels faced each other). The
   section is now mirrored by default and "Flip section" un-mirrors it, so an
   unticked Swept Beam matches a native beam. Re-rendered: both show the open
   side from the same angle.
5. Profile index 9999: no crash; Archicad resolved it to an existing profile
   (steel section drawn), so the rectangle fallback only covers a failing request.

## Gate C

8. Array repair: `nodeY` cut to one item through the API came back padded to
   the length of `nodeX`.
- Helix of 60 nodes (radius 4 m, 6 degree arcs): the user dragged arc grips into
  an S-shaped "snake" near the end; the first half of the helix then vanished.
  Cause: CUTPLANE is an infinite plane; the long run from node 1 to the first
  snake kink was trimmed at that kink, and the same plane crossed the circle
  again on the far side. Fix: the cut is a finite CUTFORM box on the mitre
  plane, sized from the section and the kink angle. Re-rendered: whole helix
  and clean snake mitres.
- Grips: all visible grips are movable (node, curve at the segment midpoint,
  insert at one third). Hotspots cannot be coloured in GDL; each moving grip
  now carries a tracker label ("Node 3 x", "Curve segment 4: bulge",
  "Insert node after 2: x").
- User results: node drag in plan and 3D, curve grip, merge and dragging a node
  in z all work. The insert grip (a combined insX/insY drag) never committed:
  a logging build showed no Parameter Script run at all with insX/insY changed
  (Archicad dropped the edit before the script), with the helpers hidden or
  visible alike. API writes do set GLOB_MODPAR_NAME (to the written name); the
  empty runs are redraws. Redesign on the pattern that works (one-way length
  grip on a hidden helper, like the curve grip): the insert grip slides along
  the segment from its start node (`insL`) and inserts a node at that point of
  the segment, on the arc when it is one; add-node grips past each end
  (`extStart`, `extEnd`) append or prepend a node at the dropped distance along
  the end direction, continuing the slope (the user's "continue like the wall
  tool" request); a Grips setting (All / Nodes / Curves / Add nodes) filters
  the grips. `A` now drives node 2 only when `A` itself is edited.
- The insert and end grips then worked, but each edit fired more than once
  (the end grip appended twice, so the piece came out twice as long; the insert
  grip left a cluster of nodes): Archicad runs the Parameter Script several
  times per edit and GLOB_MODPAR_NAME can stay stale, even during a later API
  write (an API reset re-fired the user's last grip). Final design, for the
  curve, insert and both add-node grips alike: every helper has a hidden marker
  (sagDone, insDone, extStartDone, extEndDone); the 2D script draws the grip at
  home + (value - marker); the Parameter Script applies value - marker once and
  sets marker = value, never touching the dragged value. No GLOB_MODPAR_NAME,
  no segment lengths involved. Add-node grips rest 0.25 m past the ends (user
  asked for closer). The test beams were recreated so old helper values could
  not fire.
- Insert grips on arcs (commit 72a2b5c): the user first saw them still on the
  chord, one third along. The build had reached the linked GDL workspace
  folder, but Archicad 27 kept the old part until a ReloadLibraries (the
  placed beam had no `aDone` parameter, which that build added). After the
  reload, on a 5-node beam with 4 arcs, every curve grip sits on the arc
  midpoint and every insert grip one third along its arc. Every build into
  the workspace needs a ReloadLibraries on each open port before a live check.
- Placement (item 7): Rotated Diagonal takes three clicks (start, a point
  on the direction, the opposite corner); the length is the third click's
  projection on the direction. With it, a new beam comes out as a two-node
  straight beam of the clicked length (AC27: A = aDone = nodeX[2] = 4.498 m),
  so Archicad does run the Parameter Script with the placement's A and the
  aDone rule needs no Master Script override. Two clicks is Rotated (angle
  only; A stays at the default).
- Stale tool defaults: before that, every placement came out as the same
  5-node beam (nodes -1.75, 0, 2, 3, 4.75, A = 3). The Object tool's default
  settings still held helper values from builds bc05c88 and e5e4e7c
  (insL = [1, 0], extStart = extEnd = 0.5, markers -1); reloading a library
  part keeps same-named parameter values, and the value and marker logic read
  them as pending drags on placement (insert at 2 m, ends at 0.25 + 0.5 + 1 =
  1.75 m past). Re-picking the part in the Object Settings reset the defaults.
  Only a changed helper default under a live test file causes it; a released
  part that changes helper defaults needs a Forward Migration script.

## Gate D

Run on 29.09.2026 through `swept_beam.create_swept_beam` on a live connection
(the same code the MCP tool calls; the wrapper is covered by the unit tests),
so the Claude extension did not need a restart.

1. MCP-Test (Archicad 29), the test morph line `0EF502A3`: dry run 59 nodes,
   58 straight, 0.0 mm, steepest piece 55.4 degrees, no warnings. The line
   is a smooth 3D curve whose height rises and falls like a sine, sampled
   every 0.3 to 1.0 m with turns up to 18 degrees per vertex; with a linear
   rise per piece nothing longer than one edge fits within 2 mm, so the beam
   follows the drawn edges exactly. Placed as `4AA4B92F`; the stored nodes match
   the morph vertices within 0.001 mm (morph vertex z is relative to the
   origin, as assumed). First render: loose pieces beside the tight loop,
   where the pieces fall at about 55 degrees. Cause: a kink's cut box was sized
   without the run's slope while the overshoot it trims grows and rises with
   it (up to 0.94 m + 0.14 m of section against a 0.87 m box). Fix: the box
   holds the whole overshoot, `cutD = MAX (old size, overshoot * SQR (1 +
   slope^2) + 2 * secExtent)`, so flat kinks are unchanged. Re-rendered in plan
   and 3D: no loose pieces, the beam follows the line through the loop.
2. Same element, `update_guid` with `{"attribute": "RHS100X100X5"}`: GUID kept,
   59 nodes, profileMode "Profile attribute", profile index 47. The previews
   are too small to show the 100 mm hole (checked at gate B).
   The user confirmed in 3D: the beam follows the morph line, and the RHS
   profile is drawn correctly.
3. Polylines drawn by the user in MCP-Test (arc sign check, Review Focus 1):
   `D3AB61FC` (a -176.43 degree arc, then a +142.53 degree arc) became
   `D4D4F692`, 3 nodes, 2 arcs; `C56E5827` (+119.56 degrees) became `3BE739CF`,
   2 nodes, 1 arc. The stored segArc values equal the polyline arcs with the
   same sign; nodes and arc midpoints match the polylines within 0.0 mm, and
   the plan previews bend the way the polylines do. An Arc element `C0393551`
   (radius 2.143 m, 30.833 to 128.805 degrees) became `FA8215BE`, 2 nodes,
   segArc +97.972 degrees: same centre and radius within 0.0 mm, same start,
   end and midpoint angles.
4. MCP-Test_27 (Archicad 27), the fixture as `points`, 30 m north: 59 nodes,
   0.0 mm, placed as `0BC771D9` on story 0; stored nodes within 0.001 mm;
   render clean.

## Gate E: end cuts

Run on 29.09.2026 in Archicad 27 (`MCP-Test_27`) through this branch's MCP
server over stdio (the registered tool, `points` sent as JSON text).

1. A 4 m beam rising 1.5 m, start cut +30 degrees in plan, end -30, tilts
   -20.556 and +20.556 (the slope, for vertical faces): the cuts read back as
   given. The plan preview shows the start cut about 31 degrees off square,
   counter-clockwise; the side and 3D previews show a vertical face at the high
   end.
2. The first attempt came out 48 m long, not 4 m. Cause: Tapir's
   SetGDLParametersOfElements (1.5.0 to 1.5.9) sets the object's xRatio to A in
   metres after every write, but xRatio is the placed size over the library
   part's A; with A at 3 m each write tripled the size, even a write that did
   not name A, and the two-node Parameter Script rule moved node 2 to the new A.
   Beams with more nodes or arcs ignore A, so gate D never showed it. Fix: A, B
   and aDone default to 1 m, so the ratio equals the size (the default beam is
   still 3 m through nodeX). Repeated writes on a fresh beam then keep A, the
   ratio and node 2 exactly. Changing the default re-bases placed instances:
   two-node straight test beams in MCP-Test_27 shrank to a third at the reload
   and were restored by a write; MCP-Test had none.
3. An update through `update_guid` without cuts kept all four cuts.
4. Existing beams read 0 for all four cuts after the reload and keep TUBE's
   square ends.
5. The user dragged the new corner grip on `AA91D166` and the beam jumped:
   shorter, an extra node, both ends 1.75 m longer. Not the grip: that beam was
   placed before gate C closed from the stale tool defaults, and its stored
   values were still that placement's raw ones (two nodes, 12.9 m, pending
   insert and add-node helpers). Archicad drew the stored state; Tapir's
   GetGDLParametersOfElements opens the parameter list for editing, which runs
   the Parameter Script, and returns the result without storing it, so every
   API read showed a processed five-node beam that was never saved. The drag
   was the first real edit: the script ran, applied the pending helpers and the
   length re-based by the 1 m default, and stored it. The cut itself came out
   as dragged (-45.5 degrees). Lesson: an API read of a GDL object is the
   script's answer, not the stored state; compare with the plan when they
   might differ.

## Gate F: clickable plan symbol

Run on 29.09.2026 in Archicad 27 (`MCP-Test_27`) and 29 (`MCP-Test`), clicked
by the user inside the beams, away from their lines.

1. A footprint fill (the band the beam covers, mitred at kinks, closed on the
   end cuts) drawn with `SET FILL 0` did not make the beam clickable, although
   it drew nothing. The rendered band matched the outline on the 59-node curved
   beam and on a beam with plus and minus 30 degree end cuts (checked with a
   brick hatch).
2. Three beams across another beam, with a brick hatch, the template's
   "Background" fill and a project empty fill attribute, all background pen 0:
   all three picked the beam by a click inside, and the beam underneath stayed
   visible through all of them (pen 0 is a transparent background).
3. Default fixed: with no fill picked the part defines and uses its own empty
   fill (`DEFINE EMPTY_FILL`), independent of the project's attribute indices.
   The user confirmed the beams on the default and the one beneath them all
   select by a click inside.
