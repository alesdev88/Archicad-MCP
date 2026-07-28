import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from archicad_mcp.schemes.columns import (
    ColumnNotFound,
    DuplicateColumnCaption,
    add_column,
    move_column,
    relink,
    remove_column,
    rename_column,
    retarget_column,
)
from archicad_mcp.schemes.model import (
    KIND_BUILTIN,
    KIND_GDL_PARAM,
    KIND_PROPERTY,
    NULL_GUID,
    Binding,
    field_value,
    parse_scheme,
)
from archicad_mcp.schemes.xml_io import dumps_scheme_tree, load_scheme_tree

FIXTURE = Path(__file__).parent.parent / "fixtures" / "schemes" / "sample_scheme.xml"


def load():
    return parse_scheme(load_scheme_tree(FIXTURE))


def reparse(scheme):
    """Serialise and parse again, so assertions test what a file would contain
    rather than the in-memory objects we just mutated."""
    return parse_scheme(ET.ElementTree(ET.fromstring(dumps_scheme_tree(scheme.tree))))


def _item(item_id, parent, caption, *, first_child="0", previous="0", next_="0", index="0"):
    """A minimal Header_Item fragment carrying only the fields parse_scheme
    and _next_item_id read: ID_of_Item/Parent/firstChild/previous/next for
    tree structure, Index_of_Columns, and a Caption to tell items apart.
    Binding fields are left out on purpose, mirroring the equivalent helper
    in test_model.py: field_value/_int_field default them to '' / 0."""
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


def _scheme_xml(*items):
    """Wrap Header_Item fragments in a minimal Scheme_Settings/Header_Items
    document, standing in for a full Archicad export."""
    return (
        '<Scheme_Settings ID="1" Name="s" Scheme_Type="Element_List" Version="29.0.0">'
        "<Header_Items>" + "".join(items) + "</Header_Items>"
        "</Scheme_Settings>"
    )


def assert_chain_is_intact(scheme):
    cols = scheme.columns
    root_id = scheme.root_item.item_id
    assert field_value(scheme.root_item.element, "Numbers_of_Columns") == str(len(cols))
    expected_first = cols[0].item_id if cols else "0"
    assert field_value(scheme.root_item.element, "ID_of_firstChild") == expected_first
    for i, c in enumerate(cols):
        prev = cols[i - 1].item_id if i > 0 else "0"
        nxt = cols[i + 1].item_id if i < len(cols) - 1 else "0"
        assert field_value(c.element, "ID_of_previous") == prev
        assert field_value(c.element, "ID_of_next") == nxt
        assert field_value(c.element, "ID_of_Parent") == root_id
        # 1-based: Archicad numbers columns 1..n, root stays -1 (see the
        # comment on relink's Index_of_Columns write in columns.py).
        assert field_value(c.element, "Index_of_Columns") == str(i + 1)
    ids = [c.item_id for c in cols]
    uniques = [field_value(c.element, "UniqueID") for c in cols]
    assert len(set(ids)) == len(ids)
    assert len(set(uniques)) == len(uniques)


def test_relink_alone_changes_nothing():
    original = FIXTURE.read_text(encoding="utf-8")
    scheme = load()
    relink(scheme)
    assert dumps_scheme_tree(scheme.tree) == original


def test_remove_column():
    scheme = load()
    remove_column(scheme, "Quantity")
    assert [c.caption for c in scheme.columns] == ["Door ID", "Fire Resistance"]
    assert_chain_is_intact(scheme)
    assert [c.caption for c in reparse(scheme).columns] == ["Door ID", "Fire Resistance"]


def test_remove_column_deletes_the_element_from_header_items():
    """Checking scheme.columns and reparse(scheme).columns (test_remove_column
    above) is not enough: both walk the sibling chain, so a Header_Item that
    was reclassified as an orphan and silently re-appended is invisible to
    either. This is exactly the regression a previous orphan-preserving fix
    introduced: relink computed orphans as "everything in header_items_el
    that is neither the root nor in scheme.columns", evaluated *after*
    remove_column had already dropped Quantity from scheme.columns, so
    Quantity matched that definition perfectly and was re-appended.
    Verified empirically: before this fix, the fixture still holds 4
    Header_Item elements, with "Quantity" still among their captions, after
    remove_column(scheme, "Quantity").
    """
    scheme = load()
    assert len(list(scheme.header_items_el)) == 4
    assert scheme.orphans == []
    remove_column(scheme, "Quantity")
    # remove_column must not manufacture a new orphan out of what it removed.
    assert scheme.orphans == []
    remaining = list(scheme.header_items_el)
    assert len(remaining) == 3
    assert "Quantity" not in [field_value(e, "Caption") for e in remaining]


