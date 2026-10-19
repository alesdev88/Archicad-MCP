# modify_elements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A curated, dry-run-by-default `modify_elements` MCP tool over Tapir `SetDetailsOfElements` and the 13 `Modify*` commands, with current -> new per element, sharpened refusals (locks, groups), and a readback after the commit.

**Architecture:** Three new modules under `src/archicad_mcp/core/`: `modify_fields.py` holds the pure field knowledge (which command, which read, what to complete, what to refuse, what to warn), `modify.py` plans a call (reads, resolves names, validates, checks editability) and builds the dry-run report, `modify_commit.py` sends a plan in Tapir's order and reads it back. `editability.py` is split so callers get the kind of a refusal, and gains `lock_reasons()`.

**Tech Stack:** Python 3.12, FastMCP, multiconn-archicad, jsonschema, pytest.

**Spec:** `docs/superpowers/specs/2026-10-19-modify-elements-design.md`. Read it first; this plan cites its sections instead of repeating them.

## Global Constraints

- No em dashes or en dashes anywhere: code, comments, docs, commit messages.
- Tapir minimum 1.5.9; the tool targets Aleš's 1.7.1. Do not sync the bundled Tapir definitions in this branch (they stay 1.5.9).
- Never touch the main checkout (`/Users/alesd/Developer/Archicad MCP`): it holds Aleš's uncommitted Tapir 1.7.0 definitions and a swept-beam `params.toml` change. Work only in this worktree.
- Every `read_text` / `write_text` passes `encoding="utf-8"` (Windows CI).
- Commits as `Aleš Dolenec <285164556+alesdev88@users.noreply.github.com>`, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- No live write against Archicad without `list_instances` showing `MCP-Test` and Aleš's go-ahead. Dry runs and reads are fine on MCP-Test.
- Units: model lengths in metres, angles in radians; text and label sizes in millimetres (spec, "A change").
- GUID lists in reports are capped at 50 (`element_data.REPORT_CAP`) with `not_shown`.
- Run the whole suite with `uv run pytest -q`; baseline is 1001 passed, 1 skipped.

## Review Focus

1. A caller sends lowercase GUIDs. Tapir answers uppercase, so every set lookup by GUID (editability, groups, lock reads) misses and every element looks refused. Expected: GUIDs are uppercased on entry and the report uses the uppercased form. Test in Task 3 (`test_lowercase_guids_plan_like_uppercase`).
2. The active window is a Layout. Expected: every model element is skipped with a reason that leads with the Layout window, `active_window` is "Layout", nothing is sent. Test in Task 3 (`test_layout_window_refuses_with_the_window_named`).
3. An element type with no Modify command and no fixture (CurtainWall, Zone) gets only a layer change. Expected: planned through SetDetailsOfElements alone. Test in Task 3 (`test_layer_only_change_on_a_type_without_a_command`).
4. An empty `changes` list, or a change holding only `guid` and `keep_elevation: false`. Expected: the first returns a report with `requested: 0` and makes no reads; the second is skipped as "nothing to change". Test in Task 3 (`test_empty_and_fieldless_changes`).
5. A commit where Archicad raises in the middle (connection drop). Expected: what landed is counted, `stopped` is set, Suspend Groups is restored, the rest is under `not_sent`. Test in Task 4 (`test_raising_command_stops_and_reports`, `test_suspend_groups_restored_when_move_raises`).

---

### Task 1: Editability kinds, lock reasons, layer table, recorded fixtures

**Files:**
- Modify: `src/archicad_mcp/core/editability.py`
- Modify: `src/archicad_mcp/core/attributes.py`
- Create: `tests/fixtures/modify_elements/` with `__init__.py` (empty) and the JSON files below
- Test: `tests/test_editability_locks.py`

**Interfaces:**
- Produces in `editability.py`:
  - public constants `NOT_FOUND = "not_found"`, `HIDDEN = "hidden"`, `UNRESERVED = "unreserved"`, `LOCKED = "locked"` (rename the private ones; values unchanged).
  - `classify(conn, guids: list[str]) -> tuple[dict[str, str], str | None]`: guid -> kind for every refused guid, and the active window (None when nothing is refused or it cannot be read). The body of today's `refusals()` up to the reason text.
  - `reason_text(kind: str, window: str | None) -> str`: today's `_reason`, renamed.
  - `refusals(conn, guids)`: unchanged signature and output, now `kinds, window = classify(...)` plus `reason_text`.
  - `lock_reasons(conn, entries: dict[str, dict], layers: dict[int, dict]) -> dict[str, str]`: guid -> reason for elements inside a hotlinked module (`entries[g]` has `hotlinkId`), on a locked layer (`layers[entries[g]["layerIndex"]]["isLocked"]`), or with `General_Locked` true (one `fetch_property_values(conn, guids, ["General_Locked"])` for all of `entries`). Checked in that order; first match wins. If `General_Locked` does not resolve, or `PropertyFetchTooWideError` is raised, the element-lock check is skipped silently.
  - Reason copy (tests match the quoted fragments):
    - hotlink: `"inside a hotlinked module; Archicad refuses changes to it. Change the module's source file, or break the hotlink"`
    - layer: `f"on the locked layer '{name}'; unlock the layer (Layer Settings) and retry"`
    - element: `"locked with Edit > Locking; unlock it there and retry (this tool does not unlock)"`
