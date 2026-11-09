# Reading a Library Part Before Sampling It: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an agent see a library part's symbol before placing anything, and let learning and placement use the part's own size, with a symbol check beside the plan box measurement.

**Architecture:** A new pure module, `core/part_symbols.py`, holds the two reads, the extents and the drawing. `core/part_learning.py` and `core/create_placement.py` use it around today's samples; plan boxes stay the measurement. `core/part_preview.py` is the new tool's logic. Every new command is checked per command, so an older Tapir runs as it does today.

**Tech Stack:** Python, `uv`, `pytest`, Pillow (already a dependency), FastMCP.

**Spec:** [docs/superpowers/specs/2026-11-06-library-part-read-first-design.md](../specs/2026-11-06-library-part-read-first-design.md)

## Global Constraints

- Branch `feature/tapir-1.8.1`, local commits only, no push. Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- `gdl-src/swept-beam/params.toml` holds the user's own uncommitted change: never stage it. Stage files by name.
- Run tests with `uv run pytest`. The whole suite must stay green; it stood at 2,399 passed, 1 skipped, 18 deselected.
- New commands are gated with `conn.tapir_command_available`. No Archicad version gate and no Tapir version gate for them. The door and window fault wording goes by `create._part_name_faults_tool`.
- The transport raises `TapirCommandError` on a top-level `error`, so a refused read arrives as that exception. A per-item failure arrives as an item holding `error`. Both count as a failed read.
- Plan boxes stay the measurement. A symbol difference only warns.
- Sizes: the part's own, and 1.5 times it on both axes. Fallback `SAMPLE_SIZES` and `SAMPLE_HEIGHT`. Tolerance `part_placement.SIZE_TOLERANCE` (0.03 m).
- `preview_library_parts`: 1 to 8 names, read commands only, read-only annotation, `full` mode only.
- A `placement` that gives `size` sends exactly what it sends today. `placement.size` stays required in `modify_elements`.
- Live checks run on MCP-Test only, never on a project of the user's.

## Review Focus

Each line has its test in the task that owns the code.

- A symbol with no width or height, such as one vertical line or a single point: `draw_symbols` draws it and does not divide by zero (Task 1).
- A primitive missing its fields, such as a `Line` without `endCoordinate` or a `Polygon` without `coordinates`: `symbol_extents` skips it and does not raise (Task 1).
- A part whose A or B is not a number (text, an array, a boolean): `own_size_of` gives `None`, and learning falls back (Task 2).
- `placement.size` given with only `height`: refused with the schema's reason, not read as "use the own size" (Task 5).
- The same name twice in `names`: drawn once, numbered once, and counted once against the limit of 8 (Task 6).

---

### Task 1: Symbol extents and the drawing

**Files:**
- Create: `src/archicad_mcp/core/part_symbols.py`
- Test: `tests/test_part_symbols.py`

**Interfaces:**
- Produces:
  - `symbol_extents(primitives: list[dict]) -> tuple[float, float, float, float] | None` as `(xMin, yMin, xMax, yMax)`.
  - `draw_symbols(symbols: list[list[dict]]) -> bytes`, a PNG with one numbered cell per symbol.
  - Constants `CELL = 400`, `COLUMNS = 4`, `LINE = (0, 0, 0)`, `FILL = (215, 215, 215)`, `CROSS = (200, 0, 0)`, `NUMBER = (0, 0, 160)`.

- [ ] **Step 1: Write the failing tests**

Table-test `symbol_extents`, every value worked by hand, with `pytest.approx`. `r = math.sqrt(0.5)`. Arcs are around the origin with radius 1 unless stated:

