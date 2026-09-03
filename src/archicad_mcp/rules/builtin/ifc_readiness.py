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
        return frozenset({"elements", "ifc"}) | self.applies_to.needs

    @property
    def needed_properties(self) -> frozenset[str]:
        return frozenset() | self.applies_to.needed_properties

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
                skipped=True,
                skip_reason="IFC data unavailable: the Tapir add-on is missing "
                            "or too old for GetIFCPropertiesOfElements "
                            "(needs a release that ships the IFC commands)",
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
