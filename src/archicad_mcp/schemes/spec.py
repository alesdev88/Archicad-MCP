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
    Column,
    Scheme,
    field_value,
    same_target,
    set_field,
)

# Built-in fields addressable by name in a spec. Verified live: Quantity is
# Parameter_Type 1 with Parameter_Index -1003. Extend as more are confirmed;
# an unknown name is an error rather than a guess.
#
# The table stays small on purpose: these codes are undocumented by Archicad
# and are being mapped empirically, one verified example at a time, so a name
# is only added here once it has actually been confirmed live. Real schemes
# carry other built-in columns this table cannot yet name (measured
# examples: Parameter_Type 0 with Parameter_Index -1561 and -1599). For
# those, binding_from_bind's builtin branch also accepts a mapping of the
# raw numbers, e.g. bind: { builtin: { param_type: 0, param_index: -1561 } },
# so a spec can still express, and a clone can still reproduce, a column
# this table has no name for.
BUILTIN_FIELDS: dict[str, tuple[int, int]] = {
    "Quantity": (1, -1003),
}

GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
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


_BIND_KINDS = ("property", "gdl_param", "builtin")


def _bind_shape_error(bind: object) -> str | None:
    """None if bind has the shape every kind requires: a mapping naming
    exactly one recognised kind. A human-readable error otherwise.

    Shared by load_specs, which only collects this error into its returned
    list, and binding_from_bind, which raises SpecError from it. Factored out
    so the 'exactly one key, and it must be a recognised kind' rule cannot
    drift between the two: load_specs is the up-front check on a spec file,
    with no resolver or live model available yet; binding_from_bind is the
    last line of defence for a SchemeSpec built directly rather than loaded
    from YAML (see test_apply_rejects_duplicate_captions_even_in_a_hand_built_spec
    for why that path matters). Neither call site inspects the value that
    goes with the key: that part is kind-specific and stays in
    binding_from_bind alone.
    """
    if not isinstance(bind, dict) or len(bind) != 1:
        return f"bind must name exactly one of property, gdl_param, builtin. Got: {bind!r}"
    kind = next(iter(bind))
    if kind not in _BIND_KINDS:
        return f"Unknown bind kind {kind!r}. Use property, gdl_param, or builtin."
    return None


def binding_from_bind(bind: dict, resolver: Callable[[str], str] | None = None) -> Binding:
    shape_error = _bind_shape_error(bind)
    if shape_error is not None:
        raise SpecError(shape_error)
    kind, value = next(iter(bind.items()))
    if kind == "property":
        if GUID.match(str(value)):
            return Binding(kind=KIND_PROPERTY, property_guid=str(value))
        if resolver is None:
            # Merely having Archicad open changes nothing here: this
            # function only ever consults the resolver it was given, never
            # Archicad directly. Pointing at the resolver argument, rather
            # than at Archicad's running state, is what used to be
            # misleading: the edit_schedule_scheme tool now builds and
            # passes a resolver automatically when a spec needs one, but a
            # direct caller of apply_spec/binding_from_bind still has to
            # supply one itself.
            raise SpecError(
                f"Property {value!r} is a name, not a GUID, and this call was "
                "given no resolver to look it up. Pass a GUID, or call this "
                "with a resolver built from a live connection (see "
                "archicad_mcp.schemes.validate.property_index). The "
                "edit_schedule_scheme tool builds one automatically, "
                "connecting to Archicad, whenever a spec needs it.")
        return Binding(kind=KIND_PROPERTY, property_guid=resolver(str(value)),
                       property_name=str(value))
    if kind == "gdl_param":
        return Binding(kind=KIND_GDL_PARAM, property_name=str(value),
                       desc_name=str(value), param_type=GDL_PARAM_TYPE,
                       param_index=-1604)
    if kind == "builtin":
        if isinstance(value, dict):
            # The escape hatch for a builtin column this module cannot name
            # yet: the raw Parameter_Type/Parameter_Index pair, straight from
            # a live scheme, instead of a name from the (deliberately small,
            # see BUILTIN_FIELDS) lookup table.
            keys_ok = set(value) == {"param_type", "param_index"}
            types_ok = keys_ok and all(
                isinstance(value[k], int) and not isinstance(value[k], bool)
                for k in ("param_type", "param_index"))
            if not types_ok:
                raise SpecError(
                    "builtin as a mapping must have exactly 'param_type' and "
                    f"'param_index', both integers. Got: {value!r}")
            return Binding(kind=KIND_BUILTIN, param_type=value["param_type"],
                           param_index=value["param_index"])
        if not isinstance(value, str):
            known = ", ".join(sorted(BUILTIN_FIELDS)) or "none"
            raise SpecError(
                f"builtin must be a known field name ({known}) or a mapping with "
                f"'param_type' and 'param_index'. Got: {value!r}")
        if value not in BUILTIN_FIELDS:
            known = ", ".join(sorted(BUILTIN_FIELDS)) or "none"
            raise SpecError(f"Unknown built-in field {value!r}. Known: {known}.")
        param_type, param_index = BUILTIN_FIELDS[value]
        return Binding(kind=KIND_BUILTIN, param_type=param_type, param_index=param_index)
    # _bind_shape_error already restricts kind to _BIND_KINDS; unreachable
    # unless that tuple ever gains a name with no handler below.
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
        # "columns" and "criteria" are both optional and both default to
        # empty (absent or explicit null collapses to the same thing, since
        # .get() cannot tell them apart). Anything else that is not a list is
        # a malformed shape: collected as an error like every other one here,
        # never left to reach a "for c in <scalar>" loop below and raise an
        # uncaught TypeError (a truthy scalar such as columns: 5 or
        # columns: true used to do exactly that).
        raw_columns = entry.get("columns")
        if raw_columns is not None and not isinstance(raw_columns, list):
            errors.append(f"{path}: {entry['id']} has a 'columns' that is not "
                          f"a list, got {type(raw_columns).__name__}")
            continue
        raw_criteria = entry.get("criteria")
        if raw_criteria is not None and not isinstance(raw_criteria, list):
            errors.append(f"{path}: {entry['id']} has a 'criteria' that is not "
                          f"a list, got {type(raw_criteria).__name__}")
            continue
        columns = []
        for c in raw_columns or []:
            if not isinstance(c, dict) or "caption" not in c or "bind" not in c:
                errors.append(f"{path}: {entry['id']} has a column without "
                              "'caption' and 'bind'")
                columns = None
                break
            # Only the shape (a mapping naming exactly one recognised kind)
            # is checked here, via the same rule binding_from_bind uses. The
            # value that goes with the key (a GUID needing no resolver, a
            # name needing one, a builtin name or param_type/param_index
            # mapping) is not resolved at load time: load_specs has no
            # resolver and no live model, so that stays binding_from_bind's
            # job, run later from apply_spec.
            bind_error = _bind_shape_error(c["bind"])
            if bind_error is not None:
                errors.append(f"{path}: {entry['id']} column {c['caption']!r} "
                              f"has an invalid bind: {bind_error}")
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
                                criteria=raw_criteria or [], columns=columns))
    return specs, errors