| Case | Primitives | Expected |
|---|---|---|
| line | `Line` (0, 1) to (2, -1) | `(0, -1, 2, 1)` |
| polygon with a hole | outline (0,0), (2,0), (2,1), hole (0.5,0.1), (1,0.1), (1,0.2) | `(0, 0, 2, 1)` |
| whole arc | `origin` (1, 1), `radius` 0.5, `isWhole` true | `(0.5, 0.5, 1.5, 1.5)` |
| quarter arc | 0 to pi/2 | `(0, 0, 1, 1)` |
| crosses pi/2 | pi/4 to 3pi/4 | `(-r, r, r, 1)` |
| crosses pi | 3pi/4 to 5pi/4 | `(-1, -r, -r, r)` |
| crosses 3pi/2 | 5pi/4 to 7pi/4 | `(-r, -1, r, -r)` |
| crosses 0, end below begin | 7pi/4 to pi/4 | `(r, -r, 1, r)` |
| crosses 0, negative begin | -pi/4 to pi/4 | `(r, -r, 1, r)` |
| past 2 pi | 3pi/2 to 5pi/2 | `(0, -1, 1, 1)` |
| crosses nothing | 0.1 to 0.2 | `(cos 0.2, sin 0.1, cos 0.1, sin 0.2)` |
| mix | the line above and a whole arc, `origin` (5, 0), `radius` 1 | `(0, -1, 6, 1)` |
| nothing | `[]` | `None` |
| malformed (Review Focus) | `Line` without `endCoordinate`, `Polygon` without `coordinates`, `Arc` without `origin` | `None` |

Tests for `draw_symbols`, reading the result with `PIL.Image`:

```python
def test_the_sheet_has_one_cell_per_symbol_in_rows_of_four():
    # 1 symbol: (400, 400); 4: (1600, 400); 5: (1600, 800)

def test_a_symbol_is_drawn_black_with_the_cross_red_and_y_up():
    # Line (0, 1) to (1, 1): the mean row of LINE pixels is above the mean row of CROSS pixels,
    # which proves y runs up, and both colours are present.

def test_the_cross_shows_when_the_insertion_point_is_outside_the_symbol():
    # Line (5, 5) to (6, 6): CROSS pixels present.

@pytest.mark.parametrize("primitives", [
    [{"type": "Line", "begCoordinate": {"x": 0, "y": 0}, "endCoordinate": {"x": 0, "y": 2}}],
    [{"type": "Line", "begCoordinate": {"x": 1, "y": 1}, "endCoordinate": {"x": 1, "y": 1}}],
    [],
])
def test_a_symbol_with_no_width_or_height_is_drawn(primitives):
    # Review Focus: no exception, and a PNG of (400, 400).

def test_each_cell_shows_its_number():
    # NUMBER pixels in the top left of the second cell of a two-cell sheet.

def test_a_fill_polygon_is_filled_light_and_outlined():
    # isFill true: FILL and LINE pixels present.
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_part_symbols.py -q`
Expected: collection error, no module `archicad_mcp.core.part_symbols`.

- [ ] **Step 3: Implement both functions in `core/part_symbols.py`**

`symbol_extents`: a `Line` counts both ends; a `Polygon` its `coordinates`, not its holes; an `Arc` with `isWhole` counts `origin` plus and minus `radius` on both axes; any other `Arc` counts both ends and each of the angles 0, pi/2, pi, 3pi/2 that lies on the way from `begAngle` to `endAngle`, counter-clockwise, comparing angles modulo 2 pi. Skip a primitive missing what it needs.

