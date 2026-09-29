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
  beside the end. The beam runs from the start to the end.
- Move a node: drag its grip in plan, or in 3D to lift it.
- Curve an edge: drag the grip at the middle of a segment sideways.
- Insert a node: slide the grip at one third of a segment along it and
  release where the new node goes. On a curved segment it stays on the arc.
- Continue the beam: slide the grip just past either end outwards; a node is
  added that far past the end, following the end's direction and slope.
- Delete a node: drag it onto its neighbour.
- Too many grips: the Grips setting on the Path page shows all of them, or
  only the node, curve or add-node grips.
- Heights: set Slope (%) on the Path page and press Apply slope; then drag
  single nodes in 3D.
- Exact values and roll: the node arrays in the parameter list.

## End cuts

Each end can be cut at an angle, on the Ends page of the dialog:

- In plan: turns the cut counter-clockwise from square, up to 80 degrees.
  The grip beside each end turns it too.
- Tilt: 0 keeps the face square to the beam; positive leans the top of the
  face out past the end. A tilt equal to the beam's slope at that end gives a
  vertical face; the page shows that value for both ends.
- In 3D, each end has two grips: one on the corner turns the plan cut, one on
  the top edge of the cut face tips it.

## Profile

On the Profile page choose "Profile attribute" and pick any profile. Its
components keep their building materials in sections. If the profile is
missing in a project, the beam falls back to the rectangle.

## From a curve (MCP)

`create_swept_beam` turns a Morph line, Polyline, Line or Arc, or 3D points
from a DWG or Rhino, into a Swept Beam. Always run it as a dry run first: it
reports how many nodes it made and how far they are from the source.
`cut_start_plan`, `cut_start_tilt`, `cut_end_plan` and `cut_end_tilt` set the
end cuts in degrees; rewriting a beam with `update_guid` keeps its profile and
end cuts unless the call sets them.

Points are read as a line running straight from one point to the next. A
curve becomes an arc only where its points are close enough together that the
arc stays within the tolerance of those straight pieces (2 mm by default);
otherwise the beam follows the straight pieces. Sample a curve densely to get
arcs, or draw it as a Polyline with arcs, which converts exactly.
