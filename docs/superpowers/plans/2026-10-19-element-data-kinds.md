# Element data kinds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `get_element_data` and `set_element_data` read and write classifications, GDL parameters, door/window marker parameters and local IFC properties next to property values, through one address syntax, with the dry run, guards, grouped refusals and readback every kind shares.

**Architecture:** An address parser decides the kind from a prefix (`classification:`, `gdl:`, `marker:`, `ifc:`, else property). Pure value rules live in one module, Tapir and official readers/senders per kind in another, and a planner/committer module runs one editability check per call, plans every kind from one read, sends in a fixed order and reads everything back. The property helpers the scripting API uses keep their signatures.

**Tech Stack:** Python 3.11+, FastMCP, multiconn_archicad, pytest with `tests.conftest.FakeCore`.

**Spec:** `docs/superpowers/specs/2026-10-19-element-data-kinds-design.md`

## Global Constraints

- No em or en dashes anywhere: code, comments, docs, commit messages.
- Comment density and style match `core/modify.py` and `core/element_data.py`: a docstring that says why, comments only where a trap lives.
- Existing property-only calls keep their parameters, addresses and result keys; `plan_property_writes(conn, changes)` and `send_property_writes(conn, planned)` keep their signatures and return shapes.
- Values at the tool: Length metres, Angle radians, attribute-typed GDL parameters as index or attribute name.
- Chunks: GDL and marker reads and writes 50 elements per request; classification, IFC and property requests 500 (`PROPERTY_FETCH_CHUNK`); `MAX_PROPERTY_FETCH_ELEMENTS` applies to every kind; wildcard reads refused above `WILDCARD_ELEMENT_LIMIT = 20` elements.
- GDL string limit 511 characters, marker string limit 255.
- Number comparison in readbacks and value lists: absolute tolerance 1e-6.
- Send order: classifications, properties, GDL, markers, IFC.
- A/B on Object/Lamp skipped when the reported Tapir version is below (1, 6, 0).
- IFC writes skipped on Archicad 28 and newer.
- Run tests with `uv run pytest -q` from the worktree root.

## Review Focus

1. Lowercase or mixed-case GUIDs in either tool: the editability check must not refuse them, and responses must still line up (Task 1 test, Task 7 test).
2. A GDL write whose value a Parameter Script replaces: Tapir answers success, the result must say `not_applied` with the read value, not `applied` (Task 8 test).
3. A batch that raises mid-run (Archicad 4001): changes in that batch that did land count as applied, the rest as `not_sent`, earlier batches stay reported (Task 8 test).
4. A single Angle written in radians must reach Tapir in degrees, while an Angle array and a marker Angle go in radians (Task 3 and Task 8 tests).
5. A classification change plus a property that only exists under the new classification in one call: the property is skipped with the "second call" hint, not a bare "not available" (Task 7 test).

---

### Task 1: Case-insensitive editability comparisons

**Files:**
- Modify: `src/archicad_mcp/core/editability.py` (`_passing`, `classify`)
- Modify: `src/archicad_mcp/core/teamwork.py` (`_in_my_workspace`, `_partition`)
- Test: `tests/test_property_writes.py`

**Interfaces:**
- Produces: `_passing(...) -> set[str]` and `_in_my_workspace(...) -> set[str]` return uppercase GUIDs; every membership test against them uses `g.upper()`. Keys of `classify()` and `refusals()` stay the GUIDs as given.

- [ ] **Step 1: Write the failing test** `test_plan_accepts_lowercase_guids_when_tapir_answers_uppercase`: build `_conn_with_filters` with GUIDs `"d-1"`, `"d-2"` where `FilterElements` answers `guid.upper()` for passing elements (wrap `filter_elements`); `plan_property_writes` plans both, `skipped == []`.
- [ ] **Step 2: Run** `uv run pytest tests/test_property_writes.py -q -k lowercase`; expected FAIL (both skipped as hidden).
- [ ] **Step 3: Implement** the uppercase sets and `g.upper()` comparisons in `editability.classify` (editable, visible, mine) and `teamwork._partition` (`mine`).
- [ ] **Step 4: Run** `uv run pytest tests/test_property_writes.py tests/test_editability_locks.py -q`; expected PASS.
- [ ] **Step 5: Commit** `fix: compare GUIDs case-insensitively in the editability check`

