# Railing Creation: Design

**Date:** 2026-09-01
**Status:** Approved, not yet implemented.
**Repo:** https://github.com/alesdev88/Archicad-MCP.git

## Purpose

Create Archicad Railing elements from an MCP client: free-standing railings on
a given 3D polyline, and railings laid along the edge of an existing Stair.
Neither the official JSON API nor the Tapir add-on can create a railing today.

## Where the work lives

All of it goes in the Tapir add-on fork, in C++. The MCP gains one dispatch
entry and one catalog entry, and no geometry logic.

This is a deliberate choice over the alternative of a thin C++ create command
plus geometry derivation in this server's Python. The derivation would have
been easier to iterate on and easier to unit test in Python, but it would have
made railing-on-stair a capability of this MCP server rather than a capability
of Archicad. In the add-on it is reachable from Grasshopper, from the Tapir
Python bindings, and from any other client, and it is upstreamable as a real
feature. That portability is worth the slower edit-rebuild-restart loop.

## What the API does and does not allow

Verified against the Archicad 29 API Development Kit
(`API.Development.Kit.MAC.29.3100`).

**Creation is supported.** `ACAPI_Element_Create` accepts `API_RailingID` with
a required polyline (`ACAPinc.h:3371`). The official sample `Do_CreateRailing`
(`Examples/Element_Test/Src/Element_Basics.cpp:4603`) shows the whole shape:
read defaults, fill `memo.coords`, `memo.parcs` and `memo.polyZCoords`, set
`element.railing.nVertices`, create. Per-vertex Z means a sloped reference line
is expressible.

**Association to a host is not supported.** This is the constraint that shapes
the rest of the design. `API_RailingType` carries no host or owner field:

```
head, linkToSettings, defNode, defSegment, visibility,
isAutoOnStoryVisibility, nVertices, referenceLinePen,
contourPen, nNodes, nSegments, bottomOffset
```

`API_StairType` carries no railing field either, and the only relationship
function in the kit is a getter,
`ACAPI_HierarchicalEditing_GetHierarchicalElementOwner`
(`ACAPI_Goodies.h:1205`). There is no setter anywhere in the kit.

A railing created through the API is therefore always free-standing. It can be
placed so that it sits correctly on a stair, but it will not follow that stair
if the stair is later edited. Accepted, with a recorded host link (below) so a
later re-sync tool has something to work from.

**Tool defaults are already reachable.** Tapir ships
`ApplyFavoritesToElementDefaultsCommand`, and `CommandBase.cpp:813` and `:891`
already map `API_RailingID` to and from `"Railing"`. Applying a Railing
favorite to the Railing tool defaults works through
`execute_write_api_command` today. No new code, it only needs documenting.

## Component 1: CreateRailingsCommand

New C++ command in the fork, deriving from `CreateElementsCommandBase`, the
same base `CreateStairsCommand` uses:

```cpp
CreateElementsCommandBase ("CreateRailings", API_RailingID, "railingsData")
```

The base class (`ElementCreationCommands.cpp:40-170`) already provides per-item
`favoriteName` with defaults snapshot and restore, a per-item defaults reset
(the fix for values leaking from one item into the next), undo wrapping, and
element notifications. Only two methods need writing:
`GetInputParametersSchema` and `SetTypeSpecificParameters`.

Registration in `AddOnMain.cpp` alongside the other creation commands, at the
next Tapir minor version.

### Geometry: two mutually exclusive modes per item

- `referenceLinePoints`: an array of `{x, y, z}`. Free-standing railing.
- `hostStairGuid` plus `side` (`"left"` or `"right"`). Derived from the stair.

Giving both, or neither, is a per-item error. Errors are per item, not per
call, matching how the base class already reports them.

### Settings

Three layers, applied in this order, each overriding the one before:

1. The Railing tool defaults, read by `ACAPI_Element_GetDefaults`.
2. `favoriteName`, if given. This is the only route to rails, posts,
   balusters, panels and patterns.
3. Explicit field overrides, all optional: `bottomOffset`,
   `referenceLinePen`, `contourPen`, `floorIndex`,
   `isAutoOnStoryVisibility`.

The overrides are limited to the flat `API_RailingType` fields listed above,
because those are the only ones the struct exposes. The schema description must
say so, so that nobody hunts for a baluster-spacing parameter that cannot
exist.

### Creation

Follows the official sample: `memo.coords` 1-based, `memo.parcs`,
`memo.polyZCoords` sized `nCoords + 1`, and
`element.railing.nVertices = nCoords`.

### dryRun

`dryRun: true` returns the derived reference-line points and the resolved
settings without creating anything. This is the diagnostic path for the vertex
filter below, and the reason no separate read command is needed.

## Component 2: nosing derivation

Runs inside `SetTypeSpecificParameters` when `hostStairGuid` is given.

1. `ACAPI_Element_Get` the host stair, then `ACAPI_Element_GetMemo` with
   `APIMemoMask_All`.
2. Take `stairBoundary[0]` for the left side, `stairBoundary[1]` for the right
   (`APIdefs_Elements.h:14249` documents the index order).
3. Walk the vertices. Keep those whose `vertexData[i]` is tread-typed with
   `isTop` set. That is the nosing line. Landing vertices come along because
   they belong to the same boundary polyline.
4. Apply `horizontalOffset` along the inward normal, and `verticalOffset` to
   each Z.
5. Inherit `floorInd` from the stair.

