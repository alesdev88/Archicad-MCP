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


def _tool_meta(title: str, *, read_only: bool, destructive: bool = False) -> dict:
    """Decorator kwargs carrying the tool's title and its safety hints.

    The Connectors Directory review requires every tool to declare a title and
    the applicable readOnlyHint or destructiveHint, and clients act on them:
    a read-only tool may run without asking the user, a destructive one always
    prompts. So the hints are a safety contract, not listing metadata.

    Title twice on purpose. The MCP schema carries one at the top level and one
    inside annotations, different clients read different ones, and there is no
    version of this worth debugging later to save a line.

    Where the two ideas disagree, this codebase reads "destructive" as "changes
    the project or writes a file", which is wider than the MCP spec's "not
    purely additive". Creating an issue is additive and still marked
    destructive, because it lands in someone's .pln and a confirmation prompt
    is the right cost for that. Only transient view state (selection,
    highlighting) is a non-destructive write.
    """
    return {
        "title": title,
        "annotations": {"title": title, "readOnlyHint": read_only,
                        "destructiveHint": destructive},
    }


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
    gdl_workspace: Path | None = None,
) -> FastMCP:
    if mode not in ("verdicts", "full"):
        raise ValueError(f"mode must be 'verdicts' or 'full', got {mode!r}")
    mcp = FastMCP("archicad-mcp")
    loaded = load_rules(rules_dir)
    default_port = port
    # Carried on the server so main() can report it without loading rules twice.
    mcp.archicad_rule_count = len(loaded.rules)
    mcp.archicad_rule_errors = len(loaded.errors)
    mcp.archicad_rule_source = None if rules_dir is None else str(rules_dir)

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
                          "Tapir add-on availability. Call this first.",
              **_tool_meta("List Archicad instances", read_only=True, destructive=False))
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
                          "(then it is NOT a project total).",
              **_tool_meta("Summarize model contents", read_only=True, destructive=False))
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
                          "rule-file load errors.",
              **_tool_meta("List QA rules", read_only=True, destructive=False))
    def list_rules() -> dict:
        return {
            "source": loaded.source,
            "rules": [{"id": r.rule_id, "type": type(r).__name__,
                       "severity": r.severity, "tags": sorted(r.tags)}
                      for r in loaded.rules],
            "errors": loaded.errors,
        }

    @mcp.tool(description="Run one QA rule by id. Returns a verdict: pass/fail, "
                          "failure count, failing element GUIDs.",
              **_tool_meta("Run one QA rule", read_only=True, destructive=False))
    @_guarded
    def run_rule(rule_id: str, port: int | None = None) -> dict:
        rules = _rules_subset(rule_id=rule_id)
        return _verdict_for(rules, port).to_dict()

    @mcp.tool(description="Run all loaded QA rules (optionally only those tagged with "
                          "'ruleset') against the open model. Returns a scored verdict.",
              **_tool_meta("Audit delivery readiness", read_only=True, destructive=False))
    @_guarded
    def audit_delivery_readiness(ruleset: str | None = None, port: int | None = None) -> dict:
        return _verdict_for(_rules_subset(ruleset=ruleset), port).to_dict()

    @mcp.tool(description="Run only the IFC-related QA rules to check IFC export "
                          "readiness. Requires the Tapir add-on for IFC data.",
              **_tool_meta("Verify IFC export readiness", read_only=True, destructive=False))
    @_guarded
    def verify_ifc_export_readiness(port: int | None = None) -> dict:
        ifc_rules = [r for r in loaded.rules if "ifc" in r.needs]
        if not ifc_rules:
            return {"error": "No IFC rules configured. Add 'ifc-property-required' "
                             "rules to your rules directory."}
        return _verdict_for(ifc_rules, port).to_dict()

    @mcp.tool(description="Highlight the elements failing a rule in the Archicad window "
                          "(requires Tapir add-on).",
              **_tool_meta("Highlight failing elements", read_only=False, destructive=False))
    @_guarded
    def highlight_failures(rule_id: str, port: int | None = None) -> dict:
        rules = _rules_subset(rule_id=rule_id)
        verdict = _verdict_for(rules, port)
        guids = [g for r in verdict.results for g in r.failing_guids]
        conn = get_connection(port if port is not None else default_port)
        return actions.highlight_elements(conn, guids)

    @mcp.tool(description="Create an Archicad issue from a rule's failures and attach "
                          "the failing elements (requires Tapir add-on).",
              **_tool_meta("Create issues from rule failures", read_only=False, destructive=True))
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
        if gdl_workspace is not None:
            from archicad_mcp.gdl import tools as gdl_tools
            from archicad_mcp.gdl.workspace import Workspace
            gdl_tools.register(mcp, default_port, Workspace(gdl_workspace),
                               _tool_meta, _guarded)

    return mcp


