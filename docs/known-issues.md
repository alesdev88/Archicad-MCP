# Known issues

What is broken, what is unproven, and what has actually been verified against a
live Archicad. Everything here was found on **Archicad 29.0 build 4006** with
**Tapir 1.5.3** unless stated otherwise.

## Reading property values can crash Archicad

**This is the big one.** `GetPropertyValuesOfElements` crashed Archicad 29.0
build 4006 three times during development, with a `ComposeResult` abort that
takes unsaved work down with it.

It is **not** a volume problem. The third crash was a **single property on a
single element**: a user-defined property read on a freshly created slab.
Reading built-in properties (e.g. `ModelView_LayerName`) across thousands of real
elements worked fine. So the trigger appears to be *a specific
property/element combination*, most likely a property that is not applicable to
that element, which the API aborts on instead of returning a per-element error.

This is an Archicad-side fault. The server can trigger it but **cannot prevent
it**. Treat any `audit_delivery_readiness`, `run_rule`, `get_element_data`,
`set_element_data`, or `modify_elements` (its dry run too) against a model you
care about as capable of crashing it, and `move_elements` or `delete_elements`
when an element is refused as locked (see below). **Save first:** ask the
client to run `save_project` (it is confirm-gated, and on Teamwork it saves the
local copy without sending).

`modify_elements` reads one built-in property, `General_Locked` (the element
lock, which no Tapir command reads), on every element it plans that is not
inside a hotlinked module or on a locked layer. `move_elements`,
`delete_elements` and `set_element_data` read it only on the elements
`IsEditable` refused that neither of those explains, to name the lock. Built-in
properties are the read class that has not crashed Archicad, and it read cleanly
on slabs, objects and meshes in MCP-Test on 19.10.2026, but it has not been read
live on texts, labels, dimensions or other 2D elements yet.

### The element ceiling is blast-radius control, not a fix

The server refuses a property fetch spanning more than
`ARCHICAD_MCP_MAX_PROPERTY_ELEMENTS` elements (default `5000`). This limits how
much work a crash can destroy. It does **not** make property reads safe. A
single-element read already crashed once.

Consequences:

- **`get_model_summary`** returns `by_type` only by default, which is cheap and
  safe. Pass `include_layer_story=true` for the per-layer/per-story breakdown;
  that reads a property across every element and is refused on very large models.
- **`audit_delivery_readiness`** scopes its property fetch to the element types
  its rules target (each rule's `applies_to`), so a typed audit (fire ratings on
  walls, say) reads only walls. An audit is refused only when a rule targets
  *all* elements and the model exceeds the ceiling. Scope the rule, or raise the
  env var.
- **`find_elements`** has to read values to evaluate a property comparison,
  because nothing in the official API or Tapir filters by property server-side
  (probed across all 311 commands on 03.09.2026, AC 29/5101, Tapir 1.5.9). It
  narrows by element type server-side, drops elements on story and
  classification before any property read, and pre-checks user-defined
  properties against their classification availability so a pair the
  definition does not cover is never sent to Archicad. That pre-check is aimed
  at the suspected trigger above and predicted availability correctly on 228
  of 228 custom definitions checked live, but it is bounding, not a fix: built-in
  properties have no such check. The result reports `property_reads` so the
  blast radius of a query is visible. See [Finding elements](query.md).

## Element coverage: the official API sees only model elements

The official `API.GetAllElements` returns **model elements only**. Everything 2D
(markers, labels, dimensions, section lines, viewpoints) is missing from it.
Measured on one live project, same instance, same moment:

| Command | Elements returned |
|---|---|
| official `API.GetAllElements` | 16221 |
| Tapir `GetAllElements` | 63122 |

So the official command saw 26% of the project. `query_elements` and
`get_model_summary` used to be built on it and answered `count: 0` for types
that existed in the hundreds. A silent `0` is the worst possible output there:
it reads as "verified absent" and gets acted on.

Enumeration now goes through Tapir (`GetAllElements`, `GetElementsByType`,
`GetSelectedElements`) and falls back to the official command only when the
add-on is missing. Both tools report which one they got:

- `coverage: "whole-plan"` with Tapir.
- `coverage: "model-elements-only"` plus a `coverage_note` without it. In that
  state `element_count` is **not** a project total and a `count` of 0 is not
  proof of absence.

Asking for a single type is now one Tapir request instead of enumerating the
plan and reading back every element's type, so a typed query no longer costs
16k+ property reads.

