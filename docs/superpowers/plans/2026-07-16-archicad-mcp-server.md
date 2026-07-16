# Archicad 29 MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a cross-platform (macOS + Windows) Python MCP server for Archicad 29 with three tool tiers — verdict/QA tools driven by a local rules engine, curated core API tools, and a full-API gateway — per the approved spec at `docs/superpowers/specs/2026-07-16-archicad-mcp-design.md`.

**Architecture:** FastMCP (stdio) server on top of `multiconn_archicad`'s `CoreCommands` HTTP transport. A transport-free rules engine consumes a typed `ModelSnapshot` built by extractors; curated tools and a schema-validated gateway cover the full official + Tapir command surface. A `--mode verdicts|full` flag filters tool registration for privacy.

**Tech Stack:** Python 3.12+, uv, FastMCP ≥3, multiconn_archicad, pydantic, PyYAML, jsonschema, pytest + pytest-asyncio.

## Global Constraints

- Python `requires-python = ">=3.12"`; manage everything with `uv` (installed: 0.11.x).
- Dependencies: `fastmcp>=3.0,<4`, `multiconn_archicad>=0.6`, `pydantic>=2.7`, `pyyaml>=6`, `jsonschema>=4.21`, `httpx>=0.27`. Dev: `pytest>=8`, `pytest-asyncio>=0.24`. No other runtime deps.
- The rules package `src/archicad_mcp/rules/` must NEVER import from `fastmcp`, `archicad_mcp.server`, `archicad_mcp.connection`, `archicad_mcp.extract`, or `multiconn_archicad` (transport isolation — spec requirement).
- Verdict/`RuleResult` types must have NO fields capable of carrying element names, property values, or project info — only ids, booleans, counts, GUIDs, severities, rule messages (spec privacy requirement).
- All mutating tier-2 operations default to `dry_run=True`; `delete_elements`/`move_elements` additionally require `confirm=True` to execute.
- Official JSON API commands are invoked as `"API.<Name>"` via `CoreCommands.post_command`; Tapir commands as bare names via `CoreCommands.post_tapir_command`. Archicad's API listens on localhost ports 19723–19743.
- Tool error responses are actionable messages (e.g. "Start Archicad 29 and open a project."), never raw tracebacks.
- Tests never require a running Archicad except those marked `@pytest.mark.live` (excluded by default via `addopts = "-m 'not live'"`).
- Working directory: `/Users/alesd/Developer/Archicad MCP` (git repo on branch `main`, remote `origin` = https://github.com/alesdev88/Archicad-MCP.git). Commit after every task; do not push.
- External-shape caveat: exact JSON response shapes from Archicad are encoded once in `tests/fixtures/api_replays.py` and consumed only by `extract.py`/core modules. Task 14 (live smoke) verifies them against a real Archicad 29 and fixes mismatches in those two places only.

---

### Task 1: Project scaffold + running FastMCP server

**Files:**
- Create: `pyproject.toml`
- Create: `src/archicad_mcp/__init__.py`
- Create: `src/archicad_mcp/server.py`
- Test: `tests/test_server_smoke.py`

**Interfaces:**
- Produces: `archicad_mcp.server.build_server(mode: str = "full", rules_dir: pathlib.Path | None = None, port: int | None = None) -> fastmcp.FastMCP` and `archicad_mcp.server.main() -> None` (console entry `archicad-mcp`). Later tasks extend `build_server`.

- [ ] **Step 1: Write pyproject.toml**

```toml
[project]
name = "archicad-mcp"
version = "0.1.0"
description = "MCP server for Archicad 29: delivery-readiness QA rules plus full official + Tapir API access"
readme = "README.md"
requires-python = ">=3.12"
license = "MIT"
dependencies = [
    "fastmcp>=3.0,<4",
    "multiconn_archicad>=0.6",
    "pydantic>=2.7",
    "pyyaml>=6",
    "jsonschema>=4.21",
    "httpx>=0.27",
]

[project.scripts]
archicad-mcp = "archicad_mcp.server:main"

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.24"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/archicad_mcp"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = ["live: requires a running Archicad 29 instance with a model open"]
addopts = "-m 'not live'"
```

There is no `README.md` yet — create a one-line placeholder so builds work (Task 15 replaces it):

```markdown
# Archicad MCP

MCP server for Archicad 29. Documentation coming with v0.1.
```

- [ ] **Step 2: Write the smoke test**

`tests/test_server_smoke.py`:

```python
import json

from fastmcp import Client

from archicad_mcp.server import build_server


async def test_server_builds_and_lists_tools():
    mcp = build_server(mode="full")
    async with Client(mcp) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools}
        assert "ping" in names


async def test_ping_tool_answers():
    mcp = build_server(mode="full")
    async with Client(mcp) as client:
        result = await client.call_tool("ping", {})
        payload = json.loads(result.content[0].text)
        assert payload == {"status": "ok", "server": "archicad-mcp"}
```

- [ ] **Step 3: Write minimal server.py**

```python
from __future__ import annotations

import argparse
import os
from pathlib import Path

from fastmcp import FastMCP


def build_server(
    mode: str = "full",
    rules_dir: Path | None = None,
    port: int | None = None,
) -> FastMCP:
    if mode not in ("verdicts", "full"):
        raise ValueError(f"mode must be 'verdicts' or 'full', got {mode!r}")
    mcp = FastMCP("archicad-mcp")

    @mcp.tool(name="ping", description="Health check: confirms the archicad-mcp server is running.")
    def ping() -> dict:
        return {"status": "ok", "server": "archicad-mcp"}

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(prog="archicad-mcp")
    parser.add_argument("--mode", choices=["verdicts", "full"],
                        default=os.environ.get("ARCHICAD_MCP_MODE", "full"))
    parser.add_argument("--rules-dir", type=Path,
                        default=os.environ.get("ARCHICAD_MCP_RULES_DIR"))
    parser.add_argument("--port", type=int, default=None,
                        help="Archicad API port (19723-19743); auto-detected if omitted")
    args, _ = parser.parse_known_args()
    rules_dir = Path(args.rules_dir) if args.rules_dir else None
    build_server(mode=args.mode, rules_dir=rules_dir, port=args.port).run()


if __name__ == "__main__":
    main()
```

Note `parse_known_args`: `multiconn_archicad` also inspects `sys.argv` for `--port`/`--host`; unknown-arg tolerance avoids clashes in both directions.

Also create empty `src/archicad_mcp/__init__.py`.

- [ ] **Step 4: Install and run tests**

Run: `cd "/Users/alesd/Developer/Archicad MCP" && uv sync && uv run pytest -v`
Expected: 2 passed. (If `from fastmcp import Client` fails on the installed FastMCP 3.x, check `uv run python -c "import fastmcp; print(fastmcp.__version__)"` and consult `uv run python -c "import fastmcp; print(dir(fastmcp))"` — the in-memory client class ships in the top-level namespace in 2.x and 3.x. Fix the import, not the test intent.)

- [ ] **Step 5: Verify the console entry point starts**

Run: `uv run archicad-mcp --help`
Expected: usage text listing `--mode`, `--rules-dir`, `--port`.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml README.md src tests uv.lock
git commit -m "feat: project scaffold with runnable FastMCP server"
```

---

### Task 2: Rules types — ModelSnapshot, RuleResult, Verdict

**Files:**
- Create: `src/archicad_mcp/rules/__init__.py`
- Create: `src/archicad_mcp/rules/types.py`
- Test: `tests/rules/test_types.py` (and empty `tests/__init__.py`, `tests/rules/__init__.py`)

**Interfaces:**
- Produces (exact, used by every later rules task):

```python
Severity = Literal["error", "warning"]

@dataclass(frozen=True)
class ElementInfo:
    guid: str
    element_type: str = ""
    layer: str | None = None
    story: int | None = None
    classifications: dict[str, str | None] = field(default_factory=dict)  # system name -> classification id
    properties: dict[str, object] = field(default_factory=dict)           # property name -> value

@dataclass(frozen=True)
class ZoneInfo:
    guid: str
    number: str | None = None
    name: str | None = None

@dataclass(frozen=True)
class ModelSnapshot:
    elements: tuple[ElementInfo, ...] = ()
    layers: tuple[str, ...] = ()
    zones: tuple[ZoneInfo, ...] = ()
    ifc_properties: dict[str, dict[str, object]] | None = None  # guid -> {"Pset.Prop": value}; None = not extracted

@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    passed: bool
    severity: Severity
    message: str
    failure_count: int = 0
    failing_guids: tuple[str, ...] = ()
    skipped: bool = False
    skip_reason: str | None = None

@dataclass(frozen=True)
class Verdict:
    score: int
    passed: bool
    results: tuple[RuleResult, ...]
    def to_dict(self) -> dict: ...

class RuleConfigError(Exception): ...
```

- [ ] **Step 1: Write the failing tests**

`tests/rules/test_types.py`:

```python
import dataclasses

import pytest

from archicad_mcp.rules.types import ElementInfo, ModelSnapshot, RuleResult, Verdict


def test_rule_result_is_verdicts_only():
    """Privacy guard: RuleResult must not grow fields that can carry raw model data."""
    allowed = {"rule_id", "passed", "severity", "message",
               "failure_count", "failing_guids", "skipped", "skip_reason"}
    actual = {f.name for f in dataclasses.fields(RuleResult)}
    assert actual == allowed


def test_verdict_to_dict_round_trips():
    r = RuleResult(rule_id="walls-fire-rating", passed=False, severity="error",
                   message="1 element missing 'Fire Rating'",
                   failure_count=1, failing_guids=("g-1",))
    v = Verdict(score=50, passed=False, results=(r,))
    d = v.to_dict()
    assert d["score"] == 50
    assert d["pass"] is False
    assert d["results"][0]["rule"] == "walls-fire-rating"
    assert d["results"][0]["guids"] == ["g-1"]


def test_snapshot_defaults_are_empty_and_frozen():
    snap = ModelSnapshot()
    assert snap.elements == () and snap.ifc_properties is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.elements = ()  # type: ignore[misc]


def test_element_info_defaults():
    e = ElementInfo(guid="g-1")
    assert e.layer is None and e.properties == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/rules/test_types.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'archicad_mcp.rules'`

- [ ] **Step 3: Implement types.py**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["error", "warning"]


class RuleConfigError(Exception):
    """A rule definition (YAML or plugin) is invalid."""


@dataclass(frozen=True)
class ElementInfo:
    guid: str
    element_type: str = ""
    layer: str | None = None
    story: int | None = None
    classifications: dict[str, str | None] = field(default_factory=dict)
    properties: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ZoneInfo:
    guid: str
    number: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class ModelSnapshot:
    elements: tuple[ElementInfo, ...] = ()
    layers: tuple[str, ...] = ()
    zones: tuple[ZoneInfo, ...] = ()
    ifc_properties: dict[str, dict[str, object]] | None = None


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    passed: bool
    severity: Severity
    message: str
    failure_count: int = 0
    failing_guids: tuple[str, ...] = ()
    skipped: bool = False
    skip_reason: str | None = None

    def to_dict(self) -> dict:
        d = {"rule": self.rule_id, "pass": self.passed, "severity": self.severity,
             "message": self.message, "failures": self.failure_count,
             "guids": list(self.failing_guids)}
        if self.skipped:
            d["skipped"] = True
            d["skip_reason"] = self.skip_reason
        return d


@dataclass(frozen=True)
class Verdict:
    score: int
    passed: bool
    results: tuple[RuleResult, ...]

    def to_dict(self) -> dict:
        return {"score": self.score, "pass": self.passed,
                "results": [r.to_dict() for r in self.results]}
```

Create empty `src/archicad_mcp/rules/__init__.py`, `tests/__init__.py`, `tests/rules/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/rules/test_types.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/rules tests
git commit -m "feat: rules engine types (ModelSnapshot, RuleResult, Verdict)"
```

---

### Task 3: Rules engine — Rule protocol, run_rules, scoring

**Files:**
- Create: `src/archicad_mcp/rules/engine.py`
- Test: `tests/rules/test_engine.py`

**Interfaces:**
- Consumes: everything from `archicad_mcp.rules.types`.
- Produces:

```python
class Rule(Protocol):
    rule_id: str
    severity: Severity
    tags: frozenset[str]
    needs: frozenset[str]                # subset of {"elements","properties","classifications","layers","zones","ifc"}
    needed_properties: frozenset[str]    # property names extract must fetch
    def check(self, snapshot: ModelSnapshot) -> RuleResult: ...

def run_rules(rules: Sequence[Rule], snapshot: ModelSnapshot) -> Verdict
def data_needs(rules: Sequence[Rule]) -> frozenset[str]
def property_needs(rules: Sequence[Rule]) -> frozenset[str]
def filter_by_tag(rules: Sequence[Rule], tag: str | None) -> list[Rule]
```

Scoring (from spec): `score` = `round(100 * passed / non_skipped)` (100 when no non-skipped results); overall `passed` is True iff no non-skipped `error`-severity rule failed.

- [ ] **Step 1: Write the failing tests**

`tests/rules/test_engine.py`:

```python
from dataclasses import dataclass, field

from archicad_mcp.rules.engine import data_needs, filter_by_tag, property_needs, run_rules
from archicad_mcp.rules.types import ModelSnapshot, RuleResult


@dataclass
class StubRule:
    rule_id: str
    severity: str = "error"
    tags: frozenset = frozenset()
    needs: frozenset = frozenset({"elements"})
    needed_properties: frozenset = frozenset()
    result: RuleResult | None = None

    def check(self, snapshot):
        return self.result


def passing(rid, sev="error"):
    return StubRule(rid, sev, result=RuleResult(rid, True, sev, "ok"))


def failing(rid, sev="error"):
    return StubRule(rid, sev, result=RuleResult(
        rid, False, sev, "bad", failure_count=2, failing_guids=("a", "b")))


def skipped(rid):
    return StubRule(rid, result=RuleResult(
        rid, False, "error", "skipped", skipped=True, skip_reason="Tapir add-on required"))


def test_all_passing_scores_100():
    v = run_rules([passing("r1"), passing("r2")], ModelSnapshot())
    assert v.score == 100 and v.passed is True


def test_failing_error_rule_fails_verdict():
    v = run_rules([passing("r1"), failing("r2")], ModelSnapshot())
    assert v.score == 50 and v.passed is False


def test_failing_warning_lowers_score_but_passes():
    v = run_rules([passing("r1"), failing("r2", sev="warning")], ModelSnapshot())
    assert v.score == 50 and v.passed is True


def test_skipped_rules_excluded_from_score():
    v = run_rules([passing("r1"), skipped("r2")], ModelSnapshot())
    assert v.score == 100 and v.passed is True
    assert v.results[1].skipped is True


def test_no_rules_scores_100_and_passes():
    v = run_rules([], ModelSnapshot())
    assert v.score == 100 and v.passed is True


def test_data_and_property_needs_union():
    r1 = StubRule("r1", needs=frozenset({"elements", "properties"}),
                  needed_properties=frozenset({"Fire Rating"}))
    r2 = StubRule("r2", needs=frozenset({"zones"}))
    assert data_needs([r1, r2]) == frozenset({"elements", "properties", "zones"})
    assert property_needs([r1, r2]) == frozenset({"Fire Rating"})


def test_filter_by_tag():
    r1 = StubRule("r1", tags=frozenset({"ifc-delivery"}))
    r2 = StubRule("r2")
    assert filter_by_tag([r1, r2], "ifc-delivery") == [r1]
    assert filter_by_tag([r1, r2], None) == [r1, r2]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/rules/test_engine.py -v`
Expected: FAIL — `ModuleNotFoundError` on `archicad_mcp.rules.engine`.

- [ ] **Step 3: Implement engine.py**

```python
from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from archicad_mcp.rules.types import ModelSnapshot, RuleResult, Severity, Verdict


@runtime_checkable
class Rule(Protocol):
    rule_id: str
    severity: Severity
    tags: frozenset[str]
    needs: frozenset[str]
    needed_properties: frozenset[str]

    def check(self, snapshot: ModelSnapshot) -> RuleResult: ...


def run_rules(rules: Sequence[Rule], snapshot: ModelSnapshot) -> Verdict:
    results = tuple(rule.check(snapshot) for rule in rules)
    scored = [r for r in results if not r.skipped]
    if scored:
        score = round(100 * sum(1 for r in scored if r.passed) / len(scored))
    else:
        score = 100
    passed = all(r.passed for r in scored if r.severity == "error")
    return Verdict(score=score, passed=passed, results=results)


def data_needs(rules: Sequence[Rule]) -> frozenset[str]:
    return frozenset().union(*(r.needs for r in rules)) if rules else frozenset()


def property_needs(rules: Sequence[Rule]) -> frozenset[str]:
    return frozenset().union(*(r.needed_properties for r in rules)) if rules else frozenset()


def filter_by_tag(rules: Sequence[Rule], tag: str | None) -> list[Rule]:
    if tag is None:
        return list(rules)
    return [r for r in rules if tag in r.tags]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/rules/test_engine.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/archicad_mcp/rules/engine.py tests/rules/test_engine.py
git commit -m "feat: rules engine with scoring and needs aggregation"
```

---

### Task 4: Built-in rule types I — property-required, classification-required

**Files:**
- Create: `src/archicad_mcp/rules/builtin/__init__.py`
- Create: `src/archicad_mcp/rules/builtin/base.py`
- Create: `src/archicad_mcp/rules/builtin/property_required.py`
- Create: `src/archicad_mcp/rules/builtin/classification_required.py`
- Test: `tests/rules/test_builtin_property_classification.py`

**Interfaces:**
- Consumes: `types.py`, `engine.Rule` shape.
- Produces:

```python
# base.py
@dataclass(frozen=True)
class AppliesTo:
    element_type: str | None = None       # None or "*" = all elements
    def matches(self, element: ElementInfo) -> bool: ...
    @classmethod
    def from_config(cls, cfg: dict | None) -> "AppliesTo": ...

def common_fields(cfg: dict) -> tuple[str, Severity, frozenset[str], AppliesTo]
    # returns (rule_id, severity, tags, applies_to); raises RuleConfigError on missing id / bad severity

# each rule module
class PropertyRequiredRule:      # type name "property-required"
    @classmethod
    def from_config(cls, cfg: dict) -> "PropertyRequiredRule"   # raises RuleConfigError
class ClassificationRequiredRule:  # type name "classification-required"
    @classmethod
    def from_config(cls, cfg: dict) -> "ClassificationRequiredRule"
```

Both classes satisfy the `Rule` protocol (attributes `rule_id`, `severity`, `tags`, `needs`, `needed_properties`, method `check`).

- [ ] **Step 1: Write the failing tests**

`tests/rules/test_builtin_property_classification.py`:

```python
import pytest

from archicad_mcp.rules.builtin.classification_required import ClassificationRequiredRule
from archicad_mcp.rules.builtin.property_required import PropertyRequiredRule
from archicad_mcp.rules.types import ElementInfo, ModelSnapshot, RuleConfigError

WALL_OK = ElementInfo(guid="w-1", element_type="Wall", properties={"Fire Rating": "EI60"})
WALL_MISSING = ElementInfo(guid="w-2", element_type="Wall", properties={"Fire Rating": None})
WALL_EMPTY = ElementInfo(guid="w-3", element_type="Wall", properties={"Fire Rating": ""})
SLAB = ElementInfo(guid="s-1", element_type="Slab", properties={})


def make_prop_rule(**overrides):
    cfg = {"id": "walls-fire-rating", "type": "property-required",
           "property": "Fire Rating", "applies_to": {"element_type": "Wall"},
           "severity": "error", "tags": ["ifc-delivery"]}
    cfg.update(overrides)
    return PropertyRequiredRule.from_config(cfg)


def test_property_required_flags_missing_and_empty():
    rule = make_prop_rule()
    result = rule.check(ModelSnapshot(elements=(WALL_OK, WALL_MISSING, WALL_EMPTY, SLAB)))
    assert result.passed is False
    assert set(result.failing_guids) == {"w-2", "w-3"}
    assert result.failure_count == 2
    assert "Fire Rating" in result.message


def test_property_required_ignores_non_matching_types():
    rule = make_prop_rule()
    result = rule.check(ModelSnapshot(elements=(SLAB,)))
    assert result.passed is True and result.failure_count == 0


def test_property_required_declares_needs():
    rule = make_prop_rule()
    assert rule.needs == frozenset({"elements", "properties"})
    assert rule.needed_properties == frozenset({"Fire Rating"})
    assert rule.tags == frozenset({"ifc-delivery"})


def test_missing_id_raises_config_error():
    with pytest.raises(RuleConfigError):
        PropertyRequiredRule.from_config({"type": "property-required", "property": "X"})


def test_bad_severity_raises_config_error():
    with pytest.raises(RuleConfigError):
        make_prop_rule(severity="fatal")


def test_classification_required_flags_unclassified():
    rule = ClassificationRequiredRule.from_config(
        {"id": "all-classified", "type": "classification-required", "system": "Office System"})
    els = (ElementInfo(guid="e-1", element_type="Wall",
                       classifications={"Office System": "21-01"}),
           ElementInfo(guid="e-2", element_type="Wall",
                       classifications={"Office System": None}),
           ElementInfo(guid="e-3", element_type="Wall", classifications={}))
    result = rule.check(ModelSnapshot(elements=els))
    assert result.passed is False
    assert set(result.failing_guids) == {"e-2", "e-3"}
    assert rule.needs == frozenset({"elements", "classifications"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/rules/test_builtin_property_classification.py -v`
Expected: FAIL — `ModuleNotFoundError` on `archicad_mcp.rules.builtin`.

- [ ] **Step 3: Implement base.py**

```python
from __future__ import annotations

from dataclasses import dataclass

from archicad_mcp.rules.types import ElementInfo, RuleConfigError, Severity

_SEVERITIES = ("error", "warning")


@dataclass(frozen=True)
class AppliesTo:
    element_type: str | None = None

    def matches(self, element: ElementInfo) -> bool:
        if self.element_type in (None, "*"):
            return True
        return element.element_type == self.element_type

    @classmethod
    def from_config(cls, cfg: dict | None) -> "AppliesTo":
        cfg = cfg or {}
        return cls(element_type=cfg.get("element_type"))


def common_fields(cfg: dict) -> tuple[str, Severity, frozenset[str], AppliesTo]:
    rule_id = cfg.get("id")
    if not rule_id or not isinstance(rule_id, str):
        raise RuleConfigError(f"rule is missing a string 'id': {cfg!r}")
    severity = cfg.get("severity", "error")
    if severity not in _SEVERITIES:
        raise RuleConfigError(f"rule {rule_id!r}: severity must be one of {_SEVERITIES}, got {severity!r}")
    tags = frozenset(cfg.get("tags", []) or [])
    applies_to = AppliesTo.from_config(cfg.get("applies_to"))
    return rule_id, severity, tags, applies_to


def is_missing(value: object) -> bool:
    return value is None or value == ""
```

- [ ] **Step 4: Implement property_required.py**

```python
from __future__ import annotations

from dataclasses import dataclass

from archicad_mcp.rules.builtin.base import AppliesTo, common_fields, is_missing
from archicad_mcp.rules.types import ModelSnapshot, RuleConfigError, RuleResult, Severity

TYPE_NAME = "property-required"


@dataclass(frozen=True)
class PropertyRequiredRule:
    rule_id: str
    severity: Severity
    tags: frozenset[str]
    applies_to: AppliesTo
    property_name: str

    @property
    def needs(self) -> frozenset[str]:
        return frozenset({"elements", "properties"})

    @property
    def needed_properties(self) -> frozenset[str]:
        return frozenset({self.property_name})

    @classmethod
    def from_config(cls, cfg: dict) -> "PropertyRequiredRule":
        rule_id, severity, tags, applies_to = common_fields(cfg)
        prop = cfg.get("property")
        if not prop or not isinstance(prop, str):
            raise RuleConfigError(f"rule {rule_id!r}: 'property' (string) is required")
        return cls(rule_id, severity, tags, applies_to, prop)

    def check(self, snapshot: ModelSnapshot) -> RuleResult:
        failing = tuple(
            e.guid for e in snapshot.elements
            if self.applies_to.matches(e) and is_missing(e.properties.get(self.property_name))
        )
        return RuleResult(
            rule_id=self.rule_id,
            passed=not failing,
            severity=self.severity,
            message=f"{len(failing)} element(s) missing required property "
                    f"'{self.property_name}'",
            failure_count=len(failing),
            failing_guids=failing,
        )
```

- [ ] **Step 5: Implement classification_required.py**

```python
from __future__ import annotations

from dataclasses import dataclass

from archicad_mcp.rules.builtin.base import AppliesTo, common_fields, is_missing
from archicad_mcp.rules.types import ModelSnapshot, RuleConfigError, RuleResult, Severity

TYPE_NAME = "classification-required"


@dataclass(frozen=True)
class ClassificationRequiredRule:
    rule_id: str
    severity: Severity
    tags: frozenset[str]
    applies_to: AppliesTo
    system: str

    @property
    def needs(self) -> frozenset[str]:
        return frozenset({"elements", "classifications"})

    @property
    def needed_properties(self) -> frozenset[str]:
        return frozenset()

    @classmethod
    def from_config(cls, cfg: dict) -> "ClassificationRequiredRule":
        rule_id, severity, tags, applies_to = common_fields(cfg)
        system = cfg.get("system")
        if not system or not isinstance(system, str):
            raise RuleConfigError(f"rule {rule_id!r}: 'system' (classification system name) is required")
        return cls(rule_id, severity, tags, applies_to, system)

    def check(self, snapshot: ModelSnapshot) -> RuleResult:
        failing = tuple(
            e.guid for e in snapshot.elements
            if self.applies_to.matches(e) and is_missing(e.classifications.get(self.system))
        )
        return RuleResult(
            rule_id=self.rule_id,
            passed=not failing,
            severity=self.severity,
            message=f"{len(failing)} element(s) missing classification in system "
                    f"'{self.system}'",
            failure_count=len(failing),
            failing_guids=failing,
        )
```

Create empty `src/archicad_mcp/rules/builtin/__init__.py`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/rules/test_builtin_property_classification.py -v`
Expected: 7 passed.

- [ ] **Step 7: Commit**

```bash
git add src/archicad_mcp/rules/builtin tests/rules/test_builtin_property_classification.py
git commit -m "feat: property-required and classification-required rule types"
```

---

### Task 5: Built-in rule types II — layer-compliance, zone-number-required, ifc-property-required

**Files:**
- Create: `src/archicad_mcp/rules/builtin/layer_compliance.py`
- Create: `src/archicad_mcp/rules/builtin/zone_checks.py`
- Create: `src/archicad_mcp/rules/builtin/ifc_readiness.py`
- Test: `tests/rules/test_builtin_layers_zones_ifc.py`

**Interfaces:**
- Consumes: `base.py` helpers, `types.py`.
- Produces: `LayerComplianceRule` (type name `"layer-compliance"`, config keys `allowed: list[str]` and/or `pattern: str` regex — element layers must be in `allowed` OR match `pattern`), `ZoneNumberRequiredRule` (type name `"zone-number-required"`, no extra config), `IfcPropertyRequiredRule` (type name `"ifc-property-required"`, config key `property: "Pset_WallCommon.FireRating"`). All expose `from_config(cfg) -> Self`.
- `IfcPropertyRequiredRule.needs == frozenset({"elements", "ifc"})`; when `snapshot.ifc_properties is None` it returns a skipped `RuleResult` with `skip_reason="Tapir add-on required for IFC checks"`.

- [ ] **Step 1: Write the failing tests**

`tests/rules/test_builtin_layers_zones_ifc.py`:

```python
import pytest

from archicad_mcp.rules.builtin.ifc_readiness import IfcPropertyRequiredRule
from archicad_mcp.rules.builtin.layer_compliance import LayerComplianceRule
from archicad_mcp.rules.builtin.zone_checks import ZoneNumberRequiredRule
from archicad_mcp.rules.types import ElementInfo, ModelSnapshot, RuleConfigError, ZoneInfo


def test_layer_compliance_allowed_list():
    rule = LayerComplianceRule.from_config(
        {"id": "layers-std", "type": "layer-compliance", "allowed": ["A-WALL", "A-SLAB"]})
    els = (ElementInfo(guid="e-1", layer="A-WALL"),
           ElementInfo(guid="e-2", layer="Sketch"),
           ElementInfo(guid="e-3", layer=None))
    result = rule.check(ModelSnapshot(elements=els))
    assert set(result.failing_guids) == {"e-2", "e-3"}
    assert rule.needs == frozenset({"elements", "layers"})


def test_layer_compliance_pattern():
    rule = LayerComplianceRule.from_config(
        {"id": "layers-pattern", "type": "layer-compliance", "pattern": r"^[A-Z]-[A-Z]+$"})
    els = (ElementInfo(guid="e-1", layer="A-WALL"), ElementInfo(guid="e-2", layer="misc 01"))
    result = rule.check(ModelSnapshot(elements=els))
    assert result.failing_guids == ("e-2",)


def test_layer_compliance_requires_allowed_or_pattern():
    with pytest.raises(RuleConfigError):
        LayerComplianceRule.from_config({"id": "x", "type": "layer-compliance"})


def test_zone_number_required():
    rule = ZoneNumberRequiredRule.from_config({"id": "zones-numbered", "type": "zone-number-required"})
    zones = (ZoneInfo(guid="z-1", number="101", name="Office"),
             ZoneInfo(guid="z-2", number=None, name="Hall"),
             ZoneInfo(guid="z-3", number="", name=None))
    result = rule.check(ModelSnapshot(zones=zones))
    assert set(result.failing_guids) == {"z-2", "z-3"}
    assert rule.needs == frozenset({"zones"})


def test_ifc_property_required_checks_pset_values():
    rule = IfcPropertyRequiredRule.from_config(
        {"id": "ifc-fire", "type": "ifc-property-required",
         "property": "Pset_WallCommon.FireRating",
         "applies_to": {"element_type": "Wall"}})
    els = (ElementInfo(guid="w-1", element_type="Wall"),
           ElementInfo(guid="w-2", element_type="Wall"))
    snap = ModelSnapshot(elements=els, ifc_properties={
        "w-1": {"Pset_WallCommon.FireRating": "EI60"},
        "w-2": {},
    })
    result = rule.check(snap)
    assert result.failing_guids == ("w-2",)
    assert rule.needs == frozenset({"elements", "ifc"})


def test_ifc_rule_skips_without_tapir():
    rule = IfcPropertyRequiredRule.from_config(
        {"id": "ifc-fire", "type": "ifc-property-required", "property": "P.X"})
    result = rule.check(ModelSnapshot(elements=(ElementInfo(guid="w-1"),), ifc_properties=None))
    assert result.skipped is True
    assert result.skip_reason == "Tapir add-on required for IFC checks"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/rules/test_builtin_layers_zones_ifc.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement layer_compliance.py**

```python
from __future__ import annotations

import re
from dataclasses import dataclass

from archicad_mcp.rules.builtin.base import AppliesTo, common_fields
from archicad_mcp.rules.types import ModelSnapshot, RuleConfigError, RuleResult, Severity

TYPE_NAME = "layer-compliance"


@dataclass(frozen=True)
class LayerComplianceRule:
    rule_id: str
    severity: Severity
    tags: frozenset[str]
    applies_to: AppliesTo
    allowed: frozenset[str]
    pattern: str | None

    @property
    def needs(self) -> frozenset[str]:
        return frozenset({"elements", "layers"})

    @property
    def needed_properties(self) -> frozenset[str]:
        return frozenset()

    @classmethod
    def from_config(cls, cfg: dict) -> "LayerComplianceRule":
        rule_id, severity, tags, applies_to = common_fields(cfg)
        allowed = frozenset(cfg.get("allowed", []) or [])
        pattern = cfg.get("pattern")
        if not allowed and not pattern:
            raise RuleConfigError(f"rule {rule_id!r}: needs 'allowed' (list) and/or 'pattern' (regex)")
        if pattern is not None:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise RuleConfigError(f"rule {rule_id!r}: invalid pattern: {exc}") from exc
        return cls(rule_id, severity, tags, applies_to, allowed, pattern)

    def _layer_ok(self, layer: str | None) -> bool:
        if layer is None:
            return False
        if layer in self.allowed:
            return True
        if self.pattern is not None and re.match(self.pattern, layer):
            return True
        return False

    def check(self, snapshot: ModelSnapshot) -> RuleResult:
        failing = tuple(
            e.guid for e in snapshot.elements
            if self.applies_to.matches(e) and not self._layer_ok(e.layer)
        )
        return RuleResult(
            rule_id=self.rule_id,
            passed=not failing,
            severity=self.severity,
            message=f"{len(failing)} element(s) on non-compliant layers",
            failure_count=len(failing),
            failing_guids=failing,
        )
```

- [ ] **Step 4: Implement zone_checks.py**

```python
from __future__ import annotations

from dataclasses import dataclass

from archicad_mcp.rules.builtin.base import common_fields, is_missing
from archicad_mcp.rules.types import ModelSnapshot, RuleResult, Severity

TYPE_NAME = "zone-number-required"


@dataclass(frozen=True)
class ZoneNumberRequiredRule:
    rule_id: str
    severity: Severity
    tags: frozenset[str]

    @property
    def needs(self) -> frozenset[str]:
        return frozenset({"zones"})

    @property
    def needed_properties(self) -> frozenset[str]:
        return frozenset()

    @classmethod
    def from_config(cls, cfg: dict) -> "ZoneNumberRequiredRule":
        rule_id, severity, tags, _ = common_fields(cfg)
        return cls(rule_id, severity, tags)

    def check(self, snapshot: ModelSnapshot) -> RuleResult:
        failing = tuple(z.guid for z in snapshot.zones if is_missing(z.number))
        return RuleResult(
            rule_id=self.rule_id,
            passed=not failing,
            severity=self.severity,
            message=f"{len(failing)} zone(s) without a room number",
            failure_count=len(failing),
            failing_guids=failing,
        )
```

- [ ] **Step 5: Implement ifc_readiness.py**

```python
from __future__ import annotations

from dataclasses import dataclass

from archicad_mcp.rules.builtin.base import AppliesTo, common_fields, is_missing
from archicad_mcp.rules.types import ModelSnapshot, RuleConfigError, RuleResult, Severity

TYPE_NAME = "ifc-property-required"


@dataclass(frozen=True)
class IfcPropertyRequiredRule:
    rule_id: str
    severity: Severity
    tags: frozenset[str]
    applies_to: AppliesTo
    property_name: str  # "Pset_WallCommon.FireRating"

    @property
    def needs(self) -> frozenset[str]:
        return frozenset({"elements", "ifc"})

    @property
    def needed_properties(self) -> frozenset[str]:
        return frozenset()

    @classmethod
    def from_config(cls, cfg: dict) -> "IfcPropertyRequiredRule":
        rule_id, severity, tags, applies_to = common_fields(cfg)
        prop = cfg.get("property")
        if not prop or not isinstance(prop, str):
            raise RuleConfigError(f"rule {rule_id!r}: 'property' (\"Pset.Name\") is required")
        return cls(rule_id, severity, tags, applies_to, prop)

    def check(self, snapshot: ModelSnapshot) -> RuleResult:
        if snapshot.ifc_properties is None:
            return RuleResult(
                rule_id=self.rule_id, passed=False, severity=self.severity,
                message="IFC data not available",
                skipped=True, skip_reason="Tapir add-on required for IFC checks",
            )
        failing = tuple(
            e.guid for e in snapshot.elements
            if self.applies_to.matches(e)
            and is_missing(snapshot.ifc_properties.get(e.guid, {}).get(self.property_name))
        )
        return RuleResult(
            rule_id=self.rule_id,
            passed=not failing,
            severity=self.severity,
            message=f"{len(failing)} element(s) missing IFC property '{self.property_name}'",
            failure_count=len(failing),
            failing_guids=failing,
        )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/rules/ -v`
Expected: all pass (Tasks 2–5 suites).

- [ ] **Step 7: Commit**

```bash
git add src/archicad_mcp/rules/builtin tests/rules/test_builtin_layers_zones_ifc.py
git commit -m "feat: layer-compliance, zone-number-required, ifc-property-required rule types"
```

---

### Task 6: Rules loader — YAML files, plugin hook, bundled examples

**Files:**
- Create: `src/archicad_mcp/rules/loader.py`
- Create: `src/archicad_mcp/rules/examples/example-rules.yaml`
- Test: `tests/rules/test_loader.py`

**Interfaces:**
- Consumes: all rule classes from Task 4–5, `RuleConfigError`.
- Produces:

```python
RULE_TYPES: dict[str, type]   # "property-required" -> PropertyRequiredRule, etc. (all five)

@dataclass
class LoadedRules:
    rules: list[Rule]
    errors: list[str]         # human-readable load errors; loading never raises
    source: str               # directory path or "bundled examples"

def load_rules(rules_dir: Path | None) -> LoadedRules
```

Behavior: `rules_dir=None` → load the packaged `examples/` YAML. Otherwise read every `*.yaml`/`*.yml` in the directory (each file = YAML list of rule dicts, each dict has a `type` key naming an entry in `RULE_TYPES`), plus optional `custom_rules.py` which must define module-level `RULES: list` of Rule objects. Every bad file/rule appends to `errors` and loading continues.

- [ ] **Step 1: Write the failing tests**

`tests/rules/test_loader.py`:

```python
import textwrap

from archicad_mcp.rules.loader import RULE_TYPES, load_rules


def test_rule_types_registry_complete():
    assert set(RULE_TYPES) == {
        "property-required", "classification-required", "layer-compliance",
        "zone-number-required", "ifc-property-required",
    }


def test_load_rules_from_yaml_dir(tmp_path):
    (tmp_path / "office.yaml").write_text(textwrap.dedent("""\
        - id: walls-fire-rating
          type: property-required
          property: "Fire Rating"
          applies_to: { element_type: Wall }
          severity: error
          tags: [ifc-delivery]
        - id: zones-numbered
          type: zone-number-required
    """))
    loaded = load_rules(tmp_path)
    assert loaded.errors == []
    assert [r.rule_id for r in loaded.rules] == ["walls-fire-rating", "zones-numbered"]


def test_bad_rules_collect_errors_but_load_valid_ones(tmp_path):
    (tmp_path / "mixed.yaml").write_text(textwrap.dedent("""\
        - id: good-rule
          type: zone-number-required
        - id: bad-type
          type: no-such-type
        - type: property-required
          property: X
    """))
    (tmp_path / "broken.yaml").write_text("::: not yaml {{{")
    loaded = load_rules(tmp_path)
    assert [r.rule_id for r in loaded.rules] == ["good-rule"]
    assert len(loaded.errors) == 3  # unknown type, missing id, unparsable file


def test_custom_rules_plugin(tmp_path):
    (tmp_path / "custom_rules.py").write_text(textwrap.dedent("""\
        from archicad_mcp.rules.types import RuleResult

        class EverythingFineRule:
            rule_id = "custom-fine"
            severity = "warning"
            tags = frozenset()
            needs = frozenset({"elements"})
            needed_properties = frozenset()
            def check(self, snapshot):
                return RuleResult(self.rule_id, True, self.severity, "all fine")

        RULES = [EverythingFineRule()]
    """))
    loaded = load_rules(tmp_path)
    assert [r.rule_id for r in loaded.rules] == ["custom-fine"]


def test_none_dir_loads_bundled_examples():
    loaded = load_rules(None)
    assert loaded.errors == []
    assert loaded.rules, "bundled examples must provide at least one rule"
    assert loaded.source == "bundled examples"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/rules/test_loader.py -v`
Expected: FAIL — `ModuleNotFoundError` on loader.

- [ ] **Step 3: Implement loader.py**

```python
from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from archicad_mcp.rules.builtin.classification_required import ClassificationRequiredRule
from archicad_mcp.rules.builtin.ifc_readiness import IfcPropertyRequiredRule
from archicad_mcp.rules.builtin.layer_compliance import LayerComplianceRule
from archicad_mcp.rules.builtin.property_required import PropertyRequiredRule
from archicad_mcp.rules.builtin.zone_checks import ZoneNumberRequiredRule
from archicad_mcp.rules.engine import Rule
from archicad_mcp.rules.types import RuleConfigError

RULE_TYPES: dict[str, type] = {
    "property-required": PropertyRequiredRule,
    "classification-required": ClassificationRequiredRule,
    "layer-compliance": LayerComplianceRule,
    "zone-number-required": ZoneNumberRequiredRule,
    "ifc-property-required": IfcPropertyRequiredRule,
}

EXAMPLES_DIR = Path(__file__).parent / "examples"


@dataclass
class LoadedRules:
    rules: list[Rule] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    source: str = ""


def _load_yaml_file(path: Path, out: LoadedRules) -> None:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        out.errors.append(f"{path.name}: not valid YAML ({exc})")
        return
    if data is None:
        return
    if not isinstance(data, list):
        out.errors.append(f"{path.name}: expected a YAML list of rules")
        return
    for cfg in data:
        if not isinstance(cfg, dict):
            out.errors.append(f"{path.name}: rule entry is not a mapping: {cfg!r}")
            continue
        type_name = cfg.get("type")
        rule_cls = RULE_TYPES.get(type_name)
        if rule_cls is None:
            out.errors.append(f"{path.name}: unknown rule type {type_name!r} "
                              f"(known: {sorted(RULE_TYPES)})")
            continue
        try:
            out.rules.append(rule_cls.from_config(cfg))
        except RuleConfigError as exc:
            out.errors.append(f"{path.name}: {exc}")


def _load_plugin(path: Path, out: LoadedRules) -> None:
    try:
        spec = importlib.util.spec_from_file_location("archicad_mcp_custom_rules", path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        rules = getattr(module, "RULES", None)
        if not isinstance(rules, list):
            out.errors.append(f"{path.name}: must define module-level RULES = [...]")
            return
        out.rules.extend(rules)
    except Exception as exc:  # plugin code is user-supplied; never crash the server
        out.errors.append(f"{path.name}: failed to load ({exc})")


def load_rules(rules_dir: Path | None) -> LoadedRules:
    if rules_dir is None:
        out = LoadedRules(source="bundled examples")
        directory = EXAMPLES_DIR
    else:
        out = LoadedRules(source=str(rules_dir))
        directory = rules_dir
        if not directory.is_dir():
            out.errors.append(f"rules directory does not exist: {directory}")
            return out
    for path in sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml")):
        _load_yaml_file(path, out)
    plugin = directory / "custom_rules.py"
    if plugin.exists():
        _load_plugin(plugin, out)
    return out
```

- [ ] **Step 4: Write the bundled example rules**

`src/archicad_mcp/rules/examples/example-rules.yaml`:

```yaml
# Generic, public-safe example rules. Real office standards live OUTSIDE the
# repo in a local rules directory (--rules-dir / ARCHICAD_MCP_RULES_DIR).
- id: zones-have-numbers
  type: zone-number-required
  severity: error
  tags: [delivery]

- id: walls-classified
  type: classification-required
  system: "ARCHICAD Classification"
  applies_to: { element_type: Wall }
  severity: warning
  tags: [delivery]

- id: walls-fire-rating-ifc
  type: ifc-property-required
  property: "Pset_WallCommon.FireRating"
  applies_to: { element_type: Wall }
  severity: warning
  tags: [ifc-delivery]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/rules/test_loader.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/archicad_mcp/rules/loader.py src/archicad_mcp/rules/examples tests/rules/test_loader.py
git commit -m "feat: rules loader with YAML rule types, plugin hook, bundled examples"
```

---

### Task 7: Connection layer — discovery, Tapir detection, error types

**Files:**
- Create: `src/archicad_mcp/connection.py`
- Test: `tests/test_connection.py`
- Create: `tests/conftest.py` (FakeCore shared fixture)

**Interfaces:**
- Consumes: `multiconn_archicad.CoreCommands`, `multiconn_archicad.basic_types.Port`, `multiconn_archicad.errors.APIConnectionError/StandardAPIError/TapirCommandError`.
- Produces:

```python
PORT_RANGE = range(19723, 19744)

class ArchicadUnavailableError(Exception):
    """str(exc) is a user-facing, actionable message."""

@dataclass
class InstanceInfo:
    port: int
    version: int              # e.g. 29
    build: int
    project_name: str | None  # None when Tapir absent (official API has no project-name command)
    tapir_available: bool
    tapir_version: str | None
    def to_dict(self) -> dict

class ArchicadConnection:
    def __init__(self, port: int, core=None): ...   # core injectable for tests
    port: int
    def official(self, command: str, parameters: dict | None = None) -> dict
    def tapir(self, command: str, parameters: dict | None = None) -> dict   # raises ArchicadUnavailableError if Tapir missing
    def tapir_available(self) -> bool                                        # cached after first call

def probe_port(port: int, core=None) -> InstanceInfo | None
def discover_instances() -> list[InstanceInfo]
def get_connection(port: int | None) -> ArchicadConnection
    # port given -> connect or raise; port None -> exactly one instance required,
    # 0 instances -> ArchicadUnavailableError("No running Archicad found. Start Archicad 29 and open a project.")
    # >1 -> ArchicadUnavailableError listing ports: "Multiple Archicad instances running on ports [...]; pass 'port'."
```

- [ ] **Step 1: Write conftest.py with FakeCore**

`tests/conftest.py`:

```python
from multiconn_archicad.errors import StandardAPIError, TapirCommandError


class FakeCore:
    """Stands in for multiconn_archicad CoreCommands. Responses keyed by command
    name; a value may be a dict or a callable(parameters) -> dict. Missing keys
    raise the same error types the real transport raises."""

    def __init__(self, official=None, tapir=None):
        self.official_responses = dict(official or {})
        self.tapir_responses = dict(tapir or {})
        self.calls: list[tuple[str, dict | None]] = []

    def _lookup(self, table, command, parameters, error_cls):
        self.calls.append((command, parameters))
        if command not in table:
            raise error_cls(message=f"FakeCore: no canned response for {command}", code=None)
        value = table[command]
        return value(parameters) if callable(value) else value

    def post_command(self, command, parameters=None, timeout=None):
        return self._lookup(self.official_responses, command, parameters, StandardAPIError)

    def post_tapir_command(self, command, parameters=None, timeout=None):
        return self._lookup(self.tapir_responses, command, parameters, TapirCommandError)
```

- [ ] **Step 2: Write the failing tests**

`tests/test_connection.py`:

```python
import pytest

from archicad_mcp.connection import (
    ArchicadConnection,
    ArchicadUnavailableError,
    get_connection,
    probe_port,
)
from tests.conftest import FakeCore

PRODUCT_INFO = {"version": 29, "buildNumber": 5003, "languageCode": "INT"}
TAPIR_ON = {"API.GetProductInfo": PRODUCT_INFO,
            "API.IsAddOnCommandAvailable": {"available": True}}
TAPIR_OFF = {"API.GetProductInfo": PRODUCT_INFO,
             "API.IsAddOnCommandAvailable": {"available": False}}


def test_probe_port_with_tapir():
    core = FakeCore(official=TAPIR_ON,
                    tapir={"GetProjectInfo": {"projectName": "Test House", "untitled": False,
                                              "teamwork": False},
                           "GetAddOnVersion": {"version": "1.8.2"}})
    info = probe_port(19723, core=core)
    assert info is not None
    assert info.version == 29 and info.tapir_available is True
    assert info.project_name == "Test House"
    assert info.tapir_version == "1.8.2"


def test_probe_port_without_tapir_still_reports_instance():
    info = probe_port(19723, core=FakeCore(official=TAPIR_OFF))
    assert info is not None
    assert info.tapir_available is False and info.project_name is None


def test_probe_port_no_listener_returns_none(monkeypatch):
    from multiconn_archicad.errors import APIConnectionError

    class DeadCore:
        def post_command(self, *a, **k):
            raise APIConnectionError(message="connection refused", code=None)

    assert probe_port(19723, core=DeadCore()) is None


def test_connection_official_and_tapir_roundtrip():
    core = FakeCore(official=TAPIR_ON, tapir={"GetStories": {"stories": []}})
    conn = ArchicadConnection(19723, core=core)
    assert conn.official("API.GetProductInfo")["version"] == 29
    assert conn.tapir("GetStories") == {"stories": []}


def test_tapir_call_without_addon_gives_actionable_error():
    conn = ArchicadConnection(19723, core=FakeCore(official=TAPIR_OFF))
    with pytest.raises(ArchicadUnavailableError, match="Tapir add-on"):
        conn.tapir("GetStories")


def test_get_connection_no_instances(monkeypatch):
    monkeypatch.setattr("archicad_mcp.connection.discover_instances", lambda: [])
    with pytest.raises(ArchicadUnavailableError, match="Start Archicad"):
        get_connection(None)


def test_get_connection_multiple_instances(monkeypatch):
    from archicad_mcp.connection import InstanceInfo
    two = [InstanceInfo(19723, 29, 1, None, False, None),
           InstanceInfo(19724, 29, 1, None, False, None)]
    monkeypatch.setattr("archicad_mcp.connection.discover_instances", lambda: two)
    with pytest.raises(ArchicadUnavailableError, match="19723"):
        get_connection(None)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_connection.py -v`
Expected: FAIL — no module `archicad_mcp.connection`.

- [ ] **Step 4: Implement connection.py**

```python
from __future__ import annotations

from dataclasses import asdict, dataclass

from multiconn_archicad.basic_types import Port
from multiconn_archicad.core.core_commands import CoreCommands
from multiconn_archicad.errors import (
    APIConnectionError,
    APIErrorBase,
    CommandTimeoutError,
    RequestError,
    TapirCommandError,
)

PORT_RANGE = range(19723, 19744)

_TAPIR_PROBE = {"addOnCommandId": {"commandNamespace": "TapirCommand",
                                   "commandName": "GetAddOnVersion"}}


class ArchicadUnavailableError(Exception):
    """str(exc) is a user-facing, actionable message."""


@dataclass
class InstanceInfo:
    port: int
    version: int
    build: int
    project_name: str | None
    tapir_available: bool
    tapir_version: str | None

    def to_dict(self) -> dict:
        return asdict(self)


class ArchicadConnection:
    def __init__(self, port: int, core=None):
        self.port = port
        self._core = core if core is not None else CoreCommands(Port(port))
        self._tapir_available: bool | None = None

    def official(self, command: str, parameters: dict | None = None) -> dict:
        return self._core.post_command(command, parameters)

    def tapir_available(self) -> bool:
        if self._tapir_available is None:
            try:
                response = self.official("API.IsAddOnCommandAvailable", _TAPIR_PROBE)
                self._tapir_available = bool(response.get("available"))
            except APIErrorBase:
                self._tapir_available = False
        return self._tapir_available

    def tapir(self, command: str, parameters: dict | None = None) -> dict:
        if not self.tapir_available():
            raise ArchicadUnavailableError(
                f"'{command}' requires the Tapir add-on, which is not installed in "
                f"the Archicad instance on port {self.port}. Install it from "
                "https://github.com/ENZYME-APD/tapir-archicad-automation/releases "
                "(Options > Add-On Manager), then retry."
            )
        return self._core.post_tapir_command(command, parameters)


def probe_port(port: int, core=None) -> InstanceInfo | None:
    conn = ArchicadConnection(port, core=core)
    try:
        product = conn.official("API.GetProductInfo")
    except (APIConnectionError, RequestError, CommandTimeoutError):
        return None
    tapir = conn.tapir_available()
    project_name = None
    tapir_version = None
    if tapir:
        try:
            info = conn.tapir("GetProjectInfo")
            project_name = info.get("projectName")
            tapir_version = conn.tapir("GetAddOnVersion").get("version")
        except (APIErrorBase, TapirCommandError, ArchicadUnavailableError):
            pass
    return InstanceInfo(
        port=port,
        version=int(product.get("version", 0)),
        build=int(product.get("buildNumber", 0)),
        project_name=project_name,
        tapir_available=tapir,
        tapir_version=tapir_version,
    )


def discover_instances() -> list[InstanceInfo]:
    found = []
    for port in PORT_RANGE:
        info = probe_port(port)
        if info is not None:
            found.append(info)
    return found


def get_connection(port: int | None) -> ArchicadConnection:
    if port is not None:
        if probe_port(port) is None:
            raise ArchicadUnavailableError(
                f"No Archicad answering on port {port}. Is it running with a project open?")
        return ArchicadConnection(port)
    instances = discover_instances()
    if not instances:
        raise ArchicadUnavailableError(
            "No running Archicad found. Start Archicad 29 and open a project.")
    if len(instances) > 1:
        ports = ", ".join(str(i.port) for i in instances)
        raise ArchicadUnavailableError(
            f"Multiple Archicad instances running (ports {ports}). "
            "Pass the 'port' parameter to choose one; call list_instances to see them.")
    return ArchicadConnection(instances[0].port)
```

Note: error constructor signature `ErrorClass(message=..., code=...)` matches `multiconn_archicad.errors` (verified in its source). If instantiation fails in tests, check `uv run python -c "import inspect, multiconn_archicad.errors as e; print(inspect.signature(e.APIConnectionError.__init__))"` and adapt the FakeCore raises, not the production code.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_connection.py -v`
Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
git add src/archicad_mcp/connection.py tests/test_connection.py tests/conftest.py
git commit -m "feat: connection layer with discovery and Tapir graceful degradation"
```

---

### Task 8: Extractors — API replay fixtures + build_snapshot

**Files:**
- Create: `src/archicad_mcp/extract.py`
- Create: `tests/fixtures/__init__.py`
- Create: `tests/fixtures/api_replays.py`
- Test: `tests/test_extract.py`

**Interfaces:**
- Consumes: `ArchicadConnection` (Task 7), `ModelSnapshot`/`ElementInfo`/`ZoneInfo` (Task 2).
- Produces:

```python
# Built-in property names used for layer/story/zone data. THE single place to
# fix if live verification (Task 14) shows different nonLocalizedNames.
BUILTIN_LAYER = "General_LayerName"
BUILTIN_STORY = "General_HomeStoryNumber"
BUILTIN_ZONE_NUMBER = "Zone_ZoneNumber"
BUILTIN_ZONE_NAME = "Zone_ZoneName"

def resolve_property_ids(conn, names: Iterable[str]) -> dict[str, dict]
    # property name -> {"guid": ...} propertyId payload; unknown names omitted

def get_all_element_ids(conn) -> list[str]
def build_snapshot(conn, needs: frozenset[str], property_names: frozenset[str] = frozenset()) -> ModelSnapshot
```

`build_snapshot` fetches only what `needs` demands: `elements` → guids + types; `properties` → requested `property_names` + `BUILTIN_LAYER` (+ `BUILTIN_STORY`); `classifications` → all systems' classifications; `layers` → layer attribute names; `zones` → Zone-type elements with number/name built-ins; `ifc` → Tapir `GetIFCPropertiesOfElements` (sets `ifc_properties=None` when Tapir unavailable, so IFC rules self-skip).

Official command sequence used (all via `conn.official`): `API.GetAllElements`, `API.GetTypesOfElements`, `API.GetAllPropertyNames`, `API.GetPropertyIds`, `API.GetPropertyValuesOfElements`, `API.GetAllClassificationSystems`, `API.GetClassificationsOfElements`, `API.GetAttributesByType`, `API.GetLayerAttributes`.

- [ ] **Step 1: Write the replay fixtures**

`tests/fixtures/api_replays.py` — the ONE encoding of Archicad's response shapes (dicts as returned after multiconn strips the `{"succeeded":…, "result":…}` envelope). Task 14 verifies these against live Archicad 29:

```python
"""Canned official-API responses for a model with two walls and one zone.

Shapes follow the official JSON API documentation
(https://archicadapi.graphisoft.com/JSONInterfaceDocumentation/). If live
verification finds a mismatch, fix it HERE and in extract.py only.
"""

E = [{"elementId": {"guid": g}} for g in ("w-1", "w-2", "z-1")]

GET_ALL_ELEMENTS = {"elements": E}

GET_TYPES = {"types": [
    {"typeOfElement": {"elementId": {"guid": "w-1"}, "elementType": "Wall"}},
    {"typeOfElement": {"elementId": {"guid": "w-2"}, "elementType": "Wall"}},
    {"typeOfElement": {"elementId": {"guid": "z-1"}, "elementType": "Zone"}},
]}

GET_ALL_PROPERTY_NAMES = {"properties": [
    {"type": "BuiltIn", "nonLocalizedName": "General_LayerName"},
    {"type": "BuiltIn", "nonLocalizedName": "General_HomeStoryNumber"},
    {"type": "BuiltIn", "nonLocalizedName": "Zone_ZoneNumber"},
    {"type": "BuiltIn", "nonLocalizedName": "Zone_ZoneName"},
    {"type": "UserDefined", "localizedName": ["OFFICE", "Fire Rating"]},
]}


def get_property_ids(parameters):
    """Echo one propertyId per requested property, guid derived from its name."""
    out = []
    for p in parameters["properties"]:
        key = p.get("nonLocalizedName") or "/".join(p.get("localizedName", []))
        out.append({"propertyId": {"guid": f"pid-{key}"}})
    return {"properties": out}


def get_property_values(parameters):
    """Values keyed (element guid, property guid). NotAvailable errors for the zone's
    wall-only props mirror real API behavior."""
    values = {
        ("w-1", "pid-General_LayerName"): "A-WALL",
        ("w-2", "pid-General_LayerName"): "Sketch",
        ("z-1", "pid-General_LayerName"): "A-ZONE",
        ("w-1", "pid-General_HomeStoryNumber"): 1,
        ("w-2", "pid-General_HomeStoryNumber"): 2,
        ("z-1", "pid-General_HomeStoryNumber"): 1,
        ("w-1", "pid-OFFICE/Fire Rating"): "EI60",
        ("z-1", "pid-Zone_ZoneNumber"): "101",
        ("z-1", "pid-Zone_ZoneName"): "Office",
    }
    result = []
    for el in parameters["elements"]:
        row = []
        for prop in parameters["properties"]:
            key = (el["elementId"]["guid"], prop["propertyId"]["guid"])
            if key in values:
                row.append({"propertyValue": {"value": values[key], "status": "normal"}})
            else:
                row.append({"error": {"code": 1, "message": "Property not available"}})
        result.append({"propertyValues": row})
    return {"propertyValuesForElements": result}


GET_CLASSIFICATION_SYSTEMS = {"classificationSystems": [
    {"classificationSystemId": {"guid": "cs-1"}, "name": "ARCHICAD Classification",
     "version": "2.0"},
]}


def get_classifications(parameters):
    by_guid = {"w-1": {"classificationId": {"guid": "c-wall"}},
               "w-2": None, "z-1": {"classificationId": {"guid": "c-zone"}}}
    result = []
    for el in parameters["elements"]:
        item = by_guid[el["elementId"]["guid"]]
        one = {"classificationSystemId": {"guid": "cs-1"}}
        if item:
            one["classificationId"] = item["classificationId"]
        result.append({"classificationIds": [{"classificationId": one}]})
    return {"elementClassifications": result}


GET_ATTRIBUTES_BY_TYPE = {"attributeIds": [{"attributeId": {"guid": "layer-1"}},
                                           {"attributeId": {"guid": "layer-2"}}]}

GET_LAYER_ATTRIBUTES = {"attributes": [
    {"layerAttribute": {"attributeId": {"guid": "layer-1"}, "name": "A-WALL"}},
    {"layerAttribute": {"attributeId": {"guid": "layer-2"}, "name": "A-ZONE"}},
]}

TAPIR_IFC_PROPERTIES = {"elements": [
    {"elementId": {"guid": "w-1"},
     "properties": [{"propertySetName": "Pset_WallCommon",
                     "name": "FireRating", "value": "EI60"}]},
    {"elementId": {"guid": "w-2"}, "properties": []},
    {"elementId": {"guid": "z-1"}, "properties": []},
]}

OFFICIAL = {
    "API.GetProductInfo": {"version": 29, "buildNumber": 5003, "languageCode": "INT"},
    "API.IsAddOnCommandAvailable": {"available": True},
    "API.GetAllElements": GET_ALL_ELEMENTS,
    "API.GetTypesOfElements": GET_TYPES,
    "API.GetAllPropertyNames": GET_ALL_PROPERTY_NAMES,
    "API.GetPropertyIds": get_property_ids,
    "API.GetPropertyValuesOfElements": get_property_values,
    "API.GetAllClassificationSystems": GET_CLASSIFICATION_SYSTEMS,
    "API.GetClassificationsOfElements": get_classifications,
    "API.GetAttributesByType": GET_ATTRIBUTES_BY_TYPE,
    "API.GetLayerAttributes": GET_LAYER_ATTRIBUTES,
}

TAPIR = {
    "GetIFCPropertiesOfElements": TAPIR_IFC_PROPERTIES,
    "GetProjectInfo": {"projectName": "Test House", "untitled": False, "teamwork": False},
    "GetAddOnVersion": {"version": "1.8.2"},
}
```

- [ ] **Step 2: Write the failing tests**

`tests/test_extract.py`:

```python
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.extract import build_snapshot, resolve_property_ids
from tests.conftest import FakeCore
from tests.fixtures import api_replays


def make_conn(tapir=True):
    official = dict(api_replays.OFFICIAL)
    if not tapir:
        official["API.IsAddOnCommandAvailable"] = {"available": False}
    return ArchicadConnection(19723, core=FakeCore(
        official=official, tapir=api_replays.TAPIR if tapir else {}))


def test_resolve_property_ids_builtin_and_user():
    ids = resolve_property_ids(make_conn(), ["General_LayerName", "OFFICE/Fire Rating"])
    assert ids["General_LayerName"] == {"guid": "pid-General_LayerName"}
    assert ids["OFFICE/Fire Rating"] == {"guid": "pid-OFFICE/Fire Rating"}


def test_snapshot_elements_types_layers_properties():
    snap = build_snapshot(make_conn(),
                          needs=frozenset({"elements", "properties", "layers"}),
                          property_names=frozenset({"OFFICE/Fire Rating"}))
    by_guid = {e.guid: e for e in snap.elements}
    assert by_guid["w-1"].element_type == "Wall"
    assert by_guid["w-1"].layer == "A-WALL"
    assert by_guid["w-2"].layer == "Sketch"
    assert by_guid["w-1"].properties["OFFICE/Fire Rating"] == "EI60"
    assert by_guid["w-2"].properties["OFFICE/Fire Rating"] is None  # not available -> None
    assert set(snap.layers) == {"A-WALL", "A-ZONE"}


def test_snapshot_classifications():
    snap = build_snapshot(make_conn(), needs=frozenset({"elements", "classifications"}))
    by_guid = {e.guid: e for e in snap.elements}
    assert by_guid["w-1"].classifications == {"ARCHICAD Classification": "c-wall"}
    assert by_guid["w-2"].classifications == {"ARCHICAD Classification": None}


def test_snapshot_zones():
    snap = build_snapshot(make_conn(), needs=frozenset({"zones"}))
    assert len(snap.zones) == 1
    zone = snap.zones[0]
    assert (zone.guid, zone.number, zone.name) == ("z-1", "101", "Office")


def test_snapshot_ifc_with_tapir():
    snap = build_snapshot(make_conn(), needs=frozenset({"elements", "ifc"}))
    assert snap.ifc_properties == {
        "w-1": {"Pset_WallCommon.FireRating": "EI60"}, "w-2": {}, "z-1": {}}


def test_snapshot_ifc_without_tapir_is_none():
    snap = build_snapshot(make_conn(tapir=False), needs=frozenset({"elements", "ifc"}))
    assert snap.ifc_properties is None


def test_minimal_needs_makes_no_extra_calls():
    conn = make_conn()
    build_snapshot(conn, needs=frozenset({"elements"}))
    called = {c for c, _ in conn._core.calls}
    assert "API.GetPropertyValuesOfElements" not in called
    assert "API.GetClassificationsOfElements" not in called
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_extract.py -v`
Expected: FAIL — no module `archicad_mcp.extract`.

- [ ] **Step 4: Implement extract.py**

```python
from __future__ import annotations

from typing import Iterable

from archicad_mcp.connection import ArchicadConnection, ArchicadUnavailableError
from archicad_mcp.rules.types import ElementInfo, ModelSnapshot, ZoneInfo

BUILTIN_LAYER = "General_LayerName"
BUILTIN_STORY = "General_HomeStoryNumber"
BUILTIN_ZONE_NUMBER = "Zone_ZoneNumber"
BUILTIN_ZONE_NAME = "Zone_ZoneName"


def _property_name_payload(name: str) -> dict:
    """User-defined properties are addressed 'Group/Name'; everything else BuiltIn."""
    if "/" in name:
        group, prop = name.split("/", 1)
        return {"type": "UserDefined", "localizedName": [group, prop]}
    return {"type": "BuiltIn", "nonLocalizedName": name}


def resolve_property_ids(conn: ArchicadConnection, names: Iterable[str]) -> dict[str, dict]:
    names = list(names)
    if not names:
        return {}
    payload = [_property_name_payload(n) for n in names]
    response = conn.official("API.GetPropertyIds", {"properties": payload})
    out: dict[str, dict] = {}
    for name, item in zip(names, response.get("properties", [])):
        if "propertyId" in item:
            out[name] = item["propertyId"]
    return out


def get_all_element_ids(conn: ArchicadConnection) -> list[str]:
    response = conn.official("API.GetAllElements")
    return [e["elementId"]["guid"] for e in response.get("elements", [])]


def _element_payload(guids: list[str]) -> list[dict]:
    return [{"elementId": {"guid": g}} for g in guids]


def _fetch_types(conn, guids: list[str]) -> dict[str, str]:
    response = conn.official("API.GetTypesOfElements", {"elements": _element_payload(guids)})
    out = {}
    for item in response.get("types", []):
        t = item.get("typeOfElement", {})
        out[t.get("elementId", {}).get("guid", "")] = t.get("elementType", "")
    return out


def fetch_property_values(conn, guids: list[str], names: list[str]) -> dict[str, dict[str, object]]:
    """guid -> {property name -> value or None}."""
    if not guids or not names:
        return {g: {} for g in guids}
    ids = resolve_property_ids(conn, names)
    resolved = [n for n in names if n in ids]
    response = conn.official("API.GetPropertyValuesOfElements", {
        "elements": _element_payload(guids),
        "properties": [{"propertyId": ids[n]} for n in resolved],
    })
    out: dict[str, dict[str, object]] = {}
    rows = response.get("propertyValuesForElements", [])
    for guid, row in zip(guids, rows):
        values: dict[str, object] = {}
        for name, cell in zip(resolved, row.get("propertyValues", [])):
            pv = cell.get("propertyValue")
            values[name] = pv.get("value") if pv else None
        for name in names:
            values.setdefault(name, None)
        out[guid] = values
    return out


def _fetch_classifications(conn, guids: list[str]) -> dict[str, dict[str, str | None]]:
    systems = conn.official("API.GetAllClassificationSystems").get("classificationSystems", [])
    system_names = {s["classificationSystemId"]["guid"]: s["name"] for s in systems}
    response = conn.official("API.GetClassificationsOfElements", {
        "elements": _element_payload(guids),
        "classificationSystemIds": [{"classificationSystemId": {"guid": g}}
                                    for g in system_names],
    })
    out: dict[str, dict[str, str | None]] = {}
    for guid, row in zip(guids, response.get("elementClassifications", [])):
        per_system: dict[str, str | None] = {}
        for item in row.get("classificationIds", []):
            cid = item.get("classificationId", {})
            system_guid = cid.get("classificationSystemId", {}).get("guid")
            name = system_names.get(system_guid, system_guid or "?")
            inner = cid.get("classificationId")
            per_system[name] = inner.get("guid") if inner else None
        out[guid] = per_system
    return out


def _fetch_layer_names(conn) -> tuple[str, ...]:
    ids = conn.official("API.GetAttributesByType", {"attributeType": "Layer"})
    attribute_ids = ids.get("attributeIds", [])
    if not attribute_ids:
        return ()
    response = conn.official("API.GetLayerAttributes", {"attributeIds": attribute_ids})
    return tuple(a["layerAttribute"]["name"] for a in response.get("attributes", []))


def _fetch_ifc(conn, guids: list[str]) -> dict[str, dict[str, object]] | None:
    if not conn.tapir_available():
        return None
    try:
        response = conn.tapir("GetIFCPropertiesOfElements",
                              {"elements": _element_payload(guids)})
    except ArchicadUnavailableError:
        return None
    out: dict[str, dict[str, object]] = {}
    for item in response.get("elements", []):
        guid = item.get("elementId", {}).get("guid", "")
        props = {}
        for p in item.get("properties", []):
            props[f"{p.get('propertySetName')}.{p.get('name')}"] = p.get("value")
        out[guid] = props
    return out


def build_snapshot(conn: ArchicadConnection, needs: frozenset[str],
                   property_names: frozenset[str] = frozenset()) -> ModelSnapshot:
    elements: tuple[ElementInfo, ...] = ()
    layers: tuple[str, ...] = ()
    zones: tuple[ZoneInfo, ...] = ()
    ifc: dict[str, dict[str, object]] | None = None

    want_elements = bool(needs & {"elements", "properties", "classifications", "ifc", "zones"})
    guids = get_all_element_ids(conn) if want_elements else []
    types = _fetch_types(conn, guids) if guids else {}

    if "elements" in needs and guids:
        prop_names = set(property_names)
        if needs & {"properties", "layers"}:
            prop_names |= {BUILTIN_LAYER, BUILTIN_STORY}
        values = (fetch_property_values(conn, guids, sorted(prop_names))
                  if "properties" in needs or "layers" in needs else {g: {} for g in guids})
        classif = (_fetch_classifications(conn, guids)
                   if "classifications" in needs else {g: {} for g in guids})
        elements = tuple(
            ElementInfo(
                guid=g,
                element_type=types.get(g, ""),
                layer=values.get(g, {}).get(BUILTIN_LAYER),
                story=values.get(g, {}).get(BUILTIN_STORY),
                classifications=classif.get(g, {}),
                properties={k: v for k, v in values.get(g, {}).items()
                            if k not in (BUILTIN_LAYER, BUILTIN_STORY)},
            )
            for g in guids
        )

    if "layers" in needs:
        layers = _fetch_layer_names(conn)

    if "zones" in needs:
        zone_guids = [g for g in guids if types.get(g) == "Zone"]
        zone_values = fetch_property_values(
            conn, zone_guids, [BUILTIN_ZONE_NUMBER, BUILTIN_ZONE_NAME])
        zones = tuple(
            ZoneInfo(guid=g,
                     number=zone_values.get(g, {}).get(BUILTIN_ZONE_NUMBER),
                     name=zone_values.get(g, {}).get(BUILTIN_ZONE_NAME))
            for g in zone_guids
        )

    if "ifc" in needs:
        ifc = _fetch_ifc(conn, guids)

    return ModelSnapshot(elements=elements, layers=layers, zones=zones, ifc_properties=ifc)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_extract.py -v`
Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
git add src/archicad_mcp/extract.py tests/fixtures tests/test_extract.py
git commit -m "feat: needs-driven ModelSnapshot extraction with replay fixtures"
```

---

### Task 9: Tier 1 — verdict tools, actions, mode filter

**Files:**
- Create: `src/archicad_mcp/actions.py`
- Modify: `src/archicad_mcp/server.py` (replace the `ping` placeholder with real registration)
- Test: `tests/test_tier1_tools.py`

**Interfaces:**
- Consumes: `load_rules`/`LoadedRules` (Task 6), `run_rules`/`data_needs`/`property_needs`/`filter_by_tag` (Task 3), `build_snapshot` (Task 8), `get_connection`/`discover_instances`/`ArchicadUnavailableError` (Task 7).
- Produces in `actions.py`:

```python
def highlight_elements(conn, guids: list[str]) -> dict   # Tapir HighlightElements, returns {"highlighted": N}
def create_issues(conn, rule_id: str, message: str, guids: list[str]) -> dict
    # Tapir CreateIssue + AttachElementsToIssue, returns {"issue_created": True, "attached": N}
```

- Produces in `server.py`: full tier-1 registration inside `build_server`. Tool list (mode `verdicts` AND `full`): `list_instances`, `get_model_summary`, `list_rules`, `run_rule`, `audit_delivery_readiness`, `verify_ifc_export_readiness`, `highlight_failures`, `create_issues_from_failures`. The `ping` tool is removed (update `tests/test_server_smoke.py` to assert `list_rules` instead of `ping`, and delete the ping payload test).
- Every tool catches `ArchicadUnavailableError` and returns `{"error": str(exc)}` instead of raising.
- Helper produced for reuse by later tasks (module-level in `server.py`):

```python
def _tool_error(exc: Exception) -> dict: return {"error": str(exc)}
def _verdict_for(rules, request_port) -> Verdict   # connection -> needs -> snapshot -> run_rules
```

- [ ] **Step 1: Write the failing tests**

`tests/test_tier1_tools.py`:

```python
import json
import textwrap

import pytest
from fastmcp import Client

import archicad_mcp.server as server_mod
from archicad_mcp.connection import ArchicadConnection, InstanceInfo
from archicad_mcp.server import build_server
from tests.conftest import FakeCore
from tests.fixtures import api_replays

TIER1 = {"list_instances", "get_model_summary", "list_rules", "run_rule",
         "audit_delivery_readiness", "verify_ifc_export_readiness",
         "highlight_failures", "create_issues_from_failures"}


@pytest.fixture
def fake_archicad(monkeypatch):
    tapir = dict(api_replays.TAPIR)
    tapir["HighlightElements"] = {}
    tapir["CreateIssue"] = {"issueId": {"guid": "issue-1"}}
    tapir["AttachElementsToIssue"] = {}
    core = FakeCore(official=api_replays.OFFICIAL, tapir=tapir)
    conn = ArchicadConnection(19723, core=core)
    monkeypatch.setattr(server_mod, "get_connection", lambda port: conn)
    monkeypatch.setattr(
        server_mod, "discover_instances",
        lambda: [InstanceInfo(19723, 29, 5003, "Test House", True, "1.8.2")])
    return core


def rules_dir(tmp_path):
    (tmp_path / "rules.yaml").write_text(textwrap.dedent("""\
        - id: zones-numbered
          type: zone-number-required
        - id: walls-fire-ifc
          type: ifc-property-required
          property: "Pset_WallCommon.FireRating"
          applies_to: { element_type: Wall }
          tags: [ifc-delivery]
    """))
    return tmp_path


async def call(mcp, tool, args=None):
    async with Client(mcp) as client:
        result = await client.call_tool(tool, args or {})
        return json.loads(result.content[0].text)


async def test_verdicts_mode_registers_only_tier1(tmp_path):
    mcp = build_server(mode="verdicts", rules_dir=rules_dir(tmp_path))
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
    assert names == TIER1


async def test_list_instances(fake_archicad, tmp_path):
    mcp = build_server(mode="verdicts", rules_dir=rules_dir(tmp_path))
    payload = await call(mcp, "list_instances")
    assert payload["instances"][0]["port"] == 19723
    assert payload["instances"][0]["tapir_available"] is True


async def test_list_rules_reports_loaded_rules(tmp_path):
    mcp = build_server(mode="verdicts", rules_dir=rules_dir(tmp_path))
    payload = await call(mcp, "list_rules")
    assert {r["id"] for r in payload["rules"]} == {"zones-numbered", "walls-fire-ifc"}
    assert payload["errors"] == []


async def test_audit_returns_scored_verdict(fake_archicad, tmp_path):
    mcp = build_server(mode="verdicts", rules_dir=rules_dir(tmp_path))
    payload = await call(mcp, "audit_delivery_readiness")
    # zone z-1 has number 101 -> zones-numbered passes; w-2 misses IFC FireRating -> fails
    by_rule = {r["rule"]: r for r in payload["results"]}
    assert by_rule["zones-numbered"]["pass"] is True
    assert by_rule["walls-fire-ifc"]["pass"] is False
    assert by_rule["walls-fire-ifc"]["guids"] == ["w-2"]
    assert payload["score"] == 50


async def test_audit_with_ruleset_tag_filters(fake_archicad, tmp_path):
    mcp = build_server(mode="verdicts", rules_dir=rules_dir(tmp_path))
    payload = await call(mcp, "audit_delivery_readiness", {"ruleset": "ifc-delivery"})
    assert [r["rule"] for r in payload["results"]] == ["walls-fire-ifc"]


async def test_run_rule_single(fake_archicad, tmp_path):
    mcp = build_server(mode="verdicts", rules_dir=rules_dir(tmp_path))
    payload = await call(mcp, "run_rule", {"rule_id": "zones-numbered"})
    assert payload["results"][0]["rule"] == "zones-numbered"


async def test_run_rule_unknown_id_is_actionable(fake_archicad, tmp_path):
    mcp = build_server(mode="verdicts", rules_dir=rules_dir(tmp_path))
    payload = await call(mcp, "run_rule", {"rule_id": "nope"})
    assert "nope" in payload["error"] and "zones-numbered" in payload["error"]


async def test_get_model_summary_aggregates_only(fake_archicad, tmp_path):
    mcp = build_server(mode="verdicts", rules_dir=rules_dir(tmp_path))
    payload = await call(mcp, "get_model_summary")
    assert payload["by_type"] == {"Wall": 2, "Zone": 1}
    assert payload["by_layer"] == {"A-WALL": 1, "Sketch": 1, "A-ZONE": 1}
    assert payload["by_story"] == {"1": 2, "2": 1}
    assert "elements" not in payload  # aggregates only, no raw dumps


async def test_highlight_failures_calls_tapir(fake_archicad, tmp_path):
    mcp = build_server(mode="verdicts", rules_dir=rules_dir(tmp_path))
    payload = await call(mcp, "highlight_failures", {"rule_id": "walls-fire-ifc"})
    assert payload["highlighted"] == 1
    assert any(c == "HighlightElements" for c, _ in fake_archicad.calls)


async def test_create_issues_from_failures(fake_archicad, tmp_path):
    mcp = build_server(mode="verdicts", rules_dir=rules_dir(tmp_path))
    payload = await call(mcp, "create_issues_from_failures", {"rule_id": "walls-fire-ifc"})
    assert payload["issue_created"] is True and payload["attached"] == 1


async def test_archicad_down_gives_actionable_error(monkeypatch, tmp_path):
    from archicad_mcp.connection import ArchicadUnavailableError

    def boom(port):
        raise ArchicadUnavailableError("No running Archicad found. Start Archicad 29 and open a project.")

    monkeypatch.setattr(server_mod, "get_connection", boom)
    mcp = build_server(mode="verdicts", rules_dir=rules_dir(tmp_path))
    payload = await call(mcp, "audit_delivery_readiness")
    assert payload["error"].startswith("No running Archicad")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tier1_tools.py -v`
Expected: FAIL — tools not registered / imports missing.

- [ ] **Step 3: Implement actions.py**

```python
from __future__ import annotations

from archicad_mcp.connection import ArchicadConnection

_HIGHLIGHT_COLOR = [50, 255, 100, 100]      # green-ish, semi-transparent
_OTHER_COLOR = [0, 0, 255, 128]


def _element_payload(guids: list[str]) -> list[dict]:
    return [{"elementId": {"guid": g}} for g in guids]


def highlight_elements(conn: ArchicadConnection, guids: list[str]) -> dict:
    if not guids:
        return {"highlighted": 0}
    conn.tapir("HighlightElements", {
        "elements": _element_payload(guids),
        "highlightedColors": [_HIGHLIGHT_COLOR for _ in guids],
        "wireframe3D": True,
        "nonHighlightedColor": _OTHER_COLOR,
    })
    return {"highlighted": len(guids)}


def create_issues(conn: ArchicadConnection, rule_id: str, message: str,
                  guids: list[str]) -> dict:
    response = conn.tapir("CreateIssue", {"name": f"[{rule_id}] {message}"})
    issue_id = response.get("issueId")
    attached = 0
    if issue_id and guids:
        conn.tapir("AttachElementsToIssue", {
            "issueId": issue_id,
            "elements": _element_payload(guids),
            "type": "Highlight",
        })
        attached = len(guids)
    return {"issue_created": bool(issue_id), "attached": attached}
```

- [ ] **Step 4: Rewrite server.py with tier-1 registration**

Replace the whole file:

```python
from __future__ import annotations

import argparse
import os
from collections import Counter
from pathlib import Path

from fastmcp import FastMCP

from archicad_mcp import actions
from archicad_mcp.connection import (
    ArchicadUnavailableError,
    discover_instances,
    get_connection,
)
from archicad_mcp.extract import build_snapshot
from archicad_mcp.rules.engine import (
    data_needs,
    filter_by_tag,
    property_needs,
    run_rules,
)
from archicad_mcp.rules.loader import load_rules
from archicad_mcp.rules.types import Verdict


def _tool_error(exc: Exception) -> dict:
    return {"error": str(exc)}


def build_server(
    mode: str = "full",
    rules_dir: Path | None = None,
    port: int | None = None,
) -> FastMCP:
    if mode not in ("verdicts", "full"):
        raise ValueError(f"mode must be 'verdicts' or 'full', got {mode!r}")
    mcp = FastMCP("archicad-mcp")
    loaded = load_rules(rules_dir)
    default_port = port

    def _rules_subset(ruleset: str | None = None, rule_id: str | None = None):
        rules = loaded.rules
        if rule_id is not None:
            rules = [r for r in rules if r.rule_id == rule_id]
            if not rules:
                known = ", ".join(sorted(r.rule_id for r in loaded.rules)) or "none loaded"
                raise ArchicadUnavailableError(
                    f"Unknown rule '{rule_id}'. Loaded rules: {known}.")
            return rules
        return filter_by_tag(rules, ruleset)

    def _verdict_for(rules, request_port: int | None) -> Verdict:
        conn = get_connection(request_port if request_port is not None else default_port)
        snapshot = build_snapshot(conn, data_needs(rules), property_needs(rules))
        return run_rules(rules, snapshot)

    # ---------- Tier 1: verdict tools (both modes) ----------

    @mcp.tool(description="List running Archicad instances: port, version, open project, "
                          "Tapir add-on availability. Call this first.")
    def list_instances() -> dict:
        return {"instances": [i.to_dict() for i in discover_instances()]}

    @mcp.tool(description="Aggregate element counts by type, story, and layer. "
                          "Returns counts only, never element data.")
    def get_model_summary(port: int | None = None) -> dict:
        try:
            conn = get_connection(port if port is not None else default_port)
            snapshot = build_snapshot(
                conn, needs=frozenset({"elements", "properties"}))
        except ArchicadUnavailableError as exc:
            return _tool_error(exc)
        by_type = Counter(e.element_type for e in snapshot.elements)
        by_layer = Counter(e.layer for e in snapshot.elements if e.layer)
        by_story = Counter(str(e.story) for e in snapshot.elements
                           if e.story is not None)
        return {"element_count": len(snapshot.elements),
                "by_type": dict(by_type), "by_layer": dict(by_layer),
                "by_story": dict(by_story)}

    @mcp.tool(description="List loaded QA rules (id, type, severity, tags) and any "
                          "rule-file load errors.")
    def list_rules() -> dict:
        return {
            "source": loaded.source,
            "rules": [{"id": r.rule_id, "type": type(r).__name__,
                       "severity": r.severity, "tags": sorted(r.tags)}
                      for r in loaded.rules],
            "errors": loaded.errors,
        }

    @mcp.tool(description="Run one QA rule by id. Returns a verdict: pass/fail, "
                          "failure count, failing element GUIDs.")
    def run_rule(rule_id: str, port: int | None = None) -> dict:
        try:
            rules = _rules_subset(rule_id=rule_id)
            return _verdict_for(rules, port).to_dict()
        except ArchicadUnavailableError as exc:
            return _tool_error(exc)

    @mcp.tool(description="Run all loaded QA rules (optionally only those tagged with "
                          "'ruleset') against the open model. Returns a scored verdict.")
    def audit_delivery_readiness(ruleset: str | None = None, port: int | None = None) -> dict:
        try:
            return _verdict_for(_rules_subset(ruleset=ruleset), port).to_dict()
        except ArchicadUnavailableError as exc:
            return _tool_error(exc)

    @mcp.tool(description="Run only the IFC-related QA rules to check IFC export "
                          "readiness. Requires the Tapir add-on for IFC data.")
    def verify_ifc_export_readiness(port: int | None = None) -> dict:
        ifc_rules = [r for r in loaded.rules if "ifc" in r.needs]
        if not ifc_rules:
            return {"error": "No IFC rules configured. Add 'ifc-property-required' "
                             "rules to your rules directory."}
        try:
            return _verdict_for(ifc_rules, port).to_dict()
        except ArchicadUnavailableError as exc:
            return _tool_error(exc)

    @mcp.tool(description="Highlight the elements failing a rule in the Archicad window "
                          "(requires Tapir add-on).")
    def highlight_failures(rule_id: str, port: int | None = None) -> dict:
        try:
            rules = _rules_subset(rule_id=rule_id)
            verdict = _verdict_for(rules, port)
            guids = [g for r in verdict.results for g in r.failing_guids]
            conn = get_connection(port if port is not None else default_port)
            return actions.highlight_elements(conn, guids)
        except ArchicadUnavailableError as exc:
            return _tool_error(exc)

    @mcp.tool(description="Create an Archicad issue from a rule's failures and attach "
                          "the failing elements (requires Tapir add-on).")
    def create_issues_from_failures(rule_id: str, port: int | None = None) -> dict:
        try:
            rules = _rules_subset(rule_id=rule_id)
            verdict = _verdict_for(rules, port)
            result = verdict.results[0]
            conn = get_connection(port if port is not None else default_port)
            return actions.create_issues(conn, rule_id, result.message,
                                         list(result.failing_guids))
        except ArchicadUnavailableError as exc:
            return _tool_error(exc)

    if mode == "full":
        _register_full_mode_tools(mcp, default_port)

    return mcp


def _register_full_mode_tools(mcp: FastMCP, default_port: int | None) -> None:
    """Tier 2 + 3 tools. Extended in later tasks."""


def main() -> None:
    parser = argparse.ArgumentParser(prog="archicad-mcp")
    parser.add_argument("--mode", choices=["verdicts", "full"],
                        default=os.environ.get("ARCHICAD_MCP_MODE", "full"))
    parser.add_argument("--rules-dir", type=Path,
                        default=os.environ.get("ARCHICAD_MCP_RULES_DIR"))
    parser.add_argument("--port", type=int, default=None,
                        help="Archicad API port (19723-19743); auto-detected if omitted")
    args, _ = parser.parse_known_args()
    rules_dir = Path(args.rules_dir) if args.rules_dir else None
    build_server(mode=args.mode, rules_dir=rules_dir, port=args.port).run()


if __name__ == "__main__":
    main()
```

Update `tests/test_server_smoke.py`: replace `"ping"` assertions — `test_server_builds_and_lists_tools` asserts `"list_rules" in names`; delete `test_ping_tool_answers`.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: all pass, including the updated smoke test.

- [ ] **Step 6: Commit**

```bash
git add src/archicad_mcp/server.py src/archicad_mcp/actions.py tests
git commit -m "feat: tier-1 verdict tools with mode filter and Tapir write-back"
```

---

### Task 10: Tier 2 — query_elements, get_element_data, set_element_data

**Files:**
- Create: `src/archicad_mcp/core/__init__.py`
- Create: `src/archicad_mcp/core/query.py`
- Create: `src/archicad_mcp/core/element_data.py`
- Modify: `src/archicad_mcp/server.py` (`_register_full_mode_tools`)
- Test: `tests/test_tier2_query_data.py`

**Interfaces:**
- Consumes: `ArchicadConnection`, `extract.fetch_property_values`, `extract.resolve_property_ids`, `extract.build_snapshot`, `extract._element_payload` — import the public names; add `element_payload(guids) -> list[dict]` as a public alias in `extract.py` and switch `actions.py` to import it (delete both private copies).
- Produces in `core/query.py`:

```python
def query_elements(conn, element_type: str | None = None, layer: str | None = None,
                   story: int | None = None, classification_system: str | None = None,
                   selection_only: bool = False) -> dict
# -> {"count": N, "guids": [...], "by_type": {...}} ; filters AND-combined;
# selection_only starts from API.GetSelectedElements instead of API.GetAllElements;
# classification_system filters to elements CLASSIFIED in that system
```

- Produces in `core/element_data.py`:

```python
def get_element_data(conn, guids: list[str], properties: list[str] | None = None,
                     include_classifications: bool = False) -> dict
# -> {"elements": [{"guid", "type", "layer", "properties": {...},
#                   "classifications": {...}?}, ...]}   (full mode: raw values allowed)

def set_element_data(conn, changes: list[dict], dry_run: bool = True) -> dict
# changes: [{"guid": ..., "property": "Group/Name", "value": ...}]
# dry_run -> {"dry_run": True, "planned_changes": [...]} with current -> new values
# commit  -> API.SetPropertyValuesOfElements; {"dry_run": False, "applied": N}
```

- `_register_full_mode_tools` registers `query_elements`, `get_element_data`, `set_element_data` (thin wrappers: resolve connection, catch `ArchicadUnavailableError`, delegate).

- [ ] **Step 1: Write the failing tests**

`tests/test_tier2_query_data.py`:

```python
import json

import pytest
from fastmcp import Client

import archicad_mcp.server as server_mod
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.server import build_server
from tests.conftest import FakeCore
from tests.fixtures import api_replays


@pytest.fixture
def core(monkeypatch):
    official = dict(api_replays.OFFICIAL)
    official["API.GetSelectedElements"] = {"elements": [{"elementId": {"guid": "w-1"}}]}
    official["API.SetPropertyValuesOfElements"] = {"executionResults": [{"success": True}]}
    core = FakeCore(official=official, tapir=dict(api_replays.TAPIR))
    monkeypatch.setattr(server_mod, "get_connection",
                        lambda port: ArchicadConnection(19723, core=core))
    return core


async def call(tool, args=None):
    mcp = build_server(mode="full")
    async with Client(mcp) as client:
        result = await client.call_tool(tool, args or {})
        return json.loads(result.content[0].text)


async def test_full_mode_registers_tier2(core):
    mcp = build_server(mode="full")
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
    assert {"query_elements", "get_element_data", "set_element_data"} <= names


async def test_verdicts_mode_hides_tier2(core):
    mcp = build_server(mode="verdicts")
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
    assert "query_elements" not in names


async def test_query_by_type(core):
    payload = await call("query_elements", {"element_type": "Wall"})
    assert payload["count"] == 2 and set(payload["guids"]) == {"w-1", "w-2"}


async def test_query_by_type_and_layer(core):
    payload = await call("query_elements", {"element_type": "Wall", "layer": "Sketch"})
    assert payload["guids"] == ["w-2"]


async def test_query_selection_only(core):
    payload = await call("query_elements", {"selection_only": True})
    assert payload["guids"] == ["w-1"]


async def test_get_element_data_returns_values(core):
    payload = await call("get_element_data",
                         {"guids": ["w-1"], "properties": ["OFFICE/Fire Rating"],
                          "include_classifications": True})
    el = payload["elements"][0]
    assert el["guid"] == "w-1" and el["type"] == "Wall"
    assert el["properties"]["OFFICE/Fire Rating"] == "EI60"
    assert el["classifications"] == {"ARCHICAD Classification": "c-wall"}


async def test_set_element_data_dry_run_by_default(core):
    payload = await call("set_element_data", {"changes": [
        {"guid": "w-2", "property": "OFFICE/Fire Rating", "value": "EI30"}]})
    assert payload["dry_run"] is True
    assert payload["planned_changes"] == [
        {"guid": "w-2", "property": "OFFICE/Fire Rating",
         "current": None, "new": "EI30"}]
    assert not any(c == "API.SetPropertyValuesOfElements" for c, _ in core.calls)


async def test_set_element_data_commit(core):
    payload = await call("set_element_data", {"changes": [
        {"guid": "w-2", "property": "OFFICE/Fire Rating", "value": "EI30"}],
        "dry_run": False})
    assert payload == {"dry_run": False, "applied": 1}
    call_names = [c for c, _ in core.calls]
    assert "API.SetPropertyValuesOfElements" in call_names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tier2_query_data.py -v`
Expected: FAIL.

- [ ] **Step 3: Add `element_payload` to extract.py and implement core/query.py**

In `extract.py`, rename `_element_payload` to `element_payload` (keep a module-level alias `_element_payload = element_payload` until callers migrate in this step — then update `extract.py`'s internal callers and `actions.py`'s import and delete both the alias and `actions.py`'s private copy).

`src/archicad_mcp/core/query.py`:

```python
from __future__ import annotations

from collections import Counter

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.extract import (
    BUILTIN_LAYER,
    BUILTIN_STORY,
    fetch_property_values,
)


def _selected_guids(conn: ArchicadConnection) -> list[str]:
    response = conn.official("API.GetSelectedElements")
    return [e["elementId"]["guid"] for e in response.get("elements", [])]


def _all_guids(conn: ArchicadConnection) -> list[str]:
    response = conn.official("API.GetAllElements")
    return [e["elementId"]["guid"] for e in response.get("elements", [])]


def _types_for(conn: ArchicadConnection, guids: list[str]) -> dict[str, str]:
    from archicad_mcp.extract import element_payload
    response = conn.official("API.GetTypesOfElements", {"elements": element_payload(guids)})
    return {t["typeOfElement"]["elementId"]["guid"]: t["typeOfElement"]["elementType"]
            for t in response.get("types", [])}


def query_elements(conn: ArchicadConnection, element_type: str | None = None,
                   layer: str | None = None, story: int | None = None,
                   classification_system: str | None = None,
                   selection_only: bool = False) -> dict:
    guids = _selected_guids(conn) if selection_only else _all_guids(conn)
    types = _types_for(conn, guids) if guids else {}

    if element_type is not None:
        guids = [g for g in guids if types.get(g) == element_type]

    if layer is not None or story is not None:
        values = fetch_property_values(conn, guids, [BUILTIN_LAYER, BUILTIN_STORY])
        if layer is not None:
            guids = [g for g in guids if values.get(g, {}).get(BUILTIN_LAYER) == layer]
        if story is not None:
            guids = [g for g in guids if values.get(g, {}).get(BUILTIN_STORY) == story]

    if classification_system is not None:
        from archicad_mcp.extract import _fetch_classifications
        classif = _fetch_classifications(conn, guids) if guids else {}
        guids = [g for g in guids if classif.get(g, {}).get(classification_system)]

    by_type = Counter(types.get(g, "") for g in guids)
    return {"count": len(guids), "guids": guids, "by_type": dict(by_type)}
```

- [ ] **Step 4: Implement core/element_data.py**

```python
from __future__ import annotations

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.extract import (
    BUILTIN_LAYER,
    _fetch_classifications,
    element_payload,
    fetch_property_values,
    resolve_property_ids,
)


def get_element_data(conn: ArchicadConnection, guids: list[str],
                     properties: list[str] | None = None,
                     include_classifications: bool = False) -> dict:
    properties = properties or []
    response = conn.official("API.GetTypesOfElements", {"elements": element_payload(guids)})
    types = {t["typeOfElement"]["elementId"]["guid"]: t["typeOfElement"]["elementType"]
             for t in response.get("types", [])}
    values = fetch_property_values(conn, guids, [BUILTIN_LAYER, *properties])
    classif = _fetch_classifications(conn, guids) if include_classifications else {}
    elements = []
    for g in guids:
        item = {"guid": g, "type": types.get(g, ""),
                "layer": values.get(g, {}).get(BUILTIN_LAYER),
                "properties": {p: values.get(g, {}).get(p) for p in properties}}
        if include_classifications:
            item["classifications"] = classif.get(g, {})
        elements.append(item)
    return {"elements": elements}


def set_element_data(conn: ArchicadConnection, changes: list[dict],
                     dry_run: bool = True) -> dict:
    prop_names = sorted({c["property"] for c in changes})
    guids = [c["guid"] for c in changes]
    current = fetch_property_values(conn, guids, prop_names)
    planned = [{"guid": c["guid"], "property": c["property"],
                "current": current.get(c["guid"], {}).get(c["property"]),
                "new": c["value"]}
               for c in changes]
    if dry_run:
        return {"dry_run": True, "planned_changes": planned}
    ids = resolve_property_ids(conn, prop_names)
    payload = [{"elementPropertyValue": {
                    "elementId": {"guid": c["guid"]},
                    "propertyId": ids[c["property"]],
                    "propertyValue": {"value": c["value"]}}}
               for c in changes if c["property"] in ids]
    conn.official("API.SetPropertyValuesOfElements",
                  {"elementPropertyValues": payload})
    return {"dry_run": False, "applied": len(payload)}
```

- [ ] **Step 5: Register the three tools in `_register_full_mode_tools`**

Replace the stub in `server.py`:

```python
def _register_full_mode_tools(mcp: FastMCP, default_port: int | None) -> None:
    from archicad_mcp.core import element_data as _element_data
    from archicad_mcp.core import query as _query

    def _conn(port: int | None):
        return get_connection(port if port is not None else default_port)

    @mcp.tool(description="Query elements with AND-combined filters: element_type, "
                          "layer, story, classification_system, selection_only. "
                          "Returns GUIDs and counts.")
    def query_elements(element_type: str | None = None, layer: str | None = None,
                       story: int | None = None, classification_system: str | None = None,
                       selection_only: bool = False, port: int | None = None) -> dict:
        try:
            return _query.query_elements(_conn(port), element_type, layer, story,
                                         classification_system, selection_only)
        except ArchicadUnavailableError as exc:
            return _tool_error(exc)

    @mcp.tool(description="Read type, layer, requested properties (address user "
                          "properties as 'Group/Name') and optionally classifications "
                          "for the given element GUIDs.")
    def get_element_data(guids: list[str], properties: list[str] | None = None,
                         include_classifications: bool = False,
                         port: int | None = None) -> dict:
        try:
            return _element_data.get_element_data(_conn(port), guids, properties,
                                                  include_classifications)
        except ArchicadUnavailableError as exc:
            return _tool_error(exc)

    @mcp.tool(description="Write element property values. DRY-RUN BY DEFAULT: returns "
                          "planned changes (current -> new) without touching the model. "
                          "Pass dry_run=false to commit.")
    def set_element_data(changes: list[dict], dry_run: bool = True,
                         port: int | None = None) -> dict:
        try:
            return _element_data.set_element_data(_conn(port), changes, dry_run)
        except ArchicadUnavailableError as exc:
            return _tool_error(exc)
```

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/archicad_mcp/core src/archicad_mcp/extract.py src/archicad_mcp/actions.py src/archicad_mcp/server.py tests/test_tier2_query_data.py
git commit -m "feat: tier-2 query and element data tools with dry-run writes"
```

---

### Task 11: Tier 2 — create_elements, move_elements, delete_elements, manage_selection

**Files:**
- Create: `src/archicad_mcp/core/create.py`
- Create: `src/archicad_mcp/core/mutate.py`
- Create: `src/archicad_mcp/core/selection.py`
- Modify: `src/archicad_mcp/server.py` (extend `_register_full_mode_tools`)
- Test: `tests/test_tier2_mutations.py`

**Interfaces:**
- Consumes: `ArchicadConnection.tapir`, `extract.element_payload`.
- Produces:

```python
# create.py
CREATE_COMMANDS: dict[str, tuple[str, str]] = {
    # element_type -> (tapir command, payload key)
    "column": ("CreateColumns", "columnsData"),
    "slab": ("CreateSlabs", "slabsData"),
    "zone": ("CreateZones", "zonesData"),
    "polyline": ("CreatePolylines", "polylinesData"),
    "object": ("CreateObjects", "objectsData"),
    "mesh": ("CreateMeshes", "meshesData"),
}
def create_elements(conn, element_type: str, items: list[dict], dry_run: bool = True) -> dict
# dry_run -> {"dry_run": True, "command": "CreateSlabs", "payload": {...}}
# commit  -> {"dry_run": False, "created": <len of response elements>, "elements": [...guids...]}
# unknown element_type -> {"error": "... valid types: [...] . For door/window/stair and other
#   Tapir creation commands use execute_api_command."}

# mutate.py
def move_elements(conn, guids: list[str], vector: dict, confirm: bool = False) -> dict
# vector = {"x": float, "y": float, "z": float} in meters; not confirm -> {"error": ...refuse...}
# confirm -> Tapir MoveElements {"elementsWithMoveVectors": [{"elementId":..., "moveVector": vector}]}
def delete_elements(conn, guids: list[str], confirm: bool = False) -> dict
# not confirm -> {"error": "Refusing to delete N element(s) without confirm=true."}
# confirm -> Tapir DeleteElements {"elements": [...]}; returns {"deleted": N}

# selection.py
def manage_selection(conn, action: str, guids: list[str] | None = None) -> dict
# action "get" -> official API.GetSelectedElements -> {"guids": [...]}
# action "set" -> Tapir ChangeSelectionOfElements addElementsToSelection (clears first via "clear")
# action "clear" -> Tapir ChangeSelectionOfElements removing current selection
# unknown action -> {"error": ...}
```

Note on `CREATE_COMMANDS` coverage: the map contains only creation commands present in Tapir's command set as bundled by Task 13's definitions sync; door/window/stair creation flows via the tier-3 gateway (their Tapir schemas need a host element and are better served by `describe_api_command`). The error message for unknown types must say exactly that.

- [ ] **Step 1: Write the failing tests**

`tests/test_tier2_mutations.py`:

```python
import json

import pytest
from fastmcp import Client

import archicad_mcp.server as server_mod
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.server import build_server
from tests.conftest import FakeCore
from tests.fixtures import api_replays


@pytest.fixture
def core(monkeypatch):
    official = dict(api_replays.OFFICIAL)
    official["API.GetSelectedElements"] = {"elements": [{"elementId": {"guid": "w-1"}}]}
    tapir = dict(api_replays.TAPIR)
    tapir["CreateSlabs"] = {"elements": [{"elementId": {"guid": "new-slab-1"}}]}
    tapir["MoveElements"] = {}
    tapir["DeleteElements"] = {}
    tapir["ChangeSelectionOfElements"] = {}
    core = FakeCore(official=official, tapir=tapir)
    monkeypatch.setattr(server_mod, "get_connection",
                        lambda port: ArchicadConnection(19723, core=core))
    return core


async def call(tool, args=None):
    mcp = build_server(mode="full")
    async with Client(mcp) as client:
        result = await client.call_tool(tool, args or {})
        return json.loads(result.content[0].text)


SLAB_ITEM = {"polygonCoordinates": [{"x": 0, "y": 0}, {"x": 5, "y": 0}, {"x": 5, "y": 5}],
             "level": 0.0}


async def test_create_elements_dry_run_default(core):
    payload = await call("create_elements", {"element_type": "slab", "items": [SLAB_ITEM]})
    assert payload["dry_run"] is True
    assert payload["command"] == "CreateSlabs"
    assert payload["payload"] == {"slabsData": [SLAB_ITEM]}
    assert not any(c == "CreateSlabs" for c, _ in core.calls)


async def test_create_elements_commit(core):
    payload = await call("create_elements",
                         {"element_type": "slab", "items": [SLAB_ITEM], "dry_run": False})
    assert payload == {"dry_run": False, "created": 1, "elements": ["new-slab-1"]}


async def test_create_elements_unknown_type_points_to_gateway(core):
    payload = await call("create_elements", {"element_type": "door", "items": [{}]})
    assert "execute_api_command" in payload["error"]


async def test_move_refuses_without_confirm(core):
    payload = await call("move_elements",
                         {"guids": ["w-1"], "vector": {"x": 1.0, "y": 0.0, "z": 0.0}})
    assert "confirm" in payload["error"]
    assert not any(c == "MoveElements" for c, _ in core.calls)


async def test_move_with_confirm(core):
    payload = await call("move_elements",
                         {"guids": ["w-1"], "vector": {"x": 1.0, "y": 0.0, "z": 0.0},
                          "confirm": True})
    assert payload == {"moved": 1}
    command, params = [c for c in core.calls if c[0] == "MoveElements"][0]
    assert params["elementsWithMoveVectors"][0]["moveVector"] == {"x": 1.0, "y": 0.0, "z": 0.0}


async def test_delete_refuses_without_confirm(core):
    payload = await call("delete_elements", {"guids": ["w-1", "w-2"]})
    assert "2 element(s)" in payload["error"]


async def test_delete_with_confirm(core):
    payload = await call("delete_elements", {"guids": ["w-1"], "confirm": True})
    assert payload == {"deleted": 1}


async def test_selection_get_uses_official_api(core):
    payload = await call("manage_selection", {"action": "get"})
    assert payload == {"guids": ["w-1"]}


async def test_selection_set(core):
    payload = await call("manage_selection", {"action": "set", "guids": ["w-2"]})
    assert payload == {"selected": 1}
    assert any(c == "ChangeSelectionOfElements" for c, _ in core.calls)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tier2_mutations.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement create.py, mutate.py, selection.py**

`src/archicad_mcp/core/create.py`:

```python
from __future__ import annotations

from archicad_mcp.connection import ArchicadConnection

CREATE_COMMANDS: dict[str, tuple[str, str]] = {
    "column": ("CreateColumns", "columnsData"),
    "slab": ("CreateSlabs", "slabsData"),
    "zone": ("CreateZones", "zonesData"),
    "polyline": ("CreatePolylines", "polylinesData"),
    "object": ("CreateObjects", "objectsData"),
    "mesh": ("CreateMeshes", "meshesData"),
}


def create_elements(conn: ArchicadConnection, element_type: str,
                    items: list[dict], dry_run: bool = True) -> dict:
    entry = CREATE_COMMANDS.get(element_type.lower())
    if entry is None:
        return {"error": f"Unknown element_type '{element_type}'. Valid types: "
                         f"{sorted(CREATE_COMMANDS)}. For door/window/stair and other "
                         "Tapir creation commands use execute_api_command "
                         "(describe_api_command shows the schema)."}
    command, payload_key = entry
    payload = {payload_key: items}
    if dry_run:
        return {"dry_run": True, "command": command, "payload": payload}
    response = conn.tapir(command, payload)
    created = [e["elementId"]["guid"] for e in response.get("elements", [])]
    return {"dry_run": False, "created": len(created), "elements": created}
```

`src/archicad_mcp/core/mutate.py`:

```python
from __future__ import annotations

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.extract import element_payload


def move_elements(conn: ArchicadConnection, guids: list[str], vector: dict,
                  confirm: bool = False) -> dict:
    if not confirm:
        return {"error": f"Refusing to move {len(guids)} element(s) without "
                         "confirm=true. Review the GUIDs and vector, then retry "
                         "with confirm=true."}
    conn.tapir("MoveElements", {"elementsWithMoveVectors": [
        {"elementId": {"guid": g}, "moveVector": vector} for g in guids]})
    return {"moved": len(guids)}


def delete_elements(conn: ArchicadConnection, guids: list[str],
                    confirm: bool = False) -> dict:
    if not confirm:
        return {"error": f"Refusing to delete {len(guids)} element(s) without "
                         "confirm=true. Deletion is irreversible; retry with "
                         "confirm=true only if certain."}
    conn.tapir("DeleteElements", {"elements": element_payload(guids)})
    return {"deleted": len(guids)}
```

`src/archicad_mcp/core/selection.py`:

```python
from __future__ import annotations

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.extract import element_payload


def manage_selection(conn: ArchicadConnection, action: str,
                     guids: list[str] | None = None) -> dict:
    if action == "get":
        response = conn.official("API.GetSelectedElements")
        return {"guids": [e["elementId"]["guid"] for e in response.get("elements", [])]}
    if action == "set":
        guids = guids or []
        conn.tapir("ChangeSelectionOfElements",
                   {"addElementsToSelection": element_payload(guids)})
        return {"selected": len(guids)}
    if action == "clear":
        current = conn.official("API.GetSelectedElements").get("elements", [])
        conn.tapir("ChangeSelectionOfElements",
                   {"removeElementsFromSelection": current})
        return {"cleared": len(current)}
    return {"error": f"Unknown action '{action}'. Use 'get', 'set', or 'clear'."}
```

- [ ] **Step 4: Register the tools in `_register_full_mode_tools`**

Append inside `_register_full_mode_tools` (after the Task-10 tools):

```python
    from archicad_mcp.core import create as _create
    from archicad_mcp.core import mutate as _mutate
    from archicad_mcp.core import selection as _selection

    @mcp.tool(description="Create elements (column/slab/zone/polyline/object/mesh) via "
                          "Tapir. DRY-RUN BY DEFAULT: shows the exact command and payload. "
                          "Pass dry_run=false to create. Other types: use execute_api_command.")
    def create_elements(element_type: str, items: list[dict], dry_run: bool = True,
                        port: int | None = None) -> dict:
        try:
            return _create.create_elements(_conn(port), element_type, items, dry_run)
        except ArchicadUnavailableError as exc:
            return _tool_error(exc)

    @mcp.tool(description="Move elements by a vector {x,y,z} in meters. Refuses without "
                          "confirm=true.")
    def move_elements(guids: list[str], vector: dict, confirm: bool = False,
                      port: int | None = None) -> dict:
        try:
            return _mutate.move_elements(_conn(port), guids, vector, confirm)
        except ArchicadUnavailableError as exc:
            return _tool_error(exc)

    @mcp.tool(description="Delete elements. IRREVERSIBLE. Refuses without confirm=true.")
    def delete_elements(guids: list[str], confirm: bool = False,
                        port: int | None = None) -> dict:
        try:
            return _mutate.delete_elements(_conn(port), guids, confirm)
        except ArchicadUnavailableError as exc:
            return _tool_error(exc)

    @mcp.tool(description="Get, set, or clear the current element selection in Archicad. "
                          "action: 'get' | 'set' | 'clear'.")
    def manage_selection(action: str, guids: list[str] | None = None,
                         port: int | None = None) -> dict:
        try:
            return _selection.manage_selection(_conn(port), action, guids)
        except ArchicadUnavailableError as exc:
            return _tool_error(exc)
```

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/archicad_mcp/core src/archicad_mcp/server.py tests/test_tier2_mutations.py
git commit -m "feat: tier-2 creation, mutation, and selection tools with confirm guards"
```

---

### Task 12: Tier 2 — get_project_info, list_attributes, manage_issues, publish

**Files:**
- Create: `src/archicad_mcp/core/project.py`
- Create: `src/archicad_mcp/core/attributes.py`
- Create: `src/archicad_mcp/core/issues.py`
- Create: `src/archicad_mcp/core/publish.py`
- Modify: `src/archicad_mcp/server.py` (extend `_register_full_mode_tools`)
- Test: `tests/test_tier2_project_issues.py`

**Interfaces:**

```python
# project.py
def get_project_info(conn) -> dict
# always includes {"archicad_version", "build"} from API.GetProductInfo.
# With Tapir also: project (GetProjectInfo), stories (GetStories), hotlinks (GetHotlinks),
# geolocation_present (GetGeoLocation). Without Tapir: {"note": "Install the Tapir add-on
# for project name, stories, hotlinks and geolocation."}

# attributes.py
ATTRIBUTE_DETAIL_COMMANDS = {
    "Layer": "API.GetLayerAttributes",
    "BuildingMaterial": "API.GetBuildingMaterialAttributes",
    "Composite": "API.GetCompositeAttributes",
    "Surface": "API.GetSurfaceAttributes",
    "Profile": "API.GetProfileAttributes",
    "ZoneCategory": "API.GetZoneCategoryAttributes",
}
def list_attributes(conn, attribute_type: str) -> dict
# {"attribute_type": ..., "names": [...]} ; unknown type -> {"error": ... lists valid types}

# issues.py
def manage_issues(conn, action: str, name: str | None = None, issue_id: str | None = None,
                  comment: str | None = None, guids: list[str] | None = None,
                  bcf_path: str | None = None) -> dict
# actions: "list" (GetIssues), "create" (CreateIssue name required),
# "comment" (AddCommentToIssue issue_id+comment), "attach" (AttachElementsToIssue issue_id+guids),
# "export_bcf" (ExportIssuesToBCF bcf_path), "import_bcf" (ImportIssuesFromBCF bcf_path)
# missing required args or unknown action -> {"error": ...}

# publish.py
def publish(conn, publisher_set_name: str) -> dict   # Tapir PublishPublisherSet -> {"published": name}
```

- [ ] **Step 1: Write the failing tests**

`tests/test_tier2_project_issues.py`:

```python
import json

import pytest
from fastmcp import Client

import archicad_mcp.server as server_mod
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.server import build_server
from tests.conftest import FakeCore
from tests.fixtures import api_replays


def make_core(tapir_on=True):
    official = dict(api_replays.OFFICIAL)
    if not tapir_on:
        official["API.IsAddOnCommandAvailable"] = {"available": False}
    tapir = dict(api_replays.TAPIR)
    tapir.update({
        "GetStories": {"stories": [{"index": 0, "name": "Ground"},
                                   {"index": 1, "name": "First"}]},
        "GetHotlinks": {"hotlinks": []},
        "GetGeoLocation": {"projectLocation": {"longitude": 14.5, "latitude": 46.05}},
        "GetIssues": {"issues": [{"issueId": {"guid": "i-1"}, "name": "Old issue"}]},
        "CreateIssue": {"issueId": {"guid": "i-2"}},
        "AddCommentToIssue": {},
        "AttachElementsToIssue": {},
        "ExportIssuesToBCF": {},
        "PublishPublisherSet": {},
    })
    return FakeCore(official=official, tapir=tapir if tapir_on else {})


@pytest.fixture
def core(monkeypatch):
    core = make_core()
    monkeypatch.setattr(server_mod, "get_connection",
                        lambda port: ArchicadConnection(19723, core=core))
    return core


async def call(tool, args=None):
    mcp = build_server(mode="full")
    async with Client(mcp) as client:
        result = await client.call_tool(tool, args or {})
        return json.loads(result.content[0].text)


async def test_project_info_with_tapir(core):
    payload = await call("get_project_info")
    assert payload["archicad_version"] == 29
    assert payload["project"]["projectName"] == "Test House"
    assert len(payload["stories"]) == 2
    assert payload["geolocation_present"] is True


async def test_project_info_without_tapir(monkeypatch):
    core = make_core(tapir_on=False)
    monkeypatch.setattr(server_mod, "get_connection",
                        lambda port: ArchicadConnection(19723, core=core))
    payload = await call("get_project_info")
    assert payload["archicad_version"] == 29
    assert "Tapir" in payload["note"]


async def test_list_attributes_layers(core):
    payload = await call("list_attributes", {"attribute_type": "Layer"})
    assert payload["names"] == ["A-WALL", "A-ZONE"]


async def test_list_attributes_unknown_type(core):
    payload = await call("list_attributes", {"attribute_type": "Pen"})
    assert "Layer" in payload["error"]


async def test_issues_list_and_create(core):
    listed = await call("manage_issues", {"action": "list"})
    assert listed["issues"][0]["name"] == "Old issue"
    created = await call("manage_issues", {"action": "create", "name": "Fix walls"})
    assert created["issue_id"] == "i-2"


async def test_issues_create_requires_name(core):
    payload = await call("manage_issues", {"action": "create"})
    assert "name" in payload["error"]


async def test_publish(core):
    payload = await call("publish", {"publisher_set_name": "IFC Export"})
    assert payload == {"published": "IFC Export"}
    command, params = [c for c in core.calls if c[0] == "PublishPublisherSet"][0]
    assert params == {"publisherSetName": "IFC Export"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tier2_project_issues.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the four modules**

`src/archicad_mcp/core/project.py`:

```python
from __future__ import annotations

from archicad_mcp.connection import ArchicadConnection


def get_project_info(conn: ArchicadConnection) -> dict:
    product = conn.official("API.GetProductInfo")
    out: dict = {"archicad_version": product.get("version"),
                 "build": product.get("buildNumber")}
    if not conn.tapir_available():
        out["note"] = ("Install the Tapir add-on for project name, stories, "
                       "hotlinks and geolocation.")
        return out
    out["project"] = conn.tapir("GetProjectInfo")
    out["stories"] = conn.tapir("GetStories").get("stories", [])
    out["hotlinks"] = conn.tapir("GetHotlinks").get("hotlinks", [])
    geo = conn.tapir("GetGeoLocation")
    out["geolocation_present"] = bool(geo.get("projectLocation"))
    return out
```

`src/archicad_mcp/core/attributes.py`:

```python
from __future__ import annotations

from archicad_mcp.connection import ArchicadConnection

ATTRIBUTE_DETAIL_COMMANDS = {
    "Layer": "API.GetLayerAttributes",
    "BuildingMaterial": "API.GetBuildingMaterialAttributes",
    "Composite": "API.GetCompositeAttributes",
    "Surface": "API.GetSurfaceAttributes",
    "Profile": "API.GetProfileAttributes",
    "ZoneCategory": "API.GetZoneCategoryAttributes",
}


def list_attributes(conn: ArchicadConnection, attribute_type: str) -> dict:
    command = ATTRIBUTE_DETAIL_COMMANDS.get(attribute_type)
    if command is None:
        return {"error": f"Unknown attribute_type '{attribute_type}'. "
                         f"Valid: {sorted(ATTRIBUTE_DETAIL_COMMANDS)}."}
    ids = conn.official("API.GetAttributesByType", {"attributeType": attribute_type})
    attribute_ids = ids.get("attributeIds", [])
    if not attribute_ids:
        return {"attribute_type": attribute_type, "names": []}
    response = conn.official(command, {"attributeIds": attribute_ids})
    names = []
    for item in response.get("attributes", []):
        # each item is {"<type>Attribute": {..., "name": ...}}
        inner = next(iter(item.values()), {})
        if isinstance(inner, dict) and "name" in inner:
            names.append(inner["name"])
    return {"attribute_type": attribute_type, "names": names}
```

`src/archicad_mcp/core/issues.py`:

```python
from __future__ import annotations

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.extract import element_payload


def manage_issues(conn: ArchicadConnection, action: str, name: str | None = None,
                  issue_id: str | None = None, comment: str | None = None,
                  guids: list[str] | None = None, bcf_path: str | None = None) -> dict:
    if action == "list":
        return {"issues": conn.tapir("GetIssues").get("issues", [])}
    if action == "create":
        if not name:
            return {"error": "manage_issues action 'create' requires 'name'."}
        response = conn.tapir("CreateIssue", {"name": name})
        return {"issue_id": response.get("issueId", {}).get("guid")}
    if action == "comment":
        if not issue_id or not comment:
            return {"error": "action 'comment' requires 'issue_id' and 'comment'."}
        conn.tapir("AddCommentToIssue", {"issueId": {"guid": issue_id}, "text": comment})
        return {"commented": issue_id}
    if action == "attach":
        if not issue_id or not guids:
            return {"error": "action 'attach' requires 'issue_id' and 'guids'."}
        conn.tapir("AttachElementsToIssue", {"issueId": {"guid": issue_id},
                                             "elements": element_payload(guids),
                                             "type": "Highlight"})
        return {"attached": len(guids)}
    if action == "export_bcf":
        if not bcf_path:
            return {"error": "action 'export_bcf' requires 'bcf_path'."}
        conn.tapir("ExportIssuesToBCF", {"exportPath": bcf_path, "useExternalId": False,
                                         "alignBySurveyPoint": True})
        return {"exported": bcf_path}
    if action == "import_bcf":
        if not bcf_path:
            return {"error": "action 'import_bcf' requires 'bcf_path'."}
        conn.tapir("ImportIssuesFromBCF", {"importPath": bcf_path,
                                           "alignBySurveyPoint": True})
        return {"imported": bcf_path}
    return {"error": f"Unknown action '{action}'. Valid: list, create, comment, "
                     "attach, export_bcf, import_bcf."}
```

`src/archicad_mcp/core/publish.py`:

```python
from __future__ import annotations

from archicad_mcp.connection import ArchicadConnection


def publish(conn: ArchicadConnection, publisher_set_name: str) -> dict:
    conn.tapir("PublishPublisherSet", {"publisherSetName": publisher_set_name})
    return {"published": publisher_set_name}
```

- [ ] **Step 4: Register the four tools in `_register_full_mode_tools`**

Append (same pattern as before — each wrapper resolves `_conn(port)`, delegates, catches `ArchicadUnavailableError`):

```python
    from archicad_mcp.core import attributes as _attributes
    from archicad_mcp.core import issues as _issues
    from archicad_mcp.core import project as _project
    from archicad_mcp.core import publish as _publish

    @mcp.tool(description="Project info: Archicad version, project name, stories, "
                          "hotlinks, geolocation presence (Tapir enriches).")
    def get_project_info(port: int | None = None) -> dict:
        try:
            return _project.get_project_info(_conn(port))
        except ArchicadUnavailableError as exc:
            return _tool_error(exc)

    @mcp.tool(description="List attribute names by type: Layer, BuildingMaterial, "
                          "Composite, Surface, Profile, ZoneCategory.")
    def list_attributes(attribute_type: str, port: int | None = None) -> dict:
        try:
            return _attributes.list_attributes(_conn(port), attribute_type)
        except ArchicadUnavailableError as exc:
            return _tool_error(exc)

    @mcp.tool(description="Manage Archicad issues (Tapir): action = list | create | "
                          "comment | attach | export_bcf | import_bcf.")
    def manage_issues(action: str, name: str | None = None, issue_id: str | None = None,
                      comment: str | None = None, guids: list[str] | None = None,
                      bcf_path: str | None = None, port: int | None = None) -> dict:
        try:
            return _issues.manage_issues(_conn(port), action, name, issue_id,
                                         comment, guids, bcf_path)
        except ArchicadUnavailableError as exc:
            return _tool_error(exc)

    @mcp.tool(description="Fire an Archicad publisher set by name (Tapir).")
    def publish(publisher_set_name: str, port: int | None = None) -> dict:
        try:
            return _publish.publish(_conn(port), publisher_set_name)
        except ArchicadUnavailableError as exc:
            return _tool_error(exc)
```

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/archicad_mcp/core src/archicad_mcp/server.py tests/test_tier2_project_issues.py
git commit -m "feat: tier-2 project info, attributes, issues, publish tools"
```

---

### Task 13: Tier 3 — gateway (definitions sync, registry, list/describe/execute)

**Files:**
- Create: `scripts/sync_tapir_defs.py`
- Create: `src/archicad_mcp/gateway/__init__.py`
- Create: `src/archicad_mcp/gateway/definitions/` (populated by the sync script)
- Create: `src/archicad_mcp/gateway/registry.py`
- Create: `src/archicad_mcp/gateway/execute.py`
- Modify: `src/archicad_mcp/server.py` (extend `_register_full_mode_tools`)
- Test: `tests/test_gateway.py`

**Interfaces:**

```python
# registry.py
@dataclass(frozen=True)
class CommandInfo:
    name: str            # "API.GetAllElements" or "CreateDoors"
    kind: str            # "official" | "tapir"
    group: str           # Tapir group name, or "Official JSON API"
    description: str
    input_schema: dict | None   # resolved JSON schema (Tapir only)

def build_registry() -> dict[str, CommandInfo]
# Tapir side: parse gateway/definitions/command_definitions.js (+ resolve "$ref" via
#   common_schema_definitions.js — same algorithm as multiconn's own generator).
# Official side: names from typing.get_args(multiconn_archicad.core.literal_commands.AddonCommandType);
#   description = "Official Archicad JSON API command. Schema: <docs URL anchor>"; input_schema=None.

# execute.py
def list_api_commands(registry, group: str | None = None) -> dict
#   {"commands": [{"name","kind","group","summary"}...], "groups": [...]}
def describe_api_command(registry, name: str) -> dict
#   full CommandInfo incl. schema; unknown -> {"error": ... nearest matches ...}
def execute_api_command(registry, conn, name: str, params: dict | None = None) -> dict
#   Tapir command with schema: jsonschema.validate(params, schema) first -> readable
#   error {"error": "...", "schema": {...}} on ValidationError.
#   Routes: kind=="official" -> conn.official(name, params); else conn.tapir(name, params)
```

- [ ] **Step 1: Write the sync script**

`scripts/sync_tapir_defs.py`:

```python
"""Refresh bundled Tapir command definitions from the Tapir repository.

Run whenever the Tapir add-on is updated:  uv run python scripts/sync_tapir_defs.py
"""
from pathlib import Path

import httpx

BASE = ("https://raw.githubusercontent.com/ENZYME-APD/"
        "tapir-archicad-automation/main/docs/archicad-addon")
FILES = ["command_definitions.js", "common_schema_definitions.js"]
TARGET = Path(__file__).resolve().parent.parent / "src/archicad_mcp/gateway/definitions"


def main() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        response = httpx.get(f"{BASE}/{name}", follow_redirects=True, timeout=30)
        response.raise_for_status()
        (TARGET / name).write_text(response.text, encoding="utf-8")
        print(f"synced {name} ({len(response.text)} bytes)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the sync script to populate definitions**

Run: `uv run python scripts/sync_tapir_defs.py`
Expected: two `synced …` lines; `src/archicad_mcp/gateway/definitions/` now holds both files. If the URLs 404 (Tapir repo layout moved), find the two files in the repo tree at https://github.com/ENZYME-APD/tapir-archicad-automation and update `BASE` — the files are referenced by Tapir's own docs page.

- [ ] **Step 3: Write the failing tests**

`tests/test_gateway.py`:

```python
import json

import pytest
from fastmcp import Client

import archicad_mcp.server as server_mod
from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.gateway.registry import build_registry
from archicad_mcp.server import build_server
from tests.conftest import FakeCore
from tests.fixtures import api_replays


def test_registry_contains_both_kinds():
    registry = build_registry()
    assert "API.GetAllElements" in registry
    assert registry["API.GetAllElements"].kind == "official"
    tapir_names = [c.name for c in registry.values() if c.kind == "tapir"]
    assert "GetProjectInfo" in tapir_names
    assert len(tapir_names) >= 80  # current Tapir ships 100+ commands


def test_tapir_commands_have_resolved_schemas():
    registry = build_registry()
    with_schema = [c for c in registry.values()
                   if c.kind == "tapir" and c.input_schema is not None]
    assert with_schema, "at least some Tapir commands declare input schemas"
    sample = json.dumps([c.input_schema for c in with_schema])
    assert "$ref" not in sample, "all $ref pointers must be resolved"


@pytest.fixture
def core(monkeypatch):
    tapir = dict(api_replays.TAPIR)
    tapir["GetStories"] = {"stories": []}
    core = FakeCore(official=dict(api_replays.OFFICIAL), tapir=tapir)
    monkeypatch.setattr(server_mod, "get_connection",
                        lambda port: ArchicadConnection(19723, core=core))
    return core


async def call(tool, args=None):
    mcp = build_server(mode="full")
    async with Client(mcp) as client:
        result = await client.call_tool(tool, args or {})
        return json.loads(result.content[0].text)


async def test_list_api_commands_grouped(core):
    payload = await call("list_api_commands")
    assert "groups" in payload and len(payload["commands"]) > 100


async def test_list_api_commands_filter_by_group(core):
    payload = await call("list_api_commands", {"group": "Official JSON API"})
    assert all(c["group"] == "Official JSON API" for c in payload["commands"])


async def test_describe_known_command(core):
    payload = await call("describe_api_command", {"name": "GetProjectInfo"})
    assert payload["name"] == "GetProjectInfo" and payload["kind"] == "tapir"


async def test_describe_unknown_suggests(core):
    payload = await call("describe_api_command", {"name": "GetProjInfo"})
    assert "GetProjectInfo" in payload["error"]


async def test_execute_routes_official(core):
    payload = await call("execute_api_command", {"name": "API.GetAllElements"})
    assert len(payload["elements"]) == 3


async def test_execute_routes_tapir(core):
    payload = await call("execute_api_command", {"name": "GetStories"})
    assert payload == {"stories": []}


async def test_execute_validates_tapir_params(core):
    registry = build_registry()
    # pick a Tapir command with a schema declaring required fields
    candidates = [c for c in registry.values()
                  if c.kind == "tapir" and c.input_schema
                  and c.input_schema.get("required")]
    assert candidates
    name = candidates[0].name
    payload = await call("execute_api_command", {"name": name, "params": {}})
    assert "error" in payload and "schema" in payload
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/test_gateway.py -v`
Expected: FAIL — no gateway module.

- [ ] **Step 5: Implement registry.py**

```python
from __future__ import annotations

import json
import typing
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

from multiconn_archicad.core.literal_commands import AddonCommandType

DEFINITIONS_DIR = Path(__file__).parent / "definitions"
OFFICIAL_DOCS = "https://archicadapi.graphisoft.com/JSONInterfaceDocumentation/"


@dataclass(frozen=True)
class CommandInfo:
    name: str
    kind: str
    group: str
    description: str
    input_schema: dict | None

    def to_dict(self) -> dict:
        return asdict(self)


def _load_js_json(path: Path, var_name: str):
    text = path.read_text(encoding="utf-8")
    text = text.replace(f"var {var_name} = ", "").rstrip("; \n")
    return json.loads(text)


def _resolve_refs(schema, definitions, seen=None):
    if seen is None:
        seen = set()
    if isinstance(schema, dict):
        if "$ref" in schema:
            ref = schema["$ref"]
            if ref.startswith("#/"):
                key = ref[2:]
                if key in seen:
                    return definitions[key]
                seen.add(key)
                return _resolve_refs(definitions[key], definitions, seen)
        return {k: _resolve_refs(v, definitions, seen) for k, v in schema.items()}
    if isinstance(schema, list):
        return [_resolve_refs(item, definitions, seen) for item in schema]
    return schema


@lru_cache(maxsize=1)
def build_registry() -> dict[str, CommandInfo]:
    registry: dict[str, CommandInfo] = {}

    groups = _load_js_json(DEFINITIONS_DIR / "command_definitions.js", "gCommands")
    definitions = _load_js_json(
        DEFINITIONS_DIR / "common_schema_definitions.js", "gSchemaDefinitions")
    for group in groups:
        for cmd in group.get("commands", []):
            schema = cmd.get("inputScheme")
            resolved = _resolve_refs(schema, definitions) if schema is not None else None
            registry[cmd["name"]] = CommandInfo(
                name=cmd["name"], kind="tapir", group=group["name"],
                description=cmd.get("description", ""), input_schema=resolved)

    for name in typing.get_args(AddonCommandType):
        if name in registry:
            continue
        registry[name] = CommandInfo(
            name=name, kind="official", group="Official JSON API",
            description=f"Official Archicad JSON API command. Docs: {OFFICIAL_DOCS}",
            input_schema=None)

    return registry
```

- [ ] **Step 6: Implement execute.py**

```python
from __future__ import annotations

import difflib

import jsonschema

from archicad_mcp.connection import ArchicadConnection
from archicad_mcp.gateway.registry import CommandInfo


def list_api_commands(registry: dict[str, CommandInfo], group: str | None = None) -> dict:
    commands = [c for c in registry.values() if group is None or c.group == group]
    return {
        "groups": sorted({c.group for c in registry.values()}),
        "commands": [{"name": c.name, "kind": c.kind, "group": c.group,
                      "summary": c.description.split(".")[0][:120]}
                     for c in sorted(commands, key=lambda c: (c.group, c.name))],
    }


def describe_api_command(registry: dict[str, CommandInfo], name: str) -> dict:
    info = registry.get(name)
    if info is None:
        close = difflib.get_close_matches(name, registry.keys(), n=3)
        hint = f" Did you mean: {', '.join(close)}?" if close else ""
        return {"error": f"Unknown command '{name}'.{hint} "
                         "Use list_api_commands to browse."}
    return info.to_dict()


def execute_api_command(registry: dict[str, CommandInfo], conn: ArchicadConnection,
                        name: str, params: dict | None = None) -> dict:
    info = registry.get(name)
    if info is None:
        return describe_api_command(registry, name)  # carries the error + suggestions
    if info.kind == "tapir" and info.input_schema is not None:
        try:
            jsonschema.validate(params or {}, info.input_schema)
        except jsonschema.ValidationError as exc:
            return {"error": f"Parameters for '{name}' failed validation: {exc.message}. "
                             "If the live add-on disagrees, refresh definitions with "
                             "scripts/sync_tapir_defs.py.",
                    "schema": info.input_schema}
    if info.kind == "official":
        return conn.official(name, params)
    return conn.tapir(name, params)
```

Create `src/archicad_mcp/gateway/__init__.py` (empty).

- [ ] **Step 7: Register the three gateway tools**

Append inside `_register_full_mode_tools`:

```python
    from archicad_mcp.gateway import execute as _gateway
    from archicad_mcp.gateway.registry import build_registry

    registry = build_registry()

    @mcp.tool(description="Catalog of ALL available Archicad API commands (official "
                          "JSON API + Tapir), optionally filtered by group.")
    def list_api_commands(group: str | None = None) -> dict:
        return _gateway.list_api_commands(registry, group)

    @mcp.tool(description="Full description and input schema for one API command. "
                          "Call before execute_api_command.")
    def describe_api_command(name: str) -> dict:
        return _gateway.describe_api_command(registry, name)

    @mcp.tool(description="Execute any Archicad API command by name (official 'API.*' "
                          "or Tapir). Params validated against the bundled schema "
                          "where available. Prefer the dedicated tools when one exists.")
    def execute_api_command(name: str, params: dict | None = None,
                            port: int | None = None) -> dict:
        try:
            return _gateway.execute_api_command(registry, _conn(port), name, params)
        except ArchicadUnavailableError as exc:
            return _tool_error(exc)
```

Ensure the definitions ship in the wheel — hatchling includes package files under `src/archicad_mcp/` by default, including `.js`; verify with `uv build && unzip -l dist/*.whl | grep definitions` (expect both `.js` files listed).

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest -v`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add scripts src/archicad_mcp/gateway src/archicad_mcp/server.py tests/test_gateway.py
git commit -m "feat: tier-3 API gateway with synced Tapir definitions and schema validation"
```

---

### Task 14: Live smoke test + response-shape verification against real Archicad 29

**Files:**
- Create: `tests/test_live.py`
- Possibly modify: `tests/fixtures/api_replays.py`, `src/archicad_mcp/extract.py` (only if live shapes differ)

**Interfaces:** none new — this task validates the replay fixtures against reality. **Requires a human:** Archicad 29 running on this Mac with a NON-SENSITIVE test model open (spec privacy rule), ideally with Tapir installed. If no Archicad is available in the execution environment, implement the test file, run it to confirm it skips cleanly, and flag the live run as an open item in the final report — do not silently skip verification.

- [ ] **Step 1: Write the live test**

`tests/test_live.py`:

```python
"""Live tests against a running Archicad 29 with a NON-SENSITIVE test model open.

Run manually:  uv run pytest -m live -v
Never run against a client project (privacy rule).
"""
import pytest

from archicad_mcp.connection import discover_instances, get_connection
from archicad_mcp.extract import (
    BUILTIN_LAYER,
    BUILTIN_STORY,
    BUILTIN_ZONE_NAME,
    BUILTIN_ZONE_NUMBER,
    build_snapshot,
)

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def conn():
    instances = discover_instances()
    if not instances:
        pytest.skip("no running Archicad instance")
    return get_connection(instances[0].port)


def test_product_info_is_archicad_29(conn):
    info = conn.official("API.GetProductInfo")
    assert info["version"] >= 29


def test_builtin_property_names_resolve(conn):
    """THE canary: if these names don't resolve, fix the BUILTIN_* constants in
    extract.py using the dump printed below."""
    from archicad_mcp.extract import resolve_property_ids
    wanted = [BUILTIN_LAYER, BUILTIN_STORY, BUILTIN_ZONE_NUMBER, BUILTIN_ZONE_NAME]
    ids = resolve_property_ids(conn, wanted)
    missing = [n for n in wanted if n not in ids]
    if missing:
        names = conn.official("API.GetAllPropertyNames")
        builtin = sorted(p.get("nonLocalizedName", "") for p in names["properties"]
                         if p.get("type") == "BuiltIn")
        print("\n".join(builtin))
        pytest.fail(f"Built-in names not found: {missing}. "
                    "Pick the right ones from the dump above and update extract.py.")


def test_full_snapshot_builds(conn):
    snap = build_snapshot(
        conn,
        needs=frozenset({"elements", "properties", "classifications", "layers",
                         "zones", "ifc"}))
    assert snap.elements, "test model must contain elements"
    assert snap.layers, "test model must contain layers"
    types = {e.element_type for e in snap.elements}
    print(f"element types found: {sorted(types)}")


def test_tapir_status_reported(conn):
    print(f"tapir available: {conn.tapir_available()}")
```

- [ ] **Step 2: Confirm default runs still exclude live tests**

Run: `uv run pytest -v`
Expected: all pass; `test_live.py` collected as deselected (marker filter).

- [ ] **Step 3: Run live (human-in-the-loop)**

Ask the user to start Archicad 29 with a non-sensitive test model, then:
Run: `uv run pytest -m live -v`
Expected: 4 passed (or skip if no instance). If `test_builtin_property_names_resolve` fails, update the `BUILTIN_*` constants in `extract.py` from the printed dump, adjust `tests/fixtures/api_replays.py` `GET_ALL_PROPERTY_NAMES` to match, and re-run the entire suite. If any response shape differs (KeyError in extract), fix `api_replays.py` + `extract.py` together.

- [ ] **Step 4: Commit**

```bash
git add tests/test_live.py tests/fixtures/api_replays.py src/archicad_mcp/extract.py
git commit -m "test: live smoke suite verifying API shapes against Archicad 29"
```

---

### Task 15: CI, README, packaging check

**Files:**
- Create: `.github/workflows/test.yml`
- Modify: `README.md` (replace placeholder)

**Interfaces:** none new.

- [ ] **Step 1: Write the CI workflow**

`.github/workflows/test.yml`:

```yaml
name: test
on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync
      - run: uv run pytest -v
      - run: uv build
```

- [ ] **Step 2: Write the README**

Replace `README.md` with (adjust nothing marked exact — config snippets must be copy-pasteable):

````markdown
# Archicad MCP

MCP server for **Archicad 29** (macOS + Windows). Connects Claude Desktop,
Claude Code, or any MCP client to a *running* Archicad instance.

Two things in one server:

1. **Delivery-readiness QA** — a local rules engine (your standards as YAML)
   returning verdicts: pass/fail, scores, failing element GUIDs.
2. **Full API access** — curated tools for querying, editing, and creating
   elements, plus a gateway to every official JSON API and
   [Tapir](https://github.com/ENZYME-APD/tapir-archicad-automation) command.

## Privacy: pick your mode

| Mode | Tools exposed | Model data sent to the AI |
|---|---|---|
| `--mode verdicts` | 8 QA tools | Verdicts only: rule ids, counts, GUIDs. Never element names, property values, or project info. |
| `--mode full` (default) | everything | Raw model data flows to the AI by design. |

Claims like "no data leaves your computer" don't apply to any MCP server —
tool *results* go to the model. In `full` mode, treat the model contents as
shared with your AI provider; use `verdicts` mode for confidential projects.

## Requirements

- Archicad 29 running with a project open (the JSON API talks to the live app)
- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Optional but recommended: the [Tapir add-on](https://github.com/ENZYME-APD/tapir-archicad-automation/releases)
  — required for element creation, issues, IFC checks, highlighting, publishing

## Install

```bash
uv tool install git+https://github.com/alesdev88/Archicad-MCP.git
```

## Configure Claude Desktop

macOS — `~/Library/Application Support/Claude/claude_desktop_config.json`
Windows — `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "archicad": {
      "command": "archicad-mcp",
      "args": ["--mode", "full"],
      "env": { "ARCHICAD_MCP_RULES_DIR": "/path/to/your/rules" }
    }
  }
}
```

Claude Code:

```bash
claude mcp add archicad -- archicad-mcp --mode full
```

## Writing rules

Point `ARCHICAD_MCP_RULES_DIR` (or `--rules-dir`) at a directory of YAML files:

```yaml
- id: walls-fire-rating
  type: property-required
  property: "OFFICE/Fire Rating"   # user properties: "Group/Name"
  applies_to: { element_type: Wall }
  severity: error
  tags: [ifc-delivery]
```

Rule types: `property-required`, `classification-required`, `layer-compliance`,
`zone-number-required`, `ifc-property-required`. Custom logic goes in
`custom_rules.py` in the same directory (module-level `RULES = [...]`).
Without a rules dir, bundled example rules load. Keep office standards out of
public repos.

## Tools

**QA (both modes):** `list_instances`, `get_model_summary`, `list_rules`,
`run_rule`, `audit_delivery_readiness`, `verify_ifc_export_readiness`,
`highlight_failures`, `create_issues_from_failures`

**Core (full mode):** `query_elements`, `get_element_data`, `set_element_data`,
`create_elements`, `move_elements`, `delete_elements`, `manage_selection`,
`get_project_info`, `list_attributes`, `manage_issues`, `publish`
— every write is dry-run by default; delete/move require `confirm=true`.

**Gateway (full mode):** `list_api_commands`, `describe_api_command`,
`execute_api_command` — the complete official + Tapir command surface.
Refresh Tapir schemas after add-on updates: `uv run python scripts/sync_tapir_defs.py`.

## Development

```bash
uv sync && uv run pytest          # offline suite
uv run pytest -m live -v          # against a running Archicad (test models only!)
```
````

- [ ] **Step 3: Verify packaging and full suite one last time**

Run: `uv build && uv run pytest -v`
Expected: wheel + sdist built; all tests pass.

- [ ] **Step 4: Commit**

```bash
git add .github README.md
git commit -m "docs: README and cross-platform CI workflow"
```

---

## Final verification (after all tasks)

- [ ] `uv run pytest -v` — full offline suite green.
- [ ] `uv run archicad-mcp --help` — CLI works.
- [ ] Count check: `verdicts` mode exposes exactly 8 tools; `full` mode exposes 22 (8 + 11 + 3).
- [ ] Spec cross-check: every tool named in the spec's three-tier table exists with the spec'd name.
- [ ] Working tree clean, all commits on `main`. Do NOT push — the user decides when to publish.