`draw_symbols`: each cell scales `symbol_extents` joined with the origin to fit `CELL` less a margin of the builder's choice, with y up. A span of zero takes scale 1 on that axis. Draw lines and arcs in `LINE`, a polygon's outline in `LINE` and, with `isFill`, its inside in `FILL`, the cross at the origin in `CROSS`, and the 1-based number in `NUMBER` with Pillow's default font, in the top left of the cell. Return PNG bytes.

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_part_symbols.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/core/part_symbols.py tests/test_part_symbols.py
git commit -m "feat: a library part's symbol extents and drawing"
```

---

### Task 2: The two reads and `own_size` in the record

**Files:**
- Modify: `src/archicad_mcp/core/part_symbols.py`, `src/archicad_mcp/core/part_records.py`, `src/archicad_mcp/core/part_learning.py`
- Test: `tests/test_part_symbols.py`, `tests/test_part_records.py`

**Interfaces:**
- Consumes: nothing from Task 1 beyond the module.
- Produces:
  - `parameter_number(parameters: list | None, name: str) -> float | None`, moved from `part_learning._number`, which imports and uses it.
  - `default_parameters(conn, name: str) -> list[dict] | None`: `GetGDLParametersOfLibraryParts` with `{"libraryParts": [{"libraryPartName": name}]}`, answered under `gdlParametersOfLibraryParts[0].parameters`. `None` when the command is missing, the read raises `TapirCommandError`, the item holds `error`, or there are no parameters.
  - `own_size_of(parameters: list | None) -> dict | None`: `{"a": float, "b": float, "height": float | None}` from `A`, `B`, `ZZYZX`.
  - `symbol_of(conn, name: str, element_guid: str | None = None) -> list[dict] | None`: `GetLibraryPart2DPrimitives` with `name` and, when given, `elementId`, answered under `primitives`. `None` when the command is missing or the read raises `TapirCommandError`.
  - `PartRecord.own_size: dict | None = None`.

- [ ] **Step 1: Write the failing tests**

Use `FakeCore` and `ArchicadConnection(19723, core=core)` with `API.IsAddOnCommandAvailable` answering per command.

```python
def test_own_size_comes_from_a_b_and_zzyzx():
    # A 0.8, B 2.0, ZZYZX 0.5 -> {"a": 0.8, "b": 2.0, "height": 0.5}

def test_a_part_without_zzyzx_has_no_height():
    # -> {"a": 0.9, "b": 2.1, "height": None}

@pytest.mark.parametrize("zzyzx", [0, -1, "tall"])
def test_a_height_not_above_zero_is_no_height(zzyzx):
    # -> height None, a and b kept

@pytest.mark.parametrize("a", [0, -0.5, None, "wide", True, [1.0]])
def test_an_a_that_is_no_positive_number_gives_no_size(a):
    # Review Focus -> None. Same for B, one case.

def test_no_parameters_give_no_size():
    # own_size_of(None) is None and own_size_of([]) is None

def test_default_parameters_ask_by_name():
    # Sent exactly {"libraryParts": [{"libraryPartName": "Bed Double 01"}]}

@pytest.mark.parametrize("failure", ["missing", "raises", "item error", "empty"])
def test_default_parameters_are_none_when_the_read_fails(failure): ...

def test_symbol_of_asks_for_the_defaults_or_an_instance():
    # symbol_of(conn, "Door") sends {"name": "Door"};
    # symbol_of(conn, "Door", "G") sends {"name": "Door", "elementId": {"guid": "G"}}

@pytest.mark.parametrize("failure", ["missing", "raises"])
def test_symbol_of_is_none_when_the_read_fails(failure): ...
```

In `tests/test_part_records.py`:

```python
def test_a_record_without_own_size_loads_with_none(): ...
def test_own_size_round_trips_through_the_store(tmp_path): ...
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_part_symbols.py tests/test_part_records.py -q`
Expected: new tests fail on missing names.

- [ ] **Step 3: Implement the reads, `own_size_of`, the moved helper and the record field**

`own_size_of` gives `None` unless A and B are numbers above zero, and `height` is `None` unless ZZYZX is one. `parameter_number` already excludes booleans.

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_part_symbols.py tests/test_part_records.py tests/test_part_learning.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/core/part_symbols.py src/archicad_mcp/core/part_records.py src/archicad_mcp/core/part_learning.py tests/test_part_symbols.py tests/test_part_records.py
git commit -m "feat: read a part's own defaults and symbol, and keep its own size in the record"
```

---

### Task 3: Learning samples at the part's size, and what it says about itself

