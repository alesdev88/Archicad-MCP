from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from archicad_mcp.schemes.columns import ColumnNotFound, DuplicateColumnCaption
from archicad_mcp.schemes.model import (
    KIND_GDL_PARAM,
    KIND_PROPERTY,
    NULL_GUID,
    Scheme,
    parse_scheme,
)
from archicad_mcp.schemes.spec import SpecError, apply_spec, load_specs
from archicad_mcp.schemes.xml_io import (
    load_scheme_tree,
    round_trips_exactly,
    save_scheme_tree,
)


def _load(path: str) -> Scheme | dict:
    """Returns a Scheme, or an {"error": ...} envelope the tool can return as-is."""
    try:
        p = Path(path).expanduser()
    except RuntimeError as exc:
        return {"error": f"Path {path} could not be resolved: {exc}"}
    try:
        # is_dir()/is_file() stat the path themselves. A path longer than the
        # OS name-length limit makes them raise OSError (ENAMETOOLONG) on
        # Python 3.12 and 3.13 instead of returning False; this must be caught
        # here same as the read failure below, or it escapes read_schedule_scheme
        # uncaught and breaks the "always returns a dict" contract.
        if p.is_dir():
            return {"error": f"{p} is a directory, not a scheme file. Point this at the "
                             "exported Scheme_Settings XML file instead."}
        if not p.is_file():
            return {"error": f"Scheme file not found: {p}. Export one from Archicad via "
                             "Document > Schedules > Scheme Settings > Export."}
    except OSError as exc:
        return {"error": f"{p} could not be read: {exc}"}
    try:
        tree = load_scheme_tree(p)
    except ET.ParseError as exc:
        return {"error": f"{p} is not valid XML: {exc}"}
    except OSError as exc:
        # ET.parse opens the file itself, so a file that exists but cannot be
        # read (permissions, transient I/O error) raises OSError here, not
        # ParseError. read_schedule_scheme must always return a dict, so this
        # has to be caught same as the parse failure above, just reported
        # with a message that does not claim the XML itself is invalid.
        return {"error": f"{p} could not be read: {exc}"}
    if tree.getroot().tag != "Scheme_Settings":
        return {"error": f"{p} is not a schedule scheme. Expected a Scheme_Settings "
                         f"root, got {tree.getroot().tag}."}
    scheme = parse_scheme(tree)
    # Every later operation reads through root_item (relink rewrites the chain
    # from it), so a scheme without one is rejected here rather than crashing
    # somewhere less obvious.
    if scheme.root_item is None:
        return {"error": f"{p} has no root Header_Item, so its column tree has no "
                         "anchor. The export is incomplete or corrupt."}
    return scheme


def _binding_detail(binding) -> str:
    if binding.kind == KIND_PROPERTY:
        return binding.property_guid
    if binding.kind == KIND_GDL_PARAM:
        return binding.property_name
    return f"type {binding.param_type}, index {binding.param_index}"


def _criterion_target(criterion) -> str:
    if criterion.element_class_id and criterion.element_class_id != NULL_GUID:
        return criterion.element_class_id
    return criterion.property_guid


def read_schedule_scheme(path: str) -> dict:
    scheme = _load(path)
    if isinstance(scheme, dict):
        return scheme
    return {
        "name": scheme.name,
        "scheme_id": scheme.scheme_id,
        "scheme_type": scheme.scheme_type,
        "version": scheme.version,
        "column_count": len(scheme.columns),
        "columns": [{"index": i, "caption": c.caption, "binds_to": c.binding.kind,
                     "detail": _binding_detail(c.binding)}
                    for i, c in enumerate(scheme.columns)],
        "criteria": [{"param_type": c.param_type, "relation_index": c.relation_index,
                      "target": _criterion_target(c), "and_next": c.and_next}
                     for c in scheme.criteria],
    }


def edit_schedule_scheme(path: str, spec_path: str, spec_id: str | None = None,
                         output: str | None = None, dry_run: bool = True) -> dict:
    scheme = _load(path)
    if isinstance(scheme, dict):
        return scheme

    specs, errors = load_specs(Path(spec_path).expanduser())
    if errors:
        return {"error": "Spec file problems: " + "; ".join(errors)}
    if not specs:
        return {"error": f"No scheme specs found in {spec_path}."}
    if spec_id is None:
        spec = specs[0]
    else:
        matched = [s for s in specs if s.spec_id == spec_id]
        if not matched:
            known = ", ".join(s.spec_id for s in specs)
            return {"error": f"Unknown spec id {spec_id!r}. Available: {known}."}
        spec = matched[0]

    source = Path(path).expanduser()
    # The guard for everything we do not model. If the input contains a
    # construct our serializer would rewrite (a document-level comment, an
    # explicit <Tag></Tag>), refuse rather than silently mangling it.
    if not round_trips_exactly(source):
        return {"error": f"{source} contains XML this server would rewrite when "
                         "saving, so editing it could corrupt parts of the scheme "
                         "outside the columns and criteria. This does not happen "
                         "with schemes exported by Archicad. Re-export it from "
                         "Scheme Settings rather than hand-editing the XML."}

    warnings = []
    if spec.template and spec.template != source.name:
        warnings.append(
            f"Spec {spec.spec_id!r} was written against {spec.template!r} but is "
            f"being applied to {source.name!r}. Check this is deliberate.")

    columns_before = [c.caption for c in scheme.columns]
    try:
        changes = apply_spec(spec, scheme)
    except (SpecError, ColumnNotFound, DuplicateColumnCaption) as exc:
        # apply_spec's own pre-checks (resolving every binding before any
        # mutation, scanning spec.columns for duplicate captions up front)
        # make ColumnNotFound and DuplicateColumnCaption unreachable through a
        # well-formed spec today, but they are documented exceptions of the
        # column and spec layers this function sits on top of. This tool's
        # contract, like read_schedule_scheme's, is to always return a dict:
        # nothing from a layer below may escape as a raw exception, now or
        # after a future change to apply_spec.
        return {"error": f"Could not apply spec {spec.spec_id!r}: {exc}"}
    columns_after = [c.caption for c in scheme.columns]

    written = None
    if not dry_run:
        dest = Path(output).expanduser() if output else \
            source.with_suffix(".edited" + source.suffix)
        if dest.resolve() == source.resolve():
            return {"error": "Refusing to overwrite the input scheme. Pass a "
                             "different 'output' path so the export stays intact."}
        save_scheme_tree(scheme.tree, dest)
        written = str(dest)

    return {"spec_id": spec.spec_id, "dry_run": dry_run, "changes": changes,
            "warnings": warnings, "columns_before": columns_before,
            "columns_after": columns_after, "written": written}