def _register_full_mode_tools(mcp: FastMCP, default_port: int | None) -> None:
    from archicad_mcp.core import element_data as _element_data
    from archicad_mcp.core import query as _query
    from archicad_mcp.core import schemes as core_schemes

    def _conn(port: int | None):
        return get_connection(port if port is not None else default_port)

    @mcp.tool(description=(
        "Find elements matching criteria groups. Groups combine with OR; inside "
        "a group the comparisons combine with logical_operator 'and' (default) "
        "or 'or'. Each group: {element_types: [\"Wall\", ...] (Archicad type "
        "names), element_types_operator: 'is'|'is_not', logical_operator, "
        "comparisons: [{property, operator, value}]}. "
        "'property' is 'Group/Name' for a user property, the API name for a "
        "built-in (e.g. ModelView_LayerName), a property GUID, "
        "'classification:<System name>' for the element's classification item, "
        "or 'story' for the 0-based home story index. Call search_definitions "
        "to find the exact property address. Operators: equal, not_equal, less, "
        "greater, less_or_equal, greater_or_equal, contains, does_not_contain, "
        "starts_with, ends_with (strings, case-insensitive), is_in_branch_of, "
        "is_direct_child_of, is_not_in_branch_of, is_not_direct_child_of "
        "(classification items, by item ID like 'Wall' or GUID), and the unary "
        "has_value, has_no_value, is_user_undefined, is_not_user_undefined, "
        "available, not_available (no value). Numeric values use SI base "
        "units: m for length, m2 for area, m3 for volume, radian for angles; "
        "convert first (3000 mm -> 3). Enum values are their display text. "
        "An element with no usable value matches no binary operator. "
        "Returns GUIDs, counts, how many elements had properties read, and "
        "'coverage' ('whole-plan' with Tapir, 'model-elements-only' without: "
        "then 2D elements are invisible and 0 is not proof of absence). "
        "Property comparisons read values in the server (no API filters by "
        "property); a read spanning more than the element ceiling is refused, "
        "so narrow with element_types, story or classification first."),
              **_tool_meta("Find elements by criteria", read_only=True, destructive=False))
    @_guarded
    def find_elements(groups: list[dict], selection_only: bool = False,
                      port: int | None = None) -> dict:
        return _query.find_elements(_conn(port), groups, selection_only)

    from archicad_mcp.core import definitions as _definitions

    @mcp.tool(description=(
        "Fuzzy search over property and attribute definitions, so a caller "
        "does not need to know the exact 'Group/Name'. Matches names, groups "
        "and enum values; case- and accent-insensitive. kind: 'property', "
        "'attribute' (layers, fills, surfaces, composites, profiles, pen "
        "tables, ...) or 'any'. alternatives: up to 6 synonyms or translations "
        "searched too (useful on non-English projects). editable_only: keep "
        "only properties whose value can be written on at least one element "
        "type; check it before set_element_data. Each property match carries "
        "'property', the exact address find_elements, get_element_data, "
        "set_element_data and rules accept, plus value_type, measure_type "
        "(Length/Area/Volume/Angle values are in m, m2, m3, radian), "
        "collection, editable, expression_based and enum_values. Reads "
        "definitions only, never property values."),
              **_tool_meta("Search property and attribute definitions",
                           read_only=True, destructive=False))
    @_guarded
    def search_definitions(query: str, kind: str = "any",
                           alternatives: list[str] | None = None,
                           editable_only: bool = False, limit: int = 25,
                           port: int | None = None) -> dict:
        return _definitions.search_definitions(_conn(port), query, kind, alternatives,
                                               editable_only, limit)

    @mcp.tool(description="Read type, layer, requested properties (address user "
                          "properties as 'Group/Name') and optionally classifications "
                          "for the given element GUIDs.",
              **_tool_meta("Read element data", read_only=True, destructive=False))
    @_guarded
    def get_element_data(guids: list[str], properties: list[str] | None = None,
                         include_classifications: bool = False,
                         port: int | None = None) -> dict:
        return _element_data.get_element_data(_conn(port), guids, properties,
                                              include_classifications)

    @mcp.tool(description="Write element property values. DRY-RUN BY DEFAULT: returns "
                          "planned changes (current -> new) without touching the model. "
                          "Pass dry_run=false to commit.",
              **_tool_meta("Write element properties", read_only=False, destructive=True))
    @_guarded
    def set_element_data(changes: list[dict], dry_run: bool = True,
                         port: int | None = None) -> dict:
        return _element_data.set_element_data(_conn(port), changes, dry_run)

    from archicad_mcp.core import create as _create
    from archicad_mcp.core import mutate as _mutate
    from archicad_mcp.core import selection as _selection

    @mcp.tool(description="Create elements (column/slab/zone/polyline/object/mesh) via "
                          "Tapir. DRY-RUN BY DEFAULT: shows the exact command and payload. "
                          "Pass dry_run=false to create. Other types: use "
                          "execute_write_api_command.",
              **_tool_meta("Create elements", read_only=False, destructive=True))
    @_guarded
    def create_elements(element_type: str, items: list[dict], dry_run: bool = True,
                        port: int | None = None) -> dict:
        return _create.create_elements(_conn(port), element_type, items, dry_run)

    @mcp.tool(description="Move elements by a vector {x,y,z} in meters. Refuses without "
                          "confirm=true.",
              **_tool_meta("Move elements", read_only=False, destructive=True))
    @_guarded
    def move_elements(guids: list[str], vector: dict, confirm: bool = False,
                      port: int | None = None) -> dict:
        return _mutate.move_elements(_conn(port), guids, vector, confirm)

    @mcp.tool(description="Delete elements. IRREVERSIBLE. Refuses without confirm=true.",
              **_tool_meta("Delete elements", read_only=False, destructive=True))
    @_guarded
    def delete_elements(guids: list[str], confirm: bool = False,
                        port: int | None = None) -> dict:
        return _mutate.delete_elements(_conn(port), guids, confirm)

    @mcp.tool(description="Return the GUIDs of the elements currently selected in "
                          "Archicad.",
              **_tool_meta("Read current selection", read_only=True, destructive=False))
    @_guarded
    def get_selection(port: int | None = None) -> dict:
        return _selection.get_selection(_conn(port))

    @mcp.tool(description="Replace the current selection with the given element GUIDs. "
                          "Whatever the user had selected by hand is deselected.",
              **_tool_meta("Replace current selection", read_only=False, destructive=False))
    @_guarded
    def set_selection(guids: list[str], port: int | None = None) -> dict:
        return _selection.set_selection(_conn(port), guids)

    @mcp.tool(description="Deselect everything in the Archicad window.",
              **_tool_meta("Clear current selection", read_only=False, destructive=False))
    @_guarded
    def clear_selection(port: int | None = None) -> dict:
        return _selection.clear_selection(_conn(port))

    from archicad_mcp.core import attributes as _attributes
    from archicad_mcp.core import issues as _issues
    from archicad_mcp.core import project as _project
    from archicad_mcp.core import publish as _publish

    @mcp.tool(description="Project info: Archicad version, project name, stories, "
                          "hotlinks, geolocation presence (Tapir enriches).",
              **_tool_meta("Get project info", read_only=True, destructive=False))
    @_guarded
    def get_project_info(port: int | None = None) -> dict:
        return _project.get_project_info(_conn(port))

    @mcp.tool(description="List attribute names by type: Layer, BuildingMaterial, "
                          "Composite, Surface, Profile, ZoneCategory.",
              **_tool_meta("List attributes", read_only=True, destructive=False))
    @_guarded
    def list_attributes(attribute_type: str, port: int | None = None) -> dict:
        return _attributes.list_attributes(_conn(port), attribute_type)

    @mcp.tool(description="List the issues in the open project, with their ids "
                          "(requires the Tapir add-on).",
              **_tool_meta("List issues", read_only=True, destructive=False))
    @_guarded
    def list_issues(port: int | None = None) -> dict:
        return _issues.list_issues(_conn(port))

    @mcp.tool(description="Create a new issue in the open project and return its id "
                          "(requires the Tapir add-on).",
              **_tool_meta("Create an issue", read_only=False, destructive=True))
    @_guarded
    def create_issue(name: str, port: int | None = None) -> dict:
        return _issues.create_issue(_conn(port), name)

    @mcp.tool(description="Add a text comment to an existing issue, addressed by its id "
                          "(requires the Tapir add-on).",
              **_tool_meta("Comment on an issue", read_only=False, destructive=True))
    @_guarded
    def add_issue_comment(issue_id: str, comment: str, port: int | None = None) -> dict:
        return _issues.add_issue_comment(_conn(port), issue_id, comment)

    @mcp.tool(description="Attach elements to an existing issue as highlights "
                          "(requires the Tapir add-on).",
              **_tool_meta("Attach elements to an issue", read_only=False, destructive=True))
    @_guarded
    def attach_elements_to_issue(issue_id: str, guids: list[str],
                                 port: int | None = None) -> dict:
        return _issues.attach_elements_to_issue(_conn(port), issue_id, guids)

    @mcp.tool(description="Export every issue in the project to a BCF file at the given "
                          "path, aligned to the survey point. Overwrites the file if it "
                          "exists (requires the Tapir add-on).",
              **_tool_meta("Export issues to BCF", read_only=False, destructive=True))
    @_guarded
    def export_issues_bcf(bcf_path: str, port: int | None = None) -> dict:
        return _issues.export_issues_bcf(_conn(port), bcf_path)

    @mcp.tool(description="Import issues into the project from a BCF file, aligned to "
                          "the survey point (requires the Tapir add-on).",
              **_tool_meta("Import issues from BCF", read_only=False, destructive=True))
    @_guarded
    def import_issues_bcf(bcf_path: str, port: int | None = None) -> dict:
        return _issues.import_issues_bcf(_conn(port), bcf_path)

    @mcp.tool(description="Fire an Archicad publisher set by name (Tapir).",
              **_tool_meta("Run a publisher set", read_only=False, destructive=True))
    @_guarded
    def publish(publisher_set_name: str, port: int | None = None) -> dict:
        return _publish.publish(_conn(port), publisher_set_name)

    @mcp.tool(description="Describe an exported Archicad schedule scheme XML: its "
                          "criteria and its ordered columns, with what each column "
                          "binds to. Schedules have no API, so export the scheme "
                          "first via Document > Schedules > Scheme Settings > Export "
                          "and pass the file path. Reads the file only, never "
                          "Archicad.",
              **_tool_meta("Read a schedule scheme", read_only=True, destructive=False))
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
                          "so the name can be resolved.",
              **_tool_meta("Edit a schedule scheme", read_only=False, destructive=True))
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
                          "not risk the property-read crash.",
              **_tool_meta("Validate a schedule scheme", read_only=True, destructive=False))
    @_guarded
    def validate_schedule_scheme(path: str, port: int | None = None) -> dict:
        return core_schemes.validate_schedule_scheme(
            path, port if port is not None else default_port)

    from archicad_mcp.gateway import execute as _gateway
    from archicad_mcp.gateway.registry import build_registry

    registry = build_registry()

    @mcp.tool(description="Catalog of ALL available Archicad API commands (official "
                          "JSON API + Tapir). Filter by group, or by access='read' / "
                          "'write' to see which of the two execute tools runs a "
                          "given command.",
              **_tool_meta("List API commands", read_only=True, destructive=False))
    def list_api_commands(group: str | None = None, access: str | None = None) -> dict:
        return _gateway.list_api_commands(registry, group, access)

    @mcp.tool(description="Full description and input schema for one API command. "
                          "Call before execute_read_api_command or "
                          "execute_write_api_command.",
              **_tool_meta("Describe an API command", read_only=True, destructive=False))
    def describe_api_command(name: str) -> dict:
        return _gateway.describe_api_command(registry, name)

    @mcp.tool(description="Run one read-only Archicad API command by name and return "
                          "its result. Covers the official Archicad JSON API "
                          "(https://archicadapi.graphisoft.com/JSONInterfaceDocumentation/) "
                          "and the Tapir add-on "
                          "(https://github.com/ENZYME-APD/tapir-archicad-automation). "
                          "Reads only: a command that changes the project is refused "
                          "here and belongs to execute_write_api_command. Params are "
                          "validated against the bundled schema where available. "
                          "Prefer the dedicated tools when one exists.",
              **_tool_meta("Run a read-only API command", read_only=True, destructive=False))
    @_guarded
    def execute_read_api_command(name: str, params: dict | str | None = None,
                                 port: int | None = None) -> dict:
        return _gateway.execute_read_api_command(
            registry, lambda: _conn(port), name, params)

    @mcp.tool(description="Run one Archicad API command that changes the project. "
                          "Covers the official Archicad JSON API "
                          "(https://archicadapi.graphisoft.com/JSONInterfaceDocumentation/) "
                          "and the Tapir add-on "
                          "(https://github.com/ENZYME-APD/tapir-archicad-automation). "
                          "IRREVERSIBLE for many commands, and reaches DeleteElements "
                          "and QuitArchicad among others. Refuses without confirm=true; "
                          "the refusal echoes the command and params it would have run. "
                          "Params are validated against the bundled schema where "
                          "available. Prefer the dedicated tools when one exists.",
              **_tool_meta("Run a write API command", read_only=False, destructive=True))
    @_guarded
    def execute_write_api_command(name: str, params: dict | str | None = None,
                                  confirm: bool = False,
                                  port: int | None = None) -> dict:
        return _gateway.execute_write_api_command(
            registry, lambda: _conn(port), name, params, confirm)


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