def test_add_then_remove_a_column_leaves_element_count_unchanged():
    """Guards against the file growing on every edit. Under the regression
    described in test_remove_column_deletes_the_element_from_header_items,
    every remove_column left a dead Header_Item behind instead of deleting
    it, so this add-then-remove round trip would have left 5 elements
    (4 original + 1 added, with the removal doing nothing) instead of
    returning to the original 4.
    """
    scheme = load()
    before = len(list(scheme.header_items_el))
    add_column(scheme, "Notes", Binding(kind=KIND_BUILTIN))
    remove_column(scheme, "Notes")
    assert len(list(scheme.header_items_el)) == before


def test_remove_unknown_column_raises():
    with pytest.raises(ColumnNotFound):
        remove_column(load(), "Nope")


def test_add_column_appends_by_default():
    scheme = load()
    add_column(scheme, "Notes", Binding(kind=KIND_PROPERTY,
                                        property_guid="11111111-2222-3333-4444-555555555555"))
    assert [c.caption for c in scheme.columns][-1] == "Notes"
    assert_chain_is_intact(scheme)
    round_tripped = reparse(scheme)
    assert round_tripped.columns[-1].caption == "Notes"
    assert round_tripped.columns[-1].binding.kind == KIND_PROPERTY


def test_add_column_at_index():
    scheme = load()
    add_column(scheme, "Notes", Binding(kind=KIND_BUILTIN, param_type=1, param_index=-1003),
               index=0)
    assert [c.caption for c in scheme.columns][0] == "Notes"
    assert_chain_is_intact(scheme)


def test_added_column_gets_fresh_ids():
    scheme = load()
    existing = {c.item_id for c in scheme.columns} | {scheme.root_item.item_id}
    new = add_column(scheme, "Notes", Binding(kind=KIND_BUILTIN))
    assert new.item_id not in existing
    assert_chain_is_intact(scheme)


def test_index_of_columns_is_one_based_like_real_archicad_exports():
    """Measured directly on two real Archicad 29.0.0 scheme exports (a
    27-column door schedule, a 20-column window schedule): both number
    columns starting at 1, with the root Header_Item fixed at -1. Pins that
    convention explicitly so a future edit cannot quietly simplify relink
    back to 0-based indices without a test noticing."""
    scheme = load()
    add_column(scheme, "Notes", Binding(kind=KIND_BUILTIN))
    cols = scheme.columns
    assert field_value(cols[0].element, "Index_of_Columns") == "1"
    assert field_value(cols[-1].element, "Index_of_Columns") == str(len(cols))
    assert field_value(scheme.root_item.element, "Index_of_Columns") == "-1"


def test_added_column_avoids_colliding_with_a_high_id_orphan():
    """_next_item_id must scan every Header_Item present in the file, not
    just root_item plus the reachable sibling chain. An orphan (a Header_Item
    with a valid ID_of_Parent but not linked into the chain, the parse case
    pinned by test_item_orphaned_from_the_chain_is_missing_from_columns in
    test_model.py) is invisible to scheme.columns, so a scan limited to
    root_item + columns under-counts the true maximum id in the file.

    Root is 9000, the one reachable column is 1001: a scan blind to the
    orphan computes next id 9001, exactly the orphan's id, an actual
    collision, not a hypothetical one.
    """
    xml = _scheme_xml(
        _item("9000", "0", "Root", first_child="1001", index="-1"),
        _item("1001", "9000", "Alpha", index="1"),
        _item("9001", "9000", "Orphan", index="2"),  # not linked into the chain
    )
    scheme = parse_scheme(ET.ElementTree(ET.fromstring(xml)))
    assert [c.caption for c in scheme.columns] == ["Alpha"]

    new = add_column(scheme, "Notes", Binding(kind=KIND_BUILTIN))
    assert new.item_id == "9002"


def test_move_column():
    scheme = load()
    move_column(scheme, "Fire Resistance", 0)
    assert [c.caption for c in scheme.columns] == ["Fire Resistance", "Door ID", "Quantity"]
    assert_chain_is_intact(scheme)


def test_rename_column():
    scheme = load()
    rename_column(scheme, "Quantity", "Count")
    assert [c.caption for c in scheme.columns] == ["Door ID", "Count", "Fire Resistance"]
    assert reparse(scheme).columns[1].caption == "Count"


def test_retarget_column():
    scheme = load()
    retarget_column(scheme, "Fire Resistance",
                    Binding(kind=KIND_PROPERTY,
                            property_guid="99999999-8888-7777-6666-555555555555"))
    col = reparse(scheme).columns[2]
    assert col.binding.kind == KIND_PROPERTY
    assert col.binding.property_guid == "99999999-8888-7777-6666-555555555555"
    # The old GDL parameter fields must be cleared, not left behind.
    assert col.binding.param_type == 0


