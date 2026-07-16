import pytest

from archicad_mcp.rules.builtin.classification_required import ClassificationRequiredRule
from archicad_mcp.rules.builtin.property_required import PropertyRequiredRule
from archicad_mcp.rules.types import ElementInfo, ModelSnapshot, RuleConfigError

WALL_OK = ElementInfo(guid="w-1", element_type="Wall", properties={"Fire Rating": "EI60"})
WALL_MISSING = ElementInfo(guid="w-2", element_type="Wall", properties={"Fire Rating": None})
WALL_EMPTY = ElementInfo(guid="w-3", element_type="Wall", properties={"Fire Rating": ""})
SLAB = ElementInfo(guid="s-1", element_type="Slab", properties={})


def make_prop_rule(**overrides):
    cfg = {"id": "walls-fire-rating", "type": "property-required",
           "property": "Fire Rating", "applies_to": {"element_type": "Wall"},
           "severity": "error", "tags": ["ifc-delivery"]}
    cfg.update(overrides)
    return PropertyRequiredRule.from_config(cfg)


def test_property_required_flags_missing_and_empty():
    rule = make_prop_rule()
    result = rule.check(ModelSnapshot(elements=(WALL_OK, WALL_MISSING, WALL_EMPTY, SLAB)))
    assert result.passed is False
    assert set(result.failing_guids) == {"w-2", "w-3"}
    assert result.failure_count == 2
    assert "Fire Rating" in result.message


def test_property_required_ignores_non_matching_types():
    rule = make_prop_rule()
    result = rule.check(ModelSnapshot(elements=(SLAB,)))
    assert result.passed is True and result.failure_count == 0


def test_property_required_declares_needs():
    rule = make_prop_rule()
    assert rule.needs == frozenset({"elements", "properties"})
    assert rule.needed_properties == frozenset({"Fire Rating"})
    assert rule.tags == frozenset({"ifc-delivery"})


def test_missing_id_raises_config_error():
    with pytest.raises(RuleConfigError):
        PropertyRequiredRule.from_config({"type": "property-required", "property": "X"})


def test_bad_severity_raises_config_error():
    with pytest.raises(RuleConfigError):
        make_prop_rule(severity="fatal")


def test_classification_required_flags_unclassified():
    rule = ClassificationRequiredRule.from_config(
        {"id": "all-classified", "type": "classification-required", "system": "Office System"})
    els = (ElementInfo(guid="e-1", element_type="Wall",
                       classifications={"Office System": "21-01"}),
           ElementInfo(guid="e-2", element_type="Wall",
                       classifications={"Office System": None}),
           ElementInfo(guid="e-3", element_type="Wall", classifications={}))
    result = rule.check(ModelSnapshot(elements=els))
    assert result.passed is False
    assert set(result.failing_guids) == {"e-2", "e-3"}
    assert rule.needs == frozenset({"elements", "classifications"})
