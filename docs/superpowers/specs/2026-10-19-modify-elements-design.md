# modify_elements: Design

**Date:** 2026-10-19
**Status:** Built on branch `feature/modify-elements` (2026-10-19): plan executed, final review fixes in, live dry run on MCP-Test. Not merged; the live commit and lock checks wait for Aleš.
**Repo:** https://github.com/alesdev88/Archicad-MCP.git

## Purpose

The MCP can create, move and delete elements, but it cannot change one except
through the raw gateway (`execute_write_api_command`). The 2026-10-19 Tapir
command triage (`docs/superpowers/specs/2026-10-19-tapir-command-triage.md`)
ranked that the largest gap. `modify_elements` closes it with one curated,
dry-run-by-default tool over Tapir's `SetDetailsOfElements` (story, layer, draw
order) and its 13 `Modify*` commands (walls, beams, slabs, columns, windows,
doors, morphs, roofs, meshes, objects, lamps, texts, labels).

Typical asks it must answer in one dry run and one commit:

- "Move these 40 objects to layer Interior - Furniture."
- "Put these slabs on story 2." "Bring these hatches to the front."
- "Make these walls 2.80 m high." "Change the composite of these slabs to X."
- "Make all these doors 0.90 m wide." "Turn these lamps off."

Success means the dry run shows, per element, the current value next to the new
one, names every element Archicad would refuse and why (including locked and
grouped ones), and a commit reports what Archicad actually did rather than what
was asked.

The raw commands are full of traps a caller cannot see from their schemas (read
in the Tapir 1.5.9 source, see "What the Tapir source says" below): absolute z
writes that silently move an element to another story, a draw order that always
answers success, result lists that come back in another order than sent, a
rotation that turns about the wrong axis, partial objects that zero their
missing fields. Catching those is most of this tool's value over the gateway.

## What exists and is reused

| Piece | Where | Used for |
|---|---|---|
| Editability pre-check | `core/editability.py` | Elements Archicad would refuse: not in the active window's database, hidden layer, not reserved in Teamwork, not editable. Refactored so the tool gets the kind of refusal, not only its text (below). |
| Refusal grouping | `core/element_data.py` `group_failures()`, `cap_list()`, `error_fields()`; `core/mutate.py` `_group()` | Archicad's per-element refusals grouped by code and message; skipped elements grouped by reason; GUID lists capped at 50. |
| Command schemas | `gateway/registry.py` `build_registry()` | Each Modify command's resolved input schema and "since" version, so the tool knows the fields without a hand-written copy and follows Tapir definition syncs. |
| Command availability | `ArchicadConnection.tapir_command_available()` | Skip a change whose command the installed Tapir lacks. |
| Property reads | `extract.py` `fetch_property_values()` | The built-in `General_Locked` property (element lock). |

## Live facts (MCP-Test, port 19723, AC 29/5101, Tapir 1.5.10, 2026-10-19, reads only)

- `GetDetailsOfElements` with `fields: [type, id, floorIndex, layerIndex,
  drawIndex, details, hotlinkId]` answers per element in request order; an
  unknown GUID gets `{"error": {"code": -2130313115, "message": "Failed to get
  the details of element"}}`. `hotlinkId` is absent on an element that did not
  come in through a hotlink.
- A slab on story 1 (level 3.0) reads `zCoordinate: 3.25` and `level: 0.25`.
  Its `polygonOutline` repeats the first point at the end.
- An object reads `origin` {x, y, z}, `dimensions`, `angle`, `useFixSize`,
  `visibility`, `linkToSettings` and the pen and attribute fields.
- Tapir `GetAttributesByType` answers `{attributes: [{attributeId: {guid}, index,
  name}]}`; `index` is what `layerIndex` holds. Layer names can carry trailing
  spaces ("Interior - Furniture ").
- Tapir `GetLayers` (1.5.4) answers `index`, `name`, `isHidden`, `isLocked`.
- Tapir `GetStories` answers `stories: [{index, level, name}]`; names can be empty.
- `GetGroupsOfElements` answers `{"error": {"code": -2130313215, "message": "The
  element is not part of any group."}}` for an ungrouped element, otherwise
  `{"groupId": {"guid"}}`. `GetSuspendGroupsMode` answers `{"suspendGroups": bool}`.
