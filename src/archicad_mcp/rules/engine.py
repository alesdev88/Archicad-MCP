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


# Element-level data needs that trigger a per-element fetch over the snapshot's
# element set, including the crash-prone GetPropertyValuesOfElements sweep.
# "layers" is here because an element's layer is read as a per-element property,
# so a layer rule reads snapshot.elements and MUST widen the scope like any other
# element rule (a layer rule targeting all elements forces a full fetch). A rule
# that reads snapshot.elements while declaring none of these needs would be
# invisible to scope narrowing; builtins all declare one.
_ELEMENT_FETCH_NEEDS = frozenset({"properties", "classifications", "ifc", "layers"})


def element_type_scope(rules: Sequence[Rule]) -> frozenset[str] | None:
    """The element types a per-element property fetch can be restricted to.

    Returns a set of element-type names when every rule that drives an
    element-property fetch targets a specific type (via applies_to), so the
    extractor need only pull those elements instead of the whole model.
    Returns None when the fetch cannot be narrowed. Any such rule targets all
    elements (applies_to is absent, None, or "*"), so the caller must fetch
    everything (and the fetch ceiling may then refuse an oversized model).
    """
    types: set[str] = set()
    for rule in rules:
        if not (rule.needs & _ELEMENT_FETCH_NEEDS):
            continue
        applies_to = getattr(rule, "applies_to", None)
        element_type = getattr(applies_to, "element_type", None)
        if element_type in (None, "*"):
            return None
        types.add(element_type)
    return frozenset(types) if types else None
