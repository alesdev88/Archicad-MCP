# Property Reads Through Tapir as a Fallback: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A property the official API cannot read (any property of a 2D element, and the definitions it refuses on every element) is read through Tapir's `GetPropertyValuesOfElements` on Tapir 2.0.0 or newer, and is reported as "not read" with its reason everywhere else, never as "not available".

**Architecture:** `extract.fetch_property_cells` stays the one place every property read goes through. It keeps the official error instead of discarding it, and with `tapir_fallback=True` fills those gaps from one Tapir call per chunk, converting Tapir's text to typed cells with a new pure function, `extract.tapir_cell`. `criteria.Cell` gains a `notRead` status that no operator matches. Read callers opt in to the fallback; write planning does not.

**Tech Stack:** Python 3.12, `uv`, `pytest`, FastMCP. No new dependency.

**Spec:** [docs/superpowers/specs/2026-11-11-tapir-property-read-fallback-design.md](../specs/2026-11-11-tapir-property-read-fallback-design.md)

## Global Constraints

- Branch `feature/tapir-property-read-fallback`, cut from `main` once the spec and this plan are on it. Local commits only, no push. Commit messages end with `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`.
- `gdl-src/swept-beam/params.toml` holds the user's own uncommitted change: never stage it. Stage files by name.
- Run tests with `uv run pytest`. The whole suite must stay green; it stood at 2643 passed, 1 skipped, 18 deselected.
- The official read is tried first for every pair and its cells are used unchanged. The fallback never overwrites a cell the official read answered.
- The fallback runs only when the caller passes `tapir_fallback=True`, Tapir answers, and its reported version is 2.0.0 or newer. It always sends `includeAvailability: true` and `useSIUnits: true`.
- The fallback never sends a property id that is not in the project's `GetAllProperties` list. One unknown id fails every element of a Tapir call.
- The element ceiling (`MAX_PROPERTY_FETCH_ELEMENTS`) and the chunk size (`PROPERTY_FETCH_CHUNK`) apply to the fallback as they do to the read.
- Official error codes: 6702 on a cell is an unsupported definition, 7203 on a row is an unsupported element. Any other error stays the empty cell `{}` it is today.
- Tapir texts: undefined is `"<Undefined>"`, list items are joined with `"; "`, booleans are `"True"` and `"False"`.
- No write command is sent by anything in this plan, except the live check's own lock and unlock in Task 9.
- Live checks run on MCP-Test only, never on a project of the user's.

## Review Focus

Each line has its test in the task that owns the code.

- A property address given as a GUID that is not a definition of the project, read together with a 2D element: it is not sent to Tapir, and the other properties of that element are still read (Task 4).
- Tapir fails during the fallback with `TapirCommandError`: the official answers are kept, the gaps stay `notRead`, and nothing is raised (Task 4).
- Tapir text that ignores `useSIUnits` (`"3,00"`, `"90°"`) or a boolean in another language: a `string` cell marked `unparsed`, never a `ValueError` (Task 2).
- A negating operator on a cell that was not read (`not_available`, `has_no_value`, `is_not_user_undefined`, `available`): no match, while another comparison of an `or` group still decides (Tasks 1 and 5).
- Tapir answers fewer rows, or fewer values in a row, than it was asked: the rest stay `notRead` and no value lands on the wrong pair (Task 4).

---

### Task 1: `Cell` gets a `notRead` status that no operator matches

