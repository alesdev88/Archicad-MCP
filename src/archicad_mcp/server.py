from __future__ import annotations

import argparse
import functools
import os
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from fastmcp import FastMCP
from multiconn_archicad.errors import APIErrorBase

from archicad_mcp import actions
from archicad_mcp.connection import (
    NO_OPEN_PROJECT_CODE,
    PORT_RANGE,
    ArchicadUnavailableError,
    InstanceInfo,
    discover_instances,
    get_connection,
)
from archicad_mcp.extract import build_snapshot, coverage_of
from archicad_mcp.rules.engine import (
    data_needs,
    element_type_scope,
    filter_by_tag,
    property_needs,
    run_rules,
)
from archicad_mcp.rules.loader import load_rules
from archicad_mcp.rules.types import Verdict

# Errors the tool layer converts into an actionable {"error": ...} envelope
# instead of leaking a protocol-level ToolError. ArchicadUnavailableError is
# raised by the connection layer (nothing answering); APIErrorBase is the shared
# base of every multiconn error raised *during* a command (StandardAPIError,
# TapirCommandError, timeouts, connection drops, ...).
_HANDLED_ERRORS = (ArchicadUnavailableError, APIErrorBase)


def _tool_error(exc: Exception) -> dict:
    if isinstance(exc, APIErrorBase):
        # Code 4001 ("Invalid program status") means Archicad cannot execute
        # commands right now: no project open, or a modal dialog blocking the
        # API while one is open. Only that code gets the hint; appending it to
        # every API error (e.g. a schema rejection) misleads.
        message = f"Archicad API error: {exc.message}"
        if getattr(exc, "code", None) == NO_OPEN_PROJECT_CODE:
            message += (". Open a project in Archicad if none is open; if one "
                        "is open, close any modal dialog (e.g. Object "
                        "Settings) blocking the API, and retry.")
        return {"error": message}
    return {"error": str(exc)}