**Files:**
- Modify: `src/archicad_mcp/core/part_learning.py`, `src/archicad_mcp/server.py` (the `learn_library_part` description), `docs/tools.md`
- Test: `tests/test_part_learning.py`

**Interfaces:**
- Consumes: `default_parameters`, `own_size_of` from Task 2; `create._part_name_faults_tool`.
- Produces:
  - Constant `SECOND_SIZE_FACTOR = 1.5`.
  - `_object_samples(name, origin, story, variants, own_size=None)`.
  - `_Learning(conn, name, kind, origin, story, variants, own_size=None, defaults=None)`.
  - `_record(earlier, name, guid, kind, tapir, footprint, wall_flipped, own_size)`.

- [ ] **Step 1: Extend the fake and write the failing tests**

Give `Fake` three new arguments. `own` is a dict of parameter values, answered by `GetGDLParametersOfLibraryParts` as `[{"name": k, "type": "Length", "value": v}, ...]`. `own_fails` makes that read raise `TapirCommandError`. `missing` is a list of commands `API.IsAddOnCommandAvailable` answers `False` for. Its default is both new commands, so every existing test keeps running without them.

```python
def test_samples_take_the_parts_own_size():
    # Fake(own={"A": 0.8, "B": 2.0, "ZZYZX": 0.5}, missing=()), object "Bed Double 01":
    # dimensions sent, in order: (0.8, 2.0, 0.5), (1.2, 3.0, 0.5), mirrored (0.8, 2.0, 0.5)

@pytest.mark.parametrize("options", [
    {"missing": ["GetGDLParametersOfLibraryParts"]},
    {"own_fails": True, "missing": ()},
    {"own": {"A": 0, "B": 2.0}, "missing": ()},
    {"own": {"B": 2.0}, "missing": ()},
])
def test_samples_fall_back_to_the_fixed_sizes(options):
    # dimensions are SAMPLE_SIZES at SAMPLE_HEIGHT, as today

def test_a_part_with_no_height_samples_at_the_fixed_height():
    # own {"A": 0.8, "B": 2.0, "ZZYZX": 0}: (0.8, 2.0, SAMPLE_HEIGHT), (1.2, 3.0, SAMPLE_HEIGHT)

def test_the_footprint_fits_the_samples_at_the_parts_own_size():
    # corner_boxes with own A 0.8, B 2.0: footprint {"xMin": [0, 0], "xMax": [1, 0],
    # "yMin": [0, 0], "yMax": [1, 0]}, as with the fixed sizes

@pytest.mark.parametrize("kind, part", [("object", "Chair 01"), ("door", "Door")])
def test_learning_records_the_parts_own_size(tmp_path, kind, part):
    # own {"A": 0.9, "B": 2.1}: record.own_size == {"a": 0.9, "b": 2.1, "height": None}

def test_a_door_is_still_sampled_at_the_fixed_width():
    # own {"A": 0.9, "B": 2.1}: CreateDoors items have width OPENING_WIDTH

def test_learning_again_replaces_the_own_size(tmp_path): ...

def test_learning_again_keeps_the_own_size_when_the_read_fails(tmp_path):
    # Earlier record with own_size; own_fails True: the earlier own_size stays

def test_the_tool_is_said_to_hold_the_part_only_where_the_fault_exists():
    # tapir_version "1.8.0": "Door tool now holds 'Door'" in warnings
    # tapir_version "1.8.2": no warning containing "now holds"

def test_an_openings_parameters_fall_back_to_the_parts_defaults():
    # The fake's door sample gives no parameters (Fake.gdl read, as on Tapir 1.7.2);
    # own {"gs_leaf_1_open": 0, "A": 0.9, "B": 2.1}, missing=():
    # out["parameters"] holds gs_leaf_1_open, and a warning says "defaults"

def test_the_parameter_warning_blames_tapir_only_where_the_fault_exists():
    # A door sample, which the fake gives no parameters, and no defaults.
    # "1.8.0": "Tapir 1.8.0 and older" in the warning. "1.8.2": no "Tapir 1.8.0" and no "1.7.2".

def test_an_older_tapir_sends_only_what_it_sends_today():
    # Fake(tapir_version="1.8.0") with the default missing: neither new command in the
    # commands sent, dimensions SAMPLE_SIZES, record.own_size None
```

