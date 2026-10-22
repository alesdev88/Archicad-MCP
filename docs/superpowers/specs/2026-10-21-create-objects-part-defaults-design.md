# CreateObjects from the library part's own defaults: design

Date: 21.10.2026. Step 2 of three for placing library elements well (step 1,
Tapir 1.7.2, merged as PR #27; step 3, the library part reads, follows).

## Intent

An agent rebuilding an apartment from a sketch must be able to place a named
library part and get that part, at the size and settings the part itself
defines, without anyone first picking it in Archicad's Object Settings. Today
it gets the named part wearing another part's settings.

## The problem, as the code has it

Tapir's `CreateObjects` and `CreateLamps` (archicad-addon/Sources/
ElementCreationCommands.cpp, main 8c607d93, 20.10.2026) start every item from
`ACAPI_Element_GetDefaults`, the Object (or Lamp) tool's current settings and
parameter list, and `ResolveLibraryPartName` then only sets
`element.object.libInd` to the named part. So the new object keeps:

- the tool's xRatio/yRatio and A, B, ZZYZX unless `dimensions` is sent (live
  29.09.2026, AC27, Tapir 1.5.9: the Object tool's size, not the part's own);
- the parameter list (`memo.params`) of whatever part the tool holds, applied to
  a different part;
- the tool's layer (no layer field; on the LZV tunnel job objects landed on a
  hidden layer and later parameter writes failed).

Tapir 1.7.2 already loads the named part's own defaults for doors and windows:
`ApplyWindowOrDoorLibraryPartToDefaults` (ExtendedElementCommands.cpp) calls
`ACAPI_LibraryPart_GetParams` before the element is built. It is not the model
to copy whole, though (builder check, 21.10.2026): it loads them whenever
`libraryPartName` is sent, with no same-part check, so a favourite of that part
loses its parameter list, and it writes the Door and Window tool defaults
through `ACAPI_Element_ChangeDefaults` without restoring them, which changes the
user's next manual placement. The PR text must not claim parity. `CreateGridElements` (GridElementCommands.cpp) already takes GDL
`parameters` by name (`ApplyParameter`) and a `layerIndex`.

## Goals

1. `CreateObjects` and `CreateLamps`: when `libraryPartName` names a part other
   than the one the defaults hold, the item starts from that part's own default
   size and GDL parameter list, as the Object Settings dialog does when another
   part is picked.
2. Both commands take `parameters`: GDL parameters set by name at creation.
3. Both commands take `layerIndex`.
4. An upstream Tapir pull request that a maintainer can merge, tested live on
   Archicad 29.
5. The MCP's `create_elements` follows: its "no dimensions" warning reflects
   whether the running Tapir has the fix, and doors and windows sent without
   `sillHeight` get a warning (they take the Door or Window tool's sill; live
   21.10.2026 a door took 0.1 m).

## Non-goals

- Running the part's Parameter Script after placement (open Tapir PR #1350,
  RunParameterScriptOfElements). Where a part's script derives values, the MCP
  can set them with SetGDLParametersOfElements after creation, which runs it.
