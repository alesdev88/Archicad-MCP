from pathlib import Path
import xml.etree.ElementTree as ET

from archicad_mcp.schemes.model import (
    KIND_BUILTIN,
    KIND_GDL_PARAM,
    KIND_PROPERTY,
    Scheme,
    field_value,
    is_element,
    parse_scheme,
    set_field,
)
from archicad_mcp.schemes.xml_io import load_scheme_tree

FIXTURE = Path(__file__).parent.parent / "fixtures" / "schemes" / "sample_scheme.xml"


def load():
    return parse_scheme(load_scheme_tree(FIXTURE))


def _item(
    item_id: str,
    parent: str,
    caption: str,
    *,
    first_child: str = "0",
    previous: str = "0",
    next_: str = "0",
    index: str = "0",
) -> str:
    """A minimal Header_Item fragment: only the fields parse_scheme reads for
    tree structure (ID_of_Item/Parent/firstChild/next), plus a Caption to
    tell columns apart, plus Index_of_Columns for the ordering test, plus
    ID_of_previous for verisimilitude even though parse_scheme's traversal
    never reads it: it walks forward from ID_of_firstChild via ID_of_next
    alone, and ID_of_previous is written by relink (columns.py), not read by
    parse_scheme. Binding fields (ACPropertyGuid, Parameter_Type, ...) are
    left out on purpose: field_value/_int_field default them to '' / 0, and
    none of the tests using this helper assert on binding."""
    return (
        "<Header_Item>"
        f'<Index_of_Columns value="{index}"/>'
        f'<ID_of_Item value="{item_id}"/>'
        f'<ID_of_Parent value="{parent}"/>'
        f'<ID_of_firstChild value="{first_child}"/>'
        f'<ID_of_previous value="{previous}"/>'
        f'<ID_of_next value="{next_}"/>'
        f"<Caption>{caption}</Caption>"
        "</Header_Item>"
    )


def _scheme_xml(*items: str) -> str:
    """Wrap Header_Item fragments in a minimal Scheme_Settings/Header_Items
    document, standing in for a full Archicad export."""
    return (
        '<Scheme_Settings ID="1" Name="s" Scheme_Type="Element_List" Version="29.0.0">'
        "<Header_Items>" + "".join(items) + "</Header_Items>"
        "</Scheme_Settings>"
    )


def _parse_xml(xml: str) -> Scheme:
    """Parse a bare XML string straight into a Scheme, bypassing the fixture
    file and xml_io entirely. These tests pin parse_scheme's tree traversal,
    not the byte-exact round trip xml_io is responsible for."""
    return parse_scheme(ET.ElementTree(ET.fromstring(xml)))


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
    """Document order, ID_of_Item order, and Index_of_Columns order are all
    different from each other and from chain order here, so only an
    implementation that genuinely walks firstChild/next can get this right.

    chain:    Alpha -> Beta -> Gamma
    document: Gamma, Beta, Alpha   (physical order below)
    by id:    Beta(1001), Gamma(2001), Alpha(3001)
    by index: Gamma(0), Alpha(1), Beta(2)
    """
    xml = _scheme_xml(
        _item("9000", "0", "Root", first_child="3001", index="-1"),
        _item("2001", "9000", "Gamma", previous="1001", next_="0", index="0"),
        _item("1001", "9000", "Beta", previous="3001", next_="2001", index="2"),
        _item("3001", "9000", "Alpha", next_="1001", index="1"),
    )
    s = _parse_xml(xml)
    assert [c.caption for c in s.columns] == ["Alpha", "Beta", "Gamma"]


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


def test_is_element_rejects_comments_and_processing_instructions():
    """is_element is what keeps comments/PIs out of item_els, and therefore
    out of by_id and root_els. It cannot be exercised end to end through
    parse_scheme: a bare comment or PI has no children, so field_value on it
    is always "", which can never equal "0" (so it is never picked as a
    root), and empty string is falsy, so it can never be followed as a
    traversal id either (the chain walk's `current and ...` guard stops
    instead of looking it up). Asserting on the predicate directly is the
    only level at which this guarantee is actually checked."""
    assert is_element(ET.Comment("not a column")) is False
    assert is_element(ET.ProcessingInstruction("target", "data")) is False
    assert is_element(ET.Element("Header_Item")) is True


def test_comment_in_header_items_does_not_become_a_column():
    """Regression pin, not a filter-isolation test: a comment mixed into
    Header_Items must not crash the parser or change the column count. This
    holds regardless of is_element, since a comment's fields are always
    empty (see test_is_element_rejects_comments_and_processing_instructions
    for the test that actually isolates the filter)."""
    tree = load_scheme_tree(FIXTURE)
    items_el = tree.getroot().find("Header_Items")
    # Insert a comment as a child alongside the real items
    comment = ET.Comment("This is a comment, not a column")
    items_el.insert(1, comment)
    s = parse_scheme(tree)
    # The columns should still be the same 3, not 4
    assert [c.caption for c in s.columns] == ["Door ID", "Quantity", "Fire Resistance"]


