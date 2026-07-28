from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from archicad_mcp.schemes.columns import (
    add_column,
    move_column,
    remove_column,
    retarget_column,
)
from archicad_mcp.schemes.model import (
    GDL_PARAM_TYPE,
    KIND_BUILTIN,
    KIND_GDL_PARAM,
    KIND_PROPERTY,
    Binding,
    Scheme,
    set_field,
)

# Built-in fields addressable by name in a spec. Verified live: Quantity is
# Parameter_Type 1 with Parameter_Index -1003. Extend as more are confirmed;
# an unknown name is an error rather than a guess.
BUILTIN_FIELDS: dict[str, tuple[int, int]] = {
    "Quantity": (1, -1003),
}

_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                   r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class SpecError(Exception):
    pass


@dataclass
class ColumnSpec:
    caption: str
    bind: dict
    width: str | None = None


@dataclass
class SchemeSpec:
    spec_id: str
    # Which export this spec was written against. Optional, and never used to
    # locate the file: the tool's own 'path' argument is the template. It exists
    # so applying the window spec to the door export can be caught and warned
    # about instead of silently producing nonsense.
    template: str | None = None
    name: str | None = None
    criteria: list[dict] = field(default_factory=list)
    columns: list[ColumnSpec] = field(default_factory=list)


def _duplicate_captions(columns: list[ColumnSpec]) -> list[str]:
    """Captions repeated more than once in columns, in first-seen order.

    Captions are the only key add_column/rename_column (columns.py) use to
    address a column, so a spec listing the same caption twice would
    eventually hit their DuplicateColumnCaption guard, a confusing exception
    pointing at the column layer instead of at the spec that actually caused
    it. Both callers below check this up front and refuse before that guard
    is ever reachable.
    """
    seen: set[str] = set()
    dupes: list[str] = []
    for c in columns:
        if c.caption in seen and c.caption not in dupes:
            dupes.append(c.caption)
        seen.add(c.caption)
    return dupes


def binding_from_bind(bind: dict, resolver: Callable[[str], str] | None = None) -> Binding:
    if not isinstance(bind, dict) or len(bind) != 1:
        raise SpecError(f"bind must name exactly one of property, gdl_param, builtin. "
                        f"Got: {bind!r}")
    kind, value = next(iter(bind.items()))
    if kind == "property":
        if _GUID.match(str(value)):
            return Binding(kind=KIND_PROPERTY, property_guid=str(value))
        if resolver is None:
            raise SpecError(
                f"Property {value!r} is a name, not a GUID, and no live model is "
                "available to resolve it. Pass a GUID, or run with Archicad open "
                "so the name can be looked up.")
        return Binding(kind=KIND_PROPERTY, property_guid=resolver(str(value)),
                       property_name=str(value))
    if kind == "gdl_param":
        return Binding(kind=KIND_GDL_PARAM, property_name=str(value),
                       desc_name=str(value), param_type=GDL_PARAM_TYPE,
                       param_index=-1604)
    if kind == "builtin":
        if value not in BUILTIN_FIELDS:
            known = ", ".join(sorted(BUILTIN_FIELDS)) or "none"
            raise SpecError(f"Unknown built-in field {value!r}. Known: {known}.")
        param_type, param_index = BUILTIN_FIELDS[value]
        return Binding(kind=KIND_BUILTIN, param_type=param_type, param_index=param_index)
    raise SpecError(f"Unknown bind kind {kind!r}. Use property, gdl_param, or builtin.")


def load_specs(path: Path) -> tuple[list[SchemeSpec], list[str]]:
    """Returns (specs, errors). A malformed file is reported, never raised, so a
    bad spec cannot take down the tool."""
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return [], [f"{path}: {exc}"]
    if not isinstance(raw, list):
        return [], [f"{path}: expected a list of scheme specs, got {type(raw).__name__}"]

    specs, errors = [], []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            errors.append(f"{path}: entry {i} is not a mapping")
            continue
        if not entry.get("id"):
            errors.append(f"{path}: entry {i} is missing 'id'")
            continue
        columns = []
        for c in entry.get("columns") or []:
            if not isinstance(c, dict) or "caption" not in c or "bind" not in c:
                errors.append(f"{path}: {entry['id']} has a column without "
                              "'caption' and 'bind'")
                columns = None
                break
            columns.append(ColumnSpec(caption=str(c["caption"]), bind=c["bind"],
                                      width=c.get("width")))
        if columns is None:
            continue
        dupes = _duplicate_captions(columns)
        if dupes:
            listed = ", ".join(repr(d) for d in dupes)
            errors.append(
                f"{path}: {entry['id']} lists the same column caption more than "
                f"once: {listed}. Captions are the only key used to address a "
                "column, so each one must be unique within a spec.")
            continue
        template = entry.get("template")
        specs.append(SchemeSpec(spec_id=str(entry["id"]),
                                template=str(template) if template else None,
                                name=entry.get("name"),
                                criteria=entry.get("criteria") or [], columns=columns))
    return specs, errors


def apply_spec(spec: SchemeSpec, scheme: Scheme,
               resolver: Callable[[str], str] | None = None) -> list[str]:
    """Make the scheme's columns match the spec. Returns a human-readable change
    log. Criteria are preserved as-is; editing them needs the Param_Type table
    that does not exist yet."""
    dupes = _duplicate_captions(spec.columns)
    if dupes:
        listed = ", ".join(repr(d) for d in dupes)
        raise SpecError(
            f"Spec {spec.spec_id!r} lists the same column caption more than once: "
            f"{listed}. Captions are the only key used to address a column, so "
            "each one must be unique within a spec. load_specs should have caught "
            "this already; check how this SchemeSpec was constructed.")

    changes: list[str] = []
    # Criteria editing needs the undocumented Param_Type table. Until it exists,
    # say so rather than silently dropping a criteria: block the user wrote.
    if spec.criteria:
        changes.append(
            f"IGNORED the criteria block ({len(spec.criteria)} entries): criteria "
            "editing is not implemented yet, so the template's criteria are kept "
            "unchanged. See docs/scheme-criteria-codes.md.")
    if spec.name is not None and spec.name != scheme.root.get("Name"):
        changes.append(f"renamed scheme to {spec.name!r}")
        scheme.root.set("Name", spec.name)

    wanted = [c.caption for c in spec.columns]
    for existing in [c.caption for c in scheme.columns]:
        if existing not in wanted:
            remove_column(scheme, existing)
            changes.append(f"removed column {existing!r}")

    for target_index, col_spec in enumerate(spec.columns):
        binding = binding_from_bind(col_spec.bind, resolver)
        current = {c.caption: c for c in scheme.columns}
        if col_spec.caption in current:
            column = current[col_spec.caption]
            if column.binding != binding:
                retarget_column(scheme, col_spec.caption, binding)
                changes.append(f"retargeted column {col_spec.caption!r}")
            if scheme.columns.index(column) != target_index:
                move_column(scheme, col_spec.caption, target_index)
                changes.append(f"moved column {col_spec.caption!r} to {target_index}")
        else:
            add_column(scheme, col_spec.caption, binding, index=target_index)
            changes.append(f"added column {col_spec.caption!r}")
        if col_spec.width is not None:
            column = {c.caption: c for c in scheme.columns}[col_spec.caption]
            set_field(column.element, "Width_of_cell_portrait", str(col_spec.width))
            set_field(column.element, "Width_of_cell_landscape", str(col_spec.width))
    return changes