**The selection has the same gap.** The official `API.GetSelectedElements`
leaves out every selected element whose type the official API does not support:
markers (a CutPlane, say) and, verified live on AC 29/5101 with Tapir 1.5.10
(28.09.2026), a native MEP duct route, for which `API.GetTypesOfElements`
answers 7203 "Element not supported". `get_selection` returned `{"guids": []}`
with the route selected. `get_selection`, `set_selection`, `clear_selection` and
`find_elements(selection_only=true)` now read the selection through Tapir
`GetSelectedElements` whenever the running add-on has that command, and fall
back to the official one otherwise. `get_selection` and `find_elements` report
the same `coverage` field for it, with a `coverage_note` on the fallback. Before
the fix, `clear_selection` left such an element selected and `set_selection`
appended to it instead of replacing it.

**Tapir adds before it removes.** `ChangeSelectionOfElements` runs every add
first and every remove second (Tapir 1.7.1, ElementCommands.cpp,
`ChangeSelectionOfElementsCommand::Execute`). `set_selection` used to send the
whole current selection as the remove list, so an element that was already
selected and was asked for again ended up deselected. It now removes only what
is not in the new list, compared without case, and reads the selection back:
`selected` is the count Archicad really selected, `not_selected` lists the rest
(it answers success for section Objects, railing sub-elements and elevation
marker parts and leaves them unselected), and the result carries the same
`coverage` field as `get_selection`.

**Element types had the same gap, far wider than MEP.** Types were read with
the official `API.GetTypesOfElements` alone. It answers one item per requested
element, in request order, and names model elements only. Measured live on AC
29/5101 with Tapir 1.5.10 (28.09.2026), on the whole plan of a live project:

| Answer | Elements |
|---|---|
| typed | 27225 |
| 7203 "Element not supported" | 35902 |

The 7203 elements exist: every label, line, polyline, dimension and marker,
curtain wall panels and frames, stair and railing parts, beam and column
segments, and native MEP elements (a duct route and all 7 of its segments and
nodes). A GUID that is not in the project answers 7204 "Element not found"
instead. The extractor treated both errors alike, so:

- `get_model_summary` and `find_elements` put those 35902 elements in a `by_type`
  bucket keyed `""`, and `get_element_data` reported their type as `""`.
- `reserve_elements` / `release_elements` derive `not_found` from this read, so
  they called every one of them not found and never attempted it.

Now 7204 alone means not found. The 7203 elements are typed by Tapir
`GetDetailsOfElements` with `fields: ["type"]` (Tapir 1.5.7+), asked only about
those elements, which added 3.2 s on the 63k-element plan (typing the whole plan
through Tapir took 8.3 s). Tapir named all but 475 of them. The rest, native MEP
elements among them, are labelled `"Unknown"`, the same word Tapir uses; so is
every 7203 element when Tapir is missing or refuses the call. Tapir's
`GetElementsByType` refuses `"Unknown"` ("Invalid elementType"), so
`find_elements(element_types=["Unknown"])` matches it against the types read
back over the whole plan (475 elements, 6.3 s live). Verified live after the fix:
no `""` bucket in the summary, and the route counted as known while a made-up
GUID stayed not found.

A Tapir older than 1.5.7 has no `fields`; how it answers was not tried live. If
it refuses the call, those elements stay `"Unknown"` (unit-tested only). Also checked live and rejected as type or existence sources:
Tapir `GetMEPElements` returned an empty list with the route in the plan,
`FilterElements` refuses a call without filters, and `GetMEPRoutingElements`
answers for routes only.

## Classifications were read from the wrong key until 0.4.0

`API.GetClassificationsOfElements` returns each element's item under
`classificationItemId`, and omits that key when the element is unclassified in
the system. The extractor read a key named `classificationId` that never exists
at that level, so **every element looked unclassified**: `classification-required`
rules failed every element they looked at, and `query_elements(classification_system=…)`
returned nothing. Found by the 0.4.0 live probe (AC 29/5101, 03.09.2026), fixed
in the extractor and the recorded fixture. If an old audit reported every wall
as unclassified, that was this.

## Attribute types the official API accepts

`API.GetAttributesByType` on AC 29 build 5101 answers for Layer, Line, Fill,
Composite, Surface, LayerCombination, ZoneCategory, Profile, PenTable and
BuildingMaterial, and refuses MEPSystem and OperationProfile with error 4002
even though the schema lists them. `search_definitions` searches the ten that
work; `list_attributes` still offers its original six.

## Teamwork credentials are stripped from `get_project_info`

On a Teamwork project, Tapir's `GetProjectInfo.projectLocation` is a
`teamwork://user:<JWT refresh token>@host/path` URL. The token is a live
credential, and returning it verbatim put it in the model's context and in the
session transcript. The tool now drops the `user:token` segment and keeps the
host and project path, with a regex backstop that redacts any JWT-shaped string
surviving in another field.

## Teamwork credentials were not stripped from raw API answers