`API_StairBoundaryData` gives `coords`, `pends`, `parcs`, `edgeData` and
`vertexData` (`APIdefs_Elements.h:14251`), and
`API_StairBoundaryVertexData` carries `zValue` per vertex
(`APIdefs_Elements.h:14173`). Vertex types come from `API_StairPolyTypeID`,
whose members include `APISP_Tread`, `APISP_Riser`, `APISP_LeftBoundary`,
`APISP_RightBoundary` and `APISP_WalkingLine`
(`APIdefs_Elements.h:13630-13643`).

### The unverified assumption

There is no `APIMemoMask_StairBoundary`. The mask list
(`APIdefs_Elements.h:17731-17796`) goes from `APIMemoMask_StairStructure`
straight to the Railing masks. Nothing in the dev kit examples reads
`stairBoundary`, and neither does Tapir. So the plan above rests on
`APIMemoMask_All` populating a field that nobody has been shown to read.

**This is the first thing to verify, before any other work.** A throwaway
command that reads one stair's memo and reports whether `stairBoundary[0]` and
`stairBoundary[1]` are populated, and how many vertices each has, settles it in
one build.

If they come back empty, in order of preference:

- **Fallback A.** Every tread is a real element. `API_StairTreadType` carries
  `head` and an `owner` GUID (`APIdefs_Elements.h:15413`, `:15470`), and
  `APIMemoMask_StairTread` returns the stair's tread data. Enumerate the
  stair's `API_TreadID` children and read each one's own polygon.
- **Fallback B.** Compute analytically from `API_StairType`: baseline,
  `flightWidth`, `riserHeight`, `treadDepth`, `stepNum`. Correct for standard
  flights, wrong for any hand-edited stair. This is the floor, not the target.

## Component 3: host link

When `hostStairGuid` is given, the command records which stair the railing came
from, so that a later re-sync tool has something to find.

- Ensure a property group and a text property definition exist, using
  `ACAPI_Property_CreatePropertyGroup` (`ACAPinc.h:7956`) and
  `ACAPI_Property_CreatePropertyDefinition` (`ACAPinc.h:7973`). Tapir already
  has working usage of both at `PropertyCommands.cpp:901` and `:1326`.
- Write the host stair's GUID onto the created railing.

Two constraints on this:

- It fires only on the stair path. A free-standing railing never touches the
  property schema.
- The response states explicitly when it created the group or the definition.
  Writing to a project's property schema is a real change to an office
  template, and it must not happen quietly.

The group and property names are **not yet chosen**. They land in the office
template, so they are Aleš's call, and the implementation plan must not
invent them.

## Component 4: MCP surface

Thin, and free of geometry.

- `src/archicad_mcp/core/create.py`: add `"railing": ("CreateRailings",
  "railingsData")` to `CREATE_COMMANDS`. The existing `create_elements` tool
  then reaches it, including its `dry_run`.
- `src/archicad_mcp/gateway/registry.py`: add a local overlay of fork-only
  command definitions, merged into `build_registry()`.

The overlay is not optional polish. `_resolve` in
`src/archicad_mcp/gateway/execute.py` rejects any command name absent from the
bundled catalog, and that catalog is regenerated by
`scripts/sync_tapir_defs.py` from upstream Tapir *releases*. Without an
overlay, `create_elements` runs `CreateRailings` while
`execute_write_api_command` refuses it by name, and `describe_api_command`
claims it does not exist. The same command reachable by one route and not
another, decided by an implementation detail no caller can see, is worse than
either rule on its own.

The overlay lives in its own file so `sync_tapir_defs.py` cannot clobber it,
and it carries the same fields as a synced entry so `classify_access` and
schema validation behave identically.

## Out of scope

- **True associativity.** Not expressible in the API, as established above.
  Edit the stair and the railing stays where it was put.
- **A re-sync tool.** `refresh_stair_railings` gets its own design. Rebuilding
  means delete and recreate: new GUIDs, and any hand edits to the railing lost.
  That tradeoff deserves its own discussion rather than a paragraph here.
- **Railings on slabs, roofs or free edges.** Same derivation problem, a
  different data source. Later, if wanted.

## Testing

**Python, offline.** Ordinary and cheap: the new `CREATE_COMMANDS` entry
dispatches to the right command and payload key, `dry_run` returns the payload
without connecting, the overlay merges into the registry, an overlaid command
classifies as a write, and its schema validates. Fixtures follow the existing
`tests/fixtures/api_replays.py` pattern.

**C++, live.** Tapir has no unit harness, so this is manual verification
against the small non-sensitive test model, in this order:

1. Free-standing railing on an explicit 3-point polyline with varying Z.
2. Straight single flight.
3. L-shape with a landing.
4. Winder.
5. Curved flight.
6. `favoriteName` applied, and confirmed not to leak into the next item or
   into the tool defaults after the call.
7. Property group and definition created once, reused on the second call.

`dryRun` first at every step, so a wrong vertex filter prints points instead of
placing junk in the model.

## Sequencing

1. Verify `stairBoundary` is populated. Everything downstream depends on the
   answer, and one of the two fallbacks changes the shape of component 2.
2. `CreateRailings` with `referenceLinePoints` only, plus the MCP dispatch
   entry and the registry overlay. Deliverable: railings on any polyline,
   through the MCP.
3. Stair derivation and `dryRun`.
4. Host link recording, once the group and property names are chosen.
