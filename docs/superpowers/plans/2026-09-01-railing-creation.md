# Railing Creation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create Archicad Railing elements from an MCP client, either on an explicit 3D polyline or laid along the edge of an existing Stair.

**Architecture:** A new `RailingCommands` group in the Tapir add-on fork adds three JSON commands: `GetStairBoundaries` (read), `GetStairRailingReferenceLine` (read), and `CreateRailings` (write). All geometry derivation is C++ in the add-on, so the capability belongs to Archicad rather than to this MCP server. The MCP gains one dispatch entry and a sync-immune registry overlay, and no geometry logic.

**Tech Stack:** C++ / Archicad 29 API DevKit, CMake plus Xcode on macOS, Python 3 with pytest and fastmcp for the MCP side.

## Global Constraints

- **Design of record:** `docs/superpowers/specs/2026-09-01-railing-creation-design.md`. Read it before starting.
- **Two repositories.** Add-on work is in `/Users/alesd/Developer/tapir-archicad-automation` on branch `feature/section-dimension-chains`. MCP work is in `/Users/alesd/Developer/Archicad MCP` on `main`. Commit to each separately.
- **No em dashes (U+2014) or en dashes (U+2013)** in any file, comment, string, or commit message. Rewrite the sentence rather than swapping the character: a colon for a definition, commas or parentheses for an aside, two sentences for two welded clauses. Verify with a command that does not itself contain the characters:

```bash
python3 -c "import sys; t=open(sys.argv[1],encoding='utf-8').read(); print(sum(t.count(c) for c in chr(8212)+chr(8211)))" <file>
```

Expected: `0`.
- **Commit identity:** `Aleš Dolenec <285164556+alesdev88@users.noreply.github.com>`.
- **Command version string:** `"1.5.9"`, matching `ADDON_VERSION` in `archicad-addon/Sources/AddOnVersion.hpp` and the fork's existing `CreateAssociativeDimensionChainsOnSectionCommand` registration.
- **Live testing only against the small non-sensitive test model.** Never a production project.
- **CMake globs sources** (`Tools/CMakeCommon.cmake:156,160` use `GLOB_RECURSE ... CONFIGURE_DEPENDS`), so new `.cpp`/`.hpp` files under `Sources/` need no `CMakeLists.txt` edit.
- **Task 7 is blocked** until Aleš chooses the property group and property names. Do not invent them.

## Build and reload cycle (used by every C++ task)

```bash
cd /Users/alesd/Developer/tapir-archicad-automation/archicad-addon
cmake --build Build/AC29 --config RelWithDebInfo
```

The `Build/AC29` tree is already configured (Xcode generator, `AC_VERSION=29`, DevKit at `Build/DevKits/AC29/Support`), so no re-configure step is needed. To load the rebuilt bundle into a running Archicad:

Do NOT use `Tools/update_addon_and_restart_archicad.py`: it requires a
`--downloadUrl` and fetches a published release, so it fails on a locally built
bundle. Archicad loads the add-on from `/Applications/Graphisoft/Archicad 29/
Add-Ons/`, which is writable without sudo, so the reload is quit, copy, relaunch:

```bash
curl -s -X POST http://127.0.0.1:19723 -H 'Content-Type: application/json' \
  -d '{"command":"API.ExecuteAddOnCommand","parameters":{"addOnCommandId":{"commandNamespace":"TapirCommand","commandName":"QuitArchicad"},"addOnCommandParameters":{}}}'
# wait for the process to exit, then:
ditto "/Users/alesd/Developer/tapir-archicad-automation/archicad-addon/Build/AC29/RelWithDebInfo/TapirAddOn_AC29_Mac.bundle" \
     "/Applications/Graphisoft/Archicad 29/Add-Ons/TapirAddOn_AC29_Mac.bundle"
open "/Applications/Graphisoft/Archicad 29/Archicad 29.app"
```

`ditto` rather than `cp` because it preserves the bundle's permission bits and
symlinks, which is the same reason the upstream reload script uses it.

**Every reload is gated.** Quitting Archicad closes whatever the owner has open.
Build, then stop and ask before running the quit command.

## File structure

**Add-on (new):**
- `archicad-addon/Sources/RailingCommands.hpp`: declarations for the three command classes plus the shared derivation free function.
- `archicad-addon/Sources/RailingCommands.cpp`: implementations. New file rather than an addition to `ExtendedElementCommands.cpp`, which is already 7564 lines, and per the repo's stated "one file per command group" convention.

**Add-on (modified):**
- `archicad-addon/Sources/AddOnMain.cpp`: include the new header, register three commands in the `elementCommands` group.

**MCP (new):**
- `src/archicad_mcp/gateway/definitions/local_commands.json`: fork-only command definitions, immune to `scripts/sync_tapir_defs.py`.

**MCP (modified):**
- `src/archicad_mcp/core/create.py`: one entry in `CREATE_COMMANDS`.
- `src/archicad_mcp/gateway/registry.py`: merge the overlay in `build_registry()`.
- `tests/test_tier2_mutations.py`, `tests/test_gateway.py`: coverage for both.

---

### Task 1: Prove `stairBoundary` is populated, via `GetStairBoundaries`

The whole stair-derivation design rests on `APIMemoMask_All` populating `API_ElementMemo::stairBoundary`, which nothing in the DevKit examples or in Tapir has been shown to read. This task settles it, and its deliverable is the diagnostic command you will use for the rest of the work.

