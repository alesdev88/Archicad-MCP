# find_collisions and apply_favorites Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two curated tools, `find_collisions` (with a `no-collisions` QA rule type) and `apply_favorites`, over Tapir 1.7.1 commands.

**Architecture:** Core logic in two new modules, `core/collisions.py` and `core/favorites.py`, registered in `server.py` like the other full-mode tools. The rule type plugs into the existing snapshot pipeline: rules declare a `collision_query`, `build_snapshot` answers them into `ModelSnapshot.collisions`.

**Tech Stack:** Python 3.11, FastMCP, multiconn_archicad, pytest with `tests.conftest.FakeCore`.

**Spec:** `docs/superpowers/specs/2026-10-19-collisions-favorites-design.md`

## Global Constraints

- Never an em or en dash in code, docs, commits or chat.
- Lengths in metres, volume tolerance in m3, surface tolerance in m2.
- GUIDs compared upper case (Tapir answers upper case).
- Report lists capped with `core.element_data.cap_list` (REPORT_CAP 50), overflow as `not_shown`.
- Tool descriptions say what the tool cannot see; results never claim more than a read confirmed.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- Lowercase GUIDs passed in: pairs and skips must still match them (Task 1, Task 4 tests use lowercase input).
- An Archicad 30 answer spelt `hasClearanceCollision`: read as clearance, not dropped (Task 1).
- The same element in both groups, or Archicad answering a mirrored pair: one pair, never a self pair (Task 1).
- An element whose ID was empty before the favourite: `keep=["id"]` writes the empty ID back (Task 5).
- A favourite name that differs only in case: not applied silently; the error names the exact favourite (Task 4).

---

### Task 1: Collision core

**Files:**
- Create: `src/archicad_mcp/core/collisions.py`
- Test: `tests/test_find_collisions.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class CollisionQuery: element_types: tuple[str, ...]; against_types: tuple[str, ...] | None; volume_tolerance: float = 0.001; surface_tolerance: float | None = None`
  - `@dataclass(frozen=True) class Collision: element: str; against: str; body: bool; clearance: bool` with property `kind -> str` ("body", "clearance", "body and clearance")
  - `resolve_types(names: list[str]) -> tuple[str, ...]` raising `ValueError` with the valid names
  - `elements_of_types(conn, types: tuple[str, ...]) -> list[str]`
  - `detect(conn, group: list[str], against: list[str] | None, volume_tolerance: float, surface_tolerance: float | None) -> list[Collision]`
  - `answer(conn, query: CollisionQuery) -> list[Collision]` (types to GUIDs, then `detect`)
  - `find_collisions(conn, guids, element_types, against_guids, against_types, volume_tolerance=0.001, surface_tolerance=None) -> dict`

- [ ] **Step 1: Write the failing tests** in `tests/test_find_collisions.py` with a FakeCore whose `GetCollisions` returns canned pairs and records the request:
  - `test_settings_always_sent`: `detect(..., 0.001, None)` sends `{"volumeTolerance": 0.001, "performSurfaceCheck": False, "surfaceTolerance": 0.001}`; with `surface_tolerance=0.02` sends `performSurfaceCheck: True, surfaceTolerance: 0.02`.
  - `test_same_group_sent_twice`: `against=None` sends group 1 as `elementsGroup2`.
  - `test_clearance_spelt_both_ways`: answers with `hasClearenceCollision` and with `hasClearanceCollision` both give `clearance=True`.
  - `test_self_and_mirrored_pairs_dropped`: answer `[(A,B),(B,A),(A,A)]` gives one `Collision(A, B)`.
  - `test_resolve_types`: `["walls", "BEAM"]` gives `("Wall", "Beam")`; `["Wal"]` raises ValueError naming "Wall".
  - `test_find_collisions_report`: two groups by GUID (lowercase input), details read gives types, IDs and floorIndex; result has `collisions == 2`, `pairs[0]` keys `element, element_type, element_id, against, against_type, against_id, story, kind`, `by_type_pair == [{"types": "Morph / Wall", "count": 2}]`, `elements` lists the three GUIDs upper case, `checked == {"elements": 1, "against": 2}`.
  - `test_unknown_guid_listed`: a GUID the details read answers with an error is in `not_found` and not sent.
  - `test_no_groups_is_error`: no guids and no types gives `{"error": ...}` naming both parameters.
  - `test_pairs_capped`: 60 pairs give 50 in `pairs` and `not_shown == 10`.
