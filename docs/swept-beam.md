# Swept Beam

A library part for sloped, curved beams with any Profile Manager profile. It
edits like a native element: move nodes, curve edges, insert and merge nodes,
add nodes past the ends, lift nodes, all with grips. Built as an Archicad 27
part, so the same file works in Archicad 27, 28, 29 and 30.

## Install

1. Build it: `uv run archicad-gdl build-source gdl-src/swept-beam --out <folder>`.
2. Put `Swept Beam.gsm` in the office library (and the BIMcloud library for
   Teamwork projects), or add the output folder in Library Manager.
3. After every new build, reload libraries (File > Libraries and Objects >
   Reload Libraries). Archicad keeps the old part until then.
4. Never open and save it in the Library Part Editor: saving in a newer
   Archicad makes it unreadable for older ones. Change the source in
   `gdl-src/swept-beam` and build again.

## Draw

- Place it with the Object tool using the Rotated Diagonal geometry method,
  three clicks: the start, a point in the beam's direction, then a point
  beside the end. The beam runs from the start to the end. Every new beam
  starts straight with square ends, whatever path or cuts the tool's
  settings picked up from an edited beam; profile, reference line, slope,
  pens and grip switches carry over as set.
- Move a node: drag its grip in plan, or in 3D to lift it.
- Curve an edge: drag the grip at the middle of a segment sideways.
- Insert a node: slide the grip at one third of a segment along it and
  release where the new node goes. On a curved segment it stays on the arc.
- Continue the beam: slide the grip just past either end outwards; a node is
  added that far past the end, following the end's direction and slope.
- Delete a node: drag it onto its neighbour.
- Too many grips: the Grips checkboxes on the Path page switch the node,
  curve, add-node and end cut grips on or off, in any mix; "Profile grips"
  on the Segments page does the same for the profile grips.
- Change a segment's profile: drag the grip two thirds along the segment
  any distance; the segment moves to the next profile (see Profiles per
  segment).
- Heights: set Slope (%) on the Path page and close the dialog: the node
  heights follow the slope from node 1 along the plan length. Then drag
  single nodes in 3D; "Re-apply slope" puts them back on the slope.
- Exact values and roll: the node arrays in the parameter list.

## Reference line

The nodes are the beam's reference line: you draw and drag along them, and
"Show reference axis" draws them. On the Path page, "Reference line at" puts
the beam's left face, centre or right face on that line (looking from the
first node to the last), and "Offset from the reference line" moves the beam
away from it; with Centre, a positive offset moves it to the right. To run a
beam 40 cm clear of an edge, draw along the edge, pick the face towards it and
set the offset to 0.40. Flip turns the section over without moving the beam.
The Profile page's origin shift moves the section within itself.

## End cuts

Each end can be cut at an angle, on the Ends page of the dialog:

- In plan: turns the cut counter-clockwise from square, up to 80 degrees.
  The grip beside each end turns it too.
- Tilt: 0 keeps the face square to the beam; positive leans the top of the
  face out past the end. A tilt equal to the beam's slope at that end gives a
  vertical face; the page shows that value for both ends.
- In 3D, each end has two grips: one on the corner turns the plan cut, one on
  the top edge of the cut face tips it.

## Floor plan

The whole plan symbol is clickable, not only its lines: a footprint fill
covers the band the beam takes in plan. It is empty with a transparent
background by default, so nothing shows; pick a hatch and pens on the Floor
plan page to show the beam filled.

## Profile

On the Profile page choose "Profile attribute" and pick any profile. Its
components keep their building materials in sections. If the profile is
missing in a project, the beam falls back to the rectangle.

## Profiles per segment

One beam can carry up to three profiles, for an edge that is detailed
differently on one side. On the Segments page pick Profile 1, 2 and 3 (2 and
3 start empty) and set each segment to 1, 2 or 3 in the list. On the beam,
each segment has a grip two thirds along it, in plan and in 3D: drag it any
distance and the segment moves to the next profile, 1, 2, 3 and round,
skipping a slot that is empty or repeats an earlier profile. The grips show
only once two different profiles are set, so a beam with one profile looks
as before.

Where the profile changes at a corner, both sides keep the mitre; elsewhere
the two pieces meet square on the node. A segment set to an empty slot shows
Profile 1. Left face and Right face hold each segment's own face on the
reference line, so profiles of different widths keep that face in line. The
Rectangle section, the origin shift, Flip and the surface override stay one
setting for the whole beam; draw the three profiles round the same reference
point in Profile Manager so they line up. Inserting a node keeps the
segment's profile on both halves, a node added past an end continues the end
segment's profile, and a merged node leaves the longer segment's profile.

## From a curve (MCP)

`create_swept_beam` turns a Morph line, Polyline, Line or Arc, or 3D points
from a DWG or Rhino, into a Swept Beam. Always run it as a dry run first: it
reports how many nodes it made and how far they are from the source.
`cut_start_plan`, `cut_start_tilt`, `cut_end_plan` and `cut_end_tilt` set the
end cuts in degrees, and `ref_line` ("left face", "centre", "right face") with
`ref_offset` places the beam off the curve; rewriting a beam with
`update_guid` keeps its profile, end cuts and reference line unless the call
sets them. It keeps the segment profiles too when the new path has as many
nodes as the beam; otherwise every segment goes back to Profile 1, and the
result's warnings say how many were on Profile 2 or 3.

With Tapir 1.7.2 or newer, which cannot resize an array parameter, the tool
resets the beam's path, grows it to the node count with the add-node grip and
then writes every node; the dry run says so (`array_writes`). Two nodes closer
than 1 cm in plan (a vertical piece) are refused there, because the beam would
merge them in the middle of the write. Details:
`docs/superpowers/specs/2026-10-19-tapir-1.7.2-array-writes.md`.

Points are read as a line running straight from one point to the next. A
curve becomes an arc only where its points are close enough together that the
arc stays within the tolerance of those straight pieces (2 mm by default);
otherwise the beam follows the straight pieces. Sample a curve densely to get
arcs, or draw it as a Polyline with arcs, which converts exactly.