- Produces in `attributes.py`:
  - `tapir_attributes(conn, attribute_type: str) -> list[dict]`: `[{"index": int, "name": str, "guid": str}]` from Tapir `GetAttributesByType`.
  - `layer_table(conn) -> dict[int, dict]`: index -> `{"name", "guid", "isHidden", "isLocked"}`, from `tapir_attributes(conn, "Layer")` plus one Tapir `GetLayers` call with `fields: ["isHidden", "isLocked"]`.

- [ ] **Step 1: Record the fixtures**

Write these files under `tests/fixtures/modify_elements/`, each a JSON object with a `"_source"` string and the payload. Recorded ones come from MCP-Test on 2026-10-19 (AC 29/5101, Tapir 1.5.10) and are already in this session's transcript; re-read them with `execute_read_api_command` on MCP-Test if needed (reads only, check `list_instances` first).

| File | Payload | Source |
|---|---|---|
| `details_recorded.json` | `GetDetailsOfElements` items for slab `05C7A02F-4251-6F40-8407-67893BEFF6AC` (SLA - 001), slab `8BD33B9C-5B7D-5C46-8B28-9EC1CB9310CC` (SLA - 002, story 1), object `5349C8FC-A4BF-3548-A614-77BEB2CE4261` (FU - 003), mesh `D4DB880C-8083-F749-B27F-0886B2D6044B` (TR - 005), keyed by GUID, plus `"not_found"`: the error item | recorded |
| `layers_by_type.json` | Tapir `GetAttributesByType` Layer answer (all 40 layers) | recorded |
| `get_layers.json` | Tapir `GetLayers` items for layers 33 and 56 | recorded |
| `stories.json` | Tapir `GetStories` answer (3 stories, levels 0, 3, 6) | recorded |
| `groups.json` | `{"ungrouped": <the error item>, "grouped": {"groupId": {"guid": "GROUP-1"}}}` | recorded error; grouped from the schema |
| `details_schema.json` | items for `WALL-1` (Wall, Straight, story 0, `begThickness` 0.3, `visibility` all four keys), `WALL-2` (Wall, Trapezoid), `DOOR-1` (Door, `ownerElementId` WALL-1), `WINDOW-1`, `ROOF-M` (Roof, `roofClass` MultiPlane, `zCoordinate` 3.5, `level` 0.5, floor 1), `ROOF-S` (SinglePlane), `TEXT-1` (Text, `position`, `zCoordinate` 0), `MORPH-1` (Morph, `origin` {1,2,0}), `BEAM-1` (Beam, `level` 0.2), `CW-1` (CurtainWall, details `{}`), `HATCH-1` (Hatch) | written from the bundled `WallDetails`, `WindowDoorDetails`, `RoofDetails`, `TextDetails`, `MorphDetails`, `BeamDetails` schemas; `_source` says so |

- [ ] **Step 2: Write the failing tests** in `tests/test_editability_locks.py`

```python
from tests.test_mutate import Project   # the editability fake from PR #10

def test_classify_names_the_kind_of_each_refusal():
    p = Project(floor_plan=["A", "B", "C", "D"], hidden={"B"}, locked={"C"},
                teamwork=True, mine={"A", "B", "C"})
    kinds, window = classify(p.connect(), ["A", "B", "C", "D", "NOPE"])
    assert kinds == {"B": HIDDEN, "C": LOCKED, "D": UNRESERVED, "NOPE": NOT_FOUND}
    assert window == "FloorPlan"

def test_refusals_text_is_unchanged():
    # reason_text(LOCKED, "FloorPlan") still says "hotlinked module" (the
    # move/delete tests depend on it) and a Layout window leads the text.

def test_lock_reasons_hotlink_then_layer_then_element_lock():
    # entries: "H" hotlinkId + layer 33; "L" layer 56 (locked); "E" layer 33,
    # General_Locked true; "OK" layer 33. layers from layer_table over the fixtures
    # with isLocked forced True for 56.
    assert "hotlinked module" in out["H"]
    assert "'- Hidden'" in out["L"] and "locked layer" in out["L"]
    assert "Edit > Locking" in out["E"]
    assert "OK" not in out

def test_lock_reasons_reads_general_locked_once():
    # exactly one API.GetPropertyValuesOfElements call, 4 elements, 1 property

def test_lock_reasons_skips_the_element_lock_when_the_property_does_not_resolve():
    # API.GetPropertyIds answers no propertyId -> no "E" reason, no exception

def test_layer_table_from_recorded_answers():
    table = layer_table(conn)
    assert table[33]["name"] == "Structural - Bearing"
    assert table[3]["name"] == "Interior - Furniture "     # trailing space kept
    assert table[56]["isHidden"] is True and table[56]["isLocked"] is False
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/test_editability_locks.py -q`
Expected: FAIL (ImportError on `classify`, `lock_reasons`, `layer_table`).

- [ ] **Step 4: Implement the interfaces above.** `fetch_property_values` and `PropertyFetchTooWideError` come from `archicad_mcp.extract`.

- [ ] **Step 5: Run the new tests and the whole suite**

Run: `uv run pytest -q`
Expected: all pass; the move, delete and set_element_data tests unchanged.

