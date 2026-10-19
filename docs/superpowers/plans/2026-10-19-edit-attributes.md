# edit_attributes and Teamwork object sets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A dry-run-by-default `edit_attributes` tool over Tapir's attribute commands that never overwrites by accident, `list_attributes` details, and Teamwork reservation of non-element object sets wired into the definition and attribute tools.

**Architecture:** Pure per-type tables (`core/attribute_types.py`) drive planning in `core/attribute_edit.py`; reads and reference naming live in `core/attributes.py`; reservation states in `core/teamwork.py`. Every write plans first, sends grouped by command, then reads back.

**Tech Stack:** Python 3, FastMCP, multiconn_archicad, jsonschema, pytest with `FakeCore`.

**Spec:** `docs/superpowers/specs/2026-10-19-edit-attributes-design.md`

## Global Constraints

- Tapir 1.7.1 for `object_sets` (`ReserveLockableObjectSet`); `TOOL_COMMANDS["edit_attributes"] = ("GetAttributesByType",)`; per-type commands through `require_tapir_command`.
- No em or en dashes anywhere (code, docs, commits).
- Live writes only on MCP-Test (port 19723, check `projectName == "MCP-Test"`) or MCP-Test_30, with `MCP-Canary` names created and deleted; never an existing attribute.
- Teamwork is unit-tested only; never write to CVP.
- Never stage the main checkout's `gdl-src/swept-beam/params.toml` (not in this worktree; do not copy it).

## Review Focus

1. A `create` whose name differs from an existing one only by case or a trailing space: planned with a near-name warning, never an overwrite (test `test_create_next_to_a_near_name_warns`).
2. A layer delete with a layout active: skipped, not counted as zero (test `test_a_layer_delete_needs_the_floor_plan_active`).
3. A composite modify that names only a skin thickness: `useWith`, other skins and separators survive (test `test_a_composite_modify_keeps_use_with_and_patches_skins_by_position`).
4. A whole Create command rejected by Archicad's schema: later commands not sent, listed under `stopped` (test `test_a_whole_command_refused_stops_the_rest`).
5. A reference by name to an attribute created in the same call: resolved on commit after the create (test `test_a_reference_to_an_attribute_created_in_the_same_call_resolves_on_commit`).

---

### Task 1: Teamwork object sets in reserve/release (done, 071f8de)

**Files:** `src/archicad_mcp/core/teamwork.py`, `src/archicad_mcp/server.py`, `tests/test_teamwork_object_sets.py`

**Interfaces (produced):** `canonical_set(name) -> str`, `reserve_call(set_name) -> str`, `SetState(name, status, held_by, error)`, `SetProblem(text, blocking)`, `object_set_states(conn, names) -> dict[str, SetState] | None`, `set_problem(state) -> SetProblem | None`, `teamwork_entries(states, needed_for) -> list[dict]`, `reserve_elements(conn, guids, confirm=False, object_sets=None)`, `release_elements(...)` same, `DEFINITIONS_SET = "ClassificationsAndProperties"`.

- [x] Tests for aliases, each status row, read-back, conflicts, 1.7.1 gate, tool params; implementation; commit.

### Task 2: Definition tools name the set (done, 071f8de)

**Files:** `core/definition_edit.py` (`definitions_reservation(conn, targets) -> (teamwork list | None, hint | None)`, `with_hint(groups, hint)`), `core/classification_edit.py`, `tests/test_definition_teamwork.py`, `tests/fixtures/definition_edit_replays.py` (`teamwork=` option).

- [x] Tests; implementation (no check when nothing will be sent); commit.

### Task 3: list_attributes details (done, a245873)

**Files:** `core/attribute_types.py` (`AttrType`, `TYPES`, `WRITE_ORDER`, `lookup_type`, `REFERENCE_FIELDS`, `LAYER_LIST`, `ENUMS`, `READ_ONLY_FIELDS`, `IDENTITY_FIELDS`), `core/attributes.py` (`list_attributes(conn, attribute_type, details=False, names=None, limit=None, offset=0)`, `read_records(conn, attr, guids) -> list[dict | None]`, `AttributeNames` with `headers/by_guid/by_index/by_name/label/forget`, `named_references(record, attr_type, names)`), `server.py`, `tests/test_list_attributes.py`, `tests/fixtures/attribute_replays.py`.

