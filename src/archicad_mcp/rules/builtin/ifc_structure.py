from __future__ import annotations

from dataclasses import dataclass

from archicad_mcp.rules.builtin.base import common_fields
from archicad_mcp.rules.builtin.collisions import _types
from archicad_mcp.rules.types import IfcStructure, ModelSnapshot, RuleResult, Severity

_REQUIRED = ("IfcProject", "IfcSite", "IfcBuilding")


@dataclass(frozen=True)
class IfcSpatialStructureRule:
    """Fails when Archicad reports no IfcProject, IfcSite or IfcBuilding, and, for the types
    in element_types, on an element whose IFC story is not its home story. How often those
    two differ in a real project is not known yet, so that comparison runs only when asked
    for. applies_to is not used. Reads no properties."""
    rule_id: str
    severity: Severity
    tags: frozenset[str]
    story_element_types: tuple[str, ...] = ()

    @property
    def needs(self) -> frozenset[str]:
        return frozenset({"ifc_structure"})

    @property
    def needed_properties(self) -> frozenset[str]:
        return frozenset()

    @classmethod
    def from_config(cls, cfg: dict) -> "IfcSpatialStructureRule":
        rule_id, severity, tags, _ = common_fields(cfg)
        return cls(rule_id, severity, tags, _types(cfg, "element_types", rule_id) or ())

    def _skipped(self, reason: str) -> RuleResult:
        return RuleResult(rule_id=self.rule_id, passed=False, severity=self.severity,
                          message="IFC spatial structure not checked", skipped=True,
                          skip_reason=reason)

    def check(self, snapshot: ModelSnapshot) -> RuleResult:
        found = snapshot.ifc_structure
        if found is None:
            return self._skipped("IFC structure unavailable: the Tapir add-on is missing or "
                                 "has no GetIFCSpatialStructure (a Tapir newer than 1.8.0, "
                                 "Archicad 28)")
        if not isinstance(found, IfcStructure):
            return self._skipped(f"IFC structure unavailable: {found}")
        absent = [name for name in _REQUIRED if name not in found.reported]
        compared = [(guid, ifc, home) for guid, (kind, ifc, home) in found.stories.items()
                    if kind in self.story_element_types]
        unread = sum(1 for _, ifc, home in compared if ifc is None or home is None)
        wrong = tuple(guid for guid, ifc, home in compared
                      if ifc is not None and home is not None and ifc != home)
        parts = []
        if absent:
            parts.append(f"Archicad reports no {', '.join(absent)}")
        if self.story_element_types:
            parts.append(f"{len(wrong)} of {len(compared)} element(s) have an IFC story other "
                         "than their home story")
        if unread:
            parts.append(f"the story of {unread} could not be read")
        message = "; ".join(parts) or "IfcProject, IfcSite and IfcBuilding are reported"
        return RuleResult(rule_id=self.rule_id, passed=not absent and not wrong,
                          severity=self.severity, message=message,
                          failure_count=len(absent) + len(wrong), failing_guids=wrong)