**Files:**
- Create: `archicad-addon/Sources/RailingCommands.hpp`
- Create: `archicad-addon/Sources/RailingCommands.cpp`
- Modify: `archicad-addon/Sources/AddOnMain.cpp` (include near the other command headers; registration inside the `elementCommands` group, which opens at line 280)

**Interfaces:**
- Consumes: `CommandBase`, `GetGuidFromObjectState`, `CreateErrorResponse`, `CreateFailedExecutionResult` from `CommandBase.hpp`.
- Produces: JSON command `GetStairBoundaries`, taking `{"stairs": [{"elementId": {"guid": "..."}}]}` and returning `{"stairBoundaries": [{"elementId": ..., "sides": [{"side": "left"|"right", "vertexCount": N, "vertices": [{"x": ..., "y": ..., "z": ..., "type": "...", "isTop": bool, "isFront": bool, "isBack": bool}]}]}]}`. Task 5 reuses the same memo-reading code.

- [ ] **Step 1: Create the header**

```cpp
#pragma once

#include "CommandBase.hpp"

class GetStairBoundariesCommand : public CommandBase
{
public:
    GetStairBoundariesCommand ();
    virtual GS::String GetName () const override;
    virtual GS::Optional<GS::UniString> GetInputParametersSchema () const override;
    virtual GS::Optional<GS::UniString> GetResponseSchema () const override;
    virtual GS::ObjectState Execute (const GS::ObjectState& parameters, GS::ProcessControl& processControl) const override;
};
```

- [ ] **Step 2: Implement the command**

Create `RailingCommands.cpp`. The name string for a vertex type comes from a small local mapping over `API_StairPolyTypeID` (`APIdefs_Elements.h:13630-13643`).

```cpp
#include "RailingCommands.hpp"
#include "ObjectState.hpp"

static const char* StairPolyTypeName (API_StairPolyTypeID type)
{
    switch (type) {
        case APISP_BaseLine:            return "BaseLine";
        case APISP_LeftBoundary:        return "LeftBoundary";
        case APISP_RightBoundary:       return "RightBoundary";
        case APISP_Tread:               return "Tread";
        case APISP_Riser:               return "Riser";
        case APISP_WalkingLine:         return "WalkingLine";
        case APISP_FloorPlanSymb:       return "FloorPlanSymb";
        case APISP_BreakMark:           return "BreakMark";
        case APISP_DummyTreadLeading:   return "DummyTreadLeading";
        case APISP_DummyTreadTrailing:  return "DummyTreadTrailing";
        default:                        return "Undefined";
    }
}

GetStairBoundariesCommand::GetStairBoundariesCommand () :
    CommandBase (CommonSchema::Used)
{
}

GS::String GetStairBoundariesCommand::GetName () const
{
    return "GetStairBoundaries";
}
```

Then `Execute`: read `stairs`, and for each entry call `ACAPI_Element_Get` to confirm the element is `API_StairID`, then `ACAPI_Element_GetMemo (guid, &memo, APIMemoMask_All)`. For side index 0 and 1, read `memo.stairBoundary[side]`, take `polygon.nCoords` as the count, and emit one vertex object per coordinate combining `(*coords)[i]` with `vertexData[i]`. Guard every pointer: `coords` or `vertexData` being null is the answer this task exists to discover, so report it as `vertexCount: 0` with an empty array rather than crashing. Dispose the memo with `ACAPI_DisposeElemMemoHdls`.

- [ ] **Step 3: Register the command**

In `AddOnMain.cpp`, add `#include "RailingCommands.hpp"` alongside the other command headers, then inside the `elementCommands` group:

```cpp
        err |= RegisterCommand<GetStairBoundariesCommand> (
            elementCommands, "1.5.9",
            "Returns the left and right boundary polylines of the given Stair elements, with the type flags and elevation of every vertex."
        );
```

- [ ] **Step 4: Build**

Run: `cd /Users/alesd/Developer/tapir-archicad-automation/archicad-addon && cmake --build Build/AC29 --config RelWithDebInfo`
Expected: build succeeds, `Build/AC29/RelWithDebInfo/TapirAddOn_AC29_Mac.bundle` is rewritten.

- [ ] **Step 5: Load into Archicad and run against a real stair**

Load the bundle with the reload command above. Open the small test model, select a straight stair, get its GUID, and call the command through the MCP:

Run: `execute_read_api_command` with `name="GetStairBoundaries"` and `params={"stairs": [{"elementId": {"guid": "<stair-guid>"}}]}`

This will fail with "Unknown command" until Task 4 lands the overlay. Until then call it directly:

```bash
curl -s -X POST http://127.0.0.1:19723 -H 'Content-Type: application/json' \
  -d '{"command":"API.ExecuteAddOnCommand","parameters":{"addOnCommandId":{"commandNamespace":"TapirCommand","commandName":"GetStairBoundaries"},"addOnCommandParameters":{"stairs":[{"elementId":{"guid":"<stair-guid>"}}]}}}'
```

- [ ] **Step 6: Decide, and write the answer into the spec**

**This is a gate, not a formality.**

- Both sides report a non-zero `vertexCount` with plausible `z` values that increase along the flight: the design holds. Record the observed vertex types in the spec's "unverified assumption" section, replacing it with what was actually seen, and continue to Task 2.
- Either side is empty, or every `z` is 0: **stop**. The design's Fallback A (enumerate `API_TreadID` children) or Fallback B (analytic from `API_StairType`) now applies, and Task 5 has to be rewritten. Report to Aleš before writing any more code.