`get_project_info` was not the only way to read that location.
`execute_read_api_command("GetProjectInfo")` returned Tapir's answer verbatim,
token included (seen live on a BIMcloud project on 19.10.2026), and `run_script`
could return, print or raise it. Now the gateway scrubs every answer it gets
back, which also covers `ac.cmd` in scripts, and every tool's reply, errors
included, passes the same scrub on the way out. A URL's `user:token@` segment
is dropped wherever it sits, inside a traceback or printed text as well as in
a field of its own, and any JWT-shaped string becomes `[redacted-token]`. A
script that calls the connection directly still sees the raw answer, but what
it hands back is scrubbed. One side effect: any value holding a URL with a
user name in it, such as a property value, comes back without the user name.

## The gateway tools tolerate `params` sent as text

Some MCP clients collapse a nullable object field (`dict | None`) to an untyped
schema and then send the value as a JSON string, which made every parameterized
command unreachable with `Input should be a valid dictionary`. The server emits
a correct schema, so this is a client-side defect, but `params` is now accepted
as either an object or a JSON-encoded object. The parsed value still goes
through schema validation. A string that is not JSON gets an explicit error and
no command is sent.

## Elements in hotlinked modules cannot be written

Archicad refuses property writes to elements placed through a hotlinked module
with code 6001, `TeamWork permission denied`, even when the project is not a
Teamwork project. With Tapir, `set_element_data` and `run_script` detect these
elements before writing (Tapir `FilterElements` with `IsEditable`) and list
them under `skipped`. Seen live on 23.09.2026: 113 doors in two hotlinked
modules of a local copy of a large project. Edit them in the module's source
file. The reason says "inside a hotlinked module" when the element's details
carry a `hotlinkId` (Tapir 1.5.9 and newer).

## Archicad changes only the active window's database

Archicad acts on elements in the database of the active window and passes over
the rest without an error. Verified live on 28.09.2026 (AC 29/5101, Tapir
1.5.10, a Teamwork project): with a Layout window active, Tapir `DeleteElements`
on 12 reserved floor-plan labels answered success and deleted none of them, and
`delete_elements` reported `{"deleted": 12}`. Tapir `FilterElements` with
`IsEditable` had said 0 of 12 beforehand. With the floor plan active, the same
call deleted all 12.

Since then:

- `delete_elements` and `move_elements` run the same `IsEditable` check as
  `set_element_data` and send only the elements that pass and that the active
  window's database lists (see below). The rest come back
  under `not_deleted` / `not_moved`, grouped by reason, with `active_window`
  (Tapir `GetCurrentWindowType`). A reason from a window other than the floor
  plan names the window first, because from a Layout even a reserved element
  reads as not editable and "not reserved" alone is the wrong lead.
- `deleted` is what a read no longer finds (Tapir `GetDetailsOfElements`),
  never the requested count. `DeleteElements` answered one success for the
  whole batch on Tapir 1.5.10, so its answer alone proved nothing. Tapir 1.7.1
  answers per element, and a survivor carries Archicad's message.
- `moved` counts Tapir's per-element `executionResults`, which `MoveElements`
  does report. Positions are not read back: there is no single position read
  that covers every element type, and the pre-checks remove the cases seen to
  fail silently.
- `set_element_data` already counted per-element results and still does; its
  values are not read back either, because that is the property read that has
  crashed Archicad (see above). `apply_changeset` does read back.
- `deploy_gdl_object` checks the deletion of its preview probe and says so in
  `cleanup_failed` when the element is still there.

`IsEditable` does not say why, so the reason for a refused element is built
from further reads, in this order:

| Reason | Read |
|---|---|
| not found | Tapir `GetDetailsOfElements` with `fields: ["type"]` answers an error entry (a wrong GUID, deleted already, or, from a window other than the floor plan, in another window's database) |
| in another view's database | `IsEditable` passed it, but Tapir `GetAllElements` (the active window's database) does not list it |
| hidden layer | `FilterElements` with `IsVisibleByLayer` |
| not reserved | a Teamwork project, and `FilterElements` with `InMyWorkspace` |
| inside a hotlinked module | `GetDetailsOfElements` answers a `hotlinkId` |
| on a locked layer (named) | the element's `layerIndex` is a layer Tapir `GetLayers` answers `isLocked` for |
| locked with Edit > Locking | the built-in property `General_Locked` is true (a property read, see the crash above), read only when neither of the two above applies |
| locked | whatever is left: none of the three reads above names the lock, or they could not be made (a Tapir older than 1.5.9, no `GetLayers`) |

The lock reads are made only for the elements refused as locked, so a call
where nothing is locked reads no layers and no properties.

