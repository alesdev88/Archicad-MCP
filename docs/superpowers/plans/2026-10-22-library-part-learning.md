# Learning library parts (piece 1): implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The agent learns a library part once from a few samples (footprint, head, hinge and swing, sash states), the MCP remembers it, and `create_elements` / `modify_elements` place it by what the sketch shows: a footprint with a head direction, or a hinge point and an opening direction.

**Architecture:** Three new tools: `learn_library_part` places samples clear of the model, measures them, shows them in one image, deletes them and stores the measurement; `record_library_part` stores what the agent judged; `list_library_part_records` reads the store. The store is a JSON file under the MCP's data folder (`core/part_records.py`). Pure arithmetic turns a record plus a sketch placement into Tapir fields (`core/part_placement.py`); `create_elements` and `modify_elements` call it for items that carry a `placement` object, which is removed before Tapir's schema check.

**Tech Stack:** Python 3.12+ (uv), FastMCP 3.4.4, pytest, Pillow, Tapir 1.7.2, Archicad 29 and 30.

**Spec:** `docs/superpowers/specs/2026-10-22-library-part-learning-design.md` (piece 1 only; piece 2, the part reads from the next Tapir release, gets its own plan once that release's definitions are synced).

## Global Constraints

- Works on Tapir 1.7.2 (installed in Archicad 29 and 30). Doors and windows by `libraryPartName` need 1.7.2.
- Archicad 29 and 30, macOS and Windows.
- Write tools: dry run by default, wrapped in `_guarded`, replies scrubbed, refusals with the reason.
- Lengths in metres, angles in radians, as the rest of `create_elements`.
- Records live in `data_root() / "library-parts.json"`; `data_root()` is `~/Library/Application Support/archicad-mcp` on macOS, `%LOCALAPPDATA%\archicad-mcp` on Windows, `~/.local/share/archicad-mcp` elsewhere (the base `backup_root` uses today).
- A record is keyed by part name and holds the part's main GUID; a different main GUID under the same name refuses the old record.
- Learning refuses unless the active window is `FloorPlan`.
- Every learning call deletes what it placed, also on failure, and confirms with `editability.present` that nothing is left.
- Learning reports what Tapir 1.7.2 cannot undo: the Door or Window tool left on the sampled part; the plan's zoom, if Task 1 makes learning zoom.
- Speed targets: a learned part placed with no samples; learning one part in about a minute or two; samples of one part captured in one image.
- Live tests only on MCP-Test (port 19723) or MCP-Test_30 (port 19724) after checking `projectName`, elements near the origin or just past the model, all deleted.
- No em or en dashes in code, comments, docs, commits or tool text.
- Commits as `Aleš Dolenec <285164556+alesdev88@users.noreply.github.com>`, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A model with thousands of elements:** the sample spot must be found in a few seconds (bounding boxes read in chunks), not minutes. Task 4 pins chunking with a 2500-element fake.
2. **A crash or refusal in the middle of learning** (the third of four samples fails): everything already placed is still deleted, and the reply names what failed. Task 4.
3. **An unreadable or hand-edited records file:** saving must refuse rather than overwrite it with an empty store. Task 2.
4. **A wall drawn right to left** (begin east of end) and a door asked to hinge at the east jamb: the hinge and swing come out right, because they are taken relative to the wall's own direction. Task 3.
5. **The same part placed mirrored with its head along local X:** mirroring reverses that head, so the angle must account for it. Task 3.

---

## Task 1: Live probe (no product code)

Pins three facts the later tasks depend on. Run on MCP-Test_30 (Archicad 30, port 19724) or MCP-Test (Archicad 29, port 19723), whichever is free; Archicad 29 had a modal dialog open on 22.10.2026.

**Files:**
- Create: scratchpad script `probe_part_learning.py` (not committed)
- Modify: this plan, section "Probe results" at the end

- [ ] **Step 1: Write the probe script.** It asserts `GetProjectInfo.projectName` is the test model, then near (0, 30):
  - places Chair 01 twice with `dimensions` {0.5, 0.5, 0.9}, angle 0, `reflected` false and true; Bed Double 01 at {1.0, 1.0, 1.0} and {2.0, 1.5, 1.0};
  - reads `API.Get2DBoundingBoxes` for the four objects (print the raw reply) and `GetDetailsOfElements` coordinates;
  - creates one wall from (0, 34) to (12, 34), places "Window" at `centerOffset` 1.5, 4.5, 7.5, 10.5 with (`reflected`, `oSide`) = (false, false), (true, false), (false, true), (true, true), width 1.0, height 1.2, sillHeight 0.9;
  - saves a 2D `GetElementPreviewImage` (width and height 256) of every sample to files;
  - deletes everything and prints `GetAllElements` count before and after.
- [ ] **Step 2: Run it and look at the images.**
- [ ] **Step 3: Record in "Probe results":** (a) the exact `API.Get2DBoundingBoxes` reply keys; (b) whether `reflected` mirrors an object's local x (x becomes -x) or local y, read from the two chair boxes against their insertion points; (c) whether the 2D previews show the mirroring of the chair and the different hinge and swing of the four windows; (d) for each window combination, the hinge jamb ("begin" or "end" of the wall) and the swing side ("left" or "right" of the wall's direction); (e) whether `centerOffset` is measured from the wall's begin point; (f) the element count back to its start.
- [ ] **Step 4: Commit the plan update.**

