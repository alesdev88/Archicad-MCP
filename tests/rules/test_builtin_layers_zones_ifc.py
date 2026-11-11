import pytest

from archicad_mcp.rules.builtin.ifc_readiness import IfcPropertyRequiredRule
from archicad_mcp.rules.builtin.layer_compliance import LayerComplianceRule
from archicad_mcp.rules.builtin.zone_checks import ZoneNumberRequiredRule
from archicad_mcp.rules.types import ElementInfo, ModelSnapshot, RuleConfigError, ZoneInfo


def test_layer_compliance_allowed_list():
    rule = LayerComplianceRule.from_config(
        {"id": "layers-std", "type": "layer-compliance", "allowed": ["A-WALL", "A-SLAB"]})
    els = (ElementInfo(guid="e-1", layer="A-WALL"),
           ElementInfo(guid="e-2", layer="Sketch"),
           ElementInfo(guid="e-3", layer=None))
    result = rule.check(ModelSnapshot(elements=els))
    assert set(result.failing_guids) == {"e-2", "e-3"}
    assert rule.needs == frozenset({"elements", "layers"})


def test_layer_compliance_pattern():
    rule = LayerComplianceRule.from_config(
        {"id": "layers-pattern", "type": "layer-compliance", "pattern": r"^[A-Z]-[A-Z]+$"})
    els = (ElementInfo(guid="e-1", layer="A-WALL"), ElementInfo(guid="e-2", layer="misc 01"))
    result = rule.check(ModelSnapshot(elements=els))
    assert result.failing_guids == ("e-2",)


def test_layer_compliance_requires_allowed_or_pattern():
    with pytest.raises(RuleConfigError):
        LayerComplianceRule.from_config({"id": "x", "type": "layer-compliance"})


def test_zone_number_required():
    rule = ZoneNumberRequiredRule.from_config({"id": "zones-numbered", "type": "zone-number-required"})
    zones = (ZoneInfo(guid="z-1", number="101", name="Office"),
             ZoneInfo(guid="z-2", number=None, name="Hall"),
             ZoneInfo(guid="z-3", number="", name=None))
    result = rule.check(ModelSnapshot(zones=zones))
    assert set(result.failing_guids) == {"z-2", "z-3"}
    assert rule.needs == frozenset({"zones"})


def test_ifc_property_required_checks_pset_values():
    rule = IfcPropertyRequiredRule.from_config(
        {"id": "ifc-fire", "type": "ifc-property-required",
         "property": "Pset_WallCommon.FireRating",
         "applies_to": {"element_type": "Wall"}})
    els = (ElementInfo(guid="w-1", element_type="Wall"),
           ElementInfo(guid="w-2", element_type="Wall"))
    snap = ModelSnapshot(elements=els, ifc_properties={
        "w-1": {"Pset_WallCommon.FireRating": "EI60"},
        "w-2": {},
    })
    result = rule.check(snap)
    assert result.failing_guids == ("w-2",)
    assert rule.needs == frozenset({"elements", "ifc"})


def test_ifc_rule_skips_without_tapir():
    rule = IfcPropertyRequiredRule.from_config(
        {"id": "ifc-fire", "type": "ifc-property-required", "property": "P.X"})
    result = rule.check(ModelSnapshot(elements=(ElementInfo(guid="w-1"),), ifc_properties=None))
    assert result.skipped is True
    assert "too old" in result.skip_reason and "Tapir" in result.skip_reason


def test_layer_compliance_skips_when_a_layer_was_not_read():
    rule = LayerComplianceRule.from_config(
        {"id": "layers-std", "type": "layer-compliance", "allowed": ["A-WALL"]})
    els = (ElementInfo(guid="e-1", layer="A-WALL"),
           ElementInfo(guid="e-2", layer=None, not_read={"layer": "because"}),
           ElementInfo(guid="e-3", layer=None, not_read={"layer": "other"}))
    result = rule.check(ModelSnapshot(elements=els))
    assert result.skipped is True and "'layer'" in result.skip_reason
    assert result.skip_reason == "'layer' could not be read on 2 element(s): because"


def test_layer_compliance_fails_on_a_readable_element_despite_an_unread_one():
    rule = LayerComplianceRule.from_config(
        {"id": "layers-std", "type": "layer-compliance", "allowed": ["A-WALL"]})
    els = (ElementInfo(guid="e-1", layer="Sketch"),
           ElementInfo(guid="e-2", layer=None, not_read={"layer": "because"}))
    result = rule.check(ModelSnapshot(elements=els))
    assert result.skipped is False and result.passed is False
    assert result.failing_guids == ("e-1",)
    assert result.message.endswith("; 1 element(s) could not be read and were not checked")


def _ifc_where_rule():
    return IfcPropertyRequiredRule.from_config(
        {"id": "ifc-fire", "type": "ifc-property-required",
         "property": "Pset_WallCommon.FireRating",
         "applies_to": {"element_type": "Wall",
                        "where": [{"property": "Load Bearing",
                                   "operator": "has_no_value"}]}})


def test_ifc_rule_does_not_fail_a_wall_whose_where_property_was_not_read():
    unread = ElementInfo(guid="w-1", element_type="Wall", not_read={"Load Bearing": "why"})
    result = _ifc_where_rule().check(
        ModelSnapshot(elements=(unread,), ifc_properties={"w-1": {}}))
    assert "w-1" not in result.failing_guids
    assert result.skipped is True and result.passed is False
    assert result.skip_reason == "'Load Bearing' could not be read on 1 element(s): why"


def test_ifc_rule_fails_a_readable_wall_beside_an_unread_one():
    unread = ElementInfo(guid="w-1", element_type="Wall", not_read={"Load Bearing": "why"})
    readable = ElementInfo(guid="w-2", element_type="Wall", properties={"Load Bearing": None})
    result = _ifc_where_rule().check(ModelSnapshot(
        elements=(unread, readable), ifc_properties={"w-1": {}, "w-2": {}}))
    assert result.skipped is False and result.failing_guids == ("w-2",)
    assert result.message.endswith("; 1 element(s) could not be read and were not checked")


def test_ifc_rule_skips_for_missing_ifc_before_anything_unread():
    unread = ElementInfo(guid="w-1", element_type="Wall", not_read={"Load Bearing": "why"})
    result = _ifc_where_rule().check(ModelSnapshot(elements=(unread,), ifc_properties=None))
    assert result.skipped is True and "Tapir" in result.skip_reason