- [ ] **Step 6: Commit** `refactor: editability names the kind of each refusal; lock reasons and layer table for modify_elements`

---

### Task 2: Field knowledge (`modify_fields.py`)

**Files:**
- Create: `src/archicad_mcp/core/modify_fields.py`
- Test: `tests/test_modify_fields.py`

**Interfaces:**
- Consumes: `gateway.registry.build_registry()` (resolved input schemas).
- Produces (all pure, no connection):
  - `TYPE_COMMANDS: dict[str, str]`: the 13 rows of the spec's "Element types and commands".
  - `LIST_KEYS: dict[str, str]`: command -> its top-level array key (`"ModifyWalls": "wallsWithDetails"`, ..., `"ModifyMeshes": "meshesData"`).
  - `MIN_TAPIR = (1, 5, 9)`, `DIMENSIONS_FIXED_IN = (1, 6, 0)`.
  - `COMMON_FIELDS = frozenset({"story", "layer", "draw_order", "keep_elevation"})`.
  - `FLAT_TYPES`: the 2D and annotation types the spec lists under `keep_elevation`, by Archicad type name (Text, Label, Dimension, RadialDimension, LevelDimension, AngleDimension, Hatch, Line, PolyLine, Arc, Circle, Spline, Hotspot, Drawing, Picture, Detail, Worksheet, Elevation, InteriorElevation, CutPlane, ChangeMarker).
  - `HOSTED_TYPES = frozenset({"Door", "Window", "Skylight", "Opening"})`; `OPENING_TYPES = frozenset({"Door", "Window", "Skylight"})`.
  - `ATTRIBUTE_FIELDS: dict[str, str]`, `INDEX_ATTRIBUTE_FIELDS = {"lineTypeIndex": "Line"}`, `SURFACE_OVERRIDES`, `PEN_OVERRIDES = frozenset({"cutFillPen", "cutFillBackgroundPen"})`: the spec's attribute table.
  - `item_schema(command: str) -> dict`: the per-item schema; for ModifyMeshes the `meshData` schema, so callers validate flat fields the same way for every command.
  - `allowed_fields(command: str) -> frozenset[str]`: item schema properties minus `elementId` and `floorIndex`.
  - `refusal(command: str, field: str, fields: dict, entry: dict, tapir: tuple[int, int, int]) -> str | None`: the spec's skip reasons 4 for one field (`fields` is the whole change, for the "without polygonOutline" and "story with absolute z" cases; `entry` is the GetDetailsOfElements item, for `roofClass` and `useFixSize`).
  - `complete(command: str, field: str, value, entry: dict)`: the spec's completion table; dicts merge recursively, given values win; anything not in the table is returned unchanged.
  - `current_value(command: str, field: str, entry: dict)`: the spec's field table; `None` when there is no read.
  - `expected_read(command: str, field: str, sent, entry: dict)`: what a read shows after the write; morph `translation` is `origin + translation`, everything else `sent`.
  - `compared(command: str, field: str) -> bool`: False for list-valued geometry (`polygonOutline`, `polygonArcs`, `polygonCoordinates`, `holes`, `sublines`, `body`, `levels`, `runs`, `textureProjectionCoords`) and for fields `current_value` cannot read.
  - `absolute_z(command: str, field: str, value) -> float | None`: the z an absolute-z field sets (slab and column `zCoordinate`, roof `level`, object and lamp `coordinates["z"]`, text `coordinate["z"]`), else None.
  - `home_story(z: float, stories: list[dict]) -> int`: Tapir's rule: index of the highest story whose `level <= z`, stories in index order; 0 if none.
  - `side_effects(command: str, fields: dict, entry: dict) -> list[str]`: the spec's "Side effects" warnings that apply.
  - `summarize(value)`: lists of 4 or more items become `"<n> points"` when the items are coordinate dicts, else `"<n> items"`; a dict whose `json.dumps` exceeds 200 characters becomes `"an object of <n> fields"`; anything else unchanged.
  - `values_match(expected, actual, tol: float = 1e-6) -> bool`: recursive; numbers within `tol`; strings that both look like GUIDs compare case-insensitively; dict keys in `expected` only (extra keys in `actual` are ignored).

- [ ] **Step 1: Write the failing tests** in `tests/test_modify_fields.py`, loading entries from the Task 1 fixtures:

