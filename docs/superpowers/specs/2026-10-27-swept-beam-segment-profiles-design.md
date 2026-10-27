# Swept Beam: a profile per segment, design

**Date:** 2026-10-27
**Status:** Agreed in chat with Aleš, not built yet.
**Extends:** `docs/superpowers/specs/2026-09-29-swept-beam-design.md` (read it first)
and the part as it is now in `gdl-src/swept-beam`.

## Why

Aleš draws the edge of a cantilever (a balcony or slab edge) as one Swept Beam
that runs round the edge, with a custom edge profile from Profile Manager. One
side of the edge is detailed differently and needs a different custom profile.
Today a beam has one profile, so the only way is two beams that meet at a
corner, with end cuts set by hand, which drift apart whenever the path is
edited.

Two routes were on the table: cut the beam into two objects, or let each
segment pick its profile. One beam with a profile per segment was agreed: the
corners keep their mitre, and there is still one element to edit when the path
changes.

## Goals

- A beam holds up to three profiles: Profile 1, 2 and 3. Three, so this does
  not have to be reopened when a third detail turns up.
- Every segment (node i to node i + 1) uses one of the three.
- The choice can be made in the settings dialog and with a grip on the beam,
  in plan and in 3D.
- Where the profile changes at a corner, both sides keep the plumb mitre the
  beam already makes there.
- Beams already placed look and behave exactly as before once the part is
  updated: every segment starts on Profile 1.

## Non-goals

- Cutting the beam into two separate objects.
- A profile change part way along a segment. Insert a node there first.
- Offset, Flip or surface override per profile. They stay one setting for the
  whole beam.
- A rectangle section per segment. Rectangle stays a whole-beam setting.
- Closed loops (a beam that ends on its own start and mitres there). The beam
  stays open; Aleš's loops stop against the building.
- `create_swept_beam` taking profiles per segment. It only has to keep working.

## Anti-goals (a failure even if the rest works)

- A placed beam changes its path, shape or profile after the part is updated.
- Profile choices land on the wrong segments after inserting, adding or
  merging nodes.
- Switching one segment moves the beam or changes another segment.
- The part no longer opens in Archicad 27.

## Behaviour

### Parameters

- `beamProfile` stays and is shown as "Profile 1". It keeps its name, so placed
  beams keep their profile.