def test_removing_every_column_leaves_a_valid_scheme():
    scheme = load()
    for caption in ["Door ID", "Quantity", "Fire Resistance"]:
        remove_column(scheme, caption)
    assert scheme.columns == []
    assert_chain_is_intact(scheme)
    assert field_value(scheme.root_item.element, "ID_of_firstChild") == "0"


def test_removing_down_to_a_single_column_leaves_the_chain_intact():
    """No existing test calls assert_chain_is_intact with exactly one column
    left: the closest is test_removing_every_column_leaves_a_valid_scheme,
    which goes all the way to zero. Pinned separately because a single
    remaining column is the case where ID_of_previous and ID_of_next are
    both "0" on the very same element, which is easy to get backwards."""
    scheme = load()
    remove_column(scheme, "Door ID")
    remove_column(scheme, "Quantity")
    assert [c.caption for c in scheme.columns] == ["Fire Resistance"]
    assert_chain_is_intact(scheme)
    sole = scheme.columns[0]
    assert field_value(sole.element, "ID_of_previous") == "0"
    assert field_value(sole.element, "ID_of_next") == "0"
    assert field_value(scheme.root_item.element, "ID_of_firstChild") == sole.item_id


def test_retarget_column_to_builtin():
    """Fire Resistance starts as a GDL parameter, so it carries a real
    ACPropertyName and Parameter_Desc_Name. Retargeting to builtin must clear
    both. test_retarget_column already covers retargeting to a property; this
    and test_retarget_column_to_gdl_param cover the other two target kinds
    _apply_binding can produce."""
    scheme = load()
    retarget_column(scheme, "Fire Resistance",
                    Binding(kind=KIND_BUILTIN, param_type=7, param_index=-1010))
    col = reparse(scheme).columns[2]
    assert col.binding.kind == KIND_BUILTIN
    assert col.binding.param_type == 7
    assert col.binding.param_index == -1010
    assert field_value(col.element, "ACPropertyGuid") == NULL_GUID
    assert field_value(col.element, "ACPropertyName") == ""
    assert field_value(col.element, "Parameter_Type") == "7"
    assert field_value(col.element, "Parameter_Index") == "-1010"
    assert field_value(col.element, "Parameter_Desc_Name") == ""


def test_retarget_column_to_gdl_param():
    """Door ID starts as a property binding, so it carries a real
    ACPropertyGuid. Retargeting to a GDL parameter must clear the guid back
    to NULL_GUID, since that field belongs to the property binding, not to
    GDL: this is the "stale GUID" _apply_binding's docstring warns about."""
    scheme = load()
    retarget_column(scheme, "Door ID",
                    Binding(kind=KIND_GDL_PARAM, property_name="Custom Param",
                            desc_name="Custom Desc"))
    col = reparse(scheme).columns[0]
    assert col.binding.kind == KIND_GDL_PARAM
    assert col.binding.property_name == "Custom Param"
    assert col.binding.desc_name == "Custom Desc"
    assert field_value(col.element, "ACPropertyGuid") == NULL_GUID
    assert field_value(col.element, "ACPropertyName") == "Custom Param"
    assert field_value(col.element, "Parameter_Type") == "180"
    assert field_value(col.element, "Parameter_Index") == "-1604"
    assert field_value(col.element, "Parameter_Desc_Name") == "Custom Desc"


def test_add_column_refuses_duplicate_caption():
    """Captions are the only key _find uses to look up a column. A duplicate
    would make retarget_column/rename_column silently act on whichever one
    _find happens to match first. Verified empirically: before this fix,
    adding a second "Door ID" to the sample fixture succeeds silently."""
    scheme = load()
    with pytest.raises(DuplicateColumnCaption):
        add_column(scheme, "Door ID", Binding(kind=KIND_BUILTIN))
    # The refusal must not leave a half-added column behind.
    assert [c.caption for c in scheme.columns] == ["Door ID", "Quantity", "Fire Resistance"]


def test_rename_column_refuses_existing_caption():
    """Same hazard as test_add_column_refuses_duplicate_caption, reached via
    rename_column instead of add_column."""
    scheme = load()
    with pytest.raises(DuplicateColumnCaption):
        rename_column(scheme, "Quantity", "Door ID")
    assert [c.caption for c in scheme.columns] == ["Door ID", "Quantity", "Fire Resistance"]


