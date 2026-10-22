# Learning library parts and placing them as the sketch shows: design

Date: 22.10.2026. Step 3 of three for placing library elements well (step 1,
Tapir 1.7.2, merged as PR #27; step 2, CreateObjects from the part's own
defaults, is Tapir PR #1395 with the MCP side in PR #28).

## Intent

An agent rebuilding an apartment from a sketch must get the doors, windows
and furniture right the first time: the right part, the right size, the right
way round. Success, as stated on 21.10.2026 and not contradicted: he fixes
nothing by hand afterwards.

In his first apartment test the walls were right and everything placed from
the library was not: the multisash window could not be changed to a normal
window, door and window widths could not be edited, and the beds faced the
wrong way. Step 1 fixed placing doors and windows by part name and editing
widths. Step 2 fixes objects and lamps, but only once a Tapir release ships
#1395: on 1.7.2 an object placed by name still takes the Object tool's size
and the values of its parameters. What is left besides is knowledge the API
does not hold: which part matches what the sketch draws, and which way a
placed part faces.

## How he reads a sketch (his words, 22.10.2026)

- He picks a window part by its plain name in the Window tool's settings:
  "Window" or "Double Window". Both exist under exactly those names in the
  Archicad 29 and 30 libraries.
- Lines dividing a window in the sketch give the number of sashes; many
  divisions mean a multi-sash window.
- Width is a check: a window about 1.2 m wide is a single window or at most a
  double window, never a multi-sash one.
- The opening arcs are drawn: they show which sash opens and where it is
  hinged. Doors are the same: the arc shows the swing and, with several
  leaves, which leaf opens. Real floor plans and sketches draw them alike.
- Beds: the pillows are almost always drawn, or at least the duvet, so the
  head end is known.
- Trying a couple of sample objects to get a part right is fine, "as long as
  it doesn't take like 20 min to select a window".

## The problem, as the code has it

- `create_elements` places an object or lamp by its insertion point
  (`coordinates`), `angle` (radians) and `reflected`, and a door or window by
  its host wall, `centerOffset`, `width`, `height`, `sillHeight`, `reflected`,
  `oSide` and `refSide` (Tapir's CreateObjects, CreateWindows and CreateDoors;
  `modify_elements` takes the same fields through ModifyObjects,
  ModifyWindows and ModifyDoors).
- Where the insertion point sits on a part, and which local axis is its head
  or front, differ per part and no API reports them. Live on 21.10.2026,
  Bed Double 01 inserts at its foot-left corner with its head along its own
  +Y. Rotating a bed about that corner moves it off its footprint as well as
  turning it, which is how the beds in the test ended up wrong.
- Which way `reflected` and `oSide` turn a door's or window's hinge and swing
  in plan is a property of the part's 2D script, not of the API, so the agent
  cannot know it in advance.
- Which sash of a window opens, and how, lives in the part's own GDL
  parameters, whose names differ between parts.
- Tapir's `GetAvailableLibraryParts` lists 7405 parts in MCP-Test_30, 170 of
  them with "window" in the name, mixing placeable windows with macros,
  markers, curtain wall panels, IFC helpers and dialog pictures, all with
  `typeId` Object. The agent cannot tell which ones the Window tool offers.

## Goals

The work comes in two pieces, built in this order (agreed 22.10.2026).

### Piece 1: learn a part once, then place it by what the sketch shows

1. **Learning.** For a named library part, the MCP places one or a few
   samples, measures them, shows them to the agent as an image, and deletes
   them. The agent records what it saw. What a part record holds:
   - objects and lamps: where the footprint lies around the insertion point
     in the part's own coordinates at angle 0, as a function of A and B
     (measured from samples placed at two explicit sizes, because on 1.7.2
     a sample sent without `dimensions` takes the Object tool's size); the
     local direction of the head or front, as the agent judged it; whether
     the part is one-sided (mirroring changes it);
   - doors and windows: the number of sashes or leaves; which way `reflected`
     moves the hinge and which way `oSide` moves the swing, in plan, for this
     part; the GDL parameters that set each sash's or leaf's opening, and
     the values that make a given sash or leaf open (and how) or stay fixed.
2. **Remembering.** Part records persist across sessions and projects, so a
   part learned once costs nothing later. A record is tied to the part's
   name and main GUID, the only identity Tapir 1.7.2 reports (see
   Decisions), and can be dropped and learned again on request.
3. **Placing by what the sketch shows.** `create_elements` (and
   `modify_elements`, to correct a placed element) accept, for a learned
   part:
   - objects and lamps: the footprint as the sketch shows it (its centre
     and size, or its rectangle) and the direction the head or front points,
     plus mirrored or not; the MCP works out `coordinates`, `angle`,
     `reflected` and `dimensions`. The size comes from the sketch; once the
     running Tapir has #1395 it may be left out, and the part's own is used;
   - doors and windows: the hinge side and the opening direction, stated
     against the plan (for example "hinged at the jamb nearer this point,
     opening toward this room"), which the MCP turns into `reflected` and
     `oSide` for that part; and, for a part with several sashes or leaves,
     which of them open, as the arcs show (his words: the arc shows "what
     leaf it opens if it has multiple leafs"). On 1.7.2 CreateWindows and
     CreateDoors take no GDL parameters, so the MCP sets those with
     SetGDLParametersOfElements right after the create; if that write
     fails, the element stays and the result names it as placed but not
     set (my call), so the agent can correct it.
   The plain fields keep working as today.
4. **The dry run names the part per item** and what the MCP derived (angle,
   mirroring, opening side), so he and the agent see "Window, 1.2 m, hinged
   left, opening in" before anything is placed. An item naming a part with
   no record says so and asks for it to be learned first.
5. **Checking the result.** After placing, the agent captures the plan with
   `capture_window` and compares arcs, heads and footprints with the sketch,
   correcting with `modify_elements` before reporting done. The tool
   descriptions tell the agent to do this; the MCP does not judge images.
6. **The agent's reading rules** go into the tool descriptions (or the
   server instructions): choose a part from the sashes, leaves and arcs
   drawn; prefer the plain generic part ("Window", "Door", "Double Window")
   when the sketch does not say more; treat width as a check (about 1.2 m is
   a single or double window); find the head end from the pillows or the
   duvet; ask when the sketch shows neither, or when the count and the width
   disagree.

### Piece 2: part reads from the Tapir release

7. **Finding parts as the tools offer them.** A read that lists the placeable
   parts of a kind (window, door, object, lamp, and others the tools know),
   optionally narrowed by name, using Tapir's GetLibraryPartDetails and
   IsLibraryPartSubtypeOf (bot PR #1273: `creatorElementType`,
   `isPlaceable`, `fixSize`) and GetLibraryPartMetadata (bot PR #1272:
   description, keywords). Until the running Tapir has them, it falls back to
   the plain name list and says that kinds are unknown.
8. **A part's own defaults before placing.** Its default size and parameters
   from GetGDLParametersOfLibraryParts (bot PR #1234). Learning (goal 1) uses
   it, when present, to find the sash and leaf parameters without guessing.
9. **Hotspots and transforms** (bot PRs #1335 and #1132) replace or confirm
   the measured footprint and the placed angle, when present.
10. **Swapping a placed part** (bot PR #1394, ChangeLibraryPartOfElements):
    `modify_elements` (or a new field on it) can switch a placed door, window
    or object to another part, keeping its size. Optional in this piece;
    worth it because his first failure was exactly this.

Piece 2 waits for a Tapir release that ships those PRs. They are due to merge
by themselves on 23.10.2026 (#1394 on 25.10); Tapir releases weekly on
Mondays, so probably 26.10. The MCP's definitions are synced from that
release, and every new read is gated on the running Tapir having the command
(`tapir_requirements.py`), as the other tools are.

## Speed (his limit, with targets that are my call)

- Choosing a part already learned: seconds, no samples.
- Learning a new part: about a minute or two, with all its samples placed
  side by side and captured in one image.
- Placing a floor's openings or furniture: one batch, then one look at the
  plan, not one per element.
- Never anything like 20 minutes for one window.

## Non-goals

- Reading the sketch inside the MCP. The agent reads it (it sees images);
  the MCP measures, remembers, derives and places.
- Knowing a part's front without looking. No API holds it; the agent judges
  it once from a sample.
- A preview picture of a part before placing. None of the bot PRs gives one
  (#1272 leaves GSMObject's preview unused); a sample stands in for it.
- Keyword search of the library (bot PR #1353): held for a maintainer
  (`needs-maintainer`), its pattern takes words, not the wildcards it
  documents, and it caps at 50 results. Name filtering in the MCP serves
  meanwhile.
- New Tapir commands of our own in this step. Findings on the bot PRs are
  reported on them, with his yes (the case-sensitive name note on #1394 was
  posted 22.10.2026).
- A hand-curated catalogue of office parts. Records come from learning; an
  office list could seed them later.

## Anti-goals

- A sample left in his model. Every learning call deletes what it placed,
  also when it fails part way, and checks that the element count is back.
- A sample that disturbs his drawing: it goes on the current story, clear of
  the model, never into a selection, a schedule or a Teamwork reservation he
  holds.
- A side effect kept quiet. What 1.7.2 cannot undo is reported in the
  result: the Door or Window tool left on the last sample's part (accepted
  by him), and the plan left zoomed on the samples (there is no command to
  set the zoom back).
- A wrong orientation reported as done. Placing is followed by a look.
- A record trusted for another part: a different main GUID under the same
  name refuses the old record.
- Slowness, as above.

## Decisions

His (22.10.2026): the part follows the sashes, leaves and arcs drawn; width
is a check; the head end comes from the pillows or the duvet; a couple of
samples per part is fine, 20 minutes per window is not; the two pieces in
this order; learning may leave the Door and Window tools set to the last
sample's part (Tapir 1.7.2's CreateDoors and CreateWindows change the tool
defaults by `libraryPartName` and never restore them), as long as the
result says so; our doors and windows Tapir PR fixes it later.

Mine (stated in chat on 22.10.2026, he answered "ok"):

- **Learn once, remember for good.** Records live in a file under the MCP's
  own data folder (the base of `tapir_install.install.backup_root`:
  `~/Library/Application Support/archicad-mcp` on macOS, `%LOCALAPPDATA%`
  on Windows), keyed by the part's name and main GUID.
- **Identity without revisions (builder check, 22.10.2026).** Tapir 1.7.2's
  `GetAvailableLibraryParts` `guid` and `GetDetailsOfElements`
  `libPart.ownUnID` are both the main GUID only; no 1.7.2 command reports a
  revision, so a part saved again keeps its key. That is accepted for now:
  the look after every placement (goal 5) catches a part that draws
  differently, and a record can be dropped and learned again. When a Tapir
  read reports the full unId with its revision (bot PR #1273 returns
  `unId`), records key on it. Worth noting live: whether "Window" has the
  same main GUID in the Archicad 29 and 30 libraries.
- **Objects carry their size on 1.7.2.** A sample or an item sent without
  `dimensions` takes the Object tool's current size (the 5.4 m bed of
  21.10.2026), so samples are measured at two explicit sizes and object
  items take their size from the sketch. Records stay valid after the
  Tapir upgrade, because they describe the footprint as a function of A
  and B, not one size.
- **The active window.** Learning places and captures on the floor plan;
  when another window is active it refuses with a message instead of
  switching behind his back, as `create_elements` already needs the floor
  plan or 3D window for model elements.
- **The MCP measures, the agent judges.** Geometry (footprint around the
  insertion point, sizes) is measured from the placed sample: the floor plan
  2D bounding box (official `Get2DBoundingBoxes`) or Tapir's
  `Get3DBoundingBoxes`, later hotspots. What only a look tells (head side,
  which flag flips the hinge or the swing) the agent records after seeing
  the capture.
- **Samples go in the project being worked on**, because that is where the
  part's library is loaded: on the current story, just outside the extent of
  the model, deleted in the same call.
- **Placement by footprint and head** is an addition to the item fields, not
  a replacement.
- **The plain generic part is the default** when the sketch says only
  "window" or "door".

## Constraints

- Tapir 1.7.2 is installed in Archicad 29 and 30 now; piece 1 must work on
  it. Piece 2 needs the next release.
- Archicad 29 and 30, macOS and Windows, as the rest of the MCP.
- Write tools follow the MCP's pattern: dry run by default, `_guarded`,
  credentials scrubbed, refusals with the reason, element guards for other
  views, hidden or locked layers and Teamwork.
- Live tests only on MCP-Test (Archicad 29, port 19723) or MCP-Test_30
  (Archicad 30), after checking the project name, near the origin, every
  element deleted afterwards.
- No em or en dashes anywhere.

## Testing

- Offline: records saved and read back, a different main GUID refusing the
  old record, the footprint-and-head arithmetic (every quadrant, mirrored,
  a centre insertion point and a corner one, two sizes), the
  hinge-and-swing mapping, the sash and leaf parameter write after a
  create (and a failed one), dry-run texts.
- Live, MCP-Test:
  1. learn Bed Double 01, Chair 01, "Window", "Double Window" and "Door";
     each learning call leaves the element count as it found it;
  2. place a bed by its footprint (centre and size) with the head toward
     each of the four walls of a room: the bed covers its rectangle, the
     pillows at the given end;
  3. place a window and a door in a wall with each hinge side and each
     opening direction, and a Double Window with only its left sash
     opening: the plan arcs match what was asked;
  4. a small apartment from a sketch, timed: a known part in seconds, the
     floor in one batch and one look.

## Left to the builder

The tool names and how learning splits into calls (one call that places,
captures and waits for the agent's verdict, or a measuring call and a
recording call); the file format; how many samples a door or window needs to
tell what `reflected` and `oSide` do (possibly four placed side by side in
one temporary wall); how to find sash and leaf parameters on 1.7.2, before
GetGDLParametersOfLibraryParts exists (reading a placed sample's parameters
with GetGDLParametersOfElements is the obvious route); how the hinge side
and opening direction are stated in an item; where exactly outside the
model a sample goes.