**Files:**
- Modify: `src/archicad_mcp/criteria.py` (`Cell`, `Cell.from_api`, `evaluate_value`, the module docstring's list of statuses)
- Test: `tests/test_criteria.py`

**Interfaces:**
- Produces: `Cell(status, value=None, reason=None)` with `reason: str | None`; `Cell.from_api({"status": "notRead", "reason": r})` returns `Cell("notRead", reason=r)`; `evaluate_value(op, cell, wanted)` is `False` for every `op` when `cell.status == "notRead"`.

- [ ] **Step 1: Write the failing tests**

```python
NOT_READ = Cell("notRead", reason="because")

def test_from_api_keeps_a_not_read_cell_and_its_reason():
    assert Cell.from_api({"status": "notRead", "reason": "because"}) == NOT_READ

@pytest.mark.parametrize("op", ["has_value", "has_no_value", "is_user_undefined",
                                "is_not_user_undefined", "available", "not_available",
                                "equal", "not_equal", "contains", "does_not_contain"])
def test_no_operator_matches_a_cell_that_was_not_read(op):
    assert evaluate_value(op, NOT_READ, "x") is False

def test_a_not_read_cell_is_not_usable():
    assert NOT_READ.usable is False
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/test_criteria.py -k "not_read" -v`
Expected: FAIL, `Cell` takes no `reason`.

- [ ] **Step 3: Add `reason: str | None = None` to `Cell`, map `notRead` in `from_api`, and return `False` first in `evaluate_value` for a `notRead` cell**

- [ ] **Step 4: Run the file**

Run: `uv run pytest tests/test_criteria.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/criteria.py tests/test_criteria.py
git commit -m "feat: a cell that was not read matches no operator"
```

---

### Task 2: `extract.tapir_cell` turns Tapir's text into a typed cell

**Files:**
- Modify: `src/archicad_mcp/extract.py`
- Create: `tests/test_tapir_cell.py`

**Interfaces:**
- Produces: `tapir_cell(raw: dict, definition: dict) -> dict`. `raw` is one item of Tapir's `propertyValues` (`{"propertyValue": {"value": str}, "isAvailable": bool}` or `{"error": {...}, "isAvailable": bool}`). `definition` is one item of `GetAllProperties` (`propertyValueType`, `propertyCollectionType`, `propertyMeasureType`). Every returned cell carries `"via": "tapir"`.
- Produces: constants `TAPIR_UNDEFINED_TEXT = "<Undefined>"` and `TAPIR_LIST_SEPARATOR = "; "`.

Declared types seen live: `propertyValueType` is `Real`, `Integer`, `String`, `Boolean` or `Guid`; `propertyCollectionType` is `Single`, `List`, `SingleChoiceEnumeration` or `MultipleChoiceEnumeration`; `propertyMeasureType` is `Default`, `Length`, `Area`, `Volume` or `Angle`.

- [ ] **Step 1: Write the failing tests**

```python
def d(value_type, collection="Single", measure="Default"):
    return {"propertyValueType": value_type, "propertyCollectionType": collection,
            "propertyMeasureType": measure}

def v(text):
    return {"propertyValue": {"value": text}, "isAvailable": True}

@pytest.mark.parametrize("raw, definition, cell_type, value", [
    (v("0.29999999999999999"), d("Real", measure="Length"), "length", pytest.approx(0.3)),
    (v("6.3000000000000007"), d("Real", measure="Area"), "area", pytest.approx(6.3)),
    (v("11.890408816319191"), d("Real", measure="Volume"), "volume", pytest.approx(11.890408816)),
    (v("1.5"), d("Real"), "number", 1.5),
    (v("90"), d("Real", measure="Angle"), "angle", pytest.approx(math.pi / 2)),
    (v("23"), d("Integer"), "integer", 23),
    (v("True"), d("Boolean"), "boolean", True),
    (v("False"), d("Boolean"), "boolean", False),
    (v("SW - 032"), d("String"), "string", "SW - 032"),
    (v("8AAE8204-4D9F-44A7-A108-9F888AD60ED2"), d("Guid"), "string",
     "8AAE8204-4D9F-44A7-A108-9F888AD60ED2"),
    (v("Main Model"), d("String", "SingleChoiceEnumeration"), "string", "Main Model"),
    (v("51; 59"), d("Integer", "List"), "integerList", [51, 59]),
    (v(""), d("String", "List"), "stringList", []),
    (v("1.5; 2"), d("Real", "List", "Length"), "lengthList", [1.5, 2.0]),
    (v("A; B"), d("String", "MultipleChoiceEnumeration"), "multiEnum", ["A", "B"]),
])
def test_a_value_becomes_a_typed_cell(raw, definition, cell_type, value):
    assert tapir_cell(raw, definition) == {
        "status": "normal", "type": cell_type, "value": value, "via": "tapir"}

@pytest.mark.parametrize("raw, status", [
    ({"error": {"code": -2130312908, "message": "m"}, "isAvailable": False}, "notAvailable"),
    ({"error": {"code": -2130312908, "message": "m"}, "isAvailable": True}, "notEvaluated"),
    ({"error": {"code": -2130312908, "message": "m"}}, "notAvailable"),
    (v("<Undefined>"), "userUndefined"),
])
def test_statuses(raw, status):
    for definition in (d("String"), d("Real", measure="Length"), d("Integer", "List")):
        assert tapir_cell(raw, definition) == {"status": status, "via": "tapir"}

@pytest.mark.parametrize("text, definition", [
    ("3,00", d("Real", measure="Length")), ("90°", d("Real", measure="Angle")),
    ("Da", d("Boolean")), ("", d("Integer")), ("1; x", d("Integer", "List")),
])
def test_text_that_does_not_parse_is_kept_as_text(text, definition):
    assert tapir_cell(v(text), definition) == {
        "status": "normal", "type": "string", "value": text, "unparsed": True, "via": "tapir"}

def test_an_unknown_declared_type_is_text():
    assert tapir_cell(v("x"), {})["type"] == "string"
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/test_tapir_cell.py -v`
Expected: FAIL, `tapir_cell` is not defined.

- [ ] **Step 3: Implement `tapir_cell` and the two constants in `src/archicad_mcp/extract.py`**

The measure type picks the cell type for `Real` (`Length` `length`, `Area` `area`, `Volume` `volume`, `Angle` `angle`, anything else `number`); a `List` appends `List` to the item's type; an angle is converted from degrees to radians, item by item in a list. A list in which any item does not parse is unparsed as a whole.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_tapir_cell.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/extract.py tests/test_tapir_cell.py
git commit -m "feat: Tapir's property text becomes a typed cell"
```

---

### Task 3: `fetch_property_cells` keeps what the official read refused

**Files:**
- Modify: `src/archicad_mcp/extract.py` (`fetch_property_cells`)
- Test: `tests/test_extract.py`

**Interfaces:**
- Produces: constants `OFFICIAL_UNSUPPORTED_DEFINITION = 6702`, `OFFICIAL_UNSUPPORTED_ELEMENT = 7203`, and the exact reasons
  - `NOT_READ_DEFINITION = "the official API does not support this property definition; Tapir 2.0.0 or newer reads it"`
  - `NOT_READ_ELEMENT = "the official API reads no property of this element (2D and annotation elements are outside it); Tapir 2.0.0 or newer reads them"`
- Produces: a refused cell is `{"status": "notRead", "reason": <one of the two>}`. A row refused with 7203 gives that cell for every requested name that resolved. `fetch_property_values` returns `None` for such a cell, as it does today.

- [ ] **Step 1: Write the failing tests** in `tests/test_extract.py`, with a `FakeCore` whose `API.GetPropertyValuesOfElements` answers one row with `{"error": {"code": 7203, "message": "Element not supported"}}` and one row whose second value is `{"error": {"code": 6702, "message": "Property definition not supported"}}`

```python
def test_a_refused_element_is_not_read_with_its_reason():
    cells = fetch_property_cells(conn, ["label-1", "w-1"], ["General_Locked", "X/Layer"])
    assert cells["label-1"] == {
        "General_Locked": {"status": "notRead", "reason": extract.NOT_READ_ELEMENT},
        "X/Layer": {"status": "notRead", "reason": extract.NOT_READ_ELEMENT}}

def test_a_refused_definition_is_not_read_with_its_reason():
    assert cells["w-1"]["X/Layer"] == {"status": "notRead", "reason": extract.NOT_READ_DEFINITION}
    assert cells["w-1"]["General_Locked"] == {"type": "boolean", "status": "normal", "value": False}

def test_another_error_is_still_an_empty_cell():   # code 4002 on a cell
    assert cells["w-1"]["Other"] == {}

def test_values_of_cells_not_read_are_none():
    assert fetch_property_values(conn, ["label-1"], ["General_Locked"]) == {
        "label-1": {"General_Locked": None}}
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/test_extract.py -k "refused or another_error or not_read" -v`
Expected: FAIL, the cells are `{}`.

- [ ] **Step 3: Record the two refusals in `fetch_property_cells` instead of dropping them**

- [ ] **Step 4: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS. A test elsewhere that fed a 6702 or 7203 fake and expected `{}` is updated to the new cell.

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/extract.py tests/test_extract.py
git commit -m "feat: a property the official API refuses is kept as not read, with the reason"
```

---

### Task 4: The fallback fills the gaps from Tapir

**Files:**
- Modify: `src/archicad_mcp/extract.py` (`fetch_property_cells`, `fetch_property_values`)
- Create: `tests/test_property_fallback.py`

**Interfaces:**
- Consumes: `tapir_cell` (Task 2), the `notRead` cells and reasons (Task 3), `installed_version` and `_key` from `archicad_mcp.tapir_requirements` (already imported in `extract.py`).
- Produces: `fetch_property_cells(conn, guids, names, *, tapir_fallback: bool = False, report: dict | None = None) -> dict[str, dict[str, dict]]`. When `report` is given, `report["tapir_elements"]` is the number of elements sent to Tapir, summed over chunks (0 when none).
- Produces: `fetch_property_values(conn, guids, names, *, tapir_fallback: bool = False)`, which passes the flag on.
- Produces: `TAPIR_VALUE_FALLBACK_SINCE = (2, 0, 0)` and the reasons
  - `NOT_READ_UNKNOWN_ID = "the property id is not among the project's definitions"`
  - `NOT_READ_TAPIR_ELEMENT = "Tapir could not read this element either: "` (the Tapir message follows)

Behaviour, per chunk, only when the chunk has at least one `notRead` cell:

1. The gate: `tapir_fallback` is true, `conn.tapir_available()`, and `_key(installed_version(conn) or "") >= TAPIR_VALUE_FALLBACK_SINCE`. Otherwise the cells stay as Task 3 left them.
2. The definitions: one `GetAllProperties` call per `fetch_property_cells` call, made on the first chunk that needs it, indexed by property GUID without case.
3. A gap whose property id is not in the definitions gets `NOT_READ_UNKNOWN_ID` and is not sent.
4. One `GetPropertyValuesOfElements` Tapir call: the elements that have a gap, against the properties that have a gap and a definition, with `includeAvailability` and `useSIUnits` true.
5. Each answered value replaces the `notRead` cell of its pair, through `tapir_cell`, and only where the cell is `notRead`. A row that is an error sets `NOT_READ_TAPIR_ELEMENT + message` on that element's gaps.
6. `APIErrorBase` from either Tapir call leaves the gaps as they were. `ArchicadUnavailableError` is not caught.

- [ ] **Step 1: Write the failing tests** in `tests/test_property_fallback.py`. One helper builds a `FakeCore` with: the official read of Task 3's fake (a refused label, a wall with one refused definition and one answered boolean); Tapir `GetAddOnVersion` of a given version; `GetAllProperties` listing `General_Locked` as Boolean and `X/Layer` as String; and a Tapir `GetPropertyValuesOfElements` callable that records its parameters.

```python
def test_it_fills_a_refused_element_and_a_refused_definition():
    cells = fetch_property_cells(conn, ["label-1", "w-1"], NAMES, tapir_fallback=True)
    assert cells["label-1"]["General_Locked"] == {
        "status": "normal", "type": "boolean", "value": True, "via": "tapir"}
    assert cells["w-1"]["X/Layer"] == {
        "status": "normal", "type": "string", "value": "A-WALL", "via": "tapir"}

def test_an_official_answer_is_never_replaced():
    assert cells["w-1"]["General_Locked"] == {"type": "boolean", "status": "normal", "value": False}

def test_the_tapir_call_carries_both_options_and_only_the_gaps():
    sent = tapir_calls(core)[0]
    assert sent["includeAvailability"] is True and sent["useSIUnits"] is True
    assert sent["elements"] == element_payload(["label-1", "w-1"])
    assert sent["properties"] == [{"propertyId": ID["General_Locked"]}, {"propertyId": ID["X/Layer"]}]

@pytest.mark.parametrize("setup", ["flag_off", "tapir_1_8_0", "no_tapir"])
def test_without_the_gate_tapir_is_not_asked(setup):
    assert tapir_calls(core) == []
    assert cells["label-1"]["General_Locked"]["status"] == "notRead"

def test_no_gap_means_no_tapir_call_at_all():          # official answers everything
    assert [c for c, _ in core.calls if c in ("GetAllProperties", "GetAddOnVersion")] == []

def test_an_id_outside_the_definitions_is_not_sent():   # names include a bare unknown GUID
    assert {"propertyId": {"guid": UNKNOWN}} not in tapir_calls(core)[0]["properties"]
    assert cells["label-1"][UNKNOWN] == {"status": "notRead", "reason": extract.NOT_READ_UNKNOWN_ID}
    assert cells["label-1"]["General_Locked"]["status"] == "normal"

def test_a_tapir_error_for_one_element_names_it():
    assert cells["label-1"]["General_Locked"] == {
        "status": "notRead",
        "reason": extract.NOT_READ_TAPIR_ELEMENT + "Failed to get property values for element"}
    assert cells["w-1"]["X/Layer"]["status"] == "normal"

def test_tapir_failing_keeps_the_official_answers_and_raises_nothing():   # callable raises TapirCommandError
    assert cells["w-1"]["General_Locked"]["value"] is False
    assert cells["label-1"]["General_Locked"] == {"status": "notRead", "reason": extract.NOT_READ_ELEMENT}

def test_a_short_tapir_answer_leaves_the_rest_not_read():   # one row for two elements, one value for two
    assert cells["w-1"]["X/Layer"]["status"] == "notRead"

def test_the_report_counts_elements_sent_to_tapir():
    report = {}
    fetch_property_cells(conn, ["label-1", "w-1"], NAMES, tapir_fallback=True, report=report)
    assert report == {"tapir_elements": 2}

def test_fetch_property_values_passes_the_flag():
    assert fetch_property_values(conn, ["label-1"], ["General_Locked"], tapir_fallback=True) == {
        "label-1": {"General_Locked": True}}
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/test_property_fallback.py -v`
Expected: FAIL, `fetch_property_cells` takes no `tapir_fallback`.

- [ ] **Step 3: Implement the fallback in `extract.py`** as the six numbered rules above, in a private helper called once per chunk.

- [ ] **Step 4: Run the tests, then the suite**

Run: `uv run pytest tests/test_property_fallback.py -v && uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/extract.py tests/test_property_fallback.py
git commit -m "feat: property reads the official API refuses fall back to Tapir 2.0.0"
```

---

### Task 5: `find_elements` uses the fallback and reports what it could not read

**Files:**
- Modify: `src/archicad_mcp/core/query.py` (`find_elements`, the module docstring), `docs/query.md`
- Test: `tests/test_query.py`

**Interfaces:**
- Consumes: `fetch_property_cells(..., tapir_fallback=True, report=...)` (Task 4), `Cell.from_api` (Task 1).
- Produces: `property_reads` also counts `report["tapir_elements"]` of each request. A new result field, present only when a cell was not read: `"not_read": [{"reason": str, "properties": [sorted names], "cells": int}]`, one entry per reason, sorted by reason.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_property_of_a_2d_element_is_read_through_tapir():
    result = find_elements(conn, [{"element_types": ["Label"], "comparisons": [
        {"property": "General_Locked", "operator": "equal", "value": False}]}])
    assert result["count"] == 1 and "not_read" not in result
    assert result["property_reads"] == 2          # one official, one through Tapir

def test_without_tapir_2_the_gap_is_reported_and_matches_nothing():
    assert result["count"] == 0
    assert result["not_read"] == [{"reason": extract.NOT_READ_ELEMENT,
                                   "properties": ["General_Locked"], "cells": 1}]

def test_not_available_does_not_match_a_cell_that_was_not_read():
    # operator not_available on a definition the official API refuses, no Tapir
    assert result["count"] == 0

def test_an_or_group_is_still_decided_by_its_other_comparison():
    # logical_operator "or": a not-read comparison and a layer comparison that matches
    assert result["count"] == 1
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/test_query.py -k "tapir or not_read or or_group" -v`
Expected: FAIL.

- [ ] **Step 3: Pass `tapir_fallback=True` and a `report` in `find_elements`, add the Tapir elements to `read_count`, and build `not_read` from the `notRead` cells**

- [ ] **Step 4: Document `not_read` and the fallback in `docs/query.md`**, beside `property_reads` and `skipped_not_available`.

- [ ] **Step 5: Run the suite and commit**

Run: `uv run pytest -q`
Expected: PASS.

```bash
git add src/archicad_mcp/core/query.py docs/query.md tests/test_query.py
git commit -m "feat: find_elements reads 2D elements and unsupported definitions through Tapir, and says what it could not read"
```

---

### Task 6: `get_element_data` and write planning name what was not read

**Files:**
- Modify: `src/archicad_mcp/core/element_data.py` (`get_element_data`, `plan_property_writes`), `docs/element-data.md`
- Test: `tests/test_element_data_reads.py`, `tests/test_property_writes.py`

**Interfaces:**
- Consumes: `fetch_property_cells(..., tapir_fallback=True)` (Task 4).
- Produces: in `get_element_data`, a property that was not read has value `None` and an entry `unreadable[<address text>] = <reason>` in the element's existing `unreadable` map; an unread layer is `"layer": None` with `unreadable["layer"]`. In `plan_property_writes`, which keeps calling `fetch_property_cells` without the fallback, a change on a `notRead` cell is skipped with the cell's reason.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_2d_element_property_comes_through_tapir():
    item = get_element_data(conn, ["label-1"], ["General_Locked"])["elements"][0]
    assert item["properties"]["General_Locked"] is True and "unreadable" not in item

def test_without_tapir_2_the_reason_is_given_instead_of_a_value():
    assert item["properties"]["General_Locked"] is None
    assert item["unreadable"] == {"General_Locked": extract.NOT_READ_ELEMENT,
                                  "layer": extract.NOT_READ_ELEMENT}

def test_a_write_on_a_refused_definition_is_skipped_with_that_reason():
    planned, skipped = plan_property_writes(conn, [
        {"guid": "w-1", "property": "X/Layer", "value": "A"}])
    assert skipped == [{"guid": "w-1", "property": "X/Layer",
                        "reason": extract.NOT_READ_DEFINITION}]

def test_write_planning_never_asks_tapir_for_values():
    assert "GetPropertyValuesOfElements" not in [c for c, _ in core.calls if c != "API.GetPropertyValuesOfElements"]
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/test_element_data_reads.py tests/test_property_writes.py -k "tapir or reason" -v`
Expected: FAIL.

- [ ] **Step 3: Read cells in `get_element_data` (one `fetch_property_cells` call for `[BUILTIN_LAYER, *names]` with the fallback), take each value from its cell, and fill `unreadable`; in `plan_property_writes` check `cell.get("status") == "notRead"` before the value-type check**

- [ ] **Step 4: Document the `unreadable` reasons in `docs/element-data.md`**

- [ ] **Step 5: Run the suite and commit**

Run: `uv run pytest -q`
Expected: PASS.

```bash
git add src/archicad_mcp/core/element_data.py docs/element-data.md tests/test_element_data_reads.py tests/test_property_writes.py
git commit -m "feat: get_element_data reads through Tapir where the official API cannot, and set_element_data says why it skips"
```

---

### Task 7: The lock read and scripts use the fallback

**Files:**
- Modify: `src/archicad_mcp/core/editability.py` (`lock_reasons`, the comment above `ELEMENT_LOCK_PROPERTY`), `src/archicad_mcp/scripting/api.py` (`props`), `docs/scripting.md`
- Test: `tests/test_editability_locks.py`, `tests/test_scripting_api.py`

**Interfaces:**
- Consumes: `fetch_property_values(..., tapir_fallback=True)` (Task 4).
- Produces: no new name. `lock_reasons` names `ELEMENT_LOCK_REASON` for a locked 2D element on Tapir 2.0.0; `ac.props` returns the Tapir-read value for a gap and `None` where nothing read it.

- [ ] **Step 1: Write the failing tests**

```python
def test_lock_reasons_names_the_lock_of_a_2d_element_through_tapir():
    reasons = lock_reasons(conn, {"label-1": {"type": "Label", "layerIndex": 1}}, LAYERS)
    assert reasons == {"label-1": ELEMENT_LOCK_REASON}

def test_lock_reasons_stays_silent_when_the_lock_was_not_read():   # Tapir 1.8.0
    assert reasons == {}

def test_props_in_a_script_reads_a_2d_element_through_tapir():
    assert api.props(["label-1"], ["General_Locked"]) == {"label-1": {"General_Locked": True}}
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/test_editability_locks.py tests/test_scripting_api.py -k "tapir or not_read" -v`
Expected: FAIL.

- [ ] **Step 3: Pass `tapir_fallback=True` in both calls.** `_shared_id_warnings` in `core/create.py`, `read_back_properties` and the zone read in `build_snapshot` stay as they are.

- [ ] **Step 4: Note in `docs/scripting.md` that `ac.props` reads 2D elements on Tapir 2.0.0, and run the suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/core/editability.py src/archicad_mcp/scripting/api.py docs/scripting.md tests/test_editability_locks.py tests/test_scripting_api.py
git commit -m "feat: the lock of a 2D element and ac.props read through Tapir"
```

---

### Task 8: Rules skip on what was not read

**Files:**
- Modify: `src/archicad_mcp/rules/types.py` (`ElementInfo`), `src/archicad_mcp/extract.py` (`build_snapshot`), `src/archicad_mcp/rules/builtin/base.py`, `property_required.py`, `layer_compliance.py`, `classification_required.py`, `docs/rules.md`
- Test: `tests/test_extract.py`, `tests/rules/test_builtin_property_classification.py`, `tests/rules/test_builtin_layers_zones_ifc.py`

**Interfaces:**
- Consumes: `fetch_property_cells(..., tapir_fallback=True)` (Task 4).
- Produces: `ElementInfo.not_read: dict[str, str]` (default empty), property name to reason; the layer is under `LAYER_KEY = "layer"`, a new constant in `rules/types.py`.
- Produces: in `rules/builtin/base.py`, `unread_skip(rule, snapshot: ModelSnapshot, names: Iterable[str]) -> RuleResult | None`. It looks at the elements whose type `rule.applies_to` accepts (the `where` comparisons are not evaluated, since they may be the unread ones). When any has one of `names` in `not_read`, it returns a skipped `RuleResult` built like `LibraryPartsLoadedRule._skipped` (`passed=False`, `skipped=True`) with `skip_reason = f"{count} element(s) could not be read for '{name}': {reason}"`, for the first such name in sorted order. Otherwise `None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_snapshot_reads_gaps_through_tapir_and_records_what_stays_unread():
    snap = build_snapshot(conn, needs=frozenset({"elements", "properties", "layers"}),
                          property_names=frozenset({"General_Locked"}))
    label = {e.guid: e for e in snap.elements}["label-1"]
    assert label.properties["General_Locked"] is True and label.not_read == {}

def test_without_tapir_2_the_snapshot_records_the_reasons():
    assert label.not_read == {"General_Locked": extract.NOT_READ_ELEMENT,
                              "layer": extract.NOT_READ_ELEMENT}

def test_property_required_skips_when_its_property_was_not_read():
    result = rule.check(snapshot_with(not_read={"OFFICE/Fire Rating": "because"}))
    assert result.skipped is True
    assert result.skip_reason == "1 element(s) could not be read for 'OFFICE/Fire Rating': because"

def test_property_required_runs_when_only_another_type_was_not_read():
    # the rule applies to Wall, the unread element is a Label
    assert result.skipped is False

def test_layer_compliance_skips_when_a_layer_was_not_read():
    assert result.skipped is True and "'layer'" in result.skip_reason
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/test_extract.py tests/rules -k "not_read or unread or through_tapir" -v`
Expected: FAIL.

- [ ] **Step 3: Implement.** `build_snapshot` reads cells with the fallback for the element sweep, derives the values it derived before, and fills `not_read`. Each rule calls `unread_skip` first in `check`: `property_required` with its property and `applies_to.needed_properties`; `layer_compliance` with `LAYER_KEY` and `applies_to.needed_properties`; `classification_required` with `applies_to.needed_properties` only. `zone_checks` has no `applies_to` and its zone read stays official, so it is not touched.

- [ ] **Step 4: Document the skip in `docs/rules.md`, and run the suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/rules/types.py src/archicad_mcp/rules/builtin src/archicad_mcp/extract.py docs/rules.md tests/test_extract.py tests/rules
git commit -m "feat: a rule skips, with the reason, on a property that could not be read"
```

---

### Task 9: The live checks on MCP-Test, and the record

**Files:**
- Modify: `docs/known-issues.md` (section "The pre-check spike of 11.11.2026" and a new section for these results), `tests/test_live.py`

**Interfaces:**
- Consumes: everything above, on a running Archicad with MCP-Test open and stock Tapir 2.0.0.

- [ ] **Step 1: Add two live tests to `tests/test_live.py`**

```python
def test_live_a_2d_element_property_is_read_through_tapir(conn):
    # the first Label or PolyLine of the project
    cells = fetch_property_cells(conn, [guid], ["General_Locked"], tapir_fallback=True)
    assert cells[guid]["General_Locked"]["via"] == "tapir"
    assert cells[guid]["General_Locked"]["type"] == "boolean"

def test_live_a_definition_the_official_api_refuses_is_read_through_tapir(conn):
    # General Parameters / Layer, by its GUID 25826253-26F2-4D30-BE1B-0FFBB1314903, on a wall
    assert cell["status"] == "normal" and cell["type"] == "string" and cell["via"] == "tapir"
```

- [ ] **Step 2: Save MCP-Test, then run the live tests**

Run: `ARCHICAD_MCP_LIVE_PORT=<port> uv run pytest -m live -v`
Expected: PASS, the two new ones included.

- [ ] **Step 3: Run the spec's six checks by hand through the tools and write down each answer.** In order: (1) lock a label and a polyline in MCP-Test, `find_elements` with `General_Locked equal true` finds both, `move_elements` in a dry run names the lock, then unlock them; (2) a non-empty real, integer and text list and a multiple-choice enumeration through `get_element_data`; (3) the three texts on an Archicad in another language, if one is installed, otherwise recorded as not run; (4) a fetch of 500 elements with gaps, timed against the official read alone; (5) a stair's tread and a curtain wall panel; (6) Tapir's source of `GetPropertyValuesOfElements` at the 2.0.0 tag, for whether it composes values the way the official command does.

A check that comes out against a table of the spec stops this task: fix the owning task's code and test first, then return here.

- [ ] **Step 4: Write the results into `docs/known-issues.md`** as a section "Property reads through Tapir: the live check of DD.MM.YYYY", with the items not run listed as such, and change the spike section's sentence that the server does not use Tapir's read.

- [ ] **Step 5: Run the suite and commit**

Run: `uv run pytest -q`
Expected: PASS.

```bash
git add docs/known-issues.md tests/test_live.py
git commit -m "docs: the Tapir property fallback, checked live on MCP-Test"
```
