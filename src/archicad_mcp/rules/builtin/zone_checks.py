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
