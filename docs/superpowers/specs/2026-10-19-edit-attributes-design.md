# edit_attributes and Teamwork object sets: Design

**Date:** 2026-10-19
**Status:** Built on branch `feature/edit-attributes`; unit-tested; live-checked on MCP-Test (AC 29) and MCP-Test_30 (AC 30) with canary attributes; Teamwork not verified live. Items 7 and the Teamwork half of item 10 of the 2026-10-19 Tapir command triage (`docs/superpowers/specs/2026-10-19-tapir-command-triage.md`, untracked in the main checkout).
**Repo:** https://github.com/alesdev88/Archicad-MCP.git

## Purpose

Two gaps, one design, because the second guards the first.

1. **Attributes cannot be edited safely.** Every Tapir `Create*` attribute
   command (layers, layer combinations, lines, fills, zone categories, pen
   tables, profiles, building materials, composites, surfaces) is "create or
   overwrite": with `overwriteExisting: true` a call naming an existing layer
   replaces it, and Tapir answers success. The gateway passes that straight
   through. `edit_attributes` gives the same commands a dry run that names every
   attribute that already exists and what would change (current -> new), and
   never overwrites unless the change says so.
2. **Teamwork projects block those edits, and the property and classification
   editors, without saying why.** In a Teamwork project an attribute type, and
   the property and classification definitions, must be reserved before they
   can be changed. Tapir 1.7.1 added `ReserveLockableObjectSet`,
   `ReleaseLockableObjectSet` and `GetLockableObjectSetStatus` for exactly that.
   `reserve_elements` / `release_elements` gain `object_sets`, and every
   definition and attribute tool names the set to reserve when it is not.

Typical asks it must answer in one dry run and one commit:

- "Create these 12 office layers." (some already exist, with a trailing space)
- "Rename layer X to Y and lock it." "Make composite Z's core 0.20 m."
- "Delete the fills we don't use any more." "Delete layer Temp."
- "Set the Fire rating property of these building materials."
- On CVP (Teamwork): "Rename property Office/Status" when the property and
  classification set is not reserved.

Success means: no existing attribute is changed unless the change addresses it
explicitly; the dry run shows current -> new for every one that would change;
a commit reads the attributes back and reports what Archicad actually did; and
in a Teamwork project the dry run says which set must be reserved and by which
call, before anything is sent.

## What exists and is reused

- `core/attributes.py`: `list_attributes` (official API, six types),
  `tapir_attributes` (index, name, guid per type), `layer_table`.
- `core/definitions.py`: the ten attribute types the official
  `GetAttributesByType` answers on AC 29, each with its official detail command.
- `core/definition_edit.py`: `PlannedEdit`, `group_by_error`, `split_results`,
  the `planned` / `skipped` / `applied` / `failed` result vocabulary.
- `core/modify.py` / `modify_fields.py`: attribute fields given by name
  (`ATTRIBUTE_FIELDS`, `INDEX_ATTRIBUTE_FIELDS`) and shown by name in diffs.
- `core/create.py`: item validation against Tapir's bundled schema with
  `jsonschema`, problems listed by item index and path.
- `core/teamwork.py`: `reserve_elements` / `release_elements`, `_is_teamwork`,
  the confirm gate.
- `tapir_requirements.py`: `TOOL_COMMANDS` and `require_tapir_command` for
  commands only some arguments use.

## Live facts (MCP-Test, port 19723, AC 29/5101, Tapir 1.7.1, 2026-10-19, reads only)

- MCP-Test, MCP-Test_30 and MCP-Test_28 are open, all three with Tapir 1.7.1.
- `GetLockableObjectSetStatus` in a solo project answers an **error** for every
  name, valid or not: "No lockable object set named 'LayerSettingsDialog' was
  found. Check the name and that the project is a Teamwork project."
  `ACAPI_Teamwork_FindLockableObjectSet` returns a null GUID outside Teamwork.
  So the set names cannot be checked in a solo project, and the tools must not
  call these commands unless `GetProjectInfo.isTeamwork` is true.
- Layer names carry trailing spaces in the stock template
  (`"Interior - Furniture "`, `"Dimensioning - Site "`), and layer index 1 (the
  Archicad Layer) is named `"\u0014"`.
- `GetLayers`, `GetComposites`, `GetBuildingMaterials` take
  `{"attributeIds": [{"attributeId": {"guid"}}]}` and answer a list in the same
  order, an `{"error"}` entry for an unknown GUID. `GetLayerCombinations` takes
  `{"attributes": [...]}` instead and wraps each answer in
  `{"layerCombination": {..., "attributeIndex"}}`.
