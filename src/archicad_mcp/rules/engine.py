from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from archicad_mcp.rules.types import ModelSnapshot, RuleResult, Severity, Verdict


@runtime_checkable
class Rule(Protocol):
    rule_id: str
    severity: Severity
    tags: frozenset[str]
    needs: frozenset[str]
    needed_properties: frozenset[str]

    def check(self, snapshot: ModelSnapshot) -> RuleResult: ...


def run_rules(rules: Sequence[Rule], snapshot: ModelSnapshot) -> Verdict:
    results = tuple(rule.check(snapshot) for rule in rules)
    scored = [r for r in results if not r.skipped]
    if scored:
        score = round(100 * sum(1 for r in scored if r.passed) / len(scored))
    else:
        score = 100
    passed = all(r.passed for r in scored if r.severity == "error")
    return Verdict(score=score, passed=passed, results=results)


def data_needs(rules: Sequence[Rule]) -> frozenset[str]:
    return frozenset().union(*(r.needs for r in rules)) if rules else frozenset()


def property_needs(rules: Sequence[Rule]) -> frozenset[str]:
    return frozenset().union(*(r.needed_properties for r in rules)) if rules else frozenset()


def filter_by_tag(rules: Sequence[Rule], tag: str | None) -> list[Rule]:
    if tag is None:
        return list(rules)
    return [r for r in rules if tag in r.tags]
