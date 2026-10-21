# CreateObjects from the part's own defaults: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tapir's CreateObjects and CreateLamps place a named part at its own default size and parameters, and take GDL `parameters` and a `layerIndex`; the MCP warns about doors and windows sent without `sillHeight`.

**Architecture:** One change in the per-item create path of ElementCreationCommands.cpp: after `ResolveLibraryPartName`, when the resolved `libInd` differs from the defaults', load the part's own defaults (`ACAPI_LibraryPart_GetParams`) into `memo.params` and set xRatio/yRatio from its own A/B; then `dimensions`, `parameters`, `layerIndex`. Helpers shared with GridElementCommands.cpp. The MCP change is a dry-run warning in `core/create.py`.

**Tech Stack:** C++ (Archicad DevKit 29.3000, Xcode, CMake) for Tapir; Python 3.12 (pytest) for the MCP.

**Spec:** `docs/superpowers/specs/2026-10-21-create-objects-part-defaults-design.md`

## Global Constraints

- Tapir compiles for Archicad 25 to 30 on Windows and macOS; follow the existing `#ifdef ServerMainVers_*` patterns.
- Build: `cmake -B archicad-addon/Build/AC29 -G Xcode -DAC_VERSION=29 -DAC_API_DEVKIT_DIR=archicad-addon/Build/DevKits/AC29/Support archicad-addon`, then `cmake --build archicad-addon/Build/AC29 --config RelWithDebInfo -- MACOSX_DEPLOYMENT_TARGET=12.0`. Re-run configure after adding a source file.
- The command's registered version does not change (fields on an existing command). Docs under `docs/archicad-addon` are generated; never edit them.
- Tapir worktree: `/Users/alesd/Developer/tapir-create-objects-part-defaults`, branch `fix/create-objects-part-defaults` from upstream main 8c607d93. MCP worktree: `.claude/worktrees/object-part-defaults`, branch `feature/object-part-defaults`.
- Live tests only on MCP-Test (port 19723, check `projectName == "MCP-Test"`), near the origin, deleted afterwards. Pushing to the fork and opening the upstream PR need his explicit yes.
- No em or en dashes anywhere (code comments, commits, PR text).

## Review Focus

1. The tool already holds the named part (or a `favoriteName` applied it): its size and parameters must be kept, not reset to the part's defaults. Live test 2 pins it.
2. `useXYFixSize` off on the tool defaults: xRatio/yRatio must be 1.0, not the part's A/B in metres (the #718/#719 unit trap). Live test 1 reads `dimensions` back and compares with GDL A/B.
3. `dimensions` and `parameters` A/B together: `parameters` comes last and wins, and xRatio/yRatio follow it. Live test 3 includes A in `parameters`.
4. A batch mixing an item with `favoriteName`, one with another part and one with the tool's part: each item starts from its own defaults (the loop resets per item). Live test 5 sends such a batch.
5. An unknown parameter name in one item of a batch: that item fails, the others are created. Live test 3.

---

### Task 1: Load the part's own defaults when the part differs