- The built-in properties `General_Locked`, `IdAndCategories_Locked` and
  `ModelView_IsLayerLocked` exist and read `false` on an unlocked slab, object and
  mesh. Not yet seen: a `true` reading on a locked element (needs a LockElements
  write, see Live check).

## What the Tapir source says (tag 1.5.9, read 2026-10-19; installed is 1.5.10)

File names under `archicad-addon/Sources/`.

**Results.** Each command is one undo step; a failed item never rolls back the
others. `executionResults` has one entry per item, matched by position (no GUID
is echoed). Beams, columns and slabs change in two steps, so a failed item can
be partly changed.

**SetDetailsOfElements reorders its results.** Before running, it moves every
Door, Window and Skylight whose `drawIndex` is absent, 0 or above 7 to the
front of the batch, keeping their relative order (ElementCommands.cpp
1796-1827). Results come back in that order. The tool sends the batch already
in that order, so position still matches.

**SetDetailsOfElements fields.**
- `floorIndex` sets the home story only. The element keeps its height above the
  story, so its absolute elevation changes by the difference of the two story
  levels. No check that the story exists.
- `layerIndex` is the integer of `GetAttributesByType` `index`. No check that
  the layer exists.
- `drawIndex` is applied with BringForward / SendBackward / ResetOrder steps,
  clamped to 1 to 14, 0 resets to the type's default (5 drawings, 6 hatches and
  zones, 7 model elements, 8 objects and lamps, 9 lines and beams, 10
  dimensions, labels, texts). Doors and windows cannot go below 7, and above 7
  they move by stepping their wall. Step errors are ignored: the item always
  answers success.

**Absolute z writes re-home the element.** ModifySlabs `zCoordinate`,
ModifyColumns `zCoordinate`, ModifyRoofs `level`, ModifyObjects / ModifyLamps
`coordinates.z` take an absolute z and set the home story to the highest story
whose level is at or below it (CommandBase.cpp 742-782). Resending an unchanged
z can therefore move an element to another story: an object homed on story 1
with a negative offset lands on story 0 at the same height. A z below the lowest
story lands on story 0 with the z as its offset. ModifyTexts `coordinate.z`
only picks the story; its offset is discarded.

**Wrong element type.** ModifyWalls, Beams, Slabs, Columns, Windows, Doors and
Morphs do not check the element type; given another type's GUID they write the
wrong union member. The tool routes by the type it read, never by guess.

**Partial objects.** Wall `visibility` is rebuilt from scratch: omitted
sub-fields become false or 0. Beam and column `coverFill.transformation` and
slab `floorFill.orientation` zero their omitted sub-fields. Object, lamp and
text `coordinates` / `coordinate` and object `dimensions` need all of x, y, z.

**ModifyMorphs `rotationDegreesZ` is wrong.** It mixes rows 0 and 2 of the
transformation matrix, columns 0 to 3 (ExtendedElementCommands.cpp 6457-6467).
Rows 0 and 2 are x' and z' (APIdefs_Base.h 408-410), so this turns the morph
about the world Y axis and swings it around the project origin. A turn about Z
mixes rows 0 and 1. Read in the source, not seen live.