# Archicad's own export carries a column's cell width once per page
# orientation. Only Width_of_cell_portrait has ever been confirmed present:
# the bundled sample fixture, itself byte-verified against a real Archicad
# export, has no Width_of_cell_landscape on any of its four Header_Items.
# Portrait is therefore treated as the authoritative current value; see
# _apply_width for what that means for Width_of_cell_landscape.
_WIDTH_FIELDS = ("Width_of_cell_portrait", "Width_of_cell_landscape")


def _apply_width(column: Column, caption: str, width: str) -> list[str]:
    """Write `width` to a column's cell-width field(s). Returns the change log
    entries for doing so, empty when the column already has this width.

    Compares only against Width_of_cell_portrait to decide whether anything
    needs to change: if it already reads `width`, this returns immediately
    without even looking at Width_of_cell_landscape, so a spec that repeats a
    column's current width is a true no-op regardless of whether that
    column's XML happens to carry a landscape field at all. This is what the
    previous version of this function got wrong: it called set_field
    unconditionally and reported nothing, so a dry run could show
    changes == [] for an edit that had, in fact, already rewritten the file
    (this tool's whole safety promise), and committing that same spec then
    produced a changed file with nothing in the log to explain why.

    A field that does not already exist on this column is never created:
    set_field's only way to create a missing field is the ET.SubElement
    fallback, which appends a child with no indentation or trailing newline
    of its own, producing output that is well-formed XML but visually
    malformed next to every hand-formatted sibling around it. Nothing in this
    codebase has confirmed Width_of_cell_landscape is a field Archicad writes
    for every scheme, so inventing one into a file that never had it risks
    the byte-exact guarantee ("mutate only what we model, leave the rest
    alone") far more than simply leaving it alone. Either way the gap is
    reported in the change log, so it is visible rather than silently
    absorbed.
    """
    if field_value(column.element, "Width_of_cell_portrait") == width:
        return []
    changes = [f"set width of column {caption!r} to {width!r}"]
    for tag in _WIDTH_FIELDS:
        if column.element.find(tag) is None:
            changes.append(f"column {caption!r} has no {tag} field; left unchanged")
            continue
        set_field(column.element, tag, width)
    return changes


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

    # Resolve every column's binding before anything below is touched. If any
    # bind is invalid, binding_from_bind raises here, before remove_column,
    # add_column, retarget_column, or the scheme rename have run a single
    # time, so a spec with one bad column among many leaves the scheme
    # exactly as it was, not partially edited. Resolving one at a time inside
    # the loop further down used to let a spec whose only column had a bad
    # bind delete every existing column first and raise only afterwards.
    bindings = [binding_from_bind(col_spec.bind, resolver) for col_spec in spec.columns]

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

    for target_index, (col_spec, binding) in enumerate(zip(spec.columns, bindings)):
        current = {c.caption: c for c in scheme.columns}
        if col_spec.caption in current:
            column = current[col_spec.caption]
            if not same_target(column.binding, binding):
                retarget_column(scheme, col_spec.caption, binding)
                changes.append(f"retargeted column {col_spec.caption!r}")
            if scheme.columns.index(column) != target_index:
                move_column(scheme, col_spec.caption, target_index)
                changes.append(f"moved column {col_spec.caption!r} to {target_index}")
        else:
            column = add_column(scheme, col_spec.caption, binding, index=target_index)
            changes.append(f"added column {col_spec.caption!r}")
        # column is carried forward from whichever branch above ran, rather
        # than re-derived by indexing a fresh caption dict here: retarget_column
        # and move_column mutate that same Column object in place (they never
        # replace it in scheme.columns), and add_column returns the one it just
        # inserted. A second by-caption lookup added nothing but a KeyError
        # this loop can never actually hit, so there is nothing left to catch.
        if col_spec.width is not None:
            changes.extend(_apply_width(column, col_spec.caption, str(col_spec.width)))
    return changes
