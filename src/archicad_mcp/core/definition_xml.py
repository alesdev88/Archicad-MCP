"""Just enough of the Property / Classification Manager XML to preview an import:
which names it carries. Archicad does the real parsing on import.

Tag names are from a real AC 29 export (2026-09-25). A Property Manager export is
one BuildingInformation file holding both the classification systems and the
property groups, so either parser can be pointed at it.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

PROPERTY_GROUP = "PropertyDefinitionGroup"
PROPERTY = "PropertyDefinition"
SYSTEM = "System"
ITEM = "Item"
NAME = "Name"
CODE = "ID"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _root(text: str) -> ET.Element:
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"not valid XML: {exc}") from exc


def _child_text(el: ET.Element, name: str) -> str:
    for child in el:
        if _local(child.tag) == name:
            return (child.text or "").strip()
    return ""


def parse_property_xml(text: str) -> list[tuple[str, str]]:
    """(group name, property name) for every property definition in the file."""
    out = []
    for group in _root(text).iter():
        if _local(group.tag) != PROPERTY_GROUP:
            continue
        group_name = _child_text(group, NAME)
        for prop in group.iter():
            if _local(prop.tag) == PROPERTY:
                out.append((group_name, _child_text(prop, NAME)))
    return out


def parse_classification_xml(text: str) -> list[tuple[str, list[str]]]:
    """(system name, every item code in it) for every classification system."""
    out = []
    for system in _root(text).iter():
        if _local(system.tag) != SYSTEM:
            continue
        codes = [_child_text(item, CODE) for item in system.iter() if _local(item.tag) == ITEM]
        out.append((_child_text(system, NAME), codes))
    return out