- [ ] **Step 2: Run** `uv run pytest tests/test_find_collisions.py -q`; expected: import errors.
- [ ] **Step 3: Implement** `core/collisions.py`. Type names from `core.query_schema.element_type_names()` (drop "all"), matched without case, one trailing `s` stripped. Details read with fields `["type", "id", "floorIndex"]` in `PROPERTY_FETCH_CHUNK` chunks. `kind` order in `by_type_pair`: the two type names sorted and joined with " / ", groups largest first.
- [ ] **Step 4: Run** the tests; expected: PASS.
- [ ] **Step 5: Commit** `feat: collision detection core for find_collisions`.

### Task 2: find_collisions tool

**Files:**
- Modify: `src/archicad_mcp/server.py` (register after `show_elements`), `src/archicad_mcp/tapir_requirements.py` (`TOOL_COMMANDS["find_collisions"] = ("GetCollisions", "GetElementsByType")`), `manifest.json` (tools list), `README.md` (tool list and a section after `show_elements`)
- Test: `tests/test_find_collisions.py`, `tests/test_tool_annotations.py` only if it lists read-only tools

**Interfaces:**
- Consumes: `collisions.find_collisions` from Task 1.
- Produces: MCP tool `find_collisions(guids: list[str] | None = None, element_types: list[str] | None = None, against_guids: list[str] | None = None, against_types: list[str] | None = None, volume_tolerance: float = 0.001, surface_tolerance: float | None = None, port: int | None = None) -> dict`, `_tool_meta("Find collisions", read_only=True)`, `_tapir_conn(port, "find_collisions")`.

- [ ] **Step 1: Write the failing test** `test_tool_registered_full_mode_only`: `build_server("full")` lists `find_collisions` with `readOnlyHint` true; `build_server("verdicts")` does not list it.
- [ ] **Step 2: Run** it; expected: FAIL.
- [ ] **Step 3: Register** the tool. Description: groups, tolerances and units, "Archicad does not count elements its own junctions clean up (crossing walls), and checks only closed solids whose building material takes part in collision detection: no pairs is not proof of no overlap", "pass `elements` to show_elements to show them", a whole-model check can take a while.
- [ ] **Step 4: Run** `uv run pytest -q`; expected: all pass (manifest test included).
- [ ] **Step 5: Commit** `feat: find_collisions tool`.

### Task 3: no-collisions rule type

**Files:**
- Create: `src/archicad_mcp/rules/builtin/collisions.py`
- Modify: `src/archicad_mcp/rules/types.py` (`ModelSnapshot.collisions: dict | None = None`), `src/archicad_mcp/rules/engine.py`, `src/archicad_mcp/rules/loader.py` (`RULE_TYPES["no-collisions"]`), `src/archicad_mcp/extract.py` (`build_snapshot(..., collision_queries: frozenset = frozenset())`), `src/archicad_mcp/server.py` (`_verdict_for` passes them), `README.md` (rule types section)
- Test: `tests/rules/test_collision_rule.py`

**Interfaces:**
- Consumes: `CollisionQuery`, `Collision`, `answer` from Task 1.
- Produces: `NoCollisionsRule` (fields `rule_id, severity, tags, query: CollisionQuery`, `needs = {"collisions"}`, `needed_properties = frozenset()`, `collision_query` property returning `query`, `from_config(cfg)`, `check(snapshot)`); `engine.collision_queries(rules) -> frozenset[CollisionQuery]`.

- [ ] **Step 1: Write the failing tests:**
  - `test_from_config`: the YAML in the spec loads with types resolved and tolerances; missing `element_types` raises `RuleConfigError`; an unknown type raises `RuleConfigError` naming it.
  - `test_check_fails_with_pairs`: snapshot with two pairs gives `passed False`, `failure_count 2`, `failing_guids` the three unique GUIDs, message names "2 collision(s)".
  - `test_check_passes_without_pairs`.
  - `test_skips_without_tapir`: `snapshot.collisions is None` gives `skipped True`.
  - `test_build_snapshot_answers_queries`: FakeCore with `GetElementsByType` and `GetCollisions`; `build_snapshot(conn, frozenset({"collisions"}), collision_queries=frozenset({q}))` fills `snapshot.collisions[q]`, and with no queries never calls `GetCollisions`.
  - `test_build_snapshot_without_getcollisions`: Tapir lacking the command gives `collisions is None`.
