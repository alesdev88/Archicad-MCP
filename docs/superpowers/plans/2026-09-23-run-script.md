# run_script Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `run_script` and `apply_changeset`, so an N-element Archicad task takes one planning call and one apply call, with raw element data never passing through the model's context.

**Architecture:** `run_script` runs the caller's Python in a child interpreter (`python -I -m archicad_mcp.scripting.runner`) with an `ac` object that can read Archicad but only records writes. The server stores the recorded operations as a single-use changeset and returns a capped summary. `apply_changeset` checks the project, sends the recorded writes through the existing write paths, reads the values back and reports per element.

**Tech Stack:** Python 3.12+, FastMCP, multiconn_archicad, jsonschema, pytest with `asyncio_mode = "auto"`.

**Spec:** `docs/superpowers/specs/2026-09-23-run-script-design.md`

## Global Constraints

- Branch: `feat/run-script`. Never stage `docs/api-dashboard.html` or anything under `src/archicad_mcp/gateway/definitions/`: those carry the owner's uncommitted Tapir 1.5.9 sync.
- Never use em dashes or en dashes in code, comments, docs or commit messages. Rewrite the sentence instead.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Run tests with `.venv/bin/python -m pytest` from the repo root. The suite excludes `live` tests by default.
- Comment style: explain why, in full sentences, matching `gateway/execute.py` and `core/element_data.py`.
- Enabling: `--enable-scripts` or `ARCHICAD_MCP_SCRIPTS` in (`1`, `true`, `yes`, case-insensitive); full mode only; `--transport http` requires `--allow-scripts-over-http`.
- Limits: `timeout_s` clamped to 1..600 (default 120); `max_output_chars` default 20000; changeset TTL 30 minutes; store capacity 20; preview samples 20; apply report caps 50; traceback 30 lines; stderr tail 2000 characters.
- Property reads inside scripts keep `MAX_PROPERTY_FETCH_ELEMENTS` (env `ARCHICAD_MCP_MAX_PROPERTY_ELEMENTS`, default 5000).
- Child tests construct `ArchicadConnection(19743)`: multiconn rejects ports outside 19723..19744, and constructing does not connect.

## File Structure

| File | Responsibility |
|---|---|
| `src/archicad_mcp/core/element_data.py` (modify) | `value_fit`, `plan_property_writes`, `send_property_writes`; `set_element_data` rebuilt on them |
| `src/archicad_mcp/scripting/__init__.py` (create) | package marker |
| `src/archicad_mcp/scripting/api.py` (create) | `ArchicadError`, `Recorder`, `ScriptAPI` (the `ac` object) |
| `src/archicad_mcp/scripting/execute.py` (create) | `execute(code, ac) -> Outcome`, `to_jsonable`, `cap_text`, `cap_result` |
| `src/archicad_mcp/scripting/runner.py` (create) | child entry point |
| `src/archicad_mcp/scripting/child.py` (create) | `run_child(code, port, timeout_s) -> dict` |
| `src/archicad_mcp/scripting/changesets.py` (create) | `Changeset`, `ChangesetStore`, `ChangesetError`, `summarize` |
| `src/archicad_mcp/scripting/apply.py` (create) | `apply_changeset(...)` |
| `src/archicad_mcp/scripting/tools.py` (create) | `execute_script(...)`, `register(...)` |
| `src/archicad_mcp/server.py` (modify) | `enable_scripts` in `build_server`, CLI flags, http check, banner |
| `manifest.json`, `scripts/build_dashboard.py` (modify) | tool lists, `enable_scripts` user_config |
| `tests/test_property_writes.py`, `tests/test_scripting_api.py`, `tests/test_scripting_execute.py`, `tests/test_scripting_child.py`, `tests/test_scripting_changesets.py`, `tests/test_scripting_tools.py` (create) | tests |
| `tests/test_manifest.py`, `tests/test_tool_annotations.py`, `tests/test_startup_banner.py` (modify) | registration coverage |
| `docs/scripting.md` (create), `README.md`, `docs/known-issues.md` (modify) | docs |

---

### Task 1: Split property writes into plan and send, with type checks

**Files:**
- Modify: `src/archicad_mcp/core/element_data.py` (whole `set_element_data`, new functions above it)
- Test: `tests/test_property_writes.py` (create)

**Interfaces:**
- Consumes: `extract.fetch_property_cells(conn, guids, names)`, `extract.resolve_property_ids(conn, names)`, `extract.PROPERTY_FETCH_CHUNK` (500).
- Produces:
  - `value_fit(value_type: str, value) -> tuple[object, str | None]`: the value to send and, if it does not fit, the reason.
  - `plan_property_writes(conn, changes: list[dict]) -> tuple[list[dict], list[dict]]`: `changes` items are `{"guid", "property", "value"}`. Planned items are `{"guid", "property", "current", "new", "payload"}` where `payload` is one `elementPropertyValues` entry. Skipped items are `{"guid", "property", "reason"}`.
  - `send_property_writes(conn, planned: list[dict]) -> tuple[int, list[dict]]`: applied count and failed items `{"guid", "property", "code", "message"}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_property_writes.py`:

```python
"""Planning and sending property writes, shared by set_element_data and scripts."""
import pytest

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.core.element_data import (
    plan_property_writes,
    send_property_writes,
    value_fit,
)
from tests.conftest import FakeCore
from tests.fixtures import api_replays


def _conn_with_cells(cells: dict, set_results=None):
    """A connection whose property cells are exactly `cells`.

    cells maps (guid, "Group/Name") to a propertyValue dict, or to None for a
    property that is not available on that element.
    """
    official = dict(api_replays.OFFICIAL)

    def values(params):
        rows = []
        for el in params["elements"]:
            row = []
            for p in params["properties"]:
                name = p["propertyId"]["guid"].removeprefix("pid-")
                cell = cells.get((el["elementId"]["guid"], name))
                row.append({"error": {"code": 1, "message": "n/a"}} if cell is None
                           else {"propertyValue": cell})
            rows.append({"propertyValues": row})
        return {"propertyValuesForElements": rows}

    official["API.GetPropertyValuesOfElements"] = values
    official["API.SetPropertyValuesOfElements"] = set_results or (
        lambda p: {"executionResults": [{"success": True}
                                        for _ in p["elementPropertyValues"]]})
    core = FakeCore(official=official, tapir=dict(api_replays.TAPIR))
    return ArchicadConnection(19723, core=core), core


@pytest.mark.parametrize("value_type,value,sent", [
    ("string", "001", "001"),
    ("integer", 12, 12),
    ("integer", "12", 12),
    ("integer", "-5", -5),
    ("number", 2, 2.0),
    ("length", 0.9, 0.9),
    ("boolean", True, True),
    ("area", 3.5, 3.5),
])
def test_values_that_fit_are_sent_in_the_property_type(value_type, value, sent):
    out, reason = value_fit(value_type, value)
    assert reason is None
    assert out == sent and type(out) is type(sent)


@pytest.mark.parametrize("value_type,value", [
    ("integer", "001"),   # the CVP case: a leading zero cannot survive an Integer
    ("integer", "12a"),
    ("integer", True),
    ("string", 12),
    ("number", "2.5"),
    ("boolean", "yes"),
])
def test_values_that_do_not_fit_are_refused_with_a_reason(value_type, value):
    _, reason = value_fit(value_type, value)
    assert reason is not None
    assert value_type in reason and repr(value) in reason


def test_unknown_types_pass_through_unchecked():
    assert value_fit("someFutureType", object) == (object, None)


def test_plan_builds_typed_payloads_and_keeps_the_current_value():
    conn, core = _conn_with_cells({
        ("d-1", "D/Pozicija"): {"type": "integer", "status": "normal", "value": 0}})
    planned, skipped = plan_property_writes(conn, [
        {"guid": "d-1", "property": "D/Pozicija", "value": "12"}])
    assert skipped == []
    assert planned == [{
        "guid": "d-1", "property": "D/Pozicija", "current": 0, "new": 12,
        "payload": {"elementId": {"guid": "d-1"},
                    "propertyId": {"guid": "pid-D/Pozicija"},
                    "propertyValue": {"type": "integer", "status": "normal",
                                      "value": 12}}}]
    assert not any(c == "API.SetPropertyValuesOfElements" for c, _ in core.calls)


def test_plan_skips_each_unwritable_change_with_its_reason():
    conn, _ = _conn_with_cells({
        ("d-1", "D/Pozicija"): {"type": "integer", "status": "normal", "value": 0},
        ("d-1", "D/Finish"): {"type": "singleEnum", "status": "normal"},
        ("d-2", "D/Pozicija"): None})
    planned, skipped = plan_property_writes(conn, [
        {"guid": "d-1", "property": "D/Pozicija", "value": "001"},
        {"guid": "d-1", "property": "D/Finish", "value": "Oak"},
        {"guid": "d-2", "property": "D/Pozicija", "value": 3}])
    assert planned == []
    reasons = {(s["guid"], s["property"]): s["reason"] for s in skipped}
    assert "'001'" in reasons[("d-1", "D/Pozicija")]
    assert "enum" in reasons[("d-1", "D/Finish")]
    assert "available" in reasons[("d-2", "D/Pozicija")]


def test_plan_skips_a_property_name_that_does_not_resolve():
    conn, core = _conn_with_cells({})
    core.official_responses["API.GetPropertyIds"] = {
        "properties": [{"error": {"code": 1, "message": "not found"}}]}
    planned, skipped = plan_property_writes(conn, [
        {"guid": "d-1", "property": "No/Such", "value": "x"}])
    assert planned == []
    assert "resolve" in skipped[0]["reason"]


def _planned(n):
    return [{"guid": f"g-{i}", "property": "D/P", "current": None, "new": "x",
             "payload": {"elementId": {"guid": f"g-{i}"}}} for i in range(n)]


def test_send_batches_and_reports_each_refused_element():
    def set_results(params):
        items = params["elementPropertyValues"]
        return {"executionResults": [
            {"success": False, "error": {"code": 6001,
                                         "message": "TeamWork permission denied"}}
            if item["elementId"]["guid"] == "g-500" else {"success": True}
            for item in items]}

    conn, core = _conn_with_cells({}, set_results=set_results)
    applied, failed = send_property_writes(conn, _planned(501))
    assert applied == 500
    assert failed == [{"guid": "g-500", "property": "D/P", "code": 6001,
                       "message": "TeamWork permission denied"}]
    sends = [c for c, _ in core.calls if c == "API.SetPropertyValuesOfElements"]
    assert len(sends) == 2  # 500 per request, the same chunk as property reads


def test_send_nothing_sends_nothing():
    conn, core = _conn_with_cells({})
    assert send_property_writes(conn, []) == (0, [])
    assert core.calls == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_property_writes.py -q`