- A composite reads as `useWith`, `skins` (`type`, `framePen`, `thickness` in
  metres, `buildingMaterialId`) and `separators` (`linePen`, `lineTypeId`). A
  building material reads its fill and surface by **index** (`cutFillIndex`,
  `cutSurfaceIndex`), not by GUID.

## The Teamwork lockable object sets

### What Archicad and Tapir provide

Tapir 1.7.1 (`TeamworkCommands.cpp`, unchanged on main since the tag apart from
a schema refactor) wraps three DevKit calls, each with dialogs off:

| Command | DevKit call | Answer |
|---|---|---|
| `ReserveLockableObjectSet {objectSetName}` | `ACAPI_Teamwork_ReserveLockable` | `executionResult`, and `conflicts` (`userId`, `userName`) when another user holds it |
| `ReleaseLockableObjectSet {objectSetName}` | `ACAPI_Teamwork_ReleaseLockable` | `executionResult` |
| `GetLockableObjectSetStatus {objectSetName}` | `ACAPI_Teamwork_GetLockableStatus` | `status`: `Free`, `Editable` (mine), `Locked` (another user, see `conflicts`), `NotAvailable` (server offline), `NotExist`; an unknown name is an error |

Two DevKit facts shape the tool:

- **Compound sets.** Layers and layer combinations are one set,
  `LayerSettingsDialog`; reserving a member of a compound set alone "may result
  insecure functionality ... should be avoided" (ACAPinc.h, FindLockableObjectSet).
  Archicad's own string table also has `LayerCombinations` as a separate name;
  the tool never sends it.
- **Releasing sends.** "The modifications to the object set are sent to the
  server project right before the server actually unlocks the objects"
  (ACAPinc.h, ReleaseLockable). Releasing a set is therefore also a Send of that
  set's changes, and the tool says so.

Unlike elements, a set's holder **can** be read without attempting the
reservation, so the dry run of `reserve_elements` for sets is complete.

### The set names

Documented in the DevKit (`ACAPI_Teamwork_FindLockableObjectSet`):
`BuildingMaterials`, `Cities`, `Composites`, `Favorites`, `FillTypes`,
`LayerSettingsDialog`, `LineTypes`, `MarkupStyles`, `MEPSystems`,
`ModelViewOptions`, `OperationProfiles`, `PenTables`, `Profiles`, `ProjectInfo`,
`GeoLocation`, `PreferencesDialog`, `GraphicOverrides`, `Surfaces`,
`ZoneCategories`.

**Not documented, found in Archicad 29's `APICore` string table next to the
documented ones:** `ClassificationsAndProperties`, `ProjectLibraryList`,
`IssueTags`, `LayerCombinations`. Property definitions and classifications have
no documented set, and `ClassificationsAndProperties` is the only candidate. It
is unverified until a Teamwork project answers for it (see Verification).

The tool accepts these names, Archicad's exact spelling, plus aliases so an
agent does not need to know the API names:

| Given (case-insensitive) | Sent |
|---|---|
| `Layer`, `Layers`, `LayerCombination`, `LayerCombinations`, `LayerSettingsDialog` | `LayerSettingsDialog` |
| `Line`, `Lines`, `LineType`, `LineTypes` | `LineTypes` |
| `Fill`, `Fills`, `FillType`, `FillTypes` | `FillTypes` |
| `Composite`, `Composites` | `Composites` |
| `PenTable`, `PenTables` | `PenTables` |
| `Profile`, `Profiles` | `Profiles` |
| `BuildingMaterial`, `BuildingMaterials` | `BuildingMaterials` |
| `Surface`, `Surfaces` | `Surfaces` |
| `ZoneCategory`, `ZoneCategories` | `ZoneCategories` |
| `Properties`, `PropertyDefinitions`, `Classifications`, `ClassificationsAndProperties` | `ClassificationsAndProperties` |
| any other name | sent as given; Tapir's "no lockable object set named" error is reported for that set |

### `reserve_elements` / `release_elements` gain `object_sets`

Extended rather than a sibling tool: the triage put these commands there, the
confirm gate and the "not a Teamwork project" refusal are the same, and one
reservation tool is easier for an agent to find than two.

```
reserve_elements(guids: list[str] | None = None, object_sets: list[str] | None = None,
                 confirm: bool = False, port)
release_elements(guids: list[str] | None = None, object_sets: list[str] | None = None,
                 confirm: bool = False, port)
```

