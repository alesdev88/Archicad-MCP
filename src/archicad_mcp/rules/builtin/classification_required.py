from __future__ import annotations

from dataclasses import dataclass

from archicad_mcp.rules.builtin.base import (
    AppliesTo,
    checked_result,
    common_fields,
    is_missing,
    split_unread,
)
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
        return frozenset({"elements", "classifications"}) | self.applies_to.needs

    @property
    def needed_properties(self) -> frozenset[str]:
        return frozenset() | self.applies_to.needed_properties

    @classmethod
    def from_config(cls, cfg: dict) -> "ClassificationRequiredRule":
        rule_id, severity, tags, applies_to = common_fields(cfg)
        system = cfg.get("system")
        if not system or not isinstance(system, str):
            raise RuleConfigError(f"rule {rule_id!r}: 'system' (classification system name) is required")
        return cls(rule_id, severity, tags, applies_to, system)

    def check(self, snapshot: ModelSnapshot) -> RuleResult:
        split = split_unread(self, snapshot, self.applies_to.needed_properties)
        failing = tuple(
            e.guid for e in split.readable
            if self.applies_to.matches(e) and is_missing(e.classifications.get(self.system))
        )
        return checked_result(
            self, split, failing,
            f"{len(failing)} element(s) missing classification in system '{self.system}'")