```python
def test_every_type_maps_to_a_command_in_the_bundled_definitions():
    assert set(TYPE_COMMANDS) == {"Wall", "Beam", "Slab", "Column", "Window", "Door",
        "Morph", "Roof", "Mesh", "Object", "Lamp", "Text", "Label"}
    assert all(item_schema(c)["properties"] for c in TYPE_COMMANDS.values())

def test_allowed_fields():
    assert {"level", "sublines"} <= allowed_fields("ModifyMeshes")
    assert not {"floorIndex", "meshData", "elementId"} & allowed_fields("ModifyMeshes")
    assert {"height", "thickness"} <= allowed_fields("ModifyWalls")

@pytest.mark.parametrize("command,field,fields,entry_key,fragment", [
    ("ModifyObjects", "offset", {"offset": {"x": 1, "y": 0}}, "object", "discards"),
    ("ModifyLamps", "lightColor", {"lightColor": {}}, "object", "discards"),
    ("ModifyMorphs", "rotationDegreesZ", {"rotationDegreesZ": 90}, "morph", "xAxis"),
    ("ModifySlabs", "polygonArcs", {"polygonArcs": []}, "slab", "polygonOutline"),
    ("ModifyMeshes", "holes", {"holes": []}, "mesh", "polygonCoordinates"),
    ("ModifyMeshes", "contourPen", {"contourPen": 0}, "mesh", "0 or less"),
    ("ModifyMeshes", "floorIndex", {"floorIndex": 1}, "mesh", "story"),
    ("ModifyRoofs", "thickness", {"thickness": 0.2}, "roof_single", "single-plane"),
])
def test_refused_fields_name_the_reason(...):
    assert fragment in refusal(command, field, fields, entry, (1, 7, 1))

def test_dimensions_refused_only_on_old_tapir_with_fix_size_off():
    # object fixture has useFixSize false
    assert "1.6.0" in refusal("ModifyObjects", "dimensions", f, obj, (1, 5, 10))
    assert refusal("ModifyObjects", "dimensions", f, obj, (1, 7, 1)) is None

def test_completion():
    assert complete("ModifyObjects", "coordinates", {"x": 5.0}, obj) == {"x": 5.0, "y": 0, "z": 0}
    assert complete("ModifyMorphs", "translation", {"z": 1.0}, morph) == {"x": 0, "y": 0, "z": 1.0}
    assert complete("ModifyTexts", "coordinate", {"x": 1.0}, text) == {"x": 1.0, "y": <position.y>, "z": <zCoordinate>}
    merged = complete("ModifyWalls", "visibility", {"showAllAbove": True}, wall)
    assert merged["showAllAbove"] is True and merged["showOnHome"] == wall["details"]["visibility"]["showOnHome"]
    ff = complete("ModifySlabs", "floorFill", {"orientation": {"matrix00": 2}}, slab)["orientation"]
    assert ff["matrix00"] == 2 and ff["matrix11"] == 1 and ff["type"] == "Global"
    assert complete("ModifySlabs", "thickness", 0.25, slab) == 0.25

def test_current_values_are_in_the_write_frame():
    assert current_value("ModifySlabs", "zCoordinate", slab2) == 3.25   # absolute, not level 0.25
    assert current_value("ModifyRoofs", "level", roof_m) == 3.5
    assert current_value("ModifyWalls", "thickness", wall) == 0.3
    assert current_value("ModifyObjects", "coordinates", obj) == {"x": 3.676672178, "y": 0, "z": 0}
    assert current_value("ModifyDoors", "reveal", door) is None

def test_expected_read_of_a_morph_translation_adds_the_origin():
    assert expected_read("ModifyMorphs", "translation", {"x": 1, "y": 0, "z": 0}, morph) == {"x": 2, "y": 2, "z": 0}

def test_absolute_z_and_home_story():
    stories = <stories fixture>["stories"]
    assert absolute_z("ModifyObjects", "coordinates", {"x": 0, "y": 0, "z": 2.5}) == 2.5
    assert absolute_z("ModifyBeams", "level", 0.5) is None
    assert [home_story(z, stories) for z in (2.5, 3.0, 3.25, 7.0, -1.0)] == [0, 1, 1, 2, 0]

def test_side_effects():
    assert any("removes the holes" in w for w in side_effects("ModifySlabs", {"polygonOutline": [...]}, slab))
    assert any("edge" in w for w in side_effects("ModifySlabs", {"holes": []}, slab))
    assert any("relativeTopStory" in w for w in side_effects("ModifyWalls", {"height": 3.0}, wall))
    assert any("start thickness" in w for w in side_effects("ModifyWalls", {"thickness": 0.2}, wall_trapezoid))
    assert not side_effects("ModifyWalls", {"thickness": 0.2}, wall)
    assert any("word wrap" in w for w in side_effects("ModifyTexts", {"text": "A"}, text))
    assert not side_effects("ModifyTexts", {"style": {"angle": 0.1}}, text)

def test_summarize_and_compared():
    assert summarize(mesh["details"]["polygonCoordinates"]) == "25 points"
    assert summarize(0.3) == 0.3 and summarize({"x": 1, "y": 2}) == {"x": 1, "y": 2}
    assert compared("ModifySlabs", "thickness") and not compared("ModifySlabs", "polygonOutline")
    assert not compared("ModifyDoors", "reveal")

def test_values_match():
    assert values_match(0.25, 0.2500000001)
    assert not values_match(0.25, 0.26)
    assert values_match({"guid": "a8836ee9-5c72-492b-a7f6-30efed26b3ee"}, {"guid": "A8836EE9-5C72-492B-A7F6-30EFED26B3EE"})
    assert values_match({"overridden": True}, {"overridden": True, "attributeId": {"guid": "X"}})
```

- [ ] **Step 2: Run them to verify they fail** (`uv run pytest tests/test_modify_fields.py -q`, ImportError).
- [ ] **Step 3: Implement the interfaces above.** Read the spec's field table, completion table, skip reasons 4 and side-effect list as the source of the values; keep each as a module-level table, not branches.
- [ ] **Step 4: Run** `uv run pytest tests/test_modify_fields.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: field knowledge for modify_elements (commands, reads, completion, refusals, side effects)`

---

### Task 3: Planning and the dry run (`modify.py`)