From the floor plan, the reads reach every database. Live on 19.10.2026, with
the floor plan active, `IsEditable` passed a Text in a layout database and a
Line in a section database as editable.

**Write test (MCP-Test, AC 29/5101, Tapir 1.7.1, 19.10.2026, floor plan
active)** with probe Texts the test created on the Sheet Index layout, in
Building Section and on the floor plan, and deleted again afterwards:

| Sent from the floor plan | Floor-plan Text | Layout and section Texts |
|---|---|---|
| `FilterElements` `IsEditable` | passed | passed |
| `GetDetailsOfElements` | found | found, with their real positions |
| Tapir `GetAllElements` (no `databases`) | listed | not listed |
| `MoveElements` by 1 cm | success, moved | **success, not moved** (read in their own windows) |
| `DeleteElements` | success, gone | "Failed to delete the element." per element, kept |
| `SetPropertyValuesOfElements` `General_ElementID` | 7203 "Element not supported" | 7203 "Element not supported" |

So `move_elements` counted the passed-over elements as moved. A delete of only
the layout Text, and a batch of a floor-plan Text with a layout Text, gave the
same answer: the floor-plan one deleted, the layout one kept. The same holds
read-only for what was already in MCP-Test: `GetDetailsOfElements` found all
8 layout Drawings and all 6 section-only Objects, and `GetAllElements` listed
none of them. No `FilterElements` filter separates them: `IsVisibleIn3D` drops
them, but it drops every floor-plan 2D element too.

All three tools therefore refuse the elements `IsEditable` passed that Tapir
`GetAllElements` of the active database does not list, as "in another view's
database", with the advice to open that view. GUIDs are compared without
case, and an empty or failed listing refuses nothing. Not known: whether a
property write from the floor plan lands on another database's element of a
type the official API supports. Texts are not supported at all, so the test
could not say. `modify_elements` makes the same check before planning.

The official `GetTypesOfElements` cannot answer "does it exist": live on
28.09.2026 it answered 7203 "Element not supported" for every label, existing
ones included.

**Run live (28.09.2026, read-only, floor plan active)** on the 12 GUIDs of the
report. By then 10 had been deleted from the floor plan and 2 kept and released
from the workspace. The 10 came back "not found" and the 2 "not reserved in
Teamwork", which is their actual state. `GetDetailsOfElements` with
`fields: ["type"]` answered `{"type": "Label"}` for a kept one and an error
entry for a deleted one. Not yet run live: the same check from a Layout. A
delete that goes through ran in the write test above.

## Writing enum properties is not supported

`singleEnum` and `multiEnum` properties need an `EnumValueId`, not a plain value.
`set_element_data` reports them as `skipped` with a reason rather than silently
failing. To set one, use `execute_write_api_command` with the enum's id.

## Editing definitions needs the new Tapir commands

`edit_property_definitions`, `edit_classifications` and `import_definitions`
need a Tapir add-on that has `UpdateClassificationItems`. Tapir 1.5.4 to 1.5.9
upstream accept `UpdatePropertyDefinitions`, but only for expressions and adding
enum values, and silently ignore the other fields. So the tools check for the new
command first and send nothing without it.

Verified live on AC 29 (2026-09-25, the MCP-Test project):

- **Removing an enum option** leaves every element that held it at
  `<Undefined>`. It does not fall back to the property's default. The tools
  cannot say how many elements are affected, because counting needs property
  value reads (see the crash above), so the dry run warns instead.
- **Importing with `replace`** keeps the property's GUID and updates the
  definition in place: description changed, availability kept, nothing created
  or removed. Element values survive it. The same has not been checked for
  classification systems, so `merge` is the safer policy there.
- **A Property Manager export holds both** the classification systems and the
  property groups, in one `BuildingInformation` file. Availability in it names
  classifications by system name, system version and item code, not by GUID.
- **Teamwork: not yet verified.** Archicad answers `APIERR_NOACCESSRIGHT` per
  item when the user lacks the right, and the tools pass that on in plain words.
  Which Teamwork right that is has not been checked on a real project.

## Tapir version matters

IFC commands (`GetIFCPropertiesOfElements`) only exist in newer Tapir releases.
**1.4.0 does not have them** and IFC rules skip. Verified working on **1.5.3**.

The server probes availability per command, so an older add-on degrades to
`skipped` instead of erroring mid-fetch. Note that `project_name` and
`tapir_version` in `list_instances` come from Tapir too. Without the add-on they
are `null` even though the instance is otherwise fine.

### Tapir 1.7.2 (live 21.10.2026, MCP-Test on Archicad 29 build 5101, MCP-Test_30 on Archicad 30)

