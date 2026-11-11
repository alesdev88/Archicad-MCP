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
        return frozenset({"elements", "properties"}) | self.applies_to.needs

    @property
    def needed_properties(self) -> frozenset[str]:
        return frozenset({self.property_name}) | self.applies_to.needed_properties

    @classmethod
    def from_config(cls, cfg: dict) -> "PropertyRequiredRule":
        rule_id, severity, tags, applies_to = common_fields(cfg)
        prop = cfg.get("property")
        if not prop or not isinstance(prop, str):
            raise RuleConfigError(f"rule {rule_id!r}: 'property' (string) is required")
        return cls(rule_id, severity, tags, applies_to, prop)

    def check(self, snapshot: ModelSnapshot) -> RuleResult:
        split = split_unread(self, snapshot, self.needed_properties)
        failing = tuple(
            e.guid for e in split.readable
            if self.applies_to.matches(e) and is_missing(e.properties.get(self.property_name))
        )
        return checked_result(
            self, split, failing,
            f"{len(failing)} element(s) missing required property '{self.property_name}'")