- [ ] **Step 7: Commit**

```bash
cd /Users/alesd/Developer/tapir-archicad-automation
git add archicad-addon/Sources/RailingCommands.hpp archicad-addon/Sources/RailingCommands.cpp archicad-addon/Sources/AddOnMain.cpp
git commit -m "feat: add GetStairBoundaries, a read command for stair boundary polylines"
```

---

### Task 2: `CreateRailings` on an explicit polyline

**Files:**
- Modify: `archicad-addon/Sources/RailingCommands.hpp`
- Modify: `archicad-addon/Sources/RailingCommands.cpp`
- Modify: `archicad-addon/Sources/AddOnMain.cpp`

**Interfaces:**
- Consumes: `CreateElementsCommandBase` from `ElementCreationCommands.hpp`, which supplies `favoriteName` handling, per-item defaults reset, undo wrapping and element notifications (`ElementCreationCommands.cpp:40-170`). Also `ResolveFloorIndexAndOffset` and `Get3DCoordinateFromObjectState` from `CommandBase.hpp:141,62`, and `GetOptionalDouble` from `ExtendedElementCommands.cpp:70`.
- Produces: JSON command `CreateRailings`, array field `railingsData`, each item `{"referenceLinePoints": [{"x","y","z"}], "favoriteName"?, "bottomOffset"?, "referenceLinePen"?, "contourPen"?, "floorIndex"?, "isAutoOnStoryVisibility"?}`. Task 6 adds `ownerStairId`, `side`, `horizontalOffset`, `verticalOffset` to the same item schema.

- [ ] **Step 1: Give `GetOptionalDouble` a header declaration**

`GetOptionalDouble` is defined at `ExtendedElementCommands.cpp:70` with external linkage but is declared in no header, so `RailingCommands.cpp` cannot call it as things stand. Add the declaration to `ExtendedElementCommands.hpp`, next to the free functions already declared at the top of that file (`BuildMeshPolyMemoFromGeometry` and `BuildMeshSublinesMemoFromGeometry`):

```cpp
GS::Optional<double> GetOptionalDouble (const GS::ObjectState& parameters, const char* fieldName);
```

- [ ] **Step 2: Declare the command**

Add to `RailingCommands.hpp`:

```cpp
#include "ElementCreationCommands.hpp"

class CreateRailingsCommand : public CreateElementsCommandBase
{
public:
    CreateRailingsCommand ();
    virtual GS::Optional<GS::UniString> GetInputParametersSchema () const override;
    virtual GS::Optional<GS::ObjectState> SetTypeSpecificParameters (API_Element& element, API_ElementMemo& memo, const Stories& stories, const GS::ObjectState& parameters) const override;
};
```

- [ ] **Step 3: Implement the constructor and schema**

```cpp
CreateRailingsCommand::CreateRailingsCommand () :
    CreateElementsCommandBase ("CreateRailings", API_RailingID, "railingsData")
{
}
```

The schema mirrors `CreateStairsCommand::GetInputParametersSchema` (`ExtendedElementCommands.cpp:3700`). `railingsData` is a required array; each item has `favoriteName` (string), `referenceLinePoints` (array of `#/Coordinate3D`, `minItems: 2`), `bottomOffset` (number), `referenceLinePen` (integer), `contourPen` (integer), `floorIndex` (integer), `isAutoOnStoryVisibility` (boolean), with `additionalProperties: false` and `required: ["referenceLinePoints"]`.

The `favoriteName` description must repeat the base class's wording: "Optional name of a favorite to base the new element on. Its settings are applied first, then the explicitly given fields override them."

Add this sentence to the `railingsData` description, because it is the answer to the question every caller will ask: "Rails, posts, balusters, panels and patterns are not settable here. They are only reachable through favoriteName, because API_RailingType exposes no fields for them."

- [ ] **Step 4: Implement `SetTypeSpecificParameters`**

```cpp
GS::Optional<GS::ObjectState> CreateRailingsCommand::SetTypeSpecificParameters (API_Element& element, API_ElementMemo& memo, const Stories& stories, const GS::ObjectState& parameters) const
{
    GS::Array<GS::ObjectState> referenceLinePoints;
    parameters.Get ("referenceLinePoints", referenceLinePoints);
    if (referenceLinePoints.GetSize () < 2) {
        return CreateErrorResponse (APIERR_BADPARS, "referenceLinePoints must have at least 2 points.");
    }

    GS::Array<API_Coord3D> points;
    for (const GS::ObjectState& os : referenceLinePoints) {
        points.Push (Get3DCoordinateFromObjectState (os));
    }

    return ApplyRailingReferenceLine (element, memo, stories, parameters, points);
}
```

`ApplyRailingReferenceLine` is a free function in the same file, shared with Task 6. Declare it in the header as:

```cpp
GS::Optional<GS::ObjectState> ApplyRailingReferenceLine (API_Element& element, API_ElementMemo& memo, const Stories& stories, const GS::ObjectState& parameters, const GS::Array<API_Coord3D>& points);
```

It does the following, in order:

1. Resolve the storey from the first point's Z, using the pattern from `CreateStairsCommand` (`ExtendedElementCommands.cpp:3776`):