Expected: collection error, `ImportError: cannot import name 'plan_property_writes'`.

- [ ] **Step 3: Implement**

In `src/archicad_mcp/core/element_data.py`, change the import block to add `PROPERTY_FETCH_CHUNK`:

```python
from archicad_mcp.extract import (
    BUILTIN_LAYER,
    PROPERTY_FETCH_CHUNK,
    _fetch_classifications,
    _fetch_types,
    element_payload,
    fetch_property_cells,
    fetch_property_values,
    resolve_property_ids,
)
```

Add below `_ENUM_TYPES`:

```python
# Property value types that take a plain number. The measure types are numbers
# in the API; the unit is the project's, not part of the value.
_NUMERIC_TYPES = frozenset({"number", "length", "area", "volume", "angle"})


def value_fit(value_type: str, value) -> tuple[object, str | None]:
    """The value to send for a property of `value_type`, and why it does not fit.

    Checked before anything is written, because Archicad's refusal arrives per
    element after the whole batch is sent, and one type mistake then fails every
    element the same way. The case that forced this: a 3-digit door number
    "001" aimed at an Integer property, which cannot hold a leading zero.
    """
    if value_type == "string":
        if isinstance(value, str):
            return value, None
        return value, f"'string' property takes text, got {value!r}"
    if value_type == "integer":
        if isinstance(value, int) and not isinstance(value, bool):
            return value, None
        if isinstance(value, str):
            try:
                number = int(value)
            except ValueError:
                number = None
            if number is not None and str(number) == value:
                return number, None
        return value, (f"'integer' property cannot hold {value!r}; send a whole "
                       "number, or change the property to String in Property "
                       "Manager if the value needs leading zeros")
    if value_type in _NUMERIC_TYPES:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value), None
        return value, f"'{value_type}' property takes a number, got {value!r}"
    if value_type == "boolean":
        if isinstance(value, bool):
            return value, None
        return value, f"'boolean' property takes true or false, got {value!r}"
    # Types this check does not know are left to Archicad, whose refusal is
    # still reported per element by send_property_writes.
    return value, None


def plan_property_writes(conn: ArchicadConnection,
                         changes: list[dict]) -> tuple[list[dict], list[dict]]:
    """Check each change against the element's current cell and build its payload.

    Returns (planned, skipped). Planned items carry the finished
    SetPropertyValuesOfElements entry, so sending needs no further reads.
    Nothing is written here.
    """
    prop_names = sorted({c["property"] for c in changes})
    guids = list(dict.fromkeys(c["guid"] for c in changes))
    # Read the raw cells: they carry the property's `type`, which the write
    # payload must echo back (a bare {"value": ...} is rejected by the API).
    cells = fetch_property_cells(conn, guids, prop_names)
    ids = resolve_property_ids(conn, prop_names)
    planned: list[dict] = []
    skipped: list[dict] = []
    for c in changes:
        guid, prop = c["guid"], c["property"]
        cell = cells.get(guid, {}).get(prop, {})
        value_type = cell.get("type")
        send = c["value"]
        if prop not in ids:
            reason = "property name did not resolve"
        elif value_type is None:
            reason = ("could not determine the property's value type "
                      "(is it available on this element?)")
        elif value_type in _ENUM_TYPES:
            reason = (f"'{value_type}' properties take an enum value id, not a "
                      "plain value; set it via execute_write_api_command (or "
                      "ac.cmd in a script) with the enum's id")
        else:
            send, reason = value_fit(value_type, c["value"])
        if reason is not None:
            skipped.append({"guid": guid, "property": prop, "reason": reason})
            continue
        planned.append({
            "guid": guid, "property": prop, "current": cell.get("value"), "new": send,
            "payload": {"elementId": {"guid": guid}, "propertyId": ids[prop],
                        "propertyValue": {"type": value_type, "status": "normal",
                                          "value": send}}})
    return planned, skipped


def send_property_writes(conn: ArchicadConnection,
                         planned: list[dict]) -> tuple[int, list[dict]]:
    """Send planned writes in batches. Returns (applied, failed per element)."""
    applied = 0
    failed: list[dict] = []
    for start in range(0, len(planned), PROPERTY_FETCH_CHUNK):
        chunk = planned[start:start + PROPERTY_FETCH_CHUNK]
        response = conn.official("API.SetPropertyValuesOfElements",
                                 {"elementPropertyValues": [p["payload"] for p in chunk]})
        results = (response or {}).get("executionResults", [])
        for i, p in enumerate(chunk):
            # Lenient: missing/short executionResults (or a missing "success" key)
            # are treated as success rather than crashing.
            outcome = results[i] if i < len(results) else {}
            if outcome.get("success", True):
                applied += 1
                continue
            # Each failure names its element and Archicad's reason (6001 is a
            # Teamwork reservation, including elements inside a hotlink), so
            # the caller can act on it without a second query pass.
            error = outcome.get("error") or {}
            failed.append({"guid": p["guid"], "property": p["property"],
                           "code": error.get("code"), "message": error.get("message")})
    return applied, failed
```

Replace the whole `set_element_data` function with:

```python
def set_element_data(conn: ArchicadConnection, changes: list[dict],
                     dry_run: bool = True) -> dict:
    if dry_run:
        prop_names = sorted({c["property"] for c in changes})
        cells = fetch_property_cells(conn, [c["guid"] for c in changes], prop_names)
        return {"dry_run": True, "planned_changes": [
            {"guid": c["guid"], "property": c["property"],
             "current": cells.get(c["guid"], {}).get(c["property"], {}).get("value"),
             "new": c["value"]}
            for c in changes]}
    planned, skipped = plan_property_writes(conn, changes)
    result: dict = {"dry_run": False, "applied": 0}
    if planned:
        applied, failed = send_property_writes(conn, planned)
        result["applied"] = applied
        if failed:
            result["failed"] = failed
    if skipped:
        result["skipped"] = skipped
    return result
```

- [ ] **Step 4: Run the new tests and the existing set_element_data tests**

Run: `.venv/bin/python -m pytest tests/test_property_writes.py tests/test_tier2_query_data.py -q`
Expected: all pass.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/archicad_mcp/core/element_data.py tests/test_property_writes.py
git commit -m "refactor: split property writes into plan and send, check value types

set_element_data now refuses a value that does not fit the property type
(a leading-zero string into an Integer) before sending, and sends in
500-element batches. Scripts reuse both halves.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The `ac` object

**Files:**
- Create: `src/archicad_mcp/scripting/__init__.py`, `src/archicad_mcp/scripting/api.py`
- Test: `tests/test_scripting_api.py`

**Interfaces:**
- Consumes: `plan_property_writes` (Task 1); `core.query.find_elements(conn, groups, selection_only) -> dict` with `"guids"` or `"error"`; `extract.fetch_property_values`, `extract.element_payload`, `extract.MAX_PROPERTY_FETCH_ELEMENTS`, `extract.PROPERTY_FETCH_CHUNK`, `extract.PropertyFetchTooWideError`; `gateway.execute._validate(info, params) -> dict | None`, `gateway.execute._dispatch(conn, info, params) -> dict`, `gateway.execute.describe_api_command(registry, name) -> dict`.
- Produces:
  - `class ArchicadError(Exception)` with `.message: str`, `.code: int | None`.
  - `@dataclass class Recorder` with `operations: list[dict]` and `skipped: list[dict]`. Operations are `{"kind": "props", "writes": [planned...]}` or `{"kind": "command", "name": str, "params": dict | None}`.
  - `class ScriptAPI(conn, registry, recorder)` with `port`, `find`, `props`, `details`, `cmd`, `set_props`, and class attribute `ArchicadError`.

- [ ] **Step 1: Write the failing tests**

Create `src/archicad_mcp/scripting/__init__.py` containing only:

```python
"""Run caller-supplied Python next to Archicad; see docs/scripting.md."""
```

Create `tests/test_scripting_api.py`:

```python
"""The `ac` object scripts see: reads go to Archicad, writes are only recorded."""
import pytest

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.gateway.registry import build_registry
from archicad_mcp.scripting.api import ArchicadError, Recorder, ScriptAPI
from tests.conftest import FakeCore
from tests.fixtures import api_replays


@pytest.fixture
def setup():
    official = dict(api_replays.OFFICIAL)
    official["API.SetPropertyValuesOfElements"] = {"executionResults": []}
    core = FakeCore(official=official, tapir=dict(api_replays.TAPIR))
    recorder = Recorder()
    ac = ScriptAPI(ArchicadConnection(19723, core=core), build_registry(), recorder)
    return ac, core, recorder


def _sent(core, name):
    return [p for c, p in core.calls if c == name]


def test_find_returns_the_guids(setup):
    ac, _, _ = setup
    assert sorted(ac.find([{"element_types": ["Wall"]}])) == ["w-1", "w-2"]


def test_find_with_bad_groups_raises_the_criteria_error(setup):
    ac, _, _ = setup
    with pytest.raises(ValueError):
        ac.find([{"logical_operator": "xor"}])


def test_props_reads_values(setup):
    ac, _, _ = setup
    assert ac.props(["w-1"], ["OFFICE/Fire Rating"]) == {
        "w-1": {"OFFICE/Fire Rating": "EI60"}}


def test_details_are_keyed_by_guid(setup):
    ac, _, _ = setup
    details = ac.details(["w-1", "w-2"])
    assert details["w-2"]["floorIndex"] == 1


def test_cmd_runs_a_read(setup):
    ac, _, _ = setup
    assert ac.cmd("GetProjectInfo")["projectName"] == "Test House"


def test_cmd_records_a_write_and_sends_nothing(setup):
    ac, core, recorder = setup
    # The Tapir schema wants real GUID syntax, unlike the fixture's "w-1".
    params = {"elements": [{"elementId": {"guid": "00000000-0000-0000-0000-000000000001"}}],
              "highlightedColors": [[255, 0, 0, 128]]}
    assert ac.cmd("HighlightElements", params) == {"recorded": 1}
    assert _sent(core, "HighlightElements") == []
    assert recorder.operations == [
        {"kind": "command", "name": "HighlightElements", "params": params}]


def test_cmd_validates_a_write_before_recording_it(setup):
    ac, _, recorder = setup
    with pytest.raises(ValueError, match="failed validation"):
        ac.cmd("HighlightElements", {})
    assert recorder.operations == []


def test_cmd_refuses_an_unknown_name_with_suggestions(setup):
    ac, _, _ = setup
    with pytest.raises(ValueError, match="Did you mean"):
        ac.cmd("GetProjectInf")


def test_an_archicad_refusal_raises_archicad_error(setup):
    ac, _, _ = setup
    # Read-classified and absent from the fixtures, so FakeCore refuses it the
    # way Archicad would.
    with pytest.raises(ArchicadError) as info:
        ac.cmd("API.GetActivePenTables")
    assert "no canned response" in info.value.message
    assert ac.ArchicadError is ArchicadError


def test_set_props_plans_into_the_recorder_and_sends_nothing(setup):
    ac, core, recorder = setup
    counts = ac.set_props([("w-2", "OFFICE/Fire Rating", "EI30"),
                           {"guid": "z-1", "property": "OFFICE/Fire Rating",
                            "value": "EI30"}])
    assert counts == {"planned": 1, "skipped": 1}
    assert _sent(core, "API.SetPropertyValuesOfElements") == []
    [op] = recorder.operations
    assert op["kind"] == "props"
    assert [w["guid"] for w in op["writes"]] == ["w-2"]
    assert recorder.skipped[0]["guid"] == "z-1"


def test_consecutive_set_props_calls_share_one_operation(setup):
    ac, _, recorder = setup
    ac.set_props([("w-1", "OFFICE/Fire Rating", "EI30")])
    ac.set_props([("w-2", "OFFICE/Fire Rating", "EI30")])
    assert len(recorder.operations) == 1
    assert len(recorder.operations[0]["writes"]) == 2


def test_port_is_exposed(setup):
    ac, _, _ = setup
    assert ac.port == 19723
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_scripting_api.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'archicad_mcp.scripting.api'`.