### Task 2: The IFC read uses Tapir's real response shape

**Files:**
- Modify: `src/archicad_mcp/extract.py` (`_fetch_ifc`, new `parse_ifc_response`)
- Modify: `tests/fixtures/api_replays.py` (`TAPIR_IFC_PROPERTIES`)
- Test: `tests/test_extract.py`

**Interfaces:**
- Produces: `parse_ifc_response(chunk: list[str], response: dict) -> dict[str, dict[str, str] | str]`: GUID (as in `chunk`, positional) -> `{"Pset.Name": text}` or the error message string for an error entry. Reads `response["elementIFCProperties"][i]["ifcProperties"]`.
- `_fetch_ifc` keeps its return contract (`None` when unavailable; error entries become `{}`).

- [ ] **Step 1: Change the fixture** to the live shape: `{"elementIFCProperties": [{"elementId": {"guid": "w-1"}, "ifcProperties": [{"propertySetName": "Pset_WallCommon", "name": "FireRating", "value": "EI60"}]}, {"elementId": {"guid": "w-2"}, "ifcProperties": []}, {"elementId": {"guid": "z-1"}, "ifcProperties": []}]}` (it has to be callable per chunk: answer only the requested elements, in order).
- [ ] **Step 2: Write tests** `test_parse_ifc_response_matches_by_position_and_keeps_errors` (lowercase chunk GUIDs vs uppercase answer still map; `{"error": {"code": 1, "message": "The given element was not found"}}` -> that message) and keep `test_classification_and_ifc_fetches_are_chunked` asserting `{"Pset_WallCommon.FireRating": "EI60"}`.
- [ ] **Step 3: Run** `uv run pytest tests/test_extract.py -q`; expected FAIL.
- [ ] **Step 4: Implement** `parse_ifc_response` and use it in `_fetch_ifc`.
- [ ] **Step 5: Run** `uv run pytest tests/test_extract.py tests/rules -q`; expected PASS.
- [ ] **Step 6: Commit** `fix: read IFC properties from the shape Tapir answers`

### Task 3: Addresses and value rules (pure)

**Files:**
- Create: `src/archicad_mcp/core/element_address.py`, `src/archicad_mcp/core/element_values.py`
- Test: `tests/test_element_address.py`, `tests/test_element_values.py`