At least one of the two lists is required. Element handling is unchanged, and a
call with only `guids` returns exactly today's result. With `object_sets` the
result gains an `object_sets` list, one entry per set in the order given
(duplicates and aliases of one set collapse into one entry):

```
{"set": "LayerSettingsDialog", "asked": ["Layers"], "status": "Free",
 "held_by": [], "outcome": ...}
```

| Status before | Reserve dry run | Reserve confirm | Release dry run | Release confirm |
|---|---|---|---|---|
| `Free` | `would_reserve` | attempt; `reserved` if the status afterwards is `Editable` | `not_mine` | nothing sent |
| `Editable` | `already_mine` | nothing sent | `would_release` | attempt; `released` if the status afterwards is not `Editable` |
| `Locked` | `held_by_others`, with names | nothing sent (Archicad would refuse) | `not_mine` | nothing sent |
| `NotAvailable` | `server_unavailable` | nothing sent | `server_unavailable` | nothing sent |
| error (unknown name) | `error` with Tapir's message | nothing sent | `error` | nothing sent |

A reservation attempt that fails reports Tapir's `conflicts` as `held_by` and
the message. The outcome after an attempt is read back from
`GetLockableObjectSetStatus`, not taken from `executionResult`, the same
"what Archicad did, not what it answered" rule the element half follows.
The release dry run notes that releasing a set sends its changes to the server.

The `object_sets` half needs `ReserveLockableObjectSet` (Tapir 1.7.1), checked
with `require_tapir_command` only when `object_sets` is given; `TOOL_COMMANDS`
for both tools stays `ReserveElements` / `ReleaseElements`.

### Which writes need which set: the shared check