Update the existing assertion at `tests/test_part_learning.py:450` to the new wording: `probably because Tapir 1.8.0 and older place doors and windows by part name`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_part_learning.py -q`
Expected: the new tests fail, and the updated assertion fails.

- [ ] **Step 3: Implement**

In `learn_library_part`, after `_loaded_part`, read `default_parameters` and `own_size_of`, and pass them to `_Learning` and `_record`. `_record` keeps `earlier.own_size` when the new read gave `None` and the record is for the same part. In `_openings` and `_opening_parameters`, gate the fault wording with `_part_name_faults_tool(self.conn)`, and use `_named_values(defaults)` when the sample gives no parameters, with a warning that these are the part's defaults, not the sample's.

- [ ] **Step 4: Update the tool description and `docs/tools.md`**

The `learn_library_part` description and the learning paragraph in `docs/tools.md` place the door and window fault at Tapir 1.8.0 and older, where they now name only 1.7.2. They add that on a newer Tapir learning samples at the part's own size and records it.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_part_learning.py tests/test_tool_annotations.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/archicad_mcp/core/part_learning.py src/archicad_mcp/server.py docs/tools.md tests/test_part_learning.py
git commit -m "feat: learning samples at the part's own size and records it"
```

---

### Task 4: The symbol check

**Files:**
- Modify: `src/archicad_mcp/core/part_learning.py`
- Test: `tests/test_part_learning.py`

**Interfaces:**
- Consumes: `symbol_of`, `symbol_extents`; `part_placement.SIZE_TOLERANCE`; `_from_insertion`.
- Produces: `_Learning._check_symbols(self, sized: list[_Placed], boxes: list[tuple | None]) -> str | None`, giving `"agrees"`, `"differs"`, `"unread"`, or `None` on a Tapir without the command. The object reply gains `symbol_check` when it is not `None`.

- [ ] **Step 1: Extend the fake and write the failing tests**

Give `Fake` a `symbol` argument: a callable `(a, b) -> primitives`, used by `GetLibraryPart2DPrimitives` for the `dimensions` of the element named in `elementId`. A callable that raises `TapirCommandError` is a refused read. Define `rect(x0, y0, x1, y1)` to build a closed `Polygon`.

```python
def test_a_symbol_that_matches_the_box_agrees():
    # corner_boxes, symbol rect(0, 0, a, b), missing=():
    # out["symbol_check"] == "agrees", and no warning mentions the symbol

def test_a_difference_inside_the_tolerance_agrees():
    # symbol rect(0, 0, a + 0.02, b)

def test_a_symbol_that_differs_warns_and_changes_no_measurement():
    # symbol rect(0, 0, a + 0.05, b):
    # out["symbol_check"] == "differs"; a warning names the sample's label and both boxes;
    # out["measured"] == the footprint the same Fake gives without a symbol

def test_every_refused_read_is_unread():
    # out["symbol_check"] == "unread", a warning, and learning still records

def test_a_symbol_with_nothing_in_it_counts_as_unread():
    # symbol returns []

def test_one_refused_read_leaves_the_verdict_to_the_other():
    # The first size refused, the second matching: "agrees", and a warning names the first

def test_only_the_sized_samples_are_read():
    # The guids sent are the first two samples'; the mirrored and variant samples are not asked

def test_there_is_no_symbol_check_on_a_tapir_without_the_command():
    # default missing: "symbol_check" not in out

def test_a_door_has_no_symbol_check():
    # kind "door", missing=(): "symbol_check" not in out
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_part_learning.py -q -k symbol`
Expected: fail, no `symbol_check` in the reply.

