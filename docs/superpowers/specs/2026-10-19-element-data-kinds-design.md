# get_element_data and set_element_data: more kinds of element data

Date: 2026-10-19. Item 2 of the Tapir command triage
(`docs/superpowers/specs/2026-10-19-tapir-command-triage.md`, untracked in the main checkout).

## Why

`set_element_data` writes property values and nothing else. Everything else an
office changes on elements in bulk goes through the raw gateway today, without a
dry run, an editability check or a report of what landed:

- **Classifications.** Classifying elements is the office CLASS_RULES job. There is no
  curated classification write.
- **GDL parameters** of objects, lamps, doors, windows and skylights. Tapir
  Get/SetGDLParametersOfElements are used only inside the swept beam and the road
  marking (`core/placed_path.py`, `core/road_marking.py`).
- **Door and window marker parameters** (Tapir 1.7.1): the stamp next to an opening,
  which carries the door number on many office markers.
- **Local IFC properties** (Tapir 1.7.0 SetIFCPropertiesOfElements).

`get_element_data` reads properties and, with `include_classifications`, classification
item GUIDs. It cannot read GDL, marker or IFC values, so an agent cannot show
"current -> new" before writing them or check them after.

## Goals

1. One address syntax for every kind, used by both tools, so one `set_element_data` call
   can mix kinds without ambiguity and a value read with `get_element_data` can be written
   back with the same address.
2. Existing property-only calls work unchanged: same parameters, same addresses, same
   result keys.
3. `set_element_data` keeps its contract for every kind: dry run by default showing
   current -> new per change; elements Archicad would refuse (other database, hidden or
   locked layer, hotlinked module, Edit > Locking, not reserved) skipped up front with
   the reason from `core/editability.py`; Archicad's refusals grouped by code and message;
   and a readback that reports what actually landed.
4. Tapir's unit traps are hidden: the caller reads and writes one unit per value type.

## Non-goals

- Writing single items of a GDL array (`index1`/`index2`). A caller changes an array by
  sending the whole array; reading it first gives the rest. Item addressing can come later.
- GDL parameter writes on zones, labels, drawings and curtain wall parts. Tapir reads
  them; its write path patches the element only for objects, lamps, doors, windows and
  skylights, and the others are unverified.
- Property enum writes (still refused with the existing reason), dictionary-type GDL
  parameters (the Archicad API has none).
- Bulk wildcard dumps: wildcards are for finding names on a few elements (below).
- A `find_elements` comparison on GDL, marker or IFC values.
- Unlocking, reserving or switching windows for the caller.

## Anti-goals

- A dry run that says "fine" for a change Archicad will refuse or silently replace. The
  dry run runs the same planning the commit runs.
- A success count taken from Archicad's answer alone. A Parameter Script can replace a
  written GDL value and Tapir still answers success; the readback decides `applied`.
- Breaking the scripting API: `plan_property_writes` and `send_property_writes` keep
  their signatures and behaviour (`scripting/api.py`, `scripting/apply.py` call them).

## Addresses

`get_element_data(properties=[...])` and `set_element_data` changes'
`property` take addresses. The prefix decides the kind; everything else is a property, as
today ('Group/Name', a built-in non-localized name, or a property GUID). `find_elements`
already reserves `classification:`, so the new prefixes follow it.

| Address | Kind | Value read | Value written |
|---|---|---|---|
| `Group/Name`, built-in name, property GUID | property | as today | as today |
| `classification:<System>` | classification item in that system | the item ID (code), or null when unclassified | an item ID or an item GUID; null unclassifies |
| `gdl:<parameter name>` | GDL parameter of the element | see "Values" | see "Values" |
| `marker:<parameter name>` | GDL parameter of a door's or window's marker | see "Values" | see "Values"; arrays refused |
| `ifc:<Pset>.<Name>` | local IFC property | the text Tapir reads | a string, number or boolean |

- `<System>` is the system name, or `name version` when two systems share a name (the
  labels `ClassificationIndex` in `core/definition_edit.py` already builds and
  `edit_classifications` uses).
