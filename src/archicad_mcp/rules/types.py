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
