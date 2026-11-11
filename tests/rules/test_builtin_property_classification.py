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


def test_property_required_skips_when_its_property_was_not_read():
    unread = ElementInfo(guid="w-9", element_type="Wall", properties={"Fire Rating": None},
                         not_read={"Fire Rating": "because"})
    result = make_prop_rule().check(ModelSnapshot(elements=(WALL_OK, unread)))
    assert result.skipped is True and result.passed is False
    assert result.skip_reason == "'Fire Rating' could not be read on 1 element(s): because"


def test_property_required_runs_when_only_another_type_was_not_read():
    label = ElementInfo(guid="l-1", element_type="Label", not_read={"Fire Rating": "because"})
    result = make_prop_rule().check(ModelSnapshot(elements=(WALL_OK, label)))
    assert result.skipped is False and result.passed is True


def test_classification_required_skips_when_a_where_property_was_not_read():
    rule = ClassificationRequiredRule.from_config(
        {"id": "c", "type": "classification-required", "system": "Uniclass",
         "applies_to": {"element_type": "Wall",
                        "where": [{"property": "Fire Rating", "operator": "has_value"}]}})
    unread = ElementInfo(guid="w-9", element_type="Wall", not_read={"Fire Rating": "why"})
    result = rule.check(ModelSnapshot(elements=(unread,)))
    assert result.skipped is True
    assert result.skip_reason == "'Fire Rating' could not be read on 1 element(s): why"


def test_property_required_fails_on_a_readable_wall_despite_an_unread_label():
    label = ElementInfo(guid="l-1", element_type="Label", not_read={"Fire Rating": "because"})
    result = make_prop_rule(applies_to={}).check(ModelSnapshot(elements=(WALL_MISSING, label)))
    assert result.skipped is False and result.passed is False
    assert result.failing_guids == ("w-2",) and result.failure_count == 1
    assert result.message.endswith("; 1 element(s) could not be read and were not checked")


def test_property_required_skips_on_an_unread_label_when_nothing_readable_fails():
    label = ElementInfo(guid="l-1", element_type="Label", not_read={"Fire Rating": "because"})
    result = make_prop_rule(applies_to={}).check(ModelSnapshot(elements=(WALL_OK, label)))
    assert result.skipped is True and result.passed is False
    assert result.skip_reason == "'Fire Rating' could not be read on 1 element(s): because"


def test_property_required_skip_counts_the_first_name_and_takes_its_first_reason():
    rule = make_prop_rule(applies_to={
        "where": [{"property": "Acoustic Rating", "operator": "has_value"}]})
    first = ElementInfo(guid="l-1", element_type="Label",
                        not_read={"Fire Rating": "one", "Acoustic Rating": "a"})
    second = ElementInfo(guid="l-2", element_type="Label", not_read={"Acoustic Rating": "b"})
    result = rule.check(ModelSnapshot(elements=(first, second)))
    assert result.skipped is True
    assert result.skip_reason == "'Acoustic Rating' could not be read on 2 element(s): a"


def test_property_required_fails_without_a_note_when_nothing_was_unread():
    result = make_prop_rule().check(ModelSnapshot(elements=(WALL_MISSING,)))
    assert result.passed is False
    assert "could not be read" not in result.message


def test_classification_required_fails_on_a_readable_element_despite_an_unread_one():
    rule = ClassificationRequiredRule.from_config(
        {"id": "c", "type": "classification-required", "system": "Uniclass",
         "applies_to": {"where": [{"property": "Fire Rating", "operator": "has_value"}]}})
    wall = ElementInfo(guid="w-1", element_type="Wall", properties={"Fire Rating": "EI60"})
    label = ElementInfo(guid="l-1", element_type="Label", not_read={"Fire Rating": "why"})
    result = rule.check(ModelSnapshot(elements=(wall, label)))
    assert result.skipped is False and result.passed is False
    assert result.failing_guids == ("w-1",)
    assert result.message.endswith("; 1 element(s) could not be read and were not checked")