def test_cycle_in_next_chain_truncates_at_the_repeat():
    """A cycle in ID_of_next must not hang the parser. The `seen` guard stops
    the walk the moment it would revisit a node, so the columns collected
    are exactly those visited once before the repeat, in chain order."""
    xml = _scheme_xml(
        _item("9000", "0", "Root", first_child="1001"),
        _item("1001", "9000", "Alpha", next_="1002"),
        _item("1002", "9000", "Beta", previous="1001", next_="1003"),
        _item("1003", "9000", "Gamma", previous="1002", next_="1001"),  # cycles back to Alpha
    )
    s = _parse_xml(xml)
    assert [c.caption for c in s.columns] == ["Alpha", "Beta", "Gamma"]


def test_dangling_next_id_truncates_the_rest():
    """Beta.next points at an id that appears nowhere in the document. Gamma
    has a valid ID_of_Parent, so it plainly belongs to the scheme, but the
    chain walk never reaches it: it stops the moment it follows the dangling
    id and finds nothing in by_id."""
    xml = _scheme_xml(
        _item("9000", "0", "Root", first_child="1001"),
        _item("1001", "9000", "Alpha", next_="1002"),
        _item("1002", "9000", "Beta", previous="1001", next_="9999"),  # dangling, no such id
        _item("1003", "9000", "Gamma", previous="1002", next_="0"),
    )
    s = _parse_xml(xml)
    assert [c.caption for c in s.columns] == ["Alpha", "Beta"]


def test_missing_root_yields_no_columns_and_no_root_item():
    """No Header_Item has ID_of_Parent == "0", so there is nothing to anchor
    the chain walk on. Both root_item and columns come back empty."""
    xml = _scheme_xml(
        _item("1001", "9000", "Alpha", next_="1002"),
        _item("1002", "9000", "Beta", previous="1001", next_="0"),
    )
    s = _parse_xml(xml)
    assert s.root_item is None
    assert s.columns == []


def test_two_roots_picks_the_first_in_document_order():
    """Two Header_Items both claim ID_of_Parent == "0". Document order breaks
    the tie (see the comment in model.py above root_els): the first one in
    the file wins, not the one with the lowest id (an id sort would pick
    "Second Root", since 1000 < 5000)."""
    xml = _scheme_xml(
        _item("5000", "0", "First Root"),
        _item("1000", "0", "Second Root"),
    )
    s = _parse_xml(xml)
    assert s.root_item.caption == "First Root"


def test_item_orphaned_from_the_chain_is_missing_from_columns():
    """Orphan has a valid ID_of_Parent, so it plainly belongs to this scheme,
    but nothing's firstChild or next points to it. It is invisible to the
    chain walk and silently absent from columns."""
    xml = _scheme_xml(
        _item("9000", "0", "Root", first_child="1001"),
        _item("1001", "9000", "Alpha", next_="0"),
        _item("1002", "9000", "Orphan", next_="0"),
    )
    s = _parse_xml(xml)
    assert [c.caption for c in s.columns] == ["Alpha"]


def test_parse_scheme_orphans_is_empty_for_a_well_formed_scheme():
    """The sample fixture's four Header_Items are all either the root or
    reachable through the sibling chain, so parse_scheme has nothing to
    record as orphaned."""
    s = load()
    assert s.orphans == []


def test_parse_scheme_records_the_orphan_at_parse_time():
    """Same document as
    test_item_orphaned_from_the_chain_is_missing_from_columns: Orphan (1002)
    has a valid ID_of_Parent but nothing's firstChild or next points to it.
    parse_scheme must snapshot it into scheme.orphans right here, at parse
    time, before any mutation runs: relink (columns.py) later re-appends
    exactly this snapshot, and must not try to rediscover orphans by set
    difference against scheme.columns after a mutation has already changed
    that list. See the relink docstring in columns.py for why that would
    silently undo remove_column."""
    xml = _scheme_xml(
        _item("9000", "0", "Root", first_child="1001"),
        _item("1001", "9000", "Alpha", next_="0"),
        _item("1002", "9000", "Orphan", next_="0"),
    )
    s = _parse_xml(xml)
    assert [field_value(e, "Caption") for e in s.orphans] == ["Orphan"]


def test_set_field_creates_a_missing_field_with_a_value_attribute_not_text():
    """set_field's only way to create a field that does not already exist is
    the ET.SubElement fallback. Every other test that calls set_field does so
    on a field the fixture (or a hand-built element) already carries, either
    rewriting an existing 'value' attribute or existing text, so nothing pins
    the shape of a freshly created field. This matters: Caption and
    Parameter_Desc_Name carry their payload as element text rather than a
    'value' attribute (see leaf_value's two branches), so a caller relying on
    set_field to create one of those correctly, or a future edit that deletes
    this fallback or swaps it for writing .text, would be silently wrong
    without a test built on an element missing the field entirely, as here,
    ever noticing."""
    el = ET.Element("Header_Item")
    assert el.find("Brand_New_Field") is None

    set_field(el, "Brand_New_Field", "42")

    child = el.find("Brand_New_Field")
    assert child is not None
    assert child.attrib == {"value": "42"}
    assert child.text is None
    assert field_value(el, "Brand_New_Field") == "42"