```cpp
    const auto floorIndexAndOffset = ResolveFloorIndexAndOffset (parameters, "floorIndex", points[0].z, stories);
    element.header.floorInd = floorIndexAndOffset.first;
```

2. Apply the optional flat overrides, each only when present, so the favorite's or the tool default's value survives otherwise:

```cpp
    const auto bottomOffset = GetOptionalDouble (parameters, "bottomOffset");
    if (bottomOffset.HasValue ()) {
        element.railing.bottomOffset = bottomOffset.Get ();
    }
    Int32 referenceLinePen = 0;
    if (parameters.Get ("referenceLinePen", referenceLinePen)) {
        element.railing.referenceLinePen = static_cast<short> (referenceLinePen);
    }
    Int32 contourPen = 0;
    if (parameters.Get ("contourPen", contourPen)) {
        element.railing.contourPen = static_cast<short> (contourPen);
    }
    bool isAutoOnStoryVisibility = false;
    if (parameters.Get ("isAutoOnStoryVisibility", isAutoOnStoryVisibility)) {
        element.railing.isAutoOnStoryVisibility = isAutoOnStoryVisibility;
    }
```

3. Free any polyline handles the defaults brought in, then build the reference line, following `Do_CreateRailing` (`Examples/Element_Test/Src/Element_Basics.cpp:4634-4638`). Coordinates are 1-based, index 0 unused, and `polyZCoords` is sized `nCoords + 1`:

```cpp
    if (memo.coords != nullptr) {
        BMKillHandle (reinterpret_cast<GSHandle*>(&memo.coords));
    }
    if (memo.parcs != nullptr) {
        BMKillHandle (reinterpret_cast<GSHandle*>(&memo.parcs));
    }
    if (memo.polyZCoords != nullptr) {
        BMKillHandle (reinterpret_cast<GSHandle*>(&memo.polyZCoords));
    }

    const Int32 nCoords = static_cast<Int32> (points.GetSize ());
    memo.coords = reinterpret_cast<API_Coord**> (BMAllocateHandle ((nCoords + 1) * sizeof (API_Coord), ALLOCATE_CLEAR, 0));
    memo.parcs = reinterpret_cast<API_PolyArc**> (BMAllocateHandle (0, ALLOCATE_CLEAR, 0));
    memo.polyZCoords = reinterpret_cast<double**> (BMAllocateHandle ((nCoords + 1) * sizeof (double), ALLOCATE_CLEAR, 0));

    for (Int32 i = 0; i < nCoords; ++i) {
        (*memo.coords)[i + 1].x = points[i].x;
        (*memo.coords)[i + 1].y = points[i].y;
        (*memo.polyZCoords)[i + 1] = points[i].z - floorIndexAndOffset.second;
    }

    element.railing.nVertices = static_cast<UInt32> (nCoords);

    return {};
```

The Z subtraction is deliberate: `polyZCoords` is relative to the storey the element sits on, and `ResolveFloorIndexAndOffset` returns that storey's elevation as its second member. Step 6 verifies this against the model; if railings land at the wrong height by exactly one storey elevation, this line is why.

- [ ] **Step 5: Register the command**

```cpp
        err |= RegisterCommand<CreateRailingsCommand> (
            elementCommands, "1.5.9",
            "Creates Railing elements on the given 3D reference line polylines."
        );
```

- [ ] **Step 6: Build**

Run: `cd /Users/alesd/Developer/tapir-archicad-automation/archicad-addon && cmake --build Build/AC29 --config RelWithDebInfo`
Expected: build succeeds.

- [ ] **Step 7: Live test, a level railing and a sloped one**

Load the bundle, open the test model, and call `CreateRailings` twice via the `curl` shape from Task 1 Step 5.

Level, all Z equal:
```json
{"railingsData":[{"referenceLinePoints":[{"x":0,"y":0,"z":0},{"x":3,"y":0,"z":0},{"x":3,"y":2,"z":0}]}]}
```
Expected: one railing on the floor plan following that path, standing at storey level.

Sloped, rising Z:
```json
{"railingsData":[{"referenceLinePoints":[{"x":0,"y":5,"z":0},{"x":4,"y":5,"z":1.2}]}]}
```
Expected: one railing whose top rail climbs 1.2 m over 4 m. If it comes out level, `polyZCoords` is not being read and the allocation size is the first thing to check.

Then repeat the level case with `"favoriteName": "<any existing railing favorite>"` and confirm two things: the railing picks up the favorite's rails and balusters, and after the call the Railing tool's own settings dialog is unchanged. The base class restores the defaults it borrowed, and this is the check that it did.

- [ ] **Step 8: Commit**

```bash
cd /Users/alesd/Developer/tapir-archicad-automation
git add archicad-addon/Sources/RailingCommands.hpp archicad-addon/Sources/RailingCommands.cpp archicad-addon/Sources/AddOnMain.cpp
git commit -m "feat: add CreateRailings for explicit 3D reference lines"
```

---

### Task 3: MCP dispatch entry for railings

**Files:**
- Modify: `src/archicad_mcp/core/create.py:5-12`
- Test: `tests/test_tier2_mutations.py`

**Interfaces:**
- Consumes: the `CreateRailings` command and `railingsData` array field from Task 2.
- Produces: `create_elements(element_type="railing", ...)`, reachable with the existing `dry_run` semantics.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_tier2_mutations.py`, after the existing slab tests:

```python
RAILING_ITEM = {"referenceLinePoints": [{"x": 0, "y": 0, "z": 0},
                                        {"x": 3, "y": 0, "z": 0.9}]}