- [ ] **Step 3: Implement**

Create `src/archicad_mcp/scripting/api.py`:

```python
"""The `ac` object a script sees.

Reads go straight to Archicad. Writes never do: property writes are planned
(checked and turned into finished payloads) and write-classified commands are
validated, and both are appended to a Recorder. The server turns the recording
into a changeset that apply_changeset sends later, so nothing a script does can
change the model before the user has seen the preview.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass, field

from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.connection import ArchicadConnection, ArchicadUnavailableError
from archicad_mcp.core.element_data import plan_property_writes
from archicad_mcp.core.query import find_elements
from archicad_mcp.extract import (
    MAX_PROPERTY_FETCH_ELEMENTS,
    PROPERTY_FETCH_CHUNK,
    PropertyFetchTooWideError,
    element_payload,
    fetch_property_values,
)
from archicad_mcp.gateway.execute import _dispatch, _validate, describe_api_command
from archicad_mcp.gateway.registry import CommandInfo


class ArchicadError(Exception):
    """Archicad or Tapir refused a call made from a script. `code` may be None."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message if code is None else f"{message} (code {code})")
        self.message = message
        self.code = code


def _translated(method):
    # Scripts catch one exception type for every refusal, instead of learning
    # multiconn's error hierarchy.
    @functools.wraps(method)
    def wrapper(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except APIErrorBase as exc:
            raise ArchicadError(exc.message, getattr(exc, "code", None)) from None
        except ArchicadUnavailableError as exc:
            raise ArchicadError(str(exc)) from None
    return wrapper


@dataclass
class Recorder:
    """Planned operations, in the order the script asked for them."""
    operations: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)

    def add_writes(self, planned: list[dict]) -> None:
        if not planned:
            return
        # Consecutive set_props calls become one operation, so apply sends them
        # in as few batches as possible.
        if self.operations and self.operations[-1]["kind"] == "props":
            self.operations[-1]["writes"].extend(planned)
        else:
            self.operations.append({"kind": "props", "writes": list(planned)})

    def add_command(self, name: str, params: dict | None) -> int:
        self.operations.append({"kind": "command", "name": name, "params": params})
        return sum(1 for op in self.operations if op["kind"] == "command")


def _change(item) -> dict:
    if isinstance(item, dict):
        return {"guid": item["guid"], "property": item["property"],
                "value": item["value"]}
    guid, prop, value = item
    return {"guid": guid, "property": prop, "value": value}


class ScriptAPI:
    ArchicadError = ArchicadError

    def __init__(self, conn: ArchicadConnection, registry: dict[str, CommandInfo],
                 recorder: Recorder):
        self._conn = conn
        self._registry = registry
        self._recorder = recorder

    @property
    def port(self) -> int:
        return self._conn.port

    @_translated
    def find(self, groups: list[dict], selection_only: bool = False) -> list[str]:
        result = find_elements(self._conn, groups, selection_only)
        if "error" in result:
            raise ValueError(result["error"])
        return result["guids"]

    @_translated
    def props(self, guids, names) -> dict[str, dict[str, object]]:
        return fetch_property_values(self._conn, list(guids), list(names))

    @_translated
    def details(self, guids) -> dict[str, dict]:
        guids = list(guids)
        # Same ceiling as property reads: the element-count limit is about how
        # much one read asks of Archicad, and a script is no exception.
        if len(guids) > MAX_PROPERTY_FETCH_ELEMENTS:
            raise PropertyFetchTooWideError(
                f"Refusing to read element details across {len(guids)} elements "
                f"(limit {MAX_PROPERTY_FETCH_ELEMENTS}). Read in scoped chunks, or "
                "raise ARCHICAD_MCP_MAX_PROPERTY_ELEMENTS if you accept the risk.")
        out: dict[str, dict] = {}
        for start in range(0, len(guids), PROPERTY_FETCH_CHUNK):
            chunk = guids[start:start + PROPERTY_FETCH_CHUNK]
            response = self._conn.tapir("GetDetailsOfElements",
                                        {"elements": element_payload(chunk)})
            for guid, item in zip(chunk, response.get("detailsOfElements", [])):
                out[guid] = item
        return out

    @_translated
    def cmd(self, name: str, params: dict | None = None):
        info = self._registry.get(name)
        if info is None:
            raise ValueError(describe_api_command(self._registry, name)["error"])
        error = _validate(info, params)
        if error is not None:
            raise ValueError(error["error"])
        if info.access == "read":
            return _dispatch(self._conn, info, params)
        # Validated now, so a malformed payload fails in the preview rather
        # than halfway through apply_changeset.
        return {"recorded": self._recorder.add_command(name, params)}

    @_translated
    def set_props(self, changes) -> dict:
        planned, skipped = plan_property_writes(self._conn, [_change(c) for c in changes])
        self._recorder.add_writes(planned)
        self._recorder.skipped.extend(skipped)
        return {"planned": len(planned), "skipped": len(skipped)}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_scripting_api.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/scripting/__init__.py src/archicad_mcp/scripting/api.py tests/test_scripting_api.py
git commit -m "feat: the ac object scripts use, reads live and writes recorded

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Running a script and capping its output

**Files:**
- Create: `src/archicad_mcp/scripting/execute.py`
- Test: `tests/test_scripting_execute.py`

**Interfaces:**
- Consumes: any object as `ac` (tests pass a plain `object()`).
- Produces:
  - `@dataclass class Outcome` with `result: object`, `stdout: str`, `error: str | None`, `traceback: str | None`.
  - `execute(code: str, ac) -> Outcome`.
  - `to_jsonable(value) -> object`: round-trips through JSON with `default=str`.
  - `cap_text(text: str, limit: int) -> tuple[str, bool]`.
  - `cap_result(value, limit: int) -> tuple[object, bool]`: returns the value unchanged when its JSON form fits, else the capped JSON text.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scripting_execute.py`:

```python
"""execute() runs code with `ac` bound; failures are reported, never raised."""
from archicad_mcp.scripting.execute import cap_result, cap_text, execute, to_jsonable

AC = object()


def test_result_and_print_come_back():
    outcome = execute("print('hi')\nresult = {'n': ac is not None}", AC)
    assert outcome.result == {"n": True}
    assert outcome.stdout == "hi\n"
    assert outcome.error is None and outcome.traceback is None


def test_result_defaults_to_none():
    assert execute("x = 1", AC).result is None


def test_an_exception_is_reported_with_its_traceback():
    outcome = execute("print('before')\n1 / 0", AC)
    assert outcome.error == "ZeroDivisionError: division by zero"
    assert "ZeroDivisionError" in outcome.traceback
    assert outcome.stdout == "before\n"


def test_a_syntax_error_is_reported():
    outcome = execute("def (", AC)
    assert outcome.error.startswith("SyntaxError")


def test_sys_exit_is_reported_not_propagated():
    outcome = execute("import sys\nsys.exit(3)", AC)
    assert outcome.error == "script called exit(3)"


def test_traceback_keeps_only_the_last_lines():
    code = "def f(n):\n    return f(n - 1) if n else 1 / 0\nf(50)"
    outcome = execute(code, AC)
    assert len(outcome.traceback.splitlines()) <= 30


def test_to_jsonable_stringifies_what_json_cannot_carry():
    assert to_jsonable({"s": {1}, "n": 2}) == {"s": "{1}", "n": 2}


def test_cap_text():
    assert cap_text("abc", 5) == ("abc", False)
    text, cut = cap_text("x" * 12, 5)
    assert cut is True
    assert text == "xxxxx... [truncated, 7 more characters]"


def test_cap_result_keeps_small_values_as_values():
    assert cap_result({"a": [1, 2]}, 100) == ({"a": [1, 2]}, False)


def test_cap_result_turns_large_values_into_capped_text():
    value, cut = cap_result(list(range(1000)), 20)
    assert cut is True
    assert isinstance(value, str) and value.startswith("[0, 1, 2")
    assert "truncated" in value
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_scripting_execute.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'archicad_mcp.scripting.execute'`.

- [ ] **Step 3: Implement**

Create `src/archicad_mcp/scripting/execute.py`:

```python
"""Run one script with `ac` bound, and shape what comes back.

Kept free of process handling so that everything a script can do to its
namespace is testable in-process against FakeCore.
"""
from __future__ import annotations

import contextlib
import io
import json
import traceback
from dataclasses import dataclass

TRACEBACK_LINES = 30


@dataclass
class Outcome:
    result: object = None
    stdout: str = ""
    error: str | None = None
    traceback: str | None = None


def execute(code: str, ac) -> Outcome:
    """Run `code` with `ac` and `result` bound. Never raises for script errors."""
    buffer = io.StringIO()
    namespace = {"__name__": "__script__", "ac": ac, "result": None}
    outcome = Outcome()
    try:
        compiled = compile(code, "<script>", "exec")
        # Captured, because the process's stdout carries the reply document
        # and one stray print there would corrupt it.
        with contextlib.redirect_stdout(buffer):
            exec(compiled, namespace)  # noqa: S102 - running the caller's code is the tool
    except SystemExit as exc:
        outcome.error = f"script called exit({exc.code})"
    except Exception as exc:  # noqa: BLE001 - every script failure is reported
        outcome.error = f"{type(exc).__name__}: {exc}"
        lines = traceback.format_exc().rstrip().splitlines()
        outcome.traceback = "\n".join(lines[-TRACEBACK_LINES:])
    outcome.stdout = buffer.getvalue()
    outcome.result = namespace.get("result")
    return outcome


def to_jsonable(value):
    """JSON-safe copy of value; anything JSON cannot carry becomes str()."""
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def cap_text(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return f"{text[:limit]}... [truncated, {len(text) - limit} more characters]", True


def cap_result(value, limit: int) -> tuple[object, bool]:
    """The value itself when its JSON fits, otherwise its capped JSON text.

    A careless `result = all_the_data` must not flood the conversation, which
    is the whole reason scripts exist.
    """
    text = json.dumps(value, ensure_ascii=False)
    if len(text) <= limit:
        return value, False
    return cap_text(text, limit)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_scripting_execute.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/scripting/execute.py tests/test_scripting_execute.py
git commit -m "feat: run a script with ac bound and cap what it returns

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The child process

**Files:**
- Create: `src/archicad_mcp/scripting/runner.py`, `src/archicad_mcp/scripting/child.py`
- Test: `tests/test_scripting_child.py`

**Interfaces:**
- Consumes: `ScriptAPI`, `Recorder` (Task 2); `execute`, `to_jsonable` (Task 3); `build_registry()`; `ArchicadConnection(port)`.
- Produces: `run_child(code: str, port: int, timeout_s: float) -> dict`. On success, the reply is `{"result", "stdout", "error", "traceback", "operations", "skipped"}`. On failure, it is `{"error": str}` plus `"stderr"` when the child died.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scripting_child.py`:

```python
"""run_child spawns a real interpreter. Scripts here never touch Archicad."""
import time

from archicad_mcp.scripting.child import run_child

PORT = 19743  # valid for multiconn, and constructing a connection does not connect


def test_result_round_trips():
    reply = run_child("result = {'a': 1}", PORT, 30)
    assert reply["result"] == {"a": 1}
    assert reply["error"] is None
    assert reply["operations"] == [] and reply["skipped"] == []


def test_non_ascii_survives_the_pipe():
    reply = run_child("result = 'Pozicija vrat, čšž'\nprint('ščž')", PORT, 30)
    assert reply["result"] == "Pozicija vrat, čšž"
    assert reply["stdout"] == "ščž\n"


def test_an_exception_comes_back_as_an_error():
    reply = run_child("1 / 0", PORT, 30)
    assert reply["error"].startswith("ZeroDivisionError")
    assert "ZeroDivisionError" in reply["traceback"]
    assert reply["operations"] == []


def test_sys_exit_is_reported():
    reply = run_child("import sys\nsys.exit(3)", PORT, 30)
    assert reply["error"] == "script called exit(3)"


def test_a_hard_exit_reports_the_exit_code_and_stderr():
    # Flushed by hand: os._exit skips the flush, and stderr is only line-buffered.
    reply = run_child("import os, sys\nsys.stderr.write('boom\\n')\nsys.stderr.flush()\nos._exit(3)",
                      PORT, 30)
    assert "code 3" in reply["error"]
    assert "boom" in reply["stderr"]


def test_an_endless_script_is_stopped_at_the_timeout():
    started = time.monotonic()
    reply = run_child("while True:\n    pass", PORT, 2)
    assert reply == {"error": "script timed out after 2 s and was stopped"}
    assert time.monotonic() - started < 15


def test_a_huge_print_does_not_corrupt_the_reply():
    reply = run_child("print('x' * 200000)\nresult = 1", PORT, 30)
    assert reply["result"] == 1
    assert len(reply["stdout"]) == 200001
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_scripting_child.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'archicad_mcp.scripting.child'`.

- [ ] **Step 3: Implement the runner**

Create `src/archicad_mcp/scripting/runner.py`:

```python
"""Child-process entry point for run_script.

Reads {"code", "port"} as JSON on stdin and writes exactly one JSON document to
stdout. Started as `python -I -m archicad_mcp.scripting.runner`: -I keeps a
stray PYTHONPATH or user site-packages out of the interpreter that runs the
caller's code.
"""
from __future__ import annotations

import json
import sys


def main() -> int:
    # UTF-8 both ways, whatever the platform's locale: Windows pipes default to
    # a code page that cannot carry Slovenian property values.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    reply_stream = sys.stdout
    # Only the reply may reach the parent's pipe; anything else that writes to
    # stdout during setup (a library, a warning) goes to stderr instead.
    sys.stdout = sys.stderr
    request = json.loads(sys.stdin.read())

    from archicad_mcp.connection import ArchicadConnection
    from archicad_mcp.gateway.registry import build_registry
    from archicad_mcp.scripting.api import Recorder, ScriptAPI
    from archicad_mcp.scripting.execute import execute, to_jsonable

    recorder = Recorder()
    ac = ScriptAPI(ArchicadConnection(request["port"]), build_registry(), recorder)
    outcome = execute(request["code"], ac)
    failed = outcome.error is not None
    reply = {
        "result": to_jsonable(outcome.result),
        "stdout": outcome.stdout,
        "error": outcome.error,
        "traceback": outcome.traceback,
        # A script that raised offers nothing for apply: half a plan is worse
        # than none.
        "operations": [] if failed else recorder.operations,
        "skipped": [] if failed else recorder.skipped,
    }
    json.dump(reply, reply_stream, default=str, ensure_ascii=False)
    reply_stream.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Implement the parent side**

Create `src/archicad_mcp/scripting/child.py`:

```python
"""Start the runner in a fresh interpreter and collect its reply.

A child process rather than a thread: Python cannot stop a thread, so a
runaway loop would hold the server until restart, and a crash or exit in the
script would take the server with it.
"""
from __future__ import annotations

import json
import subprocess
import sys

STDERR_TAIL = 2000


def run_child(code: str, port: int, timeout_s: float) -> dict:
    """Run `code` against `port`. Returns the runner's reply, or {"error": ...}."""
    request = json.dumps({"code": code, "port": port}, ensure_ascii=False)
    try:
        # sys.executable is the server's own interpreter, which inside the
        # .mcpb bundle is the bundled runtime with this package installed.
        proc = subprocess.run(
            [sys.executable, "-I", "-m", "archicad_mcp.scripting.runner"],
            input=request, capture_output=True, text=True, encoding="utf-8",
            timeout=timeout_s)
    except subprocess.TimeoutExpired:
        # subprocess.run has already killed the child. Its captured print
        # output died with it, because the runner buffers it.
        return {"error": f"script timed out after {timeout_s:g} s and was stopped"}
    try:
        reply = json.loads(proc.stdout)
    except ValueError:
        reply = None
    if not isinstance(reply, dict):
        return {"error": f"script process exited with code {proc.returncode} "
                         "without a reply",
                "stderr": (proc.stderr or "")[-STDERR_TAIL:]}
    return reply
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_scripting_child.py -q`
Expected: all pass, in a few seconds (each spawn costs about 0.3 s, and the timeout test takes 2 s).

- [ ] **Step 6: Commit**

```bash
git add src/archicad_mcp/scripting/runner.py src/archicad_mcp/scripting/child.py tests/test_scripting_child.py
git commit -m "feat: run scripts in a child interpreter with a hard timeout

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Changesets and applying them

**Files:**
- Create: `src/archicad_mcp/scripting/changesets.py`, `src/archicad_mcp/scripting/apply.py`
- Test: `tests/test_scripting_changesets.py`

**Interfaces:**
- Consumes: `send_property_writes` (Task 1); `extract.fetch_property_cells`; `gateway.execute._dispatch`; `connection.InstanceInfo`; operation shapes from Task 2.
- Produces:
  - `class ChangesetError(Exception)`.
  - `@dataclass class Changeset` with `id`, `port`, `project`, `operations`, `skipped`, `created`, `expires_at`, `applied`, plus `property_writes() -> list[dict]` and `command_counts() -> dict[str, int]`.
  - `class ChangesetStore(ttl_s=1800, capacity=20, clock=time.monotonic)` with `add(port, project, operations, skipped) -> Changeset` and `lookup(changeset_id) -> Changeset` (raises `ChangesetError`).
  - `summarize(cs: Changeset) -> dict`.
  - `apply_changeset(store, changeset_id, confirm, registry, probe, connect) -> dict`, where `probe(port) -> InstanceInfo | None` and `connect(port) -> ArchicadConnection`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scripting_changesets.py`:

```python
"""Changesets: stored plans that apply once, exactly as previewed."""
import pytest

from archicad_mcp.connection import ArchicadConnection, InstanceInfo
from archicad_mcp.gateway.registry import build_registry
from archicad_mcp.scripting.apply import apply_changeset
from archicad_mcp.scripting.changesets import ChangesetError, ChangesetStore, summarize
from tests.conftest import FakeCore
from tests.fixtures import api_replays


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _write(guid, value, prop="OFFICE/Fire Rating"):
    return {"guid": guid, "property": prop, "current": None, "new": value,
            "payload": {"elementId": {"guid": guid},
                        "propertyId": {"guid": f"pid-{prop}"},
                        "propertyValue": {"type": "string", "status": "normal",
                                          "value": value}}}


def _props(*writes):
    return {"kind": "props", "writes": list(writes)}


# ---------- store ----------

def test_lookup_returns_what_was_added():
    store = ChangesetStore()
    cs = store.add(19723, "Test House", [_props(_write("w-1", "EI30"))], [])
    assert store.lookup(cs.id) is cs
    assert cs.id.startswith("cs-")


def test_changesets_expire():
    clock = Clock()
    store = ChangesetStore(ttl_s=60, clock=clock)
    cs = store.add(19723, "P", [], [])
    clock.now += 61
    with pytest.raises(ChangesetError, match="Unknown or expired"):
        store.lookup(cs.id)


def test_the_oldest_changeset_is_evicted_past_capacity():
    store = ChangesetStore(capacity=2)
    first = store.add(19723, "P", [], [])
    store.add(19723, "P", [], [])
    store.add(19723, "P", [], [])
    with pytest.raises(ChangesetError):
        store.lookup(first.id)


def test_an_applied_changeset_says_so():
    store = ChangesetStore()
    cs = store.add(19723, "P", [], [])
    cs.applied = True
    with pytest.raises(ChangesetError, match="already applied"):
        store.lookup(cs.id)


