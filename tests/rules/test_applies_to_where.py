"""applies_to.where: rules scope with the same criteria find_elements takes."""
import pytest

from archicad_mcp.rules.builtin.base import AppliesTo
from archicad_mcp.rules.builtin.property_required import PropertyRequiredRule
from archicad_mcp.rules.engine import data_needs, property_needs
from archicad_mcp.rules.types import ElementInfo, ModelSnapshot, RuleConfigError

EXT = ElementInfo("w-1", "Wall", story=0, properties={"OFFICE/Type": "External", "OFFICE/Fire Rating": None},
                  classifications={"AC": "c-wall"})
INT = ElementInfo("w-2", "Wall", story=1, properties={"OFFICE/Type": "Internal", "OFFICE/Fire Rating": None},
                  classifications={"AC": None})


def make_rule(where):
    return PropertyRequiredRule.from_config({
        "id": "ext-walls-fire-rating", "type": "property-required",
        "property": "OFFICE/Fire Rating",
        "applies_to": {"element_type": "Wall", "where": where}})


def test_where_narrows_the_scope_and_widens_the_fetch():
    rule = make_rule([{"property": "OFFICE/Type", "operator": "equal", "value": "external"}])
    result = rule.check(ModelSnapshot(elements=(EXT, INT)))
    assert result.failing_guids == ("w-1",)
    assert rule.needed_properties == frozenset({"OFFICE/Fire Rating", "OFFICE/Type"})
    assert property_needs([rule]) == rule.needed_properties


def test_where_on_story_and_classification_declare_their_needs():
    rule = make_rule([{"property": "story", "operator": "equal", "value": 1},
                      {"property": "classification:AC", "operator": "has_no_value"}])
    assert rule.needs == frozenset({"elements", "properties", "story", "classifications"})
    assert data_needs([rule]) == rule.needs
    assert rule.check(ModelSnapshot(elements=(EXT, INT))).failing_guids == ("w-2",)


def test_where_comparisons_are_anded():
    rule = make_rule([{"property": "story", "operator": "equal", "value": 0},
                      {"property": "OFFICE/Type", "operator": "equal", "value": "Internal"}])
    assert rule.check(ModelSnapshot(elements=(EXT, INT))).passed is True


def test_bad_where_is_a_config_error_naming_the_rule():
    with pytest.raises(RuleConfigError, match="ext-walls-fire-rating"):
        make_rule([{"property": "OFFICE/Type", "operator": "like", "value": "x"}])
    with pytest.raises(RuleConfigError, match="must be a list"):
        make_rule({"property": "x"})


def test_branch_operators_are_refused_in_rules():
    with pytest.raises(RuleConfigError, match="is_in_branch_of"):
        make_rule([{"property": "classification:AC", "operator": "is_in_branch_of", "value": "Building"}])


def test_applies_to_without_where_is_unchanged():
    scope = AppliesTo.from_config({"element_type": "Wall"})
    assert scope.where == () and scope.needs == frozenset() and scope.matches(EXT)
    assert AppliesTo.from_config(None).matches(INT)