- A GDL or marker name is matched exactly; failing that, a single case-insensitive match
  is used (GDL names are case-insensitive) and the dry run shows the name as the part
  spells it.
- `ifc:` splits at the first `.`: the property set name before it, the property name
  after it. This is the `Pset.Name` form the `ifc-property-required` rule already uses.
- Reads only: `gdl:*`, `marker:*`, `ifc:*` and `classification:*` expand to every
  parameter (without those flagged Hidden), every IFC property, or every system. A
  wildcard in a write is skipped with a reason.
- A prefix wins over a property name: a user property group literally called `gdl:x`
  would need its GUID. Called out in the tool description.

### Values

GDL and marker values keep Tapir's read units, so a value read is a value that can be
written back:

- Length in metres; RealNumber, Intensity, RGBColor as numbers.
- **Angle in radians**, matching property angles (`search_definitions` says radian) and
  what Tapir reads. Tapir's write path is not symmetric (below), so the tool converts.
- Integer and PenColor as whole numbers. LineType, Material (Surface), FillPattern,
  BuildingMaterial and Profile read as attribute indexes; a write takes the index or the
  attribute's name, resolved through Tapir GetAttributesByType (`tapir_attributes`).
- Boolean as true/false; LightSwitch as "On"/"Off" (true/false accepted and converted).
- String and Title as text: GDL up to 511 characters, marker up to 255 (Tapir copies
  into fixed buffers and truncates silently past these).
- Arrays: a list for one dimension, a list of rows for two. The read reshapes Tapir's
  flat list by `dimension2` (a dimension2 of 1 reads as a flat list, which is also how
  Tapir stores a one-dimensional write).

## get_element_data

Signature unchanged: `get_element_data(guids, properties=None,
include_classifications=False, port=None)`. Addresses go in `properties`.

Per element, as today: `guid`, `type`, `layer`, `properties` (keyed by the address as
given, wildcards expanded to concrete addresses; None where the element has no such
value), and `classifications` when `include_classifications` is true (unchanged: system
name -> item GUID). New, only when needed:

- `labels`: for wildcard reads, address -> display name where the library part gives one
  that differs from the parameter name (`gdl:A` -> "Width"). Display names are how a
  caller finds "the opening angle" among cryptic parameter names.
- `unreadable`: kind -> reason when a whole kind cannot be read for the element, for
  example `{"gdl": "a Wall has no GDL parameters"}`, `{"marker": "has no marker"}`, or the
  Tapir-missing reason. Its addresses read None.

Each kind is read only when an address asks for it, in chunks (GDL and marker reads:
50 elements per request, because each answer carries every parameter with its value list;
classification and IFC reads: 500). The existing element ceiling
(`MAX_PROPERTY_FETCH_ELEMENTS`) applies to every kind.

A wildcard returns hundreds of values per door, so a call with a wildcard is refused
above 20 elements (`WILDCARD_ELEMENT_LIMIT`), with the advice to read one element with
the wildcard and then name the parameters for the rest.

## set_element_data

Signature unchanged: `set_element_data(changes, dry_run=True, port=None)`. A change is
`{"guid", "property": <address>, "value"}`, plus `"ifc_type"` for an `ifc:` address
(optional, see IFC).

### Planning (dry run and commit alike)

1. Validate each change: a dict with a GUID string, an address string and a `value` key.
   A malformed change is skipped with the reason; it does not fail the call.
