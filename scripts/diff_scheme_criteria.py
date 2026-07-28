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
from pathlib import Path

from archicad_mcp.schemes.model import field_value, parse_scheme
from archicad_mcp.schemes.xml_io import load_scheme_tree

# Every field of a Criterion worth watching. Anything that moves between two
# exports is a candidate for the code table.
WATCHED = [
    "Param_Type", "Relation_Index", "ACPropertyGuid", "ACPropertyName",
    "ACPropertyGroup", "ACPropertyType", "AndNext", "Before_Brackets",
    "After_Brackets", "ExtendedElem_ElemClassId", "ExtendedElem_SpecialType",
    "Variable_Type_ID", "Variable", "IFCType", "IFCAssignmentType",
]


def _criterion_values(criterion) -> dict[str, str]:
    values = {tag: field_value(criterion.element, tag) for tag in WATCHED}
    value_el = criterion.element.find("UniValue/Variant/Value")
    values["UniValue"] = (value_el.text or "").strip() if value_el is not None else ""
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
        for tag in b:
            if b[tag] != a[tag]:
                changes.append({"index": i, "field": tag,
                                "before": b[tag], "after": a[tag]})
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
        print(f"{where}: {c['field']}: {c['before']!r} -> {c['after']!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