- [ ] **Step 3: Implement `_check_symbols` and add its result to `_objects`' reply**

Compare `symbol_extents` with `_from_insertion(box, p)` edge by edge against `SIZE_TOLERANCE`. A sample whose box is `None` is not compared. Read the availability once with `conn.tapir_command_available("GetLibraryPart2DPrimitives")`, so a Tapir without it sends nothing.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_part_learning.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/core/part_learning.py tests/test_part_learning.py
git commit -m "feat: learning checks each sample's symbol against its plan box"
```

---

### Task 5: Placement takes the part's own size

**Files:**
- Modify: `src/archicad_mcp/core/create_placement.py`, `src/archicad_mcp/core/part_placement.py`, `src/archicad_mcp/core/modify_placement.py`, `src/archicad_mcp/server.py` (the `create_elements` description), `docs/tools.md`
- Test: `tests/test_create_elements.py`, `tests/test_part_placement.py`, `tests/test_modify_elements.py`

**Interfaces:**
- Consumes: `PartRecord.own_size` from Task 2.
- Produces:
  - `part_placement.own_length_width(record: PartRecord) -> tuple[float, float] | None`: `(b, a)` for a head along y, `(a, b)` along x; `None` without a usable `own_size`.
  - `create_placement.MODIFY_PLACEMENT_SCHEMAS`: as `PLACEMENT_SCHEMAS`, with `size` required on objects and lamps; `modify_placement` uses it.
  - `OBJECT_PLACEMENT["required"] == ["center", "head"]`. `size` keeps `required: ["length", "width"]` inside it.

- [ ] **Step 1: Write the failing tests**

In `tests/test_part_placement.py`:

```python
@pytest.mark.parametrize("head, expected", [("+y", (2.0, 1.6)), ("-y", (2.0, 1.6)),
                                            ("+x", (1.6, 2.0)), ("-x", (1.6, 2.0))])
def test_own_length_and_width_follow_the_head(head, expected):
    # own_size {"a": 1.6, "b": 2.0, "height": 0.6}

@pytest.mark.parametrize("own", [None, "x", {"a": 1.6}, {"a": 0, "b": 2.0}])
def test_no_usable_own_size_gives_nothing(own): ...
```

In `tests/test_create_elements.py`, with `bed_record(own_size=OWN)`, `OWN = {"a": 1.6, "b": 2.0, "height": 0.6}`, `Plan(library=["Bed Double 01"])`, `store(tmp_path, record)`, and an item `{"libraryPartName": "Bed Double 01", "placement": {...}}`:

```python
def test_a_placement_without_size_takes_the_parts_own(tmp_path):
    # placement {"center": {"x": 0, "y": 0}, "head": {"x": 0, "y": 1}}:
    # dimensions {"x": 1.6, "y": 2.0, "z": 0.6}; coordinates x 0, y -1.0 (the footprint's
    # middle is y 1.0 along +y from the insertion point); shown in placements[0]["derived"]

def test_a_placement_without_size_follows_a_head_along_x(tmp_path):
    # bed_record(head="+x", own_size=OWN), head {"x": 1, "y": 0}:
    # dimensions {"x": 1.6, "y": 2.0, "z": 0.6}, angle 0

def test_a_placement_with_size_sends_what_it_always_sent(tmp_path):
    # The same payload with and without own_size on the record

def test_a_placement_without_size_or_own_size_is_skipped(tmp_path):
    # reason contains "size" and "learn the part again"

@pytest.mark.parametrize("size_height, recorded, own_height, expected", [
    (0.9, 0.7, 0.6, 0.9), (None, 0.7, 0.6, 0.7), (None, None, 0.6, 0.6)])
def test_the_height_comes_from_the_size_then_the_record_then_the_part(
        tmp_path, size_height, recorded, own_height, expected): ...

def test_with_no_height_anywhere_the_item_is_skipped_without_blaming_tapir(tmp_path):
    # reason contains "holds no height" and not "1.7.2"

