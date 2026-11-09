from __future__ import annotations

from dataclasses import dataclass

from archicad_mcp.rules.builtin.base import common_fields
from archicad_mcp.rules.types import MissingParts, ModelSnapshot, RuleResult, Severity


@dataclass(frozen=True)
class LibraryPartsLoadedRule:
    """Fails on objects, lamps, windows and doors whose library part is not loaded: the
    ones drawn as dots. applies_to is not used, as the types are fixed by what Archicad
    can name a missing part for. Reads no properties. The result names no part: a verdict
    holds counts and GUIDs, and GetMissingLibPartNames names them through the gateway."""
    rule_id: str
    severity: Severity
    tags: frozenset[str]

    @property
    def needs(self) -> frozenset[str]:
        return frozenset({"library_parts"})

    @property
    def needed_properties(self) -> frozenset[str]:
        return frozenset()

    @classmethod
    def from_config(cls, cfg: dict) -> "LibraryPartsLoadedRule":
        rule_id, severity, tags, _ = common_fields(cfg)
        return cls(rule_id, severity, tags)

    def _skipped(self, reason: str) -> RuleResult:
        return RuleResult(rule_id=self.rule_id, passed=False, severity=self.severity,
                          message="Library parts not checked", skipped=True,
                          skip_reason=reason)

    def check(self, snapshot: ModelSnapshot) -> RuleResult:
        found = snapshot.missing_parts
        if found is None:
            return self._skipped("Library part data unavailable: the Tapir add-on is missing "
                                 "or has no GetMissingLibPartNames (a Tapir newer than 1.8.0)")
        if not isinstance(found, MissingParts):
            return self._skipped(f"Library part data unavailable: {found}")
        message = (f"{len(found.guids)} of {found.checked} element(s) use a library part "
                   "that is not loaded")
        if found.unread:
            message += f"; the part of {found.unread} could not be read"
        return RuleResult(rule_id=self.rule_id, passed=not found.guids, severity=self.severity,
                          message=message, failure_count=len(found.guids),
                          failing_guids=found.guids)