def _rules_phrase(rule_count: int, rules_source: str | None) -> str:
    """'1 rule loaded from /x' or '3 bundled example rules loaded (no rules
    directory set)'. A bare count was read as "scoring against nothing" when
    it was the office folder holding one uncommented rule, so the count has
    to carry where it came from."""
    noun = "rule" if rule_count == 1 else "rules"
    if rules_source:
        return f"{rule_count} {noun} loaded from {rules_source}"
    return f"{rule_count} bundled example {noun} loaded (no rules directory set)"


def format_startup_banner(mode: str, rule_count: int,
                          instances: Sequence[InstanceInfo],
                          rule_errors: int = 0,
                          gdl_workspace: Path | None = None,
                          rules_source: str | None = None) -> str:
    """The diagnostic lines written to stderr at startup.

    This is what someone reads in mcp-server-archicad.log when the tools are
    not behaving, so every line has to be actionable on its own.
    """
    head = f"{_BANNER_PREFIX} mode={mode}, {_rules_phrase(rule_count, rules_source)}"
    if rule_errors:
        head += f", {rule_errors} rule file(s) rejected (call list_rules for details)"
    # GDL tools register only in full mode with a workspace folder set
    if mode == "full" and gdl_workspace is not None:
        head += f", GDL workspace {gdl_workspace}"
    else:
        head += ", GDL tools off"
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