- [ ] **Step 2: Run** `uv run pytest tests/rules/test_collision_rule.py -q`; expected: FAIL.
- [ ] **Step 3: Implement.** `collision_queries` collects `getattr(rule, "collision_query", None)`. `build_snapshot` answers each query once only when `"collisions" in needs`, using `conn.tapir_command_available("GetCollisions")`.
- [ ] **Step 4: Run** `uv run pytest -q`; expected: all pass.
- [ ] **Step 5: Commit** `feat: no-collisions QA rule type`.

### Task 4: Favourites listing and dry run

**Files:**
- Create: `src/archicad_mcp/core/favorites.py`
- Test: `tests/test_apply_favorites.py`

**Interfaces:**
- Consumes: `core.editability.refusals`, `core.mutate._group`, `core.element_data.cap_list`.
- Produces:
  - `favorites_by_type(conn, types: list[str]) -> dict[str, list[str]]` (types with none left out)
  - `KEEPABLE = ("id", "layer")`
  - `@dataclass class FavoritePlan: favorite; favorite_types; requested; planned: list[dict] (guid, type, id, layerIndex); skipped: dict[str, str]; warnings: dict[str, list[str]]; flags: dict[str, bool]; keep: tuple[str, ...]; window`
  - `plan(conn, guids, favorite, flags, keep) -> FavoritePlan | dict`
  - `apply_favorites(conn, guids, favorite, settings, classifications, categories, properties, keep, to_defaults, dry_run) -> dict`

- [ ] **Step 1: Write the failing tests** (FakeCore: `GetFavoritesByType` per type, `GetDetailsOfElements`, `FilterElements` passing everything unless a test says otherwise):
  - `test_listing_for_guids`: no favourite, GUIDs of a wall and a door give `{"favorites": {"Wall": [...], "Door": [...]}}` and no write call.
  - `test_listing_all_types`: no GUIDs, no favourite: types with favourites only.
  - `test_type_mismatch_skipped`: "Partition Wall" on a door is skipped with "'Partition Wall' is not a Door favourite (it is a Wall favourite)".
  - `test_unknown_favourite_error`: "Partition wall" (wrong case) gives an error naming "Partition Wall"; nothing planned.
  - `test_refused_elements_skipped`: an element IsEditable refuses is skipped with the editability reason.
  - `test_warnings_per_type`: a wall plan warns about height, top link, reference line and structure; a door about library part, size and sill; every element about the layer; with `keep=[]` also the Element ID; with default keep no ID warning.
  - `test_bad_keep`: `keep=["height"]` gives an error naming `modify_elements`.
  - `test_to_defaults_dry_run`: `to_defaults=True`, no GUIDs: names the Wall tool; with GUIDs: error.
  - `test_lowercase_guids`: lowercase input plans the upper-case GUIDs.
- [ ] **Step 2: Run** `uv run pytest tests/test_apply_favorites.py -q`; expected: FAIL.
- [ ] **Step 3: Implement** listing, `plan` and the dry-run report (`dry_run, favorite, favorite_type(s), requested, planned_count, planned (capped), skipped (grouped with _group), warnings (grouped like modify._grouped_warnings), applies {settings, classifications, categories, properties}, keep, active_window`). Favourite types for the error and mismatch text come from scanning `favorites_by_type` over every element type once per call. Close names with `difflib.get_close_matches(favorite, all_names, n=3, cutoff=0.6)`, matching case-insensitively.
- [ ] **Step 4: Run** the tests; expected: PASS.
- [ ] **Step 5: Commit** `feat: apply_favorites listing and dry run`.

### Task 5: Favourites commit

**Files:**
- Modify: `src/archicad_mcp/core/favorites.py`
- Test: `tests/test_apply_favorites.py`

**Interfaces:**
- Consumes: Task 4's `FavoritePlan`; `core.modify_fields.ATTRIBUTE_FIELDS`; `core.attributes.layer_table`, `tapir_attributes`; `extract.resolve_property_ids`; `core.element_data.group_failures`.
- Produces: `commit(conn, plan: FavoritePlan) -> dict`; `diff(before: dict, after: dict, names) -> dict[str, dict]`.