- Setting the Element ID at creation (open Tapir PR #906).
- `parameters` and `layerIndex` on doors and windows: a possible second PR.
- Array parameters in `parameters` (the grid helper refuses them as well).
- Swapping the library part of an object already placed (open Tapir PR #1394).

## Anti-goals

- A silent change for a caller who relies on the tool's settings for the same
  part: when the defaults already hold the named part (the tool, or a
  `favoriteName` applied first), its parameter list and size are kept.
- A parameter that is quietly not set: see the decision on unknown names below.

## Decisions

His: the order of the three steps; our own PR, not an issue for Tapir's bot;
objects start from the part's own defaults, with GDL parameters and a layer.

Mine (stated in chat on 21.10.2026, he answered "looks right"):

- **Default behaviour, not opt-in.** The current behaviour pairs a part with
  another part's parameter list, which is a bug, and doors and windows already
  work this way. The PR says so plainly, with a before and after from MCP-Test.
- **"Same part" keeps the defaults.** Compare the `libInd` the defaults hold
  with the one `libraryPartName` resolves to; only when they differ are the
  part's own defaults loaded. A `favoriteName` naming that same part therefore
  keeps the favourite's settings.
- **The part's own size.** With the part's defaults loaded, xRatio/yRatio come
  from the part's own A and B in the unit the element's `useXYFixSize` implies
  (metres with it on, 1.0 with it off; see Tapir #718/#719), and A, B, ZZYZX in
  the parameter list agree with them. `dimensions`, when sent, still wins, as
  today.
- **Order of application:** defaults (tool or favourite), then the part's own
  defaults if the part differs, then `dimensions`, then `parameters`, then
  `layerIndex`. A parameter named A or B in `parameters` updates xRatio/yRatio as
  in CreateGridElements.
- **Unknown parameter names fail the item** with an error naming them, and no
  element is created; a wrong value type too. This differs from
  CreateGridElements, which reports warnings, because the CreateObjects response
  has no warnings field and a silently skipped parameter is the kind of fault
  that cost hours before.
- **`layerIndex`:** a positive index, as CreateGridElements; an index that is no
  layer fails the item.
- **The MCP waits for a release.** The fix adds no command, so the MCP cannot
  detect it by command; it gates on the Tapir version that ships it, known only
  once it is released (1.7.3 or 1.8.0, depending on the week). Until then the
  "no dimensions" warning stays, and `create_elements` does not offer
  `parameters` or `layerIndex`: they arrive with the definitions sync from that
  release, since items follow Tapir's schema. Live tests 3 and 4 run as raw Tapir
  calls and the example.
- **Doors and windows stay out of this PR** unless he says otherwise (asked
  21.10.2026): their same-part rule and the tool-default side effect are a
  second PR.
- **Reuse, don't copy:** `ApplyParameter` and the own-size reader come from
  GridElementCommands.cpp (moved to a shared place); ElementCreationCommands.cpp
  already has its own `GetLibraryPartSize`, which should become the one shared
  helper.

## Constraints

- Tapir builds for Archicad 25 to 30 on Windows and macOS; the code must compile
  on all (CI checks every version). Follow the existing `#ifdef ServerMainVers_*`
  patterns, for example around `ACAPI_CreateAttributeIndex`.
- Tapir's contribution rules (CLAUDE.md in the Tapir repo): reuse CommandBase and
  SchemaDefinitions helpers; a Python example under `archicad-addon/Examples/`;
  docs are generated, never hand-edited; the command's registered version stays
  (fields are added to an existing command).
- The PR goes from his fork (`alesdev88/tapir-archicad-automation`) to
  `ENZYME-APD/tapir-archicad-automation`; earlier ones (#718 to #720) were merged
  by tlorantfy within a day.
- Live tests only on MCP-Test (Archicad 29), elements near the origin, deleted
  afterwards. The local build is installed over Tapir 1.7.2 with
  `install-tapir --from-file`, which backs up the release copy; 1.7.2 goes back
  when testing ends.

## Testing

- Live on MCP-Test with the local AC29 build, before and after the change:
  1. set the Object tool to one part (Chair 01) and create another (Bed Double 01)
     without `dimensions`: the bed's A, B, ZZYZX and parameter list are the bed's
     own, not the chair's;
  2. the same with the tool already on Bed Double 01 and a changed parameter:
     the change is kept;
  3. `parameters` sets a numeric, a boolean and a string parameter; an unknown
     name fails the item and creates nothing;
  4. `layerIndex` puts the object on that layer;
  5. a lamp, the same as 1;
  6. `capture_window` shows the bed at its own size.
- A Python example in `archicad-addon/Examples/` that a maintainer can run.
- The MCP's offline tests for the new warnings, and its live suite.

## Left to the builder

The shared helper's file and name; whether `GetParams` returns A and B inside the
parameter list (the DevKit says "others than A and B"; read it in the first build
and handle both); the example's exact content; the error texts; whether CreateLamps
needs anything beyond sharing the object path.