def test_summary_counts_and_samples():
    writes = [_write(f"g-{i}", "x") for i in range(25)]
    ops = [_props(*writes), {"kind": "command", "name": "HighlightElements",
                             "params": {}}]
    skipped = [{"guid": "s", "property": "p", "reason": "r"}]
    cs = ChangesetStore().add(19724, "Oprema-objekti", ops, skipped)
    summary = summarize(cs)
    assert summary["property_writes"] == 25
    assert summary["commands"] == {"HighlightElements": 1}
    assert summary["skipped"] == 1 and summary["skipped_sample"] == skipped
    assert len(summary["sample"]) == 20
    assert summary["sample"][0] == {"guid": "g-0", "property": "OFFICE/Fire Rating",
                                    "current": None, "new": "x"}
    assert summary["port"] == 19724 and summary["project"] == "Oprema-objekti"
    assert summary["expires_at"].endswith("Z")


# ---------- apply ----------

class Model:
    """A stateful fake: writes land in `values` and reads come from it."""

    def __init__(self):
        self.values = {}
        self.refuse = set()   # guids Archicad refuses (Teamwork 6001)
        self.ignore = set()   # guids Archicad reports as written but drops

    def set(self, params):
        results = []
        for item in params["elementPropertyValues"]:
            guid = item["elementId"]["guid"]
            if guid in self.refuse:
                results.append({"success": False, "error": {
                    "code": 6001, "message": "TeamWork permission denied"}})
                continue
            if guid not in self.ignore:
                key = (guid, item["propertyId"]["guid"])
                self.values[key] = item["propertyValue"]["value"]
            results.append({"success": True})
        return {"executionResults": results}

    def get(self, params):
        rows = []
        for el in params["elements"]:
            row = []
            for p in params["properties"]:
                key = (el["elementId"]["guid"], p["propertyId"]["guid"])
                cell = {"type": "string", "status": "normal"}
                if key in self.values:
                    cell["value"] = self.values[key]
                row.append({"propertyValue": cell})
            rows.append({"propertyValues": row})
        return {"propertyValuesForElements": rows}


@pytest.fixture
def world():
    model = Model()
    official = dict(api_replays.OFFICIAL)
    official["API.SetPropertyValuesOfElements"] = model.set
    official["API.GetPropertyValuesOfElements"] = model.get
    tapir = dict(api_replays.TAPIR)
    core = FakeCore(official=official, tapir=tapir)
    project = {"name": "Test House"}

    def probe(port):
        return InstanceInfo(port=port, version=29, build=5101,
                            project_name=project["name"], tapir_available=True,
                            tapir_version="1.5.9")

    def run(store, cs_id, confirm=True):
        return apply_changeset(store, cs_id, confirm, build_registry(), probe=probe,
                               connect=lambda port: ArchicadConnection(port, core=core))

    return model, core, project, run


def _sets(core):
    return [c for c, _ in core.calls if c == "API.SetPropertyValuesOfElements"]


def test_apply_refuses_without_confirm_and_repeats_the_summary(world):
    model, core, _, run = world
    store = ChangesetStore()
    cs = store.add(19723, "Test House", [_props(_write("w-1", "EI30"))], [])
    out = run(store, cs.id, confirm=False)
    assert "confirm=true" in out["error"]
    assert out["changeset"]["property_writes"] == 1
    assert _sets(core) == []
    assert store.lookup(cs.id) is cs  # still available


def test_apply_refuses_when_another_project_is_open(world):
    _, core, project, run = world
    store = ChangesetStore()
    cs = store.add(19723, "Test House", [_props(_write("w-1", "EI30"))], [])
    project["name"] = "Other Project"
    out = run(store, cs.id)
    assert "Other Project" in out["error"] and "Nothing was written" in out["error"]
    assert _sets(core) == []


def test_apply_writes_reads_back_and_is_single_use(world):
    model, core, _, run = world
    store = ChangesetStore()
    cs = store.add(19723, "Test House",
                   [_props(_write("w-1", "EI30"), _write("w-2", "EI90"))], [])
    out = run(store, cs.id)
    assert out == {"applied": 2, "failed": [], "mismatched": [], "commands": []}
    assert model.values[("w-1", "pid-OFFICE/Fire Rating")] == "EI30"
    with pytest.raises(ChangesetError, match="already applied"):
        store.lookup(cs.id)


def test_apply_reports_refusals_and_readback_mismatches(world):
    model, _, _, run = world
    model.refuse.add("w-1")
    model.ignore.add("w-2")
    store = ChangesetStore()
    cs = store.add(19723, "Test House",
                   [_props(_write("w-1", "EI30"), _write("w-2", "EI90"))], [])
    out = run(store, cs.id)
    assert out["applied"] == 1
    assert out["failed"] == [{"guid": "w-1", "property": "OFFICE/Fire Rating",
                              "code": 6001, "message": "TeamWork permission denied"}]
    # The refused element is not read back: its failure is already reported.
    assert out["mismatched"] == [{"guid": "w-2", "property": "OFFICE/Fire Rating",
                                  "sent": "EI90", "read": None}]


def test_apply_stops_at_the_first_failed_command(world):
    model, core, _, run = world
    store = ChangesetStore()
    # HighlightElements has no canned response, so FakeCore refuses it.
    ops = [_props(_write("w-1", "EI30")),
           {"kind": "command", "name": "HighlightElements", "params": {}},
           _props(_write("w-2", "EI90"))]
    cs = store.add(19723, "Test House", ops, [])
    out = run(store, cs.id)
    assert out["applied"] == 1
    assert out["commands"] == [{"name": "HighlightElements", "ok": False,
                                "code": None,
                                "message": "FakeCore: no canned response for "
                                           "HighlightElements"}]
    assert "stopped" in out
    assert ("w-2", "pid-OFFICE/Fire Rating") not in model.values


def test_apply_caps_long_reports(world):
    model, _, _, run = world
    guids = [f"g-{i}" for i in range(60)]
    model.refuse.update(guids)
    store = ChangesetStore()
    cs = store.add(19723, "Test House", [_props(*[_write(g, "x") for g in guids])], [])
    out = run(store, cs.id)
    assert len(out["failed"]) == 50
    assert out["failed_not_shown"] == 10
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_scripting_changesets.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'archicad_mcp.scripting.apply'`.

- [ ] **Step 3: Implement the store**

Create `src/archicad_mcp/scripting/changesets.py`:

```python
"""Planned operations waiting for apply_changeset.

In memory only: a server restart loses them, which costs one rerun of the
script. Single use, so an apply cannot be repeated by accident, and short-lived,
so a preview cannot be applied long after the model has moved on.
"""
from __future__ import annotations

import secrets
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

TTL_SECONDS = 30 * 60
CAPACITY = 20
SAMPLE = 20


class ChangesetError(Exception):
    """str(exc) is a user-facing, actionable message."""


@dataclass
class Changeset:
    id: str
    port: int
    project: str | None
    operations: list[dict]
    skipped: list[dict]
    created: float
    expires_at: datetime
    applied: bool = False

    def property_writes(self) -> list[dict]:
        return [w for op in self.operations if op["kind"] == "props"
                for w in op["writes"]]

    def command_counts(self) -> dict[str, int]:
        return dict(Counter(op["name"] for op in self.operations
                            if op["kind"] == "command"))


class ChangesetStore:
    def __init__(self, ttl_s: float = TTL_SECONDS, capacity: int = CAPACITY,
                 clock=time.monotonic):
        self._ttl = ttl_s
        self._capacity = capacity
        self._clock = clock
        self._items: dict[str, Changeset] = {}  # insertion order is age

    def add(self, port: int, project: str | None, operations: list[dict],
            skipped: list[dict]) -> Changeset:
        self._expire()
        cs = Changeset(id=f"cs-{secrets.token_hex(6)}", port=port, project=project,
                       operations=operations, skipped=skipped, created=self._clock(),
                       expires_at=datetime.now(timezone.utc) + timedelta(seconds=self._ttl))
        self._items[cs.id] = cs
        while len(self._items) > self._capacity:
            self._items.pop(next(iter(self._items)))
        return cs

    def lookup(self, changeset_id: str) -> Changeset:
        self._expire()
        cs = self._items.get(changeset_id)
        if cs is None:
            raise ChangesetError(
                f"Unknown or expired changeset '{changeset_id}'. Changesets last "
                f"{self._ttl / 60:g} minutes and are lost when the server "
                "restarts; run the script again.")
        if cs.applied:
            raise ChangesetError(
                f"Changeset '{changeset_id}' was already applied. Run the script "
                "again to plan a new one.")
        return cs

    def _expire(self) -> None:
        now = self._clock()
        for key in [k for k, cs in self._items.items() if now - cs.created > self._ttl]:
            del self._items[key]


def summarize(cs: Changeset) -> dict:
    writes = cs.property_writes()
    return {
        "id": cs.id,
        "expires_at": cs.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "port": cs.port,
        "project": cs.project,
        "property_writes": len(writes),
        "commands": cs.command_counts(),
        "skipped": len(cs.skipped),
        "sample": [{k: w[k] for k in ("guid", "property", "current", "new")}
                   for w in writes[:SAMPLE]],
        "skipped_sample": cs.skipped[:SAMPLE],
    }
```

- [ ] **Step 4: Implement apply**

Create `src/archicad_mcp/scripting/apply.py`:

```python
"""Send a stored changeset, exactly as it was previewed, and check the result."""
from __future__ import annotations

import math

from multiconn_archicad.errors import APIErrorBase

from archicad_mcp.core.element_data import send_property_writes
from archicad_mcp.extract import fetch_property_cells
from archicad_mcp.gateway.execute import _dispatch
from archicad_mcp.scripting.changesets import ChangesetError, ChangesetStore, summarize

REPORT_CAP = 50


def _same(read, sent) -> bool:
    if isinstance(sent, float) or isinstance(read, float):
        try:
            return math.isclose(float(read), float(sent), rel_tol=1e-9, abs_tol=1e-9)
        except (TypeError, ValueError):
            return False
    return read == sent


def _read_back(conn, writes: list[dict], failed: list[dict]) -> list[dict]:
    """Writes Archicad accepted but that do not read back as sent."""
    refused = {(f["guid"], f["property"]) for f in failed}
    done = [w for w in writes if (w["guid"], w["property"]) not in refused]
    if not done:
        return []
    cells = fetch_property_cells(conn, list(dict.fromkeys(w["guid"] for w in done)),
                                 sorted({w["property"] for w in done}))
    out = []
    for w in done:
        read = cells.get(w["guid"], {}).get(w["property"], {}).get("value")
        if not _same(read, w["new"]):
            out.append({"guid": w["guid"], "property": w["property"],
                        "sent": w["new"], "read": read})
    return out