```bash
git add docs/superpowers/plans/2026-10-22-library-part-learning.md
git commit -m "docs: probe results for library part learning"
```

---

## Task 2: The record store

**Files:**
- Create: `src/archicad_mcp/data_dir.py`
- Modify: `src/archicad_mcp/tapir_install/install.py` (`backup_root` uses `data_root`)
- Create: `src/archicad_mcp/core/part_records.py`
- Test: `tests/test_part_records.py`

**Interfaces:**
- Produces: `data_root(platform: str | None = None) -> Path`.
- Produces: `KINDS = ("object", "lamp", "door", "window")`; `COMBOS = ("ff", "tf", "ft", "tt")` (first letter `reflected`, second `oSide`, `t`/`f`); `HEADS = ("+x", "-x", "+y", "-y")`.
- Produces: `@dataclass class PartRecord` with fields `name: str`, `guid: str`, `kind: str`, `learned: str` (ISO date), `tapir: str | None`, `footprint: dict | None` (`{"xMin": [k, c], "xMax": [k, c], "yMin": [k, c], "yMax": [k, c]}`), `head: str | None`, `one_sided: bool | None`, `height: float | None`, `combos: dict[str, list[str]] | None` (combo to `[hinge, swing]`, hinge `"begin"`/`"end"`, swing `"left"`/`"right"`), `leaves: int | None`, `leaf_states: dict[str, dict] | None` (state name to `{gdl parameter name: value}`); methods `missing() -> list[str]` (objects and lamps need footprint, head, one_sided; doors and windows need combos, leaves) and `to_json() -> dict`, `from_json(data: dict) -> PartRecord`.
- Produces: `class PartRecords` with `__init__(self, path: Path | None = None)` (default `data_root() / "library-parts.json"`), `get(name: str) -> PartRecord | None`, `save(record: PartRecord) -> None`, `forget(name: str) -> bool`, `all() -> list[PartRecord]`; `class RecordsFileError(Exception)`.
- Produces: `identity_problem(record: PartRecord, guid: str) -> str | None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_data_root_per_platform(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert data_root("win32") == tmp_path / "archicad-mcp"
    assert data_root("darwin") == Path.home() / "Library" / "Application Support" / "archicad-mcp"
    assert backup_root("win32") == tmp_path / "archicad-mcp" / "tapir-backups"

def test_saved_record_reads_back_after_restart(tmp_path):
    PartRecords(tmp_path / "r.json").save(bed())
    assert PartRecords(tmp_path / "r.json").get("Bed Double 01") == bed()

def test_missing_names_what_the_agent_has_not_recorded():
    assert bed(head=None, one_sided=None).missing() == ["head", "one_sided"]
    assert window(combos=None).missing() == ["combos"]

def test_another_main_guid_refuses_the_record():
    assert "learned for another part" in identity_problem(bed(guid="G1"), "G2")
    assert identity_problem(bed(guid="G1"), "g1") is None   # GUIDs compare without case

def test_forget_removes_only_that_part(tmp_path): ...

def test_an_unreadable_file_is_never_overwritten(tmp_path):
    path = tmp_path / "r.json"; path.write_text("{not json")
    with pytest.raises(RecordsFileError):
        PartRecords(path).save(bed())
    assert path.read_text() == "{not json"
```

`bed()` and `window()` are test helpers building a complete record with keyword overrides.

- [ ] **Step 2: Run** `uv run pytest tests/test_part_records.py -q`; expect FAIL (module not found).
- [ ] **Step 3: Implement** `data_dir.py`, the `backup_root` change, and `part_records.py`. The file is `{"version": 1, "parts": {name: record}}`, written to a temporary file in the same folder and moved over the old one. A missing file is an empty store; an unreadable one raises `RecordsFileError` naming the path on `get`, `save` and `all`.
- [ ] **Step 4: Run** `uv run pytest tests/test_part_records.py tests/test_tapir_install.py -q`; expect PASS.
- [ ] **Step 5: Commit** `feat: a store for learned library parts`.

---

## Task 3: Placement arithmetic

**Files:**
- Create: `src/archicad_mcp/core/part_placement.py`
- Test: `tests/test_part_placement.py`

