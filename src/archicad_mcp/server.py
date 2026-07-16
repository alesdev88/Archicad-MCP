from __future__ import annotations

import argparse
import os
from collections import Counter
from pathlib import Path

from fastmcp import FastMCP

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


def _tool_error(exc: Exception) -> dict:
    return {"error": str(exc)}


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
        return filter_by_tag(rules, ruleset)

    def _verdict_for(rules, request_port: int | None) -> Verdict:
        conn = get_connection(request_port if request_port is not None else default_port)
        snapshot = build_snapshot(conn, data_needs(rules), property_needs(rules))
        return run_rules(rules, snapshot)

    # ---------- Tier 1: verdict tools (both modes) ----------

    @mcp.tool(description="List running Archicad instances: port, version, open project, "
                          "Tapir add-on availability. Call this first.")
    def list_instances() -> dict:
        return {"instances": [i.to_dict() for i in discover_instances()]}

    @mcp.tool(description="Aggregate element counts by type, story, and layer. "
                          "Returns counts only, never element data.")
    def get_model_summary(port: int | None = None) -> dict:
        try:
            conn = get_connection(port if port is not None else default_port)
            snapshot = build_snapshot(
                conn, needs=frozenset({"elements", "properties"}))
        except ArchicadUnavailableError as exc:
            return _tool_error(exc)
        by_type = Counter(e.element_type for e in snapshot.elements)
        by_layer = Counter(e.layer for e in snapshot.elements if e.layer)
        by_story = Counter(str(e.story) for e in snapshot.elements
                           if e.story is not None)
        return {"element_count": len(snapshot.elements),
                "by_type": dict(by_type), "by_layer": dict(by_layer),
                "by_story": dict(by_story)}

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
    def run_rule(rule_id: str, port: int | None = None) -> dict:
        try:
            rules = _rules_subset(rule_id=rule_id)
            return _verdict_for(rules, port).to_dict()
        except ArchicadUnavailableError as exc:
            return _tool_error(exc)

    @mcp.tool(description="Run all loaded QA rules (optionally only those tagged with "
                          "'ruleset') against the open model. Returns a scored verdict.")
    def audit_delivery_readiness(ruleset: str | None = None, port: int | None = None) -> dict:
        try:
            return _verdict_for(_rules_subset(ruleset=ruleset), port).to_dict()
        except ArchicadUnavailableError as exc:
            return _tool_error(exc)

    @mcp.tool(description="Run only the IFC-related QA rules to check IFC export "
                          "readiness. Requires the Tapir add-on for IFC data.")
    def verify_ifc_export_readiness(port: int | None = None) -> dict:
        ifc_rules = [r for r in loaded.rules if "ifc" in r.needs]
        if not ifc_rules:
            return {"error": "No IFC rules configured. Add 'ifc-property-required' "
                             "rules to your rules directory."}
        try:
            return _verdict_for(ifc_rules, port).to_dict()
        except ArchicadUnavailableError as exc:
            return _tool_error(exc)

    @mcp.tool(description="Highlight the elements failing a rule in the Archicad window "
                          "(requires Tapir add-on).")
    def highlight_failures(rule_id: str, port: int | None = None) -> dict:
        try:
            rules = _rules_subset(rule_id=rule_id)
            verdict = _verdict_for(rules, port)
            guids = [g for r in verdict.results for g in r.failing_guids]
            conn = get_connection(port if port is not None else default_port)
            return actions.highlight_elements(conn, guids)
        except ArchicadUnavailableError as exc:
            return _tool_error(exc)

    @mcp.tool(description="Create an Archicad issue from a rule's failures and attach "
                          "the failing elements (requires Tapir add-on).")
    def create_issues_from_failures(rule_id: str, port: int | None = None) -> dict:
        try:
            rules = _rules_subset(rule_id=rule_id)
            verdict = _verdict_for(rules, port)
            result = verdict.results[0]
            conn = get_connection(port if port is not None else default_port)
            return actions.create_issues(conn, rule_id, result.message,
                                         list(result.failing_guids))
        except ArchicadUnavailableError as exc:
            return _tool_error(exc)

    if mode == "full":
        _register_full_mode_tools(mcp, default_port)

    return mcp


def _register_full_mode_tools(mcp: FastMCP, default_port: int | None) -> None:
    """Tier 2 + 3 tools. Extended in later tasks."""


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