def emit_startup_banner(mode: str, rule_count: int, rule_errors: int = 0,
                        gdl_workspace: Path | None = None,
                        rules_source: str | None = None) -> None:
    """Write the banner to stderr. Never raises, never touches stdout.

    Under stdio transport stdout is the JSON-RPC channel: one stray byte there
    corrupts the stream and the client drops the server. And a diagnostic that
    can itself abort startup is worse than no diagnostic, so discovery failure
    degrades to the config line alone.
    """
    try:
        instances = discover_instances()
    except Exception as exc:  # noqa: BLE001 - diagnostics must not break startup
        prefix_config = (f"{_BANNER_PREFIX} mode={mode}, "
                         f"{_rules_phrase(rule_count, rules_source)}")
        # GDL tools register only in full mode with a workspace folder set
        if mode == "full" and gdl_workspace is not None:
            prefix_config += f", GDL workspace {gdl_workspace}"
        else:
            prefix_config += ", GDL tools off"
        print(f"{prefix_config} (instance discovery failed: {exc})",
              file=sys.stderr, flush=True)
        return
    print(format_startup_banner(mode, rule_count, instances, rule_errors, gdl_workspace,
                                rules_source),
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


def resolve_gdl_workspace(raw: str | Path | None) -> Path | None:
    """None for an unset or blank workspace, so the GDL tools stay unregistered.

    Same trap as resolve_rules_dir: an unfilled .mcpb field arrives as an empty
    string, and Path("") is Path("."), which is truthy. Registering the GDL
    tools against the working directory would be worse than not registering
    them, because builds would write .gsm files into it.
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
    parser.add_argument("--gdl-workspace", type=Path,
                        default=resolve_gdl_workspace(
                            os.environ.get("ARCHICAD_MCP_GDL_WORKSPACE")))
    args, _ = parser.parse_known_args()
    rules_dir = resolve_rules_dir(args.rules_dir)
    gdl_workspace = resolve_gdl_workspace(args.gdl_workspace)
    server = build_server(mode=args.mode, rules_dir=rules_dir, port=args.port,
                          gdl_workspace=gdl_workspace)
    emit_startup_banner(args.mode, server.archicad_rule_count,
                        server.archicad_rule_errors, gdl_workspace,
                        server.archicad_rule_source)
    server.run()


if __name__ == "__main__":
    main()