**Files:**
- Create: `src/archicad_mcp/core/modify.py`
- Create: `tests/fixtures/modify_project.py` (the fake)
- Test: `tests/test_modify_elements.py`

**Interfaces:**
- Consumes: Task 1 (`classify`, `reason_text`, `lock_reasons`, `layer_table`, `tapir_attributes`), Task 2 (everything), `element_data.cap_list`, `mutate._group`, `extract.element_payload`.
- Produces:
  - `@dataclass Planned`: `guid: str`, `type: str`, `id: str | None`, `entry: dict` (the details item), `story: int | None`, `layer: int | None`, `draw_order: int | None`, `keep_elevation: bool`, `command: str | None`, `item: dict` (type fields, completed and resolved, no `elementId`), `display: dict[str, dict]` (field -> `{"current", "new"}`), `expected: dict[str, object]` (field -> expected read; `story` -> index, `layer` -> index, `draw_order` -> int; Modify fields from `expected_read`), `level_shift: float` (new story level minus old; 0 without a story change).
  - `@dataclass Plan`: `requested: int`, `planned: list[Planned]`, `skipped: dict[str, str]`, `warnings: dict[str, list[str]]`, `window: str | None`, `stories: list[dict]`, `suspend_groups: bool | None`, `groups: dict[str, str]` (guid -> group GUID, grouped planned elements only), `tapir: tuple[int, int, int]`.
  - `plan_changes(conn, changes: list[dict]) -> Plan | dict`: a `Plan`, or `{"error": ...}` for a malformed call (a change that is not an object or has no string `guid`: "change 2 has no guid") or a missing / older Tapir ("modify_elements needs Tapir 1.5.9 or newer; this Archicad has 1.5.8. Releases: https://github.com/ENZYME-APD/tapir-archicad-automation/releases").
  - `report_dry_run(plan: Plan) -> dict`: the spec's "Dry run result" shape. Warnings grouped like `skipped` (`{"warning", "count", "guids"}`), keys with nothing in them left out.
  - `modify_elements(conn, changes: list[dict], dry_run: bool = True) -> dict`: plan, then `report_dry_run` or `modify_commit.commit` (Task 4; until then commit returns `{"error": "not implemented"}`, removed in Task 4).

Planning order (spec "What is skipped before anything is sent"): uppercase GUIDs, merge duplicates, refuse empty; one `GetDetailsOfElements` with the seven fields (fallback without `fields` is not needed: 1.5.9 is the minimum); `classify`; `layer_table`; `lock_reasons`; story, layer, draw-order and keep-elevation checks; per field `refusal`, attribute resolution (one `tapir_attributes` call per attribute type actually used), `complete`, then `jsonschema` validation of the finished item against `item_schema(command)`; `tapir_command_available(command)`; groups (`GetGroupsOfElements` for planned GUIDs, `GetSuspendGroupsMode` once if any is grouped); warnings (`side_effects`, re-home via `absolute_z` and `home_story`, story move or keep-elevation text, door and window draw order). The first reason per element wins.

Story move text: `f"keeps its height above the story, so it moves by {shift:+.2f} m"`; keep-elevation text: `f"stays at the same height; its offset from the story changes by {-shift:+.2f} m"`; re-home text: `f"moves from story {old} to story {new}: Tapir takes the story from the absolute z"`; grouped text: `f"is in a group (Suspend Groups is {'on' if s else 'off'}); only this element is sent, its group mates are not part of the request"`.

`tests/fixtures/modify_project.py` provides `class ModifyProject`, built from the Task 1 fixtures:
- state: `elements: dict[str, dict]` (GUID -> details item, deep-copied), `editable`, `hidden`, `teamwork`, `mine`, `window`, `locked_property: set[str]` (General_Locked true), `groups: dict[str, str]`, `suspend: bool`, `suspend_history: list[bool]`, `version: str` ("1.7.1"), `missing_commands: set[str]`, `attributes: dict[str, list[dict]]` (type -> `[{"index", "name", "guid"}]`, Layer from the fixture plus a Composite "Slab 250" and a Surface "Paint - White").
- read handlers for every command Task 3 calls; write handlers added in Task 4.
- `connect() -> ArchicadConnection` on a `FakeCore` (as `tests/test_mutate.Project.connect` does); `sent(command) -> list[dict]`.

- [ ] **Step 1: Write the failing tests** in `tests/test_modify_elements.py`. Each test builds a `ModifyProject`, calls `modify_elements(conn, changes)` (dry run) and asserts on the report. Names and the assertions that define them:

