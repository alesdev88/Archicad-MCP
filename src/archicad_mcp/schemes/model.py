from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

NULL_GUID = "00000000-0000-0000-0000-000000000000"

# How a column gets its data. Verified against real 29.0.0 schemes: an Archicad
# property carries a real ACPropertyGuid; a GDL library parameter carries
# Parameter_Type 180 with the parameter's name in ACPropertyName; everything
# else is a built-in field addressed by Parameter_Type plus Parameter_Index
# (Quantity is type 1, index -1003).
KIND_PROPERTY = "property"
KIND_GDL_PARAM = "gdl_param"
KIND_BUILTIN = "builtin"

GDL_PARAM_TYPE = 180


def field_value(el: ET.Element, tag: str) -> str:
    """A child's payload. Most carry it in a 'value' attribute, some (Caption,
    Parameter_Desc_Name) carry it as text."""
    child = el.find(tag)
    if child is None:
        return ""
    if "value" in child.attrib:
        return child.attrib["value"]
    return (child.text or "").strip()


def set_field(el: ET.Element, tag: str, value: str) -> None:
    child = el.find(tag)
    if child is None:
        child = ET.SubElement(el, tag)
        child.set("value", value)
        return
    if "value" in child.attrib:
        child.set("value", value)
    else:
        child.text = value


def _int_field(el: ET.Element, tag: str) -> int:
    try:
        return int(field_value(el, tag))
    except ValueError:
        return 0


def _is_element(node) -> bool:
    """True if node is a real element, not a comment or PI."""
    return isinstance(node.tag, str)


@dataclass(frozen=True)
class Binding:
    kind: str
    property_guid: str = NULL_GUID
    property_name: str = ""
    param_type: int = 0
    param_index: int = 0
    desc_name: str = ""


@dataclass
class Column:
    item_id: str
    caption: str
    binding: Binding
    unique_id: str
    element: ET.Element = field(repr=False)


@dataclass
class Criterion:
    param_type: int
    relation_index: int
    property_guid: str
    element_class_id: str
    and_next: int
    element: ET.Element = field(repr=False)


@dataclass
class Scheme:
    tree: ET.ElementTree
    root: ET.Element = field(repr=False)
    scheme_id: str = ""
    name: str = ""
    scheme_type: str = ""
    version: str = ""
    root_item: Column | None = None
    columns: list[Column] = field(default_factory=list)
    criteria: list[Criterion] = field(default_factory=list)
    header_items_el: ET.Element | None = field(default=None, repr=False)


def binding_of(item_el: ET.Element) -> Binding:
    guid = field_value(item_el, "ACPropertyGuid")
    param_type = _int_field(item_el, "Parameter_Type")
    param_index = _int_field(item_el, "Parameter_Index")
    name = field_value(item_el, "ACPropertyName")
    desc = field_value(item_el, "Parameter_Desc_Name")
    if guid and guid != NULL_GUID:
        kind = KIND_PROPERTY
    elif param_type == GDL_PARAM_TYPE:
        kind = KIND_GDL_PARAM
    else:
        kind = KIND_BUILTIN
    return Binding(kind=kind, property_guid=guid or NULL_GUID, property_name=name,
                   param_type=param_type, param_index=param_index, desc_name=desc)


def _column_of(item_el: ET.Element) -> Column:
    return Column(item_id=field_value(item_el, "ID_of_Item"),
                  caption=field_value(item_el, "Caption"),
                  binding=binding_of(item_el),
                  unique_id=field_value(item_el, "UniqueID"),
                  element=item_el)


def parse_scheme(tree: ET.ElementTree) -> Scheme:
    root = tree.getroot()
    items_el = root.find("Header_Items")
    # Filter to only real elements, skipping comments and PIs
    item_els = [e for e in (list(items_el) if items_el is not None else [])
                 if _is_element(e)]
    by_id = {field_value(e, "ID_of_Item"): e for e in item_els}

    root_els = [e for e in item_els if field_value(e, "ID_of_Parent") == "0"]
    root_item = _column_of(root_els[0]) if root_els else None

    # Order comes from the sibling chain, never document order. Guard against a
    # malformed file looping forever.
    columns: list[Column] = []
    seen: set[str] = set()
    current = field_value(root_els[0], "ID_of_firstChild") if root_els else "0"
    while current and current != "0" and current in by_id and current not in seen:
        seen.add(current)
        el = by_id[current]
        columns.append(_column_of(el))
        current = field_value(el, "ID_of_next")

    criteria = []
    for c in root.findall("Criteria_Settings/Complex_Criteria/Criterion"):
        criteria.append(Criterion(param_type=_int_field(c, "Param_Type"),
                                  relation_index=_int_field(c, "Relation_Index"),
                                  property_guid=field_value(c, "ACPropertyGuid"),
                                  element_class_id=field_value(c, "ExtendedElem_ElemClassId"),
                                  and_next=_int_field(c, "AndNext"),
                                  element=c))

    return Scheme(tree=tree, root=root, scheme_id=root.get("ID", ""),
                  name=root.get("Name", ""), scheme_type=root.get("Scheme_Type", ""),
                  version=root.get("Version", ""), root_item=root_item,
                  columns=columns, criteria=criteria, header_items_el=items_el)
