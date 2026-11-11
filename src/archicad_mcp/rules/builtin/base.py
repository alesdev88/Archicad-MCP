from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from archicad_mcp.criteria import (
    BRANCH_OPERATORS,
    Cell,
    Comparison,
    CriteriaError,
    comparison_matches,
    parse_comparison,
)
from archicad_mcp.rules.types import ElementInfo, ModelSnapshot, RuleConfigError, RuleResult, Severity

_SEVERITIES = ("error", "warning")


@dataclass(frozen=True)
class AppliesTo:
    """Which elements a rule looks at: an element type, and optionally a
    `where` list of criteria in the same shape find_elements takes, all of
    which must hold (AND). The criteria evaluator is shared with
    find_elements, so a scope that was worked out interactively as a query
    pastes straight into a rule."""
    element_type: str | None = None
    where: tuple[Comparison, ...] = ()

    def matches(self, element: ElementInfo) -> bool:
        if self.element_type not in (None, "*") and element.element_type != self.element_type:
            return False
        return all(comparison_matches(c, self._cell(element, c)) for c in self.where)

    @staticmethod
    def _cell(element: ElementInfo, cmp: Comparison) -> Cell:
        if cmp.kind == "story":
            return Cell.from_value(element.story)
        if cmp.kind == "classification":
            return Cell.from_value(element.classifications.get(cmp.system))
        return Cell.from_value(element.properties.get(cmp.property))

    @property
    def needs(self) -> frozenset[str]:
        out = set()
        for c in self.where:
            if c.kind == "story":
                out.add("story")
            elif c.kind == "classification":
                out.add("classifications")
            else:
                out.add("properties")
        return frozenset(out)

    @property
    def needed_properties(self) -> frozenset[str]:
        return frozenset(c.property for c in self.where if c.kind == "property")

    @classmethod
    def from_config(cls, cfg: dict | None, rule_id: str = "?") -> "AppliesTo":
        cfg = cfg or {}
        raw_where = cfg.get("where") or []
        if not isinstance(raw_where, list):
            raise RuleConfigError(f"rule {rule_id!r}: applies_to.where must be a list")
        where = []
        for i, raw in enumerate(raw_where):
            try:
                cmp = parse_comparison(raw, f"applies_to.where[{i}]")
            except CriteriaError as exc:
                raise RuleConfigError(f"rule {rule_id!r}: {exc}") from exc
            if cmp.operator in BRANCH_OPERATORS:
                # A snapshot carries item GUIDs, not the tree, so branch tests
                # cannot be answered offline. Refuse at load rather than fail
                # silently at run time.
                raise RuleConfigError(
                    f"rule {rule_id!r}: applies_to.where[{i}]: '{cmp.operator}' is "
                    "not supported in rules; use equal/not_equal against an item GUID, "
                    "or has_value/has_no_value")
            where.append(cmp)
        return cls(element_type=cfg.get("element_type"), where=tuple(where))


def common_fields(cfg: dict) -> tuple[str, Severity, frozenset[str], AppliesTo]:
    rule_id = cfg.get("id")
    if not rule_id or not isinstance(rule_id, str):
        raise RuleConfigError(f"rule is missing a string 'id': {cfg!r}")
    severity = cfg.get("severity", "error")
    if severity not in _SEVERITIES:
        raise RuleConfigError(f"rule {rule_id!r}: severity must be one of {_SEVERITIES}, got {severity!r}")
    tags = frozenset(cfg.get("tags", []) or [])
    applies_to = AppliesTo.from_config(cfg.get("applies_to"), rule_id)
    return rule_id, severity, tags, applies_to


def is_missing(value: object) -> bool:
    """The has_no_value sense of the criteria language, for plain values."""
    return not Cell.from_value(value).usable


@dataclass(frozen=True)
class ReadSplit:
    """The snapshot's elements for one rule: those it can evaluate, and those of a type
    its applies_to accepts on which one of its names could not be read."""
    readable: tuple[ElementInfo, ...]
    unread: tuple[ElementInfo, ...]
    names: frozenset[str]


def split_unread(rule, snapshot: ModelSnapshot, names: Iterable[str]) -> ReadSplit:
    """Split the snapshot's elements for a rule that reads `names` (property names, or
    LAYER_KEY for the layer). An element is unread when its type is accepted by
    applies_to.element_type and one of the names is in its not_read. Its `where`
    comparisons are not evaluated: they may be the very values that were not read."""
    wanted = rule.applies_to.element_type
    names = frozenset(names)
    readable, unread = [], []
    for e in snapshot.elements:
        accepted = wanted in (None, "*") or e.element_type == wanted
        (unread if accepted and names & e.not_read.keys() else readable).append(e)
    return ReadSplit(tuple(readable), tuple(unread), names)


def checked_result(rule, split: ReadSplit, failing: tuple[str, ...],
                   message: str) -> RuleResult:
    """The result of a rule that evaluated split.readable. A failure stands, and says how
    many elements were not checked. With no failure, an unread element makes the rule
    skip: the first name in sorted order that is unread anywhere decides the reason."""
    if failing:
        if split.unread:
            message += (f"; {len(split.unread)} element(s) could not be read "
                        "and were not checked")
        return RuleResult(rule_id=rule.rule_id, passed=False, severity=rule.severity,
                          message=message, failure_count=len(failing),
                          failing_guids=failing)
    if split.unread:
        for name in sorted(split.names):
            unread = [e for e in split.unread if name in e.not_read]
            if unread:
                return RuleResult(
                    rule_id=rule.rule_id, passed=False, severity=rule.severity,
                    message=f"Not checked: '{name}' could not be read on some elements",
                    skipped=True,
                    skip_reason=f"'{name}' could not be read on {len(unread)} "
                                f"element(s): {unread[0].not_read[name]}")
    return RuleResult(rule_id=rule.rule_id, passed=True, severity=rule.severity,
                      message=message)