**Interfaces:**
- Consumes: `PartRecord` (Task 2).
- Produces: `@dataclass(frozen=True) class Sample: a: float; b: float; box: tuple[float, float, float, float]` (xMin, yMin, xMax, yMax relative to the insertion point, angle 0, not mirrored).
- Produces: `fit_footprint(samples: list[Sample]) -> dict` (x edges fitted against `a`, y edges against `b`, each as `[k, c]`; raises `ValueError` without two samples differing in both `a` and `b`).
- Produces: `footprint_at(footprint: dict, a: float, b: float) -> tuple[float, float, float, float]`.
- Produces: `object_fields(record: PartRecord, center: dict, length: float, width: float, height: float, head: dict, mirrored: bool) -> dict` returning `{"coordinates": {"x", "y", "z"}, "angle": float, "reflected": bool, "dimensions": {"x", "y", "z"}}`; `center` may carry `z` (default 0); `head` is a direction vector `{"x", "y"}`; angle in [0, 2π).
- Produces: `opening_fields(record: PartRecord, wall_beg: dict, wall_end: dict, center_offset: float, hinge_near: dict, opens_toward: dict) -> dict` returning `{"reflected": bool, "oSide": bool}`; raises `ValueError` when no recorded combo gives that hinge and swing.

How: a head along local ±y makes B the length and A the width, along ±x the other way round. Mirroring maps local x to -x (or y, per Probe results (b)); it applies to the footprint and to the head. The angle turns the (possibly mirrored) local head onto `head`; the insertion point is `center` minus the rotated local centre of `footprint_at(A, B)`. For an opening: the hinge is `"begin"` when `hinge_near` projects onto the wall axis before `center_offset`, else `"end"`; the swing is `"left"` when `opens_toward` lies left of the wall's direction (positive cross product), else `"right"`; the combo is looked up in `record.combos`.

- [ ] **Step 1: Write the failing tests**

```python
BED = record(head="+y", footprint={"xMin": [0, 0], "xMax": [1, 0], "yMin": [0, 0], "yMax": [1, 0]})  # corner insertion
CENTRED = record(head="+y", footprint={"xMin": [-0.5, 0], "xMax": [0.5, 0], "yMin": [-0.5, 0], "yMax": [0.5, 0]})

def test_fit_recovers_a_corner_and_a_centre_insertion(): ...

@pytest.mark.parametrize("head, angle", [({"x": 0, "y": 1}, 0.0), ({"x": -1, "y": 0}, pi / 2),
                                         ({"x": 0, "y": -1}, pi), ({"x": 1, "y": 0}, 3 * pi / 2)])
def test_bed_covers_its_rectangle_with_the_head_each_way(head, angle):
    f = object_fields(BED, {"x": 5, "y": 5}, length=2.0, width=1.8, height=0.5, head=head, mirrored=False)
    assert f["angle"] == pytest.approx(angle)
    assert f["dimensions"] == {"x": 1.8, "y": 2.0, "z": 0.5}
    assert placed_centre(BED, f) == pytest.approx((5, 5))   # helper: rotate the local centre back

def test_centre_insertion_lands_on_the_centre(): ...
def test_head_along_local_x_swaps_length_and_width(): ...
def test_mirrored_flips_local_x(): ...          # per Probe results (b)
def test_mirrored_head_along_x_turns_the_other_way(): ...   # Review Focus 5

def test_hinge_and_swing_pick_the_recorded_combo():
    w = record(kind="window", combos={"ff": ["begin", "left"], "tf": ["end", "left"],
                                      "ft": ["begin", "right"], "tt": ["end", "right"]})
    assert opening_fields(w, {"x": 0, "y": 0}, {"x": 10, "y": 0}, 4.0,
                          hinge_near={"x": 4.6, "y": 0}, opens_toward={"x": 4, "y": -1}) \
        == {"reflected": True, "oSide": True}

def test_a_wall_drawn_right_to_left_keeps_hinge_and_swing_right(): ...   # Review Focus 4
def test_no_recorded_combo_for_that_hinge_and_swing_is_an_error(): ...
```

- [ ] **Step 2: Run** `uv run pytest tests/test_part_placement.py -q`; expect FAIL.
- [ ] **Step 3: Implement** `part_placement.py`.
- [ ] **Step 4: Run** the same; expect PASS.
- [ ] **Step 5: Commit** `feat: placement arithmetic for learned library parts`.

---

## Task 4: `learn_library_part` (core)