**Files:**
- Modify: `archicad-addon/Sources/ElementCreationCommands.cpp` (`ResolveLibraryPartName`, `GetLibraryPartSize`, `CreateObjectsCommand::SetTypeSpecificParameters`, `CreateLampsCommand::SetTypeSpecificParameters`)
- Modify: `archicad-addon/Sources/GridElementCommands.cpp` (drop its copy of the size reader in favour of the shared one)
- Create or modify: one shared place for `GetLibraryPartSize` and `ApplyParameter` (builder's choice, declared in a header both files include)
- Test: live, MCP-Test

**Interfaces:**
- Produces: `GSErrCode LoadLibraryPartDefaults (API_Element& element, API_ElementMemo& memo)`: replaces `memo.params` with the part's own list from `ACAPI_LibraryPart_GetParams (element.object.libInd, ...)` and sets xRatio/yRatio (metres with `useXYFixSize` on, 1.0 with it off).

- [ ] **Step 1:** With the installed 1.7.2, record the "before": Chair 01 and Bed Double 01 without `dimensions` (done 21.10.2026: bed 5.4 x 2.0 x 0.2 m, chair 1.2 x 0.4 x 0.2 m; parameter names were each part's own).
- [ ] **Step 2:** In both SetTypeSpecificParameters, keep the defaults' `libInd` before `ResolveLibraryPartName`; call `LoadLibraryPartDefaults` only when it changed. Read once whether `GetParams` puts A, B and ZZYZX in the list; make xRatio/yRatio and the list agree either way.
- [ ] **Step 3:** Build; expect `** BUILD SUCCEEDED **`.
- [ ] **Step 4:** Swap the build into Archicad 29 (quit AC29 first, his OK; the 1.7.2 bundle is backed up) and run live tests 1, 2 and 5 below. Expected: test 1 gives the bed and the chair their own default sizes and `dimensions` equal to GDL A/B.
- [ ] **Step 5:** Commit `fix: CreateObjects and CreateLamps start from the library part's own defaults`.

### Task 2: `parameters` and `layerIndex` on create

**Files:**
- Modify: `archicad-addon/Sources/ElementCreationCommands.cpp` (`BuildLibraryPartBasedSchema` create schema only; `CreateObjectsCommand`/`CreateLampsCommand` SetTypeSpecificParameters)
- Create: `archicad-addon/Examples/create_objects_from_part_defaults.py`

**Interfaces:**
- Consumes: the shared `ApplyParameter` from Task 1's shared place.
- Produces: schema fields `parameters` (array of `{name, value}`, value number, boolean or string, same as CreateGridElements) and `layerIndex` (integer, positive) on `objectsData[]` and `lampsData[]` items.

- [ ] **Step 1:** Apply after `dimensions`: each `parameters` entry through `ApplyParameter`; any warning it pushes fails the item with an error listing them, and no element is created. A or B among them updates xRatio/yRatio as CreateGridElements does.
- [ ] **Step 2:** `layerIndex` > 0 sets `element.header.layer` (the CreateGridElements pattern); an index that is no layer attribute fails the item.
- [ ] **Step 3:** Write the example: places a bed from its own defaults, one with `parameters` and `layerIndex`, prints what it reads back, deletes them.
- [ ] **Step 4:** Build, swap, run live tests 3 and 4 and the example. Expected: as listed below.
- [ ] **Step 5:** Commit `feat: CreateObjects and CreateLamps take GDL parameters and a layer`.

### Task 3: The upstream PR

- [ ] **Step 1:** Live test 6 (`capture_window`, bed at its own size) and the lamp (test 1 with a lamp part).
- [ ] **Step 2:** Put Tapir 1.7.2 back in Archicad 29 (`archicad-mcp install-tapir --archicad 29 --replace --yes`) and confirm `GetAddOnVersion` 1.7.2.
- [ ] **Step 3:** Draft the PR text (before and after numbers, the "same part keeps its settings" rule, unknown names fail the item) and show it to him. On his yes: push to `fork`, open the PR against `ENZYME-APD/tapir-archicad-automation` main.

### Task 4: MCP side

**Files:**
- Modify: `src/archicad_mcp/core/create.py`
- Test: `tests/test_create_elements.py`
- Commit the spec and this plan on the same branch.

- [ ] **Step 1: Write the failing test**

```python
def test_a_door_without_a_sill_height_warns_that_it_takes_the_tool_sill():
    plan = Plan(types={W1: "Wall"})
    result = create_elements(plan.connection(), "door",
                             [door(W1), door(W1, sillHeight=0.0)])
    [warning] = [w for w in result["warnings"] if "sillHeight" in w]
    assert "item 0" in warning and "Door tool" in warning
```

- [ ] **Step 2:** Run `uv run pytest tests/test_create_elements.py -k sill -q`; expect FAIL.
- [ ] **Step 3:** Extend `_TOOL_SIZED`-style handling with a door/window sill warning (`Door tool`, `Window tool`).
- [ ] **Step 4:** Run the full suite; expect all pass.
- [ ] **Step 5:** Commit `feat: create_elements warns about doors and windows without sillHeight`; the "no dimensions" warning stays until a Tapir release has Task 1 (then gate it on that version).

## Live tests (MCP-Test, Archicad 29, local build)

1. Tool on one part, create Chair 01 and Bed Double 01 without `dimensions`: each at its own default size, `dimensions` equal to GDL A/B, no ZZYZX 0.2.
2. Create a Bed Double 01 with `dimensions`, then create one more Bed Double 01 by `favoriteName` of a favourite saved from it (or with the tool set to it in the UI): the size of the first is kept.
3. `parameters`: `gs_bed_height` (number), `bHeadboard` (boolean), one string parameter, and A; an unknown name in a second item fails only that item.
4. `layerIndex` of an existing non-default layer: the object is on it.
5. A batch: favourite item, other-part item, plain item: each right.
6. `capture_window` of test 1's bed.