- **Version labels.** Nine commands new in the 1.7.2 definitions are labelled
  1.7.1 (`SaveCurrentWindowAsImage`, `Get3DProjectionSettings`,
  `Set3DProjectionSettings` and six design option commands), but the 1.7.1
  release has none of them. `CORRECTED_VERSIONS` in `tapir_requirements.py`
  gates them on 1.7.2.
- **Electrical commands on Archicad 29.** `CreateElectricalPanels` and the other
  electrical commands are registered (`IsAddOnCommandAvailable` says true) but
  answer "This command requires Archicad 30 or newer." On Archicad 30
  (MCP-Test_30, build 3001) a panel, a circuit on it and a device on the
  circuit were created and deleted.
- **A circuit is not a placed element.** `GetAllElements` does not list it, so
  `delete_elements` refused the test circuit as "in another view's database",
  which is wrong. Deleting its panel removed it.
- **Doors take the Door tool's settings.** A door created by `libraryPartName`
  without `sillHeight` came in with the Door tool's 0.1 m sill. Send every size
  that matters, as with objects.
- **Referenced elements are checked as if they were changed.** A level
  dimension's, a label's or a dimension's parent element is skipped when
  Archicad would refuse to change it (not reserved in Teamwork, in a hotlinked
  module, on a locked or hidden layer), although the new element only refers to
  it. Whether Archicad accepts such a parent is unverified; a hotlinked site mesh
  is the case to try.
- **A part's insertion point is its own.** `Bed Double 01` at angle 0 is placed
  by its foot-left corner, with the pillows towards +Y. "Placed against the
  wall" by its insertion point, the bed ran through the wall; `capture_window`
  showed it, and a move of the bed's length fixed it.
- **A door or window placed by part name has GDL parameters that can be neither
  read nor written, and its tool passes the fault on** (live 22 and 23.10.2026,
  Archicad 29, MCP-Test). For `libraryPartName`, Tapir 1.7.2's `CreateDoors` and
  `CreateWindows` build the Door or Window tool's parameter list from the bare part
  (`ACAPI_LibraryPart_GetParams`) and create the opening from it; Archicad will not
  open that list, so `GetGDLParametersOfElements` answers "Failed to get parameters
  of element with guid ...!" and `SetGDLParametersOfElements` "Failed to change
  parameters of element with guid ...". The tool keeps the list, so every later
  opening from it, hand-placed ones included, has the same fault. A Double Window
  with identical details placed through the API from a favourite read all 714
  parameters and took writes; plain "Door" and "Window" placed from the favourites
  "Generic Door" and "Generic Window" read 811 and 658. One placement from a
  favourite, or the part picked again in the tool's settings, puts the tool right;
  openings already placed keep the fault. `create_elements` warns about doors and
  windows sent by `libraryPartName`, and a learned door or window shows no
  parameters, so a placement's `leaves` end in `placed_not_set`.
- **"Double Window" draws at least 1.5 m wide** (live 22.10.2026). Sent 1.0, 1.2,
  1.5 and 2.0 m wide, its plan boxes were 1.5, 1.5, 1.5 and 2.0 m, while
  `GetDetailsOfElements` kept the width sent. A sketch's 1.2 m double window comes
  out 1.5 m with this part; learning does not measure opening widths, so only the
  look after placing shows it. "Window" at 1.0 m drew 1.0 m.
- **A part's GDL A and B may not be its drawn size** (live 22.10.2026). Chair 01's
  parameter script forces A = B, B winning: sent 2.0 x 1.5 it reads back A 1.5,
  B 1.5 and draws 2.0 x 2.0, its plan width following the A sent and its depth
  max(A, B) sent. Bed Double 01 clamps B to at least 1.2, and there the read-back
  is the drawn size. Learning fits each axis of a footprint against whichever of
  the read-back and the sent size its plan box follows, and keeps no footprint
  when an axis follows neither, so Chair 01 is placed by coordinates, not by
  `placement`.
- Verified on 1.7.2: doors and windows by `libraryPartName`, door and window
  width and sill changes through `modify_elements`, a Swept Beam grown from 3 to
  5 nodes (its arrays read back at 5), a Road Marking, a curtain wall, a grid
  element, a level dimension, `capture_window`, and the live suite (16 passed);
  on Archicad 30 also doors and windows by part name and `capture_window`.

## Attribute writes: what Tapir's create-or-overwrite commands do

Read in Tapir 1.7.1's `AttributeCommands.cpp` (2026-10-19), and the reason
`edit_attributes` plans the way it does:

- **Create or overwrite.** Without `overwriteExisting` an existing name answers
  "Already exists." per item; with it, the given GUID (or else the name) is
  overwritten, and a name that matches nothing is created. `edit_attributes`
  sends creates with it off, so a create can never replace an attribute.
