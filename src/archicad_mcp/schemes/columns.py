from __future__ import annotations

import copy
import uuid
import xml.etree.ElementTree as ET

from archicad_mcp.schemes.model import (
    GDL_PARAM_TYPE,
    KIND_GDL_PARAM,
    KIND_PROPERTY,
    NULL_GUID,
    Binding,
    Column,
    Scheme,
    _is_element,
    binding_of,
    field_value,
    set_field,
)


class ColumnNotFound(Exception):
    pass


class DuplicateColumnCaption(Exception):
    pass


def _find(scheme: Scheme, caption: str) -> Column:
    for c in scheme.columns:
        if c.caption == caption:
            return c
    known = ", ".join(c.caption for c in scheme.columns) or "none"
    raise ColumnNotFound(f"No column captioned {caption!r}. Columns: {known}.")


def _next_item_id(scheme: Scheme) -> str:
    """Highest ID_of_Item anywhere in the file, plus one.

    Must scan every Header_Item actually present in scheme.header_items_el,
    not just root_item plus the reachable sibling chain (scheme.columns): a
    Header_Item can be orphaned from that chain and still hold an id, and an
    id computed only from the reachable items can collide with it.
    """
    highest = 0
    for node in scheme.header_items_el:
        if not _is_element(node):
            continue
        try:
            highest = max(highest, int(field_value(node, "ID_of_Item")))
        except ValueError:
            continue
    return str(highest + 1)


def relink(scheme: Scheme) -> None:
    """Rewrite every link field from the order of scheme.columns.

    Rebuilding the whole chain from one ordered list is far harder to get wrong
    than splicing prev/next pointers per operation, and it means every mutation
    shares one tested code path. Fields we do not model are untouched.

    A Header_Item that exists in the file but is not reachable from the root's
    sibling chain (an orphan, the same case _next_item_id scans for) is not a
    column, but it is still real data: relink is not entitled to delete it.
    Orphans are re-appended after the chained columns, left exactly as found
    (not renumbered, not rewired, not counted in Numbers_of_Columns or given
    an Index_of_Columns), so nothing in the file is lost.
    """
    items_el = scheme.header_items_el
    root_el = scheme.root_item.element
    column_els = {col.element for col in scheme.columns}
    orphans = [el for el in items_el
              if _is_element(el) and el is not root_el and el not in column_els]

    for el in list(items_el):
        items_el.remove(el)
    items_el.append(root_el)
    for col in scheme.columns:
        items_el.append(col.element)
    for orphan in orphans:
        items_el.append(orphan)

    set_field(root_el, "Numbers_of_Columns", str(len(scheme.columns)))
    set_field(root_el, "ID_of_firstChild",
              scheme.columns[0].item_id if scheme.columns else "0")

    for i, col in enumerate(scheme.columns):
        set_field(col.element, "ID_of_Parent", scheme.root_item.item_id)
        # 1-based, not 0-based: measured directly on two real Archicad 29.0.0
        # scheme exports (a 27-column door schedule, a 20-column window
        # schedule). Both show columns numbered 1..n with the root's own
        # Index_of_Columns fixed at -1. Do not "simplify" this to str(i).
        set_field(col.element, "Index_of_Columns", str(i + 1))
        set_field(col.element, "ID_of_previous",
                  scheme.columns[i - 1].item_id if i > 0 else "0")
        set_field(col.element, "ID_of_next",
                  scheme.columns[i + 1].item_id if i < len(scheme.columns) - 1 else "0")


def _apply_binding(item_el: ET.Element, binding: Binding) -> None:
    """Write a binding, clearing the fields the other binding kinds use so a
    retarget cannot leave a stale GUID or parameter index behind."""
    if binding.kind == KIND_PROPERTY:
        set_field(item_el, "ACPropertyGuid", binding.property_guid)
        set_field(item_el, "ACPropertyName", binding.property_name)
        set_field(item_el, "Parameter_Type", "0")
        set_field(item_el, "Parameter_Index", "0")
        set_field(item_el, "Parameter_Desc_Name", "")
    elif binding.kind == KIND_GDL_PARAM:
        set_field(item_el, "ACPropertyGuid", NULL_GUID)
        set_field(item_el, "ACPropertyName", binding.property_name)
        set_field(item_el, "Parameter_Type", str(binding.param_type or GDL_PARAM_TYPE))
        set_field(item_el, "Parameter_Index", str(binding.param_index or -1604))
        set_field(item_el, "Parameter_Desc_Name", binding.desc_name or binding.property_name)
    else:
        set_field(item_el, "ACPropertyGuid", NULL_GUID)
        set_field(item_el, "ACPropertyName", "")
        set_field(item_el, "Parameter_Type", str(binding.param_type))
        set_field(item_el, "Parameter_Index", str(binding.param_index))
        set_field(item_el, "Parameter_Desc_Name", "")


def add_column(scheme: Scheme, caption: str, binding: Binding,
               index: int | None = None, template_caption: str | None = None) -> Column:
    """Insert a column. Formatting is inherited by deep-copying a template
    column, so widths, fonts, totals and colours match the scheme rather than
    being invented."""
    if any(c.caption == caption for c in scheme.columns):
        raise DuplicateColumnCaption(
            f"A column captioned {caption!r} already exists. Captions are the "
            "only key used to address columns, so a duplicate would make "
            "retarget_column and rename_column act on whichever one is found first."
        )
    if template_caption is not None:
        template_el = _find(scheme, template_caption).element
    elif scheme.columns:
        template_el = scheme.columns[0].element
    else:
        template_el = scheme.root_item.element

    el = copy.deepcopy(template_el)
    item_id = _next_item_id(scheme)
    set_field(el, "ID_of_Item", item_id)
    set_field(el, "UniqueID", str(uuid.uuid4()).upper())
    set_field(el, "Caption", caption)
    set_field(el, "Numbers_of_Columns", "0")
    set_field(el, "ID_of_firstChild", "0")
    _apply_binding(el, binding)

    column = Column(item_id=item_id, caption=caption, binding=binding_of(el),
                    unique_id=field_value(el, "UniqueID"), element=el)
    at = len(scheme.columns) if index is None else index
    scheme.columns.insert(at, column)
    relink(scheme)
    return column


def remove_column(scheme: Scheme, caption: str) -> None:
    scheme.columns.remove(_find(scheme, caption))
    relink(scheme)


def move_column(scheme: Scheme, caption: str, to_index: int) -> None:
    column = _find(scheme, caption)
    scheme.columns.remove(column)
    scheme.columns.insert(to_index, column)
    relink(scheme)


def rename_column(scheme: Scheme, caption: str, new_caption: str) -> None:
    column = _find(scheme, caption)
    if any(c.caption == new_caption for c in scheme.columns if c is not column):
        raise DuplicateColumnCaption(
            f"A column captioned {new_caption!r} already exists. Captions are "
            "the only key used to address columns, so a duplicate would make "
            "retarget_column and rename_column act on whichever one is found first."
        )
    set_field(column.element, "Caption", new_caption)
    column.caption = new_caption


def retarget_column(scheme: Scheme, caption: str, binding: Binding) -> None:
    column = _find(scheme, caption)
    _apply_binding(column.element, binding)
    column.binding = binding_of(column.element)
