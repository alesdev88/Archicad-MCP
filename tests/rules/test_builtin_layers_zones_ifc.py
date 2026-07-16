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
    assert result.skip_reason == "Tapir add-on required for IFC checks"
