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