| Test | Asserts |
|---|---|
| `test_dry_run_shows_current_and_new_per_field` | slab SLA - 001, `layer: "Finish - Floor"`, `thickness: 0.25` -> `planned[0]["changes"] == {"layer": {"current": "Structural - Bearing", "new": "Finish - Floor"}, "thickness": {"current": 0.3, "new": 0.25}}`, `planned[0]["id"] == "SLA - 001"`, `planned_count == 1` |
| `test_dry_run_sends_nothing` | no call whose name starts with `Modify`, `SetDetails`, `Move`, `SetSuspend`, `Lock`, `Unlock` |
| `test_layer_names_match_exactly_then_trimmed_and_case_insensitive` | `"interior - furniture"` -> new `"Interior - Furniture "`; `"Nope"` -> skipped reason contains `"no layer named 'Nope'"` |
| `test_story_by_index_or_name` | `story: "Ground Floor"` plans index 0; `story: 7` skipped with `"no story 7"`; an empty-named story cannot be addressed by `""` |
| `test_story_change_warns_how_far_it_moves` | slab on story 0 -> `story: 1`: a warning contains `"moves by +3.00 m"`; same with `keep_elevation: True`: `"stays at the same height"` |
| `test_keep_elevation_rules` | `keep_elevation` without `story` skipped; on TEXT-1 skipped with `"no elevation"`; `"yes"` skipped |
| `test_unknown_field_lists_the_allowed_ones` | slab `height: 3` -> reason contains `"ModifySlabs has no field 'height'"` and `"thickness"` |
| `test_layer_only_change_on_a_type_without_a_command` | CW-1 `layer` -> planned, `command is None`; CW-1 `thickness` -> skipped with `"only story, layer and draw_order"` |
| `test_refusals_from_the_field_table` | object `offset`; morph `rotationDegreesZ`; DOOR-1 `story`; slab `story` + `zCoordinate`; ROOF-S `thickness`: each skipped with the Task 2 fragment |
| `test_attribute_names_are_resolved_and_shown_by_name` | slab `compositeId: "Slab 250"`: the plan's item holds `{"guid": <its guid>}`, display shows names both sides; `"Nope"` -> `"no Composite named 'Nope'"`; WALL-1 `sideMaterial: "Paint - White"` -> item `{"overridden": True, "attributeId": {"guid": ...}}`; WALL-1 `cutFillPen: {"penIndex": 5}` -> item `{"overridden": True, "penIndex": 5}`; mesh `lineTypeIndex: <a Line name>` -> item holds that line's integer index |
| `test_partial_coordinates_are_completed_and_rehoming_warned` | object on story 1 with origin z 2.5 (fixture copy): `coordinates: {"x": 5}` -> item `{"x": 5, "y": <y>, "z": 2.5}` and a warning contains `"moves from story 1 to story 0"` |
| `test_schema_failures_are_skips` | slab `thickness: 0` skipped (reason contains `"thickness"`); `draw_order: 15` skipped; `draw_order: 0` planned |
| `test_editability_and_lock_reasons` | hidden -> `"hidden layer"`; `locked_property` -> `"Edit > Locking"`; `hotlinkId` -> `"hotlinked module"`; layer 56 locked -> `"'- Hidden'"` |
| `test_layout_window_refuses_with_the_window_named` | window Layout, nothing editable: every element skipped, reason contains `"Layout"`, `active_window == "Layout"`, no write calls |
| `test_grouped_elements_warn_with_the_suspend_state` | `groups={"5349...": "GROUP-1"}`, suspend off: a warning contains `"Suspend Groups is off"` |
| `test_old_tapir_is_refused_before_any_read` | version "1.5.8" -> `{"error": ...}` containing `"1.5.9"`; no `GetDetailsOfElements` call |
| `test_object_dimensions_depend_on_the_tapir_version` | object `dimensions: {"x": 1.2}` with version "1.5.10" skipped (`"1.6.0"`); with "1.7.1" planned, item dims completed `{"x": 1.2, "y": 1, "z": 1.89}` |
| `test_duplicate_guids_merge_and_conflicts_skip` | two changes for the slab (`layer`, then `thickness`) -> one planned element with both; same field two values -> skipped `"two different values"` |
| `test_planned_list_is_capped` | 60 elements -> `len(planned) == 50`, `not_shown == 10`, `planned_count == 60` |
| `test_missing_command_is_a_skip` | `missing_commands={"ModifyMeshes"}` -> mesh `level: 0.3` skipped with `"ModifyMeshes"` and `"1.5.4"` |
| `test_malformed_change_refuses_the_call` | `[{"layer": "X"}]` -> `{"error": "change 1 has no guid"}`, no calls |
| `test_long_values_are_summarized` | mesh `polygonCoordinates` (25 points sent): display `current == "25 points"` |
| `test_lowercase_guids_plan_like_uppercase` | slab GUID lowercased: planned, and `planned[0]["guid"]` is uppercase |
| `test_empty_and_fieldless_changes` | `[]` -> `{"dry_run": True, "requested": 0, "planned_count": 0}` and no calls; `[{"guid": slab, "keep_elevation": False}]` -> skipped `"nothing to change"` |

- [ ] **Step 2: Run them to verify they fail** (`uv run pytest tests/test_modify_elements.py -q`).
- [ ] **Step 3: Write `tests/fixtures/modify_project.py`** (read handlers) and **implement `modify.py`** with the interfaces above.
- [ ] **Step 4: Run** `uv run pytest tests/test_modify_elements.py tests/test_modify_fields.py tests/test_editability_locks.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: modify_elements planning and dry run`

---

### Task 4: Commit, keep-elevation move, readback (`modify_commit.py`)

**Files:**
- Create: `src/archicad_mcp/core/modify_commit.py`
- Modify: `src/archicad_mcp/core/modify.py` (call `commit`)
- Modify: `src/archicad_mcp/core/element_data.py` (`group_failures`)
- Modify: `tests/fixtures/modify_project.py` (write handlers)
- Test: `tests/test_modify_commit.py`