- **What an overwrite drops.** Fields not sent keep their value, except: a
  composite's `useWith` is cleared and its skins and separators rebuilt from the
  request; a pen table or profile overwrite with `sourceAttributeId` replaces all
  pens or the geometry. A building material's `id`, `manufacturer` and
  `description` reach Archicad as null when not sent; AC 29 and AC 30 keep them
  (live 19.10.2026), and a `modify` resends them anyway. A `modify` refuses the
  source-copy fields.
- **Silent defaults.** A misspelt `lineType` makes the line Solid, a misspelt
  `subType` makes the fill Vector, an unknown `cutFillOrientation` becomes
  ProjectOrigin, an unknown building material or line type in a composite
  becomes index 0. All checked before sending.
- **Texture flags can only be switched on.** Turning one off is refused.
- **Archicad's own attributes** (index 1 of layers, lines and fills; the Solid
  and Empty fills; the last composite) refuse changes and deletes.
- **Deleting a layer deletes its elements**, in every view (DevKit). The tool
  deletes a layer only with the floor plan active and no floor-plan element on
  it, and says that other views are not counted.
- **Nothing is undoable** except `SetPropertyValuesOfAttributes`.
- **Property values of attributes.** Reading them did not crash Archicad 29 or
  30 (live 19.10.2026, a canary and a stock building material, available and
  unavailable properties): an unavailable property answers "Not available or
  not evaluated property" per item, which the dry run turns into a skip. Values
  read as display strings ("113.000" for a Real), so only String properties are
  compared. A write that answers `{}` (neither success nor error) is counted as
  not written. Writing was not tried live: a canary material has no available
  property until it is classified.
- **Seen live (19.10.2026, AC 29 and AC 30):** a Dashed line's `period` follows
  its dash items (period 3 read back as 2, their sum), so a period-only change
  is refused; new building materials start with cut surface and fill index 0
  (no attribute) and new layers with intersection group 0, so a create without
  them warns.

## Teamwork reservation: what Tapir reports and what is derived

Tapir 1.5.9's `ReserveElements` (present since 1.1.4, wrapping
`ACAPI_Teamwork_ReserveElements` with dialogs off) answers with an
`executionResult` and a `conflicts` list: the elements it could not reserve,
each with the holding user's id and name. Nothing else. `ReleaseElements`
answers with an `executionResult` only. So of the four outcomes
`reserve_elements` reports, one comes from Archicad and three are derived:

| Outcome | Source |
|---|---|
| `reserved_by_others` | Tapir `conflicts` |
| `not_found` | GUIDs the official `GetTypesOfElements` does not know |
| `already_mine` | Tapir `FilterElements` with `InMyWorkspace`, before the attempt |
| `indirectly_reserved` | `InMyWorkspace` over the whole plan before and after the attempt, minus what was asked for |

There is **no read that says who holds an element** without trying to reserve
it. `ACAPI_Teamwork_GetLockableStatus` covers lockable object sets (attributes),
not elements. That is why the dry run stops at not-found and already-mine, and
why the tool is confirm-gated instead of dry-run-by-default in the usual sense.

**Verified live (03.09.2026, AC 29/5101, Tapir 1.5.9)** on a BIMcloud Teamwork
project with 62,997 elements, with the owner's go-ahead, on one selected door:

| Step | Result |
|---|---|
| dry run | the door listed under `would_attempt`, nothing sent to Archicad |
| `reserve_elements(confirm=true)` | the door reserved; `indirectly_reserved` held its **wall and the wall's other door**, neither asked for. 8.6 s, most of it the two whole-plan `InMyWorkspace` sweeps |
| `release_elements(confirm=true)` on all three | all three released, `still_mine` empty. 1.7 s |

So Archicad does reserve linked elements on the side, in both directions
(door to wall, wall to its other door), and the before-and-after sweep is what
catches it. Tapir reports none of that; the DevKit documentation is silent on
it. Not seen live yet: a `reserved_by_others` conflict, because no teammate
held the door at the time. Its shape is the one in Tapir's source.

### Non-element object sets (Tapir 1.7.1)

`reserve_elements` and `release_elements` take `object_sets`, which go through
Tapir 1.7.1's `ReserveLockableObjectSet`, `ReleaseLockableObjectSet` and
`GetLockableObjectSetStatus` (`ACAPI_Teamwork_ReserveLockable`,
`ReleaseLockable`, `GetLockableStatus`, dialogs off). Unlike elements, a set's
holder can be read without attempting the reservation, so the dry run is
complete. A commit reads the status back rather than trusting the answer.