async def test_create_railing_dry_run(core):
    payload = await call("create_elements",
                         {"element_type": "railing", "items": [RAILING_ITEM]})
    assert payload["dry_run"] is True
    assert payload["command"] == "CreateRailings"
    assert payload["payload"] == {"railingsData": [RAILING_ITEM]}
    assert not any(c == "CreateRailings" for c, _ in core.calls)


async def test_create_railing_commit(core):
    core.tapir_responses["CreateRailings"] = {
        "elements": [{"elementId": {"guid": "new-railing-1"}}]}
    payload = await call("create_elements",
                         {"element_type": "railing", "items": [RAILING_ITEM],
                          "dry_run": False})
    assert payload == {"dry_run": False, "created": 1,
                       "elements": ["new-railing-1"]}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd "/Users/alesd/Developer/Archicad MCP" && uv run pytest tests/test_tier2_mutations.py -k railing -v`
Expected: FAIL. `test_create_railing_dry_run` fails on `payload["dry_run"]` raising `KeyError`, because `create_elements` returns the unknown-type error dict instead.

- [ ] **Step 3: Add the dispatch entry**

In `src/archicad_mcp/core/create.py`, add one line to `CREATE_COMMANDS`, keeping the existing order and style:

```python
    "railing": ("CreateRailings", "railingsData"),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd "/Users/alesd/Developer/Archicad MCP" && uv run pytest tests/test_tier2_mutations.py -v`
Expected: PASS, including the pre-existing tests. `test_create_elements_unknown_type_points_to_gateway` still passes because it uses `"door"`, which remains unknown.

- [ ] **Step 5: Document that Railing tool defaults already work**

The spec records that changing the Railing tool's own default settings needs no new code: Tapir's `ApplyFavoritesToElementDefaults` accepts a Railing favorite, and `CommandBase.cpp:813,891` already map `API_RailingID` to and from `"Railing"`. Nothing in the codebase says so, and a reader looking for a `set_railing_defaults` tool will not find one.

Add a short note to `docs/index.md`, in whatever section covers element creation, giving the working call:

```
To change the Railing tool's own defaults so your next manual placement
inherits them, apply a Railing favorite to the tool defaults:

  execute_write_api_command
    name:   ApplyFavoritesToElementDefaults
    params: {"favorites": [{"favoriteName": "<your railing favorite>"}]}

This is separate from favoriteName on create_elements, which applies a
favorite to one new element and leaves the tool defaults as it found them.
```

Confirm the `favorites` payload shape against `describe_api_command` before writing it, since the schema comes from `#/Favorites` in the bundled definitions rather than from this plan.

- [ ] **Step 6: Commit**

```bash
cd "/Users/alesd/Developer/Archicad MCP"
git add src/archicad_mcp/core/create.py tests/test_tier2_mutations.py docs/index.md
git commit -m "feat: reach CreateRailings from create_elements"
```

---

### Task 4: Registry overlay for fork-only commands

Without this, `create_elements` runs `CreateRailings` while `execute_write_api_command` refuses it by name and `describe_api_command` reports it does not exist, because `_resolve` in `gateway/execute.py:84-95` checks the bundled catalog and `scripts/sync_tapir_defs.py` regenerates that catalog from upstream Tapir releases only.

**Files:**
- Create: `src/archicad_mcp/gateway/definitions/local_commands.json`
- Modify: `src/archicad_mcp/gateway/registry.py:86-112`
- Test: `tests/test_gateway.py`

**Interfaces:**
- Consumes: `CommandInfo` and `classify_access` from `registry.py:44,26`.
- Produces: every name in `local_commands.json` present in `build_registry()` output with `kind="tapir"`, a resolved `input_schema`, and an `access` from `classify_access`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_gateway.py`:

```python
def test_local_overlay_commands_are_registered():
    registry = build_registry()
    assert "CreateRailings" in registry
    assert registry["CreateRailings"].access == "write"
    assert registry["GetStairBoundaries"].access == "read"


def test_local_overlay_commands_carry_a_schema():
    registry = build_registry()
    schema = registry["CreateRailings"].input_schema
    assert schema is not None
    assert "railingsData" in schema["properties"]
```

Match the existing import style at the top of that file, which already imports `build_registry`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd "/Users/alesd/Developer/Archicad MCP" && uv run pytest tests/test_gateway.py -k overlay -v`
Expected: FAIL with `AssertionError` on `"CreateRailings" in registry`.

- [ ] **Step 3: Create the overlay file**

`src/archicad_mcp/gateway/definitions/local_commands.json`, shaped like one `command_definitions.js` group so the same loop can consume it:

```json
{
  "note": "Commands present only in the local Tapir fork, not in any upstream release. scripts/sync_tapir_defs.py does not touch this file. Remove an entry once it lands upstream and a synced release carries it.",
  "groups": [
    {
      "name": "Element Commands",
      "commands": [
        {
          "name": "GetStairBoundaries",
          "version": "1.5.9",
          "description": "Returns the left and right boundary polylines of the given Stair elements, with the type flags and elevation of every vertex.",
          "inputScheme": {
            "type": "object",
            "properties": {
              "stairs": {
                "type": "array",
                "items": {"$ref": "#/ElementId"}
              }
            },
            "additionalProperties": false,
            "required": ["stairs"]
          }
        },
        {
          "name": "CreateRailings",
          "version": "1.5.9",
          "description": "Creates Railing elements on the given 3D reference line polylines.",
          "inputScheme": {
            "type": "object",
            "properties": {
              "railingsData": {
                "type": "array",
                "items": {"type": "object"}
              }
            },
            "additionalProperties": false,
            "required": ["railingsData"]
          }
        }
      ]
    }
  ]
}
```