- [x] Tests; implementation; live read check on MCP-Test (all ten types answered, references named); commit.

### Task 4: edit_attributes planning (dry run)

**Files:**
- Create: `src/archicad_mcp/core/attribute_edit.py`
- Test: `tests/test_attribute_edit.py` (written; the dry-run tests), fixture `WritableStore` in `tests/fixtures/attribute_replays.py`

**Interfaces:**
- Consumes: Task 1 `object_set_states`, `set_problem`, `teamwork_entries`; Task 3 tables and `AttributeNames`, `read_records`, `named_references`; `definition_edit.Definitions`, `group_by_error`; `element_data.error_fields`; `editability.active_window`; `tapir_requirements.require_tapir_command`.
- Produces: `edit_attributes(conn, changes: list[dict], dry_run: bool = True) -> dict`; `field_lists() -> dict` (the `changes=[]` answer).

- [x] **Step 1:** Run `uv run pytest tests/test_attribute_edit.py -q`; expected: ImportError (red).
- [x] **Step 2:** Implement planning per the spec's "Dry run" steps 1 to 11, in this order per change: shape, Tapir commands for the action, address or collision (exact, GUID, near name by `" ".join(name.split()).casefold()`), built-ins (index 1 of Layer/Line/Fill, Solid/Empty fills, last composite), refused modify fields, merge (`skins`/`separators` by position, `layers` by layer name, `pens` by index, dict fields shallow-merged, `texture` whole), per-type extras from the spec table, enums from `ENUMS`, references to Tapir form (pending names of same-call creates get placeholder `00000000-0000-0000-0000-000000000000` / index 0), whole-float to int by schema, `jsonschema` item validation, layer delete (floor plan active, count by `layerIndex`), properties (String compared only), nothing to change; then Teamwork across all plans (layer flag-only modifies and property-only modifies advisory).
- [x] **Step 3:** Diff paths: `a.b`, positional `skins[1].thickness`, keyed `pens[5].width`, `layers[<layer name>].isLocked`.
- [x] **Step 4:** Run the dry-run tests; expected: pass.

### Task 5: edit_attributes commit and read-back

**Files:** `core/attribute_edit.py`, `tests/test_attribute_edit.py` (commit tests)

- [x] **Step 1:** Batches in `WRITE_ORDER`, creates (`overwriteExisting: false`) then modifies (`true`) per type; payloads re-resolved by name before each batch (`AttributeNames.forget` for types written); then `SetPropertyValuesOfAttributes`; then `DeleteAttributes` (`{"attributeType", "attributeId": {"attributeId": {"guid"}}}`).
- [x] **Step 2:** Per-item answers to `applied` / `failed` (grouped with `group_by_error`, `{}` property answer = "not written: ..."); an `APIErrorBase` on a command sets `error` (via `error_fields`), lists that batch under `failed`, later plans under `stopped` as `{action, type, target}`.
- [x] **Step 3:** Read back: creates by name, modifies by GUID; `now` = asked keys (creates also `name`); `not_applied` entries `{type, target, field, sent, read}` from the same diff; deletes verified gone.
- [x] **Step 4:** Run `uv run pytest tests/test_attribute_edit.py -q`; expected: all pass. Commit.

### Task 6: Tool wiring and docs

**Files:** `server.py` (tool `edit_attributes`, destructive, uses `_tapir_conn(port, "edit_attributes")`), `tapir_requirements.py` (`"edit_attributes": ("GetAttributesByType",)`), `tests/test_tool_annotations.py` (`WRITERS`), `manifest.json`, `scripts/build_dashboard.py` (`WRAPPED_BY` for the attribute and lockable set commands) and regenerated `docs/api-dashboard.html`, `README.md`, `docs/known-issues.md`.

- [x] **Step 1:** Add the tool test already in `tests/test_attribute_edit.py`; run full suite red then green: `uv run pytest -q`.
- [x] **Step 2:** Docs; `uv run python scripts/build_dashboard.py`; full suite green; commit.

### Task 7: Live verification (MCP-Test)

- [x] Follow the spec's Verification list with a script in the scratchpad that imports the worktree's `core` functions, checks `projectName == "MCP-Test"`, uses `MCP-Canary` names and deletes them in a `finally`. Record results in the spec's Verification section and commit.

### Task 8: PR

- [x] Push `feature/edit-attributes`, open the PR against `main` (no auto-merge), wait for CI green.