- [ ] **Step 1: Write the failing tests** (FakeCore whose `ApplyFavoritesToElements` mutates a details store the way the live run did: ID to "SW - 001", layerIndex 54 to 33, height 2.4 to 3):
  - `test_commit_reports_changes`: `changed[guid]` holds `id`, `layer` (by name), `details.height`, `details.relativeTopStory`; `applied == 1`.
  - `test_keep_id_restores`: default keep writes `General_ElementID` back with `API.SetPropertyValuesOfElements` (`{"type": "string", "status": "normal", "value": "TEST-A"}`), `restored == {"id": 1}`, and `changed` has no `id`.
  - `test_keep_empty_id`: an element whose ID was "" gets "" written back.
  - `test_keep_layer_restores`: `keep=["id", "layer"]` sends `SetDetailsOfElements` with the old `layerIndex`.
  - `test_unchanged_reported`: an element the favourite already matched is in `unchanged`.
  - `test_failures_grouped`: execution results with -2130313115, -2130313113 and -2130312311 are grouped with the plain sentence for each.
  - `test_not_read_back_note`: the result says classifications and properties are not read back when those flags are on.
  - `test_to_defaults_commit`: calls `ApplyFavoritesToElementDefaults` with `{"favorites": ["Partition Wall"]}`.
- [ ] **Step 2: Run** the tests; expected: FAIL.
- [ ] **Step 3: Implement** `commit`: details before (fields `type, id, layerIndex, drawIndex, details`), one `ApplyFavoritesToElements` call with the four flags, restores for `keep` on elements whose read-after differs, details after, `diff` flattening nested dicts to dotted names, `layerIndex` shown as `layer` by name, `ATTRIBUTE_FIELDS` GUIDs shown by name. A stop of the apply call is reported as `stopped` with what the read-after shows.
- [ ] **Step 4: Run** the tests; expected: PASS.
- [ ] **Step 5: Commit** `feat: apply_favorites commit with keep and a change report`.

### Task 6: apply_favorites tool

**Files:**
- Modify: `src/archicad_mcp/server.py`, `src/archicad_mcp/tapir_requirements.py` (`TOOL_COMMANDS["apply_favorites"] = ("GetFavoritesByType", "ApplyFavoritesToElements")`), `tests/test_tool_annotations.py` (`WRITERS` gets `apply_favorites`), `manifest.json`, `README.md`
- Test: `tests/test_apply_favorites.py`

**Interfaces:**
- Consumes: `favorites.apply_favorites` from Tasks 4 and 5.
- Produces: MCP tool `apply_favorites(guids: list[str] | None = None, favorite: str | None = None, settings: bool = True, classifications: bool = True, categories: bool = True, properties: bool = True, keep: list[str] | None = None, to_defaults: bool = False, dry_run: bool = True, port: int | None = None) -> dict`; `keep=None` means `["id"]`. `_tool_meta("Apply favorites", read_only=False, destructive=True)`.

- [ ] **Step 1: Write the failing test** `test_apply_favorites_registered`: full mode lists it as destructive; verdicts mode does not.
- [ ] **Step 2: Run** it; expected: FAIL.
- [ ] **Step 3: Register** the tool with a description covering listing, dry run, what a favourite overwrites (from the spec), `keep`, the four flags and `to_defaults`.
- [ ] **Step 4: Run** `uv run pytest -q`; expected: all pass.
- [ ] **Step 5: Commit** `feat: apply_favorites tool`.

### Task 7: Live check and docs

**Files:**
- Modify: `docs/superpowers/specs/2026-10-19-collisions-favorites-design.md` (a "Live check" section)

- [ ] **Step 1:** Run the worktree's server against MCP-Test (check `project_name` first) through a short script calling the core functions; test elements near the origin.
- [ ] **Step 2:** `find_collisions` on the test walls and closed morphs, both settings; the `no-collisions` rule through `audit_delivery_readiness` with a temporary rules directory in the scratchpad.
- [ ] **Step 3:** `apply_favorites` dry run, commit with default keep (ID back to its old value), with `keep=["id","layer"]`, on a door and a window, and listing.
- [ ] **Step 4:** Delete the test elements; record results in the design doc; `uv run pytest -q` and `uv run ruff check` if configured.
- [ ] **Step 5: Commit** `docs: live check of find_collisions and apply_favorites`.