- **Solo projects answer an error for every name.** Live on MCP-Test
  (19.10.2026): "No lockable object set named 'LayerSettingsDialog' was
  found", for documented names too. `FindLockableObjectSet` finds nothing
  outside Teamwork, so the tools ask only when `isTeamwork` is true, and the
  names cannot be checked in a solo project.
- **Layers and layer combinations are one set**, `LayerSettingsDialog`. The
  DevKit says a member of a compound set must not be reserved alone, so
  `Layers` and `LayerCombinations` both map to it.
- **Releasing sends.** The DevKit: the set's modifications are sent to the
  server right before it is unlocked.
- **Properties and classifications: an undocumented set.** No documented set
  covers them. Archicad 29's and 30's `APICore` have
  `ClassificationsAndProperties` in the same string table as the documented
  names, so `Properties` and `Classifications` map to it. On CVP (Teamwork,
  19.10.2026, status reads only) it answered `Free` like every documented name,
  while an unknown name errored. The definition tools and `edit_attributes`
  skip an edit whose set is free or held by someone else, with the reason and
  the reserve call; a status that cannot be read (server offline, an old
  Tapir) only warns.
- **Not tried on a Teamwork project yet:** reserving, releasing, the
  `Editable` and `Locked` answers, and Archicad's refusal of an edit without
  the reservation. Unit-tested against the shapes in Tapir's source.

## Creating elements: what Tapir's schemas leave out

Found drawing a whole basement into a test project on 23.10.2026 (Archicad 29
build 5101, Tapir 1.7.2). `create_elements` now replies these as `notes` with
the item schema (`items=[]`) and warns where it can check them.

- **Heights.** A column's `coordinates.z`, a slab's `level`, and a beam's and a
  stair's `zCoordinate` are absolute elevations even when `floorIndex` is given;
  a wall's `zCoordinate` is the offset above that story. A column sent with z 0
  on a story at -2.80 stood from 0.00 to 2.60. The dry run warns when such a
  height lies off the story it names.
- **Sides.** With `referenceLineLocation` Outside a wall's body lies to the left
  of begin to end, so an outer outline drawn clockwise ends up outside the
  building. A door with `oSide` false swings to the right of its wall's
  direction; flipping `oSide` also moves the hinge to the other jamb, and
  `reflected` moves it back. A window with `oSide` false has its outside on the
  right.
- **Stairs.** `baseLinePoints` is the stair's left edge and the flight lies
  `flightWidth` to its right. A U given as its centre line overlaps itself and
  Archicad answers only "Failed to create new Stair"; the dry run now warns.
  CreateStairs has no turn or winder setting, so a tight U (16 risers in
  2.2 x 2.0 m) ran its top flight 1.65 m past the baseline and through the slab
  above; this cannot be fixed here, and the create reports such a stair. Shape
  it in Archicad's stair editing.
- **Element IDs.** Doors and windows placed from a favourite took the
  favourite's ID, and zones the tool's: 14 doors were all `DOO - 004`. The
  create warns and points to `set_element_data` (`General_ElementID`).
- **Zone stamps.** Zones from CreateZones showed no stamp, in the app or a
  saved picture, even after UpdateZones. Not a fault: the template's Model View
  Options hid zone stamps (that office labels zones with the Label tool), and
  switched on, every stamp was there.
- **Counts.** Tapir's GetAllElements lists a stair's own GDL parts (its grid,
  break marks, ...), which the official API types Object; `get_model_summary`
  counts them apart as `owned_parts`.

## `publish` is unvalidated

The test model had no publisher sets, so `publish` has never run end to end.
Everything else in the tool list has, as listed below.

## Connection

The server scans ports **19723-19743** and picks the instance with a project
open. Behaviour worth knowing:

| Situation | What happens |
|---|---|
| Nothing on any port | `No running Archicad found. Start Archicad 29 and open a project.` |
| Archicad running, no project open | Reported by `list_instances` with `project_open: false` and version/build `0`. Archicad refuses even `API.GetProductInfo` (error `4001`) until a project is open, and tools then fail with a message naming the two possible causes below. |
| Archicad running, project open, but a modal dialog open (e.g. Object Settings) | The dialog blocks the whole API with the same error `4001` as a missing project, so this looks identical to the row above and the two cannot be told apart. Tools fail with a message naming both causes; close the dialog and retry. |
| Several instances, projects open | Discovery refuses to guess. Pass `port` to the tool; `list_instances` shows the options. |
| Archicad quits or crashes mid-session | Tools report that Archicad is not responding on that port and may have been closed or crashed. |

Tapir absence is concluded **only** from a command-level failure, never from a
transport failure. Otherwise a crashed Archicad would report as "Tapir not
installed" and send you off reinstalling a perfectly good add-on.