**Interfaces:**
- `element_address`: constants `PROPERTY, CLASSIFICATION, GDL, MARKER, IFC` (the strings `"property"`, `"classification"`, `"gdl"`, `"marker"`, `"ifc"`); `@dataclass(frozen=True) class Address: text: str; kind: str; key: str; wildcard: bool`; `parse_address(text: str) -> Address | str` (str is the reason). Prefix match is case-insensitive; key is the rest, stripped. Errors: empty key; `ifc:` key without a `.` with text on both sides ("write ifc:<Pset>.<Name>"). Wildcard = key `"*"`. `ifc_parts(address) -> tuple[str, str]` splits the key at the first `.`.
- `element_values`:
  - `ATTRIBUTE_TYPES = {"LineType": "Line", "Material": "Surface", "FillPattern": "Fill", "BuildingMaterial": "BuildingMaterial", "Profile": "Profile"}`; `GDL_STRING_LIMIT = 511`; `MARKER_STRING_LIMIT = 255`; `TOLERANCE = 1e-6`.
  - `read_value(entry: dict)`: Tapir's `value`, reshaped to rows of `dimension2` when `dimension2 > 1`.
  - `is_array(entry) -> bool` (has `dimension1`).
  - `fit(entry: dict, value, *, string_limit: int, arrays: bool, resolve_attribute) -> tuple[object, str | None]`: the value to send in tool units, or the reason. `resolve_attribute(attribute_type: str, name: str) -> int` raises `ValueError(reason)`. Rules from the spec "Values" section; a float with an integral value is accepted for integer types; bools are never numbers; LightSwitch accepts "On"/"Off" or a bool (converted); arrays only when `arrays` is true, each item checked against the base type, 2D rows of equal length; unsupported types ("Dictionary", unknown) give a reason.
  - `to_tapir(entry: dict, value, kind: str)`: `math.degrees(value)` only for kind `"gdl"`, type `"Angle"`, not an array; otherwise the value unchanged.
  - `warnings(entry: dict, value) -> list[str]`: `isLocked` true; a plain value list (`possibleValues` items with a `value` key and no `flag`) with `canHaveCustomValue` false that does not contain the value (first 5 listed in the text).
  - `same(expected, read) -> bool`: numbers within `TOLERANCE`, lists item by item, else `==`; a bool never equals a number.
  - `ifc_type_for(value) -> str | None` and `ifc_fit(value, ifc_type: str) -> str | None` (Tapir's rules: IfcBoolean/IfcLogical bool, IfcLogical also "Unknown"; IfcInteger, IfcCountMeasure, IfcTimeStamp whole numbers; IfcReal and `*Measure` except IfcDescriptiveMeasure numbers; anything else text); `ifc_same(value, ifc_type, read_text) -> bool` (bool vs "True"/"False", numbers numerically, text exactly).

- [ ] **Step 1: Write tests** in `tests/test_element_address.py`: `"Office/Status"` -> property; `"classification:Uniclass 2015"` -> key `"Uniclass 2015"`; `"GDL:A"` -> gdl `"A"`; `"marker:*"` -> wildcard; `"ifc:Pset_DoorCommon.FireRating"` -> `ifc_parts` `("Pset_DoorCommon", "FireRating")`; `"ifc:NoDot"` and `"gdl: "` -> str reasons.
- [ ] **Step 2: Write tests** in `tests/test_element_values.py`, parametrized: Length 0.9 -> 0.9; Integer 2.0 -> 2; Integer True -> reason; Boolean "yes" -> reason; LightSwitch True -> "On"; String of 512 chars with limit 511 -> reason; array entry `{"dimension1": 3, "dimension2": 1}` given 0.5 -> reason naming "whole array"; scalar entry given `[1, 2]` -> reason; 2D ragged `[[1, 2], [3]]` -> reason; Material "Paint" with a resolver returning 12 -> 12; resolver raising -> its reason; Dictionary -> reason; `read_value` of `{"value": [1, 2, 3, 4], "dimension1": 2, "dimension2": 2}` -> `[[1, 2], [3, 4]]` and of `dimension2: 1` -> flat; `to_tapir` single Angle `math.pi / 2` for gdl -> 90.0, for marker -> `math.pi / 2`, Angle array unchanged; `warnings` for `isLocked` and for a value list `[{"value": 1}, {"value": 2}]` with `canHaveCustomValue: False` and value 3; `same(0.9, 0.9000004)` true, `same(True, 1)` false; `ifc_type_for` for "x", True, 3, 2.5; `ifc_same(True, "IfcBoolean", "True")`, `ifc_same(2.5, "IfcReal", "2.500000")`.
- [ ] **Step 3: Run** `uv run pytest tests/test_element_address.py tests/test_element_values.py -q`; expected FAIL (modules missing).
- [ ] **Step 4: Implement** both modules.
- [ ] **Step 5: Run** the same command; expected PASS.
- [ ] **Step 6: Commit** `feat: element data addresses and GDL, marker and IFC value rules`

### Task 4: Readers and the shared sender

**Files:**
- Create: `src/archicad_mcp/core/element_kinds.py`
- Test: `tests/test_element_kinds.py`

**Interfaces:**
- Consumes: `parse_ifc_response` (Task 2), `ClassificationIndex` / `ClassSystem` (`core/definition_edit.py`), `install_hint`.
- Produces:
  - `PARAMETER_COMMANDS = {"gdl": ("GetGDLParametersOfElements", "gdlParametersOfElements", "SetGDLParametersOfElements", "elementsWithGDLParameters", "gdlParameters"), "marker": ("GetMarkerParametersOfElements", "markerParametersOfElements", "SetMarkerParametersOfElements", "elementsWithMarkerParameters", "markerParameters")}`; `PARAMETER_CHUNK = 50`.
  - `tapir_requirement(conn, command: str) -> str | None`: None when available, else "needs Tapir <since> or newer (<command>)" plus `install_hint(conn.archicad_version(), replace=conn.tapir_available())`; `<since>` from `build_registry()[command].version`.
  - `tapir_version(conn) -> tuple[int, int, int] | None` (move `modify._version` here; `modify` imports it).
  - `read_parameters(conn, guids: list[str], kind: str) -> dict[str, dict]`: GUID -> `{"params": {name: entry}}` or `{"error": message}`, positional, chunked.
  - `find_parameter(params: dict[str, dict], name: str) -> dict | None`: exact, else the single case-insensitive match.
  - `read_classifications(conn, guids: list[str], systems: list[ClassSystem]) -> dict[str, dict[str, str | None]]`: GUID -> system label -> item GUID or None. Tapir GetClassificationsOfElements when available, else official; each entry parsed as `item.get("classificationId", item)`; an all-zero item GUID, a missing item or an error entry is None.
  - `read_ifc(conn, guids) -> dict[str, dict[str, str] | str]` via Tapir GetIFCPropertiesOfElements and `parse_ifc_response`.
  - `@dataclass class Sent: outcomes: list; stopped: Exception | None`; `send_items(call, command: str, list_key: str, items: list[dict], chunk: int) -> Sent` where `call` is `conn.tapir` or `conn.official`; each outcome is `"ok"`, `{"code", "message"}`, `"unanswered"` (in the batch that raised) or `"not_sent"` (after it). A missing result counts as success, as `send_property_writes` does today.
- `send_property_writes` (`core/element_data.py`) is reimplemented on `send_items` with its return contract unchanged.

- [ ] **Step 1: Write tests**: `read_parameters` positional with lowercase GUIDs and an error entry; chunking at 50 (120 GUIDs -> 3 requests); `find_parameter` exact beats casefold, two casefold matches -> None; `read_classifications` parses the official nested shape and the Tapir flat shape, all-zero GUID -> None, falls back to official when `tapir_command_available` is false; `send_items` with a raise on batch 2 of 3 -> outcomes `ok`, `unanswered`, `not_sent` in the right slices and `stopped` set; `tapir_requirement` names the version from the registry; the existing `test_send_*` tests in `tests/test_property_writes.py` still pass.
- [ ] **Step 2: Run** `uv run pytest tests/test_element_kinds.py tests/test_property_writes.py -q`; expected FAIL.
- [ ] **Step 3: Implement** `element_kinds.py`, move `_version` and rebase `send_property_writes`.
- [ ] **Step 4: Run** `uv run pytest -q`; expected PASS (modify tests included).
- [ ] **Step 5: Commit** `feat: readers and a shared sender for element data kinds`

### Task 5: get_element_data reads every kind

**Files:**
- Modify: `src/archicad_mcp/core/element_data.py` (`get_element_data`)
- Test: `tests/test_element_data_reads.py`

**Interfaces:**
- Consumes: Tasks 3 and 4.
- Produces: `get_element_data(conn, guids, properties=None, include_classifications=False) -> dict`; `WILDCARD_ELEMENT_LIMIT = 20`. Returns `{"error": reason}` for an invalid address, an unknown classification system (listing the systems), or a wildcard with more than 20 GUIDs. Per element: existing keys; `properties` keyed by address text with wildcards expanded (`gdl:<name>`, `marker:<name>`, `ifc:<Pset>.<Name>`, `classification:<label>`), Hidden-flagged parameters left out of wildcards; `labels` (wildcard expansions only, displayName differing from the name); `unreadable` keyed by kind (`"gdl"`, `"marker"`, `"ifc"`) with the read error or `tapir_requirement` text. Classification values are the item code (`index.items[guid].code`), None when unclassified, the GUID when the index lacks it.

- [ ] **Step 1: Write tests** with a FakeCore: property-only call returns exactly the old shape (no `labels`/`unreadable` keys); `gdl:A` and `gdl:a` on an Object read 0.9; `gdl:*` lists only non-Hidden parameters and gives `labels`; a Wall gets `unreadable["gdl"]` and None values; `marker:*` on a door; `ifc:Pset_DoorCommon.FireRating`; `classification:ARCHICAD Classification` returns the code `"Door"`; no Tapir -> `unreadable` carries the install hint; 21 GUIDs with `gdl:*` -> `{"error"}`; `"ifc:bad"` -> `{"error"}`.
- [ ] **Step 2: Run** `uv run pytest tests/test_element_data_reads.py -q`; expected FAIL.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** `uv run pytest -q`; expected PASS.
- [ ] **Step 5: Commit** `feat: get_element_data reads classifications, GDL, marker and IFC values`

### Task 6: Property readback shared with apply_changeset

**Files:**
- Modify: `src/archicad_mcp/core/element_data.py` (new `read_back_properties`; `plan_property_writes` gains `refusals`)
- Modify: `src/archicad_mcp/scripting/apply.py` (`_read_back`, `_same` move out)
- Test: `tests/test_property_writes.py`, `tests/test_scripting_changesets.py`

**Interfaces:**
- Produces: `read_back_properties(conn, writes: list[dict], failed: list[dict]) -> list[dict]` (body of `scripting.apply._read_back`, returning `{guid, property, sent, read}` mismatches); `same_property_value(read, sent) -> bool` (the old `_same`); `plan_property_writes(conn, changes, refusals: dict[str, str] | None = None)` uses the given map instead of calling `editability.refusals`.
- `scripting/apply.py` imports both and its behaviour is unchanged.

- [ ] **Step 1: Write test** `test_plan_uses_given_refusals_without_reading_them` (no FilterElements call when `refusals={}` is passed).
- [ ] **Step 2: Run** `uv run pytest tests/test_property_writes.py -q -k given_refusals`; expected FAIL.
- [ ] **Step 3: Implement** the move and the parameter.
- [ ] **Step 4: Run** `uv run pytest -q`; expected PASS (scripting tests included).
- [ ] **Step 5: Commit** `refactor: share the property readback between set_element_data and apply_changeset`

### Task 7: set_element_data planning and dry run

**Files:**
- Create: `src/archicad_mcp/core/element_writes.py`
- Modify: `src/archicad_mcp/core/element_data.py` (`set_element_data` delegates)
- Test: `tests/test_element_writes.py`; update `tests/test_property_writes.py`

**Interfaces:**
- Consumes: Tasks 3, 4, 6; `editability.refusals`; `ClassificationIndex.load`; `tapir_attributes`; `extract._fetch_types`.
- Produces:
  - `@dataclass class Change: order: int; guid: str; address: Address; value: object; ifc_type: str | None = None; current: object = None; new: object = None; send: object = None; target: object = None; warning: str | None = None; entry: dict | None = None` (`send` in Tapir units, `target` what the readback must find: tool-unit value, attribute index, item GUID, or the IFC value).
  - `@dataclass class Plan: changes: list[Change]; skipped: list[dict]; property_planned: list[dict]`.
  - `plan(conn, changes: list) -> Plan`: validation, duplicate handling (key `(guid.upper(), kind, key.casefold() for gdl/marker else key)`), one `editability.refusals` call, then per kind. Skips are `{"guid", "property", "reason"}` in input order.
  - `dry_run_result(plan: Plan) -> dict`: `{"dry_run": True, "planned_changes": [{guid, property, current, new, warning?}], "skipped"?}` in input order.
- Kind rules (spec "The kinds"): GDL writes only on Object, Lamp, Door, Window, Skylight; A/B on Object/Lamp gated on `tapir_version < (1, 6, 0)`; marker needs the 1.7.1 commands; IFC needs SetIFCPropertiesOfElements and `conn.archicad_version() < 28` (None counts as unknown: allowed, Archicad's answer decides); classification value `None` unclassifies, a code resolves within the named system, then a GUID of an item in that system; a property skipped as "could not determine the property's value type" on an element that has a classification change in the call gets the "send the classification first, then this property in a second call" hint.

- [ ] **Step 1: Write tests**: a mixed call (property, classification, `gdl:A` on an Object, `marker:iNumber` on a door, `ifc:` on Archicad 29) dry-runs to four planned changes and one IFC skip with the Archicad 28 reason, and sends nothing (no `Set*` command in `core.calls`); a single Angle shows radians in `new` and `send` holds degrees; `gdl:A` with `tapir_version` 1.5.9 on an Object -> skipped with the update hint, on a Door -> planned with the opening-size warning; `gdl:` on a Wall -> skipped; a Material by name shows the name in `current`/`new` and `target` is the index; a hidden-layer element skips all its changes with one reason; conflicting duplicates skip both, identical ones collapse; a classification code shared by two items -> skipped "address it by GUID"; `null` on `gdl:` -> skipped; `ifc_type` on a property address -> skipped; the classification-then-property hint (Review Focus 5); the old property dry run test cases updated to the new shape (skips now listed).
- [ ] **Step 2: Run** `uv run pytest tests/test_element_writes.py tests/test_property_writes.py -q`; expected FAIL.
- [ ] **Step 3: Implement** `plan`, `dry_run_result`, and make `set_element_data(conn, changes, dry_run=True)` return `dry_run_result(plan(...))` on a dry run.
- [ ] **Step 4: Run** `uv run pytest -q`; expected PASS.
- [ ] **Step 5: Commit** `feat: set_element_data plans classifications, GDL, marker and IFC changes`

### Task 8: Commit, readback and result

**Files:**
- Modify: `src/archicad_mcp/core/element_writes.py` (`commit`)
- Test: `tests/test_element_writes.py`, `tests/test_property_writes.py`

**Interfaces:**
- Consumes: Task 7 `Plan`, Task 4 `send_items`, readers, Task 6 `read_back_properties`.
- Produces: `commit(conn, plan: Plan) -> dict` with keys from the spec ("Readback and result"): `applied`, `not_applied` (`[{"property", "count", "sample": [{"guid", "sent", "read"}]}]`, largest first, sample of `FAILURE_SAMPLE`), `failed` (`group_failures`), `skipped`, `not_sent` (`{"count", "guids"}` capped by `cap_list`), `unconfirmed` (`{"count", "guids", "note"}`), `stopped` (`error_fields`). GDL and marker items: one per element with all its parameters; a failed element fails each of its changes with Tapir's message.

- [ ] **Step 1: Write tests**: all kinds land -> `applied` equals the number of changes and no other keys besides `dry_run`; a GDL value the fake "Parameter Script" replaces -> `not_applied` with `sent` and `read`; Tapir refuses one door's GDL entry -> each of its changes in `failed` with the message; the GDL send raises 4001 on its second batch -> earlier classification and property writes stay applied, the raising batch's changes that the fake applied count as applied, the rest under `not_sent`, `stopped` set; the readback read raising -> `unconfirmed` with the count; the single Angle reaches the fake SetGDLParametersOfElements in degrees and an Angle array in radians; marker Angle in radians; `test_set_element_data_keeps_the_count_when_a_batch_fails` updated to `{"dry_run": False, "applied": 500, "not_sent": {"count": 500, "guids": [...first 50]}, "stopped": ...}` with `_conn_with_cells` now applying successful writes to its cells.
- [ ] **Step 2: Run** `uv run pytest tests/test_element_writes.py tests/test_property_writes.py -q`; expected FAIL.
- [ ] **Step 3: Implement** `commit` and wire `set_element_data(..., dry_run=False)` to it.
- [ ] **Step 4: Run** `uv run pytest -q`; expected PASS.
- [ ] **Step 5: Commit** `feat: set_element_data sends every kind and reads back what landed`

### Task 9: Tool descriptions and docs

**Files:**
- Modify: `src/archicad_mcp/server.py` (`get_element_data`, `set_element_data` descriptions)
- Modify: `manifest.json`, `scripts/build_dashboard.py` (descriptions; `WRAPPED_BY` gains GetGDLParametersOfElements, SetGDLParametersOfElements, GetMarkerParametersOfElements, SetMarkerParametersOfElements, GetClassificationsOfElements, SetClassificationsOfElements, API.SetClassificationsOfElements, SetIFCPropertiesOfElements, GetAttributesByType where missing)
- Modify: `docs/query.md` (the address section), `README.md` (tool list line if it describes set_element_data)
- Test: `tests/test_manifest.py`, `tests/test_server_smoke.py`

- [ ] **Step 1: Write** both descriptions: the address table in one sentence each kind, units (metres, radians), the wildcard limit, `labels`/`unreadable`, IFC on Archicad 27 and older only, the result keys including `not_applied`, `not_sent`, `unconfirmed`.
- [ ] **Step 2: Run** `uv run pytest -q` and `uv run python scripts/build_dashboard.py --help` (or its check mode if it has one); expected PASS.
- [ ] **Step 3: Commit** `docs: describe the element data addresses`

### Task 10: Live check and PR

Follow the spec's "Live check" list on MCP-Test (port 19723) after `list_instances` shows `project_name` MCP-Test, using a dev server from this worktree (`uv run archicad-mcp` through the gateway, or a short script with `ArchicadConnection(19723)` calling `core.element_data`). Save the project first (SaveProject). Place test elements near the origin, delete them after. Record the findings (angle unit, Tapir classification read shape, unclassified answer, marker parameters of the office door marker) in the spec under a "Live findings" section, fix anything the live run contradicts with a test first, then push the branch and open the PR (no auto-merge).
