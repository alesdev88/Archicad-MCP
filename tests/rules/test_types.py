import dataclasses

import pytest

from archicad_mcp.rules.types import ElementInfo, ModelSnapshot, RuleResult, Verdict


def test_rule_result_is_verdicts_only():
    """Privacy guard: RuleResult must not grow fields that can carry raw model data."""
    allowed = {"rule_id", "passed", "severity", "message",
               "failure_count", "failing_guids", "skipped", "skip_reason"}
    actual = {f.name for f in dataclasses.fields(RuleResult)}
    assert actual == allowed


def test_verdict_to_dict_round_trips():
    r = RuleResult(rule_id="walls-fire-rating", passed=False, severity="error",
                   message="1 element missing 'Fire Rating'",
                   failure_count=1, failing_guids=("g-1",))
    v = Verdict(score=50, passed=False, results=(r,))
    d = v.to_dict()
    assert d["score"] == 50
    assert d["pass"] is False
    assert d["results"][0]["rule"] == "walls-fire-rating"
    assert d["results"][0]["guids"] == ["g-1"]


def test_snapshot_defaults_are_empty_and_frozen():
    snap = ModelSnapshot()
    assert snap.elements == () and snap.ifc_properties is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.elements = ()  # type: ignore[misc]


def test_element_info_defaults():
    e = ElementInfo(guid="g-1")
    assert e.layer is None and e.properties == {}
