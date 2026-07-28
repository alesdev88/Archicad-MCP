import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from archicad_mcp.schemes.columns import (
    ColumnNotFound,
    add_column,
    move_column,
    relink,
    remove_column,
    rename_column,
    retarget_column,
)
from archicad_mcp.schemes.model import (
    KIND_BUILTIN,
    KIND_PROPERTY,
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
        assert field_value(c.element, "Index_of_Columns") == str(i)
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