**Interfaces:**
- Consumes: Task 3 `Plan`, `Planned`, `plan_changes`; Task 2 `LIST_KEYS`, `OPENING_TYPES`, `compared`, `current_value`, `values_match`.
- Produces:
  - `commit(conn, plan: Plan) -> dict`: the spec's "Commit" steps 1 to 6 and result shape.
  - `setdetails_order(planned: list[Planned]) -> list[Planned]`: stable partition, openings needing their host first: `type in OPENING_TYPES` and `draw_order` not in 1..7 (absent counts as needing). The same rule as Tapir's pre-scan (ElementCommands.cpp 1796-1827).
  - `element_data.group_failures(failed: list[dict], detail_key: str = "property") -> list[dict]`: samples carry `{"guid", detail_key}`; existing callers unchanged.

Sending: SetDetailsOfElements items are `{"elementId": {"guid"}, "details": {"floorIndex"?, "layerIndex"?, "drawIndex"?}}`; Modify items `{"elementId": {"guid"}, **item}`, except ModifyMeshes `{"elementId": {"guid"}, "meshData": item}`; batches of 500 (`extract.PROPERTY_FETCH_CHUNK`). Keep-elevation: one `MoveElements` call for all of them, `moveVector {"x": 0, "y": 0, "z": -level_shift}`; if any of them is grouped and `plan.suspend_groups` is False, `SetSuspendGroupsMode {"suspendGroups": true}` before and `false` after in a `finally`. Group mates: `GetElementsOfGroups` for the plan's group GUIDs, minus the sent elements; their full details read before step 1 and after step 5, compared with `values_match` both ways.

`modified` = sent elements with no failure from any command and no `not_applied` field. `unverified` counts, per field name, the fields `compared()` rejects plus keep-elevation elements whose absolute height has no read.