**Archicad discards** object and lamp `offset` and `useFixedAngle`, and lamp
`lightColor` (Tapir's own schema descriptions).

**Lock reads.** FilterElements `IsEditable` is `APIFilt_IsEditable`: "is not on
a locked/deleted/invisible layer" in the DevKit header. Live it also rejected
elements in hotlinked modules and unreserved Teamwork elements
(`editability.py` docstring). Whether it rejects a manually locked element is
unknown. Tapir has no read of the element lock (`API_Elem_Head.lockId` is never
read); the built-in `General_Locked` property is the read the tool uses. Layer
locks read from `GetLayers` `isLocked`.

**Object `dimensions`** before Tapir 1.6.0 are scaled wrongly when the object's
`useFixSize` is false, both on write (#719) and on read (#720). 1.6.0 and later
carry both fixes.

## The tool

```
modify_elements(changes: list[dict], dry_run: bool = True, port: int | None = None) -> dict
```

Registered in `server.py` right after `move_elements`, full mode only, with
`_tool_meta("Modify elements", read_only=False, destructive=True)`. Dry run by
default, like `set_element_data`: `dry_run=false` commits. No separate
`confirm` (move and delete have one because they have no dry run).

### A change

```json
{"guid": "05C7A02F-...",
 "story": 1,
 "layer": "Structural - Bearing",
 "draw_order": 0,
 "thickness": 0.25,
 "compositeId": "Slab 250"}
```

- `guid` (required) and at least one more field.
- **Any element type**, sent through `SetDetailsOfElements`:
  - `story`: Archicad's story index (ground floor 0, basements negative, the
    index `find_elements` uses), or a story name that matches exactly one story.
    By default the element keeps its height above the story, as Tapir and
    Archicad do, so it moves up or down by the difference of the two story
    levels; the dry run says by how much.
  - `keep_elevation: true` (with `story`, decided by Aleš 2026-10-19): relink
    without moving. After the story change the tool moves the element back by
    the level difference with Tapir `MoveElements`, the recipe that relinked the
    SketchUp IFC import on 2026-09-30. If Suspend Groups is off and the element
    is grouped, the tool turns Suspend Groups on for that move and restores it
    afterwards, so the move does not drag the group along. Refused on 2D and
    annotation types (texts, labels, dimensions, hatches, lines, arcs, circles,
    splines, polylines, hotspots, drawings, pictures, markers), which have no
    elevation.
  - `layer`: a layer name. Exact match first; failing that, the one layer whose
    name matches after trimming spaces and ignoring case.
  - `draw_order`: an integer 1 to 14, or 0 for the element type's default.
- **Type-specific fields**, sent through the element type's Modify command,
  under Tapir's own field names (`height`, `thickness`, `sillHeight`,
  `lightIsOn`, ...). The allowed names come from the command's input schema in
  the bundled definitions. For a mesh the tool wraps the fields into
  ModifyMeshes' `meshData`; the caller passes them flat like every other type.
- **Attributes by name.** These fields take an attribute name or Tapir's
  `{"guid": ...}`:

  | Field | Attribute type |
  |---|---|
  | `buildingMaterialId` | BuildingMaterial |
  | `compositeId` | Composite |
  | `profileId` | Profile |
  | `surfaceId` | Surface |
  | `lineTypeId`, morph `cutLineType`, `uncutLineType`, `overheadLineType` | Line |
  | `sectionFillId`, morph `coverFillType` | Fill |
  | mesh `lineTypeIndex` (an index, not a GUID) | Line |
  | `referenceMaterial`, `oppositeMaterial`, `sideMaterial`, `topMaterial`, `bottomMaterial` | Surface, as shorthand for `{"overridden": true, "attributeId": ...}` |

  A Tapir object for an overridden material or pen (`{"attributeId": ...}`,
  `{"penIndex": ...}`) with no `overridden` gets `"overridden": true`.
- **Units.** Model lengths and coordinates in metres, angles in radians, as the
  API and `find_elements` use. Two exceptions, both Tapir's and Archicad's own:
  morph `rotationDegreesZ` would be degrees (it is refused, below), and text and
  label sizes (`style.height`, `leaderLine.arrowSize`, `leaderLine.contourOffset`)
  are paper sizes in millimetres, as in Archicad's dialogs.
- **Partial values are completed from the current read** for the fields where
  Tapir would otherwise zero or refuse them:

  | Field (types) | Completed from |
  |---|---|
  | `coordinates` (Object, Lamp) | `origin` x, y, z |
  | `dimensions` (Object, Lamp) | `dimensions` x, y, z |
  | `coordinate` (Text) | `position` x, y and `zCoordinate` |
  | `begCoordinate`, `endCoordinate` (Wall, Beam), `origin` (Column) | the same keys |
  | `visibility` (Wall, Object, Lamp) | `visibility` |
  | `coverFill` (Beam, Column), `floorFill` (Slab) | the same keys, nested sub-objects too |
  | `translation` (Morph) | missing axes are 0 (it is a move by) |