## Built-in property names (verified)

Confirmed against a live Archicad 29.0 model on 2026-07-16:

- The layer name is **`ModelView_LayerName`**. There is no `General_LayerName`.
- Zone number and name are **`Zone_ZoneNumber`** and **`Zone_ZoneName`**.
- An element's **story is not a property**. It comes from Tapir
  `GetDetailsOfElements.floorIndex`, the **story index** (ground floor 0,
  basements negative), which is why the
  `story` address in `find_elements` and the `get_model_summary` breakdowns key
  on floorIndex.

## Validated end to end

Live-run against Archicad 29.0/4006:

- Instance discovery, `get_model_summary`, `query_elements` (type + story filters;
  replaced by `find_elements` in 0.4.0, whose live canary is in `tests/test_live.py`)
- `get_element_data`, `set_element_data` (dry-run, commit, read-back)
- `get_selection`, `set_selection` (replaces rather than appends), `clear_selection`
- `create_elements`, then `move_elements`, then `delete_elements`, including
  their dry-run and `confirm=true` guards
- `audit_delivery_readiness`, `run_rule`, `highlight_failures`,
  `create_issues_from_failures`, `verify_ifc_export_readiness`, `list_issues`,
  `create_issue`
- The tier-3 gateway (309 commands, writes included)

The 0.2.0 `win32` bundle, cross-built on macOS, was installed on Windows and
runs: the extension starts, the tools appear, and it reaches Archicad. That is
the check the build machine cannot perform for itself.

The live runs above predate the 0.2.0 tool split, which renamed these tools
without changing what they send to Archicad: `manage_selection` became
`get_selection` / `set_selection` / `clear_selection`, `manage_issues` became
`list_issues` and five single-purpose write tools, and `execute_api_command`
became `execute_read_api_command` and `execute_write_api_command`. The offline
suite covers the new surface; the live re-run is still owed.

Live-run against Archicad 29.0/5101 with Tapir 1.5.9 on 03.09.2026, on a
four-element test model carrying a classification system and a
classification-scoped custom property:

- `search_definitions`: every property address it returned resolved through
  `GetPropertyIds`; attribute search covered all ten working types.
- `find_elements`: a layer comparison agreed with a direct property read; `and`
  of a value with its own negation was empty and the `or` was everything;
  story and classification comparisons made zero property reads; the
  availability pre-check answered the two uncovered elements without a read,
  and the covered ones split cleanly into one `equal`, one `is_user_undefined`,
  with `has_no_value` counting all three non-values. Branch tests ran against
  the live tree.

Not validated: `publish`.

The Tapir-backed enumeration was re-verified live on AC 29/5101 with Tapir
1.5.10 (28.09.2026): Tapir `GetAllElements` listed 63127 elements against 16469
from the official command, and the whole plan was typed through the path above.
Not yet re-verified live: the `projectLocation` scrub. It is covered by unit
tests against recorded API shapes, not by a live run.

## Running the live canary

Read-only checks against a running instance. Use a **small, non-sensitive**
model, never a client or teamwork project, and re-read the crash warning above:

```bash
ARCHICAD_MCP_LIVE_PORT=<port> uv run pytest -m live -v
```

Pinning the port is deliberate: it stops the suite finding and touching whatever
model happens to be open.

The definition-editing canaries write (and restore) definitions, so they also
check the project name and skip unless it is `MCP-Test`: a blank project from the
default template with an "MCP Test" classification system (Building > Wall, Slab,
Object; Site) and an "MCP Test/Fire Rating" property available for Wall and
Object. Ports are handed out per launch, so a port alone does not identify a
test model.

## Schedules

Schedules have no programmatic interface. No command in the official JSON API
or Tapir reads or writes a schedule, and Graphisoft's developer forum states
the C++ API does not reach them either. The only supported route is the Scheme
Settings Import and Export XML, which is what the `*_schedule_scheme` tools
operate on. This means every schedule edit needs two manual clicks in Archicad,
before and after.

Whether re-importing an edited scheme updates it in place or creates a numbered
duplicate is **not yet confirmed**. Graphisoft's documentation says duplicate
names are auto-numbered, but exported schemes carry stable IDs that suggest an
in-place match may be possible. Test on a scratch project before relying on
either behaviour.

## Scripts keep the property-read ceiling

`ac.props` and `ac.details` in `run_script` refuse to read across more than
`ARCHICAD_MCP_MAX_PROPERTY_ELEMENTS` elements (default 5000) in one call, like
every other property read. Read in scoped chunks.

`ac.set_props` hits the same ceiling: planning reads each element's current
value, so one call that changes more than 5000 elements is refused. Split the
changes into several `set_props` calls; they still end up in one changeset.
