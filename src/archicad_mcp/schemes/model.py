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


def is_element(node) -> bool:
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


def same_target(a: Binding, b: Binding) -> bool:
    """True when two bindings name the same data, ignoring presentational
    fields a YAML spec never expresses (property_name, desc_name).

    A Binding read from a live scheme's XML (binding_of, below) carries
    ACPropertyName and Parameter_Desc_Name: fields the file uses only to
    display a label, never to identify what the column is bound to. A
    Binding built from a YAML spec (binding_from_bind in spec.py) never sets
    either, since a spec has no display label to invent. Comparing full
    dataclass equality then calls two bindings different whenever a spec's
    minimal Binding meets the fuller one read back from the file, even when
    both name the exact same target. apply_spec must use this instead of !=
    so that applying a spec which already describes the scheme is a true
    no-op: no retarget, no rewritten bytes.
    """
    if a.kind != b.kind:
        return False
    if a.kind == KIND_PROPERTY:
        return a.property_guid == b.property_guid
    if a.kind == KIND_GDL_PARAM:
        return a.property_name == b.property_name
    if a.kind == KIND_BUILTIN:
        return a.param_type == b.param_type and a.param_index == b.param_index
    return False


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
    orphans: list[ET.Element] = field(default_factory=list, repr=False)


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
                 if is_element(e)]
    by_id = {field_value(e, "ID_of_Item"): e for e in item_els}

    # A well-formed scheme has exactly one root (ID_of_Parent == "0"). If a
    # malformed file has more than one, document order breaks the tie: we
    # take the first match, not the one with the lowest id. Documented choice,
    # not an accident.
    root_els = [e for e in item_els if field_value(e, "ID_of_Parent") == "0"]
    root_item = _column_of(root_els[0]) if root_els else None
    root_el = root_els[0] if root_els else None

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

    # A Header_Item that is neither the root nor reachable through the
    # sibling chain just walked is an orphan: real data sitting in the file
    # that is not a column. Snapshot it now, from item_els (already filtered
    # through is_element, so comments and PIs are never counted), before any
    # mutation runs. relink (columns.py) must re-append exactly this
    # snapshot rather than rediscovering orphans later by set difference
    # against scheme.columns: after a mutation such as remove_column,
    # scheme.columns no longer reflects what this parse found reachable, and
    # a column that was just removed would be indistinguishable from a
    # genuine orphan, so it would be silently re-appended instead of
    # actually disappearing from the file.
    column_els = {col.element for col in columns}
    orphans = [e for e in item_els if e is not root_el and e not in column_els]

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
                  columns=columns, criteria=criteria, header_items_el=items_el,
                  orphans=orphans)
