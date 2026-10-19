"""Addresses: one syntax for every kind of element data both tools take."""
import pytest

from archicad_mcp.core.element_address import (
    CLASSIFICATION,
    GDL,
    IFC,
    MARKER,
    PROPERTY,
    Address,
    ifc_parts,
    parse_address,
)


@pytest.mark.parametrize("text,kind,key", [
    ("Office/Status", PROPERTY, "Office/Status"),
    ("General_ElementID", PROPERTY, "General_ElementID"),
    ("8D07E8C2-4C9B-4F43-9C66-1F0E5F2D33A1", PROPERTY, "8D07E8C2-4C9B-4F43-9C66-1F0E5F2D33A1"),
    ("classification:Uniclass 2015", CLASSIFICATION, "Uniclass 2015"),
    ("gdl:A", GDL, "A"),
    ("GDL:gs_leaf_angle", GDL, "gs_leaf_angle"),
    ("marker: iNumber ", MARKER, "iNumber"),
    ("ifc:Pset_DoorCommon.FireRating", IFC, "Pset_DoorCommon.FireRating"),
])
def test_the_prefix_decides_the_kind(text, kind, key):
    address = parse_address(text)
    assert address == Address(text=text, kind=kind, key=key, wildcard=False)


def test_a_star_is_a_wildcard():
    address = parse_address("marker:*")
    assert address.kind == MARKER and address.wildcard


def test_ifc_splits_at_the_first_dot():
    assert ifc_parts(parse_address("ifc:Pset_DoorCommon.Fire.Rating")) == \
        ("Pset_DoorCommon", "Fire.Rating")


@pytest.mark.parametrize("text,words", [
    ("gdl: ", "parameter name"),
    ("classification:", "system"),
    ("ifc:NoDot", "ifc:<Pset>.<Name>"),
    ("ifc:.Name", "ifc:<Pset>.<Name>"),
])
def test_malformed_addresses_give_a_reason(text, words):
    reason = parse_address(text)
    assert isinstance(reason, str) and words in reason


def test_a_blank_address_stays_a_property_as_before():
    # A blank name read as a missing property (None) before addresses had
    # prefixes; it must not start failing whole calls.
    assert parse_address("").kind == PROPERTY