The `railingsData` item schema is deliberately loose here. The add-on holds the authoritative schema and validates on arrival, and a second copy in this file would drift. The overlay's job is reachability and read/write classification, not re-validation.

- [ ] **Step 4: Merge the overlay in `build_registry`**

In `registry.py`, add the constant next to `DEFINITIONS_DIR`:

```python
LOCAL_DEFINITIONS = DEFINITIONS_DIR / "local_commands.json"
```

Then, inside `build_registry()`, after the loop that fills the registry from `command_definitions.js` and before the official-command loop:

```python
    # Commands that exist only in the local Tapir fork. They are merged here so
    # that every route into the add-on consults one registry: without this,
    # create_elements would reach a fork command that execute_write_api_command
    # refuses by name. Upstream definitions win on a name clash, because a
    # command that has landed upstream no longer needs the overlay.
    if LOCAL_DEFINITIONS.exists():
        local = json.loads(LOCAL_DEFINITIONS.read_text(encoding="utf-8"))
        for group in local.get("groups", []):
            for cmd in group.get("commands", []):
                if cmd["name"] in registry:
                    continue
                schema = cmd.get("inputScheme")
                resolved = _resolve_refs(schema, definitions) if schema is not None else None
                registry[cmd["name"]] = CommandInfo(
                    name=cmd["name"], kind="tapir", group=group["name"],
                    description=cmd.get("description", ""), input_schema=resolved,
                    version=cmd.get("version"), access=classify_access(cmd["name"]))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd "/Users/alesd/Developer/Archicad MCP" && uv run pytest tests/test_gateway.py -v`
Expected: PASS, all of them. `test_tapir_commands_have_resolved_schemas` must still pass, which is why `_resolve_refs` runs over the overlay too.

- [ ] **Step 6: Confirm the package ships the file**

`pyproject.toml` uses a hatch wheel target. Check that `local_commands.json` is included the same way the `.js` definitions are.

Run: `cd "/Users/alesd/Developer/Archicad MCP" && uv build && python -c "import zipfile,glob; z=zipfile.ZipFile(sorted(glob.glob('dist/*.whl'))[-1]); print([n for n in z.namelist() if 'definitions' in n])"`
Expected: the output lists `local_commands.json` alongside `command_definitions.js`. If it does not, add the pattern to the wheel target's included files in `pyproject.toml` and re-run.

- [ ] **Step 7: Commit**

```bash
cd "/Users/alesd/Developer/Archicad MCP"
git add src/archicad_mcp/gateway/definitions/local_commands.json src/archicad_mcp/gateway/registry.py tests/test_gateway.py pyproject.toml
git commit -m "feat: merge a sync-immune overlay of fork-only commands into the gateway registry"
```

---

### Task 5: Nosing derivation and `GetStairRailingReferenceLine`

The approved spec put this behind a `dryRun` flag on `CreateRailings`. That is not implementable as designed: `SetTypeSpecificParameters` is `const`, returns only an optional error, and the base class calls `ACAPI_Element_Create` unconditionally afterwards (`ElementCreationCommands.hpp:13`), so a dry run would require changing a virtual signature shared by roughly twenty commands. A separate read command gives the same diagnostic, shares the derivation code, and is classified read so the MCP reaches it without a confirmation prompt.

**Files:**
- Modify: `archicad-addon/Sources/RailingCommands.hpp`
- Modify: `archicad-addon/Sources/RailingCommands.cpp`
- Modify: `archicad-addon/Sources/AddOnMain.cpp`
- Modify: `src/archicad_mcp/gateway/definitions/local_commands.json`

**Interfaces:**
- Consumes: the memo-reading code from Task 1, and the observed vertex-type behaviour recorded there in Step 6.
- Produces: the free function

```cpp
GS::Optional<GS::ObjectState> DeriveStairRailingReferenceLine (const API_Guid& stairGuid, const GS::UniString& side, double horizontalOffset, double verticalOffset, GS::Array<API_Coord3D>& points);
```

returning an error ObjectState on failure and filling `points` on success, plus the JSON command `GetStairRailingReferenceLine` taking `{"stairs": [{"elementId": ..., "side": "left"|"right", "horizontalOffset"?: number, "verticalOffset"?: number}]}`. Task 6 calls the same free function.

- [ ] **Step 1: Implement the derivation function**

In `RailingCommands.cpp`. Steps, in order:

1. `ACAPI_Element_Get` the stair. If the type is not `API_StairID`, return `CreateErrorResponse (APIERR_BADID, "ownerStairId does not identify a Stair element.")`.
2. `ACAPI_Element_GetMemo (stairGuid, &memo, APIMemoMask_All)`, with an `OnExit` guard calling `ACAPI_DisposeElemMemoHdls`.
3. Side index: `"left"` gives 0, `"right"` gives 1, per `APIdefs_Elements.h:14249`. Any other value is `CreateErrorResponse (APIERR_BADPARS, "side must be \"left\" or \"right\".")`.
4. Walk `i` from 1 to `polygon.nCoords`. Keep a vertex when `vertexData[i].type == APISP_Tread && vertexData[i].isTop`. This is the filter Task 1 Step 6 validated; if the observed data disagreed, use what was recorded there instead and update this line's comment to say what was seen.
5. Build each point as `{coords[i].x, coords[i].y, vertexData[i].zValue + verticalOffset}`.
6. Apply `horizontalOffset` by moving each point along the inward normal. For point `i`, take the unit vector perpendicular to the segment to its neighbour, sign it towards the stair's other boundary, and scale by `horizontalOffset`. Skip entirely when `horizontalOffset` is 0, which is the default and the common case.
7. If fewer than two points survive, return `CreateErrorResponse (APIERR_GENERAL, "Could not derive a reference line: the stair boundary yielded fewer than 2 usable vertices.")`. That message is what tells you a filter is wrong rather than a stair being odd, so keep it specific.

- [ ] **Step 2: Implement and register the read command**

`GetStairRailingReferenceLineCommand` follows the same `CommandBase` shape as Task 1. Per entry, call `DeriveStairRailingReferenceLine` and return either the error or `{"elementId": ..., "side": ..., "points": [{"x","y","z"}]}` using `Create3DCoordinateObjectState` (`CommandBase.hpp:66`).

```cpp
        err |= RegisterCommand<GetStairRailingReferenceLineCommand> (
            elementCommands, "1.5.9",
            "Returns the reference line a railing would follow along one side of the given Stair elements, without creating anything."
        );
```

- [ ] **Step 3: Add it to the MCP overlay**

Append a third command entry to `local_commands.json`, in the same shape as the two from Task 4. `classify_access` will class it read on the `Get` prefix, so it becomes reachable through `execute_read_api_command` with no confirmation prompt.

- [ ] **Step 4: Build**

Run: `cd /Users/alesd/Developer/tapir-archicad-automation/archicad-addon && cmake --build Build/AC29 --config RelWithDebInfo`
Expected: build succeeds.

- [ ] **Step 5: Live test across four stair shapes**

Load the bundle. For each of a straight flight, an L-shape with a landing, a winder, and a curved flight in the test model, call `GetStairRailingReferenceLine` for both sides and check three things: the point count is close to the tread count rather than double or triple it, Z rises monotonically along a flight and stays flat across a landing, and the left and right runs are offset from each other by the flight width rather than sitting on top of one another.

Record any shape that comes out wrong. A winder or curved flight failing while straight and L work is a filter problem worth fixing here, before Task 6 starts placing elements from it.

- [ ] **Step 6: Commit**

```bash
cd /Users/alesd/Developer/tapir-archicad-automation
git add archicad-addon/Sources/RailingCommands.hpp archicad-addon/Sources/RailingCommands.cpp archicad-addon/Sources/AddOnMain.cpp
git commit -m "feat: derive a railing reference line from a stair boundary"

cd "/Users/alesd/Developer/Archicad MCP"
git add src/archicad_mcp/gateway/definitions/local_commands.json
git commit -m "feat: reach GetStairRailingReferenceLine from the gateway"
```

---

### Task 6: `ownerStairId` mode on `CreateRailings`

**Files:**
- Modify: `archicad-addon/Sources/RailingCommands.cpp`

**Interfaces:**
- Consumes: `DeriveStairRailingReferenceLine` from Task 5 and `ApplyRailingReferenceLine` from Task 2.
- Produces: `railingsData` items accepting `ownerStairId` (`$ref: "#/ElementId"`, matching the `ownerWallId` convention `CreateWindowsCommand` already uses at `ExtendedElementCommands.cpp:3879`), `side` (`"left"` or `"right"`), `horizontalOffset` (number, default 0), `verticalOffset` (number, default 0), as an alternative to `referenceLinePoints`.

- [ ] **Step 1: Extend the schema**

Add the four properties to the `railingsData` item schema. `ownerStairId` is `{ "$ref": "#/ElementId" }`, so it arrives as an object with a `guid` field, not a bare string. This matches `ownerWallId` in `CreateWindowsCommand` (`ExtendedElementCommands.cpp:3879`), and it is what makes the field resolvable by `GetGuidFromObjectState`.

`referenceLinePoints` stops being in `required`, because an item now satisfies the command by supplying either it or `ownerStairId`. Say so in the item description, since JSON Schema `oneOf` across two property groups reads badly in a generated command reference: "Supply either referenceLinePoints, or ownerStairId together with side. Supplying both, or neither, is an error."

- [ ] **Step 2: Branch in `SetTypeSpecificParameters`**