def _report(applied: int, failed: list[dict], mismatched: list[dict],
            commands: list[dict], stopped: dict | None = None) -> dict:
    result = {"applied": applied, "failed": failed[:REPORT_CAP],
              "mismatched": mismatched[:REPORT_CAP], "commands": commands}
    if len(failed) > REPORT_CAP:
        result["failed_not_shown"] = len(failed) - REPORT_CAP
    if len(mismatched) > REPORT_CAP:
        result["mismatched_not_shown"] = len(mismatched) - REPORT_CAP
    if stopped is not None:
        result["stopped"] = stopped
    return result


def apply_changeset(store: ChangesetStore, changeset_id: str, confirm: bool,
                    registry: dict, probe, connect) -> dict:
    try:
        cs = store.lookup(changeset_id)
    except ChangesetError as exc:
        return {"error": str(exc)}
    if not confirm:
        return {"error": "This changeset changes the project and was not confirmed. "
                         "Re-send with confirm=true to apply it.",
                "changeset": summarize(cs)}
    info = probe(cs.port)
    if info is None or not info.project_open:
        return {"error": f"No Archicad with an open project answers on port "
                         f"{cs.port}. Nothing was written."}
    # Checked by name because that is what the API offers. Without Tapir both
    # names are None and the check cannot tell projects apart.
    if info.project_name != cs.project:
        return {"error": f"Port {cs.port} now has project {info.project_name!r} "
                         f"open, but this changeset was planned against "
                         f"{cs.project!r}. Nothing was written."}
    conn = connect(cs.port)
    # Single use from here on, whatever happens: a half-applied changeset must
    # not be re-sent blindly.
    cs.applied = True
    applied = 0
    failed: list[dict] = []
    mismatched: list[dict] = []
    commands: list[dict] = []
    for op in cs.operations:
        if op["kind"] == "props":
            try:
                count, refused = send_property_writes(conn, op["writes"])
            except APIErrorBase as exc:
                return _report(applied, failed, mismatched, commands,
                               stopped={"at": "property writes",
                                        "code": getattr(exc, "code", None),
                                        "message": exc.message})
            applied += count
            failed.extend(refused)
            mismatched.extend(_read_back(conn, op["writes"], refused))
            continue
        try:
            _dispatch(conn, registry[op["name"]], op["params"])
        except APIErrorBase as exc:
            # Later operations may depend on this one, so nothing after it runs.
            commands.append({"name": op["name"], "ok": False,
                             "code": getattr(exc, "code", None), "message": exc.message})
            return _report(applied, failed, mismatched, commands,
                           stopped={"at": op["name"]})
        commands.append({"name": op["name"], "ok": True})
    return _report(applied, failed, mismatched, commands)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_scripting_changesets.py -q`
Expected: all pass. `HighlightElements` goes through `conn.tapir`, which first checks `API.IsAddOnCommandAvailable` (present in the fixtures), then FakeCore raises `TapirCommandError` for the missing canned response; that is an `APIErrorBase`, so the command is reported and the run stops.

- [ ] **Step 6: Commit**

```bash
git add src/archicad_mcp/scripting/changesets.py src/archicad_mcp/scripting/apply.py tests/test_scripting_changesets.py
git commit -m "feat: single-use changesets, applied as previewed and read back

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The tools, the switch, and registration

**Files:**
- Create: `src/archicad_mcp/scripting/tools.py`
- Modify: `src/archicad_mcp/server.py` (`build_server`, banner functions, `main`, new `resolve_flag`, `check_script_transport`)
- Modify: `manifest.json`, `scripts/build_dashboard.py`
- Modify: `tests/test_manifest.py`, `tests/test_tool_annotations.py`, `tests/test_startup_banner.py`
- Test: `tests/test_scripting_tools.py` (create)

**Interfaces:**
- Consumes: everything from Tasks 2 to 5; `connection.get_connection`, `connection.probe_port`, `connection.ArchicadConnection`.
- Produces:
  - `scripting.tools.execute_script(store, code, port, timeout_s, max_output_chars) -> dict`.
  - `scripting.tools.register(mcp, default_port, tool_meta, guarded) -> None`, registering `run_script` and `apply_changeset`.
  - `server.build_server(..., enable_scripts: bool = False)`.
  - `server.resolve_flag(raw: str | None) -> bool`.
  - `server.check_script_transport(enable_scripts: bool, mode: str, transport: str, allow_over_http: bool) -> str | None`.
  - `format_startup_banner(..., scripts_enabled: bool = False)`, `emit_startup_banner(..., scripts_enabled: bool = False)`, `start_startup_banner(..., scripts_enabled: bool = False)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scripting_tools.py`:

```python
"""run_script and apply_changeset as MCP tools, and the switch that registers them."""
import json

import pytest
from fastmcp import Client

import archicad_mcp.scripting.tools as tools_mod
from archicad_mcp.connection import ArchicadConnection, InstanceInfo
from archicad_mcp.server import (
    build_server,
    check_script_transport,
    format_startup_banner,
    resolve_flag,
)
from tests.conftest import FakeCore
from tests.fixtures import api_replays


async def _names(**kwargs):
    async with Client(build_server(**kwargs)) as client:
        return {t.name for t in await client.list_tools()}


async def test_scripts_are_off_by_default():
    names = await _names(mode="full")
    assert "run_script" not in names and "apply_changeset" not in names


async def test_scripts_register_in_full_mode_when_enabled():
    names = await _names(mode="full", enable_scripts=True)
    assert {"run_script", "apply_changeset"} <= names


async def test_verdicts_mode_ignores_the_switch():
    assert "run_script" not in await _names(mode="verdicts", enable_scripts=True)


@pytest.mark.parametrize("raw,expected", [
    (None, False), ("", False), ("0", False), ("false", False), ("no", False),
    ("1", True), ("true", True), ("TRUE", True), (" yes ", True)])
def test_resolve_flag(raw, expected):
    assert resolve_flag(raw) is expected


def test_http_needs_the_second_flag():
    message = check_script_transport(True, "full", "http", False)
    assert "--allow-scripts-over-http" in message
    assert check_script_transport(True, "full", "http", True) is None
    assert check_script_transport(True, "full", "stdio", False) is None
    assert check_script_transport(False, "full", "http", False) is None
    # Verdicts mode never registers the tools, so there is nothing to refuse.
    assert check_script_transport(True, "verdicts", "http", False) is None


def test_banner_names_the_script_state():
    assert "scripts off" in format_startup_banner("full", 1, [])
    assert "scripts on" in format_startup_banner("full", 1, [], scripts_enabled=True)
    assert "scripts ignored in verdicts mode" in format_startup_banner(
        "verdicts", 1, [], scripts_enabled=True)


# ---------- execute_script, the logic behind run_script ----------

INFO = InstanceInfo(port=19723, version=29, build=5101, project_name="Test House",
                    tapir_available=True, tapir_version="1.5.9")


@pytest.fixture
def wired(monkeypatch):
    core = FakeCore(official=dict(api_replays.OFFICIAL), tapir=dict(api_replays.TAPIR))
    monkeypatch.setattr(tools_mod, "get_connection",
                        lambda port: ArchicadConnection(19723, core=core))
    monkeypatch.setattr(tools_mod, "probe_port", lambda port: INFO)
    calls = []

    def fake_run(reply):
        def run(code, port, timeout_s):
            calls.append((code, port, timeout_s))
            return reply
        monkeypatch.setattr(tools_mod.child, "run_child", run)
    return fake_run, calls


def _write(guid):
    return {"guid": guid, "property": "OFFICE/Fire Rating", "current": None,
            "new": "EI30", "payload": {"elementId": {"guid": guid}}}


def test_a_plan_becomes_a_changeset(wired):
    fake_run, calls = wired
    fake_run({"result": {"n": 1}, "stdout": "", "error": None, "traceback": None,
              "operations": [{"kind": "props", "writes": [_write("w-1")]}],
              "skipped": []})
    store = tools_mod.ChangesetStore()
    out = tools_mod.execute_script(store, "code", None, 120, 20000)
    assert out["result"] == {"n": 1}
    assert out["changeset"]["property_writes"] == 1
    assert out["changeset"]["project"] == "Test House"
    assert store.lookup(out["changeset"]["id"]).port == 19723
    assert calls == [("code", 19723, 120.0)]


def test_a_script_with_no_writes_offers_no_changeset(wired):
    fake_run, _ = wired
    fake_run({"result": 1, "stdout": "", "error": None, "traceback": None,
              "operations": [], "skipped": []})
    out = tools_mod.execute_script(tools_mod.ChangesetStore(), "c", None, 120, 20000)
    assert "changeset" not in out


def test_skipped_changes_are_shown_even_with_nothing_to_apply(wired):
    fake_run, _ = wired
    skipped = [{"guid": "d-1", "property": "D/P", "reason": "cannot hold '001'"}]
    fake_run({"result": None, "stdout": "", "error": None, "traceback": None,
              "operations": [], "skipped": skipped})
    out = tools_mod.execute_script(tools_mod.ChangesetStore(), "c", None, 120, 20000)
    assert "changeset" not in out
    assert out["skipped"] == 1 and out["skipped_sample"] == skipped


def test_an_error_is_passed_through_without_a_changeset(wired):
    fake_run, _ = wired
    fake_run({"result": None, "stdout": "partial", "error": "KeyError: 'x'",
              "traceback": "Traceback...", "operations": [], "skipped": []})
    out = tools_mod.execute_script(tools_mod.ChangesetStore(), "c", None, 120, 20000)
    assert out["error"] == "KeyError: 'x'"
    assert out["traceback"] == "Traceback..."
    assert out["stdout"] == "partial"
    assert "changeset" not in out


def test_outputs_are_capped_and_marked(wired):
    fake_run, _ = wired
    fake_run({"result": list(range(1000)), "stdout": "y" * 100, "error": None,
              "traceback": None, "operations": [], "skipped": []})
    out = tools_mod.execute_script(tools_mod.ChangesetStore(), "c", None, 120, 50)
    assert out["truncated"] == ["result", "stdout"]
    assert "truncated" in out["result"] and "truncated" in out["stdout"]


def test_the_timeout_is_clamped(wired):
    fake_run, calls = wired
    fake_run({"result": None, "stdout": "", "error": None, "traceback": None,
              "operations": [], "skipped": []})
    tools_mod.execute_script(tools_mod.ChangesetStore(), "c", None, 5000, 20000)
    tools_mod.execute_script(tools_mod.ChangesetStore(), "c", None, 0, 20000)
    assert [c[2] for c in calls] == [600.0, 1.0]


# ---------- end to end through the MCP layer ----------

async def test_run_then_apply_through_the_tools(wired, monkeypatch):
    fake_run, _ = wired
    fake_run({"result": None, "stdout": "", "error": None, "traceback": None,
              "operations": [{"kind": "props", "writes": [_write("w-1")]}],
              "skipped": []})
    written = []
    official = dict(api_replays.OFFICIAL)
    official["API.SetPropertyValuesOfElements"] = lambda p: (
        written.extend(p["elementPropertyValues"])
        or {"executionResults": [{"success": True}]})
    apply_core = FakeCore(official=official, tapir=dict(api_replays.TAPIR))
    monkeypatch.setattr(tools_mod, "open_connection",
                        lambda port: ArchicadConnection(port, core=apply_core))

    mcp = build_server(mode="full", enable_scripts=True)
    async with Client(mcp) as client:
        planned = json.loads((await client.call_tool(
            "run_script", {"code": "irrelevant"})).content[0].text)
        cs_id = planned["changeset"]["id"]
        refused = json.loads((await client.call_tool(
            "apply_changeset", {"changeset_id": cs_id})).content[0].text)
        assert "confirm=true" in refused["error"]
        assert written == []
        done = json.loads((await client.call_tool(
            "apply_changeset", {"changeset_id": cs_id, "confirm": True})).content[0].text)
    assert done["applied"] == 1
    assert written == [{"elementId": {"guid": "w-1"}}]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_scripting_tools.py -q`
