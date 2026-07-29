"""Derive the undocumented criteria code tables from paired scheme exports.

Archicad's Scheme Settings encodes each criterion as a numeric Param_Type and
Relation_Index with no public documentation. The way to learn them is
empirical: export a scheme, change exactly one criterion in the GUI, export
again, and diff. Run this on each pair and record the result in
docs/scheme-criteria-codes.md.

Usage:
    uv run python scripts/diff_scheme_criteria.py before.xml after.xml
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from archicad_mcp.schemes.model import is_element, leaf_value, parse_scheme
from archicad_mcp.schemes.xml_io import load_scheme_tree

# Fields already understood well enough that a change in them is expected
# rather than a discovery. _criterion_values below reads every child a
# Criterion actually has, known or not, so this list no longer gates what is
# visible. It is kept only to order the output (known fields first, in this
# order) and to decide what counts as "known" for the unrecognised marker.
WATCHED = [
    "Param_Type", "Relation_Index", "ACPropertyGuid", "ACPropertyName",
    "ACPropertyGroup", "ACPropertyType", "AndNext", "Before_Brackets",
    "After_Brackets", "ExtendedElem_ElemClassId", "ExtendedElem_SpecialType",
    "Variable_Type_ID", "Variable", "IFCType", "IFCAssignmentType",
]

# The one nested path under UniValue this tool already knows how to read
# (see docs/scheme-criteria-codes.md). Any other path under UniValue, or
# under any other tag, is a genuine discovery and gets flagged as such.
_KNOWN_NESTED = {"UniValue/Variant/Value"}
KNOWN_FIELDS = frozenset(WATCHED) | _KNOWN_NESTED

# Rank used to order the output: known fields first in WATCHED's order
# (nested UniValue paths grouped where UniValue used to sit in the old
# fixed dict), everything else after, sorted alphabetically among itself.
_FIELD_ORDER = {tag: i for i, tag in enumerate(WATCHED + ["UniValue"])}


def _field_sort_key(field: str) -> tuple:
    top = field.split("/", 1)[0]
    if top in _FIELD_ORDER:
        return (0, _FIELD_ORDER[top], field)
    return (1, 0, field)


def _flatten(el: ET.Element, prefix: str) -> dict[str, str]:
    """Flatten one Criterion child into {path: value} pairs, recursing into
    nested containers such as UniValue so a change several levels down comes
    back as a full path like "UniValue/Variant/Value" instead of collapsing
    into an ambiguous top-level "UniValue"."""
    children = [c for c in el if is_element(c)]
    values: dict[str, str] = {}
    if "value" in el.attrib or not children:
        values[prefix] = leaf_value(el)
    for child in children:
        values.update(_flatten(child, f"{prefix}/{child.tag}"))
    return values


def _criterion_values(criterion) -> dict[str, str]:
    """Every field actually present on this criterion, flattened to
    {path: value}. Walks the real children of the element instead of a
    fixed allowlist, so a field nobody has named yet still shows up the
    moment it changes, which is the entire point of this tool."""
    values: dict[str, str] = {}
    for child in criterion.element:
        if not is_element(child):
            continue  # skip comments and processing instructions
        values.update(_flatten(child, child.tag))
    return values


def diff_criteria(before_path: Path, after_path: Path) -> list[dict]:
    before = parse_scheme(load_scheme_tree(Path(before_path)))
    after = parse_scheme(load_scheme_tree(Path(after_path)))

    changes: list[dict] = []
    if len(before.criteria) != len(after.criteria):
        changes.append({"index": -1, "field": "criterion_count",
                        "before": str(len(before.criteria)),
                        "after": str(len(after.criteria))})

    for i in range(min(len(before.criteria), len(after.criteria))):
        b = _criterion_values(before.criteria[i])
        a = _criterion_values(after.criteria[i])
        for field_name in sorted(b.keys() | a.keys(), key=_field_sort_key):
            before_value = b.get(field_name, "")
            after_value = a.get(field_name, "")
            if before_value != after_value:
                change = {"index": i, "field": field_name,
                          "before": before_value, "after": after_value}
                if field_name not in KNOWN_FIELDS:
                    change["unrecognised"] = True
                changes.append(change)
    return changes


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    changes = diff_criteria(Path(sys.argv[1]), Path(sys.argv[2]))
    if not changes:
        print("No criteria differences.")
        return 0
    for c in changes:
        where = "count" if c["index"] < 0 else f"criterion {c['index']}"
        flag = "  [UNRECOGNISED FIELD]" if c.get("unrecognised") else ""
        print(f"{where}: {c['field']}: {c['before']!r} -> {c['after']!r}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