def _guarded(func):
    """Wrap a tool handler so connection/API errors become {"error": ...}."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except _HANDLED_ERRORS as exc:
            return _tool_error(exc)
    return wrapper


def build_server(
    mode: str = "full",
    rules_dir: Path | None = None,
    port: int | None = None,
) -> FastMCP:
    if mode not in ("verdicts", "full"):
        raise ValueError(f"mode must be 'verdicts' or 'full', got {mode!r}")
    mcp = FastMCP("archicad-mcp")
    loaded = load_rules(rules_dir)
    default_port = port
    # Carried on the server so main() can report it without loading rules twice.
    mcp.archicad_rule_count = len(loaded.rules)
    mcp.archicad_rule_errors = len(loaded.errors)

    def _rules_subset(ruleset: str | None = None, rule_id: str | None = None):
        rules = loaded.rules
        if rule_id is not None:
            rules = [r for r in rules if r.rule_id == rule_id]
            if not rules:
                known = ", ".join(sorted(r.rule_id for r in loaded.rules)) or "none loaded"
                raise ArchicadUnavailableError(
                    f"Unknown rule '{rule_id}'. Loaded rules: {known}.")
            return rules
        matched = filter_by_tag(rules, ruleset)
        if ruleset is not None and not matched:
            tags = sorted({t for r in loaded.rules for t in r.tags})
            known = ", ".join(tags) or "no tags defined"
            raise ArchicadUnavailableError(
                f"Unknown ruleset tag '{ruleset}'. Known tags: {known}.")
        return matched

    def _verdict_for(rules, request_port: int | None) -> Verdict:
        conn = get_connection(request_port if request_port is not None else default_port)
        snapshot = build_snapshot(conn, data_needs(rules), property_needs(rules),
                                  element_types=element_type_scope(rules))
        return run_rules(rules, snapshot)

    # ---------- Tier 1: verdict tools (both modes) ----------

    @mcp.tool(description="List running Archicad instances: port, version, open project, "
                          "Tapir add-on availability. Call this first.")
    @_guarded
    def list_instances() -> dict:
        instances = [i.to_dict() for i in discover_instances()]
        if mode == "verdicts":
            # Privacy: verdicts mode must never leak project info to the AI.
            for inst in instances:
                inst["project_name"] = None
        return {"instances": instances}

    @mcp.tool(description="Aggregate element counts. by_type is always returned "
                          "(cheap and safe). Set include_layer_story=true to also "
                          "break down by layer and story, which reads a property "
                          "across every element and is refused on very large models "
                          "(can crash Archicad). Counts only, never element data. "
                          "'coverage' says what element_count spans: 'whole-plan' "
                          "with the Tapir add-on, 'model-elements-only' without it "
                          "(then it is NOT a project total).")
    @_guarded
    def get_model_summary(include_layer_story: bool = False,
                          port: int | None = None) -> dict:
        conn = get_connection(port if port is not None else default_port)
        needs = frozenset({"elements", "properties", "story"}) if include_layer_story \
            else frozenset({"elements"})
        snapshot = build_snapshot(conn, needs=needs)
        by_type = Counter(e.element_type for e in snapshot.elements)
        result = {"element_count": len(snapshot.elements),
                  "by_type": dict(by_type), **coverage_of(conn)}
        if include_layer_story:
            by_layer = Counter(e.layer for e in snapshot.elements if e.layer)
            by_story = Counter(str(e.story) for e in snapshot.elements
                               if e.story is not None)
            result["by_layer"] = dict(by_layer)
            result["by_story"] = dict(by_story)
        return result

    @mcp.tool(description="List loaded QA rules (id, type, severity, tags) and any "
                          "rule-file load errors.")
    def list_rules() -> dict:
        return {
            "source": loaded.source,
            "rules": [{"id": r.rule_id, "type": type(r).__name__,
                       "severity": r.severity, "tags": sorted(r.tags)}
                      for r in loaded.rules],
            "errors": loaded.errors,
        }

    @mcp.tool(description="Run one QA rule by id. Returns a verdict: pass/fail, "
                          "failure count, failing element GUIDs.")
    @_guarded
    def run_rule(rule_id: str, port: int | None = None) -> dict:
        rules = _rules_subset(rule_id=rule_id)
        return _verdict_for(rules, port).to_dict()

    @mcp.tool(description="Run all loaded QA rules (optionally only those tagged with "
                          "'ruleset') against the open model. Returns a scored verdict.")
    @_guarded
    def audit_delivery_readiness(ruleset: str | None = None, port: int | None = None) -> dict:
        return _verdict_for(_rules_subset(ruleset=ruleset), port).to_dict()

    @mcp.tool(description="Run only the IFC-related QA rules to check IFC export "
                          "readiness. Requires the Tapir add-on for IFC data.")
    @_guarded
    def verify_ifc_export_readiness(port: int | None = None) -> dict:
        ifc_rules = [r for r in loaded.rules if "ifc" in r.needs]
        if not ifc_rules:
            return {"error": "No IFC rules configured. Add 'ifc-property-required' "
                             "rules to your rules directory."}
        return _verdict_for(ifc_rules, port).to_dict()

    @mcp.tool(description="Highlight the elements failing a rule in the Archicad window "
                          "(requires Tapir add-on).")
    @_guarded
    def highlight_failures(rule_id: str, port: int | None = None) -> dict:
        rules = _rules_subset(rule_id=rule_id)
        verdict = _verdict_for(rules, port)
        guids = [g for r in verdict.results for g in r.failing_guids]
        conn = get_connection(port if port is not None else default_port)
        return actions.highlight_elements(conn, guids)

    @mcp.tool(description="Create an Archicad issue from a rule's failures and attach "
                          "the failing elements (requires Tapir add-on).")
    @_guarded
    def create_issues_from_failures(rule_id: str, port: int | None = None) -> dict:
        rules = _rules_subset(rule_id=rule_id)
        verdict = _verdict_for(rules, port)
        result = verdict.results[0]
        conn = get_connection(port if port is not None else default_port)
        return actions.create_issues(conn, rule_id, result.message,
                                     list(result.failing_guids))

    if mode == "full":
        _register_full_mode_tools(mcp, default_port)

    return mcp


def _register_full_mode_tools(mcp: FastMCP, default_port: int | None) -> None:
    from archicad_mcp.core import element_data as _element_data
    from archicad_mcp.core import query as _query
    from archicad_mcp.core import schemes as core_schemes

    def _conn(port: int | None):
        return get_connection(port if port is not None else default_port)

    @mcp.tool(description="Query elements with AND-combined filters: element_type, "
                          "layer, story, classification_system, selection_only. "
                          "Returns GUIDs and counts, plus 'coverage': 'whole-plan' "
                          "with the Tapir add-on, 'model-elements-only' without it "
                          "(2D elements such as markers, labels and section lines "
                          "are then invisible, so a count of 0 is not proof of "
                          "absence).")
    @_guarded
    def query_elements(element_type: str | None = None, layer: str | None = None,
                       story: int | None = None, classification_system: str | None = None,
                       selection_only: bool = False, port: int | None = None) -> dict:
        return _query.query_elements(_conn(port), element_type, layer, story,
                                     classification_system, selection_only)

    @mcp.tool(description="Read type, layer, requested properties (address user "
                          "properties as 'Group/Name') and optionally classifications "
                          "for the given element GUIDs.")
    @_guarded
    def get_element_data(guids: list[str], properties: list[str] | None = None,
                         include_classifications: bool = False,
                         port: int | None = None) -> dict:
        return _element_data.get_element_data(_conn(port), guids, properties,
                                              include_classifications)

    @mcp.tool(description="Write element property values. DRY-RUN BY DEFAULT: returns "
                          "planned changes (current -> new) without touching the model. "
                          "Pass dry_run=false to commit.")
    @_guarded
    def set_element_data(changes: list[dict], dry_run: bool = True,
                         port: int | None = None) -> dict:
        return _element_data.set_element_data(_conn(port), changes, dry_run)

    from archicad_mcp.core import create as _create
    from archicad_mcp.core import mutate as _mutate
    from archicad_mcp.core import selection as _selection

    @mcp.tool(description="Create elements (column/slab/zone/polyline/object/mesh) via "
                          "Tapir. DRY-RUN BY DEFAULT: shows the exact command and payload. "
                          "Pass dry_run=false to create. Other types: use execute_api_command.")
    @_guarded
    def create_elements(element_type: str, items: list[dict], dry_run: bool = True,
                        port: int | None = None) -> dict:
        return _create.create_elements(_conn(port), element_type, items, dry_run)

    @mcp.tool(description="Move elements by a vector {x,y,z} in meters. Refuses without "
                          "confirm=true.")
    @_guarded
    def move_elements(guids: list[str], vector: dict, confirm: bool = False,
                      port: int | None = None) -> dict:
        return _mutate.move_elements(_conn(port), guids, vector, confirm)

    @mcp.tool(description="Delete elements. IRREVERSIBLE. Refuses without confirm=true.")
    @_guarded
    def delete_elements(guids: list[str], confirm: bool = False,
                        port: int | None = None) -> dict:
        return _mutate.delete_elements(_conn(port), guids, confirm)

    @mcp.tool(description="Get, set, or clear the current element selection in Archicad. "
                          "action: 'get' | 'set' | 'clear'.")
    @_guarded
    def manage_selection(action: str, guids: list[str] | None = None,
                         port: int | None = None) -> dict:
        return _selection.manage_selection(_conn(port), action, guids)

    from archicad_mcp.core import attributes as _attributes
    from archicad_mcp.core import issues as _issues
    from archicad_mcp.core import project as _project
    from archicad_mcp.core import publish as _publish

    @mcp.tool(description="Project info: Archicad version, project name, stories, "
                          "hotlinks, geolocation presence (Tapir enriches).")
    @_guarded
    def get_project_info(port: int | None = None) -> dict:
        return _project.get_project_info(_conn(port))

    @mcp.tool(description="List attribute names by type: Layer, BuildingMaterial, "
                          "Composite, Surface, Profile, ZoneCategory.")
    @_guarded
    def list_attributes(attribute_type: str, port: int | None = None) -> dict:
        return _attributes.list_attributes(_conn(port), attribute_type)

    @mcp.tool(description="Manage Archicad issues (Tapir): action = list | create | "
                          "comment | attach | export_bcf | import_bcf.")
    @_guarded
    def manage_issues(action: str, name: str | None = None, issue_id: str | None = None,
                      comment: str | None = None, guids: list[str] | None = None,
                      bcf_path: str | None = None, port: int | None = None) -> dict:
        return _issues.manage_issues(_conn(port), action, name, issue_id,
                                     comment, guids, bcf_path)

    @mcp.tool(description="Fire an Archicad publisher set by name (Tapir).")
    @_guarded
    def publish(publisher_set_name: str, port: int | None = None) -> dict:
        return _publish.publish(_conn(port), publisher_set_name)

    @mcp.tool(description="Describe an exported Archicad schedule scheme XML: its "
                          "criteria and its ordered columns, with what each column "
                          "binds to. Schedules have no API, so export the scheme "
                          "first via Document > Schedules > Scheme Settings > Export "
                          "and pass the file path. Reads the file only, never "
                          "Archicad.")
    def read_schedule_scheme(path: str) -> dict:
        return core_schemes.read_schedule_scheme(path)

    @mcp.tool(description="Apply a YAML scheme spec to an exported schedule scheme "
                          "XML: set the columns and their order, retarget bindings, "
                          "rename the scheme. DRY-RUN BY DEFAULT: returns the before "
                          "and after column lists and writes nothing until "
                          "dry_run=false. Never overwrites the input; writes to "
                          "'output' or to <name>.edited.xml beside it. Import the "
                          "result via Document > Schedules > Scheme Settings > "
                          "Import. Criteria are preserved, not yet editable. A spec "
                          "that binds every property by GUID needs no Archicad "
                          "connection and runs fully offline; a spec that binds a "
                          "property by a 'Group/Name' string needs Archicad open "
                          "so the name can be resolved.")
    @_guarded
    def edit_schedule_scheme(path: str, spec_path: str, spec_id: str | None = None,
                             output: str | None = None, dry_run: bool = True,
                             port: int | None = None) -> dict:
        return core_schemes.edit_schedule_scheme(
            path, spec_path, spec_id, output, dry_run,
            port if port is not None else default_port)

    @mcp.tool(description="Check an exported schedule scheme against the open "
                          "project: do its property bindings still exist, and does "
                          "any column caption disagree with what it is bound to. "
                          "Reads property definitions only, not values, so it does "
                          "not risk the property-read crash.")
    @_guarded
    def validate_schedule_scheme(path: str, port: int | None = None) -> dict:
        return core_schemes.validate_schedule_scheme(
            path, port if port is not None else default_port)

    from archicad_mcp.gateway import execute as _gateway
    from archicad_mcp.gateway.registry import build_registry

    registry = build_registry()

    @mcp.tool(description="Catalog of ALL available Archicad API commands (official "
                          "JSON API + Tapir), optionally filtered by group.")
    def list_api_commands(group: str | None = None) -> dict:
        return _gateway.list_api_commands(registry, group)

    @mcp.tool(description="Full description and input schema for one API command. "
                          "Call before execute_api_command.")
    def describe_api_command(name: str) -> dict:
        return _gateway.describe_api_command(registry, name)

    @mcp.tool(description="Execute any Archicad API command by name (official 'API.*' "
                          "or Tapir). Params validated against the bundled schema "
                          "where available. Prefer the dedicated tools when one exists.")
    @_guarded
    def execute_api_command(name: str, params: dict | str | None = None,
                            port: int | None = None) -> dict:
        return _gateway.execute_api_command(registry, _conn(port), name, params)


_BANNER_PREFIX = "archicad-mcp:"

# What the user loses when the add-on is absent, named rather than implied, so
# the log line answers "why did create_elements fail" without a docs round trip.
_NO_TAPIR_NOTE = ("element creation, issues, IFC checks, highlighting and "
                  "publishing are unavailable, and element counts cover model "
                  "elements only")


def _instance_line(info: InstanceInfo, mode: str) -> str:
    if not info.project_open:
        detail = f" ({info.status_error})" if info.status_error else ""
        return (f"{_BANNER_PREFIX} Archicad on port {info.port} refused API "
                f"commands{detail}: it has no project open, or a modal dialog "
                "is blocking the API. Close any open dialog or open a project")
    parts = [f"Archicad {info.version} (build {info.build}) on port {info.port}"]
    # verdicts mode exists to keep project identity out of what this server
    # surfaces; the log is no exception.
    if info.project_name and mode != "verdicts":
        parts.append(f"project {info.project_name!r}")
    if info.tapir_available:
        parts.append(f"Tapir {info.tapir_version or 'version unknown'}")
    else:
        parts.append(f"Tapir add-on not installed ({_NO_TAPIR_NOTE})")
    return f"{_BANNER_PREFIX} " + ", ".join(parts)


def format_startup_banner(mode: str, rule_count: int,
                          instances: Sequence[InstanceInfo],
                          rule_errors: int = 0) -> str:
    """The diagnostic lines written to stderr at startup.

    This is what someone reads in mcp-server-archicad.log when the tools are
    not behaving, so every line has to be actionable on its own.
    """
    head = f"{_BANNER_PREFIX} mode={mode}, {rule_count} rules loaded"
    if rule_errors:
        head += f", {rule_errors} rule file(s) rejected (call list_rules for details)"
    lines = [head]
    if not instances:
        first, last = PORT_RANGE[0], PORT_RANGE[-1]
        lines.append(
            f"{_BANNER_PREFIX} no Archicad answering on ports {first}-{last}. "
            "Start Archicad 29 and open a project. Tools connect on demand, so "
            "this server does not need restarting once it is up.")
    else:
        lines.extend(_instance_line(info, mode) for info in instances)
    return "\n".join(lines)


def emit_startup_banner(mode: str, rule_count: int, rule_errors: int = 0) -> None:
    """Write the banner to stderr. Never raises, never touches stdout.

    Under stdio transport stdout is the JSON-RPC channel: one stray byte there
    corrupts the stream and the client drops the server. And a diagnostic that
    can itself abort startup is worse than no diagnostic, so discovery failure
    degrades to the config line alone.
    """
    try:
        instances = discover_instances()
    except Exception as exc:  # noqa: BLE001 - diagnostics must not break startup
        print(f"{_BANNER_PREFIX} mode={mode}, {rule_count} rules loaded "
              f"(instance discovery failed: {exc})", file=sys.stderr, flush=True)
        return
    print(format_startup_banner(mode, rule_count, instances, rule_errors),
          file=sys.stderr, flush=True)


def resolve_mode(raw: str | None) -> str:
    """Fall back to 'full' for an unset or blank mode.

    An .mcpb bundle substitutes an unfilled user_config field as an empty
    string, so the env var arrives set-but-empty. Passing that straight to
    argparse turns an optional setting into a startup crash.
    """
    return raw.strip() if raw and raw.strip() else "full"


def resolve_rules_dir(raw: str | Path | None) -> Path | None:
    """None for an unset or blank rules directory, so the bundled rules load.

    Path("") is Path("."), which is truthy. A blank env var therefore used to
    slip past an `if rules_dir` guard and scan the working directory, loading
    zero rules instead of falling back to the bundled examples.
    """
    text = str(raw).strip() if raw is not None else ""
    return Path(text) if text else None


def main() -> None:
    parser = argparse.ArgumentParser(prog="archicad-mcp")
    parser.add_argument("--mode", choices=["verdicts", "full"],
                        default=resolve_mode(os.environ.get("ARCHICAD_MCP_MODE")))
    parser.add_argument("--rules-dir", type=Path,
                        default=resolve_rules_dir(
                            os.environ.get("ARCHICAD_MCP_RULES_DIR")))
    parser.add_argument("--port", type=int, default=None,
                        help="Archicad API port (19723-19743); auto-detected if omitted")
    args, _ = parser.parse_known_args()
    rules_dir = resolve_rules_dir(args.rules_dir)
    server = build_server(mode=args.mode, rules_dir=rules_dir, port=args.port)
    emit_startup_banner(args.mode, server.archicad_rule_count,
                        server.archicad_rule_errors)
    server.run()


if __name__ == "__main__":
    main()