Expected: collection error, `ImportError: cannot import name 'check_script_transport'` (or `No module named 'archicad_mcp.scripting.tools'`).

- [ ] **Step 3: Implement `scripting/tools.py`**

Create `src/archicad_mcp/scripting/tools.py`:

```python
"""run_script and apply_changeset.

Registered only when scripts are enabled (--enable-scripts or
ARCHICAD_MCP_SCRIPTS) in full mode. There is no sandbox: the switch, and the
refusal to serve scripts over http without a second flag, are the boundary.
"""
from __future__ import annotations

from archicad_mcp.connection import ArchicadConnection, get_connection, probe_port
from archicad_mcp.gateway.registry import build_registry
from archicad_mcp.scripting import child
from archicad_mcp.scripting.apply import apply_changeset as _apply
from archicad_mcp.scripting.changesets import SAMPLE, ChangesetStore, summarize
from archicad_mcp.scripting.execute import cap_result, cap_text

MAX_TIMEOUT_S = 600.0

# Module-level so tests can replace it; apply opens its own connection because
# the changeset, not the caller, names the port.
open_connection = ArchicadConnection

RUN_DESCRIPTION = (
    "Run a short Python script next to Archicad and return only what the script "
    "returns. Use it for tasks spanning many elements, so GUID lists and raw "
    "element data never pass through the conversation. The script sees `ac`: "
    "ac.find(groups) -> GUIDs (same groups as find_elements), "
    "ac.props(guids, ['Group/Name', ...]) -> {guid: {name: value}}, "
    "ac.details(guids) -> {guid: Tapir element details}, "
    "ac.cmd(name, params) for any API command, "
    "ac.set_props([(guid, 'Group/Name', value), ...]); errors raise "
    "ac.ArchicadError. Set `result` to what you want back; print() is captured "
    "too; both are capped at max_output_chars. NEVER WRITES: set_props and "
    "write commands are recorded into a changeset, returned as counts, 20 sample "
    "changes (current -> new) and skipped changes with reasons. Apply it with "
    "apply_changeset. Runs in a separate process, stopped after timeout_s "
    "(max 600).")

APPLY_DESCRIPTION = (
    "Apply a changeset planned by run_script: writes exactly what was previewed, "
    "in order, then reads every written property back. Refuses without "
    "confirm=true, and refuses if a different project is now open on the port. "
    "Single use; changesets expire after 30 minutes. Returns applied, failed "
    "(guid, property, code, message per refused element), mismatched readbacks, "
    "and command outcomes; a failed command stops the run.")


def execute_script(store: ChangesetStore, code: str, port: int | None,
                   timeout_s: float, max_output_chars: int) -> dict:
    # Resolve the port the way every tool does, so "several instances, pass
    # port" is answered before a child process starts.
    conn = get_connection(port)
    info = probe_port(conn.port)
    project = info.project_name if info is not None else None
    timeout = min(max(float(timeout_s), 1.0), MAX_TIMEOUT_S)
    limit = max(int(max_output_chars), 1)
    reply = child.run_child(code, conn.port, timeout)

    out: dict = {}
    truncated: list[str] = []
    if "result" in reply:
        out["result"], cut = cap_result(reply["result"], limit)
        if cut:
            truncated.append("result")
    if reply.get("stdout"):
        out["stdout"], cut = cap_text(reply["stdout"], limit)
        if cut:
            truncated.append("stdout")
    if reply.get("error"):
        out["error"] = reply["error"]
        for key in ("traceback", "stderr"):
            if reply.get(key):
                out[key] = reply[key]
    elif reply.get("operations"):
        cs = store.add(conn.port, project, reply["operations"], reply.get("skipped", []))
        out["changeset"] = summarize(cs)
    elif reply.get("skipped"):
        # Nothing to apply, but the caller has to learn why its writes vanished.
        out["skipped"] = len(reply["skipped"])
        out["skipped_sample"] = reply["skipped"][:SAMPLE]
    if truncated:
        out["truncated"] = truncated
    return out


def register(mcp, default_port, tool_meta, guarded) -> None:
    store = ChangesetStore()
    registry = build_registry()

    @mcp.tool(description=RUN_DESCRIPTION,
              **tool_meta("Run a script", read_only=False, destructive=True))
    @guarded
    def run_script(code: str, port: int | None = None, timeout_s: float = 120,
                   max_output_chars: int = 20000) -> dict:
        return execute_script(store, code, port if port is not None else default_port,
                              timeout_s, max_output_chars)

    @mcp.tool(description=APPLY_DESCRIPTION,
              **tool_meta("Apply a changeset", read_only=False, destructive=True))
    @guarded
    def apply_changeset(changeset_id: str, confirm: bool = False) -> dict:
        # Looked up at call time, so tests can replace them on this module.
        return _apply(store, changeset_id, confirm, registry,
                      probe=probe_port, connect=open_connection)
```

Note on the readOnly/destructive choice: `run_script` never writes through `ac`, but the script is unsandboxed Python, so it is annotated destructive. Say so in `tests/test_tool_annotations.py` (Step 6).

- [ ] **Step 4: Wire `build_server`, the banner and `main` in `server.py`**

In `build_server`, change the signature and the full-mode block:

```python
def build_server(
    mode: str = "full",
    rules_dir: Path | None = None,
    port: int | None = None,
    gdl_workspace: Path | None = None,
    enable_scripts: bool = False,
) -> FastMCP:
```

```python
    if mode == "full":
        _register_full_mode_tools(mcp, default_port)
        if gdl_workspace is not None:
            from archicad_mcp.gdl import tools as gdl_tools
            from archicad_mcp.gdl.workspace import Workspace
            gdl_tools.register(mcp, default_port, Workspace(gdl_workspace),
                               _tool_meta, _guarded)
        if enable_scripts:
            from archicad_mcp.scripting import tools as script_tools
            script_tools.register(mcp, default_port, _tool_meta, _guarded)
```

Add a helper above `format_startup_banner` and use it in both banner places:

```python
def _tool_gates(mode: str, gdl_workspace: Path | None, scripts_enabled: bool) -> str:
    """The ', GDL ...' and ', scripts ...' tail of the banner's config line."""
    # GDL tools register only in full mode with a workspace folder set
    if mode == "full" and gdl_workspace is not None:
        tail = f", GDL workspace {gdl_workspace}"
    else:
        tail = ", GDL tools off"
    if not scripts_enabled:
        return tail + ", scripts off"
    if mode != "full":
        return tail + ", scripts ignored in verdicts mode"
    return tail + ", scripts on"
```

In `format_startup_banner`, add the parameter `scripts_enabled: bool = False` after `rules_source`, and replace the GDL `if/else` block with:

```python
    head += _tool_gates(mode, gdl_workspace, scripts_enabled)
```

In `emit_startup_banner`, add `scripts_enabled: bool = False` after `rules_source`. In its `except` branch replace the GDL `if/else` with `prefix_config += _tool_gates(mode, gdl_workspace, scripts_enabled)`, and pass `scripts_enabled=scripts_enabled` to `format_startup_banner`.

In `start_startup_banner`, add `scripts_enabled: bool = False` after `rules_source` and extend the thread args to `(mode, rule_count, rule_errors, gdl_workspace, rules_source, scripts_enabled)`.

Add below `resolve_transport`:

```python
def resolve_flag(raw: str | None) -> bool:
    """True for 1, true or yes, case-insensitive. Unset or blank is False.

    An .mcpb bundle passes a boolean user_config field as the text "true" or
    "false", and an unfilled one as an empty string.
    """
    return (raw or "").strip().lower() in {"1", "true", "yes"}


def check_script_transport(enable_scripts: bool, mode: str, transport: str,
                           allow_over_http: bool) -> str | None:
    """Why this configuration must not start, or None.

    run_script executes arbitrary Python on this machine. Over stdio only the
    local client that launched the server can reach it; over http, anything that
    reaches the listening port can. That second case has to be asked for twice.
    """
    if enable_scripts and mode == "full" and transport == "http" and not allow_over_http:
        return ("--enable-scripts runs arbitrary Python on this machine, and "
                "--transport http lets any client that reaches the port call it. "
                "Pass --allow-scripts-over-http as well if that is intended, or "
                "drop --enable-scripts.")
    return None
```

In `main`, add the two arguments after `--http-port`:

```python
    parser.add_argument("--enable-scripts", action="store_true",
                        default=resolve_flag(os.environ.get("ARCHICAD_MCP_SCRIPTS")),
                        help="register run_script and apply_changeset (full mode "
                             "only); they run arbitrary Python on this machine")
    parser.add_argument("--allow-scripts-over-http", action="store_true",
                        help="permit --enable-scripts together with --transport http")
```

After `args, _ = parser.parse_known_args()`, before building the server:

```python
    refusal = check_script_transport(args.enable_scripts, args.mode, args.transport,
                                     args.allow_scripts_over_http)
    if refusal is not None:
        parser.error(refusal)
```

Pass `enable_scripts=args.enable_scripts` to `build_server`, and `scripts_enabled=args.enable_scripts` to `start_startup_banner` (as a keyword, after `server.archicad_rule_source`).

- [ ] **Step 5: Run the new tests**