def test_a_size_with_only_a_height_is_refused(tmp_path):
    # Review Focus. placement "size": {"height": 1.0}: skipped, reason names "length"

@pytest.mark.parametrize("element_type", ["door", "window"])
def test_an_openings_width_and_height_come_from_the_part(tmp_path, element_type):
    # door_record(kind=element_type, own_size={"a": 0.9, "b": 2.1, "height": None}), no width or
    # height on the item: width 0.9, height 2.1 sent, and both in placements[0]["derived"]

def test_an_openings_own_width_and_height_stay(tmp_path):
    # Item width 1.0: width 1.0, height 2.1

def test_an_opening_without_own_size_leaves_them_out(tmp_path):
    # Neither "width" nor "height" in the payload

def test_an_openings_sill_is_not_filled(tmp_path):
    # Item without sillHeight, own_size given: no "sillHeight" in the payload
```

In `tests/test_modify_elements.py`, with `_bed_project(tmp_path, own_size=OWN)`:

```python
def test_a_modify_placement_still_needs_a_size(tmp_path):
    # placement = _bed(); del placement["size"]: the call's error names change 1 and "size"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_part_placement.py tests/test_create_elements.py tests/test_modify_elements.py -q`
Expected: the new tests fail.

- [ ] **Step 3: Implement**

`own_length_width` treats `own_size` as usable only when it is a dict whose `a` and `b` are numbers above zero. In `create_placement._object_fields`, take the length and width from `size` or `own_length_width`, and the height from `size.height`, `record.height`, then `own_size.height`. `NO_HEIGHT` says the record holds no height and what to do, without naming a Tapir version. In `_opening_fields`, add `width` from `a` and `height` from `b` to the returned fields for each one the item leaves out. `resolved[index].update(fields)` and `derived` then carry them. Update the `size` description in `OBJECT_PLACEMENT`.

- [ ] **Step 4: Update the `create_elements` description and `docs/tools.md`**

The description's placement sentence says size is optional for a part learned on a newer Tapir, as it then takes the part's own, and that a door's or window's width and height default to the part's. The learning paragraph in `docs/tools.md` says the same.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_part_placement.py tests/test_create_elements.py tests/test_modify_elements.py tests/test_modify_commit.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/archicad_mcp/core/create_placement.py src/archicad_mcp/core/part_placement.py src/archicad_mcp/core/modify_placement.py src/archicad_mcp/server.py docs/tools.md tests/test_create_elements.py tests/test_part_placement.py tests/test_modify_elements.py
git commit -m "feat: a placement without a size takes the part's own"
```

---

### Task 6: `preview_library_parts`

**Files:**
- Create: `src/archicad_mcp/core/part_preview.py`
- Modify: `src/archicad_mcp/server.py`, `src/archicad_mcp/tapir_requirements.py`, `scripts/build_dashboard.py`, `docs/api-dashboard.html`, `docs/tools.md`, `docs/known-issues.md`, `README.md` if it lists the tool
- Test: `tests/test_part_preview.py`

**Interfaces:**
- Consumes: `symbol_of`, `symbol_extents`, `draw_symbols`, `default_parameters`, `own_size_of`; `core.create.LoadedParts.guid_of`.
- Produces: `preview_library_parts(conn, names: list[str]) -> dict`, in `core/part_preview.py`, with keys `parts`, `not_drawn`, `note` and, when something was drawn, `png`. On a bad count or a missing command, `{"error": ...}`. `MAX_PARTS = 8`. The tool in `server.py` pops `png` as `learn_library_part` does, and returns `[Image, result]` or the result alone.

- [ ] **Step 1: Write the failing tests**

```python
def test_parts_are_drawn_numbered_with_their_sizes():
    # "Bed Double 01" (own A 1.6, B 2.0, ZZYZX 0.6, symbol rect) and "Door" (own A 0.9, B 2.1):
    # parts == [{"number": 1, "name": "Bed Double 01", "a": 1.6, "b": 2.0, "height": 0.6,
    #            "extent": [0, 0, 1.6, 2.0]}, {"number": 2, "name": "Door", ...}]
    # not_drawn == [], and png opens as a (800, 400) image