- The same GUID in two changes is merged into one; the same field with two
  different values skips that element.

### Tapir version

The tool needs Tapir 1.5.9 or newer (the `fields` filter and `hotlinkId` of
GetDetailsOfElements; GetLayers, GetGroupsOfElements and the Suspend Groups
commands are older). Below that it refuses the whole call with the installed
version and a link to the releases page, sending nothing. Aleš runs 1.7.1 from
2026-10-19. Its definitions add 15 wall fields and `circleBased` on beams; the
bundled definitions in this branch are 1.5.9, so those fields are refused as
unknown until a definitions sync, after which they work with no code change.
Every trap listed above is unchanged at 1.7.1 (checked in the source).

### Element types and commands

| Element type | Command | Since |
|---|---|---|
| Wall | ModifyWalls | 1.4.0 |
| Beam | ModifyBeams | 1.4.0 |
| Slab | ModifySlabs | 1.4.0 |
| Column | ModifyColumns | 1.4.0 |
| Window | ModifyWindows | 1.4.0 |
| Door | ModifyDoors | 1.4.0 |
| Morph | ModifyMorphs | 1.4.0 |
| Roof (multi-plane only) | ModifyRoofs | 1.4.0 |
| Mesh | ModifyMeshes | 1.5.4 |
| Object | ModifyObjects | 1.5.7 |
| Lamp | ModifyLamps | 1.5.7 |
| Text | ModifyTexts | 1.5.9 |
| Label | ModifyLabels | 1.5.9 |
| anything else | story, layer, draw_order only | 1.0.7 |

### Field table: what the dry run reads as "current"

`current` and `new` are both in the read's frame, so they compare directly.
Fields not listed read under their own name in `details` with no conversion.

| Command | Field | Read from | Note |
|---|---|---|---|
| any | `story` | `floorIndex` | |
| any | `layer` | `layerIndex`, shown by name | |
| any | `draw_order` | `drawIndex` | |
| ModifyWalls | `thickness` | `begThickness` | |
| ModifySlabs | `zCoordinate` | `zCoordinate` (absolute) | not `level`, which is relative |
| ModifyColumns | `zCoordinate` | `zCoordinate` (absolute) | |
| ModifyRoofs | `level` | `zCoordinate` (absolute) | the write is absolute, `details.level` is relative |
| ModifyRoofs | `levels` | `levels` (`height`, `angle`) | key names differ |
| ModifyRoofs | `polygonOutline` | `pivotPolygonOutline` | |
| ModifyObjects, ModifyLamps | `coordinates` | `origin` | |
| ModifyTexts | `coordinate` | `position` and `zCoordinate` | |
| ModifyMorphs | `translation` | `origin`; `new` is `origin + translation` | a move by, not a position |
| ModifyWindows, ModifyDoors | `reveal`, `revealDepthOffset` | none | `current: null` |

Attribute fields show the attribute's name both sides. Beams' and columns'
cross-section fields read segment 0. Long values (polygons, holes, sublines,
morph bodies, roof levels, text runs) show as a size, such as "25 points", not
the coordinates.

### What is skipped before anything is sent

Each element is all or nothing: if any of its fields cannot be sent, none is,
and the element is listed under `skipped` with the reason. In order:

1. Not found: no details for the GUID in the active window's database.
2. Not editable, from the editability check, with the reason sharpened:
   hidden layer; not reserved in Teamwork; inside a hotlinked module
   (`hotlinkId`); on a locked layer (named); locked with Edit > Locking
   (`General_Locked`); otherwise "Archicad reports it as not editable". The
   last three are checked on every element, not only those IsEditable
   rejected, since IsEditable may miss an element lock. When the active window
   is not the floor plan, the reason leads with it, as `move_elements` does.
   The tool never unlocks, reserves or turns a layer on.
3. A type-specific field on a type no Modify command covers, or a field the
   type's command does not have. The reason lists the allowed fields.
