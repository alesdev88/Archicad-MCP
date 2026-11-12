from __future__ import annotations

import re
from dataclasses import dataclass

from archicad_mcp.rules.builtin.base import AppliesTo, checked_result, common_fields, split_unread
from archicad_mcp.rules.types import LAYER_KEY, ModelSnapshot, RuleConfigError, RuleResult, Severity

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
        return frozenset({"elements", "layers"}) | self.applies_to.needs

    @property
    def needed_properties(self) -> frozenset[str]:
        return frozenset() | self.applies_to.needed_properties

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
        split = split_unread(self, snapshot, {LAYER_KEY} | self.applies_to.needed_properties)
        failing = tuple(
            e.guid for e in split.readable
            if self.applies_to.matches(e) and not self._layer_ok(e.layer)
        )
        return checked_result(
            self, split, failing, f"{len(failing)} element(s) on non-compliant layers")