def test_the_note_says_what_the_picture_leaves_out():
    # note mentions "default parameters" and "hotspots"

@pytest.mark.parametrize("names", [[], [f"P{i}" for i in range(9)]])
def test_any_other_count_is_an_error_and_reads_nothing(names):
    # "error" in out, and no call of either read

def test_a_name_given_twice_is_drawn_once():
    # Review Focus. ["Door", "Door"]: one part; nine names with one repeated are accepted

def test_a_name_not_found_says_the_closest():
    # Symbol read raises for "Beds Double 01": not_drawn reason contains "Bed Double 01";
    # the other part is still drawn

def test_the_library_is_listed_only_when_a_name_fails():
    # No GetAvailableLibraryParts when every part is drawn

def test_a_part_with_nothing_to_draw_is_not_drawn():
    # primitives []: not_drawn with that reason, and parts == []

def test_with_nothing_drawn_there_is_no_image():
    # "png" not in out

def test_a_part_whose_defaults_cannot_be_read_is_drawn_without_sizes():
    # a, b and height None, and still in parts

@pytest.mark.parametrize("command", ["GetLibraryPart2DPrimitives",
                                     "GetGDLParametersOfLibraryParts"])
def test_a_tapir_without_either_command_is_refused(command):
    # "error" names the command, and nothing is drawn

def test_it_sends_no_write():
    # Every command sent is in {"GetLibraryPart2DPrimitives", "GetGDLParametersOfLibraryParts",
    # "GetAvailableLibraryParts", "GetAddOnVersion"}

async def test_the_tool_returns_the_image_then_the_json(monkeypatch): ...
async def test_the_tool_is_full_mode_only(): ...
```

`tests/test_tool_annotations.py` needs no change: a tool outside `WRITERS` must be read-only.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_part_preview.py -q`
Expected: collection error, no module `archicad_mcp.core.part_preview`.

- [ ] **Step 3: Implement `core/part_preview.py`**

Collapse `names` with `dict.fromkeys`, then check 1 to `MAX_PARTS`. Check both commands with `require_tapir_command(conn, command, "preview_library_parts")`. For a failed read, `LoadedParts(conn).guid_of(name)` gives the reason when the part is not loaded; when it is, the reason says Archicad gave no symbol. Round each value of `extent` to 4 decimals.

- [ ] **Step 4: Register the tool**

In `server.py`, beside `learn_library_part`, with `_tool_meta("Preview library parts", read_only=True, destructive=False)` and `_tapir_conn(port, "preview_library_parts")`. Add `"preview_library_parts": ("GetLibraryPart2DPrimitives", "GetGDLParametersOfLibraryParts")` to `TOOL_COMMANDS`. Add the tool to the tool list and `WRAPPED_BY` in `scripts/build_dashboard.py`. Its description says to use it to choose between candidates before learning, and that it does not replace the look after placing.

- [ ] **Step 5: Update the docs**

- `docs/tools.md`: a short paragraph on the tool, and the removal of the 22.10.2026 limit that no preview exists.
- `docs/known-issues.md`, section "Tapir after 1.8.0": the five live check items from the spec.
- `uv run python scripts/build_dashboard.py`.

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -q`
Expected: all pass, with no failure of any kind.

- [ ] **Step 7: Commit**

```bash
git add src/archicad_mcp/core/part_preview.py src/archicad_mcp/server.py src/archicad_mcp/tapir_requirements.py scripts/build_dashboard.py docs/api-dashboard.html docs/tools.md docs/known-issues.md tests/test_part_preview.py
git commit -m "feat: preview_library_parts draws a part's symbol with nothing placed"
```