4. Fields the tool refuses, with the reason:
   - `elementId`; `floorIndex` on a mesh or text (use `story`).
   - object and lamp `offset`, `useFixedAngle`, lamp `lightColor` (Archicad
     discards them).
   - morph `rotationDegreesZ` (turns about the wrong axis; send `xAxis`,
     `yAxis`, `zAxis`).
   - `story` on a Door, Window, Skylight or Opening (it lives in its host's
     story).
   - `story` together with an absolute z field on the same element (slab or
     column `zCoordinate`, roof `level`, object or lamp `coordinates`, text
     `coordinate`): Tapir derives the story from the z, so the two fight.
   - `polygonArcs` without `polygonOutline` (slab, roof), `polygonArcs` or
     `holes` without `polygonCoordinates` (mesh), roof `holes` without
     `polygonOutline`: Tapir ignores them.
   - mesh `contourPen`, `levelPen`, `lineTypeIndex` of 0 or less (ignored).
   - any field on a single-plane roof (`roofClass` "SinglePlane": ModifyRoofs
     refuses it).
   - object or lamp `dimensions` when Tapir is older than 1.6.0 and the
     element's `useFixSize` is false.
5. An unknown story, layer or attribute name; `draw_order` not an integer 0 to
   14; `keep_elevation` without `story`, not a boolean, or on a 2D type.
6. The built item fails the command's JSON schema (wrong type, bad enum value,
   a thickness of 0). The reason is the schema message.
7. The installed Tapir lacks the command. The reason names the command and the
   version it arrived in.

### Warnings (planned, not blocking)

Grouped by text with a count and GUIDs, like `skipped`:

- **Grouped**: the element belongs to a group, with the Suspend Groups state.
  Only this element is sent; its group mates are not part of the request.
- **Re-homed**: an absolute z field puts the element on another story than its
  current one ("moves from story 1 to story 0: Tapir takes the story from the
  absolute z"). Computed from `GetStories` levels.
- **Story change**: "keeps its height above the story, so it moves by +3.00 m";
  with `keep_elevation`, "stays at the same height; its offset from the story
  changes by -3.00 m".
- **Side effects:**
  - slab `polygonOutline` or `holes`: edge angles and per-edge surfaces reset;
    `polygonOutline` without `holes` removes the holes.
  - mesh `polygonCoordinates` without `holes` removes the holes.
  - wall or column `height` unlinks the top from its story (`relativeTopStory`
    becomes 0).
  - wall `thickness` on a Trapezoid wall changes only the start thickness.
  - beam `holes` replace all holes; beam or column `slantAngle` sets `isSlanted`
    unless it is sent; beam or column cross-section fields apply to every
    segment.
  - morph `body` replaces the whole geometry.
  - roof `polygonOutline` replaces the pivot polygon and makes the overhang an
    offset; `eavesOverhang` makes the overhang an offset too.
  - object or lamp `useFixSize` changes how the stored size is read: the object
    may change size.
  - text or label `text`, `runs` or a run-level `style` field (pen, font, faces,
    height, effects) rebuilds the content as one paragraph: word wrap turns off
    and the style applies to every run.
  - `draw_order` on a door or window: 1 to 6 are unreachable, and above its
    wall's level the door moves with the wall.

### Dry run result

```json
{"dry_run": true,
 "requested": 12,
 "planned_count": 10,
 "planned": [
   {"guid": "05C7...", "type": "Slab", "id": "SLA - 001",
    "changes": {"layer": {"current": "Structural - Bearing", "new": "Finish - Floor"},
                "thickness": {"current": 0.3, "new": 0.25}}}],
 "skipped": [{"reason": "...", "count": 2, "guids": ["..."]}],
 "warnings": [{"warning": "...", "count": 1, "guids": ["..."]}],
 "active_window": "FloorPlan"}
```

`planned` lists at most 50 elements, with `not_shown` counting the rest, like
the other write reports. `requested` counts distinct GUIDs. `active_window`
appears when anything was refused by the editability check, as in
`move_elements`. Keys with nothing in them are left out.

### Commit

Planning runs again on fresh reads; nothing from an earlier dry run is trusted.
Then:

1. `SetDetailsOfElements` for the elements with story, layer or draw order, in
   batches of 500, each batch ordered as Tapir will run it (openings needing
   their host first).
2. `MoveElements` for the `keep_elevation` elements whose story change
   succeeded, by minus the level difference in z, with Suspend Groups on
   around it when needed (restored even if the move raises).
3. Each Modify command for its elements, in batches of 500. Story first, then
   type fields, so a relative field (beam or mesh `level`) is relative to the
   new story. An element whose step 1 or 2 failed is not sent on: it is listed
   under `not_sent` with the reason, so a relative value never lands on a story
   the dry run did not show.
4. A command that raises stops the sending; what landed before it is still
   reported, with `stopped` (as `set_element_data` does). Elements not sent
   because of it are listed under `not_sent`.
5. Read back every sent element with `GetDetailsOfElements` and compare the
   scalar fields and small objects (coordinates, dimensions, attributes,
   overridden materials and pens, visibility) with what was sent: numbers
   within 1e-6, in the field table's frame. Geometry lists (polygons, holes,
   sublines, bodies, roof levels, text runs) and fields without a read are
   counted under `unverified`, not compared. A `keep_elevation` element is
   checked for its absolute height: `zCoordinate` where the read has one, else
   `origin.z`, else story level plus `level`; none of them, `unverified`.