`core/teamwork.py` gains the functions the write tools call before sending:
`object_set_states(conn, names)` (None outside Teamwork, after one
`GetProjectInfo`; otherwise each set's `SetState`: status, holders, or why it
could not be read), `set_problem(state)` (None when the set is mine, else the
text and whether it blocks) and `teamwork_entries(states, needed_for)` (the
result's `teamwork` list). The definition tools reach them through
`definition_edit.definitions_reservation`. Each set's status is read once per
call and classified:

| Status | Documented set (attribute types) | Undocumented mapping (`ClassificationsAndProperties`, property values of attributes) |
|---|---|---|
| `Editable` | proceed | proceed |
| `Free` | the targets are skipped: "the Composites set is not reserved; reserve it first with reserve_elements(object_sets=["Composites"], confirm=true)" | warning with the same call; the commit still sends, and a failure group carries the hint |
| `Locked` | skipped: "the Composites set is reserved by <user>; they must release it first" | warning naming the holder |
| `NotAvailable` | warning "Teamwork server unavailable, reservation unknown"; sent | same |
| error, or Tapir older than 1.7.1 | warning "could not check the reservation of X: <message>"; sent | same |

Why two columns: blocking on a documented set saves a batch of cryptic
per-item refusals; blocking on a guessed set name could stop a write that would
have worked. Once a Teamwork project confirms `ClassificationsAndProperties`,
the right column can move to the left one.

Every dry run in a Teamwork project carries a `teamwork` list:
`[{"set", "status", "held_by", "needed_for": [targets]}]`.

Wired into:

| Tool | Set |
|---|---|
| `edit_property_definitions`, `edit_classifications`, `import_definitions` | `ClassificationsAndProperties` (warning column) |
| `edit_attributes` create, modify (fields), delete | the attribute type's set (blocking column) |
| `edit_attributes` layer modifies that only show, hide, lock, unlock or wireframe a layer | `LayerSettingsDialog` (warning column: Archicad's interface lets Teamwork users do that without reserving, and reserving the set blocks every teammate's layer edits; whether the API agrees is unverified) |
| `edit_attributes` property values | the attribute type's set (warning column; which set Archicad wants is unverified) |

For the three definition tools the check is the only change: their planning,
result shapes and gates stay as they are, plus the `teamwork` key in a
Teamwork project and the hint on failures.

## What the Tapir source says about the attribute commands

Read 2026-10-19 from Tapir's `AttributeCommands.cpp` and `PropertyCommands.cpp`
(byte-identical to tag 1.7.1), the AC 29 DevKit headers and Tapir's own live
test scripts. The full write-up with line numbers is
`side-session-notes/.../tapir-attribute-semantics.md` (outside the repo); what
shapes this design:

**Matching.** With `overwriteExisting: false` an existing attribute is found by
**name only**, and an existing name answers per item `"Already exists."`
(APIERR_ATTREXIST, -2130312915; composites answer -7003 "Composite already
exists."). With `true` a given `attributeId` GUID wins over the name; without
one it is an upsert by name; when nothing matches it **creates**. Sending A's
GUID with B's name renames A onto B's name with no check in Tapir. `index` is
unusable (schema says string, code reads Int32). Items are independent and the
answers line up with the input, but one item failing schema validation rejects
the whole request, and nothing is transactional or undoable (only
`SetPropertyValuesOfAttributes` runs as one undo step).

**What an overwrite keeps.** The existing attribute is read first and only the
fields sent are changed, so for layers, lines, fills, zone categories, pen
tables and surfaces an omitted field keeps its value (Tapir's live tests prove
it). The exceptions are the danger list:

| Command | Silently lost or wrong |
|---|---|
| CreateComposites | `useWith` omitted: availability for walls, slabs, roofs and shells cleared. `skins` and `separators` always replaced whole. An unresolvable building material or line type written as index 0 |
| CreateProfiles | `sourceAttributeId` on an overwrite: own geometry and the five use-with booleans replaced by the source's. `replaceSkins`: every skin and profile modifier dropped |
| CreatePenTables | `sourceAttributeId` on an overwrite: all 255 pens replaced |
| CreateBuildingMaterials | `id`, `manufacturer`, `description` omitted: null pointers reach Modify; keep or clear is unverified |
| CreateLines | misspelt `lineType` turns the line Solid; `period`, `height`, `dashItems`, `lineItems` ignored unless `lineType` is sent too |
| CreateFills | misspelt `subType` turns the fill Vector; changing `subType` without the new type's geometry leaves mismatched data |
| CreateSurfaces | texture flags can be switched on, never off; unknown `materialType` becomes General |
| CreateBuildingMaterials | unknown `cutFillOrientation` becomes ProjectOrigin |
| CreateLayerCombinations | a listed layer takes all four values from the request (an omitted flag is false); unknown layer GUIDs dropped |
| SetPropertyValuesOfAttributes | an unavailable property, an unknown attribute or a duplicate pair answers `{}`: nothing written, no error |

**Built-ins.** Index 1 of layers, lines and fills, and the Solid and Empty fills,
refuse Modify and Delete (APIERR_REFUSEDPAR); so does the last composite on
Delete. Teamwork answers APIERR_NOTMINE when the set is not reserved.

**References** inside items are `{"attributeId": {"guid"}}`, except a building
material's `cutFillIndex` / `cutSurfaceIndex` (plain indices) and a layer
combination's `layers[].attributeId` (one level, `{"guid"}`). Delete takes
`{"attributeType", "attributeId": {"attributeId": {"guid"}}}`.

**Get vs Create.** Field names and units match one to one for layers, lines,
fills, zone categories, pen tables, surfaces and building materials.
`GetLayerCombinations` takes `attributes`, wraps each answer in
`layerCombination` and calls the index `attributeIndex`. `GetProfiles` returns
geometry (`skins` with outlines and edges) and read-only fields (`useWith`,
`width`, ...) that `CreateProfiles` rejects; profile skins are edited through
`skinOverrides` keyed by `skinId`. `GetComposites` omits a skin's
`buildingMaterialId` when it does not resolve, while Create requires it. Layer
flags read from `GetLayers` follow the active layer combination.

## edit_attributes

### The tool

```
edit_attributes(changes: list[dict], dry_run: bool = True, port)
```

Dry run by default; `dry_run=false` commits. Each change has exactly one action
key, whose value is the attribute type:

| Change | Meaning |
|---|---|
| `{"create": "Layer", "name": "X-Temp", <fields>}` | a new attribute. **Never overwrites**: a name that exists is skipped (below) |
| `{"modify": "Layer", "attribute": "A-Wall", <fields>}` | change an existing attribute, addressed by name or GUID. `name` among the fields renames it. Fields not given keep their current value |
| `{"modify": "BuildingMaterial", "attribute": "Concrete", "properties": {"Group/Name": "value"}}` | property values of an attribute (may sit next to fields) |
| `{"delete": "Fill", "attribute": "Old hatch"}` | delete an attribute |

Types: `Layer`, `LayerCombination`, `Line`, `Fill`, `ZoneCategory`, `PenTable`,
`Profile`, `BuildingMaterial`, `Composite`, `Surface`; plurals and any case
work. `MEPSystem` stays in the gateway (the triage put MEP there).

Fields are Tapir's own field names for that type's `Create*` command, with its
units (metres; paper millimetres for pen widths; radians; pen numbers 1 to 255;
colours 0 to 1), with one convenience: a field that holds another attribute
takes that attribute's **name** as a string, the way `modify_elements` does,
and the dry run and `list_attributes(details=true)` show references by name:

| Field | Refers to | Sent as |
|---|---|---|
| `buildingMaterialId` (composite skins, profile skin overrides and edges) | BuildingMaterial | `{"attributeId": {"guid"}}` |
| `lineTypeId`, `contourLineTypeId`, `cutEndLineTypeId` | Line | `{"attributeId": {"guid"}}` |
| `fillId` (surfaces, profile skins) | Fill | `{"attributeId": {"guid"}}` |
| `surfaceId` (profile skins) | Surface | `{"attributeId": {"guid"}}` |
| `cutFillIndex` (building materials) | Fill | index |
| `cutSurfaceIndex` (building materials) | Surface | index |
| `sourceAttributeId` (pen tables, profiles) | the same type | `{"attributeId": {"guid"}}` |
| `layers[].attributeId` (layer combinations) | Layer | `{"guid"}` |

A GUID object in Tapir's form is accepted too. Calling with `changes=[]`
returns the field list of every type (name, JSON type, which fields take an
attribute name, and the fields `modify` refuses).

"Refusing overwrites unless explicitly asked" is the split between `create`
and `modify`: a `create` can never replace anything (it is sent with
`overwriteExisting: false`, so even a name a teammate added since the dry run
answers "Already exists."), and a `modify` names the attribute it changes.
There is no `overwrite` flag to forget about.

### What `modify` sends

Always `overwriteExisting: true`, the attribute's own GUID as `attributeId`,
and `name` (the new one, or the current one), never `index`. Then, per type,
whatever keeps the rest of the attribute as it is:

| Type | Sent besides the requested fields | Refused in a modify |
|---|---|---|
| Layer | nothing | |
| LayerCombination | the **whole** current layer list, with the requested layers' given flags laid over their current ones (a listed layer otherwise loses every flag not sent) | |
| Line | `lineType` (current) when `period`, `height`, `dashItems` or `lineItems` is sent | |
| Fill | nothing | `subType` (a new type needs new geometry: create a new fill) |
| ZoneCategory | all three stamp fields (current) when one is sent; Tapir applies `stampName` only with both GUIDs | |
| PenTable | nothing; `pens` changes only the pens listed | `sourceAttributeId` (replaces all 255 pens) |
| Profile | nothing | `sourceAttributeId`, `newSkins`, `replaceSkins` (replace or drop the geometry). Allowed: `name`, the five use-with booleans, `skinOverrides` by `skinId` |
| BuildingMaterial | `id`, `manufacturer`, `description` (current) | |
| Composite | `useWith`, `skins`, `separators` (current), since Tapir clears or replaces them | |
| Surface | the whole current `texture` when `texture` is sent (its `name` is required) | turning a texture flag off (Tapir can only switch flags on) |

List fields (`skins`, `separators`, `dashItems`, a layer combination's
`layers`, `pens`) behave as Tapir's do, with two conveniences: a composite's
`skins` or `separators` item that leaves out a field takes it from the current
item at the same position, and `pens` / `layers` entries patch only the entries
they name (by `index` / layer). A composite whose skin count changes must send
`separators` too (skins plus one).

### Checked before anything is sent

Tapir silently falls back on bad values, so every one of these is checked
locally and turns into a skip reason instead:

- **Enums**: `lineType` (Solid, Dashed, Symbol), `subType` (Vector, Solid,
  Empty, Symbol, LinearGradient, RadialGradient, Image), `cutFillOrientation`
  (ProjectOrigin, ElementOrigin, FitToSkin), `materialType` and composite
  skin `type` (Core, Finish, Other) and `useWith` values from the schema.
- **References**: every name or GUID must resolve to an existing attribute of
  the right type (a composite would otherwise write index 0).
- **Schema**: the finished payload item is validated against Tapir's bundled
  `Create*` item schema (`additionalProperties: false` there means one bad key
  would reject the whole batch). Whole-number floats in integer fields are sent
  as integers (Tapir reads pens into `short`, which ignores a float).
- **Composites**: a create needs `useWith`, `skins` and `separators`
  (skins plus one); without `useWith` nothing could use it.
- **Profiles**: a create needs `sourceAttributeId` or `newSkins`.

### Dry run

Read once per call: Tapir `GetAttributesByType` for each type the changes touch
(index, name, GUID) and for each type a reference names, the type's `Get*`
command for the attributes a change addresses or collides with, and in a
Teamwork project the sets' status.

Per change, in order:

1. **Shape.** Exactly one action key; a known type; `create` has `name`,
   `modify` and `delete` have `attribute`; unknown fields for the type are an
   error listing the allowed ones.
2. **Address.** `attribute` resolves by exact name, then GUID. No match: skipped
   "no Layer named 'X'". A near match (same name ignoring case and surrounding
   spaces, like the template's `"Interior - Furniture "`) is named in the
   reason, never used silently.
3. **Create collisions.** A `create` whose name exists is skipped with reason
   "Layer 'X' already exists; to change it send {"modify": "Layer",
   "attribute": "X", ...}", and the entry carries `would_change`: current ->
   new for every field the create gives that differs. A near match is a
   **warning** on a planned create, because Archicad would create a second,
   nearly identical name.
4. **Rename collisions.** A `modify` renaming onto a name another attribute of
   the type has (exactly or nearly) is skipped; Tapir does not check.
5. **Built-ins.** Modify and delete of index 1 (layer, line, fill) and of the
   Solid and Empty fills are skipped (Archicad refuses them); delete of the
   last composite too.
6. **The checks above** (enums, references, schema, required fields, refused
   modify fields).
7. **Layer delete.** Deleting a layer deletes every element on it in every
   database (DevKit, `ACAPI_Attribute_Delete`), and it cannot be undone. Tapir
   `GetAllElements` lists only the active window's database (seen live
   19.10.2026, what `editability.add_elsewhere` relies on), so a layer delete
   is **skipped unless the floor plan is the active window**: from a layout or
   section the count would be zero while the floor plan's elements on the
   layer are deleted. With the floor plan active, the dry run counts the
   floor-plan elements on the layer (`GetAllElements`, then
   `GetDetailsOfElements` with `fields: ["layerIndex"]`, chunked, a failing
   chunk retried in smaller pieces; never a property read) and **skips the
   delete when the count is not zero**: "N elements are on layer X and would be
   deleted with it; move them (modify_elements) or delete them
   (delete_elements) first". With zero it is planned with the warning that
   elements in sections, elevations, interior elevations, details, worksheets
   and layouts are not counted and would go too.
8. **Other deletes** are planned with the warning that elements and attributes
   using it keep pointing at a missing attribute (DevKit) and that a delete
   cannot be undone.
9. **Property values.** Addresses resolve like `set_element_data` (`Group/Name`
   or GUID, custom properties). Values are display strings in metres, square
   metres, cubic metres and degrees (Tapir's parsing); numbers are sent as
   text. Current values come from `GetPropertyValuesOfAttributes`, which
   answers Archicad's display string, probably in the project's working units
   (unverified), so the diff shows it as Archicad displays it and only String
   properties are compared for "nothing to change" and `not_applied`; other
   types report `now` without a verdict. A property that answers an error on
   the read is skipped as "not available for this attribute". Whether that
   read can crash Archicad the way element property reads did is checked live
   before this ships; if it does, property values ship write-only (no current
   value, `{}` answers still caught) and the spec says so.
10. **Teamwork.** The shared `object_set_check` with the type's set (blocking)
    for fields, create and delete; warning-only for property values.
11. **Nothing to change.** A `modify` whose every field equals the current value
    is planned with the warning "nothing to change" and not sent.

Result:

```
{"dry_run": true,
 "planned": [{"action": "create", "type": "Layer", "target": "X-Temp", "fields": {...}},
             {"action": "modify", "type": "Composite", "target": "Wall 300",
              "changes": {"name": ["Wall 300", "Wall 300 EPS"],
                          "skins[1].thickness": [0.1, 0.12]}},
             {"action": "delete", "type": "Fill", "target": "Old hatch",
              "warnings": ["..."]}],
 "skipped": [{"action": "create", "type": "Layer", "target": "Interior - Partition",
              "errors": ["... already exists ..."], "would_change": {...}}],
 "teamwork": [...]}          # Teamwork projects only
```

Diffs name list items by position (`skins[1].thickness`) and show attribute
references by name.

### Commit

Planned changes are sent grouped by command, one call per command, in this
order: per type, creates (`overwriteExisting: false`) before modifies
(`overwriteExisting: true`), types in the order fills, lines, surfaces,
building materials, layers, layer combinations, zone categories, pen tables,
profiles, composites (each before the types that refer to it); then property
values (`SetPropertyValuesOfAttributes`); deletes last (`DeleteAttributes`), so
nothing is deleted that another change still names. A reference to an
attribute created in the same call is resolved just before its batch is sent,
by name from a fresh `GetAttributesByType` (Tapir's returned GUID is not
trusted, per the source notes).

Tapir answers per item, index-aligned. Then the touched attributes are read
back with `GetAttributesByType` and `Get*` (by name for creates: Tapir's
returned GUID is not trusted, per the source notes):

- `applied`: `{action, type, target, now}`, `now` holding the fields that were
  asked for as Archicad now reports them.
- `not_applied`: a field Archicad answered success for but did not change (the
  read-back differs from the request), as `modify_elements` reports it.
- `failed`: Archicad's refusals grouped by message (`group_by_error`), with a
  few targets each; a `{}` property answer counts as "not written: property
  not available for this attribute"; in a Teamwork project a group whose set
  was not `Editable` carries the reserve hint.
- `error` and `stopped`: a whole command refused (for example schema
  rejection); the commands not sent after it are listed.

### `list_attributes` gains details

```
list_attributes(attribute_type: str, details: bool = False,
                names: list[str] | None = None, limit: int | None = None,
                offset: int = 0, port)
```

- All ten types (the four new ones, `Line`, `Fill`, `LayerCombination`,
  `PenTable`, answer the official `GetAttributesByType` on AC 29, see
  known-issues). Plurals and any case work.
- `details=false`: unchanged, names only, official API, works without Tapir.
- `details=true`: the type's Tapir `Get*` record for the attributes named in
  `names` (unknown ones listed under `not_found`), or all of them paged by
  `limit` / `offset`: 2 pen tables (255 pens each), 10 profiles (geometry), 50
  of the rest by default. References are shown by name (`<missing Fill 7>`
  when one does not resolve), in the vocabulary `edit_attributes` takes, so
  "read, change, send back" needs no translation. Layer combinations are
  unwrapped and get `index`.

### Tapir version

`TOOL_COMMANDS["edit_attributes"] = ("GetAttributesByType",)`: the only command
every call needs. Each type's `Create*` and `Get*` command, `DeleteAttributes`
and `Get/SetPropertyValuesOfAttributes` are checked with
`require_tapir_command` when a change needs them, and the changes of a type
whose command is missing are skipped with that refusal. The detailed `Get*`
commands, the rename fix and line, fill and profile geometry preservation all
arrived in Tapir 1.5.4; the layer combination fix for unlisted layers in 1.5.8,
which does not matter here because a modify sends the whole layer list.
`list_attributes(details=true)` checks the type's `Get*` command the same way.

## Code layout

| File | What |
|---|---|
| `core/attribute_types.py` | pure tables: per type the commands, array key, `Get*` input and output keys, Teamwork set, reference fields, enums, refused modify fields, what a modify adds |
| `core/attribute_edit.py` | `edit_attributes`: planning, dry run, commit, read-back |
| `core/attributes.py` | `list_attributes` with details; reference naming shared with `attribute_edit` |
| `core/teamwork.py` | set aliases, set states and problems, the object-set half of reserve and release |
| `core/definition_edit.py`, `core/classification_edit.py` | the reservation check |
| `server.py` | the new tool, new parameters, descriptions |
| `tapir_requirements.py` | `edit_attributes` entry |

## Tests

Unit tests against `FakeCore` with responses shaped like Tapir 1.7.1's schemas
and the live reads above (`tests/fixtures/attribute_replays.py`; its store
answers Create the way the source does: by name without overwrite, GUID first
with it, omitted fields kept except the danger list):

- reserve/release object sets: aliases collapse, each status row of the table,
  read-back after an attempt, conflicts reported, nothing sent outside Teamwork
  or in a dry run, `object_sets` needs 1.7.1, `guids`-only results unchanged.
- the shared check: solo project makes one call; blocking vs warning;
  unreadable status and old Tapir become warnings.
- definition tools in a fake Teamwork project: `teamwork` key in the dry run,
  hint on failure groups; solo results unchanged (existing tests stay green).
- `edit_attributes`: every skip reason, near-name warnings, create collision
  with `would_change`, the per-type modify payloads of the table above (a
  composite modify keeps `useWith`, a building material keeps its texts, a
  layer combination sends every layer), refused modify fields, enum and
  reference checks, layer delete counting and refusal, built-ins refused,
  command order on commit, a reference to an attribute created in the same
  call, per-item errors grouped, `{}` property answers, `not_applied` from the
  read-back, Teamwork skip and hint, `changes=[]` returns the field lists.
- `list_attributes`: the new types, details with names resolved, paging,
  missing references.
- Tool registration, annotations (`edit_attributes` is a destructive writer),
  manifest entry, `TOOL_COMMANDS`.

Teamwork behaviour is unit-tested only, against the shapes in Tapir's source;
see Verification.

## Verification

**Live, solo projects (MCP-Test on AC 29; MCP-Test_30 for a second check),
throwaway attributes only**, names prefixed `MCP-Canary`, deleted afterwards;
no existing office attribute is modified or deleted:

1. Dry run of a create that collides with a stock layer: skipped,
   `would_change` shown, nothing sent (compare `GetLayers` before and after).
2. Create a canary fill-referencing building material, a canary composite using
   it, a canary layer, line and layer combination; read back.
3. Modify each canary (rename, a flag, a skin thickness, a building material
   description left out); confirm untouched fields survive, in particular the
   composite's `useWith` and the building material's texts.
4. Property value on the canary building material: the read first, on
   MCP-Test_30 after saving, since element property reads have crashed
   Archicad 29.
5. Delete the canaries; the canary layer delete with zero elements on it.
6. `reserve_elements(object_sets=...)` in a solo project refuses as "not a
   Teamwork project"; the definition tools' results are unchanged.

### Live check, 2026-10-19

MCP-Test (AC 29/5101) and MCP-Test_30 (AC 30/3001), Tapir 1.7.1, floor plan
active, both solo projects. Script: create, modify and delete `MCP-Canary`
attributes through `core.attribute_edit`; the full attribute table (name and
GUID of all ten types) compared before and after: identical on both.

| Step | Result (both versions) |
|---|---|
| Dry run of a create named `Interior - Partition` | skipped, `would_change` `isLocked: [false, true]`, `GetLayers` unchanged |
| Creates: composite naming a building material created in the same call, the material (fill by name), a dashed line, a layer, a layer combination naming that layer | dry run wrote nothing; commit applied all 5, no `not_applied`; the composite's skin resolved to the new material |
| Modifies: layer rename + lock, composite skin thickness only, material density only, line dash items, layer combination flag on the renamed layer | all applied; composite kept `useWith: ["Wall"]` and its separators; material kept id, manufacturer, description |
| First run (before the fixes) | the layer combination failed with "no Layer named 'MCP-Canary Layer'": the layer was renamed first and the reference was looked up by name at send time; a line `period` 3 read back as 2 (the sum of its dash items); a new material had cut surface index 0. All three fixed and covered by tests |
| Raw `CreateBuildingMaterials` overwrite without the texts | id, manufacturer and description kept: the "possible wipe" in the source notes does not happen on AC 29 or 30 |
| Refusals (dry run) | misspelt `lineType`, unknown building material in a skin: skipped with the reason |
| `reserve_elements(object_sets=["Layers"])` | "not a Teamwork project" |
| Property values (after `SaveProject`) | reads did not crash either version; unavailable property: per-item error "Not available or not evaluated property", turned into a skip; stock "Brick" GWP read as "113.000". Writes not tried live (a canary material has no available property until classified) |
| Cleanup | every canary deleted through `edit_attributes`; the empty canary layer deleted with the floor plan active |

**Teamwork, not verified.** Whether `ClassificationsAndProperties` is the set
Archicad wants for definitions, which set attribute property values need, and
the exact refusal Archicad gives without a reservation can only be seen on a
Teamwork project. The only one here is CVP, production, which needs Aleš's
explicit go-ahead; until then the Teamwork half is unit-tested against the
shapes in Tapir's source and the README and known-issues say so.

## Out of scope

- `MEPSystem` attributes, `OperationProfile` (no Tapir create), pens and fonts.
- Changing a fill's type, or a profile's geometry from another profile, in
  place: create a new one.
- Replacing an attribute in use before deleting it (Attribute Manager's
  "delete and replace"): no API.
- Counting the elements that use a non-layer attribute before a delete (needs
  reading every element).
- `ReserveHotlinkCacheManagement` (item `update_hotlinks` in the triage).
- Auto-reserving a set before a write: a reservation is visible to the team, so
  it stays an explicit, confirm-gated call.

## Calls made in this design (not discussed with Aleš)

- `create` / `modify` instead of an `overwrite` flag.
- `modify` adds what each type's overwrite would otherwise lose, and refuses
  the fields that replace geometry or pens.
- `object_sets` on the existing reservation tools instead of sibling tools.
- Layer deletes refused while elements are on the layer, and unless the floor
  plan is the active window.
- Layer flag-only modifies in Teamwork warn instead of block.
- Attribute property values compared only for String properties.
- Blocking only on documented sets; warnings for the guessed
  `ClassificationsAndProperties` and for attribute property values.
- `MCP-Canary` prefix for live attributes.
