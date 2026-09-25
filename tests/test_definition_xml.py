from pathlib import Path

import pytest

from archicad_mcp.core.definition_xml import parse_classification_xml, parse_property_xml

XML = Path(__file__).parent / "fixtures" / "xml"


def test_real_property_export_lists_group_and_name():
    pairs = parse_property_xml((XML / "properties_mcp_test.xml").read_text(encoding="utf-8"))
    assert ("MCP Test", "Fire Rating") in pairs


def test_real_classification_export_lists_system_and_codes():
    systems = parse_classification_xml(
        (XML / "classifications_mcp_test.xml").read_text(encoding="utf-8"))
    assert [s for s, _ in systems] == ["MCP Test"]
    assert set(systems[0][1]) == {"Building", "Wall", "Slab", "Object", "Site"}


def test_property_names_inside_classification_ids_are_not_mistaken_for_groups():
    # A Property Manager export carries the classification systems too; their
    # <Name> elements must not turn into property groups or properties.
    text = ("<BuildingInformation><Classification><System><Name>S</Name><Items/></System>"
            "</Classification><PropertyDefinitionGroups><PropertyDefinitionGroup><Name>G</Name>"
            "<PropertyDefinitions><PropertyDefinition><Name>P</Name></PropertyDefinition>"
            "</PropertyDefinitions></PropertyDefinitionGroup></PropertyDefinitionGroups>"
            "</BuildingInformation>")
    assert parse_property_xml(text) == [("G", "P")]
    assert parse_classification_xml(text) == [("S", [])]


def test_malformed_xml_raises_value_error():
    with pytest.raises(ValueError, match="not valid XML"):
        parse_property_xml("<unclosed>")