6. Group mates: for every sent element in a group, the other members of its
   group (`GetElementsOfGroups`) are read before sending and after, and any
   whose details changed is listed under `group_mates_changed`. Whether a
   Modify or SetDetails write on one member changes the others is not known;
   this makes it visible instead of assumed.

```json
{"dry_run": false,
 "requested": 12,
 "modified": 9,
 "failed": [{"code": 6001, "message": "...", "count": 1,
             "sample": [{"guid": "...", "command": "ModifySlabs"}]}],
 "not_applied": [{"field": "draw_order", "count": 1,
                  "sample": [{"guid": "...", "sent": 1, "read": 7}]}],
 "partly_applied": {"count": 1, "guids": ["..."]},
 "unverified": {"polygonOutline": 3},
 "not_sent": [{"reason": "...", "count": 1, "guids": ["..."]}],
 "group_mates_changed": {"count": 0, "guids": []},
 "skipped": [...], "warnings": [...],
 "stopped": {"code": null, "message": "..."},
 "active_window": "FloorPlan"}
```

- `modified`: elements whose every command answered success and whose compared
  fields read back as sent.
- `failed`: Archicad's per-element refusals grouped by code and message
  (`group_failures`), the command in each sample.
- `not_applied`: success answered, field reads back different (draw order steps
  that did nothing, a discarded field Tapir has not documented yet).
- `partly_applied`: failed elements with at least one field that did land.

## Code layout

- `core/modify.py`: planning, sending, readback. Pure helpers (field table,
  completion, ordering, comparison) at module level so tests reach them
  without a connection.
- `core/editability.py`: `classify()` returns the kind of each refusal, and
  `refusals()` becomes its text wrapper with unchanged output for
  `move_elements`, `delete_elements` and `set_element_data`. New `lock_reasons()`
  for hotlink, locked layer and `General_Locked`, used by this tool only for now.
- `core/element_data.py`: `group_failures()` gains a parameter for the sample's
  second key (`property` stays the default; this tool passes `command`).
- `server.py`, `manifest.json`, README tools section, `test_tool_annotations.py`
  `WRITERS`.

## Tests

`tests/test_modify_elements.py` against a fake Tapir built from recorded shapes:
the MCP-Test reads above (slab, mesh, object, the not-found error,
`GetAttributesByType` Layer, `GetStories`, `GetLayers`, `GetGroupsOfElements`
both shapes, `GetSuspendGroupsMode`) saved under `tests/fixtures/modify_elements/`.
Wall, door, roof, text and morph details are not in MCP-Test; their fixtures are
written from the bundled schemas and say so in the file. The fake implements
SetDetailsOfElements' reordering, so a test fails if the tool maps results by
the order it sent. It also implements MoveElements and the Suspend Groups mode,
so a test fails if Suspend Groups is left on. Existing editability, move,
delete and set_element_data tests must pass unchanged.

## Live check

