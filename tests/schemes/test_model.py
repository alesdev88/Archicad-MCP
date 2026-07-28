from pathlib import Path
import xml.etree.ElementTree as ET

from archicad_mcp.schemes.model import (
    KIND_BUILTIN,
    KIND_GDL_PARAM,
    KIND_PROPERTY,
    parse_scheme,
)
from archicad_mcp.schemes.xml_io import load_scheme_tree

FIXTURE = Path(__file__).parent.parent / "fixtures" / "schemes" / "sample_scheme.xml"


def load():
    return parse_scheme(load_scheme_tree(FIXTURE))


def test_reads_scheme_header():
    s = load()
    assert s.scheme_id == "9001"
    assert s.name == "Sample Door Scheme"
    assert s.scheme_type == "Element_List"
    assert s.version == "29.0.0"


def test_columns_exclude_the_root_item():
    s = load()
    assert [c.caption for c in s.columns] == ["Door ID", "Quantity", "Fire Resistance"]
    assert s.root_item.caption == "Sample Door Scheme"


def test_column_order_follows_the_linked_list_not_document_order():
    # Reverse the XML children; the linked list still dictates the order.
    tree = load_scheme_tree(FIXTURE)
    items = tree.getroot().find("Header_Items")
    children = list(items)
    for c in children:
        items.remove(c)
    for c in reversed(children):
        items.append(c)
    s = parse_scheme(tree)
    assert [c.caption for c in s.columns] == ["Door ID", "Quantity", "Fire Resistance"]


def test_recognises_all_three_binding_kinds():
    s = load()
    by_caption = {c.caption: c.binding for c in s.columns}
    assert by_caption["Door ID"].kind == KIND_PROPERTY
    assert by_caption["Door ID"].property_guid == "69A58F6F-1111-4000-8000-000000000001"
    assert by_caption["Quantity"].kind == KIND_BUILTIN
    assert by_caption["Quantity"].param_type == 1
    assert by_caption["Quantity"].param_index == -1003
    assert by_caption["Fire Resistance"].kind == KIND_GDL_PARAM
    assert by_caption["Fire Resistance"].property_name == "Fire Rating Param"


def test_reads_criteria():
    s = load()
    assert len(s.criteria) == 2
    assert s.criteria[0].param_type == 88
    assert s.criteria[0].element_class_id == "D8F07689-9CFA-4FBE-AEB4-0A60B8E667EE"
    assert s.criteria[1].param_type == 232
    assert s.criteria[1].property_guid == "432FA53A-B71E-404B-A9D5-F1964237A3EB"


def test_comment_in_header_items_not_treated_as_column():
    """Comments inside Header_Items should not be mistaken for columns."""
    tree = load_scheme_tree(FIXTURE)
    items_el = tree.getroot().find("Header_Items")
    # Insert a comment as a child alongside the real items
    comment = ET.Comment("This is a comment, not a column")
    items_el.insert(1, comment)
    s = parse_scheme(tree)
    # The columns should still be the same 3, not 4
    assert [c.caption for c in s.columns] == ["Door ID", "Quantity", "Fire Resistance"]