**Files:**
- Create: `src/archicad_mcp/core/part_learning.py`
- Test: `tests/test_part_learning.py` (a fake in the style of `tests/test_create_elements.py`'s `Plan`)

**Interfaces:**
- Consumes: `PartRecords`, `PartRecord`, `KINDS` (Task 2); `Sample`, `fit_footprint` (Task 3); `editability.active_window`, `editability.present`; `tapir_requirements.installed_version`; `actions.fit_in_window` and `window_image.capture_window` only if Probe results (c) says previews do not show mirroring and swing.
- Produces: `learn_library_part(conn: ArchicadConnection, records: PartRecords, name: str, kind: str, variants: list[dict] | None = None) -> dict`; on success the dict carries `"png": bytes` (one image of all samples, each labelled), `"part"`, `"kind"`, `"guid"`, `"samples"` (label per sample, in image order), `"measured"` (objects: the fitted footprint), `"parameters"` (the first sample's GDL parameters: name, type, value), `"warnings"`, `"next"` (which `record_library_part` fields are still needed).
- Produces: constants `SAMPLE_SIZES = ((1.0, 1.0), (2.0, 1.5))`, `SAMPLE_HEIGHT = 1.0`, `CLEARANCE = 5.0`, `SPACING = 2.5`, `BOX_CHUNK = 1000`.
- Produces: `sample_origin(conn: ArchicadConnection) -> tuple[float, float]`: `GetAllElements`, then `API.Get2DBoundingBoxes` in chunks of `BOX_CHUNK`; returns (max xMax + `CLEARANCE`, min yMin), or (0, 0) with no elements.

Samples, in a row from `sample_origin` along +X, `SPACING` apart:
- object or lamp: one per `SAMPLE_SIZES` (angle 0, not mirrored, `dimensions` {a, b, `SAMPLE_HEIGHT`}), one mirrored at the first size, one per variant at the first size;
- door or window: a temporary wall along +X long enough for four combos plus the variants, one opening per `COMBOS` entry (width 1.0; window height 1.2, sill 0.9; door height 2.1, sill 0), one per variant with combo `ff`.
A variant is `{gdl parameter name: value}`, written with `SetGDLParametersOfElements` after the create; a refused variant is reported, not fatal.

The image: by default the 2D `GetElementPreviewImage` of each sample (256 px) pasted into one labelled sheet with Pillow, so the user's view is not touched. If Probe results (c) found the previews blind to mirroring or swing: `fit_in_window` on the samples, then `capture_window`, and a warning that the plan stays zoomed there.

Refusals, each with nothing placed: kind not in `KINDS`; active window not `FloorPlan`; no loaded part of that name (closest names, as `create._library_part_checks` words it); door or window on a Tapir older than 1.7.2.

After learning, the store holds the part's record with `guid`, `kind`, `learned`, `tapir` and the measured `footprint`; the agent's fields stay as they were if the same GUID was learned before, else `None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_object_samples_are_measured_and_all_deleted(tmp_path):
    fake = Fake(boxes=corner_boxes())
    out = learn_library_part(fake.connection(), PartRecords(tmp_path / "r.json"), "Bed Double 01", "object")
    assert out["measured"]["xMax"] == pytest.approx([1.0, 0.0])
    assert fake.deleted == fake.created_guids and fake.remaining == []
    assert out["png"].startswith(b"\x89PNG") and len(out["samples"]) == 3

def test_samples_go_past_the_model():
    assert sample_origin(Fake(boxes_of_model=(-3, -2, 40, 9)).connection()) == (45.0, -2.0)

def test_thousands_of_elements_are_boxed_in_chunks():          # Review Focus 1
    fake = Fake(model_elements=2500); sample_origin(fake.connection())
    assert fake.box_calls == 3

def test_a_failure_part_way_still_deletes_what_was_placed():   # Review Focus 2
    fake = Fake(fail_create_at=2)
    out = learn_library_part(fake.connection(), records, "Window", "window")
    assert "error" in out and fake.remaining == [] and "temporary wall" in str(fake.deleted_types)

def test_window_learning_says_the_tool_now_holds_the_part():
    out = learn_library_part(Fake().connection(), records, "Window", "window")
    assert any("Window tool now holds 'Window'" in w for w in out["warnings"])

def test_refuses_outside_the_floor_plan(): ...
def test_refuses_an_unknown_part_with_the_closest_names(): ...
def test_refuses_doors_on_tapir_older_than_1_7_2(): ...
def test_relearning_the_same_guid_keeps_the_agents_fields(): ...
def test_a_refused_variant_is_reported_not_fatal(): ...
```

- [ ] **Step 2: Run** `uv run pytest tests/test_part_learning.py -q`; expect FAIL.
- [ ] **Step 3: Implement** `part_learning.py`. Deletion runs in a `finally` over every GUID created, wall included; `present` afterwards; any survivor becomes an `"error"` naming its GUID.
- [ ] **Step 4: Run** the same; expect PASS.
- [ ] **Step 5: Commit** `feat: learn_library_part places, measures and removes samples`.

---

## Task 5: `record_library_part`, `list_library_part_records` and the server

**Files:**
- Modify: `src/archicad_mcp/core/part_learning.py` (add the two functions)
- Modify: `src/archicad_mcp/server.py` (three tools in `_register_full_mode_tools`)
- Modify: `src/archicad_mcp/tapir_requirements.py` (`TOOL_COMMANDS["learn_library_part"]`)
- Test: `tests/test_part_learning.py`, `tests/test_server_smoke.py`

**Interfaces:**
- Consumes: Task 2, Task 4.
- Produces: `record_library_part(records: PartRecords, name: str, head: str | None = None, one_sided: bool | None = None, height: float | None = None, combos: dict | None = None, leaves: int | None = None, leaf_states: dict | None = None, forget: bool = False) -> dict` returning `{"record": ..., "missing": [...]}` or `{"error": ...}`. Refuses a part never learned, a `head` not in `HEADS`, a combo key not in `COMBOS`, a hinge or swing word outside `begin/end` and `left/right`, `leaves` < 1, a `leaf_states` value that is not a dict.
- Produces: `list_library_part_records(records: PartRecords, names: list[str] | None = None) -> dict` returning `{"records": [...], "path": str}`.
- Produces: tools `learn_library_part(name: str, kind: str, variants: list[dict] | None = None, port: int | None = None)` (returns `[Image, result]` like `capture_window`), `record_library_part(...)`, `list_library_part_records(names: list[str] | None = None)`. The last two need no Archicad. Descriptions carry the reading rules of Task 8.
- Produces: `TOOL_COMMANDS["learn_library_part"] = ("GetAvailableLibraryParts", "GetCurrentWindowType", "CreateObjects", "CreateLamps", "CreateWalls", "CreateWindows", "CreateDoors", "GetElementPreviewImage", "GetGDLParametersOfElements", "SetGDLParametersOfElements", "DeleteElements")`.

- [ ] **Step 1: Write the failing tests**

```python
def test_recording_the_agents_fields_completes_the_record(tmp_path):
    records = learned(tmp_path, "Bed Double 01", "object")        # helper: a stored measurement
    out = record_library_part(records, "Bed Double 01", head="+y", one_sided=False)
    assert out["missing"] == [] and records.get("Bed Double 01").head == "+y"

def test_recording_a_part_never_learned_is_refused(tmp_path): ...
@pytest.mark.parametrize("fields", [{"head": "up"}, {"combos": {"xx": ["begin", "left"]}},
                                    {"combos": {"ff": ["top", "left"]}}, {"leaves": 0},
                                    {"leaf_states": {"left": 1}}])
def test_bad_values_are_refused_and_nothing_is_saved(tmp_path, fields): ...
def test_forget_removes_the_record(tmp_path): ...
def test_list_names_the_file_and_filters_by_name(tmp_path): ...
def test_the_three_tools_exist_in_full_mode_only(): ...   # the pattern in test_server_smoke.py
```
- [ ] **Step 2: Run** `uv run pytest tests/test_part_learning.py tests/test_server_smoke.py -q`; expect FAIL.
- [ ] **Step 3: Implement** the two functions, the tool registrations (`_tool_meta`: learn is a write, record writes only the local file, list reads only) and the `TOOL_COMMANDS` entry.
- [ ] **Step 4: Run** `uv run pytest -q`; expect all PASS.
- [ ] **Step 5: Commit** `feat: record and list learned library parts`.

---

## Task 6: `placement` in `create_elements`

**Files:**
- Modify: `src/archicad_mcp/core/create.py`
- Test: `tests/test_create_elements.py`

**Interfaces:**
- Consumes: `PartRecords`, `identity_problem` (Task 2); `object_fields`, `opening_fields` (Task 3).
- Produces: `create_elements(conn, element_type, items, dry_run=True, records: PartRecords | None = None)` (server passes its store).
- Produces: item field `placement`, for `object`, `lamp`, `door`, `window` only, removed before the schema check:
  - object or lamp: `{"center": {"x", "y", "z"?}, "size": {"length", "width", "height"?}, "head": {"x", "y"}, "mirrored": bool?}`; `size.height` falls back to the record's `height`;
  - door or window: `{"hinge_near": {"x", "y"}, "opens_toward": {"x", "y"}, "leaves": str?}` (`leaves` names a `leaf_states` key).
- Produces: `create_elements(..., items=[])` for those four types adds `"placement_schema"` describing the above.
- Produces: `write_leaf_state(conn: ArchicadConnection, guid: str, parameters: dict) -> str | None` in `part_placement.py` (one `SetGDLParametersOfElements` call; Archicad's message on refusal, else `None`), used here and in Task 7.
- Produces: the server builds one `PartRecords()` in `_register_full_mode_tools` and passes it to `create_elements`, `modify_elements` and the Task 5 tools.
- Produces: dry run and commit add `"placements": [{"index", "part", "derived"}]`, `derived` being the fields `placement` produced (and `leaves` with its parameters).

Rules:
- `placement` needs `libraryPartName` and a record with no `missing()` fields whose GUID matches the loaded part (the GUID comes from the `GetAvailableLibraryParts` answer `_library_part_checks` already reads); otherwise the item is skipped with a reason that says to call `learn_library_part` or `record_library_part`.
- An item that gives both `placement` and a field it derives (`coordinates`, `angle`, `reflected`, `dimensions`, `oSide`) is skipped: "placement sets X; leave one out".
- Object without `size.height` and no recorded `height`: skipped, "give size.height in metres: Tapir 1.7.2 cannot read a part's own height".
- Door or window: the wall's `begCoordinate` and `endCoordinate` come from `GetDetailsOfElements` of `ownerWallId`.
- After the commit, `leaves` parameters go out with `SetGDLParametersOfElements` per created element; a refusal lists the element under `"placed_not_set"` with Archicad's message, and the element stays.
- Doors and windows sent with `libraryPartName` (with or without `placement`) get the warning that the Door or Window tool now holds that part.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_bed_by_footprint_becomes_tapir_fields(tmp_path): ...
def test_placement_without_a_record_is_skipped_with_the_next_step(): ...
def test_a_record_for_another_guid_is_skipped(): ...
def test_placement_and_coordinates_together_are_skipped(): ...
def test_object_without_height_falls_back_to_the_record_then_skips(): ...
def test_a_door_by_hinge_point_and_swing_gets_reflected_and_oside(): ...
def test_leaves_are_written_after_the_create(): ...
def test_a_refused_leaves_write_keeps_the_door_and_reports_it(): ...
def test_doors_by_part_name_warn_that_the_tool_now_holds_it(): ...
def test_items_without_placement_are_unchanged(): ...   # the existing suite already covers most
def test_empty_items_show_the_placement_schema(): ...
```

- [ ] **Step 2: Run** `uv run pytest tests/test_create_elements.py -q`; expect the new tests to FAIL.
- [ ] **Step 3: Implement.** A `_resolve_placements(conn, name, items, records, parts) -> tuple[list[dict], dict[int, str], list[dict], dict[int, dict]]` (Tapir items, skips, placements report, leaf writes per index) runs before `_problems`; `_commit` gets the leaf writes.
- [ ] **Step 4: Run** `uv run pytest -q`; expect all PASS.
- [ ] **Step 5: Commit** `feat: create_elements places learned parts by footprint, hinge and swing`.

---

## Task 7: `placement` in `modify_elements`

**Files:**
- Modify: `src/archicad_mcp/core/modify.py`, `src/archicad_mcp/core/modify_commit.py`
- Test: `tests/test_modify_elements.py`, `tests/test_modify_commit.py`

**Interfaces:**
- Consumes: Task 2, Task 3; `write_leaf_state` (Task 6).
- Produces: `modify_elements(conn, changes, dry_run=True, records: PartRecords | None = None)`; a change may carry `placement` (same shape as Task 6) for an object, lamp, door or window. The part and its GUID come from the element's `GetDetailsOfElements` `libPart`; the wall of a door or window from its owner.
- Produces: a change whose `placement` cannot be resolved makes the whole call an error naming the change (my call: modify's per-element skips assume Tapir fields).

- [ ] **Step 1: Write the failing tests**

```python
def test_a_placed_bed_turned_by_its_head_shows_new_coordinates_and_angle(): ...
def test_a_door_hinged_at_the_other_jamb_flips_reflected(): ...
def test_leaves_are_written_after_the_commit(): ...
def test_a_refused_leaves_write_is_reported_under_placed_not_set(): ...
def test_an_element_of_an_unlearned_part_makes_the_call_an_error(): ...
```
- [ ] **Step 2: Run** `uv run pytest tests/test_modify_elements.py tests/test_modify_commit.py -q`; expect FAIL.
- [ ] **Step 3: Implement:** `placement` is turned into fields before `_merge_changes`; the commit runs `write_leaf_state` after its read back.
- [ ] **Step 4: Run** `uv run pytest -q`; expect all PASS.
- [ ] **Step 5: Commit** `feat: modify_elements corrects placed parts by footprint, hinge and swing`.

---

## Task 8: Reading rules and docs

**Files:**
- Modify: `src/archicad_mcp/server.py` (descriptions of `learn_library_part`, `record_library_part`, `create_elements`)
- Modify: `README.md` (tool list and a short section after `capture_window`'s)
- Test: `tests/test_server_smoke.py`

The descriptions say, in this order and in these terms:
- learn a part once before placing it by `placement`; a learned part is placed with no samples;
- choose the part from what the sketch draws: the lines dividing a window give its sashes, the arcs give which sash or leaf opens and where it is hinged; prefer the plain generic part ("Window", "Door", "Double Window") when the sketch says no more;
- width is a check: a window about 1.2 m wide is a single or a double window, never a multi-sash one; when the count and the width disagree, ask;
- the head of a bed is the end with the pillows, or with the duvet's turned-back edge; when the sketch shows neither, ask;
- after placing, look (`capture_window`) and correct with `modify_elements` before reporting done.

- [ ] **Step 1: Write the failing test** that each of the three descriptions contains "pillows" or "sashes" as appropriate, and no em or en dash appears in any tool description.
- [ ] **Step 2: Run** `uv run pytest tests/test_server_smoke.py -q`; expect FAIL.
- [ ] **Step 3: Write** the descriptions and the README section.
- [ ] **Step 4: Run** `uv run pytest -q`; expect all PASS.
- [ ] **Step 5: Commit** `docs: reading rules for placing library parts from a sketch`.

---

## Task 9: Live tests

**Files:**
- Create: scratchpad script `live_part_learning.py` (not committed)

- [ ] **Step 1:** On MCP-Test (or MCP-Test_30), check `projectName`, note the element count.
- [ ] **Step 2:** Learn Bed Double 01, Chair 01, "Window", "Double Window" and "Door"; time each; record the agent's verdicts from the images. Expect about a minute or two per part and the count unchanged after each.
- [ ] **Step 3:** Place a bed by its footprint (centre and size) with the head toward each of the four walls of a 4 x 4 m room near the origin; `capture_window`; expect the bed on its rectangle with the pillows at the given end.
- [ ] **Step 4:** Place "Window" and "Door" with each hinge side and each opening direction, and a "Double Window" with only its left sash opening; expect the plan arcs to match.
- [ ] **Step 5:** A small apartment from a sketch, timed: learned parts placed in seconds, the floor in one batch and one look.
- [ ] **Step 6:** Delete every test element; the count is back to its start.
- [ ] **Step 7:** Record the timings and anything learned in the memory note and the Second Brain (brief status, topic note, changelog).

---

## Probe results

Run 22.10.2026 on MCP-Test (Archicad 29, port 19723, Tapir 1.7.2, floor plan active) in four passes: the brief's samples (run 1); the same with 3D and Section previews, a 512 px preview and floor plan captures (run 2); the Door tool's default part "Door" with the same four combos (run 3); those doors in a wall drawn right to left, plus a GDL read of a bed (run 4). The doors and the reversed wall go beyond the brief because "Window" draws no hinge or swing anywhere (see (c), (d)). Walls came from `CreateWalls` with only begin and end, so they carry the Wall tool default: Composite, `referenceLineLocation` "CoreOutside", `flipped` true, thickness 0.3, height 3.0.

**(a) `API.Get2DBoundingBoxes`.** Request `{"elements": [{"elementId": {"guid": ...}}, ...]}`. Reply, one item per element in request order, world coordinates in metres:

```json
{"boundingBoxes2D": [{"boundingBox2D": {"xMin": 4, "yMin": 30, "xMax": 5, "yMax": 31.2}},
                     {"error": {"code": 7203, "message": "Element not supported (element guid: \"...\")"}},
                     {"error": {"code": 7204, "message": "Element not found (element guid: \"...\")"}}]}
```

The only top-level key is `boundingBoxes2D`; an item holds either `boundingBox2D` (keys `xMin`, `yMin`, `xMax`, `yMax`) or `error`. In MCP-Test the two PolyLines answer 7203; an unknown GUID answers 7204. All 36 elements of MCP-Test came back in 0.19 s in one call (no larger model was tried). Objects, walls, windows and doors all have boxes. A window's box is only the wall's thickness band (`yMin` 33.95, `yMax` 34.25 in a wall on y = 34); a door's box reaches out to the side it swings to (see (d)).

**(b) `reflected` mirrors the object's local x (x becomes -x).** Boxes relative to the insertion point, angle 0:

| sample | `dimensions` | `reflected` | box x | box y |
|---|---|---|---|---|
| Chair 01 at (0, 30) | 0.5, 0.5, 0.9 | false | -0.25 .. 0.25 | -0.25 .. 0.283062117 |
| Chair 01 at (2, 30) | 0.5, 0.5, 0.9 | true | -0.25 .. 0.25 | -0.25 .. 0.283062117 |
| Bed Double 01 at (4, 30) | 1.0, 1.0, 1.0 | false | 0 .. 1 | 0 .. 1.2 |
| Bed Double 01 at (6, 30) | 2.0, 1.5, 1.0 | false | 0 .. 2 | 0 .. 1.5 |
| Bed Double 01 at (10, 30) (extra) | 1.0, 1.0, 1.0 | true | -1 .. 0 | 0 .. 1.2 |

The chair is symmetric about its local y axis, so its two boxes are equal; that rules out a y mirror (it would give y -0.283 .. 0.25) but cannot show the x mirror. The extra mirrored bed shows it: x 0 .. 1 becomes -1 .. 0, y unchanged.

Bed Double 01 has a minimum length: its GDL `B` reads 1.2 when 1.0 was sent (`possibleValues` `[{"value": 1.2, "flag": "GreaterThanOrEqual"}]`; `A` has 0.8 the same way), and `ZZYZX` is `Disabled` at 0.66 whatever `dimensions.z` says. `GetDetailsOfElements` still reports `dimensions` y 1.0, as sent. So the 1.0 x 1.0 sample is drawn 1.0 x 1.2, and fitting `yMax` against `b` over the two samples (1.0 gives 1.2, 1.5 gives 1.5) would give 0.6 b + 0.6 instead of b. `GetDetailsOfElements` object details carry `origin` {x, y, z}, `dimensions`, `angle`, `reflected` and an `offset` {x, y} (Chair 01: {0, -0.25}; Bed Double 01: {0, 0}).

**(c) What the pictures show.**
- 2D `GetElementPreviewImage` draws at a fixed 100 px per metre with the insertion point at the image centre, at 256 and at 512 px alike: the 1 x 1 bed covers x 128 .. 228 px of a 256 px image, the 2 x 1.5 bed 256 .. 456 px of a 512 px one. A 256 px preview therefore shows only 1.28 m each way from the insertion point; the 2.0 x 1.5 bed is cut off at 256 px. Only angle 0 was tried for objects.
- Chair 01 mirrored: not visible; the two 2D previews are byte for byte the same.
- Bed Double 01 mirrored: visible only as the drawing moving to the left of the centre (x 28 .. 128 px instead of 128 .. 228 px); the bed symbol itself is symmetric.
- "Window": no hinge and no swing in any picture. 2D previews: (false, false) equals (true, false) and (false, true) equals (true, true) byte for byte; the two pairs differ only in which side of the centre the frame is drawn. 3D and Section previews: (false, false) equals (true, true) and (true, false) equals (false, true), showing a frame and glass with no sash or handle. Floor plan capture: the four windows look the same, no opening lines.
- "Door" (extra): the floor plan capture shows leaf and arc plainly. Its 2D previews show `reflected` (the leaf moves to the other jamb) but not `oSide` ((false, false) looks like (false, true)), so they cannot tell the hinge jamb or the swing side relative to the wall.
- So the 2D previews cannot answer hinge or swing for openings, and show an object's mirroring only through where the drawing sits against the centre. The floor plan capture (`FitInWindow`, then `SaveCurrentWindowAsImage` with `entireDrawing` false) answered the doors. `FitInWindow` replies `{"success": true}`; `SaveCurrentWindowAsImage` replies `{"imageFilePath": ..., "zoom": {"xMin", "yMin", "xMax", "yMax"}, "rotation": 0}` and wrote a 2138 x 1152 px PNG (the window's size). The plan stays zoomed there afterwards.

**(d) Hinge and swing per (`reflected`, `oSide`).** For "Window": not established; nothing drawn shows a sash, hinge or swing, and its GDL parameters could not be read (below), so whether this part opens at all is unknown. For "Door" (the Door tool's default part, created without `libraryPartName`), read from the floor plan captures (the open leaf stands at the hinge jamb) and from the door boxes (they reach out on the swing side), identical in a wall drawn +X (begin (0, 38), end (12, 38)) and in one drawn right to left (begin (12, 38), end (0, 38)):

| `reflected`, `oSide` | hinge jamb | swing side of the wall's direction |
|---|---|---|
| false, false | begin | right |
| true, false | end | right |
| false, true | end | left |
| true, true | begin | left |

That is: `oSide` false swings right, true swings left; the hinge is at the begin jamb when `reflected` equals `oSide`. Door boxes in the +X wall: `yMin` 37.13 / `yMax` 38.269 for `oSide` false (right is -y), `yMin` 37.931 / `yMax` 38.885 for true. In the reversed wall: 37.731 .. 38.87 for `oSide` false (right of -X is +y), 37.115 .. 38.069 for true. Both walls had `flipped` true; a wall with `flipped` false was not tried: `CreateWalls` has no `flipped` field, no Tapir command found sets it, and MCP-Test has no walls of its own.

`GetGDLParametersOfElements` fails for doors and windows here: one window and two doors (runs 3 and 4, one door read again 1 s after the create) answered `{"error": {"code": -2130313215, "message": "Failed to get parameters of element with guid ...!"}}`, and the four windows of run 2 came back without `parameters` (that raw reply was not kept), while a Bed Double 01 in the same session answered 86 `parameters`, each with `name`, `displayName`, `index`, `type`, `value`, `isLocked`, `flags`, and for A and B `possibleValues` and `canHaveCustomValue`.

Opening details from `GetDetailsOfElements`: `libPart` {`name`, `parentUnID`, `ownUnID`}, `ownerElementId`, `ownerElementType`, `width`, `height`, `sillHeight`, `centerOffset`, `reflected`, `refSide`, `oSide`, all as sent.

**(e) `centerOffset` is measured from the wall's begin point** along the wall. Windows at 1.5, 4.5, 7.5, 10.5 in the wall from (0, 34) to (12, 34) have boxes x 1 .. 2, 4 .. 5, 7 .. 8, 10 .. 11. Doors at the same offsets in the wall from (12, 38) to (0, 38) sit at x 10.5, 7.5, 4.5, 1.5 (box x 10.013 .. 10.987 for 1.5).

**(f) Element count.** `GetAllElements` gave 36 before and 36 after in each of the four runs; a `GetDetailsOfElements` of every created GUID afterwards found none of them, and every `DeleteElements` result was `success` true. Nothing was left behind.
