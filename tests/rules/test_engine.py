from dataclasses import dataclass, field

from archicad_mcp.rules.engine import data_needs, filter_by_tag, property_needs, run_rules
from archicad_mcp.rules.types import ModelSnapshot, RuleResult


@dataclass
class StubRule:
    rule_id: str
    severity: str = "error"
    tags: frozenset = frozenset()
    needs: frozenset = frozenset({"elements"})
    needed_properties: frozenset = frozenset()
    result: RuleResult | None = None

    def check(self, snapshot):
        return self.result


def passing(rid, sev="error"):
    return StubRule(rid, sev, result=RuleResult(rid, True, sev, "ok"))


def failing(rid, sev="error"):
    return StubRule(rid, sev, result=RuleResult(
        rid, False, sev, "bad", failure_count=2, failing_guids=("a", "b")))


def skipped(rid):
    return StubRule(rid, result=RuleResult(
        rid, False, "error", "skipped", skipped=True, skip_reason="Tapir add-on required"))


def test_all_passing_scores_100():
    v = run_rules([passing("r1"), passing("r2")], ModelSnapshot())
    assert v.score == 100 and v.passed is True


def test_failing_error_rule_fails_verdict():
    v = run_rules([passing("r1"), failing("r2")], ModelSnapshot())
    assert v.score == 50 and v.passed is False


def test_failing_warning_lowers_score_but_passes():
    v = run_rules([passing("r1"), failing("r2", sev="warning")], ModelSnapshot())
    assert v.score == 50 and v.passed is True


def test_skipped_rules_excluded_from_score():
    v = run_rules([passing("r1"), skipped("r2")], ModelSnapshot())
    assert v.score == 100 and v.passed is True
    assert v.results[1].skipped is True


def test_no_rules_scores_100_and_passes():
    v = run_rules([], ModelSnapshot())
    assert v.score == 100 and v.passed is True


def test_data_and_property_needs_union():
    r1 = StubRule("r1", needs=frozenset({"elements", "properties"}),
                  needed_properties=frozenset({"Fire Rating"}))
    r2 = StubRule("r2", needs=frozenset({"zones"}))
    assert data_needs([r1, r2]) == frozenset({"elements", "properties", "zones"})
    assert property_needs([r1, r2]) == frozenset({"Fire Rating"})


def test_filter_by_tag():
    r1 = StubRule("r1", tags=frozenset({"ifc-delivery"}))
    r2 = StubRule("r2")
    assert filter_by_tag([r1, r2], "ifc-delivery") == [r1]
    assert filter_by_tag([r1, r2], None) == [r1, r2]