- Two more Profile parameters for Profile 2 and Profile 3, shown only when the
  section source is "Profile attribute", like `beamProfile` is now. Their
  default is 0, none (the Wall Table Bracket's Profile parameter does the
  same), so a placed beam picks them up empty rather than on an attribute
  nobody chose. *(Claude's call.)*
- A per-segment integer array of profile numbers (1, 2 or 3), one item per
  node like the other per-segment arrays (`segArc`, `segSag`, `insL`), the
  last item unused. Default 1.
- A hidden value and marker pair of length arrays for the profile grip, the
  same pattern as `insL` / `insDone` (see the Parameter Script header).
- A "Profile grips" checkbox, on by default, like the other grip groups. (Built
  on the Segments page: the Grips row on the Path page has no room left.)
- Names are the builder's call, except that `beamProfile` must not change.

### Which section a segment gets

- Section source "Rectangle": the whole beam is the rectangle, as now. The
  segment choices are kept but not used.
- Section source "Profile attribute": segment j uses the profile in the slot
  its array item names. An empty slot (none) means Profile 1, so a segment
  set to an empty slot looks like its neighbours on Profile 1. *(Claude's
  call.)* A slot that is set but whose profile cannot be read (missing, or
  geometry the part cannot split) falls back to the rectangle for those
  segments only, as Profile 1 does today for the whole beam.
- Offset (`profileOffsetU`, `profileOffsetW`), Flip, the reference line and
  the surface override apply to every slot. The profiles line up by their own
  Profile Manager origins, so they need to be drawn round the same reference
  point.
- The reference line's "Left face" and "Right face" are worked out per
  segment from that segment's own profile. With profiles of different widths,
  each segment keeps its own left (or right) face on the node line, which is
  what an edge profile wants (the outer face stays on the slab edge). *(Claude's
  call.)*

### Joints

- The 3D script already sweeps the path in runs between kinks. A run now also
  ends at a node where the next segment uses a different profile.
- Profile change at a kink: both sides are trimmed by the existing plumb mitre
  plane at that node, each with its own section. Nothing new in the cut
  itself.
- Profile change at a node that is not a kink: the two pieces meet on a plane
  through the node, square to the path there.
- The overshoot past a kink and the cut box are sized from the section extent
  (`secExtent`). With different sections on the two sides, they must be large
  enough for the larger one, or pieces poke out of the box (the gate D bug in
  the original findings).

### Editing in the dialog

- The three profile pickers sit together.
- A list with one row per segment ("Segment 1", "Segment 2", ...) where each
  row is set to 1, 2 or 3. It scrolls, because a beam made from a long morph
  line can have dozens of segments. The GDL reference offers `UI_LISTFIELD`
  with `UI_LISTITEM{2}`, which takes a parameter name with an array index. A
  row that also shows the profile's name would be nice, not required.
- Shown only in "Profile attribute" mode.
- Layout and which page the list goes on are the builder's call. The dialog
  keeps its 444 px pages with Next and Back.

### Editing on the beam

- Each segment gets a profile grip two thirds along it, on the arc for a
  curved segment, sliding along the segment like the insert grip. Two thirds,
  because the insert grip sits at one third and the curve grip in the middle.
  *(Claude's call.)*
- Dragging it any distance steps that segment to the next profile, 1, 2, 3
  and back to 1. The drag goes through the value and marker pattern the other
  helper grips use: the Parameter Script compares the two, steps the profile
  once and sets the marker to the value. It never sets the dragged value back
  (gate C in the original findings).
- The step skips a slot that is empty or holds the same profile as an earlier
  slot (Profile 2 the same as Profile 1, or Profile 3 the same as 1 or 2).
  With only Profile 1 and Profile 2 set, a drag toggles between them. The
  Parameter Script can compare the attribute indices, it does not need the
  Profile requests. *(Claude's call.)*
- While fewer than two different profiles are set, the profile grips are not
  drawn at all, so every beam placed before this change looks exactly as it
  did, with no grips that do nothing. *(Claude's call.)*
- The hotspot's description names the segment and its profile, for example
  "Segment 3: Profile 2, drag to switch".
- The grip is drawn in plan and in 3D. In 3D it sits at the same point of the
  path at the path's height there. Today the curve and insert grips are plan
  only, so this is the first segment grip in 3D.
- Shown only in "Profile attribute" mode, with the Profiles grip checkbox on
  and at least two different profiles set.

### Node edits keep the choices in step

Every place in the Parameter Script that inserts, shifts or deletes array items
does the same to the new arrays:

- Insert on segment j: both halves keep segment j's profile.
- Add a node past the end: the new last segment takes the old last segment's
  profile. Past the start: the new first segment takes the first segment's.
- Merge (a node dragged onto its neighbour): the remaining segment keeps the
  profile of the longer of the two, the same rule `segArc` follows there.
- A fresh beam (a placement that starts the path over): every segment back to
  Profile 1, as the end cuts go back to square. *(Claude's call.)*
- Repair: the profile array is padded with 1 to the node count, and any value
  outside 1 to 3 becomes 1. A beam placed before this change has no such
  array and comes out all on Profile 1.

### Floor plan

- The plan projection (`PROJECT2{3}`) follows the 3D, so each side shows its
  own profile with no extra work.
- The footprint fill (the click target, and the plan fill when shown) follows
  each segment's own width instead of one width for the whole beam.
- The end cut grips and the end cut pivot use the first and the last segment's
  own section extents.

### `create_swept_beam`

- It keeps working with no new arguments. It already writes `segSag`,
  `sagDone`, `insL` and `insDone` at exactly n items; it does the same for the
  new arrays, grip helpers all 0.
- A new beam gets every segment on Profile 1.
- An update (`update_guid`) keeps the beam's segment choices, in line with
  `docs/swept-beam.md`, which promises an update keeps the profile, end cuts
  and reference line unless the call sets them, and with `_kept_cuts`, which
  writes the cuts back after the Tapir 1.7.2+ path reset. Read the choices
  before that reset (it is a fresh beam, which sets them to 1) and write them
  back when the new path has the same number of nodes. When the node count
  changes, every segment goes to Profile 1, and the dry run and the result say
  how many segments on Profile 2 or 3 went back to Profile 1. *(Claude's
  call.)* Its tests follow.
- `docs/swept-beam.md` gets a short "Profiles per segment" section in Draw,
  and its `update_guid` line mentions the segment choices.

## Addendum, 27.10.2026: segment numbers and None

Asked after the first release: a beam with 20 segments is hard to edit from
the settings without knowing where each segment is, and a segment should be
able to be "none", an empty space.

- "Show segment numbers" (Floor plan page, off by default) labels each segment
  at its middle in plan with its number and profile ("3: P2", "3: None"; just
  the number with the Rectangle section), horizontal on screen, in the node
  heights' text pen and size. Aleš chose number and profile.
- None is a fourth choice (`segProfile` 0): nothing is drawn, and the pieces
  beside the gap end square on the node (Aleš's choice), never on a mitre. The
  plan fill stops at the gap. It works with the Rectangle section too.
- The grip steps 1, 2, 3, None and round (Aleš's choice), so the grips now show
  on every beam, also with one profile; "Profile grips" hides them.
- An end whose segment is None has no end cut grips. `create_swept_beam` keeps
  None on an update like the other choices and counts it when the node count
  changes. Part version 0.3.0.

## Constraints

- Source of truth: `gdl-src/swept-beam` in this repo. Build with
  `uv run archicad-gdl build-source gdl-src/swept-beam --out <folder>`, the
  Archicad 27 converter. Never open and save the part in the Library Part
  Editor.
- One `.gsm` for Archicad 27 to 30. The GUID stays the same: this is an update
  of the part, and placed beams must pick it up.
- Raise `version` in `libpart.toml` (0.1.0 to 0.2.0).
- The main checkout's uncommitted `params.toml` change (`rectBMat` 36, the
  office template's steel) stays out of commits, as before. The copies in the
  office library keep 36.
- After the build, the new part goes to the Archicad GDL repo
  (`Library/Structure/Swept Beam`, `.gsm` plus `source/`, then
  `tools/add_preview.py`) and to `~/Documents/Archicad GDL Workspace`.
- Live checks run in the MCP-Test project. Archicad ports move on restart;
  find MCP-Test by name.

## Left to the builder

- Parameter names (except `beamProfile`), grip IDs (unused ranges next to the
  existing ones), dialog layout.
- How the 3D and 2D scripts read three profiles' geometry and bounding boxes
  (three sets of requests up front, or a request per run).
- How the run split and the square joint are coded, as long as the joints
  behave as described.
- The test approach: the repo's Python tests for `create_swept_beam`, plus
  live checks in MCP-Test of a loop with a profile change at a corner, at a
  non-corner node, and after inserting, adding and merging nodes.