`list_instances` first (the port can change when Archicad restarts after the
Tapir update); MCP-Test only. A dry run (reads only) on a slab (layer,
thickness, story), an object (coordinates x only, layer, draw order) and a mesh
(level). Asked of Aleš, not done without his go-ahead: lock one MCP-Test element
with LockElements, read `General_Locked` and the dry run's reason, unlock it;
and one commit on a scratch element with readback.

### Live check, 2026-10-19 (dry run only)

MCP-Test on port 19723, Archicad 29/5101, Tapir 1.7.1 (Aleš updated it that
day; an Archicad 30 with MCP-Test_30 ran beside it on 19724 and was not used).
`list_instances` confirmed the project name first. The branch's
`modify_elements` ran in a guard that failed on any write command:

- Four changes planned: SLA - 001 layer, thickness and story 0 -> 1; FU - 003
  coordinates `{"x": 4.0}` (completed to `{"x": 4.0, "y": 0, "z": 0}`), layer
  and draw order 0; TR - 005 level; SLA - 002 story 1 -> 0 with
  `keep_elevation`. A fifth change gave FU - 003 again in lower case with
  `dimensions`; it merged into the uppercase one.
- Current values matched the recorded fixtures. Warnings: "moves by +3.00 m"
  for SLA - 001 and "stays at the same height; its offset from the story
  changes by +3.00 m" for SLA - 002. Nothing skipped.
- Commands called: only reads (GetAddOnVersion, GetDetailsOfElements,
  FilterElements, GetStories, GetAttributesByType, GetLayers,
  GetGroupsOfElements, IsAddOnCommandAvailable, GetPropertyIds,
  GetPropertyValuesOfElements). The four elements read back identical.
- New with Tapir 1.7.1: FU - 003's `dimensions` read `{x: 1.7, y: 0.54,
  z: 1.89}`, its size in metres, where 1.5.10 had read `{x: 1, y: 1}` (the
  ratio, `useFixSize` off). The #720 fix is live, and the 1.6.0 gate on
  `dimensions` let the change through.
- A first run of the live test met `4001 "Invalid program status (ongoing user
  input)"`: Archicad refuses commands while a user is mid-input. That is the
  dialog-blocked text the gateway notes had been waiting to see.

After the review fixes the same dry run ran again, still reads only. SLA - 002
had been deleted from MCP-Test in the meantime (not by this tool) and came back
skipped as "not found"; the other three planned as before.

Still owed, with Aleš's go-ahead: a LockElements / `General_Locked` check and
one commit with readback on a scratch element.

## Out of scope

- `SetDetailsOfElements` `typeSpecificDetails` (zones, lines, arcs, hotspots,
  splines, polylines, hatches, drawings, text settings).
- ModifyOpenings (Tapir #1011, not in the 1.5.x definitions), MEP routes,
  curtain walls, stairs, railings, skylights.
- Unlocking, suspending groups, reserving, turning layers on: the tool names
  the fix and leaves the decision with the user.
- GDL parameters and classifications (triage item 2), rotation of anything but
  a morph's axes (triage puts RotateElements in `move_elements`).
- Wiring `lock_reasons()` into move, delete and set_element_data: follow-up.
- Fixing `rotationDegreesZ` upstream in Tapir: follow-up.

## Decided with Aleš

- `story` moves the element with the story by default; `keep_elevation: true`
  relinks it in place (2026-10-19).
- The tool targets his Tapir 1.7.1.

## Calls made in this design (not discussed with Aleš)

- Tapir 1.5.9 is the minimum; below it the tool refuses the whole call.
- An element whose story change (or keep-elevation move) failed gets no Modify
  write.
- Group mates are read back after a commit and reported if they changed.

- Tapir's own field names for type-specific fields, not a curated vocabulary,
  so the tool follows Tapir definition syncs.
- Attributes, layers by name; stories by index or name.
- Story and layer always through `SetDetailsOfElements`; ModifyMeshes' and
  ModifyTexts' own `floorIndex` is not exposed.
- All or nothing per element; duplicate GUIDs merged.
- Readback after the commit; `modified` counted from it.
- Grouped elements warn; locked ones are skipped with the sharper reason.
- Text sizes stay in millimetres.
- `General_Locked` read on every planned element (a built-in property, the
  read class that has not crashed Archicad).