Run: `.venv/bin/python -m pytest tests/test_scripting_tools.py tests/test_startup_banner.py -q`
Expected: all pass.

- [ ] **Step 6: Update the registration tests, manifest and dashboard catalog**

In `tests/test_manifest.py`, change the server build to include scripts, and add a test for the new field:

```python
    server = build_server(mode="full", gdl_workspace=tmp_path, enable_scripts=True)
```

```python
def test_manifest_declares_the_enable_scripts_switch():
    raw = json.loads(MANIFEST.read_text())
    field = raw["user_config"]["enable_scripts"]
    assert field["type"] == "boolean" and field["default"] is False
    assert raw["server"]["mcp_config"]["env"]["ARCHICAD_MCP_SCRIPTS"] == \
        "${user_config.enable_scripts}"
```

In `tests/test_tool_annotations.py`:
- In `_tools`, build with `build_server(mode=mode, gdl_workspace=Path(tmp), enable_scripts=True)` and extend the comment: scripts are enabled for the same reason the workspace is passed, so the conditional tools are covered.
- Add `"run_script", "apply_changeset"` to `WRITERS`, with a comment above the set's closing brace:

```python
    # run_script never writes through `ac`, but its code is unsandboxed Python,
    # so claiming readOnlyHint would let a client run arbitrary code unprompted.
    "run_script", "apply_changeset",
```

In `manifest.json`:
- Add to `server.mcp_config.env`: `"ARCHICAD_MCP_SCRIPTS": "${user_config.enable_scripts}"`.
- Add to `user_config`, after `gdl_workspace`:

```json
    "enable_scripts": {
      "type": "boolean",
      "title": "Enable scripts",
      "description": "Register run_script and apply_changeset, which run Python on this computer next to Archicad so bulk tasks take two calls instead of hundreds. There is no sandbox: turn this on only for clients you trust. Needs Mode set to 'full'. Scripts never write directly; apply_changeset writes what a script planned, after confirmation.",
      "default": false,
      "required": false
    }
```

- Add to `tools`, after `deploy_gdl_object`:

```json
    {
      "name": "run_script",
      "description": "Run a Python script next to Archicad; writes are recorded into a changeset, never applied."
    },
    {
      "name": "apply_changeset",
      "description": "Apply a changeset planned by run_script. Requires confirm=true."
    }
```

In `scripts/build_dashboard.py`, add to `TOOLS` after the `deploy_gdl_object` row:

```python
    # Scripting. Full mode plus --enable-scripts; as with the GDL rows, "mode"
    # alone does not describe the gate.
    {"name": "run_script", "cat": "Scripting", "mode": "full", "mutates": True,
     "desc": "Run a Python script next to Archicad with an ac object for reads. Writes are recorded into a changeset and previewed, never applied."},
    {"name": "apply_changeset", "cat": "Scripting", "mode": "full", "mutates": True,
     "desc": "Apply a changeset planned by run_script, then read the values back. Refuses without confirm=true or when another project is open."},
```

Do not regenerate `docs/api-dashboard.html` on this branch; it carries the owner's uncommitted definitions change.

- [ ] **Step 7: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass. If `test_the_dashboard_lists_every_tool` fails because its `_tools("full")` now includes the script tools, that is expected to pass once `TOOLS` has both rows; recheck the names.

- [ ] **Step 8: Check the CLI refusal by hand**

Run: `.venv/bin/python -m archicad_mcp.server --enable-scripts --transport http; echo "exit $?"`
Expected: the refusal message on stderr and `exit 2`.

- [ ] **Step 9: Commit**

```bash
git add src/archicad_mcp/scripting/tools.py src/archicad_mcp/server.py manifest.json scripts/build_dashboard.py tests/test_scripting_tools.py tests/test_manifest.py tests/test_tool_annotations.py
git commit -m "feat: run_script and apply_changeset behind an opt-in switch

Off by default; full mode only; --transport http needs
--allow-scripts-over-http as well. The banner reports the state.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Documentation

**Files:**
- Create: `docs/scripting.md`
- Modify: `README.md`, `docs/known-issues.md`

**Interfaces:**
- Consumes: the finished tools (Task 6).
- Produces: user-facing docs only.

- [ ] **Step 1: Write `docs/scripting.md`**

```markdown
# Scripting: run_script and apply_changeset

Bulk tasks (numbering 400 doors, filling a property from another one) take two
calls instead of hundreds. A short Python script runs next to Archicad, reads
what it needs locally, and plans its writes. Only what the script returns, plus
a preview of the planned changes, goes back to the conversation.

## Switching it on

Off by default. Turn it on with `--enable-scripts`, the environment variable
`ARCHICAD_MCP_SCRIPTS=1`, or **Enable scripts** in the Claude Desktop extension
settings. Mode must be `full`.

With `--transport http` the server refuses to start unless you also pass
`--allow-scripts-over-http`.

## Trust model

There is no sandbox. A script is ordinary Python running as your user on the
machine that runs Archicad, and it can do anything you can. The switch is the
boundary: enable scripts only for clients you trust, and do not expose them
over http to anything you would not let run code on this computer.

What the design does guarantee is that nothing written through `ac` reaches
the model before you have seen it: `run_script` only plans, and
`apply_changeset` needs `confirm=true`.

## The flow

1. `run_script(code, port)` runs the script in a separate process, stopped
   after `timeout_s` (default 120, at most 600). It returns `result`, captured
   `stdout` and, if the script planned writes, a `changeset`: its id, counts,
   20 sample changes (current and new value) and skipped changes with reasons.
2. `apply_changeset(changeset_id, confirm=true)` writes exactly what was
   previewed, reads every written property back, and returns `applied`,
   `failed` (guid, property, Archicad's code and message) and `mismatched`.

A changeset applies once, expires after 30 minutes, and is refused if a
different project is now open on its port. A server restart loses it; rerun
the script.

## The `ac` object

| Call | Returns |
|---|---|
| `ac.find(groups, selection_only=False)` | GUIDs, with the same groups as `find_elements` |
| `ac.props(guids, ["Group/Name", ...])` | `{guid: {name: value}}` |
| `ac.details(guids)` | `{guid: details}` from Tapir `GetDetailsOfElements` (`floorIndex`, `id`, `layerIndex`, and per type data such as a wall's `begCoordinate`) |
| `ac.cmd(name, params)` | a read's response; a write is validated and recorded, returning `{"recorded": n}` |
| `ac.set_props([(guid, "Group/Name", value), ...])` | `{"planned": n, "skipped": m}` |
| `ac.port` | the Archicad port |

Refusals from Archicad raise `ac.ArchicadError` (`.code`, `.message`).

`set_props` checks each value against the property's type before planning it.
An Integer property cannot take `"001"`; that change is skipped with a reason
naming the fix (a whole number, or change the property to String in Property
Manager). Enum properties are skipped too; set them with `ac.cmd` and the
enum's id.

Property and detail reads keep the element ceiling
(`ARCHICAD_MCP_MAX_PROPERTY_ELEMENTS`, default 5000), because wide reads have
crashed Archicad. Read in scoped chunks: by element type, story or
classification.

## Example: number doors per storey

```python
doors = ac.find([{"element_types": ["Door"]}])
floors = {g: d["floorIndex"] for g, d in ac.details(doors).items()}
ordered = sorted(doors, key=lambda g: floors[g])
ac.set_props([(g, "ELEA - Vrata/Pozicija", f"{i:03d}")
              for i, g in enumerate(ordered, start=1)])
result = {"doors": len(doors),
          "per_storey": {f: sum(1 for g in doors if floors[g] == f)
                         for f in sorted(set(floors.values()))}}
```

`run_script` returns the counts and a preview; `apply_changeset` with the
returned id and `confirm=true` writes them.
```

- [ ] **Step 2: Update README and known issues**

In `README.md`, find the section listing the full-mode tools (the list around the `set_element_data, create_elements, move_elements, delete_elements` line, near line 414) and add, after the GDL tools, a short **Scripting** subsection:

```markdown
### Scripting (opt-in)

`run_script` runs a short Python script next to Archicad and returns only what
it returns; its writes are planned into a changeset. `apply_changeset` writes
that changeset after `confirm=true` and reads the values back. Off by default:
enable with `--enable-scripts`, `ARCHICAD_MCP_SCRIPTS=1`, or **Enable scripts**
in the extension settings. There is no sandbox. See
[Scripting](docs/scripting.md).
```

If the README states a total tool count anywhere (search for `tools` next to a number), update it by two and note that the scripting tools are conditional, as the GDL tools are.

In `docs/known-issues.md`, append:

```markdown
## Scripts keep the property-read ceiling

`ac.props` and `ac.details` in `run_script` refuse to read across more than
`ARCHICAD_MCP_MAX_PROPERTY_ELEMENTS` elements (default 5000) in one call, like
every other property read. Read in scoped chunks.
```

- [ ] **Step 3: Check the docs for dashes**

Run: `grep -nP "[\x{2013}\x{2014}]" docs/scripting.md README.md docs/known-issues.md`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add docs/scripting.md README.md docs/known-issues.md
git commit -m "docs: scripting guide, README section, ceiling note

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Live acceptance (needs the owner's go-ahead)

**Files:** none changed. Record results in the plan's checkboxes and the final summary.

**Interfaces:**
- Consumes: the working server built from this branch.

This task writes to a real model. Ask the owner which model and port to use before starting. Never use the live CVP Teamwork model; use a scratch copy of it, or Oprema-objekti on port 19724.

- [ ] **Step 1: Start a server with scripts on, against the agreed port**

Run: `.venv/bin/python -c "from archicad_mcp.connection import probe_port; print(probe_port(19724))"`
Expected: an `InstanceInfo` with the expected project name. Then configure the MCP client to run `archicad-mcp --enable-scripts --port <port>` and reconnect.

- [ ] **Step 2: Dry run**

Call `run_script` with the door-numbering example from `docs/scripting.md`, adapted to a property that exists as String in that model. Expected: counts per storey, `changeset.property_writes` equal to the door count, 20 samples, and any skipped doors with reasons. Time the call.

- [ ] **Step 3: Apply**

Call `apply_changeset(changeset_id, confirm=true)`. Expected: `applied` equal to the planned count, `failed` listing only elements that really cannot be written (for example ones in a hotlinked module), `mismatched` empty.

- [ ] **Step 4: Read back in a script**

Call `run_script` with a script that reads the property for all doors and returns whether the values are unique. Expected: `{"unique": true, ...}`.

- [ ] **Step 5: Check the acceptance numbers**

Total wall time for steps 2 to 4 under 2 minutes; the characters that crossed the conversation (the three tool results plus the three scripts) under about 10k. Report both numbers.