```cpp
    const GS::ObjectState* ownerStairId = parameters.Get ("ownerStairId");
    const bool hasHost = ownerStairId != nullptr;
    GS::Array<GS::ObjectState> referenceLinePoints;
    const bool hasPoints = parameters.Get ("referenceLinePoints", referenceLinePoints);

    if (hasHost == hasPoints) {
        return CreateErrorResponse (APIERR_BADPARS,
            "Supply either referenceLinePoints, or ownerStairId together with side, but not both.");
    }

    GS::Array<API_Coord3D> points;
    if (hasHost) {
        GS::UniString side;
        if (!parameters.Get ("side", side)) {
            return CreateErrorResponse (APIERR_BADPARS, "side is required when ownerStairId is given.");
        }
        const auto horizontalOffset = GetOptionalDouble (parameters, "horizontalOffset");
        const auto verticalOffset = GetOptionalDouble (parameters, "verticalOffset");
        auto error = DeriveStairRailingReferenceLine (
            GetGuidFromObjectState (*ownerStairId), side,
            horizontalOffset.HasValue () ? horizontalOffset.Get () : 0.0,
            verticalOffset.HasValue () ? verticalOffset.Get () : 0.0,
            points);
        if (error.HasValue ()) {
            return error;
        }
    } else {
        if (referenceLinePoints.GetSize () < 2) {
            return CreateErrorResponse (APIERR_BADPARS, "referenceLinePoints must have at least 2 points.");
        }
        for (const GS::ObjectState& os : referenceLinePoints) {
            points.Push (Get3DCoordinateFromObjectState (os));
        }
    }

    return ApplyRailingReferenceLine (element, memo, stories, parameters, points);
```

- [ ] **Step 3: Build**

Run: `cd /Users/alesd/Developer/tapir-archicad-automation/archicad-addon && cmake --build Build/AC29 --config RelWithDebInfo`
Expected: build succeeds.

- [ ] **Step 4: Live test, the actual feature**

Load the bundle. For each of the four stair shapes from Task 5, run `GetStairRailingReferenceLine` first, eyeball the points, then place:

```json
{"railingsData":[{"ownerStairId":"<stair-guid>","side":"left","favoriteName":"<a railing favorite>"}]}
```

Expected: a railing standing on the stair, following the flight, level across landings, at the height the favorite specifies. Then place the right side too and confirm the two do not collide.

Also run the two error paths and read the messages: an item with both `referenceLinePoints` and `ownerStairId`, and an item with `ownerStairId` pointing at a wall. Both must come back naming the actual problem, and neither may create an element.

- [ ] **Step 5: Update the MCP overlay description**

The `CreateRailings` description in `local_commands.json` now understates what it does. Change it to: "Creates Railing elements, either on a given 3D reference line or along one side of an existing Stair."

- [ ] **Step 6: Commit**

```bash
cd /Users/alesd/Developer/tapir-archicad-automation
git add archicad-addon/Sources/RailingCommands.cpp
git commit -m "feat: create railings along a stair boundary"

cd "/Users/alesd/Developer/Archicad MCP"
git add src/archicad_mcp/gateway/definitions/local_commands.json
git commit -m "docs: CreateRailings also places along a stair"
```

---

### Task 7: Record the host stair link

**BLOCKED** until Aleš chooses the property group name and the property name. They land in the office template. Do not invent them, and do not start this task with placeholders.

**Files:**
- Modify: `archicad-addon/Sources/RailingCommands.cpp`

**Interfaces:**
- Consumes: `ACAPI_Property_CreatePropertyGroup` (`ACAPinc.h:7956`), `ACAPI_Property_CreatePropertyDefinition` (`ACAPinc.h:7973`), and Tapir's existing usage of both at `PropertyCommands.cpp:901,1326` as the reference for filling `API_PropertyGroup` and `API_PropertyDefinition`.
- Produces: a text property on every railing created with `ownerStairId`, holding the host stair's GUID, plus two response fields, `createdPropertyGroup` and `createdPropertyDefinition`, both booleans.

- [ ] **Step 1: Confirm the names with Aleš, and write them into this task**

Replace this step's text with the chosen group name and property name before writing any code.

- [ ] **Step 2: Implement the ensure-and-stamp helper**

A free function that looks the group up by name, creates it only if absent, does the same for the definition, and reports whether either was created. It runs once per `CreateRailings` call, not once per item.

- [ ] **Step 3: Call it only on the host path**

The free-standing `referenceLinePoints` path must never touch the property schema. Gate the call on `hasHost` from Task 6 Step 2.

- [ ] **Step 4: Surface what was created in the response**

Add `createdPropertyGroup` and `createdPropertyDefinition` to the command response. Writing to a project's property schema is a real change to an office template, and the response is the only place a caller finds out it happened.

- [ ] **Step 5: Build, then live test the first-run and second-run cases**

Run: `cd /Users/alesd/Developer/tapir-archicad-automation/archicad-addon && cmake --build Build/AC29 --config RelWithDebInfo`

Then, on a copy of the test model that does not yet have the property: place a railing on a stair, and confirm the response reports both flags true, the property exists in Property Manager, and it holds the stair's GUID. Place a second railing and confirm both flags come back false and no duplicate definition appears. Finally place a free-standing railing and confirm the property schema is untouched.

- [ ] **Step 6: Commit**

```bash
cd /Users/alesd/Developer/tapir-archicad-automation
git add archicad-addon/Sources/RailingCommands.cpp
git commit -m "feat: record the host stair on railings created along one"
```

---

## After the plan

Not in scope, and each needs its own conversation:

- **`refresh_stair_railings`.** Rebuilding means delete and recreate: new GUIDs, hand edits lost.
- **Upstreaming.** `CreateRailings`, `GetStairBoundaries` and `GetStairRailingReferenceLine` are all individually useful and none depend on fork-local behaviour. Once any lands upstream and a release carries it, delete its entry from `local_commands.json` and let `sync_tapir_defs.py` supply it.
- **Railings on slabs, roofs and free edges.** Same derivation shape, different data source.