2. The same GUID and address twice with different values: both skipped ("given two
   different values"). Identical repeats collapse into one.
3. `editability.refusals()` once for every GUID in the call. A refused element's changes
   are all skipped with that reason.
4. Each kind plans its changes from one read of the current values (below), producing
   `{guid, property, current, new}` and, where something is likely to go wrong without
   being certain, a `warning`.

The dry run returns `{"dry_run": true, "planned_changes": [...], "skipped": [...]}` (the
latter only when non-empty). This is a change from today's property dry run, which
echoed every change without checking it: changes that would be skipped now show up
under `skipped` with the reason, and `new` shows the value as it will be sent (an
Integer "12" shows as 12).

### Sending

Order: classifications, properties, GDL, markers, IFC. Classifications first because
property availability follows classification: a property that exists only under the new
classification cannot be planned in the same call (its type is unreadable before), and
its skip reason says so when the same element has a classification change in the call:
"send the classification first, then this property in a second call".

A batch Archicad refuses outright (an exception, such as 4001 while someone is mid-input)
stops the run. What was sent before it stays reported; the changes not sent are counted
under `not_sent`.

### Readback and result

After sending, every change Archicad answered success for is read again with the same
reader the plan used (for properties, the way `scripting/apply.py` `_read_back` already
does after `apply_changeset`; that code moves into `core/element_data.py` and both use
it). Only the last value of a repeated cell counts. Result:

```
{"dry_run": false,
 "applied": N,            # changes now holding the new value
 "not_applied": [...],    # Archicad answered success but the read shows another value:
                          #   [{"property", "count", "sample": [{"guid", "sent", "read"}]}]
 "failed": [...],         # Archicad's refusals, group_failures() as today
 "skipped": [...],        # not sent, {guid, property, reason} as today
 "not_sent": {"count"},   # after a stop
 "unconfirmed": {...},    # the readback itself failed: count, GUIDs, note
 "stopped": {...}}        # the error that stopped the run, as today
```

Keys appear only when non-empty, as today. A property-only call whose writes all read
back as sent returns exactly what it returns today; `not_applied`, `unconfirmed` and
`not_sent` are the only additions, and only when something did not land.

Numbers compare with a tolerance of 1e-6 (absolute, metres and radians); text exactly;
arrays item by item.

## The kinds

### Properties

Unchanged: `plan_property_writes` / `send_property_writes`, official
API.SetPropertyValuesOfElements in chunks of 500. New: the readback above, over the
written elements and properties only, sliced by `MAX_PROPERTY_FETCH_ELEMENTS` (the same
scope as the plan's own read). `plan_property_writes` takes an optional precomputed
refusals map so the editability read runs once per call; called alone (scripting) it
reads them itself as today.

### Classifications

- Write: Tapir SetClassificationsOfElements when available (it also reaches sub-elements
  of curtain walls, stairs and railings, per its schema), else the official
  API.SetClassificationsOfElements. Same payload: `{"elementId", "classificationId":
  {"classificationSystemId", "classificationItemId"?}}`; no item id unclassifies.
- Read (plan and readback): the matching GetClassificationsOfElements. The official
  answer nests system and item under `classificationId`; the Tapir 1.7.1 source puts them
  directly in the list entry while its bundled schema nests them. Parse both. An item
  GUID of all zeros, or an error entry for that system, reads as unclassified (verify
  live which Tapir answers).
- The value resolves within the named system through `ClassificationIndex`: item ID
  first, then item GUID. An ID shared by two items in the system is skipped ("address it
  by GUID"). An unknown system or item is skipped with the systems or a hint.
- current and new show the item ID (null for unclassified).
- Works without Tapir (official API), like property writes.

### GDL parameters (`gdl:`)

- Needs Tapir GetGDLParametersOfElements and SetGDLParametersOfElements (1.5.7).
- Writes only on Object, Lamp, Door, Window, Skylight (types from the editability /
  details read or `_fetch_types`); other types are skipped with a reason. Reads on
  anything Tapir answers for.
- One SetGDLParametersOfElements entry per element with all its GDL changes, 50 elements
  per request. Tapir applies an element's parameters together and stops at the first bad
  one, so one refusal fails every GDL change of that element; each is reported under
  `failed` with Tapir's message.
- Checks from the plan read: the parameter exists; value fits the type (above); not an
  array given a single value or vice versa; string length.
- Warnings, not skips: the parameter is locked in the object's settings (`isLocked`);
  the value is not one of the parameter's listed values while the list allows no custom
  value.
- **A and B on objects and lamps:** Tapir before 1.6.0 writes A and B in the wrong unit
  when the object's size is not fixed (`useXYFixSize` off), so the object comes out
  A x libA (memory: tapir-object-size-ratio; fixed upstream in #718, first released in
  1.6.0, verified in the 1.7.1 source, `ElementGDLParameterCommands.cpp`
  `GetStoredSizeScale`). With a Tapir reporting a version below 1.6.0 these two are
  skipped with an update hint. The gate is the reported version, not a command check:
  a local Tapir build can carry newer commands without #718.
- **Angles:** Tapir passes a single Angle value to ACAPI_LibraryPart_ChangeAParameter,
  which takes degrees, and writes whole arrays straight into the parameter list, which
  holds radians; both read back in radians (found on the swept beam,
  `docs/superpowers/specs/2026-09-29-swept-beam-findings.md`). The tool takes radians and
  converts a single Angle to degrees before sending; Angle arrays go as they are.
- Attribute names for attribute-typed parameters resolve once per attribute type per
  call. current and new show names when the caller gave a name, indexes when an index.

### Marker parameters (`marker:`)

- Needs Tapir Get/SetMarkerParametersOfElements (1.7.1). Doors and windows only; Tapir
  answers "has no marker" for an opening without one, which becomes the skip reason.
- Same value rules as GDL, except arrays are refused (Tapir refuses them) and strings stop
  at 255 characters.
- Tapir writes marker values straight into the marker's parameter list, so angles go in
  radians as read: no conversion (verify live).
- Same per-element all-or-nothing, batching and readback as GDL.

### IFC properties (`ifc:`)

- Needs Tapir SetIFCPropertiesOfElements (1.7.0) and **Archicad 25 to 27**: from Archicad
  28 Tapir answers every call with "Writing IFC properties is not supported in Archicad 28
  and newer yet" (`IFCCommands.cpp`, the IFCAPI that replaced ACAPI_Element_SetIFCProperty
  has no write in Tapir). On 28 and newer every `ifc:` change is skipped with that reason
  before anything is sent. The office runs 28 to 30 and no Archicad 27 with Tapir 1.7.x
  is installed here, so the write is unit-tested only.
- Only single values (IfcPropertySingleValue). `ifc_type` (IfcLabel, IfcBoolean,
  IfcInteger, IfcReal, a measure type, ...) defaults from the value: text -> IfcLabel,
  boolean -> IfcBoolean, whole number -> IfcInteger, other number -> IfcReal. The dry run
  shows the type used. `ifc_type` on a non-`ifc:` change is skipped.
- current is the text Tapir reads; the readback compares booleans with True/False,
  numbers numerically, text exactly.

## Fixed on the way

- **The IFC read never worked.** `extract._fetch_ifc` reads `response["elements"][i]
  ["properties"]`; Tapir answers `elementIFCProperties[i].ifcProperties` (1.5.1 to 1.7.1
  source, and live on MCP-Test 19.10.2026). Every element read as having no IFC
  properties, so `verify_ifc_export_readiness` and every `ifc-property-required` rule
  reported every element as missing. The test fixture copied the wrong shape. Fixed with
  the real shape, positional matching, and error entries skipped.
- **Lowercase GUIDs failed the editability check.** Tapir FilterElements answers GUIDs
  uppercase (live 19.10.2026: a lowercase GUID came back uppercase), and
  `editability.classify` compared the answer with the GUIDs as given, so every element of
  a lowercase call was refused as on a hidden layer. Comparisons become case-insensitive
  in `editability` and `teamwork._in_my_workspace`; reasons stay keyed by the GUIDs as
  given.

## Code layout (builder's call where not stated)

- `core/element_address.py`: parse an address into (kind, key); wildcard detection.
- `core/element_values.py`: the GDL/marker value rules (fit, angle conversion, string
  limits, array shape, value-list warning) and IFC type inference; pure functions.
- `core/element_kinds.py`: one reader and one writer per new kind (classification, GDL,
  marker, IFC), each returning per-element results positionally matched.
- `core/element_data.py`: `get_element_data` and `set_element_data` orchestrate; the
  property helpers stay here with their signatures.
- `extract._fetch_ifc` fixed; `server.py` descriptions; `manifest.json` and
  `scripts/build_dashboard.py` entries; README and `docs/query.md` address notes.

## Live check (MCP-Test, Archicad 29, port 19723; then MCP-Test_30 for the reads)

Only after `list_instances` shows `project_name` MCP-Test. Save the project before any
property read. Test elements near the origin, deleted afterwards.

1. Create a wall at the origin with a door and a window (`create_elements`), and one
   object with `useFixSize` false.
2. Dry runs for every kind; nothing changes in the model.
3. Commit: a classification (and unclassify), a GDL Length (door A), a single GDL Angle
   (confirms the degree conversion: read back equal in radians), a GDL string, a GDL
   attribute parameter by name (the readback compares indexes, so check the name maps to
   the right index by reading the attribute table), an object A and B with the size not
   fixed (confirms #718 on 1.7.1 through this path), a marker parameter (string, and an
   angle if the marker has one), a property, all in one call.
4. Readback: `applied` matches; force one `not_applied` if a part's value list allows it.
5. An IFC change on Archicad 29: skipped with the Archicad 28+ reason.
6. Lowercase GUIDs pass the editability check.
7. Delete the test elements.

## Left to the builder

- Exact chunk sizes, wording of reasons, and the internal data structures.
- Whether `labels` also shows for named (non-wildcard) reads: no, unless trivial.
- Duplicate detection happens after GUID case and GDL name case are resolved.
- Dry-run values are in the caller's units (radians, names), never the degrees sent.

## Calls made without asking (for Aleš to check)

1. Prefixed addresses inside `property` instead of new change keys or new parameters.
2. Angles in radians at the tool, converted for Tapir's single-Angle write.
3. IFC writes built and gated to Archicad 27 and older, though nothing here can run them.
4. The property dry run now runs the full plan and reports `skipped`.
5. Property writes get a readback too (the brief names the readback as part of the
   contract; `apply_changeset` already reads properties back the same way).
6. Classification writes through Tapir when present, official API otherwise.
7. `include_classifications` keeps returning item GUIDs; `classification:<System>`
   addresses return item IDs.
8. Wildcard reads are capped at 20 elements per call.

## Live findings (19.10.2026, MCP-Test, Archicad 29 5101, Tapir 1.7.1)

One mixed call on a test wall, door, window and object near the origin, then deleted;
the project was saved first.

1. **Single GDL Angle:** `gdl:gs_sill_angle` sent as 0.2617993878 rad went to Tapir as
   15.0 and read back 0.261799388 rad. The degree conversion is right.
2. **Tapir's classification read** answers the flat shape (system and item directly in
   the list entry) and an all-zero item GUID for an unclassified element.
3. **Attribute by name:** `gdl:gs_leaf_mat` "Paint - Ultramarine Violet" became index 20,
   which the Surface table names so.
4. **Object A and B** on two "Cube" objects, one with `useFixSize` false and one true:
   1.25 x 0.75 read back from GDL and from GetDetailsOfElements alike. #718 holds through
   this path on 1.7.1.
5. **A Parameter Script that pins the size** ("Frezza Mediawalk"): Tapir answered success
   for A 5.5 and B 0.6, the read showed 1.7 and 0.54, and the result listed both under
   `not_applied`. The same write also replaced the stale size an API placement leaves in
   GetDetailsOfElements (5.1 x 0.54 x 0.2 became 1.7 x 0.54 x 1.89).
6. **Doors and windows created through Tapir have no marker,** even from the "Internal
   Door" favorite: GetMarkerParametersOfElements answers "has no marker", which became the
   skip reason. Marker writes are therefore unit-tested only. The marker read was checked
   on a door placed by hand in MCP-Test_28 ("Door Stamp 27", 227 parameters), read only.
7. **IFC on Archicad 29:** skipped with the Archicad 28 reason before anything was sent.
8. **Lowercase GUIDs** planned and committed like uppercase ones.
9. A door's `gdl:*`, `ifc:*` and `classification:*` together are 665 values for one
   element: the wildcard limit is warranted.
10. The reads also ran on MCP-Test_30 (Archicad 30): GDL, classifications, IFC, and the
    marker reason for an object.

