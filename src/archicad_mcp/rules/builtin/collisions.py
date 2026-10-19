from __future__ import annotations

from dataclasses import dataclass

from archicad_mcp.core.collisions import (
    DEFAULT_VOLUME_TOLERANCE,
    CollisionQuery,
    resolve_types,
)
from archicad_mcp.rules.builtin.base import common_fields
from archicad_mcp.rules.types import ModelSnapshot, RuleConfigError, RuleResult, Severity

TYPE_NAME = "no-collisions"


def _types(cfg: dict, key: str, rule_id: str) -> tuple[str, ...] | None:
    raw = cfg.get(key)
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise RuleConfigError(f"rule {rule_id!r}: '{key}' must be a list of element types")
    try:
        return resolve_types(raw)
    except ValueError as exc:
        raise RuleConfigError(f"rule {rule_id!r}: {key}: {exc}") from exc


def _tolerance(cfg: dict, key: str, rule_id: str, default):
    # A key left empty in YAML reads as None: take the default, as if absent.
    value = cfg.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise RuleConfigError(f"rule {rule_id!r}: '{key}' must be a number of 0 or more")
    return float(value)


@dataclass(frozen=True)
class NoCollisionsRule:
    """Fails when elements of element_types collide with elements of
    against_types, or with each other when against_types is omitted.

    applies_to is not used: the groups are the types, as find_collisions
    takes them.
    """
    rule_id: str
    severity: Severity
    tags: frozenset[str]
    query: CollisionQuery

    @property
    def needs(self) -> frozenset[str]:
        return frozenset({"collisions"})

    @property
    def needed_properties(self) -> frozenset[str]:
        return frozenset()

    @property
    def collision_query(self) -> CollisionQuery:
        return self.query

    @classmethod
    def from_config(cls, cfg: dict) -> "NoCollisionsRule":
        rule_id, severity, tags, _ = common_fields(cfg)
        element_types = _types(cfg, "element_types", rule_id)
        if element_types is None:
            raise RuleConfigError(f"rule {rule_id!r}: 'element_types' (a list of element "
                                  "types) is required")
        query = CollisionQuery(
            element_types=element_types,
            against_types=_types(cfg, "against_types", rule_id),
            volume_tolerance=_tolerance(cfg, "volume_tolerance", rule_id,
                                        DEFAULT_VOLUME_TOLERANCE),
            surface_tolerance=_tolerance(cfg, "surface_tolerance", rule_id, None))
        return cls(rule_id, severity, tags, query)

    def _scope(self) -> str:
        first = ", ".join(self.query.element_types)
        if self.query.against_types is None:
            return f"among {first} elements"
        return f"between {first} and {', '.join(self.query.against_types)} elements"

    def check(self, snapshot: ModelSnapshot) -> RuleResult:
        if snapshot.collisions is None:
            return RuleResult(
                rule_id=self.rule_id, passed=False, severity=self.severity,
                message="Collision data not available", skipped=True,
                skip_reason="Collision data unavailable: the Tapir add-on is missing "
                            "or has no GetCollisions (Tapir 1.2.2)")
        pairs = snapshot.collisions.get(self.query)
        if not isinstance(pairs, tuple):
            # build_snapshot stores why a query could not be answered as text.
            reason = pairs if isinstance(pairs, str) else "the collision check did not run"
            return RuleResult(
                rule_id=self.rule_id, passed=False, severity=self.severity,
                message="Collision check failed", skipped=True,
                skip_reason=f"Collision data unavailable: {reason}")
        failing = tuple(dict.fromkeys(g for c in pairs for g in (c.element, c.against)))
        return RuleResult(
            rule_id=self.rule_id,
            passed=not pairs,
            severity=self.severity,
            message=(f"{len(pairs)} collision(s) {self._scope()}, "
                     f"{len(failing)} element(s) involved"),
            failure_count=len(pairs),
            failing_guids=failing,
        )
