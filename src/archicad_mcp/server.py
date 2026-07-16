from __future__ import annotations

import argparse
import functools
import os
from collections import Counter
from pathlib import Path

from fastmcp import FastMCP
from multiconn_archicad.errors import APIErrorBase

from archicad_mcp import actions
from archicad_mcp.connection import (
    ArchicadUnavailableError,
    discover_instances,
    get_connection,
)
from archicad_mcp.extract import build_snapshot
from archicad_mcp.rules.engine import (
    data_needs,
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
        return {"error": f"Archicad API error: {exc.message}. "
                         "Is a project open in Archicad? Open one and retry."}
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
        snapshot = build_snapshot(conn, data_needs(rules), property_needs(rules))
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
                          "break down by layer and story — that reads a property "
                          "across every element and is refused on very large models "
                          "(can crash Archicad). Counts only, never element data.")
    @_guarded
    def get_model_summary(include_layer_story: bool = False,
                          port: int | None = None) -> dict:
        conn = get_connection(port if port is not None else default_port)
        needs = frozenset({"elements", "properties"}) if include_layer_story \
            else frozenset({"elements"})
        snapshot = build_snapshot(conn, needs=needs)
        by_type = Counter(e.element_type for e in snapshot.elements)
        result = {"element_count": len(snapshot.elements),
                  "by_type": dict(by_type)}
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

    def _conn(port: int | None):
        return get_connection(port if port is not None else default_port)

    @mcp.tool(description="Query elements with AND-combined filters: element_type, "
                          "layer, story, classification_system, selection_only. "
                          "Returns GUIDs and counts.")
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
    def execute_api_command(name: str, params: dict | None = None,
                            port: int | None = None) -> dict:
        return _gateway.execute_api_command(registry, _conn(port), name, params)


def main() -> None:
    parser = argparse.ArgumentParser(prog="archicad-mcp")
    parser.add_argument("--mode", choices=["verdicts", "full"],
                        default=os.environ.get("ARCHICAD_MCP_MODE", "full"))
    parser.add_argument("--rules-dir", type=Path,
                        default=os.environ.get("ARCHICAD_MCP_RULES_DIR"))
    parser.add_argument("--port", type=int, default=None,
                        help="Archicad API port (19723-19743); auto-detected if omitted")
    args, _ = parser.parse_known_args()
    rules_dir = Path(args.rules_dir) if args.rules_dir else None
    build_server(mode=args.mode, rules_dir=rules_dir, port=args.port).run()


if __name__ == "__main__":
    main()