Fake write handlers: `SetDetailsOfElements` (applies floorIndex, layerIndex, drawIndex; answers in Tapir's reordered order; `ignore_draw: set[str]` leaves drawIndex unchanged but answers success; `fail_setdetails: dict[str, dict]`), each Modify command (applies each field to `details[read key]` via `expected_read`; `fail: dict[(command, guid), dict]`; `partial: set[str]` applies then fails; `raise_on: set[str]` raises `TapirCommandError`), `MoveElements` (adds z to the absolute height keys), `SetSuspendGroupsMode` (records into `suspend_history`), `GetElementsOfGroups`, `mate_changes: dict[str, dict]` (a Modify on a grouped element also changes this mate).

- [ ] **Step 1: Write the failing tests** in `tests/test_modify_commit.py`:

| Test | Asserts |
|---|---|
| `test_commit_sends_setdetails_then_modify_and_reads_back` | slab `layer` + `thickness`, `dry_run=False` -> `{"dry_run": False, "requested": 1, "modified": 1}` exactly; SetDetailsOfElements called before ModifySlabs; the fake slab now has layerIndex of "Finish - Floor" and thickness 0.25 |
| `test_setdetails_order_matches_tapir` | `setdetails_order` on [slab(layer), DOOR-1(layer), WINDOW-1(draw_order 5), DOOR-2(draw_order 9)] -> [DOOR-1, DOOR-2, slab, WINDOW-1] |
| `test_results_are_matched_by_tapir_order` | the batch above with `fail_setdetails` on the slab only: `failed[0]["sample"] == [{"guid": slab, "command": "SetDetailsOfElements"}]`, the doors are modified |
| `test_failed_story_change_drops_the_modify_item` | BEAM-1 `story: 1, level: 0.5`, SetDetails fails -> BEAM-1 under `not_sent` with a reason naming the story change; no ModifyBeams call |
| `test_success_that_did_not_land_is_not_applied` | `ignore_draw={slab}`, `draw_order: 1` -> `not_applied == [{"field": "draw_order", "count": 1, "sample": [{"guid": slab, "sent": 1, "read": 7}]}]`, `modified == 0` |
| `test_partly_applied_failures` | BEAM-1 `width` + `level` with `partial={BEAM-1}` -> `partly_applied == {"count": 1, "guids": [BEAM-1]}` |
| `test_keep_elevation_moves_back_with_groups_suspended` | grouped object on story 0 -> `story: 1, keep_elevation: True`, suspend off: calls in order SetDetailsOfElements, SetSuspendGroupsMode(true), MoveElements (z -3.0), SetSuspendGroupsMode(false); `modified == 1`; `suspend_history == [True, False]` |
| `test_suspend_groups_restored_when_move_raises` | MoveElements raises -> `stopped` set, `suspend_history[-1] is False` |
| `test_raising_command_stops_and_reports` | slab and mesh changes, ModifySlabs raises -> `stopped["message"]`, mesh under `not_sent`, the SetDetails part of the slab counted in readback |
| `test_group_mates_changed_are_reported` | `mate_changes` -> `group_mates_changed == {"count": 1, "guids": [mate]}` |
| `test_geometry_lists_are_unverified` | slab `polygonOutline` (4 points) -> `unverified == {"polygonOutline": 1}`, `modified == 1` |
| `test_group_failures_takes_a_detail_key` (in `tests/test_property_writes.py`) | `group_failures([{"guid": "A", "command": "ModifyWalls", "code": 1, "message": "m"}], detail_key="command")[0]["sample"] == [{"guid": "A", "command": "ModifyWalls"}]` |

- [ ] **Step 2: Run them to verify they fail.**
- [ ] **Step 3: Implement** `commit`, `setdetails_order`, the `group_failures` parameter and the fake's write handlers; remove Task 3's placeholder.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** `feat: modify_elements commit with Tapir-ordered sends, keep_elevation and readback`

---

### Task 5: Register the tool; manifest, README, annotations

**Files:**
- Modify: `src/archicad_mcp/server.py` (right after `move_elements`)
- Modify: `manifest.json` (after `move_elements`)
- Modify: `README.md` (Tools section)
- Modify: `tests/test_tool_annotations.py` (`WRITERS` gains `"modify_elements"`)

**Interfaces:**
- Consumes: `modify.modify_elements`.
- Produces: the MCP tool `modify_elements(changes: list[dict], dry_run: bool = True, port: int | None = None) -> dict`, `_tool_meta("Modify elements", read_only=False, destructive=True)`, `@_guarded`.

Tool description (exact copy):

> Change existing elements. DRY-RUN BY DEFAULT: returns per element the current and new value of every field, plus skipped elements with the reason and warnings; pass dry_run=false to commit. Each change is {"guid", ...fields}. Any element: story (index, ground floor 0, or a story name; the element keeps its height above the story and moves with it unless keep_elevation=true), layer (name), draw_order (1 to 14, 0 = the type's default). Walls, beams, slabs, columns, windows, doors, morphs, multi-plane roofs, meshes, objects, lamps, texts and labels also take the fields of Tapir's Modify<Type> command by its own names (describe_api_command ModifyWalls lists them), e.g. height, thickness, sillHeight, lightIsOn. Attribute fields (buildingMaterialId, compositeId, profileId, surfaceId, lineTypeId, sectionFillId, ...) take a name. Lengths in metres, angles in radians, text sizes in mm. Partial coordinates, dimensions and visibility are completed from the current values. Elements Archicad would refuse (another view's, hidden or locked layer, not reserved in Teamwork, inside a hotlinked module, locked) are skipped with the reason, never unlocked. A commit reads the elements back: 'modified' counts what landed, 'failed' groups Archicad's refusals, 'not_applied' lists fields Archicad answered success for but did not change.

manifest entry: `{"name": "modify_elements", "description": "Change existing elements: story, layer, draw order and the type's own settings (height, thickness, sizes, attributes). Dry-run by default."}`

README: add `modify_elements` after `create_road_marking` in the Core list, and a paragraph after the find/search paragraph covering: one tool over SetDetailsOfElements and the 13 Modify commands; dry run shows current -> new; keep_elevation; refusals named, never unlocked; readback with `not_applied`; needs Tapir 1.5.9 or newer. Keep it under 12 lines, no em dashes.

- [ ] **Step 1: Add `"modify_elements"` to `WRITERS`; run** `uv run pytest tests/test_tool_annotations.py tests/test_manifest.py -q`. Expected: FAIL (tool not registered).
- [ ] **Step 2: Register the tool, add the manifest entry and the README text.**
- [ ] **Step 3: Run** `uv run pytest -q`. Expected: all pass.
- [ ] **Step 4: Commit** `feat: register modify_elements; manifest and README`

---

### Task 6: Live dry run on MCP-Test

**Files:**
- Modify: `docs/superpowers/specs/2026-10-19-modify-elements-design.md` (a "Live check, 2026-10-19" section with what was seen)
- Modify: `tests/test_live.py` (one `@pytest.mark.live` dry-run test)

- [ ] **Step 1:** `list_instances` (MCP tool). Expected: an instance with `project_name` "MCP-Test" and Tapir 1.7.1 once Aleš has updated (1.5.10 otherwise). Note the port.
- [ ] **Step 2:** Run the branch's code read-only against that port:

```bash
uv run python -c "import json; from archicad_mcp.connection import get_connection; from archicad_mcp.core.modify import modify_elements; print(json.dumps(modify_elements(get_connection(PORT), CHANGES), indent=1))"
```

with `CHANGES`: slab SLA - 001 `layer: "Finish - Floor"`, `thickness: 0.25`, `story: 1`; object FU - 003 `coordinates: {"x": 4.0}`, `layer: "Interior - Equipment"`, `draw_order: 0`; mesh TR - 005 `level: 0.3`; a slab `story: 1, keep_elevation: true`. Expected: four planned elements, current values equal to the recorded fixtures, the story warnings, no skipped, and no write command in Archicad's undo history.
- [ ] **Step 3:** Add `test_modify_elements_dry_run_is_read_only` to `tests/test_live.py`: a dry run on the first slab found, asserting `dry_run is True`, one planned element, and that `GetDetailsOfElements` reads the same `layerIndex` before and after. Run: `ARCHICAD_MCP_LIVE_PORT=PORT uv run pytest -m live -k modify -v`. Expected: PASS.
- [ ] **Step 4:** Write what was seen into the spec's live check section; commit `docs: modify_elements live dry run on MCP-Test`.
- [ ] **Step 5:** Ask Aleš (not done without his yes): lock one MCP-Test element with LockElements, read `General_Locked` and the dry-run reason, unlock; and one commit on a scratch element with readback.