def test_orphan_survives_add_remove_and_move_column():
    """relink must not silently delete a Header_Item that exists in the file
    but is unreachable from the root's sibling chain (an orphan: see
    test_item_orphaned_from_the_chain_is_missing_from_columns in
    test_model.py). It is real data, not a column, so every mutation that
    calls relink must hand it back untouched: same object, same fields,
    still a child of Header_Items. Verified empirically: before this fix,
    building a scheme with an orphan and calling add_column removes the
    orphan entirely.

    test_added_column_avoids_colliding_with_a_high_id_orphan already proves
    _next_item_id sees the orphan when picking a fresh id; this test proves
    the orphan is still there afterwards.
    """
    xml = _scheme_xml(
        _item("9000", "0", "Root", first_child="1001", index="-1"),
        _item("1001", "9000", "Alpha", next_="1002", index="1"),
        _item("1002", "9000", "Beta", previous="1001", index="2"),
        _item("9001", "9000", "Orphan", index="2"),  # not linked into the chain
    )
    scheme = parse_scheme(ET.ElementTree(ET.fromstring(xml)))
    assert [c.caption for c in scheme.columns] == ["Alpha", "Beta"]
    orphan = next(el for el in scheme.header_items_el
                  if field_value(el, "Caption") == "Orphan")

    def assert_orphan_untouched():
        assert orphan in list(scheme.header_items_el)
        assert field_value(orphan, "ID_of_Item") == "9001"
        assert field_value(orphan, "ID_of_Parent") == "9000"
        assert field_value(orphan, "ID_of_previous") == "0"
        assert field_value(orphan, "ID_of_next") == "0"
        assert field_value(orphan, "Index_of_Columns") == "2"

    add_column(scheme, "Gamma", Binding(kind=KIND_BUILTIN))
    assert_orphan_untouched()

    remove_column(scheme, "Alpha")
    assert_orphan_untouched()

    move_column(scheme, "Beta", 0)
    assert_orphan_untouched()

    assert [c.caption for c in scheme.columns] == ["Beta", "Gamma"]


# --- Finding 1: a comment or processing instruction living directly inside
# Header_Items (an office note such as "<!-- do not reorder -->") used to be
# destroyed by any edit. relink cleared every child of header_items_el and
# re-appended only the root, the columns, and scheme.orphans; comment and PI
# nodes are excluded from orphans by the is_element filter in parse_scheme,
# so nothing re-added them. round_trips_exactly still returns True for such a
# file (a pure load/save cycle preserves comments fine, see test_xml_io.py),
# so the guard in edit_schedule_scheme accepts it and the tool reports no
# error while silently dropping the comment. ---

HEADER_COMMENT_TEXT = " office standard: do not reorder "


def _load_with_header_comment(text=HEADER_COMMENT_TEXT):
    """Load the fixture with a comment inserted as the first child of
    Header_Items, then parse it fresh. Inserting the comment into an
    already-parsed Scheme's header_items_el would not do: scheme.header_comments
    is snapshotted once, at parse time, exactly like scheme.orphans, so the
    comment must already be in the tree before parse_scheme runs in order to
    be captured at all."""
    tree = load_scheme_tree(FIXTURE)
    items_el = tree.getroot().find("Header_Items")
    items_el.insert(0, ET.Comment(text))
    return parse_scheme(tree)


def test_comment_in_header_items_survives_add_column():
    scheme = _load_with_header_comment()
    add_column(scheme, "Notes", Binding(kind=KIND_BUILTIN))
    assert f"<!--{HEADER_COMMENT_TEXT}-->" in dumps_scheme_tree(scheme.tree)


def test_comment_in_header_items_survives_remove_column():
    scheme = _load_with_header_comment()
    remove_column(scheme, "Quantity")
    assert f"<!--{HEADER_COMMENT_TEXT}-->" in dumps_scheme_tree(scheme.tree)


def test_comment_in_header_items_survives_move_column():
    scheme = _load_with_header_comment()
    move_column(scheme, "Fire Resistance", 0)
    assert f"<!--{HEADER_COMMENT_TEXT}-->" in dumps_scheme_tree(scheme.tree)


def test_comment_in_header_items_position_is_not_claimed_to_be_preserved():
    """relink re-appends header_comments after the columns and orphans rather
    than restoring them to their original slot: content survives, position
    does not. Documented here as a pin, not a complaint: asserting the
    comment merely appears somewhere in Header_Items (the three tests above)
    would also pass for an implementation that happened to preserve position
    too, so this checks the specific, weaker guarantee relink actually makes."""
    scheme = _load_with_header_comment()
    add_column(scheme, "Notes", Binding(kind=KIND_BUILTIN))
    header_children = list(scheme.header_items_el)
    # Inserted as the first child originally; relink now places it last,
    # after the root, every column, and every orphan.
    assert header_children[-1] is scheme.header_comments[0]
    assert header_children.index(scheme.header_comments[0]) != 0
