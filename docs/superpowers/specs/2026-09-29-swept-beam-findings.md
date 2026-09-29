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
